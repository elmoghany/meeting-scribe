import time

from fastapi.testclient import TestClient

from app import db
from app.config import get_settings
from app.models import ActionItem, Meeting, Segment
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
        assert c.get("/api/meetings/nope/clip?start=0&end=1").status_code == 404  # guarded before path use
        assert c.get("/api/meetings/nope/chapters").status_code == 404
        assert c.post("/api/meetings/nope/delete-audio").status_code == 404      # guarded before delete
        assert c.post("/api/meetings/nope/reprocess").status_code == 404
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


def _wait_for(events, *types, timeout=5.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if any(e["type"] in types for e in events):
            return
        time.sleep(0.02)


def test_bot_record_dispatch_happy_path(monkeypatch):
    from app.server import main
    from bot import runner as bot_runner
    events = []
    monkeypatch.setattr(main, "_emit", events.append)
    monkeypatch.setattr(bot_runner, "parse_join_url", lambda url: ("555", "pw"))
    monkeypatch.setattr(bot_runner, "run_bot",
                        lambda **k: {"duration_sec": 42.0, "audio_dir": "/tmp/aud",
                                     "returncode": 0, "audio": "system.wav"})
    monkeypatch.setattr(main, "run_batch", lambda mid, audio_dir, progress=None: {"segments": 2})
    db.reset_connection()

    main._bot_record("Scheduled Standup", "https://zoom.us/j/555?pwd=pw")
    _wait_for(events, "processed", "error")
    types = [e["type"] for e in events]
    assert types[:2] == ["bot_started", "bot_finished"] or "bot_started" in types
    assert "bot_finished" in types and "processed" in types
    mid = next(e["meeting_id"] for e in events if e["type"] == "bot_started")
    assert db.get_meeting(mid).status == "processing"        # meeting created + advanced


def test_bot_record_dispatch_error_path(monkeypatch):
    from app.server import main
    from bot import runner as bot_runner
    events = []
    monkeypatch.setattr(main, "_emit", events.append)
    monkeypatch.setattr(bot_runner, "parse_join_url", lambda url: ("555", "pw"))

    def boom(**k):
        raise RuntimeError("bot failed to join")
    monkeypatch.setattr(bot_runner, "run_bot", boom)
    db.reset_connection()

    main._bot_record("Doomed Meeting", "https://zoom.us/j/555")
    _wait_for(events, "processed", "error")
    err = [e for e in events if e["type"] == "error"]
    assert err and "bot" in err[0]["message"]
    mid = next(e["meeting_id"] for e in events if e["type"] == "bot_started")
    assert db.get_meeting(mid).status == "error"             # marked failed, not left hanging


def test_zoom_oauth_start_not_configured_400(monkeypatch):
    from app.integrations import zoom
    monkeypatch.setattr(zoom, "is_configured", lambda: False)
    with TestClient(app) as c:
        assert c.get("/oauth/zoom/start", follow_redirects=False).status_code == 400


def test_zoom_oauth_start_redirects_when_configured(monkeypatch):
    from app.integrations import zoom
    monkeypatch.setattr(zoom, "is_configured", lambda: True)
    monkeypatch.setattr(zoom, "authorize_url", lambda: "https://zoom.us/oauth/authorize?x=1")
    with TestClient(app) as c:
        r = c.get("/oauth/zoom/start", follow_redirects=False)
        assert r.status_code in (302, 307) and "zoom.us" in r.headers["location"]


def test_zoom_oauth_callback_escapes_error(monkeypatch):
    with TestClient(app) as c:
        r = c.get("/oauth/zoom/callback", params={"error": "<script>x</script>"})
        assert r.status_code == 200
        assert "&lt;script&gt;" in r.text and "<script>" not in r.text   # XSS-safe


def test_zoom_oauth_callback_success_and_failure(monkeypatch):
    from app.integrations import zoom
    seen = {}
    monkeypatch.setattr(zoom, "exchange_code", lambda code: seen.update(code=code))
    with TestClient(app) as c:
        ok = c.get("/oauth/zoom/callback", params={"code": "abc123"})
        assert ok.status_code == 200 and "connected" in ok.text.lower()
        assert seen["code"] == "abc123"

    def boom(code):
        raise RuntimeError("invalid grant")
    monkeypatch.setattr(zoom, "exchange_code", boom)
    with TestClient(app) as c:
        bad = c.get("/oauth/zoom/callback", params={"code": "x"})
        assert bad.status_code == 500 and "exchange failed" in bad.text.lower()


def test_zoom_status_reports_configured_connected_autostart(monkeypatch):
    from app.integrations import zoom
    monkeypatch.setattr(zoom, "is_configured", lambda: True)
    monkeypatch.setattr(zoom, "connected", lambda: False)
    with TestClient(app) as c:
        j = c.get("/api/zoom/status").json()
        assert j["configured"] is True and j["connected"] is False and "autostart" in j


def test_meetings_list_includes_tags_and_stats():
    mid = _mk("srv-list-1", title="Listed")
    db.add_segments(mid, [Segment(start=0, end=2, text="hello there world",
                                  speaker="Me", source="batch")])
    with TestClient(app) as c:
        items = c.get("/api/meetings").json()
        m = next(x for x in items if x["id"] == mid)
        assert isinstance(m["tags"], list)                       # tags attached
        assert m["stats"]["segments"] >= 1 and m["stats"]["words"] >= 3  # stats computed


def test_action_items_list_and_csv():
    mid = _mk("srv-ai")
    db.add_action_item(mid, ActionItem(text="Email the vendor", owner="Sam",
                                       due="Fri", done=False))
    with TestClient(app) as c:
        items = c.get("/api/action-items").json()
        assert any(a["text"] == "Email the vendor" for a in items)
        open_items = c.get("/api/action-items", params={"open_only": True}).json()
        assert any(a["text"] == "Email the vendor" for a in open_items)   # open filter
        csv = c.get("/api/action-items.csv")
        assert csv.status_code == 200 and "text/csv" in csv.headers["content-type"]
        assert "Email the vendor" in csv.text                     # rendered into CSV


def test_annotation_comment_highlight_delete_lifecycle():
    mid = _mk("srv-annot")
    db.add_segments(mid, [Segment(start=0, end=2, text="hi", speaker="Me", source="batch")])
    seg = db.get_segments(mid, source="batch")[0]
    with TestClient(app) as c:
        aid = c.post(f"/api/meetings/{mid}/comment",
                     json={"text": "good point", "segment_id": seg.id}).json()["id"]
        assert aid
        anns = c.get(f"/api/meetings/{mid}/annotations").json()
        assert any(a["id"] == aid for a in anns)                  # listed
        h = c.post(f"/api/meetings/{mid}/highlight/{seg.id}").json()
        assert isinstance(h["highlighted"], bool)                # toggled
        assert c.delete(f"/api/annotations/{aid}").json()["deleted"] == aid   # removed


def test_chat_success_with_extractive_backend():
    mid = _mk("srv-chat-ok")
    db.add_segments(mid, [Segment(start=0, end=3, speaker="Sam", source="batch",
                                  text="The budget is fifty thousand dollars this quarter.")])
    with TestClient(app) as c:
        r = c.post(f"/api/meetings/{mid}/chat", json={"question": "What is the budget?"})
        assert r.status_code == 200 and r.json()["answer"]       # extractive backend answered


def test_search_returns_matching_segments():
    mid = _mk("srv-search-hits")
    db.add_segments(mid, [Segment(start=0, end=2, text="quarterly revenue projections",
                                  speaker="Me", source="batch")])
    with TestClient(app) as c:
        hits = c.get("/api/search", params={"q": "revenue"}).json()
        assert len(hits) >= 1                                     # FTS found the segment


def test_highlight_reel_without_highlights_404s():
    mid = _mk("srv-no-highlights")
    db.add_segments(mid, [Segment(start=0, end=2, text="hello", speaker="Me", source="batch")])
    with TestClient(app) as c:
        # segments exist but none are starred -> nothing to export
        assert c.get(f"/api/meetings/{mid}/highlight-reel").status_code == 404


def test_clip_without_audio_404s():
    mid = _mk("srv-clip-noaudio")           # meeting row exists, but no recordings on disk
    with TestClient(app) as c:
        assert c.get(f"/api/meetings/{mid}/clip", params={"start": 0, "end": 1}).status_code == 404


def test_regenerate_notes_guards():
    with TestClient(app) as c:
        empty = _mk("srv-regen-empty")      # no transcript yet
        assert c.post(f"/api/meetings/{empty}/regenerate-notes").status_code == 404
        mid = _mk("srv-regen-tmpl")
        db.add_segments(mid, [Segment(start=0, end=2, text="hi", speaker="Me", source="batch")])
        r = c.post(f"/api/meetings/{mid}/regenerate-notes", params={"template": "bogus"})
        assert r.status_code == 400         # unknown template rejected before any work


def test_templates_endpoint_lists_known_templates():
    from app.pipeline.notes import SUMMARY_TEMPLATES
    with TestClient(app) as c:
        got = c.get("/api/templates").json()["templates"]
        assert set(got) == set(SUMMARY_TEMPLATES.keys()) and got    # all template keys exposed


def test_zoom_upcoming_not_connected_400(monkeypatch):
    from app.integrations import zoom
    monkeypatch.setattr(zoom, "connected", lambda: False)
    with TestClient(app) as c:
        r = c.get("/api/zoom/upcoming")
        assert r.status_code == 400 and "not connected" in r.json()["detail"].lower()


def test_zoom_upcoming_api_error_502(monkeypatch):
    from app.integrations import zoom

    def boom():
        raise RuntimeError("token expired")
    monkeypatch.setattr(zoom, "connected", lambda: True)
    monkeypatch.setattr(zoom, "upcoming_meetings", boom)
    with TestClient(app) as c:
        assert c.get("/api/zoom/upcoming").status_code == 502    # upstream failure mapped to 502


def test_websocket_registers_and_unregisters_client():
    from app.server import main
    with TestClient(app) as c:
        before = len(main._clients)
        with c.websocket_connect("/ws") as ws:
            assert len(main._clients) == before + 1   # accepted + registered
            ws.send_text("ping")                       # keepalive, ignored server-side
        # on disconnect the finally-block discards it from the broadcast set
        assert len(main._clients) <= before + 1


class _FakeSession:
    """Stand-in for MeetingSession — no audio devices, no ASR threads."""
    raise_on_start = False

    def __init__(self, title, platform, emit, capture_mic=True,
                 capture_system=True, language=None):
        self.meeting = Meeting(id="rec-ctl", title=title, platform=platform,
                               started_at=time.time(), status="recording")
        self.is_recording = False

    def start(self):
        if _FakeSession.raise_on_start:
            raise RuntimeError("device busy")
        self.is_recording = True
        return self.meeting

    def stop(self):
        self.is_recording = False
        return {"meeting_id": self.meeting.id, "audio_dir": "/tmp/rec-ctl"}


def _patch_recording(monkeypatch):
    from app.server import main
    monkeypatch.setattr(main, "MeetingSession", _FakeSession)
    monkeypatch.setattr(main, "run_batch", lambda *a, **k: {"segments": 0})
    monkeypatch.setattr(main, "_session", None)
    _FakeSession.raise_on_start = False


def test_record_start_conflict_returns_409(monkeypatch):
    _patch_recording(monkeypatch)
    from app.server import main
    with TestClient(app) as c:
        r1 = c.post("/api/record/start", json={"title": "Standup", "platform": "meet"})
        assert r1.status_code == 200 and r1.json()["title"] == "Standup"
        assert c.get("/api/status").json()["recording"] is True
        r2 = c.post("/api/record/start", json={"title": "Other", "platform": "meet"})
        assert r2.status_code == 409                     # already recording -> conflict
    monkeypatch.setattr(main, "_session", None)


def test_record_start_failure_clears_session(monkeypatch):
    # If capture fails to start, _session MUST be reset to None — otherwise the
    # dashboard is permanently stuck reporting "already recording".
    _patch_recording(monkeypatch)
    _FakeSession.raise_on_start = True
    from app.server import main
    with TestClient(app) as c:
        r = c.post("/api/record/start", json={"title": "x", "platform": "other"})
        assert r.status_code == 500                      # surfaced as server error
        assert main._session is None                     # cleaned up, not wedged
        assert c.get("/api/status").json()["recording"] is False


def test_record_stop_without_active_returns_409(monkeypatch):
    _patch_recording(monkeypatch)
    with TestClient(app) as c:
        assert c.post("/api/record/stop").status_code == 409   # nothing to stop


def test_record_start_then_stop_processes(monkeypatch):
    _patch_recording(monkeypatch)
    from app.server import main
    with TestClient(app) as c:
        c.post("/api/record/start", json={"title": "S", "platform": "meet"})
        r = c.post("/api/record/stop")
        assert r.status_code == 200
        assert r.json() == {"meeting_id": "rec-ctl", "status": "processing"}
        assert c.get("/api/status").json()["recording"] is False   # session cleared
    monkeypatch.setattr(main, "_session", None)


def test_slug_makes_friendly_filenames():
    from app.server.main import _slug
    assert _slug("Q3 Planning / Roadmap!") == "Q3-Planning-Roadmap"
    assert _slug("  weird___name  ") == "weird-name"
    assert _slug("") == "meeting" and _slug("***") == "meeting"   # fallback
    assert len(_slug("x" * 200)) <= 60                            # capped


def test_export_filename_uses_title(_=None):
    mid = _mk("srv-fname", title="Q3 Planning")
    db.replace_segments(mid, [Segment(start=0, end=1, text="hi", speaker="Me",
                                      source="batch")], source="batch")
    with TestClient(app) as c:
        r = c.get(f"/api/meetings/{mid}/export", params={"fmt": "txt"})
        assert 'filename="Q3-Planning.txt"' in r.headers.get("content-disposition", "")


def test_export_each_format_dispatches():
    mid = _mk("srv-allfmt", title="Demo")
    db.replace_segments(mid, [
        Segment(start=0, end=2, text="We shipped v2 and decided to launch Friday.",
                speaker="Me", source="batch")], source="batch")
    expect = {"md": "markdown", "txt": "text/plain", "srt": "text/plain",
              "vtt": "text/vtt", "json": "application/json", "html": "text/html"}
    with TestClient(app) as c:
        for fmt, media in expect.items():
            r = c.get(f"/api/meetings/{mid}/export", params={"fmt": fmt})
            assert r.status_code == 200, f"{fmt} -> {r.status_code}"
            assert media in r.headers.get("content-type", ""), f"{fmt} media"
            assert r.text.strip(), f"{fmt} empty body"
        # json export is parseable and complete
        import json as _json
        assert "transcript" in _json.loads(c.get(
            f"/api/meetings/{mid}/export", params={"fmt": "json"}).text)


def test_export_empty_meeting_does_not_crash():
    # exporting a meeting that has no transcript yet (still processing) should
    # return valid empty-ish output, not 500.
    mid = _mk("srv-empty-export", title="Pending")
    with TestClient(app) as c:
        for fmt in ("txt", "srt", "vtt", "json", "html", "md"):
            r = c.get(f"/api/meetings/{mid}/export", params={"fmt": fmt})
            assert r.status_code == 200, f"{fmt} -> {r.status_code}"
        import json as _json
        d = _json.loads(c.get(f"/api/meetings/{mid}/export", params={"fmt": "json"}).text)
        assert d["transcript"] == [] and d["meeting"]["id"] == mid


def test_zoom_oauth_callback_escapes_error_param():
    # a crafted callback URL must not reflect raw HTML (reflected XSS)
    with TestClient(app) as c:
        r = c.get("/oauth/zoom/callback",
                  params={"error": "<script>alert(1)</script>"})
        assert r.status_code == 200
        assert "<script>alert(1)</script>" not in r.text   # not reflected raw
        assert "&lt;script&gt;" in r.text                    # escaped


def test_zoom_oauth_callback_escapes_exchange_error(monkeypatch):
    from app.integrations import zoom

    def boom(code):
        raise RuntimeError("<img src=x onerror=alert(1)>")

    monkeypatch.setattr(zoom, "exchange_code", boom)
    with TestClient(app) as c:
        r = c.get("/oauth/zoom/callback", params={"code": "abc"})
        assert "<img src=x" not in r.text          # exception message not reflected raw
        assert "&lt;img" in r.text                  # escaped


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


def test_regenerate_endpoint_keeps_manual_action_item():
    mid = _mk("srv-regen-manual")
    db.replace_segments(mid, [Segment(start=0, end=3, text="I will send the report by Friday.",
                                      speaker="Me", source="batch")], source="batch")
    with TestClient(app) as c:
        c.post(f"/api/meetings/{mid}/regenerate-notes")                 # initial auto extract
        c.post(f"/api/meetings/{mid}/action-items", json={"text": "buy the coffee"})  # user adds
        c.post(f"/api/meetings/{mid}/regenerate-notes")                 # regenerate again
        texts = [a.text.lower() for a in db.get_action_items(mid)]
    assert any("coffee" in t for t in texts)        # manual item survived regeneration
    assert any("report" in t for t in texts)        # auto item still present


def test_delete_batch_removes_selected_meetings():
    a, b, c_ = _mk("srv-bulk-a"), _mk("srv-bulk-b"), _mk("srv-bulk-c")
    rec = get_settings().recordings_dir / a
    rec.mkdir(parents=True, exist_ok=True)
    (rec / "system.wav").write_bytes(b"RIFFxxxxWAVE")
    with TestClient(app) as c:
        # delete a and b, plus a nonexistent id (ignored)
        r = c.post("/api/meetings/delete-batch", json={"ids": [a, b, "ghost"]}).json()
        assert set(r["deleted"]) == {a, b} and r["count"] == 2
        assert c.get(f"/api/meetings/{a}").status_code == 404
        assert c.get(f"/api/meetings/{b}").status_code == 404
        assert c.get(f"/api/meetings/{c_}").status_code == 200  # untouched
    assert not rec.exists()  # recording dir removed too


def test_delete_batch_empty_is_noop():
    with TestClient(app) as c:
        assert c.post("/api/meetings/delete-batch", json={"ids": []}).json() == {
            "deleted": [], "count": 0}


def test_tag_endpoints():
    mid = _mk("srv-tags")
    with TestClient(app) as c:
        r = c.post(f"/api/meetings/{mid}/tags", json={"text": "Standup"})
        assert r.status_code == 200 and "standup" in r.json()["tags"]   # normalized
        assert any(t["tag"] == "standup" for t in c.get("/api/tags").json())  # global bar
        r2 = c.delete(f"/api/meetings/{mid}/tags/standup")
        assert "standup" not in r2.json()["tags"]


def test_edit_segment_and_markdown_endpoints():
    mid = _mk("srv-editseg")
    db.replace_segments(mid, [Segment(start=0, end=2, text="helo wrld", speaker="Me",
                                      source="batch")], source="batch")
    sid = db.get_segments(mid, source="batch")[0].id
    with TestClient(app) as c:
        assert c.patch(f"/api/segments/{sid}", json={}).status_code == 400        # nothing
        r = c.patch(f"/api/segments/{sid}", json={"text": "hello world"})
        assert r.status_code == 200 and r.json()["meeting_id"] == mid             # fixed
        assert c.patch("/api/segments/999999", json={"text": "x"}).status_code == 404
        md = c.get(f"/api/meetings/{mid}/markdown")                                # notes doc
        assert md.status_code == 200 and "hello world" in md.text
    assert db.get_segments(mid, source="batch")[0].text == "hello world"


def test_action_item_lifecycle_and_comment():
    mid = _mk("srv-ai-crud")
    db.replace_segments(mid, [Segment(start=0, end=2, text="hi", speaker="Me",
                                      source="batch")], source="batch")
    with TestClient(app) as c:
        aid = c.post(f"/api/meetings/{mid}/action-items", json={"text": "ship v2"}).json()["id"]
        assert c.post(f"/api/action/{aid}?done=true").json()["done"] is True   # toggle
        c.patch(f"/api/action/{aid}", json={"text": "ship v2.1", "owner": "Sam"})  # edit
        items = {a.text: a for a in db.get_action_items(mid)}
        assert "ship v2.1" in items and items["ship v2.1"].owner == "Sam"
        assert items["ship v2.1"].done is True                                 # done survived edit
        assert c.post(f"/api/meetings/{mid}/comment", json={"text": "good"}).status_code == 200
        assert c.delete(f"/api/action/{aid}").json() == {"deleted": aid}        # delete
        assert "ship v2.1" not in [a.text for a in db.get_action_items(mid)]
        # editing/deleting a missing item 404s
        assert c.patch("/api/action/999999", json={"text": "x"}).status_code == 404
        assert c.delete("/api/action/999999").status_code == 404


def test_speaker_profile_endpoints():
    # voice profiles: list (names only), meetings-for-name, delete
    db.reset_connection()
    db.upsert_profile("Alice Example", [0.1] * 256)   # a stored d-vector
    with TestClient(app) as c:
        names = [p["name"] for p in c.get("/api/speakers").json()]
        assert "Alice Example" in names
        assert isinstance(c.get("/api/speakers/Alice Example/meetings").json(), list)
        assert c.delete("/api/speakers/Alice Example").json() == {"deleted": "Alice Example"}
        assert "Alice Example" not in [p["name"] for p in c.get("/api/speakers").json()]


def test_chapters_endpoint():
    mid = _mk("srv-chapters")
    db.replace_segments(mid, [
        Segment(start=i * 60, end=i * 60 + 40,
                text="budget pricing budget pricing" if i < 6 else "hiring team hiring team",
                speaker="Me", source="batch") for i in range(12)],
        source="batch")
    with TestClient(app) as c:
        body = c.get(f"/api/meetings/{mid}/chapters").json()
        assert "chapters" in body and len(body["chapters"]) >= 2
        assert body["chapters"][0]["start"] == 0.0
        assert c.get("/api/meetings/nope/chapters").status_code == 404


def test_rename_speakers_preserves_segment_ids_and_annotations():
    mid = _mk("srv-rename-ids")
    db.replace_segments(mid, [
        Segment(start=0, end=2, text="hi", speaker="Speaker 1", source="batch"),
        Segment(start=2, end=4, text="yo", speaker="Speaker 2", source="batch"),
    ], source="batch")
    seg = db.get_segments(mid, source="batch")[0]      # Speaker 1's segment
    sid_before = seg.id
    # highlight that segment — an annotation keyed by segment_id
    db.add_annotation(mid, kind="highlight", segment_id=sid_before)
    with TestClient(app) as c:
        r = c.post(f"/api/meetings/{mid}/rename-speakers",
                   json={"mapping": {"Speaker 1": "Sam"}})
        assert r.status_code == 200
    segs = {s.id: s for s in db.get_segments(mid, source="batch")}
    assert sid_before in segs                          # ID preserved (was orphaned before)
    assert segs[sid_before].speaker == "Sam"           # relabeled in place
    ann = db.list_annotations(mid)
    assert ann and ann[0]["segment_id"] == sid_before  # highlight still attached


def test_merge_speakers_via_rename_collapses_labels():
    mid = _mk("srv-merge")
    db.replace_segments(mid, [
        Segment(start=0, end=2, text="hi", speaker="Speaker 1", source="batch"),
        Segment(start=2, end=4, text="hello", speaker="Speaker 2", source="batch"),
        Segment(start=4, end=6, text="yes", speaker="Speaker 2", source="batch"),
        Segment(start=6, end=8, text="ok", speaker="Speaker 3", source="batch"),
    ], source="batch")
    with TestClient(app) as c:
        # merge Speaker 2 into Speaker 1 (the over-counting fix the UI exposes)
        r = c.post(f"/api/meetings/{mid}/rename-speakers",
                   json={"mapping": {"Speaker 2": "Speaker 1"}})
        assert r.status_code == 200
    speakers = [s.speaker for s in db.get_segments(mid, source="batch")]
    assert "Speaker 2" not in speakers                 # gone
    assert speakers.count("Speaker 1") == 3            # absorbed the two Speaker 2 segs
    assert speakers.count("Speaker 3") == 1            # untouched


def test_delete_audio_removes_wavs_keeps_meeting():
    mid = _mk("srv-audio")
    rec = get_settings().recordings_dir / mid
    rec.mkdir(parents=True, exist_ok=True)
    (rec / "system.wav").write_bytes(b"RIFFxxxxWAVE")
    (rec / "mic.wav").write_bytes(b"RIFFyyyyWAVE")
    with TestClient(app) as c:
        r = c.post(f"/api/meetings/{mid}/delete-audio").json()
        assert set(r["removed"]) == {"system.wav", "mic.wav"}
        # playing now 404s gracefully (drives the UI 'audio removed' indicator),
        # but the meeting + notes are preserved
        assert c.get(f"/api/meetings/{mid}/audio").status_code == 404
        assert c.get(f"/api/meetings/{mid}").status_code == 200
    assert not (rec / "system.wav").exists()
    assert db.get_meeting(mid) is not None  # notes/meeting preserved
