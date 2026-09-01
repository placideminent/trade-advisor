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
    if "ma200" in df.columns:
        ma_n = int(getattr(an, "ma_long_n", None) or 200)
        fig.add_trace(
            go.Scatter(
                x=df.index,
                y=df["ma200"],
                name=f"MA{ma_n}",
                line=dict(color="#0f766e", width=1.6),
            ),
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
        margin=dict(l=28, r=12, t=48, b=28),
        height=560,
        xaxis_rangeslider_visible=False,
        bargap=0.15,
        hovermode="x unified",
    )
    fig.update_yaxes(title_text="가격", row=1, col=1)
    fig.update_yaxes(matches="y", showticklabels=False, row=1, col=2)
    fig.update_xaxes(title_text="거래량 합", row=1, col=2)
    fig.update_yaxes(title_text="거래량", row=2, col=1)
    return fig


def _day_bars(df: pd.DataFrame) -> dict:
    out = {}
    if df is None or getattr(df, "empty", True):
        return out
    for ts, row in df.iterrows():
        d = ts.date() if hasattr(ts, "date") else ts
        out[d] = (ts, row)
    return out


def build_sim_chart(df: pd.DataFrame, marks: list[dict], title: str) -> go.Figure:
    """시뮬레이션 기간 일봉 + 매수/매도 신호 표시."""
    fig = make_subplots(
        rows=2,
        cols=1,
        shared_xaxes=True,
        row_heights=[0.76, 0.24],
        vertical_spacing=0.04,
        subplot_titles=(title, ""),
    )
    if df is None or getattr(df, "empty", True):
        fig.update_layout(title="데이터 없음")
        return fig

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

    buy_x, buy_y, buy_text, buy_size = [], [], [], []
    sell_x, sell_y, sell_text, sell_size = [], [], [], []
    buy_sizes = {"약한 매수": 11, "매수": 14, "강한 매수": 17}
    sell_sizes = {"약한 매도": 11, "매도": 14, "강한 매도": 17}
    by_day = _day_bars(df)

    for mark in marks or []:
        action = str(mark.get("신호") or "")
        day = str(mark.get("날짜") or "")
        px = mark.get("가격")
        try:
            px = float(px) if px is not None else None
        except (TypeError, ValueError):
            px = None
        try:
            key = pd.Timestamp(day).date()
        except (TypeError, ValueError):
            continue
        found = by_day.get(key)
        if found is not None:
            ts, row = found
            low = float(row["low"]) if "low" in row and pd.notna(row["low"]) else px
            high = float(row["high"]) if "high" in row and pd.notna(row["high"]) else px
        else:
            ts = pd.Timestamp(day)
            low = px
            high = px
        pct = mark.get("합산%")
        label = f"{action}" + (f" · {pct}%" if pct is not None else "")
        if action in ("약한 매수", "매수", "강한 매수"):
            if low is None:
                continue
            buy_x.append(ts)
            buy_y.append(low)
            buy_text.append(label)
            buy_size.append(buy_sizes.get(action, 13))
        elif action in ("약한 매도", "매도", "강한 매도"):
            if high is None:
                continue
            sell_x.append(ts)
            sell_y.append(high)
            sell_text.append(label)
            sell_size.append(sell_sizes.get(action, 13))

    if buy_x:
        fig.add_trace(
            go.Scatter(
                x=buy_x,
                y=buy_y,
                mode="markers",
                name="매수 신호",
                text=buy_text,
                marker=dict(
                    symbol="triangle-up",
                    size=buy_size,
                    color="#16a34a",
                    line=dict(width=1, color="#14532d"),
                ),
                hovertemplate="%{x|%Y-%m-%d}<br>%{text}<br>%{y:,.0f}<extra></extra>",
            ),
            row=1,
            col=1,
        )
    if sell_x:
        fig.add_trace(
            go.Scatter(
                x=sell_x,
                y=sell_y,
                mode="markers",
                name="매도 신호",
                text=sell_text,
                marker=dict(
                    symbol="triangle-down",
                    size=sell_size,
                    color="#dc2626",
                    line=dict(width=1, color="#7f1d1d"),
                ),
                hovertemplate="%{x|%Y-%m-%d}<br>%{text}<br>%{y:,.0f}<extra></extra>",
            ),
            row=1,
            col=1,
        )

    if "volume" in df.columns:
        colors = [
            "#16a34a" if c >= o else "#dc2626"
            for o, c in zip(df["open"], df["close"])
        ]
        fig.add_trace(
            go.Bar(
                x=df.index,
                y=df["volume"],
                name="거래량",
                marker_color=colors,
                opacity=0.7,
                showlegend=False,
            ),
            row=2,
            col=1,
        )

    fig.update_layout(
        template="plotly_white",
        font=dict(family="Malgun Gothic, Apple SD Gothic Neo, sans-serif", size=13),
        legend=dict(orientation="h", yanchor="bottom", y=1.02, x=0),
        margin=dict(l=40, r=20, t=56, b=36),
        height=560,
        xaxis_rangeslider_visible=False,
        hovermode="x unified",
    )
    fig.update_yaxes(title_text="가격", row=1, col=1)
    fig.update_yaxes(title_text="거래량", row=2, col=1)
    return fig


def build_return_vs_spy_fig(
    strat_pct: float,
    spy_pct: float,
    strat_name: str = "전략",
    spy_name: str = "SPY 보유",
) -> go.Figure:
    beat = strat_pct >= spy_pct
    fig = go.Figure()
    fig.add_trace(
        go.Bar(
            x=[strat_name, spy_name],
            y=[strat_pct, spy_pct],
            marker_color=["#16a34a" if beat else "#dc2626", "#2563eb"],
            text=[f"{strat_pct:+.1f}%", f"{spy_pct:+.1f}%"],
            textposition="outside",
            cliponaxis=False,
        )
    )
    fig.add_hline(y=0, line_color="#94a3b8", line_width=1)
    fig.update_layout(
        title="수익률 비교",
        yaxis_title="%",
        height=340,
        margin=dict(t=48, b=40, l=48, r=24),
        showlegend=False,
        plot_bgcolor="#f8fafc",
        bargap=0.45,
    )
    return fig


def build_ticker_vs_spy_fig(names: list[str], pcts: list[float], spy_pct: float) -> go.Figure:
    names = list(reversed(names))
    pcts = list(reversed(pcts))
    colors = ["#16a34a" if p >= spy_pct else "#dc2626" for p in pcts]
    fig = go.Figure()
    fig.add_trace(
        go.Bar(
            y=names,
            x=pcts,
            orientation="h",
            marker_color=colors,
            text=[f"{p:+.1f}%" for p in pcts],
            textposition="auto",
            name="전략",
        )
    )
    fig.add_vline(
        x=spy_pct,
        line_dash="dash",
        line_color="#2563eb",
        line_width=2,
        annotation_text=f"SPY {spy_pct:+.1f}%",
        annotation_position="top",
    )
    fig.add_vline(x=0, line_color="#94a3b8", line_width=1)
    fig.update_layout(
        title="종목별 수익률 vs SPY",
        xaxis_title="수익률 %",
        height=max(280, 38 * max(len(names), 1) + 90),
        margin=dict(t=56, b=40, l=16, r=24),
        plot_bgcolor="#f8fafc",
        showlegend=False,
    )
    return fig


def build_pnl_split_fig(realized: float, m2m: float, title: str = "손익 구성") -> go.Figure:
    total = realized + m2m
    fig = go.Figure()
    fig.add_trace(
        go.Bar(
            x=["실현", "평가", "합계"],
            y=[realized, m2m, total],
            marker_color=[
                "#16a34a" if realized >= 0 else "#dc2626",
                "#16a34a" if m2m >= 0 else "#dc2626",
                "#2563eb",
            ],
            text=[f"{realized:+,.0f}", f"{m2m:+,.0f}", f"{total:+,.0f}"],
            textposition="outside",
            cliponaxis=False,
        )
    )
    fig.add_hline(y=0, line_color="#94a3b8", line_width=1)
    fig.update_layout(
        title=title,
        height=320,
        margin=dict(t=48, b=40, l=48, r=24),
        showlegend=False,
        plot_bgcolor="#f8fafc",
        bargap=0.45,
    )
    return fig
