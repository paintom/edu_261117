import plotly.graph_objects as go
import pytest

from steelq import aggregator as A
from steelq import charts as CH
from steelq import config as C
from steelq.pipeline import Filters, run_analysis


def _yaxes(fig: go.Figure) -> list:
    return [k for k in fig.layout.to_plotly_json() if k.startswith("yaxis")]


def test_trend_has_no_secondary_axis(sample_result):
    fig = CH.production_rate_trend(sample_result.daily, C.COL_DATE, 1.0)
    # 위·아래 두 패널(각자 하나의 y축)이며 overlaying 보조축이 없어야 한다
    assert all(not getattr(fig.layout[k], "overlaying", None) for k in _yaxes(fig))
    assert {t.name for t in fig.data} >= {"생산량", "불량률"}


def test_target_exceed_markers(sample_result):
    fig = CH.production_rate_trend(sample_result.daily, C.COL_DATE, 1.0)
    over = [t for t in fig.data if t.name == "목표 초과"]
    expected = int((sample_result.daily[C.COL_DEFECT_RATE] > 1.0).sum())
    assert (len(over[0].x) if over else 0) == expected


def test_defect_colors_fixed_per_type(sample_clean):
    """필터로 결함 순위가 바뀌어도 결함 유형의 색은 그대로여야 한다."""
    for f in [Filters(), Filters(lines=("냉연1라인",))]:
        res = run_analysis(sample_clean, f)
        fig = CH.defect_ranked_bars(res.defect_total)
        colors = dict(zip(fig.data[0].y, fig.data[0].marker.color))
        assert colors == {d: C.DEFECT_COLORS[d] for d in colors}


def test_share_trend_legend_order(sample_result):
    fig = CH.defect_share_trend(sample_result.defect_monthly, "M")
    assert [t.name for t in fig.data] == C.DEFECT_TYPES
    assert fig.layout.legend.traceorder == "normal"


@pytest.mark.parametrize("by", [C.COL_LINE, C.COL_GRADE])
def test_group_bars_highlight_over_target(sample_result, by):
    df = sample_result.by_line if by == C.COL_LINE else sample_result.by_grade
    fig = CH.group_rate_bars(df, by, 1.0)
    crit = C.SEVERITY_MARK_COLORS[C.SEVERITY_WARNING]
    n_crit = sum(c == crit for c in fig.data[0].marker.color)
    assert n_crit == int((df[C.COL_DEFECT_RATE] > 1.0).sum())


def test_heatmap_and_anomaly_series(sample_result):
    share = A.line_defect_matrix(sample_result.data, share=True)
    CH.line_defect_heatmap(share, sample_result.line_defect)
    ld = A.line_daily_summary(sample_result.data)
    s = ld[ld[C.COL_LINE] == "냉연1라인"]
    an = sample_result.anomalies[sample_result.anomalies["라인"] == "냉연1라인"]
    fig = CH.anomaly_series(s, an, None)
    names = {t.name for t in fig.data}
    assert f"{C.SEVERITY_WARNING} 이상치" in names
