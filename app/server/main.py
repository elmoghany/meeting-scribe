"""FastAPI app powering the local dashboard.

Single active recording at a time. Live draft segments stream to all connected
browsers over a WebSocket; when you stop, the high-quality batch pass runs in a
background thread (on Cornell if enabled, else local CPU) and emits a 'processed'
event when the notes are ready.
"""
from __future__ import annotations

import asyncio
import threading
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import (FileResponse, HTMLResponse, JSONResponse,
                               PlainTextResponse, RedirectResponse)
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .. import db
from ..batch import run_batch
from ..config import get_settings
from ..session import MeetingSession

@asynccontextmanager
async def lifespan(_app: "FastAPI"):
    # --- startup ---
    global _loop, _auto
    _loop = asyncio.get_running_loop()
    s = get_settings()
    s.ensure_dirs()
    asyncio.create_task(_broadcaster())
    from ..integrations import zoom
    if s.autostart_enabled and (zoom.is_configured() or s.calendar_ics):
        from ..scheduler import AutoRecorder
        _auto = AutoRecorder(
            start_fn=lambda title, platform: _start_recording(title, platform).id,
            stop_fn=_stop_recording,
            is_recording_fn=lambda: bool(_session and _session.is_recording),
            bot_fn=_bot_record,
        )
        _auto.start()
    yield
    # --- shutdown ---
    if _auto is not None:
        _auto.stop_thread()


app = FastAPI(title="MeetingScribe", version="0.1.0", lifespan=lifespan)

_STATIC = Path(__file__).parent / "static"
app.mount("/static", StaticFiles(directory=str(_STATIC)), name="static")

# --- live event plumbing (thread -> asyncio -> websockets) ---
_clients: set[WebSocket] = set()
_loop: asyncio.AbstractEventLoop | None = None
_event_q: "asyncio.Queue[dict]" = asyncio.Queue()
_session: MeetingSession | None = None
_session_lock = threading.Lock()
_auto = None  # AutoRecorder, started if Zoom is configured


def _emit(event: dict) -> None:
    """Thread-safe: hand an event to the asyncio loop for broadcast."""
    if _loop is not None and not _loop.is_closed():
        _loop.call_soon_threadsafe(_event_q.put_nowait, event)


async def _broadcaster() -> None:
    while True:
        event = await _event_q.get()
        dead = []
        for ws in list(_clients):
            try:
                await ws.send_json(event)
            except Exception:
                dead.append(ws)
        for ws in dead:
            _clients.discard(ws)


# --------------------------------------------------------------------------- #
# models
# --------------------------------------------------------------------------- #
class StartReq(BaseModel):
    title: str = "Untitled meeting"
    platform: str = "other"
    capture_mic: bool = True
    capture_system: bool = True
    language: str | None = None  # ISO code (en/es/fr/…) or None for auto-detect


class ChatReq(BaseModel):
    question: str


class RenameReq(BaseModel):
    mapping: dict[str, str]


class CommentReq(BaseModel):
    text: str
    segment_id: int | None = None
    author: str | None = None


class TitleReq(BaseModel):
    title: str


# --------------------------------------------------------------------------- #
# recording control
# --------------------------------------------------------------------------- #
def _start_recording(title: str, platform: str, capture_mic: bool = True,
                     capture_system: bool = True, language: str | None = None):
    """Start the single active recording. Raises RuntimeError on conflict."""
    global _session
    with _session_lock:
        if _session and _session.is_recording:
            raise RuntimeError("A recording is already in progress.")
        _session = MeetingSession(title=title, platform=platform, emit=_emit,
                                  capture_mic=capture_mic, capture_system=capture_system,
                                  language=language)
        try:
            return _session.start()
        except Exception:
            _session = None
            raise


def _bot_record(title: str, join_url: str) -> None:
    """Dispatch the headless Meeting SDK bot for a scheduled Zoom meeting.
    Runs in a background thread: joins the call, records audio, then triggers
    the batch pipeline. Errors are surfaced as 'error' events; never raises."""
    import time
    import uuid

    from ..models import Meeting

    mid = time.strftime("%Y%m%d-%H%M%S") + "-bot-" + uuid.uuid4().hex[:6]

    def _bg():
        try:
            from bot.runner import parse_join_url, run_bot
            mn, pwd = parse_join_url(join_url)
            db.create_meeting(Meeting(id=mid, title=title, platform="zoom",
                                      started_at=time.time(), status="recording"))
            _emit({"type": "bot_started", "meeting_id": mid, "title": title})
            info = run_bot(meeting_number=mn, passcode=pwd, meeting_id=mid,
                           join_url=join_url, name=get_settings().bot_display_name)
            db.update_meeting(mid, ended_at=time.time(), status="processing",
                              duration_sec=info.get("duration_sec"))
            _emit({"type": "bot_finished", "meeting_id": mid,
                   "audio": info.get("audio"), "rc": info.get("returncode")})
            run_batch(mid, info["audio_dir"], progress=lambda m: _emit(
                {"type": "progress", "meeting_id": mid, "message": str(m)}))
            _emit({"type": "processed", "meeting_id": mid})
        except Exception as e:
            db.update_meeting(mid, status="error")
            _emit({"type": "error", "meeting_id": mid, "message": f"bot: {e}"})

    threading.Thread(target=_bg, daemon=True, name=f"bot-{mid}").start()


def _stop_recording() -> dict:
    """Stop the active recording and kick off batch processing in the background."""
    global _session
    with _session_lock:
        if not _session:
            raise RuntimeError("No active recording.")
        info = _session.stop()
        _session = None
    meeting_id, audio_dir = info["meeting_id"], info["audio_dir"]

    def _bg():
        try:
            stats = run_batch(meeting_id, audio_dir, progress=lambda m: _emit(
                {"type": "progress", "meeting_id": meeting_id, "message": str(m)}))
            _emit({"type": "processed", "meeting_id": meeting_id, **stats})
        except Exception as e:
            db.update_meeting(meeting_id, status="error")
            _emit({"type": "error", "meeting_id": meeting_id, "message": str(e)})

    threading.Thread(target=_bg, daemon=True, name=f"batch-{meeting_id}").start()
    return {"meeting_id": meeting_id, "status": "processing"}


@app.post("/api/record/start")
def record_start(req: StartReq):
    try:
        meeting = _start_recording(req.title, req.platform, req.capture_mic,
                                   req.capture_system, req.language)
    except RuntimeError as e:
        raise HTTPException(409 if "already" in str(e) else 500, str(e))
    except Exception as e:
        raise HTTPException(500, f"Could not start capture: {e}")
    return meeting.to_dict()


@app.post("/api/record/stop")
def record_stop():
    try:
        return _stop_recording()
    except RuntimeError as e:
        raise HTTPException(409, str(e))


@app.get("/api/status")
def status():
    rec = bool(_session and _session.is_recording)
    return {"recording": rec, "meeting_id": _session.meeting.id if rec else None}


@app.get("/api/devices")
def devices():
    try:
        from ..capture import list_devices
        return list_devices()
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=503)


# --------------------------------------------------------------------------- #
# meetings
# --------------------------------------------------------------------------- #
@app.get("/api/meetings")
def meetings(tag: str | None = None):
    stats = db.meeting_stats()
    out = []
    for m in db.list_meetings(tag=tag):
        d = m.to_dict()
        d["tags"] = db.get_tags(m.id)
        d["stats"] = stats.get(m.id, {"segments": 0, "words": 0})
        out.append(d)
    return out


@app.get("/api/tags")
def tags():
    return db.all_tags()


@app.get("/api/vocab")
def get_vocab():
    """Custom-vocabulary terms (names/jargon) used to bias transcription."""
    return {"terms": get_settings().vocab_terms()}


@app.put("/api/vocab")
def set_vocab(req: TitleReq):
    """Replace the data-dir vocabulary file (comma/newline-separated terms).
    New transcriptions pick it up (the prompt is read per-Transcriber)."""
    s = get_settings()
    terms = [t.strip() for t in req.title.replace(",", "\n").splitlines() if t.strip()]
    s.vocab_path.write_text("\n".join(terms), encoding="utf-8")
    return {"terms": s.vocab_terms()}


@app.get("/api/speakers")
def list_speakers():
    """Known voice profiles (names only; embeddings stay server-side)."""
    return [{"name": p["name"], "n_samples": p["n_samples"]} for p in db.list_profiles()]


@app.get("/api/speakers/{name}/meetings")
def speaker_meetings(name: str):
    """Meetings where this named voice appears."""
    return db.profile_meetings(name)


@app.delete("/api/speakers/{name}")
def delete_speaker(name: str):
    db.delete_profile(name)
    return {"deleted": name}


@app.get("/api/meetings/{meeting_id}")
def meeting_detail(meeting_id: str):
    m = db.get_meeting(meeting_id)
    if not m:
        raise HTTPException(404, "Meeting not found")
    summary = db.get_summary(meeting_id)
    segs = db.get_segments(meeting_id, source="batch") or db.get_segments(meeting_id)
    from ..pipeline.notes import detect_meeting_type, keywords, sentiment
    return {
        "meeting": m.to_dict(),
        "summary": summary.to_dict() if summary else None,
        "action_items": [a.to_dict() for a in db.get_action_items(meeting_id)],
        "segments": [s.to_dict() for s in segs],
        "tags": db.get_tags(meeting_id),
        "topics": keywords(segs),
        "sentiment": sentiment(segs),
        # Suggest a template only when the user hasn't set one yet.
        "suggested_template": (detect_meeting_type(segs) if not m.template else None),
    }


@app.get("/api/meetings/{meeting_id}/markdown")
def meeting_markdown(meeting_id: str):
    from ..pipeline.process import export_markdown
    if not db.get_meeting(meeting_id):
        raise HTTPException(404, "Meeting not found")
    return PlainTextResponse(export_markdown(meeting_id).read_text(encoding="utf-8"))


@app.get("/api/meetings/{meeting_id}/export")
def meeting_export(meeting_id: str, fmt: str = "txt"):
    """Export the transcript/notes. fmt: srt | vtt | txt | json | md."""
    if not db.get_meeting(meeting_id):
        raise HTTPException(404, "Meeting not found")
    fmt = fmt.lower()
    if fmt == "md":
        from ..pipeline.process import export_markdown
        body = export_markdown(meeting_id).read_text(encoding="utf-8")
        media = "text/markdown"
    else:
        from ..pipeline import exporters
        segs = db.get_segments(meeting_id, source="batch") or db.get_segments(meeting_id)
        if fmt == "json":
            body = exporters.to_json(db.get_meeting(meeting_id), db.get_summary(meeting_id),
                                     db.get_action_items(meeting_id), segs)
            media = "application/json"
        elif fmt == "html":
            body = exporters.to_html(db.get_meeting(meeting_id), db.get_summary(meeting_id),
                                     db.get_action_items(meeting_id), segs)
            media = "text/html"
        elif fmt in exporters.EXPORTERS:
            media, fn = exporters.EXPORTERS[fmt]
            body = fn(segs)
        else:
            raise HTTPException(400, f"Unknown format '{fmt}'")
    return PlainTextResponse(body, media_type=media, headers={
        "Content-Disposition": f'attachment; filename="{meeting_id}.{fmt}"'})


@app.get("/api/meetings/{meeting_id}/clip")
def meeting_clip(meeting_id: str, start: float, end: float):
    """Download a WAV clip [start,end] of the meeting — share a single highlight."""
    s = get_settings()
    rec = s.recordings_dir / meeting_id
    from ..pipeline.audiomix import ensure_meeting_wav, extract_clip
    src = ensure_meeting_wav(rec)
    if not src:
        raise HTTPException(404, "No audio for this meeting")
    out = rec / f"clip_{int(start)}_{int(end)}.wav"
    if not extract_clip(src, start, end, out, pad=0.3):
        raise HTTPException(400, "Empty or invalid clip range")
    return FileResponse(str(out), media_type="audio/wav", headers={
        "Content-Disposition": f'attachment; filename="{meeting_id}_clip.wav"'})


@app.get("/api/meetings/{meeting_id}/highlight-reel")
def meeting_highlight_reel(meeting_id: str):
    """Concatenate all highlighted (starred) segments into one shareable reel."""
    s = get_settings()
    segs = {seg.id: seg for seg in
            (db.get_segments(meeting_id, source="batch") or db.get_segments(meeting_id))}
    spans = []
    for a in db.list_annotations(meeting_id):
        if a["kind"] == "highlight" and a["segment_id"] in segs:
            seg = segs[a["segment_id"]]
            spans.append((seg.start, seg.end))
    if not spans:
        raise HTTPException(404, "No highlighted lines to export")
    from ..pipeline.audiomix import export_highlight_reel
    out = s.recordings_dir / meeting_id / "highlight_reel.wav"
    if not export_highlight_reel(s.recordings_dir / meeting_id, spans, out):
        raise HTTPException(404, "No audio for this meeting")
    return FileResponse(str(out), media_type="audio/wav", headers={
        "Content-Disposition": f'attachment; filename="{meeting_id}_highlights.wav"'})


@app.get("/api/meetings/{meeting_id}/audio")
def meeting_audio(meeting_id: str):
    """Stream the mixed meeting audio (mic+system) for the synced player.
    FileResponse serves HTTP range requests, so the <audio> element can seek."""
    if not db.get_meeting(meeting_id):
        raise HTTPException(404, "Meeting not found")
    from ..pipeline.audiomix import ensure_meeting_wav
    path = ensure_meeting_wav(get_settings().recordings_dir / meeting_id)
    if not path:
        raise HTTPException(404, "No audio for this meeting")
    return FileResponse(str(path), media_type="audio/wav")


@app.get("/api/meetings/{meeting_id}/analytics")
def meeting_analytics(meeting_id: str):
    if not db.get_meeting(meeting_id):
        raise HTTPException(404, "Meeting not found")
    from ..pipeline import exporters
    segs = db.get_segments(meeting_id, source="batch") or db.get_segments(meeting_id)
    return exporters.talk_time(segs)


@app.get("/api/meetings/{meeting_id}/chapters")
def meeting_chapters(meeting_id: str):
    """Auto-detected jump-to-topic chapters for the timeline."""
    if not db.get_meeting(meeting_id):
        raise HTTPException(404, "Meeting not found")
    from ..pipeline import notes
    segs = db.get_segments(meeting_id, source="batch") or db.get_segments(meeting_id)
    return {"chapters": notes.chapters(segs)}


@app.post("/api/meetings/{meeting_id}/title")
def set_title(meeting_id: str, req: TitleReq):
    if not db.get_meeting(meeting_id):
        raise HTTPException(404, "Meeting not found")
    db.update_meeting(meeting_id, title=req.title.strip() or "Untitled meeting")
    return {"title": db.get_meeting(meeting_id).title}


class SummaryEditReq(BaseModel):
    overview: str = ""
    key_points: list[str] = []
    decisions: list[str] = []


@app.put("/api/meetings/{meeting_id}/summary")
def edit_summary(meeting_id: str, req: SummaryEditReq):
    """Manually edit the AI notes (overview / key points / decisions)."""
    if not db.get_meeting(meeting_id):
        raise HTTPException(404, "Meeting not found")
    from ..models import Summary
    from ..pipeline.process import export_markdown
    summ = Summary(
        overview=req.overview.strip(),
        key_points=[p.strip() for p in req.key_points if p.strip()],
        decisions=[d.strip() for d in req.decisions if d.strip()],
    )
    db.save_summary(meeting_id, summ)
    export_markdown(meeting_id)
    return summ.to_dict()


@app.post("/api/meetings/{meeting_id}/delete-audio")
def delete_audio(meeting_id: str):
    """Free disk by deleting the WAV recordings; keeps transcript & notes."""
    rec = get_settings().recordings_dir / meeting_id
    removed, freed = [], 0
    if rec.exists():
        for p in rec.glob("*.wav"):
            freed += p.stat().st_size
            p.unlink()
            removed.append(p.name)
    return {"removed": removed, "freed_mb": round(freed / 1e6, 1)}


def _delete_one(meeting_id: str) -> None:
    db.delete_meeting(meeting_id)
    import shutil
    rec = get_settings().recordings_dir / meeting_id
    if rec.exists():
        shutil.rmtree(rec, ignore_errors=True)


@app.delete("/api/meetings/{meeting_id}")
def meeting_delete(meeting_id: str):
    _delete_one(meeting_id)
    return {"deleted": meeting_id}


class BulkDeleteReq(BaseModel):
    ids: list[str]


@app.post("/api/meetings/delete-batch")
def meetings_delete_batch(req: BulkDeleteReq):
    """Delete several meetings (and their recordings) at once."""
    deleted = []
    for mid in req.ids:
        if db.get_meeting(mid):
            _delete_one(mid)
            deleted.append(mid)
    return {"deleted": deleted, "count": len(deleted)}


@app.post("/api/meetings/{meeting_id}/tags")
def add_meeting_tag(meeting_id: str, req: CommentReq):
    db.add_tag(meeting_id, req.text)
    return {"tags": db.get_tags(meeting_id)}


@app.delete("/api/meetings/{meeting_id}/tags/{tag}")
def remove_meeting_tag(meeting_id: str, tag: str):
    db.remove_tag(meeting_id, tag)
    return {"tags": db.get_tags(meeting_id)}


@app.post("/api/meetings/{meeting_id}/rename-speakers")
def rename_speakers(meeting_id: str, req: RenameReq):
    # Enroll a persistent voice profile for each rename (if we have that
    # speaker's embedding), so this person is auto-recognized in future meetings.
    enrolled = []
    for old, new in req.mapping.items():
        emb = db.get_meeting_embedding(meeting_id, old)
        if emb and new.strip() and new != old:
            db.upsert_profile(new, emb)
            db.save_meeting_embeddings(meeting_id, {new: emb})
            enrolled.append(new)
    # Relabel in place (keeps segment IDs, so highlights/comments stay attached).
    updated = db.rename_segment_speakers(meeting_id, req.mapping)
    from ..pipeline.process import export_markdown
    export_markdown(meeting_id)
    return {"updated": updated, "enrolled": enrolled}


@app.post("/api/meetings/{meeting_id}/reprocess")
def reprocess(meeting_id: str):
    s = get_settings()
    audio_dir = str(s.recordings_dir / meeting_id)

    # capture each annotation's anchor TIME before re-transcription churns segment IDs
    old = {s.id: s.start for s in db.get_segments(meeting_id, source="batch")}
    anchors = [(a["id"], old.get(a["segment_id"])) for a in db.list_annotations(meeting_id)
               if a["segment_id"] in old]

    def _bg():
        try:
            stats = run_batch(meeting_id, audio_dir,
                              progress=lambda m: _emit({"type": "progress",
                                                        "meeting_id": meeting_id,
                                                        "message": str(m)}))
            if anchors:                       # re-attach highlights/comments to the new segments
                db.reanchor_annotations(meeting_id, anchors)
            _emit({"type": "processed", "meeting_id": meeting_id, **stats})
        except Exception as e:
            _emit({"type": "error", "meeting_id": meeting_id, "message": str(e)})

    threading.Thread(target=_bg, daemon=True).start()
    return {"meeting_id": meeting_id, "status": "processing"}


@app.get("/api/templates")
def list_templates():
    """Available summary templates (meeting types)."""
    from ..pipeline.notes import SUMMARY_TEMPLATES
    return {"templates": list(SUMMARY_TEMPLATES.keys())}


@app.post("/api/meetings/{meeting_id}/regenerate-notes")
def regenerate_notes(meeting_id: str, template: str | None = None):
    """Re-run summary + action-item extraction on the existing transcript
    (no re-transcription). Optional `template` (standup/one_on_one/…) tailors
    the LLM summary and is remembered on the meeting."""
    if not db.get_meeting(meeting_id):
        raise HTTPException(404, "Meeting not found")
    segs = db.get_segments(meeting_id, source="batch") or db.get_segments(meeting_id)
    if not segs:
        raise HTTPException(404, "No transcript to summarize yet")
    from ..pipeline.notes import SUMMARY_TEMPLATES, get_notes_backend
    from ..pipeline.process import export_markdown
    if template is not None and template not in SUMMARY_TEMPLATES:
        raise HTTPException(400, f"Unknown template '{template}'")
    if template is not None:
        db.update_meeting(meeting_id, template=template)
    tmpl = template or db.get_meeting(meeting_id).template
    backend = get_notes_backend()
    summary, items = backend.summarize(segs, template=tmpl)
    db.save_summary(meeting_id, summary)
    db.save_action_items(meeting_id, items)
    export_markdown(meeting_id)
    return {"key_points": len(summary.key_points), "action_items": len(items),
            "backend": backend.backend, "template": tmpl}


@app.post("/api/action/{item_id}")
def toggle_action(item_id: int, done: bool = True):
    db.set_action_done(item_id, done)
    return {"id": item_id, "done": done}


class ActionItemReq(BaseModel):
    text: str | None = None
    owner: str | None = None
    due: str | None = None


def _reexport(meeting_id: str | None):
    if meeting_id:
        from ..pipeline.process import export_markdown
        export_markdown(meeting_id)


@app.post("/api/meetings/{meeting_id}/action-items")
def add_action_item(meeting_id: str, req: ActionItemReq):
    """Manually add an action item the AI missed."""
    if not db.get_meeting(meeting_id):
        raise HTTPException(404, "Meeting not found")
    if not (req.text and req.text.strip()):
        raise HTTPException(400, "Action item text is required")
    from ..models import ActionItem
    item_id = db.add_action_item(meeting_id, ActionItem(
        text=req.text.strip(), owner=(req.owner or None), due=(req.due or None)))
    _reexport(meeting_id)
    return {"id": item_id}


@app.patch("/api/action/{item_id}")
def edit_action_item(item_id: int, req: ActionItemReq):
    """Edit an action item's text/owner/due."""
    mid = db.update_action_item(item_id, text=req.text, owner=req.owner, due=req.due)
    if not mid:
        raise HTTPException(404, "Action item not found")
    _reexport(mid)
    return {"id": item_id, "meeting_id": mid}


@app.delete("/api/action/{item_id}")
def remove_action_item(item_id: int):
    mid = db.delete_action_item(item_id)
    if not mid:
        raise HTTPException(404, "Action item not found")
    _reexport(mid)
    return {"deleted": item_id}


class SegmentEditReq(BaseModel):
    text: str | None = None
    speaker: str | None = None


@app.patch("/api/segments/{segment_id}")
def edit_segment(segment_id: int, req: SegmentEditReq):
    """Manually correct a transcript line's text and/or speaker."""
    if req.text is None and req.speaker is None:
        raise HTTPException(400, "Nothing to update")
    res = db.update_segment(segment_id, text=req.text, speaker=req.speaker)
    if not res:
        raise HTTPException(404, "Segment not found")
    from ..pipeline.process import export_markdown
    export_markdown(res["meeting_id"])
    return res


@app.get("/api/action-items")
def all_action_items(open_only: bool = False, owner: str | None = None):
    """Every action item across all meetings, with meeting context."""
    return db.all_action_items(open_only=open_only, owner=owner)


@app.get("/api/action-items.csv")
def all_action_items_csv(open_only: bool = False, owner: str | None = None):
    from ..pipeline import exporters
    rows = db.all_action_items(open_only=open_only, owner=owner)
    return PlainTextResponse(
        exporters.action_items_csv(rows), media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="action-items.csv"'})


# --------------------------------------------------------------------------- #
# highlights + comments
# --------------------------------------------------------------------------- #
@app.get("/api/meetings/{meeting_id}/annotations")
def get_annotations(meeting_id: str):
    return db.list_annotations(meeting_id)


@app.post("/api/meetings/{meeting_id}/comment")
def add_comment(meeting_id: str, req: CommentReq):
    if not req.text.strip():
        raise HTTPException(400, "Empty comment")
    aid = db.add_annotation(meeting_id, kind="comment", text=req.text.strip(),
                            segment_id=req.segment_id, author=req.author)
    return {"id": aid}


@app.post("/api/meetings/{meeting_id}/highlight/{segment_id}")
def toggle_highlight(meeting_id: str, segment_id: int):
    return {"highlighted": db.toggle_highlight(meeting_id, segment_id)}


@app.delete("/api/annotations/{annotation_id}")
def delete_annotation(annotation_id: int):
    db.delete_annotation(annotation_id)
    return {"deleted": annotation_id}


# --------------------------------------------------------------------------- #
# chat + search
# --------------------------------------------------------------------------- #
@app.post("/api/meetings/{meeting_id}/chat")
def chat(meeting_id: str, req: ChatReq):
    segs = db.get_segments(meeting_id, source="batch") or db.get_segments(meeting_id)
    if not segs:
        raise HTTPException(404, "No transcript yet for this meeting")
    from ..pipeline.notes import get_notes_backend
    answer = get_notes_backend().chat(req.question, segs)
    return {"question": req.question, "answer": answer}


@app.get("/api/search")
def search(q: str):
    if not q.strip():
        return []
    return db.search(q)


@app.post("/api/ask")
def ask_all_meetings(req: ChatReq):
    """Cross-meeting Q&A (like Fireflies AskFred / Fathom Perfect Recall):
    retrieve the most relevant segments across EVERY meeting via FTS, then let
    the notes backend answer over them with per-meeting citations."""
    from ..models import Segment
    from ..pipeline.notes import fts_query_from_question, get_notes_backend

    q = req.question.strip()
    if not q:
        raise HTTPException(400, "Empty question")
    ftsq = fts_query_from_question(q)
    hits = db.search(ftsq, limit=40) if ftsq else []
    if not hits:
        return {"question": q, "answer": "I couldn't find anything about that "
                "across your meetings.", "sources": []}
    # Turn hits into segments whose speaker carries the meeting citation, so the
    # backend's answer (and the extractive retrieval) is attributable.
    ctx = []
    for h in hits:
        text = (h.get("snippet") or "").replace("[", "").replace("]", "")
        ctx.append(Segment(start=h.get("start", 0.0), end=h.get("start", 0.0) + 1,
                           text=text, speaker=f"{h['title']} · {h['speaker']}",
                           source="batch"))
    answer = get_notes_backend().chat(q, ctx)
    return {"question": q, "answer": answer,
            "sources": hits[:10]}


# --------------------------------------------------------------------------- #
# Zoom integration (auto-record scheduled meetings)
# --------------------------------------------------------------------------- #
@app.get("/oauth/zoom/start")
def zoom_oauth_start():
    from ..integrations import zoom
    if not zoom.is_configured():
        raise HTTPException(400, "Set ZOOM_CLIENT_ID / ZOOM_CLIENT_SECRET in .env first.")
    return RedirectResponse(zoom.authorize_url())


@app.get("/oauth/zoom/callback", response_class=HTMLResponse)
def zoom_oauth_callback(code: str = "", error: str = ""):
    from ..integrations import zoom
    if error:
        return HTMLResponse(f"<h2>Zoom authorization failed: {error}</h2>")
    try:
        zoom.exchange_code(code)
    except Exception as e:
        return HTMLResponse(f"<h2>Zoom token exchange failed</h2><pre>{e}</pre>",
                            status_code=500)
    return HTMLResponse("<h2>&#9989; Zoom connected. You can close this tab and "
                        "return to MeetingScribe.</h2>")


@app.get("/api/zoom/status")
def zoom_status():
    from ..integrations import zoom
    configured = zoom.is_configured()
    return {"configured": configured,
            "connected": (zoom.connected() if configured else False),
            "autostart": get_settings().autostart_enabled}


@app.get("/api/zoom/upcoming")
def zoom_upcoming():
    from ..integrations import zoom
    if not zoom.connected():
        raise HTTPException(400, "Zoom not connected. Visit /oauth/zoom/start.")
    try:
        return zoom.upcoming_meetings()
    except Exception as e:
        raise HTTPException(502, f"Zoom API error: {e}")


# --------------------------------------------------------------------------- #
# websocket + index
# --------------------------------------------------------------------------- #
@app.websocket("/ws")
async def ws(websocket: WebSocket):
    await websocket.accept()
    _clients.add(websocket)
    try:
        while True:
            await websocket.receive_text()  # keepalive / ignore inbound
    except WebSocketDisconnect:
        pass
    finally:
        _clients.discard(websocket)


@app.get("/", response_class=HTMLResponse)
def index():
    return (_STATIC / "index.html").read_text(encoding="utf-8")
