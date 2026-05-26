"""FastAPI app powering the local dashboard.

Single active recording at a time. Live draft segments stream to all connected
browsers over a WebSocket; when you stop, the high-quality batch pass runs in a
background thread (on Cornell if enabled, else local CPU) and emits a 'processed'
event when the notes are ready.
"""
from __future__ import annotations

import asyncio
import threading
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

app = FastAPI(title="MeetingScribe", version="0.1.0")

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


@app.on_event("startup")
async def _startup() -> None:
    global _loop, _auto
    _loop = asyncio.get_running_loop()
    s = get_settings()
    s.ensure_dirs()
    asyncio.create_task(_broadcaster())

    # Auto-record scheduled Zoom meetings, if configured.
    from ..integrations import zoom
    if s.autostart_enabled and zoom.is_configured():
        from ..scheduler import AutoRecorder
        _auto = AutoRecorder(
            start_fn=lambda title, platform: _start_recording(title, platform).id,
            stop_fn=_stop_recording,
            is_recording_fn=lambda: bool(_session and _session.is_recording),
        )
        _auto.start()


@app.on_event("shutdown")
async def _shutdown() -> None:
    if _auto is not None:
        _auto.stop_thread()


# --------------------------------------------------------------------------- #
# models
# --------------------------------------------------------------------------- #
class StartReq(BaseModel):
    title: str = "Untitled meeting"
    platform: str = "other"
    capture_mic: bool = True
    capture_system: bool = True


class ChatReq(BaseModel):
    question: str


class RenameReq(BaseModel):
    mapping: dict[str, str]


# --------------------------------------------------------------------------- #
# recording control
# --------------------------------------------------------------------------- #
def _start_recording(title: str, platform: str, capture_mic: bool = True,
                     capture_system: bool = True):
    """Start the single active recording. Raises RuntimeError on conflict."""
    global _session
    with _session_lock:
        if _session and _session.is_recording:
            raise RuntimeError("A recording is already in progress.")
        _session = MeetingSession(title=title, platform=platform, emit=_emit,
                                  capture_mic=capture_mic, capture_system=capture_system)
        try:
            return _session.start()
        except Exception:
            _session = None
            raise


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
                                   req.capture_system)
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
def meetings():
    return [m.to_dict() for m in db.list_meetings()]


@app.get("/api/meetings/{meeting_id}")
def meeting_detail(meeting_id: str):
    m = db.get_meeting(meeting_id)
    if not m:
        raise HTTPException(404, "Meeting not found")
    summary = db.get_summary(meeting_id)
    segs = db.get_segments(meeting_id, source="batch") or db.get_segments(meeting_id)
    return {
        "meeting": m.to_dict(),
        "summary": summary.to_dict() if summary else None,
        "action_items": [a.to_dict() for a in db.get_action_items(meeting_id)],
        "segments": [s.to_dict() for s in segs],
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
        elif fmt in exporters.EXPORTERS:
            media, fn = exporters.EXPORTERS[fmt]
            body = fn(segs)
        else:
            raise HTTPException(400, f"Unknown format '{fmt}'")
    return PlainTextResponse(body, media_type=media, headers={
        "Content-Disposition": f'attachment; filename="{meeting_id}.{fmt}"'})


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


@app.delete("/api/meetings/{meeting_id}")
def meeting_delete(meeting_id: str):
    db.delete_meeting(meeting_id)
    return {"deleted": meeting_id}


@app.post("/api/meetings/{meeting_id}/rename-speakers")
def rename_speakers(meeting_id: str, req: RenameReq):
    from ..pipeline import assemble
    segs = db.get_segments(meeting_id, source="batch")
    segs = assemble.rename_speakers(segs, req.mapping)
    db.replace_segments(meeting_id, segs, source="batch")
    from ..pipeline.process import export_markdown
    export_markdown(meeting_id)
    return {"updated": len(segs)}


@app.post("/api/meetings/{meeting_id}/reprocess")
def reprocess(meeting_id: str):
    s = get_settings()
    audio_dir = str(s.recordings_dir / meeting_id)

    def _bg():
        try:
            stats = run_batch(meeting_id, audio_dir,
                              progress=lambda m: _emit({"type": "progress",
                                                        "meeting_id": meeting_id,
                                                        "message": str(m)}))
            _emit({"type": "processed", "meeting_id": meeting_id, **stats})
        except Exception as e:
            _emit({"type": "error", "meeting_id": meeting_id, "message": str(e)})

    threading.Thread(target=_bg, daemon=True).start()
    return {"meeting_id": meeting_id, "status": "processing"}


@app.post("/api/action/{item_id}")
def toggle_action(item_id: int, done: bool = True):
    db.set_action_done(item_id, done)
    return {"id": item_id, "done": done}


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
