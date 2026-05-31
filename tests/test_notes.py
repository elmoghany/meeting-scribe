from app.models import Segment
from app.pipeline import notes


def _seg(text, speaker="Me", start=0.0):
    return Segment(start=start, end=start + 3, text=text, speaker=speaker, source="batch")


def test_chapters_split_by_time_and_titled_by_keywords():
    # ~20 min meeting: budget talk early, hiring talk late.
    segs = []
    for i in range(20):
        segs.append(Segment(start=i * 60, end=i * 60 + 30,
                            text="budget revenue pricing budget revenue" if i < 10
                            else "hiring candidate interview hiring candidate",
                            speaker="Me", source="batch"))
    chs = notes.chapters(segs, target_sec=300)  # ~4 chapters over 20 min
    assert 2 <= len(chs) <= 8
    # ordered by time, non-overlapping-ish, start at the meeting start
    assert chs[0]["start"] == 0.0
    assert all(chs[i]["start"] <= chs[i + 1]["start"] for i in range(len(chs) - 1))
    titles = " ".join(c["title"].lower() for c in chs)
    assert "budget" in titles or "revenue" in titles      # early topic surfaced
    assert "hiring" in titles or "candidate" in titles     # late topic surfaced


def test_chapters_empty_or_tiny_input():
    assert notes.chapters([]) == []
    assert notes.chapters([Segment(start=0, end=2, text="hi", speaker="Me", source="batch")]) == []


def test_chapters_clamped_to_segment_count():
    segs = [Segment(start=i * 600, end=i * 600 + 5, text=f"topic {i} word word",
                    speaker="Me", source="batch") for i in range(3)]
    chs = notes.chapters(segs, target_sec=60, max_chapters=8)  # would want many, only 3 segs
    assert len(chs) <= 3


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


def test_action_items_precision_recall_corpus():
    """Guards both directions: real action items must survive, conversational
    false positives (questions, hedged musings, fragments) must be rejected."""
    keep = [
        "I'll send the quarterly report by Friday.",
        "Alex, you should review the new design before tomorrow.",
        "We need to ship the release this week.",
        "Let's schedule a follow-up next Monday.",
        "I will email the vendor about pricing.",
        "Please update the deck before the standup.",
        "I'll maybe send the notes on Friday.",          # hedge + strong commit -> kept
    ]
    drop = [
        "What should we do about the budget?",            # question
        "Do you think we need to worry about latency?",   # question
        "Maybe we should look into that someday.",        # hedged, no commitment
        "I think we should explore that idea.",           # hedged musing
        "Let's see.",                                     # trivial fragment
    ]
    for t in keep:
        assert notes.extract_action_items([_seg(t)]), f"lost a real action item: {t!r}"
    for t in drop:
        assert notes.extract_action_items([_seg(t)]) == [], f"false positive kept: {t!r}"


def test_action_items_no_well_ill_youcan_false_positives():
    """Regression for real false positives found on the All-In podcast:
    `we'?ll` matched "well"/"as well"/"well-respected", `i'?ll` matched "ill",
    and the permissive "you can" cue fired on musings."""
    for t in [
        "Gavin is anchoring day two as well.",
        "Obviously, Carpathi is super well-respected.",
        "He was ill last week.",
        "you can potentially live out this idea about chips someday.",
    ]:
        assert notes.extract_action_items([_seg(t)]) == [], f"false positive: {t!r}"
    # Real contractions must still register.
    assert notes.extract_action_items([_seg("I'll send the deck by Monday.")])
    assert notes.extract_action_items([_seg("We'll ship the release this week.")])


def test_extractive_summary_dedupes_near_duplicates():
    # The "ship v2" idea is repeated three times with minor variation.
    text = (
        "We will ship v2 next week. "
        "Ship v2 next week is the plan. "
        "We are shipping v2 next week. "
        "Marketing needs the launch assets. "
        "Engineering owns the auth migration. "
    )
    s = notes.extractive_summary(text, max_points=5)
    # "ship v2 next week" should appear at most once across key_points.
    ship = [k for k in s.key_points if "ship" in k.lower() and "v2" in k.lower()]
    assert len(ship) == 1, f"expected 1 ship-v2 line, got {len(ship)}: {ship}"
    # The other distinct ideas should still surface.
    joined = " ".join(s.key_points).lower()
    assert "marketing" in joined and "auth" in joined


def test_extractive_summary_strips_speaker_labels():
    """Real bug from a multi-speaker YouTube verification: the extractive
    summary included sentences like "Speaker 1: Yeah." — speaker prefixes were
    leaking into the scored sentence pool. Fix: summarize plain transcript text,
    not the speaker-labeled form."""
    segs = [
        _seg("Yeah.", speaker="Speaker 1"),
        _seg("OK so what is a neural network?", speaker="Speaker 2"),
        _seg("Right.", speaker="Speaker 1"),
        _seg("A neural network is a mathematical abstraction of the brain.",
             speaker="Speaker 2"),
        _seg("A neural network has knobs and the knobs need a proper setting.",
             speaker="Speaker 2"),
    ]
    summary, _ = notes.ExtractiveNotes().summarize(segs)
    blob = " ".join([summary.overview] + summary.key_points).lower()
    assert "speaker 1:" not in blob and "speaker 2:" not in blob


def test_extractive_decisions_dedupe():
    text = (
        "We decided to use OAuth. "
        "We decided to use OAuth for the integration. "
        "We agreed to use OAuth. "
        "We decided to ship on Friday. "
    )
    s = notes.extractive_summary(text)
    # Three near-identical OAuth decisions should collapse to one;
    # the Friday decision is distinct and should be kept.
    oauth = [d for d in s.decisions if "oauth" in d.lower()]
    friday = [d for d in s.decisions if "friday" in d.lower()]
    assert len(oauth) == 1 and len(friday) == 1


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


def test_overview_prefers_high_signal_over_intro_position():
    """Overview should lead with the most central sentence, not whatever came
    first (usually greetings). The recurring topic here is the budget/launch,
    not the 'thanks for joining' opener."""
    text = (
        "Thanks everyone for joining the call today. "
        "We need to finalize the launch budget. The launch budget drives the "
        "whole launch plan and the launch budget is the main blocker. "
        "Marketing is waiting on the launch budget decision. "
        "Anyway, nice weather lately."
    )
    s = notes.extractive_summary(text, max_points=4)
    assert "budget" in s.overview.lower()           # high-signal content
    assert "thanks" not in s.overview.lower()        # not the intro greeting


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
