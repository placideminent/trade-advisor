"""OHLCV 수집. 분석일은 지정일 이전 데이터만 사용한다."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from time import monotonic
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


_yahoo_fail_streak = 0
_yahoo_skip_until = 0.0


def _yahoo_blocked() -> bool:
    return monotonic() < _yahoo_skip_until


def reset_yahoo_gate() -> None:
    """종목을 바꿀 때 Yahoo 차단을 풀어 다음 종목이 바로 건너뛰지 않게 한다."""
    global _yahoo_fail_streak, _yahoo_skip_until
    _yahoo_fail_streak = 0
    _yahoo_skip_until = 0.0


def _mark_yahoo(ok: bool) -> None:
    """연속 실패면 잠시 Yahoo를 건너뛰고 FDR/Naver로 넘어가게 한다."""
    global _yahoo_fail_streak, _yahoo_skip_until
    if ok:
        _yahoo_fail_streak = 0
        return
    _yahoo_fail_streak += 1
    if _yahoo_fail_streak >= 2:
        _yahoo_skip_until = monotonic() + 5.0


def _fetch_yf(
    symbol: str,
    start: date,
    end: date,
    interval: str = "1d",
    *,
    respect_gate: bool = True,
) -> pd.DataFrame:
    import yfinance as yf

    if respect_gate and _yahoo_blocked():
        return pd.DataFrame()
    kwargs = dict(
        start=start.isoformat(),
        end=(end + timedelta(days=1)).isoformat(),
        interval=interval,
        auto_adjust=True,
        progress=False,
        threads=False,
        timeout=12,
    )
    try:
        try:
            raw = yf.download(symbol, **kwargs)
        except TypeError:
            kwargs.pop("timeout", None)
            raw = yf.download(symbol, **kwargs)
        df = _normalize_ohlcv(raw)
        _mark_yahoo(not df.empty)
        return df
    except Exception:
        _mark_yahoo(False)
        return pd.DataFrame()


def _market_tz(market: str):
    name = MARKET_TZ.get(market, "UTC")
    try:
        return ZoneInfo(name)
    except Exception:
        return timezone.utc


def market_today(market: str) -> date:
    """그 시장 달력의 오늘. Streamlit Cloud UTC와 한국 날짜가 어긋나지 않게 쓴다."""
    return datetime.now(_market_tz(market)).date()


def drop_incomplete_session(df: pd.DataFrame, as_of: date) -> pd.DataFrame:
    """당일 미완성 봉을 빼고, 직전 완성 세션까지만 구조·지표에 쓴다."""
    if df is None or df.empty:
        return df
    idx = pd.DatetimeIndex(pd.to_datetime(df.index))
    keep = df.loc[idx.normalize() < pd.Timestamp(as_of)]
    if len(keep) >= 20:
        return keep.copy()
    if len(df) > 1:
        return df.iloc[:-1].copy()
    return df


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


def to_market_wall(df: pd.DataFrame, market: str) -> pd.DataFrame:
    """시세 인덱스를 시장 시간대 벽시계로 맞춘다."""
    if df.empty:
        return df
    work = df.copy()
    tz = _market_tz(market)
    idx = pd.DatetimeIndex(pd.to_datetime(work.index))
    if idx.tz is None:
        idx = idx.tz_localize("UTC")
    work.index = idx.tz_convert(tz)
    work.index = _index_naive_wall(work.index)
    return work


def _fetch_yahoo_chart(symbol: str, start: date, end: date, interval: str = "60m") -> pd.DataFrame:
    """yfinance가 막힐 때를 위한 Yahoo chart API. Streamlit Cloud에서 더 잘 되는 경우가 많다."""
    from time import sleep

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
        for attempt in range(3):
            try:
                resp = requests.get(
                    f"https://{host}/v8/finance/chart/{symbol}",
                    params=params,
                    headers=headers,
                    timeout=15,
                )
                if resp.status_code in (429, 503, 502):
                    sleep(1.2 * (attempt + 1))
                    continue
                resp.raise_for_status()
                result = (resp.json().get("chart") or {}).get("result") or []
                if not result:
                    break
                node = result[0]
                ts = node.get("timestamp") or []
                quote = ((node.get("indicators") or {}).get("quote") or [{}])[0]
                if not ts:
                    break
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
                df = _normalize_ohlcv(raw)
                _mark_yahoo(not df.empty)
                if df.empty:
                    break
                return df
            except Exception as exc:
                last_err = exc
                sleep(0.6 * (attempt + 1))
                continue
    _mark_yahoo(False)
    if last_err:
        return pd.DataFrame()
    return pd.DataFrame()


def _fetch_intraday(symbol: str, start: date, end: date) -> pd.DataFrame:
    df = _fetch_yf(symbol, start, end, interval="1h")
    if not df.empty:
        return df
    return _fetch_yahoo_chart(symbol, start, end, interval="60m")


def fetch_intraday_range(market: str, ticker: str, start: date, end: date) -> pd.DataFrame:
    """1시간봉을 구간별로 나눠 받아 이어 붙인다. Yahoo가 긴 구간을 줄이지 않게 한다."""
    frames: list[pd.DataFrame] = []
    cur = start
    while cur <= end:
        if _yahoo_blocked() and not frames:
            break
        nxt = min(cur + timedelta(days=55), end)
        chunk = pd.DataFrame()
        try:
            if market == "KR":
                code = _kr_code(ticker)
                for symbol in _kr_yahoo_symbols(code):
                    try:
                        chunk = _fetch_intraday(symbol, cur - timedelta(days=1), nxt)
                    except Exception:
                        chunk = pd.DataFrame()
                    if not chunk.empty:
                        break
            elif market == "US":
                chunk = _fetch_intraday(ticker.strip().upper(), cur - timedelta(days=1), nxt)
            else:
                key = ticker.strip().upper().replace("-USD", "").replace("USDT", "").replace("/", "")
                info = CRYPTO.get(key)
                symbol = info["symbol"] if info else f"{key}-USD"
                chunk = _fetch_intraday(symbol, cur - timedelta(days=1), nxt)
        except Exception:
            chunk = pd.DataFrame()
        if not chunk.empty:
            frames.append(chunk)
        cur = nxt + timedelta(days=1)
    if not frames:
        return pd.DataFrame()
    out = pd.concat(frames).sort_index()
    return out[~out.index.duplicated(keep="last")]


def _kr_code(ticker: str) -> str:
    raw = str(ticker or "").strip()
    if raw.isdigit():
        return raw.zfill(6)
    return raw


def _us_yahoo_symbols(ticker: str) -> list[str]:
    raw = str(ticker or "").strip().upper().replace("/", "-")
    aliases = {
        "GOOGL": ["GOOGL", "GOOG"],
        "GOOG": ["GOOG", "GOOGL"],
        "BRK.B": ["BRK-B", "BRK.B"],
        "BRK.A": ["BRK-A", "BRK.A"],
        "BRK-B": ["BRK-B", "BRK.B"],
        "BRK-A": ["BRK-A", "BRK.A"],
    }
    out = list(aliases.get(raw, [raw]))
    dotted = raw.replace("-", ".")
    if dotted not in out:
        out.append(dotted)
    return list(dict.fromkeys([s for s in out if s]))


COINGECKO_IDS = {
    "BTC": "bitcoin",
    "ETH": "ethereum",
    "SOL": "solana",
    "XRP": "ripple",
    "ONDO": "ondo-finance",
    "BNB": "binancecoin",
    "DOGE": "dogecoin",
}


def _fetch_coingecko_daily(key: str, start: date, end: date) -> pd.DataFrame:
    """Yahoo가 막힌 코인 일봉 폴백."""
    import requests

    cid = COINGECKO_IDS.get(str(key or "").strip().upper())
    if not cid:
        return pd.DataFrame()
    span = max((end - start).days + 3, 7)
    days = 365 if span > 180 else 180 if span > 90 else 90 if span > 30 else 30
    try:
        resp = requests.get(
            f"https://api.coingecko.com/api/v3/coins/{cid}/ohlc",
            params={"vs_currency": "usd", "days": days},
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=20,
        )
        resp.raise_for_status()
        rows = resp.json()
        if not isinstance(rows, list) or not rows:
            return pd.DataFrame()
        recs = []
        for row in rows:
            if not isinstance(row, (list, tuple)) or len(row) < 5:
                continue
            recs.append(
                {
                    "date": pd.to_datetime(row[0], unit="ms", utc=True),
                    "open": row[1],
                    "high": row[2],
                    "low": row[3],
                    "close": row[4],
                    "volume": 0,
                }
            )
        if not recs:
            return pd.DataFrame()
        raw = pd.DataFrame(recs).set_index("date").sort_index()
        return _normalize_ohlcv(raw)
    except Exception:
        return pd.DataFrame()


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


def _fetch_stooq_daily(symbol: str, start: date, end: date, *, crypto: bool = False) -> pd.DataFrame:
    """Yahoo가 막힐 때 미국·코인 일봉 폴백."""
    import io
    import requests

    raw = str(symbol or "").strip().lower()
    if not raw:
        return pd.DataFrame()
    if crypto:
        key = raw.replace("-usd", "").replace("usdt", "").replace("/", "")
        query = f"{key}usd"
    else:
        query = raw.replace("/", "-")
        if "." not in query and "-" not in query:
            query = f"{query}.us"
    try:
        resp = requests.get(
            "https://stooq.com/q/d/l/",
            params={
                "s": query,
                "d1": start.strftime("%Y%m%d"),
                "d2": end.strftime("%Y%m%d"),
                "i": "d",
            },
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=15,
        )
        resp.raise_for_status()
        text = (resp.text or "").strip()
        if not text or text[:1] == "<" or "no data" in text.lower():
            return pd.DataFrame()
        raw_df = pd.read_csv(io.StringIO(text))
        rename = {c: str(c).strip().lower() for c in raw_df.columns}
        raw_df = raw_df.rename(columns=rename)
        if "date" not in raw_df.columns or "close" not in raw_df.columns:
            return pd.DataFrame()
        raw_df["date"] = pd.to_datetime(raw_df["date"], errors="coerce")
        raw_df = raw_df.dropna(subset=["date"]).set_index("date").sort_index()
        return _normalize_ohlcv(raw_df)
    except Exception:
        return pd.DataFrame()


def _fetch_daily(symbol: str, start: date, end: date) -> pd.DataFrame:
    df = pd.DataFrame()
    try:
        if _yahoo_blocked():
            from time import sleep

            sleep(1.0)
        df = _fetch_yf(symbol, start, end, interval="1d", respect_gate=False)
    except Exception:
        df = pd.DataFrame()
    if df.empty:
        df = _fetch_yahoo_chart(symbol, start, end, interval="1d")
    return df


def _fetch_naver_daily(code: str, start: date, end: date) -> pd.DataFrame:
    """Cloud에서 FDR/Yahoo가 막힐 때 한국 일봉 폴백."""
    import json
    import re
    import requests

    code = _kr_code(code)
    if not code.isdigit():
        return pd.DataFrame()
    params = {
        "symbol": code,
        "requestType": "1",
        "startTime": start.strftime("%Y%m%d"),
        "endTime": end.strftime("%Y%m%d"),
        "timeframe": "day",
    }
    headers = {
        "User-Agent": "Mozilla/5.0",
        "Referer": "https://finance.naver.com/",
    }
    for url in (
        "https://api.finance.naver.com/siseJson.naver",
        "https://fchart.stock.naver.com/siseJson.nhn",
    ):
        try:
            resp = requests.get(url, params=params, headers=headers, timeout=20)
            resp.raise_for_status()
            text = resp.text.strip()
            text = re.sub(r"^[^(]*\(", "", text)
            text = re.sub(r"\)\s*;?\s*$", "", text)
            rows = json.loads(text.replace("'", '"'))
            if not isinstance(rows, list) or len(rows) < 2:
                continue
            recs = []
            for row in rows[1:]:
                if not isinstance(row, (list, tuple)) or len(row) < 6:
                    continue
                recs.append(
                    {
                        "date": str(row[0]),
                        "open": row[1],
                        "high": row[2],
                        "low": row[3],
                        "close": row[4],
                        "volume": row[5],
                    }
                )
            if not recs:
                continue
            out = pd.DataFrame(recs)
            out.index = pd.to_datetime(out["date"], errors="coerce")
            out = out.drop(columns=["date"])
            return _normalize_ohlcv(out)
        except Exception:
            continue
    return pd.DataFrame()


def _parse_price(val) -> float | None:
    if val is None:
        return None
    if isinstance(val, (int, float)):
        px = float(val)
        return px if px > 0 else None
    text = str(val).replace(",", "").replace(" ", "").strip()
    if not text:
        return None
    try:
        px = float(text)
    except (TypeError, ValueError):
        return None
    return px if px > 0 else None


def _naver_payload_price(node: dict) -> tuple[float | None, str]:
    """정규장 현재가, 장 마감 후면 NXT 시간외 최근 체결가."""
    if not isinstance(node, dict):
        return None, ""
    if isinstance(node.get("datas"), list) and node["datas"]:
        node = node["datas"][0] if isinstance(node["datas"][0], dict) else node
    regular = _parse_price(
        node.get("closePrice")
        or node.get("nv")
        or node.get("nowVal")
        or node.get("currentPrice")
    )
    market_status = str(node.get("marketStatus") or "").upper()
    over = node.get("overMarketPriceInfo")
    over = over if isinstance(over, dict) else {}
    over_px = _parse_price(over.get("overPrice"))
    session = str(over.get("tradingSessionType") or "").upper()
    over_status = str(over.get("overMarketStatus") or "").upper()
    off_hours = session in ("AFTER_MARKET", "PRE_MARKET")
    if over_px and off_hours and (over_status == "OPEN" or market_status != "OPEN"):
        label = "Naver 시간외(프리)" if "PRE" in session else "Naver 시간외(NXT)"
        return over_px, label
    if (
        over_px
        and market_status != "OPEN"
        and regular
        and abs(over_px - regular) > 1e-9
    ):
        return over_px, "Naver 시간외(NXT)"
    if regular:
        return regular, "Naver 현재가"
    if over_px:
        return over_px, "Naver 시간외"
    return None, ""


def _naver_spot(code: str) -> tuple[float | None, str]:
    import requests

    headers = {
        "User-Agent": "Mozilla/5.0",
        "Referer": "https://m.stock.naver.com/",
    }
    urls = (
        f"https://m.stock.naver.com/api/stock/{code}/basic",
        f"https://polling.finance.naver.com/api/realtime/domestic/stock/{code}",
    )
    for url in urls:
        try:
            resp = requests.get(url, headers=headers, timeout=10)
            resp.raise_for_status()
            px, src = _naver_payload_price(resp.json())
            if px:
                return px, src
        except Exception:
            continue
    try:
        resp = requests.get(
            "https://polling.finance.naver.com/api/realtime",
            params={"query": f"SERVICE_ITEM:{code}"},
            headers=headers,
            timeout=10,
        )
        resp.raise_for_status()
        datas = (((resp.json().get("result") or {}).get("areas") or [{}])[0].get("datas") or [{}])[0]
        px = _parse_price(datas.get("nv"))
        if px:
            return px, "Naver 현재가"
    except Exception:
        pass
    return None, ""


def _yahoo_spot(symbol: str) -> tuple[float | None, str]:
    import requests

    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
    for host in ("query1.finance.yahoo.com", "query2.finance.yahoo.com"):
        try:
            resp = requests.get(
                f"https://{host}/v8/finance/chart/{symbol}",
                params={"range": "1d", "interval": "1m", "includePrePost": "true"},
                headers=headers,
                timeout=12,
            )
            resp.raise_for_status()
            result = (resp.json().get("chart") or {}).get("result") or []
            if not result:
                continue
            node = result[0]
            meta = node.get("meta") or {}
            quote = ((node.get("indicators") or {}).get("quote") or [{}])[0]
            timestamps = node.get("timestamp") or []
            closes = quote.get("close") or []
            last_px = None
            last_ts = None
            for ts, close in zip(timestamps, closes):
                if close is None:
                    continue
                try:
                    last_px = float(close)
                    last_ts = int(ts)
                except (TypeError, ValueError):
                    continue
            periods = meta.get("currentTradingPeriod") or {}

            def _span(name: str) -> tuple[int | None, int | None]:
                part = periods.get(name) if isinstance(periods, dict) else None
                if not isinstance(part, dict):
                    return None, None
                try:
                    start = int(part["start"]) if part.get("start") is not None else None
                    end = int(part["end"]) if part.get("end") is not None else None
                except (TypeError, ValueError, KeyError):
                    return None, None
                return start, end

            pre_s, pre_e = _span("pre")
            post_s, post_e = _span("post")
            label = "Yahoo 현재가"
            if last_ts is not None:
                if pre_s is not None and pre_e is not None and pre_s <= last_ts < pre_e:
                    label = "Yahoo 시간외(프리)"
                elif post_s is not None and last_ts >= post_s:
                    label = "Yahoo 시간외(애프터)"
            if last_px:
                return last_px, label
            px = meta.get("regularMarketPrice")
            if px is not None:
                return float(px), "Yahoo 현재가"
        except Exception:
            continue
    return None, ""


def fetch_spot_price(market: str, ticker: str) -> tuple[float | None, str]:
    """조회 직후 현재가. 정규장 마감 후면 시간외(NXT/프리·애프터) 최근 체결가."""
    if market == "KR":
        code = _kr_code(ticker)
        px, src = _naver_spot(code)
        if px:
            return px, src
        for symbol in _kr_yahoo_symbols(code):
            px, src = _yahoo_spot(symbol)
            if px:
                return px, f"{src} ({symbol})"
        return None, ""
    if market == "US":
        symbol = ticker.strip().upper()
        return _yahoo_spot(symbol)
    if market == "CRYPTO":
        key = ticker.strip().upper().replace("-USD", "").replace("USDT", "").replace("/", "")
        info = CRYPTO.get(key)
        symbol = info["symbol"] if info else f"{key}-USD"
        return _yahoo_spot(symbol)
    return None, ""


def fetch_ohlcv(
    market: str,
    ticker: str,
    as_of: date,
    lookback_days: int = 365,
    timeframe: str = "1d",
) -> tuple[pd.DataFrame, dict]:
    """지정일(as_of)까지의 봉만 반환. 1개월 주식은 1시간봉, 2~3개월은 4시간봉, 그 이상은 일봉."""
    as_of = _to_date(as_of)
    reset_yahoo_gate()
    if timeframe not in ("1h", "4h", "1d"):
        timeframe = "1d"
    interval = "1h" if timeframe in ("1h", "4h") else "1d"
    pad = 7 if timeframe in ("1h", "4h") else 10
    start = as_of - timedelta(days=int(lookback_days * 1.2) + pad)
    meta = {
        "market": market,
        "ticker": ticker,
        "name": ticker,
        "source": "",
        "timeframe": timeframe,
    }

    if market == "KR":
        code = _kr_code(ticker)
        meta["ticker"] = code
        try:
            listing = load_kr_listing()
            row = listing.loc[listing["Code"] == code]
            if not row.empty:
                meta["name"] = str(row.iloc[0]["Name"])
        except Exception:
            pass
        df = pd.DataFrame()
        want_intra = timeframe in ("1h", "4h")
        used_intra = False
        if want_intra:
            for i, symbol in enumerate(_kr_yahoo_symbols(code)):
                if i and _yahoo_blocked():
                    break
                df = _fetch_intraday(symbol, start, as_of)
                if not df.empty:
                    meta["source"] = f"Yahoo Finance ({symbol})"
                    used_intra = True
                    break
        if df.empty:
            try:
                df = _fetch_fdr(code, start, as_of)
                if not df.empty:
                    meta["source"] = "FinanceDataReader"
            except Exception:
                df = pd.DataFrame()
        if df.empty:
            df = _fetch_naver_daily(code, start, as_of)
            if not df.empty:
                meta["source"] = "Naver"
        if df.empty:
            for i, symbol in enumerate(_kr_yahoo_symbols(code)):
                if i and _yahoo_blocked():
                    break
                df = _fetch_daily(symbol, start, as_of)
                if not df.empty:
                    meta["source"] = f"Yahoo Finance ({symbol})"
                    break
        if want_intra and not used_intra and not df.empty:
            timeframe = "1d"
            meta["note"] = "시간봉을 받지 못해 일봉으로 계산합니다."
    elif market == "US":
        symbols = _us_yahoo_symbols(ticker)
        symbol = symbols[0]
        meta["ticker"] = symbol
        meta["name"] = symbol
        df = pd.DataFrame()
        want_intra = timeframe in ("1h", "4h")
        used_intra = False
        if want_intra:
            for symbol in symbols:
                df = _fetch_intraday(symbol, start, as_of)
                if not df.empty:
                    used_intra = True
                    meta["source"] = "Yahoo Finance"
                    meta["ticker"] = symbol
                    break
        if df.empty:
            for symbol in symbols:
                df = _fetch_daily(symbol, start, as_of)
                if not df.empty:
                    meta["source"] = "Yahoo Finance"
                    meta["ticker"] = symbol
                    break
        if df.empty:
            for symbol in symbols:
                try:
                    df = _fetch_fdr(symbol, start, as_of)
                except Exception:
                    df = pd.DataFrame()
                if not df.empty:
                    meta["source"] = "FinanceDataReader"
                    meta["ticker"] = symbol
                    break
        if df.empty:
            for symbol in symbols:
                df = _fetch_stooq_daily(symbol, start, as_of)
                if not df.empty:
                    meta["source"] = "Stooq"
                    meta["ticker"] = symbol
                    break
        if want_intra and not used_intra and not df.empty:
            timeframe = "1d"
            meta["note"] = "시간봉을 받지 못해 일봉으로 계산합니다."
    elif market == "CRYPTO":
        key = ticker.strip().upper().replace("-USD", "").replace("USDT", "").replace("/", "")
        info = CRYPTO.get(key)
        symbol = info["symbol"] if info else f"{key}-USD"
        meta["ticker"] = key
        meta["name"] = info["name"] if info else key
        df = pd.DataFrame()
        want_intra = timeframe in ("1h", "4h")
        used_intra = False
        if want_intra:
            df = _fetch_intraday(symbol, start, as_of)
            if not df.empty:
                used_intra = True
        if df.empty:
            df = _fetch_daily(symbol, start, as_of)
            if not df.empty:
                meta["source"] = "Yahoo Finance"
        if df.empty:
            df = _fetch_stooq_daily(symbol, start, as_of, crypto=True)
            if not df.empty:
                meta["source"] = "Stooq"
        if df.empty:
            df = _fetch_coingecko_daily(key, start, as_of)
            if not df.empty:
                meta["source"] = "CoinGecko"
        if not meta.get("source") and not df.empty:
            meta["source"] = "Yahoo Finance"
        if want_intra and not used_intra and not df.empty:
            timeframe = "1d"
            meta["note"] = "시간봉을 받지 못해 일봉으로 계산합니다."
    else:
        raise ValueError(f"지원하지 않는 시장: {market}")

    if df.empty:
        return df, meta

    meta["timeframe"] = timeframe
    if timeframe == "4h":
        intra = df
        df = resample_4h(df, market)
        if df.empty:
            df = to_market_wall(intra, market)
            timeframe = "1h"
            meta["timeframe"] = "1h"
            meta["bar"] = "1시간봉"
            meta["note"] = "4시간봉 변환에 실패해 1시간봉으로 계산합니다."
        else:
            meta["bar"] = "4시간봉"
    if timeframe == "1h":
        df = to_market_wall(df, market)
        meta["bar"] = "1시간봉"
    if timeframe == "1d":
        df = df.copy()
        df.index = _index_naive_wall(df.index)
        meta["bar"] = "일봉"

    if df.empty:
        return df, meta

    cutoff = pd.Timestamp(as_of) + pd.Timedelta(days=1) - pd.Timedelta(seconds=1)
    window_start = pd.Timestamp(as_of) - pd.Timedelta(days=lookback_days)
    work = df.copy()
    work.index = _index_naive_wall(work.index)
    sliced = work.loc[(work.index >= window_start) & (work.index <= cutoff)]
    if sliced.empty:
        sliced = work.loc[work.index <= cutoff]
    return sliced, meta


def _usdkrw_from_close(px) -> float | None:
    """원/달러. KRWUSD(0.0007대)로 오면 뒤집는다."""
    try:
        val = float(px)
    except (TypeError, ValueError):
        return None
    if val <= 0:
        return None
    if val < 1:
        val = 1.0 / val
    if 500 <= val <= 3000:
        return val
    return None


def _close_on_or_before(df: pd.DataFrame, as_of: date) -> tuple[float | None, date | None]:
    if df is None or df.empty or "close" not in df.columns:
        return None, None
    work = df.copy()
    work.index = _index_naive_wall(work.index)
    cutoff = pd.Timestamp(as_of) + pd.Timedelta(days=1) - pd.Timedelta(seconds=1)
    sliced = work.loc[work.index <= cutoff]
    if sliced.empty:
        return None, None
    px = _usdkrw_from_close(sliced["close"].iloc[-1])
    if px is None:
        return None, None
    ts = sliced.index[-1]
    try:
        when = ts.date() if hasattr(ts, "date") else pd.Timestamp(ts).date()
    except Exception:
        when = as_of
    return px, when


def _naver_usdkrw(as_of: date) -> tuple[float | None, date | None, str]:
    """Naver 원/달러 일별. Cloud에서 Yahoo/FDR이 막힐 때."""
    import requests

    headers = {
        "User-Agent": "Mozilla/5.0",
        "Referer": "https://m.stock.naver.com/marketindex/exchange/FX_USDKRW",
    }
    try:
        resp = requests.get(
            "https://m.stock.naver.com/front-api/marketIndex/prices",
            params={
                "category": "exchange",
                "reutersCode": "FX_USDKRW",
                "page": 1,
                "pageSize": 40,
            },
            headers=headers,
            timeout=12,
        )
        resp.raise_for_status()
        payload = resp.json()
        rows = None
        if isinstance(payload, dict):
            result = payload.get("result")
            if isinstance(result, list):
                rows = result
            elif isinstance(result, dict):
                rows = (
                    result.get("prices")
                    or result.get("data")
                    or result.get("priceInfos")
                    or result.get("list")
                )
            if rows is None:
                rows = payload.get("prices") or payload.get("data")
        elif isinstance(payload, list):
            rows = payload
        if not isinstance(rows, list):
            return None, None, ""
        best_px = None
        best_day = None
        for row in rows:
            if not isinstance(row, dict):
                continue
            raw_day = (
                row.get("localTradedAt")
                or row.get("tradeDate")
                or row.get("date")
                or row.get("bizdate")
                or ""
            )
            try:
                day = pd.Timestamp(str(raw_day)[:10]).date()
            except Exception:
                continue
            if day > as_of:
                continue
            px = _usdkrw_from_close(
                row.get("closePrice")
                or row.get("nv")
                or row.get("close")
                or row.get("value")
            )
            if not px:
                continue
            if best_day is None or day > best_day:
                best_px = px
                best_day = day
        if best_px:
            return best_px, best_day, "Naver FX_USDKRW"
    except Exception:
        pass
    return None, None, ""


def fetch_usdkrw(as_of: date) -> tuple[float | None, str, date | None]:
    """조회일(as_of) 기준 원/달러 종가. 휴일이면 직전 거래일."""
    as_of = _to_date(as_of)
    start = as_of - timedelta(days=21)
    try:
        df = _fetch_fdr("USD/KRW", start, as_of)
        px, when = _close_on_or_before(df, as_of)
        if px:
            return px, "FinanceDataReader USD/KRW", when
    except Exception:
        pass
    for symbol, src in (("USDKRW=X", "Yahoo USDKRW=X"), ("KRW=X", "Yahoo KRW=X")):
        try:
            df = _fetch_daily(symbol, start, as_of)
            px, when = _close_on_or_before(df, as_of)
            if px:
                return px, src, when
        except Exception:
            continue
    if as_of >= market_today("KR"):
        spot, src = _yahoo_spot("USDKRW=X")
        px = _usdkrw_from_close(spot)
        if px:
            return px, src or "Yahoo 현재가", as_of
    px, when, src = _naver_usdkrw(as_of)
    if px:
        return px, src, when
    return None, "", None


def fetch_usdkrw_history(start: date, end: date) -> pd.Series:
    """기간 원/달러 종가. 인덱스는 날짜."""
    start = _to_date(start)
    end = _to_date(end)
    pad = start - timedelta(days=14)
    df = pd.DataFrame()
    try:
        df = _fetch_fdr("USD/KRW", pad, end)
    except Exception:
        df = pd.DataFrame()
    if df is None or df.empty:
        for symbol in ("USDKRW=X", "KRW=X"):
            try:
                df = _fetch_daily(symbol, pad, end)
            except Exception:
                df = pd.DataFrame()
            if df is not None and not df.empty:
                break
    if df is None or df.empty or "close" not in getattr(df, "columns", []):
        return pd.Series(dtype=float)
    work = df.copy()
    work.index = _index_naive_wall(work.index)
    closes = pd.to_numeric(work["close"], errors="coerce").dropna()
    out = {}
    for ts, px in closes.items():
        val = _usdkrw_from_close(px)
        if not val:
            continue
        try:
            day = ts.date() if hasattr(ts, "date") else pd.Timestamp(ts).date()
        except Exception:
            continue
        if day > end:
            continue
        out[day] = val
    if not out:
        return pd.Series(dtype=float)
    ser = pd.Series(out).sort_index()
    return ser

