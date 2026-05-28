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
from .integrations import ics, zoom

# start_fn(title, platform) -> meeting_id ; stop_fn() ; is_recording_fn() -> bool
StartFn = Callable[[str, str], str]
StopFn = Callable[[], None]
IsRecFn = Callable[[], bool]


def parse_zoom_time(iso: str) -> float:
    """'2026-05-26T15:00:00Z' -> epoch seconds (UTC)."""
    return datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp()


class AutoRecorder(threading.Thread):
    def __init__(self, start_fn: StartFn, stop_fn: StopFn, is_recording_fn: IsRecFn,
                 bot_fn=None):
        super().__init__(daemon=True, name="auto-recorder")
        self._start, self._stop, self._rec = start_fn, stop_fn, is_recording_fn
        # bot_fn(topic: str, join_url: str) -> None — runs the headless bot in
        # a background thread; if provided and the candidate has a join URL,
        # the bot is dispatched instead of local capture.
        self._bot_fn = bot_fn
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

        for c in self._candidates(s, now):
            if c["id"] in self._started_ids:
                continue
            # start if the meeting begins within the lead window (or just began)
            if -s.autostart_buffer_sec <= (c["start"] - now) <= s.autostart_lead_sec:
                # Prefer the headless bot when the candidate has a join URL.
                if self._bot_fn and s.bot_enabled and c.get("join_url"):
                    self._bot_fn(c["topic"], c["join_url"])
                    self._started_ids.add(c["id"])
                    # bot manages its own end; we don't set _active for stop
                elif not self._rec():
                    self._start(c["topic"], c["platform"])
                    self._started_ids.add(c["id"])
                    self._active = (c["id"], now + c["dur"] * 60 + s.autostart_buffer_sec)
                break

    def _candidates(self, s, now: float) -> list[dict]:
        """Gather upcoming meetings from all configured calendar sources."""
        out: list[dict] = []
        try:
            if zoom.is_configured() and zoom.connected():
                for m in zoom.upcoming_meetings():
                    if m.get("start_time"):
                        out.append({"id": "zoom:" + m["id"], "topic": m["topic"],
                                    "platform": "zoom",
                                    "start": parse_zoom_time(m["start_time"]),
                                    "dur": (m.get("duration") or 60),
                                    "join_url": m.get("join_url")})
        except Exception as e:
            self.last_error = f"zoom: {e}"
        text = self._load_ics(s)
        if text:
            try:
                for e in ics.upcoming_events(text, horizon_sec=86400, now=now):
                    out.append({"id": f"ics:{e.summary}:{int(e.start)}", "topic": e.summary,
                                "platform": "other", "start": e.start, "dur": 60})
            except Exception as e:
                self.last_error = f"ics-parse: {e}"
        return out

    def _load_ics(self, s) -> str | None:
        src = s.calendar_ics
        if not src:
            return None
        try:
            if src.startswith("http"):
                import httpx
                return httpx.get(src, timeout=15, follow_redirects=True).text
            from pathlib import Path
            return Path(src).read_text(encoding="utf-8")
        except Exception as e:
            self.last_error = f"ics-load: {e}"
            return None
