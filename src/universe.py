"""종목 유니버스: 한국/미국 주식 + 주요 코인."""

from __future__ import annotations

CRYPTO = {
    "BTC": {"symbol": "BTC-USD", "name": "비트코인", "name_en": "Bitcoin"},
    "ETH": {"symbol": "ETH-USD", "name": "이더리움", "name_en": "Ethereum"},
    "SOL": {"symbol": "SOL-USD", "name": "솔라나", "name_en": "Solana"},
    "XRP": {"symbol": "XRP-USD", "name": "엑스알피", "name_en": "XRP"},
    "ONDO": {"symbol": "ONDO-USD", "name": "온도", "name_en": "Ondo"},
    "BNB": {"symbol": "BNB-USD", "name": "바이낸스코인", "name_en": "BNB"},
    "DOGE": {"symbol": "DOGE-USD", "name": "도지코인", "name_en": "Dogecoin"},
}


def crypto_key(ticker: str | None) -> str:
    return (
        str(ticker or "")
        .strip()
        .upper()
        .replace("-USD", "")
        .replace("USDT", "")
        .replace("/", "")
    )


def is_crypto(market: str | None, ticker: str | None = None) -> bool:
    if str(market or "").upper() == "CRYPTO":
        return True
    return crypto_key(ticker) in CRYPTO

KR_PRESETS = [
    ("005930", "삼성전자"),
    ("000660", "SK하이닉스"),
    ("035420", "NAVER"),
    ("035720", "카카오"),
    ("005380", "현대차"),
    ("051910", "LG화학"),
    ("006400", "삼성SDI"),
    ("068270", "셀트리온"),
    ("105560", "KB금융"),
    ("055550", "신한지주"),
    ("012450", "한화에어로스페이스"),
    ("373220", "LG에너지솔루션"),
]

US_PRESETS = [
    ("AAPL", "Apple"),
    ("MSFT", "Microsoft"),
    ("NVDA", "NVIDIA"),
    ("TSLA", "Tesla"),
    ("AMZN", "Amazon"),
    ("GOOGL", "Alphabet"),
    ("META", "Meta"),
    ("AMD", "AMD"),
]

LOOKBACK_OPTIONS = {
    "1개월": {"days": 30, "timeframe": "1h"},
    "2개월": {"days": 60, "timeframe": "4h"},
    "3개월": {"days": 90, "timeframe": "4h"},
    "6개월": {"days": 180, "timeframe": "1d"},
    "1년": {"days": 365, "timeframe": "1d"},
}

# 코인은 24시간이라 짧은 조회에서 봉을 더 굵게 쓴다.
CRYPTO_LOOKBACK_TIMEFRAMES = {
    "1개월": "12h",
    "2개월": "1d",
    "3개월": "1d",
}

BAR_NAMES = {
    "1h": "1시간봉",
    "4h": "4시간봉",
    "12h": "12시간봉",
    "1d": "일봉",
}

INTRA_TIMEFRAMES = frozenset({"1h", "4h", "12h"})


def lookback_bar_caption() -> str:
    return (
        "주식은 1개월 1시간봉, 2·3개월 4시간봉, 6개월·1년 일봉입니다. "
        "코인은 1개월 12시간봉, 2·3개월·6개월·1년은 일봉입니다."
    )


def resolve_lookback(label, market=None) -> dict:
    """조회 기간 설정을 항상 {days, timeframe} 로 맞춘다. 코인 1개월은 12시간봉, 2개월부터는 일봉."""
    spec = LOOKBACK_OPTIONS.get(label)
    if isinstance(spec, dict) and "days" in spec and "timeframe" in spec:
        out = {"days": int(spec["days"]), "timeframe": str(spec["timeframe"])}
    elif isinstance(spec, int):
        if spec <= 30:
            tf = "1h"
        elif spec <= 90:
            tf = "4h"
        else:
            tf = "1d"
        out = {"days": int(spec), "timeframe": tf}
    else:
        fallback = LOOKBACK_OPTIONS["6개월"]
        out = {"days": int(fallback["days"]), "timeframe": str(fallback["timeframe"])}
    if str(market or "").upper() == "CRYPTO":
        override = CRYPTO_LOOKBACK_TIMEFRAMES.get(label)
        if override:
            out["timeframe"] = override
        elif isinstance(spec, int):
            if spec <= 30:
                out["timeframe"] = "12h"
            elif spec <= 90:
                out["timeframe"] = "1d"
    return out

MARKETS = {
    "한국 주식": "KR",
    "미국 주식": "US",
    "코인": "CRYPTO",
}


def crypto_choices() -> list[tuple[str, str]]:
    return [(k, f"{v['name']} ({k})") for k, v in CRYPTO.items()]
