"""규칙 기반 매수/매도/홀딩. 지정일 현재가와 지지·저항·매물대·추세만 사용."""

from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

from .universe import is_crypto

SIGNAL_RULE_VERSION = 125
# 이 숫자를 올리면 배점 조절창 위젯 키·제목도 같이 바뀌어 예전 설명이 남지 않는다.
# 중립 기준점. 이보다 높으면 매수, 낮으면 매도.
SCORE_BASE = 10
# 합산 %는 0점 이하=0%, 10점=50%, 20점 이상=100%.
SCORE_LO = 0
SCORE_HI = 20

DEFAULT_WEIGHTS = {
    "base": 10,
    "trend": 0,
    "trend_lookback_1m_up": -1,
    "trend_lookback_1m_down": 1,
    "trend_lookback_short_up": -1,
    "trend_lookback_short_down": 1,
    "trend_1m": 0,
    "swing_low_near": 0,
    "swing_high_near": 0,
    "down_line_near": -1,
    "price_down_line_near": -1,
    "trendline_dir_down": 0,
    "trendline_dir_down_break": 0,
    "trendline_dir_down_upnear": 0,
    "trendline_1m_up": 0,
    "trendline_1m_up_both_up": 0,
    "trendline_up_1m_down": 0,
    "up_line_near": 1,
    "short_up_line_near": 1,
    "up_line_break": 0,
    "support_near": 1,
    "support_break": 0,
    "resist_near": -1,
    "poc": 1,
    "val": 1,
    "vah": -1,
    "rsi": 2,
    "rsi_high": 0,
    "drop10_15": 1,
    "ma20": 1,
    "ma60_near": 1,
    "ma200_near": 1,
    "ma_cross_20_60": -1,
    "bar_spike_20": -1,
    "ath_clear": 0,
    "ath_resist": 0,
    "ath_reenter": 0,
    "ath_fail": 0,
    "chg1_50": 0,
    "chg1_down1": 0,
    "chg1_down10": 1,
    "chg1_down20": 2,
    "chg1_down30": 3,
    "chg1_down40": 0,
    "chg1_down50": 0,
    "chg6_50": 0,
    "chg6_200": 0,
    "chg6_300": -2,
    "chg6_500": 0,
    "chg6_600": -4,
    "chg6_800": -6,
    "rr_penalty": 0,
    "option_wall": 1,
}

DEFAULT_CUTS_STOCK = {
    "buy_weak": 75,
    "buy_mid": 80,
    "buy_strong": 85,
    "sell_weak": 30,
    "sell_mid": 25,
    "sell_strong": 20,
}
DEFAULT_CUTS_CRYPTO = {
    "buy_weak": 75,
    "buy_mid": 80,
    "buy_strong": 85,
    "sell_weak": 35,
    "sell_mid": 30,
    "sell_strong": 25,
}
DEFAULT_CUTS = dict(DEFAULT_CUTS_STOCK)
LEGACY_DEFAULT_CUTS = {
    "buy_weak": 65,
    "buy_mid": 70,
    "buy_strong": 75,
    "sell_weak": 27,
    "sell_mid": 24,
    "sell_strong": 21,
}
PREV_DEFAULT_CUTS = {
    "buy_weak": 70,
    "buy_mid": 75,
    "buy_strong": 79,
    "sell_weak": 40,
    "sell_mid": 35,
    "sell_strong": 30,
}

WEIGHT_FIELDS = [
    ("base", "기본", "시작할 때 항상 줌"),
    ("trend_lookback_short_up", "조회기간 추세", "(1개월,2개월 조회에만 적용) 조회기간 상승"),
    ("trend_lookback_short_down", "조회기간 추세", "(1개월,2개월 조회에만 적용) 조회기간 하락"),
    ("trend_lookback_1m_up", "조회기간 추세", "(3개월,6개월,1년 조회에만 적용) 조회기간 상승 · 1개월(1시간봉) 상승 / (3개월,6개월,1년 조회에만 적용) 조회기간 횡보 · 1개월(1시간봉) 상승"),
    ("trend_lookback_1m_down", "조회기간 추세", "(3개월,6개월,1년 조회에만 적용) 조회기간 하락 · 1개월(1시간봉) 하락 / (3개월,6개월,1년 조회에만 적용) 조회기간 횡보 · 1개월(1시간봉) 하락"),
    ("down_line_near", "단기 하락 추세선 근접", "단기 하락 추세선에 근접 했을 때, 돌파하면 무효. 장기 하락 추세선 근접 감점과 겹치면 무효"),
    ("price_down_line_near", "장기 하락 추세선 근접", "현재가가 장기 하락 추세선에 근접 했을 때, 돌파하면 무효"),
    ("up_line_near", "장기 상승 추세선 근접", "장기 상승 추세선 근접 했을 때, 이탈하면 무효"),
    ("short_up_line_near", "단기 상승 추세선 근접", "단기 상승 추세선 근접 했을 때, 이탈하면 무효. 장기 상승선 근접 가점과 겹치면 무효"),
    ("support_near", "지지 근접", "지지선 근접이고 강도 4 이상일 때"),
    ("resist_near", "저항 근접", "저항선 바로 옆이고, 강도 4 이상일 때"),
    ("poc", "최대 매물 (POC)", "현재가가 거래가 가장 많았던 가격 근접 했을 때, 이탈 시 무효"),
    ("val", "밸류 하단 (VAL)", "현재가가 싼 구간 아래일때 (추세 무관)"),
    ("vah", "밸류 상단 (VAH)", "현재가가 비싼 구간 위일때 (거리 무관)"),
    ("rsi", "RSI 35 이하", "RSI 35 이하 (너무 많이 떨어짐)일 때 가점"),
    ("rsi_high", "RSI 70 이상", "RSI 70 이상 (너무 많이 오름)일 때 이 크기만큼 감점. 기본 0 (v115부터 끔)"),
    ("drop10_15", "10일 고점 대비 하락", "(주식만, 코인 제외) 종가가 최근 10일 최고 종가보다 15% 이상 낮을 때 가점"),
    ("ma20", "20일선", "현재가가 20일선 근처일때, 20일선 완전 이탈시 무효"),
    ("ma60_near", "60일선", "현재가가 60일선 근처일때, 60일선 완전 이탈시 무효"),
    ("ma200_near", "180일선", "현재가가 장기 이평 근처일 때. 6개월 조회는 180일선, 1년 조회는 300일선, 완전이탈시 무효"),
    ("ma_cross_20_60", "20일선 60일선 교차", "20일선 방향이 하방으로 떨어지면서 60일선 아래로 떨어지기 시작할때, 떨어지고 4봉이상 지나면 무효"),
    ("chg1_down10", "1개월 하락률", "한 달동안 15% 이상 25%미만 하락 했을 때, 장기 상승 추세선 이탈시 무효"),
    ("chg1_down20", "1개월 하락률", "한 달 동안 25% 이상 35% 미만 떨어짐, 장기 상승 추세선 이탈시 무효"),
    ("chg1_down30", "1개월 하락률", "한 달 동안 35% 이상 떨어짐, 장기 상승 추세선 이탈시 무효"),
    ("chg6_800", "6개월 상승률", "6개월 동안 800% 이상 오름, 횡보 추세나 하락 추세시 무효, 현재가가 볼린저 밴드(20일, 2σ) 안이면 감점 절반 (v115부터 감점 2배, v122부터 장기 하락선 돌파 무효 제거)"),
    ("chg6_600", "6개월 상승률", "6개월 동안 600% 이상 오름, 횡보 추세나 하락 추세시 무효, 현재가가 볼린저 밴드(20일, 2σ) 안이면 감점 절반 (v115부터 감점 2배, v122부터 장기 하락선 돌파 무효 제거)"),
    ("chg6_300", "6개월 상승률", "6개월 동안 300% 이상 600% 미만 오름, 횡보 추세나 하락 추세시 무효, 현재가가 볼린저 밴드(20일, 2σ) 안이면 감점 절반 (v115부터 감점 2배, v122부터 장기 하락선 돌파 무효 제거)"),
    ("bar_spike_20", "단기 급상승", "1개 봉만에 20% 이상 상승했을 시"),
    ("option_wall", "옵션", "기존 결과가 홀딩이면 옵션은 보지 않음 / 기존이 매도인데, 만기가 14일 안이고, 위쪽에 콜 벽이 두껍고 아래 풋 벽이 얇음 / 반대로 아래 풋 벽이 두껍고 위 콜 벽이 얇음 / 기존이 매수인데, 아래 풋 벽이 얇고 위 콜 벽이 두꺼움"),
]

# 점수에서도, 배점 창에서도 쓰지 않음.
DROPPED_WEIGHT_KEYS = frozenset({
    "trend_1m",
    "swing_low_near",
    "swing_high_near",
    "trendline_up_1m_down",
    "trendline_dir_down_break",
    "up_line_break",
    "chg1_down1",
    "chg1_down40",
    "chg1_down50",
    "chg6_50",
    "chg6_200",
    "chg6_500",
    "rr_penalty",
    "trend",
    "ath_fail",
    "chg1_50",
    "trendline_dir_down",
    "trendline_dir_down_upnear",
    "trendline_1m_up",
    "trendline_1m_up_both_up",
    "support_break",
    "ath_clear",
    "ath_resist",
    "ath_reenter",
})
_HIDDEN_WEIGHT_LABELS = (
    "손익비",
    "손익비 부족",
    "1개월 상승률",
    "신고가 돌파 실패",
    "신고가 달성",
    "신고가 저항",
    "신고가 이탈",
    "1개월 상승선 근접",
    "스윙 저점 근접",
    "스윙 고점 근접",
    "양쪽 추세선 상승·단기하락",
    "둘 다 하락·하락선 돌파",
    "둘 다 상승·1개월 상승선 상방",
    "상승 추세선 이탈",
    "추세선 방향",
    "하방향 1개월 추세선 고려",
    "상방향 1개월 추세선 고려",
    "지지 이탈",
    "MA20 아래",
    "1개월 상승 30%",
    "1개월 상승 50%",
    "1개월 하락 1%",
    "1개월 하락 40%",
    "1개월 하락 50%",
    "6개월 상승 50%",
    "6개월 상승 200%",
    "6개월 상승 500%",
)

RETURN_TIER_DEFAULTS = {
    "base": 10,
    "chg1_50": 0,
    "chg1_down1": 0,
    "chg1_down10": 1,
    "chg1_down20": 2,
    "chg1_down30": 3,
    "chg1_down40": 0,
    "chg1_down50": 0,
    "chg6_50": 0,
    "chg6_200": 0,
    "chg6_300": -2,
    "chg6_500": 0,
    "chg6_600": -4,
    "chg6_800": -6,
    "swing_low_near": 0,
    "swing_high_near": 0,
    "trendline_up_1m_down": 0,
    "trendline_dir_down": 0,
    "trendline_dir_down_break": 0,
    "trendline_dir_down_upnear": 0,
    "trendline_1m_up": 0,
    "trendline_1m_up_both_up": 0,
    "support_break": 0,
    "bar_spike_20": -1,
    "up_line_near": 1,
    "short_up_line_near": 1,
    "up_line_break": 0,
    "ma20": 1,
    "ma60_near": 1,
    "ma_cross_20_60": -1,
}


def migrate_return_tiers(weights: dict) -> dict:
    """1개월·6개월 수익률 구간 배점을 새 기본값으로 맞춘다."""
    for key, val in RETURN_TIER_DEFAULTS.items():
        weights[key] = int(val)
    return weights


_V114_WEIGHT_DEFAULTS = {"rsi": 1, "chg6_300": -1, "chg6_600": -2, "chg6_800": -3}


def migrate_weights_v115(weights: dict, src_keys=None) -> dict:
    """v114 기본 배점(RSI ±1, 6개월 상승률 -1/-2/-3)을 v115 기본으로 바꾼다. 직접 바꾼 값은 유지.

    v114까지 RSI 한 칸이 35 이하 가점·70 이상 감점을 같이 정했다. 기본(1)이면 35 이하 +2, 70 이상 0.
    직접 바꾼 값이면 35 이하는 그대로, 70 이상 감점도 같은 크기로 남긴다. rsi_high는 감점 크기(양수)로 저장한다.
    """
    try:
        old_rsi = int(weights.get("rsi", _V114_WEIGHT_DEFAULTS["rsi"]))
    except (TypeError, ValueError):
        old_rsi = _V114_WEIGHT_DEFAULTS["rsi"]
    keys = set(src_keys) if src_keys is not None else set(weights)
    if "rsi_high" not in keys:
        if old_rsi == _V114_WEIGHT_DEFAULTS["rsi"]:
            weights["rsi"] = int(DEFAULT_WEIGHTS["rsi"])
            weights["rsi_high"] = int(DEFAULT_WEIGHTS["rsi_high"])
        else:
            weights["rsi_high"] = abs(old_rsi)
    for key in ("chg6_300", "chg6_600", "chg6_800"):
        try:
            if int(weights.get(key, _V114_WEIGHT_DEFAULTS[key])) == _V114_WEIGHT_DEFAULTS[key]:
                weights[key] = int(DEFAULT_WEIGHTS[key])
        except (TypeError, ValueError):
            weights[key] = int(DEFAULT_WEIGHTS[key])
    weights.setdefault("drop10_15", int(DEFAULT_WEIGHTS["drop10_15"]))
    return weights


def migrate_base_v103(weights: dict) -> dict:
    """v102에서 잘못 넣은 기본 12점을 10점(50%)으로 고친다."""
    try:
        if int(weights.get("base", 10)) == 12:
            weights["base"] = 10
    except (TypeError, ValueError):
        weights["base"] = 10
    return weights
WEIGHT_FIELDS = [row for row in WEIGHT_FIELDS if row[0] not in DROPPED_WEIGHT_KEYS]


def visible_weight_fields() -> list[tuple[str, str, str]]:
    """배점 창에 그릴 항목만. 삭제한 규칙 이름·키는 여기서 한 번 더 걸러 낸다."""
    rows: list[tuple[str, str, str]] = []
    for key, label, hint in WEIGHT_FIELDS:
        if key in DROPPED_WEIGHT_KEYS:
            continue
        if any(snip in label for snip in _HIDDEN_WEIGHT_LABELS):
            continue
        rows.append((key, label, hint))
    return rows


def weight_widget_key(field: str) -> str:
    """배점 number_input 키. 규칙 버전이 바뀌면 Streamlit이 예전 help를 재사용하지 않는다."""
    return f"wv{int(SIGNAL_RULE_VERSION)}_{field}"


def weight_widget_prefixes() -> tuple[str, ...]:
    """현재·예전 배점 위젯 접두사. 값 이전과 삭제 항목 정리에 쓴다."""
    ver = int(SIGNAL_RULE_VERSION)
    out: list[str] = []
    seen: set[str] = set()
    for v in range(ver, 78, -1):
        for prefix in (f"wv{v}_", f"w{v}_"):
            if prefix not in seen:
                seen.add(prefix)
                out.append(prefix)
    if "w_" not in seen:
        out.append("w_")
    return tuple(out)


def rule_panel_title() -> str:
    return f"평가 배점·기준 · 규칙 v{SIGNAL_RULE_VERSION}"


def rule_weight_ui_caption() -> str:
    return f"규칙 v{SIGNAL_RULE_VERSION}."


def score_scale_caption() -> str:
    return (
        f"합산 %는 {SCORE_LO}점=0%, {SCORE_BASE}점(기본)=50%, {SCORE_HI}점=100%입니다. "
        "항목 점수와 매수/매도 컷을 바꿀 수 있습니다."
    )

_OLD_SELL_TRIOS = (
    (40, 35, 30),
    (35, 30, 25),
    (27, 24, 21),
)


def migrate_sell_cuts(cuts: dict) -> dict:
    """예전 통합 매도 컷을 코인 기본(45/40/30)으로 올린다."""
    try:
        trio = (
            int(cuts.get("sell_weak") or 0),
            int(cuts.get("sell_mid") or 0),
            int(cuts.get("sell_strong") or 0),
        )
    except (TypeError, ValueError):
        return cuts
    if trio in _OLD_SELL_TRIOS:
        cuts["sell_weak"] = int(DEFAULT_CUTS_CRYPTO["sell_weak"])
        cuts["sell_mid"] = int(DEFAULT_CUTS_CRYPTO["sell_mid"])
        cuts["sell_strong"] = int(DEFAULT_CUTS_CRYPTO["sell_strong"])
    return cuts


def migrate_stock_buy_cuts(cuts: dict) -> dict:
    """예전 주식 매수 컷을 새 기본으로 올린다. 직접 바꾼 값은 유지."""
    try:
        trio = (
            int(cuts.get("buy_weak") or 0),
            int(cuts.get("buy_mid") or 0),
            int(cuts.get("buy_strong") or 0),
        )
    except (TypeError, ValueError):
        return cuts
    if trio in ((65, 70, 75), (70, 75, 79)):
        cuts["buy_weak"] = int(DEFAULT_CUTS_STOCK["buy_weak"])
        cuts["buy_mid"] = int(DEFAULT_CUTS_STOCK["buy_mid"])
        cuts["buy_strong"] = int(DEFAULT_CUTS_STOCK["buy_strong"])
    return cuts


def _cuts_match(src: dict, ref: dict) -> bool:
    try:
        return all(int(src.get(k, 0)) == int(v) for k, v in ref.items())
    except (TypeError, ValueError):
        return False


_PREV_CUTS_STOCK_V84 = {
    "buy_weak": 70,
    "buy_mid": 75,
    "buy_strong": 79,
    "sell_weak": 35,
    "sell_mid": 30,
    "sell_strong": 25,
}
_PREV_CUTS_CRYPTO_V84 = {
    "buy_weak": 70,
    "buy_mid": 75,
    "buy_strong": 79,
    "sell_weak": 45,
    "sell_mid": 40,
    "sell_strong": 30,
}


def migrate_cuts_v85(stock: dict, crypto: dict) -> tuple[dict, dict]:
    """직전 기본 컷이면 엑셀 v85 컷으로 올린다."""
    if _cuts_match(stock, _PREV_CUTS_STOCK_V84) or _cuts_match(stock, {
        "buy_weak": 65, "buy_mid": 70, "buy_strong": 75,
        "sell_weak": 35, "sell_mid": 30, "sell_strong": 25,
    }):
        stock = dict(DEFAULT_CUTS_STOCK)
    if _cuts_match(crypto, _PREV_CUTS_CRYPTO_V84):
        crypto = dict(DEFAULT_CUTS_CRYPTO)
    return stock, crypto


_PREV_CUTS_STOCK_V100 = {
    "buy_weak": 60,
    "buy_mid": 67,
    "buy_strong": 73,
    "sell_weak": 33,
    "sell_mid": 27,
    "sell_strong": 20,
}
_PREV_CUTS_CRYPTO_V100 = {
    "buy_weak": 67,
    "buy_mid": 73,
    "buy_strong": 80,
    "sell_weak": 37,
    "sell_mid": 33,
    "sell_strong": 27,
}


def migrate_cuts_v102(stock: dict, crypto: dict) -> tuple[dict, dict]:
    """v100 기본 컷이면 엑셀 v101 컷(주식·코인 동일)으로 올린다."""
    stock, crypto = migrate_cuts_v85(stock, crypto)
    if _cuts_match(stock, _PREV_CUTS_STOCK_V100):
        stock = dict(DEFAULT_CUTS_STOCK)
    if _cuts_match(crypto, _PREV_CUTS_CRYPTO_V100):
        crypto = dict(DEFAULT_CUTS_CRYPTO)
    return stock, crypto


_PREV_CUTS_V106 = {
    "buy_weak": 55,
    "buy_mid": 65,
    "buy_strong": 73,
    "sell_weak": 35,
    "sell_mid": 25,
    "sell_strong": 20,
}


def migrate_cuts_v107(stock: dict, crypto: dict) -> tuple[dict, dict]:
    """v106 기본 컷이면 주식 65/70/75·코인 70/75/80, 매도 35/30/25로 올린다."""
    stock, crypto = migrate_cuts_v102(stock, crypto)
    if _cuts_match(stock, _PREV_CUTS_V106):
        stock = dict(DEFAULT_CUTS_STOCK)
    if _cuts_match(crypto, _PREV_CUTS_V106):
        crypto = dict(DEFAULT_CUTS_CRYPTO)
    crypto = migrate_crypto_buy_cuts(crypto)
    stock = migrate_stock_sell_cuts(stock)
    return stock, crypto


def migrate_stock_sell_cuts(cuts: dict) -> dict:
    """예전 주식 기본 매도 컷(35/30/25)을 새 기본(30/25/20)으로 내린다. 직접 바꾼 값은 유지."""
    try:
        trio = (
            int(cuts.get("sell_weak") or 0),
            int(cuts.get("sell_mid") or 0),
            int(cuts.get("sell_strong") or 0),
        )
    except (TypeError, ValueError):
        return cuts
    if trio == (35, 30, 25):
        cuts = dict(cuts)
        for k in ("sell_weak", "sell_mid", "sell_strong"):
            cuts[k] = int(DEFAULT_CUTS_STOCK[k])
    return cuts


def migrate_crypto_buy_cuts(cuts: dict) -> dict:
    """예전 코인 기본 매수 컷(70/75/80)을 새 기본(75/80/85)으로 올린다. 직접 바꾼 값은 유지."""
    try:
        trio = (
            int(cuts.get("buy_weak") or 0),
            int(cuts.get("buy_mid") or 0),
            int(cuts.get("buy_strong") or 0),
        )
    except (TypeError, ValueError):
        return cuts
    if trio == (70, 75, 80):
        cuts = dict(cuts)
        for k in ("buy_weak", "buy_mid", "buy_strong"):
            cuts[k] = int(DEFAULT_CUTS_CRYPTO[k])
    return cuts


def _copy_cuts(src: dict | None, defaults: dict) -> dict:
    data = dict(defaults)
    if not isinstance(src, dict):
        return data
    for key, default in defaults.items():
        if key not in src:
            continue
        try:
            data[key] = int(src[key])
        except (TypeError, ValueError):
            data[key] = default
    return data


def cuts_for_market(rule: dict | None, market: str | None) -> dict:
    cfg = merge_rule(rule)
    if str(market or "").upper() == "CRYPTO":
        return dict(cfg["cuts_crypto"])
    return dict(cfg["cuts"])


CUT_FIELDS = [
    ("buy_weak", "약한 매수", "% 이상"),
    ("buy_mid", "매수", "% 이상"),
    ("buy_strong", "강한 매수", "% 이상"),
    ("sell_weak", "약한 매도", "% 이하"),
    ("sell_mid", "매도", "% 이하"),
    ("sell_strong", "강한 매도", "% 이하"),
]


def merge_rule(rule: dict | None) -> dict:
    weights = dict(DEFAULT_WEIGHTS)
    cuts = dict(DEFAULT_CUTS_STOCK)
    cuts_crypto = dict(DEFAULT_CUTS_CRYPTO)
    if isinstance(rule, dict):
        weights.update(rule.get("weights") or {})
        if rule.get("cuts_crypto"):
            cuts = _copy_cuts(rule.get("cuts"), DEFAULT_CUTS_STOCK)
            cuts_crypto = _copy_cuts(rule.get("cuts_crypto"), DEFAULT_CUTS_CRYPTO)
        elif rule.get("cuts"):
            cuts_crypto = _copy_cuts(rule.get("cuts"), DEFAULT_CUTS_CRYPTO)
            migrate_sell_cuts(cuts_crypto)
    for key in DROPPED_WEIGHT_KEYS:
        weights[key] = 0
    return {"weights": weights, "cuts": cuts, "cuts_crypto": cuts_crypto}

from .analysis import Analysis, Level, classify_trend, find_swings, _rising_up_line


@dataclass
class Signal:
    action: str
    confidence: int
    score: int
    reasons: list[str] = field(default_factory=list)
    stop: float | None = None
    target: float | None = None
    reward_risk: float | None = None
    nearest_support: Level | None = None
    nearest_resistance: Level | None = None
    summary: str = ""
    score_pct: int = 0
    score_min: int = 0
    score_max: int = 0
    score_rows: list[dict] = field(default_factory=list)
    action_base: str = ""
    score_pct_base: int = 0
    option_applied: bool = False
    dip_buy: bool = False
    market_dip: bool = False
    hammer_buy: bool = False
    nasdaq_drop_buy: bool = False


def _inside_bollinger(an, price: float) -> bool:
    """종가 기준 20일·2σ 볼린저 밴드 안(하단 < 현재가 < 상단)인지."""
    try:
        df = an.df
        col = "close" if "close" in df else "Close"
        c = df[col]
        if getattr(an, "as_of", None) is not None:
            c = c[c.index <= an.as_of]
        bb = [float(x) for x in c.tail(BB_DAYS) if x == x and x > 0]
        if len(bb) < BB_DAYS:
            return False
        mean = sum(bb) / len(bb)
        sd = (sum((x - mean) ** 2 for x in bb) / (len(bb) - 1)) ** 0.5
        return mean - BB_K * sd < float(price) < mean + BB_K * sd
    except Exception:
        return False


def score_bounds(_has_6m: bool | None = None) -> tuple[int, int]:
    return SCORE_LO, SCORE_HI


def score_to_pct(score: int, _has_6m: bool | None = None) -> int:
    s = max(float(SCORE_LO), min(float(SCORE_HI), float(score)))
    if SCORE_BASE <= SCORE_LO or SCORE_HI <= SCORE_BASE:
        if SCORE_HI <= SCORE_LO:
            return 50
        pct = (s - SCORE_LO) / (SCORE_HI - SCORE_LO) * 100
    elif s <= SCORE_BASE:
        pct = (s - SCORE_LO) / (SCORE_BASE - SCORE_LO) * 50.0
    else:
        pct = 50.0 + (s - SCORE_BASE) / (SCORE_HI - SCORE_BASE) * 50.0
    return int(round(max(0.0, min(100.0, pct))))


def _n_day_change(an: Analysis, price: float, days: int) -> float | None:
    df = an.df
    if df is None or df.empty:
        return None
    start = pd.Timestamp(an.as_of) - pd.Timedelta(days=days)
    past = df.loc[df.index <= start]
    base = float(past["close"].iloc[-1]) if not past.empty else float(df["close"].iloc[0])
    if base <= 0:
        return None
    return price / base - 1.0


def _one_month_change(an: Analysis, price: float) -> float | None:
    return _n_day_change(an, price, 30)


def _one_bar_return(an: Analysis, price: float) -> float | None:
    df = an.df
    if df is None or getattr(df, "empty", True) or len(df) < 2 or "close" not in df.columns:
        return None
    prev = float(pd.to_numeric(df["close"], errors="coerce").iloc[-2])
    if not (prev > 0):
        return None
    return float(price) / prev - 1.0


def _ath_since(df, last_price: float | None = None) -> tuple[int, float] | None:
    """조회 기간 최고가(고가)가 난 뒤 지난 봉 수와 그 가격. 현재 봉이 신고가면 0."""
    if df is None or getattr(df, "empty", True) or "high" not in getattr(df, "columns", []):
        return None
    h = pd.to_numeric(df["high"], errors="coerce")
    if h.isna().all():
        return None
    ath = float(h.max())
    n = len(h)
    if last_price is not None:
        try:
            px = float(last_price)
        except (TypeError, ValueError):
            px = None
        else:
            if px > ath:
                return 0, px
    eps = max(1e-9, abs(ath) * 1e-8)
    pos = None
    for i in range(n - 1, -1, -1):
        v = h.iloc[i]
        if pd.notna(v) and float(v) >= ath - eps:
            pos = i
            break
    if pos is None:
        return None
    return n - 1 - int(pos), ath


def _resistance_above_ath(levels, ath: float) -> Level | None:
    """신고가보다 뚜렷이 위에 있는 저항. 신고가 자체는 위가 아님."""
    cutoff = float(ath) + max(abs(float(ath)) * 1e-4, 1e-6)
    best = None
    for lv in levels or []:
        try:
            p = float(lv.price)
        except (TypeError, ValueError, AttributeError):
            continue
        if p <= cutoff:
            continue
        if best is None or p < float(best.price):
            best = lv
    return best


def _price_was_above_line(df, line, last_price: float | None = None) -> bool:
    """조회 기간 안에 고가·현재가가 추세선 위로 올라간 적이 있는지."""
    if df is None or getattr(df, "empty", True) or line is None:
        return False
    highs = pd.to_numeric(df["high"], errors="coerce") if "high" in df.columns else None
    n = len(df)
    for i in range(n):
        y = _line_y_at(line, float(i))
        if y is None:
            continue
        if highs is not None:
            h = highs.iloc[i]
            if pd.notna(h) and float(h) > y:
                return True
        if last_price is not None and i == n - 1 and float(last_price) > y:
            return True
    return False


def _bars_since_last_above_line(df, line, last_price: float | None = None) -> int | None:
    """선 위로 올라간 마지막 봉이 끝에서 몇 봉 전인지. 없으면 None."""
    if df is None or getattr(df, "empty", True) or line is None:
        return None
    highs = pd.to_numeric(df["high"], errors="coerce") if "high" in df.columns else None
    n = len(df)
    last_i = None
    for i in range(n):
        y = _line_y_at(line, float(i))
        if y is None:
            continue
        above = False
        if highs is not None:
            h = highs.iloc[i]
            if pd.notna(h) and float(h) > y:
                above = True
        if last_price is not None and i == n - 1 and float(last_price) > y:
            above = True
        if above:
            last_i = i
    if last_i is None:
        return None
    return n - 1 - int(last_i)


def _fmt(price: float) -> str:
    if price >= 1000:
        return f"{price:,.0f}"
    if price >= 1:
        return f"{price:,.2f}"
    return f"{price:.6f}"


def period_return(df, as_of, price: float, days: int = 180) -> float | None:
    """as_of 기준 days일 전 종가 대비 현재가 수익률.

    목표일이 주말·휴장이면 그 직전(없으면 직후) 거래일 종가를 쓴다.
    """
    try:
        if df is None or getattr(df, "empty", True) or price is None or float(price) <= 0:
            return None
        if "close" not in getattr(df, "columns", []):
            return None
        closes = pd.to_numeric(df["close"], errors="coerce")
        dates = pd.to_datetime(df.index, utc=True, errors="coerce")
        dates = pd.DatetimeIndex(dates).tz_convert(None).normalize()
        as_day = pd.Timestamp(str(pd.Timestamp(as_of).date()))
        target = as_day - pd.Timedelta(days=int(days))
        ok = dates.notna() & closes.notna()
        past = ok & (dates <= target)
        if bool(past.any()):
            base = float(closes.loc[past].iloc[-1])
        else:
            base = float(closes.loc[ok].iloc[0])
        if not (base > 0):
            return None
        return float(price) / base - 1.0
    except Exception:
        try:
            base = float(df["close"].iloc[0])
            if base > 0:
                return float(price) / base - 1.0
        except Exception:
            return None
        return None


UP_LINE_BREAK_BARS = 4


def _nearest_swing_price(points, price: float) -> tuple[float, float] | None:
    """스윙 점 목록에서 현재가에 가장 가까운 (가격, 이격)."""
    best: tuple[float, float] | None = None
    for item in points or []:
        try:
            level = float(item[1] if isinstance(item, (tuple, list)) else item)
        except (TypeError, ValueError, IndexError):
            continue
        dist = abs(price - level)
        if best is None or dist < best[1]:
            best = (level, dist)
    return best


def _ma20_cross_below_ma60(df, bars: int = 4) -> bool:
    """20일선이 하방으로 60일선 아래를 막 깬 직후(bars봉 이내)."""
    if df is None or getattr(df, "empty", True) or len(df) < bars + 2:
        return False
    if "ma20" not in df.columns or "ma60" not in df.columns:
        return False
    m20 = pd.to_numeric(df["ma20"], errors="coerce")
    m60 = pd.to_numeric(df["ma60"], errors="coerce")
    last20, last60 = m20.iloc[-1], m60.iloc[-1]
    if pd.isna(last20) or pd.isna(last60) or last20 >= last60:
        return False
    prev20 = m20.iloc[-2]
    if pd.isna(prev20) or last20 >= prev20:
        return False
    for i in range(1, bars + 1):
        a, b = m20.iloc[-1 - i], m60.iloc[-1 - i]
        if pd.isna(a) or pd.isna(b):
            continue
        if a >= b:
            return True
    return False


def _up_line_from_df(df) -> tuple[float, float, float, float] | None:
    """차트와 같은 상승선. 최저 저점에서 안 깨질 때까지 당긴다."""
    if df is None or getattr(df, "empty", True) or len(df) < 9:
        return None
    _highs, lows = find_swings(df)
    if len(lows) < 2:
        return None
    return _rising_up_line(lows, len(df) - 1)


def _window_df(df, as_of, days: int):
    if df is None or getattr(df, "empty", True):
        return None
    start = pd.Timestamp(as_of) - pd.Timedelta(days=int(days))
    return df.loc[df.index >= start]


def _bars_below_line(df, line, near: float, last_price: float | None = None) -> int:
    """끝에서부터 추세선 아래로 완전히 이탈한 연속 봉 수."""
    if df is None or getattr(df, "empty", True) or line is None:
        return 0
    if "close" not in getattr(df, "columns", []):
        return 0
    closes = pd.to_numeric(df["close"], errors="coerce")
    n = len(closes)
    count = 0
    for i in range(n - 1, -1, -1):
        y = _line_y_at(line, float(i))
        if y is None:
            break
        if i == n - 1 and last_price is not None:
            px = float(last_price)
        else:
            px = closes.iloc[i]
            if pd.isna(px):
                break
            px = float(px)
        if px < y - near:
            count += 1
        else:
            break
    return count


def _line_y_at(line, x: float) -> float | None:
    x0, y0, x1, y1 = (float(v) for v in line)
    if x1 == x0:
        return None
    return y0 + (y1 - y0) / (x1 - x0) * (x - x0)


def _line_dir(line) -> str | None:
    if line is None:
        return None
    x0, y0, x1, y1 = (float(v) for v in line)
    if x1 == x0:
        return None
    dy = y1 - y0
    scale = max(abs(y0), abs(y1), 1.0)
    if abs(dy) / scale < 1e-6:
        return "flat"
    return "up" if dy > 0 else "down"


GATE_DAYS = 20
GATE_BAND = 0.05
GATE_BAND_SELL = 0.03
BB_DAYS = 20
BB_K = 2.0
_BUY_SET = ("약한 매수", "매수", "강한 매수")
_SELL_SET = ("약한 매도", "매도", "강한 매도")


def _daily_closes(df, price: float) -> list[float]:
    """봉을 날짜별 마지막 종가로 묶고, 마지막 날은 현재가로 바꾼다. 일봉이면 종가 그대로."""
    out: list[float] = []
    try:
        if df is not None and not getattr(df, "empty", True) and "close" in df:
            close = pd.to_numeric(df["close"], errors="coerce").dropna()
            if len(close):
                idx = pd.DatetimeIndex(close.index)
                days = idx.normalize()
                daily = close.groupby(days).last()
                out = [float(v) for v in daily.iloc[:-1].tolist()]
    except Exception:
        out = []
    try:
        px = float(price)
    except (TypeError, ValueError):
        px = 0.0
    if px > 0:
        out.append(px)
    return out


UPLINE_BB_SKIP = 0.05


def _price_gate(action: str, price: float, closes_d: list[float], skip_bb: bool = False) -> tuple[str, str]:
    """매수는 20일 최저 종가 +5% 안이고 볼린저 하단(20일, 2σ) 아래일 때만, 매도는 20일 최고 종가 -3% 안일 때만 낸다."""
    if action not in _BUY_SET and action not in _SELL_SET:
        return action, ""
    window = [c for c in closes_d[-GATE_DAYS:] if c and c > 0]
    if not window or not price or price <= 0:
        return action, ""
    if action in _BUY_SET:
        low = min(window)
        if price > low * (1.0 + GATE_BAND) + 1e-12:
            gap = (price / low - 1.0) * 100.0
            return "홀딩", (
                f"20일 저점 조건: 합산은 {action} 구간이지만 20일 최저 종가 {_fmt(low)}보다 "
                f"{gap:.1f}% 높아(5% 초과) 홀딩"
            )
        if skip_bb:
            return action, ""
        bb = [c for c in closes_d[-BB_DAYS:] if c and c > 0]
        if len(bb) < BB_DAYS:
            return "홀딩", f"볼린저 하단 조건: 합산은 {action} 구간이지만 종가가 {BB_DAYS}일보다 적어 하단을 못 구해 홀딩"
        mean = sum(bb) / len(bb)
        var = sum((c - mean) ** 2 for c in bb) / (len(bb) - 1)
        lower = mean - BB_K * var ** 0.5
        if not price < lower:
            return "홀딩", (
                f"볼린저 하단 조건: 합산은 {action} 구간이지만 현재가 {_fmt(price)}가 "
                f"볼린저 하단(20일, 2σ) {_fmt(lower)}보다 위라 홀딩"
            )
        return action, ""
    high = max(window)
    if price < high * (1.0 - GATE_BAND_SELL) - 1e-12:
        gap = (1.0 - price / high) * 100.0
        return "홀딩", (
            f"20일 고점 조건: 합산은 {action} 구간이지만 20일 최고 종가 {_fmt(high)}보다 "
            f"{gap:.1f}% 낮아(3% 초과) 홀딩"
        )
    return action, ""


DIP_MA_DAYS = 60
DIP_MA_SHIFT = 10
DIP_RSI_MAX = 40.0


def _dip_buy_note(closes_d: list[float], price: float, chg6, rsi_val) -> str:
    """눌림목 매수: 60일선이 10일 전보다 높고, 6개월 수익률 > 0, 종가가 볼린저 하단(20일, 2σ) 아래, RSI 40 이하.

    합산 점수 컷과 20일 저점 조건은 보지 않는다. 맞으면 설명 문장, 아니면 빈 문자열.
    """
    try:
        rsi_f = float(rsi_val)
    except (TypeError, ValueError):
        return ""
    if rsi_f != rsi_f or rsi_f > DIP_RSI_MAX:
        return ""
    if chg6 is None or not float(chg6) > 0:
        return ""
    vals = [c for c in closes_d if c and c > 0]
    if len(vals) < DIP_MA_DAYS + DIP_MA_SHIFT or not price or price <= 0:
        return ""
    ma_now = sum(vals[-DIP_MA_DAYS:]) / DIP_MA_DAYS
    ma_prev = sum(vals[-DIP_MA_DAYS - DIP_MA_SHIFT:-DIP_MA_SHIFT]) / DIP_MA_DAYS
    if not ma_now > ma_prev:
        return ""
    bb = vals[-BB_DAYS:]
    mean = sum(bb) / len(bb)
    var = sum((c - mean) ** 2 for c in bb) / (len(bb) - 1)
    lower = mean - BB_K * var ** 0.5
    if not price < lower:
        return ""
    return (
        f"눌림목 매수: 60일선 상승 중(10일 전 {_fmt(ma_prev)} → {_fmt(ma_now)}) · 6개월 {float(chg6) * 100:+.1f}% · "
        f"볼린저 하단 {_fmt(lower)} 아래 · RSI {rsi_f:.0f}"
    )


MKT_DIP_INDEX_RSI_MAX = 30.0
MKT_DIP_RSI_MAX = 40.0


def _market_dip_note(as_of, market, rsi_val) -> str:
    """시장 과매도 매수: 나스닥(^IXIC) RSI(14) 30 이하이고 종목 RSI 40 이하. 지수 데이터 없으면 건너뜀."""
    try:
        rsi_f = float(rsi_val)
    except (TypeError, ValueError):
        return ""
    if rsi_f != rsi_f or rsi_f > MKT_DIP_RSI_MAX:
        return ""
    try:
        from .market_index import index_rsi_on

        idx = index_rsi_on(as_of, market)
    except Exception:
        idx = None
    if idx is None or idx > MKT_DIP_INDEX_RSI_MAX:
        return ""
    return f"시장 과매도 매수: 나스닥 RSI {idx:.0f} · RSI {rsi_f:.0f}"


HAMMER_WICK_MIN = 0.60
HAMMER_BODY_MAX = 0.30
HAMMER_RSI_MAX = 35.0
HAMMER_DD20_MIN = 0.10


def _hammer_buy_note(df, price: float, rsi_val) -> str:
    """망치형 반등 매수: 오늘 일봉 아래꼬리 ≥ 고저폭 60%, 몸통 ≤ 30%, RSI 35 이하, 종가가 20일 최고 종가 대비 10% 이상 아래.

    일봉(또는 일봉으로 묶을 수 있는 시간봉) OHLC가 없으면 건너뛴다.
    """
    try:
        rsi_f = float(rsi_val)
        px = float(price)
    except (TypeError, ValueError):
        return ""
    if rsi_f != rsi_f or rsi_f > HAMMER_RSI_MAX or not px > 0:
        return ""
    try:
        if df is None or getattr(df, "empty", True) or not all(c in df for c in ("open", "high", "low", "close")):
            return ""
        work = df[["open", "high", "low", "close"]].apply(pd.to_numeric, errors="coerce").dropna()
        if len(work) < 20:
            return ""
        idx = pd.DatetimeIndex(work.index)
        if len(idx) > 2 and pd.Series(idx).diff().dropna().median() > pd.Timedelta(days=1, hours=12):
            return ""  # 여러 날 묶음 봉이면 일봉 캔들이 아님
        g = work.groupby(idx.normalize())
        day = pd.DataFrame({"open": g["open"].first(), "high": g["high"].max(), "low": g["low"].min(), "close": g["close"].last()})
        if len(day) < 20:
            return ""
        o, h, l = float(day["open"].iloc[-1]), float(day["high"].iloc[-1]), float(day["low"].iloc[-1])
        c = px
        h, l = max(h, c), min(l, c)
        rng = h - l
        if not rng > 0:
            return ""
        wick = (min(o, c) - l) / rng
        body = abs(c - o) / rng
        if wick < HAMMER_WICK_MIN or body > HAMMER_BODY_MAX:
            return ""
        closes = [float(v) for v in day["close"].iloc[-20:-1].tolist()] + [c]
        hi20 = max(closes)
        dd = c / hi20 - 1
        if dd > -HAMMER_DD20_MIN:
            return ""
    except Exception:
        return ""
    return (
        f"망치형 반등 매수: 아래꼬리 {wick * 100:.0f}% · 몸통 {body * 100:.0f}% · "
        f"20일 고점 {_fmt(hi20)} 대비 {dd * 100:.1f}% · RSI {rsi_f:.0f}"
    )


NQ_DROP_MIN = 0.10
NQ_DROP_RSI_MAX = 40.0


def _nasdaq_drop_note(as_of, market, rsi_val) -> str:
    """나스닥 급락 매수: 나스닥 종가가 20일 최고 종가보다 10% 이상 아래이고 종목 RSI 40 이하. 지수 데이터 없으면 건너뜀."""
    try:
        rsi_f = float(rsi_val)
    except (TypeError, ValueError):
        return ""
    if rsi_f != rsi_f or rsi_f > NQ_DROP_RSI_MAX:
        return ""
    try:
        from .market_index import index_drop20_on

        dd = index_drop20_on(as_of, market)
    except Exception:
        dd = None
    if dd is None or dd > -NQ_DROP_MIN:
        return ""
    return f"나스닥 급락 매수: 나스닥 20일 고점 대비 {dd * 100:.1f}% · RSI {rsi_f:.0f}"


def _stock_sell_hold_note(market, ticker, price: float, closes_d: list[float]) -> str:
    """v123: 주식 매도 보류 사유 (크립토는 빈 문자열). 종가가 볼린저 상단(20일, 2σ) 위가 아니면 보류."""
    mk = str(market or "").upper()
    tk = str(ticker or "").upper()
    if mk == "CRYPTO" or (not mk and tk.endswith("-USD")):
        return ""
    bb = [c for c in (closes_d or [])[-BB_DAYS:] if c and c > 0]
    if len(bb) < BB_DAYS:
        return f"매도 보류: 종가가 {BB_DAYS}일보다 적어 볼린저 상단을 못 구함 (주식)"
    mean = sum(bb) / len(bb)
    var = sum((c - mean) ** 2 for c in bb) / (len(bb) - 1)
    upper = mean + BB_K * var ** 0.5
    if float(price) > upper:
        return ""
    return f"매도 보류: 볼린저 상단(20일, 2σ) {_fmt(upper)} 아래 (주식)"


HIVOL_ATR_MED = 6.0  # v124: 최근 250거래일 14일 ATR% 중앙값 기준
HIVOL_DAYS = 250
HIVOL_MIN_BARS = 60


def _atr_pct_median(an, daily_long=None) -> float | None:
    """일봉 기준 14일 ATR%(ATR/종가)의 최근 250거래일 중앙값. 데이터가 60봉 미만이면 None.

    v125: daily_long(약 400일 일봉 DataFrame 또는 그것을 돌려주는 함수)이 있으면 그것으로 계산한다.
    없으면 분석 데이터(조회기간)로 계산한다."""
    try:
        df = None
        if daily_long is not None:
            try:
                df = daily_long() if callable(daily_long) else daily_long
            except Exception:
                df = None
        if df is None or getattr(df, "empty", True):
            df = getattr(an, "df", None)
        if df is None or getattr(df, "empty", True):
            return None
        d = df[["high", "low", "close"]].apply(pd.to_numeric, errors="coerce").dropna()
        as_of = getattr(an, "as_of", None)
        idx = pd.DatetimeIndex(d.index)
        if idx.tz is not None:
            idx = idx.tz_localize(None)
        d.index = idx
        if as_of is not None:
            cut = pd.Timestamp(as_of)
            if cut.tzinfo is not None:
                cut = cut.tz_localize(None)
            d = d[d.index.normalize() <= cut.normalize()]
        idx = pd.DatetimeIndex(d.index)
        g = d.groupby(idx.normalize())
        d = pd.DataFrame({"high": g["high"].max(), "low": g["low"].min(), "close": g["close"].last()})
        h, l, c = d["high"], d["low"], d["close"]
        tr = pd.concat([h - l, (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1)
        atrp = (tr.rolling(14).mean() / c * 100.0).dropna().tail(HIVOL_DAYS)
        if len(atrp) < HIVOL_MIN_BARS:
            return None
        return float(atrp.median())
    except Exception:
        return None


def _high_vol_sell_hold_note(market, ticker, an, daily_long=None) -> str:
    """v124: 주식 중 ATR% 중앙값이 6% 이상인 고변동 종목은 매도 보류 (크립토 제외)."""
    mk = str(market or "").upper()
    tk = str(ticker or "").upper()
    if mk == "CRYPTO" or (not mk and tk.endswith("-USD")):
        return ""
    med = _atr_pct_median(an, daily_long)
    if med is None or med < HIVOL_ATR_MED:
        return ""
    return f"매도 보류: 고변동 종목 (ATR 중앙값 {med:.1f}%)"


def _action_from_pct(score_pct: int, cuts: dict) -> str:
    buy_weak = int(cuts["buy_weak"])
    buy_mid = int(cuts["buy_mid"])
    buy_strong = int(cuts["buy_strong"])
    sell_weak = int(cuts["sell_weak"])
    sell_mid = int(cuts["sell_mid"])
    sell_strong = int(cuts["sell_strong"])
    if score_pct >= buy_strong:
        return "강한 매수"
    if score_pct >= buy_mid:
        return "매수"
    if score_pct >= buy_weak:
        return "약한 매수"
    if score_pct <= sell_strong:
        return "강한 매도"
    if score_pct <= sell_mid:
        return "매도"
    if score_pct <= sell_weak:
        return "약한 매도"
    return "홀딩"


def _daily_range(df, atr: float, price: float) -> float:
    """차트 봉을 하루로 묶어 마지막 날의 고가−저가. 없으면 ATR."""
    fallback = float(atr) if atr and atr > 0 else max(float(price) * 0.02, 1e-8)
    if df is None or getattr(df, "empty", True):
        return fallback
    if "high" not in df.columns or "low" not in df.columns:
        return fallback
    try:
        work = df[["high", "low"]].copy()
        work.index = pd.DatetimeIndex(pd.to_datetime(work.index))
        daily = work.resample("1D").agg(high=("high", "max"), low=("low", "min")).dropna()
        if daily.empty:
            return fallback
        last = daily.iloc[-1]
        span = float(last["high"]) - float(last["low"])
        if span > 0:
            return span
    except (TypeError, ValueError, KeyError):
        return fallback
    return fallback


def _near_band(price: float, atr: float, df=None) -> float:
    """근접 거리: 하루 변동폭의 40%와 주가의 0.8% 중 큰 값."""
    px = float(price) if price and price > 0 else 0.0
    day_range = _daily_range(df, atr, px if px > 0 else 1.0)
    return max(day_range * 0.40, (px if px > 0 else day_range) * 0.008)


def recommend(
    an: Analysis,
    six_month_chg: float | None = None,
    lookback_days: int | None = None,
    rule: dict | None = None,
    option_walls: dict | None = None,
    market: str | None = None,
    ticker: str | None = None,
    df_1m=None,
    daily_long=None,
    **_unused,
) -> Signal:
    cfg = merge_rule(rule)
    w = cfg["weights"]
    crypto = is_crypto(market, ticker)
    cuts = dict(cfg["cuts_crypto"] if crypto else cfg["cuts"])
    cut_kind = "코인" if crypto else "주식"

    def wp(key: str) -> int:
        return int(w.get(key, DEFAULT_WEIGHTS[key]))

    price = an.price
    atr = an.atr if an.atr and an.atr > 0 else price * 0.02
    near = _near_band(price, atr, getattr(an, "df", None))

    nsup = an.supports[0] if an.supports else None
    nres = an.resistances[0] if an.resistances else None

    score = 0
    reasons: list[str] = []
    score_rows: list[dict] = []

    def add(item: str, detail: str, pts: float) -> None:
        nonlocal score
        if isinstance(pts, float) and pts.is_integer():
            pts = int(pts)
        score += pts
        if isinstance(score, float) and score.is_integer():
            score = int(score)
        label = f"+{pts:g}" if pts > 0 else f"{pts:g}"
        score_rows.append({"항목": item, "내용": detail, "점수": label})
        reasons.append(f"{item}: {detail} ({label})")

    base = wp("base")
    add("기본", "시작할 때 항상 줌", base)

    t_ko = {"up": "상승", "down": "하락", "sideways": "횡보"}
    t_main = an.trend or "sideways"
    main_txt = t_ko.get(t_main, t_main)
    lookback = int(lookback_days) if lookback_days is not None else None
    if lookback is not None and lookback <= 60:
        if t_main == "up":
            add("조회기간 추세", f"조회기간 {main_txt}", wp("trend_lookback_short_up"))
        elif t_main == "down":
            add("조회기간 추세", f"조회기간 {main_txt}", wp("trend_lookback_short_down"))
        else:
            add("조회기간 추세", f"조회기간 {main_txt}", 0)
    elif lookback is not None and lookback > 60:
        t_1m = None
        if df_1m is not None and not getattr(df_1m, "empty", True):
            try:
                px_1m = float(pd.to_numeric(df_1m["close"], errors="coerce").iloc[-1])
            except (TypeError, ValueError, IndexError, KeyError):
                px_1m = float(price)
            t_1m = classify_trend(df_1m, px_1m if px_1m > 0 else float(price))
        m1_txt = t_ko.get(t_1m, t_1m or "없음")
        if t_1m == "up" and t_main in ("up", "sideways"):
            add("조회기간 추세", f"조회기간 {main_txt} · 1개월 상승", wp("trend_lookback_1m_up"))
        elif t_1m == "down" and t_main in ("down", "sideways"):
            add("조회기간 추세", f"조회기간 {main_txt} · 1개월 하락", wp("trend_lookback_1m_down"))
        else:
            add("조회기간 추세", f"조회기간 {main_txt} · 1개월 {m1_txt}", 0)
    else:
        add("조회기간 추세", "조회기간 없음", 0)

    up_line = an.up_line
    down_line = an.down_line
    y_dn = _line_y_at(down_line, float(down_line[2])) if down_line else None
    y_up = _line_y_at(up_line, float(up_line[2])) if up_line else None
    near_dn = y_dn is not None and abs(price - y_dn) <= near and price <= y_dn
    broke_dn = y_dn is not None and price > y_dn
    near_up = y_up is not None and abs(price - y_up) <= near and price >= y_up
    broke_up = y_up is not None and price < y_up
    pdn_line = getattr(an, "price_down_line", None)
    y_pdn = _line_y_at(pdn_line, float(pdn_line[2])) if pdn_line else None
    pdn_near = (
        y_pdn is not None
        and price <= y_pdn
        and abs(price - y_pdn) <= near
        and wp("price_down_line_near") != 0
    )
    if not down_line:
        add("단기 하락 추세선 근접", "단기 하락선 없음", 0)
    elif y_dn is None:
        add("단기 하락 추세선 근접", "단기 하락선 위치를 계산하지 못함", 0)
    elif broke_dn:
        add("단기 하락 추세선 근접", f"단기 하락선 {_fmt(y_dn)} 상향 돌파라 무효", 0)
    elif near_dn and pdn_near:
        add(
            "단기 하락 추세선 근접",
            f"단기 하락선 {_fmt(y_dn)} 근처지만 장기 하락선 근접과 겹쳐 무효",
            0,
        )
    elif near_dn:
        add(
            "단기 하락 추세선 근접",
            f"단기 하락선 {_fmt(y_dn)} 근처 (이격 {_fmt(abs(price - y_dn))})",
            wp("down_line_near"),
        )
    else:
        add(
            "단기 하락 추세선 근접",
            f"단기 하락선 {_fmt(y_dn)} 과 이격 {_fmt(abs(price - y_dn))}",
            0,
        )

    if not pdn_line:
        add("장기 하락 추세선 근접", "장기 하락선 없음", 0)
    elif y_pdn is None:
        add("장기 하락 추세선 근접", "장기 하락선 위치를 계산하지 못함", 0)
    elif price > y_pdn:
        add("장기 하락 추세선 근접", f"장기 하락선 {_fmt(y_pdn)} 상향 돌파라 무효", 0)
    elif abs(price - y_pdn) <= near:
        add(
            "장기 하락 추세선 근접",
            f"장기 하락선 {_fmt(y_pdn)} 근처 (이격 {_fmt(abs(price - y_pdn))})",
            wp("price_down_line_near"),
        )
    else:
        add(
            "장기 하락 추세선 근접",
            f"장기 하락선 {_fmt(y_pdn)} 과 이격 {_fmt(abs(price - y_pdn))}",
            0,
        )

    if not up_line:
        add("장기 상승 추세선 근접", "장기 상승선 없음", 0)
    elif y_up is None:
        add("장기 상승 추세선 근접", "장기 상승선 위치를 계산하지 못함", 0)
    elif broke_up:
        add("장기 상승 추세선 근접", f"장기 상승선 {_fmt(y_up)} 이탈이라 무효", 0)
    elif near_up:
        add(
            "장기 상승 추세선 근접",
            f"장기 상승선 {_fmt(y_up)} 근처 (이격 {_fmt(abs(price - y_up))})",
            wp("up_line_near"),
        )
    else:
        add(
            "장기 상승 추세선 근접",
            f"장기 상승선 {_fmt(y_up)} 과 이격 {_fmt(abs(price - y_up))}",
            0,
        )

    short_up = getattr(an, "short_up_line", None)
    y_short = _line_y_at(short_up, float(short_up[2])) if short_up else None
    near_short = y_short is not None and abs(price - y_short) <= near and price >= y_short
    broke_short = y_short is not None and price < y_short
    if not short_up:
        add("단기 상승 추세선 근접", "단기 상승선 없음", 0)
    elif y_short is None:
        add("단기 상승 추세선 근접", "단기 상승선 위치를 계산하지 못함", 0)
    elif broke_short:
        add("단기 상승 추세선 근접", f"단기 상승선 {_fmt(y_short)} 이탈이라 무효", 0)
    elif near_short and near_up:
        add(
            "단기 상승 추세선 근접",
            f"단기 상승선 {_fmt(y_short)} 근처지만 장기 상승선 근접과 겹쳐 무효",
            0,
        )
    elif near_short:
        add(
            "단기 상승 추세선 근접",
            f"단기 상승선 {_fmt(y_short)} 근처 (이격 {_fmt(abs(price - y_short))})",
            wp("short_up_line_near"),
        )
    else:
        add(
            "단기 상승 추세선 근접",
            f"단기 상승선 {_fmt(y_short)} 과 이격 {_fmt(abs(price - y_short))}",
            0,
        )

    if nsup:
        dist_s = price - nsup.price
        pct_s = dist_s / price * 100
        sup_str = float(nsup.strength)
        if dist_s <= near:
            if sup_str >= 4:
                add(
                    "지지 근접",
                    f"근접 {_fmt(nsup.price)} ({nsup.note}, 강도 {sup_str:.1f}, 이격 {pct_s:.2f}%)",
                    wp("support_near"),
                )
            else:
                add(
                    "지지 근접",
                    f"근접 {_fmt(nsup.price)} ({nsup.note}, 강도 {sup_str:.1f} · 4 미만 가점 없음, 이격 {pct_s:.2f}%)",
                    0,
                )
        else:
            add("지지 근접", f"{_fmt(nsup.price)} 까지 {pct_s:.2f}% (강도 {sup_str:.1f})", 0)
    else:
        add("지지 근접", "없음", 0)

    if nres:
        dist_r = nres.price - price
        pct_r = dist_r / price * 100
        res_str = float(nres.strength)
        if dist_r <= near:
            if res_str >= 4:
                add(
                    "저항 근접",
                    f"근접 {_fmt(nres.price)} ({nres.note}, 강도 {res_str:.1f}, 이격 {pct_r:.2f}%)",
                    wp("resist_near"),
                )
            else:
                add(
                    "저항 근접",
                    f"근접 {_fmt(nres.price)} ({nres.note}, 강도 {res_str:.1f} · 4 미만 감점 없음, 이격 {pct_r:.2f}%)",
                    0,
                )
        else:
            add("저항 근접", f"{_fmt(nres.price)} 까지 {pct_r:.2f}% (강도 {res_str:.1f})", 0)
    else:
        add("저항 근접", "없음", 0)

    poc_pts = abs(wp("poc"))
    val_pts = abs(wp("val"))
    if abs(price - an.poc) <= near:
        add("최대 매물 (POC)", f"최대 매물 {_fmt(an.poc)} 근처 (이격 {_fmt(abs(price - an.poc))})", poc_pts)
    else:
        add("최대 매물 (POC)", f"최대 매물 {_fmt(an.poc)} 과 이격 {_fmt(abs(price - an.poc))} · 이탈 무효", 0)
    if price < an.val:
        add("밸류 하단 (VAL)", f"하단 {_fmt(an.val)} 아래", val_pts)
    else:
        add("밸류 하단 (VAL)", f"하단 {_fmt(an.val)} (해당 없음)", 0)
    if price > an.vah:
        add("밸류 상단 (VAH)", f"상단 {_fmt(an.vah)} 위", wp("vah"))
    else:
        add("밸류 상단 (VAH)", f"상단 {_fmt(an.vah)} (해당 없음)", 0)

    if an.rsi >= 70:
        add("RSI", f"{an.rsi:.1f} (70 이상)", -abs(wp("rsi_high")))
    elif an.rsi <= 35:
        add("RSI", f"{an.rsi:.1f} (35 이하)", abs(wp("rsi")))
    else:
        add("RSI", f"{an.rsi:.1f} 중립", 0)

    def _ma_near(name: str, level, pts_key: str) -> None:
        if level is None:
            add(name, f"{name} 없음", 0)
        elif abs(price - level) <= near:
            add(name, f"{name} {_fmt(level)} 근처 (이격 {_fmt(abs(price - level))})", wp(pts_key))
        else:
            add(name, f"{name} {_fmt(level)} 과 이격 {_fmt(abs(price - level))} · 이탈 무효", 0)

    _ma_near("20일선", an.ma20, "ma20")
    _ma_near("60일선", an.ma60, "ma60_near")
    _ma_near("180일선", getattr(an, "ma200", None), "ma200_near")

    if _ma20_cross_below_ma60(an.df, 4):
        add("20일선 60일선 교차", "20일선이 하방으로 60일선 아래를 막 깸", wp("ma_cross_20_60"))
    else:
        add("20일선 60일선 교차", "최근 4봉 안 교차 아님", 0)

    chg = _one_month_change(an, price)
    if chg is None:
        add("1개월 하락률", "계산 불가", 0)
    else:
        chg_pct = chg * 100.0
        if chg_pct <= -35 + 1e-9:
            band, key = "35% 이상 하락", "chg1_down30"
        elif chg_pct <= -25 + 1e-9:
            band, key = "25% 이상 35% 미만 하락", "chg1_down20"
        elif chg_pct <= -15 + 1e-9:
            band, key = "15% 이상 25% 미만 하락", "chg1_down10"
        else:
            band, key = None, None
        if key and broke_up:
            add("1개월 하락률", f"{chg_pct:.1f}% ({band}) · 장기 상승선 이탈이라 무효", 0)
        elif key:
            add("1개월 하락률", f"{chg_pct:.1f}% ({band})", wp(key))
        elif chg_pct < 0:
            add("1개월 하락률", f"{chg_pct:.1f}%", 0)
        else:
            add("1개월 하락률", "해당 없음", 0)

    closes_d = _daily_closes(getattr(an, "df", None), price)
    if crypto:
        add("10일 고점 대비 하락", "코인 제외", 0)
    elif len(closes_d) < 2:
        add("10일 고점 대비 하락", "직전 종가 없음", 0)
    else:
        hi10 = max(closes_d[-11:])
        drop10 = price / hi10 - 1.0 if hi10 > 0 else 0.0
        if drop10 <= -0.15 + 1e-9:
            add("10일 고점 대비 하락", f"10일 최고 종가 {_fmt(hi10)} 대비 {drop10 * 100:.1f}% (15% 이상 하락)", wp("drop10_15"))
        else:
            add("10일 고점 대비 하락", f"10일 최고 종가 {_fmt(hi10)} 대비 {drop10 * 100:.1f}%", 0)

    chg6 = six_month_chg
    if chg6 is None:
        chg6 = period_return(an.df, an.as_of, price, 180)
    if chg6 is None:
        add("6개월 상승률", "6개월 전 가격 없음", 0)
    elif chg6 >= 3.0:
        if chg6 >= 8.0:
            band, rng, key = "800% 이상", "800% 이상", "chg6_800"
        elif chg6 >= 6.0:
            band, rng, key = "600% 이상", "600% 이상 800% 미만", "chg6_600"
        else:
            band, rng, key = "300% 이상", "300% 이상 600% 미만", "chg6_300"
        if an.trend != "up":
            kind = "하락" if an.trend == "down" else "횡보"
            add("6개월 상승률", f"{chg6 * 100:.1f}% ({band}) · {kind}라 무효", 0)
        else:
            pen = wp(key)
            bb_in = _inside_bollinger(an, price)
            if bb_in and pen:
                add("6개월 상승률", f"{chg6 * 100:.1f}% ({rng}) · 볼린저 밴드 내 감점 절반", pen / 2)
            else:
                add("6개월 상승률", f"{chg6 * 100:.1f}% ({rng})", pen)
    else:
        add("6개월 상승률", f"{chg6 * 100:.1f}%", 0)

    bar_ret = _one_bar_return(an, price)
    if bar_ret is None:
        add("단기 급상승", "직전 봉 없음", 0)
    elif bar_ret >= 0.20 - 1e-9:
        add("단기 급상승", f"1봉 {bar_ret * 100:.1f}% (20% 이상)", wp("bar_spike_20"))
    else:
        add("단기 급상승", f"1봉 {bar_ret * 100:.1f}%", 0)

    stop = None
    target = None
    if nsup:
        stop = nsup.price - atr * 0.35
    if nres:
        target = nres.price

    rr = None
    if stop and target and price > stop:
        risk = price - stop
        reward = target - price
        if risk > 0:
            rr = reward / risk

    bar_count = 0 if an.df is None else len(an.df)
    buy_weak = int(cuts["buy_weak"])
    buy_mid = int(cuts["buy_mid"])
    buy_strong = int(cuts["buy_strong"])
    sell_weak = int(cuts["sell_weak"])
    sell_mid = int(cuts["sell_mid"])
    sell_strong = int(cuts["sell_strong"])
    if bar_count < 50:
        reasons.append(
            f"표본 {bar_count}봉으로 짧음 — 약한 매수 {buy_weak}%↑ / 강한 매도 {sell_strong}%↓"
        )

    score = max(0, score)
    score_pct_base = score_to_pct(score)
    action_base = _action_from_pct(score_pct_base, cuts)
    option_applied = False
    if option_walls is not None and action_base in ("약한 매수", "매수", "강한 매수", "약한 매도", "매도", "강한 매도"):
        from .options import option_wall_adjust

        opt_pts, opt_detail = option_wall_adjust(action_base, option_walls, wp("option_wall"))
        add("옵션", opt_detail, opt_pts)
        score = max(0, score)
        option_applied = True

    lo, hi = SCORE_LO, SCORE_HI
    score_pct = score_to_pct(score)
    action = _action_from_pct(score_pct, cuts)
    gate_note = ""
    # v120: 장기 상승 추세선을 5% 이상 밑돌면(이탈) 점수 매수의 볼린저 하단 확인을 생략 (20일 저점 조건은 유지)
    skip_bb = bool(y_up is not None and y_up > 0 and price < y_up * (1.0 - UPLINE_BB_SKIP))
    action_base, _ = _price_gate(action_base, price, closes_d, skip_bb)
    pre_gate = action
    action, gate_note = _price_gate(action, price, closes_d, skip_bb)
    if skip_bb and action in _BUY_SET and pre_gate in _BUY_SET and _price_gate(pre_gate, price, closes_d)[0] == "홀딩":
        reasons.append(f"장기 상승선 {_fmt(y_up)} 대비 {(price / y_up - 1) * 100:.1f}% 이탈로 볼린저 하단 확인 생략")
    if gate_note:
        reasons.append(gate_note)
    # v123: 주식(비크립토) 매도는 종가가 볼린저 상단(20일, 2σ) 위일 때만. 옵션 반영 후 결과에도 적용.
    if action in _SELL_SET or action_base in _SELL_SET:
        sell_hold = _stock_sell_hold_note(market, ticker, price, closes_d)
        if not sell_hold:
            sell_hold = _high_vol_sell_hold_note(market, ticker, an, daily_long)
        if sell_hold:
            if action in _SELL_SET:
                action = "홀딩"
                gate_note = gate_note or sell_hold
                reasons.append(sell_hold)
            if action_base in _SELL_SET:
                action_base = "홀딩"
    dip_note = ""
    if action == "홀딩":
        dip_note = _dip_buy_note(closes_d, price, chg6, getattr(an, "rsi", None))
        if dip_note:
            action = "약한 매수"
            action_base = "약한 매수"
            reasons.append(dip_note)
    mkt_note = ""
    if action == "홀딩":
        mkt_note = _market_dip_note(getattr(an, "as_of", None), market, getattr(an, "rsi", None))
        if mkt_note:
            action = "약한 매수"
            action_base = "약한 매수"
            reasons.insert(0, mkt_note)
    ham_note = ""
    if action == "홀딩":
        ham_note = _hammer_buy_note(getattr(an, "df", None), price, getattr(an, "rsi", None))
        if ham_note:
            action = "약한 매수"
            action_base = "약한 매수"
            reasons.insert(0, ham_note)
    nqd_note = ""
    if action == "홀딩":
        nqd_note = _nasdaq_drop_note(getattr(an, "as_of", None), market, getattr(an, "rsi", None))
        if nqd_note:
            action = "약한 매수"
            action_base = "약한 매수"
            reasons.insert(0, nqd_note)
    reasons.append(
        f"합산 {score_pct}% ({score}점, 범위 {lo}~{hi}) · "
        f"{cut_kind} 컷 · 약한매수 {buy_weak}%↑ / 매수 {buy_mid}%↑ / 강한매수 {buy_strong}%↑ · "
        f"약한매도 {sell_weak}%↓ / 매도 {sell_mid}%↓ / 강한매도 {sell_strong}%↓"
        f" · 규칙 v{SIGNAL_RULE_VERSION}"
    )
    if action != action_base:
        reasons.append(f"기존 규칙 {action_base} → 옵션 반영 후 {action}")

    if score_pct >= buy_strong:
        summary = "합산이 높아 강한 매수 구간입니다."
    elif score_pct >= buy_mid:
        summary = "매수 구간에 들어왔습니다."
    elif score_pct >= buy_weak:
        summary = "매수 쪽으로 기울었지만 강도는 약한 구간입니다."
    elif score_pct <= sell_strong:
        summary = "합산이 낮아 강한 매도 구간입니다."
    elif score_pct <= sell_mid:
        summary = "매도 구간에 들어왔습니다."
    elif score_pct <= sell_weak:
        summary = "매도 쪽으로 기울었지만 강도는 약한 구간입니다."
    else:
        summary = "지지와 저항 사이이거나 신호가 엇갈려 관망(홀딩)이 낫습니다."
        if bar_count < 50:
            summary = "조회 기간이 짧아 신호가 쉽게 바뀝니다. 지금은 관망(홀딩)이 낫습니다."

    if gate_note:
        summary = gate_note + " 지금은 관망(홀딩)이 낫습니다."
    if dip_note:
        summary = dip_note + ". 합산 점수와 상관없이 상승 추세 속 급한 눌림이라 약한 매수로 봅니다."
    if nqd_note:
        summary = nqd_note + ". 합산 점수와 상관없이 나스닥이 짧은 기간에 크게 빠져 약한 매수로 봅니다."
    if ham_note:
        summary = ham_note + ". 합산 점수와 상관없이 과매도 급락 뒤 망치형 반등 캔들이라 약한 매수로 봅니다."
    if mkt_note:
        summary = mkt_note + ". 합산 점수와 상관없이 시장 전체가 과매도라 약한 매수로 봅니다."
    if action != action_base:
        summary = f"기존 규칙 {action_base}에 옵션을 반영해 {action}로 조정했습니다. " + summary
    if action in ("매수", "약한 매수", "강한 매수") and not dip_note and not mkt_note and not ham_note and not nqd_note:
        summary = "하락·눌림 쪽에서 지지·하단 조건이 맞습니다. " + summary
    elif action in ("매도", "약한 매도", "강한 매도"):
        summary = "상승·고가 쪽에서 저항·상단 조건이 맞습니다. " + summary

    tilt = abs(score - base)
    confidence = int(max(35, min(90, 50 + tilt * 12)))
    if action == "홀딩":
        confidence = int(max(30, 55 - tilt * 4))

    return Signal(
        action=action,
        confidence=confidence,
        score=score,
        reasons=reasons,
        stop=stop,
        target=target,
        reward_risk=rr,
        nearest_support=nsup,
        nearest_resistance=nres,
        summary=summary,
        score_pct=score_pct,
        score_min=lo,
        score_max=hi,
        score_rows=score_rows,
        action_base=action_base,
        score_pct_base=score_pct_base,
        option_applied=option_applied,
        dip_buy=bool(dip_note),
        market_dip=bool(mkt_note),
        hammer_buy=bool(ham_note),
        nasdaq_drop_buy=bool(nqd_note),
    )


REPEAT_BUY_DAYS = 20
REPEAT_BUY_TOL = 0.001


def repeat_buy_warning(
    an: Analysis,
    sig: Signal,
    lookback_days: int | None = None,
    rule: dict | None = None,
    market: str | None = None,
    ticker: str | None = None,
    days: int = REPEAT_BUY_DAYS,
    df_full=None,
    df_1m_full=None,
) -> str:
    """표시 전용 경고. 매수 신호 + 20일 신저가 종가 + 직전 20거래일 안에 더 높은 가격의 매수 신호가 있었으면 문구를 돌려준다.
    매수/매도 판정·점수는 바꾸지 않는다 (규칙 버전과 무관)."""
    try:
        if sig is None or sig.action not in _BUY_SET:
            return ""
        df = getattr(an, "df", None)
        price = float(getattr(an, "price", 0) or 0)
        if df is None or getattr(df, "empty", True) or price <= 0:
            return ""
        closes_d = _daily_closes(df, price)
        if len(closes_d) < 2:
            return ""
        if price > min(closes_d[-days:]) * (1.0 + REPEAT_BUY_TOL):
            return ""
        from .analysis import analyze

        close = pd.to_numeric(df["close"], errors="coerce").dropna()
        idx = pd.DatetimeIndex(close.index)
        daily = close.groupby(idx.normalize()).last()
        prior = list(daily.index[:-1])[-days:]
        lb = int(lookback_days or 180)
        src = df
        if df_full is not None and not getattr(df_full, "empty", True) and "close" in df_full:
            if getattr(df_full.index, "tz", None) == getattr(df.index, "tz", None):
                src = df_full
        for d in reversed(prior):
            px = float(daily.loc[d])
            if px <= price * (1.0 + REPEAT_BUY_TOL):
                continue
            cutoff = d + pd.Timedelta(days=1) - pd.Timedelta(seconds=1)
            start = d - pd.Timedelta(days=lb)
            if getattr(src.index, "tz", None) is not None:
                cutoff = cutoff.tz_localize(src.index.tz) if cutoff.tzinfo is None else cutoff
                start = start.tz_localize(src.index.tz) if start.tzinfo is None else start
            sub = src.loc[(src.index >= start) & (src.index <= cutoff)]
            if len(sub) < 20:
                continue
            try:
                an_d = analyze(sub, as_of=d.date(), spot_price=px, price_source="해당일 종가", lookback_days=lookback_days)
                chg6 = period_return(src, d.date(), px, 180)
                w1m = None
                if df_1m_full is not None and not getattr(df_1m_full, "empty", True):
                    t1 = df_1m_full.index
                    c1, s1 = d + pd.Timedelta(days=1) - pd.Timedelta(seconds=1), d - pd.Timedelta(days=30)
                    if getattr(t1, "tz", None) is not None:
                        c1, s1 = c1.tz_localize(t1.tz), s1.tz_localize(t1.tz)
                    w1m = df_1m_full.loc[(t1 >= s1) & (t1 <= c1)]
                    if len(w1m) < 24:
                        w1m = None
                s = recommend(
                    an_d, six_month_chg=chg6, lookback_days=lookback_days, rule=rule,
                    market=market, ticker=ticker, df_1m=w1m,
                )
            except Exception:
                continue
            if s.action in _BUY_SET:
                return (
                    f"⚠️ 하락 중 반복 매수: 최근 {days}일 내 매수({d.strftime('%m-%d')} {_fmt(px)}) 후 "
                    f"신저가 갱신 · 분할 진입 권장"
                )
        return ""
    except Exception:
        return ""


def repeat_buy_candidate(an: Analysis, sig: Signal, days: int = REPEAT_BUY_DAYS) -> bool:
    """추가 시세를 불러올 필요가 있는지 빠르게 확인 (매수 + 20일 신저가 종가)."""
    try:
        if sig is None or sig.action not in _BUY_SET:
            return False
        price = float(getattr(an, "price", 0) or 0)
        closes_d = _daily_closes(getattr(an, "df", None), price)
        return price > 0 and len(closes_d) >= 2 and price <= min(closes_d[-days:]) * (1.0 + REPEAT_BUY_TOL)
    except Exception:
        return False


def add_repeat_buy_warning(an: Analysis, sig: Signal, **kwargs) -> Signal:
    note = repeat_buy_warning(an, sig, **kwargs)
    if note:
        sig.reasons.insert(0, note)
        sig.summary = note + ". " + (sig.summary or "")
    return sig
