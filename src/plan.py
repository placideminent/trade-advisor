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
    _qty_maps,
    _sell_qty,
    normalize_sim,
    run_backtest,
)
from .data import fetch_usdkrw, fetch_usdkrw_history
from .universe import resolve_lookback

DRIFT_PP = 5.0


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


@dataclass
class PlanResult:
    start: date
    end: date
    monthly_krw: float
    months: int
    cash_krw: float = 0.0
    contributed_krw: float = 0.0
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
    drift_pp: float = DRIFT_PP,
    progress=None,
) -> PlanResult:
    spec = resolve_lookback(lookback_label)
    lookback_days = int(spec["days"])
    timeframe = str(spec["timeframe"])
    months = max(1, int(months or 1))
    monthly_krw = max(0.0, float(monthly_krw or 0))
    drift = max(0.0, float(drift_pp if drift_pp is not None else DRIFT_PP)) / 100.0
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
        item_sim = normalize_sim(item.get("sim") or sim)
        bt = run_backtest(
            item["market"],
            item["ticker"],
            start,
            end,
            lookback_days,
            timeframe,
            lookback_label,
            rule,
            item_sim,
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
            "sim": item_sim,
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
    shares = {key: 0.0 for key in daily_by_key}
    avg = {key: 0.0 for key in daily_by_key}
    trades: list[dict] = []
    months_log: list[dict] = []

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

    for i, as_of in enumerate(dates):
        if progress:
            progress(n_items, n_items, str(as_of))
        month_key = (as_of.year, as_of.month)
        if month_key != last_month:
            cash += monthly_krw
            contributed += monthly_krw
            last_month = month_key
            months_log.append({"년월": f"{as_of.year}-{as_of.month:02d}", "적립": monthly_krw, "현금": cash})
        fx = _fx_on(fx_hist, as_of, fx_fallback)
        if any(_is_usd(meta[k]["market"]) for k in shares) and not fx:
            continue
        total, values = mtm(as_of, fx)

        # 매도: 목표보다 drift 이상 무거울 때만, 목표까지
        for key, row in list(daily_by_key.items()):
            day = row.get(as_of)
            if not day:
                continue
            action = day.get("신호")
            if action not in SELL_ACTIONS:
                continue
            sh = shares[key]
            if sh <= 0:
                continue
            px = float(day["가격"])
            if px <= 0:
                continue
            target = weights.get(key, 0)
            cur_w = (values.get(key, 0) / total) if total > 0 else 0.0
            if cur_w <= target + drift:
                continue
            over_krw = (cur_w - target) * total
            krw_px = _native_to_krw(px, meta[key]["market"], fx)
            if not krw_px:
                continue
            max_qty = over_krw / krw_px
            sim_d = meta[key]["sim"]
            buy_map, sell_pct, sell_fixed, share_cut = _qty_maps(sim_d)
            lot = _lot(meta[key]["market"])
            qty = _sell_qty(action, sh, share_cut, sell_pct, sell_fixed, lot)
            qty = min(qty, _floor_lot(max_qty, lot), sh)
            if qty <= 0:
                continue
            proceeds = qty * krw_px
            shares[key] -= qty
            if shares[key] <= 0:
                shares[key] = 0.0
                avg[key] = 0.0
            cash += proceeds
            trades.append(
                {
                    "날짜": as_of.isoformat(),
                    "종목": meta[key]["name"],
                    "시장": meta[key]["market"],
                    "신호": action,
                    "체결": "매도",
                    "수량": qty,
                    "가격": px,
                    "원화": proceeds,
                    "비중": f"{cur_w * 100:.1f}%→목표 {target * 100:.1f}%",
                }
            )
            total, values = mtm(as_of, fx)

        # 매수: 신호 + 월 적립 현금, 목표+drift를 넘기지 않음
        buy_candidates = []
        for key, row in daily_by_key.items():
            day = row.get(as_of)
            if not day:
                continue
            action = day.get("신호")
            if action not in BUY_ACTIONS:
                continue
            px = float(day["가격"])
            if px <= 0:
                continue
            buy_candidates.append((key, action, px))
        buy_candidates.sort(key=lambda t: (values.get(t[0], 0) / total if total else 0) - weights.get(t[0], 0))

        for key, action, px in buy_candidates:
            if cash <= 0:
                break
            krw_px = _native_to_krw(px, meta[key]["market"], fx)
            if not krw_px:
                continue
            total, values = mtm(as_of, fx)
            target = weights.get(key, 0)
            cur_w = (values.get(key, 0) / total) if total > 0 else 0.0
            if cur_w >= target + drift:
                continue
            room_krw = max(0.0, (target + drift) * (total + cash) - values.get(key, 0))
            sim_d = meta[key]["sim"]
            buy_map, _sp, _sf, _sc = _qty_maps(sim_d)
            lot = _lot(meta[key]["market"])
            want = _floor_lot(float(buy_map.get(action) or 0), lot)
            if want <= 0:
                continue
            max_by_cash = _floor_lot(cash / krw_px, lot)
            max_by_room = _floor_lot(room_krw / krw_px, lot)
            qty = min(want, max_by_cash, max_by_room)
            if qty <= 0:
                continue
            cost = qty * krw_px
            prev = shares[key]
            avg[key] = (avg[key] * prev + px * qty) / (prev + qty)
            shares[key] = prev + qty
            cash -= cost
            trades.append(
                {
                    "날짜": as_of.isoformat(),
                    "종목": meta[key]["name"],
                    "시장": meta[key]["market"],
                    "신호": action,
                    "체결": "매수",
                    "수량": qty,
                    "가격": px,
                    "원화": cost,
                    "비중": f"목표 {target * 100:.1f}%",
                }
            )

    fx_end = _fx_on(fx_hist, end, fx_fallback)
    total, values = mtm(end, fx_end)
    holdings = []
    for key, info in meta.items():
        sh = shares[key]
        px = last_px.get(key) or 0.0
        val = values.get(key, 0.0)
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
                "실제비중": w * 100,
                "목표비중": weights.get(key, 0) * 100,
            }
        )
    result.cash_krw = cash
    result.contributed_krw = contributed
    result.holdings = holdings
    result.trades = trades
    result.months_log = months_log
    return result
