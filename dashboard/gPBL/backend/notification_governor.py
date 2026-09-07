"""Notification governor — 4-tier severity, LLM gating, snooze/DND/backoff.

Persisted as governor_json on the sessions row so a restart or second tab
does not reset cooldowns. Dedupe key is frozenset(qualifying flags).
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable

ACTION_NONE = "none"
ACTION_NOTICE = "notice"
ACTION_LLM = "llm"
ACTION_REPEAT = "repeat"
ACTION_ESCALATE = "escalate"
ACTION_BREAK = "break_suggest"

SEVERITY_NORMAL = "normal"
SEVERITY_NOTICE = "notice"
SEVERITY_ALERT = "alert"
SEVERITY_ESCALATED = "escalated"

DEFAULT_DANGER_FLAGS = ("critically_close", "head_too_low", "head_too_high", "head_tilted")
DEFAULT_NOTICE_ONLY = ("head_turned", "too_dark", "low_blink_rate", "too_far", "too_bright")
TOO_CLOSE_FLAG = "too_close"
DISABLED_FLAGS = frozenset({"too_long"})

MOCK_SPOKEN = {
    "too_close": "Move farther from the screen.",
    "critically_close": "Sit back — you are too close to the screen.",
    "head_too_low": "Lift your head and look at the screen.",
    "head_too_high": "Lower your chin slightly.",
    "head_tilted": "Straighten your head so it is not tilted.",
    "head_turned": "Turn your head back toward the screen.",
    "too_dark": "Turn on a brighter light.",
    "too_bright": "Reduce glare or lower the brightness.",
    "low_blink_rate": "Blink a few times and rest your eyes.",
    "too_far": "Move a little closer to the screen.",
}

BACKEND_TICK_FLAGS = frozenset(
    {
        "too_dark",
        "too_bright",
        "low_blink_rate",
        "too_far",
        "too_close",
        "critically_close",
    }
)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime | None) -> str | None:
    return dt.isoformat() if dt else None


def _parse_iso(value: Any) -> datetime | None:
    if not value or not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def flag_key(flags: Iterable[str]) -> str:
    return "|".join(sorted({str(f) for f in flags if f and f not in DISABLED_FLAGS}))


SPEAK_PRIORITY = (
    "head_too_low",
    "head_too_high",
    "head_tilted",
    "head_turned",
    "critically_close",
    "too_close",
    "too_far",
    "too_dark",
    "too_bright",
    "low_blink_rate",
)


def primary_flag(flags: Iterable[str], previous_key: str | None = None) -> str | None:
    """Pick the flag to speak about: newly added first, then head pose before distance."""
    current = [str(f) for f in flags if f and f not in DISABLED_FLAGS]
    if not current:
        return None
    present = set(current)
    prev = {p for p in str(previous_key or "").split("|") if p}
    added = [f for f in SPEAK_PRIORITY if f in present and f not in prev]
    if added:
        return added[0]
    for flag in SPEAK_PRIORITY:
        if flag in present:
            return flag
    return current[0]


def spoken_line_for_flags(flags: Iterable[str], previous_key: str | None = None) -> str:
    flag = primary_flag(flags, previous_key=previous_key)
    if flag:
        return MOCK_SPOKEN.get(flag) or "Adjust your posture and workspace."
    return "Adjust your posture and workspace."


def notice_toast_for_flags(flags: Iterable[str]) -> str:
    labels = [str(f).replace("_", " ") for f in flags]
    if not labels:
        return "Posture notice."
    return "Notice: " + ", ".join(labels) + "."


def _severity_cfg(rules: dict | None) -> dict:
    sev = (rules or {}).get("severity") or {}
    return {
        "notice_hold_sec": float(sev.get("notice_hold_sec", 60)),
        "alert_too_close_sec": float(sev.get("alert_too_close_sec", 60)),
        "danger_hold_sec": float(sev.get("danger_hold_sec", 15)),
        "escalate_sec": float(sev.get("escalate_sec", 300)),
        "danger_flags": set(sev.get("danger_flags") or DEFAULT_DANGER_FLAGS),
        "notice_only_flags": set(sev.get("notice_only_flags") or DEFAULT_NOTICE_ONLY),
        "too_close_flag": str(sev.get("too_close_flag") or TOO_CLOSE_FLAG),
    }


def _governor_cfg(rules: dict | None) -> dict:
    gov = (rules or {}).get("governor") or {}
    backoff = gov.get("backoff_sec") or [60, 180]
    return {
        "backoff_sec": [float(x) for x in backoff],
        "snooze_sec": float(gov.get("snooze_sec", 600)),
        "normal_reset_sec": float(gov.get("normal_reset_sec", 120)),
        "corrected_watch_sec": float(gov.get("corrected_watch_sec", 60)),
    }


def _session_cfg(rules: dict | None) -> dict:
    sess = (rules or {}).get("session") or {}
    sitting = (rules or {}).get("sitting_minutes") or {}
    demo_mode = bool(sitting.get("demo_mode"))
    demo_max_minutes = float(sitting.get("demo_max_minutes") or 3)
    if demo_mode:
        break_suggest = demo_max_minutes * 60.0
    else:
        break_suggest = sess.get("break_suggest_sec")
        if break_suggest is None:
            break_suggest = float(sitting.get("max_continuous", 20)) * 60.0
    return {
        "grace_sec": float(sess.get("grace_sec", 20)),
        "break_suggest_sec": float(break_suggest),
        "demo_mode": demo_mode,
        "demo_max_minutes": demo_max_minutes,
    }


def sitting_threshold_sec(rules: dict | None = None) -> float:
    """Effective continuous-sitting threshold used by break_suggest and the UI ring."""
    return _session_cfg(rules)["break_suggest_sec"]


def sitting_demo_public(rules: dict | None = None) -> dict:
    sitting = (rules or {}).get("sitting_minutes") or {}
    cfg = _session_cfg(rules)
    return {
        "demo_mode": bool(cfg["demo_mode"]),
        "demo_max_minutes": int(sitting.get("demo_max_minutes") or cfg["demo_max_minutes"] or 3),
        "sitting_threshold_sec": int(cfg["break_suggest_sec"]),
    }


def fresh() -> dict:
    return {
        "dnd": False,
        "snooze_until": None,
        "backoff_index": 0,
        "last_flag_key": None,
        "prev_flag_key": None,
        "last_alert_at": None,
        "alert_started_at": None,
        "normal_since": None,
        "escalated": False,
        "force_ack": False,
        "pending_advice": None,
        "last_spoken_line": None,
        "last_summary": None,
        "reminder_count": 0,
        "corrected_count": 0,
        "awaiting_flags": None,
        "correction_until": None,
        "break_suggested": False,
        "pending_break": None,
        "last_notice_key": None,
        "severity": SEVERITY_NORMAL,
    }


def load(raw: Any) -> dict:
    state = fresh()
    if isinstance(raw, str) and raw.strip():
        import json

        try:
            raw = json.loads(raw)
        except json.JSONDecodeError:
            raw = None
    if isinstance(raw, dict):
        state.update({k: raw[k] for k in state if k in raw})
    return state


def dump(state: dict) -> str:
    import json

    return json.dumps(state, ensure_ascii=False)


def duration_for(flag: str, durations: dict[str, float], camera: dict[str, float] | None = None) -> float:
    value = float(durations.get(flag) or 0.0)
    if camera:
        value = max(value, float(camera.get(flag) or 0.0))
        if flag == "critically_close":
            value = max(value, float(camera.get("too_close") or 0.0))
    return value


def merge_durations(
    camera: dict[str, float] | None,
    backend: dict[str, float] | None,
    active_flags: Iterable[str],
) -> dict[str, float]:
    merged: dict[str, float] = {}
    camera = camera or {}
    backend = backend or {}
    keys = set(camera) | set(backend) | {str(f) for f in active_flags}
    for flag in keys:
        if flag in DISABLED_FLAGS:
            continue
        merged[flag] = duration_for(flag, backend, camera)
    return merged


def tick_backend_durations(
    current: dict[str, float],
    active_flags: Iterable[str],
    dt_sec: float,
) -> dict[str, float]:
    active = {str(f) for f in active_flags if f not in DISABLED_FLAGS}
    next_durations = dict(current)
    dt = max(0.0, float(dt_sec))
    for flag in BACKEND_TICK_FLAGS:
        if flag in active:
            next_durations[flag] = float(next_durations.get(flag) or 0.0) + dt
        else:
            next_durations[flag] = 0.0
    return next_durations


def qualifying_flags(
    active_flags: Iterable[str],
    durations: dict[str, float],
    rules: dict | None = None,
) -> list[str]:
    cfg = _severity_cfg(rules)
    qualified: list[str] = []
    for flag in sorted({str(f) for f in active_flags if f and f not in DISABLED_FLAGS}):
        held = float(durations.get(flag) or 0.0)
        if flag in cfg["danger_flags"]:
            need = cfg["danger_hold_sec"]
        elif flag == cfg["too_close_flag"]:
            need = cfg["alert_too_close_sec"]
        else:
            need = cfg["notice_hold_sec"]
        if held + 1e-9 >= need:
            qualified.append(flag)
    return qualified


def classify_severity(qualified: Iterable[str], rules: dict | None = None) -> str:
    cfg = _severity_cfg(rules)
    flags = [f for f in qualified if f not in DISABLED_FLAGS]
    if not flags:
        return SEVERITY_NORMAL
    if cfg["too_close_flag"] in flags:
        return SEVERITY_ALERT
    if any(f in cfg["danger_flags"] for f in flags):
        return SEVERITY_ALERT
    if len(flags) >= 2:
        return SEVERITY_ALERT
    return SEVERITY_NOTICE


def _snooze_active(state: dict, now: datetime) -> bool:
    until = _parse_iso(state.get("snooze_until"))
    if until is None:
        return False
    if until.tzinfo is None:
        until = until.replace(tzinfo=timezone.utc)
    return until > now


def _start_correction_watch(state: dict, flags: list[str], now: datetime, watch_sec: float) -> None:
    state["awaiting_flags"] = list(flags)
    state["correction_until"] = _iso(now + timedelta(seconds=watch_sec))


def update_correction(state: dict, current_qualified: Iterable[str], now: datetime) -> None:
    awaiting = state.get("awaiting_flags") or []
    if not awaiting:
        return
    until = _parse_iso(state.get("correction_until"))
    present = {str(f) for f in current_qualified}
    if not (set(awaiting) & present):
        state["corrected_count"] = int(state.get("corrected_count") or 0) + 1
        state["awaiting_flags"] = None
        state["correction_until"] = None
        return
    if until is not None and now >= until:
        state["awaiting_flags"] = None
        state["correction_until"] = None


def _new_pending(
    *,
    kind: str,
    flag_set: list[str],
    severity: str,
    spoken_line: str | None,
    summary: str | None,
    speak: bool,
    toast: str,
    actions: list[str],
    recommendations: list[str] | None = None,
) -> dict:
    return {
        "id": uuid.uuid4().hex,
        "kind": kind,
        "flag_set": list(flag_set),
        "severity": severity,
        "spoken_line": spoken_line,
        "summary": summary,
        "recommendations": list(recommendations or []),
        "speak": bool(speak),
        "toast": toast,
        "actions": list(actions),
        "created_at": _iso(_utcnow()),
    }


def tick(
    state: dict,
    *,
    now: datetime | None = None,
    severity: str,
    flag_set: list[str],
    in_grace: bool,
    can_fire: bool,
    rules: dict | None = None,
    exposure_sec: float = 0.0,
) -> tuple[dict, str]:
    """Advance governor. Returns (state, action).

    action is one of: none, notice, llm, repeat, escalate, break_suggest.
    LLM is only requested on flag-set change at alert (never on escalate).
    """
    now = now or _utcnow()
    gov_cfg = _governor_cfg(rules)
    flags = [f for f in flag_set if f not in DISABLED_FLAGS]
    key = flag_key(flags)
    state = load(state)

    update_correction(state, flags, now)

    if not can_fire or in_grace:
        state["severity"] = severity
        return state, ACTION_NONE

    if state.get("dnd"):
        state["severity"] = severity
        return state, ACTION_NONE

    if _snooze_active(state, now):
        state["severity"] = severity
        return state, ACTION_NONE

    if severity == SEVERITY_NORMAL:
        if not state.get("normal_since"):
            state["normal_since"] = _iso(now)
        normal_since = _parse_iso(state.get("normal_since")) or now
        if (now - normal_since).total_seconds() >= gov_cfg["normal_reset_sec"]:
            state["backoff_index"] = 0
            state["last_flag_key"] = None
            state["last_notice_key"] = None
        state["alert_started_at"] = None
        if not state.get("force_ack"):
            state["escalated"] = False
        state["severity"] = SEVERITY_NORMAL
        return state, ACTION_NONE

    state["normal_since"] = None

    if severity == SEVERITY_NOTICE:
        state["alert_started_at"] = None
        if not state.get("force_ack"):
            state["escalated"] = False
        state["severity"] = SEVERITY_NOTICE
        if key and key != state.get("last_notice_key"):
            state["last_notice_key"] = key
            state["pending_advice"] = _new_pending(
                kind=ACTION_NOTICE,
                flag_set=flags,
                severity=SEVERITY_NOTICE,
                spoken_line=None,
                summary=notice_toast_for_flags(flags),
                speak=False,
                toast=notice_toast_for_flags(flags),
                actions=["snooze", "dnd"],
            )
            return state, ACTION_NOTICE
        return state, ACTION_NONE

    # alert (and possibly escalate)
    if not state.get("alert_started_at"):
        state["alert_started_at"] = _iso(now)
    alert_started = _parse_iso(state.get("alert_started_at")) or now
    alert_held = (now - alert_started).total_seconds()

    if alert_held >= _severity_cfg(rules)["escalate_sec"]:
        if not state.get("escalated"):
            state["escalated"] = True
            state["force_ack"] = True
            state["severity"] = SEVERITY_ESCALATED
            last_line = state.get("last_spoken_line")
            state["pending_advice"] = _new_pending(
                kind=ACTION_ESCALATE,
                flag_set=flags,
                severity=SEVERITY_ESCALATED,
                spoken_line=last_line,
                summary=state.get("last_summary") or "Please acknowledge this reminder.",
                speak=False,
                toast="Still off posture — please acknowledge.",
                actions=["ack"],
            )
            return state, ACTION_ESCALATE
        state["severity"] = SEVERITY_ESCALATED
        return state, ACTION_NONE

    state["severity"] = SEVERITY_ALERT
    backoff = gov_cfg["backoff_sec"] or [60.0]
    index = int(state.get("backoff_index") or 0)
    index = max(0, min(index, len(backoff) - 1))

    if key != state.get("last_flag_key"):
        state["prev_flag_key"] = state.get("last_flag_key")
        state["last_flag_key"] = key
        state["backoff_index"] = 0
        state["last_alert_at"] = _iso(now)
        state["reminder_count"] = int(state.get("reminder_count") or 0) + 1
        _start_correction_watch(state, flags, now, gov_cfg["corrected_watch_sec"])
        return state, ACTION_LLM

    last_alert = _parse_iso(state.get("last_alert_at"))
    wait = backoff[index]
    if last_alert is not None and (now - last_alert).total_seconds() < wait:
        return state, ACTION_NONE

    state["last_alert_at"] = _iso(now)
    state["backoff_index"] = min(index + 1, len(backoff) - 1)
    return state, ACTION_REPEAT


def apply_llm_result(
    state: dict,
    *,
    flag_set: list[str],
    advice: dict,
    severity: str = SEVERITY_ALERT,
) -> dict:
    spoken = spoken_line_for_flags(flag_set, previous_key=state.get("prev_flag_key"))
    summary = advice.get("summary") or spoken
    state["last_spoken_line"] = spoken
    state["last_summary"] = summary
    state["pending_advice"] = _new_pending(
        kind="alert",
        flag_set=flag_set,
        severity=severity,
        spoken_line=spoken,
        summary=summary,
        speak=True,
        toast=spoken,
        actions=["snooze", "dnd"],
        recommendations=advice.get("recommendations") or [],
    )
    return state


def should_suggest_break(
    state: dict,
    *,
    exposure_sec: float,
    in_grace: bool,
    can_fire: bool,
    rules: dict | None = None,
) -> bool:
    if state.get("break_suggested"):
        return False
    if not can_fire or in_grace:
        return False
    return float(exposure_sec) >= _session_cfg(rules)["break_suggest_sec"]


def mark_break_suggested(state: dict, severity: str = SEVERITY_NOTICE, rules: dict | None = None) -> dict:
    del severity
    minutes = max(1, int(round(_session_cfg(rules)["break_suggest_sec"] / 60.0)))
    unit = "minute" if minutes == 1 else "minutes"
    state["break_suggested"] = True
    state["pending_break"] = {
        "id": uuid.uuid4().hex,
        "kind": ACTION_BREAK,
        "toast": "Start a break?",
        "summary": f"You have been at the desk for {minutes} {unit}.",
        "spoken_line": None,
        "speak": False,
        "actions": ["start_break"],
        "created_at": _iso(_utcnow()),
    }
    return state


def apply_repeat(state: dict, flag_set: list[str]) -> dict:
    spoken = spoken_line_for_flags(flag_set, previous_key=state.get("prev_flag_key"))
    summary = state.get("last_summary") or spoken
    state["last_spoken_line"] = spoken
    state["pending_advice"] = _new_pending(
        kind="alert",
        flag_set=flag_set,
        severity=SEVERITY_ALERT,
        spoken_line=spoken,
        summary=summary,
        speak=True,
        toast=spoken,
        actions=["snooze", "dnd"],
    )
    return state


def snooze(state: dict, *, now: datetime | None = None, rules: dict | None = None) -> dict:
    now = now or _utcnow()
    sec = _governor_cfg(rules)["snooze_sec"]
    state["snooze_until"] = _iso(now + timedelta(seconds=sec))
    state["escalated"] = False
    state["force_ack"] = False
    state["alert_started_at"] = None
    return state


def ack(state: dict, *, now: datetime | None = None) -> dict:
    now = now or _utcnow()
    state["escalated"] = False
    state["force_ack"] = False
    state["alert_started_at"] = _iso(now)
    return state


def set_dnd(state: dict, enabled: bool | None = None) -> dict:
    if enabled is None:
        state["dnd"] = not bool(state.get("dnd"))
    else:
        state["dnd"] = bool(enabled)
    if state["dnd"]:
        state["escalated"] = False
        state["force_ack"] = False
    return state


def led_from_severity(session_state: str, severity: str) -> dict:
    """Green = MONITORING+normal; red = alert; blink = escalated; off otherwise."""
    off = {"red": False, "green": False, "blink": False}
    if session_state != "monitoring":
        return off
    if severity == SEVERITY_ESCALATED:
        return {"red": True, "green": False, "blink": True}
    if severity == SEVERITY_ALERT:
        return {"red": True, "green": False, "blink": False}
    return {"red": False, "green": True, "blink": False}
