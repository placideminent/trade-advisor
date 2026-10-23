"""시장 지수(나스닥 ^IXIC) 일봉 RSI. 시장 과매도 매수 규칙(v118)용.

미래 데이터 금지: 미국 주식·코인은 해당 날짜 이하의 마지막 지수 종가, 한국 주식은
같은 날짜 미국 종가가 한국 장 마감 뒤라 그 전날까지의 미국 종가만 쓴다.
데이터를 못 받으면 None을 돌려 규칙을 건너뛴다.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from threading import Lock
from time import monotonic

import pandas as pd

NASDAQ_SYMBOL = "^IXIC"
RSI_DAYS = 14
_TTL_SEC = 1800.0
_LOCK = Lock()
_MEM: dict[str, tuple[float, pd.Series]] = {}
_OVERRIDE: dict[str, pd.Series] = {}


def rsi_series(closes: pd.Series, n: int = RSI_DAYS) -> pd.Series:
    """Wilder RSI (ewm alpha=1/n)."""
    s = pd.Series(closes, dtype=float).dropna()
    dl = s.diff()
    up = dl.clip(lower=0).ewm(alpha=1 / n, adjust=False).mean()
    dn = (-dl.clip(upper=0)).ewm(alpha=1 / n, adjust=False).mean()
    return 100 - 100 / (1 + up / dn)


def _as_date(v) -> date | None:
    if v is None:
        return None
    try:
        if isinstance(v, datetime):
            return v.date()
        if isinstance(v, date):
            return v
        return pd.Timestamp(v).date()
    except Exception:
        return None


def set_index_closes(symbol: str, closes: pd.Series | None) -> None:
    """백테스트·테스트용: 지수 종가를 직접 넣는다 (None이면 해제)."""
    if closes is None:
        _OVERRIDE.pop(symbol, None)
        return
    s = pd.Series(closes, dtype=float).dropna()
    s.index = [_as_date(x) for x in s.index]
    _OVERRIDE[symbol] = s.sort_index()


def _load_closes(symbol: str) -> pd.Series | None:
    if symbol in _OVERRIDE:
        return _OVERRIDE[symbol]
    now = monotonic()
    with _LOCK:
        hit = _MEM.get(symbol)
        if hit and now - hit[0] < _TTL_SEC:
            return hit[1]
    s = None
    try:
        from .data import CACHE_DIR, _fetch_daily

        safe = symbol.replace("^", "_")
        cache_file = CACHE_DIR / f"index_{safe}_{date.today().isoformat()}.csv"
        if cache_file.exists():
            df = pd.read_csv(cache_file, index_col=0)
            s = df.iloc[:, 0]
        else:
            end = date.today() + timedelta(days=1)
            df = _fetch_daily(symbol, end - timedelta(days=800), end)
            if df is not None and not df.empty:
                cols = {str(c).lower(): c for c in df.columns}
                if "close" in cols:
                    s = df[cols["close"]].astype(float)
                    s.to_frame("close").to_csv(cache_file)
        if s is not None and len(s):
            s = s.dropna()
            s.index = [_as_date(x) for x in s.index]
            s = s[~pd.Index(s.index).duplicated(keep="last")].sort_index()
        else:
            s = None
    except Exception:
        s = None
    if s is not None:
        with _LOCK:
            _MEM[symbol] = (now, s)
    return s


def index_rsi_on(as_of, market: str | None, symbol: str = NASDAQ_SYMBOL) -> float | None:
    """as_of 기준 지수 RSI(14). 한국 주식은 전날까지의 미국 종가만. 없으면 None."""
    d = _as_date(as_of)
    if d is None:
        return None
    s = _load_closes(symbol)
    if s is None or len(s) < RSI_DAYS + 5:
        return None
    if str(market or "").upper() == "KR":
        s = s[[x < d for x in s.index]]
    else:
        s = s[[x <= d for x in s.index]]
    if len(s) < RSI_DAYS + 5:
        return None
    v = float(rsi_series(s).iloc[-1])
    return v if v == v else None
