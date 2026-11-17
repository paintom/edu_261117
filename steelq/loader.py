"""파일 입력: CSV/엑셀 읽기, 인코딩 판별, 컬럼 자동/수동 매핑, 복수 파일 병합 (PRD F1, 3.2, 3.3).

Streamlit에 의존하지 않는다. `source`로는 파일 경로, bytes, 또는
`.getvalue()`/`.read()`를 가진 파일 객체(Streamlit UploadedFile 포함)를 받는다.
"""

from __future__ import annotations

import io
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pandas as pd

from steelq import config

# 병합·정제 로그 추적용 메타 컬럼
META_FILE = "_원본파일"
META_ROW = "_원본행"
META_COLUMNS = [META_FILE, META_ROW]


class LoaderError(Exception):
    """사용자에게 그대로 보여줄 수 있는 파일 읽기 오류."""


# ---------------------------------------------------------------------------
# 헤더 정규화 / 매핑
# ---------------------------------------------------------------------------
_PAREN_RE = re.compile(r"[\(\[（].*?[\)\]）]")
_SEP_RE = re.compile(r"[\s_\-\./]+")


def normalize_header(name: Any) -> str:
    """대소문자·공백·구분자·괄호 안 단위를 제거해 비교용 키로 만든다.

    예: " Prod_Date " -> "proddate", "생산량(톤)" -> "생산량"
    """
    text = str(name).strip().lower()
    text = _PAREN_RE.sub("", text)
    return _SEP_RE.sub("", text)


def _alias_lookup() -> dict[str, str]:
    lookup: dict[str, str] = {}
    for standard, aliases in config.COLUMN_ALIASES.items():
        for alias in [standard, *aliases]:
            lookup.setdefault(normalize_header(alias), standard)
    return lookup


def auto_map_columns(columns: list[Any]) -> tuple[dict[str, str], list[str]]:
    """원본 컬럼명을 표준 컬럼명에 자동 매핑한다.

    Returns:
        (mapping, missing): mapping은 {원본컬럼: 표준컬럼}, missing은 매핑되지 않은 필수 표준 컬럼.
        같은 표준 컬럼에 여러 원본 컬럼이 해당하면 앞쪽 컬럼만 사용한다.
    """
    lookup = _alias_lookup()
    mapping: dict[str, str] = {}
    used: set[str] = set()
    for col in columns:
        standard = lookup.get(normalize_header(col))
        if standard and standard not in used:
            mapping[str(col)] = standard
            used.add(standard)
    missing = [c for c in config.REQUIRED_COLUMNS if c not in used]
    return mapping, missing


# ---------------------------------------------------------------------------
# 파일 읽기
# ---------------------------------------------------------------------------
def _to_bytes(source: Any) -> bytes:
    if isinstance(source, (bytes, bytearray)):
        return bytes(source)
    if isinstance(source, (str, Path)):
        return Path(source).read_bytes()
    if hasattr(source, "getvalue"):
        return source.getvalue()
    if hasattr(source, "read"):
        data = source.read()
        if hasattr(source, "seek"):
            source.seek(0)
        return data
    raise LoaderError(f"지원하지 않는 입력 형식입니다: {type(source).__name__}")


def _source_name(source: Any, filename: str | None) -> str:
    if filename:
        return filename
    if isinstance(source, (str, Path)):
        return Path(source).name
    return getattr(source, "name", "uploaded")


def _extension(name: str) -> str:
    ext = Path(name).suffix.lower()
    if ext not in config.SUPPORTED_EXTENSIONS:
        raise LoaderError(
            f"'{name}': 지원하지 않는 파일 형식입니다. "
            f"{', '.join(config.SUPPORTED_EXTENSIONS)} 파일을 업로드하세요."
        )
    return ext


def read_csv_bytes(data: bytes) -> tuple[pd.DataFrame, str]:
    """CSV bytes를 인코딩을 순차 시도하며 읽는다. (DataFrame, 사용된 인코딩) 반환."""
    if not data.strip():
        raise LoaderError("파일이 비어 있습니다.")
    last_error: Exception | None = None
    for enc in config.CSV_ENCODINGS:
        try:
            text = data.decode(enc)
        except UnicodeDecodeError as exc:
            last_error = exc
            continue
        try:
            df = pd.read_csv(io.StringIO(text), skipinitialspace=True)
        except pd.errors.EmptyDataError as exc:
            raise LoaderError("파일이 비어 있습니다.") from exc
        except pd.errors.ParserError as exc:
            raise LoaderError(f"CSV 형식을 해석할 수 없습니다: {exc}") from exc
        return df, enc
    raise LoaderError(
        "CSV 인코딩을 인식할 수 없습니다. UTF-8 또는 CP949(엑셀 기본 저장)로 저장해 주세요."
    ) from last_error


def list_sheets(source: Any, filename: str | None = None) -> list[str]:
    """엑셀 파일의 시트 이름 목록. CSV이면 빈 리스트."""
    name = _source_name(source, filename)
    if _extension(name) == ".csv":
        return []
    try:
        with pd.ExcelFile(io.BytesIO(_to_bytes(source))) as xls:
            return [str(s) for s in xls.sheet_names]
    except Exception as exc:  # 손상 파일 등
        raise LoaderError(f"'{name}': 엑셀 파일을 열 수 없습니다 ({exc}).") from exc


def read_file(
    source: Any, filename: str | None = None, sheet_name: str | int | None = 0
) -> tuple[pd.DataFrame, str | None]:
    """CSV/XLSX/XLS 파일을 DataFrame으로 읽는다.

    Returns:
        (df, encoding_or_sheet): CSV이면 사용된 인코딩, 엑셀이면 읽은 시트명.
    """
    name = _source_name(source, filename)
    ext = _extension(name)
    data = _to_bytes(source)

    if ext == ".csv":
        df, info = read_csv_bytes(data)
    else:
        if not data:
            raise LoaderError(f"'{name}': 파일이 비어 있습니다.")
        try:
            with pd.ExcelFile(io.BytesIO(data)) as xls:
                sheet = xls.sheet_names[sheet_name] if isinstance(sheet_name, int) else sheet_name
                df = pd.read_excel(xls, sheet_name=sheet)
            info = str(sheet)
        except LoaderError:
            raise
        except Exception as exc:
            raise LoaderError(f"'{name}': 엑셀 파일을 읽을 수 없습니다 ({exc}).") from exc

    # 완전히 빈 행/열 제거, 헤더 공백 정리
    df = df.dropna(how="all").dropna(axis=1, how="all")
    df.columns = [str(c).strip() for c in df.columns]
    if df.empty:
        raise LoaderError(f"'{name}': 데이터 행이 없습니다 (헤더만 있거나 빈 파일).")
    return df, info


# ---------------------------------------------------------------------------
# 로드 결과 / 병합
# ---------------------------------------------------------------------------
@dataclass
class LoadedFile:
    """읽어 들인 파일 1개와 컬럼 매핑 상태."""

    name: str
    raw: pd.DataFrame
    mapping: dict[str, str] = field(default_factory=dict)  # {원본컬럼: 표준컬럼}
    missing: list[str] = field(default_factory=list)
    info: str | None = None  # CSV 인코딩 또는 엑셀 시트명
    zero_fill: list[str] = field(default_factory=list)  # 파일에 없어 0으로 채울 결함 컬럼

    @property
    def is_complete(self) -> bool:
        return not self.missing

    def set_mapping(self, mapping: dict[str, str], zero_fill: list[str] | None = None) -> None:
        """수동 매핑 적용. mapping은 {원본컬럼: 표준컬럼}. 같은 표준 컬럼 중복 지정 시 오류.

        zero_fill: 파일에 아예 없는 결함 컬럼을 0(미발생)으로 채우도록 지정. 결함 컬럼만 허용.
        """
        zero_fill = list(zero_fill or [])
        not_defect = [c for c in zero_fill if c not in config.DEFECT_TYPES]
        if not_defect:
            raise LoaderError(f"0으로 채울 수 있는 것은 결함 컬럼뿐입니다: {', '.join(not_defect)}")
        targets = [t for t in mapping.values() if t]
        dup = {t for t in targets if targets.count(t) > 1}
        if dup:
            raise LoaderError(f"같은 표준 컬럼에 여러 원본 컬럼이 지정되었습니다: {', '.join(sorted(dup))}")
        unknown = [c for c in mapping if c not in self.raw.columns]
        if unknown:
            raise LoaderError(f"파일에 없는 컬럼입니다: {', '.join(unknown)}")
        self.mapping = {k: v for k, v in mapping.items() if v}
        self.zero_fill = [c for c in zero_fill if c not in self.mapping.values()]
        self.missing = [c for c in config.REQUIRED_COLUMNS
                        if c not in self.mapping.values() and c not in self.zero_fill]

    def standardized(self) -> pd.DataFrame:
        """표준 컬럼 + 메타 컬럼(_원본파일, _원본행)만 남긴 DataFrame."""
        if not self.is_complete:
            raise LoaderError(f"'{self.name}': 필수 컬럼이 매핑되지 않았습니다: {', '.join(self.missing)}")
        df = self.raw[list(self.mapping.keys())].rename(columns=self.mapping)
        for col in self.zero_fill:
            df[col] = 0.0
        df = df[config.REQUIRED_COLUMNS].copy()
        df.insert(0, META_FILE, self.name)
        # 엑셀/CSV에서 보이는 행 번호 (헤더가 1행이므로 데이터는 2행부터)
        df.insert(1, META_ROW, self.raw.index.to_numpy() + 2)
        return df


def load_file(source: Any, filename: str | None = None, sheet_name: str | int | None = 0) -> LoadedFile:
    """파일 1개를 읽고 자동 컬럼 매핑까지 수행한다."""
    name = _source_name(source, filename)
    raw, info = read_file(source, filename=name, sheet_name=sheet_name)
    raw = raw.reset_index(drop=True)
    mapping, missing = auto_map_columns(list(raw.columns))
    return LoadedFile(name=name, raw=raw, mapping=mapping, missing=missing, info=info)


def merge_files(files: list[LoadedFile]) -> pd.DataFrame:
    """매핑이 완료된 파일들을 표준 스키마로 병합한다."""
    if not files:
        raise LoaderError("병합할 파일이 없습니다.")
    incomplete = [f.name for f in files if not f.is_complete]
    if incomplete:
        raise LoaderError(f"컬럼 매핑이 완료되지 않은 파일이 있습니다: {', '.join(incomplete)}")
    return pd.concat([f.standardized() for f in files], ignore_index=True)
