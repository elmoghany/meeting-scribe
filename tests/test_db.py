import time

from app import db
from app.models import ActionItem, Meeting, Segment, Summary


def _mk(meeting_id="m-test"):
    db.reset_connection()
    db.create_meeting(Meeting(id=meeting_id, title="Test", platform="meet",
                              started_at=time.time()))
    return meeting_id


def test_meeting_crud():
    mid = _mk("m-crud")
    got = db.get_meeting(mid)
    assert got and got.title == "Test" and got.platform == "meet"
    db.update_meeting(mid, status="done", duration_sec=120.0)
    assert db.get_meeting(mid).status == "done"
    assert any(m.id == mid for m in db.list_meetings())


def test_segments_and_fts_search():
    mid = _mk("m-fts")
    db.add_segments(mid, [
        Segment(start=0, end=2, text="Let's discuss the quarterly budget.",
                speaker="Me", source="batch"),
        Segment(start=2, end=4, text="The roadmap looks solid.",
                speaker="Others", source="batch"),
    ])
    segs = db.get_segments(mid, source="batch")
    assert len(segs) == 2
    hits = db.search("budget")
    assert any(h["meeting_id"] == mid for h in hits)
    assert "budget" in " ".join(h["snippet"].lower() for h in hits)


def test_replace_segments_swaps_source():
    mid = _mk("m-replace")
    db.add_segment(mid, Segment(start=0, end=1, text="draft", speaker="Me", source="live"))
    db.replace_segments(mid, [
        Segment(start=0, end=1, text="final text", speaker="Me", source="batch")
    ], source="batch")
    assert len(db.get_segments(mid, source="live")) == 1   # live untouched
    assert db.get_segments(mid, source="batch")[0].text == "final text"


def test_summary_and_actions():
    mid = _mk("m-sum")
    db.save_summary(mid, Summary(overview="ov", key_points=["a", "b"], decisions=["d"]))
    s = db.get_summary(mid)
    assert s.overview == "ov" and s.key_points == ["a", "b"]
    db.save_action_items(mid, [ActionItem(text="do x", owner="Me", due="Friday")])
    items = db.get_action_items(mid)
    assert items[0].text == "do x" and not items[0].done
    db.set_action_done(items[0].id, True)
    assert db.get_action_items(mid)[0].done is True


def test_delete_cascades():
    mid = _mk("m-del")
    db.add_segment(mid, Segment(start=0, end=1, text="x", speaker="Me", source="batch"))
    db.delete_meeting(mid)
    assert db.get_meeting(mid) is None
    assert db.get_segments(mid) == []


def test_annotations_comment_and_highlight():
    mid = _mk("m-annot")
    sid = db.add_segment(mid, Segment(start=0, end=1, text="x", speaker="Me", source="batch"))
    cid = db.add_annotation(mid, kind="comment", text="great point", segment_id=sid)
    anns = db.list_annotations(mid)
    assert any(a["id"] == cid and a["text"] == "great point" for a in anns)
    # toggle highlight on/off
    assert db.toggle_highlight(mid, sid) is True
    assert any(a["kind"] == "highlight" and a["segment_id"] == sid
               for a in db.list_annotations(mid))
    assert db.toggle_highlight(mid, sid) is False
    assert not any(a["kind"] == "highlight" for a in db.list_annotations(mid))
    # delete comment
    db.delete_annotation(cid)
    assert not any(a["id"] == cid for a in db.list_annotations(mid))


def test_annotations_cascade_on_meeting_delete():
    mid = _mk("m-annot-del")
    db.add_annotation(mid, kind="comment", text="hi")
    db.delete_meeting(mid)
    assert db.list_annotations(mid) == []
