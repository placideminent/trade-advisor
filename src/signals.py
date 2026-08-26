"""규칙 기반 매수/매도/홀딩. 지정일 현재가와 지지·저항·매물대·추세만 사용."""

from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

SIGNAL_RULE_VERSION = 7
# 중립 기준점. 이보다 높으면 매수, 낮으면 매도.
SCORE_BASE = 10
# 합산 %는 조회 기간과 상관없이 같은 눈금(이론상 최저~최고)을 쓴다.
SCORE_LO = SCORE_BASE - 15  # -5
SCORE_HI = SCORE_BASE + 9  # 19

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


def _one_month_change(an: Analysis, price: float) -> float | None:
    df = an.df
    if df is None or df.empty:
        return None
    start = pd.Timestamp(an.as_of) - pd.Timedelta(days=30)
    past = df.loc[df.index <= start]
    base = float(past["close"].iloc[-1]) if not past.empty else float(df["close"].iloc[0])
    if base <= 0:
        return None
    return price / base - 1.0


def _fmt(price: float) -> str:
    if price >= 1000:
        return f"{price:,.0f}"
    if price >= 1:
        return f"{price:,.2f}"
    return f"{price:.6f}"


def recommend(
    an: Analysis,
    htf_6m_action: str | None = None,
    htf_6m_pct: int | None = None,
) -> Signal:
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

    add("기본", "중립 시작점", SCORE_BASE)

    if an.trend == "up":
        add("추세", f"상승 · 비싸게 살 수 있어 감점. {an.price_label} {_fmt(price)}", -2)
    elif an.trend == "down":
        add("추세", f"하락 · 싸게 살 수 있어 가점. {an.price_label} {_fmt(price)}", 2)
    else:
        add("추세", f"횡보. {an.price_label} {_fmt(price)}", 0)

    if htf_6m_action == "매수":
        add("6개월 평가", "매수", 1)
    elif htf_6m_action == "매도":
        add("6개월 평가", "매도", -1)
    elif htf_6m_action == "홀딩":
        add("6개월 평가", "홀딩", 0)

    if htf_6m_pct is not None:
        if htf_6m_pct >= 60:
            add("6개월 상방/하단", f"합산 {htf_6m_pct}% · 상승 상방 근접", -3)
        elif htf_6m_pct < 40:
            add("6개월 상방/하단", f"합산 {htf_6m_pct}% · 하락 하단 근접", 3)
        else:
            add("6개월 상방/하단", f"합산 {htf_6m_pct}% · 중간", 0)

    if nsup:
        dist_s = price - nsup.price
        pct_s = dist_s / price * 100
        if dist_s <= near:
            add("지지", f"근접 {_fmt(nsup.price)} ({nsup.note}, 이격 {pct_s:.2f}%)", 2)
        else:
            add("지지", f"{_fmt(nsup.price)} 까지 {pct_s:.2f}%", 0)
        if price < nsup.price - atr * 0.15:
            add("지지 이탈", f"현재가 < {_fmt(nsup.price)}", -2)
    else:
        add("지지", "없음", 0)

    if nres:
        dist_r = nres.price - price
        pct_r = dist_r / price * 100
        if dist_r <= near:
            add("저항", f"근접 {_fmt(nres.price)} ({nres.note}, 이격 {pct_r:.2f}%)", -2)
        else:
            add("저항", f"{_fmt(nres.price)} 까지 {pct_r:.2f}%", 0)
    else:
        add("저항", "없음", 0)

    if abs(price - an.poc) <= near:
        if an.trend == "up":
            add("POC", f"최대 매물 {_fmt(an.poc)} 부근 · 상승 추세", 1)
        elif an.trend == "down":
            add("POC", f"최대 매물 {_fmt(an.poc)} 부근 · 하락 추세", -1)
        else:
            add("POC", f"최대 매물 {_fmt(an.poc)} 부근 · 횡보", 0)
        add("VAL", f"하단 {_fmt(an.val)} (POC가 우선이라 미적용)", 0)
        add("VAH", f"상단 {_fmt(an.vah)}", -2)
    elif price > an.vah:
        add("POC", f"최대 매물 {_fmt(an.poc)} · 현재가가 멀리 있음", 0)
        add("VAL", f"하단 {_fmt(an.val)} · 현재가가 VAL 위", 0)
        add("VAH", f"상단 {_fmt(an.vah)} 위", -2)
    elif price < an.val:
        add("POC", f"최대 매물 {_fmt(an.poc)} · 현재가가 멀리 있음", 0)
        if an.trend == "down":
            add("VAL", f"하단 {_fmt(an.val)} 아래 · 하락 추세", -1)
        elif an.trend == "up":
            add("VAL", f"하단 {_fmt(an.val)} 아래 · 상승 추세", 1)
        else:
            add("VAL", f"하단 {_fmt(an.val)} 아래 · 횡보", 0)
        add("VAH", f"상단 {_fmt(an.vah)}", -2)
    else:
        add("POC", f"최대 매물 {_fmt(an.poc)} · 밸류 구간 내부", 0)
        add("VAL", f"하단 {_fmt(an.val)} ~ 현재가 사이", 0)
        add("VAH", f"상단 {_fmt(an.vah)}", -2)

    if an.rsi >= 70:
        add("RSI", f"{an.rsi:.1f} 과매수", -1)
    elif an.rsi <= 30:
        add("RSI", f"{an.rsi:.1f} 과매도", 1)
    else:
        add("RSI", f"{an.rsi:.1f} 중립", 0)

    if an.ma20 is None:
        add("MA20", "이동평균 없음", 0)
    elif price > an.ma20:
        add("MA20", f"{an.price_label} > MA20 ({_fmt(an.ma20)})", 0)
    elif an.trend != "up":
        add("MA20", f"{an.price_label} < MA20 ({_fmt(an.ma20)})", -1)
    else:
        add("MA20", f"{an.price_label} < MA20 ({_fmt(an.ma20)}) · 상승 추세라 감점 없음", 0)

    chg = _one_month_change(an, price)
    if chg is None:
        add("1개월 상승률", "계산 불가", 0)
    elif chg >= 1.0:
        add("1개월 상승률", f"{chg * 100:.1f}% (100% 이상)", -4)
    elif chg >= 0.5:
        add("1개월 상승률", f"{chg * 100:.1f}% (50% 이상)", -2)
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

    if rr is not None and rr < 1.2 and score >= SCORE_BASE + 2:
        add("손익비", f"{rr:.2f} · 저항까지 여유 부족", -1)
    elif rr is not None:
        add("손익비", f"{rr:.2f} (목표 {_fmt(target)} / 손절 {_fmt(stop)})", 0)
    else:
        add("손익비", "목표·손절을 잡지 못함", 0)

    bar_count = 0 if an.df is None else len(an.df)
    buy_pct_cut = 70 if bar_count < 50 else 60
    sell_pct_cut = 30 if bar_count < 50 else 40
    if bar_count < 50:
        reasons.append(
            f"표본 {bar_count}봉으로 짧음 — 매수 {buy_pct_cut}% 이상 / 매도 {sell_pct_cut}% 이하"
        )

    score = max(0, score)
    lo, hi = SCORE_LO, SCORE_HI
    score_pct = score_to_pct(score)
    reasons.append(
        f"합산 {score_pct}% ({score}점, 범위 {lo}~{hi}) · 매수 {buy_pct_cut}%↑ / 매도 {sell_pct_cut}%↓"
    )

    if score_pct >= buy_pct_cut:
        action = "매수"
        summary = "하락·눌림 쪽에서 지지·하단 조건이 맞아 매수 쪽으로 기울었습니다."
    elif score_pct <= sell_pct_cut:
        action = "매도"
        summary = "상승·고가 쪽에서 저항·상단 조건이 맞아 매도/관망 쪽으로 기울었습니다."
    else:
        action = "홀딩"
        summary = "지지와 저항 사이이거나 신호가 엇갈려 관망(홀딩)이 낫습니다."
        if bar_count < 50:
            summary = "조회 기간이 짧아 신호가 쉽게 바뀝니다. 지금은 관망(홀딩)이 낫습니다."

    tilt = abs(score - SCORE_BASE)
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
