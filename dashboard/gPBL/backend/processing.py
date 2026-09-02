"""
PostureCare processing — rules from backend/data/rules.json.

process_reading is a pure function: same raw sample in → same dict out.
Sitting time, persistence, LED, and LLM live in session_manager.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import detectors
import rules_store


def get_rules() -> dict:
    return rules_store.get_rules()


def extract_float(data: dict, field_names: list[str]) -> float | None:
    for name in field_names:
        if name in data and data[name] is not None:
            return float(data[name])
    return None


def sanitize_distance(value: float | None) -> float | None:
    """Negative ultrasonic echoes (-1) are missing, not critically close."""
    if value is None or value < 0:
        return None
    return value


def adc_to_lux(adc_value: float, cfg: dict) -> float:
    """Convert 12-bit LDR ADC reading to lux (see docs/light-adc-to-lux.md)."""
    adc_max = float(cfg["adc_max"])
    v_cc = float(cfg["v_cc"])
    r_fixed = float(cfg["r_fixed_ohm"])
    gamma = float(cfg["gamma"])
    circuit = cfg.get("circuit", "ldr_to_vcc")

    if adc_value <= 0 or adc_max <= 0:
        return 0.0

    v_out = v_cc * (adc_value / adc_max)
    if v_out <= 0:
        return 0.0

    if circuit == "ldr_to_gnd":
        if v_out >= v_cc:
            return 0.0
        r_ldr = r_fixed * (v_out / (v_cc - v_out))
    else:
        r_ldr = r_fixed * ((v_cc - v_out) / v_out)

    if r_ldr <= 0:
        return 0.0

    return 10.0 * ((r_fixed / r_ldr) ** (1.0 / gamma))


def resolve_brightness_lux(raw: dict, rules: dict) -> tuple[float | None, float | None]:
    fields = rules["firebase_fields"]
    adc_cfg = rules.get("light_adc")

    adc_val = extract_float(raw, fields.get("light_adc", ["light_adc"]))
    if adc_val is not None and adc_cfg:
        return adc_to_lux(adc_val, adc_cfg), adc_val

    lux = extract_float(raw, fields["brightness"])
    if lux is not None:
        return lux, None

    return None, None


def evaluate_risk(
    distance_cm: float | None,
    brightness_lux: float | None,
    sitting_minutes: int | None = 0,
) -> dict:
    rules = get_rules()
    events = detectors.run_core_events(distance_cm, brightness_lux, sitting_minutes, rules)

    if not events:
        return {
            "risk_score": 0,
            "risk_level": "normal",
            "warning_messages": ["All readings within PostureCare targets"],
            "events": [],
        }

    score = sum(e["score"] for e in events)
    messages = [e["message"] for e in events]
    max_warning = rules["risk_level"]["warning_max_score"]
    level = "warning" if score <= max_warning else "high"
    return {
        "risk_score": score,
        "risk_level": level,
        "warning_messages": messages,
        "events": events,
    }


def should_call_llm(risk_level: str) -> bool:
    return risk_level in get_rules()["llm"]["call_when"]


def should_light_led(risk_level: str) -> bool:
    """Drives the firmware's status LED: on (red) for any elevated risk,
    off (green) when normal — reuses the same risk_level rules already
    compute, so there's no separate on/off threshold to keep in sync."""
    return risk_level != "normal"


def build_llm_user_prompt(
    distance_cm: float | None,
    brightness_lux: float | None,
    sitting_minutes: int | None,
    head_pitch: float | None = None,
    head_roll: float | None = None,
    head_yaw: float | None = None,
    mode: str = "advice",
    flag_set: list[str] | None = None,
    warning_messages: list[str] | None = None,
) -> str:
    p = get_rules()["llm_prompt"]
    dist_str = f"{distance_cm:.0f}cm" if distance_cm is not None else "N/A"
    lux_str = f"{brightness_lux:.0f} lux" if brightness_lux is not None else "N/A"
    sit_str = f"{sitting_minutes} minutes" if sitting_minutes is not None else "N/A"
    prompt = (
        f"Data from PostureCare sensors:\n"
        f"- Distance to screen: {dist_str} (Target: {p['distance_target']})\n"
        f"- Light levels: {lux_str} (Target: {p['light_target']})\n"
        f"- Continuous sitting time: {sit_str}\n"
    )
    if head_pitch is not None or head_roll is not None or head_yaw is not None:
        p_str = f"{head_pitch:.1f}°" if head_pitch is not None else "N/A"
        r_str = f"{head_roll:.1f}°" if head_roll is not None else "N/A"
        y_str = f"{head_yaw:.1f}°" if head_yaw is not None else "N/A"
        prompt += f"- Head rotation angles: Pitch={p_str} (Target: -5° to +5°), Roll={r_str} (Target: ±10°), Yaw={y_str} (Target: ±20°)\n"
    flags = [str(f) for f in (flag_set or []) if f]
    if flags:
        prompt += f"- Active flags: {', '.join(flags)}\n"
    msgs = [str(m) for m in (warning_messages or []) if m]
    if msgs:
        prompt += f"- Detector notes: {'; '.join(msgs)}\n"
    if mode == "explain":
        prompt += (
            "\nThis is an explain request. The current state may already be within targets. "
            "Describe the readings; do not invent a problem. "
            "Only suggest a change if a listed value is outside its target. "
            "If everything is in range, say so clearly."
        )
    else:
        prompt += "\nGive me specific advice."
    return prompt


def process_reading(
    raw: Any,
    default_device_id: str = "esp32_001",
    sitting_minutes: int | None = None,
) -> dict | None:
    """Pure transform of one Firebase sample. No DB writes, no sitting clock."""
    if not isinstance(raw, dict) or not raw:
        return None

    rules = get_rules()
    fields = rules["firebase_fields"]
    face_present = raw.get("face_present")
    brightness_lux, light_adc = resolve_brightness_lux(raw, rules)

    cam_dist = sanitize_distance(
        extract_float(raw, ["camera_distance_cm", "camera_distance", "ai_distance_cm", "dist_cm"])
    )
    ultra_dist = sanitize_distance(
        extract_float(
            raw,
            fields.get(
                "ultrasonic_distance",
                ["ultrasonic_distance_cm", "ultrasonic_distance"],
            ),
        )
    )
    if ultra_dist is None:
        generic = sanitize_distance(extract_float(raw, fields.get("distance", ["distance_cm", "distance"])))
        if cam_dist is None:
            ultra_dist = generic
        elif generic is not None and generic != cam_dist:
            ultra_dist = generic

    if face_present is False:
        cam_dist = None

    active_distance = cam_dist if cam_dist is not None else ultra_dist
    distance_cm = active_distance

    has_ai = face_present is True or (
        face_present is not False
        and (
            raw.get("camera_distance_cm") is not None
            or raw.get("ear") is not None
            or raw.get("head_pitch") is not None
            or raw.get("pitch") is not None
        )
    )
    tracking_active = has_ai and face_present is not False

    ear = extract_float(raw, fields.get("ear", ["ear"])) if tracking_active else None
    blinks = extract_float(raw, fields.get("blinks", ["blinks"]))
    blink_rate_bpm = extract_float(raw, fields.get("blink_rate", ["blink_rate", "blink_rate_bpm"])) if tracking_active else None
    head_pitch = extract_float(raw, fields.get("head_pitch", ["head_pitch", "pitch"])) if tracking_active else None
    head_roll = extract_float(raw, fields.get("head_roll", ["head_roll", "roll"])) if tracking_active else None
    head_yaw = extract_float(raw, fields.get("head_yaw", ["head_yaw", "yaw"])) if tracking_active else None
    raw_warnings = raw.get("warnings") if (tracking_active and isinstance(raw.get("warnings"), list)) else []

    risk = evaluate_risk(distance_cm, brightness_lux, sitting_minutes)
    extended_events = (
        detectors.run_extended_events(blink_rate_bpm, head_pitch, head_roll, head_yaw, rules)
        if tracking_active
        else []
    )
    all_events = [
        e
        for e in (list(risk.get("events") or []) + list(extended_events))
        if e.get("flag") != "too_long"
    ]

    all_warnings = [w for w in raw_warnings if isinstance(w, str) and w.strip()]
    for msg in risk["warning_messages"]:
        if msg and "PostureCare targets" not in msg and msg not in all_warnings:
            all_warnings.append(msg)
    for evt in extended_events:
        msg = evt.get("message")
        if msg and msg not in all_warnings:
            all_warnings.append(msg)

    raw_posture = raw.get("posture_status")
    if face_present is False:
        posture_status = "NO_FACE"
    elif raw_posture in ["GOOD", "WARNING", "DANGER"]:
        posture_status = raw_posture
    else:
        has_danger_event = any(
            e.get("flag") in ["head_too_low", "head_too_high", "head_tilted", "critically_close"]
            for e in extended_events
        )
        if has_danger_event:
            posture_status = "DANGER"
        elif len(all_warnings) > 0:
            posture_status = "WARNING"
        else:
            posture_status = "GOOD"

    risk_level = risk["risk_level"]
    if len(all_warnings) > 0 and risk_level == "normal":
        risk_level = "high" if (posture_status == "DANGER" or len(all_warnings) >= 2) else "warning"

    if len(all_warnings) == 0:
        all_warnings = ["All readings within PostureCare targets"]

    head_pose_cfg = rules.get("head_pose", {})
    head_pose_thresholds = {
        "pitch_down_max_deg": head_pose_cfg.get("pitch_down_max_deg", head_pose_cfg.get("pitch_forward_max_deg", 5)),
        "pitch_up_max_deg": head_pose_cfg.get("pitch_up_max_deg", 5),
        "roll_max_deg": head_pose_cfg.get("roll_max_deg", 15),
        "yaw_max_deg": head_pose_cfg.get("yaw_max_deg", 20),
    }

    nose_x = extract_float(raw, ["nose_x"]) if tracking_active else None
    nose_y = extract_float(raw, ["nose_y"]) if tracking_active else None

    flag_durations = raw.get("flag_durations") if isinstance(raw.get("flag_durations"), dict) else {}
    flag_set = sorted({str(e["flag"]) for e in all_events if e.get("flag")})

    return {
        "device_id": raw.get("device_id") or default_device_id,
        "distance_cm": round(cam_dist, 1) if cam_dist is not None else (round(ultra_dist, 1) if ultra_dist is not None else None),
        "ultrasonic_distance_cm": round(ultra_dist, 1) if ultra_dist is not None else None,
        "light_adc": light_adc,
        "brightness_lux": brightness_lux,
        "sitting_minutes": sitting_minutes,
        "blink_rate_bpm": blink_rate_bpm,
        "ear": round(ear, 3) if ear is not None else None,
        "blinks": int(blinks) if blinks is not None else None,
        "ear_threshold": 0.294,
        "head_pitch_deg": head_pitch,
        "head_roll_deg": head_roll,
        "head_yaw_deg": head_yaw,
        "nose_x": round(nose_x, 3) if nose_x is not None else None,
        "nose_y": round(nose_y, 3) if nose_y is not None else None,
        "head_pose_thresholds": head_pose_thresholds,
        "posture_status": posture_status,
        "risk_score": risk["risk_score"] + sum(e.get("score", 1) for e in extended_events),
        "risk_level": risk_level,
        "warning_messages": all_warnings,
        "events": all_events,
        "llm_eligible": should_call_llm(risk_level),
        "face_present": bool(face_present) if face_present is not None else None,
        "face_lost_sec": extract_float(raw, ["face_lost_sec"]),
        "flag_set": flag_set,
        "flag_durations": flag_durations,
        "timestamp": raw.get("timestamp") or datetime.now(timezone.utc).isoformat(),
    }


def get_rules_public() -> dict:
    return get_rules()
