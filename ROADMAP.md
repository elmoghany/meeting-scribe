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

## Next
10. ⬜ "All action items" cross-meeting view
11. ⬜ Persistent speaker profiles (name once, matched across meetings via embeddings)
12. ⬜ Slide/screenshot capture during screen-share segments

| CRM/Slack/Notion integrations | ✅ via key-free outbound webhook |
