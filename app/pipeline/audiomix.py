"""Produce a single playable meeting track by mixing the mic ("Me") and system
("Others") streams. Cached as meeting.wav next to the source recordings so the
dashboard's audio player can stream + seek it (served with HTTP range support).
"""
from __future__ import annotations

from pathlib import Path

import numpy as np


def ensure_meeting_wav(rec_dir: str | Path, sample_rate: int = 16000) -> Path | None:
    """Mix mic.wav + system.wav into meeting.wav (cached). Returns the path, or
    None if no source audio exists.

    Re-mixes if the cache is older than any source (so a re-recording invalidates
    the stale mix automatically — was a real bug; the original short-circuit on
    'cache exists' silently served stale audio after Reprocess).
    """
    import soundfile as sf

    rec = Path(rec_dir)
    out = rec / "meeting.wav"
    sources = [rec / "mic.wav", rec / "system.wav"]
    existing = [p for p in sources if p.exists()]
    if out.exists() and existing:
        out_mtime = out.stat().st_mtime
        if all(p.stat().st_mtime <= out_mtime for p in existing):
            return out  # cache fresh — reuse

    tracks: list[np.ndarray] = []
    for p in existing:
        a, _sr = sf.read(str(p), dtype="float32", always_2d=False)
        if a.ndim > 1:
            a = a.mean(axis=1)
        tracks.append(a)
    if not tracks:
        return None

    n = max(len(t) for t in tracks)
    mix = np.zeros(n, dtype=np.float32)
    for t in tracks:
        padded = np.zeros(n, dtype=np.float32)
        padded[: len(t)] = t
        mix += padded
    peak = float(np.max(np.abs(mix))) or 1.0
    if peak > 1.0:
        mix /= peak  # prevent clipping when both streams are loud
    sf.write(str(out), mix, sample_rate, subtype="PCM_16")
    return out


def extract_clip(meeting_wav: str | Path, start: float, end: float,
                 out_path: str | Path, pad: float = 0.0) -> Path | None:
    """Write the [start, end] span of a meeting WAV to out_path. `pad` seconds
    of context can be added on each side. Returns the path, or None if the
    source is missing or the range is empty. Pure soundfile slice — testable."""
    import soundfile as sf

    src = Path(meeting_wav)
    if not src.exists():
        return None
    info = sf.info(str(src))
    sr = info.samplerate
    s = max(0.0, start - pad)
    e = min(info.frames / sr, end + pad)
    if e <= s:
        return None
    audio, _sr = sf.read(str(src), dtype="float32",
                         start=int(s * sr), stop=int(e * sr), always_2d=False)
    if audio.ndim > 1:
        audio = audio.mean(axis=1)
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    sf.write(str(out), audio, sr, subtype="PCM_16")
    return out


def export_highlight_reel(rec_dir: str | Path, spans: list[tuple[float, float]],
                          out_path: str | Path, gap: float = 0.4) -> Path | None:
    """Concatenate the given [start,end] spans of meeting.wav into one reel WAV
    (a short `gap` of silence between spans). Returns the path, or None."""
    import soundfile as sf

    src = ensure_meeting_wav(rec_dir)
    if not src or not spans:
        return None
    info = sf.info(str(src))
    sr = info.samplerate
    silence = np.zeros(int(gap * sr), dtype=np.float32)
    parts: list[np.ndarray] = []
    for start, end in sorted(spans):
        s, e = max(0.0, start), min(info.frames / sr, end)
        if e <= s:
            continue
        a, _ = sf.read(str(src), dtype="float32", start=int(s * sr),
                       stop=int(e * sr), always_2d=False)
        if a.ndim > 1:
            a = a.mean(axis=1)
        if parts:
            parts.append(silence)
        parts.append(a)
    if not parts:
        return None
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    sf.write(str(out), np.concatenate(parts), sr, subtype="PCM_16")
    return out
