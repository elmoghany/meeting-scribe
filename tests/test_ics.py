"""ICS calendar parsing + recurrence expansion (pure, deterministic)."""
from datetime import datetime, timezone

from app.integrations import ics

NOW = datetime(2026, 6, 1, 9, 0, 0, tzinfo=timezone.utc)  # 2026-06-01 is a Monday
NOW_TS = NOW.timestamp()


def _cal(events: str) -> str:
    return "BEGIN:VCALENDAR\n" + events + "END:VCALENDAR\n"


def _ev(dtstart: str, summary: str = "Sync", rrule: str | None = None) -> str:
    s = f"BEGIN:VEVENT\nSUMMARY:{summary}\nDTSTART:{dtstart}\n"
    if rrule:
        s += f"RRULE:{rrule}\n"
    return s + "END:VEVENT\n"


def test_single_event_in_window():
    evs = ics.upcoming_events(_cal(_ev("20260601T100000Z", "Standup")),
                              horizon_sec=86400, now=NOW_TS)
    assert len(evs) == 1 and evs[0].summary == "Standup"
    assert abs(evs[0].start - datetime(2026, 6, 1, 10, tzinfo=timezone.utc).timestamp()) < 1


def test_event_outside_window_excluded():
    assert ics.upcoming_events(_cal(_ev("20260605T100000Z")), horizon_sec=86400,
                               now=NOW_TS) == []


def test_daily_count():
    evs = ics.upcoming_events(_cal(_ev("20260601T100000Z", "Daily", "FREQ=DAILY;COUNT=3")),
                              horizon_sec=3 * 86400, now=NOW_TS)
    assert len(evs) == 3


def test_daily_interval():
    evs = ics.upcoming_events(_cal(_ev("20260601T100000Z", rrule="FREQ=DAILY;INTERVAL=2")),
                              horizon_sec=5 * 86400, now=NOW_TS)
    assert len(evs) == 3  # 06-01, 06-03, 06-05


def test_until_stops():
    evs = ics.upcoming_events(
        _cal(_ev("20260601T100000Z", rrule="FREQ=DAILY;UNTIL=20260602T235959Z")),
        horizon_sec=10 * 86400, now=NOW_TS)
    assert len(evs) == 2  # 06-01 and 06-02 only


def test_weekly_byday_keeps_time_and_days():
    evs = ics.upcoming_events(
        _cal(_ev("20260601T100000Z", rrule="FREQ=WEEKLY;BYDAY=MO,WE,FR")),
        horizon_sec=7 * 86400, now=NOW_TS)
    assert len(evs) >= 3
    for e in evs:
        dt = datetime.fromtimestamp(e.start, timezone.utc)
        assert dt.weekday() in (0, 2, 4)   # Mon/Wed/Fri only
        assert (dt.hour, dt.minute) == (10, 0)  # time preserved (no doubling bug)


def test_line_unfolding():
    txt = ("BEGIN:VCALENDAR\nBEGIN:VEVENT\nSUMMARY:Long title that is\n  folded\n"
           "DTSTART:20260601T100000Z\nEND:VEVENT\nEND:VCALENDAR\n")
    evs = ics.upcoming_events(txt, horizon_sec=86400, now=NOW_TS)
    assert evs[0].summary == "Long title that is folded"


def test_unsupported_rrule_returns_seed_only():
    evs = ics.upcoming_events(_cal(_ev("20260601T100000Z", rrule="FREQ=MONTHLY")),
                              horizon_sec=2 * 86400, now=NOW_TS)
    assert len(evs) == 1
