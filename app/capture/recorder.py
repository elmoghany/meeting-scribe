"""Dual-stream audio capture for meetings.

Records two synchronized 16 kHz mono WAV files:
  * ``mic.wav``    — your microphone           → speaker "Me"
  * ``system.wav`` — system-audio loopback     → the other participants

Capturing the system's audio output (WASAPI loopback on Windows) is what lets
MeetingScribe work with Google Meet AND Zoom without any API key or bot: it
simply listens to whatever your speakers are playing during the call.

While recording, fixed-length windows are emitted via ``on_window`` so the live
transcriber can draft text in near-real-time.

``soundcard`` is imported lazily so the rest of the package (DB, server, remote
submitter) imports on machines without audio libraries.
"""
from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Callable, Literal

import numpy as np

Stream = Literal["mic", "system"]
# on_window(stream, samples_float32_mono, window_start_sec)
WindowCB = Callable[[Stream, "np.ndarray", float], None]


def _soundcard():
    try:
        import soundcard as sc  # noqa: PLC0415
        return sc
    except Exception as e:  # pragma: no cover - environment dependent
        raise RuntimeError(
            "soundcard is required for capture. Install with `pip install soundcard`. "
            f"(import error: {e})"
        ) from e


def list_devices() -> dict:
    """Enumerate input devices and the loopback we'll use for 'Others'.

    Each lookup is guarded so a machine missing a default mic (or speaker) still
    reports what it *does* have instead of crashing.
    """
    sc = _soundcard()
    out: dict = {}
    try:
        out["default_microphone"] = str(sc.default_microphone().name)
    except Exception as e:
        out["default_microphone"] = None
        out["microphone_error"] = f"{type(e).__name__}: {e}"
    try:
        spk = sc.default_speaker()
        out["default_speaker"] = str(spk.name)
        out["loopback_source"] = str(spk.name) + " (loopback)"
    except Exception as e:
        out["default_speaker"] = None
        out["speaker_error"] = f"{type(e).__name__}: {e}"
    try:
        out["all_microphones"] = [str(m.name)
                                  for m in sc.all_microphones(include_loopback=True)]
    except Exception as e:
        out["all_microphones_error"] = f"{type(e).__name__}: {e}"
    return out


class _StreamWorker(threading.Thread):
    """Records one source to a WAV file and emits live windows."""

    def __init__(self, stream: Stream, mic_obj, out_path: Path, sample_rate: int,
                 window_sec: float, on_window: WindowCB | None, stop_evt: threading.Event,
                 t0_holder: list[float]):
        super().__init__(daemon=True, name=f"capture-{stream}")
        self.stream = stream
        self._mic = mic_obj
        self._out_path = out_path
        self._sr = sample_rate
        self._window_n = int(window_sec * sample_rate)
        self._on_window = on_window
        # NB: must NOT be named `_stop` — that shadows threading.Thread._stop().
        self._stop_evt = stop_evt
        self._t0_holder = t0_holder  # shared start time across both workers
        self.error: Exception | None = None
        self.frames_written = 0

    def run(self) -> None:  # pragma: no cover - needs real audio device
        import soundfile as sf

        block = max(1, self._sr // 4)  # ~250 ms reads
        buf = np.empty(0, dtype=np.float32)
        win_start_frame = 0
        try:
            with sf.SoundFile(str(self._out_path), mode="w", samplerate=self._sr,
                              channels=1, subtype="PCM_16") as wav, \
                 self._mic.recorder(samplerate=self._sr, channels=1) as rec:
                # Record the shared t0 the moment the first stream actually starts.
                if self._t0_holder[0] == 0.0:
                    self._t0_holder[0] = time.time()
                while not self._stop_evt.is_set():
                    data = rec.record(numframes=block)  # (frames, 1) float32
                    mono = data[:, 0].astype(np.float32)
                    wav.write(mono)
                    self.frames_written += len(mono)
                    if self._on_window is not None:
                        buf = np.concatenate([buf, mono])
                        while len(buf) >= self._window_n:
                            window = buf[: self._window_n]
                            buf = buf[self._window_n:]
                            t_start = win_start_frame / self._sr
                            win_start_frame += self._window_n
                            try:
                                self._on_window(self.stream, window, t_start)
                            except Exception:
                                pass  # never let a transcription hiccup kill capture
                # flush trailing audio as a final (short) window
                if self._on_window is not None and len(buf) > self._sr * 0.5:
                    self._on_window(self.stream, buf, win_start_frame / self._sr)
        except Exception as e:
            self.error = e


class DualRecorder:
    """Capture mic + system loopback to two WAV files under ``out_dir``."""

    def __init__(self, out_dir: str | Path, sample_rate: int = 16000,
                 window_sec: float = 5.0, on_window: WindowCB | None = None,
                 capture_mic: bool = True, capture_system: bool = True):
        self.out_dir = Path(out_dir)
        self.out_dir.mkdir(parents=True, exist_ok=True)
        self.sample_rate = sample_rate
        self.window_sec = window_sec
        self.on_window = on_window
        self.capture_mic = capture_mic
        self.capture_system = capture_system
        self._stop = threading.Event()
        self._workers: list[_StreamWorker] = []
        self._t0_holder = [0.0]
        self.mic_path = self.out_dir / "mic.wav"
        self.system_path = self.out_dir / "system.wav"

    def start(self) -> None:
        sc = _soundcard()
        self._stop.clear()
        if self.capture_mic:
            mic = sc.default_microphone()
            self._workers.append(_StreamWorker(
                "mic", mic, self.mic_path, self.sample_rate, self.window_sec,
                self.on_window, self._stop, self._t0_holder))
        if self.capture_system:
            spk = sc.default_speaker()
            loopback = sc.get_microphone(id=str(spk.name), include_loopback=True)
            self._workers.append(_StreamWorker(
                "system", loopback, self.system_path, self.sample_rate, self.window_sec,
                self.on_window, self._stop, self._t0_holder))
        if not self._workers:
            raise RuntimeError("Nothing to capture: enable mic and/or system audio.")
        for w in self._workers:
            w.start()

    def stop(self) -> dict:
        """Stop capture; return paths + duration. Raises if a worker errored."""
        self._stop.set()
        for w in self._workers:
            w.join(timeout=10)
        errs = [w.error for w in self._workers if w.error]
        if errs:
            raise errs[0]
        duration = 0.0
        if self._workers:
            duration = max(w.frames_written for w in self._workers) / self.sample_rate
        return {
            "mic_path": str(self.mic_path) if self.capture_mic else None,
            "system_path": str(self.system_path) if self.capture_system else None,
            "started_at": self._t0_holder[0],
            "duration_sec": duration,
        }

    @property
    def is_recording(self) -> bool:
        return any(w.is_alive() for w in self._workers)
