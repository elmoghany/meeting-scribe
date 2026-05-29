"""Cross-meeting Q&A ("ask all my meetings") — FTS query builder + endpoint."""
import time

from fastapi.testclient import TestClient

from app import db
from app.models import Meeting, Segment
from app.pipeline.notes import fts_query_from_question
from app.server.main import app


def test_fts_query_from_question():
    q = fts_query_from_question("What did we decide about the Q2 budget?")
    # stopwords (what/did/we/the/about) dropped; salient terms quoted + OR-joined
    assert '"decide"' in q and '"budget"' in q
    assert " OR " in q
    assert '"the"' not in q and '"we"' not in q


def test_fts_query_empty_for_stopwords_only():
    assert fts_query_from_question("what is it?") == ""


def test_ask_endpoint_finds_across_meetings():
    db.reset_connection()
    a = "ask-m1"
    b = "ask-m2"
    for mid, title in [(a, "Budget Sync"), (b, "Roadmap Review")]:
        if not db.get_meeting(mid):
            db.create_meeting(Meeting(id=mid, title=title, platform="zoom",
                                      started_at=time.time()))
    db.replace_segments(a, [Segment(start=12, end=15, text="We approved the OAuth "
                                    "migration for the budget.", speaker="Sam",
                                    source="batch")], source="batch")
    db.replace_segments(b, [Segment(start=30, end=33, text="The roadmap depends on "
                                    "the OAuth work shipping first.", speaker="Alex",
                                    source="batch")], source="batch")
    with TestClient(app) as c:
        r = c.post("/api/ask", json={"question": "What about the OAuth work?"}).json()
    # answer should reference OAuth, and sources should span BOTH meetings
    assert "oauth" in r["answer"].lower()
    titles = {s["title"] for s in r["sources"]}
    assert {"Budget Sync", "Roadmap Review"} <= titles


def test_ask_endpoint_no_match():
    db.reset_connection()
    with TestClient(app) as c:
        r = c.post("/api/ask", json={"question": "xylophonezzz quuxnzz?"}).json()
    assert r["sources"] == []


def test_ask_endpoint_empty_question():
    with TestClient(app) as c:
        assert c.post("/api/ask", json={"question": "   "}).status_code == 400
