import pandas as pd
import pytest
from openpyxl import load_workbook

from steelq import config as C
from steelq.pipeline import Filters, run_analysis
from steelq.report import REPORT_SHEETS, build_report, report_filename


@pytest.fixture(scope="module")
def workbook(sample_result):
    return load_workbook(build_report(sample_result))


def test_sheets(workbook):
    assert workbook.sheetnames == REPORT_SHEETS


def test_charts(workbook):
    counts = {ws.title: len(ws._charts) for ws in workbook}
    # 보조축 대신 생산량·불량률을 별도 차트로 (일별·월별 각 2개)
    assert counts["일별집계"] == 2 and counts["월별집계"] == 2
    assert counts["결함분석"] == 2 and counts["라인·강종"] == 1
    assert sum(counts.values()) == 7


def test_rate_format_and_value(workbook, sample_result):
    ws = workbook["일별집계"]
    header = [c.value for c in ws[1]]
    col = header.index(C.COL_DEFECT_RATE) + 1
    cell = ws.cell(row=2, column=col)
    assert cell.number_format == "0.00%"
    assert cell.value == pytest.approx(sample_result.daily[C.COL_DEFECT_RATE].iloc[0] / 100)
    assert ws.freeze_panes == "B2"
    assert len(ws.conditional_formatting) == 1


def test_summary_contains_comments(workbook, sample_result):
    texts = [c.value for row in workbook["요약"].iter_rows() for c in row if isinstance(c.value, str)]
    for cm in sample_result.comments:
        assert cm.text in texts


def test_filter_reflected(sample_clean):
    res = run_analysis(sample_clean, Filters(lines=("냉연1라인",)))
    wb = load_workbook(build_report(res))
    ws = wb["정제데이터"]
    header = [c.value for c in ws[1]]
    col = header.index(C.COL_LINE) + 1
    assert {ws.cell(row=r, column=col).value for r in range(2, ws.max_row + 1)} == {"냉연1라인"}


def test_empty_result_raises(sample_clean):
    res = run_analysis(sample_clean, Filters(lines=()))
    with pytest.raises(ValueError):
        build_report(res)


def test_filename():
    assert report_filename(pd.Timestamp("2026-08-01"), pd.Timestamp("2026-10-31")) == \
        "SteelQ_Report_20260801_20261031.xlsx"
