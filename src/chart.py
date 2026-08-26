"""Plotly 차트: 캔들 + 추세선 + 지지/저항 + 매물대."""

from __future__ import annotations

import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots

from .analysis import Analysis
from .signals import Signal, _fmt


def _price_text(value: float) -> str:
    return _fmt(value)


def build_chart(an: Analysis, sig: Signal, title: str) -> go.Figure:
    df = an.df
    if df is None or df.empty:
        fig = go.Figure()
        fig.update_layout(title="데이터 없음")
        return fig

    fig = make_subplots(
        rows=2,
        cols=2,
        shared_xaxes=True,
        shared_yaxes=False,
        column_widths=[0.78, 0.22],
        row_heights=[0.74, 0.26],
        specs=[[{"colspan": 1}, {"rowspan": 2}], [{}, None]],
        horizontal_spacing=0.02,
        vertical_spacing=0.04,
        subplot_titles=(title, "매물대", "거래량", ""),
    )

    fig.add_trace(
        go.Candlestick(
            x=df.index,
            open=df["open"],
            high=df["high"],
            low=df["low"],
            close=df["close"],
            name="일봉",
            increasing_line_color="#16a34a",
            decreasing_line_color="#dc2626",
        ),
        row=1,
        col=1,
    )

    if "ma20" in df.columns:
        fig.add_trace(
            go.Scatter(x=df.index, y=df["ma20"], name="MA20", line=dict(color="#2563eb", width=1.4)),
            row=1,
            col=1,
        )
    if "ma60" in df.columns:
        fig.add_trace(
            go.Scatter(x=df.index, y=df["ma60"], name="MA60", line=dict(color="#9333ea", width=1.4)),
            row=1,
            col=1,
        )

    x0 = df.index[0]
    x1 = df.index[-1]

    def hline(y: float, color: str, dash: str, name: str, width: float = 1.2):
        fig.add_trace(
            go.Scatter(
                x=[x0, x1],
                y=[y, y],
                mode="lines",
                name=name,
                line=dict(color=color, width=width, dash=dash),
                hovertemplate=f"{name}: {_price_text(y)}<extra></extra>",
            ),
            row=1,
            col=1,
        )

    for lv in an.supports[:4]:
        hline(lv.price, "#16a34a", "dash", f"지지 { _price_text(lv.price) }")
    for lv in an.resistances[:4]:
        hline(lv.price, "#dc2626", "dash", f"저항 { _price_text(lv.price) }")

    hline(an.price, "#0f172a", "solid", f"{an.price_label} {_price_text(an.price)}", 2.0)
    hline(an.poc, "#ca8a04", "solid", f"POC {_price_text(an.poc)}", 1.6)
    hline(an.val, "#a3a3a3", "dot", f"VAL {_price_text(an.val)}")
    hline(an.vah, "#a3a3a3", "dot", f"VAH {_price_text(an.vah)}")

    if an.up_line:
        i0, y0, i1, y1 = an.up_line
        i0 = int(max(0, min(len(df) - 1, i0)))
        i1 = int(max(0, min(len(df) - 1, i1)))
        fig.add_trace(
            go.Scatter(
                x=[df.index[i0], df.index[i1]],
                y=[y0, y1],
                mode="lines",
                name="상승 추세선",
                line=dict(color="#22c55e", width=2),
            ),
            row=1,
            col=1,
        )
    if an.down_line:
        i0, y0, i1, y1 = an.down_line
        i0 = int(max(0, min(len(df) - 1, i0)))
        i1 = int(max(0, min(len(df) - 1, i1)))
        fig.add_trace(
            go.Scatter(
                x=[df.index[i0], df.index[i1]],
                y=[y0, y1],
                mode="lines",
                name="하락 추세선",
                line=dict(color="#ef4444", width=2),
            ),
            row=1,
            col=1,
        )

    if an.swing_highs:
        fig.add_trace(
            go.Scatter(
                x=[t for t, _ in an.swing_highs],
                y=[p for _, p in an.swing_highs],
                mode="markers",
                name="스윙고점",
                marker=dict(color="#ef4444", size=8, symbol="triangle-down"),
            ),
            row=1,
            col=1,
        )
    if an.swing_lows:
        fig.add_trace(
            go.Scatter(
                x=[t for t, _ in an.swing_lows],
                y=[p for _, p in an.swing_lows],
                mode="markers",
                name="스윙저점",
                marker=dict(color="#22c55e", size=8, symbol="triangle-up"),
            ),
            row=1,
            col=1,
        )

    colors = ["#16a34a" if c >= o else "#dc2626" for o, c in zip(df["open"], df["close"])]
    fig.add_trace(
        go.Bar(x=df.index, y=df["volume"], name="거래량", marker_color=colors, opacity=0.7, showlegend=False),
        row=2,
        col=1,
    )

    if len(an.vp_centers):
        vp_colors = []
        for p in an.vp_centers:
            if abs(p - an.poc) < 1e-9:
                vp_colors.append("#ca8a04")
            elif an.val <= p <= an.vah:
                vp_colors.append("#64748b")
            else:
                vp_colors.append("#cbd5e1")
        fig.add_trace(
            go.Bar(
                x=an.vp_volumes,
                y=an.vp_centers,
                orientation="h",
                name="매물대",
                marker_color=vp_colors,
                showlegend=False,
                hovertemplate="가격 %{y}<br>거래량 %{x}<extra></extra>",
            ),
            row=1,
            col=2,
        )

    fig.update_layout(
        template="plotly_white",
        font=dict(family="Malgun Gothic, Apple SD Gothic Neo, sans-serif", size=13),
        legend=dict(orientation="h", yanchor="bottom", y=1.02, x=0),
        margin=dict(l=40, r=20, t=60, b=40),
        height=780,
        xaxis_rangeslider_visible=False,
        bargap=0.15,
        hovermode="x unified",
    )
    fig.update_yaxes(title_text="가격", row=1, col=1)
    fig.update_yaxes(matches="y", showticklabels=False, row=1, col=2)
    fig.update_xaxes(title_text="거래량 합", row=1, col=2)
    fig.update_yaxes(title_text="거래량", row=2, col=1)
    return fig
