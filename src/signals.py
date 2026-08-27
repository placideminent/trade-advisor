"""규칙 기반 매수/매도/홀딩. 지정일 현재가와 지지·저항·매물대·추세만 사용."""

from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

SIGNAL_RULE_VERSION = 22
# 중립 기준점. 이보다 높으면 매수, 낮으면 매도.
SCORE_BASE = 10
# 합산 %는 조회 기간과 상관없이 같은 눈금(이론상 최저~최고)을 쓴다.
SCORE_LO = SCORE_BASE - 15  # -5
SCORE_HI = SCORE_BASE + 9  # 19

DEFAULT_WEIGHTS = {
    "base": 10,
    "trend": 2,
    "trend_1m": 1,
    "trendline_cross": 1,
    "chg6_10": 1,
    "chg6_50": -1,
    "chg6_100": -2,
    "support_near": 2,
    "support_break": -2,
    "resist_near": -2,
    "resist_strong": -1,
    "vol_sup_air": -1,
    "vol_sup_room": 1,
    "poc": 1,
    "val": 1,
    "rsi": 1,
    "ma20": -1,
    "chg1_50": -3,
    "chg1_100": -4,
    "rr_penalty": -1,
}

DEFAULT_CUTS = {
    "buy_weak": 65,
    "buy_mid": 70,
    "buy_strong": 75,
    "sell_weak": 27,
    "sell_mid": 24,
    "sell_strong": 21,
}

WEIGHT_FIELDS = [
    ("base", "기본", "중립 시작점"),
    ("trend", "추세", "가점/감점 크기. 1·2개월은 상승 +, 3개월 이상은 하락 +"),
    ("trend_1m", "1개월 추세", "3개월 이상 조회 시. 1개월 조회 추세 상승 +, 하락 −"),
    ("trendline_cross", "추세선 돌파", "상승선이 하락선 위이거나, 상승선은 상향·하락선은 하향일 때"),
    ("chg6_10", "6개월 10~30%", "6개월 전 대비 10% 이상 30% 미만"),
    ("chg6_50", "6개월 50%+", "6개월 전 대비 50% 이상 100% 미만"),
    ("chg6_100", "6개월 100%+", "6개월 전 대비 100% 이상"),
    ("support_near", "지지 근접", "근접 지지. 강도 1 이하면 가점 없음"),
    ("support_break", "지지 이탈", "지지 아래로 이탈"),
    ("resist_near", "저항 근접", "저항 바로 아래/근처"),
    ("resist_strong", "강한 저항", "근접 저항 강도 2 이상이고 근접 감점이 없을 때"),
    ("vol_sup_air", "약한 매물대·아래 공백", "지지 매물대 강도 1 미만이고 다음 지지가 10% 이상 아래"),
    ("vol_sup_room", "약한 매물대·위 여유", "지지 매물대 강도 1 미만이고 다음 저항이 10% 이상 위"),
    ("poc", "POC", "최대 매물 부근. 상승 +, 하락 −"),
    ("val", "VAL", "밸류 하단 아래. 상승 +, 하락 −"),
    ("rsi", "RSI", "과매도 +, 과매수 −"),
    ("ma20", "MA20 아래", "현재가 < MA20 (상승 추세면 0)"),
    ("chg1_50", "1개월 상승 50%", "1개월 상승 50% 이상"),
    ("chg1_100", "1개월 상승 100%", "1개월 상승 100% 이상"),
    ("rr_penalty", "손익비 부족", "손익비 1.2 미만이고 점수가 높을 때"),
]

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
    cuts = dict(DEFAULT_CUTS)
    if isinstance(rule, dict):
        weights.update(rule.get("weights") or {})
        cuts.update(rule.get("cuts") or {})
    return {"weights": weights, "cuts": cuts}

from .analysis import Analysis, Level


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
    """as_of 기준 days일 전 종가 대비 현재가 수익률."""
    if df is None or getattr(df, "empty", True) or price is None or price <= 0:
        return None
    start = pd.Timestamp(as_of) - pd.Timedelta(days=days)
    past = df.loc[df.index <= start]
    if past.empty:
        return None
    base = float(past["close"].iloc[-1])
    if base <= 0:
        return None
    return float(price) / base - 1.0


def recommend(
    an: Analysis,
    six_month_chg: float | None = None,
    lookback_days: int | None = None,
    trend_1m: str | None = None,
    action_1m: str | None = None,
    rule: dict | None = None,
) -> Signal:
    cfg = merge_rule(rule)
    w = cfg["weights"]
    cuts = cfg["cuts"]

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

    if lookback_days is not None and lookback_days >= 90:
        t1 = abs(wp("trend_1m"))
        if trend_1m == "up":
            add("1개월 추세", "1개월 조회 기준 상승", t1)
        elif trend_1m == "down":
            add("1개월 추세", "1개월 조회 기준 하락", -t1)
        elif trend_1m == "sideways":
            add("1개월 추세", "1개월 조회 기준 횡보", 0)
        else:
            add("1개월 추세", "1개월 조회 추세를 계산하지 못함", 0)

    up_line = an.up_line
    down_line = an.down_line
    if up_line and down_line:
        x0u, y0u, x1u, y1u = (float(v) for v in up_line)
        x0d, y0d, x1d, y1d = (float(v) for v in down_line)
        slope_up = (y1u - y0u) / (x1u - x0u) if x1u != x0u else 0.0
        slope_down = (y1d - y0d) / (x1d - x0d) if x1d != x0d else 0.0
        above = y1u > y1d
        aligned = slope_up > 0 and slope_down < 0
        if above or aligned:
            why = []
            if above:
                why.append(f"상승선 {_fmt(y1u)} > 하락선 {_fmt(y1d)}")
            if aligned:
                why.append("상승선 상향 · 하락선 하향")
            add("추세선 돌파", " · ".join(why), wp("trendline_cross"))
        else:
            add("추세선 돌파", f"상승선 {_fmt(y1u)} ≤ 하락선 {_fmt(y1d)} · 기울기 미충족", 0)
    else:
        add("추세선 돌파", "상승선 또는 하락선 없음", 0)

    chg6 = six_month_chg
    if chg6 is None:
        chg6 = period_return(an.df, an.as_of, price, 180)
    if chg6 is None:
        add("6개월 상승률", "6개월 전 가격 없음", 0)
    elif chg6 >= 1.0:
        add("6개월 상승률", f"{chg6 * 100:.1f}% (100% 이상)", wp("chg6_100"))
    elif chg6 >= 0.50:
        add("6개월 상승률", f"{chg6 * 100:.1f}% (50% 이상 100% 미만)", wp("chg6_50"))
    elif chg6 >= 0.30:
        add("6개월 상승률", f"{chg6 * 100:.1f}% (30% 이상 50% 미만)", 0)
    elif chg6 >= 0.10:
        add("6개월 상승률", f"{chg6 * 100:.1f}% (10% 이상 30% 미만)", wp("chg6_10"))
    else:
        add("6개월 상승률", f"{chg6 * 100:.1f}%", 0)

    if nsup:
        dist_s = price - nsup.price
        pct_s = dist_s / price * 100
        sup_str = float(nsup.strength)
        if dist_s <= near:
            if sup_str <= 1:
                add(
                    "지지",
                    f"근접 {_fmt(nsup.price)} ({nsup.note}, 강도 {sup_str:.1f} · 약해 가점 없음, 이격 {pct_s:.2f}%)",
                    0,
                )
            else:
                add(
                    "지지",
                    f"근접 {_fmt(nsup.price)} ({nsup.note}, 강도 {sup_str:.1f}, 이격 {pct_s:.2f}%)",
                    wp("support_near"),
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
            add(
                "저항",
                f"근접 {_fmt(nres.price)} ({nres.note}, 강도 {res_str:.1f}, 이격 {pct_r:.2f}%)",
                wp("resist_near"),
            )
        elif res_str >= 2:
            add(
                "저항",
                f"{_fmt(nres.price)} 까지 {pct_r:.2f}% · 강도 {res_str:.1f} (2 이상, 근접 감점 없어도 적용)",
                wp("resist_strong"),
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
            gap_res = ((nres.price - price) / price) if nres and price else None
            if next_sup and gap_sup is not None and gap_sup >= 0.10:
                add(
                    "지지 매물대",
                    f"강도 {vs_str:.2f} · 다음 지지 {_fmt(next_sup.price)} 까지 {gap_sup * 100:.1f}%",
                    wp("vol_sup_air"),
                )
            elif nres and gap_res is not None and gap_res >= 0.10:
                add(
                    "지지 매물대",
                    f"강도 {vs_str:.2f} · 다음 저항 {_fmt(nres.price)} 까지 {gap_res * 100:.1f}%",
                    wp("vol_sup_room"),
                )
            else:
                add(
                    "지지 매물대",
                    f"강도 {vs_str:.2f} · 다음 지지/저항 이격 10% 미만이거나 저항 없음",
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
        add("VAH", f"상단 {_fmt(an.vah)} (강세 구간 정상 · 감점 없음)", 0)
    elif price > an.vah:
        add("POC", f"최대 매물 {_fmt(an.poc)} · 현재가가 멀리 있음", 0)
        add("VAL", f"하단 {_fmt(an.val)} · 현재가가 VAL 위", 0)
        add("VAH", f"상단 {_fmt(an.vah)} 위 (강세 구간 정상 · 감점 없음)", 0)
    elif price < an.val:
        add("POC", f"최대 매물 {_fmt(an.poc)} · 현재가가 멀리 있음", 0)
        if an.trend == "down":
            add("VAL", f"하단 {_fmt(an.val)} 아래 · 하락 추세", -val_pts)
        elif an.trend == "up":
            add("VAL", f"하단 {_fmt(an.val)} 아래 · 상승 추세", val_pts)
        else:
            add("VAL", f"하단 {_fmt(an.val)} 아래 · 횡보", 0)
        add("VAH", f"상단 {_fmt(an.vah)} (감점 없음)", 0)
    else:
        add("POC", f"최대 매물 {_fmt(an.poc)} · 밸류 구간 내부", 0)
        add("VAL", f"하단 {_fmt(an.val)} ~ 현재가 사이", 0)
        add("VAH", f"상단 {_fmt(an.vah)} (감점 없음)", 0)

    rsi_pts = abs(wp("rsi"))
    if an.rsi >= 70:
        add("RSI", f"{an.rsi:.1f} 과매수", -rsi_pts)
    elif an.rsi <= 30:
        add("RSI", f"{an.rsi:.1f} 과매도", rsi_pts)
    else:
        add("RSI", f"{an.rsi:.1f} 중립", 0)

    if an.ma20 is None:
        add("MA20", "이동평균 없음", 0)
    elif price > an.ma20:
        add("MA20", f"{an.price_label} > MA20 ({_fmt(an.ma20)})", 0)
    elif an.trend != "up":
        add("MA20", f"{an.price_label} < MA20 ({_fmt(an.ma20)})", wp("ma20"))
    else:
        add("MA20", f"{an.price_label} < MA20 ({_fmt(an.ma20)}) · 상승 추세라 감점 없음", 0)

    chg = _one_month_change(an, price)
    if chg is None:
        add("1개월 상승률", "계산 불가", 0)
    elif chg >= 1.0:
        add("1개월 상승률", f"{chg * 100:.1f}% (100% 이상)", wp("chg1_100"))
    elif chg >= 0.5:
        add("1개월 상승률", f"{chg * 100:.1f}% (50% 이상)", wp("chg1_50"))
    else:
        add("1개월 상승률", f"{chg * 100:.1f}%", 0)

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
    lo, hi = SCORE_LO, SCORE_HI
    score_pct = score_to_pct(score)
    reasons.append(
        f"합산 {score_pct}% ({score}점, 범위 {lo}~{hi}) · "
        f"약한매수 {buy_weak}%↑ / 매수 {buy_mid}%↑ / 강한매수 {buy_strong}%↑ · "
        f"약한매도 {sell_weak}%↓ / 매도 {sell_mid}%↓ / 강한매도 {sell_strong}%↓"
    )

    if score_pct >= buy_strong:
        action = "강한 매수"
        summary = "합산이 높아 강한 매수 구간입니다."
    elif score_pct >= buy_mid:
        action = "매수"
        summary = "매수 구간에 들어왔습니다."
    elif score_pct >= buy_weak:
        action = "약한 매수"
        summary = "매수 쪽으로 기울었지만 강도는 약한 구간입니다."
    elif score_pct <= sell_strong:
        action = "강한 매도"
        summary = "합산이 낮아 강한 매도 구간입니다."
    elif score_pct <= sell_mid:
        action = "매도"
        summary = "매도 구간에 들어왔습니다."
    elif score_pct <= sell_weak:
        action = "약한 매도"
        summary = "매도 쪽으로 기울었지만 강도는 약한 구간입니다."
    else:
        action = "홀딩"
        summary = "지지와 저항 사이이거나 신호가 엇갈려 관망(홀딩)이 낫습니다."
        if bar_count < 50:
            summary = "조회 기간이 짧아 신호가 쉽게 바뀝니다. 지금은 관망(홀딩)이 낫습니다."

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
    )
