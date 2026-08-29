"""시점 기준 지지·저항·매물대 분석 → 매수/매도/홀딩 제안."""

from __future__ import annotations

import html
import os
import time
from datetime import date, timedelta
from functools import partial

import pandas as pd
import streamlit as st
import streamlit.components.v1 as components

from src.analysis import analyze
from src.backtest import DEFAULT_SIM, normalize_sim, run_backtest, spy_hold_return
from src.chart import (
    build_chart,
    build_pnl_split_fig,
    build_return_vs_spy_fig,
    build_sim_chart,
    build_ticker_vs_spy_fig,
)
from src.data import drop_incomplete_session, fetch_ohlcv, fetch_spot_price, fetch_usdkrw, market_today, search_kr
from src.fundamentals import fetch_fundamentals, fmt_per, fmt_pct, fmt_pp, per_gap_text

from src.prefs import (
    COOKIE_NAME,
    MAX_FAVORITES,
    UID_COOKIE_NAME,
    cookie_set_html,
    decode_cookie,
    format_uid,
    load_user_prefs_file,
    load_user_prefs_remote,
    merge_prefs,
    new_uid,
    normalize_uid,
    remote_enabled,
    save_user_prefs_file,
    save_user_prefs_remote,
    snapshot_key,
)

try:
    from src.prefs import (
        QUERY_PREFS,
        QUERY_UID,
        encode_browser,
        localstorage_boot_html,
        localstorage_restore_html,
        remote_last_error,
    )
except ImportError:
    QUERY_PREFS = "_p"
    QUERY_UID = "_u"

    def encode_browser(data: dict) -> str:
        from src.prefs import encode_cookie

        return encode_cookie(data)

    def localstorage_restore_html(uid: str) -> str:
        return ""

    def localstorage_boot_html() -> str:
        return ""

    def remote_last_error() -> str:
        return ""
from src.signals import (
    CUT_FIELDS,
    DEFAULT_CUTS,
    DEFAULT_WEIGHTS,
    WEIGHT_FIELDS,
    period_return,
    recommend,
    _fmt,
)


def _make_signal(
    an,
    six_month_chg=None,
    lookback_days=None,
    rule=None,
):
    try:
        return recommend(
            an,
            six_month_chg=six_month_chg,
            lookback_days=lookback_days,
            rule=rule,
        )
    except TypeError:
        try:
            return recommend(an, six_month_chg=six_month_chg, lookback_days=lookback_days, rule=rule)
        except TypeError:
            try:
                return recommend(an, six_month_chg=six_month_chg)
            except TypeError:
                return recommend(an)


def _init_rule_widgets() -> None:
    for key, default in DEFAULT_WEIGHTS.items():
        sk = f"w_{key}"
        if sk not in st.session_state:
            st.session_state[sk] = int(default)
        # 새로 추가된 +배점 칸이 number_input 최솟값 -10으로 잡히는 경우 바로잡는다.
        elif int(default) >= 0 and int(st.session_state[sk]) == -10:
            st.session_state[sk] = int(default)
    for key, default in DEFAULT_CUTS.items():
        st.session_state.setdefault(f"c_{key}", int(default))
    for key, default in DEFAULT_SIM.items():
        st.session_state.setdefault(f"s_{key}", int(default))


def _read_rule_from_sidebar() -> dict:
    weights = {key: int(st.session_state.get(f"w_{key}", default)) for key, default in DEFAULT_WEIGHTS.items()}
    cuts = {key: int(st.session_state.get(f"c_{key}", default)) for key, default in DEFAULT_CUTS.items()}
    return {"weights": weights, "cuts": cuts}


def _reset_rule_widgets() -> None:
    for key, default in DEFAULT_WEIGHTS.items():
        st.session_state[f"w_{key}"] = int(default)
    for key, default in DEFAULT_CUTS.items():
        st.session_state[f"c_{key}"] = int(default)


def _read_sim_from_sidebar() -> dict:
    return {key: int(st.session_state.get(f"s_{key}", default)) for key, default in DEFAULT_SIM.items()}


def _reset_sim_widgets() -> None:
    for key, default in DEFAULT_SIM.items():
        st.session_state[f"s_{key}"] = int(default)


def _fav_sim_prefix(market: str, ticker: str) -> str:
    return f"sf_{market}_{ticker}_"


def _sim_qty_fields(prefix: str) -> None:
    q1, q2, q3 = st.columns(3)
    with q1:
        st.number_input("약한 매수 주", min_value=0, max_value=1000, step=1, key=f"{prefix}buy_weak")
    with q2:
        st.number_input("매수 주", min_value=0, max_value=1000, step=1, key=f"{prefix}buy_mid")
    with q3:
        st.number_input("강한 매수 주", min_value=0, max_value=1000, step=1, key=f"{prefix}buy_strong")
    st.number_input("이 수량 이상이면 % 매도", min_value=1, max_value=10000, step=1, key=f"{prefix}share_cut")
    p1, p2, p3 = st.columns(3)
    with p1:
        st.number_input("약한 매도 %", min_value=1, max_value=100, step=1, key=f"{prefix}sell_weak_pct")
    with p2:
        st.number_input("매도 %", min_value=1, max_value=100, step=1, key=f"{prefix}sell_mid_pct")
    with p3:
        st.number_input("강한 매도 %", min_value=1, max_value=100, step=1, key=f"{prefix}sell_strong_pct")
    f1, f2, f3 = st.columns(3)
    with f1:
        st.number_input("약한 매도 주(미만)", min_value=1, max_value=1000, step=1, key=f"{prefix}sell_weak_qty")
    with f2:
        st.number_input("매도 주(미만)", min_value=1, max_value=1000, step=1, key=f"{prefix}sell_mid_qty")
    with f3:
        st.number_input("강한 매도 주(미만)", min_value=1, max_value=1000, step=1, key=f"{prefix}sell_strong_qty")


def _read_sim_from_prefix(prefix: str) -> dict:
    raw = {key: st.session_state.get(f"{prefix}{key}", default) for key, default in DEFAULT_SIM.items()}
    return normalize_sim(raw)


def _init_fav_sim_widgets(global_sim: dict) -> None:
    for item in _fav_list():
        prefix = _fav_sim_prefix(item["market"], item["ticker"])
        src = item.get("sim") if isinstance(item.get("sim"), dict) else None
        for key, default in DEFAULT_SIM.items():
            sk = f"{prefix}{key}"
            if sk in st.session_state:
                continue
            if src and key in src:
                try:
                    st.session_state[sk] = int(src[key])
                    continue
                except (TypeError, ValueError):
                    pass
            st.session_state[sk] = int(global_sim.get(key, default))


def _sync_fav_sims() -> None:
    out = []
    for item in _fav_list():
        prefix = _fav_sim_prefix(item["market"], item["ticker"])
        entry = {
            "market": item["market"],
            "ticker": item["ticker"],
            "name": item.get("name") or item["ticker"],
            "sim": _read_sim_from_prefix(prefix),
        }
        out.append(entry)
    st.session_state.favorites = out


def _copy_global_sim_to_favs() -> None:
    global_sim = _read_sim_from_sidebar()
    for item in _fav_list():
        prefix = _fav_sim_prefix(item["market"], item["ticker"])
        for key, val in global_sim.items():
            st.session_state[f"{prefix}{key}"] = int(val)


def _reset_one_fav_sim(market: str, ticker: str) -> None:
    prefix = _fav_sim_prefix(market, ticker)
    for key, default in DEFAULT_SIM.items():
        st.session_state[f"{prefix}{key}"] = int(default)


ACTION_CLASS = {
    "강한 매수": "action-buy-strong",
    "매수": "action-buy",
    "약한 매수": "action-buy-weak",
    "강한 매도": "action-sell-strong",
    "매도": "action-sell",
    "약한 매도": "action-sell-weak",
    "홀딩": "action-hold",
}
FAVBAR_COLORS = {
    "강한 매수": ("#86efac", "#14532d"),
    "매수": ("#dcfce7", "#14532d"),
    "약한 매수": ("#ecfccb", "#3f6212"),
    "강한 매도": ("#fca5a5", "#7f1d1d"),
    "매도": ("#fee2e2", "#7f1d1d"),
    "약한 매도": ("#fecaca", "#9f1239"),
    "홀딩": ("#fef9c3", "#713f12"),
}
FAVBAR_KIND = {
    "강한 매수": "bstrong",
    "매수": "bmid",
    "약한 매수": "bweak",
    "강한 매도": "sstrong",
    "매도": "smid",
    "약한 매도": "sweak",
    "홀딩": "hold",
}


def _cookie_map():
    try:
        ctx = getattr(st, "context", None)
        cookies = getattr(ctx, "cookies", None) if ctx is not None else None
        return cookies
    except Exception:
        return None


def _cookie_token() -> str | None:
    try:
        cookies = _cookie_map()
        if cookies is None:
            return None
        raw = cookies.get(COOKIE_NAME)
        if raw is None:
            return None
        return str(raw)
    except Exception:
        return None


def _cookie_uid() -> str:
    try:
        cookies = _cookie_map()
        if cookies is None:
            return ""
        return normalize_uid(cookies.get(UID_COOKIE_NAME))
    except Exception:
        return ""


def _query_uid() -> str:
    try:
        raw = st.query_params.get(QUERY_UID)
        if raw is None:
            return ""
        if isinstance(raw, list):
            raw = raw[0] if raw else ""
        return normalize_uid(raw)
    except Exception:
        return ""


def _bind_uid_url(uid: str) -> None:
    uid = normalize_uid(uid)
    if not uid:
        return
    try:
        if _query_uid() != uid:
            st.query_params[QUERY_UID] = uid
    except Exception:
        pass


def _query_prefs() -> dict | None:
    try:
        raw = st.query_params.get(QUERY_PREFS)
        if raw is None:
            return None
        if isinstance(raw, list):
            raw = raw[0] if raw else None
        return decode_cookie(raw)
    except Exception:
        return None


def _bind_prefs_url(payload: dict) -> None:
    uid = normalize_uid(payload.get("uid"))
    _bind_uid_url(uid)
    if not payload.get("favorites") and not int(payload.get("ts") or 0):
        return
    try:
        token = encode_browser(payload)
        current = st.query_params.get(QUERY_PREFS)
        if isinstance(current, list):
            current = current[0] if current else None
        if current != token:
            st.query_params[QUERY_PREFS] = token
    except Exception:
        pass


def _prefs_payload(rule: dict) -> dict:
    return {
        "uid": normalize_uid(st.session_state.get("prefs_uid")),
        "ts": int(st.session_state.get("_prefs_ts") or 0),
        "weights": rule["weights"],
        "cuts": rule["cuts"],
        "sim": {key: int(st.session_state.get(f"s_{key}", default)) for key, default in DEFAULT_SIM.items()},
        "favorites": list(st.session_state.get("favorites") or []),
    }


def _apply_loaded_prefs(loaded: dict) -> None:
    for key, default in DEFAULT_WEIGHTS.items():
        val = int(loaded["weights"].get(key, default))
        if key == "support_near" and val == 2:
            val = 1
        if key == "resist_near" and val == -2:
            val = -1
        if key == "chg1_50" and val == -3:
            val = -1
        if key == "chg1_100" and val == -4:
            val = -2
        if key == "chg1_down20" and val == 2:
            val = 1
        st.session_state[f"w_{key}"] = val
    for key, default in DEFAULT_CUTS.items():
        st.session_state[f"c_{key}"] = int(loaded["cuts"].get(key, default))
    sim = loaded.get("sim") or {}
    for key, default in DEFAULT_SIM.items():
        st.session_state[f"s_{key}"] = int(sim.get(key, default))
    st.session_state.favorites = list(loaded.get("favorites") or [])


def _bootstrap_prefs() -> None:
    adopt = normalize_uid(st.session_state.pop("_prefs_adopt", None))
    if st.session_state.get("_prefs_boot") and not adopt:
        return
    cookie_data = decode_cookie(_cookie_token())
    query_data = _query_prefs()
    uid = (
        adopt
        or normalize_uid((cookie_data or {}).get("uid"))
        or _cookie_uid()
        or normalize_uid((query_data or {}).get("uid"))
        or _query_uid()
        or normalize_uid(st.session_state.get("prefs_uid"))
        or new_uid()
    )
    sources = []
    if cookie_data and (not cookie_data.get("uid") or cookie_data.get("uid") == uid):
        sources.append(cookie_data)
    if query_data and (not query_data.get("uid") or query_data.get("uid") == uid):
        sources.append(query_data)
    sources.append(load_user_prefs_file(uid))
    sources.append(load_user_prefs_remote(uid))
    loaded = merge_prefs(*sources)
    loaded["uid"] = uid
    _apply_loaded_prefs(loaded)
    st.session_state.prefs_uid = uid
    st.session_state._prefs_ts = int(loaded.get("ts") or 0)
    _bind_uid_url(uid)
    if loaded.get("favorites") or adopt or query_data:
        _bind_prefs_url(loaded)
    st.session_state._prefs_snapshot = snapshot_key(loaded)
    st.session_state._prefs_boot = True
    st.session_state._prefs_just_booted = not bool(adopt)
    st.session_state._prefs_need_cookie = True
    st.session_state._prefs_remote_ok = remote_enabled()
    if adopt and _score_empty(loaded):
        st.session_state._prefs_ls_html = localstorage_restore_html(uid)
        if remote_enabled():
            st.session_state._prefs_adopt_msg = (
                f"{format_uid(uid)} 클라우드 기록이 없습니다. "
                "예전에 토큰 없이 쓰던 목록은 리부트 때 지워졌을 수 있습니다."
            )
        else:
            st.session_state._prefs_adopt_msg = (
                "클라우드 저장이 아직 연결되지 않았습니다. 이 브라우저에 남은 저장이 있으면 불러옵니다."
            )
    elif adopt:
        st.session_state._prefs_adopt_msg = f"{format_uid(uid)} 저장을 불러왔습니다."


def _score_empty(loaded: dict) -> bool:
    return not loaded.get("favorites") and not loaded.get("ts")


def _persist_prefs(rule: dict) -> None:
    payload = _prefs_payload(rule)
    uid = normalize_uid(payload.get("uid"))
    snap = snapshot_key(payload)
    just_booted = bool(st.session_state.pop("_prefs_just_booted", False))
    if st.session_state.get("_prefs_snapshot") == snap:
        if just_booted and uid:
            save_user_prefs_file(uid, payload)
            if payload.get("favorites"):
                _bind_prefs_url(payload)
                if remote_enabled():
                    st.session_state._prefs_remote_ok = save_user_prefs_remote(uid, payload)
        if st.session_state.pop("_prefs_force_remote", False) and uid and remote_enabled():
            st.session_state._prefs_remote_ok = save_user_prefs_remote(uid, payload)
        if (not just_booted) and st.session_state.pop("_prefs_need_cookie", False):
            st.session_state._prefs_cookie_html = cookie_set_html(payload)
            st.session_state._prefs_cookie_dirty = True
            _bind_uid_url(uid)
            if payload.get("favorites") or int(payload.get("ts") or 0):
                _bind_prefs_url(payload)
        return
    st.session_state._prefs_snapshot = snap
    payload["ts"] = int(time.time())
    st.session_state._prefs_ts = payload["ts"]
    if uid:
        save_user_prefs_file(uid, payload)
    if just_booted:
        if payload.get("favorites"):
            _bind_prefs_url(payload)
            if uid and remote_enabled():
                st.session_state._prefs_remote_ok = save_user_prefs_remote(uid, payload)
        return
    if uid and remote_enabled():
        st.session_state._prefs_remote_ok = save_user_prefs_remote(uid, payload)
    st.session_state._prefs_cookie_html = cookie_set_html(payload)
    st.session_state._prefs_cookie_dirty = True
    _bind_uid_url(uid)
    if payload.get("favorites") or int(payload.get("ts") or 0):
        _bind_prefs_url(payload)
    st.session_state.pop("_prefs_need_cookie", None)


def _emit_boot_restore() -> None:
    """같은 브라우저에 남은 저장 코드가 있으면 주소에 다시 붙인다."""
    if _query_uid() or _cookie_uid() or _cookie_token():
        return
    if st.session_state.get("_prefs_boot_ls_done"):
        return
    st.session_state._prefs_boot_ls_done = True
    html = localstorage_boot_html()
    if not html:
        return
    try:
        components.html(html, height=1, width=1)
    except Exception:
        pass


def _emit_ls_restore() -> None:
    html = st.session_state.pop("_prefs_ls_html", None)
    if not html:
        return
    try:
        components.html(html, height=1, width=1)
    except Exception:
        pass


def _emit_prefs_cookie() -> None:
    if not st.session_state.pop("_prefs_cookie_dirty", False):
        return
    html = st.session_state.get("_prefs_cookie_html")
    if not html:
        return
    try:
        components.html(html, height=1, width=1)
    except Exception:
        pass


def _fav_list() -> list[dict]:
    return list(st.session_state.get("favorites") or [])


def _is_fav(market: str, ticker: str) -> bool:
    return any(f.get("market") == market and f.get("ticker") == ticker for f in _fav_list())


def _add_fav(market: str, ticker: str, name: str) -> None:
    if _is_fav(market, ticker):
        return
    favs = _fav_list()
    if len(favs) >= MAX_FAVORITES:
        st.session_state._fav_full = True
        return
    favs.append(
        {
            "market": market,
            "ticker": ticker,
            "name": name or ticker,
            "sim": _read_sim_from_sidebar(),
        }
    )
    st.session_state.favorites = favs


def _remove_fav(market: str, ticker: str) -> None:
    st.session_state.favorites = [
        f for f in _fav_list() if not (f.get("market") == market and f.get("ticker") == ticker)
    ]


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
    page_title="자산 트레이드 분석기",
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
    st.title("자산 트레이드 분석기")
    st.caption("외부 접속용 비밀번호가 설정되어 있습니다.")
    pw = st.text_input("비밀번호", type="password")
    if st.button("입장", type="primary"):
        if pw == password:
            st.session_state.auth_ok = True
            st.rerun()
        st.error("비밀번호가 올바르지 않습니다.")
    st.stop()


_emit_boot_restore()
require_login()
_bootstrap_prefs()
_emit_ls_restore()

st.markdown(
    """
    <style>
      .block-container { padding-top: 3.2rem; padding-bottom: 2.5rem; }
      h1 {
        overflow: visible !important;
        white-space: normal !important;
        line-height: 1.4 !important;
        word-break: keep-all;
        padding-top: 0.15rem;
      }
      .action-buy-strong { background:#86efac; color:#14532d; padding:0.75rem 0.9rem; border-radius:10px; }
      .action-buy { background:#dcfce7; color:#14532d; padding:0.75rem 0.9rem; border-radius:10px; }
      .action-buy-weak { background:#ecfccb; color:#3f6212; padding:0.75rem 0.9rem; border-radius:10px; }
      .action-sell-strong { background:#fca5a5; color:#7f1d1d; padding:0.75rem 0.9rem; border-radius:10px; }
      .action-sell { background:#fee2e2; color:#7f1d1d; padding:0.75rem 0.9rem; border-radius:10px; }
      .action-sell-weak { background:#fecaca; color:#9f1239; padding:0.75rem 0.9rem; border-radius:10px; }
      .action-hold { background:#fef9c3; color:#713f12; padding:0.75rem 0.9rem; border-radius:10px; }
      .fav-bar {
        min-height: 4.6rem;
        box-sizing: border-box;
        display: flex;
        flex-direction: column;
        justify-content: center;
        align-items: stretch;
        width: 100%;
        text-align: left;
        padding: 0.7rem 0.85rem !important;
      }
      .fav-bar .fav-meta,
      .fav-bar .fav-main {
        display: block;
        width: 100%;
        box-sizing: border-box;
        text-align: left !important;
        font-weight: 800;
        letter-spacing: -0.02em;
        line-height: 1.35;
        margin: 0;
        padding: 0;
        overflow: hidden;
        text-overflow: ellipsis;
        white-space: nowrap;
      }
      .fav-bar .fav-meta { font-size: 0.92rem; opacity: 0.92; }
      .fav-bar .fav-main { font-size: 1.12rem; margin-top: 0.22rem; }
      .fav-bar .fav-main-grid {
        display: grid;
        grid-template-columns: minmax(0, 1.35fr) minmax(0, 1.05fr) minmax(0, 0.85fr);
        gap: 0.15rem 0.45rem;
        width: 100%;
        white-space: nowrap;
      }
      .fav-bar .fav-main-grid > span {
        display: block;
        min-width: 0;
        overflow: hidden;
        text-overflow: ellipsis;
        text-align: left !important;
        font-weight: 800;
        letter-spacing: -0.02em;
      }
      [data-testid="stMarkdownContainer"]:has(.fav-bar),
      [data-testid="stMarkdownContainer"]:has(.fav-bar) p {
        margin: 0 !important;
        padding: 0 !important;
      }
      div[data-testid="stHorizontalBlock"]:has([class*="st-key-unfav_"]) {
        flex-wrap: nowrap !important;
        align-items: stretch !important;
        gap: 0.4rem !important;
      }
      div[data-testid="stHorizontalBlock"]:has([class*="st-key-unfav_"]) > div:last-child {
        flex: 0 0 3.6rem !important;
        width: 3.6rem !important;
        min-width: 3.6rem !important;
      }
      div[data-testid="stHorizontalBlock"]:has([class*="st-key-unfav_"]) [data-testid="stVerticalBlock"] {
        gap: 0 !important;
      }
      div[class*="st-key-favbar_"] {
        position: relative;
        z-index: 5;
        margin-top: -4.6rem !important;
        height: 4.6rem !important;
        min-height: 4.6rem !important;
        overflow: hidden;
      }
      div[class*="st-key-favbar_"] button {
        width: 100% !important;
        min-height: 4.6rem !important;
        height: 4.6rem !important;
        margin: 0 !important;
        opacity: 0 !important;
        cursor: pointer !important;
        border: none !important;
        box-shadow: none !important;
        background: transparent !important;
      }
      div[class*="st-key-unfav_"] button {
        min-height: 4.6rem !important;
        height: 100% !important;
        margin-top: 0 !important;
        border-radius: 10px !important;
        padding: 0 0.25rem !important;
      }
      .ta-table { width:100%; border-collapse:collapse; font-size:0.92rem; table-layout:auto; }
      .ta-table th, .ta-table td {
        border-bottom:1px solid #e5e7eb; padding:0.5rem 0.4rem;
        text-align:left; vertical-align:top; word-break:break-word;
      }
      .ta-table th { background:#f8fafc; font-weight:600; }
      @media (max-width: 640px) {
        .block-container {
          padding-top: 3.6rem !important;
          padding-left: 0.55rem;
          padding-right: 0.55rem;
        }
        h1 { font-size: 1.28rem !important; line-height: 1.45 !important; }
        div[data-testid="stHorizontalBlock"]:has([class*="st-key-unfav_"]) {
          flex-wrap: nowrap !important;
        }
        .fav-bar .fav-meta { font-size: 0.86rem; }
        .fav-bar .fav-main { font-size: 1.02rem; }
        h2, h3 { font-size: 1.05rem !important; }
        .action-buy-strong, .action-buy, .action-buy-weak,
        .action-sell-strong, .action-sell, .action-sell-weak, .action-hold {
          padding: 0.6rem 0.7rem; font-size: 0.95rem;
        }
      }
    </style>
    """,
    unsafe_allow_html=True,
)

st.title("자산 트레이드 분석기")
st.caption(
    "조회 시점의 현재가를 기준으로 추세선·지지/저항·매물대를 보고 "
    "매수 / 매도 / 홀딩을 제안합니다. "
    "1개월은 1시간봉, 2·3개월은 4시간봉, 6개월·1년은 일봉입니다. 투자 자문이 아닙니다."
)


def _preset_label(code: str, name: str) -> str:
    return f"{name} ({code})"


def _show_table(df: pd.DataFrame) -> None:
    if df is None or getattr(df, "empty", True):
        return
    html = df.to_html(index=False, escape=True, classes="ta-table", border=0)
    st.markdown(html, unsafe_allow_html=True)


def _kv_table(pairs: list[tuple[str, str]]) -> None:
    _show_table(pd.DataFrame(pairs, columns=["항목", "값"]))


def _transpose_records(rows: list[dict], index_key: str = "기간") -> pd.DataFrame:
    if not rows:
        return pd.DataFrame()
    periods = [str(r.get(index_key) or "") for r in rows]
    keys = [k for k in rows[0].keys() if k != index_key]
    data = {"항목": keys}
    for rec, period in zip(rows, periods):
        data[period] = [rec.get(k) for k in keys]
    return pd.DataFrame(data)


class _EmptyBars(RuntimeError):
    """빈 봉은 Streamlit 캐시에 넣지 않는다. 예외는 캐시되지 않는다."""


@st.cache_data(ttl=600, show_spinner=False)
def _cached_ohlcv(market: str, ticker: str, as_of_iso: str, lookback_days: int, timeframe: str, _ver: str = "1h1m"):
    df, meta = fetch_ohlcv(
        market,
        ticker,
        date.fromisoformat(as_of_iso),
        lookback_days,
        timeframe=timeframe,
    )
    if df is None or getattr(df, "empty", True):
        raise _EmptyBars("봉 없음")
    return df, meta


@st.cache_data(ttl=90, show_spinner=False)
def _cached_spot(market: str, ticker: str):
    px, src = fetch_spot_price(market, ticker)
    if not px:
        raise RuntimeError("no_spot")
    return px, src


def _load_ohlcv(market: str, ticker: str, as_of, lookback_days: int, timeframe: str, retries: int = 2):
    last_meta = {"ticker": ticker, "name": ticker, "timeframe": timeframe}
    as_of_iso = as_of.isoformat() if hasattr(as_of, "isoformat") else str(as_of)
    tries = 1 + max(0, int(retries))
    for i in range(tries):
        if i:
            time.sleep(min(0.8 * i, 2.4))
        try:
            df, meta = _cached_ohlcv(market, ticker, as_of_iso, lookback_days, timeframe)
            if df is not None and not getattr(df, "empty", True):
                return df.copy(), dict(meta)
        except Exception:
            pass
    return pd.DataFrame(), last_meta


def _quick_signal(market: str, ticker: str, as_of, lookback_days: int, timeframe: str, rule: dict) -> dict:
    as_of = min(as_of, market_today(market))
    try:
        df, meta = _load_ohlcv(market, ticker, as_of, lookback_days, timeframe, retries=2)
        df = df.copy()
        meta = dict(meta)
        tf = str(meta.get("timeframe") or timeframe)
    except Exception as exc:
        return {"market": market, "ticker": ticker, "name": ticker, "error": str(exc)}
    if df.empty:
        return {"market": market, "ticker": ticker, "name": ticker, "error": "봉 없음"}
    last_bar_price = float(df["close"].iloc[-1])
    is_live = as_of == market_today(market)
    spot_price = last_bar_price
    spot_source = "해당일 종가"
    if is_live:
        try:
            live_px, live_src = _cached_spot(market, ticker)
        except Exception:
            live_px, live_src = None, ""
        if live_px:
            spot_price, spot_source = live_px, live_src
        trimmed = drop_incomplete_session(df, as_of)
        if trimmed is not None and not trimmed.empty:
            df = trimmed
    try:
        analysis = analyze(
            df,
            as_of=as_of,
            spot_price=spot_price,
            price_source=spot_source,
            live=is_live,
        )
        six_month_chg = None
        try:
            src_6m = df
            if lookback_days < 180:
                src_6m, _ = _load_ohlcv(market, ticker, as_of, 180, "1d", retries=1)
            six_month_chg = period_return(src_6m, as_of, spot_price, 180)
        except Exception:
            six_month_chg = None
        signal = _make_signal(
            analysis,
            six_month_chg,
            lookback_days,
            rule,
        )
    except Exception as extra:
        return {
            "market": market,
            "ticker": ticker,
            "name": meta.get("name") or ticker,
            "error": str(extra),
        }
    return {
        "market": market,
        "ticker": str(meta.get("ticker") or ticker),
        "name": str(meta.get("name") or ticker),
        "action": signal.action,
        "score_pct": signal.score_pct,
        "price": analysis.price,
        "price_label": analysis.price_label,
        "error": None,
    }


def _render_fav_add() -> None:
    st.markdown("**종목 추가**")
    mlab = st.radio("시장", list(MARKETS.keys()), horizontal=True, key="favadd_market")
    market = MARKETS[mlab]
    ticker = ""
    name = ""
    if market == "KR":
        query = st.text_input("종목명 또는 코드", key="favadd_kr_q", placeholder="예: 삼성전자, 005930")
        if query.strip():
            try:
                hits = search_kr(query.strip())
            except Exception as extra:
                st.warning(f"검색 실패: {extra}")
                hits = None
            if hits is not None and not hits.empty:
                options = [f"{r.Name} ({r.Code})" for r in hits.itertuples()]
                picked = st.selectbox("검색 결과", options, key="favadd_kr_pick")
                ticker = picked.split("(")[-1].rstrip(")")
                name = picked.rsplit(" (", 1)[0]
            else:
                st.caption("검색 결과가 없습니다.")
    elif market == "US":
        ticker = st.text_input("티커", key="favadd_us", placeholder="예: AAPL, NVDA").strip().upper()
        name = ticker
    else:
        ticker = st.text_input("심볼", key="favadd_crypto", placeholder="예: BTC, ETH").strip().upper()
        info = CRYPTO.get(ticker)
        name = f"{info['name']} ({ticker})" if info else ticker
    if ticker and st.button("★ 즐겨찾기 추가", type="primary", key="favadd_btn"):
        if _is_fav(market, ticker):
            st.info("이미 즐겨찾기에 있습니다.")
        else:
            _add_fav(market, ticker, name or ticker)
            if st.session_state.pop("_fav_full", False):
                st.warning(f"즐겨찾기는 {MAX_FAVORITES}개까지입니다.")
            else:
                st.rerun()
    st.markdown("---")


def _fav_row_key(market: str, ticker: str) -> str:
    return f"{market}|{ticker}"


def _fav_bar_markup(
    css_class: str,
    meta: str,
    main: str = "",
    *,
    price: str = "",
    action: str = "",
    score: str = "",
) -> str:
    meta_e = html.escape(str(meta))
    if price or action or score:
        body = (
            "<div class='fav-main fav-main-grid'>"
            f"<span>{html.escape(price)}</span>"
            f"<span>{html.escape(action)}</span>"
            f"<span>{html.escape(score)}</span>"
            "</div>"
        )
    else:
        body = f"<div class='fav-main'>{html.escape(str(main))}</div>"
    return f"<div class='{css_class} fav-bar'><div class='fav-meta'>{meta_e}</div>{body}</div>"


def _render_fav_row(row: dict, as_of, *, analyzed: bool) -> None:
    cols = st.columns([6, 1], gap="small", vertical_alignment="center")
    safe = f"{row['market']}_{str(row.get('ticker') or '').replace('.', '_')}"
    name = row.get("name") or row.get("ticker")
    ticker = row.get("ticker")
    meta = f"{name} ({ticker})"
    css = "action-hold"
    main = "아직 분석하지 않음"
    clickable = False
    kind = "hold"
    if not analyzed:
        pass
    elif row.get("error"):
        main = f"계산 실패: {row['error']}"
    else:
        action = row.get("action") or "홀딩"
        kind = FAVBAR_KIND.get(action, "hold")
        css = ACTION_CLASS.get(action, "action-hold")
        price_txt = _fmt(row["price"]) if row.get("price") else "-"
        label = (row.get("price_label") or "").strip() or "가격"
        clickable = True
    with cols[0]:
        if clickable:
            st.markdown(
                _fav_bar_markup(
                    css,
                    meta,
                    price=f"{label} {price_txt}",
                    action=f"제안 {action}",
                    score=f"합산 {row.get('score_pct')}%",
                ),
                unsafe_allow_html=True,
            )
        else:
            st.markdown(_fav_bar_markup(css, meta, main), unsafe_allow_html=True)
        if clickable:
            if st.button(
                "이 종목 분석",
                key=f"favbar_{kind}_{safe}",
                use_container_width=True,
                help="이 종목 분석 화면으로 이동합니다.",
            ):
                st.session_state._jump_analysis = {
                    "market": row["market"],
                    "ticker": row["ticker"],
                    "name": name,
                    "as_of": as_of.isoformat(),
                }
                st.rerun()
    with cols[1]:
        st.button(
            "삭제",
            key=f"unfav_{row['market']}_{row['ticker']}",
            on_click=partial(_remove_fav, row["market"], row["ticker"]),
            use_container_width=True,
        )


def _render_favorites(as_of, lookback_days: int, timeframe: str, lookback_label: str, rule: dict) -> None:
    st.subheader("즐겨찾기")
    st.caption(
        f"조회 기간 {lookback_label} · 분석일 {as_of} · "
        "추가만 하면 목록에만 들어갑니다. **분석하기**를 눌러야 계산합니다. "
        "색 막대를 누르면 그 종목 분석으로 갑니다."
    )
    _render_fav_add()
    favs = _fav_list()
    if not favs:
        st.info("아직 즐겨찾기한 종목이 없습니다. 위에서 검색해 추가하세요.")
        return
    run = st.button("분석하기", type="primary", use_container_width=True, key="fav_run_btn")
    if run:
        results = []
        prev = st.session_state.get("fav_board") or {}
        reuse = (
            prev.get("as_of") == as_of.isoformat()
            and prev.get("lookback") == lookback_label
        )
        prev_ok = {}
        if reuse:
            for row in prev.get("results") or []:
                if row and not row.get("error"):
                    prev_ok[_fav_row_key(row.get("market"), row.get("ticker"))] = row
        bar = st.progress(0, text="즐겨찾기 계산 중...")
        n_fav = len(favs)
        failed_once = False
        for i, item in enumerate(favs, 1):
            name = item.get("name") or item.get("ticker")
            key = _fav_row_key(item["market"], item["ticker"])
            if key in prev_ok:
                row = dict(prev_ok[key])
                row["name"] = name or row.get("name") or item["ticker"]
                results.append(row)
                bar.progress(i / n_fav, text=f"{name} 유지")
                continue
            if i > 1:
                time.sleep(1.1 if failed_once else 0.35)
            bar.progress(i / n_fav, text=f"{name} 계산 중...")
            row = _quick_signal(
                item["market"],
                item["ticker"],
                as_of,
                lookback_days,
                timeframe,
                rule,
            )
            row["name"] = name or row.get("name") or item["ticker"]
            if row.get("error"):
                failed_once = True
            results.append(row)
        bar.empty()
        st.session_state.fav_board = {
            "as_of": as_of.isoformat(),
            "lookback": lookback_label,
            "results": results,
        }
        n_fail = sum(1 for r in results if r.get("error"))
        if n_fail:
            st.warning(
                f"{n_fail}종목은 시세를 받지 못했습니다. "
                "**분석하기**를 다시 누르면 실패한 종목만 다시 받습니다."
            )
    board = st.session_state.get("fav_board") or {}
    same_ctx = (
        board.get("as_of") == as_of.isoformat()
        and board.get("lookback") == lookback_label
    )
    by_key = {}
    if same_ctx:
        for row in board.get("results") or []:
            by_key[_fav_row_key(row.get("market"), row.get("ticker"))] = row
    elif board:
        st.caption("시점이나 조회 기간이 바뀌었습니다. 다시 **분석하기**를 누르면 이 조건으로 계산합니다.")
    for item in favs:
        key = _fav_row_key(item["market"], item["ticker"])
        row = by_key.get(key)
        if row:
            _render_fav_row(row, as_of, analyzed=True)
        else:
            _render_fav_row(item, as_of, analyzed=False)


def _market_name(code: str) -> str:
    for label, key in MARKETS.items():
        if key == code:
            return label
    return code or "-"


def _sim_currency(market: str) -> str:
    """KR는 원, 미국 주식·코인은 달러."""
    return "KRW" if market == "KR" else "USD"


def _fmt_ccy(amount: float, ccy: str) -> str:
    if ccy == "KRW":
        return f"{amount:+,.0f}원"
    return f"${amount:+,.2f}"


def _strategy_pnl(result) -> tuple[float, float, float]:
    invested = float(getattr(result, "invested", 0) or 0)
    pnl = float(result.realized or 0) + float(result.m2m or 0)
    pct = (pnl / invested * 100.0) if invested else 0.0
    return invested, pnl, pct


@st.cache_data(ttl=600, show_spinner=False)
def _cached_spy_hold(start_iso: str, end_iso: str) -> tuple[float | None, str]:
    return spy_hold_return(date.fromisoformat(start_iso), date.fromisoformat(end_iso))


@st.cache_data(ttl=600, show_spinner=False)
def _cached_usdkrw(as_of_iso: str) -> tuple[float | None, str, str]:
    rate, src, when = fetch_usdkrw(date.fromisoformat(as_of_iso))
    return rate, src, when.isoformat() if when else ""


def _show_spy_compare(results: list, start: date, end: date) -> None:
    spy_pct, spy_err = _cached_spy_hold(start.isoformat(), end.isoformat())
    st.subheader("S&P 500 (SPY) 비교")
    if spy_pct is None:
        st.info(f"SPY 동일기간 수익을 받지 못했습니다: {spy_err or '알 수 없음'}")
        return
    rows = []
    ok = [r for r in results if not getattr(r, "error", None)]
    if len(ok) == 1:
        invested, pnl, pct = _strategy_pnl(ok[0])
        ccy = _sim_currency(getattr(ok[0], "market", "") or "")
        rows.append(
            {
                "구분": f"{ok[0].name} 전략",
                "투입": _fmt_ccy(invested, ccy) if invested else "-",
                "손익": _fmt_ccy(pnl, ccy),
                "수익률": f"{pct:+.2f}%" if invested else "-",
            }
        )
        spy_pnl = invested * spy_pct / 100.0 if invested else 0.0
        rows.append(
            {
                "구분": "SPY 매수 후 보유",
                "투입": _fmt_ccy(invested, ccy) if invested else "-",
                "손익": _fmt_ccy(spy_pnl, ccy) if invested else "-",
                "수익률": f"{spy_pct:+.2f}%",
            }
        )
        if invested:
            rows.append(
                {
                    "구분": "차이 (전략−SPY)",
                    "투입": "-",
                    "손익": _fmt_ccy(pnl - spy_pnl, ccy),
                    "수익률": f"{pct - spy_pct:+.2f}%p",
                }
            )
    else:
        by_ccy: dict[str, dict] = {}
        for result in ok:
            ccy = _sim_currency(getattr(result, "market", "") or "")
            invested, pnl, _pct = _strategy_pnl(result)
            bucket = by_ccy.setdefault(ccy, {"invested": 0.0, "pnl": 0.0, "n": 0})
            bucket["invested"] += invested
            bucket["pnl"] += pnl
            bucket["n"] += 1
        for ccy in ("KRW", "USD"):
            bucket = by_ccy.get(ccy)
            if not bucket:
                continue
            label = "원 전략" if ccy == "KRW" else "달러 전략 (미국·코인)"
            invested = bucket["invested"]
            pnl = bucket["pnl"]
            pct = (pnl / invested * 100.0) if invested else 0.0
            spy_pnl = invested * spy_pct / 100.0 if invested else 0.0
            rows.append(
                {
                    "구분": f"{label} {bucket['n']}종목",
                    "투입": _fmt_ccy(invested, ccy) if invested else "-",
                    "손익": _fmt_ccy(pnl, ccy),
                    "수익률": f"{pct:+.2f}%" if invested else "-",
                }
            )
            rows.append(
                {
                    "구분": f"SPY 동일 투입 ({'원' if ccy == 'KRW' else '달러'})",
                    "투입": _fmt_ccy(invested, ccy) if invested else "-",
                    "손익": _fmt_ccy(spy_pnl, ccy) if invested else "-",
                    "수익률": f"{spy_pct:+.2f}%",
                }
            )
            if invested:
                rows.append(
                    {
                        "구분": f"차이 ({'원' if ccy == 'KRW' else '달러'})",
                        "투입": "-",
                        "손익": _fmt_ccy(pnl - spy_pnl, ccy),
                        "수익률": f"{pct - spy_pct:+.2f}%p",
                    }
                )
    _show_table(pd.DataFrame(rows))
    if len(ok) == 1:
        invested, pnl, pct = _strategy_pnl(ok[0])
        c1, c2, c3 = st.columns(3)
        c1.metric("전략 수익률", f"{pct:+.2f}%" if invested else "-", f"{pct - spy_pct:+.2f}%p vs SPY" if invested else None)
        c2.metric("SPY 보유", f"{spy_pct:+.2f}%")
        beat = "지수보다 나음" if invested and pct >= spy_pct else "지수보다 못함"
        c3.metric("상대평가", beat if invested else "-")
        g1, g2 = st.columns(2)
        with g1:
            st.plotly_chart(
                build_return_vs_spy_fig(pct, spy_pct, ok[0].name or "전략"),
                use_container_width=True,
            )
        with g2:
            st.plotly_chart(
                build_pnl_split_fig(float(ok[0].realized or 0), float(ok[0].m2m or 0), "손익 구성"),
                use_container_width=True,
            )
    else:
        names, pcts = [], []
        for result in ok:
            invested, _pnl, pct = _strategy_pnl(result)
            if invested:
                names.append(str(result.name or result.ticker))
                pcts.append(pct)
        if names:
            st.plotly_chart(
                build_ticker_vs_spy_fig(names, pcts, spy_pct),
                use_container_width=True,
            )
        metric_ccy = [c for c in ("KRW", "USD") if c in by_ccy]
        if metric_ccy:
            cards = st.columns(len(metric_ccy))
            for i, ccy in enumerate(metric_ccy):
                bucket = by_ccy[ccy]
                invested = bucket["invested"]
                pnl = bucket["pnl"]
                pct = (pnl / invested * 100.0) if invested else 0.0
                label = "원" if ccy == "KRW" else "달러"
                cards[i].metric(
                    f"{label} 전략 vs SPY",
                    f"{pct:+.2f}%" if invested else "-",
                    f"{pct - spy_pct:+.2f}%p" if invested else None,
                )
    st.caption(
        f"{start} ~ {end} SPY(SPDR S&P 500 ETF)를 기간 첫날 사서 끝날까지 보유한 수익률과 비교합니다. "
        "초록은 지수 이상, 빨강은 지수 미만. 전략 수익률은 매수 투입 대비 (실현+평가) 손익입니다. "
        "원 표시는 환율 없이 같은 %만 견줍니다."
    )


def _sim_qty_caption(start: date, end: date, lookback_label: str, sim: dict) -> str:
    return (
        f"{start} ~ {end} · 조회 {lookback_label} · "
        f"매수 {sim['buy_weak']}/{sim['buy_mid']}/{sim['buy_strong']}주 · "
        f"잔량 {sim['share_cut']}주 이상 "
        f"{sim['sell_weak_pct']}/{sim['sell_mid_pct']}/{sim['sell_strong_pct']}% · "
        f"미만 {sim['sell_weak_qty']}/{sim['sell_mid_qty']}/{sim['sell_strong_qty']}주"
    )


def _show_sim_result(result, *, with_spy: bool = True) -> None:
    if result.error:
        st.error(result.error)
        return
    ccy = _sim_currency(getattr(result, "market", "") or "")
    _kv_table(
        [
            ("종목", f"{result.name} ({result.ticker})"),
            ("잔량", f"{result.shares:,}주"),
            ("평단", _fmt(result.avg) if result.shares else "-"),
            ("종료가", _fmt(result.last_px)),
            ("평가손익", f"{_fmt_ccy(result.m2m, ccy)} ({result.m2m_pct:+.2f}%)"),
            ("실현손익", _fmt_ccy(result.realized, ccy)),
            ("거래일", f"{result.days}일"),
        ]
    )
    counts = result.counts or {}
    st.caption(
        f"{result.name} ({result.ticker}) · 거래일 {result.days}일 · "
        + " · ".join(f"{k} {v}" for k, v in counts.items())
    )
    trades = result.trades or []
    marks = list(getattr(result, "signals", None) or [])
    if not marks:
        marks = [
            t
            for t in trades
            if str(t.get("체결")) in ("매수", "매도", "잔량0")
        ]
    chart_df = getattr(result, "chart_df", None)
    if chart_df is not None and not getattr(chart_df, "empty", True):
        st.plotly_chart(
            build_sim_chart(
                chart_df,
                marks,
                f"{result.name} · {result.start} ~ {result.end}",
            ),
            use_container_width=True,
        )
        st.caption("초록 ▲ 매수 신호 · 빨강 ▼ 매도 신호. 약한/보통/강할수록 마커가 큽니다.")
    else:
        st.info("기간 차트를 그릴 일봉이 없습니다. 시뮬레이션을 다시 실행하세요.")
    buys = [t for t in trades if str(t.get("체결")) == "매수"]
    sells = [t for t in trades if str(t.get("체결")) in ("매도", "잔량0")]

    def _trade_table(rows: list) -> pd.DataFrame:
        df_t = pd.DataFrame(rows)
        if df_t.empty:
            return df_t
        if "가격" in df_t.columns:
            df_t["가격"] = df_t["가격"].map(lambda x: f"{x:,.0f}")
        if "평단" in df_t.columns:
            df_t["평단"] = df_t["평단"].map(lambda x: f"{x:,.0f}")
        return df_t

    st.markdown(f"**매수 신호 ({len(buys)})**")
    if buys:
        _show_table(_trade_table(buys))
        st.caption(" · ".join(t["날짜"] for t in buys))
    else:
        st.info("매수 신호가 없습니다.")
    st.markdown(f"**매도 신호 ({len(sells)})**")
    if sells:
        _show_table(_trade_table(sells))
        filled = [t for t in sells if t.get("체결") == "매도"]
        skipped = [t for t in sells if t.get("체결") == "잔량0"]
        if filled:
            st.caption("체결: " + " · ".join(t["날짜"] for t in filled))
        if skipped:
            st.caption("잔량 0: " + " · ".join(t["날짜"] for t in skipped))
    else:
        st.info("매도 신호가 없습니다.")
    if with_spy and not result.error:
        _show_spy_compare([result], result.start, result.end)


def _show_sim_favorites(results: list) -> None:
    rows = []
    totals: dict[str, dict] = {}
    for result in results:
        market = getattr(result, "market", "") or ""
        ccy = _sim_currency(market)
        if result.error:
            rows.append(
                {
                    "종목": result.name or result.ticker,
                    "시장": _market_name(market),
                    "잔량": "-",
                    "평단": "-",
                    "종료가": "-",
                    "평가손익": "-",
                    "실현손익": "-",
                    "비고": result.error,
                }
            )
            continue
        bucket = totals.setdefault(ccy, {"m2m": 0.0, "realized": 0.0, "n": 0})
        bucket["m2m"] += float(result.m2m or 0)
        bucket["realized"] += float(result.realized or 0)
        bucket["n"] += 1
        rows.append(
            {
                "종목": f"{result.name} ({result.ticker})",
                "시장": _market_name(market),
                "잔량": f"{result.shares:,}주",
                "평단": _fmt(result.avg) if result.shares else "-",
                "종료가": _fmt(result.last_px),
                "평가손익": f"{_fmt_ccy(result.m2m, ccy)} ({result.m2m_pct:+.2f}%)",
                "실현손익": _fmt_ccy(result.realized, ccy),
                "비고": "",
            }
        )
    st.subheader("종목 요약")
    _show_table(pd.DataFrame(rows))
    if totals:
        st.subheader("통화별 합계")
        ccy_rows = []
        for ccy in ("KRW", "USD"):
            bucket = totals.get(ccy)
            if not bucket:
                continue
            label = "원 (한국 주식)" if ccy == "KRW" else "달러 (미국 주식·코인)"
            m2m = bucket["m2m"]
            realized = bucket["realized"]
            ccy_rows.append(
                {
                    "통화": label,
                    "종목 수": f"{bucket['n']}종목",
                    "평가손익": _fmt_ccy(m2m, ccy),
                    "실현손익": _fmt_ccy(realized, ccy),
                    "평가+실현": _fmt_ccy(m2m + realized, ccy),
                }
            )
        starts = [r.start for r in results if getattr(r, "start", None)]
        ends = [r.end for r in results if getattr(r, "end", None)]
        as_of_end = max(ends) if ends else None
        rate = None
        rate_src = ""
        rate_day = ""
        if as_of_end:
            rate, rate_src, rate_day = _cached_usdkrw(as_of_end.isoformat())
        krw_b = totals.get("KRW") or {"m2m": 0.0, "realized": 0.0, "n": 0}
        usd_b = totals.get("USD") or {"m2m": 0.0, "realized": 0.0, "n": 0}
        if rate:
            n_all = int(krw_b["n"]) + int(usd_b["n"])
            krw_m2m = float(krw_b["m2m"]) + float(usd_b["m2m"]) * rate
            krw_real = float(krw_b["realized"]) + float(usd_b["realized"]) * rate
            usd_m2m = float(usd_b["m2m"]) + float(krw_b["m2m"]) / rate
            usd_real = float(usd_b["realized"]) + float(krw_b["realized"]) / rate
            ccy_rows.append(
                {
                    "통화": "합계 (원)",
                    "종목 수": f"{n_all}종목",
                    "평가손익": _fmt_ccy(krw_m2m, "KRW"),
                    "실현손익": _fmt_ccy(krw_real, "KRW"),
                    "평가+실현": _fmt_ccy(krw_m2m + krw_real, "KRW"),
                }
            )
            ccy_rows.append(
                {
                    "통화": "합계 (달러)",
                    "종목 수": f"{n_all}종목",
                    "평가손익": _fmt_ccy(usd_m2m, "USD"),
                    "실현손익": _fmt_ccy(usd_real, "USD"),
                    "평가+실현": _fmt_ccy(usd_m2m + usd_real, "USD"),
                }
            )
        _show_table(pd.DataFrame(ccy_rows))
        if rate:
            used = rate_day or str(as_of_end)
            extra = f", {used}" if str(used) != str(as_of_end) else ""
            st.caption(
                f"코인은 달러 기준입니다. 합계는 조회 종료일 {as_of_end} 원/달러 "
                f"{rate:,.2f}원({rate_src}{extra})으로 달러를 환산했습니다."
            )
            m1, m2 = st.columns(2)
            m1.metric("합계 평가+실현 (원)", _fmt_ccy(krw_m2m + krw_real, "KRW"))
            m2.metric("합계 평가+실현 (달러)", _fmt_ccy(usd_m2m + usd_real, "USD"))
        else:
            st.caption(
                "코인은 달러 기준입니다. 원/달러 환율을 받지 못해 원·달러 합계는 표시하지 않습니다."
            )
        if starts and ends:
            _show_spy_compare(results, min(starts), max(ends))
    for result in results:
        title = f"{result.name} ({result.ticker})"
        if result.error:
            title += " · 실패"
        else:
            ccy = _sim_currency(getattr(result, "market", "") or "")
            title += f" · 평가 {_fmt_ccy(result.m2m, ccy)}"
        with st.expander(title, expanded=False):
            _show_sim_result(result, with_spy=False)


def _render_simulation(
    market: str,
    ticker: str,
    display_name: str,
    start: date,
    end: date,
    lookback_days: int,
    timeframe: str,
    lookback_label: str,
    rule: dict,
    sim: dict,
    run: bool,
    scope: str = "선택한 종목",
) -> None:
    st.subheader("시뮬레이션")
    st.caption(
        "매일 조회 기간만큼만 보고 신호를 낸 뒤, 설정한 수량으로 사고팝니다. "
        "평가 배점·매수/매도 컷은 왼쪽 값을 그대로 씁니다."
    )
    qty_txt = _sim_qty_caption(start, end, lookback_label, sim)
    favs = _fav_list()
    all_fav = scope == "즐겨찾기 전체"

    if all_fav:
        if not favs:
            st.info("아직 즐겨찾기한 종목이 없습니다. 종목 분석 화면에서 별표로 추가하세요.")
            return
        st.write(f"**즐겨찾기 {len(favs)}종목** · {start} ~ {end} · 조회 {lookback_label} · 종목별 수량")
        if run:
            bar = st.progress(0, text="즐겨찾기 시뮬레이션 중...")
            out = []
            n_fav = len(favs)
            for i, item in enumerate(favs):
                name = item.get("name") or item.get("ticker")
                item_sim = normalize_sim(item.get("sim") or sim)
                def _prog(j, n, as_of, i=i, name=name, n_fav=n_fav):
                    bar.progress(
                        min((i + j / max(n, 1)) / max(n_fav, 1), 1.0),
                        text=f"{name} {as_of} ({i + 1}/{n_fav})",
                    )
                with st.spinner(f"{name} 계산 중..."):
                    result = run_backtest(
                        item["market"],
                        item["ticker"],
                        start,
                        end,
                        lookback_days,
                        timeframe,
                        lookback_label,
                        rule,
                        item_sim,
                        progress=_prog,
                    )
                if item.get("name"):
                    result.name = item["name"]
                out.append(result)
            bar.empty()
            st.session_state.sim_fav_results = out
            st.session_state.sim_result = None
        results = st.session_state.get("sim_fav_results")
        if not results:
            st.info("왼쪽에서 기간·수량을 정한 뒤 **시뮬레이션 실행**을 누르세요.")
            return
        _show_sim_favorites(results)
        return

    if not ticker:
        st.info("왼쪽에서 종목을 고르세요.")
        return
    st.write(f"**{display_name or ticker}** · {qty_txt}")
    if run:
        bar = st.progress(0, text="시세 수집 및 일별 계산 중...")

        def _prog(i, n, as_of):
            bar.progress(min(i / max(n, 1), 1.0), text=f"{as_of} ({i}/{n})")

        with st.spinner("시뮬레이션 실행 중..."):
            st.session_state.sim_result = run_backtest(
                market,
                ticker,
                start,
                end,
                lookback_days,
                timeframe,
                lookback_label,
                rule,
                sim,
                progress=_prog,
            )
        bar.empty()
        st.session_state.sim_fav_results = None

    result = st.session_state.get("sim_result")
    if not result:
        st.info("왼쪽에서 기간·수량을 정한 뒤 **시뮬레이션 실행**을 누르세요.")
        return
    _show_sim_result(result)


def _apply_analysis_jump() -> None:
    jump = st.session_state.pop("_jump_analysis", None)
    if not jump:
        return
    market = str(jump.get("market") or "")
    ticker = str(jump.get("ticker") or "")
    name = str(jump.get("name") or ticker)
    st.session_state.app_page = "종목 분석"
    for label, code in MARKETS.items():
        if code == market:
            st.session_state.market_pick = label
            break
    if market == "KR":
        preset_map = {_preset_label(c, n): c for c, n in KR_PRESETS}
        reverse = {code: lab for lab, code in preset_map.items()}
        if ticker in reverse:
            st.session_state.kr_preset = reverse[ticker]
            st.session_state.kr_search = ""
        else:
            st.session_state.kr_search = ticker
    elif market == "US":
        st.session_state.us_custom = ticker
    else:
        st.session_state.crypto_custom = ticker
    st.session_state._jump_name = name
    as_of_raw = str(jump.get("as_of") or "")
    if as_of_raw:
        try:
            st.session_state[f"as_of_{market}"] = date.fromisoformat(as_of_raw[:10])
        except ValueError:
            pass
    st.session_state._auto_run = True


with st.sidebar:
    _apply_analysis_jump()
    page = st.radio("화면", ["종목 분석", "즐겨찾기", "시뮬레이션"], horizontal=True, key="app_page")
    st.header("조회 조건")
    sim_scope = "선택한 종목"
    if page == "시뮬레이션":
        sim_scope = st.radio(
            "대상",
            ["선택한 종목", "즐겨찾기 전체"],
            horizontal=True,
            key="sim_scope",
        )
        if sim_scope == "즐겨찾기 전체":
            st.caption(f"즐겨찾기 {len(_fav_list())}종목을 같은 기간·수량으로 각각 돌립니다.")
    pick_one = page != "시뮬레이션" or sim_scope == "선택한 종목"
    market = "KR"
    ticker = ""
    display_name = ""
    if pick_one:
        market_label = st.radio("시장", list(MARKETS.keys()), horizontal=False, key="market_pick")
        market = MARKETS[market_label]

    if pick_one and market == "KR":
        preset_map = {_preset_label(c, n): c for c, n in KR_PRESETS}
        choice = st.selectbox("대표 종목", list(preset_map.keys()), key="kr_preset")
        query = st.text_input("종목명 또는 코드 검색", placeholder="예: 삼성전자, 005930", key="kr_search")
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
    elif pick_one and market == "US":
        preset_map = {_preset_label(c, n): c for c, n in US_PRESETS}
        choice = st.selectbox("대표 종목", list(preset_map.keys()), key="us_preset")
        custom = st.text_input("티커 직접 입력", placeholder="예: AAPL, NVDA", key="us_custom")
        if custom.strip():
            ticker = custom.strip().upper()
            display_name = ticker
        else:
            ticker = preset_map[choice]
            display_name = choice
    elif pick_one:
        cmap = {label: key for key, label in crypto_choices()}
        labels = [label for _, label in crypto_choices()]
        choice = st.selectbox("코인", labels, index=0, key="crypto_preset")
        custom = st.text_input("심볼 직접 입력", placeholder="예: BTC, ETH, ONDO", key="crypto_custom")
        if custom.strip():
            ticker = custom.strip().upper()
            info = CRYPTO.get(ticker)
            display_name = f"{info['name']} ({ticker})" if info else ticker
        else:
            ticker = cmap[choice]
            display_name = choice

    today_m = market_today(market) if pick_one else date.today()
    as_of = today_m
    sim_start = today_m - timedelta(days=150)
    sim_end = today_m
    if page == "시뮬레이션":
        d1, d2 = st.columns(2)
        date_key = market if pick_one else "fav"
        with d1:
            sim_start = st.date_input(
                "시작일",
                value=sim_start,
                max_value=today_m,
                key=f"sim_start_{date_key}",
            )
        with d2:
            sim_end = st.date_input(
                "종료일",
                value=today_m,
                max_value=today_m,
                key=f"sim_end_{date_key}",
            )
    else:
        as_of = st.date_input(
            "분석 시점",
            value=today_m,
            max_value=today_m,
            key=f"as_of_{market}",
        )
    lookback_keys = list(LOOKBACK_OPTIONS.keys())
    lookback_label = st.selectbox(
        "조회 기간",
        lookback_keys,
        index=lookback_keys.index("3개월" if page == "시뮬레이션" else "1년"),
        key="lookback_sim" if page == "시뮬레이션" else "lookback_v2",
    )
    lookback_spec = resolve_lookback(lookback_label)
    lookback_days = int(lookback_spec["days"])
    timeframe = str(lookback_spec["timeframe"])

    _init_rule_widgets()
    with st.expander("평가 배점·기준", expanded=False):
        st.caption("합산 % 눈금(-5~19점)은 그대로 두고, 항목 점수와 매수/매도 컷만 바꿉니다. 바꾼 값은 리부트 후에도 남깁니다.")
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
                default = int(DEFAULT_WEIGHTS[key])
                if key == "base":
                    lo, hi = 0, 30
                elif default >= 0:
                    lo, hi = 0, 10
                else:
                    lo, hi = -10, 10
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
    sim = dict(DEFAULT_SIM)
    if page == "시뮬레이션":
        with st.expander("기본 매매 수량", expanded=sim_scope != "즐겨찾기 전체"):
            st.caption(
                "약한 매수 / 매수 / 강한 매수 주 수, 잔량 기준 이상이면 % 매도, 미만이면 주 수. "
                "바꾼 값은 저장됩니다."
            )
            _sim_qty_fields("s_")
            st.button("수량 기본값", use_container_width=True, on_click=_reset_sim_widgets)
        sim = _read_sim_from_sidebar()
        if sim_scope == "즐겨찾기 전체":
            favs_now = _fav_list()
            _init_fav_sim_widgets(sim)
            with st.expander("종목별 매매 수량", expanded=True):
                if not favs_now:
                    st.caption("즐겨찾기가 없습니다.")
                else:
                    st.caption("종목마다 따로 정한 수량으로 시뮬레이션하고, 즐겨찾기와 같이 저장됩니다.")
                    st.button(
                        "위 기본 수량을 모든 종목에 넣기",
                        use_container_width=True,
                        on_click=_copy_global_sim_to_favs,
                    )
                    for item in favs_now:
                        label = f"{item.get('name') or item['ticker']} ({item['ticker']})"
                        with st.expander(label, expanded=False):
                            _sim_qty_fields(_fav_sim_prefix(item["market"], item["ticker"]))
                            st.button(
                                "이 종목 기본값",
                                key=f"reset_sim_{item['market']}_{item['ticker']}",
                                use_container_width=True,
                                on_click=partial(_reset_one_fav_sim, item["market"], item["ticker"]),
                            )
            _sync_fav_sims()
    rule = _read_rule_from_sidebar()
    _persist_prefs(rule)
    _emit_prefs_cookie()
    uid_show = format_uid(st.session_state.get("prefs_uid") or "")
    st.caption(f"내 저장 코드: **{uid_show or '-'}**")
    with st.expander(
        "다른 기기에서 이어가기",
        expanded=not remote_enabled() or not st.session_state.get("_prefs_remote_ok"),
    ):
        if remote_enabled():
            st.caption(
                "클라우드에 연결되어 있습니다. 이 코드를 폰·다른 PC에 넣으면 "
                "리부트 후에도 같은 즐겨찾기가 불러와집니다."
            )
            if st.button("지금 클라우드에 저장", use_container_width=True):
                st.session_state._prefs_force_remote = True
                st.rerun()
            if st.session_state.get("_prefs_remote_ok"):
                st.caption("클라우드 저장 완료.")
            err = remote_last_error()
            if err:
                st.warning(err)
        else:
            st.caption("지금 Secrets에 GitHub 토큰이 없어, 리부트 후 다른 기기에서는 불러올 수 없습니다. 한 번만 연결하면 됩니다.")
            st.markdown(
                "1. [GitHub 토큰 만들기](https://github.com/settings/tokens) → **Generate new token (classic)**  \n"
                "2. 권한에서 **gist** 만 체크하고 생성, 나온 `ghp_...` 를 복사  \n"
                "3. 앱 오른쪽 아래 **Manage app → Settings → Secrets** 에 아래를 넣고 Save  \n"
                "4. **Reboot app** 한 뒤, 즐겨찾기가 있는 기기에서 **지금 클라우드에 저장**"
            )
            st.code('GITHUB_TOKEN = "ghp_여기에붙여넣기"', language="toml")
        st.text_input("저장 코드", key="prefs_code_in", placeholder="예: AB3K-9M2Q")
        if st.button("이 코드로 불러오기", use_container_width=True):
            other = normalize_uid(st.session_state.get("prefs_code_in"))
            if not other:
                st.session_state._prefs_adopt_msg = "코드 형식이 아닙니다. 8자리입니다."
            elif other == normalize_uid(st.session_state.get("prefs_uid")) and _fav_list():
                st.session_state._prefs_adopt_msg = "이미 이 코드로 연결되어 있습니다."
            else:
                st.session_state._prefs_adopt = other
                st.session_state._prefs_boot = False
                st.rerun()
        msg = st.session_state.get("_prefs_adopt_msg")
        if msg:
            st.info(msg)
    run = False
    run_sim = False
    if page == "종목 분석":
        run = st.button("분석하기", type="primary", use_container_width=True)
        if st.session_state.pop("_auto_run", False):
            run = True
    elif page == "시뮬레이션":
        run_sim = st.button("시뮬레이션 실행", type="primary", use_container_width=True)


if page == "즐겨찾기":
    _render_favorites(as_of, lookback_days, timeframe, lookback_label, rule)
    st.stop()

if page == "시뮬레이션":
    _render_simulation(
        market,
        ticker,
        display_name,
        sim_start,
        sim_end,
        lookback_days,
        timeframe,
        lookback_label,
        rule,
        sim,
        run_sim,
        sim_scope,
    )
    st.stop()

if not run:
    st.info("왼쪽에서 시장·종목·시점을 고른 뒤 **분석하기**를 누르세요.")
    st.markdown(
        """
        #### 이 프로그램이 하는 일
        1. 한국 주식, 미국 주식, 비트코인·이더리움·솔라나·XRP·온도 등 원하는 종목을 고릅니다.
        2. **과거 특정 날짜**를 시점으로 넣으면 그 날 이후 시세는 보지 않습니다.
        3. 그 시점의 추세선, 지지/저항, 주요 매물대를 그린 뒤 매수·매도·홀딩을 제안합니다.
        4. 종목을 즐겨찾기에 넣으면 한 화면에서 제안만 모아 볼 수 있습니다.
        5. 시뮬레이션 화면에서 한 종목 또는 즐겨찾기 전체를 돌립니다. 즐겨찾기는 종목별 수량을 따로 저장합니다.

        1개월은 1시간봉, 2·3개월은 4시간봉, 6개월·1년은 일봉으로 계산합니다.
        평가 배점·즐겨찾기·시뮬레이션 수량은 접속자마다 따로 저장됩니다. 다른 기기는 저장 코드로 이어갑니다.
        """
    )
    st.stop()

if not ticker:
    st.error("종목을 선택하세요.")
    st.stop()

bar_name = {"1h": "1시간봉", "4h": "4시간봉"}.get(timeframe, "일봉")
with st.spinner(f"{display_name or ticker} / {as_of} {bar_name} 수집 중..."):
    try:
        df, meta = _load_ohlcv(market, ticker, as_of, lookback_days, timeframe, retries=2)
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
is_live = as_of == market_today(market)
if is_live:
    try:
        spot_price, spot_source = fetch_spot_price(market, ticker)
    except Exception:
        spot_price, spot_source = None, ""
    if spot_price is None:
        spot_price = last_bar_price
        spot_source = "실시간 시세를 못 받아 최근 봉 가격 사용"
        st.warning("실시간 현재가를 받지 못해 마지막 봉 가격으로 계산합니다.")
    df = drop_incomplete_session(df, as_of)
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
        live=is_live,
    )
    six_month_chg = None
    try:
        src_6m = df
        if lookback_days < 180:
            src_6m, _meta_6m = _load_ohlcv(market, ticker, as_of, 180, "1d", retries=1)
        six_month_chg = period_return(src_6m, as_of, spot_price, 180)
    except Exception:
        six_month_chg = None
    signal = _make_signal(
        analysis,
        six_month_chg,
        lookback_days,
        rule,
    )
except Exception as exc:
    st.error(f"분석 실패: {exc}")
    st.stop()

try:
    fund = fetch_fundamentals(market, ticker, signal.action)
except Exception as exc:
    fund = None
    fund_fetch_error = str(exc)
else:
    fund_fetch_error = None

value_side = ""
if fund is not None and not fund.error and fund.value_label:
    value_side = f" · 가격 {fund.value_label}"

action_class = ACTION_CLASS.get(signal.action, "action-hold")
six_txt = (
    f" · 6개월 가격 {six_month_chg * 100:+.1f}%"
    if six_month_chg is not None
    else ""
)
st.markdown(
    f"""
    <div class="{action_class}">
      <div style="font-size:0.85rem;opacity:0.8">{meta.get("name") or display_name} ·
      분석일 {as_of} · {meta.get("bar", bar_name)} · {analysis.price_label} 기준
      {(" · " + analysis.price_source) if analysis.price_source else ""}</div>
      <div style="font-size:1.25rem;font-weight:700;margin:0.25rem 0">제안 {signal.action}{value_side}</div>
      <div style="font-size:0.95rem">{signal.summary}</div>
    </div>
    """,
    unsafe_allow_html=True,
)

price_cell = _fmt(analysis.price)
if is_live and last_bar_price and abs(float(analysis.price) - last_bar_price) > 1e-9:
    price_cell = f"{_fmt(analysis.price)} (봉 {_fmt(last_bar_price)})"
_kv_table(
    [
        ("제안", signal.action),
        ("가격 판정", (fund.value_label if fund and not fund.error else None) or "-"),
        ("합산", f"{signal.score_pct}%{six_txt}"),
        ("신뢰", f"{signal.confidence}%"),
        (analysis.price_label, price_cell),
        ("지지", _fmt(signal.nearest_support.price) if signal.nearest_support else "-"),
        ("저항", _fmt(signal.nearest_resistance.price) if signal.nearest_resistance else "-"),
        ("POC", _fmt(analysis.poc)),
        ("VAL", _fmt(analysis.val)),
        ("VAH", _fmt(analysis.vah)),
        ("RSI", f"{analysis.rsi:.1f}"),
        ("손익비", f"{signal.reward_risk:.2f}" if signal.reward_risk is not None else "-"),
        ("손절 참고", _fmt(signal.stop) if signal.stop else "-"),
        ("1차 목표", _fmt(signal.target) if signal.target else "-"),
    ]
)

if _is_fav(market, ticker):
    st.button(
        "즐겨찾기 해제",
        use_container_width=True,
        on_click=partial(_remove_fav, market, ticker),
    )
else:
    st.button(
        "★ 즐겨찾기 추가",
        use_container_width=True,
        on_click=partial(_add_fav, market, ticker, display_name or ticker),
    )
if st.session_state.pop("_fav_full", False):
    st.warning(f"즐겨찾기는 {MAX_FAVORITES}개까지입니다.")

st.subheader("점수 내역")
if signal.score_rows:
    _show_table(pd.DataFrame(signal.score_rows))

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
_show_table(pd.DataFrame(rows))

last_txt = (
    analysis.last_bar.strftime("%Y-%m-%d %H:%M")
    if timeframe in ("1h", "4h")
    else str(analysis.last_bar.date())
)
title = f"{meta.get('name', ticker)} ({meta.get('ticker', ticker)})  ·  {bar_name}  ·  {analysis.price_label} {_fmt(analysis.price)}"
st.plotly_chart(
    build_chart(analysis, signal, title),
    use_container_width=True,
    config={"displayModeBar": False, "responsive": True},
)

st.caption(
    f"데이터: {meta.get('source')} · {bar_name} · "
    f"{df.index[0]} ~ {df.index[-1]} ({len(df)}봉) · "
    f"제안 기준: {analysis.price_label} {_fmt(analysis.price)}"
    + (f" ({analysis.price_source})" if analysis.price_source else "")
    + (f" · 봉 종가 {_fmt(analysis.bar_close)}" if analysis.bar_close else "")
    + " · 지정일 이후 시세는 포함하지 않습니다. "
    "매물대는 각 봉의 고가~저가에 거래량을 나눠 쌓은 근사치입니다."
)

st.subheader("펀더멘털")
if fund is None and fund_fetch_error:
    st.info(f"펀더 요약을 불러오지 못했습니다: {fund_fetch_error}")
if fund is not None:
    if fund.error:
        st.info(fund.error)
    else:
        verdict = fund.value_label or "판단 보류"
        st.markdown(f"**가격 판정: {verdict}**")
        if fund.per_vs_industry is not None:
            st.caption(f"업종 대비 실적 PER {fund.per_vs_industry:.2f}배 · 기술적 점수와는 별개입니다.")
        if fund.value_reasons:
            _show_table(pd.DataFrame({"판정 근거": fund.value_reasons}))
        for note in fund.warnings:
            st.warning(note)
        gap_label = f"{fund.per_gap:+.2f}배" if fund.per_gap is not None else "-"
        fund_rows = [
            ("실적 PER", fmt_per(fund.per)),
            ("추정 PER", fmt_per(fund.forward_per)),
            ("추정−실적", gap_label),
            ("업종 PER", fmt_per(fund.industry_per)),
            ("PBR", fmt_per(fund.pbr)),
            ("시총", fund.market_cap or "-"),
            ("배당수익률", fmt_pct(fund.dividend_yield)),
            ("외인소진율", fmt_pct(fund.foreign_rate)),
            (
                "매출이익률",
                fmt_pct(fund.sales_margin)
                + (f" ({fund.sales_margin_asof})" if fund.sales_margin_asof else ""),
            ),
            ("매출이익률 증감", fmt_pp(fund.sales_profit_yoy)),
            ("영업이익률", fmt_pct(fund.op_margin)),
            ("영업이익률 증감", fmt_pp(fund.op_yoy)),
        ]
        if fund.eps is not None:
            fund_rows.append(("EPS", _fmt(fund.eps)))
        if fund.forward_eps is not None:
            fund_rows.append(("추정 EPS", _fmt(fund.forward_eps)))
        if fund.high_52w or fund.low_52w:
            fund_rows.append(("52주", f"{fund.low_52w or '-'} ~ {fund.high_52w or '-'}"))
        if fund.per_asof:
            fund_rows.append(("실적 기준", fund.per_asof))
        _kv_table(fund_rows)
        st.caption(per_gap_text(fund))
        if fund.summary:
            st.write(fund.summary)
        if fund.quarters:
            _show_table(_transpose_records(fund.quarters))
        st.caption(
            f"출처: {fund.source}. 최근 공시·컨센서스 기준이며 지정일 과거 재무를 재구성하지 않습니다. "
            "기술적 점수에는 넣지 않고 경고만 표시합니다."
        )
