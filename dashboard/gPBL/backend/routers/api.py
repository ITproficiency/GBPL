import time
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

import config
import firebase_client
import history_store
import insight_service
import llm_service
import processing
import rules_store

router = APIRouter(prefix="/api", tags=["api"])

MIN_COOLDOWN_SEC = 10
MAX_COOLDOWN_SEC = 600

_last_analyze_at: float = 0
_last_insight_at: float = 0

SOUNDS_DIR = Path(__file__).resolve().parent.parent / "static" / "sounds"
SOUND_EXTENSIONS = {".mp3", ".ogg", ".wav", ".m4a"}


@router.get("/sounds")
def get_sounds():
    """List ambient sound files from static/sounds/ — drop a file in, it shows up.
    No DB/config entry needed; the dashboard's sound picker is built from this."""
    if not SOUNDS_DIR.exists():
        return {"sounds": []}
    sounds = [
        {"id": f.stem, "label": f.stem.replace("_", " ").replace("-", " ").title(), "file": f.name}
        for f in sorted(SOUNDS_DIR.iterdir())
        if f.is_file() and f.suffix.lower() in SOUND_EXTENSIONS
    ]
    return {"sounds": sounds}


def _analyze_cooldown_sec() -> int:
    return processing.get_rules().get("llm", {}).get("analyze_cooldown_sec", config.ANALYZE_COOLDOWN_SEC)


def _insight_cooldown_sec() -> int:
    return processing.get_rules().get("insight", {}).get("cooldown_sec", config.INSIGHT_COOLDOWN_SEC)


@router.get("/rules")
def get_rules():
    """Public rules — same source as risk evaluation (for dashboard display)."""
    return {
        **processing.get_rules_public(),
        "insight_window_minutes": config.INSIGHT_WINDOW_MINUTES,
        "insight_min_readings": config.INSIGHT_MIN_READINGS,
        "analyze_cooldown_sec": _analyze_cooldown_sec(),
        "insight_cooldown_sec": _insight_cooldown_sec(),
    }


class CooldownSettings(BaseModel):
    analyze_cooldown_sec: int = Field(ge=MIN_COOLDOWN_SEC, le=MAX_COOLDOWN_SEC)
    insight_cooldown_sec: int = Field(ge=MIN_COOLDOWN_SEC, le=MAX_COOLDOWN_SEC)


@router.put("/settings/cooldowns")
def update_cooldowns(settings: CooldownSettings):
    """Let the dashboard configure how often Advice/Insight may call the LLM,
    instead of a fixed value baked into the backend only an admin could change."""
    rules_store.update_cooldowns(settings.analyze_cooldown_sec, settings.insight_cooldown_sec)
    return {
        "analyze_cooldown_sec": settings.analyze_cooldown_sec,
        "insight_cooldown_sec": settings.insight_cooldown_sec,
    }


@router.get("/sensor")
def get_sensor():
    try:
        raw = firebase_client.read_sensor_raw()
        reading = processing.process_reading(raw, default_device_id=config.DEFAULT_DEVICE_ID)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    if reading is None:
        return JSONResponse(content=None, status_code=200)
    return reading


@router.get("/advice")
def get_advice(limit: int = 5):
    try:
        return {"items": firebase_client.get_advice_list(limit=limit)}
    except FileNotFoundError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.post("/analyze")
def analyze_now():
    """Read Firebase → process_reading() → LLM if llm_eligible."""
    global _last_analyze_at

    now = time.time()
    cooldown = _analyze_cooldown_sec()
    if now - _last_analyze_at < cooldown:
        wait = int(cooldown - (now - _last_analyze_at))
        raise HTTPException(status_code=429, detail=f"Wait {wait}s before analyzing again")

    try:
        raw = firebase_client.read_sensor_raw()
        reading = processing.process_reading(raw, default_device_id=config.DEFAULT_DEVICE_ID)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    if reading is None:
        raise HTTPException(status_code=404, detail="No sensor data in Firebase")

    if not reading["llm_eligible"]:
        return {
            "status": "skipped",
            "message": "Readings normal — LLM not needed",
            "reading": reading,
        }

    advice = llm_service.analyze_warning(reading)
    advice_id = firebase_client.push_advice(advice, reading)
    _last_analyze_at = now

    return {
        "status": "ok",
        "advice_id": advice_id,
        "reading": reading,
        "advice": advice,
    }


@router.post("/insights")
def create_insight(window_minutes: int | None = None):
    """Aggregate the recent window from history_store and ask the LLM to narrate it."""
    global _last_insight_at

    now = time.time()
    cooldown = _insight_cooldown_sec()
    if now - _last_insight_at < cooldown:
        wait = int(cooldown - (now - _last_insight_at))
        raise HTTPException(status_code=429, detail=f"Wait {wait}s before generating another insight")

    result = insight_service.generate_insight(config.DEFAULT_DEVICE_ID, window_minutes)
    if result is None:
        raise HTTPException(
            status_code=404,
            detail=f"Not enough history yet (need at least {config.INSIGHT_MIN_READINGS} readings in the window)",
        )
    _last_insight_at = now
    return result


@router.get("/insights")
def get_insights(limit: int = 5):
    return {"items": history_store.get_recent_insights(config.DEFAULT_DEVICE_ID, limit=limit)}
