import io

import pandas as pd
import pytest

from steelq import config
from steelq.loader import (
    META_FILE,
    META_ROW,
    LoaderError,
    auto_map_columns,
    load_file,
    merge_files,
    normalize_header,
    read_file,
)

ROWS = [
    ["2026-10-01", "열연1라인", "SS400", "1,250.5", 1.2, 0.8, 0.5, 0.3, 0.1],
    ["2026-10-01", "냉연1라인", "SPCC", 800.0, 0.9, 0.2, 0.1, 0.2, 0.0],
]


@pytest.fixture
def std_df() -> pd.DataFrame:
    return pd.DataFrame(ROWS, columns=config.REQUIRED_COLUMNS)


def _csv_bytes(df: pd.DataFrame, encoding: str) -> bytes:
    return df.to_csv(index=False).encode(encoding)


@pytest.mark.parametrize(
    "raw, expected",
    [(" Prod_Date ", "proddate"), ("생산량(톤)", "생산량"), ("Steel-Grade", "steelgrade"), ("LINE NAME", "linename")],
)
def test_normalize_header(raw, expected):
    assert normalize_header(raw) == expected


@pytest.mark.parametrize("encoding", ["utf-8-sig", "utf-8", "cp949"])
def test_csv_encodings(std_df, encoding):
    df, used = read_file(_csv_bytes(std_df, encoding), filename="a.csv")
    assert list(df.columns) == config.REQUIRED_COLUMNS
    assert df.iloc[0][config.COL_LINE] == "열연1라인"
    assert used == ("cp949" if encoding == "cp949" else "utf-8-sig")


def test_xlsx_read(std_df):
    buf = io.BytesIO()
    std_df.to_excel(buf, index=False, sheet_name="데이터")
    df, sheet = read_file(buf.getvalue(), filename="a.xlsx", sheet_name=0)
    assert sheet == "데이터"
    assert len(df) == 2 and df.iloc[1][config.COL_GRADE] == "SPCC"


def test_english_alias_mapping():
    cols = ["Date", "Line", "Steel Grade", "Qty", "Scratch", "Rolling_Defect", "Scale", "Dimension", "Surface Crack"]
    mapping, missing = auto_map_columns(cols)
    assert missing == []
    assert mapping["Date"] == config.COL_DATE
    assert mapping["Qty"] == config.COL_PRODUCTION
    assert mapping["Surface Crack"] == "표면균열"


def test_korean_alias_with_unit():
    mapping, missing = auto_map_columns(["생산일자", "생산라인", "강종명", "생산량(톤)", *config.DEFECT_TYPES])
    assert missing == []
    assert mapping["생산량(톤)"] == config.COL_PRODUCTION


def test_duplicate_alias_uses_first_column():
    mapping, _ = auto_map_columns(["일자", "Date", "라인"])
    assert mapping == {"일자": config.COL_DATE, "라인": config.COL_LINE}


def test_missing_column_and_manual_mapping(std_df):
    df = std_df.rename(columns={config.COL_PRODUCTION: "출강톤수"})
    lf = load_file(_csv_bytes(df, "utf-8-sig"), filename="b.csv")
    assert lf.missing == [config.COL_PRODUCTION]
    with pytest.raises(LoaderError):
        lf.standardized()
    lf.set_mapping({**lf.mapping, "출강톤수": config.COL_PRODUCTION})
    assert lf.is_complete
    assert config.COL_PRODUCTION in lf.standardized().columns


def test_manual_mapping_rejects_duplicate_target(std_df):
    lf = load_file(_csv_bytes(std_df, "utf-8-sig"), filename="c.csv")
    with pytest.raises(LoaderError):
        lf.set_mapping({**lf.mapping, "스크래치": config.COL_PRODUCTION})


def test_merge_files_adds_meta(std_df):
    a = load_file(_csv_bytes(std_df, "utf-8-sig"), filename="a.csv")
    b = load_file(_csv_bytes(std_df, "cp949"), filename="b.csv")
    merged = merge_files([a, b])
    assert len(merged) == 4
    assert list(merged.columns) == [META_FILE, META_ROW, *config.REQUIRED_COLUMNS]
    assert merged[META_FILE].tolist() == ["a.csv", "a.csv", "b.csv", "b.csv"]
    assert merged[META_ROW].tolist() == [2, 3, 2, 3]  # 엑셀 행 번호 (헤더=1행)


@pytest.mark.parametrize("payload", [b"", b"   \n", "일자,라인\n".encode("utf-8")])
def test_empty_or_header_only(payload):
    with pytest.raises(LoaderError):
        read_file(payload, filename="empty.csv")


def test_unsupported_extension():
    with pytest.raises(LoaderError):
        read_file(b"abc", filename="data.txt")


def test_read_from_path(tmp_path, std_df):
    p = tmp_path / "x.csv"
    p.write_bytes(_csv_bytes(std_df, "cp949"))
    lf = load_file(p)
    assert lf.name == "x.csv" and lf.is_complete
