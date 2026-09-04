from __future__ import annotations
"""Background poller — syncs Firebase sensor data into SQLite and updates LED status.

Needed because readings only flowed through the API on-demand (dashboard poll
or /api/analyze) before this — nothing logged history for LLM window aggregation.
"""


import asyncio
import logging

import config
import firebase_client
import history_store
import processing

logger = logging.getLogger(__name__)

_last_ts: str | None = None
_last_led_state: tuple | None = None


async def _poll_once() -> None:
    global _last_ts, _last_led_state
    raw = await asyncio.to_thread(firebase_client.read_sensor_raw)
    reading = processing.process_reading(raw, default_device_id=config.DEFAULT_DEVICE_ID)
    if reading is None:
        return

    posture_status = reading.get("posture_status", "GOOD")
    risk_level = reading.get("risk_level", "normal")
    warning_msgs = reading.get("warning_messages", [])

    green_led = (posture_status == "GOOD")
    red_led = (posture_status in ["WARNING", "DANGER"] or risk_level in ["warning", "high"])
    buzzer = (posture_status == "DANGER")

    current_led_tuple = (green_led, red_led, buzzer, posture_status)
    if current_led_tuple != _last_led_state:
        await asyncio.to_thread(
            firebase_client.push_led_state,
            green_led=green_led,
            red_led=red_led,
            buzzer=buzzer,
            status=posture_status,
            warning_messages=warning_msgs
        )
        _last_led_state = current_led_tuple

    if reading["timestamp"] == _last_ts:
        return
    _last_ts = reading["timestamp"]
    history_store.insert_reading(reading)

    # Auto-generate & push AI advice when posture risk is elevated
    if reading.get("llm_eligible", False):
        try:
            import llm_service
            advice = llm_service.analyze_warning(reading)
            await asyncio.to_thread(firebase_client.push_advice, advice, reading)
        except Exception as err:
            logger.warning("Auto AI advice push failed: %s", err)


async def run_poller() -> None:
    while True:
        try:
            await _poll_once()
        except Exception:
            logger.exception("Sensor poll failed")
        await asyncio.sleep(config.SENSOR_POLL_INTERVAL_SEC)
