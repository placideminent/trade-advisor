"""만기 임박 옵션 콜월·풋월. 현재 체인만 쓰며 과거 시점에는 적용하지 않는다."""

from __future__ import annotations

from datetime import date, datetime, timezone

import pandas as pd

OPTION_DTE_MAX = 14
OPTION_NEAR_PCT = 0.05
OPTION_FAR_PCT = 0.08
OPTION_THICK_RATIO = 1.8
OPTION_THICK_FRAC = 0.25
OPTION_THIN_FRAC = 0.12
OPTION_SCAN_PCT = 0.15

BUY_ACTIONS = ("약한 매수", "매수", "강한 매수")
SELL_ACTIONS = ("약한 매도", "매도", "강한 매도")


def _to_date(value) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return pd.Timestamp(value).date()


def _unix_date(ts) -> date | None:
    try:
        return datetime.fromtimestamp(int(ts), tz=timezone.utc).date()
    except (TypeError, ValueError, OSError, OverflowError):
        return None


def _oi(val) -> int:
    try:
        if val is None or (isinstance(val, float) and pd.isna(val)):
            return 0
        return max(int(float(val)), 0)
    except (TypeError, ValueError):
        return 0


def _strike(val) -> float | None:
    try:
        px = float(val)
    except (TypeError, ValueError):
        return None
    return px if px > 0 else None


def _rows_from_records(records) -> list[tuple[float, int]]:
    out: list[tuple[float, int]] = []
    if records is None:
        return out
    if isinstance(records, pd.DataFrame):
        if records.empty or "strike" not in records.columns:
            return out
        oi_col = "openInterest" if "openInterest" in records.columns else None
        strikes = records["strike"].tolist()
        ois = records[oi_col].tolist() if oi_col else [0] * len(strikes)
        for strike_v, oi_v in zip(strikes, ois):
            strike = _strike(strike_v)
            if not strike:
                continue
            out.append((strike, _oi(oi_v)))
        return out
    for item in records:
        if not isinstance(item, dict):
            continue
        strike = _strike(item.get("strike"))
        if not strike:
            continue
        out.append((strike, _oi(item.get("openInterest"))))
    return out


def _pick_wall(rows: list[tuple[float, int]], price: float, *, above: bool) -> tuple[float | None, int, float | None]:
    if price <= 0:
        return None, 0, None
    band = price * OPTION_SCAN_PCT
    if above:
        cand = [(s, oi) for s, oi in rows if s > price]
        near = [(s, oi) for s, oi in cand if s - price <= band]
    else:
        cand = [(s, oi) for s, oi in rows if s < price]
        near = [(s, oi) for s, oi in cand if price - s <= band]
    pool = near or cand
    if not pool:
        return None, 0, None
    best_oi = max(oi for _s, oi in pool)
    tied = [(s, oi) for s, oi in pool if oi == best_oi]
    strike, oi = min(tied, key=lambda x: abs(x[0] - price))
    return strike, oi, abs(strike - price) / price


def _thick(oi: int, other: int, chain_max: int) -> bool:
    if oi <= 0:
        return False
    rel_other = other > 0 and oi >= other * OPTION_THICK_RATIO
    rel_chain = chain_max > 0 and oi >= chain_max * OPTION_THICK_FRAC
    return bool(rel_other or rel_chain)


def _thin(oi: int, other: int, chain_max: int) -> bool:
    if oi <= 0:
        return True
    if chain_max > 0 and oi < chain_max * OPTION_THIN_FRAC:
        return True
    if other > 0 and oi * OPTION_THICK_RATIO <= other:
        return True
    return False


def _pct_txt(pct: float | None, above: bool) -> str:
    if pct is None:
        return "없음"
    side = "위" if above else "아래"
    return f"{side} {pct * 100:.1f}%"


def option_wall_adjust(action: str, walls: dict | None, weight: int = 1) -> tuple[int, str]:
    """기존 매수/매도 판정 뒤 추가 점수. weight 는 보통 1."""
    w = abs(int(weight or 0))
    if not w:
        return 0, "옵션 월 배점 0"
    if not isinstance(walls, dict):
        return 0, "옵션 포지션 없음"
    if walls.get("error"):
        return 0, f"옵션 포지션을 받지 못함 ({walls.get('error')})"
    if not walls.get("soon"):
        dte = walls.get("days_left")
        exp = walls.get("expiry") or "-"
        extra = f"최근 만기 {exp}"
        if dte is not None:
            extra += f" ({dte}일)"
        return 0, f"만기가 {OPTION_DTE_MAX}일 안에 없음 · {extra}"
    if action == "홀딩" or action not in BUY_ACTIONS + SELL_ACTIONS:
        return 0, "홀딩이라 옵션 월을 가감하지 않음"

    call_oi = _oi(walls.get("call_oi"))
    put_oi = _oi(walls.get("put_oi"))
    chain_max = _oi(walls.get("chain_max_oi")) or max(call_oi, put_oi)
    call_pct = walls.get("call_pct")
    put_pct = walls.get("put_pct")
    try:
        call_pct = float(call_pct) if call_pct is not None else None
    except (TypeError, ValueError):
        call_pct = None
    try:
        put_pct = float(put_pct) if put_pct is not None else None
    except (TypeError, ValueError):
        put_pct = None

    call_near = call_pct is not None and call_pct <= OPTION_NEAR_PCT
    put_near = put_pct is not None and put_pct <= OPTION_NEAR_PCT
    call_far = call_pct is None or call_pct > OPTION_FAR_PCT
    put_far = put_pct is None or put_pct > OPTION_FAR_PCT
    call_thick = _thick(call_oi, put_oi, chain_max)
    put_thick = _thick(put_oi, call_oi, chain_max)
    call_thin = _thin(call_oi, put_oi, chain_max)
    put_thin = _thin(put_oi, call_oi, chain_max)

    exp = walls.get("expiry") or "-"
    dte = walls.get("days_left")
    head = f"만기 {exp}" + (f" ({dte}일)" if dte is not None else "")
    call_s = walls.get("call_strike")
    put_s = walls.get("put_strike")
    call_txt = "콜월 없음" if not call_s else f"콜월 {call_s:g} OI {call_oi:,} ({_pct_txt(call_pct, True)})"
    put_txt = "풋월 없음" if not put_s else f"풋월 {put_s:g} OI {put_oi:,} ({_pct_txt(put_pct, False)})"
    base = f"{head} · {call_txt} · {put_txt}"

    both_far = call_far and put_far
    both_thin = call_thin and put_thin
    thick_call_thin_put = call_near and call_thick and put_thin
    thick_put_thin_call = put_near and put_thick and call_thin

    if action in SELL_ACTIONS:
        if both_far:
            return 0, f"{base} · 콜·풋월이 멀리 있어 0점"
        if both_thin:
            return 0, f"{base} · 콜·풋월이 둘 다 얇아 0점"
        if thick_call_thin_put:
            return -w, f"{base} · 근처 콜월 두껍/풋월 얇음"
        if thick_put_thin_call:
            return w, f"{base} · 근처 풋월 두껍/콜월 얇음"
        return 0, f"{base} · 매도 국면 해당 패턴 아님"

    # 매수: 근처 풋얇 + 근처 콜두껍만 -1. 반대·그외 0.
    if put_near and put_thin and call_near and call_thick:
        return -w, f"{base} · 근처 풋월 얇음/콜월 두꺼움"
    return 0, f"{base} · 매수 국면 추가 감점 없음"


def _pick_expiry(expiries: list[date], as_of: date) -> tuple[date | None, date | None, int | None]:
    future = sorted(d for d in expiries if d >= as_of)
    if not future:
        return None, None, None
    soon = [d for d in future if (d - as_of).days <= OPTION_DTE_MAX]
    if soon:
        d = soon[0]
        return d, d, (d - as_of).days
    d = future[0]
    return None, d, (d - as_of).days


def _walls_from_chain(calls, puts, price: float, expiry: date, as_of: date, source: str, soon: bool) -> dict:
    call_rows = _rows_from_records(calls)
    put_rows = _rows_from_records(puts)
    chain_max = 0
    for _s, oi in call_rows + put_rows:
        if oi > chain_max:
            chain_max = oi
    call_s, call_oi, call_pct = _pick_wall(call_rows, price, above=True)
    put_s, put_oi, put_pct = _pick_wall(put_rows, price, above=False)
    return {
        "expiry": expiry.isoformat(),
        "days_left": (expiry - as_of).days,
        "soon": bool(soon),
        "call_strike": call_s,
        "call_oi": call_oi,
        "call_pct": call_pct,
        "put_strike": put_s,
        "put_oi": put_oi,
        "put_pct": put_pct,
        "chain_max_oi": chain_max,
        "source": source,
        "error": None,
    }


def _fetch_yahoo_options(symbol: str, as_of: date, price: float) -> dict | None:
    import requests

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Accept": "application/json",
    }
    last_err = None
    payload = None
    for host in ("query1.finance.yahoo.com", "query2.finance.yahoo.com"):
        try:
            resp = requests.get(
                f"https://{host}/v7/finance/options/{symbol}",
                headers=headers,
                timeout=12,
            )
            resp.raise_for_status()
            payload = resp.json()
            break
        except Exception as extra:
            last_err = extra
            payload = None
    if not payload:
        return None if last_err is None else {"error": str(last_err)[:120]}

    result = ((payload.get("optionChain") or {}).get("result") or [])
    if not result:
        return {"error": "옵션 체인 없음"}
    node = result[0]
    expiries = []
    for ts in node.get("expirationDates") or []:
        day = _unix_date(ts)
        if day:
            expiries.append(day)
    soon, nearest, dte = _pick_expiry(expiries, as_of)
    target = soon or nearest
    if target is None:
        return {"error": "만기 일정 없음"}

    options = (node.get("options") or [{}])[0]
    exp_ts = int(datetime(target.year, target.month, target.day, tzinfo=timezone.utc).timestamp())
    got_exp = _unix_date(options.get("expirationDate"))
    if got_exp != target:
        fetched = None
        for host in ("query1.finance.yahoo.com", "query2.finance.yahoo.com"):
            try:
                resp = requests.get(
                    f"https://{host}/v7/finance/options/{symbol}",
                    params={"date": exp_ts},
                    headers=headers,
                    timeout=12,
                )
                resp.raise_for_status()
                nxt = ((resp.json().get("optionChain") or {}).get("result") or [])
                if nxt:
                    fetched = (nxt[0].get("options") or [{}])[0]
                    break
            except Exception:
                continue
        if fetched:
            options = fetched

    quote_px = _strike((node.get("quote") or {}).get("regularMarketPrice"))
    px = price if price > 0 else (quote_px or 0)
    if px <= 0:
        return {"error": "현재가 없음"}
    return _walls_from_chain(
        options.get("calls") or [],
        options.get("puts") or [],
        px,
        target,
        as_of,
        "Yahoo 옵션",
        soon is not None,
    )


def _fetch_yf_options(symbol: str, as_of: date, price: float) -> dict | None:
    import yfinance as yf

    ticker = yf.Ticker(symbol)
    raw = list(ticker.options or [])
    expiries = []
    for item in raw:
        try:
            expiries.append(date.fromisoformat(str(item)[:10]))
        except ValueError:
            continue
    soon, nearest, dte = _pick_expiry(expiries, as_of)
    target = soon or nearest
    if target is None:
        return {"error": "만기 일정 없음"}
    chain = ticker.option_chain(target.isoformat())
    px = price if price > 0 else 0
    if px <= 0:
        return {"error": "현재가 없음"}
    walls = _walls_from_chain(
        chain.calls,
        chain.puts,
        px,
        target,
        as_of,
        "Yahoo yfinance",
        soon is not None,
    )
    if dte is not None and soon is None:
        walls["days_left"] = dte
    return walls


def fetch_option_walls(ticker: str, as_of: date, price: float) -> dict:
    """US 주식 현재 옵션 체인에서 가장 가까운 만기의 콜월·풋월."""
    as_of = _to_date(as_of)
    symbol = str(ticker or "").strip().upper().replace("/", "-")
    if not symbol:
        return {"error": "티커 없음", "soon": False}
    try:
        walls = _fetch_yahoo_options(symbol, as_of, float(price or 0))
        if walls and not walls.get("error") and int(walls.get("chain_max_oi") or 0) > 0:
            return walls
        yf_walls = _fetch_yf_options(symbol, as_of, float(price or 0))
        if yf_walls and not yf_walls.get("error"):
            return yf_walls
        return walls or yf_walls or {"error": "옵션 체인 없음", "soon": False}
    except Exception as extra:
        return {"error": str(extra)[:120], "soon": False}
