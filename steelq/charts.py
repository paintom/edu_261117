"""Streamlit 화면용 Plotly 차트 (PRD F6).

Streamlit에 의존하지 않고 plotly Figure만 반환한다. 디자인 원칙:
- 보조축(이중 y축) 금지 — 생산량과 불량률은 x축을 공유하는 위·아래 두 패널로 나눈다.
- 결함 유형 색은 유형에 고정(config.DEFECT_COLORS) — 필터로 계열이 줄어도 색이 바뀌지 않는다.
- 상태 색(정상/주의/경고)은 상태 의미일 때만, 항상 라벨과 함께 쓴다.
- 얇은 마크, 연한 그리드, 선택적 직접 라벨, 한글 hover 툴팁.
"""

from __future__ import annotations

import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots

from steelq import aggregator as A
from steelq import config as C

DEFECTS = C.DEFECT_TYPES
PROD, TOTAL, RATE = C.COL_PRODUCTION, C.COL_TOTAL_DEFECT, C.COL_DEFECT_RATE
CRITICAL = C.SEVERITY_MARK_COLORS[C.SEVERITY_WARNING]
FONT = 'system-ui, -apple-system, "Segoe UI", "Malgun Gothic", "Apple SD Gothic Neo", "Noto Sans KR", sans-serif'
PLOTLY_CONFIG = {"displayModeBar": False, "locale": "ko"}
# 막대는 점마다 SVG 요소가 생겨 수천 개면 브라우저가 느려진다 → 이 개수를 넘으면 면적 선으로 그린다
MAX_BARS = 400


def _production_trace(x, y, hovertemplate: str):
    """생산량 계열. 점이 적으면 막대, 많으면(장기간 일별) 가벼운 면적 선."""
    if len(y) <= MAX_BARS:
        return go.Bar(x=x, y=y, name="생산량", marker_color=C.SERIES_PRIMARY, hovertemplate=hovertemplate)
    return go.Scatter(x=x, y=y, name="생산량", mode="lines", line=dict(color=C.SERIES_PRIMARY, width=1),
                      fill="tozeroy", fillcolor="rgba(42,120,214,0.25)", hovertemplate=hovertemplate)


def _base_layout(fig: go.Figure, height: int, legend: bool = True) -> go.Figure:
    fig.update_layout(
        height=height,
        margin=dict(l=8, r=16, t=40 if legend else 16, b=8),
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        font=dict(family=FONT, size=13, color=C.INK_SECONDARY),
        hoverlabel=dict(bgcolor="white", bordercolor=C.AXIS_COLOR, font=dict(family=FONT, size=13, color=C.INK_PRIMARY)),
        showlegend=legend,
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="left", x=0, title=None,
                    font=dict(size=12, color=C.INK_SECONDARY)),
        bargap=0.25,
        barcornerradius=4,
    )
    fig.update_xaxes(automargin=True, showgrid=False, linecolor=C.AXIS_COLOR, ticks="", tickfont=dict(color=C.INK_MUTED, size=12),
                     title_font=dict(size=12, color=C.INK_MUTED))
    fig.update_yaxes(automargin=True, gridcolor=C.GRID_COLOR, zeroline=False, linecolor=C.AXIS_COLOR, ticks="",
                     tickfont=dict(color=C.INK_MUTED, size=12), title_font=dict(size=12, color=C.INK_MUTED))
    return fig


def _target_line(fig: go.Figure, target: float, row: int | None = None, col: int | None = None) -> None:
    kw = dict(row=row, col=col) if row else {}
    fig.add_hline(y=target, line=dict(color=CRITICAL, width=1.5, dash="dash"),
                  annotation=dict(text=f"목표 {target:.2f}%", font=dict(size=11, color=CRITICAL),
                                  bgcolor="rgba(255,255,255,0.8)"),
                  annotation_position="top left", **kw)


# ---------------------------------------------------------------------------
# 생산량 · 불량률 추이 (위·아래 두 패널, x축 공유)
# ---------------------------------------------------------------------------
def production_rate_trend(df: pd.DataFrame, x: str, target: float, height: int = 460) -> go.Figure:
    """생산량 막대(위) + 불량률 꺾은선(아래, 목표선·초과 지점 강조). df는 daily/monthly 집계."""
    is_date = pd.api.types.is_datetime64_any_dtype(df[x])
    x_fmt = "%Y-%m-%d" if is_date else ""
    xs = df[x]
    fig = make_subplots(rows=2, cols=1, shared_xaxes=True, vertical_spacing=0.1, row_heights=[0.42, 0.58],
                        subplot_titles=("생산량 (톤)", "불량률 (%)"))
    fig.add_trace(_production_trace(
        xs, df[PROD],
        f"%{{x{('|' + x_fmt) if x_fmt else ''}}}<br>생산량 <b>%{{y:,.1f}}톤</b><extra></extra>",
    ), row=1, col=1)
    fig.add_trace(go.Scatter(
        x=xs, y=df[RATE], name="불량률", mode="lines+markers" if len(df) <= 40 else "lines",
        line=dict(color=C.BRAND_NAVY, width=2), marker=dict(size=8, color=C.BRAND_NAVY),
        customdata=df[[TOTAL]].to_numpy(),
        hovertemplate=(f"%{{x{('|' + x_fmt) if x_fmt else ''}}}<br>불량률 <b>%{{y:.2f}}%</b>"
                       "<br>불량량 %{customdata[0]:,.1f}톤<extra></extra>"),
    ), row=2, col=1)
    over = df[df[RATE] > target]
    if not over.empty:
        fig.add_trace(go.Scatter(
            x=over[x], y=over[RATE], name="목표 초과", mode="markers+text" if len(over) <= 8 else "markers",
            marker=dict(size=10, color=CRITICAL, line=dict(color="white", width=2)),
            text=[f"{v:.2f}%" for v in over[RATE]], textposition="top center",
            textfont=dict(size=11, color=C.INK_SECONDARY),
            hovertemplate=f"%{{x{('|' + x_fmt) if x_fmt else ''}}}<br>목표 초과 <b>%{{y:.2f}}%</b><extra></extra>",
        ), row=2, col=1)
    _target_line(fig, target, row=2, col=1)
    ymax = max(float(df[RATE].max() if df[RATE].notna().any() else 0), target) * 1.18
    fig.update_yaxes(range=[0, ymax], ticksuffix="%", row=2, col=1)
    fig.update_yaxes(tickformat=",.0f", rangemode="tozero", row=1, col=1)
    if is_date:
        fig.update_xaxes(tickformat="%m-%d", hoverformat="%Y-%m-%d")
    else:
        fig.update_xaxes(type="category")
    _base_layout(fig, height)
    fig.update_layout(hovermode="x unified" if is_date else "closest", showlegend=False)
    for ann in fig.layout.annotations:  # 패널 제목은 축 라벨처럼 차분하게
        if ann.text in ("생산량 (톤)", "불량률 (%)"):
            ann.update(font=dict(size=12, color=C.INK_SECONDARY), x=0, xanchor="left")
    return fig


# ---------------------------------------------------------------------------
# 결함
# ---------------------------------------------------------------------------
def defect_ranked_bars(defect_total: pd.DataFrame, height: int = 300) -> go.Figure:
    """결함 유형 순위 막대 (Pareto). 누적 점유율은 보조축 대신 라벨로 표기."""
    df = defect_total.iloc[::-1]  # 가로 막대는 아래부터 그려지므로 뒤집어 1위가 위
    fig = go.Figure(go.Bar(
        x=df[A.COL_SHARE], y=df[A.COL_DEFECT_TYPE], orientation="h",
        marker_color=[C.DEFECT_COLORS.get(d, C.SERIES_PRIMARY) for d in df[A.COL_DEFECT_TYPE]],
        text=[f"{s:.1f}%  · 누적 {c:.1f}%" for s, c in zip(df[A.COL_SHARE], df[A.COL_CUM_SHARE])],
        textposition="outside", textfont=dict(size=12, color=C.INK_SECONDARY), cliponaxis=False,
        customdata=df[[A.COL_DEFECT_AMOUNT, A.COL_CUM_SHARE]].to_numpy(),
        hovertemplate="<b>%{y}</b><br>점유율 %{x:.2f}%<br>불량량 %{customdata[0]:,.1f}톤"
                      "<br>누적 %{customdata[1]:.1f}%<extra></extra>",
    ))
    xmax = float(df[A.COL_SHARE].max() or 0) * 2.0
    fig.update_xaxes(range=[0, max(xmax, 1)], ticksuffix="%", showgrid=True, gridcolor=C.GRID_COLOR)
    fig.update_yaxes(showgrid=False)
    return _base_layout(fig, height, legend=False)


def defect_share_trend(trend: pd.DataFrame, freq: str, height: int = 360) -> go.Figure:
    """결함 유형별 점유율 추이. freq="D"면 100% 누적 영역, "M"이면 100% 누적 막대."""
    fig = go.Figure()
    x = trend[A.COL_PERIOD]
    for d in DEFECTS:
        color = C.DEFECT_COLORS[d]
        common = dict(name=d, x=x, y=trend[d],
                      hovertemplate=f"{d} <b>%{{y:.1f}}%</b><extra></extra>")
        if freq == "D":
            fig.add_trace(go.Scatter(**common, mode="lines", stackgroup="one", groupnorm="percent",
                                     line=dict(width=0.5, color="white"), fillcolor=color))
        else:
            fig.add_trace(go.Bar(**common, marker=dict(color=color, line=dict(color="white", width=2)),
                                 text=[f"{v:.0f}%" if v >= 6 else "" for v in trend[d]],
                                 textposition="inside", insidetextanchor="middle",
                                 textfont=dict(size=11, color="#0b0b0b" if d == "치수불량" else "white")))
    if freq == "M":
        fig.update_layout(barmode="stack")
        fig.update_xaxes(type="category")
    else:
        fig.update_xaxes(tickformat="%m-%d", hoverformat="%Y-%m-%d")
    fig.update_yaxes(range=[0, 100], ticksuffix="%")
    _base_layout(fig, height)
    # 누적 차트에서 Plotly는 범례를 자동으로 뒤집으므로 결함 유형 정의 순서로 고정
    fig.update_layout(hovermode="x unified", barcornerradius=0, legend_traceorder="normal")
    return fig


def line_defect_heatmap(matrix_share: pd.DataFrame, matrix_ton: pd.DataFrame, height: int = 300) -> go.Figure:
    """라인 × 결함 구성비(%) 히트맵. 셀에 % 표기, hover에 톤 표시."""
    z = matrix_share[DEFECTS].to_numpy()
    tons = matrix_ton.set_index(C.COL_LINE).loc[matrix_share[C.COL_LINE], DEFECTS].to_numpy()
    zmax = float(pd.DataFrame(z).max().max() or 1)
    text = [[f"{v:.1f}%" for v in row] for row in z]
    text_colors = [["white" if v > zmax * 0.42 else C.INK_PRIMARY for v in row] for row in z]
    fig = go.Figure(go.Heatmap(
        z=z, x=DEFECTS, y=matrix_share[C.COL_LINE], customdata=tons,
        colorscale=[[i / (len(C.SEQUENTIAL_BLUE) - 1), c] for i, c in enumerate(C.SEQUENTIAL_BLUE)],
        zmin=0, zmax=zmax, xgap=2, ygap=2,
        colorbar=dict(title=dict(text="구성비", font=dict(size=11)), ticksuffix="%", thickness=10, len=0.9),
        hovertemplate="<b>%{y}</b> · %{x}<br>라인 내 구성비 %{z:.1f}%<br>불량량 %{customdata:,.1f}톤<extra></extra>",
    ))
    for i, line in enumerate(matrix_share[C.COL_LINE]):
        for j, d in enumerate(DEFECTS):
            fig.add_annotation(x=d, y=line, text=text[i][j], showarrow=False,
                               font=dict(size=12, color=text_colors[i][j]))
    fig.update_yaxes(autorange="reversed", showgrid=False)
    fig.update_xaxes(side="top")
    _base_layout(fig, height, legend=False)
    fig.update_layout(margin=dict(l=8, r=8, t=32, b=8))
    return fig


def group_rate_bars(df: pd.DataFrame, by: str, target: float, height: int | None = None) -> go.Figure:
    """라인/강종별 불량률 막대. 목표 초과 막대만 경고 색 + '목표 초과' 라벨."""
    d = df.dropna(subset=[RATE]).iloc[::-1]
    over = d[RATE] > target
    fig = go.Figure(go.Bar(
        x=d[RATE], y=d[by], orientation="h",
        marker_color=[CRITICAL if o else C.SERIES_PRIMARY for o in over],
        text=[f"{r:.2f}%" + ("  ▲ 목표 초과" if o else "") for r, o in zip(d[RATE], over)],
        textposition="outside", cliponaxis=False, textfont=dict(size=12, color=C.INK_SECONDARY),
        customdata=d[[PROD, TOTAL]].to_numpy(),
        hovertemplate="<b>%{y}</b><br>불량률 %{x:.2f}%<br>생산량 %{customdata[0]:,.1f}톤"
                      "<br>불량량 %{customdata[1]:,.1f}톤<extra></extra>",
    ))
    fig.add_vline(x=target, line=dict(color=CRITICAL, width=1.5, dash="dash"),
                  annotation=dict(text=f"목표 {target:.2f}%", font=dict(size=11, color=CRITICAL)),
                  annotation_position="bottom right")
    xmax = max(float(d[RATE].max() if len(d) else 0), target) * 1.7
    fig.update_xaxes(range=[0, xmax], ticksuffix="%", showgrid=True, gridcolor=C.GRID_COLOR)
    fig.update_yaxes(showgrid=False, type="category")
    return _base_layout(fig, height or max(180, 44 * len(d) + 60), legend=False)


# ---------------------------------------------------------------------------
# 이상치
# ---------------------------------------------------------------------------
def anomaly_series(series: pd.DataFrame, anomalies: pd.DataFrame, target: float | None,
                   height: int = 480) -> go.Figure:
    """한 라인(또는 공장 전체)의 일별 불량률·생산량에 이상치 지점을 심각도 색 마커로 표시."""
    fig = make_subplots(rows=2, cols=1, shared_xaxes=True, vertical_spacing=0.1, row_heights=[0.58, 0.42],
                        subplot_titles=("불량률 (%)", "생산량 (톤)"))
    x = series[C.COL_DATE]
    fig.add_trace(go.Scatter(x=x, y=series[RATE], mode="lines", name="불량률",
                             line=dict(color=C.BRAND_NAVY, width=2),
                             hovertemplate="불량률 %{y:.2f}%<extra></extra>"), row=1, col=1)
    fig.add_trace(_production_trace(x, series[PROD], "생산량 %{y:,.1f}톤<extra></extra>"), row=2, col=1)
    if target is not None:
        _target_line(fig, target, row=1, col=1)

    if not anomalies.empty:
        shown: set[str] = set()
        for sev in [C.SEVERITY_CAUTION, C.SEVERITY_WARNING]:
            for metric, row in [("불량률(%)", 1), ("생산량(톤)", 2)]:
                a = anomalies[(anomalies["심각도"] == sev) & (anomalies["지표"] == metric)]
                if a.empty:
                    continue
                a = a.groupby("일자").agg(값=("값", "first"), 탐지방법=("탐지방법", " · ".join)).reset_index()
                fig.add_trace(go.Scatter(
                    x=a["일자"], y=a["값"], mode="markers", name=f"{sev} 이상치",
                    legendgroup=sev, showlegend=sev not in shown,
                    marker=dict(size=12 if sev == C.SEVERITY_WARNING else 10, color=C.SEVERITY_MARK_COLORS[sev],
                                symbol="diamond" if sev == C.SEVERITY_WARNING else "circle",
                                line=dict(color="white", width=2)),
                    customdata=a[["탐지방법"]].to_numpy(),
                    hovertemplate=f"<b>{sev}</b> · %{{customdata[0]}}<br>%{{y:,.2f}}<extra></extra>",
                ), row=row, col=1)
                shown.add(sev)
    fig.update_yaxes(rangemode="tozero", ticksuffix="%", row=1, col=1)
    fig.update_yaxes(rangemode="tozero", tickformat=",.0f", row=2, col=1)
    fig.update_xaxes(tickformat="%m-%d", hoverformat="%Y-%m-%d")
    _base_layout(fig, height)
    # 범례를 패널 제목 위로 올려 겹치지 않게 한다
    fig.update_layout(hovermode="x unified", margin=dict(t=64), legend=dict(y=1.13))
    for ann in fig.layout.annotations:
        if ann.text in ("생산량 (톤)", "불량률 (%)"):
            ann.update(font=dict(size=12, color=C.INK_SECONDARY), x=0, xanchor="left")
    return fig
