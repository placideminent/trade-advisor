"""즐겨찾기 비중·월 적립 투자계획 시뮬레이션."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta

import pandas as pd

from .backtest import (
    BUY_ACTIONS,
    DEFAULT_SIM,
    SELL_ACTIONS,
    _floor_lot,
    _lot,
    run_backtest,
)
from .data import fetch_ohlcv, fetch_usdkrw, fetch_usdkrw_history
from .universe import resolve_lookback

DEFAULT_PLAN_PCTS = {
    "buy_weak": 10.0,
    "buy_mid": 30.0,
    "buy_strong": 50.0,
    "sell_weak": 5.0,
    "sell_mid": 10.0,
    "sell_strong": 20.0,
}
PLAN_PCT_FIELDS = (
    ("buy_weak", "약한 매수"),
    ("buy_mid", "매수"),
    ("buy_strong", "강한 매수"),
    ("sell_weak", "약한 매도"),
    ("sell_mid", "매도"),
    ("sell_strong", "강한 매도"),
)
_ACTION_PCT_KEY = {
    "약한 매수": "buy_weak",
    "매수": "buy_mid",
    "강한 매수": "buy_strong",
    "약한 매도": "sell_weak",
    "매도": "sell_mid",
    "강한 매도": "sell_strong",
}


def normalize_plan_pcts(raw: dict | None) -> dict:
    data = dict(DEFAULT_PLAN_PCTS)
    if not isinstance(raw, dict):
        return data
    for key, default in DEFAULT_PLAN_PCTS.items():
        if key not in raw:
            continue
        try:
            data[key] = max(0.0, min(100.0, float(raw[key])))
        except (TypeError, ValueError):
            data[key] = default
    return data


def _action_frac(action: str, pcts: dict) -> float:
    key = _ACTION_PCT_KEY.get(action)
    if not key:
        return 0.0
    try:
        return max(0.0, float(pcts.get(key) or 0)) / 100.0
    except (TypeError, ValueError):
        return 0.0


def _pay_krw(cost: float, month_left: float, cash: float) -> tuple[float, float]:
    """월 배정부터 쓰고, 모자라면 현금. (새 월배정, 새 현금)."""
    cost = max(0.0, float(cost or 0))
    month_left = max(0.0, float(month_left or 0))
    cash = max(0.0, float(cash or 0))
    take = min(cost, month_left)
    return month_left - take, max(0.0, cash - (cost - take))


def _fill_qty(
    want_krw: float,
    available: float,
    krw_px: float,
    lot: float,
    max_shares: float | None = None,
) -> float:
    """비율 금액으로 살 수량. 1호도 안 되면 한도+현금으로 최소 1호."""
    if krw_px <= 0 or lot <= 0:
        return 0.0
    available = max(0.0, float(available or 0))
    want_krw = max(0.0, float(want_krw or 0))
    qty = _floor_lot(min(want_krw, available) / krw_px, lot)
    min_cost = lot * krw_px
    if qty <= 0 and available + 1e-9 >= min_cost:
        qty = lot
    if max_shares is not None:
        qty = min(qty, _floor_lot(float(max_shares), lot))
    if qty > 0 and qty * krw_px > available + 1e-6:
        qty = _floor_lot(available / krw_px, lot)
    return qty if qty > 0 else 0.0


def _is_usd(market: str) -> bool:
    return str(market or "").upper() in ("US", "CRYPTO")


def _fx_on(hist: pd.Series, as_of: date, fallback: float | None) -> float | None:
    if hist is not None and not hist.empty:
        picked = hist.loc[hist.index <= as_of]
        if not picked.empty:
            try:
                return float(picked.iloc[-1])
            except (TypeError, ValueError):
                pass
    if fallback and fallback > 0:
        return float(fallback)
    return None


def _native_to_krw(px: float, market: str, fx: float | None) -> float | None:
    if px is None or px <= 0:
        return None
    if _is_usd(market):
        if not fx or fx <= 0:
            return None
        return float(px) * float(fx)
    return float(px)


def _as_date(ts) -> date:
    return ts.date() if hasattr(ts, "date") else ts


def _close_on_or_before(df: pd.DataFrame, as_of: date) -> float | None:
    if df is None or df.empty or "close" not in df.columns:
        return None
    picked = df.loc[[_as_date(ts) <= as_of for ts in df.index]]
    if picked.empty:
        picked = df.loc[[_as_date(ts) >= as_of for ts in df.index]]
        if picked.empty:
            return None
        row = picked.iloc[0]
    else:
        row = picked.iloc[-1]
    try:
        px = float(row["close"])
    except (TypeError, ValueError):
        return None
    return px if px > 0 else None


def _spy_monthly_value(
    contrib_days: list[date],
    monthly_krw: float,
    end: date,
    fx_hist: pd.Series,
    fx_fallback: float | None,
) -> tuple[float | None, str]:
    """같은 달에 같은 금액을 SPY에 넣은 최종 원화 평가액."""
    if not contrib_days or monthly_krw <= 0:
        return 0.0, ""
    start = min(contrib_days)
    lookback = max((end - start).days + 30, 40)
    try:
        df, _meta = fetch_ohlcv("US", "SPY", end, lookback, "1d")
    except Exception as extra:
        return None, str(extra)
    if df is None or df.empty:
        return None, "SPY 시세를 받지 못했습니다."
    shares = 0.0
    last_fx = fx_fallback
    for day in contrib_days:
        px = _close_on_or_before(df, day)
        fx = _fx_on(fx_hist, day, last_fx)
        if fx:
            last_fx = fx
        if not px or not fx:
            continue
        krw_px = px * fx
        if krw_px <= 0:
            continue
        shares += monthly_krw / krw_px
    last_px = _close_on_or_before(df, end)
    end_fx = _fx_on(fx_hist, end, last_fx)
    if not shares or not last_px or not end_fx:
        return None, "SPY 월적립을 계산하지 못했습니다."
    return shares * last_px * end_fx, ""


@dataclass
class PlanResult:
    start: date
    end: date
    monthly_krw: float
    months: int
    cash_krw: float = 0.0
    contributed_krw: float = 0.0
    pnl_krw: float = 0.0
    pnl_pct: float = 0.0
    spy_value_krw: float | None = None
    spy_pnl_krw: float | None = None
    spy_pct: float | None = None
    spy_note: str = ""
    error: str | None = None
    fx_note: str = ""
    holdings: list = field(default_factory=list)
    trades: list = field(default_factory=list)
    months_log: list = field(default_factory=list)


def run_plan(
    items: list[dict],
    start: date,
    months: int,
    monthly_krw: float,
    lookback_label: str,
    rule: dict | None,
    sim: dict | None = None,
    pcts: dict | None = None,
    progress=None,
) -> PlanResult:
    spec = resolve_lookback(lookback_label)
    lookback_days = int(spec["days"])
    timeframe = str(spec["timeframe"])
    months = max(1, int(months or 1))
    monthly_krw = max(0.0, float(monthly_krw or 0))
    pcts = normalize_plan_pcts(pcts)
    _ = sim
    m0 = start.year * 12 + (start.month - 1) + months
    y, m = divmod(m0, 12)
    end = date(start.year, start.month, start.day)
    for day in (start.day, 28, 27, 1):
        try:
            end = date(y, m + 1, day)
            break
        except ValueError:
            continue
    result = PlanResult(start=start, end=end, monthly_krw=monthly_krw, months=months)
    if not items:
        result.error = "즐겨찾기한 종목이 없습니다."
        return result
    if start > end:
        result.error = "기간이 올바르지 않습니다."
        return result

    weights = {}
    total_w = 0.0
    for item in items:
        key = f"{item['market']}|{item['ticker']}"
        try:
            w = float(item.get("weight") or 0)
        except (TypeError, ValueError):
            w = 0.0
        w = max(0.0, w)
        weights[key] = w
        total_w += w
    if total_w <= 0:
        result.error = "종목 비중을 1% 이상 넣어 주세요."
        return result
    for key in list(weights):
        weights[key] = weights[key] / total_w

    n_items = len(items)
    daily_by_key: dict[str, dict] = {}
    last_px: dict[str, float] = {}
    meta: dict[str, dict] = {}
    for i, item in enumerate(items):
        key = f"{item['market']}|{item['ticker']}"
        if progress:
            progress(i, n_items, f"{item.get('name') or item['ticker']} 신호")
        bt = run_backtest(
            item["market"],
            item["ticker"],
            start,
            end,
            lookback_days,
            timeframe,
            lookback_label,
            rule,
            DEFAULT_SIM,
            progress=None,
            use_options=False,
        )
        if bt.error:
            result.error = f"{item.get('name') or item['ticker']}: {bt.error}"
            return result
        by_day = {}
        for row in bt.daily or []:
            try:
                day = date.fromisoformat(str(row["날짜"])[:10])
            except (TypeError, ValueError):
                continue
            by_day[day] = row
        daily_by_key[key] = by_day
        last_px[key] = float(bt.last_px or 0)
        meta[key] = {
            "market": item["market"],
            "ticker": item["ticker"],
            "name": item.get("name") or item["ticker"],
        }

    try:
        fx_hist = fetch_usdkrw_history(start - timedelta(days=21), end)
    except Exception:
        fx_hist = pd.Series(dtype=float)
    if fx_hist is None:
        fx_hist = pd.Series(dtype=float)
    fx_fallback = None
    try:
        fx_fallback, src, _when = fetch_usdkrw(end)
        result.fx_note = src or ""
    except Exception:
        fx_fallback = None
    if (fx_hist is None or fx_hist.empty) and not fx_fallback:
        result.error = "원/달러 환율을 받지 못해 달러 자산을 계산할 수 없습니다."
        return result

    dates = sorted({d for by in daily_by_key.values() for d in by})
    dates = [d for d in dates if start <= d <= end]
    if not dates:
        result.error = "거래일이 없습니다."
        return result

    cash = 0.0
    contributed = 0.0
    last_month = None
    month_budget = {key: 0.0 for key in daily_by_key}
    month_left = {key: 0.0 for key in daily_by_key}
    shares = {key: 0.0 for key in daily_by_key}
    avg = {key: 0.0 for key in daily_by_key}
    buy_krw = {key: 0.0 for key in daily_by_key}
    sell_krw = {key: 0.0 for key in daily_by_key}
    trades: list[dict] = []
    months_log: list[dict] = []
    contrib_days: list[date] = []
    pending: dict[str, dict] = {}

    def px_of(key: str, as_of: date) -> float | None:
        row = daily_by_key[key].get(as_of)
        if row:
            try:
                px = float(row["가격"])
            except (TypeError, ValueError):
                px = 0.0
            if px > 0:
                last_px[key] = px
                return px
        return last_px.get(key) or None

    def mtm(as_of: date, fx: float | None) -> tuple[float, dict]:
        values = {}
        total = cash
        for key, sh in shares.items():
            px = px_of(key, as_of)
            if not sh or not px:
                values[key] = 0.0
                continue
            krw = _native_to_krw(px, meta[key]["market"], fx)
            if krw is None:
                values[key] = 0.0
                continue
            val = sh * krw
            values[key] = val
            total += val
        return total, values

    def execute_pending() -> None:
        nonlocal cash
        for key, info in pending.items():
            action = info.get("action")
            as_of = info.get("as_of")
            px = float(info.get("px") or 0)
            fx = info.get("fx")
            if action not in BUY_ACTIONS and action not in SELL_ACTIONS:
                continue
            if px <= 0:
                continue
            krw_px = _native_to_krw(px, meta[key]["market"], fx)
            if not krw_px:
                trades.append(
                    {
                        "날짜": as_of.isoformat() if as_of else "",
                        "종목": meta[key]["name"],
                        "시장": meta[key]["market"],
                        "신호": action,
                        "체결": "미체결",
                        "수량": 0,
                        "가격": px,
                        "원화": 0,
                        "비중": "환율 없음",
                    }
                )
                continue
            frac = _action_frac(action, pcts)
            if frac <= 0:
                continue
            lot = _lot(meta[key]["market"])
            want_krw = month_budget.get(key, 0) * frac
            if action in SELL_ACTIONS:
                sh = shares[key]
                qty = _fill_qty(want_krw, sh * krw_px, krw_px, lot, max_shares=sh)
                if qty <= 0:
                    trades.append(
                        {
                            "날짜": as_of.isoformat() if as_of else "",
                            "종목": meta[key]["name"],
                            "시장": meta[key]["market"],
                            "신호": action,
                            "체결": "미체결",
                            "수량": 0,
                            "가격": px,
                            "원화": 0,
                            "비중": "잔량 없음" if sh <= 0 else "1주 미만",
                        }
                    )
                    continue
                proceeds = qty * krw_px
                shares[key] -= qty
                if shares[key] <= 0:
                    shares[key] = 0.0
                    avg[key] = 0.0
                cash += proceeds
                sell_krw[key] += proceeds
                trades.append(
                    {
                        "날짜": as_of.isoformat() if as_of else "",
                        "종목": meta[key]["name"],
                        "시장": meta[key]["market"],
                        "신호": action,
                        "체결": "매도",
                        "수량": qty,
                        "가격": px,
                        "원화": proceeds,
                        "비중": f"월한도 {frac * 100:.0f}%",
                    }
                )
                continue
            available = month_left.get(key, 0) + cash
            qty = _fill_qty(want_krw, available, krw_px, lot)
            if qty <= 0:
                trades.append(
                    {
                        "날짜": as_of.isoformat() if as_of else "",
                        "종목": meta[key]["name"],
                        "시장": meta[key]["market"],
                        "신호": action,
                        "체결": "미체결",
                        "수량": 0,
                        "가격": px,
                        "원화": 0,
                        "비중": "1주 가격이 한도+현금보다 큼",
                    }
                )
                continue
            cost = qty * krw_px
            month_left[key], cash = _pay_krw(cost, month_left.get(key, 0), cash)
            buy_krw[key] += cost
            prev = shares[key]
            avg[key] = (avg[key] * prev + px * qty) / (prev + qty) if prev + qty else px
            shares[key] = prev + qty
            trades.append(
                {
                    "날짜": as_of.isoformat() if as_of else "",
                    "종목": meta[key]["name"],
                    "시장": meta[key]["market"],
                    "신호": action,
                    "체결": "매수",
                    "수량": qty,
                    "가격": px,
                    "원화": cost,
                    "비중": f"월한도 {frac * 100:.0f}%",
                }
            )
        pending.clear()

    for i, as_of in enumerate(dates):
        if progress:
            progress(n_items, n_items, str(as_of))
        month_key = (as_of.year, as_of.month)
        if month_key != last_month:
            if last_month is not None:
                execute_pending()
                cash += sum(month_left.values())
                for key in month_left:
                    month_left[key] = 0.0
            contributed += monthly_krw
            last_month = month_key
            for key in month_left:
                add = monthly_krw * weights.get(key, 0)
                month_budget[key] = add
                month_left[key] = add
            contrib_days.append(as_of)
            months_log.append(
                {
                    "년월": f"{as_of.year}-{as_of.month:02d}",
                    "날짜": as_of.isoformat(),
                    "적립": monthly_krw,
                    "현금": cash,
                }
            )
        fx = _fx_on(fx_hist, as_of, fx_fallback)
        for key, row in daily_by_key.items():
            day = row.get(as_of)
            if not day:
                continue
            action = day.get("신호")
            if action not in BUY_ACTIONS and action not in SELL_ACTIONS:
                continue
            try:
                px = float(day["가격"])
            except (TypeError, ValueError):
                px = 0.0
            if px <= 0:
                continue
            if _is_usd(meta[key]["market"]) and not fx:
                continue
            pending[key] = {"as_of": as_of, "action": action, "px": px, "fx": fx}

    execute_pending()
    cash += sum(month_left.values())
    fx_end = _fx_on(fx_hist, end, fx_fallback)
    total, values = mtm(end, fx_end)
    holdings = []
    for key, info in meta.items():
        sh = shares[key]
        px = last_px.get(key) or 0.0
        val = values.get(key, 0.0)
        invested = buy_krw.get(key, 0.0)
        sold = sell_krw.get(key, 0.0)
        ticker_pnl = val + sold - invested
        ticker_pct = (ticker_pnl / invested * 100.0) if invested > 0 else None
        w = (val / total) if total > 0 else 0.0
        holdings.append(
            {
                "종목": info["name"],
                "시장": info["market"],
                "티커": info["ticker"],
                "잔량": sh,
                "평단": avg[key],
                "종가": px,
                "평가(원)": val,
                "투입": invested,
                "매도대금": sold,
                "수익금": ticker_pnl,
                "수익률": ticker_pct,
                "실제비중": w * 100,
                "월배정비중": weights.get(key, 0) * 100,
            }
        )
    result.cash_krw = cash
    result.contributed_krw = contributed
    result.pnl_krw = total - contributed
    result.pnl_pct = (result.pnl_krw / contributed * 100.0) if contributed > 0 else 0.0
    if progress:
        progress(n_items, n_items, "S&P 500 비교")
    spy_val, spy_err = _spy_monthly_value(
        contrib_days, monthly_krw, end, fx_hist, fx_fallback
    )
    result.spy_value_krw = spy_val
    result.spy_note = spy_err
    if spy_val is not None and contributed > 0:
        result.spy_pnl_krw = spy_val - contributed
        result.spy_pct = result.spy_pnl_krw / contributed * 100.0
    result.holdings = holdings
    result.trades = trades
    result.months_log = months_log
    return result
