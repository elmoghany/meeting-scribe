"""Schema migration + idempotency.

Opening a DB created by an OLDER MeetingScribe (before the `confidence` /
`template` columns and some tables existed) must migrate it in place without
losing data, and re-opening an already-current DB must be a no-op. _connect()
re-runs _SCHEMA on every fresh connection (i.e. every app start), so any
non-`IF NOT EXISTS` statement would crash on the second run — this pins that.
"""
import sqlite3

from app import db

# Pre-migration schema: meetings WITHOUT `template`, segments WITHOUT
# `confidence`, and none of the later tables (summaries, action_items, ...).
_OLD_SCHEMA = """
CREATE TABLE meetings (
    id TEXT PRIMARY KEY, title TEXT NOT NULL, platform TEXT NOT NULL DEFAULT 'other',
    started_at REAL NOT NULL, ended_at REAL, status TEXT NOT NULL DEFAULT 'recording',
    language TEXT, duration_sec REAL
);
CREATE TABLE segments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    meeting_id TEXT NOT NULL REFERENCES meetings(id) ON DELETE CASCADE,
    start REAL NOT NULL, end REAL NOT NULL, speaker TEXT NOT NULL DEFAULT 'Unknown',
    text TEXT NOT NULL, source TEXT NOT NULL DEFAULT 'live'
);
"""


def _seed_old_db(path):
    con = sqlite3.connect(str(path))
    con.executescript(_OLD_SCHEMA)
    con.execute("INSERT INTO meetings (id, title, platform, started_at) "
                "VALUES ('m1', 'Old Meeting', 'meet', 1.0)")
    con.execute("INSERT INTO segments (meeting_id, start, end, speaker, text, source) "
                "VALUES ('m1', 0, 1, 'Me', 'hello world', 'batch')")
    con.commit()
    con.close()


def test_old_db_migrates_in_place_and_keeps_data(tmp_path, monkeypatch):
    _seed_old_db(tmp_path / "meetingscribe.db")
    s = db.get_settings()
    monkeypatch.setattr(s, "data_dir", tmp_path)   # db_path = tmp_path/meetingscribe.db
    db.reset_connection()
    try:
        conn = db._connect()                        # runs _SCHEMA + idempotent migrations

        seg_cols = {r[1] for r in conn.execute("PRAGMA table_info(segments)")}
        mtg_cols = {r[1] for r in conn.execute("PRAGMA table_info(meetings)")}
        assert "confidence" in seg_cols             # added by migration
        assert "template" in mtg_cols               # added by migration

        # pre-existing data survived
        m = db.get_meeting("m1")
        assert m and m.title == "Old Meeting"
        segs = db.get_segments("m1")
        assert len(segs) == 1 and segs[0].text == "hello world"

        # tables the old DB lacked were created
        names = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        assert {"summaries", "action_items", "annotations", "segments_fts"} <= names

        # idempotent: a SECOND fresh connection re-runs _SCHEMA over the migrated
        # DB without error (every app restart does this) and data is intact
        db.reset_connection()
        db._connect()
        assert db.get_meeting("m1").title == "Old Meeting"
    finally:
        db.reset_connection()                       # don't leak the temp conn to other tests
