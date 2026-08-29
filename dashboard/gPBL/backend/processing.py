"""
PostureCare processing — rules from backend/data/rules.json.
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


def resolve_brightness_lux(raw: dict, rules: dict) -> tuple[float, float | None]:
    fields = rules["firebase_fields"]
    adc_cfg = rules.get("light_adc")

    adc_val = extract_float(raw, fields.get("light_adc", ["light_adc"]))
    if adc_val is not None and adc_cfg:
        return adc_to_lux(adc_val, adc_cfg), adc_val

    lux = extract_float(raw, fields["brightness"])
    if lux is not None:
        return lux, None

    return 0.0, None


def evaluate_risk(
    distance_cm: float,
    brightness_lux: float,
    sitting_minutes: int = 0,
) -> dict:
    rules = get_rules()
    events = detectors.run_core_events(distance_cm, brightness_lux, sitting_minutes, rules)

    if not events:
        return {
            "risk_score": 0,
            "risk_level": "normal",
            "warning_messages": ["All readings within PostureCare targets"],
        }

    score = sum(e["score"] for e in events)
    messages = [e["message"] for e in events]
    max_warning = rules["risk_level"]["warning_max_score"]
    level = "warning" if score <= max_warning else "high"
    return {"risk_score": score, "risk_level": level, "warning_messages": messages}


def should_call_llm(risk_level: str) -> bool:
    return risk_level in get_rules()["llm"]["call_when"]


def should_light_led(risk_level: str) -> bool:
    """Drives the firmware's status LED: on (red) for any elevated risk,
    off (green) when normal — reuses the same risk_level rules already
    compute, so there's no separate on/off threshold to keep in sync."""
    return risk_level != "normal"


def build_llm_user_prompt(
    distance_cm: float,
    brightness_lux: float,
    sitting_minutes: int,
) -> str:
    p = get_rules()["llm_prompt"]
    return (
        f"Data from PostureCare sensors:\n"
        f"- Distance to screen: {distance_cm:.0f}cm (Target: {p['distance_target']})\n"
        f"- Light levels: {brightness_lux:.0f} lux (Target: {p['light_target']})\n"
        f"- Continuous sitting time: {sitting_minutes} minutes\n\n"
        f"Give me specific advice."
    )


_presence_since: datetime | None = None


def track_sitting_minutes(distance_cm: float | None, rules: dict) -> int:
    """No dedicated presence sensor — approximate continuous sitting time from
    the distance sensor already in use: readings within presence.max_distance_cm
    count as "someone's at the desk" and accumulate; stepping out of range (or
    no distance reading) resets the clock."""
    global _presence_since
    max_cm = rules.get("presence", {}).get("max_distance_cm", 120)
    present = distance_cm is not None and 0 < distance_cm <= max_cm
    now = datetime.now(timezone.utc)
    if not present:
        _presence_since = None
        return 0
    if _presence_since is None:
        _presence_since = now
    return int((now - _presence_since).total_seconds() // 60)


def process_reading(raw: Any, default_device_id: str = "esp32_001") -> dict | None:
    if not isinstance(raw, dict):
        return None

    rules = get_rules()
    fields = rules["firebase_fields"]
    distance = extract_float(raw, fields["distance"])
    brightness_lux, light_adc = resolve_brightness_lux(raw, rules)

    has_brightness = light_adc is not None or extract_float(raw, fields["brightness"]) is not None
    if distance is None and not has_brightness:
        return None

    distance_cm = distance if distance is not None else 0.0
    sitting_minutes = track_sitting_minutes(distance, rules)
    blink_rate_bpm = extract_float(raw, fields.get("blink_rate", ["blink_rate"]))
    head_pitch = extract_float(raw, fields.get("head_pitch", ["head_pitch"]))
    head_roll = extract_float(raw, fields.get("head_roll", ["head_roll"]))
    head_yaw = extract_float(raw, fields.get("head_yaw", ["head_yaw"]))

    risk = evaluate_risk(distance_cm, brightness_lux, sitting_minutes)
    extended_events = detectors.run_extended_events(blink_rate_bpm, head_pitch, head_roll, head_yaw, rules)

    return {
        "device_id": raw.get("device_id") or default_device_id,
        "distance_cm": distance_cm,
        "light_adc": light_adc,
        "brightness_lux": brightness_lux,
        "sitting_minutes": sitting_minutes,
        "blink_rate_bpm": blink_rate_bpm,
        "head_pitch_deg": head_pitch,
        "head_roll_deg": head_roll,
        "head_yaw_deg": head_yaw,
        "risk_score": risk["risk_score"],
        "risk_level": risk["risk_level"],
        "warning_messages": risk["warning_messages"],
        "events": extended_events,
        "llm_eligible": should_call_llm(risk["risk_level"]),
        "timestamp": raw.get("timestamp") or datetime.now(timezone.utc).isoformat(),
    }


def get_rules_public() -> dict:
    return get_rules()
