from app.models import Segment
from app.pipeline import assemble
from app.pipeline.assemble import Turn


def _seg(start, end, text, speaker="Unknown"):
    return Segment(start=start, end=end, text=text, speaker=speaker, source="batch")


def test_assign_speakers_by_overlap():
    asr = [_seg(0, 2, "hello"), _seg(2.1, 4, "world")]
    turns = [Turn(0, 2, "SPEAKER_00"), Turn(2, 4, "SPEAKER_01")]
    out = assemble.assign_speakers(asr, turns)
    assert out[0].speaker == "SPEAKER_00"
    assert out[1].speaker == "SPEAKER_01"


def test_assign_speakers_no_turns_keeps_label():
    asr = [_seg(0, 2, "hi", speaker="Me")]
    assert assemble.assign_speakers(asr, [])[0].speaker == "Me"


def test_apply_me_prior():
    segs = [_seg(0, 2, "I think so", "SPEAKER_00"), _seg(3, 5, "agreed", "SPEAKER_01")]
    mic = [Turn(0, 2, "Me")]  # user spoke during the first segment
    out = assemble.apply_me_prior(segs, mic, min_overlap=0.5)
    assert out[0].speaker == "Me"
    assert out[1].speaker == "SPEAKER_01"


def test_apply_me_prior_below_threshold_keeps_remote_speaker():
    # A brief mic blip (1s) during a long remote segment (4s) is only 25%
    # overlap — under the 0.5 threshold, so it must NOT be relabeled "Me".
    segs = [_seg(0, 4, "long remote turn", "SPEAKER_00")]
    mic = [Turn(0, 1, "Me")]
    out = assemble.apply_me_prior(segs, mic, min_overlap=0.5)
    assert out[0].speaker == "SPEAKER_00"   # interjection doesn't steal the segment


def test_apply_me_prior_no_mic_turns_is_noop():
    segs = [_seg(0, 2, "x", "SPEAKER_00"), _seg(2, 4, "y", "SPEAKER_01")]
    out = assemble.apply_me_prior(segs, [], min_overlap=0.5)
    assert [s.speaker for s in out] == ["SPEAKER_00", "SPEAKER_01"]


def test_merge_adjacent_same_speaker():
    segs = [_seg(0, 1, "Hello", "Me"), _seg(1.2, 2, "there", "Me"),
            _seg(5, 6, "Hi", "Others")]
    merged = assemble.merge_adjacent(segs, max_gap=1.0)
    assert len(merged) == 2
    assert merged[0].text == "Hello there"
    assert merged[0].speaker == "Me"


def test_merge_caps_length_for_monologue():
    # 12 contiguous same-speaker 5s segments (no gaps) = 60s of monologue.
    segs = [_seg(i * 5, i * 5 + 5, f"part {i}", "Me") for i in range(12)]
    merged = assemble.merge_adjacent(segs, max_gap=1.0, max_len=30.0)
    # without a cap this would be ONE 60s block; capped at 30s it stays splittable
    assert len(merged) >= 2
    assert all((m.end - m.start) <= 30.0 + 5 for m in merged)  # ~cap (+ one trailing seg)
    assert max(m.end for m in merged) == 60.0                  # full span preserved


def test_merge_keeps_gap_split():
    segs = [_seg(0, 1, "a", "Me"), _seg(10, 11, "b", "Me")]  # 9s gap
    assert len(assemble.merge_adjacent(segs, max_gap=1.0)) == 2


def test_renumber_speakers():
    segs = [_seg(0, 1, "a", "SPEAKER_02"), _seg(1, 2, "b", "SPEAKER_05"),
            _seg(2, 3, "c", "SPEAKER_02"), _seg(3, 4, "d", "Me")]
    out = assemble.renumber_speakers(segs)
    assert out[0].speaker == "Speaker 1"   # SPEAKER_02 first seen
    assert out[1].speaker == "Speaker 2"   # SPEAKER_05
    assert out[2].speaker == "Speaker 1"   # SPEAKER_02 again -> same
    assert out[3].speaker == "Me"          # kept untouched


def test_rename_and_transcript():
    segs = [_seg(0, 1, "hi", "SPEAKER_00")]
    assemble.rename_speakers(segs, {"SPEAKER_00": "Sam"})
    assert segs[0].speaker == "Sam"
    text = assemble.to_transcript(segs)
    assert "Sam: hi" in text and "[00:00]" in text
