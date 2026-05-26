"""SQLite storage with FTS5 full-text search over transcripts.

Pure stdlib (sqlite3) — no ORM, no extra deps. The DB lives at
``settings.db_path`` (under C:\\cornell\\meetingnotes by default).
"""
from __future__ import annotations

import json
import sqlite3
import threading
from contextlib import contextmanager
from typing import Iterator

from .config import get_settings
from .models import ActionItem, Meeting, Segment, Summary

_SCHEMA = """
CREATE TABLE IF NOT EXISTS meetings (
    id            TEXT PRIMARY KEY,
    title         TEXT NOT NULL,
    platform      TEXT NOT NULL DEFAULT 'other',
    started_at    REAL NOT NULL,
    ended_at      REAL,
    status        TEXT NOT NULL DEFAULT 'recording',
    language      TEXT,
    duration_sec  REAL
);

CREATE TABLE IF NOT EXISTS segments (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    meeting_id  TEXT NOT NULL REFERENCES meetings(id) ON DELETE CASCADE,
    start       REAL NOT NULL,
    end         REAL NOT NULL,
    speaker     TEXT NOT NULL DEFAULT 'Unknown',
    text        TEXT NOT NULL,
    source      TEXT NOT NULL DEFAULT 'live'
);
CREATE INDEX IF NOT EXISTS idx_segments_meeting ON segments(meeting_id, start);

CREATE TABLE IF NOT EXISTS summaries (
    meeting_id  TEXT PRIMARY KEY REFERENCES meetings(id) ON DELETE CASCADE,
    overview    TEXT NOT NULL DEFAULT '',
    key_points  TEXT NOT NULL DEFAULT '[]',
    decisions   TEXT NOT NULL DEFAULT '[]',
    created_at  REAL NOT NULL DEFAULT (strftime('%s','now'))
);

CREATE TABLE IF NOT EXISTS action_items (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    meeting_id  TEXT NOT NULL REFERENCES meetings(id) ON DELETE CASCADE,
    text        TEXT NOT NULL,
    owner       TEXT,
    due         TEXT,
    done        INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_action_meeting ON action_items(meeting_id);

-- highlights (kind='highlight', segment-anchored) and comments (kind='comment')
CREATE TABLE IF NOT EXISTS annotations (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    meeting_id  TEXT NOT NULL REFERENCES meetings(id) ON DELETE CASCADE,
    segment_id  INTEGER,
    kind        TEXT NOT NULL DEFAULT 'comment',
    text        TEXT NOT NULL DEFAULT '',
    author      TEXT,
    created_at  REAL NOT NULL DEFAULT (strftime('%s','now'))
);
CREATE INDEX IF NOT EXISTS idx_annot_meeting ON annotations(meeting_id);

-- Full-text search over transcript text, kept in sync via triggers.
CREATE VIRTUAL TABLE IF NOT EXISTS segments_fts USING fts5(
    text, speaker UNINDEXED, meeting_id UNINDEXED,
    content='segments', content_rowid='id'
);
CREATE TRIGGER IF NOT EXISTS segments_ai AFTER INSERT ON segments BEGIN
    INSERT INTO segments_fts(rowid, text, speaker, meeting_id)
    VALUES (new.id, new.text, new.speaker, new.meeting_id);
END;
CREATE TRIGGER IF NOT EXISTS segments_ad AFTER DELETE ON segments BEGIN
    INSERT INTO segments_fts(segments_fts, rowid, text, speaker, meeting_id)
    VALUES ('delete', old.id, old.text, old.speaker, old.meeting_id);
END;
CREATE TRIGGER IF NOT EXISTS segments_au AFTER UPDATE ON segments BEGIN
    INSERT INTO segments_fts(segments_fts, rowid, text, speaker, meeting_id)
    VALUES ('delete', old.id, old.text, old.speaker, old.meeting_id);
    INSERT INTO segments_fts(rowid, text, speaker, meeting_id)
    VALUES (new.id, new.text, new.speaker, new.meeting_id);
END;
"""

_lock = threading.Lock()
_conn: sqlite3.Connection | None = None


def _connect() -> sqlite3.Connection:
    global _conn
    if _conn is None:
        s = get_settings()
        s.ensure_dirs()
        _conn = sqlite3.connect(str(s.db_path), check_same_thread=False)
        _conn.row_factory = sqlite3.Row
        _conn.execute("PRAGMA foreign_keys = ON")
        _conn.execute("PRAGMA journal_mode = WAL")
        _conn.executescript(_SCHEMA)
        _conn.commit()
    return _conn


def reset_connection() -> None:
    """Close the cached connection (used by tests when the data dir changes)."""
    global _conn
    if _conn is not None:
        _conn.close()
        _conn = None


@contextmanager
def cursor() -> Iterator[sqlite3.Cursor]:
    conn = _connect()
    with _lock:
        cur = conn.cursor()
        try:
            yield cur
            conn.commit()
        finally:
            cur.close()


# --------------------------------------------------------------------------- #
# meetings
# --------------------------------------------------------------------------- #
def create_meeting(m: Meeting) -> None:
    with cursor() as c:
        c.execute(
            "INSERT INTO meetings(id,title,platform,started_at,ended_at,status,language,duration_sec)"
            " VALUES (?,?,?,?,?,?,?,?)",
            (m.id, m.title, m.platform, m.started_at, m.ended_at, m.status,
             m.language, m.duration_sec),
        )


def update_meeting(meeting_id: str, **fields) -> None:
    if not fields:
        return
    cols = ", ".join(f"{k} = ?" for k in fields)
    with cursor() as c:
        c.execute(f"UPDATE meetings SET {cols} WHERE id = ?",
                  (*fields.values(), meeting_id))


def get_meeting(meeting_id: str) -> Meeting | None:
    with cursor() as c:
        row = c.execute("SELECT * FROM meetings WHERE id = ?", (meeting_id,)).fetchone()
    return _row_to_meeting(row) if row else None


def list_meetings(limit: int = 100) -> list[Meeting]:
    with cursor() as c:
        rows = c.execute(
            "SELECT * FROM meetings ORDER BY started_at DESC LIMIT ?", (limit,)
        ).fetchall()
    return [_row_to_meeting(r) for r in rows]


def delete_meeting(meeting_id: str) -> None:
    with cursor() as c:
        c.execute("DELETE FROM meetings WHERE id = ?", (meeting_id,))


def _row_to_meeting(r: sqlite3.Row) -> Meeting:
    return Meeting(
        id=r["id"], title=r["title"], platform=r["platform"],
        started_at=r["started_at"], ended_at=r["ended_at"], status=r["status"],
        language=r["language"], duration_sec=r["duration_sec"],
    )


# --------------------------------------------------------------------------- #
# segments
# --------------------------------------------------------------------------- #
def add_segment(meeting_id: str, seg: Segment) -> int:
    with cursor() as c:
        cur = c.execute(
            "INSERT INTO segments(meeting_id,start,end,speaker,text,source)"
            " VALUES (?,?,?,?,?,?)",
            (meeting_id, seg.start, seg.end, seg.speaker, seg.text, seg.source),
        )
        return int(cur.lastrowid)


def add_segments(meeting_id: str, segs: list[Segment]) -> None:
    with cursor() as c:
        c.executemany(
            "INSERT INTO segments(meeting_id,start,end,speaker,text,source)"
            " VALUES (?,?,?,?,?,?)",
            [(meeting_id, s.start, s.end, s.speaker, s.text, s.source) for s in segs],
        )


def replace_segments(meeting_id: str, segs: list[Segment], source: str) -> None:
    """Swap out all segments of a given source (e.g. replace 'live' draft with
    the polished 'batch' result)."""
    with cursor() as c:
        c.execute("DELETE FROM segments WHERE meeting_id = ? AND source = ?",
                  (meeting_id, source))
        c.executemany(
            "INSERT INTO segments(meeting_id,start,end,speaker,text,source)"
            " VALUES (?,?,?,?,?,?)",
            [(meeting_id, s.start, s.end, s.speaker, s.text, s.source) for s in segs],
        )


def get_segments(meeting_id: str, source: str | None = None) -> list[Segment]:
    q = "SELECT * FROM segments WHERE meeting_id = ?"
    args: list = [meeting_id]
    if source:
        q += " AND source = ?"
        args.append(source)
    q += " ORDER BY start"
    with cursor() as c:
        rows = c.execute(q, args).fetchall()
    return [
        Segment(id=r["id"], meeting_id=r["meeting_id"], start=r["start"], end=r["end"],
                speaker=r["speaker"], text=r["text"], source=r["source"])
        for r in rows
    ]


# --------------------------------------------------------------------------- #
# summary + action items
# --------------------------------------------------------------------------- #
def save_summary(meeting_id: str, s: Summary) -> None:
    with cursor() as c:
        c.execute(
            "INSERT INTO summaries(meeting_id,overview,key_points,decisions)"
            " VALUES (?,?,?,?)"
            " ON CONFLICT(meeting_id) DO UPDATE SET"
            " overview=excluded.overview, key_points=excluded.key_points,"
            " decisions=excluded.decisions",
            (meeting_id, s.overview, json.dumps(s.key_points), json.dumps(s.decisions)),
        )


def get_summary(meeting_id: str) -> Summary | None:
    with cursor() as c:
        r = c.execute("SELECT * FROM summaries WHERE meeting_id = ?",
                      (meeting_id,)).fetchone()
    if not r:
        return None
    return Summary(overview=r["overview"],
                   key_points=json.loads(r["key_points"]),
                   decisions=json.loads(r["decisions"]))


def save_action_items(meeting_id: str, items: list[ActionItem]) -> None:
    with cursor() as c:
        c.execute("DELETE FROM action_items WHERE meeting_id = ?", (meeting_id,))
        c.executemany(
            "INSERT INTO action_items(meeting_id,text,owner,due,done)"
            " VALUES (?,?,?,?,?)",
            [(meeting_id, a.text, a.owner, a.due, int(a.done)) for a in items],
        )


def get_action_items(meeting_id: str) -> list[ActionItem]:
    with cursor() as c:
        rows = c.execute("SELECT * FROM action_items WHERE meeting_id = ? ORDER BY id",
                         (meeting_id,)).fetchall()
    return [ActionItem(id=r["id"], text=r["text"], owner=r["owner"],
                       due=r["due"], done=bool(r["done"])) for r in rows]


def set_action_done(item_id: int, done: bool) -> None:
    with cursor() as c:
        c.execute("UPDATE action_items SET done = ? WHERE id = ?", (int(done), item_id))


# --------------------------------------------------------------------------- #
# annotations (highlights + comments)
# --------------------------------------------------------------------------- #
def add_annotation(meeting_id: str, kind: str = "comment", text: str = "",
                   segment_id: int | None = None, author: str | None = None) -> int:
    with cursor() as c:
        cur = c.execute(
            "INSERT INTO annotations(meeting_id,segment_id,kind,text,author)"
            " VALUES (?,?,?,?,?)",
            (meeting_id, segment_id, kind, text, author))
        return int(cur.lastrowid)


def toggle_highlight(meeting_id: str, segment_id: int) -> bool:
    """Toggle a highlight on a segment. Returns True if now highlighted."""
    with cursor() as c:
        row = c.execute(
            "SELECT id FROM annotations WHERE meeting_id=? AND segment_id=? AND kind='highlight'",
            (meeting_id, segment_id)).fetchone()
        if row:
            c.execute("DELETE FROM annotations WHERE id=?", (row["id"],))
            return False
        c.execute("INSERT INTO annotations(meeting_id,segment_id,kind,text)"
                  " VALUES (?,?,'highlight','')", (meeting_id, segment_id))
        return True


def list_annotations(meeting_id: str) -> list[dict]:
    with cursor() as c:
        rows = c.execute(
            "SELECT * FROM annotations WHERE meeting_id=? ORDER BY created_at, id",
            (meeting_id,)).fetchall()
    return [dict(r) for r in rows]


def delete_annotation(annotation_id: int) -> None:
    with cursor() as c:
        c.execute("DELETE FROM annotations WHERE id=?", (annotation_id,))


# --------------------------------------------------------------------------- #
# search
# --------------------------------------------------------------------------- #
def search(query: str, limit: int = 50) -> list[dict]:
    """Full-text search across every meeting's transcript. Returns hits with a
    highlighted snippet, ordered by relevance."""
    with cursor() as c:
        rows = c.execute(
            """
            SELECT s.meeting_id, m.title, s.speaker, s.start,
                   snippet(segments_fts, 0, '[', ']', ' … ', 12) AS snippet
            FROM segments_fts
            JOIN segments s ON s.id = segments_fts.rowid
            JOIN meetings m ON m.id = s.meeting_id
            WHERE segments_fts MATCH ?
            ORDER BY bm25(segments_fts)
            LIMIT ?
            """,
            (query, limit),
        ).fetchall()
    return [dict(r) for r in rows]
