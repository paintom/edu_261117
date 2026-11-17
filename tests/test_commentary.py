from datetime import date

import pytest

from steelq import config as C
from steelq.cleaner import clean
from steelq.commentary import generate_comments, josa_ro
from steelq.pipeline import AnalysisParams, Filters, run_analysis
from tests.conftest import make_df


def test_sample_comments(sample_result):
    cs = sample_result.comments
    rules = [c.rule for c in cs]
    assert 3 <= len(cs) <= C.COMMENT_MAX_LINES
    assert rules[0] == "C1"
    assert rules == sorted(rules, key=C.COMMENT_ORDER.index)
    for must in ["C1", "C2", "C4", "C5", "C6", "C8", "C10"]:
        assert must in rules
    text = {c.rule: c.text for c in cs}
    assert "스케일" in text["C5"]          # 샘플은 스케일 점유율이 증가하도록 설계
    assert "후판1라인" in text["C6"]
    assert all(any(ch.isdigit() for ch in c.text) for c in cs)  # 모든 코멘트에 수치 근거


def test_severity_values(sample_result):
    assert {c.severity for c in sample_result.comments} <= set(C.SEVERITY_ORDER)


def test_target_severity(sample_clean):
    over = run_analysis(sample_clean, params=AnalysisParams(target_rate=0.5))
    c2 = next(c for c in over.comments if c.rule == "C2")
    assert c2.severity == C.SEVERITY_WARNING and "초과" in c2.text


def test_single_month_skips_month_rules(sample_clean):
    res = run_analysis(sample_clean, Filters(start=date(2026, 10, 1)))
    rules = {c.rule for c in res.comments}
    assert "C3" not in rules and "C5" not in rules


def test_single_line_skips_group_comparison(sample_clean):
    res = run_analysis(sample_clean, Filters(lines=("냉연1라인",)))
    assert "C6" not in {c.rule for c in res.comments}


def test_empty_data():
    d = clean(make_df([{C.COL_DATE: "2026-10-01", C.COL_LINE: "A", C.COL_GRADE: "X", C.COL_PRODUCTION: 1}])).data
    assert generate_comments(d.iloc[0:0], d.iloc[0:0], None) == []


@pytest.mark.parametrize("word, expected", [("스케일", "로"), ("압연흠", "으로"), ("표면균열", "로"),
                                            ("스크래치", "로"), ("치수불량", "으로"), ("ABC", "(으)로")])
def test_josa(word, expected):
    assert josa_ro(word) == expected
