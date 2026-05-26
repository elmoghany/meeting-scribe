# MeetingScribe — roadmap & Otter parity

Tracking the autonomous feature-parity work against Otter.ai. Everything stays
**local, key-free, MIT**.

## Parity with Otter — status

| Otter feature | MeetingScribe |
|---|---|
| Real-time transcription | ✅ live draft (faster-whisper, CPU) |
| AI summary / key points / decisions | ✅ local LLM + extractive fallback |
| Action items | ✅ owner + due date, checkable |
| Speaker identification | ✅ pyannote / key-free resemblyzer; **rename in UI** |
| Ask-your-meeting chat | ✅ over transcript |
| Search across meetings | ✅ SQLite FTS5 |
| Auto-join / calendar | ✅ Zoom auto-**record** (`.ics` import next) |
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

## Next
6. ⬜ `.ics` calendar import → auto-record any calendar (key-free, no Google/MS OAuth)
7. ⬜ Live summary + live action items during the call
8. ⬜ Persistent speaker profiles (name once, matched across meetings via embeddings)
9. ⬜ Per-meeting outbound webhook (key-free Slack/Notion/Zapier bridge)
10. ⬜ Slide/screenshot capture during screen-share segments
