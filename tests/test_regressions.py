"""4단계 검토에서 발견한 문제(#1~#14)의 회귀 테스트."""

import io
import time

import numpy as np
import pandas as pd
import pytest
from openpyxl import load_workbook

from steelq import aggregator as A
from steelq import cleaner as CL
from steelq import config as C
from steelq.cleaner import clean, parse_dates
from steelq.loader import LoadedFile, LoaderError, auto_map_columns, load_file, merge_files
from steelq.pipeline import run_analysis
from steelq.report import build_report


def mk(rows):
    return pd.DataFrame(rows, columns=C.REQUIRED_COLUMNS)


def row(date="2026-10-01", line="L", grade="G", prod=100.0, scratch=1.0):
    return [date, line, grade, prod, scratch, 0, 0, 0, 0]


def via_csv(df: pd.DataFrame):
    """CSV를 거쳐 읽어야 숫자형 일자가 int 컬럼이 되는 실제 상황을 재현한다."""
    return merge_files([load_file(df.to_csv(index=False).encode("utf-8"), filename="x.csv")])


# ---------------------------------------------------------------------------
# #1 숫자형 일자 / #14 한글·모호한 일자
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("dates, expected", [
    ([20261001, 20261002], ["2026-10-01", "2026-10-02"]),       # YYYYMMDD 정수
    ([46296, 46297], ["2026-10-01", "2026-10-02"]),             # 엑셀 날짜 일련번호
    (["2026년 10월 3일", "2026년10월4일"], ["2026-10-03", "2026-10-04"]),
    (["2026-10-01", "2026.10.02"], ["2026-10-01", "2026-10-02"]),
])
def test_numeric_and_korean_dates(dates, expected):
    res = clean(via_csv(mk([row(date=dates[0]), row(date=dates[1])])))
    assert res.data[C.COL_DATE].dt.strftime("%Y-%m-%d").tolist() == expected
    assert res.count(CL.R_FULL_DUP) == 0  # 1970-01-01로 뭉개져 중복 삭제되면 안 된다


def test_out_of_range_number_is_invalid_date():
    res = clean(via_csv(mk([row(date=123), row(date="2026-10-02")])))
    assert res.count(CL.R_DATE_DROP) == 1
    assert res.data[C.COL_DATE].dt.year.min() == 2026


def test_ambiguous_slash_date_is_flagged():
    parsed, amb = parse_dates(pd.Series(["02/10/2026", "13/10/2026", "2026-10-01"]))
    assert parsed.dt.strftime("%Y-%m-%d").tolist() == ["2026-02-10", "2026-10-13", "2026-10-01"]
    assert amb.tolist() == [True, False, False]


# ---------------------------------------------------------------------------
# #8 미상 키 중복 / #9 제어문자 / #11 라벨 통일
# ---------------------------------------------------------------------------
def test_unknown_key_duplicates_are_kept_and_warned():
    res = clean(mk([row(line=None, prod=100), row(line=None, prod=200)]))
    assert len(res.data) == 2
    assert res.count(CL.R_WARN_UNKNOWN_DUP) == 2


def test_control_characters_removed_and_report_builds():
    res = clean(mk([row(line="L\x01", grade="g\x7f")]))
    assert res.data[C.COL_LINE].iloc[0] == "L" and res.data[C.COL_GRADE].iloc[0] == "G"
    build_report(run_analysis(res))


def test_report_survives_illegal_chars_in_file_name():
    df = mk([row()])
    df.insert(0, "_원본파일", "bad\x02name.csv")
    df.insert(1, "_원본행", [2])
    wb = load_workbook(build_report(run_analysis(clean(df))))
    assert "정제데이터" in wb.sheetnames


def test_line_case_and_numeric_labels_unified():
    res = clean(mk([row(line="line1"), row(date="2026-10-02", line="LINE1"),
                    row(date="2026-10-03", line=1.0), row(date="2026-10-04", line="1")]))
    assert sorted(res.data[C.COL_LINE].unique()) == ["1", "LINE1"]


# ---------------------------------------------------------------------------
# #6 결함 컬럼 0 채우기
# ---------------------------------------------------------------------------
def test_zero_fill_missing_defect_column():
    df = mk([row()]).drop(columns=["표면균열"])
    mapping, missing = auto_map_columns(list(df.columns))
    lf = LoadedFile("a.csv", df, mapping, missing)
    assert lf.missing == ["표면균열"]
    lf.set_mapping(lf.mapping, zero_fill=["표면균열"])
    assert lf.is_complete
    assert (lf.standardized()["표면균열"] == 0).all()


def test_zero_fill_only_for_defects():
    df = mk([row()]).drop(columns=[C.COL_PRODUCTION])
    mapping, missing = auto_map_columns(list(df.columns))
    lf = LoadedFile("a.csv", df, mapping, missing)
    with pytest.raises(LoaderError):
        lf.set_mapping(lf.mapping, zero_fill=[C.COL_PRODUCTION])


# ---------------------------------------------------------------------------
# #12 전월 생산량 0 → inf 금지 / #13 C10 기준 명시
# ---------------------------------------------------------------------------
def test_prod_change_no_inf():
    m = A.monthly_summary(clean(mk([row(date="2026-09-01", prod=0, scratch=0), row(prod=100)])).data)
    assert not np.isinf(m[A.COL_PROD_DIFF].fillna(0)).any()


def test_c10_mentions_scope(sample_result):
    c10 = next(c for c in sample_result.comments if c.rule == "C10")
    assert "필터 적용 전" in c10.text


# ---------------------------------------------------------------------------
# #5 대용량 리포트 성능 (콤마 문자열 10만 행)
# ---------------------------------------------------------------------------
@pytest.mark.slow
def test_large_report_performance():
    n = 100_000
    rng = np.random.default_rng(0)
    dates = pd.date_range("2000-01-01", periods=n // 10, freq="D").strftime("%Y-%m-%d")
    df = pd.DataFrame({
        C.COL_DATE: np.repeat(dates, 10)[:n],
        C.COL_LINE: np.tile([f"L{i}" for i in range(10)], n // 10)[:n],
        C.COL_GRADE: "G",
        C.COL_PRODUCTION: [f"{v:,.1f}" for v in rng.uniform(500, 2000, n)],
        **{d: rng.uniform(0, 3, n).round(2) for d in C.DEFECT_TYPES},
    })
    t = time.time()
    res = run_analysis(clean(via_csv(df)))
    buf = build_report(res)
    assert time.time() - t < 30   # PRD 5장: 10만 행 30초 이내
    assert load_workbook(io.BytesIO(buf.getvalue()), read_only=True).sheetnames[-1] == "정제데이터"
