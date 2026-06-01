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
from .assemble import _split_long

# Subtitle cues should show only a few seconds of text at a time; a 30s
# paragraph cue is unreadable on screen. Split long segments into ~this-long
# cues for SRT/VTT (timing within a segment is approximate but readability wins).
_CUE_MAX_SEC = 8.0
_SENTENCE = re.compile(r"(?<=[.!?])\s+")


def _cues(segments: list[Segment]) -> list[Segment]:
    """Split long segments into readable subtitle cues, preferring SENTENCE
    boundaries (a cue breaking mid-sentence reads badly). Falls back to an
    even time/word split when a segment has no usable sentence breaks. Cue
    timing is char-proportional — approximate but natural."""
    out: list[Segment] = []
    for s in sorted(segments, key=lambda x: x.start):
        dur = s.end - s.start
        if dur <= _CUE_MAX_SEC:
            out.append(s)
            continue
        sents = [x.strip() for x in _SENTENCE.split(s.text.strip()) if x.strip()]
        if len(sents) < 2:
            out.extend(_split_long(s, _CUE_MAX_SEC))   # no sentence breaks
            continue
        total = sum(len(x) for x in sents) or 1
        t = s.start
        for j, sent in enumerate(sents):
            end = s.end if j == len(sents) - 1 else min(s.end, t + dur * len(sent) / total)
            out.append(Segment(start=t, end=end, text=sent,
                               speaker=s.speaker, source=s.source))
            t = end
    return out


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
    for i, s in enumerate(_cues(segments), 1):
        lines.append(str(i))
        lines.append(f"{_ts(s.start)} --> {_ts(s.end)}")
        who = f"{s.speaker}: " if s.speaker and s.speaker != "Unknown" else ""
        lines.append(f"{who}{s.text.strip()}")
        lines.append("")
    return "\n".join(lines)


def to_vtt(segments: list[Segment]) -> str:
    """WebVTT subtitles (browser-native, used by the audio player)."""
    out = ["WEBVTT", ""]
    for s in _cues(segments):
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
    from .notes import chapters as _chapters
    return json.dumps({
        "meeting": meeting.to_dict() if meeting else None,
        "summary": summary.to_dict() if summary else None,
        "action_items": [a.to_dict() for a in action_items],
        "analytics": talk_time(segments),
        "chapters": _chapters(segments),
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


def _esc(s: str) -> str:
    return (s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


def to_html(meeting: Meeting | None, summary: Summary | None,
            action_items: list[ActionItem], segments: list[Segment]) -> str:
    """A single self-contained HTML file (inline CSS, no external assets) with
    the notes + speaker-labeled transcript — openable anywhere, shareable."""
    title = meeting.title if meeting else "Meeting notes"
    rows = []
    for s in sorted(segments, key=lambda x: x.start):
        mm, ss = divmod(int(s.start), 60)
        rows.append(
            f'<div class="seg"><span class="ts">{mm:02d}:{ss:02d}</span>'
            f'<span class="spk">{_esc(s.speaker)}</span>{_esc(s.text.strip())}</div>')
    parts = [
        "<!doctype html><html><head><meta charset='utf-8'>",
        f"<title>{_esc(title)}</title>",
        "<style>body{font:15px/1.6 -apple-system,Segoe UI,Roboto,sans-serif;"
        "max-width:780px;margin:40px auto;padding:0 18px;color:#1a1e27}"
        "h1{font-size:24px}h2{font-size:16px;margin-top:28px;color:#444}"
        ".seg{padding:4px 0;border-bottom:1px solid #eee}"
        ".ts{color:#999;font-size:12px;margin-right:8px}"
        ".spk{font-weight:600;color:#2563eb;margin-right:6px}"
        ".muted{color:#888}li{margin:3px 0}</style></head><body>",
        f"<h1>{_esc(title)}</h1>",
    ]
    if meeting:
        import time as _t
        when = _t.strftime("%Y-%m-%d %H:%M", _t.localtime(meeting.started_at))
        mins = (meeting.duration_sec or 0) / 60
        parts.append(f"<p class='muted'>{_esc(meeting.platform.title())} · {when} · "
                     f"{mins:.0f} min</p>")
    if summary and summary.overview:
        parts += ["<h2>Summary</h2>", f"<p>{_esc(summary.overview)}</p>"]
    if summary and summary.key_points:
        parts += ["<h2>Key points</h2><ul>"] + \
                 [f"<li>{_esc(p)}</li>" for p in summary.key_points] + ["</ul>"]
    if summary and summary.decisions:
        parts += ["<h2>Decisions</h2><ul>"] + \
                 [f"<li>{_esc(d)}</li>" for d in summary.decisions] + ["</ul>"]
    if action_items:
        parts.append("<h2>Action items</h2><ul>")
        for a in action_items:
            meta = " · ".join(x for x in [a.owner, a.due] if x)
            box = "☑" if a.done else "☐"
            parts.append(f"<li>{box} {_esc(a.text)}"
                         + (f" <span class='muted'>({_esc(meta)})</span>" if meta else "")
                         + "</li>")
        parts.append("</ul>")
    tt = talk_time(segments)
    if tt["num_speakers"] > 1:
        parts.append("<h2>Speakers</h2><ul>")
        for sp in tt["speakers"]:
            parts.append(
                f"<li><b>{_esc(sp['speaker'])}</b> "
                f"<span class='muted'>— {sp['time_pct']}% · {int(sp['seconds'])}s · "
                f"{sp['words']} words</span></li>")
        parts.append("</ul>")
    from .notes import chapters as _chapters
    chs = _chapters(segments)
    if chs:
        parts.append("<h2>Chapters</h2><ul>")
        for c in chs:
            mm, ss = divmod(int(c["start"]), 60)
            parts.append(f"<li><span class='muted'>{mm:02d}:{ss:02d}</span> {_esc(c['title'])}</li>")
        parts.append("</ul>")
    parts += ["<h2>Transcript</h2>"] + rows + ["</body></html>"]
    return "".join(parts)


EXPORTERS = {
    "srt": ("text/plain", to_srt),
    "vtt": ("text/vtt", to_vtt),
    "txt": ("text/plain", lambda segs: to_txt(segs)),
}
