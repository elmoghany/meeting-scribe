"""Cornell remote-submission orchestration (paramiko mocked, no SSH/cluster).

submit_and_fetch is pure control flow around an SSH client + SFTP: make remote
dirs, upload WAVs, write+submit an sbatch script, poll squeue until the job
leaves the queue, then fetch result.json. We fake the transport via _connect so
the job-id parsing, poll loop, and error paths are tested without a cluster.
"""
import json

import pytest

from app.models import ActionItem, Segment, Summary
from app.pipeline.process import PipelineResult
from app.remote import cornell


def _result_bytes():
    res = PipelineResult(
        [Segment(start=0, end=2, text="hi", speaker="Me", source="batch", confidence=0.9)],
        Summary(overview="o", key_points=["k"], decisions=[]),
        [ActionItem(text="do x", owner="Sam", due="Fri", done=False)],
        "en", 2.0, "transformers", {})
    return json.dumps(res.to_json()).encode()


class _Chan:
    def __init__(self, rc):
        self._rc = rc

    def recv_exit_status(self):
        return self._rc


class _Stream:
    def __init__(self, data="", rc=0):
        self._data = data.encode() if isinstance(data, str) else data
        self.channel = _Chan(rc)

    def read(self):
        return self._data


class _WFile:
    def __init__(self, store, key):
        self._store, self._key, self._buf = store, key, ""

    def __enter__(self):
        return self

    def write(self, s):
        self._buf += s

    def __exit__(self, *a):
        self._store[self._key] = self._buf


class _RFile:
    def __init__(self, data):
        self._data = data

    def __enter__(self):
        return self

    def read(self):
        return self._data

    def __exit__(self, *a):
        pass


class _FakeSFTP:
    def __init__(self, result_bytes, store):
        self.puts, self._result, self._store = [], result_bytes, store

    def put(self, local, remote):
        self.puts.append((local, remote))

    def open(self, path, mode):
        if mode.startswith("r"):
            return _RFile(self._result)
        return _WFile(self._store, path)


class _FakeSSH:
    def __init__(self, responder, sftp):
        self._responder, self._sftp = responder, sftp
        self.closed = False

    def open_sftp(self):
        return self._sftp

    def exec_command(self, cmd, timeout=None):
        rc, out, err = self._responder(cmd)
        return None, _Stream(out, rc), _Stream(err)

    def close(self):
        self.closed = True


def _wire(monkeypatch, responder, store=None):
    store = store if store is not None else {}
    sftp = _FakeSFTP(_result_bytes(), store)
    ssh = _FakeSSH(responder, sftp)
    monkeypatch.setattr(cornell, "_connect", lambda: ssh)
    return ssh, sftp, store


def _audio(tmp_path):
    (tmp_path / "mic.wav").write_bytes(b"RIFFmic")
    (tmp_path / "system.wav").write_bytes(b"RIFFsys")
    return str(tmp_path)


def test_submit_and_fetch_happy_path(tmp_path, monkeypatch):
    state = {"squeue": 0}

    def responder(cmd):
        if "sbatch" in cmd:
            return (0, "98765;cornell-cluster", "")     # --parsable -> jobid;cluster
        if "squeue" in cmd:
            state["squeue"] += 1
            return (0, "RUNNING" if state["squeue"] < 2 else "", "")  # finishes 2nd poll
        return (0, "", "")                               # mkdir / test -f succeed

    ssh, sftp, store = _wire(monkeypatch, responder)
    msgs = []
    res = cornell.submit_and_fetch("m-remote", _audio(tmp_path), poll_sec=0,
                                   progress=msgs.append)

    assert [s.text for s in res.segments] == ["hi"]      # parsed from result.json
    assert res.action_items[0].owner == "Sam" and res.backend == "transformers"
    assert len(sftp.puts) == 2                            # both WAVs uploaded
    assert any("job.sbatch" in k for k in store)          # sbatch script written
    assert any("98765" in m for m in msgs)                # job id surfaced in progress
    assert ssh.closed is True                             # connection closed in finally


def test_submit_and_fetch_sbatch_failure_raises(tmp_path, monkeypatch):
    def responder(cmd):
        if "sbatch" in cmd:
            return (1, "", "QOSMaxJobs limit")           # submission rejected
        return (0, "", "")

    _wire(monkeypatch, responder)
    with pytest.raises(RuntimeError, match="sbatch failed"):
        cornell.submit_and_fetch("m1", _audio(tmp_path), poll_sec=0)


def test_submit_and_fetch_missing_result_raises_with_log(tmp_path, monkeypatch):
    def responder(cmd):
        if "sbatch" in cmd:
            return (0, "5;c", "")
        if "squeue" in cmd:
            return (0, "", "")                           # finishes immediately
        if "test -f" in cmd:
            return (1, "", "")                           # result.json absent
        if "tail" in cmd:
            return (0, "Traceback: CUDA OOM", "")        # log tail fetched
        return (0, "", "")

    _wire(monkeypatch, responder)
    with pytest.raises(RuntimeError, match="no result.json"):
        cornell.submit_and_fetch("m2", _audio(tmp_path), poll_sec=0)


def test_submit_and_fetch_times_out(tmp_path, monkeypatch):
    def responder(cmd):
        if "sbatch" in cmd:
            return (0, "7;c", "")
        return (0, "RUNNING", "")                         # never leaves the queue

    _wire(monkeypatch, responder)
    with pytest.raises(TimeoutError):
        cornell.submit_and_fetch("m3", _audio(tmp_path), poll_sec=0, max_wait_sec=0)


def test_process_remote_persists_and_summarizes(monkeypatch):
    res = PipelineResult(
        [Segment(start=0, end=1, text="a", speaker="Me", source="batch")],
        Summary(overview="o", key_points=[], decisions=[]),
        [ActionItem(text="t", owner=None, due=None, done=False)],
        "en", 1.0, "transformers", {})
    monkeypatch.setattr(cornell, "submit_and_fetch", lambda *a, **k: res)
    persisted = {}
    from app.pipeline import process
    monkeypatch.setattr(process, "persist_result",
                        lambda mid, r: persisted.update(mid=mid, n=len(r.segments)))
    out = cornell.process_remote("m-final", "/aud")
    assert persisted == {"mid": "m-final", "n": 1}        # persisted to local DB
    assert out == {"segments": 1, "action_items": 1, "language": "en",
                   "backend": "transformers", "via": "cornell"}
