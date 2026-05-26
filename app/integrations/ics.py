"""Dependency-free iCalendar (.ics) parsing for key-free auto-record.

Google/Outlook/Apple calendars all publish a secret ``.ics`` URL — no OAuth, no
API key. We parse VEVENTs (DTSTART + SUMMARY) and expand simple recurrences
(DAILY/WEEKLY, INTERVAL/COUNT/UNTIL/BYDAY) to list the meetings starting within a
time window, so the scheduler can auto-record them.

Limitations (documented): times with a TZID are treated as UTC-naive (most feeds
use UTC 'Z'); MONTHLY/YEARLY rules and EXDATE are not expanded. Good enough for
the common standup/sync case.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

_WEEKDAYS = {"MO": 0, "TU": 1, "WE": 2, "TH": 3, "FR": 4, "SA": 5, "SU": 6}


@dataclass
class Event:
    summary: str
    start: float  # epoch seconds (UTC)


def _unfold(text: str) -> list[str]:
    """RFC5545 line unfolding: continuation lines begin with space or tab."""
    out: list[str] = []
    for raw in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        if raw[:1] in (" ", "\t") and out:
            out[-1] += raw[1:]
        else:
            out.append(raw)
    return out


def _parse_dt(value: str) -> datetime | None:
    v = value.strip()
    try:
        if v.endswith("Z"):
            return datetime.strptime(v, "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)
        if "T" in v:  # naive -> assume UTC
            return datetime.strptime(v, "%Y%m%dT%H%M%S").replace(tzinfo=timezone.utc)
        return datetime.strptime(v, "%Y%m%d").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _parse_rrule(value: str) -> dict:
    out: dict = {}
    for part in value.split(";"):
        if "=" in part:
            k, _, val = part.partition("=")
            out[k.upper()] = val
    return out


def _expand(start: datetime, rrule: dict, window_end: datetime,
            cap: int = 2000) -> list[datetime]:
    """Expand a DAILY/WEEKLY RRULE into occurrences up to window_end."""
    freq = rrule.get("FREQ", "").upper()
    if freq not in ("DAILY", "WEEKLY"):
        return [start]  # unsupported recurrence → just the seed occurrence
    interval = max(1, int(rrule.get("INTERVAL", "1") or "1"))
    count = int(rrule["COUNT"]) if rrule.get("COUNT") else None
    until = _parse_dt(rrule["UNTIL"]) if rrule.get("UNTIL") else None
    bydays = [ _WEEKDAYS[d] for d in rrule.get("BYDAY", "").split(",")
               if d in _WEEKDAYS ] if rrule.get("BYDAY") else None

    occ: list[datetime] = []
    emitted = 0
    if freq == "DAILY":
        cur = start
        step = timedelta(days=interval)
        while cur <= window_end and len(occ) < cap:
            if until and cur > until:
                break
            occ.append(cur)
            emitted += 1
            if count and emitted >= count:
                break
            cur += step
    else:  # WEEKLY
        days = bydays if bydays else [start.weekday()]
        # Monday 00:00 of the seed week; the event time is added per-occurrence.
        week_start = (start - timedelta(days=start.weekday())).replace(
            hour=0, minute=0, second=0, microsecond=0)
        wk = 0
        while len(occ) < cap:
            base = week_start + timedelta(weeks=wk * interval)
            if base > window_end + timedelta(days=7):
                break
            for wd in sorted(days):
                cand = base + timedelta(days=wd, hours=start.hour, minutes=start.minute,
                                        seconds=start.second)
                if cand < start:
                    continue
                if until and cand > until:
                    return occ
                if cand > window_end:
                    continue
                occ.append(cand)
                emitted += 1
                if count and emitted >= count:
                    return occ
            wk += 1
            if wk > cap:
                break
    return occ


def parse_events(ics_text: str) -> list[tuple[str, datetime, dict]]:
    """Return raw (summary, start_dt, rrule) tuples for each VEVENT."""
    events: list[tuple[str, datetime, dict]] = []
    summary, start, rrule = "", None, {}
    in_event = False
    for line in _unfold(ics_text):
        name, _, value = line.partition(":")
        key = name.split(";", 1)[0].upper()
        if line.strip() == "BEGIN:VEVENT":
            in_event, summary, start, rrule = True, "", None, {}
        elif line.strip() == "END:VEVENT":
            if in_event and start is not None:
                events.append((summary or "Meeting", start, rrule))
            in_event = False
        elif in_event:
            if key == "SUMMARY":
                summary = value.strip()
            elif key == "DTSTART":
                start = _parse_dt(value)
            elif key == "RRULE":
                rrule = _parse_rrule(value)
    return events


def upcoming_events(ics_text: str, horizon_sec: int = 86400,
                    now: float | None = None) -> list[Event]:
    """Events starting within [now, now+horizon], recurrences expanded, sorted."""
    now_dt = datetime.fromtimestamp(now if now is not None else
                                    datetime.now(timezone.utc).timestamp(), timezone.utc)
    window_end = now_dt + timedelta(seconds=horizon_sec)
    out: list[Event] = []
    for summary, start, rrule in parse_events(ics_text):
        occurrences = _expand(start, rrule, window_end) if rrule else [start]
        for dt in occurrences:
            if now_dt - timedelta(hours=2) <= dt <= window_end:
                out.append(Event(summary=summary, start=dt.timestamp()))
    out.sort(key=lambda e: e.start)
    return out
