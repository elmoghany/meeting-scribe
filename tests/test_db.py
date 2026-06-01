import time

from app import db
from app.models import ActionItem, Meeting, Segment, Summary


def _mk(meeting_id="m-test"):
    db.reset_connection()
    db.create_meeting(Meeting(id=meeting_id, title="Test", platform="meet",
                              started_at=time.time()))
    return meeting_id


def test_search_tolerates_malformed_fts_queries():
    mid = _mk("m-search-safe")
    db.add_segments(mid, [Segment(start=0, end=2, text="we discussed the budget plan",
                                  speaker="Me", source="batch")])
    # stray quote / FTS operators must NOT raise (they used to 500 the endpoint)
    for q in ['budget"', 'budget AND', 'NEAR(', 'plan OR budget', '"unbalanced']:
        hits = db.search(q)                         # no exception
        assert isinstance(hits, list)
    # the sanitized fallback still finds the real word
    assert any("budget" in (h["snippet"] or "").lower() for h in db.search('budget"'))


def test_reanchor_annotations_after_resegment():
    mid = _mk("m-reanchor")
    db.replace_segments(mid, [
        Segment(start=0, end=5, text="intro", speaker="Me", source="batch"),
        Segment(start=5, end=10, text="the key point", speaker="Sam", source="batch"),
    ], source="batch")
    old = {s.start: s.id for s in db.get_segments(mid, source="batch")}
    hi_id = db.add_annotation(mid, kind="highlight", segment_id=old[5.0])   # star the 5-10s seg
    # capture anchor time (what the reprocess endpoint does before run_batch)
    anchors = [(a["id"], 6.0) for a in db.list_annotations(mid)]            # moment ~6s
    # reprocess re-transcribes -> NEW segments / IDs at similar times
    db.replace_segments(mid, [
        Segment(start=0, end=4, text="intro again", speaker="Me", source="batch"),
        Segment(start=4, end=11, text="the key point restated", speaker="Sam", source="batch"),
    ], source="batch")
    moved = db.reanchor_annotations(mid, anchors)
    assert moved == 1
    new_seg_at_6 = next(s for s in db.get_segments(mid, source="batch")
                        if s.start <= 6.0 <= s.end)
    ann = next(a for a in db.list_annotations(mid) if a["id"] == hi_id)
    assert ann["segment_id"] == new_seg_at_6.id        # highlight re-attached, not orphaned


def test_regenerate_preserves_manual_items_and_done_state():
    mid = _mk("m-regen-actions")
    # initial auto extraction, one already marked done
    db.save_action_items(mid, [
        ActionItem(text="send the report", done=True),
        ActionItem(text="book the room", done=False),
    ])
    # user adds a manual item and it must survive regeneration
    db.add_action_item(mid, ActionItem(text="call the vendor"))
    # regenerate: same auto set re-extracted (book room dropped, new item appears)
    db.save_action_items(mid, [
        ActionItem(text="send the report", done=False),   # re-extracted, done reset upstream
        ActionItem(text="prepare the slides", done=False),
    ])
    items = {a.text: a for a in db.get_action_items(mid)}
    assert "call the vendor" in items                 # manual item preserved
    assert items["send the report"].done is True      # done-state carried over by text match
    assert "prepare the slides" in items              # new auto item added
    assert "book the room" not in items               # stale auto item replaced


def test_meeting_stats_counts_open_actions():
    mid = _mk("m-openactions")
    db.add_segments(mid, [Segment(start=0, end=1, text="hi there", speaker="Me",
                                  source="batch")])
    db.save_action_items(mid, [
        ActionItem(text="send report", done=False),
        ActionItem(text="book room", done=False),
        ActionItem(text="already done", done=True),   # excluded from open count
    ])
    s = db.meeting_stats()[mid]
    assert s["open_actions"] == 2          # two not-done
    assert s["segments"] == 1
    # a meeting with no action items reports 0, not missing
    other = _mk("m-noactions")
    db.add_segments(other, [Segment(start=0, end=1, text="x", speaker="Me", source="batch")])
    assert db.meeting_stats()[other]["open_actions"] == 0


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


def test_search_is_stemmed_and_highlights_inflection():
    mid = _mk("m-stem")
    db.add_segments(mid, [Segment(start=0, end=3, source="batch", speaker="A",
                                  text="We approved several new engineering hires today.")])
    # a query in a different inflection still matches (porter stemming)
    for q in ("hire", "hiring", "approve", "engineer"):
        assert any(h["meeting_id"] == mid for h in db.search(q)), q
    # and the snippet highlights the actual inflected word present in the text
    snip = next(h["snippet"] for h in db.search("hire") if h["meeting_id"] == mid)
    assert "[hires]" in snip


def test_update_segment_edits_text_and_fts():
    mid = _mk("m-edit")
    sid = db.add_segment(mid, Segment(start=0, end=2, text="the kroud was loud",
                                      speaker="Me", source="batch"))
    res = db.update_segment(sid, text="the crowd was loud")
    assert res == {"id": sid, "meeting_id": mid}
    assert db.get_segments(mid, source="batch")[0].text == "the crowd was loud"
    # FTS reflects the correction (old token gone, new token findable)
    hits = db.search("crowd")
    assert any(h["meeting_id"] == mid for h in hits)
    assert not any(h["meeting_id"] == mid for h in db.search("kroud"))


def test_update_segment_speaker_and_missing():
    mid = _mk("m-edit2")
    sid = db.add_segment(mid, Segment(start=0, end=1, text="hi", speaker="Speaker 1",
                                      source="batch"))
    db.update_segment(sid, speaker="Sam")
    assert db.get_segments(mid, source="batch")[0].speaker == "Sam"
    assert db.update_segment(999999, text="x") is None   # missing
    assert db.update_segment(sid) is None                  # nothing to update


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


def test_action_item_add_edit_delete():
    mid = _mk("m-ai-crud")
    iid = db.add_action_item(mid, ActionItem(text="ship it", owner="Me", due="Fri"))
    items = db.get_action_items(mid)
    assert any(i.id == iid and i.text == "ship it" for i in items)
    assert db.update_action_item(iid, text="ship the release", owner="Sam") == mid
    edited = next(i for i in db.get_action_items(mid) if i.id == iid)
    assert edited.text == "ship the release" and edited.owner == "Sam" and edited.due == "Fri"
    assert db.update_action_item(999999, text="x") is None
    assert db.update_action_item(iid) is None
    assert db.delete_action_item(iid) == mid
    assert not any(i.id == iid for i in db.get_action_items(mid))
    assert db.delete_action_item(iid) is None


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


def test_speaker_profiles_upsert_match_delete():
    db.reset_connection()
    db.upsert_profile("Alice Zhang", [1.0, 0.0, 0.0])
    db.upsert_profile("Alice Zhang", [0.9, 0.1, 0.0])  # running mean, n=2
    profs = db.list_profiles()
    alice = next(p for p in profs if p["name"] == "Alice Zhang")
    assert alice["n_samples"] == 2
    from app.pipeline.speakerid import match
    assert match([0.95, 0.05, 0.0], profs, threshold=0.75)[0] == "Alice Zhang"
    db.delete_profile("Alice Zhang")
    assert not any(p["name"] == "Alice Zhang" for p in db.list_profiles())


def test_meeting_embeddings_roundtrip():
    mid = _mk("m-emb")
    db.save_meeting_embeddings(mid, {"Speaker 1": [0.1, 0.2], "Speaker 2": [0.3, 0.4]})
    assert db.get_meeting_embedding(mid, "Speaker 1") == [0.1, 0.2]
    assert db.get_meeting_embedding(mid, "nope") is None


def test_profile_meetings_lists_appearances():
    a = _mk("m-prof-a")
    b = _mk("m-prof-b")
    c = _mk("m-prof-c")
    db.save_meeting_embeddings(a, {"Alice": [0.1, 0.2]})
    db.save_meeting_embeddings(b, {"Alice": [0.3, 0.4], "Bob": [0.5, 0.6]})
    # c has no Alice; should be excluded
    db.save_meeting_embeddings(c, {"Bob": [0.7, 0.8]})
    rows = db.profile_meetings("Alice")
    ids = {r["meeting_id"] for r in rows}
    assert ids == {a, b}
    bob = db.profile_meetings("Bob")
    assert {r["meeting_id"] for r in bob} == {b, c}
    assert db.profile_meetings("Nobody") == []


def test_apply_profiles_recognizes_known_speaker():
    from app.models import Summary
    from app.pipeline.process import PipelineResult, apply_profiles
    db.reset_connection()
    db.upsert_profile("Bob Lee", [1.0, 0.0, 0.0])
    res = PipelineResult(
        segments=[Segment(start=0, end=2, text="hi", speaker="Speaker 1", source="batch"),
                  Segment(start=2, end=4, text="ok", speaker="Speaker 2", source="batch")],
        summary=Summary(), action_items=[], language="en", duration_sec=4.0,
        backend="extractive",
        speaker_embeddings={"Speaker 1": [0.98, 0.02, 0.0], "Speaker 2": [0.0, 0.0, 1.0]})
    matched = apply_profiles(res)
    assert matched == {"Speaker 1": "Bob Lee"}
    assert res.segments[0].speaker == "Bob Lee"      # recognized
    assert res.segments[1].speaker == "Speaker 2"    # unmatched, unchanged
    assert "Bob Lee" in res.speaker_embeddings        # embeddings re-keyed
    db.delete_profile("Bob Lee")


def test_meeting_stats_prefers_batch():
    mid = _mk("m-stats")
    db.add_segment(mid, Segment(start=0, end=1, text="one two three",
                                speaker="Me", source="live"))
    db.replace_segments(mid, [Segment(start=0, end=1, text="alpha beta",
                                      speaker="Me", source="batch")], source="batch")
    st = db.meeting_stats()[mid]
    assert st["segments"] == 1   # batch preferred over the live draft
    assert st["words"] == 2      # "alpha beta"


def test_all_action_items_across_meetings():
    a = _mk("m-ai-a")
    b = _mk("m-ai-b")
    db.save_action_items(a, [ActionItem(text="ship v2", owner="Sam"),
                             ActionItem(text="email vendor", owner="Me")])
    db.save_action_items(b, [ActionItem(text="book room", owner="Me")])
    # mark one done
    items_a = db.get_action_items(a)
    db.set_action_done(items_a[0].id, True)

    all_items = db.all_action_items()
    assert {i["text"] for i in all_items} >= {"ship v2", "email vendor", "book room"}
    # each carries meeting context
    assert all("meeting_title" in i and "meeting_id" in i for i in all_items)
    # open_only excludes the done one
    open_items = db.all_action_items(open_only=True)
    assert "ship v2" not in {i["text"] for i in open_items}
    # owner filter (DB is shared across tests, so check subset + owner correctness)
    me = db.all_action_items(owner="Me")
    assert {i["text"] for i in me} >= {"email vendor", "book room"}
    assert all(i["owner"] == "Me" for i in me)


def test_tags_add_filter_remove():
    a = _mk("m-tag-a")
    b = _mk("m-tag-b")
    db.add_tag(a, "Standup")    # normalized to lowercase
    db.add_tag(a, "team")
    db.add_tag(b, "team")
    db.add_tag(a, "team")       # duplicate ignored
    assert db.get_tags(a) == ["standup", "team"]
    # filter meetings by tag
    ids = {m.id for m in db.list_meetings(tag="team")}
    assert a in ids and b in ids
    ids = {m.id for m in db.list_meetings(tag="standup")}
    assert a in ids and b not in ids
    # tag counts
    counts = {t["tag"]: t["count"] for t in db.all_tags()}
    assert counts.get("team") == 2 and counts.get("standup") == 1
    # remove
    db.remove_tag(a, "team")
    assert db.get_tags(a) == ["standup"]
