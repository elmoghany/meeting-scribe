"""Bot Python layer: JWT signing + runner orchestration (no SDK / no network)."""
from pathlib import Path

import pytest

from app.config import get_settings
from bot import runner
from bot.jwt_sign import decode_unverified, sign_meeting_sdk_jwt


def test_jwt_deterministic():
    tok = sign_meeting_sdk_jwt("KEY", "SECRET", "1234567890", role=0,
                               ttl_sec=7200, now=1700000000)
    h, p = decode_unverified(tok)
    assert h == {"alg": "HS256", "typ": "JWT"}
    assert p["appKey"] == "KEY" and p["sdkKey"] == "KEY"
    assert p["mn"] == "1234567890" and p["role"] == 0
    assert p["iat"] == 1700000000 and p["exp"] == 1700007200
    assert p["exp"] == p["tokenExp"]


def test_jwt_signature_is_correct_hs256():
    import base64
    import hashlib
    import hmac

    tok = sign_meeting_sdk_jwt("K", "S", "1", role=0, ttl_sec=60, now=1)
    head_b64, pay_b64, sig_b64 = tok.split(".")
    expected = hmac.new(b"S", f"{head_b64}.{pay_b64}".encode(),
                        hashlib.sha256).digest()
    pad = "=" * (-len(sig_b64) % 4)
    assert base64.urlsafe_b64decode(sig_b64 + pad) == expected


def test_jwt_input_validation():
    with pytest.raises(ValueError):
        sign_meeting_sdk_jwt("", "S", "1")
    with pytest.raises(ValueError):
        sign_meeting_sdk_jwt("K", "", "1")
    with pytest.raises(ValueError):
        sign_meeting_sdk_jwt("K", "S", "1", role=2)


def test_parse_join_url():
    mn, pw = runner.parse_join_url("https://us05web.zoom.us/j/82223334444?pwd=AbCdef")
    assert mn == "82223334444" and pw == "AbCdef"
    assert runner.parse_join_url("garbage") == ("", "")
    assert runner.parse_join_url("") == ("", "")


def test_run_bot_invokes_binary_with_jwt(monkeypatch):
    monkeypatch.setenv("ZOOM_SDK_KEY", "K")
    monkeypatch.setenv("ZOOM_SDK_SECRET", "S")
    monkeypatch.setenv("MEETINGSCRIBE_BOT_BINARY", "echo")
    monkeypatch.delenv("MEETINGSCRIBE_BOT_DOCKER_IMAGE", raising=False)
    get_settings.cache_clear()

    captured = {}

    class _P:
        returncode = 0
        stderr = ""

    def fake_run(cmd, **kw):
        captured["cmd"] = cmd
        out_idx = cmd.index("--audio-out") + 1
        Path(cmd[out_idx]).write_bytes(b"RIFF0000WAVE")
        return _P()

    monkeypatch.setattr("subprocess.run", fake_run)
    info = runner.run_bot(meeting_number="999", passcode="pw",
                          meeting_id="m-bot-test", name="MS Bot")
    assert info["returncode"] == 0
    cmd = captured["cmd"]
    assert "--meeting" in cmd and "999" in cmd
    assert "--passcode" in cmd and "pw" in cmd
    assert "--name" in cmd and "MS Bot" in cmd
    assert "--jwt" in cmd
    jwt = cmd[cmd.index("--jwt") + 1]
    _h, payload = decode_unverified(jwt)
    assert payload["mn"] == "999" and payload["appKey"] == "K"
    assert info["audio"] and Path(info["audio"]).exists()


def test_run_bot_requires_sdk_credentials(monkeypatch):
    monkeypatch.delenv("ZOOM_SDK_KEY", raising=False)
    monkeypatch.delenv("ZOOM_SDK_SECRET", raising=False)
    get_settings.cache_clear()
    with pytest.raises(RuntimeError, match="ZOOM_SDK"):
        runner.run_bot(meeting_number="1", passcode="", meeting_id="m-bot-no-key")
