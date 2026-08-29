"""Firebase read/write only — no business logic here (see processing.py)."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import firebase_admin
from firebase_admin import credentials, db

import config

_app: firebase_admin.App | None = None


def init_firebase() -> firebase_admin.App:
    global _app
    if _app is not None:
        return _app
    cred = credentials.Certificate(config.FIREBASE_CREDENTIALS)
    _app = firebase_admin.initialize_app(cred, {"databaseURL": config.FIREBASE_DATABASE_URL})
    return _app


def get_ref(path: str = ""):
    init_firebase()
    return db.reference(path)


def read_sensor_raw() -> Any:
    return get_ref(config.FIREBASE_SENSOR_PATH).get()


def get_advice_list(limit: int = 5) -> list[dict]:
    data = get_ref(config.FIREBASE_ADVICE_PATH).get() or {}
    if not isinstance(data, dict):
        return []
    items = sorted(data.items(), key=lambda x: x[0], reverse=True)[:limit]
    return [{"id": k, **v} for k, v in items if isinstance(v, dict)]


def push_led_state(is_on: bool) -> None:
    """Single node (not a log) — firmware polls this to drive the status LED:
    true = light on (elevated risk), false = light off (normal)."""
    get_ref(config.FIREBASE_LED_PATH).set(is_on)


def push_advice(advice: dict, reading: dict) -> str:
    payload = {
        "device_id": reading["device_id"],
        "summary": advice["summary"],
        "recommendations": advice["recommendations"],
        "model_name": advice["model_name"],
        "risk_level": reading["risk_level"],
        "distance_cm": reading["distance_cm"],
        "brightness_lux": reading["brightness_lux"],
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    ref = get_ref(config.FIREBASE_ADVICE_PATH).push(payload)
    return ref.key or ""
