"""Qwen cross-check aggregation (pure; the model side runs only in the qwen env)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from scripts.verify_qwen import aggregate


def test_aggregate_three_way():
    res = [
        {"status": "ok", "wer_whisper_vs_qwen": 0.08,
         "wer_whisper_vs_caps": 0.10, "wer_qwen_vs_caps": 0.06},
        {"status": "ok", "wer_whisper_vs_qwen": 0.12},   # no captions for this one
        {"status": "qwen_failed"},
        {"status": "download_failed"},
    ]
    a = aggregate(res)
    assert a["n_total"] == 4 and a["n_ok"] == 2
    assert a["whisper_vs_qwen"]["n"] == 2
    assert a["whisper_vs_qwen"]["mean"] == 0.10
    # caps comparisons only counted where present
    assert a["whisper_vs_caps"]["n"] == 1 and a["whisper_vs_caps"]["mean"] == 0.10
    assert a["qwen_vs_caps"]["n"] == 1 and a["qwen_vs_caps"]["mean"] == 0.06
    assert a["by_status"]["qwen_failed"] == 1


def test_aggregate_empty():
    a = aggregate([])
    assert a["n_ok"] == 0 and a["whisper_vs_qwen"]["mean"] is None
