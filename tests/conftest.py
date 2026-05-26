"""Point MeetingScribe at a throwaway data dir for the whole test session."""
import os
import tempfile
from pathlib import Path

# Must run before any app.* import triggers get_settings().
_TMP = Path(tempfile.mkdtemp(prefix="mscribe-test-"))
os.environ["MEETINGSCRIBE_DATA_DIR"] = str(_TMP)
os.environ["MEETINGSCRIBE_LLM_BACKEND"] = "extractive"

import pytest  # noqa: E402

from app.config import get_settings  # noqa: E402


@pytest.fixture(scope="session", autouse=True)
def _data_dir():
    get_settings.cache_clear()
    s = get_settings()
    s.ensure_dirs()
    yield s
