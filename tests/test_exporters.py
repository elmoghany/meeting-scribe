from app.models import ActionItem, Meeting, Segment, Summary
from app.pipeline import exporters


def _segs():
    return [
        Segment(start=0.0, end=2.5, text="Hello everyone.", speaker="Me", source="batch"),
        Segment(start=3.0, end=7.25, text="Let's begin.", speaker="Speaker 1", source="batch"),
    ]


def test_srt_format():
    srt = exporters.to_srt(_segs())
    assert "1\n00:00:00,000 --> 00:00:02,500\nMe: Hello everyone." in srt
    assert "00:00:03,000 --> 00:00:07,250" in srt


def test_vtt_format():
    vtt = exporters.to_vtt(_segs())
    assert vtt.startswith("WEBVTT")
    assert "00:00:00.000 --> 00:00:02.500" in vtt
    assert "<v Me>Hello everyone." in vtt


def test_txt_format():
    txt = exporters.to_txt(_segs())
    assert "[00:00] Me: Hello everyone." in txt
    assert "[00:03] Speaker 1: Let's begin." in txt


def test_html_self_contained():
    m = Meeting(id="x", title="Q3 <Planning>", platform="zoom", started_at=1.0,
                duration_sec=120.0)
    html = exporters.to_html(m, Summary(overview="we shipped v2", key_points=["a & b"]),
                             [ActionItem(text="email <vendor>", owner="Sam", due="Mon")],
                             _segs())
    assert html.startswith("<!doctype html>") and html.rstrip().endswith("</html>")
    assert "<style>" in html and "http://" not in html and "https://" not in html  # self-contained
    assert "Q3 &lt;Planning&gt;" in html        # title escaped
    assert "email &lt;vendor&gt;" in html        # action item escaped
    assert "we shipped v2" in html and "Me" in html and "Hello everyone." in html


def test_json_roundtrip():
    import json
    m = Meeting(id="x", title="T", platform="zoom", started_at=1.0)
    out = exporters.to_json(m, Summary(overview="o"), [ActionItem(text="do x")], _segs())
    d = json.loads(out)
    assert d["meeting"]["id"] == "x"
    assert d["summary"]["overview"] == "o"
    assert len(d["transcript"]) == 2
    assert d["action_items"][0]["text"] == "do x"


def test_ts_rounding_carry():
    # 2.9996s rounds ms to 1000 -> carries into seconds
    assert exporters._ts(2.9996) == "00:00:03,000"


def test_action_items_csv():
    rows = [
        {"meeting_title": "Standup", "text": "ship v2", "owner": "Sam", "due": "Fri", "done": 0},
        {"meeting_title": "Sync", "text": "book room", "owner": None, "due": None, "done": 1},
    ]
    csv_text = exporters.action_items_csv(rows)
    lines = csv_text.strip().splitlines()
    assert lines[0] == "meeting,text,owner,due,done"
    assert "Standup,ship v2,Sam,Fri,no" in csv_text
    assert "Sync,book room,,,yes" in csv_text


def test_talk_time_analytics():
    segs = [
        Segment(start=0, end=10, text="one two three four five", speaker="Me", source="batch"),
        Segment(start=10, end=20, text="six seven", speaker="Sam", source="batch"),
        Segment(start=20, end=30, text="eight", speaker="Me", source="batch"),
    ]
    a = exporters.talk_time(segs)
    assert a["num_speakers"] == 2
    assert a["total_words"] == 8
    me = next(s for s in a["speakers"] if s["speaker"] == "Me")
    assert me["seconds"] == 20.0 and me["words"] == 6
    assert me["time_pct"] == round(100 * 20 / 30, 1)
    # speakers sorted by talk time desc → Me first
    assert a["speakers"][0]["speaker"] == "Me"
