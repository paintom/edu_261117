"""규칙 기반 자동 분석 코멘트 (PRD F5, 규칙 C1~C10).

외부 API 없이 템플릿 + 규칙으로 생성한다. 템플릿·임계값은 config.py에 둔다.
조건을 충족한 규칙만 출력하며, 순서는 요약 → 이슈 → 데이터 품질(config.COMMENT_ORDER).
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from steelq import aggregator, cleaner, config

C = config
T = C.COMMENT_TEMPLATES
RATE = C.COL_DEFECT_RATE


@dataclass
class Comment:
    rule: str        # "C1" ~ "C10"
    severity: str    # 정상 | 주의 | 경고
    text: str

    @property
    def category(self) -> str:
        return C.COMMENT_CATEGORIES[self.rule]

    @property
    def icon(self) -> str:
        return C.SEVERITY_ICONS[self.severity]


def _ton(v: float) -> str:
    return f"{v:,.{C.TON_DECIMALS}f}"


def _pct(v: float) -> str:
    return f"{v:.{C.RATE_DECIMALS}f}"


def josa_ro(word: str) -> str:
    """받침 유무에 따라 '로'/'으로'. 받침 없음·ㄹ받침 → 로, 그 외 받침 → 으로, 한글이 아니면 '(으)로'."""
    if not word:
        return "(으)로"
    ch = word[-1]
    if "가" <= ch <= "힣":
        jong = (ord(ch) - 0xAC00) % 28
        return "로" if jong in (0, 8) else "으로"
    if ch.isdigit():
        return "으로" if ch in "0136789" else "로"
    return "(으)로"


def _date(v) -> str:
    return pd.Timestamp(v).strftime("%Y-%m-%d")


# ---------------------------------------------------------------------------
# 규칙
# ---------------------------------------------------------------------------
def c1_summary(kpi: dict) -> Comment:
    return Comment("C1", C.SEVERITY_NORMAL, T["C1"].format(
        start=_date(kpi["start"]), end=_date(kpi["end"]), days=kpi["days"],
        production=_ton(kpi["production"]), defect=_ton(kpi["defect"]), rate=_pct(kpi["rate"])))


def c2_target(kpi: dict, target: float) -> Comment | None:
    r = kpi["rate"]
    if np.isnan(r) or target <= 0:
        return None
    diff = r - target
    if r > target:
        sev = C.SEVERITY_WARNING
    elif r >= target * C.TARGET_CAUTION_RATIO:
        sev = C.SEVERITY_CAUTION
    else:
        sev = C.SEVERITY_NORMAL
    direction = "초과" if diff > 0 else "미달"
    return Comment("C2", sev, T["C2"].format(target=f"{target:g}", diff=_pct(abs(diff)), direction=direction))


def c3_month(monthly: pd.DataFrame) -> Comment | None:
    if len(monthly) < 2:
        return None
    cur, prev = monthly.iloc[-1], monthly.iloc[-2]
    diff = cur[RATE] - prev[RATE]
    if np.isnan(diff):
        return None
    if diff >= C.MONTH_RATE_WARNING:
        sev = C.SEVERITY_WARNING
    elif diff >= C.MONTH_RATE_CAUTION:
        sev = C.SEVERITY_CAUTION
    else:
        sev = C.SEVERITY_NORMAL
    direction = "상승" if diff > 0 else ("하락" if diff < 0 else "동일")
    return Comment("C3", sev, T["C3"].format(
        month=cur[aggregator.COL_MONTH], prev_month=prev[aggregator.COL_MONTH],
        rate=_pct(cur[RATE]), diff=_pct(abs(diff)), direction=direction))


def c4_top_defect(share: pd.DataFrame) -> Comment | None:
    if share.empty or share[aggregator.COL_SHARE].isna().all():
        return None
    top = share.iloc[0]
    sev = C.SEVERITY_CAUTION if top[aggregator.COL_SHARE] >= C.TOP_DEFECT_CAUTION_SHARE else C.SEVERITY_NORMAL
    return Comment("C4", sev, T["C4"].format(
        defect=top[aggregator.COL_DEFECT_TYPE], ro=josa_ro(top[aggregator.COL_DEFECT_TYPE]), share=f"{top[aggregator.COL_SHARE]:.1f}"))


def c5_defect_trend(monthly_share: pd.DataFrame) -> Comment | None:
    if len(monthly_share) < 2:
        return None
    cur = monthly_share.iloc[-1][C.DEFECT_TYPES].astype(float)
    prev = monthly_share.iloc[-2][C.DEFECT_TYPES].astype(float)
    diff = (cur - prev).dropna()
    if diff.empty:
        return None
    defect = diff.idxmax()
    if diff[defect] < C.MONTHLY_SHARE_CHANGE_ALERT:
        return None
    sev = C.SEVERITY_WARNING if diff[defect] >= C.MONTHLY_SHARE_CHANGE_ALERT * 2 else C.SEVERITY_CAUTION
    return Comment("C5", sev, T["C5"].format(
        defect=defect, prev=f"{prev[defect]:.1f}", curr=f"{cur[defect]:.1f}", diff=f"{diff[defect]:.1f}"))


def _worst_group(groups: pd.DataFrame, by: str, kpi: dict) -> tuple[pd.Series, str] | None:
    """평균 대비 배수가 기준 이상인 최악 그룹. 그룹이 1개뿐이면 비교 의미가 없어 None."""
    valid = groups.dropna(subset=[RATE])
    if len(valid) < 2 or np.isnan(kpi["rate"]) or kpi["rate"] <= 0:
        return None
    top = valid.iloc[0]
    ratio = top[aggregator.COL_RATIO]
    if ratio < C.GROUP_RATIO_ALERT:
        return None
    sev = C.SEVERITY_WARNING if ratio >= C.GROUP_RATIO_WARNING else C.SEVERITY_CAUTION
    return top, sev


def c6_line(by_line: pd.DataFrame, kpi: dict) -> Comment | None:
    found = _worst_group(by_line, C.COL_LINE, kpi)
    if not found:
        return None
    top, sev = found
    return Comment("C6", sev, T["C6"].format(
        line=top[C.COL_LINE], rate=_pct(top[RATE]), avg=_pct(kpi["rate"]), ratio=f"{top[aggregator.COL_RATIO]:.1f}"))


def c7_grade(by_grade: pd.DataFrame, kpi: dict) -> Comment | None:
    found = _worst_group(by_grade, C.COL_GRADE, kpi)
    if not found:
        return None
    top, sev = found
    return Comment("C7", sev, T["C7"].format(
        grade=top[C.COL_GRADE], rate=_pct(top[RATE]), ratio=f"{top[aggregator.COL_RATIO]:.1f}"))


def c8_anomalies(anomalies: pd.DataFrame) -> Comment | None:
    if anomalies.empty:
        return None
    warn = int((anomalies["심각도"] == C.SEVERITY_WARNING).sum())
    # 가장 심각한 건: 심각도 순 정렬된 첫 행 중 불량률 지표 우선, 값이 가장 큰 것
    top_sev = anomalies["심각도"].map(C.SEVERITY_ORDER).min()
    cand = anomalies[anomalies["심각도"].map(C.SEVERITY_ORDER) == top_sev]
    rate_cand = cand[cand["지표"].str.startswith("불량률") & (cand["라인"] != "전체")]
    pick = (rate_cand if not rate_cand.empty else cand).sort_values("값", ascending=False).iloc[0]
    unit = "%" if "(%)" in pick["지표"] else "톤"
    metric = pick["지표"].split("(")[0]
    value = f"{pick['값']:.2f}%" if unit == "%" else f"{_ton(pick['값'])}톤"
    return Comment("C8", C.SEVERITY_WARNING if warn else C.SEVERITY_CAUTION, T["C8"].format(
        count=len(anomalies), warn=warn, date=_date(pick["일자"]), line=pick["라인"], metric=metric, value=value))


def c9_trend(daily: pd.DataFrame) -> Comment | None:
    d = daily.dropna(subset=[RATE]).tail(C.TREND_DAYS)
    if len(d) < 7:
        return None
    x = np.arange(len(d), dtype=float)
    slope = np.polyfit(x, d[RATE].to_numpy(dtype=float), 1)[0]
    change = slope * (len(d) - 1)
    if change > C.TREND_THRESHOLD:
        trend, sev = "상승", C.SEVERITY_CAUTION
    elif change < -C.TREND_THRESHOLD:
        trend, sev = "하락", C.SEVERITY_NORMAL
    else:
        trend, sev = "안정", C.SEVERITY_NORMAL
    return Comment("C9", sev, T["C9"].format(days=len(d), trend=trend, change=change))


def c10_data_quality(clean_log: pd.DataFrame) -> Comment | None:
    if clean_log is None or clean_log.empty:
        return None
    rules = clean_log["규칙"]
    dup = int(rules.isin(cleaner.DUPLICATE_RULES).sum())
    missing = int(rules.isin(cleaner.MISSING_RULES).sum())
    negative = int(rules.isin(cleaner.NEGATIVE_RULES).sum())
    if dup + missing + negative == 0:
        return None
    return Comment("C10", C.SEVERITY_CAUTION, T["C10"].format(dup=dup, missing=missing, negative=negative))


# ---------------------------------------------------------------------------
# 진입점
# ---------------------------------------------------------------------------
def generate_comments(
    data: pd.DataFrame,
    anomalies: pd.DataFrame,
    clean_log: pd.DataFrame | None,
    target_rate: float = C.TARGET_DEFECT_RATE,
) -> list[Comment]:
    """조건을 충족하는 규칙의 코멘트를 요약→이슈→데이터 순으로 반환 (최대 COMMENT_MAX_LINES줄)."""
    if data.empty:
        return []
    kpi = aggregator.overall_summary(data)
    if np.isnan(kpi["rate"]):
        return [Comment("C1", C.SEVERITY_CAUTION, "생산량이 0이어서 불량률을 계산할 수 없습니다.")]

    found = [
        c1_summary(kpi),
        c2_target(kpi, target_rate),
        c3_month(aggregator.monthly_summary(data)),
        c9_trend(aggregator.daily_summary(data)),
        c4_top_defect(aggregator.defect_share(data)),
        c5_defect_trend(aggregator.defect_share_trend(data, "M")),
        c6_line(aggregator.group_summary(data, C.COL_LINE), kpi),
        c7_grade(aggregator.group_summary(data, C.COL_GRADE), kpi),
        c8_anomalies(anomalies),
        c10_data_quality(clean_log),
    ]
    comments = [c for c in found if c is not None]
    for rule in C.COMMENT_DROP_PRIORITY:
        if len(comments) <= C.COMMENT_MAX_LINES:
            break
        comments = [c for c in comments if c.rule != rule]
    comments.sort(key=lambda c: C.COMMENT_ORDER.index(c.rule))
    return comments[: C.COMMENT_MAX_LINES]
