"""OHLCV 수집. 분석일은 지정일 이전 데이터만 사용한다."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

from .universe import CRYPTO

MARKET_TZ = {
    "KR": "Asia/Seoul",
    "US": "America/New_York",
    "CRYPTO": "UTC",
}

def _cache_dir() -> Path:
    primary = Path(__file__).resolve().parent.parent / ".cache"
    try:
        primary.mkdir(exist_ok=True)
        return primary
    except OSError:
        fallback = Path.home() / ".cache" / "trade-advisor"
        fallback.mkdir(parents=True, exist_ok=True)
        return fallback


CACHE_DIR = _cache_dir()

_KR_LISTING: pd.DataFrame | None = None


def _to_date(value) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return pd.Timestamp(value).date()


def load_kr_listing() -> pd.DataFrame:
    """KRX 종목 코드/이름. 하루 단위로 캐시."""
    global _KR_LISTING
    if _KR_LISTING is not None:
        return _KR_LISTING

    cache_file = CACHE_DIR / f"krx_{date.today().isoformat()}.csv"
    if cache_file.exists():
        _KR_LISTING = pd.read_csv(cache_file, dtype={"Code": str})
        return _KR_LISTING

    import FinanceDataReader as fdr

    listing = fdr.StockListing("KRX")
    listing = listing.rename(columns={"Code": "Code", "Name": "Name"})
    if "Code" not in listing.columns and "Symbol" in listing.columns:
        listing = listing.rename(columns={"Symbol": "Code"})
    listing["Code"] = listing["Code"].astype(str).str.zfill(6)
    keep = [c for c in ["Code", "Name", "Market"] if c in listing.columns]
    listing = listing[keep].drop_duplicates("Code")
    listing.to_csv(cache_file, index=False)
    _KR_LISTING = listing
    return _KR_LISTING


def search_kr(query: str, limit: int = 20) -> pd.DataFrame:
    listing = load_kr_listing()
    q = query.strip()
    if not q:
        return listing.head(limit)
    digits = q.replace(" ", "")
    if digits.isdigit():
        mask = listing["Code"].str.contains(digits.zfill(6) if len(digits) <= 6 else digits)
    else:
        mask = listing["Name"].str.contains(q, case=False, na=False)
    return listing.loc[mask].head(limit)


def _normalize_ohlcv(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])

    out = df.copy()
    if isinstance(out.columns, pd.MultiIndex):
        out.columns = [str(c[0]).lower() for c in out.columns]
    else:
        out.columns = [str(c).lower() for c in out.columns]

    rename = {}
    for col in out.columns:
        key = col.replace(" ", "")
        if key in ("adjclose", "adjustedclose"):
            rename[col] = "close"
        elif key in ("open", "high", "low", "close", "volume"):
            rename[col] = key
    out = out.rename(columns=rename)

    needed = ["open", "high", "low", "close", "volume"]
    missing = [c for c in needed if c not in out.columns]
    if missing:
        raise ValueError(f"OHLCV 컬럼 없음: {missing} / 실제={list(df.columns)}")

    out = out[needed].copy()
    out.index = pd.to_datetime(out.index)
    out = out.sort_index()
    out = out[~out.index.duplicated(keep="last")]
    out = out.dropna(subset=["open", "high", "low", "close"])
    out["volume"] = out["volume"].fillna(0)
    for col in needed:
        out[col] = pd.to_numeric(out[col], errors="coerce")
    out = out.dropna(subset=["open", "high", "low", "close"])
    return out


def _fetch_fdr(code: str, start: date, end: date) -> pd.DataFrame:
    import FinanceDataReader as fdr

    raw = fdr.DataReader(code, start.isoformat(), end.isoformat())
    return _normalize_ohlcv(raw)


def _index_naive_wall(idx) -> pd.DatetimeIndex:
    idx = pd.DatetimeIndex(pd.to_datetime(idx))
    if idx.tz is not None:
        return pd.to_datetime(idx.strftime("%Y-%m-%d %H:%M:%S"))
    return idx


def _fetch_yf(symbol: str, start: date, end: date, interval: str = "1d") -> pd.DataFrame:
    import yfinance as yf

    raw = yf.download(
        symbol,
        start=start.isoformat(),
        end=(end + timedelta(days=1)).isoformat(),
        interval=interval,
        auto_adjust=True,
        progress=False,
        threads=False,
    )
    return _normalize_ohlcv(raw)


def _market_tz(market: str):
    name = MARKET_TZ.get(market, "UTC")
    try:
        return ZoneInfo(name)
    except Exception:
        return timezone.utc


def resample_4h(df: pd.DataFrame, market: str) -> pd.DataFrame:
    """1시간봉을 시장 시간대 기준 4시간봉으로 합친다."""
    if df.empty:
        return df
    work = df.copy()
    tz = _market_tz(market)
    idx = pd.DatetimeIndex(pd.to_datetime(work.index))
    if idx.tz is None:
        idx = idx.tz_localize("UTC")
    work.index = idx.tz_convert(tz)
    out = work.resample("4h", label="left", closed="left").agg(
        open=("open", "first"),
        high=("high", "max"),
        low=("low", "min"),
        close=("close", "last"),
        volume=("volume", "sum"),
    )
    out = out.dropna(subset=["open", "high", "low", "close"])
    out.index = _index_naive_wall(out.index)
    return out


def _fetch_yahoo_chart(symbol: str, start: date, end: date, interval: str = "60m") -> pd.DataFrame:
    """yfinance가 막힐 때를 위한 Yahoo chart API. Streamlit Cloud에서 더 잘 되는 경우가 많다."""
    import requests

    p1 = int(datetime(start.year, start.month, start.day, tzinfo=timezone.utc).timestamp())
    p2 = int(datetime(end.year, end.month, end.day, 23, 59, tzinfo=timezone.utc).timestamp())
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Accept": "application/json",
    }
    params = {
        "period1": p1,
        "period2": p2,
        "interval": interval,
        "includePrePost": "false",
        "events": "div,splits",
    }
    last_err = None
    for host in ("query1.finance.yahoo.com", "query2.finance.yahoo.com"):
        try:
            resp = requests.get(
                f"https://{host}/v8/finance/chart/{symbol}",
                params=params,
                headers=headers,
                timeout=20,
            )
            resp.raise_for_status()
            result = (resp.json().get("chart") or {}).get("result") or []
            if not result:
                continue
            node = result[0]
            ts = node.get("timestamp") or []
            quote = ((node.get("indicators") or {}).get("quote") or [{}])[0]
            if not ts:
                continue
            raw = pd.DataFrame(
                {
                    "open": quote.get("open"),
                    "high": quote.get("high"),
                    "low": quote.get("low"),
                    "close": quote.get("close"),
                    "volume": quote.get("volume"),
                },
                index=pd.to_datetime(ts, unit="s", utc=True),
            )
            return _normalize_ohlcv(raw)
        except Exception as exc:
            last_err = exc
            continue
    if last_err:
        return pd.DataFrame()
    return pd.DataFrame()


def _fetch_intraday(symbol: str, start: date, end: date) -> pd.DataFrame:
    df = _fetch_yf(symbol, start, end, interval="1h")
    if not df.empty:
        return df
    df = _fetch_yahoo_chart(symbol, start, end, interval="60m")
    if not df.empty:
        return df
    return _fetch_yahoo_chart(symbol, start, end, interval="30m")


def _kr_yahoo_symbols(code: str) -> list[str]:
    suffixes = [".KS", ".KQ"]
    try:
        listing = load_kr_listing()
        row = listing.loc[listing["Code"] == code]
        if not row.empty and "Market" in row.columns:
            market_name = str(row.iloc[0]["Market"]).upper()
            if "KOSDAQ" in market_name:
                suffixes = [".KQ", ".KS"]
    except Exception:
        pass
    return [f"{code}{s}" for s in suffixes]


def fetch_ohlcv(
    market: str,
    ticker: str,
    as_of: date,
    lookback_days: int = 365,
    timeframe: str = "1d",
) -> tuple[pd.DataFrame, dict]:
    """지정일(as_of)까지의 봉만 반환. 1~3개월은 4시간봉, 그 이상은 일봉."""
    as_of = _to_date(as_of)
    timeframe = "4h" if timeframe == "4h" else "1d"
    interval = "1h" if timeframe == "4h" else "1d"
    pad = 7 if timeframe == "4h" else 10
    start = as_of - timedelta(days=int(lookback_days * 1.2) + pad)
    meta = {
        "market": market,
        "ticker": ticker,
        "name": ticker,
        "source": "",
        "timeframe": timeframe,
    }

    if market == "KR":
        code = ticker.strip().zfill(6)
        meta["ticker"] = code
        try:
            listing = load_kr_listing()
            row = listing.loc[listing["Code"] == code]
            if not row.empty:
                meta["name"] = str(row.iloc[0]["Name"])
        except Exception:
            pass
        df = pd.DataFrame()
        if timeframe == "1d":
            try:
                df = _fetch_fdr(code, start, as_of)
                meta["source"] = "FinanceDataReader"
            except Exception:
                df = pd.DataFrame()
        if df.empty:
            last_err = None
            for symbol in _kr_yahoo_symbols(code):
                try:
                    if timeframe == "4h":
                        df = _fetch_intraday(symbol, start, as_of)
                    else:
                        df = _fetch_yf(symbol, start, as_of, interval="1d")
                    if not df.empty:
                        meta["source"] = f"Yahoo Finance ({symbol})"
                        break
                except Exception as exc:
                    last_err = exc
                    df = pd.DataFrame()
            if df.empty and timeframe == "4h":
                try:
                    df = _fetch_fdr(code, start, as_of)
                    if not df.empty:
                        timeframe = "1d"
                        interval = "1d"
                        meta["source"] = "FinanceDataReader"
                        meta["note"] = "한국 주식 4시간봉을 받지 못해 일봉으로 계산합니다."
                except Exception as exc:
                    last_err = exc
                    df = pd.DataFrame()
            if df.empty and last_err:
                raise last_err
    elif market == "US":
        symbol = ticker.strip().upper()
        meta["ticker"] = symbol
        meta["name"] = symbol
        try:
            df = _fetch_intraday(symbol, start, as_of) if timeframe == "4h" else _fetch_yf(symbol, start, as_of)
            meta["source"] = "Yahoo Finance"
        except Exception:
            if timeframe == "4h":
                df = _fetch_yahoo_chart(symbol, start, as_of, interval="60m")
                meta["source"] = "Yahoo Finance"
                if df.empty:
                    raise
            else:
                df = _fetch_fdr(symbol, start, as_of)
                meta["source"] = "FinanceDataReader"
    elif market == "CRYPTO":
        key = ticker.strip().upper().replace("-USD", "").replace("USDT", "").replace("/", "")
        info = CRYPTO.get(key)
        symbol = info["symbol"] if info else f"{key}-USD"
        meta["ticker"] = key
        meta["name"] = info["name"] if info else key
        if timeframe == "4h":
            df = _fetch_intraday(symbol, start, as_of)
        else:
            df = _fetch_yf(symbol, start, as_of)
        meta["source"] = "Yahoo Finance"
    else:
        raise ValueError(f"지원하지 않는 시장: {market}")

    if df.empty:
        return df, meta

    meta["timeframe"] = timeframe
    if timeframe == "4h":
        df = resample_4h(df, market)
        meta["bar"] = "4시간봉"
    else:
        df = df.copy()
        df.index = _index_naive_wall(df.index)
        meta["bar"] = "일봉"

    cutoff = pd.Timestamp(as_of) + pd.Timedelta(days=1) - pd.Timedelta(seconds=1)
    window_start = pd.Timestamp(as_of) - pd.Timedelta(days=lookback_days)
    df = df.loc[(df.index >= window_start) & (df.index <= cutoff)]
    return df, meta
