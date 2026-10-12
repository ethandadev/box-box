"""What the engineer remembers between sessions: sessions, laps, debriefs and notes about the driver.

SQLite in the app folder. Every call is short and guarded: a locked or corrupt database logs an error
and the radio keeps working without memory.
"""

from __future__ import annotations

import logging
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

from box_box import paths
from box_box.state.model import LapRecord

log = logging.getLogger(__name__)

SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    id INTEGER PRIMARY KEY,
    uid TEXT NOT NULL,
    started_at REAL NOT NULL,
    ended_at REAL,
    track TEXT,
    session_type TEXT,
    best_lap_ms INTEGER,
    position INTEGER,
    laps INTEGER DEFAULT 0,
    recording TEXT
);
CREATE INDEX IF NOT EXISTS sessions_uid ON sessions(uid);
CREATE TABLE IF NOT EXISTS laps (
    session_id INTEGER NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    lap_num INTEGER NOT NULL,
    lap_ms INTEGER NOT NULL,
    s1 INTEGER, s2 INTEGER, s3 INTEGER,
    valid INTEGER, pit_lap INTEGER,
    compound INTEGER, tyre_age INTEGER, wear REAL, fuel REAL, position INTEGER,
    PRIMARY KEY (session_id, lap_num)
);
CREATE TABLE IF NOT EXISTS notes (
    id INTEGER PRIMARY KEY,
    created_at REAL NOT NULL,
    kind TEXT NOT NULL,
    text TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS radio (
    id INTEGER PRIMARY KEY,
    session_id INTEGER,
    at REAL NOT NULL,
    who TEXT NOT NULL,
    text TEXT NOT NULL
);
"""


class Memory:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or paths.database_file()
        self._lock = threading.Lock()
        self._db: sqlite3.Connection | None = None
        self.error: str | None = None
        self._open()

    def _open(self) -> None:
        db = None
        try:
            db = sqlite3.connect(self.path, check_same_thread=False, timeout=5)
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("PRAGMA foreign_keys=ON")
            db.executescript(SCHEMA)
            db.commit()
            self._db = db
        except sqlite3.DatabaseError as e:
            if db is not None:
                db.close()  # Windows can't move a file that's still open
            log.error("memory database unusable (%s); moving it aside and starting fresh", e)
            self.error = str(e)
            try:
                self.path.replace(self.path.with_suffix(f".broken-{int(time.time())}.sqlite3"))
                db = sqlite3.connect(self.path, check_same_thread=False, timeout=5)
                db.executescript(SCHEMA)
                self._db = db
                self.error = None
            except Exception as e2:  # noqa: BLE001
                log.error("memory disabled: %s", e2)
                self._db = None

    def _exec(self, sql: str, args: tuple = (), fetch: bool = False) -> list[tuple] | int | None:
        if self._db is None:
            return [] if fetch else None
        try:
            with self._lock:
                cur = self._db.execute(sql, args)
                if fetch:
                    return cur.fetchall()
                self._db.commit()
                return cur.lastrowid
        except sqlite3.Error as e:
            log.error("memory query failed: %s", e)
            return [] if fetch else None

    def close(self) -> None:
        if self._db is not None:
            with self._lock:
                self._db.close()
            self._db = None

    # ---- sessions ---------------------------------------------------------------------------------

    def start_session(self, uid: int, track: str, session_type: str, recording: str | None = None) -> int | None:
        rows = self._exec("SELECT id FROM sessions WHERE uid = ? ORDER BY id DESC LIMIT 1", (f"{uid:x}",), fetch=True)
        if rows:
            return rows[0][0]  # type: ignore[index]
        sid = self._exec(
            "INSERT INTO sessions (uid, started_at, track, session_type, recording) VALUES (?, ?, ?, ?, ?)",
            (f"{uid:x}", time.time(), track, session_type, recording),
        )
        return sid if isinstance(sid, int) else None

    def end_session(self, session_id: int, best_lap_ms: int | None, position: int | None) -> None:
        self._exec(
            "UPDATE sessions SET ended_at = ?, best_lap_ms = ?, position = ?, "
            "laps = (SELECT COUNT(*) FROM laps WHERE session_id = ?) WHERE id = ?",
            (time.time(), best_lap_ms, position, session_id, session_id),
        )

    def record_lap(self, session_id: int, r: LapRecord) -> None:
        self._exec(
            "INSERT OR REPLACE INTO laps VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                session_id,
                r.lap_num,
                r.lap_ms,
                r.sector1_ms,
                r.sector2_ms,
                r.sector3_ms,
                int(r.valid),
                int(r.pit_lap),
                r.visual_compound,
                r.tyre_age,
                r.tyre_wear,
                r.fuel_kg,
                r.position,
            ),
        )

    def recent_sessions(self, limit: int = 20) -> list[dict[str, Any]]:
        rows = self._exec(
            "SELECT id, started_at, track, session_type, best_lap_ms, position, laps, recording FROM sessions "
            "ORDER BY started_at DESC LIMIT ?",
            (limit,),
            fetch=True,
        )
        keys = ("id", "started_at", "track", "session_type", "best_lap_ms", "position", "laps", "recording")
        return [dict(zip(keys, row, strict=True)) for row in rows]  # type: ignore[union-attr]

    def track_best(self, track: str) -> int | None:
        rows = self._exec(
            "SELECT MIN(l.lap_ms) FROM laps l JOIN sessions s ON s.id = l.session_id "
            "WHERE s.track = ? AND l.valid = 1 AND l.pit_lap = 0 AND l.lap_ms > 0",
            (track,),
            fetch=True,
        )
        return rows[0][0] if rows and rows[0][0] else None  # type: ignore[index]

    # ---- notes & radio log ------------------------------------------------------------------------

    def add_note(self, kind: str, text: str) -> None:
        self._exec("INSERT INTO notes (created_at, kind, text) VALUES (?, ?, ?)", (time.time(), kind, text[:1000]))

    def notes(self, limit: int = 15) -> list[dict[str, Any]]:
        rows = self._exec(
            "SELECT created_at, kind, text FROM notes ORDER BY created_at DESC LIMIT ?", (limit,), fetch=True
        )
        return [{"at": r[0], "kind": r[1], "text": r[2]} for r in rows]  # type: ignore[union-attr]

    def radio_between(self, start: float, end: float) -> list[dict[str, Any]]:
        rows = self._exec(
            "SELECT at, who, text FROM radio WHERE at BETWEEN ? AND ? ORDER BY at", (start, end), fetch=True
        )
        return [{"at": r[0], "who": r[1], "text": r[2]} for r in rows or []]

    def log_radio(self, session_id: int | None, who: str, text: str) -> None:
        self._exec(
            "INSERT INTO radio (session_id, at, who, text) VALUES (?, ?, ?, ?)",
            (session_id, time.time(), who, text[:500]),
        )

    def driver_profile(self) -> str:
        """A few lines about the driver for the AI's context: notes, past debriefs, recent results."""
        lines = []
        for n in reversed(self.notes(12)):
            lines.append(f"- ({n['kind']}) {n['text']}")
        for s in self.recent_sessions(5):
            if s["best_lap_ms"] or s["position"]:
                when = time.strftime("%b %d", time.localtime(s["started_at"]))
                best = f", best {s['best_lap_ms'] / 1000:.3f}s" if s["best_lap_ms"] else ""
                lines.append(f"- {when}: {s['track']} {s['session_type']}, finished P{s['position']}{best}")
        return "\n".join(lines)
