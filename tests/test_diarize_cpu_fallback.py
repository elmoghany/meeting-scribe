"""diarize_pyannote should retry on CPU when a CUDA op fails (real bug: the
taylor A40 driver is too old for the env's torch, which collapsed diarization
all the way to 'Others'). Uses injected fake torch/pyannote — no real deps."""
import sys
import types



def _install_fakes(monkeypatch, cuda_available, cuda_raises,
                   reject_token_kw=False, wrap_output=False):
    calls = {"to": [], "ran_on": [], "auth_kw": []}

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
        def from_pretrained(cls, model, **kw):
            # pyannote 3.x rejects the newer `token=` kwarg with a TypeError; the
            # code must retry with `use_auth_token=`. Record which kwarg won.
            if "token" in kw and reject_token_kw:
                raise TypeError("unexpected keyword argument 'token'")
            calls["auth_kw"].append("token" if "token" in kw else "use_auth_token")
            return cls()

        def to(self, dev):
            calls["to"].append(dev.name)
            return self

        def __call__(self, wav, **kw):
            dev = calls["to"][-1]
            if dev == "cuda" and cuda_raises:
                raise RuntimeError("CUDA driver too old")
            calls["ran_on"].append(dev)
            ann = _Ann()
            # pyannote 4.x returns a DiarizeOutput wrapper; the Annotation hangs
            # off `.speaker_diarization`.
            return types.SimpleNamespace(speaker_diarization=ann) if wrap_output else ann

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


def test_pyannote_falls_back_to_use_auth_token_on_old_signature(monkeypatch):
    """pyannote 3.x rejects token=; the loader must retry with use_auth_token=."""
    from app.pipeline import diarize
    calls = _install_fakes(monkeypatch, cuda_available=False, cuda_raises=False,
                           reject_token_kw=True)
    turns = diarize.diarize_pyannote("x.wav", hf_token="t")
    assert calls["auth_kw"] == ["use_auth_token"]    # retried with the legacy kwarg
    assert len(turns) == 1                            # still produced turns


def test_pyannote_unwraps_v4_diarize_output(monkeypatch):
    """pyannote 4.x wraps the Annotation in .speaker_diarization — must unwrap it."""
    from app.pipeline import diarize
    _install_fakes(monkeypatch, cuda_available=False, cuda_raises=False,
                   wrap_output=True)
    turns = diarize.diarize_pyannote("x.wav", hf_token="t")
    assert len(turns) == 1 and turns[0].speaker == "SPEAKER_00"   # read through the wrapper


def test_pyannote_requires_token(monkeypatch):
    """No HF token -> a clear RuntimeError, not an opaque pyannote crash."""
    import pytest

    from app import config
    from app.pipeline import diarize
    _install_fakes(monkeypatch, cuda_available=False, cuda_raises=False)
    monkeypatch.setenv("HUGGINGFACE_TOKEN", "")
    config.get_settings.cache_clear()
    try:
        with pytest.raises(RuntimeError, match="HUGGINGFACE_TOKEN"):
            diarize.diarize_pyannote("x.wav")        # no hf_token arg, none in env
    finally:
        config.get_settings.cache_clear()
