"""Timestamped highlight clips: extract_clip + export_highlight_reel."""
import numpy as np
import pytest

pytest.importorskip("soundfile")
import soundfile as sf  # noqa: E402

from app.pipeline.audiomix import export_highlight_reel, extract_clip


def _make_wav(path, seconds, sr=16000, val=0.5):
    sf.write(str(path), (np.ones(int(seconds * sr)) * val).astype(np.float32), sr)


def test_extract_clip_span(tmp_path):
    src = tmp_path / "meeting.wav"
    _make_wav(src, 10)  # 10s
    out = extract_clip(src, 3.0, 5.0, tmp_path / "clip.wav", pad=0.0)
    assert out and out.exists()
    audio, sr = sf.read(str(out))
    assert abs(len(audio) / sr - 2.0) < 0.05  # ~2s span


def test_extract_clip_pad_clamped(tmp_path):
    src = tmp_path / "meeting.wav"
    _make_wav(src, 4)
    # request near the start with pad — must clamp to 0, not go negative
    out = extract_clip(src, 0.5, 1.0, tmp_path / "c.wav", pad=2.0)
    audio, sr = sf.read(str(out))
    assert len(audio) / sr <= 3.1 and len(audio) > 0


def test_extract_clip_invalid_range(tmp_path):
    src = tmp_path / "meeting.wav"
    _make_wav(src, 5)
    assert extract_clip(src, 4.0, 4.0, tmp_path / "x.wav") is None   # empty
    assert extract_clip(tmp_path / "nope.wav", 0, 1, tmp_path / "y.wav") is None


def test_highlight_reel_concatenates(tmp_path):
    # ensure_meeting_wav reads mic/system; give it a system.wav
    _make_wav(tmp_path / "system.wav", 20)
    out = export_highlight_reel(tmp_path, [(2.0, 4.0), (10.0, 11.0)],
                                tmp_path / "reel.wav", gap=0.5)
    assert out and out.exists()
    audio, sr = sf.read(str(out))
    # 2s + 0.5s gap + 1s ≈ 3.5s
    assert abs(len(audio) / sr - 3.5) < 0.1


def test_highlight_reel_no_spans(tmp_path):
    _make_wav(tmp_path / "system.wav", 5)
    assert export_highlight_reel(tmp_path, [], tmp_path / "r.wav") is None
