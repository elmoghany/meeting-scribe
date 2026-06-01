"""Per-segment confidence: model field, ASR conversion, DB roundtrip."""
from types import SimpleNamespace

from app import db
from app.models import Segment
from app.pipeline.asr import _confidence, vocab_prompt


def test_transcribe_file_builds_segments(monkeypatch):
    # cover the ASR orchestration: map model output -> Segments with confidence,
    # drop blank text, attach speaker/source, return language. Model is mocked.
    from app.pipeline import asr
    from app.pipeline.asr import Transcriber

    fake_segs = [SimpleNamespace(start=0.0, end=1.0, text=" hello ", avg_logprob=-0.2),
                 SimpleNamespace(start=1.0, end=2.0, text="   ", avg_logprob=-0.1),  # blank
                 SimpleNamespace(start=2.0, end=3.0, text="world", avg_logprob=-3.0)]

    class FakeModel:
        def transcribe(self, path, **kw):
            return iter(fake_segs), SimpleNamespace(language="en")

    monkeypatch.setattr(asr, "_load", lambda *a, **k: FakeModel())
    segs, lang = Transcriber(model_name="fake").transcribe_file("x.wav", speaker="Me")
    assert lang == "en"
    assert [s.text for s in segs] == ["hello", "world"]      # blank dropped, trimmed
    assert all(s.speaker == "Me" and s.source == "batch" for s in segs)
    assert segs[0].confidence is not None
    assert segs[1].confidence < segs[0].confidence           # lower logprob -> lower confidence


def test_vocab_prompt():
    assert vocab_prompt([]) is None
    assert vocab_prompt(["  ", ""]) is None                 # all blank -> None
    assert vocab_prompt(["Andrej", "OAuth", "  Kubernetes "]) == \
        "Glossary of names and terms: Andrej, OAuth, Kubernetes."  # trimmed + joined
    capped = vocab_prompt([f"term{i}" for i in range(100)], limit=10)
    assert capped.count(",") == 9                           # capped to 10 terms (9 commas)


def test_segment_to_dict_includes_confidence():
    s = Segment(start=0, end=1, text="hi", speaker="Me", source="batch", confidence=0.83)
    assert s.to_dict()["confidence"] == 0.83
    # absent when unset
    s2 = Segment(start=0, end=1, text="hi", speaker="Me", source="batch")
    assert "confidence" not in s2.to_dict()


def test_confidence_from_logprob():
    # exp(0) = 1.0; exp(-0.7) ≈ 0.497; exp(-3) ≈ 0.05
    assert _confidence(SimpleNamespace(avg_logprob=0.0)) == 1.0
    assert abs(_confidence(SimpleNamespace(avg_logprob=-0.7)) - 0.497) < 0.01
    assert _confidence(SimpleNamespace(avg_logprob=-3.0)) <= 0.06
    # clamped to [0, 1]
    assert _confidence(SimpleNamespace(avg_logprob=2.0)) == 1.0
    # missing -> None
    assert _confidence(SimpleNamespace()) is None
    assert _confidence(SimpleNamespace(avg_logprob=float("-inf"))) == 0.0


def test_confidence_db_roundtrip():
    from app.models import Meeting
    import time as _t
    db.reset_connection()
    mid = "m-conf"
    if not db.get_meeting(mid):
        db.create_meeting(Meeting(id=mid, title="t", platform="other",
                                  started_at=_t.time()))
    db.replace_segments(mid, [
        Segment(start=0, end=1, text="hi", speaker="Me", source="batch", confidence=0.92),
        Segment(start=1, end=2, text="ok", speaker="Me", source="batch"),
    ], source="batch")
    segs = db.get_segments(mid, source="batch")
    confs = {s.text: s.confidence for s in segs}
    assert confs["hi"] == 0.92
    assert confs["ok"] is None
