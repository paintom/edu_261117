import pandas as pd

from steelq import config as C
from steelq.anomaly import ANOMALY_COLUMNS, M_TARGET, M_Z, METRIC_PROD, METRIC_RATE, detect_anomalies
from steelq.cleaner import clean
from tests.conftest import make_df


def _hits(an: pd.DataFrame, inj: pd.DataFrame, metric: str) -> float:
    found = 0
    for _, r in inj.iterrows():
        m = an[(an["일자"] == pd.Timestamp(r["일자"])) & (an["라인"] == r["라인"]) & (an["지표"] == metric)]
        found += not m.empty
    return found / len(inj)


def test_rate_spike_detection_rate(sample, sample_result):
    inj = sample.injected
    spikes = inj[inj["유형"] == "불량률급등"]
    assert _hits(sample_result.anomalies, spikes, METRIC_RATE) >= 0.9


def test_production_drop_detected(sample, sample_result):
    inj = sample.injected
    drops = inj[inj["유형"] == "생산량급감"]
    assert _hits(sample_result.anomalies, drops, METRIC_PROD) >= 0.9


def test_columns_and_sorting(sample_result):
    an = sample_result.anomalies
    assert list(an.columns) == ANOMALY_COLUMNS
    sev = an["심각도"].map(C.SEVERITY_ORDER)
    assert sev.is_monotonic_increasing


def test_empty_input():
    empty = clean(make_df([{C.COL_DATE: "2026-10-01", C.COL_LINE: "A", C.COL_GRADE: "X", C.COL_PRODUCTION: 1}])).data
    assert detect_anomalies(empty.iloc[0:0]).empty


def test_threshold_parameters_change_results(sample_clean):
    d = sample_clean.data
    strict = detect_anomalies(d, target_rate=0.5)
    loose = detect_anomalies(d, target_rate=5.0)
    assert (strict["탐지방법"] == M_TARGET).sum() > (loose["탐지방법"] == M_TARGET).sum() == 0
    assert (detect_anomalies(d, z_threshold=10)["탐지방법"] == M_Z).sum() == 0


def test_short_series_does_not_crash():
    rows = [{C.COL_DATE: f"2026-10-0{i}", C.COL_LINE: "A", C.COL_GRADE: "X", C.COL_PRODUCTION: 100.0, "스크래치": 1.0}
            for i in range(1, 3)]
    an = detect_anomalies(clean(make_df(rows)).data)
    assert list(an.columns) == ANOMALY_COLUMNS
