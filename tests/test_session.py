"""Live rolling-notes emission (no audio devices needed — we drive _live_segs)."""
import queue
import time

from app.models import Segment
from app.session import MeetingSession


def _seg(i, text):
    return Segment(start=i * 5, end=i * 5 + 4, text=text, speaker="Me", source="live")


class _FakeRecorder:
    """Stands in for DualRecorder — captures the on_window callback, no audio."""

    def __init__(self, rec_dir, sample_rate, window_sec, on_window,
                 capture_mic, capture_system):
        self.on_window = on_window
        self.is_recording = False
        self.stopped = False

    def start(self):
        self.is_recording = True

    def stop(self):
        self.is_recording = False
        self.stopped = True
        return {"duration_sec": 12.5}


class _FakeTranscriber:
    """One segment per window; the sentinel sample 'boom' raises (ASR failure)."""

    def __init__(self, *a, **k):
        pass

    def transcribe_window(self, samples, t_offset, speaker, source, language=None):
        if samples == "boom":
            raise RuntimeError("model exploded")
        return [Segment(start=t_offset, end=t_offset + 1.0,
                        text=f"{speaker} window {samples}", speaker=speaker, source=source)]


def _wire(monkeypatch, tmp_path):
    """Point the app at a temp data dir and swap in the fake recorder/ASR."""
    from app import config, db, session
    from app.pipeline import asr
    monkeypatch.setenv("MEETINGSCRIBE_DATA_DIR", str(tmp_path))
    config.get_settings.cache_clear()
    db.reset_connection()
    monkeypatch.setattr(session, "DualRecorder", _FakeRecorder)
    monkeypatch.setattr(asr, "Transcriber", _FakeTranscriber)


def _wait_until(pred, timeout=10.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if pred():
            return True
        time.sleep(0.02)
    return False


def test_session_lifecycle_persists_and_emits(tmp_path, monkeypatch):
    """start -> feed mic+system windows -> stop: segments are transcribed off the
    capture thread, persisted, labeled Me/Others, emitted, and the meeting closes
    in 'processing' with a duration."""
    _wire(monkeypatch, tmp_path)
    from app import db
    events = []
    sess = MeetingSession(title="t", emit=events.append)
    meeting = sess.start()
    assert any(e["type"] == "started" for e in events)
    assert sess.is_recording is True                       # property reflects recorder

    # the recorder would call on_window; drive it directly
    sess._recorder.on_window("mic", "w1", 0.0)
    sess._recorder.on_window("system", "w2", 1.0)
    assert _wait_until(lambda: sum(e["type"] == "segment" for e in events) >= 2)

    info = sess.stop()
    assert sess._recorder.stopped is True
    seg_events = [e for e in events if e["type"] == "segment"]
    speakers = {e["speaker"] for e in seg_events}
    assert speakers == {"Me", "Others"}                    # mic->Me, system->Others
    assert any(e["type"] == "stopped" for e in events)
    assert info["meeting_id"] == meeting.id and info["duration_sec"] == 12.5

    row = db.get_meeting(meeting.id)
    assert row.status == "processing" and row.duration_sec == 12.5
    persisted = db.get_segments(meeting.id, source="live")
    assert {s.speaker for s in persisted} == {"Me", "Others"}   # actually written to DB


def test_session_consumer_survives_asr_error(tmp_path, monkeypatch):
    """A window whose ASR raises emits an error event but does NOT kill the
    consumer thread — the next good window is still transcribed."""
    _wire(monkeypatch, tmp_path)
    events = []
    sess = MeetingSession(title="t", emit=events.append)
    sess.start()
    sess._recorder.on_window("mic", "boom", 0.0)           # raises inside ASR
    sess._recorder.on_window("mic", "ok", 1.0)             # must still be processed
    assert _wait_until(lambda: any(e["type"] == "segment" for e in events))
    sess.stop()
    assert any(e["type"] == "error" and "live ASR" in e["message"] for e in events)
    assert any(e["type"] == "segment" for e in events)     # thread survived the error


def test_on_window_drops_when_queue_full(tmp_path, monkeypatch):
    """Backpressure: if ASR falls behind and the window queue is full, new windows
    are dropped silently rather than blocking the capture thread."""
    _wire(monkeypatch, tmp_path)
    sess = MeetingSession(title="t")
    # saturate the queue without a consumer running
    while True:
        try:
            sess._win_q.put_nowait(("mic", "x", 0.0))
        except queue.Full:
            break
    full = sess._win_q.qsize()
    sess._on_window("mic", "overflow", 9.0)                # must not raise
    assert sess._win_q.qsize() == full                     # dropped, not enqueued


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
