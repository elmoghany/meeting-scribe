"""Zoom integration — read the user's meeting schedule so MeetingScribe can
auto-start recording when a meeting begins.

Supports two app types (set via .env):

  * **Server-to-Server OAuth** (recommended for one person): set ZOOM_ACCOUNT_ID
    + ZOOM_CLIENT_ID + ZOOM_CLIENT_SECRET. No browser redirect, no callback URL.
    Tokens are fetched directly with the account_credentials grant.

  * **User-managed (General) OAuth**: set ZOOM_CLIENT_ID + ZOOM_CLIENT_SECRET +
    ZOOM_REDIRECT_URI. The user authorizes in a browser; we exchange the code at
    /oauth/zoom/callback and store + refresh tokens.

NOTE: this only *reads the schedule*. Having a bot actually join a call and
capture its audio independently requires the Zoom Meeting SDK (out of scope).
MeetingScribe instead auto-starts local system-audio capture at meeting time.
"""
from __future__ import annotations

import base64
import json
import time
from pathlib import Path
from urllib.parse import urlencode

import httpx

from ..config import get_settings

_AUTH_URL = "https://zoom.us/oauth/authorize"
_TOKEN_URL = "https://zoom.us/oauth/token"
_API = "https://api.zoom.us/v2"


def _token_path() -> Path:
    return get_settings().data_dir / "zoom_token.json"


def is_configured() -> bool:
    s = get_settings()
    return bool(s.zoom_client_id and s.zoom_client_secret)


def _basic_auth() -> str:
    s = get_settings()
    raw = f"{s.zoom_client_id}:{s.zoom_client_secret}".encode()
    return "Basic " + base64.b64encode(raw).decode()


def authorize_url(state: str = "meetingscribe") -> str:
    """Build the browser authorization URL (user-managed OAuth flow)."""
    s = get_settings()
    params = {
        "response_type": "code",
        "client_id": s.zoom_client_id or "",
        "redirect_uri": s.zoom_redirect_uri,
        "state": state,
    }
    return f"{_AUTH_URL}?{urlencode(params)}"


def _save(tok: dict) -> None:
    tok = dict(tok)
    tok.setdefault("expires_at", time.time() + tok.get("expires_in", 3600) - 60)
    _token_path().write_text(json.dumps(tok), encoding="utf-8")


def exchange_code(code: str) -> dict:
    """Exchange an authorization code for tokens (user-managed flow)."""
    s = get_settings()
    r = httpx.post(_TOKEN_URL, headers={"Authorization": _basic_auth()},
                   params={"grant_type": "authorization_code", "code": code,
                           "redirect_uri": s.zoom_redirect_uri}, timeout=20)
    r.raise_for_status()
    tok = r.json()
    _save(tok)
    return tok


def _server_to_server_token() -> dict:
    s = get_settings()
    r = httpx.post(_TOKEN_URL, headers={"Authorization": _basic_auth()},
                   params={"grant_type": "account_credentials",
                           "account_id": s.zoom_account_id}, timeout=20)
    r.raise_for_status()
    tok = r.json()
    _save(tok)
    return tok


def _refresh(refresh_token: str) -> dict:
    r = httpx.post(_TOKEN_URL, headers={"Authorization": _basic_auth()},
                   params={"grant_type": "refresh_token",
                           "refresh_token": refresh_token}, timeout=20)
    r.raise_for_status()
    tok = r.json()
    _save(tok)
    return tok


def _access_token() -> str:
    s = get_settings()
    # Server-to-Server: reuse cached token until it expires, else mint a fresh one.
    if s.zoom_account_id:
        path = _token_path()
        if path.exists():
            tok = json.loads(path.read_text())
            if tok.get("expires_at", 0) > time.time():
                return tok["access_token"]
        return _server_to_server_token()["access_token"]
    # User-managed: use stored token, refresh if expired.
    path = _token_path()
    if not path.exists():
        raise RuntimeError("Zoom not authorized yet. Visit /oauth/zoom/start.")
    tok = json.loads(path.read_text())
    if tok.get("expires_at", 0) <= time.time():
        if not tok.get("refresh_token"):
            raise RuntimeError("Zoom token expired and no refresh token; re-authorize.")
        tok = _refresh(tok["refresh_token"])
    return tok["access_token"]


def connected() -> bool:
    s = get_settings()
    if s.zoom_account_id and is_configured():
        return True
    return _token_path().exists()


def _get(path: str, params: dict | None = None) -> dict:
    r = httpx.get(f"{_API}{path}",
                  headers={"Authorization": f"Bearer {_access_token()}"},
                  params=params or {}, timeout=20)
    r.raise_for_status()
    return r.json()


def me() -> dict:
    return _get("/users/me")


def upcoming_meetings(page_size: int = 30) -> list[dict]:
    """Return normalized upcoming meetings: {id, topic, start_time, join_url}."""
    data = _get("/users/me/meetings", {"type": "upcoming", "page_size": page_size})
    out = []
    for m in data.get("meetings", []):
        out.append({
            "id": str(m.get("id")),
            "topic": m.get("topic", "Zoom meeting"),
            "start_time": m.get("start_time"),  # ISO 8601 UTC, e.g. 2026-05-26T15:00:00Z
            "duration": m.get("duration"),      # minutes
            "join_url": m.get("join_url"),
        })
    return out
