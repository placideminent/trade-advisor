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
    "6개월": 180,
    "1년": 365,
    "2년": 730,
    "5년": 1825,
}

MARKETS = {
    "한국 주식": "KR",
    "미국 주식": "US",
    "코인": "CRYPTO",
}


def crypto_choices() -> list[tuple[str, str]]:
    return [(k, f"{v['name']} ({k})") for k, v in CRYPTO.items()]
