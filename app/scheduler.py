"""Calendar-driven auto-record.

Polls the connected Zoom account for upcoming meetings and auto-starts local
system-audio capture when one is about to begin, then auto-stops it once the
meeting's scheduled window (duration + buffer) elapses. This is the key-free
interpretation of "auto-join": you attend the call, MeetingScribe records it
hands-free. (A bot that joins on its own would need the Zoom Meeting SDK.)

Wired in app.server.main via start/stop callables so it reuses the single-session
recording machinery.
"""
from __future__ import annotations

import threading
import time
from datetime import datetime
from typing import Callable

from .config import get_settings
from .integrations import zoom

# start_fn(title, platform) -> meeting_id ; stop_fn() ; is_recording_fn() -> bool
StartFn = Callable[[str, str], str]
StopFn = Callable[[], None]
IsRecFn = Callable[[], bool]


def parse_zoom_time(iso: str) -> float:
    """'2026-05-26T15:00:00Z' -> epoch seconds (UTC)."""
    return datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp()


class AutoRecorder(threading.Thread):
    def __init__(self, start_fn: StartFn, stop_fn: StopFn, is_recording_fn: IsRecFn):
        super().__init__(daemon=True, name="auto-recorder")
        self._start, self._stop, self._rec = start_fn, stop_fn, is_recording_fn
        self._run = threading.Event()
        self._run.set()
        self._started_ids: set[str] = set()
        self._active: tuple[str, float] | None = None  # (zoom_meeting_id, end_epoch)
        self.last_error: str | None = None
        self.last_poll: float | None = None

    def stop_thread(self) -> None:
        self._run.clear()

    def run(self) -> None:
        s = get_settings()
        while self._run.is_set():
            try:
                self._tick(s)
            except Exception as e:  # never let the loop die
                self.last_error = f"{type(e).__name__}: {e}"
            for _ in range(max(1, s.autostart_poll_sec)):
                if not self._run.is_set():
                    break
                time.sleep(1)

    def _tick(self, s) -> None:
        self.last_poll = time.time()
        now = time.time()

        # auto-stop an active recording once its scheduled window has passed
        if self._active and now >= self._active[1]:
            if self._rec():
                self._stop()
            self._active = None

        if not zoom.connected():
            return
        for m in zoom.upcoming_meetings():
            if not m.get("start_time") or m["id"] in self._started_ids:
                continue
            st = parse_zoom_time(m["start_time"])
            # start if the meeting begins within the lead window (or just began)
            if -s.autostart_buffer_sec <= (st - now) <= s.autostart_lead_sec:
                if not self._rec():
                    self._start(m["topic"], "zoom")
                    self._started_ids.add(m["id"])
                    dur = (m.get("duration") or 60) * 60
                    self._active = (m["id"], now + dur + s.autostart_buffer_sec)
                break
