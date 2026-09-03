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
    reset_yahoo_gate,
    to_market_wall,
)
from .signals import period_return, recommend

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
SIM_QTY_KEYS = (
    "buy_weak",
    "buy_mid",
    "buy_strong",
    "share_cut",
    "sell_weak_qty",
    "sell_mid_qty",
    "sell_strong_qty",
)


def normalize_sim(raw: dict | None) -> dict:
    data = dict(DEFAULT_SIM)
    if not isinstance(raw, dict):
        return data
    for key, default in DEFAULT_SIM.items():
        if key not in raw:
            continue
        try:
            if key in SIM_QTY_KEYS:
                data[key] = round(max(0.0, float(raw[key])), 5)
            else:
                data[key] = max(0, min(100, int(raw[key])))
        except (TypeError, ValueError):
            data[key] = default
    return data

BUY_ACTIONS = ("약한 매수", "매수", "강한 매수")
SELL_ACTIONS = ("약한 매도", "매도", "강한 매도")


@dataclass
class BacktestResult:
    name: str
    ticker: str
    start: date
    end: date
    lookback_label: str
    market: str = ""
    last_px: float = 0.0
    shares: float = 0.0
    avg: float = 0.0
    realized: float = 0.0
    m2m: float = 0.0
    m2m_pct: float = 0.0
    days: int = 0
    counts: dict = field(default_factory=dict)
    trades: list = field(default_factory=list)
    signals: list = field(default_factory=list)
    chart_df: pd.DataFrame | None = None
    error: str | None = None
    invested: float = 0.0
    daily: list = field(default_factory=list)


def _window(df: pd.DataFrame, as_of: date, days: int) -> pd.DataFrame:
    cutoff = pd.Timestamp(as_of) + pd.Timedelta(days=1) - pd.Timedelta(seconds=1)
    start = pd.Timestamp(as_of) - pd.Timedelta(days=days)
    idx = df.index
    if getattr(idx, "tz", None) is not None:
        cutoff = cutoff.tz_localize(idx.tz) if cutoff.tzinfo is None else cutoff.tz_convert(idx.tz)
        start = start.tz_localize(idx.tz) if start.tzinfo is None else start.tz_convert(idx.tz)
    return df.loc[(df.index >= start) & (df.index <= cutoff)].copy()


def _qty_maps(sim: dict) -> tuple[dict, dict, dict, float]:
    buy = {
        "약한 매수": float(sim["buy_weak"]),
        "매수": float(sim["buy_mid"]),
        "강한 매수": float(sim["buy_strong"]),
    }
    sell_pct = {
        "약한 매도": int(sim["sell_weak_pct"]) / 100.0,
        "매도": int(sim["sell_mid_pct"]) / 100.0,
        "강한 매도": int(sim["sell_strong_pct"]) / 100.0,
    }
    sell_fixed = {
        "약한 매도": float(sim["sell_weak_qty"]),
        "매도": float(sim["sell_mid_qty"]),
        "강한 매도": float(sim["sell_strong_qty"]),
    }
    return buy, sell_pct, sell_fixed, float(sim["share_cut"])


def _lot(market: str) -> float:
    return 0.00001 if str(market or "").upper() == "CRYPTO" else 1.0


def _floor_lot(qty: float, lot: float) -> float:
    if qty <= 0:
        return 0.0
    if lot >= 1:
        return float(int(qty))
    n = int(qty / lot + 1e-12)
    return round(n * lot, 5)


def _sell_qty(action: str, shares: float, share_cut: float, sell_pct: dict, sell_fixed: dict, lot: float = 1.0) -> float:
    if shares <= 0 or action not in sell_pct:
        return 0.0
    if shares < share_cut:
        qty = min(float(sell_fixed[action]), shares)
    else:
        qty = shares * float(sell_pct[action])
    qty = _floor_lot(qty, lot)
    if qty <= 0:
        return 0.0
    return min(qty, shares)


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
    use_options: bool = False,
) -> BacktestResult:
    sim = {**DEFAULT_SIM, **(sim or {})}
    buy_map, sell_pct, sell_fixed, share_cut = _qty_maps(sim)
    result = BacktestResult(
        name=ticker,
        ticker=ticker,
        start=start,
        end=end,
        lookback_label=lookback_label,
        market=market,
    )
    if start > end:
        result.error = "시작일이 종료일보다 뒤입니다."
        return result

    span = max((end - start).days, 1) + int(lookback_days) + 30
    reset_yahoo_gate()
    df_main = pd.DataFrame()
    meta: dict = {"ticker": ticker, "name": ticker}
    notes: list[str] = []

    def _try_ohlcv(tf: str):
        last_meta = dict(meta)
        for attempt in range(3):
            try:
                reset_yahoo_gate()
                df, got = fetch_ohlcv(market, ticker, end, span, tf)
                if df is not None and not df.empty:
                    return df, got
                last_meta = got or last_meta
            except Exception as extra:
                notes.append(str(extra)[:160])
            if attempt < 2:
                from time import sleep

                sleep(0.8 * (attempt + 1))
        return pd.DataFrame(), last_meta

    if timeframe in ("1h", "4h"):
        h1_start = start - timedelta(days=int(lookback_days * 1.2) + 10)
        df_1h = pd.DataFrame()
        try:
            df_1h = fetch_intraday_range(market, ticker, h1_start, end)
        except Exception as extra:
            notes.append(str(extra)[:160])
            df_1h = pd.DataFrame()
        if df_1h is not None and not df_1h.empty:
            try:
                if timeframe == "4h":
                    df_main = resample_4h(df_1h, market)
                    meta = {"name": ticker, "ticker": ticker, "bar": "4시간봉"}
                    if df_main is None or df_main.empty:
                        df_main = to_market_wall(df_1h, market)
                        meta["bar"] = "1시간봉"
                else:
                    df_main = to_market_wall(df_1h, market)
                    meta = {"name": ticker, "ticker": ticker, "bar": "1시간봉"}
            except Exception as extra:
                notes.append(str(extra)[:160])
                df_main = pd.DataFrame()
        if df_main is None or df_main.empty:
            df_main, meta = _try_ohlcv(timeframe)
    else:
        df_main, meta = _try_ohlcv("1d")

    if df_main is None or df_main.empty:
        df_main, meta = _try_ohlcv("1d")
        if meta is not None and (df_main is not None and not df_main.empty):
            meta["note"] = ((meta.get("note") or "") + " 시간봉 없이 일봉으로 계산합니다.").strip()

    try:
        df_1d, _ = fetch_ohlcv(market, ticker, end, (end - start).days + 220, "1d")
    except Exception:
        df_1d = pd.DataFrame()
    df_1m_src = pd.DataFrame()
    if str(timeframe or "") != "1h" and int(lookback_days) > 60:
        try:
            df_1m_src, _ = fetch_ohlcv(
                market, ticker, end, (end - start).days + 45, "1h"
            )
        except Exception:
            df_1m_src = pd.DataFrame()
    result.name = str(meta.get("name") or ticker)
    result.ticker = str(meta.get("ticker") or ticker)

    option_walls = None
    if use_options and str(market or "").upper() == "US" and df_main is not None and not df_main.empty:
        try:
            from .options import fetch_option_walls

            last = float(df_main["close"].iloc[-1])
            option_walls = fetch_option_walls(ticker, end, last)
        except Exception as extra:
            option_walls = {"error": str(extra)[:120], "soon": False}
        reset_yahoo_gate()

    if df_main is None or df_main.empty:
        extra = f" ({'; '.join(notes)})" if notes else ""
        result.error = f"{ticker} 시세를 받지 못했습니다.{extra}"
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

    chart_df = None
    if df_1d is not None and not df_1d.empty:
        mask = [
            start <= (ts.date() if hasattr(ts, "date") else ts) <= end
            for ts in df_1d.index
        ]
        sliced = df_1d.loc[mask].copy()
        if not sliced.empty:
            chart_df = sliced
    result.chart_df = chart_df

    shares = 0.0
    avg = 0.0
    realized = 0.0
    invested = 0.0
    last_px = 0.0
    counts: dict[str, int] = {}
    trades: list[dict] = []
    signals: list[dict] = []
    daily: list[dict] = []
    lot = _lot(market)

    for i, as_of in enumerate(days, 1):
        if progress:
            progress(i, len(days), as_of)
        w = _window(df_main, as_of, lookback_days)
        if w.empty:
            continue
        px = float(w["close"].iloc[-1])
        last_px = px
        an = analyze(
            w,
            as_of=as_of,
            spot_price=px,
            price_source="해당일 종가",
            lookback_days=lookback_days,
        )
        src_6m = _window(df_1d, as_of, 200) if df_1d is not None and not df_1d.empty else w
        chg6 = period_return(src_6m, as_of, px, 180)

        w1m = None
        if df_1m_src is not None and not df_1m_src.empty:
            w1m = _window(df_1m_src, as_of, 30)
            if w1m is None or w1m.empty:
                w1m = None
        try:
            sig = recommend(
                an,
                six_month_chg=chg6,
                lookback_days=lookback_days,
                rule=rule,
                option_walls=option_walls,
                market=market,
                df_1m=w1m,
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
        daily.append({"날짜": as_of.isoformat(), "신호": action, "합산%": sig.score_pct, "가격": px})
        if action in BUY_ACTIONS or action in SELL_ACTIONS:
            signals.append(
                {
                    "날짜": as_of.isoformat(),
                    "신호": action,
                    "합산%": sig.score_pct,
                    "가격": px,
                }
            )

        if action in buy_map:
            qty = _floor_lot(float(buy_map[action]), lot)
            if qty > 0:
                cost = avg * shares + px * qty
                shares += qty
                avg = cost / shares
                invested += px * qty
                row.update(수량=qty, 잔량=shares, 평단=avg, 체결="매수")
                trades.append(row)
        elif action in sell_pct:
            if shares <= 0:
                row.update(체결="잔량0")
                trades.append(row)
            else:
                qty = _sell_qty(action, shares, share_cut, sell_pct, sell_fixed, lot)
                realized += (px - avg) * qty
                shares -= qty
                if shares <= 0:
                    shares = 0.0
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
    result.invested = invested
    result.days = len(days)
    result.counts = counts
    result.trades = trades
    result.signals = signals
    result.daily = daily
    return result


def spy_hold_return(start: date, end: date) -> tuple[float | None, str]:
    """같은 기간 SPY(S&P 500 SPDR) 매수 후 보유 수익률(%)."""
    if start > end:
        return None, "시작일이 종료일보다 뒤입니다."
    lookback = max((end - start).days + 20, 40)
    try:
        df, _meta = fetch_ohlcv("US", "SPY", end, lookback, "1d")
    except Exception as extra:
        return None, str(extra)
    if df is None or df.empty or "close" not in df.columns:
        return None, "SPY 시세를 받지 못했습니다."

    def _as_date(ts):
        return ts.date() if hasattr(ts, "date") else ts

    picked = df.loc[[start <= _as_date(ts) <= end for ts in df.index]]
    if picked.empty:
        return None, "해당 기간 SPY 봉이 없습니다."
    first = float(picked["close"].iloc[0])
    last = float(picked["close"].iloc[-1])
    if first <= 0:
        return None, "SPY 가격이 비정상입니다."
    return (last / first - 1.0) * 100.0, ""
