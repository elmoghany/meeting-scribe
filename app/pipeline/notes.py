"""Summaries, action items, and 'ask-your-meeting' chat — all key-free.

Backends (selected by MEETINGSCRIBE_LLM_BACKEND), all local / no API keys:
  * ``llamacpp``     — a quantized GGUF model via llama-cpp-python (installed on
                       the user's PC ahead of time). Default.
  * ``transformers`` — full-precision HF model (used on the Cornell GPU node).
  * ``extractive``   — pure-Python TextRank-style fallback; always available,
                       no model download, instant. Also the safety net when a
                       chosen LLM backend can't load.

The extractive helpers are dependency-free and unit-tested.
"""
from __future__ import annotations

import json
import re
from collections import Counter

from ..config import get_settings
from ..models import ActionItem, Segment, Summary

# --------------------------------------------------------------------------- #
# Pure-python extractive helpers (no deps, testable)
# --------------------------------------------------------------------------- #
_STOP = set(
    "the a an and or but if then else for to of in on at by with as is are was were "
    "be been being this that these those i you he she it we they me him her us them my "
    "your his its our their so do does did doing have has had not no yes will would can "
    "could should may might must just about into over than too very can't won't im ive "
    "okay ok yeah yep nope uh um like really "
    # interrogatives / fillers — never useful content or search terms
    "what when where who whom whose why how which "
    "get got going gonna want need know think".split()
)
_SENT_SPLIT = re.compile(r"(?<=[.!?])\s+")

# Apostrophe class: straight ' and curly ' (ASR/captions emit either).
_AP = r"['’]"
# Cues that a sentence states a task / commitment.
# NOTE: contractions REQUIRE the apostrophe — `i'?ll` (optional) wrongly matched
# "ill" and `we'?ll` matched "well" (real false positives found on the All-In
# podcast: "as well", "well-respected"). "you can" was dropped — it's permissive
# ("you can potentially…"), not an assignment; real assignments use
# should/need to/have to/must.
_ACTION_CUES = re.compile(
    rf"\b(action item|to-?do|follow[- ]?up|i{_AP}ll|i will|we{_AP}ll|we will|"
    rf"let{_AP}s|let us|"
    r"you (should|need to|have to|must)|we (should|need to|have to|must)|"
    rf"i (need to|have to|should|must)|please|make sure|don{_AP}t forget|assign|"
    r"will (send|share|email|prepare|review|update|create|set up|schedule|draft|"
    r"check|fix|look into|circle back|"
    # task verbs that signal an assignment regardless of subject ("Sarah will
    # handle …"); deliberately excludes prediction verbs (be/help/grow/rain) to
    # avoid flagging "this will help" as an action item.
    r"handle|own|lead|drive|manage|coordinate|organize|write|build|ship|deliver|"
    r"finish|complete|contact|investigate|compile|escalate|book|put together|"
    r"reach out|follow up))\b",
    re.IGNORECASE,
)
_DECISION_CUES = re.compile(
    r"\b("
    # subject + decision verb ("the team chose …", "we decided …")
    r"(?:we|they|the team|the group|everyone) (?:decided|agreed|chose|opted)|"
    r"(?:it was|was|were) decided|"                      # passive "it was decided"
    # the noun "decision" only counts in a made-decision context, NOT
    # "we need to make a decision" / "the decision is pending"
    r"made (?:a|the|our|this) decision|decision (?:is|was) to|"
    r"agreed to|"
    r"(?:we(?:'?ll| will)|let'?s|let us) go with|"       # "we'll/let's go with"
    r"(?:we'?re|we are|we'?ll be) going with|"           # "we are going with"
    r"settled on|opted for|"
    r"finaliz(?:e|ed)|approved)\b",
    re.IGNORECASE,
)
# Hedged / speculative framing — a musing, not a commitment.
_HEDGE = re.compile(
    r"\b(maybe|perhaps|probably|i think|i guess|i feel like|i suspect|i mean|"
    r"i wonder|hopefully|someday|some day|at some point|kind of|sort of)\b",
    re.IGNORECASE,
)
# Strong first-person/again commitment — overrides a hedge.
_STRONG_COMMIT = re.compile(rf"\b(i{_AP}ll|i will|we{_AP}ll|we will)\b", re.IGNORECASE)
# "I'll say / we'll admit / I will argue / I'll tell you" — a speech act or opinion
# marker, NOT a task. ("I'll tell Sam ..." is left intact — only "tell you".)
_SPEECH_ACT = re.compile(
    rf"\b(i{_AP}ll|i will|we{_AP}ll|we will)\s+"
    r"(say|admit|argue|bet|guess|assume|suppose|add|note|mention|be honest|tell you)\b",
    re.IGNORECASE)
# "let's go to / over to / back to / on to the next clip" — a transition, not a
# task (true in meetings too: "let's go to the next agenda item"). NOT "let's go
# ahead" (which is actional), so the trailing "to" is required.
_TRANSITION = re.compile(rf"\blet{_AP}s go (back |over |on )?to\b", re.IGNORECASE)
# Conversational starters with no deliverable — "let's get started / dive in / get
# into it / begin" open a discussion, they aren't assignable tasks. Intransitive,
# so "let's dive into the Q3 numbers" (an object) is deliberately NOT matched.
_FILLER = re.compile(
    rf"\blet{_AP}s (get into it|dive\s+(right\s+|on\s+)*in|get started|begin)\b",
    re.IGNORECASE)
_DUE = re.compile(
    r"\b(by|before|on|due(?:\s+on)?|this|next|in)\s+"
    r"(today|tomorrow|tonight|"
    # weekday, optionally qualified ("next Tuesday", "this Friday")
    r"(?:next\s+|this\s+)?(?:mon|tues|wednes|thurs|fri|satur|sun)day|"
    r"(?:the\s+)?\d{1,2}(?:st|nd|rd|th)|"          # the 15th / 3rd
    r"next week|end of (the )?(day|week|month)|eod|eow|"
    # relative durations: "in two weeks", "in 3 days", "in a month"
    r"(?:a|an|one|two|three|four|five|six|seven|eight|nine|ten|few|couple of|\d+)"
    r"\s+(?:day|week|month)s?|"
    r"(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?\s*\d{1,2}|"
    r"\d{1,2}/\d{1,2}(/\d{2,4})?)\b",
    re.IGNORECASE,
)


def _tokenize(text: str) -> list[str]:
    return [w for w in re.findall(r"[a-z']+", text.lower()) if w not in _STOP and len(w) > 2]


def _soft_wrap(sent: str, max_words: int = 40) -> list[str]:
    """Break an over-long 'sentence' into readable pieces. Whisper output for some
    languages / continuous speech can lack '.!?' entirely, leaving a single
    200-word run that would become an unreadable overview / decision. Split on
    commas once past half the cap, else hard-chunk at the cap. Normal punctuated
    sentences (<= max_words) pass through unchanged."""
    words = sent.split()
    if len(words) <= max_words:
        return [sent]
    pieces: list[str] = []
    cur: list[str] = []
    for w in words:
        cur.append(w)
        if len(cur) >= max_words or (w.endswith(",") and len(cur) >= max_words // 2):
            pieces.append(" ".join(cur))
            cur = []
    if cur:
        pieces.append(" ".join(cur))
    return pieces


def _sentences(text: str) -> list[str]:
    out: list[str] = []
    for s in _SENT_SPLIT.split(text):
        s = s.strip()
        if len(s) > 3:
            out.extend(_soft_wrap(s))
    return out


def _stem(w: str) -> str:
    """Tiny suffix-stripper so 'ship' and 'shipping' compare equal."""
    for suf in ("ies", "ied", "ing", "ed", "es", "s"):
        if w.endswith(suf) and len(w) > len(suf) + 2:
            return w[: -len(suf)]
    return w


def _too_similar(a: str, b: str, threshold: float = 0.5) -> bool:
    """Stemmed-token Jaccard above threshold => effectively duplicates."""
    ta = {_stem(w) for w in _tokenize(a)}
    tb = {_stem(w) for w in _tokenize(b)}
    if not ta or not tb:
        return False
    inter = len(ta & tb)
    union = len(ta | tb)
    return union > 0 and (inter / union) >= threshold


def extractive_summary(transcript_text: str, max_points: int = 7) -> Summary:
    """Frequency-weighted sentence ranking (a lightweight TextRank) with
    near-duplicate suppression so repeated discussion doesn't dominate the
    key points."""
    sents = _sentences(transcript_text)
    if not sents:
        return Summary()
    freq = Counter()
    for s in sents:
        freq.update(_tokenize(s))
    if not freq:
        return Summary(overview=" ".join(sents[:2]))
    top = freq.most_common(1)[0][1]
    scored = []
    for i, s in enumerate(sents):
        toks = _tokenize(s)
        if not toks:
            continue
        score = sum(freq[t] for t in toks) / (len(toks) ** 0.6) / top
        scored.append((score, i, s))
    scored.sort(reverse=True)

    # Pick top-scoring sentences, skipping near-duplicates of already-chosen ones.
    picked: list[tuple[float, int, str]] = []  # (score, orig_index, sentence)
    for score, idx, s in scored:
        if any(_too_similar(s, t) for _, _, t in picked):
            continue
        picked.append((score, idx, s))
        if len(picked) >= max_points:
            break
    # key points render in document order for readability
    by_doc = sorted(picked, key=lambda x: x[1])
    key_points = [_clean(s) for _, _, s in by_doc]

    # Dedupe decisions the same way.
    decisions: list[str] = []
    for s in sents:
        if not _DECISION_CUES.search(s):
            continue
        cleaned = _clean(s)
        if any(_too_similar(cleaned, d) for d in decisions):
            continue
        decisions.append(cleaned)
        if len(decisions) >= 5:
            break

    # Overview = the highest-SCORED sentence(s), not the first in document order
    # (which is usually an intro/greeting). Lead with the single most central
    # sentence; add the next-best only if the lead is short, and cap length.
    by_score = [s for _, _, s in picked]  # picked is already score-desc
    overview = _clean(by_score[0]) if by_score else ""
    if by_score and len(overview.split()) < 8 and len(by_score) > 1:
        overview = overview + " " + _clean(by_score[1])
    return Summary(overview=overview, key_points=key_points, decisions=decisions)


_POS_WORDS = frozenset((
    # NOTE: deliberately excludes neutral product-logistics terms (ship/launch),
    # which appear constantly in neutral or negative contexts ("the launch
    # failed") and otherwise bias every product meeting positive.
    "good great excellent agree agreed love happy success won win winning "
    "perfect awesome fantastic thanks thank helpful productive "
    "ready done complete completed approved yes "
    "solved solving fixed fix nice clear clearly clean smooth smoothly "
    "appreciate appreciated appreciation effective efficient on-track unblocked"
).split())
_NEG_WORDS = frozenset((
    "bad terrible awful disaster crisis problem problems issue issues blocker "
    "blocked delayed delay fail failed failure broken broke break concerned "
    "concern worry worried sorry wrong error errors missed missing stuck angry "
    "frustrating frustrated confusing confused unclear difficult hard struggle "
    "struggling painful worst regret regression bug bugs slip slipped slipping "
    "off-track unhappy disappointed"
).split())
_NEG_GATES = frozenset({"not", "no", "never", "without", "cannot", "cant",
                        "couldnt", "wont", "didnt", "doesnt", "dont"})


def sentiment(segments: list[Segment]) -> dict:
    """Lightweight rule-based meeting sentiment (key-free, deterministic).

    Score is in [-1, 1]. Label is positive/neutral/negative based on a small
    deadzone around zero. Looks one token back for negation (so "not good"
    doesn't count as positive). Honest about precision — this is a vibes
    indicator, not a calibrated classifier.
    """
    text = " ".join(s.text for s in segments).lower()
    toks = re.findall(r"[a-z']+", text)
    pos = neg = 0
    for i, w in enumerate(toks):
        flip = i > 0 and toks[i - 1].replace("'", "") in _NEG_GATES
        if w in _POS_WORDS:
            if flip:
                neg += 1
            else:
                pos += 1
        elif w in _NEG_WORDS:
            if flip:
                pos += 1
            else:
                neg += 1
    total = pos + neg
    score = 0.0 if total == 0 else round((pos - neg) / total, 3)
    if score > 0.15:
        label = "positive"
    elif score < -0.15:
        label = "negative"
    else:
        label = "neutral"
    return {"score": score, "label": label, "positive": pos, "negative": neg}


def keywords(segments: list[Segment], top_n: int = 8) -> list[str]:
    """Extract salient topic phrases (key-free): frequent bigrams (weighted) +
    top remaining unigrams, minus stopwords. Otter-style 'topics'."""
    toks = _tokenize(" ".join(s.text for s in segments))
    if not toks:
        return []
    uni = Counter(toks)
    bi = Counter(f"{a} {b}" for a, b in zip(toks, toks[1:]) if a != b)
    bigram_phrases = [p for p, c in bi.items() if c >= 2]
    covered = {w for p in bigram_phrases for w in p.split()}
    scored = [(bi[p] * 2, p) for p in bigram_phrases]
    scored += [(c, w) for w, c in uni.items() if w not in covered and c >= 2]
    scored.sort(key=lambda x: (-x[0], x[1]))
    return [p for _, p in scored[:top_n]]


def chapters(segments: list[Segment], target_sec: float = 300.0,
             max_chapters: int = 8, min_chapters: int = 2) -> list[dict]:
    """Split the meeting into jump-to-topic chapters (key-free, deterministic).

    Sections are time-proportional (≈ one per `target_sec`, clamped to
    [min, max]), snapped to segment boundaries, each titled by its salient
    keywords. Approximate — meant for navigation, not exact topic boundaries.
    Returns ``[{"start", "end", "title"}]`` ordered by time; ``[]`` if there's
    too little to chapter.
    """
    segs = sorted(segments, key=lambda s: s.start)
    if len(segs) < 2:
        return []
    t0, t1 = segs[0].start, segs[-1].end
    dur = t1 - t0
    if dur < 120:          # too short to be worth chaptering (navigation aid only)
        return []
    n = round(dur / target_sec) if target_sec > 0 else min_chapters
    n = max(min_chapters, min(max_chapters, n, len(segs)))
    bucket = dur / n
    groups: list[list[Segment]] = [[] for _ in range(n)]
    for s in segs:
        idx = min(n - 1, int((s.start - t0) / bucket))
        groups[idx].append(s)
    out: list[dict] = []
    for g in groups:
        if not g:
            continue
        kw = keywords(g, top_n=2)
        if not kw:
            # short section with no repeated phrase: use its most salient words
            # (count≥1) rather than raw, possibly mid-sentence, segment text.
            toks = _tokenize(" ".join(s.text for s in g))
            kw = [w for w, _ in Counter(toks).most_common(2)]
        title = ", ".join(kw) if kw else (g[0].text.strip()[:40] or "…")
        title = title[:1].upper() + title[1:]
        out.append({"start": round(g[0].start, 2), "end": round(g[-1].end, 2),
                    "title": title})
    # Time-bucketing can split a single topic across adjacent buckets, yielding
    # consecutive chapters with the identical keyword title. Merge those into one
    # so the chapter list reads as distinct topics, not redundant repeats.
    merged: list[dict] = []
    for c in out:
        if merged and merged[-1]["title"] == c["title"]:
            merged[-1]["end"] = c["end"]      # extend the existing topic
        else:
            merged.append(c)
    return merged


def extract_action_items(segments: list[Segment]) -> list[ActionItem]:
    """Heuristic action-item detection with owner (speaker) and due-date capture."""
    items: list[ActionItem] = []
    seen: set[str] = set()
    for seg in segments:
        for sent in _sentences(seg.text):
            if not _ACTION_CUES.search(sent):
                continue
            if not _is_actionable(sent):
                continue
            norm = sent.lower().strip()
            if norm in seen:
                continue
            seen.add(norm)
            due_m = _DUE.search(sent)
            owner = _infer_owner(sent, seg.speaker)
            items.append(ActionItem(text=_clean(sent), owner=owner,
                                    due=due_m.group(0) if due_m else None))
    return items


def _is_actionable(sent: str) -> bool:
    """Precision filter applied AFTER a cue matches. Rejects the common
    conversational false positives — questions, trivial fragments, and hedged
    musings without a real commitment — while keeping genuine action items.

    Deliberately conservative: a hedge only disqualifies when there's no strong
    first-person commitment ("I'll", "we will") and no due date, so
    "I'll maybe send it Friday" still counts.
    """
    s = sent.strip()
    if s.endswith("?"):
        return False                       # questions aren't action items
    if _SPEECH_ACT.search(s):
        return False                       # "I'll say/admit/argue …" is talk, not a task
    if _TRANSITION.search(s):
        return False                       # "let's go to the next clip" is a transition
    if _FILLER.search(s):
        return False                       # "let's get started / dive in" opens talk, not a task
    if len(s.split()) < 4:
        return False                       # "Let's see." / "I'll check." fragments
    if _HEDGE.search(s) and not (_STRONG_COMMIT.search(s) or _DUE.search(s)):
        return False                       # "maybe we should look into it someday"
    return True


# Subjects that look like a name (capitalized) but aren't a person to assign to.
_PRONOUN_SUBJ = {"i", "it", "this", "that", "these", "those", "there", "they",
                 "we", "he", "she", "you", "the", "a", "an", "then", "so", "now",
                 "here", "what", "who", "which", "if", "and", "but", "everyone",
                 "someone", "anyone", "nobody", "let", "please"}
# "Sarah will handle …", "John to update …" — a named subject driving the task.
_NAMED_ASSIGN = re.compile(r"\b([A-Z][a-z]+)\s+(?:will|to)\s+[a-z]")


def _infer_owner(sentence: str, speaker: str) -> str | None:
    low = sentence.lower()
    if re.search(rf"\bi{_AP}ll\b|\bi will\b|\bi (need|have) to\b", low):
        return speaker if speaker not in ("Unknown",) else "Me"
    m = _NAMED_ASSIGN.search(sentence)
    if m and m.group(1).lower() not in _PRONOUN_SUBJ:
        return m.group(1)                  # "Sarah will handle …" -> Sarah
    if re.search(r"\byou (should|need to|have to|must|will)\b", low):
        return "(assigned)"
    return speaker if speaker not in ("Unknown",) else None


def _clean(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip().rstrip(".") + ("." if not s.endswith(("?", "!")) else "")


# --------------------------------------------------------------------------- #
# Backends
# --------------------------------------------------------------------------- #
_NOTES_SYS = (
    "You are an expert meeting-notes assistant. Read the transcript and respond "
    "with STRICT JSON only, no prose, matching this schema:\n"
    '{"overview": str, "key_points": [str], "decisions": [str], '
    '"action_items": [{"text": str, "owner": str|null, "due": str|null}]}'
)

# Per-meeting-type guidance appended to the LLM system prompt (Otter/Fireflies
# "templates"). Affects the LLM backends; a no-op for the extractive fallback.
SUMMARY_TEMPLATES: dict[str, str] = {
    "general": "",
    "standup": (
        " This is a daily standup. For each person, capture what they did, what "
        "they will do next, and any blockers. Put blockers and next-steps in "
        "action_items with the owner set to the speaker."),
    "one_on_one": (
        " This is a 1:1. Emphasize feedback given, growth/career topics, and "
        "concerns raised; action_items should capture follow-ups and commitments."),
    "interview": (
        " This is a candidate interview. In key_points summarize the candidate's "
        "strengths and concerns; in decisions capture any hire/no-hire lean and "
        "next steps."),
    "retro": (
        " This is a retrospective. Group key_points into what went well, what "
        "didn't, and improvements; action_items are the agreed improvements."),
    "sales": (
        " This is a sales/customer call. Capture the customer's needs, objections, "
        "and budget/timeline signals; action_items are the seller's follow-ups."),
}


def system_prompt_for(template: str | None) -> str:
    """LLM system prompt with optional meeting-type guidance appended."""
    return _NOTES_SYS + SUMMARY_TEMPLATES.get((template or "general"), "")


# Keyword signals per meeting type (lowercase substring match over the transcript).
_TYPE_SIGNALS: dict[str, tuple[str, ...]] = {
    "standup": ("stand-up", "standup", "daily sync", "blockers", "blocker",
                "what did you do yesterday", "working on today", "any blockers"),
    "interview": ("candidate", "your experience", "tell me about yourself",
                  "tell us about", "résumé", "resume", "why do you want to work",
                  "walk me through your", "previous role"),
    "retro": ("retrospective", "retro", "what went well", "what didn't go well",
              "didn't go well", "action items for next sprint", "start stop continue"),
    "one_on_one": ("one-on-one", "1:1", "1 on 1", "career growth", "your growth",
                   "feedback for you", "how are you feeling about", "development plan"),
    "sales": ("pricing", "the demo", "your budget", "contract", "procurement",
              "free trial", "decision maker", "next steps on the deal", "quote"),
}


def detect_meeting_type(segments: list[Segment], min_hits: int = 2) -> str | None:
    """Suggest a summary template from transcript keyword signals. Returns the
    best-matching type if it clears `min_hits`, else None (no confident guess).
    Pure + deterministic."""
    text = " ".join(s.text for s in segments).lower()
    best, best_hits = None, 0
    for typ, signals in _TYPE_SIGNALS.items():
        hits = sum(1 for sig in signals if sig in text)
        if hits > best_hits:
            best, best_hits = typ, hits
    return best if best_hits >= min_hits else None


def _llm_prompt(transcript: str) -> str:
    return f"Transcript:\n{transcript}\n\nReturn the JSON now."


def _parse_llm_json(raw: str, segments: list[Segment]) -> tuple[Summary, list[ActionItem]]:
    try:
        start, end = raw.find("{"), raw.rfind("}")
        data = json.loads(raw[start: end + 1])
        summary = Summary(
            overview=str(data.get("overview", "")).strip(),
            key_points=[str(x).strip() for x in data.get("key_points", []) if str(x).strip()],
            decisions=[str(x).strip() for x in data.get("decisions", []) if str(x).strip()],
        )
        items = [
            ActionItem(text=str(a.get("text", "")).strip(),
                       owner=(a.get("owner") or None),
                       due=(a.get("due") or None))
            for a in data.get("action_items", []) if str(a.get("text", "")).strip()
        ]
        if not summary.overview and not items:
            raise ValueError("empty")
        return summary, items
    except Exception:
        # Robust fallback: never fail to produce notes.
        text = to_text(segments)
        return extractive_summary(text), extract_action_items(segments)


def to_text(segments: list[Segment]) -> str:
    return "\n".join(f"{s.speaker}: {s.text.strip()}" for s in segments)


def fts_query_from_question(question: str) -> str:
    """Turn a natural-language question into a safe FTS5 MATCH query for
    cross-meeting retrieval: salient terms (stopwords/short words dropped),
    each quoted, OR-joined for recall. Returns "" if nothing salient.

    Quoting each term neutralizes FTS operator characters; OR (not the implicit
    AND) is used so a question rarely-all-present in one segment still matches.
    """
    terms = []
    seen = set()
    for t in _tokenize(question):          # already drops stopwords + len<=2
        if t not in seen:
            seen.add(t)
            terms.append('"' + t.replace('"', "") + '"')
    return " OR ".join(terms)


class ExtractiveNotes:
    backend = "extractive"

    def summarize(self, segments: list[Segment], template: str | None = None
                  ) -> tuple[Summary, list[ActionItem]]:
        # template is a no-op for the extractive backend (frequency-based, can't
        # restructure semantically); it shapes the LLM backends below.
        # Summarize over plain transcript text — passing the speaker-labeled
        # form ("Speaker 1: …") would let labels leak into the chosen sentences.
        plain = " ".join(s.text.strip() for s in segments if s.text.strip())
        return extractive_summary(plain), extract_action_items(segments)

    def chat(self, question: str, segments: list[Segment], history=None) -> str:
        # Keyword retrieval over segments; return the best-matching lines.
        q = set(_tokenize(question))
        scored = sorted(
            ((len(q & set(_tokenize(s.text))), s) for s in segments),
            key=lambda x: x[0], reverse=True,
        )
        hits = [f"[{int(s.start)//60:02d}:{int(s.start)%60:02d}] {s.speaker}: {s.text}"
                for n, s in scored[:5] if n > 0]
        if not hits:
            return "I couldn't find anything about that in this meeting."
        return "Here's what was said that's most relevant:\n\n" + "\n".join(hits)


class LlamaCppNotes:
    backend = "llamacpp"

    def __init__(self, model_path: str, n_ctx: int = 8192):
        from llama_cpp import Llama  # noqa: PLC0415

        self._llm = Llama(model_path=model_path, n_ctx=n_ctx, verbose=False,
                          n_threads=None, n_gpu_layers=0)

    def _complete(self, system: str, user: str, max_tokens: int = 1024,
                  temperature: float = 0.2) -> str:
        out = self._llm.create_chat_completion(
            messages=[{"role": "system", "content": system},
                      {"role": "user", "content": user}],
            max_tokens=max_tokens, temperature=temperature,
        )
        return out["choices"][0]["message"]["content"]

    def summarize(self, segments: list[Segment], template: str | None = None
                  ) -> tuple[Summary, list[ActionItem]]:
        raw = self._complete(system_prompt_for(template), _llm_prompt(to_text(segments)))
        return _parse_llm_json(raw, segments)

    def chat(self, question: str, segments: list[Segment], history=None) -> str:
        context = to_text(segments)[:24000]
        sys = ("Answer the user's question using ONLY the meeting transcript below. "
               "If the answer isn't in it, say so. Be concise and cite speakers.\n\n"
               f"Transcript:\n{context}")
        return self._complete(sys, question, max_tokens=512, temperature=0.3).strip()


class TransformersNotes:
    """HF transformers backend — intended for the Cornell GPU node."""

    backend = "transformers"

    def __init__(self, model_id: str = "Qwen/Qwen2.5-7B-Instruct"):
        import torch  # noqa: PLC0415
        from transformers import AutoModelForCausalLM, AutoTokenizer  # noqa: PLC0415

        self._tok = AutoTokenizer.from_pretrained(model_id)
        self._model = AutoModelForCausalLM.from_pretrained(
            model_id, torch_dtype="auto", device_map="auto",
        )
        self._torch = torch

    def _complete(self, system: str, user: str, max_new_tokens: int = 1024) -> str:
        msgs = [{"role": "system", "content": system}, {"role": "user", "content": user}]
        inputs = self._tok.apply_chat_template(msgs, add_generation_prompt=True,
                                               return_tensors="pt").to(self._model.device)
        with self._torch.no_grad():
            out = self._model.generate(inputs, max_new_tokens=max_new_tokens,
                                       do_sample=False)
        return self._tok.decode(out[0][inputs.shape[1]:], skip_special_tokens=True)

    def summarize(self, segments: list[Segment], template: str | None = None
                  ) -> tuple[Summary, list[ActionItem]]:
        raw = self._complete(system_prompt_for(template), _llm_prompt(to_text(segments)))
        return _parse_llm_json(raw, segments)

    def chat(self, question: str, segments: list[Segment], history=None) -> str:
        context = to_text(segments)[:48000]
        sys = ("Answer using ONLY the transcript below; say so if it's not covered.\n\n"
               f"Transcript:\n{context}")
        return self._complete(sys, question, max_new_tokens=512).strip()


def get_notes_backend():
    """Factory honoring settings, with graceful fallback to extractive."""
    s = get_settings()
    backend = s.llm_backend
    try:
        if backend == "llamacpp":
            if not s.gguf_path:
                raise RuntimeError("MEETINGSCRIBE_GGUF_PATH not set")
            return LlamaCppNotes(s.gguf_path)
        if backend == "transformers":
            return TransformersNotes()
    except Exception as e:  # pragma: no cover - depends on optional deps
        import sys
        print(f"[notes] {backend} backend unavailable ({e}); using extractive.",
              file=sys.stderr)
    return ExtractiveNotes()
