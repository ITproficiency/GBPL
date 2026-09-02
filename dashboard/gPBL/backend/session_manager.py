"""Session state machine — the only writer for history, LED, and the UI snapshot.

States: idle | calibrating | monitoring | away | break | ended
One live session per device_id; a second tab attaches instead of opening another.
"""

from __future__ import annotations

import logging
import threading
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

import config
import firebase_client
import history_store
import llm_service
import notification_governor as governor
import processing
import tracking_manager

logger = logging.getLogger(__name__)

STATE_IDLE = "idle"
STATE_CALIBRATING = "calibrating"
STATE_MONITORING = "monitoring"
STATE_AWAY = "away"
STATE_BREAK = "break"
STATE_ENDED = "ended"

ACTIVE_STATES = {STATE_CALIBRATING, STATE_MONITORING, STATE_AWAY, STATE_BREAK}
PERSIST_STATES = {STATE_CALIBRATING, STATE_MONITORING}

GRACE_SEC = 20.0
FACE_LOST_AWAY_SEC = 30.0
AWAY_RESET_EXPOSURE_SEC = 180.0  # 3 minutes
IDLE_END_SEC = 900.0  # 15 minutes
# Poll is typically 10s; keep sensors "live" across one missed poll while still
# treating frozen RTDB values as stale (~8s intent + poll slack).
SOURCE_LIVENESS_SEC = max(8.0, float(getattr(config, "SENSOR_POLL_INTERVAL_SEC", 10)) + 4.0)


def _read_sec(sess: dict, key: str, fallback: float, lo: float, hi: float) -> float:
    if key not in sess or sess[key] is None:
        logger.warning("session.%s missing; using fallback %.1f", key, fallback)
        return fallback
    try:
        return max(lo, min(hi, float(sess[key])))
    except (TypeError, ValueError) as err:
        logger.warning(
            "session.%s=%r invalid (%s); using fallback %.1f",
            key,
            sess[key],
            err,
            fallback,
        )
        return fallback


def session_timing(rules: dict | None = None) -> dict:
    """Read session.* from rules with clamps. Never raises; module constants are fallbacks."""
    fallbacks = {
        "grace_sec": GRACE_SEC,
        "face_lost_away_sec": FACE_LOST_AWAY_SEC,
        "away_reset_exposure_sec": AWAY_RESET_EXPOSURE_SEC,
        "idle_end_sec": IDLE_END_SEC,
    }
    try:
        if rules is None:
            return dict(fallbacks)
        if not isinstance(rules, dict):
            logger.warning(
                "session_timing rules is %s, not a dict; using fallbacks",
                type(rules).__name__,
            )
            return dict(fallbacks)
        sess = rules.get("session")
        if sess is None:
            logger.warning("session.* missing from rules; using fallbacks")
            return dict(fallbacks)
        if not isinstance(sess, dict):
            logger.warning("session.* is %s, not a dict; using fallbacks", type(sess).__name__)
            return dict(fallbacks)
    except Exception as err:
        logger.warning("session_timing failed: %s", err)
        return dict(fallbacks)

    grace = _read_sec(sess, "grace_sec", GRACE_SEC, 5.0, 120.0)
    face_lost = _read_sec(sess, "face_lost_away_sec", FACE_LOST_AWAY_SEC, 5.0, 300.0)
    away_reset = _read_sec(sess, "away_reset_exposure_sec", AWAY_RESET_EXPOSURE_SEC, 30.0, 3600.0)
    idle_end = _read_sec(sess, "idle_end_sec", IDLE_END_SEC, 60.0, 7200.0)

    if face_lost >= idle_end:
        clamped = max(5.0, min(face_lost, idle_end - 1.0))
        logger.warning(
            "session.face_lost_away_sec (%.1f) must be < idle_end_sec (%.1f); clamped to %.1f",
            face_lost,
            idle_end,
            clamped,
        )
        face_lost = clamped
    if away_reset >= idle_end:
        clamped = max(30.0, min(away_reset, idle_end - 1.0))
        logger.warning(
            "session.away_reset_exposure_sec (%.1f) must be < idle_end_sec (%.1f); clamped to %.1f",
            away_reset,
            idle_end,
            clamped,
        )
        away_reset = clamped

    return {
        "grace_sec": grace,
        "face_lost_away_sec": face_lost,
        "away_reset_exposure_sec": away_reset,
        "idle_end_sec": idle_end,
    }


def _session_timing_from_store() -> dict:
    try:
        return session_timing(processing.get_rules())
    except FileNotFoundError:
        logger.warning("rules.json not found; using session timing fallbacks")
        return session_timing(None)
    except Exception as err:
        logger.warning("failed to load session timing from rules: %s", err)
        return session_timing(None)


def _tracking_error() -> str | None:
    getter = getattr(tracking_manager, "get_last_error", None)
    if not callable(getter):
        return None
    try:
        err = getter()
    except Exception:
        return None
    if err is None:
        return None
    text = str(err).strip()
    return text or None


_managers: dict[str, "SessionManager"] = {}
_managers_lock = threading.Lock()


def get_manager(device_id: str | None = None) -> "SessionManager":
    device_id = device_id or config.DEFAULT_DEVICE_ID
    with _managers_lock:
        mgr = _managers.get(device_id)
        if mgr is None:
            mgr = SessionManager(device_id)
            _managers[device_id] = mgr
        return mgr


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime | None) -> str | None:
    return dt.isoformat() if dt else None


class SessionManager:
    def __init__(self, device_id: str):
        self.device_id = device_id
        self._lock = threading.RLock()
        self.state = STATE_IDLE
        self.session_id: str | None = None
        self.last_session_id: str | None = None
        self.source: str | None = None
        self.started_at: datetime | None = None
        self.ended_at: datetime | None = None
        self.grace_until: datetime | None = None
        self.exposure_sec: float = 0.0
        self._exposure_tick_at: datetime | None = None
        self._face_lost_since: datetime | None = None
        self._away_since: datetime | None = None
        self.face_present: bool = False
        self._reading: dict | None = None
        self._last_insert_ts: str | None = None
        self._last_led: tuple[bool, bool, bool] | None = None
        self.flag_durations: dict[str, float] = {}
        self.flag_set: list[str] = []
        self.qualified_flags: list[str] = []
        self.severity: str = governor.SEVERITY_NORMAL
        self._gov: dict = governor.fresh()
        self._backend_durations: dict[str, float] = {}
        self._duration_tick_at: datetime | None = None
        self._sensor_hash: tuple | None = None
        self._sensor_changed_at: datetime | None = None
        self._face_seen_at: datetime | None = None
        self._restore_open_session()

    def start(self, source: str = "0") -> dict:
        with self._lock:
            attaching = self.state in ACTIVE_STATES
            spawn_source = source or self.source or "0"
            already_running = tracking_manager.is_tracking_active()
            if attaching and already_running:
                if source:
                    self.source = source
                return self._tracking_payload(ok=True)

        started = True
        if not tracking_manager.is_tracking_active():
            started = tracking_manager.start_tracking(source=spawn_source)

        with self._lock:
            if self.state in ACTIVE_STATES:
                if source:
                    self.source = source
                return self._tracking_payload(ok=tracking_manager.is_tracking_active())

            if not started and not tracking_manager.is_tracking_active():
                return self._tracking_payload(ok=False)

            now = _utcnow()
            timing = _session_timing_from_store()
            self.session_id = uuid.uuid4().hex
            self.last_session_id = self.session_id
            self.source = source
            self.state = STATE_CALIBRATING
            self.started_at = now
            self.ended_at = None
            self.grace_until = now + timedelta(seconds=timing["grace_sec"])
            self.exposure_sec = 0.0
            self._exposure_tick_at = now
            self._face_lost_since = None
            self._away_since = None
            self.face_present = False
            self._last_insert_ts = None
            self.flag_durations = {}
            self.flag_set = []
            self.qualified_flags = []
            self.severity = governor.SEVERITY_NORMAL
            self._gov = governor.fresh()
            self._backend_durations = {}
            self._duration_tick_at = now
            self._face_seen_at = None
            history_store.create_session(
                {
                    "id": self.session_id,
                    "device_id": self.device_id,
                    "source": source,
                    "state": self.state,
                    "started_at": _iso(self.started_at),
                    "grace_until": _iso(self.grace_until),
                    "governor_json": governor.dump(self._gov),
                }
            )
            self._push_led({"red": False, "green": False, "blink": False})
            return self._tracking_payload(ok=True)

    def stop(self) -> dict:
        with self._lock:
            sid = self.session_id
            if self.state in ACTIVE_STATES:
                self._close_session("stop")
            self.state = STATE_IDLE
            self.session_id = None
            self.grace_until = None
            self._face_lost_since = None
            self._away_since = None
            self._exposure_tick_at = None
            session_snap = self.snapshot_session()
            extra_stats = {
                "corrected_count": int(self._gov.get("corrected_count") or 0),
                "reminder_count": int(self._gov.get("reminder_count") or 0),
                "corrected_label": (
                    f"Corrected {int(self._gov.get('corrected_count') or 0)}/"
                    f"{int(self._gov.get('reminder_count') or 0)} reminders"
                ),
            }
        tracking_manager.stop_tracking()
        self._push_led({"red": False, "green": False, "blink": False})
        insight = _generate_session_insight(self.device_id, sid, extra_stats=extra_stats)
        return {
            "status": "ok",
            "active": False,
            "session": session_snap,
            "insight": insight,
        }

    def tick(self, raw: Any) -> dict:
        idle_ended_sid = None
        with self._lock:
            now = _utcnow()
            sitting = None if self.state in (STATE_IDLE, STATE_ENDED) else int(self.exposure_sec // 60)
            reading = processing.process_reading(
                raw,
                default_device_id=self.device_id,
                sitting_minutes=sitting,
            )
            self.face_present = self._extract_face_present(raw)
            self._update_face_lost_clock(now, self.face_present)
            self._update_source_liveness(raw, now)
            self._update_flags_and_durations(raw, reading, now)

            if self.state in (STATE_IDLE, STATE_ENDED):
                if reading is not None:
                    reading = {**reading, "sitting_minutes": None, "session_id": None}
                self._reading = reading
                self._push_led({"red": False, "green": False, "blink": False})
                return self.snapshot()

            timing = _session_timing_from_store()
            if self.state == STATE_CALIBRATING and self.grace_until and now >= self.grace_until:
                self._set_state(STATE_MONITORING, now)

            lost_sec = self._face_lost_sec(now)
            if self.state in (STATE_CALIBRATING, STATE_MONITORING) and lost_sec > timing["face_lost_away_sec"]:
                self._enter_away(now)

            if self.state == STATE_AWAY:
                away_sec = self._away_sec(now)
                if away_sec >= timing["idle_end_sec"]:
                    idle_ended_sid = self.session_id
                    self._idle_end()
                    if reading is not None:
                        reading = {**reading, "sitting_minutes": None}
                    self._reading = reading
                elif self.face_present:
                    if away_sec >= timing["away_reset_exposure_sec"]:
                        self.exposure_sec = 0.0
                    self._away_since = None
                    self._face_lost_since = None
                    if self.grace_until and now < self.grace_until:
                        self._set_state(STATE_CALIBRATING, now)
                    else:
                        self._set_state(STATE_MONITORING, now)
                    self._exposure_tick_at = now

            if self.state not in (STATE_IDLE, STATE_ENDED):
                self._accumulate_exposure(now)
                reading = self._annotate_reading(reading)
                if self.state == STATE_CALIBRATING and reading is not None:
                    reading["llm_eligible"] = False

                self._run_governor(now, reading)

                if self.state in PERSIST_STATES and reading is not None:
                    ts = str(reading.get("timestamp"))
                    if ts and ts != self._last_insert_ts:
                        history_store.insert_reading(reading)
                        self._last_insert_ts = ts

                self._reading = reading
                self._apply_led()
                if self.session_id:
                    history_store.update_session(
                        self.session_id,
                        state=self.state,
                        exposure_sec=self.exposure_sec,
                        governor_json=governor.dump(self._gov),
                    )

            snap = self.snapshot()

        if idle_ended_sid:
            tracking_manager.stop_tracking()
            extra = {
                "corrected_count": int(self._gov.get("corrected_count") or 0),
                "reminder_count": int(self._gov.get("reminder_count") or 0),
                "corrected_label": (
                    f"Corrected {int(self._gov.get('corrected_count') or 0)}/"
                    f"{int(self._gov.get('reminder_count') or 0)} reminders"
                ),
            }
            _generate_session_insight(self.device_id, idle_ended_sid, extra_stats=extra)
        return snap

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "session": self.snapshot_session(),
                "reading": self._reading,
                "sources": self._sources_payload(),
            }

    def snapshot_session(self) -> dict:
        now = _utcnow()
        grace_remaining = None
        if self.grace_until and self.state == STATE_CALIBRATING:
            grace_remaining = max(0.0, (self.grace_until - now).total_seconds())
        sitting = None if self.state in (STATE_IDLE, STATE_ENDED) else int(self.exposure_sec // 60)
        return {
            "device_id": self.device_id,
            "session_id": self.session_id or self.last_session_id,
            "state": self.state,
            "source": self.source,
            "started_at": _iso(self.started_at),
            "ended_at": _iso(self.ended_at),
            "exposure_sec": round(self.exposure_sec, 1),
            "sitting_minutes": sitting,
            "grace_remaining_sec": round(grace_remaining, 1) if grace_remaining is not None else None,
            "face_present": self.face_present,
            "face_lost_sec": round(self._face_lost_sec(now), 1),
            "flag_set": list(self.flag_set),
            "flag_durations": dict(self.flag_durations),
            "qualified_flags": list(self.qualified_flags),
            "severity": self.severity,
            "dnd": bool(self._gov.get("dnd")),
            "snooze_until": self._gov.get("snooze_until"),
            "snooze_remaining_sec": _remaining_sec(self._gov.get("snooze_until"), now),
            "escalated": bool(self._gov.get("escalated")),
            "corrected_count": int(self._gov.get("corrected_count") or 0),
            "reminder_count": int(self._gov.get("reminder_count") or 0),
            "corrected_label": (
                f"Corrected {int(self._gov.get('corrected_count') or 0)}/"
                f"{int(self._gov.get('reminder_count') or 0)} reminders"
            ),
            "pending_advice": self._gov.get("pending_advice"),
            "pending_break": self._gov.get("pending_break"),
            "spoken_line": ((self._gov.get("pending_advice") or {}).get("spoken_line")),
            **governor.sitting_demo_public(processing.get_rules()),
        }

    def enter_break(self) -> dict:
        """Wave 4 hook — Pomodoro BREAK sync. Not wired in Wave 1."""
        with self._lock:
            if self.state in (STATE_MONITORING, STATE_CALIBRATING, STATE_AWAY):
                now = _utcnow()
                self._accumulate_exposure(now)
                self._set_state(STATE_BREAK, now)
                self._exposure_tick_at = None
                self._push_led({"red": False, "green": False, "blink": False})
                self._persist_governor()
            return self.snapshot()

    def leave_break(self) -> dict:
        """Wave 4 hook — return to MONITORING if a session is still open."""
        with self._lock:
            if self.state == STATE_BREAK:
                now = _utcnow()
                if self.face_present:
                    self._set_state(STATE_MONITORING, now)
                    self._exposure_tick_at = now
                else:
                    self._enter_away(now)
                self._apply_led()
                self._persist_governor()
            return self.snapshot()

    def snooze(self) -> dict:
        with self._lock:
            self._gov = governor.snooze(self._gov, now=_utcnow(), rules=processing.get_rules())
            self._persist_governor()
            return self.snapshot()

    def ack(self) -> dict:
        with self._lock:
            self._gov = governor.ack(self._gov, now=_utcnow())
            self._persist_governor()
            self._apply_led()
            return self.snapshot()

    def set_dnd(self, enabled: bool | None = None) -> dict:
        with self._lock:
            self._gov = governor.set_dnd(self._gov, enabled)
            self._persist_governor()
            self._apply_led()
            return self.snapshot()

    def ready(self) -> dict:
        """Wizard step 4: leave STATE_CALIBRATING early. 20s grace remains the fallback."""
        with self._lock:
            if self.state == STATE_CALIBRATING:
                now = _utcnow()
                self._set_state(STATE_MONITORING, now)
                self._exposure_tick_at = now if self.face_present else None
                self._apply_led()
                self._persist_governor()
            return self.snapshot()

    def _tracking_payload(self, ok: bool) -> dict:
        payload = {
            "status": "ok" if ok else "error",
            "active": tracking_manager.is_tracking_active(),
            "source": self.source,
            "session": self.snapshot_session(),
        }
        if not ok:
            err = _tracking_error()
            if err:
                payload["error"] = err
        return payload

    def _annotate_reading(self, reading: dict | None) -> dict | None:
        if reading is None:
            return None
        return {
            **reading,
            "sitting_minutes": int(self.exposure_sec // 60),
            "session_id": self.session_id,
            "state": self.state,
            "exposure_sec": round(self.exposure_sec, 1),
            "face_present": self.face_present,
            "flag_set": list(self.flag_set),
            "flag_durations": dict(self.flag_durations),
            "qualified_flags": list(self.qualified_flags),
            "severity": self.severity,
            "spoken_line": ((self._gov.get("pending_advice") or {}).get("spoken_line")),
            "llm_eligible": bool(self.flag_set) and self.state not in (STATE_IDLE, STATE_ENDED),
        }

    def _update_source_liveness(self, raw: Any, now: datetime) -> None:
        sensor_hash = _sensor_value_hash(raw)
        if sensor_hash != self._sensor_hash:
            self._sensor_hash = sensor_hash
            self._sensor_changed_at = now
        if self.face_present:
            self._face_seen_at = now

    def _sources_payload(self, now: datetime | None = None) -> dict:
        now = now or _utcnow()
        sensor_age = _age_sec(self._sensor_changed_at, now)
        face_age = _age_sec(self._face_seen_at, now)
        tracking_active = tracking_manager.is_tracking_active()
        remote_cam = _is_remote_cam_source(self.source)

        if remote_cam:
            cam_status = "live" if tracking_active else "stale"
        else:
            cam_status = "unused"

        if tracking_active and face_age is not None and face_age <= SOURCE_LIVENESS_SEC:
            ai_status = "live"
        else:
            ai_status = "stale"

        sensors_live = (
            sensor_age is not None
            and sensor_age <= SOURCE_LIVENESS_SEC
            and self._sensor_hash is not None
            and self._sensor_hash != _EMPTY_SENSOR_HASH
        )
        return {
            "sensors": {
                "status": "live" if sensors_live else "stale",
                "age_sec": sensor_age,
            },
            "cam": {
                "status": cam_status,
                "source": self.source,
                "remote": remote_cam,
                "tracking_active": tracking_active,
            },
            "ai": {
                "status": ai_status,
                "tracking_active": tracking_active,
                "face_present": self.face_present,
                "face_age_sec": face_age,
            },
        }

    def _extract_face_present(self, raw: Any) -> bool:
        if not isinstance(raw, dict):
            return False
        if "face_present" in raw and raw["face_present"] is not None:
            return bool(raw["face_present"])
        return False

    def _update_face_lost_clock(self, now: datetime, face_present: bool) -> None:
        if face_present:
            self._face_lost_since = None
            return
        if self._face_lost_since is None:
            self._face_lost_since = now

    def _face_lost_sec(self, now: datetime) -> float:
        if self._face_lost_since is None:
            return 0.0
        return max(0.0, (now - self._face_lost_since).total_seconds())

    def _away_sec(self, now: datetime) -> float:
        if self._away_since is None:
            return 0.0
        return max(0.0, (now - self._away_since).total_seconds())

    def _enter_away(self, now: datetime) -> None:
        self._accumulate_exposure(now)
        self._set_state(STATE_AWAY, now)
        if self._away_since is None:
            self._away_since = now
        self._exposure_tick_at = None
        self._push_led({"red": False, "green": False, "blink": False})

    def _accumulate_exposure(self, now: datetime) -> None:
        if self.state == STATE_MONITORING and self.face_present:
            if self._exposure_tick_at is not None:
                self.exposure_sec += max(0.0, (now - self._exposure_tick_at).total_seconds())
            self._exposure_tick_at = now
        else:
            self._exposure_tick_at = None

    def _set_state(self, state: str, now: datetime) -> None:
        self.state = state
        if self.session_id:
            history_store.update_session(self.session_id, state=state, exposure_sec=self.exposure_sec)

    def _close_session(self, reason: str) -> None:
        now = _utcnow()
        self._accumulate_exposure(now)
        self.state = STATE_ENDED
        self.ended_at = now
        if self.session_id:
            history_store.update_session(
                self.session_id,
                ended_at=_iso(now),
                exposure_sec=self.exposure_sec,
                state=STATE_ENDED,
                governor_json=governor.dump(self._gov),
            )
        self.last_session_id = self.session_id
        logger.info("Session %s closed (%s)", self.session_id, reason)

    def _idle_end(self) -> None:
        self._close_session("idle_timeout")
        self.state = STATE_IDLE
        self.session_id = None
        self.grace_until = None
        self._face_lost_since = None
        self._away_since = None
        self._exposure_tick_at = None
        self._push_led({"red": False, "green": False, "blink": False})

    def _update_flags_and_durations(self, raw: Any, reading: dict | None, now: datetime) -> None:
        flags: set[str] = set()
        if reading:
            for evt in reading.get("events") or []:
                flag = evt.get("flag")
                if flag and str(flag) not in governor.DISABLED_FLAGS:
                    flags.add(str(flag))
        camera: dict[str, float] = {}
        if isinstance(raw, dict) and isinstance(raw.get("flag_durations"), dict):
            for key, value in raw["flag_durations"].items():
                if isinstance(value, (int, float)):
                    camera[str(key)] = float(value)

        counting = self.state in (STATE_CALIBRATING, STATE_MONITORING)
        dt = 0.0
        if counting and self._duration_tick_at is not None:
            dt = min(max(0.0, (now - self._duration_tick_at).total_seconds()), 15.0)
        if counting:
            self._backend_durations = governor.tick_backend_durations(
                self._backend_durations, flags, dt
            )
            self._duration_tick_at = now
        else:
            self._duration_tick_at = None

        self.flag_set = sorted(flags)
        self.flag_durations = governor.merge_durations(camera, self._backend_durations, flags)

    def _run_governor(self, now: datetime, reading: dict | None) -> None:
        rules = processing.get_rules()
        qualified = governor.qualifying_flags(self.flag_set, self.flag_durations, rules)
        self.qualified_flags = qualified
        classified = governor.classify_severity(qualified, rules)
        in_grace = self.state == STATE_CALIBRATING
        can_fire = self.state == STATE_MONITORING
        self._gov, action = governor.tick(
            self._gov,
            now=now,
            severity=classified,
            flag_set=qualified,
            in_grace=in_grace,
            can_fire=can_fire,
            rules=rules,
            exposure_sec=self.exposure_sec,
        )
        if self._gov.get("escalated") and classified == governor.SEVERITY_ALERT:
            self.severity = governor.SEVERITY_ESCALATED
        else:
            self.severity = self._gov.get("severity") or classified

        if action == governor.ACTION_LLM and reading is not None:
            try:
                advice = llm_service.analyze_warning(
                    {**reading, "flag_set": qualified, "session_id": self.session_id, "severity": self.severity}
                )
            except Exception as err:
                logger.warning("Governor LLM failed: %s", err)
                advice = {
                    "spoken_line": governor.spoken_line_for_flags(qualified),
                    "summary": governor.spoken_line_for_flags(qualified),
                    "recommendations": [],
                    "model_name": "mock-advisor-v1",
                }
            try:
                firebase_client.push_advice(
                    advice,
                    {**reading, "session_id": self.session_id, "severity": self.severity, "flag_set": qualified},
                )
            except Exception as err:
                logger.warning("Advice push failed: %s", err)
            self._gov = governor.apply_llm_result(
                self._gov, flag_set=qualified, advice=advice, severity=self.severity
            )
        elif action == governor.ACTION_REPEAT:
            self._gov = governor.apply_repeat(self._gov, qualified)

        if action in (governor.ACTION_NONE, governor.ACTION_NOTICE) and governor.should_suggest_break(
            self._gov,
            exposure_sec=self.exposure_sec,
            in_grace=in_grace,
            can_fire=can_fire,
            rules=rules,
        ):
            self._gov = governor.mark_break_suggested(self._gov, classified, rules)

        if reading is not None:
            reading["severity"] = self.severity
            reading["qualified_flags"] = list(self.qualified_flags)
            reading["spoken_line"] = ((self._gov.get("pending_advice") or {}).get("spoken_line"))
            reading["llm_eligible"] = bool(self.flag_set) and not in_grace

    def _apply_led(self) -> None:
        payload = governor.led_from_severity(self.state, self.severity)
        self._push_led(payload)

    def _push_led(self, payload: dict) -> None:
        red = bool(payload.get("red"))
        green = bool(payload.get("green"))
        blink = bool(payload.get("blink"))
        key = (red, green, blink)
        if key == self._last_led:
            return
        try:
            firebase_client.push_led_state(red=red, green=green, blink=blink)
            self._last_led = key
        except Exception as err:
            logger.warning("LED push failed: %s", err)

    def _persist_governor(self) -> None:
        if self.session_id:
            history_store.update_session(
                self.session_id,
                governor_json=governor.dump(self._gov),
                state=self.state,
                exposure_sec=self.exposure_sec,
            )

    def _restore_open_session(self) -> None:
        row = history_store.get_open_session(self.device_id)
        if not row:
            return
        self.session_id = row["id"]
        self.last_session_id = row["id"]
        self.source = row.get("source")
        restored_state = row.get("state") or STATE_MONITORING
        self.state = restored_state if restored_state in ACTIVE_STATES else STATE_MONITORING
        self.exposure_sec = float(row.get("exposure_sec") or 0.0)
        self.started_at = _parse_iso(row.get("started_at"))
        self.grace_until = _parse_iso(row.get("grace_until"))
        self._gov = governor.load(row.get("governor_json"))
        self.severity = self._gov.get("severity") or governor.SEVERITY_NORMAL
        self._exposure_tick_at = _utcnow() if self.state == STATE_MONITORING else None
        self._duration_tick_at = _utcnow() if self.state in PERSIST_STATES else None
        logger.info("Restored open session %s in state %s", self.session_id, self.state)


def _age_sec(ts: datetime | None, now: datetime) -> float | None:
    if ts is None:
        return None
    return round(max(0.0, (now - ts).total_seconds()), 1)


def _is_remote_cam_source(source: str | None) -> bool:
    """ESP32 MJPEG URL vs local webcam index ('0', '1', ...)."""
    if not source:
        return False
    text = str(source).strip().lower()
    if text.startswith("http://") or text.startswith("https://") or text.startswith("rtsp://"):
        return True
    return False


def _sensor_value_hash(raw: Any) -> tuple:
    """Hash ESP32 sensor fields only. RTDB keeps stale values — presence is not liveness.

    Camera AI distance is excluded so a live webcam does not keep Sensors green
    after the board is unplugged.
    """
    if not isinstance(raw, dict):
        return _EMPTY_SENSOR_HASH
    light_adc = raw.get("light_adc")
    lux = raw.get("lux")
    if lux is None:
        lux = raw.get("brightness_lux")
    ultra = raw.get("ultrasonic_distance_cm")
    if ultra is None:
        ultra = raw.get("ultrasonic_distance")
    distance = ultra
    if distance is None:
        cam = raw.get("camera_distance_cm")
        board = raw.get("distance_cm")
        if board is not None and cam is not None and float(board) == float(cam):
            distance = None
        else:
            distance = board if board is not None else raw.get("distance")
    try:
        adc_key = None if light_adc is None else int(float(light_adc))
    except (TypeError, ValueError):
        adc_key = None
    try:
        lux_key = None if lux is None else round(float(lux), 1)
    except (TypeError, ValueError):
        lux_key = None
    try:
        dist_key = None if distance is None else round(float(distance), 1)
    except (TypeError, ValueError):
        dist_key = None
    return (adc_key, lux_key, dist_key)


_EMPTY_SENSOR_HASH = (None, None, None)


def _remaining_sec(until_iso: Any, now: datetime) -> float | None:
    until = _parse_iso(until_iso)
    if until is None:
        return None
    return round(max(0.0, (until - now).total_seconds()), 1)


def _parse_iso(value: Any) -> datetime | None:
    if not value or not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _generate_session_insight(
    device_id: str,
    session_id: str | None,
    extra_stats: dict | None = None,
) -> dict | None:
    if not session_id:
        return None
    try:
        import insight_service

        return insight_service.generate_insight(
            device_id, session_id=session_id, extra_stats=extra_stats
        )
    except Exception as err:
        logger.warning("Session insight failed: %s", err)
        return None
