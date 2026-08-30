from __future__ import annotations
import urllib.request
import json
from datetime import datetime, timezone
from typing import Any

import firebase_admin
from firebase_admin import credentials, db

import config

_app: firebase_admin.App | None = None
_use_rest_fallback: bool = False


def init_firebase():
    global _app, _use_rest_fallback
    if _use_rest_fallback:
        return None
    if _app is not None:
        return _app
    try:
        cred = credentials.Certificate(config.FIREBASE_CREDENTIALS)
        _app = firebase_admin.initialize_app(cred, {"databaseURL": config.FIREBASE_DATABASE_URL})
        return _app
    except Exception:
        _use_rest_fallback = True
        return None


def get_ref(path: str = ""):
    app = init_firebase()
    if app and not _use_rest_fallback:
        return db.reference(path)
    return None


def _rest_get(path: str) -> Any:
    try:
        url = f"{config.FIREBASE_DATABASE_URL.rstrip('/')}/{path.lstrip('/')}.json"
        req = urllib.request.Request(url, headers={'User-Agent': 'PostureCare-Dashboard'})
        with urllib.request.urlopen(req, timeout=3.0) as resp:
            return json.loads(resp.read().decode('utf-8'))
    except Exception:
        return None


def _rest_put(path: str, data: Any) -> bool:
    try:
        url = f"{config.FIREBASE_DATABASE_URL.rstrip('/')}/{path.lstrip('/')}.json"
        payload = json.dumps(data, ensure_ascii=False).encode('utf-8')
        req = urllib.request.Request(url, data=payload, method='PUT', headers={'Content-Type': 'application/json'})
        with urllib.request.urlopen(req, timeout=3.0) as resp:
            return True
    except Exception:
        return False


def _rest_post(path: str, data: Any) -> str:
    try:
        url = f"{config.FIREBASE_DATABASE_URL.rstrip('/')}/{path.lstrip('/')}.json"
        payload = json.dumps(data, ensure_ascii=False).encode('utf-8')
        req = urllib.request.Request(url, data=payload, method='POST', headers={'Content-Type': 'application/json'})
        with urllib.request.urlopen(req, timeout=3.0) as resp:
            res_data = json.loads(resp.read().decode('utf-8'))
            return res_data.get('name', '') if isinstance(res_data, dict) else ''
    except Exception:
        return ""


def read_sensor_raw() -> Any:
    global _use_rest_fallback
    sensor_data = None
    ai_data = None

    if not _use_rest_fallback:
        try:
            ref = get_ref(config.FIREBASE_SENSOR_PATH)
            if ref:
                sensor_data = ref.get()
            ai_ref = get_ref("ai_data")
            if ai_ref:
                ai_data = ai_ref.get()
        except Exception:
            _use_rest_fallback = True

    if sensor_data is None:
        sensor_data = _rest_get(config.FIREBASE_SENSOR_PATH) or {}
    if ai_data is None:
        ai_data = _rest_get("ai_data") or {}

    raw = {}
    if isinstance(sensor_data, dict):
        raw.update(sensor_data)
    if isinstance(ai_data, dict):
        raw.update(ai_data)

    return raw if raw else None


def get_advice_list(limit: int = 5) -> list[dict]:
    global _use_rest_fallback
    data = None
    if not _use_rest_fallback:
        try:
            ref = get_ref(config.FIREBASE_ADVICE_PATH)
            if ref:
                data = ref.get()
        except Exception:
            _use_rest_fallback = True

    if data is None:
        data = _rest_get(config.FIREBASE_ADVICE_PATH) or {}

    if not isinstance(data, dict):
        return []
    items = sorted(data.items(), key=lambda x: x[0], reverse=True)[:limit]
    return [{"id": k, **v} for k, v in items if isinstance(v, dict)]


def push_led_state(is_on: bool) -> None:
    global _use_rest_fallback
    if not _use_rest_fallback:
        try:
            ref = get_ref(config.FIREBASE_LED_PATH)
            if ref:
                ref.set(is_on)
                return
        except Exception:
            _use_rest_fallback = True
    _rest_put(config.FIREBASE_LED_PATH, is_on)


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
    global _use_rest_fallback
    if not _use_rest_fallback:
        try:
            ref = get_ref(config.FIREBASE_ADVICE_PATH)
            if ref:
                res = ref.push(payload)
                return res.key or ""
        except Exception:
            _use_rest_fallback = True
    return _rest_post(config.FIREBASE_ADVICE_PATH, payload)


def push_calibration_request(calib_type: str = "pose") -> bool:
    global _use_rest_fallback
    path = "ai_data/calibrate_dist_req" if calib_type == "distance" else "ai_data/calibrate_pose_req"
    timestamp = datetime.now(timezone.utc).isoformat()
    if not _use_rest_fallback:
        try:
            ref = get_ref(path)
            if ref:
                ref.set(timestamp)
                return True
        except Exception:
            _use_rest_fallback = True
    return _rest_put(path, timestamp)


def reset_ai_data() -> bool:
    global _use_rest_fallback
    if not _use_rest_fallback:
        try:
            ref = get_ref("ai_data")
            if ref:
                ref.delete()
                return True
        except Exception:
            _use_rest_fallback = True
    return _rest_put("ai_data", {})

