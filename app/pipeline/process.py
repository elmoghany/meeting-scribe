"""Batch post-processing: turn a meeting's raw audio into the polished,
speaker-labeled transcript + summary + action items.

``compute_pipeline`` is pure (no DB) so it runs identically locally or on the
Cornell GPU node. ``process_local`` wraps it with DB persistence + Markdown
export. The Cornell worker (app.remote.worker) calls ``compute_pipeline`` and
serializes the result to JSON, which the local side imports.
"""
from __future__ import annotations

import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

from .. import db
from ..config import get_settings
from ..models import ActionItem, Segment, Summary
from . import assemble
from .asr import Transcriber


def _log(msg: str) -> None:
    print(f"[process] {msg}", file=sys.stderr, flush=True)


@dataclass
class PipelineResult:
    segments: list[Segment]
    summary: Summary
    action_items: list[ActionItem]
    language: str | None
    duration_sec: float
    backend: str
    speaker_embeddings: dict = field(default_factory=dict)  # {speaker: d-vector}

    def to_json(self) -> dict:
        return {
            "segments": [s.to_dict() for s in self.segments],
            "summary": self.summary.to_dict(),
            "action_items": [a.to_dict() for a in self.action_items],
            "language": self.language,
            "duration_sec": self.duration_sec,
            "backend": self.backend,
            "speaker_embeddings": self.speaker_embeddings,
        }

    @staticmethod
    def from_json(d: dict) -> "PipelineResult":
        segs = [Segment(start=x["start"], end=x["end"], text=x["text"],
                        speaker=x["speaker"], source="batch",
                        confidence=x.get("confidence")) for x in d["segments"]]
        summ = Summary(overview=d["summary"]["overview"],
                       key_points=d["summary"]["key_points"],
                       decisions=d["summary"]["decisions"])
        items = [ActionItem(text=a["text"], owner=a.get("owner"), due=a.get("due"),
                            done=a.get("done", False)) for a in d["action_items"]]
        return PipelineResult(segs, summ, items, d.get("language"),
                              d.get("duration_sec", 0.0), d.get("backend", "?"),
                              d.get("speaker_embeddings", {}))


def compute_pipeline(audio_dir: str, batch_model: str | None = None) -> PipelineResult:
    """Run ASR + diarization + notes on a meeting's audio. No DB side effects."""
    s = get_settings()
    rec_dir = Path(audio_dir)
    mic_path = rec_dir / "mic.wav"
    system_path = rec_dir / "system.wav"

    transcriber = Transcriber(model_name=batch_model or s.batch_model)
    segments: list[Segment] = []
    language: str | None = None
    embeddings: dict = {}

    if system_path.exists():
        _log(f"transcribing system audio with {transcriber.model_name} ...")
        sys_segs, language = transcriber.transcribe_file(str(system_path), source="batch")
        try:
            from .diarize import label_speakers
            _log(f"diarizing system audio ({s.diarizer}) ...")
            sys_segs = label_speakers(str(system_path), sys_segs)
        except Exception as e:
            _log(f"diarization unavailable ({e}); labeling remote speakers as 'Others'.")
            for seg in sys_segs:
                if seg.speaker == "Unknown":
                    seg.speaker = "Others"
        if s.speaker_profiles:
            try:
                from .diarize import speaker_embeddings as _emb
                _log("computing speaker embeddings for profiles ...")
                embeddings = _emb(str(system_path), sys_segs)
            except Exception as e:
                _log(f"speaker embeddings unavailable ({e}).")
        segments.extend(sys_segs)

    if mic_path.exists():
        _log("transcribing microphone (Me) ...")
        mic_segs, lang2 = transcriber.transcribe_file(str(mic_path), speaker="Me",
                                                      source="batch")
        language = language or lang2
        segments.extend(mic_segs)

    if not segments:
        raise RuntimeError(f"No audio found to process in {rec_dir}")

    segments = assemble.merge_adjacent(sorted(segments, key=lambda x: x.start))

    from .notes import get_notes_backend
    backend = get_notes_backend()
    _log(f"generating notes with '{backend.backend}' backend ...")
    summary, action_items = backend.summarize(segments)
    duration = max((seg.end for seg in segments), default=0.0)
    return PipelineResult(segments, summary, action_items, language, duration,
                          backend.backend, embeddings)


def apply_profiles(res: PipelineResult) -> dict:
    """Match the result's per-speaker embeddings against stored named profiles
    (local DB) and rename matched speakers in place. Re-keys embeddings to the
    final labels. Returns {original_label: name} for matches. Local-side so it
    uses the local profile DB even when compute ran on the cluster."""
    s = get_settings()
    if not s.speaker_profiles or not res.speaker_embeddings:
        return {}
    from .speakerid import match
    profiles = db.list_profiles()
    renames: dict[str, str] = {}
    if profiles:
        for label, emb in res.speaker_embeddings.items():
            if label == "Me":
                continue
            hit = match(emb, profiles, threshold=s.speaker_match_threshold)
            if hit:
                renames[label] = hit[0]
    if renames:
        for seg in res.segments:
            if seg.speaker in renames:
                seg.speaker = renames[seg.speaker]
    res.speaker_embeddings = {renames.get(k, k): v
                              for k, v in res.speaker_embeddings.items()}
    return renames


def persist_result(meeting_id: str, res: PipelineResult) -> None:
    """Write a PipelineResult into the local DB and export Markdown."""
    matched = apply_profiles(res)
    if matched:
        _log(f"recognized known speakers: {matched}")
    db.replace_segments(meeting_id, res.segments, source="batch")
    if res.speaker_embeddings:
        db.save_meeting_embeddings(meeting_id, res.speaker_embeddings)
    db.save_summary(meeting_id, res.summary)
    db.save_action_items(meeting_id, res.action_items)
    db.update_meeting(meeting_id, status="done", language=res.language,
                      duration_sec=res.duration_sec, ended_at=time.time())
    export_markdown(meeting_id)

    # fire-and-forget outbound webhook (no-op if MEETINGSCRIBE_WEBHOOK_URL unset)
    try:
        from ..integrations.webhook import notify
        notify(meeting_id, db.get_meeting(meeting_id), res.summary, res.action_items)
    except Exception:
        pass


def process_local(meeting_id: str, audio_dir: str | None = None,
                  batch_model: str | None = None) -> dict:
    """Full batch pipeline for one meeting, run locally and persisted."""
    s = get_settings()
    rec_dir = audio_dir or str(s.recordings_dir / meeting_id)
    try:
        res = compute_pipeline(rec_dir, batch_model=batch_model)
    except Exception:
        db.update_meeting(meeting_id, status="error")
        raise
    persist_result(meeting_id, res)
    _log("done.")
    return {"segments": len(res.segments), "action_items": len(res.action_items),
            "language": res.language, "backend": res.backend}


def export_markdown(meeting_id: str) -> Path:
    """Write a shareable Markdown notes file under <data>/notes/<id>.md."""
    s = get_settings()
    m = db.get_meeting(meeting_id)
    summary = db.get_summary(meeting_id)
    items = db.get_action_items(meeting_id)
    segments = db.get_segments(meeting_id, source="batch") or db.get_segments(meeting_id)

    lines = [f"# {m.title if m else meeting_id}", ""]
    if m:
        when = time.strftime("%Y-%m-%d %H:%M", time.localtime(m.started_at))
        mins = (m.duration_sec or 0) / 60
        lines += [f"*{m.platform.title()} · {when} · {mins:.0f} min*", ""]
    if summary:
        if summary.overview:
            lines += ["## Summary", "", summary.overview, ""]
        if summary.key_points:
            lines += ["## Key points", ""] + [f"- {p}" for p in summary.key_points] + [""]
        if summary.decisions:
            lines += ["## Decisions", ""] + [f"- {d}" for d in summary.decisions] + [""]
    if items:
        lines += ["## Action items", ""]
        for a in items:
            meta = " · ".join(x for x in [a.owner, a.due] if x)
            lines.append(f"- [ ] {a.text}" + (f"  _({meta})_" if meta else ""))
        lines.append("")

    # Analytics block (topics, sentiment, talk-time) — all pure, computed here
    # from the same segments so the exported notes match what the dashboard shows.
    if segments:
        from . import exporters
        from .notes import chapters, keywords, sentiment

        topics = keywords(segments)
        if topics:
            lines += ["## Topics", "", " · ".join(topics), ""]

        chs = chapters(segments)
        if chs:
            lines += ["## Chapters", ""]
            for c in chs:
                mm, ss = divmod(int(c["start"]), 60)
                lines.append(f"- `{mm:02d}:{ss:02d}` {c['title']}")
            lines.append("")

        sent = sentiment(segments)
        if sent.get("positive") or sent.get("negative"):
            emoji = {"positive": "🙂", "negative": "🙁"}.get(sent["label"], "😐")
            sign = "+" if sent["score"] > 0 else ""
            lines += ["## Sentiment", "",
                      f"{emoji} **{sent['label']}** ({sign}{sent['score']}) — "
                      f"{sent['positive']} positive · {sent['negative']} negative cues",
                      ""]

        tt = exporters.talk_time(segments)
        if tt["speakers"]:
            lines += ["## Talk time", "",
                      "| Speaker | Time | Share | Words | WPM |",
                      "|---|--:|--:|--:|--:|"]
            for sp in tt["speakers"]:
                lines.append(f"| {sp['speaker']} | {sp['seconds']:.0f}s "
                             f"| {sp['time_pct']}% | {sp['words']} | {sp['wpm']} |")
            lines.append("")

    lines += ["## Transcript", "", assemble.to_transcript(segments), ""]

    out = s.notes_dir / f"{meeting_id}.md"
    out.write_text("\n".join(lines), encoding="utf-8")
    return out
