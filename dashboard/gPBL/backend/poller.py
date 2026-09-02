from __future__ import annotations
"""Background poller — session_manager.tick() is the only writer.

The loop still boots with the API so idle preview stays fresh. Persistence,
LED, and presence clocks live inside the session engine. No LLM here.
"""


import asyncio
import logging

import config
import firebase_client
import session_manager

logger = logging.getLogger(__name__)


async def _poll_once() -> None:
    raw = await asyncio.to_thread(firebase_client.read_sensor_raw)
    await asyncio.to_thread(session_manager.get_manager().tick, raw)


async def run_poller() -> None:
    while True:
        try:
            await _poll_once()
        except Exception:
            logger.exception("Sensor poll failed")
        await asyncio.sleep(config.SENSOR_POLL_INTERVAL_SEC)
