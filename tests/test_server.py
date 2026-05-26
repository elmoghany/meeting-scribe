import time

from fastapi.testclient import TestClient

from app import db
from app.config import get_settings
from app.models import Meeting
from app.server.main import app


def _mk(mid, title="Old"):
    db.reset_connection()
    if not db.get_meeting(mid):
        db.create_meeting(Meeting(id=mid, title=title, platform="zoom",
                                  started_at=time.time()))
    return mid


def test_set_title():
    mid = _mk("srv-title")
    with TestClient(app) as c:
        r = c.post(f"/api/meetings/{mid}/title", json={"title": "Q3 Planning"})
        assert r.status_code == 200 and r.json()["title"] == "Q3 Planning"
    assert db.get_meeting(mid).title == "Q3 Planning"


def test_set_title_blank_falls_back():
    mid = _mk("srv-title2")
    with TestClient(app) as c:
        assert c.post(f"/api/meetings/{mid}/title", json={"title": "   "}).json()["title"] \
            == "Untitled meeting"


def test_delete_audio_removes_wavs_keeps_meeting():
    mid = _mk("srv-audio")
    rec = get_settings().recordings_dir / mid
    rec.mkdir(parents=True, exist_ok=True)
    (rec / "system.wav").write_bytes(b"RIFFxxxxWAVE")
    (rec / "mic.wav").write_bytes(b"RIFFyyyyWAVE")
    with TestClient(app) as c:
        r = c.post(f"/api/meetings/{mid}/delete-audio").json()
    assert set(r["removed"]) == {"system.wav", "mic.wav"}
    assert not (rec / "system.wav").exists()
    assert db.get_meeting(mid) is not None  # notes/meeting preserved
