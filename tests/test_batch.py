"""run_batch dispatch: local CPU vs Cornell remote, with graceful fallback.

A regression here breaks all post-meeting processing — e.g. if the remote path
stops falling back to local, an unreachable cluster would silently drop notes.
The two backends are mocked so no GPU/SSH is touched.
"""
from app import batch, config
from app.pipeline import process
from app.remote import cornell


def _reset():
    config.get_settings.cache_clear()


def test_run_batch_runs_local_when_remote_disabled(monkeypatch):
    monkeypatch.setenv("MEETINGSCRIBE_REMOTE", "0")
    _reset()
    seen = {}

    def local(mid, audio_dir=None):
        seen["local"] = (mid, audio_dir)
        return {"ok": 1}

    monkeypatch.setattr(process, "process_local", local)
    try:
        out = batch.run_batch("m1", "/aud")
        assert out == {"ok": 1}
        assert seen["local"] == ("m1", "/aud")     # went straight to local
    finally:
        _reset()


def test_run_batch_uses_remote_when_enabled(monkeypatch):
    monkeypatch.setenv("MEETINGSCRIBE_REMOTE", "1")
    _reset()
    seen = {}

    def remote(mid, audio_dir, progress=print):
        seen["remote"] = mid
        return {"src": "gpu"}

    monkeypatch.setattr(cornell, "process_remote", remote)
    try:
        assert batch.run_batch("m2", "/aud")["src"] == "gpu"
        assert seen["remote"] == "m2"              # dispatched to the cluster
    finally:
        _reset()


def test_run_batch_falls_back_to_local_on_remote_failure(monkeypatch):
    monkeypatch.setenv("MEETINGSCRIBE_REMOTE", "1")
    _reset()
    msgs = []

    def boom(mid, audio_dir, progress=print):
        raise RuntimeError("cluster unreachable")

    monkeypatch.setattr(cornell, "process_remote", boom)
    monkeypatch.setattr(process, "process_local",
                        lambda mid, audio_dir=None: {"src": "local-fallback"})
    try:
        out = batch.run_batch("m3", "/aud", progress=msgs.append)
        assert out == {"src": "local-fallback"}    # recovered locally
        assert any("falling back to local" in m for m in msgs)   # and told the user
    finally:
        _reset()
