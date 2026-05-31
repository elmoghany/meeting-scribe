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


def test_degenerate_outputs_excluded_from_agreement():
    """Cases where one model returned ~nothing (qwen_empty) must NOT be scored
    as 'ok' — they previously produced WER=327 and a meaningless mean."""
    res = [
        {"status": "ok", "wer_whisper_vs_qwen": 0.05},
        {"status": "ok", "wer_whisper_vs_qwen": 0.09},
        {"status": "qwen_empty", "qwen_words": 1, "whisper_words": 327},
        {"status": "qwen_empty", "qwen_words": 16, "whisper_words": 280},
    ]
    a = aggregate(res)
    assert a["n_ok"] == 2                        # degenerate ones excluded
    assert a["whisper_vs_qwen"]["n"] == 2
    assert a["whisper_vs_qwen"]["mean"] == 0.07  # not 150+ from the outliers
    assert a["by_status"]["qwen_empty"] == 2


def test_classify_degenerate_by_ratio():
    """The run_one word-ratio rule: <30% of the other model => degenerate."""
    from scripts.verify_qwen import _classify  # noqa
    assert _classify(16, 280) == "qwen_empty"     # ratio .057
    assert _classify(7, 281) == "qwen_empty"      # ratio .025
    assert _classify(280, 16) == "whisper_empty"
    assert _classify(257, 336) == "ok"            # ratio .76 — real comparison
    assert _classify(441, 442) == "ok"
    assert _classify(0, 0) == "both_empty"
