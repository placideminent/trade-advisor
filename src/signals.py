"""규칙 기반 매수/매도/홀딩. 지정일 현재가와 지지·저항·매물대·추세만 사용."""

from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

from .universe import is_crypto

SIGNAL_RULE_VERSION = 89
# 중립 기준점. 이보다 높으면 매수, 낮으면 매도.
SCORE_BASE = 15
# 합산 %는 0점=0%, 15점=50%, 31점=100%.
SCORE_LO = 0
SCORE_HI = 31

DEFAULT_WEIGHTS = {
    "base": 15,
    "trend": 1,
    "trend_1m": 0,
    "swing_low_near": 0,
    "swing_high_near": 0,
    "down_line_near": -1,
    "trendline_dir_down": -1,
    "trendline_dir_down_break": 0,
    "trendline_dir_down_upnear": 1,
    "trendline_1m_up": 1,
    "trendline_1m_up_both_up": 1,
    "trendline_up_1m_down": 0,
    "up_line_near": 1,
    "up_line_break": 0,
    "support_near": 1,
    "support_break": -2,
    "resist_near": -1,
    "poc": 1,
    "val": 1,
    "vah": -1,
    "rsi": 1,
    "ma20": 1,
    "ma60_near": 1,
    "ma200_near": 1,
    "ma_cross_20_60": -1,
    "bar_spike_20": -1,
    "chg1_50": -1,
    "chg1_down1": 0,
    "chg1_down10": 1,
    "chg1_down20": 2,
    "chg1_down30": 3,
    "chg1_down40": 0,
    "chg1_down50": 0,
    "chg6_50": 0,
    "chg6_200": 0,
    "chg6_300": -1,
    "chg6_500": 0,
    "chg6_600": -2,
    "chg6_800": -3,
    "rr_penalty": -1,
    "option_wall": 1,
}

DEFAULT_CUTS_STOCK = {
    "buy_weak": 60,
    "buy_mid": 67,
    "buy_strong": 73,
    "sell_weak": 33,
    "sell_mid": 27,
    "sell_strong": 20,
}
DEFAULT_CUTS_CRYPTO = {
    "buy_weak": 67,
    "buy_mid": 73,
    "buy_strong": 80,
    "sell_weak": 37,
    "sell_mid": 33,
    "sell_strong": 27,
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
    ("base", "기본", "중립 시작점 15점=50%"),
    ("trend", "추세", "모든 조회에서 하락 +, 상승 −"),
    ("down_line_near", "하락 추세선 근접", "하락선 근처 −1. 위로 돌파하면 무효"),
    ("up_line_near", "상승 추세선 근접", "상승선 근처 +1. 아래로 이탈하면 무효"),
    ("trendline_dir_down", "둘 다 하락·하락선 근접", "둘 다 하락이고 하락선 근처이면 −1. 돌파 무효"),
    ("trendline_dir_down_upnear", "둘 다 하락·상승선 근접", "둘 다 하락이고 상승선 근처이면 +1. 이탈 무효"),
    ("trendline_1m_up", "둘 다 하락·1개월 상승선 상방", "둘 다 하락이고 1개월(1시간봉) 상승선이 상방이면 +1"),
    ("trendline_1m_up_both_up", "둘 다 상승·1개월 상승선 하방", "둘 다 상승이고 1개월(1시간봉) 상승선이 하방이면 +1"),
    ("support_near", "지지 근접", "근접하고 강도 4 이상일 때만 +1"),
    ("support_break", "지지 이탈", "이탈 후 다음 지지가 현재가보다 뚜렷이 아래이면 −2"),
    ("resist_near", "저항 근접", "근접하고 강도 4 이상일 때만 −1"),
    ("poc", "POC", "최대 매물 근처 +1. 이탈하면 무효"),
    ("val", "VAL", "밸류 하단 아래이고 상승 추세일 때만 +1"),
    ("vah", "VAH", "밸류 상단 바로 위 −1. 멀리 돌파하면 무효"),
    ("rsi", "RSI", "35 이하 +, 70 이상 −"),
    ("ma20", "MA20 근처", "20일선 근처 +1. 완전 이탈하면 무효"),
    ("ma60_near", "MA60 근처", "60일선 근처 +1. 완전 이탈하면 무효"),
    ("ma200_near", "장기 이평 근처", "6개월 180일선, 1년 300일선 근처 +1. 완전 이탈 무효"),
    ("ma_cross_20_60", "20·60일선 교차", "20일선이 하방으로 60일선 아래를 막 깨면 −1. 4봉 지나면 무효"),
    ("chg1_50", "1개월 상승 70%", "30일 전 대비 70% 이상 오르면 −1"),
    ("chg1_down10", "1개월 하락 10%", "30일 전 대비 10% 이상 20% 미만 떨어지면 +1"),
    ("chg1_down20", "1개월 하락 20%", "30일 전 대비 20% 이상 30% 미만 떨어지면 +2"),
    ("chg1_down30", "1개월 하락 30%", "30일 전 대비 30% 이상 떨어지면 +3"),
    ("chg6_300", "6개월 상승 300%", "6개월 전 대비 300% 이상 600% 미만 −1. 횡보·하락이면 무효"),
    ("chg6_600", "6개월 상승 600%", "6개월 전 대비 600% 이상 800% 미만 −2. 횡보·하락이면 무효"),
    ("chg6_800", "6개월 상승 800%", "6개월 전 대비 800% 이상 −3"),
    ("bar_spike_20", "단기 급상승", "직전 봉 대비 1봉에 20% 이상 오르면 −1"),
    ("rr_penalty", "손익비 부족", "손익비 1.2 미만이고 점수가 높을 때"),
    ("option_wall", "옵션 월", "기존 매수/매도 이후 추가. 만기 14일 안 콜·풋월. 매도 때 근처 콜두껍/풋얇 −1, 반대 +1. 매수 때 근처 풋얇+콜두껍 −1"),
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
})
_HIDDEN_WEIGHT_LABELS = (
    "1개월 상승선 근접",
    "스윙 저점 근접",
    "스윙 고점 근접",
    "양쪽 추세선 상승·단기하락",
    "둘 다 하락·하락선 돌파",
    "둘 다 상승·1개월 상승선 상방",
    "상승 추세선 이탈",
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
    "base": 15,
    "chg1_50": -1,
    "chg1_down1": 0,
    "chg1_down10": 1,
    "chg1_down20": 2,
    "chg1_down30": 3,
    "chg1_down40": 0,
    "chg1_down50": 0,
    "chg6_50": 0,
    "chg6_200": 0,
    "chg6_300": -1,
    "chg6_500": 0,
    "chg6_600": -2,
    "chg6_800": -3,
    "swing_low_near": 0,
    "swing_high_near": 0,
    "trendline_up_1m_down": 0,
    "trendline_dir_down": -1,
    "trendline_dir_down_break": 0,
    "trendline_dir_down_upnear": 1,
    "trendline_1m_up": 1,
    "trendline_1m_up_both_up": 1,
    "bar_spike_20": -1,
    "up_line_near": 1,
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

from .analysis import Analysis, Level, classify_trend, find_swings, _line_through


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
    if df is None or getattr(df, "empty", True) or len(df) < 9:
        return None
    _highs, lows = find_swings(df)
    if len(lows) < 2:
        return None
    return _line_through((lows[-2][2], lows[-2][1]), (lows[-1][2], lows[-1][1]), len(df) - 1)


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


def recommend(
    an: Analysis,
    six_month_chg: float | None = None,
    lookback_days: int | None = None,
    rule: dict | None = None,
    option_walls: dict | None = None,
    market: str | None = None,
    ticker: str | None = None,
    df_1m=None,
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
    near = max(atr * 0.55, price * 0.010)

    nsup = an.supports[0] if an.supports else None
    nres = an.resistances[0] if an.resistances else None

    score = 0
    reasons: list[str] = []
    score_rows: list[dict] = []

    def add(item: str, detail: str, pts: int) -> None:
        nonlocal score
        score += pts
        label = f"+{pts}" if pts > 0 else str(pts)
        score_rows.append({"항목": item, "내용": detail, "점수": label})
        reasons.append(f"{item}: {detail} ({label})")

    base = wp("base")
    add("기본", "중립 시작점", base)

    trend_pts = abs(wp("trend"))
    if an.trend == "up":
        add("추세", f"상승 · 고점 추격 감점. {an.price_label} {_fmt(price)}", -trend_pts)
    elif an.trend == "down":
        add("추세", f"하락 · 눌림 가점. {an.price_label} {_fmt(price)}", trend_pts)
    else:
        add("추세", f"횡보. {an.price_label} {_fmt(price)}", 0)

    up_line = an.up_line
    down_line = an.down_line
    y_dn = _line_y_at(down_line, float(down_line[2])) if down_line else None
    y_up = _line_y_at(up_line, float(up_line[2])) if up_line else None
    near_dn = y_dn is not None and abs(price - y_dn) <= near and price <= y_dn
    broke_dn = y_dn is not None and price > y_dn
    near_up = y_up is not None and abs(price - y_up) <= near and price >= y_up
    broke_up = y_up is not None and price < y_up
    up_dir = _line_dir(up_line)
    down_dir = _line_dir(down_line)
    both_down = up_dir == "down" and down_dir == "down"

    if not down_line:
        add("하락 추세선 근접", "하락선 없음", 0)
    elif y_dn is None:
        add("하락 추세선 근접", "하락선 위치를 계산하지 못함", 0)
    elif broke_dn:
        add("하락 추세선 근접", f"하락선 {_fmt(y_dn)} 상향 돌파라 무효", 0)
    elif near_dn:
        add(
            "하락 추세선 근접",
            f"하락선 {_fmt(y_dn)} 근처 (이격 {_fmt(abs(price - y_dn))})",
            wp("down_line_near"),
        )
    else:
        add(
            "하락 추세선 근접",
            f"하락선 {_fmt(y_dn)} 과 이격 {_fmt(abs(price - y_dn))}",
            0,
        )

    if not up_line:
        add("상승 추세선 근접", "상승선 없음", 0)
    elif y_up is None:
        add("상승 추세선 근접", "상승선 위치를 계산하지 못함", 0)
    elif broke_up:
        add("상승 추세선 근접", f"상승선 {_fmt(y_up)} 이탈이라 무효", 0)
    elif near_up:
        add(
            "상승 추세선 근접",
            f"상승선 {_fmt(y_up)} 근처 (이격 {_fmt(abs(price - y_up))})",
            wp("up_line_near"),
        )
    else:
        add(
            "상승 추세선 근접",
            f"상승선 {_fmt(y_up)} 과 이격 {_fmt(abs(price - y_up))}",
            0,
        )

    if both_down and near_dn:
        add("추세선 방향", "둘 다 하락 · 하락선 근처", wp("trendline_dir_down"))
    elif both_down and broke_dn:
        add("추세선 방향", "둘 다 하락 · 하락선 돌파라 근접 무효", 0)
    elif both_down and near_up:
        add("추세선 방향", "둘 다 하락 · 상승선 근처", wp("trendline_dir_down_upnear"))
    elif both_down and broke_up:
        add("추세선 방향", "둘 다 하락 · 상승선 이탈이라 근접 무효", 0)
    elif both_down:
        add("추세선 방향", "둘 다 하락 · 선 근처 아님", 0)
    elif not up_line or not down_line:
        add("추세선 방향", "상승선 또는 하락선 없음", 0)
    else:
        add("추세선 방향", f"상승선 {up_dir} · 하락선 {down_dir}", 0)

    w1 = df_1m if df_1m is not None and not getattr(df_1m, "empty", True) else _window_df(an.df, an.as_of, 30)
    dir_1m = _line_dir(_up_line_from_df(w1))
    both_up = up_dir == "up" and down_dir == "up"
    if both_down and dir_1m == "up":
        add("1개월 추세선", "둘 다 하락 · 1개월 상승선 상방", wp("trendline_1m_up"))
    elif both_up and dir_1m == "down":
        add("1개월 추세선", "둘 다 상승 · 1개월 상승선 하방", wp("trendline_1m_up_both_up"))
    elif both_down:
        add("1개월 추세선", f"둘 다 하락 · 1개월 상승선 {dir_1m or '없음'}이라 무효", 0)
    elif both_up:
        add("1개월 추세선", f"둘 다 상승 · 1개월 상승선 {dir_1m or '없음'}이라 무효", 0)
    else:
        add("1개월 추세선", "둘 다 상승/하락이 아니라 해당 없음", 0)

    if nsup:
        dist_s = price - nsup.price
        pct_s = dist_s / price * 100
        sup_str = float(nsup.strength)
        if dist_s <= near:
            if sup_str >= 4:
                add(
                    "지지",
                    f"근접 {_fmt(nsup.price)} ({nsup.note}, 강도 {sup_str:.1f}, 이격 {pct_s:.2f}%)",
                    wp("support_near"),
                )
            else:
                add(
                    "지지",
                    f"근접 {_fmt(nsup.price)} ({nsup.note}, 강도 {sup_str:.1f} · 4 미만 가점 없음, 이격 {pct_s:.2f}%)",
                    0,
                )
        else:
            add("지지", f"{_fmt(nsup.price)} 까지 {pct_s:.2f}% (강도 {sup_str:.1f})", 0)
        if price < nsup.price - atr * 0.15:
            nxt = an.supports[1] if an.supports and len(an.supports) > 1 else None
            if nxt is None or nxt.price < price - near:
                gap = "다음 지지 없음" if nxt is None else f"다음 지지 {_fmt(nxt.price)}"
                add("지지 이탈", f"현재가 < {_fmt(nsup.price)} · {gap}", wp("support_break"))
            else:
                add("지지 이탈", f"다음 지지 {_fmt(nxt.price)} 가 가까워 감점 없음", 0)
    else:
        add("지지", "없음", 0)

    if nres:
        dist_r = nres.price - price
        pct_r = dist_r / price * 100
        res_str = float(nres.strength)
        if dist_r <= near:
            if res_str >= 4:
                add(
                    "저항",
                    f"근접 {_fmt(nres.price)} ({nres.note}, 강도 {res_str:.1f}, 이격 {pct_r:.2f}%)",
                    wp("resist_near"),
                )
            else:
                add(
                    "저항",
                    f"근접 {_fmt(nres.price)} ({nres.note}, 강도 {res_str:.1f} · 4 미만 감점 없음, 이격 {pct_r:.2f}%)",
                    0,
                )
        else:
            add("저항", f"{_fmt(nres.price)} 까지 {pct_r:.2f}% (강도 {res_str:.1f})", 0)
    else:
        add("저항", "없음", 0)

    poc_pts = abs(wp("poc"))
    val_pts = abs(wp("val"))
    if abs(price - an.poc) <= near:
        add("POC", f"최대 매물 {_fmt(an.poc)} 근처 (이격 {_fmt(abs(price - an.poc))})", poc_pts)
    else:
        add("POC", f"최대 매물 {_fmt(an.poc)} 과 이격 {_fmt(abs(price - an.poc))} · 이탈 무효", 0)
    if price < an.val and an.trend == "up":
        add("VAL", f"하단 {_fmt(an.val)} 아래 · 상승 추세", val_pts)
    elif price < an.val:
        add("VAL", f"하단 {_fmt(an.val)} 아래 · {('하락' if an.trend == 'down' else '횡보')}이라 가점 없음", 0)
    else:
        add("VAL", f"하단 {_fmt(an.val)} (해당 없음)", 0)
    if price > an.vah and price - an.vah <= near:
        add("VAH", f"상단 {_fmt(an.vah)} 바로 위", wp("vah"))
    elif price > an.vah:
        add("VAH", f"상단 {_fmt(an.vah)} 위 · 돌파라 무효", 0)
    else:
        add("VAH", f"상단 {_fmt(an.vah)} (해당 없음)", 0)

    rsi_pts = abs(wp("rsi"))
    if an.rsi >= 70:
        add("RSI", f"{an.rsi:.1f} (70 이상)", -rsi_pts)
    elif an.rsi <= 35:
        add("RSI", f"{an.rsi:.1f} (35 이하)", rsi_pts)
    else:
        add("RSI", f"{an.rsi:.1f} 중립", 0)

    def _ma_near(name: str, level, pts_key: str) -> None:
        if level is None:
            add(name, f"{name} 없음", 0)
        elif abs(price - level) <= near:
            add(name, f"{name} {_fmt(level)} 근처 (이격 {_fmt(abs(price - level))})", wp(pts_key))
        else:
            add(name, f"{name} {_fmt(level)} 과 이격 {_fmt(abs(price - level))} · 이탈 무효", 0)

    _ma_near("MA20", an.ma20, "ma20")
    _ma_near("MA60", an.ma60, "ma60_near")
    ma_n = int(getattr(an, "ma_long_n", None) or 300)
    _ma_near(f"{ma_n}일선", getattr(an, "ma200", None), "ma200_near")

    if _ma20_cross_below_ma60(an.df, 4):
        add("20·60일선 교차", "20일선이 하방으로 60일선 아래를 막 깸", wp("ma_cross_20_60"))
    else:
        add("20·60일선 교차", "최근 4봉 안 교차 아님", 0)

    chg = _one_month_change(an, price)
    if chg is None:
        add("1개월 상승률", "계산 불가", 0)
    else:
        chg_pct = chg * 100.0
        if chg_pct >= 70 - 1e-9:
            add("1개월 상승률", f"{chg_pct:.1f}% (70% 이상 상승)", wp("chg1_50"))
        elif chg_pct <= -30 + 1e-9:
            add("1개월 하락률", f"{chg_pct:.1f}% (30% 이상 하락)", wp("chg1_down30"))
        elif chg_pct <= -20 + 1e-9:
            add("1개월 하락률", f"{chg_pct:.1f}% (20% 이상 30% 미만 하락)", wp("chg1_down20"))
        elif chg_pct <= -10 + 1e-9:
            add("1개월 하락률", f"{chg_pct:.1f}% (10% 이상 20% 미만 하락)", wp("chg1_down10"))
        else:
            add("1개월 상승률", f"{chg_pct:.1f}%", 0)

    bar_ret = _one_bar_return(an, price)
    if bar_ret is None:
        add("단기 급상승", "직전 봉 없음", 0)
    elif bar_ret >= 0.20 - 1e-9:
        add("단기 급상승", f"1봉 {bar_ret * 100:.1f}% (20% 이상)", wp("bar_spike_20"))
    else:
        add("단기 급상승", f"1봉 {bar_ret * 100:.1f}%", 0)

    chg6 = six_month_chg
    if chg6 is None:
        chg6 = period_return(an.df, an.as_of, price, 180)
    if chg6 is None:
        add("6개월 상승률", "6개월 전 가격 없음", 0)
    elif chg6 >= 8.0:
        add("6개월 상승률", f"{chg6 * 100:.1f}% (800% 이상)", wp("chg6_800"))
    elif chg6 >= 6.0:
        if an.trend != "up":
            kind = "하락" if an.trend == "down" else "횡보"
            add("6개월 상승률", f"{chg6 * 100:.1f}% (600% 이상) · {kind}라 무효", 0)
        else:
            add("6개월 상승률", f"{chg6 * 100:.1f}% (600% 이상 800% 미만)", wp("chg6_600"))
    elif chg6 >= 3.0:
        if an.trend != "up":
            kind = "하락" if an.trend == "down" else "횡보"
            add("6개월 상승률", f"{chg6 * 100:.1f}% (300% 이상) · {kind}라 무효", 0)
        else:
            add("6개월 상승률", f"{chg6 * 100:.1f}% (300% 이상 600% 미만)", wp("chg6_300"))
    else:
        add("6개월 상승률", f"{chg6 * 100:.1f}%", 0)

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

    if rr is not None and rr < 1.2 and score >= base + 2:
        add("손익비", f"{rr:.2f} · 저항까지 여유 부족", wp("rr_penalty"))
    elif rr is not None:
        add("손익비", f"{rr:.2f} (목표 {_fmt(target)} / 손절 {_fmt(stop)})", 0)
    else:
        add("손익비", "목표·손절을 잡지 못함", 0)

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
        add("옵션 월", opt_detail, opt_pts)
        score = max(0, score)
        option_applied = True

    lo, hi = SCORE_LO, SCORE_HI
    score_pct = score_to_pct(score)
    action = _action_from_pct(score_pct, cuts)
    reasons.append(
        f"합산 {score_pct}% ({score}점, 범위 {lo}~{hi}) · "
        f"{cut_kind} 컷 · 약한매수 {buy_weak}%↑ / 매수 {buy_mid}%↑ / 강한매수 {buy_strong}%↑ · "
        f"약한매도 {sell_weak}%↓ / 매도 {sell_mid}%↓ / 강한매도 {sell_strong}%↓"
        f" · 규칙 v{SIGNAL_RULE_VERSION}"
    )
    if action != action_base:
        reasons.append(f"기존 규칙 {action_base} → 옵션 월 반영 후 {action}")

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

    if action != action_base:
        summary = f"기존 규칙 {action_base}에 옵션 월을 반영해 {action}로 조정했습니다. " + summary
    if action in ("매수", "약한 매수", "강한 매수"):
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
    )
