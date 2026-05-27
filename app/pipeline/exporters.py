"""Export a meeting's transcript/notes to standard formats, and compute simple
meeting analytics. All pure functions over Segment/Summary/ActionItem — no deps,
fully unit-testable. (Markdown export lives in process.export_markdown.)
"""
from __future__ import annotations

import csv
import io
import json
import re

from ..models import ActionItem, Meeting, Segment, Summary


def _ts(seconds: float, sep: str = ",") -> str:
    """Format seconds as HH:MM:SS,mmm (SRT) or HH:MM:SS.mmm (VTT, sep='.')."""
    seconds = max(0.0, seconds)
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    ms = int(round((seconds - int(seconds)) * 1000))
    if ms == 1000:
        s, ms = s + 1, 0
    return f"{h:02d}:{m:02d}:{s:02d}{sep}{ms:03d}"


def to_srt(segments: list[Segment]) -> str:
    """SubRip subtitles, speaker-prefixed."""
    lines = []
    for i, s in enumerate(sorted(segments, key=lambda x: x.start), 1):
        lines.append(str(i))
        lines.append(f"{_ts(s.start)} --> {_ts(s.end)}")
        who = f"{s.speaker}: " if s.speaker and s.speaker != "Unknown" else ""
        lines.append(f"{who}{s.text.strip()}")
        lines.append("")
    return "\n".join(lines)


def to_vtt(segments: list[Segment]) -> str:
    """WebVTT subtitles (browser-native, used by the audio player)."""
    out = ["WEBVTT", ""]
    for s in sorted(segments, key=lambda x: x.start):
        out.append(f"{_ts(s.start, '.')} --> {_ts(s.end, '.')}")
        who = f"<v {s.speaker}>" if s.speaker and s.speaker != "Unknown" else ""
        out.append(f"{who}{s.text.strip()}")
        out.append("")
    return "\n".join(out)


def to_txt(segments: list[Segment], with_timestamps: bool = True) -> str:
    lines = []
    for s in sorted(segments, key=lambda x: x.start):
        ts = f"[{int(s.start)//60:02d}:{int(s.start)%60:02d}] " if with_timestamps else ""
        lines.append(f"{ts}{s.speaker}: {s.text.strip()}")
    return "\n".join(lines)


def to_json(meeting: Meeting | None, summary: Summary | None,
            action_items: list[ActionItem], segments: list[Segment]) -> str:
    return json.dumps({
        "meeting": meeting.to_dict() if meeting else None,
        "summary": summary.to_dict() if summary else None,
        "action_items": [a.to_dict() for a in action_items],
        "transcript": [s.to_dict() for s in sorted(segments, key=lambda x: x.start)],
    }, indent=2)


_WORD = re.compile(r"\b[\w']+\b")


def action_items_csv(rows: list[dict]) -> str:
    """CSV of action items across meetings (rows from db.all_action_items).
    Columns: meeting, text, owner, due, done."""
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["meeting", "text", "owner", "due", "done"])
    for r in rows:
        w.writerow([r.get("meeting_title", ""), r.get("text", ""),
                    r.get("owner") or "", r.get("due") or "",
                    "yes" if r.get("done") else "no"])
    return buf.getvalue()


def talk_time(segments: list[Segment]) -> dict:
    """Per-speaker talk time + word counts + share, plus totals.

    Otter-style participation analytics, computed straight from segments.
    """
    per: dict[str, dict] = {}
    for s in segments:
        d = per.setdefault(s.speaker, {"seconds": 0.0, "words": 0, "segments": 0})
        d["seconds"] += max(0.0, s.end - s.start)
        d["words"] += len(_WORD.findall(s.text))
        d["segments"] += 1
    total_sec = sum(d["seconds"] for d in per.values()) or 1.0
    total_words = sum(d["words"] for d in per.values()) or 1
    speakers = []
    for name, d in sorted(per.items(), key=lambda kv: kv[1]["seconds"], reverse=True):
        speakers.append({
            "speaker": name,
            "seconds": round(d["seconds"], 1),
            "words": d["words"],
            "segments": d["segments"],
            "time_pct": round(100 * d["seconds"] / total_sec, 1),
            "word_pct": round(100 * d["words"] / total_words, 1),
            "wpm": round(d["words"] / (d["seconds"] / 60), 1) if d["seconds"] > 1 else 0.0,
        })
    return {
        "speakers": speakers,
        "total_seconds": round(total_sec, 1),
        "total_words": total_words,
        "num_speakers": len(per),
    }


EXPORTERS = {
    "srt": ("text/plain", to_srt),
    "vtt": ("text/vtt", to_vtt),
    "txt": ("text/plain", lambda segs: to_txt(segs)),
}
