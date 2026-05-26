"""Auto-record scheduler logic, with Zoom mocked (no network)."""
import time

from app import scheduler
from app.config import get_settings


def test_parse_zoom_time():
    t = scheduler.parse_zoom_time("2026-05-26T15:00:00Z")
    assert isinstance(t, float) and t > 0


class _FakeZoom:
    def __init__(self, meetings):
        self._m = meetings
        self.connected_flag = True

    def connected(self):
        return self.connected_flag

    def upcoming_meetings(self):
        return self._m


def _auto(monkeypatch, meetings):
    fake = _FakeZoom(meetings)
    monkeypatch.setattr(scheduler, "zoom", fake)
    calls = {"start": [], "stop": 0, "recording": False}

    def start(title, platform):
        calls["start"].append((title, platform))
        calls["recording"] = True
        return "mid-1"

    def stop():
        calls["stop"] += 1
        calls["recording"] = False

    ar = scheduler.AutoRecorder(start, stop, lambda: calls["recording"])
    return ar, calls


def test_starts_meeting_about_to_begin(monkeypatch):
    now = time.time()
    iso = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now + 30))  # in 30s
    ar, calls = _auto(monkeypatch, [{"id": "z1", "topic": "Standup",
                                     "start_time": iso, "duration": 30}])
    ar._tick(get_settings())
    assert calls["start"] == [("Standup", "zoom")]
    assert calls["recording"] is True


def test_does_not_start_far_future_meeting(monkeypatch):
    now = time.time()
    iso = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now + 3600))  # in 1h
    ar, calls = _auto(monkeypatch, [{"id": "z2", "topic": "Later",
                                     "start_time": iso, "duration": 30}])
    ar._tick(get_settings())
    assert calls["start"] == []


def test_does_not_double_start_same_meeting(monkeypatch):
    now = time.time()
    iso = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now + 10))
    ar, calls = _auto(monkeypatch, [{"id": "z3", "topic": "Sync",
                                     "start_time": iso, "duration": 30}])
    ar._tick(get_settings())
    calls["recording"] = False  # pretend it stopped
    ar._tick(get_settings())    # same meeting id should not start again
    assert len(calls["start"]) == 1


def test_auto_stop_after_scheduled_end(monkeypatch):
    ar, calls = _auto(monkeypatch, [])
    calls["recording"] = True
    ar._active = ("z9", time.time() - 1)  # ended 1s ago
    ar._tick(get_settings())
    assert calls["stop"] == 1
    assert ar._active is None
