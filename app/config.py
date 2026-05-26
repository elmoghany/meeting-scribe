"""Central configuration. Reads .env (gitignored) then process env.

Settings are resolved at instantiation (not import) so tests can repoint the
data dir via env + ``get_settings.cache_clear()``. Nothing secret is hardcoded;
the HuggingFace token only ever lives in .env / the environment and is used
solely to download the gated pyannote weights.
"""
from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(_PROJECT_ROOT / ".env")


def _bool(env: str, default: bool) -> bool:
    val = os.getenv(env)
    if val is None:
        return default
    return val.strip().lower() in {"1", "true", "yes", "on"}


class Settings:
    """Process-wide settings, resolved from the environment when constructed."""

    def __init__(self) -> None:
        self.project_root = _PROJECT_ROOT

        # storage
        self.data_dir = Path(
            os.getenv("MEETINGSCRIBE_DATA_DIR", r"C:\cornell\meetingnotes")
        ).expanduser()

        # secrets
        self.hf_token = os.getenv("HUGGINGFACE_TOKEN") or None

        # optional outbound webhook (Slack/Discord/Notion/Zapier incoming URL)
        self.webhook_url = os.getenv("MEETINGSCRIBE_WEBHOOK_URL") or None

        # ASR
        self.live_model = os.getenv("MEETINGSCRIBE_LIVE_MODEL", "base.en")
        self.live_compute = os.getenv("MEETINGSCRIBE_LIVE_COMPUTE", "int8")
        self.batch_model = os.getenv("MEETINGSCRIBE_BATCH_MODEL", "large-v3")

        # diarization backend: resemblyzer (key-free, default) | pyannote (gated)
        self.diarizer = os.getenv("MEETINGSCRIBE_DIARIZER", "resemblyzer")

        # notes LLM
        self.llm_backend = os.getenv("MEETINGSCRIBE_LLM_BACKEND", "llamacpp")
        self.gguf_path = os.getenv("MEETINGSCRIBE_GGUF_PATH") or None

        # remote (Cornell SLURM)
        self.remote_enabled = _bool("MEETINGSCRIBE_REMOTE", False)
        self.cornell_user = os.getenv("CORNELL_USER", "me484")
        self.cornell_host = os.getenv("CORNELL_LOGIN_HOST",
                                      "unicorn-login-04.coecis.cornell.edu")
        self.cornell_remote_dir = os.getenv("CORNELL_REMOTE_DIR",
                                            "/home/me484/meetingnotes")

        # audio capture
        self.sample_rate = int(os.getenv("MEETINGSCRIBE_SAMPLE_RATE", "16000"))
        self.live_chunk_sec = float(os.getenv("MEETINGSCRIBE_LIVE_CHUNK_SEC", "5.0"))

        # Zoom integration (schedule reading -> auto-record). All optional.
        self.zoom_client_id = os.getenv("ZOOM_CLIENT_ID") or None
        self.zoom_client_secret = os.getenv("ZOOM_CLIENT_SECRET") or None
        self.zoom_account_id = os.getenv("ZOOM_ACCOUNT_ID") or None  # server-to-server
        self.zoom_redirect_uri = os.getenv(
            "ZOOM_REDIRECT_URI", "http://localhost:8765/oauth/zoom/callback")

        # .ics calendar (URL or local path) for key-free auto-record (any calendar)
        self.calendar_ics = os.getenv("CALENDAR_ICS_URL") or os.getenv("CALENDAR_ICS_PATH") or None

        # auto-record scheduled meetings
        self.autostart_enabled = _bool("MEETINGSCRIBE_AUTOSTART", True)
        self.autostart_lead_sec = int(os.getenv("MEETINGSCRIBE_AUTOSTART_LEAD_SEC", "120"))
        self.autostart_buffer_sec = int(os.getenv("MEETINGSCRIBE_AUTOSTART_BUFFER_SEC", "600"))
        self.autostart_poll_sec = int(os.getenv("MEETINGSCRIBE_AUTOSTART_POLL_SEC", "60"))

    @property
    def db_path(self) -> Path:
        return self.data_dir / "meetingscribe.db"

    @property
    def recordings_dir(self) -> Path:
        return self.data_dir / "recordings"

    @property
    def notes_dir(self) -> Path:
        return self.data_dir / "notes"

    @property
    def models_dir(self) -> Path:
        return self.data_dir / "models"

    def ensure_dirs(self) -> None:
        for p in (self.data_dir, self.recordings_dir, self.notes_dir, self.models_dir):
            p.mkdir(parents=True, exist_ok=True)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    s = Settings()
    s.ensure_dirs()
    return s
