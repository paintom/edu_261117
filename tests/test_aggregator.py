import numpy as np
import pandas as pd
import pytest

from steelq import aggregator as A
from steelq import config as C
from steelq.cleaner import clean
from tests.conftest import make_df

D, L, G, P = C.COL_DATE, C.COL_LINE, C.COL_GRADE, C.COL_PRODUCTION
RATE = C.COL_DEFECT_RATE


@pytest.fixture
def small():
    return clean(make_df([
        {D: "2026-09-30", L: "A", G: "X", P: 100.0, "스크래치": 1.0},
        {D: "2026-10-01", L: "A", G: "X", P: 100.0, "스크래치": 1.0},
        {D: "2026-10-01", L: "B", G: "Y", P: 900.0, "스케일": 18.0},
        {D: "2026-10-02", L: "B", G: "Y", P: 0.0, "스케일": 5.0},   # 생산량 0 → 불량률 계산 제외
    ])).data


def test_weighted_average_not_simple_mean(small):
    daily = A.daily_summary(small).set_index(D)
    # (1 + 18) / (100 + 900) = 1.9%  (행별 평균이면 1.5%)
    assert daily.loc[pd.Timestamp("2026-10-01"), RATE] == pytest.approx(1.9)


def test_zero_production_excluded_from_rate(small):
    daily = A.daily_summary(small).set_index(D)
    assert np.isnan(daily.loc[pd.Timestamp("2026-10-02"), RATE])
    kpi = A.overall_summary(small)
    assert kpi["rate"] == pytest.approx(20.0 / 1100 * 100)   # 생산량 0 행의 불량 5톤 제외
    assert kpi["defect"] == pytest.approx(25.0)              # 총불량량에는 포함


def test_monthly_diff(small):
    m = A.monthly_summary(small)
    assert m[A.COL_MONTH].tolist() == ["2026-09", "2026-10"]
    assert np.isnan(m[A.COL_RATE_DIFF].iloc[0])
    assert m[A.COL_RATE_DIFF].iloc[1] == pytest.approx(19 / 1000 * 100 - 1.0)


def test_group_summary_sorted_by_rate(small):
    g = A.group_summary(small, L)
    assert g[L].tolist() == ["B", "A"]
    assert A.COL_RATIO in g.columns


def test_filter(small):
    f = A.filter_data(small, start=pd.Timestamp("2026-10-01").date(), lines=["B"])
    assert set(f[L]) == {"B"} and len(f) == 2
    assert A.filter_data(small, lines=[]).empty


def test_shares_sum_to_100(sample_clean):
    d = sample_clean.data
    total = A.defect_share(d)
    assert total[A.COL_SHARE].sum() == pytest.approx(100, abs=0.01)
    assert total[A.COL_CUM_SHARE].iloc[-1] == pytest.approx(100, abs=0.01)
    assert total[A.COL_DEFECT_AMOUNT].is_monotonic_decreasing
    for freq in ["M", "D"]:
        trend = A.defect_share_trend(d, freq)
        sums = trend[C.DEFECT_TYPES].sum(axis=1)
        assert np.allclose(sums, 100, atol=0.01)


def test_daily_matches_independent_calc(sample_clean):
    d = sample_clean.data
    daily = A.daily_summary(d).set_index(D)
    valid = d[d[P] > 0]
    expected = valid.groupby(D)[C.COL_TOTAL_DEFECT].sum() / valid.groupby(D)[P].sum() * 100
    assert np.allclose(daily[RATE].loc[expected.index], expected)
    assert daily[P].sum() == pytest.approx(d[P].sum())


def test_line_defect_matrix(sample_clean):
    m = A.line_defect_matrix(sample_clean.data, share=True)
    assert np.allclose(m[C.DEFECT_TYPES].sum(axis=1), 100)
