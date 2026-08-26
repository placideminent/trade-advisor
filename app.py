"""시점 기준 지지·저항·매물대 분석 → 매수/매도/홀딩 제안."""

from __future__ import annotations

import os
from datetime import date, timedelta

import pandas as pd
import streamlit as st

from src.analysis import analyze
from src.chart import build_chart
from src.data import fetch_ohlcv, fetch_spot_price, search_kr
from src.signals import (
    CUT_FIELDS,
    DEFAULT_CUTS,
    DEFAULT_WEIGHTS,
    WEIGHT_FIELDS,
    period_return,
    recommend,
    _fmt,
)


def _make_signal(an, six_month_chg=None, lookback_days=None, trend_1m=None, rule=None):
    try:
        return recommend(
            an,
            six_month_chg=six_month_chg,
            lookback_days=lookback_days,
            trend_1m=trend_1m,
            rule=rule,
        )
    except TypeError:
        try:
            return recommend(
                an,
                six_month_chg=six_month_chg,
                lookback_days=lookback_days,
                trend_1m=trend_1m,
            )
        except TypeError:
            try:
                return recommend(an, six_month_chg=six_month_chg)
            except TypeError:
                return recommend(an)


def _init_rule_widgets() -> None:
    for key, default in DEFAULT_WEIGHTS.items():
        st.session_state.setdefault(f"w_{key}", int(default))
    for key, default in DEFAULT_CUTS.items():
        st.session_state.setdefault(f"c_{key}", int(default))


def _read_rule_from_sidebar() -> dict:
    weights = {key: int(st.session_state.get(f"w_{key}", default)) for key, default in DEFAULT_WEIGHTS.items()}
    cuts = {key: int(st.session_state.get(f"c_{key}", default)) for key, default in DEFAULT_CUTS.items()}
    return {"weights": weights, "cuts": cuts}


def _reset_rule_widgets() -> None:
    for key, default in DEFAULT_WEIGHTS.items():
        st.session_state[f"w_{key}"] = int(default)
    for key, default in DEFAULT_CUTS.items():
        st.session_state[f"c_{key}"] = int(default)


@st.cache_data(ttl=60, show_spinner=False)
def _cached_spot(market: str, ticker: str):
    return fetch_spot_price(market, ticker)
from src.universe import (
    CRYPTO,
    KR_PRESETS,
    LOOKBACK_OPTIONS,
    MARKETS,
    US_PRESETS,
    crypto_choices,
    resolve_lookback,
)

st.set_page_config(
    page_title="매매시점 제안",
    page_icon="📈",
    layout="wide",
    initial_sidebar_state="expanded",
)


def _app_password() -> str:
    env = os.environ.get("APP_PASSWORD", "").strip()
    if env:
        return env
    try:
        return str(st.secrets.get("APP_PASSWORD", "")).strip()
    except Exception:
        return ""


def require_login() -> None:
    """외부 배포 시 비밀번호가 있으면 막는다. 로컬은 비워 두면 그대로 통과."""
    password = _app_password()
    if not password or st.session_state.get("auth_ok"):
        return
    st.title("매매시점 제안")
    st.caption("외부 접속용 비밀번호가 설정되어 있습니다.")
    pw = st.text_input("비밀번호", type="password")
    if st.button("입장", type="primary"):
        if pw == password:
            st.session_state.auth_ok = True
            st.rerun()
        st.error("비밀번호가 올바르지 않습니다.")
    st.stop()


require_login()

st.markdown(
    """
    <style>
      .block-container { padding-top: 1.2rem; }
      .action-buy-strong { background:#86efac; color:#14532d; padding:1rem 1.2rem; border-radius:12px; }
      .action-buy { background:#dcfce7; color:#14532d; padding:1rem 1.2rem; border-radius:12px; }
      .action-buy-weak { background:#ecfccb; color:#3f6212; padding:1rem 1.2rem; border-radius:12px; }
      .action-sell-strong { background:#fca5a5; color:#7f1d1d; padding:1rem 1.2rem; border-radius:12px; }
      .action-sell { background:#fee2e2; color:#7f1d1d; padding:1rem 1.2rem; border-radius:12px; }
      .action-sell-weak { background:#fecaca; color:#9f1239; padding:1rem 1.2rem; border-radius:12px; }
      .action-hold { background:#fef9c3; color:#713f12; padding:1rem 1.2rem; border-radius:12px; }
    </style>
    """,
    unsafe_allow_html=True,
)

st.title("매매시점 제안")
st.caption(
    "조회 시점의 현재가를 기준으로 추세선·지지/저항·매물대를 보고 "
    "매수 / 매도 / 홀딩을 제안합니다. "
    "1개월은 1시간봉, 2·3개월은 4시간봉, 6개월·1년은 일봉입니다. 투자 자문이 아닙니다."
)


def _preset_label(code: str, name: str) -> str:
    return f"{name} ({code})"


with st.sidebar:
    st.header("조회 조건")
    market_label = st.radio("시장", list(MARKETS.keys()), horizontal=False)
    market = MARKETS[market_label]

    ticker = ""
    display_name = ""

    if market == "KR":
        preset_map = {_preset_label(c, n): c for c, n in KR_PRESETS}
        choice = st.selectbox("대표 종목", list(preset_map.keys()))
        query = st.text_input("종목명 또는 코드 검색", placeholder="예: 삼성전자, 005930")
        if query.strip():
            try:
                hits = search_kr(query.strip())
            except Exception as exc:
                st.warning(f"종목 검색 실패: {exc}")
                hits = pd.DataFrame()
            if hits is not None and not hits.empty:
                options = [f"{r.Name} ({r.Code})" for r in hits.itertuples()]
                picked = st.selectbox("검색 결과", options)
                ticker = picked.split("(")[-1].rstrip(")")
                display_name = picked
            else:
                st.info("검색 결과가 없습니다. 대표 종목을 사용합니다.")
                ticker = preset_map[choice]
                display_name = choice
        else:
            ticker = preset_map[choice]
            display_name = choice
    elif market == "US":
        preset_map = {_preset_label(c, n): c for c, n in US_PRESETS}
        choice = st.selectbox("대표 종목", list(preset_map.keys()))
        custom = st.text_input("티커 직접 입력", placeholder="예: AAPL, NVDA")
        if custom.strip():
            ticker = custom.strip().upper()
            display_name = ticker
        else:
            ticker = preset_map[choice]
            display_name = choice
    else:
        cmap = {label: key for key, label in crypto_choices()}
        labels = [label for _, label in crypto_choices()]
        choice = st.selectbox("코인", labels, index=0)
        custom = st.text_input("심볼 직접 입력", placeholder="예: BTC, ETH, ONDO")
        if custom.strip():
            ticker = custom.strip().upper()
            info = CRYPTO.get(ticker)
            display_name = f"{info['name']} ({ticker})" if info else ticker
        else:
            ticker = cmap[choice]
            display_name = choice

    as_of = st.date_input("분석 시점", value=date.today(), max_value=date.today())
    lookback_keys = list(LOOKBACK_OPTIONS.keys())
    lookback_label = st.selectbox(
        "조회 기간",
        lookback_keys,
        index=lookback_keys.index("1년"),
        key="lookback_v2",
    )
    lookback_spec = resolve_lookback(lookback_label)
    lookback_days = int(lookback_spec["days"])
    timeframe = str(lookback_spec["timeframe"])

    _init_rule_widgets()
    with st.expander("평가 배점·기준", expanded=False):
        st.caption("합산 % 눈금(-5~19점)은 그대로 두고, 항목 점수와 매수/매도 컷만 바꿉니다.")
        st.markdown("**매수 / 매도 기준**")
        cut_cols = st.columns(2)
        for i, (key, label, suffix) in enumerate(CUT_FIELDS):
            with cut_cols[i % 2]:
                st.number_input(
                    f"{label} ({suffix})",
                    min_value=0,
                    max_value=100,
                    step=1,
                    key=f"c_{key}",
                )
        st.markdown("**항목 배점**")
        w_cols = st.columns(2)
        for i, (key, label, hint) in enumerate(WEIGHT_FIELDS):
            with w_cols[i % 2]:
                lo, hi = (0, 30) if key == "base" else (-10, 10)
                st.number_input(
                    label,
                    min_value=lo,
                    max_value=hi,
                    step=1,
                    key=f"w_{key}",
                    help=hint,
                )
        st.button(
            "기본값으로 되돌리기",
            use_container_width=True,
            on_click=_reset_rule_widgets,
        )
    rule = _read_rule_from_sidebar()
    run = st.button("분석하기", type="primary", use_container_width=True)

    st.markdown("---")
    st.markdown(
        """
**계산 방식**
- 지정일까지 시세만 사용 (이후 봉 제외)
- **1개월 → 1시간봉**, **2·3개월 → 4시간봉**, **6개월·1년 → 일봉**
- 6개월 전 대비 10% / 20% 상승 배점은 위에서 조정
- 1·2개월 추세: 상승 +, 하락 −. 3개월 이상은 하락 +, 상승 −
- 3개월·6개월·1년: 같은 시점 1개월 조회 추세 항목
- 추세선: 최근 스윙 고점/저점 연결
- 지지·저항: 스윙 군집 + 매물대
- 매수/매도 기준과 항목 배점은 **평가 배점·기준**에서 바꿉니다
        """
    )


if not run:
    st.info("왼쪽에서 시장·종목·시점을 고른 뒤 **분석하기**를 누르세요.")
    st.markdown(
        """
        #### 이 프로그램이 하는 일
        1. 한국 주식, 미국 주식, 비트코인·이더리움·솔라나·XRP·온도 등 원하는 종목을 고릅니다.
        2. **과거 특정 날짜**를 시점으로 넣으면 그 날 이후 시세는 보지 않습니다.
        3. 그 시점의 추세선, 지지/저항, 주요 매물대를 그린 뒤 매수·매도·홀딩을 제안합니다.

        1개월은 1시간봉, 2·3개월은 4시간봉, 6개월·1년은 일봉으로 계산합니다.
        """
    )
    st.stop()

if not ticker:
    st.error("종목을 선택하세요.")
    st.stop()

@st.cache_data(ttl=600, show_spinner=False)
def _cached_ohlcv(market: str, ticker: str, as_of_iso: str, lookback_days: int, timeframe: str, _ver: str = "1h1m"):
    return fetch_ohlcv(
        market,
        ticker,
        date.fromisoformat(as_of_iso),
        lookback_days,
        timeframe=timeframe,
    )


bar_name = {"1h": "1시간봉", "4h": "4시간봉"}.get(timeframe, "일봉")
with st.spinner(f"{display_name or ticker} / {as_of} {bar_name} 수집 중..."):
    try:
        df, meta = _cached_ohlcv(
            market, ticker, as_of.isoformat(), lookback_days, timeframe
        )
        df = df.copy()
        meta = dict(meta)
        timeframe = str(meta.get("timeframe") or timeframe)
        bar_name = str(meta.get("bar") or bar_name)
    except Exception as extra:
        st.error(f"시세 수집 실패: {extra}")
        st.stop()

if df.empty:
    st.error(
        "해당 기간에 봉이 없습니다. 종목 코드나 날짜를 확인하세요. "
        "한국 주식 단기 분·시간봉은 외부 시세에서 받는데, 서버에서 막히면 비어 보일 수 있습니다."
    )
    st.stop()

if meta.get("note"):
    st.warning(meta["note"])

last_bar_price = float(df["close"].iloc[-1])
spot_price = None
spot_source = ""
if as_of == date.today():
    try:
        spot_price, spot_source = _cached_spot(market, ticker)
    except Exception:
        spot_price, spot_source = None, ""
    if spot_price is None:
        spot_price = last_bar_price
        spot_source = "최근 봉 마지막 가격"
    # 추세·매물대는 완성 봉만 쓰고, 제안 가격만 현재가를 쓴다.
    if len(df) > 1 and pd.Timestamp(df.index[-1]).date() == date.today():
        df = df.iloc[:-1].copy()
else:
    spot_price = last_bar_price
    spot_source = "해당일 종가"

if len(df) < 40:
    st.warning(
        f"{bar_name}이 {len(df)}개로 적습니다. 지지·저항 신뢰도가 낮을 수 있습니다."
    )

try:
    analysis = analyze(
        df,
        as_of=as_of,
        spot_price=spot_price,
        price_source=spot_source,
        live=(as_of == date.today()),
    )
    six_month_chg = None
    try:
        src_6m = df
        if lookback_days < 180:
            src_6m, _meta_6m = _cached_ohlcv(
                market, ticker, as_of.isoformat(), 180, "1d"
            )
            src_6m = src_6m.copy()
        six_month_chg = period_return(src_6m, as_of, spot_price, 180)
    except Exception:
        six_month_chg = None
    trend_1m = None
    if lookback_days >= 90:
        try:
            df_1m, _meta_1m = _cached_ohlcv(
                market, ticker, as_of.isoformat(), 30, "1h"
            )
            df_1m = df_1m.copy()
            if not df_1m.empty:
                px_1m = float(df_1m["close"].iloc[-1])
                if as_of == date.today() and spot_price:
                    px_1m = spot_price
                    if (
                        len(df_1m) > 1
                        and pd.Timestamp(df_1m.index[-1]).date() == date.today()
                    ):
                        df_1m = df_1m.iloc[:-1].copy()
                an_1m = analyze(
                    df_1m,
                    as_of=as_of,
                    spot_price=px_1m,
                    price_source="1개월 조회",
                    live=(as_of == date.today()),
                )
                trend_1m = an_1m.trend
        except Exception:
            trend_1m = None
    signal = _make_signal(analysis, six_month_chg, lookback_days, trend_1m, rule)
except Exception as exc:
    st.error(f"분석 실패: {exc}")
    st.stop()

action_class = {
    "강한 매수": "action-buy-strong",
    "매수": "action-buy",
    "약한 매수": "action-buy-weak",
    "강한 매도": "action-sell-strong",
    "매도": "action-sell",
    "약한 매도": "action-sell-weak",
    "홀딩": "action-hold",
}.get(signal.action, "action-hold")
six_txt = (
    f" · 6개월 가격 {six_month_chg * 100:+.1f}%"
    if six_month_chg is not None
    else ""
)
st.markdown(
    f"""
    <div class="{action_class}">
      <div style="font-size:0.9rem;opacity:0.8">{meta.get("name") or display_name} ·
      분석일 {as_of} · {meta.get("bar", bar_name)} · {analysis.price_label} 기준
      {(" · " + analysis.price_source) if analysis.price_source else ""}</div>
      <div style="font-size:1.8rem;font-weight:700;margin:0.2rem 0">제안: {signal.action}
      <span style="font-size:1rem;font-weight:500">합산 {signal.score_pct}%{six_txt} · 신뢰 {signal.confidence}%</span></div>
      <div>{signal.summary}</div>
    </div>
    """,
    unsafe_allow_html=True,
)

c1, c2, c3, c4, c5, c6 = st.columns(6)
c1.metric(analysis.price_label, _fmt(analysis.price))
c2.metric("지지", _fmt(signal.nearest_support.price) if signal.nearest_support else "-")
c3.metric("저항", _fmt(signal.nearest_resistance.price) if signal.nearest_resistance else "-")
c4.metric("POC", _fmt(analysis.poc))
c5.metric("RSI", f"{analysis.rsi:.1f}")
c6.metric(
    "손익비",
    f"{signal.reward_risk:.2f}" if signal.reward_risk is not None else "-",
)

st.subheader("점수 내역")
if signal.score_rows:
    st.dataframe(pd.DataFrame(signal.score_rows), hide_index=True, use_container_width=True)
st.caption(f"VAL {_fmt(analysis.val)} · VAH {_fmt(analysis.vah)} · POC {_fmt(analysis.poc)}")

left, right = st.columns([1.15, 0.85])
with left:
    st.subheader("근거")
    for reason in signal.reasons:
        st.write(f"- {reason}")
    if signal.stop:
        st.write(f"- 무효화(손절 참고): **{_fmt(signal.stop)}**")
    if signal.target:
        st.write(f"- 1차 목표(다음 저항): **{_fmt(signal.target)}**")

with right:
    st.subheader("주요 가격대")
    rows = []
    for lv in analysis.supports:
        rows.append({"구분": "지지", "가격": _fmt(lv.price), "근거": lv.note, "강도": round(lv.strength, 2)})
    for lv in analysis.resistances:
        rows.append({"구분": "저항", "가격": _fmt(lv.price), "근거": lv.note, "강도": round(lv.strength, 2)})
    for lv in analysis.volume_nodes:
        rows.append(
            {"구분": "매물대", "가격": _fmt(lv.price), "근거": lv.note, "강도": round(lv.strength, 2)}
        )
    st.dataframe(pd.DataFrame(rows), hide_index=True, use_container_width=True)

last_txt = (
    analysis.last_bar.strftime("%Y-%m-%d %H:%M")
    if timeframe in ("1h", "4h")
    else str(analysis.last_bar.date())
)
title = f"{meta.get('name', ticker)} ({meta.get('ticker', ticker)})  ·  {bar_name}  ·  {analysis.price_label} {_fmt(analysis.price)}"
st.plotly_chart(build_chart(analysis, signal, title), use_container_width=True)

st.caption(
    f"데이터: {meta.get('source')} · {bar_name} · "
    f"{df.index[0]} ~ {df.index[-1]} ({len(df)}봉) · "
    f"제안 기준: {analysis.price_label} {_fmt(analysis.price)}"
    + (f" ({analysis.price_source})" if analysis.price_source else "")
    + (f" · 봉 종가 {_fmt(analysis.bar_close)}" if analysis.bar_close else "")
    + " · 지정일 이후 시세는 포함하지 않습니다. "
    "매물대는 각 봉의 고가~저가에 거래량을 나눠 쌓은 근사치입니다."
)
