from __future__ import annotations
import time
from pathlib import Path
from typing import Literal, Optional

from fastapi import APIRouter, Body, HTTPException
from pydantic import BaseModel, Field

import config
import firebase_client
import history_store
import insight_service
import llm_service
import notification_governor as governor
import processing
import rules_store
import session_manager

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
    rules = processing.get_rules_public()
    return {
        **rules,
        **governor.sitting_demo_public(rules),
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


class SittingDemoSettings(BaseModel):
    demo_mode: bool
    demo_max_minutes: int | None = Field(default=None, ge=1, le=20)


@router.put("/settings/sitting-demo")
def update_sitting_demo(settings: SittingDemoSettings):
    """Toggle the demo sitting threshold (default 3 min). Does not re-enable too_long."""
    rules = rules_store.update_sitting_demo(settings.demo_mode, settings.demo_max_minutes)
    sitting = rules.get("sitting_minutes") or {}
    return {
        "demo_mode": bool(sitting.get("demo_mode")),
        "demo_max_minutes": int(sitting.get("demo_max_minutes") or 3),
        "sitting_threshold_sec": int(governor.sitting_threshold_sec(rules)),
        "max_continuous": sitting.get("max_continuous", 20),
        "too_long_enabled": bool(sitting.get("too_long_enabled")),
    }


@router.get("/sensor")
def get_sensor():
    """Read-only snapshot from session_manager. Never process_reading with side effects."""
    try:
        return session_manager.get_manager().snapshot()
    except FileNotFoundError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.get("/advice")
def get_advice(limit: int = 5):
    try:
        return {"items": firebase_client.get_advice_list(limit=limit)}
    except FileNotFoundError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


class AnalyzeRequest(BaseModel):
    mode: Literal["explain", "advice"] = "advice"


@router.post("/analyze")
def analyze_now(req: AnalyzeRequest | None = Body(default=None)):
    """Manual Get Advice / Explain — uses the session snapshot (pure reading). No auto-governor."""
    global _last_analyze_at
    mode = (req.mode if req else "advice") or "advice"
    if mode not in ("explain", "advice"):
        mode = "advice"

    now = time.time()
    cooldown = _analyze_cooldown_sec()
    if now - _last_analyze_at < cooldown:
        wait = int(cooldown - (now - _last_analyze_at))
        raise HTTPException(status_code=429, detail=f"Wait {wait}s before analyzing again")

    try:
        mgr = session_manager.get_manager()
        snap = mgr.snapshot()
        reading = snap.get("reading")
        if reading is None:
            raw = firebase_client.read_sensor_raw()
            sitting = (snap.get("session") or {}).get("sitting_minutes")
            reading = processing.process_reading(
                raw,
                default_device_id=config.DEFAULT_DEVICE_ID,
                sitting_minutes=sitting,
            )
    except FileNotFoundError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    if reading is None:
        raise HTTPException(status_code=404, detail="No sensor data in Firebase")

    if mode != "explain" and not reading.get("llm_eligible") and not reading.get("flag_set"):
        return {
            "status": "skipped",
            "mode": mode,
            "message": "Readings normal — LLM not needed",
            "reading": reading,
            "session": snap.get("session"),
        }

    if isinstance(snap.get("session"), dict) and snap["session"].get("session_id"):
        reading["session_id"] = reading.get("session_id") or snap["session"]["session_id"]

    advice = llm_service.analyze_warning(reading, mode=mode)
    advice_id = firebase_client.push_advice(advice, reading)
    _last_analyze_at = now

    return {
        "status": "ok",
        "mode": mode,
        "advice_id": advice_id,
        "reading": reading,
        "advice": advice,
        "session": snap.get("session"),
    }

@router.post("/insights")
def create_insight(session_id: Optional[str] = None):
    """Aggregate the current (or last) session from history_store and ask the LLM to narrate it."""
    global _last_insight_at

    now = time.time()
    cooldown = _insight_cooldown_sec()
    if now - _last_insight_at < cooldown:
        wait = int(cooldown - (now - _last_insight_at))
        raise HTTPException(status_code=429, detail=f"Wait {wait}s before generating another insight")

    mgr = session_manager.get_manager()
    sid = session_id or mgr.session_id or mgr.last_session_id
    if not sid:
        raise HTTPException(status_code=404, detail="No session to summarize yet")

    result = insight_service.generate_insight(config.DEFAULT_DEVICE_ID, session_id=sid)
    if result is None:
        raise HTTPException(
            status_code=404,
            detail=f"Not enough history yet (need at least {config.INSIGHT_MIN_READINGS} readings in this session)",
        )
    _last_insight_at = now
    return result


@router.get("/insights")
def get_insights(limit: int = 5):
    return {"items": history_store.get_recent_insights(config.DEFAULT_DEVICE_ID, limit=limit)}


@router.get("/session")
def get_session():
    """Session snapshot: state, severity, snooze/DND, corrected counts, pending spoken_line."""
    return session_manager.get_manager().snapshot()["session"]


class DndRequest(BaseModel):
    enabled: bool | None = None


@router.post("/session/snooze")
def session_snooze():
    return session_manager.get_manager().snooze()


@router.post("/session/ack")
def session_ack():
    return session_manager.get_manager().ack()


@router.post("/session/dnd")
def session_dnd(req: DndRequest = DndRequest()):
    return session_manager.get_manager().set_dnd(req.enabled)


@router.post("/session/break")
def session_break():
    return session_manager.get_manager().enter_break()


@router.post("/session/break/end")
def session_resume():
    return session_manager.get_manager().leave_break()


@router.post("/session/ready")
def session_ready():
    """Wizard step 4: if still calibrating, enter monitoring. 20s grace remains fallback."""
    return session_manager.get_manager().ready()


@router.get("/session/timeline")
def session_timeline(minutes: int = 10, session_id: Optional[str] = None):
    """Risk-level strip for the current or last session. Empty buckets if shorter than `minutes`."""
    minutes = max(1, min(int(minutes or 10), 60))
    mgr = session_manager.get_manager()
    sid = session_id or mgr.session_id or mgr.last_session_id
    if not sid:
        raise HTTPException(status_code=404, detail="No session to chart yet")
    result = insight_service.build_session_timeline(sid, minutes=minutes, bucket_sec=10)
    if result is None:
        raise HTTPException(status_code=404, detail="Session not found")
    return result


@router.get("/session/report")
def session_report(session_id: Optional[str] = None):
    """Printable session report from stored insight stats + last advice. Does not call the LLM again."""
    mgr = session_manager.get_manager()
    sid = session_id or mgr.session_id or mgr.last_session_id
    if not sid:
        raise HTTPException(status_code=404, detail="No session to report yet")
    result = insight_service.build_session_report(config.DEFAULT_DEVICE_ID, sid)
    if result is None:
        raise HTTPException(status_code=404, detail="Session not found")
    return result


@router.post("/calibrate/head-pose")
def calibrate_head_pose():
    """Trigger zero-angle calibration on AI tracking module via Firebase."""
    try:
        ok = firebase_client.trigger_head_pose_calibration()
        if not ok:
            raise HTTPException(status_code=502, detail="Failed to write calibration request to Firebase")
        return {"status": "ok", "message": "Head pose zero reference calibration requested"}
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


class DistanceCalibReq(BaseModel):
    known_distance_cm: float = Field(default=50.0, ge=10.0, le=200.0)


@router.post("/calibrate/distance")
def calibrate_distance(req: DistanceCalibReq):
    """Trigger camera distance calibration factor K via Firebase."""
    try:
        ok = firebase_client.trigger_distance_calibration(req.known_distance_cm)
        if not ok:
            raise HTTPException(status_code=502, detail="Failed to write distance calibration request to Firebase")
        return {"status": "ok", "message": f"Distance calibration requested at {req.known_distance_cm} cm"}
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


class TrackingStartRequest(BaseModel):
    source: str = "0"


@router.post("/tracking/start")
def start_tracking_api(req: TrackingStartRequest):
    """Start camera + open (or attach to) the device session."""
    return session_manager.get_manager().start(source=req.source)


@router.post("/tracking/stop")
def stop_tracking_api():
    """Stop Stream = Stop Session: close the row, generate insight, LED off."""
    return session_manager.get_manager().stop()


from fastapi.responses import StreamingResponse
import urllib.request

@router.get("/video_feed")
def video_feed():
    """Proxy live processed AI camera frames (with 3D pose axes & landmarks) to web dashboard."""
    def frame_generator():
        import socket
        active_port = None
        for port in [8089, 8090, 8091]:
            try:
                with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                    active_port = port
                    break
            except Exception:
                pass

        target_port = active_port or 8089
        url = f"http://127.0.0.1:{target_port}/stream"
        try:
            req = urllib.request.Request(url)
            with urllib.request.urlopen(req, timeout=10.0) as resp:
                while True:
                    chunk = resp.read(1024 * 8)
                    if not chunk:
                        break
                    yield chunk
        except Exception:
            pass

    return StreamingResponse(
        frame_generator(),
        media_type="multipart/x-mixed-replace; boundary=frame"
    )
