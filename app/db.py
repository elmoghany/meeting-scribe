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
    duration_sec  REAL,
    template      TEXT
);

CREATE TABLE IF NOT EXISTS segments (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    meeting_id  TEXT NOT NULL REFERENCES meetings(id) ON DELETE CASCADE,
    start       REAL NOT NULL,
    end         REAL NOT NULL,
    speaker     TEXT NOT NULL DEFAULT 'Unknown',
    text        TEXT NOT NULL,
    source      TEXT NOT NULL DEFAULT 'live',
    confidence  REAL
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

CREATE TABLE IF NOT EXISTS meeting_tags (
    meeting_id  TEXT NOT NULL REFERENCES meetings(id) ON DELETE CASCADE,
    tag         TEXT NOT NULL,
    PRIMARY KEY (meeting_id, tag)
);
CREATE INDEX IF NOT EXISTS idx_tags_tag ON meeting_tags(tag);

-- named voice profiles (persistent speaker identification)
CREATE TABLE IF NOT EXISTS speaker_profiles (
    name        TEXT PRIMARY KEY,
    embedding   TEXT NOT NULL,        -- json list[float]
    n_samples   INTEGER NOT NULL DEFAULT 1,
    updated_at  REAL NOT NULL DEFAULT (strftime('%s','now'))
);

-- per-meeting per-speaker mean embedding (so renaming can enroll a profile)
CREATE TABLE IF NOT EXISTS meeting_speaker_embeddings (
    meeting_id  TEXT NOT NULL REFERENCES meetings(id) ON DELETE CASCADE,
    speaker     TEXT NOT NULL,
    embedding   TEXT NOT NULL,        -- json list[float]
    PRIMARY KEY (meeting_id, speaker)
);

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
        # Idempotent migrations for older DBs (columns we added later).
        cols = {r[1] for r in _conn.execute("PRAGMA table_info(segments)").fetchall()}
        if "confidence" not in cols:
            _conn.execute("ALTER TABLE segments ADD COLUMN confidence REAL")
        mcols = {r[1] for r in _conn.execute("PRAGMA table_info(meetings)").fetchall()}
        if "template" not in mcols:
            _conn.execute("ALTER TABLE meetings ADD COLUMN template TEXT")
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
            "INSERT INTO meetings(id,title,platform,started_at,ended_at,status,"
            "language,duration_sec,template)"
            " VALUES (?,?,?,?,?,?,?,?,?)",
            (m.id, m.title, m.platform, m.started_at, m.ended_at, m.status,
             m.language, m.duration_sec, m.template),
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


def list_meetings(limit: int = 100, tag: str | None = None) -> list[Meeting]:
    with cursor() as c:
        if tag:
            rows = c.execute(
                "SELECT m.* FROM meetings m JOIN meeting_tags t ON t.meeting_id = m.id"
                " WHERE t.tag = ? ORDER BY m.started_at DESC LIMIT ?", (tag, limit)
            ).fetchall()
        else:
            rows = c.execute(
                "SELECT * FROM meetings ORDER BY started_at DESC LIMIT ?", (limit,)
            ).fetchall()
    return [_row_to_meeting(r) for r in rows]


def meeting_stats() -> dict:
    """Per-meeting {segments, words} for the list view (one query). Prefers the
    polished 'batch' transcript; falls back to 'live'. Word count is a fast
    space-based approximation."""
    with cursor() as c:
        rows = c.execute(
            "SELECT meeting_id, source, COUNT(*) n,"
            " SUM(LENGTH(text) - LENGTH(REPLACE(text, ' ', '')) + 1) w"
            " FROM segments GROUP BY meeting_id, source").fetchall()
    by: dict[str, dict] = {}
    for r in rows:
        by.setdefault(r["meeting_id"], {})[r["source"]] = (r["n"], int(r["w"] or 0))
    out = {}
    for mid, srcs in by.items():
        n, w = srcs.get("batch") or srcs.get("live") or (0, 0)
        out[mid] = {"segments": n, "words": w}
    return out


def add_tag(meeting_id: str, tag: str) -> None:
    tag = tag.strip().lower()
    if not tag:
        return
    with cursor() as c:
        c.execute("INSERT OR IGNORE INTO meeting_tags(meeting_id, tag) VALUES (?,?)",
                  (meeting_id, tag))


def remove_tag(meeting_id: str, tag: str) -> None:
    with cursor() as c:
        c.execute("DELETE FROM meeting_tags WHERE meeting_id=? AND tag=?",
                  (meeting_id, tag.strip().lower()))


def get_tags(meeting_id: str) -> list[str]:
    with cursor() as c:
        rows = c.execute("SELECT tag FROM meeting_tags WHERE meeting_id=? ORDER BY tag",
                         (meeting_id,)).fetchall()
    return [r["tag"] for r in rows]


def all_tags() -> list[dict]:
    with cursor() as c:
        rows = c.execute(
            "SELECT tag, COUNT(*) n FROM meeting_tags GROUP BY tag ORDER BY n DESC, tag"
        ).fetchall()
    return [{"tag": r["tag"], "count": r["n"]} for r in rows]


# --------------------------------------------------------------------------- #
# speaker profiles + per-meeting embeddings
# --------------------------------------------------------------------------- #
def list_profiles() -> list[dict]:
    with cursor() as c:
        rows = c.execute(
            "SELECT name, embedding, n_samples FROM speaker_profiles ORDER BY name"
        ).fetchall()
    return [{"name": r["name"], "embedding": json.loads(r["embedding"]),
             "n_samples": r["n_samples"]} for r in rows]


def upsert_profile(name: str, embedding: list[float]) -> None:
    """Create or update a named voice profile, keeping a running-mean embedding."""
    from .pipeline.speakerid import running_mean

    name = name.strip()
    if not name or not embedding:
        return
    with cursor() as c:
        row = c.execute("SELECT embedding, n_samples FROM speaker_profiles WHERE name=?",
                        (name,)).fetchone()
        if row:
            merged = running_mean(json.loads(row["embedding"]), row["n_samples"], embedding)
            c.execute("UPDATE speaker_profiles SET embedding=?, n_samples=n_samples+1,"
                      " updated_at=strftime('%s','now') WHERE name=?",
                      (json.dumps(merged), name))
        else:
            c.execute("INSERT INTO speaker_profiles(name, embedding, n_samples)"
                      " VALUES (?,?,1)", (name, json.dumps(embedding)))


def delete_profile(name: str) -> None:
    with cursor() as c:
        c.execute("DELETE FROM speaker_profiles WHERE name=?", (name,))


def save_meeting_embeddings(meeting_id: str, embeddings: dict) -> None:
    with cursor() as c:
        c.executemany(
            "INSERT OR REPLACE INTO meeting_speaker_embeddings(meeting_id,speaker,embedding)"
            " VALUES (?,?,?)",
            [(meeting_id, spk, json.dumps(emb)) for spk, emb in embeddings.items() if emb])


def profile_meetings(name: str, limit: int = 50) -> list[dict]:
    """Meetings where a named voice profile appears (post-apply_profiles rename).
    Joins meeting_speaker_embeddings with meetings on the matching speaker label."""
    with cursor() as c:
        rows = c.execute(
            "SELECT mse.meeting_id, m.title, m.started_at"
            " FROM meeting_speaker_embeddings mse"
            " JOIN meetings m ON m.id = mse.meeting_id"
            " WHERE mse.speaker = ?"
            " ORDER BY m.started_at DESC LIMIT ?",
            (name, limit),
        ).fetchall()
    return [dict(r) for r in rows]


def get_meeting_embedding(meeting_id: str, speaker: str) -> list[float] | None:
    with cursor() as c:
        r = c.execute("SELECT embedding FROM meeting_speaker_embeddings"
                      " WHERE meeting_id=? AND speaker=?", (meeting_id, speaker)).fetchone()
    return json.loads(r["embedding"]) if r else None


def delete_meeting(meeting_id: str) -> None:
    with cursor() as c:
        c.execute("DELETE FROM meetings WHERE id = ?", (meeting_id,))


def _row_to_meeting(r: sqlite3.Row) -> Meeting:
    keys = r.keys()
    return Meeting(
        id=r["id"], title=r["title"], platform=r["platform"],
        started_at=r["started_at"], ended_at=r["ended_at"], status=r["status"],
        language=r["language"], duration_sec=r["duration_sec"],
        template=(r["template"] if "template" in keys else None),
    )


# --------------------------------------------------------------------------- #
# segments
# --------------------------------------------------------------------------- #
def add_segment(meeting_id: str, seg: Segment) -> int:
    with cursor() as c:
        cur = c.execute(
            "INSERT INTO segments(meeting_id,start,end,speaker,text,source,confidence)"
            " VALUES (?,?,?,?,?,?,?)",
            (meeting_id, seg.start, seg.end, seg.speaker, seg.text, seg.source,
             seg.confidence),
        )
        return int(cur.lastrowid)


def add_segments(meeting_id: str, segs: list[Segment]) -> None:
    with cursor() as c:
        c.executemany(
            "INSERT INTO segments(meeting_id,start,end,speaker,text,source,confidence)"
            " VALUES (?,?,?,?,?,?,?)",
            [(meeting_id, s.start, s.end, s.speaker, s.text, s.source, s.confidence)
             for s in segs],
        )


def replace_segments(meeting_id: str, segs: list[Segment], source: str) -> None:
    """Swap out all segments of a given source (e.g. replace 'live' draft with
    the polished 'batch' result)."""
    with cursor() as c:
        c.execute("DELETE FROM segments WHERE meeting_id = ? AND source = ?",
                  (meeting_id, source))
        c.executemany(
            "INSERT INTO segments(meeting_id,start,end,speaker,text,source,confidence)"
            " VALUES (?,?,?,?,?,?,?)",
            [(meeting_id, s.start, s.end, s.speaker, s.text, s.source, s.confidence)
             for s in segs],
        )


def update_segment(segment_id: int, text: str | None = None,
                   speaker: str | None = None) -> dict | None:
    """Edit a segment's text and/or speaker (manual correction). The FTS index
    stays in sync via the segments_au trigger. Returns the segment's
    {id, meeting_id} or None if it doesn't exist."""
    sets, args = [], []
    if text is not None:
        sets.append("text = ?")
        args.append(text)
    if speaker is not None:
        sets.append("speaker = ?")
        args.append(speaker)
    if not sets:
        return None
    with cursor() as c:
        row = c.execute("SELECT meeting_id FROM segments WHERE id = ?",
                        (segment_id,)).fetchone()
        if not row:
            return None
        c.execute(f"UPDATE segments SET {', '.join(sets)} WHERE id = ?",
                  (*args, segment_id))
    return {"id": segment_id, "meeting_id": row["meeting_id"]}


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
                speaker=r["speaker"], text=r["text"], source=r["source"],
                confidence=(r["confidence"] if "confidence" in r.keys() else None))
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


def all_action_items(open_only: bool = False, owner: str | None = None,
                     limit: int = 500) -> list[dict]:
    """Action items across every meeting, with meeting context — for the global
    task view. Open (undone) items first, then by recency."""
    q = ("SELECT a.id, a.text, a.owner, a.due, a.done, a.meeting_id,"
         "       m.title AS meeting_title, m.started_at"
         " FROM action_items a JOIN meetings m ON m.id = a.meeting_id")
    conds, args = [], []
    if open_only:
        conds.append("a.done = 0")
    if owner:
        conds.append("a.owner = ?")
        args.append(owner)
    if conds:
        q += " WHERE " + " AND ".join(conds)
    q += " ORDER BY a.done ASC, m.started_at DESC LIMIT ?"
    args.append(limit)
    with cursor() as c:
        rows = c.execute(q, args).fetchall()
    return [dict(r) for r in rows]


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
