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

import numpy as np

from ..config import get_settings
from ..device import detect
from ..models import Segment


@lru_cache(maxsize=2)
def _load(model_name: str, device: str, compute_type: str):
    from faster_whisper import WhisperModel  # noqa: PLC0415

    s = get_settings()
    return WhisperModel(
        model_name, device=device, compute_type=compute_type,
        download_root=str(s.models_dir / "whisper"),
    )


class Transcriber:
    def __init__(self, model_name: str | None = None, device: str | None = None,
                 compute_type: str | None = None):
        s = get_settings()
        auto_dev, auto_compute = detect()
        self.model_name = model_name or s.live_model
        self.device = device or auto_dev
        self.compute_type = compute_type or auto_compute

    @property
    def model(self):
        return _load(self.model_name, self.device, self.compute_type)

    def transcribe_window(self, samples: "np.ndarray", t_offset: float = 0.0,
                          language: str | None = None, speaker: str = "Unknown",
                          source: str = "live") -> list[Segment]:
        audio = np.ascontiguousarray(samples, dtype=np.float32)
        segments, _info = self.model.transcribe(
            audio, language=language, beam_size=1, vad_filter=True,
            condition_on_previous_text=False,
        )
        out: list[Segment] = []
        for seg in segments:
            text = seg.text.strip()
            if text:
                out.append(Segment(start=t_offset + seg.start, end=t_offset + seg.end,
                                   text=text, speaker=speaker, source=source))
        return out

    def transcribe_file(self, path: str, language: str | None = None,
                        speaker: str = "Unknown", source: str = "batch",
                        ) -> tuple[list[Segment], str]:
        segments, info = self.model.transcribe(
            path, language=language, beam_size=5, vad_filter=True,
            word_timestamps=False,
        )
        out: list[Segment] = []
        for seg in segments:
            text = seg.text.strip()
            if text:
                out.append(Segment(start=seg.start, end=seg.end, text=text,
                                   speaker=speaker, source=source))
        return out, info.language
