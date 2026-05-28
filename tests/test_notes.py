from app.models import Segment
from app.pipeline import notes


def _seg(text, speaker="Me", start=0.0):
    return Segment(start=start, end=start + 3, text=text, speaker=speaker, source="batch")


def test_extract_action_items_owner_and_due():
    segs = [
        _seg("I'll send the report by Friday.", speaker="Sam"),
        _seg("You should review the design before tomorrow.", speaker="Alex"),
        _seg("The weather was nice today.", speaker="Sam"),  # not an action
    ]
    items = notes.extract_action_items(segs)
    texts = [i.text.lower() for i in items]
    assert any("send the report" in t for t in texts)
    assert any("review the design" in t for t in texts)
    assert not any("weather" in t for t in texts)
    sam_item = next(i for i in items if "send the report" in i.text.lower())
    assert sam_item.owner == "Sam"
    assert sam_item.due and "friday" in sam_item.due.lower()


def test_sentiment_positive_negative_neutral():
    pos = [_seg("Great work everyone, shipped the release. Excellent and thanks!")]
    neg = [_seg("This is a terrible blocker, we are stuck and frustrated.")]
    neu = [_seg("We discussed the agenda items and reviewed the document.")]
    assert notes.sentiment(pos)["label"] == "positive"
    assert notes.sentiment(pos)["score"] > 0
    assert notes.sentiment(neg)["label"] == "negative"
    assert notes.sentiment(neg)["score"] < 0
    assert notes.sentiment(neu)["label"] == "neutral"


def test_sentiment_negation_flips():
    # "not good" should NOT count as positive
    s = notes.sentiment([_seg("That is not good and not great.")])
    assert s["positive"] == 0 and s["negative"] >= 2


def test_sentiment_empty():
    s = notes.sentiment([])
    assert s == {"score": 0.0, "label": "neutral", "positive": 0, "negative": 0}


def test_keywords_topics():
    segs = [
        _seg("The launch plan is critical. We reviewed the launch plan today."),
        _seg("Marketing wants more budget. The budget covers the launch plan."),
        _seg("Budget and budget approvals were discussed at length."),
    ]
    kw = notes.keywords(segs, top_n=6)
    assert "budget" in kw                       # frequent unigram
    assert "launch plan" in kw                  # repeated bigram phrase
    assert any(" " in k for k in kw)            # at least one bigram phrase
    assert all(isinstance(k, str) for k in kw)
    assert len(kw) <= 6


def test_keywords_empty():
    assert notes.keywords([]) == []


def test_extractive_summary_nonempty():
    text = ("We need to ship the v2 release. The v2 release depends on the new "
            "auth service. We decided to use OAuth for auth. Marketing wants the "
            "launch next week. The auth service is the main blocker for v2.")
    s = notes.extractive_summary(text, max_points=3)
    assert s.overview
    assert 1 <= len(s.key_points) <= 3
    assert any("oauth" in d.lower() or "decided" in d.lower() for d in s.decisions)


def test_extractive_backend_summarize_and_chat():
    backend = notes.ExtractiveNotes()
    segs = [_seg("Let's finalize the budget at 50k.", "Sam"),
            _seg("I'll email the vendor about pricing.", "Alex")]
    summary, items = backend.summarize(segs)
    assert summary.key_points
    assert len(items) >= 1
    ans = backend.chat("What about the budget?", segs)
    assert "budget" in ans.lower()


def test_parse_llm_json_falls_back_on_garbage():
    segs = [_seg("I'll do the thing by Monday.", "Sam")]
    summary, items = notes._parse_llm_json("not json at all", segs)
    # fallback path still yields extractive results
    assert isinstance(summary, notes.Summary)
    assert any("thing" in i.text.lower() for i in items)


def test_parse_llm_json_valid():
    raw = ('prefix {"overview":"o","key_points":["k"],"decisions":["d"],'
           '"action_items":[{"text":"do x","owner":"Sam","due":"Mon"}]} suffix')
    summary, items = notes._parse_llm_json(raw, [])
    assert summary.overview == "o" and summary.key_points == ["k"]
    assert items[0].text == "do x" and items[0].owner == "Sam"
