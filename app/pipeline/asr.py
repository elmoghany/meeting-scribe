"""Speech-to-text via faster-whisper (CTranslate2, MIT).

Two entry points:
  * ``Transcriber.transcribe_window`` — a single live audio window (numpy float32,
    16 kHz mono) → segments, for the on-screen draft.
  * ``Transcriber.transcribe_file``   — a full WAV → segments + language, for the
    high-quality batch pass.

faster-whisper is imported lazily so importing this module never requires the
model runtime to be installed.
"""
from __future__ import annotations

from functools import lru_cache

import math

import numpy as np

from ..config import get_settings
from ..device import detect
from ..models import Segment


def _confidence(seg) -> float | None:
    """Convert faster-whisper avg_logprob (a log probability) to 0..1 confidence."""
    lp = getattr(seg, "avg_logprob", None)
    if lp is None:
        return None
    try:
        return round(max(0.0, min(1.0, math.exp(float(lp)))), 3)
    except (ValueError, OverflowError):
        return None


@lru_cache(maxsize=2)
def _load(model_name: str, device: str, compute_type: str):
    from faster_whisper import WhisperModel  # noqa: PLC0415

    s = get_settings()
    root = str(s.models_dir / "whisper")
    try:
        return WhisperModel(model_name, device=device, compute_type=compute_type,
                            download_root=root)
    except Exception as e:
        # GPU init can fail on a too-old card even when CTranslate2 counts it
        # (e.g. a local GT 710). Fall back to CPU rather than crash.
        if device != "cpu":
            import sys
            print(f"[asr] {device}/{compute_type} load failed ({type(e).__name__}); "
                  f"falling back to CPU int8.", file=sys.stderr)
            return WhisperModel(model_name, device="cpu", compute_type="int8",
                                download_root=root)
        raise


def vocab_prompt(terms: list[str], limit: int = 60) -> str | None:
    """Build a Whisper ``initial_prompt`` from custom vocabulary so the model
    biases toward these names/jargon spellings. None if no terms. Capped so the
    prompt never crowds out the model's context window."""
    terms = [t.strip() for t in terms if t and t.strip()][:limit]
    if not terms:
        return None
    return "Glossary of names and terms: " + ", ".join(terms) + "."


class Transcriber:
    def __init__(self, model_name: str | None = None, device: str | None = None,
                 compute_type: str | None = None):
        s = get_settings()
        auto_dev, auto_compute = detect()
        self.model_name = model_name or s.live_model
        self.device = device or auto_dev
        self.compute_type = compute_type or auto_compute
        self._initial_prompt = vocab_prompt(s.vocab_terms())

    @property
    def model(self):
        return _load(self.model_name, self.device, self.compute_type)

    def transcribe_window(self, samples: "np.ndarray", t_offset: float = 0.0,
                          language: str | None = None, speaker: str = "Unknown",
                          source: str = "live") -> list[Segment]:
        audio = np.ascontiguousarray(samples, dtype=np.float32)
        segments, _info = self.model.transcribe(
            audio, language=language, beam_size=1, vad_filter=True,
            condition_on_previous_text=False, initial_prompt=self._initial_prompt,
        )
        out: list[Segment] = []
        for seg in segments:
            text = seg.text.strip()
            if text:
                out.append(Segment(start=t_offset + seg.start, end=t_offset + seg.end,
                                   text=text, speaker=speaker, source=source,
                                   confidence=_confidence(seg)))
        return out

    def transcribe_file(self, path: str, language: str | None = None,
                        speaker: str = "Unknown", source: str = "batch",
                        ) -> tuple[list[Segment], str]:
        segments, info = self.model.transcribe(
            path, language=language, beam_size=5, vad_filter=True,
            word_timestamps=False, initial_prompt=self._initial_prompt,
        )
        out: list[Segment] = []
        for seg in segments:
            text = seg.text.strip()
            if text:
                out.append(Segment(start=seg.start, end=seg.end, text=text,
                                   speaker=speaker, source=source,
                                   confidence=_confidence(seg)))
        return out, info.language
