import pandas as pd
import pytest

from steelq import config
from steelq.cleaner import clean
from steelq.loader import LoadedFile, auto_map_columns, merge_files
from steelq.pipeline import run_analysis
from steelq.sample_data import generate_sample


@pytest.fixture(scope="session")
def sample():
    return generate_sample()


@pytest.fixture(scope="session")
def sample_merged(sample) -> pd.DataFrame:
    data = sample.data.copy()
    data[config.COL_DATE] = data[config.COL_DATE].dt.strftime("%Y-%m-%d")
    mapping, missing = auto_map_columns(list(data.columns))
    return merge_files([LoadedFile("sample.csv", data, mapping, missing)])


@pytest.fixture(scope="session")
def sample_clean(sample_merged):
    return clean(sample_merged)


@pytest.fixture(scope="session")
def sample_result(sample_clean):
    return run_analysis(sample_clean)


def make_df(rows: list[dict]) -> pd.DataFrame:
    """테스트용 표준 스키마 DataFrame. 누락 결함 컬럼은 0."""
    base = {d: 0.0 for d in config.DEFECT_TYPES}
    return pd.DataFrame([{**base, **r} for r in rows], columns=config.REQUIRED_COLUMNS)
