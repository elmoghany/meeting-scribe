"""Batch verification aggregation stats (pure, no network/model)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from scripts.verify_batch import aggregate


def test_aggregate_basic():
    res = [
        {"status": "ok", "wer": 0.05, "lang": "en"},
        {"status": "ok", "wer": 0.10, "lang": "en"},
        {"status": "ok", "wer": 0.15, "lang": "es"},
        {"status": "no_captions"},
        {"status": "download_failed"},
    ]
    a = aggregate(res)
    assert a["n_total"] == 5 and a["n_ok"] == 3
    assert a["wer_mean"] == round((0.05 + 0.10 + 0.15) / 3, 4)
    assert a["wer_median"] == 0.10
    assert a["wer_min"] == 0.05 and a["wer_max"] == 0.15
    assert a["by_status"] == {"ok": 3, "no_captions": 1, "download_failed": 1}
    assert a["by_lang"]["en"]["n"] == 2
    assert a["by_lang"]["en"]["wer_mean"] == 0.075


def test_aggregate_empty():
    a = aggregate([])
    assert a["n_total"] == 0 and a["n_ok"] == 0
    assert a["wer_mean"] is None and a["by_lang"] == {}


def test_aggregate_all_failed():
    a = aggregate([{"status": "error"}, {"status": "no_captions"}])
    assert a["n_ok"] == 0 and a["wer_mean"] is None
    assert a["by_status"] == {"error": 1, "no_captions": 1}
