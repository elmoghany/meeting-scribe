"""Argument parsing / dispatch for the `meetingscribe` CLI entry point.

Handlers are monkeypatched so we exercise parsing + dispatch without running
heavy command bodies (uvicorn, model downloads, etc.). main() re-reads the
module-global handlers each call, so patching cli._cmd_* takes effect.
"""
import time

import pytest

from app import cli, db
from app.models import Meeting, Segment


def test_serve_parses_host_port_reload(monkeypatch):
    cap = {}
    monkeypatch.setattr(cli, "_cmd_serve",
                        lambda a: cap.update(host=a.host, port=a.port, reload=a.reload))
    assert cli.main(["serve", "--port", "9999", "--reload"]) == 0
    assert cap == {"host": "127.0.0.1", "port": 9999, "reload": True}  # default host, parsed port


def test_record_defaults_and_invalid_platform(monkeypatch):
    cap = {}
    monkeypatch.setattr(cli, "_cmd_record",
                        lambda a: cap.update(title=a.title, platform=a.platform))
    cli.main(["record"])
    assert cap["title"] == "Untitled meeting" and cap["platform"] == "other"
    with pytest.raises(SystemExit):           # platform restricted to a choice set
        cli.main(["record", "-p", "bogus"])


def test_search_requires_query(monkeypatch):
    monkeypatch.setattr(cli, "_cmd_search", lambda a: 0)
    with pytest.raises(SystemExit):           # positional query is required
        cli.main(["search"])
    assert cli.main(["search", "budget"]) == 0


def test_no_subcommand_errors():
    with pytest.raises(SystemExit):           # subparser is required=True
        cli.main([])


def test_return_code_passthrough(monkeypatch):
    monkeypatch.setattr(cli, "_cmd_doctor", lambda a: 3)
    assert cli.main(["doctor"]) == 3          # non-zero rc propagates
    monkeypatch.setattr(cli, "_cmd_doctor", lambda a: None)
    assert cli.main(["doctor"]) == 0          # None -> 0


def test_doctor_reports_default_diarizer_deps_and_ffmpeg(capsys):
    # real run (no mock): doctor must surface the key-free diarizer deps and
    # ffmpeg so a first-run user can see what's missing.
    assert cli.main(["doctor"]) == 0
    out = capsys.readouterr().out
    assert "resemblyzer" in out and "sklearn" in out   # default diarizer deps checked
    assert "ffmpeg" in out                              # external binary checked
    assert "hf_token" in out                            # token status shown
    assert "db " in out and "meeting" in out            # DB opens + migrates, count shown
    assert "disk_free" in out and "GB" in out            # disk space reported (recording needs it)


def test_process_resolves_directory_vs_meeting_id(tmp_path, monkeypatch):
    # _cmd_process: a directory target uses the dir as audio_dir and its name as
    # the id; a bare id resolves audio_dir under recordings_dir. Swapping these
    # would make `process` look in the wrong place.
    from types import SimpleNamespace

    from app import batch, config
    cap = []
    monkeypatch.setattr(batch, "run_batch", lambda mid, audio_dir: cap.append((mid, audio_dir)) or {})

    a_dir = tmp_path / "20260101-meeting-x"
    a_dir.mkdir()
    cli._cmd_process(SimpleNamespace(target=str(a_dir)))
    assert cap[-1] == ("20260101-meeting-x", str(a_dir))     # dir -> name + itself

    monkeypatch.setenv("MEETINGSCRIBE_DATA_DIR", str(tmp_path))
    config.get_settings.cache_clear()
    try:
        cli._cmd_process(SimpleNamespace(target="just-an-id"))
        mid, audio_dir = cap[-1]
        assert mid == "just-an-id"
        assert audio_dir.endswith("just-an-id") and "recordings" in audio_dir   # under recordings_dir
    finally:
        config.get_settings.cache_clear()


def test_devices_command_prints_json(capsys, monkeypatch):
    from types import SimpleNamespace

    from app import capture
    monkeypatch.setattr(capture, "list_devices",
                        lambda: [{"index": 0, "name": "Mic"}, {"index": 1, "name": "Loopback"}])
    cli._cmd_devices(SimpleNamespace())
    out = capsys.readouterr().out
    assert '"name": "Mic"' in out and '"name": "Loopback"' in out   # JSON-rendered device list


def test_search_command_prints_hits(capsys):
    # exercises the real _cmd_search body end-to-end (db.search + formatted print)
    db.reset_connection()
    mid = "cli-search-m"
    if not db.get_meeting(mid):
        db.create_meeting(Meeting(id=mid, title="CLI Search", platform="meet",
                                  started_at=time.time()))
    db.add_segments(mid, [Segment(start=65, end=70, text="the quarterly budget review",
                                  speaker="Me", source="batch")])
    assert cli.main(["search", "quarterly"]) == 0
    out = capsys.readouterr().out
    assert "CLI Search" in out and "quarterly" in out.lower()
    assert "01:05" in out                    # 65s formatted mm:ss
