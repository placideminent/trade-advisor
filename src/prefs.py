"""배점·즐겨찾기·시뮬레이션 저장.

접속자마다 저장 코드(uid)가 있고, 파일·Gist 는 uid 칸만 읽고 쓴다.
같은 코드를 다른 기기에 넣으면 그 칸을 이어받는다.

Streamlit Cloud Reboot 은 컨테이너 파일을 지우므로
브라우저 쿠키와 (있으면) GitHub Gist 로 복구한다.
쿠키 iframe 은 첫 실행에 그리지 않는다.
"""

from __future__ import annotations

import base64
import gzip
import json
import os
import secrets
import threading
from pathlib import Path

import requests

from .signals import (
    DEFAULT_CUTS,
    DEFAULT_CUTS_CRYPTO,
    DEFAULT_CUTS_STOCK,
    DEFAULT_WEIGHTS,
    SIGNAL_RULE_VERSION,
    _copy_cuts,
    migrate_sell_cuts,
)

BROWSER_PENDING = "__pending__"

_TA_STORE_JS = """
export default function (component) {
  const { data, setStateValue } = component;
  const uidKey = "ta_uid";
  const prefsKey = "ta_prefs";
  try {
    if (data && data.mode === "set" && data.uid) {
      localStorage.setItem(uidKey, data.uid);
      if (data.token) {
        localStorage.setItem(prefsKey + "_" + data.uid, data.token);
        localStorage.setItem(prefsKey, data.token);
      }
    }
    const uid = localStorage.getItem(uidKey) || "";
    let token = "";
    if (uid) {
      token = localStorage.getItem(prefsKey + "_" + uid) || localStorage.getItem(prefsKey) || "";
    }
    setStateValue("uid", uid);
    setStateValue("token", token);
  } catch (e) {
    setStateValue("uid", "");
    setStateValue("token", "");
  }
}
"""

_TA_STORE = None


def _browser_store():
    global _TA_STORE
    if _TA_STORE is not None:
        return _TA_STORE
    try:
        import streamlit as st

        _TA_STORE = st.components.v2.component(
            "ta_store",
            html="<div></div>",
            js=_TA_STORE_JS,
        )
    except Exception:
        _TA_STORE = False
    return _TA_STORE


def read_browser_store() -> dict:
    """컴포넌트 복구는 앱을 멈출 수 있어 여기서는 읽지 않는다. 쿠키·주소·st.html 저장을 쓴다."""
    return {"uid": "", "token": "", "pending": False}


def write_browser_store(uid: str, token: str = "") -> None:
    """uid 기록은 cookie_set_html / st.html 이 담당한다."""
    return

try:
    from .backtest import DEFAULT_SIM, normalize_sim
except ImportError:
    DEFAULT_SIM = {
        "buy_weak": 5,
        "buy_mid": 10,
        "buy_strong": 15,
        "share_cut": 30,
        "sell_weak_pct": 10,
        "sell_mid_pct": 20,
        "sell_strong_pct": 30,
        "sell_weak_qty": 3,
        "sell_mid_qty": 5,
        "sell_strong_qty": 10,
    }

    def normalize_sim(raw: dict | None) -> dict:
        data = dict(DEFAULT_SIM)
        if not isinstance(raw, dict):
            return data
        for key, default in DEFAULT_SIM.items():
            if key not in raw:
                continue
            try:
                data[key] = float(raw[key]) if key in (
                    "buy_weak", "buy_mid", "buy_strong", "share_cut",
                    "sell_weak_qty", "sell_mid_qty", "sell_strong_qty",
                ) else int(raw[key])
            except (TypeError, ValueError):
                data[key] = default
        return data

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

COOKIE_NAME = "ta_prefs"
UID_COOKIE_NAME = "ta_uid"
QUERY_UID = "_u"
QUERY_PREFS = "_p"
QUERY_LS = "_ls"
QUERY_ADOPT = "_a"
COOKIE_MAX_LEN = 3500
MAX_FAVORITES = 20
MAX_USERS = 200
PREFS_VERSION = 1
STORE_VERSION = 2
UID_LEN = 8
UID_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
GIST_FILENAME = "trade-advisor-prefs.json"
GIST_DESC = "trade-advisor-prefs"
_GIST_ID_CACHE: str | None = None
_FILE_LOCK = threading.Lock()
_LAST_REMOTE_ERROR = ""


def new_uid() -> str:
    return "".join(secrets.choice(UID_ALPHABET) for _ in range(UID_LEN))


def normalize_uid(value: str | None) -> str:
    if not value:
        return ""
    raw = str(value).strip().upper().replace(" ", "").replace("-", "")
    if len(raw) != UID_LEN:
        return ""
    if any(ch not in UID_ALPHABET for ch in raw):
        return ""
    return raw


def format_uid(uid: str) -> str:
    uid = normalize_uid(uid)
    if len(uid) != UID_LEN:
        return uid
    return f"{uid[:4]}-{uid[4:]}"


def _empty() -> dict:
    return {
        "v": PREFS_VERSION,
        "uid": "",
        "ts": 0,
        "weights": dict(DEFAULT_WEIGHTS),
        "cuts": dict(DEFAULT_CUTS_STOCK),
        "cuts_crypto": dict(DEFAULT_CUTS_CRYPTO),
        "sim": dict(DEFAULT_SIM),
        "sim_options": 0,
        "rule_ver": SIGNAL_RULE_VERSION,
        "favorites": [],
        "plan": {"months": 12, "monthly_krw": 0, "drift_pp": 5, "weights": {}},
    }


def _normalize(raw: dict | None) -> dict:
    data = _empty()
    if not isinstance(raw, dict):
        return data
    weights = raw.get("weights") or {}
    cuts = raw.get("cuts") or {}
    sim = raw.get("sim") or {}
    favs = raw.get("favorites") or []
    for key, default in DEFAULT_WEIGHTS.items():
        if key in weights:
            try:
                data["weights"][key] = int(weights[key])
            except (TypeError, ValueError):
                data["weights"][key] = default
    crypto_src = raw.get("cuts_crypto")
    if isinstance(crypto_src, dict):
        data["cuts"] = _copy_cuts(cuts, DEFAULT_CUTS_STOCK)
        data["cuts_crypto"] = _copy_cuts(crypto_src, DEFAULT_CUTS_CRYPTO)
        migrate_sell_cuts(data["cuts_crypto"])
    else:
        legacy = _copy_cuts(cuts, DEFAULT_CUTS_CRYPTO)
        migrate_sell_cuts(legacy)
        data["cuts_crypto"] = legacy
        data["cuts"] = dict(DEFAULT_CUTS_STOCK)
    if data["weights"].get("trend") == 2:
        data["weights"]["trend"] = 1
    if data["weights"].get("ma20") == -1:
        data["weights"]["ma20"] = 1
    if "chg6_200" not in weights:
        try:
            old = int(weights.get("chg6_100"))
        except (TypeError, ValueError):
            old = None
        if old == -1:
            old = -2
        if old is not None:
            data["weights"]["chg6_200"] = old
    if "chg6_800" not in weights:
        try:
            old = int(weights.get("chg6_600"))
        except (TypeError, ValueError):
            old = None
        if old is not None:
            data["weights"]["chg6_800"] = old
    if "chg1_down30" not in weights:
        try:
            old = int(weights.get("chg1_down20"))
        except (TypeError, ValueError):
            old = None
        if old == 1:
            old = 2
        if old is not None:
            data["weights"]["chg1_down30"] = old
    try:
        rule_ver = int(raw.get("rule_ver") or 0)
    except (TypeError, ValueError):
        rule_ver = 0
    if rule_ver < 54:
        data["weights"] = dict(DEFAULT_WEIGHTS)
        data["cuts"] = dict(DEFAULT_CUTS_STOCK)
        data["cuts_crypto"] = dict(DEFAULT_CUTS_CRYPTO)
        rule_ver = 54
    data["rule_ver"] = rule_ver
    data["sim"] = normalize_sim(sim)
    try:
        data["sim_options"] = 1 if int(raw.get("sim_options") or 0) else 0
    except (TypeError, ValueError):
        data["sim_options"] = 0
    out_favs = []
    for item in favs:
        if not isinstance(item, dict):
            continue
        market = str(item.get("market") or "").strip().upper()
        ticker = str(item.get("ticker") or "").strip()
        name = str(item.get("name") or ticker).strip()
        if market not in ("KR", "US", "CRYPTO") or not ticker:
            continue
        if market == "KR":
            ticker = ticker.zfill(6) if ticker.isdigit() else ticker
        entry = {"market": market, "ticker": ticker, "name": name}
        if isinstance(item.get("sim"), dict):
            entry["sim"] = normalize_sim(item.get("sim"))
        out_favs.append(entry)
        if len(out_favs) >= MAX_FAVORITES:
            break
    data["favorites"] = out_favs
    plan_raw = raw.get("plan") if isinstance(raw.get("plan"), dict) else {}
    plan = {"months": 12, "monthly_krw": 0.0, "drift_pp": 5.0, "weights": {}}
    try:
        plan["months"] = max(1, int(plan_raw.get("months") or 12))
    except (TypeError, ValueError):
        pass
    try:
        plan["monthly_krw"] = max(0.0, float(plan_raw.get("monthly_krw") or 0))
    except (TypeError, ValueError):
        pass
    try:
        plan["drift_pp"] = max(0.0, float(plan_raw.get("drift_pp") or 5))
    except (TypeError, ValueError):
        pass
    wsrc = plan_raw.get("weights") if isinstance(plan_raw.get("weights"), dict) else {}
    wout = {}
    for k, v in wsrc.items():
        try:
            wout[str(k)] = float(v)
        except (TypeError, ValueError):
            continue
    plan["weights"] = wout
    data["plan"] = plan
    try:
        data["ts"] = int(raw.get("ts") or 0)
    except (TypeError, ValueError):
        data["ts"] = 0
    data["uid"] = normalize_uid(raw.get("uid") or raw.get("id"))
    return data


def snapshot_key(data: dict) -> str:
    """ts 를 빼고 비교한다. 저장 시각만 바뀌면 다시 쓰지 않는다."""
    payload = {
        "uid": normalize_uid(data.get("uid")),
        "weights": data.get("weights") or {},
        "cuts": data.get("cuts") or {},
        "cuts_crypto": data.get("cuts_crypto") or {},
        "sim": data.get("sim") or {},
        "sim_options": int(data.get("sim_options") or 0),
        "rule_ver": int(data.get("rule_ver") or 0),
        "favorites": data.get("favorites") or [],
        "plan": data.get("plan") or {},
    }
    return json.dumps(payload, sort_keys=True, ensure_ascii=False)


def _score(data: dict) -> tuple[int, int, int]:
    try:
        ts = int(data.get("ts") or 0)
    except (TypeError, ValueError):
        ts = 0
    customized = 0
    if (data.get("weights") or {}) != DEFAULT_WEIGHTS:
        customized += 1
    if (data.get("cuts") or {}) != DEFAULT_CUTS_STOCK:
        customized += 1
    if (data.get("cuts_crypto") or {}) != DEFAULT_CUTS_CRYPTO:
        customized += 1
    if (data.get("sim") or {}) != DEFAULT_SIM:
        customized += 1
    return (ts, len(data.get("favorites") or []), customized)


def merge_prefs(*sources: dict | None) -> dict:
    best = None
    best_score = (-1, -1, -1)
    for src in sources:
        if not isinstance(src, dict):
            continue
        data = _normalize(src)
        score = _score(data)
        if score > best_score:
            best = data
            best_score = score
    return best or _empty()


def _empty_store() -> dict:
    return {"v": STORE_VERSION, "users": {}}


def _parse_store(raw: object) -> dict:
    store = _empty_store()
    if not isinstance(raw, dict):
        return store
    users = raw.get("users")
    if isinstance(users, dict):
        for key, val in users.items():
            uid = normalize_uid(key)
            if not uid or not isinstance(val, dict):
                continue
            item = _normalize(val)
            item["uid"] = uid
            store["users"][uid] = item
        return store
    # 예전 단일 저장은 접속자 공유이므로 새 칸으로 옮기지 않는다.
    return store


def _prune_store(store: dict) -> dict:
    users = store.get("users") or {}
    if len(users) <= MAX_USERS:
        return store
    ranked = sorted(
        users.items(),
        key=lambda kv: int((_normalize(kv[1]).get("ts") or 0)),
        reverse=True,
    )
    store["users"] = dict(ranked[:MAX_USERS])
    return store


def _paths() -> list[Path]:
    return [
        Path.home() / ".trade-advisor" / "prefs.json",
        Path(__file__).resolve().parent.parent / ".cache" / "prefs.json",
    ]


def _read_store_file() -> dict:
    for path in _paths():
        try:
            if path.is_file():
                return _parse_store(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, json.JSONDecodeError, TypeError):
            continue
    return _empty_store()


def _write_store_file(store: dict) -> None:
    payload = json.dumps(_prune_store(store), ensure_ascii=False, indent=2)
    for path in _paths():
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(payload, encoding="utf-8")
        except OSError:
            continue


def load_user_prefs_file(uid: str) -> dict | None:
    uid = normalize_uid(uid)
    if not uid:
        return None
    item = _read_store_file().get("users", {}).get(uid)
    return _normalize(item) if isinstance(item, dict) else None


def save_user_prefs_file(uid: str, data: dict) -> None:
    uid = normalize_uid(uid)
    if not uid:
        return
    item = _normalize(data)
    item["uid"] = uid
    with _FILE_LOCK:
        store = _read_store_file()
        store.setdefault("users", {})[uid] = item
        _write_store_file(store)


def _compact_prefs(data: dict) -> dict:
    """쿠키·주소용. 종목별 수량만 빼서 용량을 줄인다. 즐겨찾기 목록은 남긴다."""
    n = _normalize(data)
    n["favorites"] = [
        {"market": f["market"], "ticker": f["ticker"], "name": f["name"]}
        for f in (n.get("favorites") or [])
    ]
    return n


def encode_cookie(data: dict, *, compact: bool = False) -> str:
    payload = _compact_prefs(data) if compact else _normalize(data)
    raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    packed = b"z" + gzip.compress(raw, 9)
    return base64.urlsafe_b64encode(packed).decode("ascii")


def encode_browser(data: dict) -> str:
    token = encode_cookie(data)
    if len(token) <= COOKIE_MAX_LEN:
        return token
    return encode_cookie(data, compact=True)


def decode_cookie(value: str | None) -> dict | None:
    if not value:
        return None
    try:
        pad = "=" * (-len(value) % 4)
        raw = base64.urlsafe_b64decode(str(value) + pad)
        if raw.startswith(b"z"):
            raw = gzip.decompress(raw[1:])
        return _normalize(json.loads(raw.decode("utf-8")))
    except (ValueError, json.JSONDecodeError, TypeError, OSError):
        return None


def cookie_set_html(data: dict, *, force: bool = False) -> str:
    """페이지 localStorage·쿠키에 저장 코드를 남긴다. 즐겨찾기가 있을 때만 설정 본문을 덮는다.

    force 가 아니면 이 브라우저에 이미 다른 코드가 있으면 덮지 않고 그 코드로 돌아간다.
    st.html(..., unsafe_allow_javascript=True) 로 페이지에서 실행해야 한다.
    """
    uid = normalize_uid(data.get("uid"))
    token = encode_browser(data).replace("\\", "\\\\").replace('"', '\\"')
    has_data = bool(data.get("favorites") or int(data.get("ts") or 0))
    script = (
        "(function(){try{"
        "function readUid(){try{var x=localStorage.getItem('ta_uid');"
        "return x?String(x).toUpperCase().replace(/[^A-Z0-9]/g,''):'';}catch(e){return '';}}"
        f'var u="{uid}";'
        f'var t="{token}";'
        f'var keep={str(has_data).lower()};'
        f'var force={str(bool(force)).lower()};'
        "var prev=readUid();"
        "if(prev && u && prev!==u && !force){return;}"
        "if(!u)return;"
        "var sec=(location.protocol==='https:')?';Secure':'';"
        f'var d="{UID_COOKIE_NAME}="+u+";path=/;max-age=31536000;SameSite=Lax"+sec;'
        "document.cookie=d;"
        "localStorage.setItem('ta_uid',u);"
        "if(keep){"
        "if(t && t.length<=3200){"
        f'var c="{COOKIE_NAME}="+t+";path=/;max-age=31536000;SameSite=Lax"+sec;'
        "document.cookie=c;"
        "}"
        "localStorage.setItem('ta_prefs',t);"
        "localStorage.setItem('ta_prefs_'+u,t);"
        "}"
        "}catch(e){}})();"
    )
    return f"<script>{script}</script>"


def localstorage_boot_html() -> str:
    """쿠키가 없는 모바일에서 localStorage 저장 코드만 주소로 한 번 붙인다."""
    return (
        "<script>(function(){try{"
        "var url=new URL(window.location.href);"
        "if(url.searchParams.get('_u'))return;"
        "if(sessionStorage.getItem('ta_boot')==='1')return;"
        "sessionStorage.setItem('ta_boot','1');"
        "var u=null;"
        "try{u=localStorage.getItem('ta_uid');}catch(e){}"
        "if(!u)return;"
        "u=String(u).toUpperCase().replace(/[^A-Z0-9]/g,'');"
        "if(u.length!==8)return;"
        "var sec=(location.protocol==='https:')?';Secure':'';"
        "document.cookie='ta_uid='+u+';path=/;max-age=31536000;SameSite=Lax'+sec;"
        "url.searchParams.set('_u',u);"
        "window.location.replace(url.toString());"
        "}catch(e){}})();</script>"
    )


def localstorage_restore_html(uid: str) -> str:
    """주소 리다이렉트는 화면을 멈추게 해서 비활성화."""
    return ""


def _get_secret(name: str) -> str:
    val = os.environ.get(name, "").strip()
    if val:
        return val
    try:
        import streamlit as st

        return str(st.secrets.get(name, "") or "").strip()
    except Exception:
        return ""


def github_token() -> str:
    return _get_secret("GITHUB_TOKEN") or _get_secret("PREFS_GITHUB_TOKEN")


def remote_enabled() -> bool:
    return bool(github_token())


def remote_last_error() -> str:
    return _LAST_REMOTE_ERROR


def _set_remote_error(msg: str) -> None:
    global _LAST_REMOTE_ERROR
    _LAST_REMOTE_ERROR = msg


def _headers(token: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "trade-advisor",
    }


def _cached_gist_id(value: str | None = None) -> str:
    global _GIST_ID_CACHE
    if value:
        _GIST_ID_CACHE = value
        return value
    if _GIST_ID_CACHE:
        return _GIST_ID_CACHE
    pinned = _get_secret("PREFS_GIST_ID")
    if pinned:
        _GIST_ID_CACHE = pinned
        return pinned
    return ""


def _find_gist_id(token: str) -> str:
    cached = _cached_gist_id()
    if cached:
        return cached
    try:
        res = requests.get(
            "https://api.github.com/gists",
            headers=_headers(token),
            params={"per_page": 100},
            timeout=8,
        )
        if res.status_code != 200:
            _set_remote_error(f"Gist 목록 실패 ({res.status_code}). 토큰에 gist 권한이 있는지 확인하세요.")
            return ""
        matches = []
        for gist in res.json() or []:
            files = gist.get("files") or {}
            desc = str(gist.get("description") or "")
            if GIST_FILENAME in files or desc == GIST_DESC:
                matches.append(gist)
        matches.sort(key=lambda g: str(g.get("updated_at") or ""), reverse=True)
        if not matches:
            return ""
        return _cached_gist_id(str(matches[0].get("id") or ""))
    except (requests.RequestException, ValueError, TypeError):
        return ""


def _gist_content(gist_id: str, token: str) -> dict | None:
    res = requests.get(
        f"https://api.github.com/gists/{gist_id}",
        headers=_headers(token),
        timeout=8,
    )
    if res.status_code != 200:
        return None
    files = res.json().get("files") or {}
    info = files.get(GIST_FILENAME) or (next(iter(files.values())) if files else None)
    if not info:
        return None
    content = info.get("content") or ""
    if (not content or info.get("truncated")) and info.get("raw_url"):
        raw = requests.get(str(info["raw_url"]), timeout=8)
        if raw.status_code == 200:
            content = raw.text
    if not content:
        return None
    return json.loads(content)


def load_store_remote() -> dict:
    token = github_token()
    if not token:
        return _empty_store()
    gist_id = _find_gist_id(token)
    if not gist_id:
        return _empty_store()
    try:
        raw = _gist_content(gist_id, token)
        return _parse_store(raw)
    except (requests.RequestException, ValueError, TypeError, json.JSONDecodeError):
        return _empty_store()


def load_user_prefs_remote(uid: str) -> dict | None:
    uid = normalize_uid(uid)
    if not uid:
        return None
    item = load_store_remote().get("users", {}).get(uid)
    return _normalize(item) if isinstance(item, dict) else None


def save_user_prefs_remote(uid: str, data: dict) -> bool:
    token = github_token()
    if not token:
        _set_remote_error("GITHUB_TOKEN 이 Secrets에 없습니다.")
        return False
    uid = normalize_uid(uid)
    if not uid:
        return False
    item = _normalize(data)
    item["uid"] = uid
    store = load_store_remote()
    store.setdefault("users", {})[uid] = item
    store = _prune_store(store)
    body = {
        GIST_FILENAME: {
            "content": json.dumps(store, ensure_ascii=False, indent=2),
        }
    }
    gist_id = _find_gist_id(token)
    try:
        if gist_id:
            res = requests.patch(
                f"https://api.github.com/gists/{gist_id}",
                headers=_headers(token),
                json={"description": GIST_DESC, "files": body},
                timeout=8,
            )
        else:
            res = requests.post(
                "https://api.github.com/gists",
                headers=_headers(token),
                json={"description": GIST_DESC, "public": False, "files": body},
                timeout=8,
            )
            if res.status_code in (200, 201):
                _cached_gist_id(str((res.json() or {}).get("id") or ""))
        ok = res.status_code in (200, 201)
        if ok:
            _set_remote_error("")
        else:
            _set_remote_error(f"Gist 저장 실패 ({res.status_code}). gist 권한 토큰인지 확인하세요.")
        return ok
    except (requests.RequestException, ValueError, TypeError) as extra:
        _set_remote_error(f"Gist 저장 오류: {extra}")
        return False
