"""DualRecorder orchestration — worker lifecycle, duration, error propagation.

The actual audio loop (_StreamWorker.run) needs a real device and is excluded
from coverage; here we mock the soundcard backend and the worker so the
start/stop/is_recording lifecycle that MeetingSession relies on is tested with
no hardware.
"""
from types import SimpleNamespace

import pytest

from app.capture import recorder


class _FakeSC:
    def default_microphone(self):
        return SimpleNamespace(name="mic0")

    def default_speaker(self):
        return SimpleNamespace(name="spk0")

    def get_microphone(self, id, include_loopback=False):
        return SimpleNamespace(name=id, loopback=include_loopback)


class _FakeWorker:
    instances = []

    def __init__(self, name, mic_obj, out_path, sample_rate, window_sec,
                 on_window, stop_event, t0_holder):
        self.name = name
        self.error = None
        self.frames_written = int(sample_rate * 2)   # pretend 2.0s captured
        self._alive = False
        t0_holder[0] = 123.0                          # simulate a start timestamp
        _FakeWorker.instances.append(self)

    def start(self):
        self._alive = True

    def join(self, timeout=None):
        self._alive = False

    def is_alive(self):
        return self._alive


def _wire(monkeypatch, worker=_FakeWorker):
    _FakeWorker.instances = []
    monkeypatch.setattr(recorder, "_soundcard", lambda: _FakeSC())
    monkeypatch.setattr(recorder, "_StreamWorker", worker)


def test_dualrecorder_start_stop_lifecycle(tmp_path, monkeypatch):
    _wire(monkeypatch)
    rec = recorder.DualRecorder(tmp_path, sample_rate=16000, window_sec=1.0)
    assert rec.is_recording is False
    rec.start()
    assert rec.is_recording is True                  # workers alive
    assert {w.name for w in _FakeWorker.instances} == {"mic", "system"}  # both streams

    info = rec.stop()
    assert rec.is_recording is False
    assert info["duration_sec"] == 2.0               # max frames / sample_rate
    assert info["mic_path"].endswith("mic.wav")
    assert info["system_path"].endswith("system.wav")
    assert info["started_at"] == 123.0


def test_dualrecorder_mic_only_omits_system_path(tmp_path, monkeypatch):
    _wire(monkeypatch)
    rec = recorder.DualRecorder(tmp_path, capture_system=False)
    rec.start()
    assert [w.name for w in _FakeWorker.instances] == ["mic"]   # only the mic worker
    info = rec.stop()
    assert info["system_path"] is None and info["mic_path"].endswith("mic.wav")


def test_dualrecorder_nothing_to_capture_raises(tmp_path, monkeypatch):
    _wire(monkeypatch)
    rec = recorder.DualRecorder(tmp_path, capture_mic=False, capture_system=False)
    with pytest.raises(RuntimeError, match="Nothing to capture"):
        rec.start()


def test_dualrecorder_stop_reraises_worker_error(tmp_path, monkeypatch):
    class _ErrWorker(_FakeWorker):
        def start(self):
            super().start()
            self.error = RuntimeError("audio device disconnected")

    _wire(monkeypatch, _ErrWorker)
    rec = recorder.DualRecorder(tmp_path)
    rec.start()
    with pytest.raises(RuntimeError, match="audio device disconnected"):
        rec.stop()                                   # a worker error surfaces, not swallowed
