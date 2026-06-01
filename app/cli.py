"""MeetingScribe command line.

  meetingscribe serve                 # launch the local web dashboard
  meetingscribe devices               # list audio inputs / loopback
  meetingscribe record -t "Standup"   # record from the terminal (Enter to stop)
  meetingscribe process <id|dir>      # (re)run the batch pipeline on a recording
  meetingscribe search "budget"       # full-text search across all meetings
  meetingscribe fetch-model           # download whisper + a quantized GGUF LLM
  meetingscribe doctor                # check environment / Cornell reachability
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .config import get_settings


def _cmd_serve(args):
    import uvicorn
    print(f"MeetingScribe dashboard → http://{args.host}:{args.port}")
    uvicorn.run("app.server.main:app", host=args.host, port=args.port, reload=args.reload)


def _cmd_devices(_args):
    from .capture import list_devices
    import json
    print(json.dumps(list_devices(), indent=2))


def _cmd_record(args):
    from .batch import run_batch
    from .session import MeetingSession

    def emit(ev):
        if ev.get("type") == "segment":
            who = ev["speaker"]
            print(f"  {int(ev['start'])//60:02d}:{int(ev['start'])%60:02d} {who}: {ev['text']}")

    sess = MeetingSession(title=args.title, platform=args.platform, emit=emit,
                          capture_mic=not args.no_mic, capture_system=not args.no_system)
    sess.start()
    print(f"● Recording '{args.title}' ({sess.meeting.id}). Press Enter to stop.\n")
    try:
        input()
    except KeyboardInterrupt:
        pass
    info = sess.stop()
    print("\n■ Stopped. Running batch pipeline…")
    stats = run_batch(info["meeting_id"], info["audio_dir"])
    print(f"Done: {stats}")
    print(f"Notes: {get_settings().notes_dir / (info['meeting_id'] + '.md')}")


def _cmd_process(args):
    from .batch import run_batch
    target = args.target
    s = get_settings()
    if Path(target).is_dir():
        meeting_id = Path(target).name
        audio_dir = target
    else:
        meeting_id = target
        audio_dir = str(s.recordings_dir / meeting_id)
    print(run_batch(meeting_id, audio_dir))


def _cmd_search(args):
    from . import db
    for h in db.search(args.query):
        mm, ss = divmod(int(h["start"]), 60)
        print(f"[{h['title']}] {h['speaker']} {mm:02d}:{ss:02d}: {h['snippet']}")


def _cmd_fetch_model(args):
    from scripts.download_models import download
    download(whisper=args.whisper, gguf_repo=args.llm)


def _cmd_verify_youtube(args):
    """Verify the pipeline on a real YouTube video (WER vs its captions)."""
    from scripts.verify_youtube import main as verify_main
    argv = [args.url, "--seconds", str(args.seconds), "--model", args.model]
    if args.out:
        argv += ["--out", args.out]
    return verify_main(argv)


def _cmd_doctor(_args):
    s = get_settings()
    print(f"data_dir        : {s.data_dir}  (exists={s.data_dir.exists()})")
    print(f"db_path         : {s.db_path}")
    try:
        from . import db as _db  # noqa: PLC0415
        n = len(_db.list_meetings(limit=100000))
        print(f"db              : ok — opens + migrates cleanly, {n} meeting(s)")
    except Exception as e:  # noqa: BLE001
        print(f"db              : ERROR — {type(e).__name__}: {e}")
    print(f"live_model      : {s.live_model} ({s.live_compute})")
    print(f"batch_model     : {s.batch_model}")
    print(f"llm_backend     : {s.llm_backend}  gguf={s.gguf_path or '-'}")
    print(f"hf_token        : {'set' if s.hf_token else 'NOT set'}")
    print(f"remote_enabled  : {s.remote_enabled}  ({s.cornell_user}@{s.cornell_host})")
    # resemblyzer + scikit-learn back the DEFAULT (key-free) diarizer; without
    # them diarization silently degrades to "Others". pyannote/llama_cpp are
    # optional. ffmpeg is an external binary needed for clips + verify-youtube.
    for mod in ("soundcard", "soundfile", "faster_whisper", "fastapi", "paramiko",
                "resemblyzer", "sklearn", "torch", "pyannote.audio", "llama_cpp"):
        try:
            __import__(mod)
            print(f"  [ok]   {mod}")
        except Exception as e:
            print(f"  [miss] {mod}  ({type(e).__name__})")
    import shutil
    ff = shutil.which("ffmpeg")
    print(f"  [{'ok' if ff else 'miss'}]   ffmpeg  "
          f"({ff or 'not on PATH — needed for clips & verify-youtube'})")
    from .device import detect
    print(f"device          : {detect()}")
    if s.remote_enabled:
        try:
            import paramiko  # noqa: F401
            from .remote.cornell import _connect, _run
            cli = _connect()
            rc, out, _ = _run(cli, "hostname; nvidia-smi -L 2>/dev/null | head -1 || true")
            cli.close()
            print(f"cornell         : reachable -> {out.strip()}")
        except Exception as e:
            print(f"cornell         : NOT reachable ({e}); is the VPN up?")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="meetingscribe", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("serve", help="launch the web dashboard")
    sp.add_argument("--host", default="127.0.0.1")
    sp.add_argument("--port", type=int, default=8765)
    sp.add_argument("--reload", action="store_true")
    sp.set_defaults(func=_cmd_serve)

    sub.add_parser("devices", help="list audio devices").set_defaults(func=_cmd_devices)

    sp = sub.add_parser("record", help="record from the terminal")
    sp.add_argument("-t", "--title", default="Untitled meeting")
    sp.add_argument("-p", "--platform", default="other", choices=["meet", "zoom", "other"])
    sp.add_argument("--no-mic", action="store_true")
    sp.add_argument("--no-system", action="store_true")
    sp.set_defaults(func=_cmd_record)

    sp = sub.add_parser("process", help="run batch pipeline on a recording")
    sp.add_argument("target", help="meeting id or path to a recording dir")
    sp.set_defaults(func=_cmd_process)

    sp = sub.add_parser("search", help="full-text search across meetings")
    sp.add_argument("query")
    sp.set_defaults(func=_cmd_search)

    sp = sub.add_parser("fetch-model", help="download whisper + a GGUF LLM")
    sp.add_argument("--whisper", default=None, help="whisper model (default: batch model)")
    sp.add_argument("--llm", default="Qwen/Qwen2.5-3B-Instruct-GGUF",
                    help="HF repo with a GGUF file")
    sp.set_defaults(func=_cmd_fetch_model)

    sp = sub.add_parser("verify-youtube",
                        help="run the pipeline on a YouTube URL + measure WER")
    sp.add_argument("url")
    sp.add_argument("--seconds", type=int, default=300)
    sp.add_argument("--model", default="small.en")
    sp.add_argument("--out", default=None)
    sp.set_defaults(func=_cmd_verify_youtube)

    sub.add_parser("doctor", help="check environment").set_defaults(func=_cmd_doctor)

    args = p.parse_args(argv)
    return args.func(args) or 0


if __name__ == "__main__":
    sys.exit(main())
