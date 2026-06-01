"""Zoom OAuth pure helpers (no network): config check, basic-auth header,
the browser authorize URL, and the token-exchange/persistence flow (httpx mocked)."""
import base64
import json
import time

import httpx

from app import config
from app.integrations import zoom


def test_zoom_oauth_url_and_config(monkeypatch):
    monkeypatch.setenv("ZOOM_CLIENT_ID", "cid123")
    monkeypatch.setenv("ZOOM_CLIENT_SECRET", "secret456")
    config.get_settings.cache_clear()
    try:
        assert zoom.is_configured() is True
        assert zoom._basic_auth() == "Basic " + base64.b64encode(b"cid123:secret456").decode()
        url = zoom.authorize_url(state="xyz")
        assert "response_type=code" in url
        assert "client_id=cid123" in url
        assert "state=xyz" in url
    finally:
        config.get_settings.cache_clear()


def test_zoom_not_configured_without_secret(monkeypatch):
    monkeypatch.setenv("ZOOM_CLIENT_ID", "cid123")
    monkeypatch.setenv("ZOOM_CLIENT_SECRET", "")     # missing secret -> not configured
    config.get_settings.cache_clear()
    try:
        assert zoom.is_configured() is False
    finally:
        config.get_settings.cache_clear()


def test_zoom_token_exchange_persist_and_access(monkeypatch):
    monkeypatch.setenv("ZOOM_CLIENT_ID", "cid")
    monkeypatch.setenv("ZOOM_CLIENT_SECRET", "sec")
    monkeypatch.delenv("ZOOM_ACCOUNT_ID", raising=False)   # user-managed flow
    config.get_settings.cache_clear()

    class _R:
        def raise_for_status(self):
            pass

        def json(self):
            return {"access_token": "AT", "refresh_token": "RT", "expires_in": 3600}

    monkeypatch.setattr(httpx, "post", lambda *a, **k: _R())
    tok_path = zoom._token_path()
    try:
        if tok_path.exists():
            tok_path.unlink()
        assert zoom.connected() is False               # no token yet
        tok = zoom.exchange_code("authcode")
        assert tok["access_token"] == "AT"
        saved = json.loads(tok_path.read_text())
        assert saved["access_token"] == "AT" and saved["expires_at"] > time.time()
        assert zoom.connected() is True                 # token persisted
        assert zoom._access_token() == "AT"             # valid token reused (no refresh)
    finally:
        if tok_path.exists():
            tok_path.unlink()
        config.get_settings.cache_clear()
