"""규칙 기반 매수/매도/홀딩. 지정일 현재가와 지지·저항·매물대·추세만 사용."""

from __future__ import annotations

from dataclasses import dataclass, field

SIGNAL_RULE_VERSION = 2

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


def _fmt(price: float) -> str:
    if price >= 1000:
        return f"{price:,.0f}"
    if price >= 1:
        return f"{price:,.2f}"
    return f"{price:.6f}"


def recommend(an: Analysis, htf_6m_action: str | None = None) -> Signal:
    price = an.price
    atr = an.atr if an.atr and an.atr > 0 else price * 0.02
    near = max(atr * 0.45, price * 0.008)

    nsup = an.supports[0] if an.supports else None
    nres = an.resistances[0] if an.resistances else None

    score = 0
    reasons: list[str] = []

    trend_kr = {"up": "상승", "down": "하락", "sideways": "횡보"}.get(an.trend, an.trend)
    if an.trend == "up":
        score += 2
        reasons.append(f"추세: 상승 (스윙 구조/이동평균). {an.price_label} {_fmt(price)}")
    elif an.trend == "down":
        score -= 2
        reasons.append(f"추세: 하락. {an.price_label} {_fmt(price)}")
    else:
        reasons.append(f"추세: 횡보. {an.price_label} {_fmt(price)}")

    if htf_6m_action == "매수":
        score += 1
        reasons.append("6개월 일봉 평가: 매수 (+1)")
    elif htf_6m_action == "매도":
        score -= 1
        reasons.append("6개월 일봉 평가: 매도 (-1)")
    elif htf_6m_action == "홀딩":
        reasons.append("6개월 일봉 평가: 홀딩 (0)")

    if nsup:
        dist_s = price - nsup.price
        pct_s = dist_s / price * 100
        if dist_s <= near:
            score += 2
            reasons.append(
                f"지지 근접: {_fmt(nsup.price)} ({nsup.note}, 이격 {pct_s:.2f}%)"
            )
        else:
            reasons.append(f"최근 지지 {_fmt(nsup.price)} 까지 {pct_s:.2f}%")
        if price < nsup.price - atr * 0.15:
            score -= 2
            reasons.append("지지선 아래로 종가 — 지지 이탈 가능성")

    if nres:
        dist_r = nres.price - price
        pct_r = dist_r / price * 100
        if dist_r <= near:
            score -= 2
            reasons.append(
                f"저항 근접: {_fmt(nres.price)} ({nres.note}, 이격 {pct_r:.2f}%)"
            )
        else:
            reasons.append(f"최근 저항 {_fmt(nres.price)} 까지 {pct_r:.2f}%")

    # 매물대 / 밸류 영역
    if abs(price - an.poc) <= near:
        reasons.append(f"POC(최대 매물) {_fmt(an.poc)} 부근 — 방향 결정 구간")
        if an.trend == "up":
            score += 1
        elif an.trend == "down":
            score -= 1
    elif price > an.vah:
        reasons.append(f"밸류 상단(VAH {_fmt(an.vah)}) 위 — 매물 소화 후 연장 또는 회귀")
        if an.trend == "down":
            score -= 1
    elif price < an.val:
        reasons.append(f"밸류 하단(VAL {_fmt(an.val)}) 아래 — 저가 매물 이탈 또는 과매도")
        if an.trend == "down":
            score -= 1
        elif an.trend == "up":
            score += 1
    else:
        reasons.append(f"밸류 영역 내부 (VAL {_fmt(an.val)} ~ VAH {_fmt(an.vah)})")

    if an.rsi >= 70:
        score -= 1
        reasons.append(f"RSI {an.rsi:.1f} 과매수")
    elif an.rsi <= 30:
        score += 1
        reasons.append(f"RSI {an.rsi:.1f} 과매도")
    else:
        reasons.append(f"RSI {an.rsi:.1f} 중립")

    if an.ma20 is not None:
        if price > an.ma20:
            reasons.append(f"{an.price_label} > MA20 ({_fmt(an.ma20)})")
        else:
            reasons.append(f"{an.price_label} < MA20 ({_fmt(an.ma20)})")
            if an.trend != "up":
                score -= 1

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

    # 손익비가 나쁘면 매수 점수 하향
    if rr is not None and rr < 1.2 and score >= 2:
        score -= 1
        reasons.append(f"손익비 {rr:.2f}로 낮음 — 저항까지 여유가 부족")
    elif rr is not None:
        reasons.append(f"예상 손익비 {rr:.2f} (목표 {_fmt(target)} / 손절 {_fmt(stop)})")

    bar_count = 0 if an.df is None else len(an.df)
    buy_cut = 5 if bar_count < 50 else 4
    sell_cut = -5 if bar_count < 50 else -4
    if bar_count < 50:
        reasons.append(f"표본 {bar_count}봉으로 짧음 — 매수/매도 기준을 더 엄격히 적용")

    if score >= buy_cut:
        action = "매수"
        summary = "상승 구조에서 지지·매물대 부근이라 매수 쪽으로 기울었습니다."
    elif score <= sell_cut:
        action = "매도"
        summary = "하락 구조이거나 저항·상단 매물대에 붙어 매도/관망 쪽으로 기울었습니다."
    else:
        action = "홀딩"
        summary = "지지와 저항 사이이거나 신호가 엇갈려 관망(홀딩)이 낫습니다."
        if bar_count < 50:
            summary = "조회 기간이 짧아 신호가 쉽게 바뀝니다. 지금은 관망(홀딩)이 낫습니다."

    confidence = int(max(35, min(90, 50 + abs(score) * 12)))
    if action == "홀딩":
        confidence = int(max(30, 55 - abs(score) * 4))

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
    )
