"""집계 (PRD F3).

입력은 cleaner.clean() 결과 데이터(총불량량 포함).
불량률은 반드시 가중 평균(Σ불량 ÷ Σ생산 × 100)으로 계산한다.
행별 불량률의 단순 평균을 쓰지 않으며, 생산량 0 이하인 행은 불량률 계산(분자·분모)에서 제외한다.
수치는 반올림하지 않고 반환한다 — 반올림은 화면·리포트 표시 단계에서 한다.
"""

from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd

from steelq import config

C = config
DEFECTS = C.DEFECT_TYPES
PROD, TOTAL, RATE = C.COL_PRODUCTION, C.COL_TOTAL_DEFECT, C.COL_DEFECT_RATE
COL_MONTH = "월"
COL_RATE_DIFF = "불량률 증감(%p)"
COL_PROD_DIFF = "생산량 증감(%)"
COL_SHARE = "점유율"
COL_CUM_SHARE = "누적 점유율"
COL_DEFECT_AMOUNT = "불량량"
COL_RATIO = "평균 대비(배)"
COL_DEFECT_TYPE = "결함 유형"
COL_PERIOD = "기간"
_RATE_DEFECT = "_불량률분자"


def rate(defect: float | pd.Series, production: float | pd.Series):
    """불량률(%) = 불량 ÷ 생산 × 100. 생산량 0 이하이면 NaN."""
    if isinstance(production, pd.Series):
        return defect / production.where(production > 0) * 100
    return defect / production * 100 if production and production > 0 else np.nan


def _with_rate_numerator(df: pd.DataFrame) -> pd.DataFrame:
    """생산량 0 이하 행의 불량은 불량률 분자에서 제외하기 위한 보조 컬럼."""
    return df.assign(**{_RATE_DEFECT: df[TOTAL].where(df[PROD] > 0, 0.0)})


def _aggregate(df: pd.DataFrame, by: str | list[str]) -> pd.DataFrame:
    work = _with_rate_numerator(df)
    sums = work.groupby(by, observed=True, sort=True)[[PROD, TOTAL, *DEFECTS, _RATE_DEFECT]].sum()
    sums[RATE] = rate(sums[_RATE_DEFECT], sums[PROD])
    return sums[[PROD, TOTAL, RATE, *DEFECTS]].reset_index()


# ---------------------------------------------------------------------------
# 필터 (F3-7)
# ---------------------------------------------------------------------------
def filter_data(
    df: pd.DataFrame,
    start: date | None = None,
    end: date | None = None,
    lines: list[str] | None = None,
    grades: list[str] | None = None,
) -> pd.DataFrame:
    """기간(양 끝 포함)·라인·강종 필터. 인자가 None이면 해당 조건 미적용, 빈 리스트면 결과 없음."""
    mask = pd.Series(True, index=df.index)
    if start is not None:
        mask &= df[C.COL_DATE] >= pd.Timestamp(start)
    if end is not None:
        mask &= df[C.COL_DATE] <= pd.Timestamp(end)
    if lines is not None:
        mask &= df[C.COL_LINE].isin(lines)
    if grades is not None:
        mask &= df[C.COL_GRADE].isin(grades)
    return df[mask].reset_index(drop=True)


# ---------------------------------------------------------------------------
# 집계
# ---------------------------------------------------------------------------
def overall_summary(df: pd.DataFrame) -> dict:
    """KPI: 총 생산량·총 불량량·평균 불량률·기간·일수·행수."""
    if df.empty:
        return {"production": 0.0, "defect": 0.0, "rate": np.nan, "start": None, "end": None, "days": 0, "rows": 0}
    work = _with_rate_numerator(df)
    return {
        "production": float(df[PROD].sum()),
        "defect": float(df[TOTAL].sum()),
        "rate": float(rate(work[_RATE_DEFECT].sum(), work[PROD].sum())),
        "start": df[C.COL_DATE].min(),
        "end": df[C.COL_DATE].max(),
        "days": int(df[C.COL_DATE].nunique()),
        "rows": len(df),
    }


def daily_summary(df: pd.DataFrame) -> pd.DataFrame:
    """일별 생산량·총불량량·불량률·결함 5종 (F3-1)."""
    return _aggregate(df, C.COL_DATE)


def line_daily_summary(df: pd.DataFrame) -> pd.DataFrame:
    """라인 × 일자 집계 (이상치 탐지 단위와 동일). 화면의 라인별 시계열 차트용."""
    return _aggregate(df, [C.COL_LINE, C.COL_DATE])


def monthly_summary(df: pd.DataFrame) -> pd.DataFrame:
    """월별 생산량·총불량량·불량률·전월 대비 증감 (F3-2). 월은 'YYYY-MM' 문자열."""
    work = df.assign(**{COL_MONTH: df[C.COL_DATE].dt.strftime("%Y-%m")})
    out = _aggregate(work, COL_MONTH)
    out.insert(4, COL_RATE_DIFF, out[RATE].diff())
    # 전월 생산량이 0이면 증감률이 무한대가 되므로 NaN(표시 '-')으로
    out.insert(5, COL_PROD_DIFF, (out[PROD].pct_change() * 100).replace([np.inf, -np.inf], np.nan))
    return out


def group_summary(df: pd.DataFrame, by: str) -> pd.DataFrame:
    """라인별/강종별 집계 (F3-3). 불량률 내림차순, 전체 평균 대비 배수 포함."""
    out = _aggregate(df, by)
    overall = overall_summary(df)["rate"]
    out.insert(4, COL_RATIO, out[RATE] / overall if overall and overall > 0 else np.nan)
    return out.sort_values(RATE, ascending=False, na_position="last").reset_index(drop=True)


def defect_share(df: pd.DataFrame) -> pd.DataFrame:
    """결함 유형별 불량량·점유율·누적 점유율 (전체 기간, Pareto 순) (F3-4)."""
    amounts = df[DEFECTS].sum().sort_values(ascending=False)
    total = amounts.sum()
    out = pd.DataFrame({COL_DEFECT_TYPE: amounts.index, COL_DEFECT_AMOUNT: amounts.to_numpy()})
    out[COL_SHARE] = out[COL_DEFECT_AMOUNT] / total * 100 if total > 0 else np.nan
    out[COL_CUM_SHARE] = out[COL_SHARE].cumsum()
    return out


def defect_share_trend(df: pd.DataFrame, freq: str = "M") -> pd.DataFrame:
    """기간별 결함 유형 점유율(%) 추이 (F3-5). freq: "D"(일) | "M"(월).

    컬럼: 기간, 총불량량, 결함 5종(점유율 %). 총불량량이 0인 기간의 점유율은 NaN.
    """
    if freq == "M":
        period = df[C.COL_DATE].dt.strftime("%Y-%m")
    elif freq == "D":
        period = df[C.COL_DATE]
    else:
        raise ValueError("freq는 'D' 또는 'M'이어야 합니다.")
    sums = df.groupby(period, sort=True)[DEFECTS].sum()
    total = sums.sum(axis=1)
    shares = sums.div(total.where(total > 0), axis=0) * 100
    shares.insert(0, TOTAL, total)
    shares.index.name = COL_PERIOD
    return shares.reset_index()


def line_defect_matrix(df: pd.DataFrame, share: bool = False) -> pd.DataFrame:
    """라인 × 결함 교차표 (F3-6). share=True면 라인 내 결함 구성비(%)."""
    sums = df.groupby(C.COL_LINE, sort=True)[DEFECTS].sum()
    total = sums.sum(axis=1)
    if share:
        sums = sums.div(total.where(total > 0), axis=0) * 100
    sums[TOTAL] = total
    return sums.reset_index()
