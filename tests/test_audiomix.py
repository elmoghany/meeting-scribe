import numpy as np
import pytest

pytest.importorskip("soundfile")
import soundfile as sf  # noqa: E402

from app.pipeline.audiomix import ensure_meeting_wav  # noqa: E402


def test_mix_pads_to_longest_and_no_clip(tmp_path):
    sr = 16000
    sf.write(tmp_path / "mic.wav", (np.ones(sr) * 0.8).astype(np.float32), sr)        # 1.0s
    sf.write(tmp_path / "system.wav", (np.ones(sr * 2) * 0.8).astype(np.float32), sr)  # 2.0s
    out = ensure_meeting_wav(tmp_path)
    assert out is not None and out.name == "meeting.wav"
    audio, osr = sf.read(str(out), dtype="float32")
    assert osr == sr
    assert abs(len(audio) - sr * 2) <= 1          # length = longest track
    assert float(np.max(np.abs(audio))) <= 1.0     # normalized, no clipping


def test_single_stream_ok(tmp_path):
    sr = 16000
    sf.write(tmp_path / "system.wav", (np.ones(sr) * 0.5).astype(np.float32), sr)
    out = ensure_meeting_wav(tmp_path)
    assert out is not None and out.exists()


def test_no_audio_returns_none(tmp_path):
    assert ensure_meeting_wav(tmp_path) is None
