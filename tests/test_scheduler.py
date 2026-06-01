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

    def is_configured(self):
        return True

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


def _bare_recorder():
    return scheduler.AutoRecorder(lambda *a: "m", lambda: None, lambda: False)


def test_load_ics_reads_local_file(tmp_path, monkeypatch):
    from app import config
    ics_file = tmp_path / "cal.ics"
    ics_file.write_text("BEGIN:VCALENDAR\nEND:VCALENDAR\n", encoding="utf-8")
    monkeypatch.setenv("CALENDAR_ICS_PATH", str(ics_file))
    monkeypatch.delenv("CALENDAR_ICS_URL", raising=False)
    config.get_settings.cache_clear()
    try:
        ar = _bare_recorder()
        assert "VCALENDAR" in ar._load_ics(get_settings())     # file contents loaded
    finally:
        config.get_settings.cache_clear()


def test_load_ics_missing_file_records_error(tmp_path, monkeypatch):
    from app import config
    monkeypatch.setenv("CALENDAR_ICS_PATH", str(tmp_path / "nope.ics"))
    monkeypatch.delenv("CALENDAR_ICS_URL", raising=False)
    config.get_settings.cache_clear()
    try:
        ar = _bare_recorder()
        assert ar._load_ics(get_settings()) is None            # unreadable -> None
        assert ar.last_error and "ics-load" in ar.last_error    # surfaced, loop survives
    finally:
        config.get_settings.cache_clear()


def test_load_ics_none_when_unconfigured(monkeypatch):
    from app import config
    monkeypatch.delenv("CALENDAR_ICS_PATH", raising=False)
    monkeypatch.delenv("CALENDAR_ICS_URL", raising=False)
    config.get_settings.cache_clear()
    try:
        assert _bare_recorder()._load_ics(get_settings()) is None   # nothing configured
    finally:
        config.get_settings.cache_clear()


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


def test_bot_dispatched_for_zoom_with_join_url(monkeypatch):
    import time as _t
    monkeypatch.setenv("MEETINGSCRIBE_BOT_ENABLED", "1")
    get_settings.cache_clear()

    fake_zoom = _FakeZoom([{"id": "z9", "topic": "Sales call",
                            "start_time": _t.strftime("%Y-%m-%dT%H:%M:%SZ",
                                                       _t.gmtime(_t.time() + 30)),
                            "duration": 30,
                            "join_url": "https://zoom.us/j/123?pwd=x"}])
    monkeypatch.setattr(scheduler, "zoom", fake_zoom)

    bot_calls, start_calls = [], []
    ar = scheduler.AutoRecorder(
        start_fn=lambda t, p: start_calls.append((t, p)) or "x",
        stop_fn=lambda: None,
        is_recording_fn=lambda: False,
        bot_fn=lambda topic, url: bot_calls.append((topic, url)),
    )
    ar._tick(get_settings())
    assert bot_calls == [("Sales call", "https://zoom.us/j/123?pwd=x")]
    assert start_calls == []  # bot took over; local capture skipped


def test_ics_source_triggers_recording(monkeypatch):
    import time as _t
    from datetime import datetime, timezone
    ar, calls = _auto(monkeypatch, [])           # zoom fake, no zoom meetings
    ar._fake = monkeypatch  # keep ref
    monkeypatch.setattr(scheduler.zoom, "connected_flag", False, raising=False)
    # an .ics event starting ~30s from now
    start = datetime.fromtimestamp(_t.time() + 30, timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    cal = (f"BEGIN:VCALENDAR\nBEGIN:VEVENT\nSUMMARY:Daily Standup\n"
           f"DTSTART:{start}\nEND:VEVENT\nEND:VCALENDAR\n")
    monkeypatch.setattr(ar, "_load_ics", lambda s: cal)
    ar._tick(get_settings())
    assert calls["start"] == [("Daily Standup", "other")]
