"""compute_pipeline orchestration — the core ASR → diarize → merge → notes flow.

ASR (Transcriber) and the diarizer (label_speakers) are mocked so no audio or
models are needed; the real extractive notes backend runs. Verifies the
assembly: mic segments are "Me", system segments get diarized labels, the two
streams merge, and a PipelineResult is produced — plus the graceful
"diarization failed → Others" fallback.
"""
from app.models import Segment
from app.pipeline import diarize, process


class _FakeTranscriber:
    def __init__(self, model_name=None):
        self.model_name = model_name or "fake-model"

    def transcribe_file(self, path, speaker=None, source="batch"):
        if "system" in path:                      # remote participants, pre-diarization
            return ([Segment(start=0.0, end=2.0, text="We should ship the report by Friday.",
                             speaker="Unknown", source=source),
                     Segment(start=2.0, end=4.0, text="Agreed, I'll handle it.",
                             speaker="Unknown", source=source)], "en")
        return ([Segment(start=4.0, end=6.0, text="Sounds good to me.",
                         speaker=speaker or "Me", source=source)], "en")


def _mk_audio(tmp_path):
    (tmp_path / "system.wav").write_bytes(b"RIFFxxxxWAVE")
    (tmp_path / "mic.wav").write_bytes(b"RIFFyyyyWAVE")
    return tmp_path


def test_compute_pipeline_assembles_mic_and_system(tmp_path, monkeypatch):
    _mk_audio(tmp_path)
    monkeypatch.setattr(process, "Transcriber", _FakeTranscriber)

    def fake_label(wav_path, segs):               # simulate diarization
        for i, s in enumerate(segs):
            s.speaker = f"Speaker {i + 1}"
        return segs
    monkeypatch.setattr(diarize, "label_speakers", fake_label)

    res = process.compute_pipeline(str(tmp_path))
    speakers = {s.speaker for s in res.segments}
    assert "Me" in speakers                        # mic stream anchored as Me
    assert {"Speaker 1", "Speaker 2"} <= speakers  # system stream diarized
    assert res.language == "en"
    assert res.backend                             # a notes backend ran (extractive default)
    assert res.duration_sec == 6.0                 # max segment end
    # extractive notes picked up the actionable line
    assert any("report" in a.text.lower() or "handle" in a.text.lower()
               for a in res.action_items) or res.summary is not None


def test_compute_pipeline_diarization_failure_falls_back_to_others(tmp_path, monkeypatch):
    _mk_audio(tmp_path)
    monkeypatch.setattr(process, "Transcriber", _FakeTranscriber)

    def boom(wav_path, segs):
        raise RuntimeError("no diarizer deps")
    monkeypatch.setattr(diarize, "label_speakers", boom)

    res = process.compute_pipeline(str(tmp_path))
    speakers = {s.speaker for s in res.segments}
    assert "Others" in speakers                    # Unknown → Others on diarizer failure
    assert "Me" in speakers                         # mic still labeled
    assert "Unknown" not in speakers                # all remote relabeled


def test_compute_pipeline_no_audio_raises(tmp_path, monkeypatch):
    monkeypatch.setattr(process, "Transcriber", _FakeTranscriber)  # no wavs written
    import pytest
    with pytest.raises(RuntimeError, match="No audio"):
        process.compute_pipeline(str(tmp_path))
