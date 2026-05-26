"""Live meeting session: capture + near-real-time draft transcription.

A ``MeetingSession`` owns a DualRecorder and a consumer thread that runs the
small live Whisper model on each audio window, writes 'live' segments to the DB,
and emits events (consumed by the dashboard WebSocket). Heavy ASR runs off the
capture threads via a queue so audio capture never stalls.
"""
from __future__ import annotations

import queue
import threading
import time
import uuid
from typing import Callable

from . import db
from .capture import DualRecorder
from .config import get_settings
from .models import Meeting

Emit = Callable[[dict], None]


class MeetingSession:
    def __init__(self, title: str = "Untitled meeting", platform: str = "other",
                 emit: Emit | None = None, capture_mic: bool = True,
                 capture_system: bool = True):
        s = get_settings()
        self.meeting = Meeting(
            id=time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:6],
            title=title, platform=platform, started_at=time.time(), status="recording",
        )
        self.emit = emit or (lambda e: None)
        self._rec_dir = s.recordings_dir / self.meeting.id
        self._win_q: "queue.Queue" = queue.Queue(maxsize=64)
        self._stop = threading.Event()
        self._recorder = DualRecorder(
            self._rec_dir, sample_rate=s.sample_rate, window_sec=s.live_chunk_sec,
            on_window=self._on_window, capture_mic=capture_mic,
            capture_system=capture_system,
        )
        self._consumer = threading.Thread(target=self._consume, daemon=True,
                                          name="live-asr")

    # -- capture-thread side: just enqueue, never block on ASR --
    def _on_window(self, stream, samples, t_start):
        try:
            self._win_q.put_nowait((stream, samples, t_start))
        except queue.Full:
            pass  # drop oldest-style: skip this window if ASR is behind

    # -- consumer thread: run live ASR, persist + emit --
    def _consume(self):
        from .pipeline.asr import Transcriber

        transcriber = Transcriber()  # uses live_model + auto device
        while not (self._stop.is_set() and self._win_q.empty()):
            try:
                stream, samples, t_start = self._win_q.get(timeout=0.5)
            except queue.Empty:
                continue
            speaker = "Me" if stream == "mic" else "Others"
            try:
                segs = transcriber.transcribe_window(
                    samples, t_offset=t_start, speaker=speaker, source="live")
            except Exception as e:
                self.emit({"type": "error", "message": f"live ASR: {e}"})
                continue
            for seg in segs:
                seg.id = db.add_segment(self.meeting.id, seg)
                self.emit({"type": "segment", "meeting_id": self.meeting.id,
                           **seg.to_dict()})

    def start(self):
        get_settings().ensure_dirs()
        db.create_meeting(self.meeting)
        self._recorder.start()
        self._consumer.start()
        self.emit({"type": "started", **self.meeting.to_dict()})
        return self.meeting

    def stop(self) -> dict:
        info = self._recorder.stop()
        self._stop.set()
        self._consumer.join(timeout=30)
        self.meeting.ended_at = time.time()
        self.meeting.duration_sec = info.get("duration_sec")
        self.meeting.status = "processing"
        db.update_meeting(self.meeting.id, ended_at=self.meeting.ended_at,
                          duration_sec=self.meeting.duration_sec, status="processing")
        self.emit({"type": "stopped", **self.meeting.to_dict()})
        return {"meeting_id": self.meeting.id, "audio_dir": str(self._rec_dir), **info}

    @property
    def is_recording(self) -> bool:
        return self._recorder.is_recording
