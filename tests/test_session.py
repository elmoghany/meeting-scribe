"""Live rolling-notes emission (no audio devices needed — we drive _live_segs)."""
from app.models import Segment
from app.session import MeetingSession


def _seg(i, text):
    return Segment(start=i * 5, end=i * 5 + 4, text=text, speaker="Me", source="live")


def test_live_notes_emitted_after_n_segments():
    events = []
    sess = MeetingSession(title="t", emit=lambda e: events.append(e),
                          capture_mic=False, capture_system=True)
    texts = [
        "I will send the budget report by Friday.",
        "We decided to use OAuth for authentication.",
        "The roadmap looks solid.",
        "You should review the design before tomorrow.",
        "Let's finalize the plan.",
        "We need to ship the beta this week.",
    ]
    for i, t in enumerate(texts):
        sess._live_segs.append(_seg(i, t))
    sess._maybe_live_notes(every=6)
    notes = [e for e in events if e["type"] == "live_notes"]
    assert len(notes) == 1
    n = notes[0]
    assert n["overview"]
    assert any("report" in a["text"].lower() for a in n["action_items"])


def test_session_stores_language():
    s = MeetingSession(title="t", language="es", capture_mic=False, capture_system=True)
    assert s.language == "es"
    # empty/None -> auto-detect (None)
    assert MeetingSession(title="t", language="", capture_mic=False).language is None
    assert MeetingSession(title="t", capture_mic=False).language is None


def test_live_notes_not_emitted_too_early():
    events = []
    sess = MeetingSession(title="t", emit=lambda e: events.append(e),
                          capture_mic=False, capture_system=True)
    sess._live_segs.append(_seg(0, "hello"))
    sess._maybe_live_notes(every=6)
    assert not [e for e in events if e["type"] == "live_notes"]
