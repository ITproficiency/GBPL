"""Load PostureCare rules from data/rules.json (single source of truth)."""

from __future__ import annotations

import json
from pathlib import Path

import config

RULES_PATH = Path(config.RULES_FILE_PATH)


def get_rules() -> dict:
    if not RULES_PATH.exists():
        raise FileNotFoundError(
            f"Missing {RULES_PATH}. Copy data/rules.json.example and edit."
        )
    data = json.loads(RULES_PATH.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or not data:
        raise ValueError(f"Invalid rules file: {RULES_PATH}")
    return data


def update_cooldowns(analyze_cooldown_sec: int, insight_cooldown_sec: int) -> dict:
    """Persist the LLM call rate limits into rules.json so they survive a restart
    and take effect immediately (rules.json is re-read on every request)."""
    data = get_rules()
    data.setdefault("llm", {})["analyze_cooldown_sec"] = analyze_cooldown_sec
    data.setdefault("insight", {})["cooldown_sec"] = insight_cooldown_sec
    RULES_PATH.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    return data


def update_sitting_demo(demo_mode: bool, demo_max_minutes: int | None = None) -> dict:
    """Persist the demo sitting threshold toggle. Does not touch too_long or risk_score."""
    data = get_rules()
    sitting = data.setdefault("sitting_minutes", {})
    sitting["demo_mode"] = bool(demo_mode)
    if demo_max_minutes is not None:
        sitting["demo_max_minutes"] = int(demo_max_minutes)
    elif sitting.get("demo_max_minutes") is None:
        sitting["demo_max_minutes"] = 3
    RULES_PATH.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    return data
