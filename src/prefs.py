"""배점·즐겨찾기 저장. 파일 + 브라우저 쿠키로 다음 방문에도 남긴다."""

from __future__ import annotations

import base64
import json
from pathlib import Path

from .signals import DEFAULT_CUTS, DEFAULT_WEIGHTS

COOKIE_NAME = "ta_prefs"
MAX_FAVORITES = 20
PREFS_VERSION = 1


def _empty() -> dict:
    return {
        "v": PREFS_VERSION,
        "weights": dict(DEFAULT_WEIGHTS),
        "cuts": dict(DEFAULT_CUTS),
        "favorites": [],
    }


def _normalize(raw: dict | None) -> dict:
    data = _empty()
    if not isinstance(raw, dict):
        return data
    weights = raw.get("weights") or {}
    cuts = raw.get("cuts") or {}
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
    return data


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
    return base64.urlsafe_b64encode(raw).decode("ascii")


def decode_cookie(value: str | None) -> dict | None:
    if not value:
        return None
    try:
        pad = "=" * (-len(value) % 4)
        raw = base64.urlsafe_b64decode(value + pad)
        return _normalize(json.loads(raw.decode("utf-8")))
    except (ValueError, json.JSONDecodeError, TypeError):
        return None


def cookie_set_html(data: dict) -> str:
    token = encode_cookie(data)
    return (
        "<html><body><script>"
        f'document.cookie="{COOKIE_NAME}={token};path=/;max-age=31536000;SameSite=Lax";'
        "</script></body></html>"
    )
