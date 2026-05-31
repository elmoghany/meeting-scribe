"""The remote GPU worker — the remote→local JSON handoff contract.

compute_pipeline (heavy: real audio + models) is mocked; we verify the worker's
arg handling and that it serializes the pipeline result to <out_json>.
"""
import json

from app.remote import worker


def test_usage_error_on_too_few_args(capsys):
    assert worker.main([]) == 2
    assert worker.main(["only_audio_dir"]) == 2
    assert "usage" in capsys.readouterr().err.lower()


def test_writes_pipeline_result_json(tmp_path, monkeypatch):
    out = tmp_path / "result.json"
    seen = {}

    class FakeResult:
        segments = [object(), object()]
        action_items = [object()]
        backend = "extractive"

        def to_json(self):
            return {"segments": [{"text": "hi"}], "backend": "extractive"}

    def fake_pipeline(audio_dir, batch_model=None):
        seen["audio_dir"] = audio_dir
        seen["model"] = batch_model
        return FakeResult()

    monkeypatch.setattr(worker, "compute_pipeline", fake_pipeline)
    rc = worker.main([str(tmp_path / "audio"), str(out), "large-v3"])
    assert rc == 0
    # passed args through correctly
    assert seen["audio_dir"].endswith("audio") and seen["model"] == "large-v3"
    # wrote valid JSON in the expected shape the local side imports
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["backend"] == "extractive" and data["segments"][0]["text"] == "hi"


def test_model_arg_optional(tmp_path, monkeypatch):
    out = tmp_path / "r.json"
    monkeypatch.setattr(worker, "compute_pipeline",
                        lambda audio_dir, batch_model=None: type(
                            "R", (), {"segments": [], "action_items": [],
                                      "backend": "x", "to_json": lambda self: {}})())
    assert worker.main([str(tmp_path / "a"), str(out)]) == 0  # no model arg
    assert out.exists()
