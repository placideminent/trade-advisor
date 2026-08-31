"""규칙 기반 매수/매도/홀딩. 지정일 현재가와 지지·저항·매물대·추세만 사용."""

from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

SIGNAL_RULE_VERSION = 68
# 중립 기준점. 이보다 높으면 매수, 낮으면 매도.
SCORE_BASE = 10
# 합산 %는 조회 기간과 상관없이 같은 눈금(이론상 최저~최고)을 쓴다.
SCORE_LO = SCORE_BASE - 15  # -5
SCORE_HI = SCORE_BASE + 9  # 19

DEFAULT_WEIGHTS = {
    "base": 10,
    "trend": 1,
    "trend_1m": 1,
    "down_line_near": -1,
    "trendline_dir_down": -1,
    "up_line_near": 1,
    "up_line_break": -1,
    "support_near": 1,
    "support_break": -2,
    "resist_near": -1,
    "vol_sup_air": -1,
    "poc": 1,
    "val": 1,
    "vah": -1,
    "rsi": 1,
    "ma20": 1,
    "ma60_near": 1,
    "ma200_near": 1,
    "chg1_50": -1,
    "chg1_down1": 1,
    "chg1_down20": 2,
    "chg1_down40": 3,
    "chg6_50": -1,
    "chg6_200": -2,
    "chg6_800": -3,
    "rr_penalty": -1,
    "option_wall": 1,
}

DEFAULT_CUTS_STOCK = {
    "buy_weak": 65,
    "buy_mid": 70,
    "buy_strong": 75,
    "sell_weak": 35,
    "sell_mid": 30,
    "sell_strong": 25,
}
DEFAULT_CUTS_CRYPTO = {
    "buy_weak": 70,
    "buy_mid": 75,
    "buy_strong": 79,
    "sell_weak": 45,
    "sell_mid": 40,
    "sell_strong": 30,
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
    ("base", "기본", "중립 시작점"),
    ("trend", "추세", "1·2개월은 상승 +, 3개월 이상은 하락 +"),
    ("trend_1m", "1개월 상승(장기)", "3개월 이상 조회에서 최근 1개월이 상승이면 +1"),
    ("down_line_near", "하락 추세선 근접", "현재가가 하락 추세선 근처이면 −1"),
    ("trendline_dir_down", "추세선 둘 다 하락", "상승선·하락선이 동시에 하락이면 −1"),
    ("up_line_near", "상승 추세선 근접", "현재가가 상승 추세선 근처이면 +1"),
    ("up_line_break", "상승 추세선 이탈", "완전 이탈 −1. 이탈 후 4봉이 지나면 무효"),
    ("support_near", "지지 근접", "근접하고 강도 4 이상일 때만 +1"),
    ("support_break", "지지 이탈", "지지 아래로 이탈"),
    ("resist_near", "저항 근접", "근접하고 강도 4 이상일 때만 −1"),
    ("vol_sup_air", "약한 매물대·아래 공백", "지지 매물대 강도 1 미만이고 다음 지지가 10% 이상 아래"),
    ("poc", "POC", "최대 매물 부근. 상승 +, 하락 −"),
    ("val", "VAL", "밸류 하단 아래. 상승만 +1, 하락은 0"),
    ("vah", "VAH", "밸류 상단 위이면 −1"),
    ("rsi", "RSI", "30 이하 +, 70 이상 −"),
    ("ma20", "MA20 아래", "현재가 < MA20. 상승 +1, 하락 −1"),
    ("ma60_near", "60일(봉)선 근처", "현재가가 60봉 이평 근처이면 +1"),
    ("ma200_near", "장기 이평 근처", "6개월은 180일선, 1년은 200일선 근처이면 +1"),
    ("chg1_50", "1개월 상승 30%", "30일 전 대비 30% 이상 오르면 −1"),
    ("chg1_down1", "1개월 하락 1%", "30일 전 대비 1% 이상 20% 미만 떨어지면 +1"),
    ("chg1_down20", "1개월 하락 20%", "30일 전 대비 20% 이상 40% 미만 떨어지면 +2"),
    ("chg1_down40", "1개월 하락 40%", "30일 전 대비 40% 이상 떨어지면 +3"),
    ("chg6_50", "6개월 상승 50%", "6개월 전 대비 50% 이상 200% 미만 −1 (모든 조회)"),
    ("chg6_200", "6개월 상승 200%", "6개월 전 대비 200% 이상 800% 미만 −2 (모든 조회)"),
    ("chg6_800", "6개월 상승 800%", "6개월 전 대비 800% 이상 −3 (모든 조회)"),
    ("rr_penalty", "손익비 부족", "손익비 1.2 미만이고 점수가 높을 때"),
    ("option_wall", "옵션 월", "기존 매수/매도 이후 추가. 만기 14일 안 콜·풋월. 매도 때 근처 콜두껍/풋얇 −1, 반대 +1. 매수 때 근처 풋얇+콜두껍 −1"),
]

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
    return {"weights": weights, "cuts": cuts, "cuts_crypto": cuts_crypto}

from .analysis import Analysis, Level, classify_trend


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
    lo, hi = SCORE_LO, SCORE_HI
    if hi <= lo:
        return 50
    pct = (score - lo) / (hi - lo) * 100
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
    **_unused,
) -> Signal:
    cfg = merge_rule(rule)
    w = cfg["weights"]
    crypto = str(market or "").upper() == "CRYPTO"
    cuts = dict(cfg["cuts_crypto"] if crypto else cfg["cuts"])
    cut_kind = "코인" if crypto else "주식"

    def wp(key: str) -> int:
        return int(w.get(key, DEFAULT_WEIGHTS[key]))

    price = an.price
    atr = an.atr if an.atr and an.atr > 0 else price * 0.02
    near = max(atr * 0.45, price * 0.008)

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

    # 1·2개월은 추세 추종(상승 +, 하락 −). 3개월 이상은 눌림 매수(하락 +, 상승 −).
    follow_trend = lookback_days is not None and lookback_days <= 60
    trend_pts = abs(wp("trend"))
    if follow_trend:
        if an.trend == "up":
            add("추세", f"상승 · 추세 추종 가점. {an.price_label} {_fmt(price)}", trend_pts)
        elif an.trend == "down":
            add("추세", f"하락 · 추세 추종 감점. {an.price_label} {_fmt(price)}", -trend_pts)
        else:
            add("추세", f"횡보. {an.price_label} {_fmt(price)}", 0)
    else:
        if an.trend == "up":
            add("추세", f"상승 · 고점 추격 매수 감점. {an.price_label} {_fmt(price)}", -trend_pts)
        elif an.trend == "down":
            add("추세", f"하락 · 눌림 매수 가점. {an.price_label} {_fmt(price)}", trend_pts)
        else:
            add("추세", f"횡보. {an.price_label} {_fmt(price)}", 0)
        t1m = "sideways"
        if an.df is not None and not getattr(an.df, "empty", True):
            start = pd.Timestamp(an.as_of) - pd.Timedelta(days=30)
            w1 = an.df.loc[an.df.index >= start]
            t1m = classify_trend(w1, price)
        if t1m == "up":
            add("추세", "최근 1개월 창에서 상승", abs(wp("trend_1m")))
        else:
            add("추세", f"최근 1개월 창에서 {('하락' if t1m == 'down' else '횡보')}", 0)

    up_line = an.up_line
    down_line = an.down_line
    if not down_line:
        add("하락 추세선 근접", "하락선 없음", 0)
    else:
        y_dn = _line_y_at(down_line, float(down_line[2]))
        if y_dn is None:
            add("하락 추세선 근접", "하락선 위치를 계산하지 못함", 0)
        elif abs(price - y_dn) <= near:
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

    up_dir = _line_dir(up_line)
    down_dir = _line_dir(down_line)
    if up_dir == "down" and down_dir == "down":
        add("추세선 방향", "상승선·하락선 둘 다 하락", wp("trendline_dir_down"))
    elif not up_line or not down_line:
        add("추세선 방향", "상승선 또는 하락선 없음", 0)
    else:
        add("추세선 방향", f"상승선 {up_dir} · 하락선 {down_dir}", 0)

    if not up_line:
        add("상승 추세선 근접", "상승선 없음", 0)
    else:
        y_up = _line_y_at(up_line, float(up_line[2]))
        if y_up is None:
            add("상승 추세선 근접", "상승선 위치를 계산하지 못함", 0)
        elif abs(price - y_up) <= near:
            add(
                "상승 추세선 근접",
                f"상승선 {_fmt(y_up)} 근처 (이격 {_fmt(abs(price - y_up))})",
                wp("up_line_near"),
            )
        elif price < y_up - near:
            n_below = _bars_below_line(an.df, up_line, near, last_price=price)
            if n_below <= 0:
                n_below = 1
            if n_below > UP_LINE_BREAK_BARS:
                add(
                    "상승 추세선 이탈",
                    f"상승선 {_fmt(y_up)} 아래 이탈 (이격 {_fmt(y_up - price)}) · {n_below}봉 지나 무효",
                    0,
                )
            else:
                add(
                    "상승 추세선 이탈",
                    f"상승선 {_fmt(y_up)} 아래로 완전 이탈 (이격 {_fmt(y_up - price)}, {n_below}봉)",
                    wp("up_line_break"),
                )
        else:
            add(
                "상승 추세선 근접",
                f"상승선 {_fmt(y_up)} 과 이격 {_fmt(abs(price - y_up))}",
                0,
            )

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
            add("지지 이탈", f"현재가 < {_fmt(nsup.price)}", wp("support_break"))
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

    vol_sups = [s for s in (an.supports or []) if "매물대" in (s.note or "")]
    if not vol_sups:
        add("지지 매물대", "매물대 지지 없음", 0)
    else:
        vs = min(vol_sups, key=lambda s: abs(price - s.price))
        vs_str = float(vs.strength)
        if vs_str >= 1:
            add("지지 매물대", f"{_fmt(vs.price)} 강도 {vs_str:.2f} (1 이상)", 0)
        else:
            lower = [s for s in (an.supports or []) if s.price < vs.price]
            next_sup = min(lower, key=lambda s: vs.price - s.price) if lower else None
            gap_sup = ((vs.price - next_sup.price) / price) if next_sup and price else None
            if next_sup and gap_sup is not None and gap_sup >= 0.10:
                add(
                    "지지 매물대",
                    f"강도 {vs_str:.2f} · 다음 지지 {_fmt(next_sup.price)} 까지 {gap_sup * 100:.1f}%",
                    wp("vol_sup_air"),
                )
            else:
                add(
                    "지지 매물대",
                    f"강도 {vs_str:.2f} · 다음 지지 이격 10% 미만이거나 아래 지지 없음",
                    0,
                )

    poc_pts = abs(wp("poc"))
    val_pts = abs(wp("val"))
    if abs(price - an.poc) <= near:
        if an.trend == "up":
            add("POC", f"최대 매물 {_fmt(an.poc)} 부근 · 상승 추세", poc_pts)
        elif an.trend == "down":
            add("POC", f"최대 매물 {_fmt(an.poc)} 부근 · 하락 추세", -poc_pts)
        else:
            add("POC", f"최대 매물 {_fmt(an.poc)} 부근 · 횡보", 0)
        add("VAL", f"하단 {_fmt(an.val)} (POC가 우선이라 미적용)", 0)
        add("VAH", f"상단 {_fmt(an.vah)} (POC가 우선이라 미적용)", 0)
    elif price > an.vah:
        add("POC", f"최대 매물 {_fmt(an.poc)} · 현재가가 멀리 있음", 0)
        add("VAL", f"하단 {_fmt(an.val)} · 현재가가 VAL 위", 0)
        add("VAH", f"상단 {_fmt(an.vah)} 위", wp("vah"))
    elif price < an.val:
        add("POC", f"최대 매물 {_fmt(an.poc)} · 현재가가 멀리 있음", 0)
        if an.trend == "up":
            add("VAL", f"하단 {_fmt(an.val)} 아래 · 상승 추세", val_pts)
        else:
            add("VAL", f"하단 {_fmt(an.val)} 아래 · {('하락' if an.trend == 'down' else '횡보')} 추세", 0)
        add("VAH", f"상단 {_fmt(an.vah)} (해당 없음)", 0)
    else:
        add("POC", f"최대 매물 {_fmt(an.poc)} · 밸류 구간 내부", 0)
        add("VAL", f"하단 {_fmt(an.val)} ~ 현재가 사이", 0)
        add("VAH", f"상단 {_fmt(an.vah)} (해당 없음)", 0)

    rsi_pts = abs(wp("rsi"))
    if an.rsi >= 70:
        add("RSI", f"{an.rsi:.1f} (70 이상)", -rsi_pts)
    elif an.rsi <= 30:
        add("RSI", f"{an.rsi:.1f} (30 이하)", rsi_pts)
    else:
        add("RSI", f"{an.rsi:.1f} 중립", 0)

    ma_pts = abs(wp("ma20"))
    if an.ma20 is None:
        add("MA20", "이동평균 없음", 0)
    elif price > an.ma20:
        add("MA20", f"{an.price_label} > MA20 ({_fmt(an.ma20)})", 0)
    elif an.trend == "up":
        add("MA20", f"{an.price_label} < MA20 ({_fmt(an.ma20)}) · 상승 추세", ma_pts)
    elif an.trend == "down":
        add("MA20", f"{an.price_label} < MA20 ({_fmt(an.ma20)}) · 하락 추세", -ma_pts)
    else:
        add("MA20", f"{an.price_label} < MA20 ({_fmt(an.ma20)}) · 횡보", 0)

    if getattr(an, "ma60", None) is None:
        add("60일선", "60봉 이평 없음 (봉 60개 미만)", 0)
    elif abs(price - an.ma60) <= near:
        add("60일선", f"60일(봉)선 {_fmt(an.ma60)} 근처 (이격 {_fmt(abs(price - an.ma60))})", wp("ma60_near"))
    else:
        add("60일선", f"60일(봉)선 {_fmt(an.ma60)} 과 이격 {_fmt(abs(price - an.ma60))}", 0)

    ma_n = int(getattr(an, "ma_long_n", None) or 200)
    ma_name = f"{ma_n}일선"
    if getattr(an, "ma200", None) is None:
        add(ma_name, f"{ma_name} 없음 (일봉 {ma_n}개 미만)", 0)
    elif abs(price - an.ma200) <= near:
        add(ma_name, f"{ma_name} {_fmt(an.ma200)} 근처 (이격 {_fmt(abs(price - an.ma200))})", wp("ma200_near"))
    else:
        add(ma_name, f"{ma_name} {_fmt(an.ma200)} 과 이격 {_fmt(abs(price - an.ma200))}", 0)

    chg = _one_month_change(an, price)
    if chg is None:
        add("1개월 상승률", "계산 불가", 0)
    else:
        chg_pct = chg * 100.0
        if chg_pct >= 30 - 1e-9:
            add("1개월 상승률", f"{chg_pct:.1f}% (30% 이상 상승)", wp("chg1_50"))
        elif chg_pct <= -40 + 1e-9:
            add("1개월 하락률", f"{chg_pct:.1f}% (40% 이상 하락)", wp("chg1_down40"))
        elif chg_pct <= -20 + 1e-9:
            add("1개월 하락률", f"{chg_pct:.1f}% (20% 이상 40% 미만 하락)", wp("chg1_down20"))
        elif chg_pct <= -1 + 1e-9:
            add("1개월 하락률", f"{chg_pct:.1f}% (1% 이상 20% 미만 하락)", wp("chg1_down1"))
        else:
            add("1개월 상승률", f"{chg_pct:.1f}%", 0)

    chg6 = six_month_chg
    if chg6 is None:
        chg6 = period_return(an.df, an.as_of, price, 180)
    if chg6 is None:
        add("6개월 상승률", "6개월 전 가격 없음", 0)
    elif chg6 >= 8.0:
        add("6개월 상승률", f"{chg6 * 100:.1f}% (800% 이상)", wp("chg6_800"))
    elif chg6 >= 2.0:
        add("6개월 상승률", f"{chg6 * 100:.1f}% (200% 이상 800% 미만)", wp("chg6_200"))
    elif chg6 >= 0.50:
        add("6개월 상승률", f"{chg6 * 100:.1f}% (50% 이상 200% 미만)", wp("chg6_50"))
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
        if follow_trend:
            summary = "단기 상승 추세 쪽으로 기울었습니다. " + summary
        else:
            summary = "하락·눌림 쪽에서 지지·하단 조건이 맞습니다. " + summary
    elif action in ("매도", "약한 매도", "강한 매도"):
        if follow_trend:
            summary = "단기 하락 추세 쪽으로 기울었습니다. " + summary
        else:
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
