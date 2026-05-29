"""diarize_pyannote should retry on CPU when a CUDA op fails (real bug: the
taylor A40 driver is too old for the env's torch, which collapsed diarization
all the way to 'Others'). Uses injected fake torch/pyannote — no real deps."""
import sys
import types

import pytest


def _install_fakes(monkeypatch, cuda_available, cuda_raises):
    calls = {"to": [], "ran_on": []}

    # fake torch
    torch = types.ModuleType("torch")

    class _Dev:
        def __init__(self, name): self.name = name

    torch.device = _Dev
    torch.cuda = types.SimpleNamespace(is_available=lambda: cuda_available)

    # fake pyannote.audio.Pipeline
    seg = types.SimpleNamespace(start=0.0, end=1.0)

    class _Ann:
        def itertracks(self, yield_label=True):
            return [(seg, None, "SPEAKER_00")]

    class _Pipeline:
        @classmethod
        def from_pretrained(cls, model, token=None):
            return cls()

        def to(self, dev):
            calls["to"].append(dev.name)
            return self

        def __call__(self, wav, **kw):
            dev = calls["to"][-1]
            if dev == "cuda" and cuda_raises:
                raise RuntimeError("CUDA driver too old")
            calls["ran_on"].append(dev)
            return _Ann()

    pa = types.ModuleType("pyannote")
    pa_audio = types.ModuleType("pyannote.audio")
    pa_audio.Pipeline = _Pipeline
    pa.audio = pa_audio
    monkeypatch.setitem(sys.modules, "torch", torch)
    monkeypatch.setitem(sys.modules, "pyannote", pa)
    monkeypatch.setitem(sys.modules, "pyannote.audio", pa_audio)
    return calls


def test_pyannote_retries_on_cpu_when_cuda_fails(monkeypatch):
    from app.pipeline import diarize
    calls = _install_fakes(monkeypatch, cuda_available=True, cuda_raises=True)
    turns = diarize.diarize_pyannote("x.wav", hf_token="t")
    assert calls["to"] == ["cuda", "cpu"]      # tried GPU, then fell back
    assert calls["ran_on"] == ["cpu"]           # actually produced on CPU
    assert len(turns) == 1 and turns[0].speaker == "SPEAKER_00"


def test_pyannote_uses_gpu_when_it_works(monkeypatch):
    from app.pipeline import diarize
    calls = _install_fakes(monkeypatch, cuda_available=True, cuda_raises=False)
    diarize.diarize_pyannote("x.wav", hf_token="t")
    assert calls["ran_on"] == ["cuda"]          # no needless CPU fallback


def test_pyannote_cpu_only_when_no_cuda(monkeypatch):
    from app.pipeline import diarize
    calls = _install_fakes(monkeypatch, cuda_available=False, cuda_raises=False)
    diarize.diarize_pyannote("x.wav", hf_token="t")
    assert calls["to"] == ["cpu"] and calls["ran_on"] == ["cpu"]
