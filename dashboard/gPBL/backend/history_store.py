"""Sensor reading + insight history — local SQLite log.

Kept separate from Firebase to avoid RTDB bandwidth/connection limits from
continuous polling (see docs/llm-ergonomics-research.md). Firebase stays the
real-time transport for the latest reading; this module is the append-only
log used for windowed aggregation (insight_service.py).
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import config

DB_PATH = Path(config.HISTORY_DB_PATH)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS readings (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    device_id       TEXT NOT NULL,
    ts              TEXT NOT NULL,
    distance_cm     REAL,
    light_adc       INTEGER,
    brightness_lux  REAL,
    sitting_minutes INTEGER,
    blink_rate_bpm  REAL,
    head_pitch_deg  REAL,
    head_roll_deg   REAL,
    head_yaw_deg    REAL,
    risk_score      INTEGER,
    risk_level      TEXT,
    events_json     TEXT NOT NULL,
    created_at      TEXT NOT NULL,
    session_id      TEXT,
    state           TEXT
);
CREATE INDEX IF NOT EXISTS idx_readings_device_ts ON readings(device_id, ts);

CREATE TABLE IF NOT EXISTS insight_snapshots (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    device_id     TEXT NOT NULL,
    period_start  TEXT NOT NULL,
    period_end    TEXT NOT NULL,
    stats_json    TEXT NOT NULL,
    summary       TEXT NOT NULL,
    model_name    TEXT NOT NULL,
    created_at    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_insights_device_created ON insight_snapshots(device_id, created_at);

CREATE TABLE IF NOT EXISTS sessions (
    id            TEXT PRIMARY KEY,
    device_id     TEXT NOT NULL,
    source        TEXT,
    state         TEXT NOT NULL,
    started_at    TEXT NOT NULL,
    ended_at      TEXT,
    exposure_sec  REAL DEFAULT 0,
    grace_until   TEXT,
    governor_json TEXT,
    created_at    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_sessions_device ON sessions(device_id, started_at);
"""


def _add_column_if_missing(conn: sqlite3.Connection, table: str, column: str, typedef: str) -> None:
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    names = {row[1] for row in rows}
    if column not in names:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {typedef}")


def _init_db() -> None:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(DB_PATH) as conn:
        conn.executescript(_SCHEMA)
        _add_column_if_missing(conn, "readings", "session_id", "TEXT")
        _add_column_if_missing(conn, "readings", "state", "TEXT")
        _add_column_if_missing(conn, "insight_snapshots", "session_id", "TEXT")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_readings_session ON readings(session_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_insights_session ON insight_snapshots(session_id)")


_init_db()


def insert_reading(reading: dict) -> None:
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            """
            INSERT INTO readings (
                device_id, ts, distance_cm, light_adc, brightness_lux,
                sitting_minutes, blink_rate_bpm,
                head_pitch_deg, head_roll_deg, head_yaw_deg,
                risk_score, risk_level, events_json, created_at, session_id, state
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                reading["device_id"],
                reading["timestamp"],
                reading.get("distance_cm"),
                reading.get("light_adc"),
                reading.get("brightness_lux"),
                reading.get("sitting_minutes"),
                reading.get("blink_rate_bpm"),
                reading.get("head_pitch_deg"),
                reading.get("head_roll_deg"),
                reading.get("head_yaw_deg"),
                reading.get("risk_score"),
                reading.get("risk_level"),
                json.dumps(reading.get("events", []), ensure_ascii=False),
                datetime.now(timezone.utc).isoformat(),
                reading.get("session_id"),
                reading.get("state"),
            ),
        )


def get_readings_since(device_id: str, since_iso: str) -> list[dict]:
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT * FROM readings WHERE device_id = ? AND ts >= ? ORDER BY ts ASC",
            (device_id, since_iso),
        ).fetchall()
        return [dict(row) for row in rows]


def get_readings_for_session(session_id: str) -> list[dict]:
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT * FROM readings WHERE session_id = ? ORDER BY ts ASC",
            (session_id,),
        ).fetchall()
        return [dict(row) for row in rows]


def create_session(session: dict) -> None:
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            """
            INSERT INTO sessions (
                id, device_id, source, state, started_at, ended_at,
                exposure_sec, grace_until, governor_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                session["id"],
                session["device_id"],
                session.get("source"),
                session["state"],
                session["started_at"],
                session.get("ended_at"),
                session.get("exposure_sec") or 0,
                session.get("grace_until"),
                session.get("governor_json"),
                datetime.now(timezone.utc).isoformat(),
            ),
        )


def update_session(session_id: str, **fields: object) -> None:
    if not session_id or not fields:
        return
    allowed = {
        "state",
        "source",
        "ended_at",
        "exposure_sec",
        "grace_until",
        "governor_json",
    }
    cols = [(key, value) for key, value in fields.items() if key in allowed]
    if not cols:
        return
    assignments = ", ".join(f"{key} = ?" for key, _ in cols)
    values = [value for _, value in cols]
    values.append(session_id)
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(f"UPDATE sessions SET {assignments} WHERE id = ?", values)


def close_session(session_id: str, ended_at: str, exposure_sec: float, state: str = "ended") -> None:
    update_session(session_id, ended_at=ended_at, exposure_sec=exposure_sec, state=state)


def get_open_session(device_id: str) -> dict | None:
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            """
            SELECT * FROM sessions
            WHERE device_id = ? AND ended_at IS NULL
            ORDER BY started_at DESC
            LIMIT 1
            """,
            (device_id,),
        ).fetchone()
        return dict(row) if row else None


def get_session(session_id: str) -> dict | None:
    if not session_id:
        return None
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT * FROM sessions WHERE id = ?",
            (session_id,),
        ).fetchone()
        return dict(row) if row else None


def get_latest_session(device_id: str) -> dict | None:
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            """
            SELECT * FROM sessions
            WHERE device_id = ?
            ORDER BY started_at DESC
            LIMIT 1
            """,
            (device_id,),
        ).fetchone()
        return dict(row) if row else None


def insert_insight(
    device_id: str,
    period_start: str,
    period_end: str,
    stats: dict,
    summary: str,
    model_name: str,
    session_id: str | None = None,
) -> int:
    with sqlite3.connect(DB_PATH) as conn:
        cur = conn.execute(
            """
            INSERT INTO insight_snapshots (
                device_id, period_start, period_end, stats_json, summary,
                model_name, created_at, session_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                device_id,
                period_start,
                period_end,
                json.dumps(stats, ensure_ascii=False),
                summary,
                model_name,
                datetime.now(timezone.utc).isoformat(),
                session_id,
            ),
        )
        return cur.lastrowid


def get_recent_insights(device_id: str, limit: int = 5) -> list[dict]:
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT * FROM insight_snapshots WHERE device_id = ? ORDER BY id DESC LIMIT ?",
            (device_id, limit),
        ).fetchall()
        items = []
        for row in rows:
            item = dict(row)
            item["stats"] = json.loads(item.pop("stats_json"))
            items.append(item)
        return items


def get_latest_insight_for_session(session_id: str) -> dict | None:
    if not session_id:
        return None
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            """
            SELECT * FROM insight_snapshots
            WHERE session_id = ?
            ORDER BY id DESC
            LIMIT 1
            """,
            (session_id,),
        ).fetchone()
        if not row:
            return None
        item = dict(row)
        item["stats"] = json.loads(item.pop("stats_json"))
        return item
