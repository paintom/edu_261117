"""철강 생산·품질 가상 샘플 데이터 생성 (PRD F8).

앱의 "샘플 데이터로 실행" 버튼과 `generate_sample.py` CLI가 함께 사용한다.
의도적으로 주입한 데이터 오류·이상치 내역을 별도 DataFrame으로 반환하여
정제 로그 건수와 이상치 탐지율 검증의 기준으로 쓴다.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from steelq import config

C = config
DEFECTS = C.DEFECT_TYPES

# 라인별 강종 배정과 강종별 생산 비중
LINE_GRADES: dict[str, dict[str, float]] = {
    "열연1라인": {"SS400": 0.45, "SM490": 0.30, "SPHC": 0.25},
    "열연2라인": {"SS400": 0.55, "SPHC": 0.45},
    "냉연1라인": {"SPCC": 0.65, "SPHC": 0.35},
    "후판1라인": {"SM490": 0.60, "API-X65": 0.40},
}
# 라인별 일 생산량 기준(톤)과 기본 불량률(%)
LINE_BASE_TON = {"열연1라인": 2400, "열연2라인": 2000, "냉연1라인": 1300, "후판1라인": 1800}
LINE_BASE_RATE = {"열연1라인": 0.65, "열연2라인": 0.80, "냉연1라인": 0.55, "후판1라인": 0.95}
GRADE_RATE_FACTOR = {"SS400": 0.95, "SM490": 1.05, "SPHC": 1.0, "SPCC": 0.9, "API-X65": 1.2}
# 라인 특성별 결함 가중치 (스크래치, 압연흠, 스케일, 치수불량, 표면균열)
LINE_DEFECT_WEIGHTS = {
    "열연1라인": [0.20, 0.25, 0.30, 0.15, 0.10],
    "열연2라인": [0.15, 0.30, 0.30, 0.15, 0.10],
    "냉연1라인": [0.45, 0.15, 0.10, 0.20, 0.10],
    "후판1라인": [0.10, 0.20, 0.20, 0.25, 0.25],
}
SCALE_TREND = 1.5       # 기간 말 스케일 가중치 = 초기 × (1 + 1.5)
WEEKEND_FACTOR = 0.7    # 주말 감산
DIRICHLET_CONC = 60     # 클수록 일별 결함 구성이 라인 평균에 가깝게 유지

# 이상치·오류 주입 비율 / 건수
N_RATE_SPIKES = 4
N_PROD_DROPS = 2
RATIO_FULL_DUP = 0.01
RATIO_KEY_DUP = 0.005
RATIO_MISSING = 0.02
RATIO_NEGATIVE = 0.005
RATIO_COMMA = 0.03

KIND_ANOMALY = "이상치"
KIND_ERROR = "데이터오류"


@dataclass
class SampleResult:
    data: pd.DataFrame       # 오류·이상치가 주입된 원본 형태 데이터
    injected: pd.DataFrame   # 주입 내역 (구분, 유형, 일자, 라인, 강종, 컬럼, 원래값, 주입값)


def _base_data(rng: np.random.Generator, start: str, end: str) -> pd.DataFrame:
    dates = pd.date_range(start, end, freq="D")
    n_days = len(dates)
    rows = []
    for d_idx, date in enumerate(dates):
        t = d_idx / max(n_days - 1, 1)
        weekend = WEEKEND_FACTOR if date.day_of_week >= 5 else 1.0
        for line, grades in LINE_GRADES.items():
            weights = np.array(LINE_DEFECT_WEIGHTS[line], dtype=float)
            weights[DEFECTS.index("스케일")] *= 1 + SCALE_TREND * t
            weights /= weights.sum()
            for grade, share in grades.items():
                prod = LINE_BASE_TON[line] * share * weekend * rng.normal(1.0, 0.08)
                prod = max(prod, 10.0)
                rate = LINE_BASE_RATE[line] * GRADE_RATE_FACTOR[grade] * rng.lognormal(0, 0.2)
                total_defect = prod * rate / 100
                split = rng.dirichlet(weights * DIRICHLET_CONC)
                row = {C.COL_DATE: date, C.COL_LINE: line, C.COL_GRADE: grade, C.COL_PRODUCTION: round(prod, 1)}
                row.update({d: round(total_defect * s, 2) for d, s in zip(DEFECTS, split)})
                rows.append(row)
    return pd.DataFrame(rows)


def _record(log: list, kind: str, typ: str, row: pd.Series, col: str | None, before, after) -> None:
    log.append({
        "구분": kind, "유형": typ,
        C.COL_DATE: row[C.COL_DATE], C.COL_LINE: row[C.COL_LINE], C.COL_GRADE: row[C.COL_GRADE],
        "컬럼": col, "원래값": before, "주입값": after,
    })


def _inject_anomalies(df: pd.DataFrame, rng: np.random.Generator, log: list) -> set[int]:
    """라인-일자 단위로 불량률 급등·생산량 급감을 주입. 영향받은 행 인덱스 반환."""
    dates = sorted(df[C.COL_DATE].unique())
    # 이동평균 기준이 쌓이도록 첫 14일 이후, 평일만
    candidates = [(d, l) for d in dates[14:] for l in LINE_GRADES if pd.Timestamp(d).day_of_week < 5]
    picks = rng.choice(len(candidates), size=N_RATE_SPIKES + N_PROD_DROPS, replace=False)
    touched: set[int] = set()
    for i, p in enumerate(picks):
        date, line = candidates[p]
        idx = df.index[(df[C.COL_DATE] == date) & (df[C.COL_LINE] == line)]
        if i < N_RATE_SPIKES:
            factor = float(rng.uniform(3.5, 5.0))
            before_rate = df.loc[idx, DEFECTS].to_numpy().sum() / df.loc[idx, C.COL_PRODUCTION].sum() * 100
            df.loc[idx, DEFECTS] = (df.loc[idx, DEFECTS] * factor).round(2)
            after_rate = df.loc[idx, DEFECTS].to_numpy().sum() / df.loc[idx, C.COL_PRODUCTION].sum() * 100
            _record(log, KIND_ANOMALY, "불량률급등", df.loc[idx[0]], C.COL_DEFECT_RATE,
                    round(before_rate, 2), round(after_rate, 2))
        else:
            factor = float(rng.uniform(0.15, 0.3))
            before = round(df.loc[idx, C.COL_PRODUCTION].sum(), 1)
            df.loc[idx, C.COL_PRODUCTION] = (df.loc[idx, C.COL_PRODUCTION] * factor).round(1)
            df.loc[idx, DEFECTS] = (df.loc[idx, DEFECTS] * factor).round(2)
            _record(log, KIND_ANOMALY, "생산량급감", df.loc[idx[0]], C.COL_PRODUCTION,
                    before, round(df.loc[idx, C.COL_PRODUCTION].sum(), 1))
        touched.update(idx.tolist())
    # 이상치 기록의 강종은 라인-일자 단위이므로 '전체'로 표기
    for rec in log:
        rec[C.COL_GRADE] = "전체"
    return touched


def _inject_errors(df: pd.DataFrame, rng: np.random.Generator, log: list, protected: set[int]) -> pd.DataFrame:
    n = len(df)
    pool = [i for i in df.index if i not in protected]
    rng.shuffle(pool)
    take = lambda k: [pool.pop() for _ in range(k)]  # noqa: E731 - 겹치지 않게 순서대로 소비

    n_missing = max(1, round(n * RATIO_MISSING))
    n_negative = max(1, round(n * RATIO_NEGATIVE))
    n_comma = max(1, round(n * RATIO_COMMA))
    n_full_dup = max(1, round(n * RATIO_FULL_DUP))
    n_key_dup = max(1, round(n * RATIO_KEY_DUP))

    # 결측: 대부분 수치 컬럼, 일부 라인/강종/일자
    missing_cols = [C.COL_PRODUCTION] * 4 + DEFECTS * 2 + [C.COL_LINE, C.COL_GRADE, C.COL_DATE]
    for i in take(n_missing):
        col = missing_cols[rng.integers(len(missing_cols))]
        _record(log, KIND_ERROR, "결측", df.loc[i], col, df.at[i, col], None)
        df.at[i, col] = np.nan if col != C.COL_DATE else pd.NaT

    # 음수
    for i in take(n_negative):
        col = [C.COL_PRODUCTION, *DEFECTS][rng.integers(len(DEFECTS) + 1)]
        before = df.at[i, col]
        df.at[i, col] = -before
        _record(log, KIND_ERROR, "음수", df.loc[i], col, before, -before)

    # 콤마 포함 숫자 문자열 (생산량)
    # 천 단위 콤마가 실제로 생기도록 1,000톤 이상 행에서 고른다
    df[C.COL_PRODUCTION] = df[C.COL_PRODUCTION].astype(object)
    big = [i for i in reversed(pool) if df.at[i, C.COL_PRODUCTION] >= 1000][:n_comma]
    for i in big:
        pool.remove(i)
    for i in big:
        before = df.at[i, C.COL_PRODUCTION]
        text = f"{before:,.1f}"
        df.at[i, C.COL_PRODUCTION] = text
        _record(log, KIND_ERROR, "콤마문자열", df.loc[i], C.COL_PRODUCTION, before, text)

    # 완전 중복 / 키 중복 (값이 다른 같은 일자+라인+강종)
    extra = []
    for i in take(n_full_dup):
        extra.append(df.loc[i].copy())
        _record(log, KIND_ERROR, "완전중복", df.loc[i], None, None, None)
    for i in take(n_key_dup):
        dup = df.loc[i].copy()
        before = dup[C.COL_PRODUCTION]
        dup[C.COL_PRODUCTION] = round(float(before) * float(rng.uniform(0.9, 1.1)), 1)
        extra.append(dup)
        _record(log, KIND_ERROR, "키중복", df.loc[i], C.COL_PRODUCTION, before, dup[C.COL_PRODUCTION])

    df = pd.concat([df, pd.DataFrame(extra)], ignore_index=True)
    # 일자 결측 행은 맨 뒤, 나머지는 일자·라인·강종 순 (중복 행이 원본 바로 뒤에 오도록 stable 정렬)
    return df.sort_values([C.COL_DATE, C.COL_LINE, C.COL_GRADE], kind="stable", na_position="last").reset_index(drop=True)


def generate_sample(
    seed: int = C.RANDOM_SEED, start: str = "2026-08-01", end: str = "2026-10-31"
) -> SampleResult:
    """오류·이상치가 주입된 가상 샘플 데이터를 생성한다. 같은 seed면 항상 같은 결과."""
    rng = np.random.default_rng(seed)
    df = _base_data(rng, start, end)
    log: list[dict] = []
    touched = _inject_anomalies(df, rng, log)
    df = _inject_errors(df, rng, log, touched)
    injected = pd.DataFrame(log)
    return SampleResult(data=df, injected=injected)
