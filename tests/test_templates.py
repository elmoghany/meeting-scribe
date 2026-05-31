"""Per-meeting-type summary templates."""
import time

from fastapi.testclient import TestClient

from app import db
from app.models import Meeting, Segment
from app.pipeline.notes import SUMMARY_TEMPLATES, system_prompt_for
from app.server.main import app


def test_system_prompt_appends_template():
    base = system_prompt_for(None)
    standup = system_prompt_for("standup")
    assert standup.startswith(base.split(" This is")[0][:20])  # same JSON schema base
    assert "standup" in standup.lower() and "blockers" in standup.lower()
    # general / unknown => no extra guidance
    assert system_prompt_for("general") == system_prompt_for(None)
    assert system_prompt_for("nonexistent") == system_prompt_for(None)


def test_detect_meeting_type():
    from app.pipeline.notes import detect_meeting_type

    def segs(text):
        return [Segment(start=0, end=1, text=text, speaker="Me", source="batch")]

    assert detect_meeting_type(segs(
        "Quick stand-up. Any blockers? What are you working on today?")) == "standup"
    assert detect_meeting_type(segs(
        "Tell me about yourself and walk me through your previous role, candidate."
    )) == "interview"
    assert detect_meeting_type(segs(
        "Retro time: what went well, what didn't go well, start stop continue."
    )) == "retro"
    assert detect_meeting_type(segs(
        "Let's talk pricing and your budget; reach the decision maker about the contract."
    )) == "sales"
    assert detect_meeting_type(segs("We discussed the weather and lunch plans.")) is None


def test_all_templates_have_distinct_guidance():
    nonempty = {k: v for k, v in SUMMARY_TEMPLATES.items() if v}
    assert len(set(nonempty.values())) == len(nonempty)  # all distinct
    assert {"standup", "one_on_one", "interview", "retro"} <= set(SUMMARY_TEMPLATES)


def test_extractive_summarize_accepts_template():
    from app.pipeline.notes import ExtractiveNotes
    segs = [Segment(start=0, end=2, text="We shipped the release today.",
                    speaker="Me", source="batch")]
    # template is a no-op for extractive but must not error
    summary, items = ExtractiveNotes().summarize(segs, template="standup")
    assert summary is not None


def test_regenerate_with_template_stores_it():
    db.reset_connection()
    mid = "tmpl-m1"
    if not db.get_meeting(mid):
        db.create_meeting(Meeting(id=mid, title="Standup", platform="zoom",
                                  started_at=time.time()))
    db.replace_segments(mid, [Segment(start=0, end=3, text="I'll ship the fix by Friday.",
                                      speaker="Sam", source="batch")], source="batch")
    with TestClient(app) as c:
        r = c.post(f"/api/meetings/{mid}/regenerate-notes", params={"template": "standup"})
        assert r.status_code == 200 and r.json()["template"] == "standup"
        # persisted on the meeting
        assert c.get(f"/api/meetings/{mid}").json()["meeting"]["template"] == "standup"
        # bad template rejected
        assert c.post(f"/api/meetings/{mid}/regenerate-notes",
                      params={"template": "bogus"}).status_code == 400
