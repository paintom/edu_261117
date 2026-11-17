"""정제 이후의 분석 파이프라인. 화면(app.py)과 엑셀 리포트(report.py)가 같은 결과를 쓰도록 한곳에서 계산한다.

    clean_result → filter → 집계 · 이상치 · 코멘트 → AnalysisResult
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

import pandas as pd

from steelq import aggregator, anomaly, commentary, config
from steelq.cleaner import CleanResult

C = config


@dataclass(frozen=True)
class AnalysisParams:
    target_rate: float = C.TARGET_DEFECT_RATE
    iqr_k: float = C.IQR_MULTIPLIER
    z_threshold: float = C.ZSCORE_THRESHOLD
    ma_window: int = C.MOVING_AVG_WINDOW
    ma_multiplier: float = C.MOVING_AVG_MULTIPLIER
    share_shift: float = C.SHARE_SHIFT_THRESHOLD


@dataclass(frozen=True)
class Filters:
    start: date | None = None
    end: date | None = None
    lines: tuple[str, ...] | None = None
    grades: tuple[str, ...] | None = None

    def describe(self, all_lines: list[str] | None = None, all_grades: list[str] | None = None) -> str:
        """리포트 표기용 필터 설명."""
        def items(sel, all_):
            if sel is None or (all_ is not None and set(sel) >= set(all_)):
                return "전체"
            return ", ".join(sel) if sel else "(없음)"
        period = f"{self.start or '처음'} ~ {self.end or '끝'}"
        return f"기간 {period} / 라인 {items(self.lines, all_lines)} / 강종 {items(self.grades, all_grades)}"


@dataclass
class AnalysisResult:
    clean: CleanResult
    data: pd.DataFrame                     # 필터 적용된 정제 데이터
    params: AnalysisParams
    filters: Filters
    kpi: dict
    daily: pd.DataFrame
    monthly: pd.DataFrame
    by_line: pd.DataFrame
    by_grade: pd.DataFrame
    defect_total: pd.DataFrame
    defect_monthly: pd.DataFrame
    defect_daily: pd.DataFrame
    line_defect: pd.DataFrame
    anomalies: pd.DataFrame
    comments: list[commentary.Comment] = field(default_factory=list)
    filter_text: str = ""

    @property
    def empty(self) -> bool:
        return self.data.empty


def run_analysis(
    clean_result: CleanResult,
    filters: Filters | None = None,
    params: AnalysisParams | None = None,
) -> AnalysisResult:
    filters = filters or Filters()
    params = params or AnalysisParams()
    full = clean_result.data
    data = aggregator.filter_data(
        full, filters.start, filters.end,
        list(filters.lines) if filters.lines is not None else None,
        list(filters.grades) if filters.grades is not None else None,
    )
    anomalies = anomaly.detect_anomalies(
        data, target_rate=params.target_rate, iqr_k=params.iqr_k, z_threshold=params.z_threshold,
        ma_window=params.ma_window, ma_multiplier=params.ma_multiplier, share_shift=params.share_shift,
    )
    return AnalysisResult(
        clean=clean_result,
        data=data,
        params=params,
        filters=filters,
        kpi=aggregator.overall_summary(data),
        daily=aggregator.daily_summary(data),
        monthly=aggregator.monthly_summary(data),
        by_line=aggregator.group_summary(data, C.COL_LINE),
        by_grade=aggregator.group_summary(data, C.COL_GRADE),
        defect_total=aggregator.defect_share(data),
        defect_monthly=aggregator.defect_share_trend(data, "M"),
        defect_daily=aggregator.defect_share_trend(data, "D"),
        line_defect=aggregator.line_defect_matrix(data),
        anomalies=anomalies,
        comments=commentary.generate_comments(data, anomalies, clean_result.log, params.target_rate),
        filter_text=filters.describe(
            sorted(full[C.COL_LINE].unique()), sorted(full[C.COL_GRADE].unique())
        ),
    )
