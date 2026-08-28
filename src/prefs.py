"""배점·즐겨찾기·시뮬레이션 저장.

로컬 파일은 같은 프로세스가 살아 있는 동안만 유효하다.
Streamlit Cloud Reboot 은 컨테이너를 새로 만들기 때문에 파일은 사라진다.
그래서 아래를 같이 쓴다.

1. 브라우저 쿠키 (부모 페이지, 첫 실행에는 iframe 을 그리지 않음)
2. 주소창 쿼리 (_ta) — 같은 탭에서 리부트할 때
3. GitHub Gist — Secrets 의 GITHUB_TOKEN 이 있으면 기기·탭이 달라도 유지
"""

from __future__ import annotations

import base64
import gzip
import json
import os
from pathlib import Path

import requests

from .backtest import DEFAULT_SIM
from .signals import DEFAULT_CUTS, DEFAULT_WEIGHTS

COOKIE_NAME = "ta_prefs"
QUERY_KEY = "_ta"
MAX_FAVORITES = 20
PREFS_VERSION = 1
GIST_FILENAME = "trade-advisor-prefs.json"
GIST_DESC = "trade-advisor-prefs"
_GIST_ID_CACHE: str | None = None


def _empty() -> dict:
    return {
        "v": PREFS_VERSION,
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
    for key, default in DEFAULT_SIM.items():
        if key in sim:
            try:
                data["sim"][key] = int(sim[key])
            except (TypeError, ValueError):
                data["sim"][key] = default
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
        out_favs.append({"market": market, "ticker": ticker, "name": name})
        if len(out_favs) >= MAX_FAVORITES:
            break
    data["favorites"] = out_favs
    try:
        data["ts"] = int(raw.get("ts") or 0)
    except (TypeError, ValueError):
        data["ts"] = 0
    return data


def snapshot_key(data: dict) -> str:
    """ts 를 빼고 비교한다. 저장 시각만 바뀌면 다시 쓰지 않는다."""
    payload = {
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


def _paths() -> list[Path]:
    return [
        Path.home() / ".trade-advisor" / "prefs.json",
        Path(__file__).resolve().parent.parent / ".cache" / "prefs.json",
    ]


def load_prefs_file() -> dict:
    for path in _paths():
        try:
            if path.is_file():
                return _normalize(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, json.JSONDecodeError, TypeError):
            continue
    return _empty()


def save_prefs_file(data: dict) -> None:
    payload = json.dumps(_normalize(data), ensure_ascii=False, indent=2)
    for path in _paths():
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(payload, encoding="utf-8")
        except OSError:
            continue


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
    """부모 페이지 쿠키에 남긴다. 위치 이동은 하지 않는다(리부트 행 방지)."""
    token = encode_cookie(data).replace("\\", "\\\\").replace('"', '\\"')
    return (
        "<html><body><script>"
        "(function(){try{"
        f'var t="{token}";'
        f'var c="{COOKIE_NAME}="+t+";path=/;max-age=31536000;SameSite=Lax";'
        "document.cookie=c;"
        "try{window.parent.document.cookie=c;}catch(e){}"
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


def load_prefs_remote() -> dict | None:
    token = github_token()
    if not token:
        return None
    gist_id = _find_gist_id(token)
    if not gist_id:
        return None
    try:
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
        return _normalize(json.loads(content))
    except (requests.RequestException, ValueError, TypeError, json.JSONDecodeError):
        return None


def save_prefs_remote(data: dict) -> bool:
    token = github_token()
    if not token:
        return False
    body = {
        GIST_FILENAME: {
            "content": json.dumps(_normalize(data), ensure_ascii=False, indent=2),
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
