"""Spawn the Zoom Meeting SDK bot binary (or its Docker image), wait for it to
join + record, and return the captured WAV path.

The actual SDK glue is the C++ `meetingscribe-bot` binary (see bot/main.cpp).
This runner is the orchestration layer — Python-only, fully testable.

Usage:
    info = run_bot(meeting_number="1234567890", passcode="abc", meeting_id="xyz",
                   join_url="https://zoom.us/j/...", max_sec=3600)
    # info["audio"] is a path to system.wav inside recordings/<meeting_id>/
"""
from __future__ import annotations

import os
import re
import shlex
import subprocess
import time
from pathlib import Path

from app.config import get_settings  # bot/ is a sibling of app/


def parse_join_url(url: str) -> tuple[str, str]:
    """Extract (meeting_number, passcode) from a Zoom join URL. passcode may be ''."""
    m = re.search(r"/j/(\d+)", url or "")
    mn = m.group(1) if m else ""
    pw = ""
    pm = re.search(r"[?&]pwd=([^&#]+)", url or "")
    if pm:
        pw = pm.group(1)
    return mn, pw


def _build_command(s, *, meeting_number: str, passcode: str, name: str,
                   audio_out: Path, max_sec: int, jwt: str) -> list[str]:
    """Build the argv to launch the bot. Uses Docker if configured, else the
    bare binary on $PATH or MEETINGSCRIBE_BOT_BINARY."""
    args = [
        "--meeting", str(meeting_number),
        "--passcode", passcode or "",
        "--name", name,
        "--jwt", jwt,
        "--audio-out", str(audio_out),
        "--max-sec", str(max_sec),
    ]
    if s.bot_docker_image:
        return [
            "docker", "run", "--rm",
            "-v", f"{audio_out.parent.as_posix()}:/recordings",
            "-e", f"BOT_AUDIO_OUT=/recordings/{audio_out.name}",
            s.bot_docker_image,
            *args[:-4],                       # everything except --audio-out value
            "--audio-out", f"/recordings/{audio_out.name}",
            "--max-sec", str(max_sec),
        ]
    return [s.bot_binary, *args]


def run_bot(meeting_number: str, passcode: str, meeting_id: str,
            name: str | None = None, max_sec: int | None = None,
            join_url: str | None = None) -> dict:
    """Run the bot, capture audio to recordings/<meeting_id>/system.wav, return
    {meeting_id, audio_dir, returncode, audio}.

    Raises if the bot binary / docker image is not configured.
    """
    from .jwt_sign import sign_meeting_sdk_jwt

    s = get_settings()
    if not (s.zoom_sdk_key and s.zoom_sdk_secret):
        raise RuntimeError("ZOOM_SDK_KEY / ZOOM_SDK_SECRET not configured for the bot")
    if join_url and not meeting_number:
        meeting_number, p_from_url = parse_join_url(join_url)
        passcode = passcode or p_from_url

    audio_dir = s.recordings_dir / meeting_id
    audio_dir.mkdir(parents=True, exist_ok=True)
    audio_out = audio_dir / "system.wav"

    jwt = sign_meeting_sdk_jwt(s.zoom_sdk_key, s.zoom_sdk_secret, meeting_number,
                               role=0)
    cmd = _build_command(
        s, meeting_number=meeting_number, passcode=passcode or "",
        name=name or s.bot_display_name, audio_out=audio_out,
        max_sec=int(max_sec if max_sec is not None else s.bot_max_meeting_sec),
        jwt=jwt,
    )

    started = time.time()
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=None,
                          env={**os.environ, "BOT_JWT": jwt})
    duration = time.time() - started
    return {
        "meeting_id": meeting_id,
        "audio_dir": str(audio_dir),
        "audio": str(audio_out) if audio_out.exists() else None,
        "returncode": proc.returncode,
        "duration_sec": round(duration, 1),
        "cmd": " ".join(shlex.quote(c) for c in cmd),
        "stderr_tail": "\n".join(proc.stderr.splitlines()[-10:]) if proc.stderr else "",
    }
