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


def test_chapters_title_fallback_uses_salient_words_not_raw_text():
    # A section with no repeated phrase (every content word unique) must still get
    # a clean keyword-ish title, not a raw mid-sentence dump. Words are GENUINELY
    # distinct per segment (digits would be stripped by _tokenize, so don't use
    # them) — no word repeats, so keywords() finds nothing and the salient-token
    # fallback runs.
    words = ("apple banana cherry date elder fig grape honey ivy jade kiwi lemon "
             "mango nutmeg olive peach quince radish sage thyme ube violet walnut "
             "yam zest amber brass coral").split()
    segs = [Segment(start=i * 30, end=i * 30 + 25,
                    text=" ".join(words[i * 3:i * 3 + 3]), speaker="Me", source="batch")
            for i in range(8)]   # 8 * 30s = 240s, every word unique
    chs = notes.chapters(segs, target_sec=120)
    assert len(chs) >= 2
    for c in chs:
        assert c["title"] and c["title"] != "…"
        assert c["title"][0].isalnum()      # salient words, not punctuation/raw dump
        # the title words come from the chapter's own (unique) vocabulary
        assert any(w.capitalize() in c["title"] or w in c["title"].lower() for w in words)


def test_parse_llm_json_valid_and_fallbacks():
    segs = [Segment(start=0, end=3, text="We shipped v2. I'll send the report Friday.",
                    speaker="Me", source="batch")]
    # valid LLM JSON (with surrounding prose) parses into Summary + ActionItems
    raw = ('Sure!\n{"overview": "We shipped v2.", "key_points": ["v2 shipped"], '
           '"decisions": ["ship v2"], "action_items": '
           '[{"text": "send report", "owner": "Sam", "due": "Friday"}]}\nDone.')
    s, items = notes._parse_llm_json(raw, segs)
    assert s.overview == "We shipped v2." and s.key_points == ["v2 shipped"]
    assert items[0].text == "send report" and items[0].owner == "Sam" and items[0].due == "Friday"
    # malformed JSON -> robust extractive fallback (still produces notes)
    s2, _ = notes._parse_llm_json("not json at all", segs)
    assert s2 is not None and s2.overview
    # empty result (no overview/items) -> ValueError -> extractive fallback
    s3, _ = notes._parse_llm_json('{"overview": "", "action_items": []}', segs)
    assert s3 is not None and s3.overview      # fell back, not the empty parse


def test_soft_wrap_keeps_trailing_remainder():
    # 50 words, cap 40 -> [40, 10]; the trailing chunk must not be dropped
    s = " ".join(f"w{i}" for i in range(50))
    pieces = notes._soft_wrap(s, max_words=40)
    assert len(pieces) == 2
    assert " ".join(pieces).split() == s.split()      # every word preserved
    assert len(pieces[-1].split()) == 10              # trailing remainder kept


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


def test_unpunctuated_runon_is_wrapped_not_one_giant_overview():
    # A poorly-punctuated transcript (no '.!?') must not yield a 100-word overview.
    runon = " ".join(["word"] * 120)   # 120 words, zero punctuation
    s = notes.extractive_summary(runon)
    # overview is now a bounded chunk, not the whole run
    assert s.overview and len(s.overview.split()) <= 45
    # a normal short sentence is untouched
    assert notes._soft_wrap("We shipped the v2 release today.") == \
        ["We shipped the v2 release today."]


def test_action_items_reject_speech_acts_but_keep_real_tasks():
    # "I'll say/admit/argue …" is discourse, not a task (found in a content audit).
    for talk in ["I will say that the budget looks fine to me.",
                 "I'll admit the rollout was rushed and messy.",
                 "I'll be honest, the demo did not go well today."]:
        assert notes.extract_action_items([_seg(talk)]) == [], f"speech act kept: {talk!r}"
    # genuine commitments — including 'tell <someone>' — still register
    assert notes.extract_action_items([_seg("I'll send the report by Friday.")])
    assert notes.extract_action_items([_seg("I'll tell Sam to review the design.")])
    # transitional "let's go to ..." is navigation, not a task — but "go ahead" is
    assert notes.extract_action_items([_seg("Let's go to the next clip now.")]) == []
    assert notes.extract_action_items([_seg("Let's go back to the budget topic.")]) == []
    assert notes.extract_action_items([_seg("Let's go ahead and schedule the review.")])
    # discussion-starter fillers (no deliverable) are not tasks...
    for filler in ["Let's get started with the meeting.", "Let's dive right on in now.",
                   "Okay everyone, let's get into it.", "Let's begin the session."]:
        assert notes.extract_action_items([_seg(filler)]) == [], f"filler kept: {filler!r}"
    # ...but an object after the verb keeps it a real task
    assert notes.extract_action_items([_seg("Let's dive into the Q3 revenue numbers.")])


def test_decisions_dont_match_bare_final():
    # Real false positives found by a content audit: bare "final" must NOT count
    # as a decision (it was matched by an over-broad cue).
    s = notes.extractive_summary("This is our final episode. The final pieces fit.")
    assert s.decisions == []
    # but the verb forms and "decision" still do
    s2 = notes.extractive_summary("We finalized the budget. The decision is to ship.")
    assert any("finaliz" in d.lower() for d in s2.decisions)
    assert any("decision" in d.lower() or "ship" in d.lower() for d in s2.decisions)


def test_sentiment_positive_negative_neutral():
    pos = [_seg("Great work everyone, shipped the release. Excellent and thanks!")]
    neg = [_seg("This is a terrible blocker, we are stuck and frustrated.")]
    neu = [_seg("We discussed the agenda items and reviewed the document.")]
    assert notes.sentiment(pos)["label"] == "positive"
    assert notes.sentiment(pos)["score"] > 0
    assert notes.sentiment(neg)["label"] == "negative"
    assert notes.sentiment(neg)["score"] < 0
    assert notes.sentiment(neu)["label"] == "neutral"


def test_sentiment_product_logistics_terms_are_neutral():
    # "ship"/"launch" are neutral logistics terms, not positive sentiment — a
    # plain delivery statement must not read as positive, and "launch failed"
    # must read negative (previously 'launch' cancelled out 'failed').
    plan = notes.sentiment([_seg("We will ship the feature Friday and launch next month.")])
    assert plan["label"] == "neutral" and plan["positive"] == 0
    bad = notes.sentiment([_seg("The launch failed and the customers are angry.")])
    assert bad["label"] == "negative"               # 'angry' + 'failed', no false positive


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


def test_extractive_summary_degenerate_input_never_crashes():
    # A meeting transcribed to silence/music, or whitespace, must yield a valid
    # (empty) Summary — not crash the notes step.
    empty = notes.extractive_summary("")
    assert empty.overview == "" and empty.key_points == [] and empty.decisions == []
    assert notes.extractive_summary("   \n\t  ").key_points == []
    # all-stopword content (no scorable tokens) falls back to the raw sentences
    s = notes.extractive_summary("the the the. and of the.")
    assert s is not None and isinstance(s.key_points, list)   # no exception


def test_extractive_backend_summarize_and_chat():
    backend = notes.ExtractiveNotes()
    segs = [_seg("Let's finalize the budget at 50k.", "Sam"),
            _seg("I'll email the vendor about pricing.", "Alex")]
    summary, items = backend.summarize(segs)
    assert summary.key_points
    assert len(items) >= 1
    ans = backend.chat("What about the budget?", segs)
    assert "budget" in ans.lower()


def test_named_assignment_action_items_with_owner():
    # "<Name> will <task-verb>" is the canonical assignment phrasing; detect it
    # and attribute the owner to the named person, not the speaker.
    segs = [_seg("Sarah will handle the database migration next week.", "Bob"),
            _seg("Carol will coordinate with the vendor.", "Bob"),
            _seg("Action item: John to update the roadmap doc.", "Alice")]
    items = notes.extract_action_items(segs)
    by_owner = {a.owner: a.text for a in items}
    assert "Sarah" in by_owner and "handle" in by_owner["Sarah"].lower()
    assert "Carol" in by_owner
    assert "John" in by_owner                       # "John to update" -> owner John, not Alice


def test_chapters_merge_adjacent_same_title():
    # one topic spilling across time buckets must not produce two consecutive
    # chapters with the identical title.
    segs = []
    for i in range(6):
        segs.append(Segment(start=i * 60, end=i * 60 + 50, source="batch", speaker="A",
                            text="budget revenue pricing cloud spend forecast numbers"))
    for i in range(6, 12):
        segs.append(Segment(start=i * 60, end=i * 60 + 50, source="batch", speaker="B",
                            text="hiring candidate interview recruiting headcount onboarding"))
    ch = notes.chapters(segs)
    titles = [c["title"] for c in ch]
    assert not any(titles[i] == titles[i + 1] for i in range(len(titles) - 1))  # no dupes
    assert len(ch) >= 2                                  # still distinct topics
    # merged chapter spans contiguous time (end of one == start region of next)
    assert all(ch[i]["end"] <= ch[i + 1]["start"] for i in range(len(ch) - 1))


def test_decision_recall_across_phrasings():
    def decs(text):
        return notes.extractive_summary(text).decisions
    assert decs("The team chose React over Vue.")
    assert decs("We are going with the vendor proposal.")
    assert decs("It was decided that we ship on Friday.")
    assert decs("Let us go with the blue theme.")
    assert decs("We settled on a weekly cadence.")


def test_decision_rejects_unmade_decisions():
    # "make a decision" / "decision is pending" are NOT decisions (precision)
    def decs(text):
        return notes.extractive_summary(text).decisions
    assert decs("We need to make a decision eventually.") == []
    assert decs("The decision is still pending review.") == []
    assert decs("I decided to grab lunch earlier today.") == []   # personal, not "we"


def test_due_date_extraction_phrasings():
    # the deadline phrasings that show up in real meetings
    def due(text):
        items = notes.extract_action_items([_seg(text)])
        return items[0].due if items else None
    assert due("I will send the deck by next Tuesday.") == "by next Tuesday"
    assert due("I will ship the fix before the 15th.") == "before the 15th"
    assert due("I will follow up in two weeks.") == "in two weeks"
    assert due("I will finish the draft by end of day.") == "by end of day"
    assert due("I will review it this Friday.") == "this Friday"


def test_due_date_does_not_match_non_temporal_phrases():
    # "next steps", "on it", "this quarter" are not deadlines
    for phrase in ["next steps", "on it", "in the project", "this quarter"]:
        assert notes._DUE.search(f"We will work on the {phrase} together.") is None


def test_prediction_will_sentences_are_not_action_items():
    # precision guard: "<thing> will <predict>" must NOT be flagged (the task-verb
    # whitelist excludes prediction verbs, and pronoun subjects aren't owners).
    segs = [_seg("It will rain tomorrow according to the forecast."),
            _seg("This will help us a lot in the long run."),
            _seg("They will probably disagree with that approach."),
            _seg("The API will return a 404 in that case."),
            _seg("Revenue will grow a lot next year we hope.")]
    assert notes.extract_action_items(segs) == []   # no false positives


def test_chat_no_match_returns_not_found():
    # a question with no token overlap with the transcript -> explicit "not found"
    backend = notes.ExtractiveNotes()
    segs = [_seg("Let's finalize the budget at 50k.", "Sam")]
    ans = backend.chat("xylophone zebra quokka", segs)
    assert "couldn't find anything" in ans.lower()


def test_too_similar_empty_token_side_returns_false():
    assert notes._too_similar("", "hello world") is False        # empty side -> not similar
    assert notes._too_similar("ship the report today",
                              "ship the report today") is True    # identical -> similar


def test_llm_prompt_wraps_transcript():
    p = notes._llm_prompt("ALPHA BETA")
    assert "ALPHA BETA" in p and "JSON" in p                      # transcript embedded + JSON ask


def test_decisions_capped_at_five():
    # seven distinct decisions; the summary keeps at most five
    transcript = " ".join([
        "We decided to migrate the database to Postgres next quarter.",
        "We agreed to launch the beta in March with limited users.",
        "We chose React for the new dashboard frontend rewrite.",
        "The annual marketing contract was approved by legal today.",
        "We're going with vendor Acme for all cloud hosting needs.",
        "Let's go with the blue color scheme for the rebrand.",
        "We will go with weekly sprints instead of a biweekly cadence.",
    ])
    s = notes.extractive_summary(transcript)
    assert len(s.decisions) == 5                                  # capped, not all seven


def test_get_notes_backend_falls_back_to_extractive_without_gguf(monkeypatch):
    # llamacpp configured but no model path -> must NOT crash; degrade to extractive
    from app import config
    monkeypatch.setenv("MEETINGSCRIBE_LLM_BACKEND", "llamacpp")
    monkeypatch.delenv("MEETINGSCRIBE_GGUF_PATH", raising=False)
    config.get_settings.cache_clear()
    try:
        backend = notes.get_notes_backend()
        assert backend.backend == "extractive"                   # graceful fallback
    finally:
        config.get_settings.cache_clear()


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
