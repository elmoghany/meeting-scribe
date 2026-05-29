"""ASR device selection must use CTranslate2's CUDA detection (not torch), and
model load must fall back to CPU if GPU init fails. Real bug: on the Cornell
A40, torch (cu130) couldn't see the cu12 driver so detect() forced CPU int8 and
large-v3 ran at 15.9% WER instead of 8.1% on the GPU."""
import sys
import types

import pytest


def test_detect_uses_ctranslate2_cuda_count(monkeypatch):
    from app import device
    fake = types.ModuleType("ctranslate2")
    fake.get_cuda_device_count = lambda: 1
    monkeypatch.setitem(sys.modules, "ctranslate2", fake)
    assert device.detect() == ("cuda", "float16")


def test_detect_cpu_when_no_cuda(monkeypatch):
    from app import device
    fake = types.ModuleType("ctranslate2")
    fake.get_cuda_device_count = lambda: 0
    monkeypatch.setitem(sys.modules, "ctranslate2", fake)
    assert device.detect() == ("cpu", "int8")


def test_detect_cpu_when_ctranslate2_missing(monkeypatch):
    from app import device
    monkeypatch.setitem(sys.modules, "ctranslate2", None)  # import raises
    assert device.detect() == ("cpu", "int8")


def test_load_falls_back_to_cpu_on_gpu_failure(monkeypatch):
    """_load(cuda) that raises should retry on CPU."""
    from app.pipeline import asr
    asr._load.cache_clear()
    attempts = []

    class _FakeModel:
        def __init__(self, name, device, compute_type, download_root):
            attempts.append((device, compute_type))
            if device == "cuda":
                raise RuntimeError("CUDA driver too old")

    fake_fw = types.ModuleType("faster_whisper")
    fake_fw.WhisperModel = _FakeModel
    monkeypatch.setitem(sys.modules, "faster_whisper", fake_fw)

    asr._load("large-v3", "cuda", "float16")
    assert attempts == [("cuda", "float16"), ("cpu", "int8")]
    asr._load.cache_clear()


def test_load_does_not_loop_on_cpu_failure(monkeypatch):
    from app.pipeline import asr
    asr._load.cache_clear()

    class _FakeModel:
        def __init__(self, *a, **k):
            raise RuntimeError("disk full")

    fake_fw = types.ModuleType("faster_whisper")
    fake_fw.WhisperModel = _FakeModel
    monkeypatch.setitem(sys.modules, "faster_whisper", fake_fw)
    with pytest.raises(RuntimeError, match="disk full"):
        asr._load("tiny.en", "cpu", "int8")
    asr._load.cache_clear()
