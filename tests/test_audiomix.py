import numpy as np
import pytest

pytest.importorskip("soundfile")
import soundfile as sf  # noqa: E402

from app.pipeline.audiomix import (  # noqa: E402
    ensure_meeting_wav,
    export_highlight_reel,
    extract_clip,
)


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


def test_stereo_sources_are_downmixed_to_mono(tmp_path):
    # real loopback can be 2-channel; every read path must collapse to mono
    sr = 16000
    stereo = np.stack([np.ones(sr) * 0.5, np.ones(sr) * 0.5], axis=1).astype(np.float32)
    sf.write(tmp_path / "system.wav", stereo, sr)             # (sr, 2) stereo
    out = ensure_meeting_wav(tmp_path)                        # downmix in the mixer
    audio, _ = sf.read(str(out), dtype="float32")
    assert audio.ndim == 1                                    # mono out

    # pass a stereo file straight to extract_clip to hit its own downmix branch
    stereo_src = tmp_path / "stereo.wav"
    sf.write(stereo_src, stereo, sr)
    clip = tmp_path / "clip.wav"
    assert extract_clip(stereo_src, 0.1, 0.6, clip) == clip
    ca, _ = sf.read(str(clip), dtype="float32")
    assert ca.ndim == 1                                       # collapsed to mono


def test_extract_clip_missing_source_returns_none(tmp_path):
    assert extract_clip(tmp_path / "nope.wav", 0.0, 1.0, tmp_path / "c.wav") is None


def test_extract_clip_empty_range_returns_none(tmp_path):
    sr = 16000
    sf.write(tmp_path / "system.wav", (np.ones(sr) * 0.5).astype(np.float32), sr)
    src = ensure_meeting_wav(tmp_path)
    # end <= start (after padding clamps) -> no clip
    assert extract_clip(src, 0.5, 0.5, tmp_path / "c.wav", pad=0.0) is None


def test_highlight_reel_joins_spans_and_handles_edges(tmp_path):
    sr = 16000
    sf.write(tmp_path / "system.wav", (np.ones(sr * 2) * 0.5).astype(np.float32), sr)  # 2s
    reel = tmp_path / "reel.wav"
    # valid multi-span join, with one degenerate (end<=start) span that's skipped
    out = export_highlight_reel(tmp_path, [(0.0, 0.4), (1.5, 1.5), (1.0, 1.4)], reel)
    assert out == reel and reel.exists()

    assert export_highlight_reel(tmp_path, [], tmp_path / "r2.wav") is None   # no spans
    # every span degenerate -> nothing to write -> None
    assert export_highlight_reel(tmp_path, [(1.0, 1.0)], tmp_path / "r3.wav") is None


def test_highlight_reel_no_audio_returns_none(tmp_path):
    assert export_highlight_reel(tmp_path, [(0.0, 1.0)], tmp_path / "r.wav") is None


def test_cache_invalidates_when_source_is_newer(tmp_path):
    import os
    sr = 16000
    src = tmp_path / "system.wav"
    sf.write(src, (np.ones(sr) * 0.3).astype(np.float32), sr)
    out = ensure_meeting_wav(tmp_path)
    assert out and out.exists()
    cache_mtime = out.stat().st_mtime

    # Re-record with a louder signal AFTER the cache was written.
    sf.write(src, (np.ones(sr) * 0.9).astype(np.float32), sr)
    os.utime(src, (cache_mtime + 5, cache_mtime + 5))

    out2 = ensure_meeting_wav(tmp_path)
    audio, _ = sf.read(str(out2), dtype="float32")
    peak = float(np.max(np.abs(audio)))
    assert peak > 0.6, f"cache returned stale quiet audio (peak={peak})"
    assert out2.stat().st_mtime > cache_mtime    # rewrote the cache
