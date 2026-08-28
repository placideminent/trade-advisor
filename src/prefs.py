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

from .backtest import DEFAULT_SIM, normalize_sim
from .signals import DEFAULT_CUTS, DEFAULT_WEIGHTS, LEGACY_DEFAULT_CUTS

COOKIE_NAME = "ta_prefs"
UID_COOKIE_NAME = "ta_uid"
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
        "cuts": dict(DEFAULT_CUTS),
        "sim": dict(DEFAULT_SIM),
        "favorites": [],
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
    for key, default in DEFAULT_CUTS.items():
        if key in cuts:
            try:
                data["cuts"][key] = int(cuts[key])
            except (TypeError, ValueError):
                data["cuts"][key] = default
    if all(data["cuts"].get(key) == old for key, old in LEGACY_DEFAULT_CUTS.items()):
        data["cuts"] = dict(DEFAULT_CUTS)
    data["sim"] = normalize_sim(sim)
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
        "sim": data.get("sim") or {},
        "favorites": data.get("favorites") or [],
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
    if (data.get("cuts") or {}) != DEFAULT_CUTS:
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


def encode_cookie(data: dict) -> str:
    raw = json.dumps(_normalize(data), ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    packed = b"z" + gzip.compress(raw, 9)
    return base64.urlsafe_b64encode(packed).decode("ascii")


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


def cookie_set_html(data: dict) -> str:
    """부모 페이지 쿠키에 uid 와 설정을 남긴다. 위치 이동은 하지 않는다."""
    token = encode_cookie(data).replace("\\", "\\\\").replace('"', '\\"')
    uid = normalize_uid(data.get("uid"))
    return (
        "<html><body><script>"
        "(function(){try{"
        f'var t="{token}";'
        f'var u="{uid}";'
        f'var c="{COOKIE_NAME}="+t+";path=/;max-age=31536000;SameSite=Lax";'
        f'var d="{UID_COOKIE_NAME}="+u+";path=/;max-age=31536000;SameSite=Lax";'
        "document.cookie=c;document.cookie=d;"
        "try{window.parent.document.cookie=c;window.parent.document.cookie=d;}catch(e){}"
        "}catch(e){}})();"
        "</script></body></html>"
    )


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
        return res.status_code in (200, 201)
    except (requests.RequestException, ValueError, TypeError):
        return False
