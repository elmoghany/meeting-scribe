from scripts.verify_multispeaker import aggregate, parse_urls_file


def test_parse_urls_file_with_and_without_counts():
    text = (
        "# a debate panel set\n"
        "https://youtu.be/aaa 3\n"
        "https://youtu.be/bbb\n"
        "   \n"
        "https://youtu.be/ccc  four\n"   # non-int count -> None
        "https://youtu.be/ddd 2  # trailing\n"
    )
    clips = parse_urls_file(text)
    assert clips == [
        ("https://youtu.be/aaa", 3),
        ("https://youtu.be/bbb", None),
        ("https://youtu.be/ccc", None),   # "four" not parseable
        ("https://youtu.be/ddd", 2),      # extra tokens after count ignored
    ]


def test_aggregate_speaker_count_accuracy():
    results = [
        {"status": "ok", "expected_speakers": 3, "detected_speakers": 3, "wer": 0.10},
        {"status": "ok", "expected_speakers": 2, "detected_speakers": 3, "wer": 0.20},  # off by 1
        {"status": "ok", "expected_speakers": 4, "detected_speakers": 2, "wer": 0.15},  # off by 2
        {"status": "ok", "expected_speakers": None, "detected_speakers": 5, "wer": 0.05},  # unscored
        {"status": "download_failed", "url": "x"},
    ]
    a = aggregate(results)
    assert a["n_total"] == 5 and a["n_ok"] == 4
    assert a["by_status"]["download_failed"] == 1
    assert a["spk_scored"] == 3            # the three with known expected + detected
    assert a["spk_exact"] == 1 and a["spk_exact_pct"] == round(100 / 3, 1)
    assert a["spk_within1"] == 2 and a["spk_within1_pct"] == round(200 / 3, 1)
    assert a["spk_mean_abs_err"] == round((0 + 1 + 2) / 3, 3)
    assert a["wer_mean"] == round((0.10 + 0.20 + 0.15 + 0.05) / 4, 4)


def test_aggregate_empty_is_safe():
    a = aggregate([])
    assert a["n_total"] == 0 and a["wer_mean"] is None and a["spk_exact_pct"] is None
