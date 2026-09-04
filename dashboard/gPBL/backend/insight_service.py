from __future__ import annotations
import json
from datetime import datetime, timedelta, timezone

import config
import firebase_client
import history_store
import llm_service
import notification_governor as governor

DEFAULT_SYSTEM_PROMPT = (
    'You are the AI coach for "PostureCare". You are given aggregated stats '
    "from a short monitoring window (not a single reading) — averages, "
    "value ranges, risk-level distribution, and counts of detector events "
    "that fired during the window.\n\n"
    "Write a brief narrative: what pattern happened across the window, why "
    "it matters, and 2-3 concrete next actions. Reason about trends and "
    "correlations between signals — do not just restate the numbers."
)


# Calibrating samples are persisted for audit but are not trusted for live
# advice (llm_eligible=False). Session summaries must use the same bar —
# otherwise untrusted rows still shape avg/min-max/risk_level_pct.
_UNTRUSTED_STATES = frozenset({"calibrating"})


def _avg(values: list[float]) -> float | None:
    return round(sum(values) / len(values), 1) if values else None


def _trusted_for_session_summary(row: dict) -> bool:
    state = row.get("state")
    if state:
        return state not in _UNTRUSTED_STATES
    # Legacy rows with no state column: sitting_minutes stays 0 through
    # calibrating (exposure only accumulates in monitoring). Also drops the
    # first monitoring minute — coarse, but better than mixing untrusted
    # samples into the LLM session stats.
    return (row.get("sitting_minutes") or 0) > 0


def _summarize_rows(
    device_id: str,
    rows: list[dict],
    window_minutes: float | None = None,
    min_readings: int | None = None,
) -> dict | None:
    need = config.INSIGHT_MIN_READINGS if min_readings is None else min_readings
    if len(rows) < need:
        return None

    distances = [r["distance_cm"] for r in rows if r["distance_cm"] is not None]
    lux_values = [r["brightness_lux"] for r in rows if r["brightness_lux"] is not None]
    pitches = [r.get("head_pitch_deg") for r in rows if r.get("head_pitch_deg") is not None]
    rolls = [r.get("head_roll_deg") for r in rows if r.get("head_roll_deg") is not None]
    yaws = [r.get("head_yaw_deg") for r in rows if r.get("head_yaw_deg") is not None]
    blink_rates = [r.get("blink_rate_bpm") for r in rows if r.get("blink_rate_bpm") is not None]

    risk_counts = {"normal": 0, "warning": 0, "high": 0}
    event_counts: dict[str, int] = {}
    for row in rows:
        risk_counts[row["risk_level"]] = risk_counts.get(row["risk_level"], 0) + 1
        for event in json.loads(row["events_json"] or "[]"):
            flag = event.get("flag")
            if flag:
                event_counts[flag] = event_counts.get(flag, 0) + 1

    total = len(rows)
    period_start = rows[0]["ts"]
    period_end = rows[-1]["ts"]
    if window_minutes is None:
        try:
            start_dt = datetime.fromisoformat(str(period_start).replace("Z", "+00:00"))
            end_dt = datetime.fromisoformat(str(period_end).replace("Z", "+00:00"))
            window_minutes = max(0.0, (end_dt - start_dt).total_seconds() / 60.0)
        except ValueError:
            window_minutes = None

    return {
        "device_id": device_id,
        "session_id": rows[0].get("session_id"),
        "window_minutes": round(window_minutes, 1) if window_minutes is not None else None,
        "reading_count": total,
        "period_start": period_start,
        "period_end": period_end,
        "distance_cm": {
            "avg": _avg(distances),
            "min": min(distances, default=None),
            "max": max(distances, default=None),
        },
        "brightness_lux": {
            "avg": _avg(lux_values),
            "min": min(lux_values, default=None),
            "max": max(lux_values, default=None),
        },
        "head_pitch_deg": {
            "avg": _avg(pitches),
            "min": min(pitches, default=None),
            "max": max(pitches, default=None),
        },
        "head_roll_deg": {
            "avg": _avg(rolls),
            "min": min(rolls, default=None),
            "max": max(rolls, default=None),
        },
        "head_yaw_deg": {
            "avg": _avg(yaws),
            "min": min(yaws, default=None),
            "max": max(yaws, default=None),
        },
        "blink_rate_bpm": {
            "avg": _avg(blink_rates),
            "min": min(blink_rates, default=None),
            "max": max(blink_rates, default=None),
        },
        "risk_level_pct": {k: round(v / total * 100, 1) for k, v in risk_counts.items()},
        "event_counts": event_counts,
    }


def build_session_summary(
    device_id: str,
    session_id: str,
    min_readings: int | None = None,
) -> dict | None:
    rows = [
        row
        for row in history_store.get_readings_for_session(session_id)
        if _trusted_for_session_summary(row)
    ]
    return _summarize_rows(device_id, rows, min_readings=min_readings)


def generate_insight(
    device_id: str,
    session_id: str | None = None,
    window_minutes: int | None = None,
    extra_stats: dict | None = None,
) -> dict | None:
    del window_minutes  # Wave 1: insights are session-scoped, never "last N minutes of all rows"
    if not session_id:
        return None

    summary = build_session_summary(device_id, session_id)
    if summary is None:
        return None
    if extra_stats:
        summary.update(extra_stats)

    user_prompt = (
        "Session summary (aggregated over this session, not a sliding wall-clock window):\n"
        f"{json.dumps(summary, ensure_ascii=False, indent=2)}"
    )
    advice = llm_service.analyze_summary(DEFAULT_SYSTEM_PROMPT, user_prompt)

    history_store.insert_insight(
        device_id=device_id,
        period_start=summary["period_start"],
        period_end=summary["period_end"],
        stats=summary,
        summary=advice["summary"],
        model_name=advice["model_name"],
        session_id=session_id,
    )
    return {"summary": summary, "advice": advice}


_RISK_RANK = {"normal": 0, "warning": 1, "high": 2}


def _parse_iso(value) -> datetime | None:
    if not value or not isinstance(value, str):
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _corrected_label_from_session(session: dict | None) -> str | None:
    if not session:
        return None
    gov = governor.load(session.get("governor_json"))
    corrected = int(gov.get("corrected_count") or 0)
    reminder = int(gov.get("reminder_count") or 0)
    return f"Corrected {corrected}/{reminder} reminders"


def build_session_timeline(
    session_id: str,
    minutes: int = 10,
    bucket_sec: int = 10,
    now: datetime | None = None,
) -> dict | None:
    """~10s buckets from session start, padded to `minutes`. Color = risk_level."""
    session = history_store.get_session(session_id)
    if session is None:
        return None
    started = _parse_iso(session.get("started_at"))
    if started is None:
        return None
    now = now or datetime.now(timezone.utc)
    ended = _parse_iso(session.get("ended_at"))
    horizon = started + timedelta(minutes=minutes)
    data_until = min(now, horizon) if ended is None else min(ended, now, horizon)
    n_buckets = max(1, int(round(minutes * 60 / bucket_sec)))

    readings = history_store.get_readings_for_session(session_id)
    ranked: list[tuple[datetime, str]] = []
    for row in readings:
        ts = _parse_iso(str(row.get("ts") or ""))
        level = str(row.get("risk_level") or "")
        if ts is None or level not in _RISK_RANK:
            continue
        ranked.append((ts, level))

    buckets = []
    for i in range(n_buckets):
        t0 = started + timedelta(seconds=i * bucket_sec)
        t1 = t0 + timedelta(seconds=bucket_sec)
        offset_sec = i * bucket_sec
        if t0 >= data_until:
            buckets.append(
                {
                    "offset_sec": offset_sec,
                    "t": t0.isoformat(),
                    "risk_level": None,
                }
            )
            continue
        best = None
        best_rank = -1
        for ts, level in ranked:
            if t0 <= ts < t1:
                rank = _RISK_RANK[level]
                if rank >= best_rank:
                    best = level
                    best_rank = rank
        buckets.append(
            {
                "offset_sec": offset_sec,
                "t": t0.isoformat(),
                "risk_level": best,
            }
        )

    return {
        "session_id": session_id,
        "minutes": minutes,
        "bucket_sec": bucket_sec,
        "started_at": session.get("started_at"),
        "ended_at": session.get("ended_at"),
        "buckets": buckets,
    }


def build_session_report(device_id: str, session_id: str) -> dict | None:
    """Stats + last insight/advice. Never calls the LLM if an insight already exists."""
    session = history_store.get_session(session_id)
    if session is None:
        return None

    stored = history_store.get_latest_insight_for_session(session_id)
    summary = None
    if stored and isinstance(stored.get("stats"), dict):
        summary = dict(stored["stats"])
    else:
        summary = build_session_summary(device_id, session_id, min_readings=1)

    exposure_sec = float(session.get("exposure_sec") or 0.0)
    corrected_label = None
    if isinstance(summary, dict):
        corrected_label = summary.get("corrected_label")
        if summary.get("exposure_sec") is None:
            summary = {**summary, "exposure_sec": round(exposure_sec, 1)}
    if not corrected_label:
        corrected_label = _corrected_label_from_session(session)

    stats = {
        "risk_level_pct": (summary or {}).get("risk_level_pct") or {"normal": 0, "warning": 0, "high": 0},
        "event_counts": (summary or {}).get("event_counts") or {},
        "events": (summary or {}).get("event_counts") or {},
        "exposure_sec": round(float((summary or {}).get("exposure_sec") or exposure_sec), 1),
        "corrected_label": corrected_label,
        "corrected_count": (summary or {}).get("corrected_count"),
        "reminder_count": (summary or {}).get("reminder_count"),
        "reading_count": (summary or {}).get("reading_count") or 0,
        "window_minutes": (summary or {}).get("window_minutes"),
        "period_start": (summary or {}).get("period_start") or session.get("started_at"),
        "period_end": (summary or {}).get("period_end") or session.get("ended_at"),
        "distance_cm": (summary or {}).get("distance_cm"),
        "brightness_lux": (summary or {}).get("brightness_lux"),
    }

    insight = None
    if stored:
        insight = {
            "summary": stored.get("summary"),
            "model_name": stored.get("model_name"),
            "created_at": stored.get("created_at"),
            "period_start": stored.get("period_start"),
            "period_end": stored.get("period_end"),
        }

    advice = firebase_client.get_latest_advice_for_session(session_id)

    return {
        "session_id": session_id,
        "session": {
            "device_id": session.get("device_id") or device_id,
            "state": session.get("state"),
            "source": session.get("source"),
            "started_at": session.get("started_at"),
            "ended_at": session.get("ended_at"),
            "exposure_sec": round(exposure_sec, 1),
        },
        "stats": stats,
        "insight": insight,
        "advice": advice,
    }
