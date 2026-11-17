import numpy as np
import pandas as pd
import pytest

from steelq import cleaner as CL
from steelq import config as C
from steelq.cleaner import clean
from tests.conftest import make_df

D, L, G, P = C.COL_DATE, C.COL_LINE, C.COL_GRADE, C.COL_PRODUCTION


def _row(date="2026-10-01", line="A", grade="SS400", prod=100.0, **defects):
    return {D: date, L: line, G: grade, P: prod, **defects}


def rules(res) -> dict:
    return dict(zip(res.summary()["규칙"], res.summary()["건수"]))


def test_full_duplicate_removed():
    df = make_df([_row(), _row(), _row(date="2026-10-02")])
    res = clean(df)
    assert len(res.data) == 2
    assert rules(res)[CL.R_FULL_DUP] == 1
    assert res.log.iloc[0]["원본행"] == 3  # 엑셀 행 번호 (헤더 1행)


@pytest.mark.parametrize("strategy, expected", [("last", 120.0), ("sum", 220.0), ("mean", 110.0)])
def test_key_duplicate_strategies(strategy, expected):
    df = make_df([_row(prod=100.0, 스크래치=1.0), _row(prod=120.0, 스크래치=3.0)])
    res = clean(df, duplicate_key_strategy=strategy)
    assert len(res.data) == 1
    assert res.data[P].iloc[0] == pytest.approx(expected)
    if strategy == "sum":
        assert res.data["스크래치"].iloc[0] == pytest.approx(4.0)
    assert rules(res)[CL.R_KEY_DUP] == 1


def test_invalid_dates_dropped_and_formats_parsed():
    df = make_df([_row(date=None), _row(date="날짜아님", line="B"), _row(date="2026/10/03", line="C"),
                  _row(date="20261004", line="D"), _row(date="2026-10-05 13:00", line="E")])
    res = clean(df)
    assert rules(res)[CL.R_DATE_DROP] == 2
    assert sorted(res.data[D].dt.strftime("%Y-%m-%d")) == ["2026-10-03", "2026-10-04", "2026-10-05"]


def test_missing_labels_become_unknown():
    df = make_df([_row(line=None), _row(grade=None, date="2026-10-02"), _row(line="  ", date="2026-10-03")])
    res = clean(df)
    assert (res.data[L] == C.UNKNOWN_LABEL).sum() == 2
    assert (res.data[G] == C.UNKNOWN_LABEL).sum() == 1
    assert rules(res)[CL.R_LABEL_MISSING] == 3


def test_label_normalization():
    df = make_df([_row(line=" 열연 1라인 ", grade="ss 400")])
    res = clean(df)
    assert res.data[L].iloc[0] == "열연1라인"
    assert res.data[G].iloc[0] == "SS400"
    assert rules(res)[CL.R_LABEL_NORMALIZE] == 2


def test_production_interpolation_then_line_median():
    df = make_df([
        _row(date="2026-10-01", prod=100.0), _row(date="2026-10-02", prod=None), _row(date="2026-10-03", prod=200.0),
        _row(date="2026-10-01", grade="SM490", prod=None),  # 같은 라인·강종에 값 없음 → 라인 중앙값
    ])
    res = clean(df)
    d = res.data.set_index([D, G])[P]
    assert d[(pd.Timestamp("2026-10-02"), "SS400")] == pytest.approx(150.0)
    assert d[(pd.Timestamp("2026-10-01"), "SM490")] == pytest.approx(150.0)  # median(100, 200)
    notes = res.log[res.log["규칙"] == CL.R_PROD_MISSING]["비고"].tolist()
    assert notes == ["직전/직후 값 보간", "라인 중앙값"] or sorted(notes) == sorted(["직전/직후 값 보간", "라인 중앙값"])


def test_defect_missing_to_zero():
    df = make_df([_row(스크래치=None, 스케일=2.0)])
    res = clean(df)
    assert res.data["스크래치"].iloc[0] == 0
    assert res.data[C.COL_TOTAL_DEFECT].iloc[0] == pytest.approx(2.0)


@pytest.mark.parametrize("strategy, expected", [("zero", 0.0), ("abs", 5.0), ("nan", 0.0)])
def test_negative_strategies(strategy, expected):
    df = make_df([_row(스크래치=-5.0)])
    res = clean(df, negative_strategy=strategy)
    assert res.data["스크래치"].iloc[0] == pytest.approx(expected)
    assert rules(res)[CL.R_NEGATIVE] == 1


def test_negative_production_nan_strategy_interpolates():
    df = make_df([_row(date="2026-10-01", prod=100.0), _row(date="2026-10-02", prod=-50.0),
                  _row(date="2026-10-03", prod=300.0)])
    res = clean(df, negative_strategy="nan")
    assert res.data[P].iloc[1] == pytest.approx(200.0)


def test_comma_strings_and_unparseable():
    df = make_df([_row(prod="1,250.5"), _row(prod="abc", date="2026-10-02"), _row(prod="300", date="2026-10-03")])
    res = clean(df)
    r = rules(res)
    assert r[CL.R_NUMERIC] == 1           # '300'은 원래 숫자로 읽히므로 기록하지 않음
    assert r[CL.R_NUMERIC_FAIL] == 1
    assert res.data[P].iloc[0] == pytest.approx(1250.5)


def test_warning_flags():
    df = make_df([_row(prod=0.0, 스크래치=1.0), _row(prod=1.0, 스크래치=5.0, date="2026-10-02")])
    res = clean(df)
    assert res.data[CL.COL_WARNING].str.len().gt(0).all()
    assert np.isnan(res.data[C.COL_DEFECT_RATE].iloc[0])
    r = rules(res)
    assert r[CL.R_WARN_ZERO_PROD] == 1 and r[CL.R_WARN_OVER] == 1


def test_invalid_option():
    with pytest.raises(ValueError):
        clean(make_df([_row()]), duplicate_key_strategy="first")


# ---------------------------------------------------------------------------
# 샘플 데이터: 주입 내역과 정제 로그 대조
# ---------------------------------------------------------------------------
def test_sample_clean_has_no_bad_values(sample_clean):
    d = sample_clean.data
    assert (d[C.NUMERIC_COLUMNS] < 0).sum().sum() == 0
    assert d[C.REQUIRED_COLUMNS].isna().sum().sum() == 0
    assert d.duplicated(subset=C.KEY_COLUMNS).sum() == 0


def test_sample_log_matches_injected(sample, sample_clean):
    inj = sample.injected
    errors = inj[inj["구분"] == "데이터오류"].groupby("유형").size()
    r = rules(sample_clean)
    assert r[CL.R_FULL_DUP] == errors["완전중복"]
    assert r[CL.R_KEY_DUP] == errors["키중복"]
    assert r[CL.R_NEGATIVE] == errors["음수"]
    assert r[CL.R_NUMERIC] == errors["콤마문자열"]
    assert sample_clean.count(CL.MISSING_RULES) == errors["결측"]
