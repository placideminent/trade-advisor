"""시점 기준 지지·저항·매물대 분석 → 매수/매도/홀딩 제안."""

from __future__ import annotations

import os
from datetime import date, timedelta

import pandas as pd
import streamlit as st

from src.analysis import analyze
from src.chart import build_chart
from src.data import fetch_ohlcv, search_kr
from src.signals import recommend, _fmt
from src.universe import CRYPTO, KR_PRESETS, LOOKBACK_OPTIONS, MARKETS, US_PRESETS, crypto_choices

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
      .action-buy { background:#dcfce7; color:#14532d; padding:1rem 1.2rem; border-radius:12px; }
      .action-sell { background:#fee2e2; color:#7f1d1d; padding:1rem 1.2rem; border-radius:12px; }
      .action-hold { background:#fef9c3; color:#713f12; padding:1rem 1.2rem; border-radius:12px; }
    </style>
    """,
    unsafe_allow_html=True,
)

st.title("시점 매매 제안")
st.caption(
    "원하는 날짜의 일봉만 사용해 추세선·지지/저항·매물대를 계산하고 "
    "매수 / 매도 / 홀딩을 제안합니다. 투자 자문이 아닙니다."
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
    lookback_label = st.selectbox("조회 기간", lookback_keys, index=lookback_keys.index("1년"))
    lookback_days = LOOKBACK_OPTIONS[lookback_label]
    run = st.button("분석하기", type="primary", use_container_width=True)

    st.markdown("---")
    st.markdown(
        """
**계산 방식**
- 지정일 **당일 종가까지**만 사용 (이후 봉 제외)
- 추세선: 최근 스윙 고점/저점 연결
- 지지·저항: 스윙 군집 + 매물대
- 매물대: 일봉 고가~저가 구간에 거래량 분배
- 신호: 규칙 점수 (추세, 이격, RSI, 손익비)
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

        일봉 근사라 분봉 매물대보다는 거칠지만, 날짜를 바꿔가며 복기하기 좋습니다.
        """
    )
    st.stop()

if not ticker:
    st.error("종목을 선택하세요.")
    st.stop()

with st.spinner(f"{display_name or ticker} / {as_of} 일봉 수집 중..."):
    try:
        df, meta = fetch_ohlcv(market, ticker, as_of, lookback_days)
    except Exception as exc:
        st.error(f"시세 수집 실패: {exc}")
        st.stop()

if df.empty:
    st.error("해당 기간에 일봉이 없습니다. 종목 코드나 날짜를 확인하세요.")
    st.stop()

min_bars = 40
if len(df) < min_bars:
    st.warning(f"봉 수가 {len(df)}개로 적습니다. 지지·저항 신뢰도가 낮을 수 있습니다.")

try:
    analysis = analyze(df, as_of=as_of)
    signal = recommend(analysis)
except Exception as exc:
    st.error(f"분석 실패: {exc}")
    st.stop()

action_class = {"매수": "action-buy", "매도": "action-sell", "홀딩": "action-hold"}[signal.action]
st.markdown(
    f"""
    <div class="{action_class}">
      <div style="font-size:0.9rem;opacity:0.8">{meta.get("name") or display_name} ·
      분석일 {as_of} · 마지막 봉 {analysis.last_bar.date()}</div>
      <div style="font-size:1.8rem;font-weight:700;margin:0.2rem 0">제안: {signal.action}
      <span style="font-size:1rem;font-weight:500">신뢰 {signal.confidence}</span></div>
      <div>{signal.summary}</div>
    </div>
    """,
    unsafe_allow_html=True,
)

c1, c2, c3, c4, c5, c6 = st.columns(6)
c1.metric("종가", _fmt(analysis.price))
c2.metric("지지", _fmt(signal.nearest_support.price) if signal.nearest_support else "-")
c3.metric("저항", _fmt(signal.nearest_resistance.price) if signal.nearest_resistance else "-")
c4.metric("POC 매물", _fmt(analysis.poc))
c5.metric("RSI", f"{analysis.rsi:.1f}")
c6.metric(
    "손익비",
    f"{signal.reward_risk:.2f}" if signal.reward_risk is not None else "-",
)

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

title = f"{meta.get('name', ticker)} ({meta.get('ticker', ticker)})  ·  {analysis.last_bar.date()} 종가 기준"
st.plotly_chart(build_chart(analysis, signal, title), use_container_width=True)

st.caption(
    f"데이터: {meta.get('source')} · 조회 시작 {df.index[0].date()} ~ {df.index[-1].date()} "
    f"({len(df)}봉) · 지정일 이후 시세는 포함하지 않습니다. "
    "일봉 매물대는 분봉 프로파일의 근사치입니다."
)
