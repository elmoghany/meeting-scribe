import time

from fastapi.testclient import TestClient

from app import db
from app.config import get_settings
from app.models import Meeting, Segment
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


def test_unknown_meeting_404s():
    with TestClient(app) as c:
        assert c.get("/api/meetings/nope").status_code == 404
        assert c.get("/api/meetings/nope/export?fmt=md").status_code == 404
        assert c.get("/api/meetings/nope/analytics").status_code == 404
        assert c.get("/api/meetings/nope/audio").status_code == 404
        assert c.post("/api/meetings/nope/title", json={"title": "x"}).status_code == 404


def test_chat_without_transcript_404s():
    mid = _mk("srv-no-transcript")
    with TestClient(app) as c:
        assert c.post(f"/api/meetings/{mid}/chat", json={"question": "hi"}).status_code == 404


def test_empty_comment_400s():
    mid = _mk("srv-empty-comment")
    with TestClient(app) as c:
        assert c.post(f"/api/meetings/{mid}/comment", json={"text": "  "}).status_code == 400


def test_empty_search_returns_empty_list():
    with TestClient(app) as c:
        assert c.get("/api/search", params={"q": "   "}).json() == []


def test_export_unknown_format_400s():
    mid = _mk("srv-bad-fmt")
    db.replace_segments(mid, [Segment(start=0, end=1, text="hi", speaker="Me",
                                      source="batch")], source="batch")
    with TestClient(app) as c:
        assert c.get(f"/api/meetings/{mid}/export", params={"fmt": "xyz"}).status_code == 400


def test_edit_summary_persists():
    mid = _mk("srv-editsum")
    with TestClient(app) as c:
        r = c.put(f"/api/meetings/{mid}/summary", json={
            "overview": "Edited overview.",
            "key_points": ["First point", "  ", "Second point"],  # blanks dropped
            "decisions": ["Ship Friday"]})
        assert r.status_code == 200
        body = r.json()
        assert body["overview"] == "Edited overview."
        assert body["key_points"] == ["First point", "Second point"]
    s = db.get_summary(mid)
    assert s.overview == "Edited overview." and s.decisions == ["Ship Friday"]


def test_edit_summary_unknown_meeting_404():
    with TestClient(app) as c:
        assert c.put("/api/meetings/nope/summary", json={"overview": "x"}).status_code == 404


def test_regenerate_notes_resummarizes():
    mid = _mk("srv-regen")
    db.replace_segments(mid, [
        Segment(start=0, end=3, text="I will send the report by Friday.",
                speaker="Me", source="batch"),
        Segment(start=3, end=6, text="We decided to launch on Thursday.",
                speaker="Sam", source="batch"),
    ], source="batch")
    with TestClient(app) as c:
        r = c.post(f"/api/meetings/{mid}/regenerate-notes").json()
    assert r["action_items"] >= 1
    assert db.get_summary(mid) is not None
    assert any("report" in a.text.lower() for a in db.get_action_items(mid))


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
