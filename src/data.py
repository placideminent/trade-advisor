"""OHLCV 수집. 분석일은 지정일 이전 데이터만 사용한다."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from pathlib import Path

import pandas as pd

from .universe import CRYPTO

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
    out.index = pd.to_datetime(out.index).tz_localize(None)
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


def _fetch_yf(symbol: str, start: date, end: date) -> pd.DataFrame:
    import yfinance as yf

    # yfinance end is exclusive on daily bars in some versions
    raw = yf.download(
        symbol,
        start=start.isoformat(),
        end=(end + timedelta(days=1)).isoformat(),
        auto_adjust=True,
        progress=False,
        threads=False,
    )
    return _normalize_ohlcv(raw)


def fetch_ohlcv(
    market: str,
    ticker: str,
    as_of: date,
    lookback_days: int = 365,
) -> tuple[pd.DataFrame, dict]:
    """지정일(as_of)까지의 일봉만 반환. 미래 데이터는 넣지 않는다."""
    as_of = _to_date(as_of)
    start = as_of - timedelta(days=int(lookback_days * 1.6) + 10)
    meta = {"market": market, "ticker": ticker, "name": ticker, "source": ""}

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
        try:
            df = _fetch_fdr(code, start, as_of)
            meta["source"] = "FinanceDataReader"
        except Exception:
            df = pd.DataFrame()
        if df.empty:
            last_err = None
            for suffix in (".KS", ".KQ"):
                try:
                    df = _fetch_yf(f"{code}{suffix}", start, as_of)
                    if not df.empty:
                        meta["source"] = f"Yahoo Finance ({code}{suffix})"
                        break
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
            df = _fetch_yf(symbol, start, as_of)
            meta["source"] = "Yahoo Finance"
        except Exception:
            df = _fetch_fdr(symbol, start, as_of)
            meta["source"] = "FinanceDataReader"
    elif market == "CRYPTO":
        key = ticker.strip().upper().replace("-USD", "").replace("USDT", "").replace("/", "")
        info = CRYPTO.get(key)
        symbol = info["symbol"] if info else f"{key}-USD"
        meta["ticker"] = key
        meta["name"] = info["name"] if info else key
        df = _fetch_yf(symbol, start, as_of)
        meta["source"] = "Yahoo Finance"
    else:
        raise ValueError(f"지원하지 않는 시장: {market}")

    if df.empty:
        return df, meta

    cutoff = pd.Timestamp(as_of) + pd.Timedelta(days=1) - pd.Timedelta(seconds=1)
    df = df.loc[df.index <= cutoff]
    return df, meta
