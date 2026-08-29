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
    created_at      TEXT NOT NULL
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
"""


def _init_db() -> None:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(DB_PATH) as conn:
        conn.executescript(_SCHEMA)


_init_db()


def insert_reading(reading: dict) -> None:
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            """
            INSERT INTO readings (
                device_id, ts, distance_cm, light_adc, brightness_lux,
                sitting_minutes, blink_rate_bpm,
                head_pitch_deg, head_roll_deg, head_yaw_deg,
                risk_score, risk_level, events_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                reading["device_id"],
                reading["timestamp"],
                reading["distance_cm"],
                reading["light_adc"],
                reading["brightness_lux"],
                reading["sitting_minutes"],
                reading.get("blink_rate_bpm"),
                reading.get("head_pitch_deg"),
                reading.get("head_roll_deg"),
                reading.get("head_yaw_deg"),
                reading["risk_score"],
                reading["risk_level"],
                json.dumps(reading.get("events", []), ensure_ascii=False),
                datetime.now(timezone.utc).isoformat(),
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


def insert_insight(
    device_id: str,
    period_start: str,
    period_end: str,
    stats: dict,
    summary: str,
    model_name: str,
) -> int:
    with sqlite3.connect(DB_PATH) as conn:
        cur = conn.execute(
            """
            INSERT INTO insight_snapshots (
                device_id, period_start, period_end, stats_json, summary, model_name, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                device_id,
                period_start,
                period_end,
                json.dumps(stats, ensure_ascii=False),
                summary,
                model_name,
                datetime.now(timezone.utc).isoformat(),
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
