# MeetingScribe — roadmap & Otter parity

Tracking the autonomous feature-parity work against Otter.ai. Everything stays
**local, key-free, MIT**.

## Parity with Otter — status

| Otter feature | MeetingScribe |
|---|---|
| Real-time transcription | ✅ live draft (faster-whisper, CPU) |
| AI summary / key points / decisions | ✅ local LLM + extractive fallback |
| Action items | ✅ owner + due date, checkable |
| Speaker identification | ✅ **pyannote community-1** (default, verified) → key-free resemblyzer fallback; rename in UI |
| Ask-your-meeting chat | ✅ over transcript |
| Search across meetings | ✅ SQLite FTS5 |
| Auto-join / calendar | ✅ Zoom auto-**record** + key-free `.ics` (any calendar) |
| Collaboration (highlights, comments) | ✅ star lines + comments |
| Screenshot/slide capture | ⬜ not started |
| Talk-time analytics | ✅ per-speaker share, words, wpm |
| Export | ✅ MD / TXT / SRT / VTT / JSON |
| Audio playback w/ synced transcript | ✅ click-to-seek + highlight |
| Folders / tags | ✅ tags + filter |
| CRM/Slack/Notion integrations | ⬜ needs keys — out of scope (maybe webhooks) |
| Mobile apps | ⬜ out of scope |

## Done (autonomous session)
1. ✅ Multi-format export (SRT/VTT/TXT/JSON) + talk-time analytics
2. ✅ Synced audio playback (click-to-seek, active-line highlight)
3. ✅ Highlights + comments (segment-anchored)
4. ✅ Rename speakers in the dashboard
5. ✅ Tags / folders + filtering
6. ✅ `.ics` calendar import → auto-record any calendar (key-free, recurrence-aware)
7. ✅ Live summary + live action items during the call (rolling extractive notes)
8. ✅ Outbound webhook on finish (key-free Slack/Discord/Notion/Zapier bridge)

9. ✅ Edit meeting title (dbl-click) + storage management (free audio, keep notes)

10. ✅ "All action items" cross-meeting view (open/all toggle, owner filter, jump to meeting)
11. ✅ Migrate FastAPI on_event → lifespan (deprecation warnings cleared)
12. ✅ Regenerate notes on demand (re-summarize transcript w/o re-transcribing)
13. ✅ Auto topic keywords per meeting (key-free)
14. ✅ pyannote 4.x (community-1) enabled + verified — high-quality diarization default
15. ✅ Language selection for transcription (auto / en / es / fr / de / …) — Otter multi-language
16. ✅ Keyboard shortcuts (/ search · space play/pause · r record)
17. ✅ CSV export of all action items (task tracker)
18. ✅ Search-within-meeting (highlight + jump + match count)
19. ✅ Meeting word-count + duration in the list
20. ✅ "Copy" notes-to-clipboard button
21. ✅ Polish: dark/light theme toggle, USAGE.md guide, fixed undefined --accent2
22. ✅ Error-path tests (404/400 coverage) + cluster re-verification of full pyannote pipeline

23. ✅ Normalize pyannote SPEAKER_00 labels → "Speaker 1/2/3" (consistency w/ resemblyzer)
24. ✅ docs/ARCHITECTURE.md (module map, design decisions, extension points)
25. ✅ Persistent speaker profiles — name a voice once, auto-recognized across
    meetings (Resemblyzer embeddings on the cluster + cosine match + enroll-on-rename;
    verified: 256-dim embeddings per speaker, matching unit-tested)
26. ✅ Headless join-bot scaffold (Zoom Meeting SDK, Linux, full production):
    JWT signing + Python runner + C++ glue (main.cpp) + CMake + Dockerfile +
    setup README. Build/run once you've downloaded the gated SDK.

27. ✅ AutoRecorder bot dispatch (opt-in via MEETINGSCRIBE_BOT_ENABLED)
28. ✅ OAuth-callback bridge on the github.io landing page (no Zoom-config change needed)
29. ✅ GitHub Actions CI (pytest on push, py 3.10/3.11/3.12 matrix, badge in README)

## Next
30. ⬜ Verify a real bot join end-to-end once the SDK is downloaded + built
31. ✅ Per-meeting rule-based sentiment chip (key-free lexicon, negation-aware)
32. ✅ Per-segment confidence display — Segment.confidence from faster-whisper
    avg_logprob; low-confidence (<50%) lines render dimmed/italic with a tooltip
33. ✅ Cross-meeting "Speakers" view — click a voice profile to expand the list
    of meetings they appeared in (jumps straight to the meeting)
34. ✅ Richer extractive summary — near-duplicate suppression (stemmed-token Jaccard)
    for key_points + decisions so repeated discussion doesn't crowd the summary
35. ✅ Markdown export now embeds Topics + Sentiment + Talk-time table
36. ✅ Fix audiomix cache invalidation (re-mix when source WAVs are newer)
37. ✅ CONTRIBUTING.md (dev setup, tests, CI, conventions)
14. ⬜ Slide/screenshot capture during screen-share segments
15. ⬜ Sentiment / topic tags per meeting (lightweight, key-free)

| CRM/Slack/Notion integrations | ✅ via key-free outbound webhook |
