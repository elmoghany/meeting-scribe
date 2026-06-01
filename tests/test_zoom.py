"""Zoom OAuth pure helpers (no network): config check, basic-auth header,
and the browser authorize URL."""
import base64

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
