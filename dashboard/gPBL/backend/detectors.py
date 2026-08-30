from __future__ import annotations
"""Per-signal detectors — pure threshold checks that turn raw values into
discrete, named events instead of one composite score.

`run_core_events()` backs `processing.evaluate_risk()`'s existing
risk_score/risk_level (distance, brightness, sitting) — factored out here so
there's one place these thresholds are checked, not two.

`run_extended_events()` covers the newer signals (blink_rate, head_pose).
These are deliberately NOT folded into the composite risk_score: a single
reading's blink rate or head angle is noisy and momentary, so scoring it
per-reading would reintroduce the if-else-on-one-record problem the window/
summary builder (next step) exists to avoid. Their events are exposed on the
reading for that future aggregation step to count/trend instead.
"""

Event = dict


def _event(signal: str, flag: str, value, message: str, score: int = 1) -> Event:
    return {"signal": signal, "flag": flag, "value": value, "message": message, "score": score}


def detect_distance(distance_cm: float, cfg: dict) -> Event | None:
    d_min, d_max = cfg["target_min"], cfg["target_max"]
    d_critical = cfg.get("critical_min")
    # Below ~30cm is its own tier, not just a stronger "too close" — Rempel et
    # al. and the Japanese office-worker study (Nakatsuka et al.) both treat
    # sub-30/40cm viewing as a distinct acute risk band, not a linear extension
    # of the 50-70cm comfort range.
    if d_critical is not None and distance_cm < d_critical:
        return _event(
            "distance", "critically_close", distance_cm,
            f"Critically close ({distance_cm:.0f} cm, under {d_critical} cm) — "
            f"sharply raises eye-strain and neck-pain risk (target {d_min}-{d_max} cm)",
            score=3,
        )
    if distance_cm < d_min:
        return _event(
            "distance", "too_close", distance_cm,
            f"Distance too close ({distance_cm:.0f} cm, target {d_min}-{d_max} cm)", score=2,
        )
    if distance_cm > d_max:
        return _event(
            "distance", "too_far", distance_cm,
            f"Distance too far ({distance_cm:.0f} cm, target {d_min}-{d_max} cm)",
        )
    return None


def detect_brightness(brightness_lux: float, cfg: dict) -> Event | None:
    lux_min = cfg["target_min"]
    lux_max = cfg.get("target_max")
    if brightness_lux < lux_min:
        return _event(
            "brightness", "too_dark", brightness_lux,
            f"Too dark ({brightness_lux:.0f} lux, target {lux_min}-{lux_max} lux)"
            if lux_max else f"Too dark ({brightness_lux:.0f} lux, target {lux_min}+ lux)",
        )
    if lux_max is not None and brightness_lux > lux_max:
        return _event(
            "brightness", "too_bright", brightness_lux,
            f"Too bright ({brightness_lux:.0f} lux, target {lux_min}-{lux_max} lux) — risk of glare",
        )
    return None


def detect_sitting(sitting_minutes: int, cfg: dict) -> Event | None:
    sit_max = cfg["max_continuous"]
    if sitting_minutes > sit_max:
        return _event(
            "sitting", "too_long", sitting_minutes,
            f"Sitting too long ({sitting_minutes} min > {sit_max} min)",
        )
    return None


def detect_blink_rate(blink_rate_bpm: float | None, cfg: dict) -> Event | None:
    if blink_rate_bpm is None:
        return None
    bpm_min = cfg["target_min_bpm"]
    if blink_rate_bpm < bpm_min:
        return _event(
            "blink_rate", "low_blink_rate", blink_rate_bpm,
            f"Low blink rate ({blink_rate_bpm:.1f}/min < {bpm_min}/min) — possible eye strain",
        )
    return None


def detect_head_pose(
    pitch: float | None, roll: float | None, yaw: float | None, cfg: dict
) -> list[Event]:
    events: list[Event] = []
    if pitch is not None and pitch > cfg["pitch_forward_max_deg"]:
        events.append(_event(
            "head_pose", "forward_head", pitch,
            f"Forward head tilt ({pitch:.0f}° > {cfg['pitch_forward_max_deg']}°)",
        ))
    if roll is not None and abs(roll) > cfg["roll_max_deg"]:
        events.append(_event(
            "head_pose", "head_tilt", roll, f"Head tilted sideways ({roll:.0f}°)",
        ))
    if yaw is not None and abs(yaw) > cfg["yaw_max_deg"]:
        events.append(_event(
            "head_pose", "head_turned", yaw, f"Head turned away ({yaw:.0f}°)",
        ))
    return events


def run_core_events(
    distance_cm: float,
    brightness_lux: float,
    sitting_minutes: int,
    rules: dict,
) -> list[Event]:
    events = [
        detect_distance(distance_cm, rules["distance_cm"]),
        detect_brightness(brightness_lux, rules["brightness_lux"]),
        detect_sitting(sitting_minutes, rules["sitting_minutes"]),
    ]
    return [e for e in events if e is not None]


def run_extended_events(
    blink_rate_bpm: float | None,
    head_pitch: float | None,
    head_roll: float | None,
    head_yaw: float | None,
    rules: dict,
) -> list[Event]:
    events = []
    blink_event = detect_blink_rate(blink_rate_bpm, rules["blink_rate"])
    if blink_event:
        events.append(blink_event)
    events.extend(detect_head_pose(head_pitch, head_roll, head_yaw, rules["head_pose"]))
    return events
