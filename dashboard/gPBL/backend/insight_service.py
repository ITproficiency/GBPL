from __future__ import annotations
import json
from datetime import datetime, timedelta, timezone

import config
import history_store
import llm_service

DEFAULT_SYSTEM_PROMPT = (
    'You are the AI coach for "PostureCare". You are given aggregated stats '
    "from a short monitoring window (not a single reading) — averages, "
    "value ranges, risk-level distribution, and counts of detector events "
    "that fired during the window.\n\n"
    "Write a brief narrative: what pattern happened across the window, why "
    "it matters, and 2-3 concrete next actions. Reason about trends and "
    "correlations between signals — do not just restate the numbers."
)


def _avg(values: list[float]) -> float | None:
    return round(sum(values) / len(values), 1) if values else None


def build_window_summary(device_id: str, window_minutes: int) -> dict | None:
    since = (datetime.now(timezone.utc) - timedelta(minutes=window_minutes)).isoformat()
    rows = history_store.get_readings_since(device_id, since)
    if len(rows) < config.INSIGHT_MIN_READINGS:
        # Too few samples to aggregate meaningfully — summarizing 1-2 readings
        # is just per-reading reasoning wearing a window's clothing.
        return None

    distances = [r["distance_cm"] for r in rows if r["distance_cm"] is not None]
    lux_values = [r["brightness_lux"] for r in rows if r["brightness_lux"] is not None]

    risk_counts = {"normal": 0, "warning": 0, "high": 0}
    event_counts: dict[str, int] = {}
    for row in rows:
        risk_counts[row["risk_level"]] = risk_counts.get(row["risk_level"], 0) + 1
        for event in json.loads(row["events_json"] or "[]"):
            event_counts[event["flag"]] = event_counts.get(event["flag"], 0) + 1

    total = len(rows)
    return {
        "device_id": device_id,
        "window_minutes": window_minutes,
        "reading_count": total,
        "period_start": rows[0]["ts"],
        "period_end": rows[-1]["ts"],
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
        "risk_level_pct": {k: round(v / total * 100, 1) for k, v in risk_counts.items()},
        "event_counts": event_counts,
    }


def generate_insight(device_id: str, window_minutes: int | None = None) -> dict | None:
    window_minutes = window_minutes or config.INSIGHT_WINDOW_MINUTES
    summary = build_window_summary(device_id, window_minutes)
    if summary is None:
        return None

    user_prompt = (
        "Session summary (aggregated over the window, not a single reading):\n"
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
    )
    return {"summary": summary, "advice": advice}
