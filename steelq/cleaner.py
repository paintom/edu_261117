"""데이터 정제 (PRD F2-1 ~ F2-11).

입력은 loader.merge_files() 결과(표준 컬럼 + _원본파일, _원본행 메타 컬럼).
메타 컬럼이 없으면 자동으로 채운다. 완전 중복 판정에서 메타 컬럼은 제외한다.

처리 순서 (앞 단계 결과가 뒤 단계에 영향을 주므로 순서가 중요):
  표기 통일 → 숫자 변환 → 일자 파싱/제거 → 완전 중복 → 음수 → 키 중복
  → 라인·강종 결측 → 생산량 결측 보간 → 결함 결측 0 → 파생 컬럼·경고 플래그
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd

from steelq import config
from steelq.loader import META_FILE, META_ROW

C = config
DEFECTS = C.DEFECT_TYPES
KEY = C.KEY_COLUMNS

COL_WARNING = "경고"
CLEAN_LOG_COLUMNS = ["규칙", "원본파일", "원본행", "컬럼", "변경전", "변경후", "비고"]

# 정제 규칙 이름 (로그·요약·코멘트 C10에서 공유)
R_LABEL_NORMALIZE = "표기 통일"
R_NUMERIC = "숫자 변환"
R_NUMERIC_FAIL = "숫자 변환 실패"
R_DATE_DROP = "일자 결측·오류 제거"
R_FULL_DUP = "완전 중복 제거"
R_NEGATIVE = "음수 보정"
R_KEY_DUP = "키 중복 처리"
R_LABEL_MISSING = "라인·강종 결측 대체"
R_PROD_MISSING = "생산량 결측 보간"
R_DEFECT_MISSING = "결함 결측 0 대체"
R_WARN_ZERO_PROD = "경고: 생산량 0·불량 발생"
R_WARN_OVER = "경고: 불량량 > 생산량"
R_WARN_UNKNOWN_DUP = "경고: 미상 키 중복(병합 안 함)"
R_WARN_AMBIGUOUS_DATE = "경고: 일/월 순서 모호"

RULE_ORDER = [
    R_LABEL_NORMALIZE, R_NUMERIC, R_NUMERIC_FAIL, R_DATE_DROP, R_FULL_DUP, R_NEGATIVE, R_KEY_DUP,
    R_LABEL_MISSING, R_PROD_MISSING, R_DEFECT_MISSING, R_WARN_ZERO_PROD, R_WARN_OVER,
    R_WARN_UNKNOWN_DUP, R_WARN_AMBIGUOUS_DATE,
]
# 코멘트 C10 집계용 분류
DUPLICATE_RULES = {R_FULL_DUP, R_KEY_DUP}
MISSING_RULES = {R_DATE_DROP, R_LABEL_MISSING, R_PROD_MISSING, R_DEFECT_MISSING}
NEGATIVE_RULES = {R_NEGATIVE}


@dataclass
class CleanResult:
    data: pd.DataFrame                                   # 정제 완료 데이터 (+ 총불량량, 불량률, 경고)
    log: pd.DataFrame = field(default_factory=lambda: pd.DataFrame(columns=CLEAN_LOG_COLUMNS))
    input_rows: int = 0
    duplicate_key_strategy: str = C.DEFAULT_DUPLICATE_KEY_STRATEGY
    negative_strategy: str = C.DEFAULT_NEGATIVE_STRATEGY

    @property
    def options_text(self) -> str:
        return (f"키 중복: {C.DUPLICATE_KEY_STRATEGIES[self.duplicate_key_strategy]} / "
                f"음수: {C.NEGATIVE_STRATEGIES[self.negative_strategy]}")

    def summary(self) -> pd.DataFrame:
        """규칙별 처리 건수 (RULE_ORDER 순)."""
        if self.log.empty:
            return pd.DataFrame({"규칙": pd.Series(dtype=str), "건수": pd.Series(dtype=int)})
        counts = self.log.groupby("규칙").size()
        rules = [r for r in RULE_ORDER if r in counts.index]
        return pd.DataFrame({"규칙": rules, "건수": [int(counts[r]) for r in rules]})

    def count(self, rules: set[str] | str) -> int:
        rules = {rules} if isinstance(rules, str) else rules
        return int(self.log["규칙"].isin(rules).sum()) if not self.log.empty else 0


# ---------------------------------------------------------------------------
# 로그 헬퍼
# ---------------------------------------------------------------------------
def _fmt(value: Any) -> str:
    """로그 값은 문자열로 저장 (혼합 타입 컬럼이 화면 표시 시 깨지지 않도록)."""
    if value is None or (isinstance(value, float) and np.isnan(value)) or value is pd.NaT:
        return ""
    try:
        if pd.isna(value):
            return ""
    except (TypeError, ValueError):
        pass
    if isinstance(value, pd.Timestamp):
        return value.strftime("%Y-%m-%d")
    if isinstance(value, (float, np.floating)):
        return f"{value:g}"
    return str(value)


class _Log:
    def __init__(self) -> None:
        self.rows: list[dict] = []

    def add(self, rule: str, df: pd.DataFrame, idx, col: str | None, before, after, note: str = "") -> None:
        """idx 행들에 대해 로그 추가. before/after는 스칼라 또는 idx로 인덱싱 가능한 Series."""
        for i in idx:
            b = before.loc[i] if isinstance(before, pd.Series) else before
            a = after.loc[i] if isinstance(after, pd.Series) else after
            self.rows.append({
                "규칙": rule,
                "원본파일": str(df.at[i, META_FILE]),
                "원본행": int(df.at[i, META_ROW]),
                "컬럼": col or "",
                "변경전": _fmt(b),
                "변경후": _fmt(a),
                "비고": note(i) if callable(note) else note,
            })

    def frame(self) -> pd.DataFrame:
        if not self.rows:
            return pd.DataFrame(columns=CLEAN_LOG_COLUMNS)
        return pd.DataFrame(self.rows, columns=CLEAN_LOG_COLUMNS)


# ---------------------------------------------------------------------------
# 개별 단계
# ---------------------------------------------------------------------------
_WS_RE = re.compile(r"\s+")
# 공백류 + 제어문자(엑셀이 거부하는 \x00-\x1f, \x7f) 제거
_STRIP_RE = re.compile(r"[\s\x00-\x1f\x7f]+")


def _label_text(v: Any) -> str:
    """라벨을 문자열로. 1.0 처럼 정수인 숫자는 '1'로 (파일마다 1/1.0으로 갈라지지 않도록)."""
    if isinstance(v, (float, np.floating)) and float(v).is_integer():
        return str(int(v))
    return str(v)


def _normalize_labels(df: pd.DataFrame, log: _Log) -> None:
    """F2-11: 라인·강종 공백·제어문자 제거, 영문 대소문자 통일(대문자). 빈 문자열은 결측으로."""
    for col in [C.COL_LINE, C.COL_GRADE]:
        orig = df[col]
        notna = orig.notna()
        new = orig.astype(object).copy()
        text = orig[notna].map(_label_text).str.replace(_STRIP_RE, "", regex=True).str.upper()
        text = text.where(text != "", None)
        new[notna] = text
        changed = notna & (new.astype(str) != orig.map(_label_text)) & new.notna()
        log.add(R_LABEL_NORMALIZE, df, df.index[changed], col, orig, new)
        df[col] = new


def _to_numeric(df: pd.DataFrame, log: _Log) -> None:
    """F2-10: '1,250.5' 같은 문자열을 숫자로. 변환 불가 문자열은 결측 처리."""
    for col in C.NUMERIC_COLUMNS:
        orig = df[col]
        if pd.api.types.is_numeric_dtype(orig):
            df[col] = orig.astype(float)
            continue
        is_str = orig.map(lambda v: isinstance(v, str))
        cleaned = orig.where(~is_str, orig.astype(str).str.replace(",", "", regex=False).str.strip())
        cleaned = cleaned.where(~(is_str & (cleaned == "")), np.nan)
        num = pd.to_numeric(cleaned, errors="coerce").astype(float)
        # pandas 3에서는 문자열이 하나라도 섞이면 컬럼 전체가 문자열이 되므로,
        # 그대로 숫자로 읽히는 값('447.6')은 로그 대상에서 제외하고 콤마·공백 등이 있던 값만 기록
        plain = pd.to_numeric(orig.where(is_str), errors="coerce")
        converted = is_str & num.notna() & plain.isna()
        failed = is_str & num.isna() & (cleaned.notna())
        log.add(R_NUMERIC, df, df.index[converted], col, orig, num)
        log.add(R_NUMERIC_FAIL, df, df.index[failed], col, orig, None, "숫자로 해석 불가 → 결측 처리")
        df[col] = num


_KO_DATE_RE = re.compile(r"^\s*(\d{4})\s*년\s*(\d{1,2})\s*월\s*(\d{1,2})\s*일?\s*$")
_SLASH_YEAR_LAST_RE = re.compile(r"^\s*(\d{1,2})[/.\-](\d{1,2})[/.\-](\d{4})\s*$")
EXCEL_EPOCH = pd.Timestamp("1899-12-30")
EXCEL_SERIAL_RANGE = (20000, 80000)        # 1954 ~ 2119년
YYYYMMDD_RANGE = (19000101, 21001231)


def parse_dates(values: pd.Series) -> tuple[pd.Series, pd.Series]:
    """다양한 일자 표기를 Timestamp로 변환한다. 반환: (파싱 결과, 일/월 순서가 모호했던 행 여부).

    - 숫자(또는 숫자 문자열): 8자리 → YYYYMMDD, 20000~80000 → 엑셀 날짜 일련번호, 그 외 숫자 → 오류
      (숫자를 그대로 to_datetime에 넘기면 1970년 기준 나노초로 해석돼 전부 1970-01-01이 된다)
    - 'YYYY년 M월 D일' 한글 표기
    - 'MM/DD/YYYY'처럼 연도가 뒤에 오는 형식은 앞자리가 12 이하면 월/일 순으로 해석하고 모호 표시
    - 그 밖의 문자열·datetime은 pandas mixed 형식 파싱
    """
    out = pd.Series(pd.NaT, index=values.index, dtype="datetime64[ns]")
    ambiguous = pd.Series(False, index=values.index)
    notna = values.notna()
    is_dt = values.map(lambda v: isinstance(v, (pd.Timestamp, np.datetime64)) or hasattr(v, "year"))
    num = pd.to_numeric(values.where(notna & ~is_dt), errors="coerce")
    is_num = num.notna()
    whole = is_num & (num == num.round())

    ymd = whole & num.between(*YYYYMMDD_RANGE)
    if ymd.any():
        out[ymd] = pd.to_datetime(num[ymd].astype("int64").astype(str), format="%Y%m%d", errors="coerce")
    serial = is_num & num.between(*EXCEL_SERIAL_RANGE)
    if serial.any():
        out[serial] = EXCEL_EPOCH + pd.to_timedelta(num[serial].round(), unit="D")

    rest = notna & ~is_num
    if rest.any():
        text = values[rest].map(lambda v: v if not isinstance(v, str) else
                                _KO_DATE_RE.sub(lambda m: f"{m[1]}-{int(m[2]):02d}-{int(m[3]):02d}", v.strip()))
        out[rest] = pd.to_datetime(text, errors="coerce", format="mixed")
        def _amb(v):
            m = _SLASH_YEAR_LAST_RE.match(v) if isinstance(v, str) else None
            return bool(m) and int(m[1]) <= 12 and int(m[2]) <= 12 and m[1] != m[2]
        ambiguous[rest] = values[rest].map(_amb)
    return out.dt.normalize(), ambiguous & out.notna()


def _parse_dates(df: pd.DataFrame, log: _Log) -> pd.DataFrame:
    """F2-3: 일자 파싱. 결측·파싱 불가 행은 제거."""
    orig = df[C.COL_DATE]
    parsed, ambiguous = parse_dates(orig)
    bad = parsed.isna()
    log.add(R_DATE_DROP, df, df.index[bad], C.COL_DATE, orig, None,
            lambda i: "일자 결측" if pd.isna(orig.at[i]) else "일자 형식 오류")
    log.add(R_WARN_AMBIGUOUS_DATE, df, df.index[ambiguous], C.COL_DATE, orig, parsed,
            "MM/DD/YYYY(월/일)로 해석 — YYYY-MM-DD 형식 권장")
    df[C.COL_DATE] = parsed
    return df[~bad]


def _drop_full_duplicates(df: pd.DataFrame, log: _Log) -> pd.DataFrame:
    """F2-1: 모든 데이터 컬럼이 같은 행은 첫 행만 유지."""
    dup = df.duplicated(subset=C.REQUIRED_COLUMNS, keep="first")
    if dup.any():
        first_row = df.groupby(C.REQUIRED_COLUMNS, dropna=False, sort=False)[META_ROW].transform("first")
        log.add(R_FULL_DUP, df, df.index[dup], None, None, None, lambda i: f"원본행 {int(first_row.at[i])}과 동일")
    return df[~dup]


def _fix_negatives(df: pd.DataFrame, log: _Log, strategy: str) -> None:
    """F2-7: 음수 보정. zero=0, abs=절댓값, nan=결측(이후 결측 규칙 적용)."""
    note = C.NEGATIVE_STRATEGIES[strategy]
    for col in C.NUMERIC_COLUMNS:
        neg = df[col] < 0
        if not neg.any():
            continue
        before = df[col].copy()
        if strategy == "zero":
            df.loc[neg, col] = 0.0
        elif strategy == "abs":
            df.loc[neg, col] = before[neg].abs()
        else:
            df.loc[neg, col] = np.nan
        log.add(R_NEGATIVE, df, df.index[neg], col, before, df[col], note)


def _merge_key_duplicates(df: pd.DataFrame, log: _Log, strategy: str) -> pd.DataFrame:
    """F2-2: 일자+라인+강종이 같은 행 처리. 키에 결측이 있는 행은 대상에서 제외."""
    keyed = df[df[KEY].notna().all(axis=1)]
    dup_all = keyed.duplicated(subset=KEY, keep=False)
    if not dup_all.any():
        return df
    note_label = C.DUPLICATE_KEY_STRATEGIES[strategy]
    drop_idx: list = []
    for _, grp in keyed[dup_all].groupby(KEY, sort=False):
        keep = grp.index[-1]  # 마지막 행 위치에 결과를 남김
        if strategy == "sum":
            df.loc[keep, C.NUMERIC_COLUMNS] = grp[C.NUMERIC_COLUMNS].sum(min_count=1).to_numpy()
        elif strategy == "mean":
            df.loc[keep, C.NUMERIC_COLUMNS] = grp[C.NUMERIC_COLUMNS].mean().to_numpy()
        others = list(grp.index[:-1])
        drop_idx.extend(others)
        keep_row = int(df.at[keep, META_ROW])
        log.add(R_KEY_DUP, df, others, C.COL_PRODUCTION, grp[C.COL_PRODUCTION], df.at[keep, C.COL_PRODUCTION],
                f"{note_label} → 원본행 {keep_row}에 반영")
    return df.drop(index=drop_idx)


def _fill_labels(df: pd.DataFrame, log: _Log) -> None:
    """F2-4: 라인·강종 결측은 '미상'.

    라인/강종이 결측인 행은 실제로는 서로 다른 라인·강종일 수 있으므로 키 중복 병합(F2-2)에서 제외하고,
    '미상'으로 대체한 뒤 같은 키가 겹치면 병합하지 않은 채 경고로 남긴다 (조용히 합산되지 않도록).
    """
    had_missing = df[[C.COL_LINE, C.COL_GRADE]].isna().any(axis=1)
    for col in [C.COL_LINE, C.COL_GRADE]:
        miss = df[col].isna()
        log.add(R_LABEL_MISSING, df, df.index[miss], col, None, C.UNKNOWN_LABEL)
        df[col] = df[col].astype(object).where(~miss, C.UNKNOWN_LABEL)
    dup = had_missing & df.duplicated(subset=KEY, keep=False)
    log.add(R_WARN_UNKNOWN_DUP, df, df.index[dup], C.COL_LINE, None, None,
            "라인·강종 결측 행끼리 키가 겹침 — 서로 다른 라인일 수 있어 병합하지 않고 각각 집계")


def _fill_production(df: pd.DataFrame, log: _Log) -> None:
    """F2-5: 생산량 결측 → 동일 라인·강종 직전/직후 값 선형 보간 → 라인 중앙값 → 전체 중앙값 → 0."""
    col = C.COL_PRODUCTION
    miss = df[col].isna()
    if not miss.any():
        return
    order = df.sort_values([C.COL_LINE, C.COL_GRADE, C.COL_DATE]).index
    s = df.loc[order, col]
    groups = [df.loc[order, C.COL_LINE], df.loc[order, C.COL_GRADE]]
    interp = s.groupby(groups, sort=False).transform(lambda x: x.interpolate(limit_direction="both"))
    interp = interp.reindex(df.index)
    method = pd.Series("직전/직후 값 보간", index=df.index)

    line_median = df[col].groupby(df[C.COL_LINE]).transform("median")
    need = interp.isna()
    interp[need] = line_median[need]
    method[need] = "라인 중앙값"
    need = interp.isna()
    overall = df[col].median()
    interp[need] = 0.0 if pd.isna(overall) else overall
    method[need] = "전체 중앙값" if not pd.isna(overall) else "0 대체(참조값 없음)"

    df.loc[miss, col] = interp[miss].round(C.TON_DECIMALS)
    log.add(R_PROD_MISSING, df, df.index[miss], col, None, df[col], lambda i: method.at[i])


def _fill_defects(df: pd.DataFrame, log: _Log) -> None:
    """F2-6: 결함 불량량 결측 → 0 (미발생으로 간주)."""
    for col in DEFECTS:
        miss = df[col].isna()
        log.add(R_DEFECT_MISSING, df, df.index[miss], col, None, 0)
        df[col] = df[col].fillna(0.0)


def _derive(df: pd.DataFrame, log: _Log) -> None:
    """총불량량·불량률 계산과 F2-8, F2-9 경고 플래그."""
    df[C.COL_TOTAL_DEFECT] = df[DEFECTS].sum(axis=1)
    prod = df[C.COL_PRODUCTION]
    df[C.COL_DEFECT_RATE] = (df[C.COL_TOTAL_DEFECT] / prod.where(prod > 0) * 100)
    warn = pd.Series("", index=df.index, dtype=object)

    zero = (prod <= 0) & (df[C.COL_TOTAL_DEFECT] > 0)
    warn[zero] = "생산량 0·불량 발생(불량률 제외)"
    log.add(R_WARN_ZERO_PROD, df, df.index[zero], C.COL_TOTAL_DEFECT, df[C.COL_TOTAL_DEFECT], None,
            "불량률 계산에서 제외")

    over = (prod > 0) & (df[C.COL_TOTAL_DEFECT] > prod)
    warn[over] = "불량량 > 생산량(데이터 오류 의심)"
    log.add(R_WARN_OVER, df, df.index[over], C.COL_TOTAL_DEFECT, df[C.COL_TOTAL_DEFECT], None,
            "집계에는 포함")
    df[COL_WARNING] = warn


# ---------------------------------------------------------------------------
# 진입점
# ---------------------------------------------------------------------------
def clean(
    df: pd.DataFrame,
    duplicate_key_strategy: str = C.DEFAULT_DUPLICATE_KEY_STRATEGY,
    negative_strategy: str = C.DEFAULT_NEGATIVE_STRATEGY,
) -> CleanResult:
    """중복·결측·음수·타입을 보정하고 정제 로그를 남긴다.

    Args:
        duplicate_key_strategy: 일자+라인+강종 키 중복 처리 — "last" | "sum" | "mean"
        negative_strategy: 음수 처리 — "zero" | "abs" | "nan"
    """
    if duplicate_key_strategy not in C.DUPLICATE_KEY_STRATEGIES:
        raise ValueError(f"알 수 없는 키 중복 처리 방식: {duplicate_key_strategy}")
    if negative_strategy not in C.NEGATIVE_STRATEGIES:
        raise ValueError(f"알 수 없는 음수 처리 방식: {negative_strategy}")
    missing = [c for c in C.REQUIRED_COLUMNS if c not in df.columns]
    if missing:
        raise ValueError(f"필수 컬럼 누락: {', '.join(missing)}")

    work = df.copy()
    if META_FILE not in work.columns:
        work.insert(0, META_FILE, "")
    if META_ROW not in work.columns:
        work.insert(1, META_ROW, np.arange(len(work)) + 2)
    work = work[[META_FILE, META_ROW, *C.REQUIRED_COLUMNS]].reset_index(drop=True)
    for col in [C.COL_LINE, C.COL_GRADE]:
        work[col] = work[col].astype(object)
    log = _Log()

    _normalize_labels(work, log)
    _to_numeric(work, log)
    work = _parse_dates(work, log)
    work = _drop_full_duplicates(work, log)
    _fix_negatives(work, log, negative_strategy)
    work = _merge_key_duplicates(work, log, duplicate_key_strategy)
    _fill_labels(work, log)
    _fill_production(work, log)
    _fill_defects(work, log)
    _derive(work, log)

    work[C.COL_LINE] = work[C.COL_LINE].astype(str)
    work[C.COL_GRADE] = work[C.COL_GRADE].astype(str)
    work = work.sort_values([C.COL_DATE, C.COL_LINE, C.COL_GRADE], kind="stable").reset_index(drop=True)
    return CleanResult(data=work, log=log.frame(), input_rows=len(df),
                       duplicate_key_strategy=duplicate_key_strategy, negative_strategy=negative_strategy)
