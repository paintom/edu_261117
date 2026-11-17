"""이상치 탐지 (PRD F4-1 ~ F4-5).

탐지 단위:
- F4-1 IQR, F4-2 Z-score, F4-3 이동평균 급등: 라인별 일별 집계(라인-일자). 강종은 '전체'.
- F4-4 목표 초과, F4-5 결함 점유율 급변: 공장 전체 일별 집계. 라인·강종은 '전체'.
  (목표 초과를 라인별로 보면 원래 불량률이 높은 라인이 매일 걸려 이상치 목록이 의미를 잃는다.
   라인 간 수준 차이는 코멘트 C6이 다룬다.)
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from steelq import aggregator, config

C = config
DEFECTS = C.DEFECT_TYPES
PROD, TOTAL, RATE = C.COL_PRODUCTION, C.COL_TOTAL_DEFECT, C.COL_DEFECT_RATE
ALL = "전체"

ANOMALY_COLUMNS = ["일자", "라인", "강종", "지표", "값", "기준", "탐지방법", "심각도"]
METRIC_RATE = "불량률(%)"
METRIC_PROD = "생산량(톤)"

M_IQR = "IQR"
M_Z = "Z-score"
M_MA = "이동평균 급등"
M_TARGET = "목표 초과"
M_SHARE = "점유율 급변"


def _rows(frame: pd.DataFrame, line, metric, value_col, basis, method, severity) -> list[dict]:
    out = []
    for i in frame.index:
        out.append({
            "일자": frame.at[i, C.COL_DATE],
            "라인": line if isinstance(line, str) else frame.at[i, C.COL_LINE],
            "강종": ALL,
            "지표": metric if isinstance(metric, str) else metric(i),
            "값": float(frame.at[i, value_col]),
            "기준": basis(i),
            "탐지방법": method,
            "심각도": severity(i),
        })
    return out


def _line_daily(df: pd.DataFrame) -> pd.DataFrame:
    return aggregator._aggregate(df, [C.COL_LINE, C.COL_DATE]).sort_values([C.COL_LINE, C.COL_DATE])


def detect_iqr(line_daily: pd.DataFrame, k: float) -> list[dict]:
    """F4-1: 라인별 일별 불량률이 Q3 + k×IQR 초과."""
    out = []
    for line, g in line_daily.groupby(C.COL_LINE):
        r = g[RATE].dropna()
        if len(r) < 4:
            continue
        q1, q3 = r.quantile([0.25, 0.75])
        iqr = q3 - q1
        upper = q3 + k * iqr
        severe = q3 + C.IQR_WARNING_MULTIPLIER * iqr
        hit = g[g[RATE] > upper]
        out += _rows(hit, line, METRIC_RATE, RATE,
                     lambda i, u=upper, kk=k: f"> {u:.2f} (Q3+{kk:g}×IQR)", M_IQR,
                     lambda i, h=hit, s=severe: C.SEVERITY_WARNING if h.at[i, RATE] > s else C.SEVERITY_CAUTION)
    return out


def detect_zscore(line_daily: pd.DataFrame, threshold: float) -> list[dict]:
    """F4-2: 라인별 일별 생산량 |z| ≥ threshold."""
    out = []
    for line, g in line_daily.groupby(C.COL_LINE):
        x = g[PROD]
        std = x.std(ddof=0)
        if len(x) < 4 or not std or np.isnan(std):
            continue
        z = (x - x.mean()) / std
        hit = g[z.abs() >= threshold]
        out += _rows(hit, line, METRIC_PROD, PROD,
                     lambda i, zz=z: f"|z|={abs(zz.at[i]):.1f} ≥ {threshold:g}", M_Z,
                     lambda i, zz=z: C.SEVERITY_WARNING if abs(zz.at[i]) >= threshold + C.ZSCORE_WARNING_MARGIN
                     else C.SEVERITY_CAUTION)
    return out


def detect_moving_average(line_daily: pd.DataFrame, window: int, multiplier: float) -> list[dict]:
    """F4-3: 라인별 일별 불량률이 직전 window일 이동평균 × multiplier 이상."""
    out = []
    min_periods = max(3, window // 2)
    for line, g in line_daily.groupby(C.COL_LINE):
        ma = g[RATE].shift(1).rolling(window, min_periods=min_periods).mean()
        hit_mask = (ma > 0) & (g[RATE] >= ma * multiplier)
        hit = g[hit_mask]
        out += _rows(hit, line, METRIC_RATE, RATE,
                     lambda i, m=ma: f"≥ {m.at[i] * multiplier:.2f} ({window}일 평균 {m.at[i]:.2f}×{multiplier:g})", M_MA,
                     lambda i, h=hit, m=ma: C.SEVERITY_WARNING
                     if h.at[i, RATE] >= m.at[i] * multiplier * C.MA_WARNING_FACTOR else C.SEVERITY_CAUTION)
    return out


def detect_target(daily: pd.DataFrame, target: float) -> list[dict]:
    """F4-4: 공장 전체 일별 불량률이 목표 초과."""
    hit = daily[daily[RATE] > target]
    return _rows(hit, ALL, METRIC_RATE, RATE, lambda i: f"> {target:g} (목표)", M_TARGET,
                 lambda i: C.SEVERITY_WARNING if hit.at[i, RATE] > target * C.TARGET_WARNING_FACTOR
                 else C.SEVERITY_CAUTION)


def detect_share_shift(df: pd.DataFrame, window: int, threshold: float) -> list[dict]:
    """F4-5: 결함 유형별 일별 점유율이 직전 window일 평균 대비 +threshold%p 이상."""
    shares = aggregator.defect_share_trend(df, "D").set_index(aggregator.COL_PERIOD)
    out = []
    min_periods = max(3, window // 2)
    for defect in DEFECTS:
        s = shares[defect]
        base = s.shift(1).rolling(window, min_periods=min_periods).mean()
        diff = s - base
        hit = diff[diff >= threshold].index
        for d in hit:
            out.append({
                "일자": d, "라인": ALL, "강종": ALL, "지표": f"{defect} 점유율(%)",
                "값": float(s.at[d]),
                "기준": f"직전 {window}일 평균 {base.at[d]:.1f} +{threshold:g}%p",
                "탐지방법": M_SHARE,
                "심각도": C.SEVERITY_WARNING if diff.at[d] >= threshold * C.SHARE_WARNING_FACTOR else C.SEVERITY_CAUTION,
            })
    return out


def detect_anomalies(
    df: pd.DataFrame,
    target_rate: float = C.TARGET_DEFECT_RATE,
    iqr_k: float = C.IQR_MULTIPLIER,
    z_threshold: float = C.ZSCORE_THRESHOLD,
    ma_window: int = C.MOVING_AVG_WINDOW,
    ma_multiplier: float = C.MOVING_AVG_MULTIPLIER,
    share_shift: float = C.SHARE_SHIFT_THRESHOLD,
) -> pd.DataFrame:
    """IQR·Z-score·이동평균 급등·목표 초과·결함 점유율 급변 탐지.

    Returns:
        ANOMALY_COLUMNS 컬럼의 DataFrame. 심각도(경고 우선) → 일자 순 정렬.
    """
    if df.empty:
        return pd.DataFrame(columns=ANOMALY_COLUMNS)
    line_daily = _line_daily(df)
    daily = aggregator.daily_summary(df)
    rows = (
        detect_iqr(line_daily, iqr_k)
        + detect_zscore(line_daily, z_threshold)
        + detect_moving_average(line_daily, ma_window, ma_multiplier)
        + detect_target(daily, target_rate)
        + detect_share_shift(df, ma_window, share_shift)
    )
    if not rows:
        return pd.DataFrame(columns=ANOMALY_COLUMNS)
    out = pd.DataFrame(rows, columns=ANOMALY_COLUMNS)
    out["_sev"] = out["심각도"].map(C.SEVERITY_ORDER)
    out = out.sort_values(["_sev", "일자", "라인"]).drop(columns="_sev").reset_index(drop=True)
    return out
