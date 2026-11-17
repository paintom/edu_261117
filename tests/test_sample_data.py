import pandas as pd

from steelq import config
from steelq.loader import auto_map_columns
from steelq.sample_data import KIND_ANOMALY, KIND_ERROR, generate_sample


def test_reproducible():
    a, b = generate_sample(seed=42), generate_sample(seed=42)
    pd.testing.assert_frame_equal(a.data, b.data)
    pd.testing.assert_frame_equal(a.injected, b.injected)


def test_schema_maps_completely():
    data = generate_sample().data
    _, missing = auto_map_columns(list(data.columns))
    assert missing == []
    assert set(data[config.COL_LINE].dropna()) == {"열연1라인", "열연2라인", "냉연1라인", "후판1라인"}


def test_injected_contents():
    res = generate_sample()
    inj = res.injected
    counts = inj.groupby(["구분", "유형"]).size()
    assert counts[(KIND_ANOMALY, "불량률급등")] >= 3
    assert counts[(KIND_ANOMALY, "생산량급감")] == 2
    for kind in ["결측", "음수", "콤마문자열", "완전중복", "키중복"]:
        assert counts[(KIND_ERROR, kind)] >= 1

    data = res.data
    key = [config.COL_DATE, config.COL_LINE, config.COL_GRADE]
    assert data.duplicated().sum() >= counts[(KIND_ERROR, "완전중복")]
    assert data.duplicated(subset=key).sum() >= counts[(KIND_ERROR, "키중복")]
    numeric = data[config.NUMERIC_COLUMNS].apply(pd.to_numeric, errors="coerce")
    assert (numeric < 0).sum().sum() == counts[(KIND_ERROR, "음수")]


def test_scale_share_increases():
    data = generate_sample().data
    num = data.dropna(subset=[config.COL_DATE]).copy()
    num[config.DEFECT_TYPES] = num[config.DEFECT_TYPES].apply(pd.to_numeric, errors="coerce").clip(lower=0)
    month = pd.to_datetime(num[config.COL_DATE]).dt.month
    share = num.groupby(month)[config.DEFECT_TYPES].sum()
    scale_share = share["스케일"] / share.sum(axis=1)
    assert scale_share.iloc[-1] > scale_share.iloc[0] + 0.05
