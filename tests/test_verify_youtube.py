"""Pure-logic tests for the YouTube verification script (VTT parser + WER).

The yt-dlp download + pipeline parts need network/cluster; they're exercised
out-of-band. These tests just lock in the helpers so we trust the numbers.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from scripts.verify_youtube import normalize_words, parse_vtt, wer


def test_parse_vtt_strips_cue_metadata_and_dedupes():
    vtt = """WEBVTT
Kind: captions
Language: en

1
00:00:00.000 --> 00:00:02.000
<c.colorE5E5E5>Hello</c> everyone

00:00:02.000 --> 00:00:04.000
Hello everyone

00:00:04.000 --> 00:00:06.000
[Music]
welcome to the meeting"""
    text = parse_vtt(vtt)
    assert "WEBVTT" not in text and "-->" not in text
    assert "<c" not in text and "[Music]" not in text
    # Auto-caps duplicate lines as cues roll — verify dedup
    assert text.count("Hello everyone") == 1
    assert "welcome to the meeting" in text


def test_normalize_words():
    assert normalize_words("Hello, World! 2024") == ["hello", "world", "2024"]
    assert normalize_words("It's a test.") == ["it's", "a", "test"]
    assert normalize_words("") == []


def test_wer_identical_zero():
    r = wer(["the", "quick", "brown", "fox"], ["the", "quick", "brown", "fox"])
    assert r["wer"] == 0.0 and r["edits"] == 0


def test_wer_one_substitution():
    r = wer(["the", "quick", "brown", "fox"], ["the", "quick", "red", "fox"])
    assert r["wer"] == 0.25 and r["edits"] == 1 and r["ref_words"] == 4


def test_wer_insertion_deletion():
    r = wer(["a", "b", "c"], ["a", "b", "c", "d"])     # one insertion
    assert r["edits"] == 1 and round(r["wer"], 3) == round(1 / 3, 3)
    r = wer(["a", "b", "c", "d"], ["a", "c", "d"])     # one deletion
    assert r["edits"] == 1 and r["wer"] == 0.25


def test_wer_empty_ref_with_hyp():
    r = wer([], ["something", "spoken"])
    assert r["wer"] == 1.0 and r["edits"] == 2


def test_wer_both_empty():
    assert wer([], [])["wer"] == 0.0
