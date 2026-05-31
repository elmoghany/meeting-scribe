# Using MeetingScribe

A quick guide to recording, reviewing, and sharing meeting notes — all locally.

## 1. Start the dashboard
```bash
cd meeting-scribe
.venv\Scripts\activate          # Windows (or source .venv/bin/activate)
meetingscribe serve             # → http://127.0.0.1:8765
```
First run downloads the live Whisper model. To pre-download everything for
fully-offline use: `python -m scripts.download_models`.

## 2. Record a meeting
1. Join your Google Meet / Zoom call as usual.
2. In the dashboard: enter a **title**, pick the **platform**, choose a
   **language** (or leave auto-detect), and click **● Start** (or press `r`).
   - **Mic (Me)** captures your microphone; **System (Others)** captures the
     call audio via WASAPI loopback. Keep both on for a full transcript.
3. Watch the **live transcript** stream in, with **live notes** (rolling summary
   + action items) updating as you talk.
4. Click **■ Stop** (or `r`) when done. The high-quality batch pass runs
   automatically (on the Cornell GPU if configured, else locally) and produces
   the polished transcript, speaker labels, summary, and action items.

## 3. Review
Open a meeting from the list to:
- **Play the audio** — click any transcript line to jump there; the playing line
  highlights. (`space` plays/pauses.)
- **Jump by chapter** — the **Chapters** list auto-splits the meeting into
  keyword-titled sections; click one to seek there.
- **Rename _or merge_ speakers** — turn "Speaker 1" into real names, or **merge**
  two labels when diarization over-splits one person. Updates everywhere.
- **Fix mistakes inline** — double-click a transcript line to correct a
  mis-transcription; edit the AI summary / key points / decisions in place.
- **Tick off action items** (add your own too), or see them all in the
  left-panel **task tracker** (toggle open-only, export **CSV**).
- **Star** key lines and add **comments**; export a starred line as an audio
  **clip**, or all starred lines as one **highlight reel**.
- **Tag** the meeting and filter your history by tag; filter the list by title.
- **Ask the meeting** — "What did we decide about the budget?" — or **ask across
  all meetings** for cross-meeting answers with citations.
- **Re-summarize with a template** — standup, 1:1, interview, retro, sales
  (auto-suggested from the transcript).
- **Custom vocabulary** — teach it names/jargon so transcription spells them right.
- **Find in transcript** with the search box (`/` focuses global search).
- See **topics**, the **dominant speaker**, and **talk-time** analytics.

## 4. Share / export
- **Copy** — full notes to clipboard for email/Slack.
- **Export** menu — Markdown, plain text, **SRT/VTT** subtitles, a self-contained
  **web page (.html)**, or JSON. Notes, chapters, talk-time, and the transcript
  are all included.
- **Manage** — multi-select meetings in the sidebar to **bulk delete**.
- **Webhook** — set `MEETINGSCRIBE_WEBHOOK_URL` to auto-post notes to
  Slack/Discord/Notion/Zapier when a meeting finishes.

## 5. Auto-record scheduled meetings
- **Zoom:** connect via OAuth (`/oauth/zoom/start`) — see the README.
- **Any calendar:** set `CALENDAR_ICS_URL` to your calendar's secret `.ics` link
  (Google/Outlook/Apple all provide one — no OAuth). MeetingScribe starts
  capture when a meeting begins and stops after its window.

## Check transcription quality on a known video
Measure the pipeline's accuracy against a YouTube video's own captions
(Word Error Rate):
```bash
meetingscribe verify-youtube "https://www.youtube.com/watch?v=..." \
    --seconds 300 --model small.en
```
It downloads the audio + captions (needs `yt-dlp` + `ffmpeg`), runs the full
pipeline, and writes a Markdown report with WER, detected speakers, and a
sample transcript. See [VERIFICATION.md](VERIFICATION.md) for measured results
(WER 3.8%/8.4%/11.5% on 1/2/3-speaker real audio).

## Keyboard shortcuts
| Key | Action |
|---|---|
| `/` | Focus search |
| `space` | Play/pause audio (when a meeting is open) |
| `r` | Start / stop recording |

## Where your data lives
Everything stays under `C:\cornell\meetingnotes` (configurable via
`MEETINGSCRIBE_DATA_DIR`): SQLite DB, audio recordings, and Markdown notes.
Use **Free audio** on a meeting to delete its WAVs while keeping the notes.
