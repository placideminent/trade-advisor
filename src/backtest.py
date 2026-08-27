"""일별 신호 백테스트. 지정일 이후 시세는 쓰지 않는다."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta

import pandas as pd

from .analysis import analyze
from .data import (
    fetch_intraday_range,
    fetch_ohlcv,
    resample_4h,
    to_market_wall,
)
from .signals import period_high, period_return, recommend

DEFAULT_SIM = {
    "buy_weak": 5,
    "buy_mid": 10,
    "buy_strong": 15,
    "share_cut": 30,
    "sell_weak_pct": 10,
    "sell_mid_pct": 20,
    "sell_strong_pct": 30,
    "sell_weak_qty": 3,
    "sell_mid_qty": 5,
    "sell_strong_qty": 10,
}

BUY_ACTIONS = ("약한 매수", "매수", "강한 매수")
SELL_ACTIONS = ("약한 매도", "매도", "강한 매도")


@dataclass
class BacktestResult:
    name: str
    ticker: str
    start: date
    end: date
    lookback_label: str
    last_px: float = 0.0
    shares: int = 0
    avg: float = 0.0
    realized: float = 0.0
    m2m: float = 0.0
    m2m_pct: float = 0.0
    days: int = 0
    counts: dict = field(default_factory=dict)
    trades: list = field(default_factory=list)
    error: str | None = None


def _window(df: pd.DataFrame, as_of: date, days: int) -> pd.DataFrame:
    cutoff = pd.Timestamp(as_of) + pd.Timedelta(days=1) - pd.Timedelta(seconds=1)
    start = pd.Timestamp(as_of) - pd.Timedelta(days=days)
    idx = df.index
    if getattr(idx, "tz", None) is not None:
        cutoff = cutoff.tz_localize(idx.tz) if cutoff.tzinfo is None else cutoff.tz_convert(idx.tz)
        start = start.tz_localize(idx.tz) if start.tzinfo is None else start.tz_convert(idx.tz)
    return df.loc[(df.index >= start) & (df.index <= cutoff)].copy()


def _qty_maps(sim: dict) -> tuple[dict, dict, dict, int]:
    buy = {
        "약한 매수": int(sim["buy_weak"]),
        "매수": int(sim["buy_mid"]),
        "강한 매수": int(sim["buy_strong"]),
    }
    sell_pct = {
        "약한 매도": int(sim["sell_weak_pct"]) / 100.0,
        "매도": int(sim["sell_mid_pct"]) / 100.0,
        "강한 매도": int(sim["sell_strong_pct"]) / 100.0,
    }
    sell_fixed = {
        "약한 매도": int(sim["sell_weak_qty"]),
        "매도": int(sim["sell_mid_qty"]),
        "강한 매도": int(sim["sell_strong_qty"]),
    }
    return buy, sell_pct, sell_fixed, int(sim["share_cut"])


def _sell_qty(action: str, shares: int, share_cut: int, sell_pct: dict, sell_fixed: dict) -> int:
    if shares <= 0 or action not in sell_pct:
        return 0
    if shares < share_cut:
        return min(sell_fixed[action], shares)
    qty = int(shares * sell_pct[action])
    return min(max(qty, 1), shares)


def run_backtest(
    market: str,
    ticker: str,
    start: date,
    end: date,
    lookback_days: int,
    timeframe: str,
    lookback_label: str,
    rule: dict | None,
    sim: dict | None = None,
    progress=None,
) -> BacktestResult:
    sim = {**DEFAULT_SIM, **(sim or {})}
    buy_map, sell_pct, sell_fixed, share_cut = _qty_maps(sim)
    result = BacktestResult(
        name=ticker,
        ticker=ticker,
        start=start,
        end=end,
        lookback_label=lookback_label,
    )
    if start > end:
        result.error = "시작일이 종료일보다 뒤입니다."
        return result

    h1_start = start - timedelta(days=int(lookback_days * 1.2) + 10)
    need_1h = timeframe in ("1h", "4h") or lookback_days >= 90
    df_1h = pd.DataFrame()
    if need_1h:
        df_1h = fetch_intraday_range(market, ticker, h1_start, end)

    df_main = pd.DataFrame()
    meta = {}
    if timeframe == "4h":
        if df_1h.empty:
            df_main, meta = fetch_ohlcv(market, ticker, end, (end - start).days + lookback_days + 15, "4h")
        else:
            df_main = resample_4h(df_1h, market)
            meta = {"name": ticker, "ticker": ticker, "bar": "4시간봉"}
    elif timeframe == "1h":
        if df_1h.empty:
            df_main, meta = fetch_ohlcv(market, ticker, end, (end - start).days + lookback_days + 15, "1h")
        else:
            df_main = to_market_wall(df_1h, market)
            meta = {"name": ticker, "ticker": ticker, "bar": "1시간봉"}
    else:
        df_main, meta = fetch_ohlcv(market, ticker, end, (end - start).days + lookback_days + 15, "1d")

    df_1d, _ = fetch_ohlcv(market, ticker, end, (end - start).days + 220, "1d")
    result.name = str(meta.get("name") or ticker)
    result.ticker = str(meta.get("ticker") or ticker)

    if df_main is None or df_main.empty:
        result.error = "해당 기간 시세를 받지 못했습니다."
        return result

    if df_1d is not None and not df_1d.empty:
        days = sorted(
            ts.date() if hasattr(ts, "date") else ts
            for ts in df_1d.index
            if start <= (ts.date() if hasattr(ts, "date") else ts) <= end
        )
    else:
        days = sorted(
            {
                (ts.date() if hasattr(ts, "date") else ts)
                for ts in df_main.index
                if start <= (ts.date() if hasattr(ts, "date") else ts) <= end
            }
        )
    if not days:
        result.error = "거래일이 없습니다."
        return result

    df_1h_wall = to_market_wall(df_1h, market) if not df_1h.empty else pd.DataFrame()

    shares = 0
    avg = 0.0
    realized = 0.0
    last_px = 0.0
    counts: dict[str, int] = {}
    trades: list[dict] = []

    for i, as_of in enumerate(days, 1):
        if progress:
            progress(i, len(days), as_of)
        w = _window(df_main, as_of, lookback_days)
        if w.empty:
            continue
        px = float(w["close"].iloc[-1])
        last_px = px
        an = analyze(w, as_of=as_of, spot_price=px, price_source="해당일 종가")
        src_6m = _window(df_1d, as_of, 200) if df_1d is not None and not df_1d.empty else w
        chg6 = period_return(src_6m, as_of, px, 180)

        peak_1m = None
        if lookback_days > 30 and not df_1h_wall.empty:
            w1 = _window(df_1h_wall, as_of, 30)
            if not w1.empty:
                peak_1m = period_high(w1)

        try:
            sig = recommend(
                an,
                six_month_chg=chg6,
                lookback_days=lookback_days,
                peak_1m=peak_1m,
                rule=rule,
            )
        except TypeError:
            sig = recommend(an, six_month_chg=chg6)

        action = sig.action
        counts[action] = counts.get(action, 0) + 1
        row = {
            "날짜": as_of.isoformat(),
            "신호": action,
            "합산%": sig.score_pct,
            "가격": px,
            "수량": 0,
            "잔량": shares,
            "평단": avg,
            "체결": "",
        }

        if action in buy_map:
            qty = buy_map[action]
            if qty > 0:
                cost = avg * shares + px * qty
                shares += qty
                avg = cost / shares
                row.update(수량=qty, 잔량=shares, 평단=avg, 체결="매수")
                trades.append(row)
        elif action in sell_pct:
            if shares <= 0:
                row.update(체결="잔량0")
                trades.append(row)
            else:
                qty = _sell_qty(action, shares, share_cut, sell_pct, sell_fixed)
                realized += (px - avg) * qty
                shares -= qty
                if shares <= 0:
                    shares = 0
                    avg = 0.0
                row.update(수량=qty, 잔량=shares, 평단=avg, 체결="매도")
                trades.append(row)

    m2m = (last_px - avg) * shares if shares else 0.0
    m2m_pct = (last_px / avg - 1.0) * 100 if shares and avg else 0.0
    result.last_px = last_px
    result.shares = shares
    result.avg = avg
    result.realized = realized
    result.m2m = m2m
    result.m2m_pct = m2m_pct
    result.days = len(days)
    result.counts = counts
    result.trades = trades
    return result
