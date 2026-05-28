"""Zoom Meeting SDK JWT signing.

The Meeting SDK requires a short-lived JWT signed with your SDK App's Client ID
+ Client Secret. Spec (HS256, since SDK 1.9.6, March 2023):

    header  = {"alg":"HS256","typ":"JWT"}
    payload = {
        "appKey":   <sdk_key>,
        "sdkKey":   <sdk_key>,         # back-compat alias
        "mn":       <meeting_number>,  # int or str
        "role":     0 | 1,             # 0 = attendee, 1 = host
        "iat":      now,
        "exp":      now + ttl,         # <= 48h, typical 2h
        "tokenExp": now + ttl,
    }

Pure stdlib (no PyJWT dependency). Unit-tested below.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def sign_meeting_sdk_jwt(sdk_key: str, sdk_secret: str, meeting_number: str | int,
                         role: int = 0, ttl_sec: int = 7200,
                         now: float | None = None) -> str:
    """Mint a Meeting SDK JWT. `now` is overridable for deterministic tests."""
    if role not in (0, 1):
        raise ValueError("role must be 0 (attendee) or 1 (host)")
    if not sdk_key or not sdk_secret:
        raise ValueError("sdk_key and sdk_secret are required")
    issued = int(now if now is not None else time.time())
    exp = issued + int(ttl_sec)
    header = {"alg": "HS256", "typ": "JWT"}
    payload = {
        "appKey": sdk_key,
        "sdkKey": sdk_key,           # back-compat with older SDKs
        "mn": str(meeting_number),
        "role": role,
        "iat": issued,
        "exp": exp,
        "tokenExp": exp,
    }
    h_b64 = _b64url(json.dumps(header, separators=(",", ":"), sort_keys=True).encode())
    p_b64 = _b64url(json.dumps(payload, separators=(",", ":"), sort_keys=True).encode())
    signing_input = f"{h_b64}.{p_b64}".encode()
    sig = hmac.new(sdk_secret.encode(), signing_input, hashlib.sha256).digest()
    return f"{h_b64}.{p_b64}.{_b64url(sig)}"


def decode_unverified(token: str) -> tuple[dict, dict]:
    """Return (header, payload) without signature verification (debug only)."""
    h_b64, p_b64, _ = token.split(".")

    def _pad(s: str) -> str:
        return s + "=" * (-len(s) % 4)

    header = json.loads(base64.urlsafe_b64decode(_pad(h_b64)))
    payload = json.loads(base64.urlsafe_b64decode(_pad(p_b64)))
    return header, payload
