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
_last_led_state: bool | None = None


async def _poll_once() -> None:
    global _last_ts, _last_led_state
    raw = await asyncio.to_thread(firebase_client.read_sensor_raw)
    reading = processing.process_reading(raw, default_device_id=config.DEFAULT_DEVICE_ID)
    if reading is None:
        return

    # Independent of the history dedupe below — the firmware needs this kept
    # current, but only written when it actually flips (avoid spamming Firebase
    # with an identical value every poll interval).
    led_on = processing.should_light_led(reading["risk_level"])
    if led_on != _last_led_state:
        await asyncio.to_thread(firebase_client.push_led_state, led_on)
        _last_led_state = led_on

    if reading["timestamp"] == _last_ts:
        return
    _last_ts = reading["timestamp"]
    history_store.insert_reading(reading)


async def run_poller() -> None:
    while True:
        try:
            await _poll_once()
        except Exception:
            logger.exception("Sensor poll failed")
        await asyncio.sleep(config.SENSOR_POLL_INTERVAL_SEC)
