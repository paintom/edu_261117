"""SteelQ Report Agent — Streamlit 진입점.

실행: streamlit run app.py
Streamlit 의존 코드는 이 파일에만 둔다. 계산 로직은 steelq/ 패키지, 차트는 steelq/charts.py,
보조 CSS는 assets/style.css, 테마는 .streamlit/config.toml 에 있다.
"""

from __future__ import annotations

import hashlib
import io
from html import escape
from pathlib import Path

import pandas as pd
import streamlit as st

from steelq import aggregator as A
from steelq import charts as CH
from steelq import config
from steelq.cleaner import CleanResult, R_WARN_OVER, R_WARN_ZERO_PROD, clean
from steelq.loader import (META_FILE, META_ROW, LoadedFile, LoaderError, auto_map_columns, list_sheets,
                           load_file, merge_files)
from steelq.pipeline import AnalysisParams, AnalysisResult, Filters, run_analysis
from steelq.report import REPORT_SHEETS, build_report, report_filename
from steelq.sample_data import generate_sample

st.set_page_config(page_title="SteelQ Report Agent", page_icon="🏭", layout="wide",
                   initial_sidebar_state="expanded")

SAMPLE_NAME = "샘플데이터"
NO_SELECTION = "(선택 안 함)"
ZERO_FILL = "(파일에 없음 · 0으로 처리)"
# 캐시는 모든 세션이 공유하는 서버 메모리에 남으므로 개수·보관 시간을 제한한다 (무료 서버 메모리 1GB)
CACHE_TTL = 1800          # 30분
CACHE_MAX_FILES = 8
CACHE_MAX_RESULTS = 12
CSS_PATH = Path(__file__).parent / "assets" / "style.css"
ALL_PLANT = "공장 전체"
SHEET_DESCRIPTIONS = {
    "요약": "분석 정보, 핵심 지표, 자동 분석 코멘트",
    "일별집계": "일자별 생산량·불량률·결함 5종 + 추이 차트",
    "월별집계": "월별 집계와 전월 대비 증감 + 추이 차트",
    "결함분석": "결함 유형 점유율, 월별·일별 점유 추이",
    "라인·강종": "라인/강종별 불량률, 라인 × 결함 교차표",
    "이상치": "탐지된 이상치 목록 (심각도 색상)",
    "정제로그": "정제 규칙별 건수와 변경 상세",
    "정제데이터": "정제·필터가 적용된 원시 데이터",
}


def inject_css() -> None:
    if CSS_PATH.exists():
        st.markdown(f"<style>{CSS_PATH.read_text(encoding='utf-8')}</style>", unsafe_allow_html=True)


def html(markup: str) -> None:
    st.markdown(markup, unsafe_allow_html=True)


def section(title: str, sub: str | None = None) -> None:
    html(f'<div class="sq-h">{escape(title)}</div>' + (f'<div class="sq-h-sub">{escape(sub)}</div>' if sub else ""))


def side_header(title: str) -> None:
    html(f'<div class="sq-side-h">{escape(title)}</div>')


def plot(fig, key: str) -> None:
    """같은 차트가 여러 탭에 나올 수 있으므로 key를 항상 지정한다 (StreamlitDuplicateElementId 방지)."""
    st.plotly_chart(fig, theme=None, config=CH.PLOTLY_CONFIG, width="stretch", key=key)


# ---------------------------------------------------------------------------
# 캐시된 데이터 로딩
# ---------------------------------------------------------------------------
@st.cache_data(show_spinner=False)
def _sample_frames() -> tuple[pd.DataFrame, pd.DataFrame]:
    result = generate_sample()
    return result.data, result.injected


@st.cache_data(show_spinner=False)
def _sample_downloads() -> tuple[bytes, bytes]:
    data, _ = _sample_frames()
    csv_bytes = data.to_csv(index=False, date_format="%Y-%m-%d").encode("utf-8-sig")
    buf = io.BytesIO()
    data.to_excel(buf, index=False, sheet_name="생산품질")
    return csv_bytes, buf.getvalue()


@st.cache_data(show_spinner=False, ttl=CACHE_TTL, max_entries=CACHE_MAX_FILES)
def _sheets(data: bytes, name: str) -> list[str]:
    return list_sheets(data, filename=name)


@st.cache_data(show_spinner=False, ttl=CACHE_TTL, max_entries=CACHE_MAX_FILES)
def _load(data: bytes, name: str, sheet: str | None) -> LoadedFile:
    return load_file(data, filename=name, sheet_name=sheet if sheet is not None else 0)


def _sample_loaded() -> LoadedFile:
    data, _ = _sample_frames()
    data = data.copy()
    # 업로드한 CSV와 같은 모양으로 보이도록 일자를 YYYY-MM-DD 문자열로 표시
    data[config.COL_DATE] = data[config.COL_DATE].dt.strftime("%Y-%m-%d")
    mapping, missing = auto_map_columns(list(data.columns))
    return LoadedFile(name=SAMPLE_NAME, raw=data, mapping=mapping, missing=missing, info="내장 샘플")


def data_key(merged: pd.DataFrame) -> str:
    """병합 데이터 전체 내용의 해시.

    st.cache_data는 5만 행 이상 DataFrame을 일부 표본 행만으로 해시하므로, 몇 칸만 고친 같은 크기의 파일을
    다시 올리면 이전 결과가 반환될 수 있다. 그래서 DataFrame 자체(_merged)는 해시 대상에서 빼고
    전체 내용 해시를 캐시 키로 쓴다.
    """
    digest = hashlib.sha256(pd.util.hash_pandas_object(merged, index=True).to_numpy().tobytes())
    digest.update("|".join(map(str, merged.columns)).encode("utf-8"))
    return digest.hexdigest()


@st.cache_data(show_spinner="데이터 정제 중...", ttl=CACHE_TTL, max_entries=CACHE_MAX_RESULTS)
def _clean(_merged: pd.DataFrame, key: str, dup: str, neg: str) -> CleanResult:
    return clean(_merged, duplicate_key_strategy=dup, negative_strategy=neg)


@st.cache_data(show_spinner="분석 중...", ttl=CACHE_TTL, max_entries=CACHE_MAX_RESULTS)
def _analyze(_merged: pd.DataFrame, key: str, dup: str, neg: str,
             filters: Filters, params: AnalysisParams) -> AnalysisResult:
    return run_analysis(_clean(_merged, key, dup, neg), filters, params)


# ---------------------------------------------------------------------------
# 오류 안내
# ---------------------------------------------------------------------------
def error_with_hint(message: str) -> None:
    hints = {
        "인코딩": "엑셀에서 **다른 이름으로 저장 → CSV UTF-8(쉼표로 분리)** 형식으로 다시 저장해 업로드하세요.",
        "비어": "데이터가 들어 있는 파일인지, 첫 행이 컬럼 제목인지 확인하세요.",
        "데이터 행이 없": "헤더 아래에 데이터 행이 있는지 확인하세요.",
        "지원하지 않는": "CSV, XLSX, XLS 파일만 업로드할 수 있습니다.",
        "엑셀 파일": "파일이 열려 있거나 암호가 걸려 있지 않은지 확인하고, 엑셀에서 다시 저장해 보세요.",
        "CSV 형식": "구분자가 쉼표(,)인지, 따옴표가 짝이 맞는지 확인하세요.",
    }
    hint = next((h for k, h in hints.items() if k in message), None)
    st.error(f"**{message}**" + (f"\n\n💡 해결 방법: {hint}" if hint else ""), icon="❌")


# ---------------------------------------------------------------------------
# 사이드바
# ---------------------------------------------------------------------------
def _on_upload_change() -> None:
    st.session_state["mode"] = "upload" if st.session_state.get("uploads") else None


def _use_sample() -> None:
    st.session_state["mode"] = "sample"


def sidebar_input() -> list[tuple[bytes, str, str, str | None]] | None:
    """① 데이터 입력. 업로드 파일 목록 [(bytes, 파일명, 표시 이름, 시트)]을 반환. 샘플 모드면 None."""
    with st.sidebar:
        side_header("① 데이터 입력")
        uploads = st.file_uploader(
            "생산·품질 파일 (CSV · XLSX · XLS, 여러 개 가능)",
            type=[ext.lstrip(".") for ext in config.SUPPORTED_EXTENSIONS],
            accept_multiple_files=True, key="uploads", on_change=_on_upload_change,
        )
        st.button("📊 샘플 데이터로 실행", type="primary", width="stretch", on_click=_use_sample,
                  help="가상 철강 생산·품질 데이터(3개월, 4개 라인)로 전체 기능을 체험합니다.")
        with st.expander("샘플 파일 내려받기"):
            csv_bytes, xlsx_bytes = _sample_downloads()
            c1, c2 = st.columns(2)
            c1.download_button("CSV", csv_bytes, file_name=config.SAMPLE_CSV.name, mime="text/csv", width="stretch")
            c2.download_button("XLSX", xlsx_bytes, file_name=config.SAMPLE_XLSX.name, width="stretch",
                               mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

        if st.session_state.get("mode") != "upload" or not uploads:
            return None

        files: list[tuple[bytes, str, str, str | None]] = []
        seen: dict[str, int] = {}
        for idx, up in enumerate(uploads):
            data = up.getvalue()
            # 같은 이름의 파일이 여러 개면 '이름 (2)'처럼 구분 (위젯 key 충돌·정제 로그 혼동 방지)
            seen[up.name] = seen.get(up.name, 0) + 1
            display = up.name if seen[up.name] == 1 else f"{up.name} ({seen[up.name]})"
            sheet = None
            if not up.name.lower().endswith(".csv"):
                try:
                    sheets = _sheets(data, up.name)
                except LoaderError as exc:
                    error_with_hint(str(exc))
                    continue
                if len(sheets) > 1:
                    sheet = st.selectbox(f"'{display}' 시트 선택", sheets, key=f"sheet::{idx}::{up.name}")
                elif sheets:
                    sheet = sheets[0]
            files.append((data, up.name, display, sheet))
        return files


def clean_options(box) -> tuple[str, str]:
    with box:
        side_header("③ 정제 옵션")
        with st.expander("중복 · 음수 처리 방식", expanded=False):
            dup = st.selectbox("키 중복(일자+라인+강종)", list(config.DUPLICATE_KEY_STRATEGIES),
                               format_func=config.DUPLICATE_KEY_STRATEGIES.get, key="opt_dup",
                               help="같은 일자·라인·강종 행이 여러 개일 때 처리 방식")
            neg = st.selectbox("음수 값", list(config.NEGATIVE_STRATEGIES),
                               format_func=config.NEGATIVE_STRATEGIES.get, key="opt_neg",
                               help="'결측 처리'를 고르면 생산량은 보간, 결함은 0으로 대체됩니다.")
    return dup, neg


def filter_controls(box, data: pd.DataFrame) -> Filters:
    lines = sorted(data[config.COL_LINE].unique())
    grades = sorted(data[config.COL_GRADE].unique())
    dmin, dmax = data[config.COL_DATE].min().date(), data[config.COL_DATE].max().date()
    # 데이터가 바뀌면 위젯 상태를 새로 만들도록 키에 데이터 서명을 넣는다
    sig = abs(hash((dmin, dmax, tuple(lines), tuple(grades)))) % 10**8
    with box:
        side_header("② 필터")
        period = st.date_input("기간", value=(dmin, dmax), min_value=dmin, max_value=dmax,
                               format="YYYY-MM-DD", key=f"f_period::{sig}")
        sel_lines = st.multiselect("라인", lines, default=lines, key=f"f_lines::{sig}", placeholder="라인 선택")
        sel_grades = st.multiselect("강종", grades, default=grades, key=f"f_grades::{sig}", placeholder="강종 선택")
    if isinstance(period, (tuple, list)):
        start = period[0] if len(period) >= 1 else dmin
        end = period[1] if len(period) >= 2 else dmax  # 기간 선택 도중(시작일만 선택)
    else:
        start = end = period
    return Filters(
        start=start, end=end,
        lines=None if set(sel_lines) == set(lines) else tuple(sel_lines),
        grades=None if set(sel_grades) == set(grades) else tuple(sel_grades),
    )


def analysis_controls(box) -> AnalysisParams:
    with box:
        side_header("④ 분석 기준")
        target = st.number_input("목표 불량률(%)", min_value=0.01, max_value=100.0,
                                 value=float(config.TARGET_DEFECT_RATE), step=0.1, format="%.2f", key="p_target")
        with st.expander("이상치 탐지 기준 (고급)"):
            iqr_k = st.slider("IQR 배수 (Q3 + k×IQR)", 0.5, 5.0, float(config.IQR_MULTIPLIER), 0.1, key="p_iqr")
            z = st.slider("Z-score 임계값 (|z| ≥)", 1.5, 6.0, float(config.ZSCORE_THRESHOLD), 0.1, key="p_z")
            ma_window = st.slider("이동평균 기간(일)", 3, 30, int(config.MOVING_AVG_WINDOW), 1, key="p_maw")
            ma_mult = st.slider("이동평균 대비 배수", 1.1, 5.0, float(config.MOVING_AVG_MULTIPLIER), 0.1, key="p_mam")
            share = st.slider("결함 점유율 급변(%p)", 5.0, 50.0, float(config.SHARE_SHIFT_THRESHOLD), 1.0, key="p_share")
    return AnalysisParams(target_rate=target, iqr_k=iqr_k, z_threshold=z, ma_window=ma_window,
                          ma_multiplier=ma_mult, share_shift=share)


# ---------------------------------------------------------------------------
# 표 표시 (숫자 형식 통일: 톤 = 천 단위 + 소수 1자리, 비율 = 소수 2자리 %)
# ---------------------------------------------------------------------------
PERCENT_COLS = {config.COL_DEFECT_RATE, A.COL_SHARE, A.COL_CUM_SHARE}
TON_COLS = {config.COL_PRODUCTION, config.COL_TOTAL_DEFECT, A.COL_DEFECT_AMOUNT, *config.DEFECT_TYPES}
STYLER_MAX_CELLS = 200_000
# 화면 표는 브라우저로 전체 데이터를 보내므로 대용량은 앞부분만 미리보기 (전체는 엑셀 리포트에 포함)
TABLE_PREVIEW_ROWS = 5_000


def _formats(df: pd.DataFrame, percent_cols: set[str], ton_cols: set[str]) -> dict:
    fmt = {}
    for col in df.columns:
        if col in percent_cols:
            fmt[col] = "{:.2f}%"
        elif col in ton_cols:
            fmt[col] = "{:,.1f}"
        elif col == A.COL_RATE_DIFF:
            fmt[col] = "{:+.2f}%p"
        elif col == A.COL_PROD_DIFF:
            fmt[col] = "{:+.1f}%"
        elif col == A.COL_RATIO:
            fmt[col] = "{:.2f}배"
        elif col == "값":
            fmt[col] = "{:,.2f}"
        elif col in ("건수", "원본행"):
            fmt[col] = "{:,}"
        elif pd.api.types.is_datetime64_any_dtype(df[col]):
            fmt[col] = "{:%Y-%m-%d}"
    return fmt


def _severity_style(v) -> str:
    if v in config.SEVERITY_COLORS:
        bg, fg = config.SEVERITY_COLORS[v]
        return f"background-color: #{bg}; color: #{fg}; font-weight: 600"
    return ""


def show_table(df: pd.DataFrame, percent_cols: set[str] | None = None, ton_cols: set[str] | None = None,
               height: int | str = "auto") -> None:
    percent_cols = PERCENT_COLS if percent_cols is None else percent_cols
    ton_cols = TON_COLS if ton_cols is None else ton_cols
    if df.size <= STYLER_MAX_CELLS:
        styler = df.style.format(_formats(df, percent_cols, ton_cols), na_rep="-")
        if "심각도" in df.columns:
            styler = styler.map(_severity_style, subset=["심각도"])
        st.dataframe(styler, hide_index=True, width="stretch", height=height)
        return
    # 대용량은 Styler 제한을 넘으므로 column_config로 형식만 지정
    cfg = {}
    for col in df.columns:
        if col in percent_cols:
            cfg[col] = st.column_config.NumberColumn(col, format="%.2f%%")
        elif col in ton_cols:
            cfg[col] = st.column_config.NumberColumn(col, format="%.1f")
        elif pd.api.types.is_datetime64_any_dtype(df[col]):
            cfg[col] = st.column_config.DateColumn(col, format="YYYY-MM-DD")
    st.dataframe(df, column_config=cfg, hide_index=True, width="stretch", height=height)


# ---------------------------------------------------------------------------
# 헤더 · 빈 화면 · 컬럼 수동 매핑
# ---------------------------------------------------------------------------
def render_header(box, result: AnalysisResult | None, source: str | None) -> None:
    chips = []
    if result is not None and not result.empty:
        k = result.kpi
        chips.append(f"📅 {pd.Timestamp(k['start']):%Y-%m-%d} ~ {pd.Timestamp(k['end']):%Y-%m-%d}")
        chips.append(f"🧾 {k['rows']:,}행 · {k['days']}일")
    if source:
        chips.append(f"📂 {source}")
    chips_html = "".join(f'<span class="sq-chip">{escape(c)}</span>' for c in chips)
    box.markdown(
        f"""<div class="sq-header">
  <div class="sq-brand"><span class="sq-logo">🏭</span>
    <div><div class="sq-title">SteelQ Report Agent</div>
    <div class="sq-sub">철강 생산·품질 일일 집계 자동화 — 정제 · 집계 · 이상치 탐지 · 분석 코멘트 · 엑셀 리포트</div></div>
  </div>
  <div class="sq-meta">{chips_html}</div>
</div>
<div class="sq-notice">🔒 공개 배포 환경입니다. <b>실제 사내 생산·품질 데이터는 업로드하지 마세요.</b>
업로드한 파일은 디스크에 저장되지 않으며, 처리 결과는 서버 메모리에 최대 30분 임시 보관된 뒤 삭제됩니다.</div>""",
        unsafe_allow_html=True,
    )


def render_empty_state() -> None:
    html("""<div class="sq-empty">
  <h3>파일을 업로드하거나 샘플 데이터로 시작하세요</h3>
  <p>생산·품질 데이터를 올리면 정제부터 엑셀 리포트까지 자동으로 처리합니다.</p>
  <div class="sq-steps">
    <div class="sq-step"><div class="sq-step-n">1</div><div class="sq-step-t">업로드</div>
      <div class="sq-step-d">왼쪽 사이드바에 CSV·엑셀 파일을 올립니다. 여러 파일도 한 번에 병합됩니다.</div></div>
    <div class="sq-step"><div class="sq-step-n">2</div><div class="sq-step-t">확인</div>
      <div class="sq-step-d">정제 결과, 일별·월별 불량률, 결함 추이, 이상치와 분석 코멘트를 확인합니다.</div></div>
    <div class="sq-step"><div class="sq-step-n">3</div><div class="sq-step-t">다운로드</div>
      <div class="sq-step-d">차트가 포함된 8개 시트 엑셀 리포트를 내려받아 바로 보고합니다.</div></div>
  </div>
</div>""")
    st.write("")
    c1, _ = st.columns([1, 3])
    c1.button("📊 샘플 데이터로 시작하기", type="primary", on_click=_use_sample, width="stretch", key="empty_sample")
    with st.expander("입력 파일 형식 안내", expanded=True):
        example = pd.DataFrame([
            ["2026-10-01", "열연1라인", "SS400", 1080.5, 1.21, 1.52, 2.05, 0.88, 0.51],
            ["2026-10-01", "냉연1라인", "SPCC", 845.2, 1.30, 0.41, 0.37, 0.52, 0.22],
        ], columns=config.REQUIRED_COLUMNS)
        st.markdown("- 1행 = **일자 × 라인 × 강종**, 생산량·불량량 단위는 **톤**\n"
                    "- 결함 5종: " + ", ".join(f"`{d}`" for d in config.DEFECT_TYPES) + "\n"
                    "- 컬럼명이 달라도(예: `Date`, `Line`, `Qty`, `생산량(톤)`) 자동으로 인식하고, "
                    "인식하지 못한 컬럼은 직접 지정할 수 있습니다.")
        show_table(example)


def manual_mapping(lf: LoadedFile, file_idx: int) -> None:
    """필수 컬럼이 누락된 파일에 대해 드롭다운으로 수동 매핑 UI를 제공하고 적용한다.

    결함 컬럼은 파일에 아예 없을 수 있으므로 '0으로 처리' 선택지를 함께 제공한다.
    """
    missing = list(lf.missing)
    message = st.empty()  # 선택 결과에 따라 오류/성공 메시지를 나중에 채운다
    mapped_sources = set(lf.mapping.keys())
    candidates = [NO_SELECTION] + [c for c in lf.raw.columns if c not in mapped_sources]
    new_mapping = dict(lf.mapping)
    zero_fill: list[str] = []
    cols = st.columns(min(len(missing), 4))
    for i, std_col in enumerate(missing):
        options = candidates + ([ZERO_FILL] if std_col in config.DEFECT_TYPES else [])
        choice = cols[i % len(cols)].selectbox(std_col, options, key=f"map::{file_idx}::{lf.name}::{std_col}")
        if choice == ZERO_FILL:
            zero_fill.append(std_col)
        elif choice != NO_SELECTION:
            new_mapping[choice] = std_col
    try:
        lf.set_mapping(new_mapping, zero_fill=zero_fill)
    except LoaderError as exc:
        message.error(f"**'{lf.name}'** 컬럼 매핑 오류: {exc}", icon="❌")
        return
    if lf.is_complete:
        parts = [f"{src} → {std}" for src, std in new_mapping.items() if std in missing]
        parts += [f"{std} → 0으로 처리" for std in zero_fill]
        message.success(f"**'{lf.name}'** 컬럼 수동 매핑 완료: {', '.join(parts)}", icon="✅")
    else:
        message.error(
            f"**'{lf.name}'** 에서 필수 컬럼을 찾지 못했습니다: **{', '.join(lf.missing)}**\n\n"
            "💡 해결 방법: 아래에서 원본 파일의 어떤 컬럼이 해당하는지 선택해 주세요. "
            "결함 컬럼이 파일에 없다면 '0으로 처리'를 고르세요.", icon="❌")


# ---------------------------------------------------------------------------
# 탭: 요약
# ---------------------------------------------------------------------------
def _delta_html(value: float | None, unit: str, up_is_bad: bool = True, digits: int = 2) -> str:
    if value is None or pd.isna(value):
        return '<span class="sq-flat">전월 데이터 없음</span>'
    if abs(value) < 10 ** -digits:
        return f'<span class="sq-flat">— 0{unit}</span> 전월비'
    up = value > 0
    cls = ("sq-up-bad" if up else "sq-down-good") if up_is_bad else "sq-flat"
    arrow = "▲" if up else "▼"
    return f'<span class="{cls}">{arrow}&nbsp;{abs(value):.{digits}f}{unit}</span>&nbsp;전월비'


def kpi_cards(res: AnalysisResult) -> None:
    k, p, m = res.kpi, res.params, res.monthly
    has_prev = len(m) >= 2
    rate_diff = m[A.COL_RATE_DIFF].iloc[-1] if has_prev else None
    prod_diff = m[A.COL_PROD_DIFF].iloc[-1] if has_prev else None
    over = k["rate"] > p.target_rate
    n_warn = int((res.anomalies["심각도"] == config.SEVERITY_WARNING).sum()) if not res.anomalies.empty else 0
    n_caution = len(res.anomalies) - n_warn
    target_badge = (f'<span class="sq-badge critical">🔴 목표 {p.target_rate:.2f}% 초과</span>' if over
                    else f'<span class="sq-badge good">🟢 목표 {p.target_rate:.2f}% 이내</span>')
    month_label = f"{m[A.COL_MONTH].iloc[-1]} " if has_prev else ""
    cards = [
        ("총 생산량", f"{k['production']:,.1f}", "톤", month_label + _delta_html(prod_diff, "%", up_is_bad=False, digits=1), False),
        ("총 불량량", f"{k['defect']:,.1f}", "톤", f"결함 5종 합계 · {k['days']}일", False),
        ("평균 불량률", f"{k['rate']:.2f}", "%", target_badge + '<div style="margin-top:4px">' + month_label
         + _delta_html(rate_diff, "%p") + "</div>", over),
        ("이상치", f"{len(res.anomalies):,}", "건",
         f'<span class="sq-badge critical">🔴 경고 {n_warn}</span><span class="sq-badge caution">🟡 주의 {n_caution}</span>'
         if len(res.anomalies) else '<span class="sq-badge good">🟢 탐지된 이상치 없음</span>', n_warn > 0),
    ]
    items = "".join(
        f'<div class="sq-kpi{" is-alert" if alert else ""}"><div class="sq-kpi-label">{label}</div>'
        f'<div class="sq-kpi-value" title="{value}{unit}">{value}<span class="sq-kpi-unit">{unit}</span></div>'
        f'<div class="sq-kpi-sub">{sub}</div></div>'
        for label, value, unit, sub, alert in cards
    )
    html(f'<div class="sq-kpis">{items}</div>')


def comment_cards(res: AnalysisResult) -> None:
    if not res.comments:
        st.info("생성된 코멘트가 없습니다.")
        return
    items = "".join(
        f'<div class="sq-comment sev-{c.severity}"><span class="sq-comment-tag">{c.icon} {c.severity}</span>'
        f'<div class="sq-comment-text"><span class="sq-comment-cat">{escape(c.category)}</span>{escape(c.text)}</div></div>'
        for c in res.comments
    )
    html(f'<div class="sq-comments">{items}</div>')


def tab_summary(res: AnalysisResult) -> None:
    kpi_cards(res)
    section("자동 분석 코멘트", "규칙 기반으로 생성 · 요약 → 이슈 → 데이터 품질 순")
    comment_cards(res)
    left, right = st.columns([3, 2], gap="large")
    with left:
        section("일별 생산량 · 불량률", "빨간 점: 목표 불량률 초과일")
        plot(CH.production_rate_trend(res.daily, config.COL_DATE, res.params.target_rate, height=400), key="sum_trend")
    with right:
        section("결함 유형 순위", "전체 불량 중 점유율 · 누적")
        plot(CH.defect_ranked_bars(res.defect_total, height=200), key="sum_pareto")
        section("라인별 불량률")
        plot(CH.group_rate_bars(res.by_line, config.COL_LINE, res.params.target_rate), key="sum_lines")


# ---------------------------------------------------------------------------
# 탭: 일별·월별
# ---------------------------------------------------------------------------
def tab_trend(res: AnalysisResult) -> None:
    unit = st.segmented_control("집계 단위", ["일별", "월별"], default="일별", key="trend_unit",
                                label_visibility="collapsed") or "일별"
    target = res.params.target_rate
    if unit == "일별":
        section("일별 생산량 · 불량률", "위: 생산량, 아래: 불량률(빨간 점선 = 목표) — 두 지표는 축을 따로 씁니다")
        plot(CH.production_rate_trend(res.daily, config.COL_DATE, target, height=500), key="trend_daily")
        with st.expander("일별 집계 표", expanded=False):
            show_table(res.daily, height=400)
    else:
        section("월별 생산량 · 불량률", "위: 생산량, 아래: 불량률(빨간 점선 = 목표)")
        plot(CH.production_rate_trend(res.monthly, A.COL_MONTH, target, height=440), key="trend_monthly")
    section("월별 집계", "불량률 증감은 전월 대비 %p")
    show_table(res.monthly)


# ---------------------------------------------------------------------------
# 탭: 결함 분석
# ---------------------------------------------------------------------------
def tab_defect(res: AnalysisResult) -> None:
    left, right = st.columns(2, gap="large")
    with left:
        section("결함 유형 Pareto", "점유율 순위와 누적 점유율")
        plot(CH.defect_ranked_bars(res.defect_total, height=280), key="def_pareto")
    with right:
        section("라인 × 결함 구성비", "각 라인의 불량을 100%로 볼 때 결함별 비중")
        share = A.line_defect_matrix(res.data, share=True)
        plot(CH.line_defect_heatmap(share, res.line_defect, height=max(220, 52 * len(share) + 70)), key="def_heat")

    c1, c2 = st.columns([3, 1])
    with c1:
        section("결함 유형별 점유율 추이", "결함 유형마다 색이 고정되어 있습니다")
    with c2:
        unit = st.segmented_control("추이 단위", ["월별", "일별"], default="월별", key="share_unit",
                                    label_visibility="collapsed") or "월별"
    if unit == "월별":
        plot(CH.defect_share_trend(res.defect_monthly, "M", height=340), key="def_share_m")
    else:
        plot(CH.defect_share_trend(res.defect_daily, "D", height=340), key="def_share_d")

    left, right = st.columns(2, gap="large")
    with left:
        section("라인별 불량률")
        plot(CH.group_rate_bars(res.by_line, config.COL_LINE, res.params.target_rate), key="def_lines")
    with right:
        section("강종별 불량률")
        plot(CH.group_rate_bars(res.by_grade, config.COL_GRADE, res.params.target_rate), key="def_grades")

    with st.expander("결함 분석 표 보기"):
        shares = set(config.DEFECT_TYPES)
        st.markdown("**결함 유형별 점유율**")
        show_table(res.defect_total)
        st.markdown("**월별 결함 점유율(%)**")
        show_table(res.defect_monthly, percent_cols=shares, ton_cols={config.COL_TOTAL_DEFECT})
        st.markdown("**라인 × 결함 불량량(톤)**")
        show_table(res.line_defect)
        st.markdown("**라인별 집계**")
        show_table(res.by_line)
        st.markdown("**강종별 집계**")
        show_table(res.by_grade)


# ---------------------------------------------------------------------------
# 탭: 이상치
# ---------------------------------------------------------------------------
def tab_anomaly(res: AnalysisResult) -> None:
    an = res.anomalies
    if an.empty:
        st.success("탐지된 이상치가 없습니다. 사이드바 ④ 분석 기준에서 탐지 기준을 조정할 수 있습니다.", icon="🟢")
        return
    sev_opts = [config.SEVERITY_WARNING, config.SEVERITY_CAUTION]
    counts = an["심각도"].value_counts()
    c1, c2 = st.columns([1, 2])
    with c1:
        sel_sev = st.pills("심각도", sev_opts, selection_mode="multi", default=sev_opts, key="an_sev",
                           format_func=lambda s: f"{config.SEVERITY_ICONS[s]} {s} {counts.get(s, 0)}건")
    with c2:
        methods = list(dict.fromkeys(an["탐지방법"]))
        sel_methods = st.pills("탐지 방법", methods, selection_mode="multi", default=methods, key="an_method")
    view = an[an["심각도"].isin(sel_sev or []) & an["탐지방법"].isin(sel_methods or [])]

    line_counts = view["라인"].value_counts()
    lines = sorted(res.data[config.COL_LINE].unique())
    options = [ALL_PLANT] + lines
    default = (line_counts.drop("전체", errors="ignore").idxmax()
               if not line_counts.drop("전체", errors="ignore").empty else ALL_PLANT)
    target_line = st.selectbox(
        "차트에 표시할 라인", options, index=options.index(default), key="an_line",
        format_func=lambda o: f"{o} ({int(line_counts.get('전체' if o == ALL_PLANT else o, 0))}건)",
    )
    if target_line == ALL_PLANT:
        series = res.daily
        marks = view[view["라인"] == "전체"]
        target = res.params.target_rate
        sub = "공장 전체 일별 집계 — 목표 초과·결함 점유율 급변 탐지 단위"
    else:
        ld = A.line_daily_summary(res.data)
        series = ld[ld[config.COL_LINE] == target_line]
        marks = view[view["라인"] == target_line]
        target = None
        sub = "라인별 일별 집계 — IQR·Z-score·이동평균 급등 탐지 단위 (◆ 경고, ● 주의)"
    section(f"{target_line} 일별 추이와 이상치", sub)
    plot(CH.anomaly_series(series, marks, target, height=460), key="an_series")

    section("이상치 목록", f"{len(view)}건 표시 · 심각도(경고 우선) → 일자 순")
    if view.empty:
        st.info("선택한 조건에 해당하는 이상치가 없습니다.")
    else:
        show_table(view, height=min(460, 38 * len(view) + 40))


# ---------------------------------------------------------------------------
# 탭: 데이터
# ---------------------------------------------------------------------------
def tab_data(loaded: list[LoadedFile], merged: pd.DataFrame | None, cr: CleanResult | None) -> None:
    if cr is not None:
        section("데이터 정제 결과", cr.options_text)
        rules = cr.summary()
        warn_rules = {R_WARN_ZERO_PROD, R_WARN_OVER}
        badges = [f'<span class="sq-rule">입력 <b>{cr.input_rows:,}</b>행</span>',
                  f'<span class="sq-rule">정제 후 <b>{len(cr.data):,}</b>행</span>']
        badges += [f'<span class="sq-rule{" warn" if r in warn_rules else ""}">{escape(r)}<b>{n:,}</b></span>'
                   for r, n in zip(rules["규칙"], rules["건수"])]
        html(f'<div class="sq-rules">{"".join(badges)}</div>')

    view = st.segmented_control("보기", ["정제 데이터", "원본 데이터", "정제 로그"] if cr is not None else ["원본 데이터"],
                                default="정제 데이터" if cr is not None else "원본 데이터", key="data_view",
                                label_visibility="collapsed") or ("정제 데이터" if cr is not None else "원본 데이터")
    if view == "원본 데이터":
        for lf in loaded:
            info = f" · {lf.info}" if lf.info else ""
            section(lf.name, f"{len(lf.raw):,}행 × {lf.raw.shape[1]}열{info} · 상위 {config.PREVIEW_ROWS}행 미리보기")
            st.dataframe(lf.raw.head(config.PREVIEW_ROWS), width="stretch", hide_index=True)
            renamed = {k: v for k, v in lf.mapping.items() if k != v}
            if renamed:
                st.caption("컬럼 매핑: " + ", ".join(f"{k} → {v}" for k, v in renamed.items()))
        if merged is not None and len(loaded) > 1:
            st.caption(f"병합 결과: {len(loaded)}개 파일 · {len(merged):,}행")
    elif view == "정제 로그" and cr is not None:
        if cr.log.empty:
            st.success("정제할 항목이 없었습니다.", icon="✅")
        else:
            rule_opts = list(cr.summary()["규칙"])
            sel = st.multiselect("규칙", rule_opts, default=rule_opts, key="log_rules")
            log_view = cr.log[cr.log["규칙"].isin(sel)]
            preview_caption(len(log_view), "정제로그")
            show_table(log_view.head(TABLE_PREVIEW_ROWS), height=460)
    elif cr is not None:
        preview_caption(len(cr.data), "정제데이터")
        show_table(cr.data.head(TABLE_PREVIEW_ROWS).rename(columns={META_FILE: "원본파일", META_ROW: "원본행"}),
                   height=460)


def preview_caption(total: int, sheet: str) -> None:
    if total > TABLE_PREVIEW_ROWS:
        st.caption(f"전체 {total:,}행 중 상위 {TABLE_PREVIEW_ROWS:,}행만 표시합니다. "
                   f"전체 내용은 엑셀 리포트의 '{sheet}' 시트에 들어 있습니다.")


# ---------------------------------------------------------------------------
# 탭: 리포트
# ---------------------------------------------------------------------------
def tab_report(res: AnalysisResult) -> None:
    section("엑셀 리포트", "현재 필터와 분석 기준이 그대로 반영됩니다 · " + res.filter_text)
    sheets = "".join(
        f'<div class="sq-sheet"><div class="sq-sheet-n">{i}. {escape(s)}</div>'
        f'<div class="sq-sheet-d">{escape(SHEET_DESCRIPTIONS.get(s, ""))}</div></div>'
        for i, s in enumerate(REPORT_SHEETS, start=1)
    )
    html(f'<div class="sq-sheets">{sheets}</div>')
    name = report_filename(res.kpi["start"], res.kpi["end"])

    # 리포트는 버튼을 누를 때만 만든다. 탭 내용은 화면이 갱신될 때마다 모두 실행되므로,
    # 미리 만들어 두면 필터·기준을 바꿀 때마다 (10만 행 기준 십수 초) 리포트를 다시 생성하게 된다.
    def _build() -> bytes:
        return build_report(res).getvalue()

    c1, c2 = st.columns([1, 2])
    c1.download_button(
        "📥 엑셀 리포트 다운로드", _build, file_name=name, type="primary", width="stretch",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", on_click="ignore",
    )
    big = len(res.data) >= 20_000
    c2.caption(f"{name} · 버튼을 누르면 생성됩니다"
               + (f" (데이터 {len(res.data):,}행 — 생성에 시간이 걸릴 수 있습니다)" if big else ""))


# ---------------------------------------------------------------------------
# 메인
# ---------------------------------------------------------------------------
def main() -> None:
    inject_css()
    header_box = st.empty()
    uploads = sidebar_input()
    mode = st.session_state.get("mode")

    loaded: list[LoadedFile] = []
    if mode == "sample":
        loaded = [_sample_loaded()]
    elif mode == "upload" and uploads:
        for data, name, display, sheet in uploads:
            try:
                lf = _load(data, name, sheet)
                loaded.append(LoadedFile(display, lf.raw, dict(lf.mapping), list(lf.missing), lf.info))
            except LoaderError as exc:
                error_with_hint(str(exc))

    source = SAMPLE_NAME if mode == "sample" else (
        f"{loaded[0].name}" + (f" 외 {len(loaded) - 1}개" if len(loaded) > 1 else "") if loaded else None)

    if not loaded:
        render_header(header_box, None, None)
        render_empty_state()
        return

    # 캐시된 객체를 변경하지 않도록 사본에 수동 매핑 적용
    loaded = [LoadedFile(lf.name, lf.raw, dict(lf.mapping), list(lf.missing), lf.info) for lf in loaded]
    for i, lf in enumerate(loaded):
        if not lf.is_complete:
            manual_mapping(lf, i)

    merged: pd.DataFrame | None = None
    if all(lf.is_complete for lf in loaded):
        try:
            merged = merge_files(loaded)
        except LoaderError as exc:
            error_with_hint(str(exc))

    # 사이드바 섹션 순서를 고정하기 위해 자리를 먼저 만들고, 계산 순서(정제 옵션 → 정제 → 필터)대로 채운다
    filter_box, clean_box, criteria_box = st.sidebar.container(), st.sidebar.container(), st.sidebar.container()

    result: AnalysisResult | None = None
    clean_result: CleanResult | None = None
    if merged is not None:
        key = data_key(merged)
        dup, neg = clean_options(clean_box)
        try:
            clean_result = _clean(merged, key, dup, neg)
        except ValueError as exc:
            error_with_hint(f"데이터 정제 중 오류: {exc}")
        if clean_result is not None and clean_result.data.empty:
            st.error("**정제 후 남은 데이터가 없습니다.**\n\n💡 해결 방법: 일자 컬럼이 날짜 형식(예: 2026-10-01)인지 확인하세요.",
                     icon="❌")
        elif clean_result is not None:
            filters = filter_controls(filter_box, clean_result.data)
            params = analysis_controls(criteria_box)
            result = _analyze(merged, key, dup, neg, filters, params)

    render_header(header_box, result, source)

    tabs = st.tabs(["📋 요약", "📈 일별·월별", "🧩 결함 분석", "🚨 이상치", "🗂️ 데이터", "📥 리포트"])
    with tabs[4]:
        tab_data(loaded, merged, clean_result)

    if result is None or result.empty:
        msg = ("선택한 필터 조건에 해당하는 데이터가 없습니다. 사이드바 ② 필터를 조정해 주세요."
               if result is not None else "데이터 준비가 끝나면 결과가 표시됩니다.")
        for i in (0, 1, 2, 3, 5):
            with tabs[i]:
                if result is not None:
                    st.warning(msg, icon="⚠️")
                else:
                    st.info(msg, icon="ℹ️")
        return

    with tabs[0]:
        tab_summary(result)
    with tabs[1]:
        tab_trend(result)
    with tabs[2]:
        tab_defect(result)
    with tabs[3]:
        tab_anomaly(result)
    with tabs[5]:
        tab_report(result)


main()
