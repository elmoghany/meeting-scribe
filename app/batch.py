"""Decide where the heavy batch pipeline runs: Cornell GPU (if enabled and
reachable) or local CPU. Used by both the CLI and the server."""
from __future__ import annotations

from .config import get_settings


def run_batch(meeting_id: str, audio_dir: str, progress=print) -> dict:
    s = get_settings()
    if s.remote_enabled:
        try:
            from .remote.cornell import process_remote
            return process_remote(meeting_id, audio_dir, progress=progress)
        except Exception as e:
            progress(f"[batch] Cornell remote failed ({e}); falling back to local CPU.")
    from .pipeline.process import process_local
    return process_local(meeting_id, audio_dir)
