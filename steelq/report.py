"""엑셀 리포트 생성 (PRD F7).

openpyxl 네이티브 차트를 포함한 8개 시트를 만들고 BytesIO로만 반환한다
(업로드·결과 데이터를 디스크에 쓰지 않는다).

수치 표기 규칙: 내부 데이터의 불량률·점유율은 '퍼센트 숫자'(0.79 = 0.79%)이므로,
엑셀에는 100으로 나눠 저장하고 서식 '0.00%'를 적용한다.
"""

from __future__ import annotations

import io
import unicodedata
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd
from openpyxl import Workbook
from openpyxl.chart import BarChart, LineChart, PieChart, Reference
from openpyxl.chart.label import DataLabelList
from openpyxl.chart.series import DataPoint
from openpyxl.formatting.rule import CellIsRule
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.cell.cell import ILLEGAL_CHARACTERS_RE
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.worksheet import Worksheet

from steelq import aggregator as A
from steelq import config
from steelq.loader import META_FILE, META_ROW
from steelq.pipeline import AnalysisResult

C = config
REPORT_SHEETS = ["요약", "일별집계", "월별집계", "결함분석", "라인·강종", "이상치", "정제로그", "정제데이터"]
KST = timezone(timedelta(hours=9), "KST")  # Windows에는 IANA tz DB가 없어 고정 오프셋 사용

# 스타일
NAVY = C.BRAND_NAVY.lstrip("#")
HEADER_FILL = PatternFill("solid", fgColor=NAVY)
HEADER_FONT = Font(bold=True, color="FFFFFF")
TITLE_FONT = Font(bold=True, size=16, color=NAVY)
SECTION_FONT = Font(bold=True, size=12, color=NAVY)
LABEL_FONT = Font(bold=True)
OVER_TARGET_FILL = PatternFill("solid", fgColor=C.SEVERITY_COLORS[C.SEVERITY_WARNING][0])
OVER_TARGET_FONT = Font(color=C.SEVERITY_COLORS[C.SEVERITY_WARNING][1])

FMT_TON = "#,##0.0"
FMT_PCT = "0.00%"
FMT_PP = '+0.00"%p";-0.00"%p";0.00"%p"'
FMT_PCT_CHANGE = '+0.0"%";-0.0"%";0.0"%"'
FMT_RATIO = '0.00"배"'
FMT_DATE = "yyyy-mm-dd"
FMT_INT = "#,##0"
FMT_DEC2 = "0.00"

PERCENT_COLUMNS = {C.COL_DEFECT_RATE, A.COL_SHARE, A.COL_CUM_SHARE}
TON_COLUMNS = {C.COL_PRODUCTION, C.COL_TOTAL_DEFECT, A.COL_DEFECT_AMOUNT, *C.DEFECT_TYPES}
COLUMN_FORMATS = {
    C.COL_DATE: FMT_DATE,
    A.COL_RATE_DIFF: FMT_PP,
    A.COL_PROD_DIFF: FMT_PCT_CHANGE,
    A.COL_RATIO: FMT_RATIO,
    "값": FMT_DEC2,
    "원본행": FMT_INT,
    "건수": FMT_INT,
}


def report_filename(start, end) -> str:
    return f"SteelQ_Report_{pd.Timestamp(start):%Y%m%d}_{pd.Timestamp(end):%Y%m%d}.xlsx"


# ---------------------------------------------------------------------------
# 표 쓰기
# ---------------------------------------------------------------------------
def _cell_value(v):
    if v is None or v is pd.NaT:
        return None
    if isinstance(v, str):
        # 엑셀이 거부하는 제어문자 제거 (그대로 쓰면 IllegalCharacterError로 리포트 전체가 실패)
        return ILLEGAL_CHARACTERS_RE.sub("", v)
    if isinstance(v, (float, np.floating)):
        return None if np.isnan(v) else float(v)
    if isinstance(v, np.integer):
        return int(v)
    if isinstance(v, pd.Timestamp):
        return v.to_pydatetime()
    return v


def write_table(
    ws: Worksheet,
    df: pd.DataFrame,
    row: int = 1,
    col: int = 1,
    percent_cols: set[str] | None = None,
    ton_cols: set[str] | None = None,
) -> tuple[int, int]:
    """df를 (row, col)부터 헤더 포함으로 쓴다. 반환: (헤더 행, 마지막 데이터 행).

    percent_cols의 값은 100으로 나눠 '0.00%' 서식, ton_cols는 '#,##0.0' 서식.
    """
    percent_cols = PERCENT_COLUMNS if percent_cols is None else percent_cols
    ton_cols = TON_COLUMNS if ton_cols is None else ton_cols
    for j, name in enumerate(df.columns):
        c = ws.cell(row=row, column=col + j, value=_cell_value(str(name)))
        c.fill, c.font = HEADER_FILL, HEADER_FONT
        c.alignment = Alignment(horizontal="center", vertical="center")

    formats = []
    for name in df.columns:
        if name in percent_cols:
            formats.append((FMT_PCT, 0.01))
        elif name in ton_cols:
            formats.append((FMT_TON, None))
        else:
            formats.append((COLUMN_FORMATS.get(name), None))

    values = df.to_numpy(dtype=object)
    for i, rec in enumerate(values, start=1):
        r = row + i
        for j, v in enumerate(rec):
            v = _cell_value(v)
            if v is None or v == "":
                continue  # 빈 셀은 만들지 않는다 (대용량에서 저장 시간 절감)
            fmt, scale = formats[j]
            if scale is not None and isinstance(v, (int, float)):
                v = v * scale
            c = ws.cell(row=r, column=col + j, value=v)
            if fmt:
                c.number_format = fmt
    return row, row + len(df)


def _section(ws: Worksheet, title: str, row: int) -> int:
    ws.cell(row=row, column=1, value=title).font = SECTION_FONT
    return row + 1


def _col_index(df: pd.DataFrame, name: str, start_col: int = 1) -> int:
    return start_col + list(df.columns).index(name)


def _highlight_over_target(ws: Worksheet, col: int, first: int, last: int, target: float) -> None:
    if last < first:
        return
    letter = get_column_letter(col)
    ws.conditional_formatting.add(
        f"{letter}{first}:{letter}{last}",
        CellIsRule(operator="greaterThan", formula=[str(target / 100)], fill=OVER_TARGET_FILL, font=OVER_TARGET_FONT),
    )


def _text_width(v) -> int:
    if v is None:
        return 0
    if isinstance(v, float):
        s = f"{v:,.2f}"
    elif isinstance(v, datetime):
        s = "2026-01-01"
    else:
        s = str(v)
    return sum(2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1 for ch in s)


def _autosize(ws: Worksheet, max_width: int = 50, sample_rows: int = 2000) -> None:
    """열 너비 자동 조정 (대용량 시트는 앞부분 sample_rows행만 측정)."""
    widths: dict[int, int] = {}
    for r_idx, row in enumerate(ws.iter_rows(values_only=True)):
        if r_idx > sample_rows:
            break
        for j, v in enumerate(row, start=1):
            widths[j] = max(widths.get(j, 0), _text_width(v))
    for j, w in widths.items():
        ws.column_dimensions[get_column_letter(j)].width = min(max(w + 2, 8), max_width)


def _show_axes(*axes) -> None:
    # openpyxl 3.1 기본값에서는 최신 Excel이 축을 숨기므로 명시적으로 표시
    for ax in axes:
        ax.delete = False


def _no_overlay(chart) -> None:
    """제목·범례·축 제목이 그래프 영역과 겹치지 않도록 overlay를 끈다 (Excel 렌더링 확인 결과 기본값은 겹침)."""
    if chart.title is not None:
        chart.title.overlay = False
    if chart.legend is not None:
        chart.legend.overlay = False
    for ax_name in ("x_axis", "y_axis"):
        ax = getattr(chart, ax_name, None)
        if ax is not None and ax.title is not None:
            ax.title.overlay = False


# ---------------------------------------------------------------------------
# 차트
# ---------------------------------------------------------------------------
def _hex(color: str) -> str:
    return color.lstrip("#").upper()


def _production_chart(ws: Worksheet, title: str, cat_col: int, prod_col: int,
                      header: int, last: int, cat_format: str | None = None) -> BarChart:
    """생산량 막대 차트. 불량률과 축을 섞지 않도록 별도 차트로 만든다(보조축 사용 안 함)."""
    bar = BarChart()
    bar.type = "col"
    bar.title = title
    bar.y_axis.title = "생산량(톤)"
    bar.y_axis.number_format = "#,##0"
    bar.add_data(Reference(ws, min_col=prod_col, min_row=header, max_row=last), titles_from_data=True)
    bar.set_categories(Reference(ws, min_col=cat_col, min_row=header + 1, max_row=last))
    if cat_format:
        bar.x_axis.number_format = cat_format
    bar.gapWidth = 50
    bar.series[0].graphicalProperties.solidFill = _hex(C.SERIES_PRIMARY)
    bar.series[0].graphicalProperties.line.noFill = True
    bar.y_axis.scaling.min = 0
    bar.legend = None
    _show_axes(bar.x_axis, bar.y_axis)
    _no_overlay(bar)
    bar.width, bar.height = 26, 8
    return bar


def _rate_chart(ws: Worksheet, title: str, cat_col: int, rate_col: int,
                header: int, last: int, cat_format: str | None = None) -> LineChart:
    """불량률 꺾은선 차트 (목표 초과 셀은 표의 조건부 서식으로 강조)."""
    line = LineChart()
    line.title = title
    line.y_axis.title = "불량률"
    line.y_axis.number_format = "0.00%"
    line.add_data(Reference(ws, min_col=rate_col, min_row=header, max_row=last), titles_from_data=True)
    line.set_categories(Reference(ws, min_col=cat_col, min_row=header + 1, max_row=last))
    if cat_format:
        line.x_axis.number_format = cat_format
    s = line.series[0]
    s.graphicalProperties.line.solidFill = _hex(C.BRAND_NAVY)
    s.graphicalProperties.line.width = 22000
    s.smooth = False
    line.y_axis.scaling.min = 0
    line.legend = None
    _show_axes(line.x_axis, line.y_axis)
    _no_overlay(line)
    line.width, line.height = 26, 8
    return line


# ---------------------------------------------------------------------------
# 시트
# ---------------------------------------------------------------------------
def _sheet_summary(ws: Worksheet, res: AnalysisResult, generated: datetime) -> None:
    kpi, p = res.kpi, res.params
    ws["A1"] = "SteelQ 생산·품질 리포트"
    ws["A1"].font = TITLE_FONT
    info = [
        ("분석 기간", f"{pd.Timestamp(kpi['start']):%Y-%m-%d} ~ {pd.Timestamp(kpi['end']):%Y-%m-%d} ({kpi['days']}일)"),
        ("생성 일시", generated.strftime("%Y-%m-%d %H:%M KST")),
        ("필터", res.filter_text),
        ("목표 불량률", p.target_rate / 100),
        ("데이터 행 수", kpi["rows"]),
        ("정제 옵션", res.clean.options_text),
    ]
    row = 3
    for label, value in info:
        ws.cell(row=row, column=1, value=label).font = LABEL_FONT
        c = ws.cell(row=row, column=2, value=value)
        if label == "목표 불량률":
            c.number_format = FMT_PCT
        elif label == "데이터 행 수":
            c.number_format = FMT_INT
        c.alignment = Alignment(horizontal="left")
        row += 1

    row = _section(ws, "핵심 지표", row + 1)
    n_warn = int((res.anomalies["심각도"] == C.SEVERITY_WARNING).sum()) if not res.anomalies.empty else 0
    rate_diff = res.monthly[A.COL_RATE_DIFF].iloc[-1] if len(res.monthly) >= 2 else np.nan
    kpis = [
        ("총 생산량(톤)", kpi["production"], FMT_TON),
        ("총 불량량(톤)", kpi["defect"], FMT_TON),
        ("평균 불량률", kpi["rate"] / 100 if not np.isnan(kpi["rate"]) else None, FMT_PCT),
        ("최근월 불량률 전월 대비", None if np.isnan(rate_diff) else float(rate_diff), FMT_PP),
        ("이상치 건수", len(res.anomalies), FMT_INT),
        ("  └ 경고", n_warn, FMT_INT),
    ]
    for j, h in enumerate(["항목", "값"], start=1):
        c = ws.cell(row=row, column=j, value=h)
        c.fill, c.font = HEADER_FILL, HEADER_FONT
    for label, value, fmt in kpis:
        row += 1
        ws.cell(row=row, column=1, value=label)
        c = ws.cell(row=row, column=2, value=value if value is not None else "-")
        c.number_format = fmt
        c.alignment = Alignment(horizontal="right")
        if label == "평균 불량률" and value is not None and kpi["rate"] > p.target_rate:
            c.fill, c.font = OVER_TARGET_FILL, OVER_TARGET_FONT

    row = _section(ws, "자동 분석 코멘트", row + 2)
    for j, h in enumerate(["구분", "심각도", "코멘트"], start=1):
        c = ws.cell(row=row, column=j, value=h)
        c.fill, c.font = HEADER_FILL, HEADER_FONT
    for cm in res.comments:
        row += 1
        bg, fg = C.SEVERITY_COLORS[cm.severity]
        ws.cell(row=row, column=1, value=cm.category)
        sev = ws.cell(row=row, column=2, value=f"{cm.icon} {cm.severity}")
        sev.fill, sev.font = PatternFill("solid", fgColor=bg), Font(bold=True, color=fg)
        sev.alignment = Alignment(horizontal="center")
        ws.cell(row=row, column=3, value=cm.text).alignment = Alignment(wrap_text=True, vertical="center")
    ws.column_dimensions["A"].width = 24
    ws.column_dimensions["B"].width = 38
    ws.column_dimensions["C"].width = 100


def _sheet_daily(ws: Worksheet, res: AnalysisResult) -> None:
    df = res.daily
    header, last = write_table(ws, df)
    ws.freeze_panes = "B2"
    _autosize(ws)
    rate_col = _col_index(df, C.COL_DEFECT_RATE)
    _highlight_over_target(ws, rate_col, header + 1, last, res.params.target_rate)
    if last > header:
        anchor_col = get_column_letter(len(df.columns) + 2)
        ws.add_chart(_production_chart(ws, "일별 생산량", 1, _col_index(df, C.COL_PRODUCTION), header, last,
                                       cat_format="mm-dd"), f"{anchor_col}2")
        ws.add_chart(_rate_chart(ws, "일별 불량률 (목표 초과일은 표에서 빨간색)", 1, rate_col, header, last,
                                 cat_format="mm-dd"), f"{anchor_col}19")


def _sheet_monthly(ws: Worksheet, res: AnalysisResult) -> None:
    df = res.monthly
    header, last = write_table(ws, df)
    ws.freeze_panes = "B2"
    _autosize(ws)
    rate_col = _col_index(df, C.COL_DEFECT_RATE)
    _highlight_over_target(ws, rate_col, header + 1, last, res.params.target_rate)
    if last > header:
        prod = _production_chart(ws, "월별 생산량", 1, _col_index(df, C.COL_PRODUCTION), header, last)
        rate = _rate_chart(ws, "월별 불량률", 1, rate_col, header, last)
        for ch in (prod, rate):
            ch.width, ch.height = 15, 8
        ws.add_chart(prod, f"A{last + 3}")
        ws.add_chart(rate, f"G{last + 3}")


def _sheet_defect(ws: Worksheet, res: AnalysisResult) -> None:
    row = _section(ws, "결함 유형별 불량량·점유율 (Pareto)", 1)
    total = res.defect_total
    header, last = write_table(ws, total, row=row)
    if last > header and total[A.COL_DEFECT_AMOUNT].sum() > 0:
        pie = PieChart()
        pie.title = "결함 유형별 점유율"
        pie.add_data(Reference(ws, min_col=2, min_row=header, max_row=last), titles_from_data=True)
        pie.set_categories(Reference(ws, min_col=1, min_row=header + 1, max_row=last))
        pie.dataLabels = DataLabelList()
        pie.dataLabels.showPercent = True
        for attr in ("showVal", "showSerName", "showCatName", "showLegendKey"):
            setattr(pie.dataLabels, attr, False)
        for i, defect in enumerate(total[A.COL_DEFECT_TYPE]):
            pt = DataPoint(idx=i)
            pt.graphicalProperties.solidFill = _hex(C.DEFECT_COLORS.get(defect, C.SERIES_PRIMARY))
            pt.graphicalProperties.line.solidFill = "FFFFFF"
            pie.series[0].dPt.append(pt)
        pie.legend.position = "r"
        _no_overlay(pie)
        pie.width, pie.height = 14, 9
        ws.add_chart(pie, "G2")

    monthly = res.defect_monthly
    shares = set(C.DEFECT_TYPES)
    row = _section(ws, "월별 결함 점유율 추이", max(last + 3, 20))
    m_header, m_last = write_table(ws, monthly, row=row, percent_cols=shares,
                                   ton_cols={C.COL_TOTAL_DEFECT})
    if m_last > m_header:
        bar = BarChart()
        bar.type = "col"
        bar.grouping = "percentStacked"
        bar.overlap = 100
        bar.title = "월별 결함 점유율 추이"
        first_defect = _col_index(monthly, C.DEFECT_TYPES[0])
        bar.add_data(Reference(ws, min_col=first_defect, max_col=first_defect + len(C.DEFECT_TYPES) - 1,
                               min_row=m_header, max_row=m_last), titles_from_data=True)
        bar.set_categories(Reference(ws, min_col=1, min_row=m_header + 1, max_row=m_last))
        bar.y_axis.number_format = "0%"
        for s, defect in zip(bar.series, C.DEFECT_TYPES):
            s.graphicalProperties.solidFill = _hex(C.DEFECT_COLORS[defect])
            s.graphicalProperties.line.solidFill = "FFFFFF"
        bar.legend.position = "b"
        _show_axes(bar.x_axis, bar.y_axis)
        _no_overlay(bar)
        bar.width, bar.height = 14, 9
        ws.add_chart(bar, f"J{row}")

    row = _section(ws, "일별 결함 점유율 추이", max(m_last + 3, row + 20))
    daily = res.defect_daily.rename(columns={A.COL_PERIOD: C.COL_DATE})
    write_table(ws, daily, row=row, percent_cols=shares, ton_cols={C.COL_TOTAL_DEFECT})
    _autosize(ws)


def _sheet_group(ws: Worksheet, res: AnalysisResult) -> None:
    target = res.params.target_rate
    row = _section(ws, "라인별 집계", 1)
    df = res.by_line
    header, last = write_table(ws, df, row=row)
    rate_col = _col_index(df, C.COL_DEFECT_RATE)
    _highlight_over_target(ws, rate_col, header + 1, last, target)
    if last > header:
        bar = BarChart()
        bar.type = "bar"
        bar.title = "라인별 불량률"
        bar.add_data(Reference(ws, min_col=rate_col, min_row=header, max_row=last), titles_from_data=True)
        bar.set_categories(Reference(ws, min_col=1, min_row=header + 1, max_row=last))
        bar.x_axis.scaling.orientation = "maxMin"  # 표와 같은 순서(불량률 높은 라인이 위)
        bar.y_axis.number_format = "0.00%"
        bar.legend = None
        bar.series[0].graphicalProperties.solidFill = _hex(C.SERIES_PRIMARY)
        for i, r in enumerate(df[C.COL_DEFECT_RATE]):
            if pd.notna(r) and r > target:
                pt = DataPoint(idx=i)
                pt.graphicalProperties.solidFill = _hex(C.SEVERITY_MARK_COLORS[C.SEVERITY_WARNING])
                bar.series[0].dPt.append(pt)
        bar.y_axis.scaling.min = 0
        _show_axes(bar.x_axis, bar.y_axis)
        _no_overlay(bar)
        bar.width, bar.height = 14, 7
        ws.add_chart(bar, f"{get_column_letter(len(df.columns) + 2)}1")

    row = _section(ws, "강종별 집계", max(last + 3, 17))
    g = res.by_grade
    g_header, g_last = write_table(ws, g, row=row)
    _highlight_over_target(ws, _col_index(g, C.COL_DEFECT_RATE), g_header + 1, g_last, target)

    row = _section(ws, "라인 × 결함 유형 불량량(톤)", g_last + 3)
    write_table(ws, res.line_defect, row=row)
    _autosize(ws)


def _sheet_anomalies(ws: Worksheet, res: AnalysisResult) -> None:
    df = res.anomalies
    if df.empty:
        ws["A1"] = "탐지된 이상치가 없습니다."
        return
    header, last = write_table(ws, df)
    ws.freeze_panes = "A2"
    sev_col = _col_index(df, "심각도")
    for r in range(header + 1, last + 1):
        c = ws.cell(row=r, column=sev_col)
        bg, fg = C.SEVERITY_COLORS.get(c.value, ("FFFFFF", "000000"))
        c.fill, c.font = PatternFill("solid", fgColor=bg), Font(bold=True, color=fg)
    _autosize(ws)


def _sheet_clean_log(ws: Worksheet, res: AnalysisResult) -> None:
    row = _section(ws, "정제 규칙별 처리 건수 (필터 적용 전 전체 데이터 기준)", 1)
    summary = res.clean.summary()
    meta = pd.DataFrame({"규칙": ["입력 행 수", "정제 후 행 수"],
                         "건수": [res.clean.input_rows, len(res.clean.data)]})
    _, last = write_table(ws, pd.concat([meta, summary], ignore_index=True), row=row)
    row = _section(ws, "상세 내역", last + 3)
    if res.clean.log.empty:
        ws.cell(row=row, column=1, value="정제 대상이 없었습니다.")
    else:
        h, _ = write_table(ws, res.clean.log, row=row)
        ws.freeze_panes = f"A{h + 1}"
    _autosize(ws)


def _sheet_clean_data(ws: Worksheet, res: AnalysisResult) -> None:
    df = res.data.rename(columns={META_FILE: "원본파일", META_ROW: "원본행"})
    if df["원본파일"].nunique() <= 1:
        df = df.drop(columns="원본파일")  # 파일이 하나면 모든 행이 같은 값 — 생략해 크기·시간 절감
    write_table(ws, df)
    ws.freeze_panes = "A2"
    ws.auto_filter.ref = ws.dimensions
    _autosize(ws)


# ---------------------------------------------------------------------------
# 진입점
# ---------------------------------------------------------------------------
def build_report(res: AnalysisResult, generated: datetime | None = None) -> io.BytesIO:
    """분석 결과로 8개 시트 엑셀 리포트를 만들어 BytesIO로 반환."""
    if res.empty:
        raise ValueError("필터 결과 데이터가 없어 리포트를 만들 수 없습니다.")
    generated = generated or datetime.now(KST)
    wb = Workbook()
    sheets = {name: (wb.active if i == 0 else wb.create_sheet()) for i, name in enumerate(REPORT_SHEETS)}
    for name, ws in sheets.items():
        ws.title = name

    _sheet_summary(sheets["요약"], res, generated)
    _sheet_daily(sheets["일별집계"], res)
    _sheet_monthly(sheets["월별집계"], res)
    _sheet_defect(sheets["결함분석"], res)
    _sheet_group(sheets["라인·강종"], res)
    _sheet_anomalies(sheets["이상치"], res)
    _sheet_clean_log(sheets["정제로그"], res)
    _sheet_clean_data(sheets["정제데이터"], res)

    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf

