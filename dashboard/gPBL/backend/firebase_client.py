from __future__ import annotations
import urllib.request
import ssl
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
        ctx = ssl._create_unverified_context()
        with urllib.request.urlopen(req, timeout=3.0, context=ctx) as resp:
            return json.loads(resp.read().decode('utf-8'))
    except Exception:
        return None


def _rest_put(path: str, data: Any) -> bool:
    try:
        url = f"{config.FIREBASE_DATABASE_URL.rstrip('/')}/{path.lstrip('/')}.json"
        payload = json.dumps(data, ensure_ascii=False).encode('utf-8')
        req = urllib.request.Request(url, data=payload, method='PUT', headers={'Content-Type': 'application/json'})
        ctx = ssl._create_unverified_context()
        with urllib.request.urlopen(req, data=payload, method='PUT', headers={'Content-Type': 'application/json'}) as resp:
            return True
    except Exception:
        return False


def _rest_post(path: str, data: Any) -> str:
    try:
        url = f"{config.FIREBASE_DATABASE_URL.rstrip('/')}/{path.lstrip('/')}.json"
        payload = json.dumps(data, ensure_ascii=False).encode('utf-8')
        req = urllib.request.Request(url, data=payload, method='POST', headers={'Content-Type': 'application/json'})
        ctx = ssl._create_unverified_context()
        with urllib.request.urlopen(req, timeout=3.0, context=ctx) as resp:
            res_data = json.loads(resp.read().decode('utf-8'))
            return res_data.get('name', '') if isinstance(res_data, dict) else ''
    except Exception:
        return ""


def read_sensor_raw() -> Any:
    """Fetch live RTDB metrics directly via REST API for zero-lag realtime synchronization."""
    sensor_data = _rest_get(config.FIREBASE_SENSOR_PATH) or {}
    ai_data = _rest_get("ai_data") or {}

    raw = {}
    if isinstance(sensor_data, dict):
        raw.update(sensor_data)
    if isinstance(ai_data, dict):
        raw.update(ai_data)

    return raw if raw else None


_in_memory_advice_list: list[dict] = []


def get_advice_list(limit: int = 5) -> list[dict]:
    data = _rest_get(config.FIREBASE_ADVICE_PATH) or {}
    combined = []
    if isinstance(data, dict):
        for k, v in data.items():
            if isinstance(v, dict):
                combined.append({"id": k, **v})
    
    for local_item in _in_memory_advice_list:
        if not any(x.get("id") == local_item.get("id") for x in combined):
            combined.append(local_item)
            
    combined.sort(key=lambda x: str(x.get("created_at", x.get("id", ""))), reverse=True)
    return combined[:limit]


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
    import time
    item_id = f"adv_{int(time.time()*1000)}"
    payload = {
        "device_id": reading.get("device_id", "esp32_001"),
        "summary": advice.get("summary", "PostureCare Advice"),
        "recommendations": advice.get("recommendations", []),
        "model_name": advice.get("model_name", "mock-advisor-v1"),
        "risk_level": reading.get("risk_level", "warning"),
        "distance_cm": reading.get("distance_cm"),
        "brightness_lux": reading.get("brightness_lux"),
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    _in_memory_advice_list.insert(0, {"id": item_id, **payload})
    
    global _use_rest_fallback
    if not _use_rest_fallback:
        try:
            ref = get_ref(config.FIREBASE_ADVICE_PATH)
            if ref:
                res = ref.push(payload)
                return res.key or item_id
        except Exception:
            _use_rest_fallback = True
    _rest_post(config.FIREBASE_ADVICE_PATH, payload)
    return item_id


def trigger_head_pose_calibration() -> bool:
    """Send calibration request to Firebase RTDB (/ai_data/calibrate_pose_req and /ai_data/calibrate_req)."""
    global _use_rest_fallback
    req_timestamp = datetime.now(timezone.utc).isoformat()
    if not _use_rest_fallback:
        try:
            ref_pose = get_ref("ai_data/calibrate_pose_req")
            if ref_pose:
                ref_pose.set(req_timestamp)
            ref_req = get_ref("ai_data/calibrate_req")
            if ref_req:
                ref_req.set(req_timestamp)
            return True
        except Exception:
            _use_rest_fallback = True
    _rest_put("ai_data/calibrate_pose_req", req_timestamp)
    return _rest_put("ai_data/calibrate_req", req_timestamp)


def trigger_distance_calibration(distance_cm: float = 50.0) -> bool:
    """Send distance calibration request to Firebase RTDB (/ai_data/calibrate_dist_req)."""
    global _use_rest_fallback
    payload = {
        "known_distance_cm": float(distance_cm),
        "req_time": datetime.now(timezone.utc).isoformat(),
    }
    if not _use_rest_fallback:
        try:
            ref = get_ref("ai_data/calibrate_dist_req")
            if ref:
                ref.set(payload)
                return True
        except Exception:
            _use_rest_fallback = True
    return _rest_put("ai_data/calibrate_dist_req", payload)


def push_calibration_request(calib_type: str = "pose") -> bool:
    if calib_type == "distance":
        return trigger_distance_calibration(50.0)
    return trigger_head_pose_calibration()


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
