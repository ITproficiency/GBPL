"""
PostureCare LLM — prompts aligned with LLMs.py.

Uses OpenRouter (default: openrouter/free) with system + user messages.
"""

from __future__ import annotations

import json
import logging
import re
import ssl
import urllib.error
import urllib.request

import config
import processing

MOCK_MODEL = "mock-advisor-v1"
logger = logging.getLogger(__name__)


def _get_llm_config() -> tuple[str, str, str]:
    import os
    api_key = os.environ.get("OPENROUTER_API_KEY", "") or getattr(config, "OPENROUTER_API_KEY", "")
    model = os.environ.get("OPENROUTER_MODEL", "") or getattr(config, "OPENROUTER_MODEL", "openai/gpt-4o-mini")
    base_url = os.environ.get("OPENROUTER_BASE_URL", "") or getattr(config, "OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1")
    return api_key, model, base_url


def analyze_warning(reading: dict) -> dict:
    """Call LLM from a processed reading dict (from processing.process_reading)."""
    rules = processing.get_rules()
    system_prompt = rules["llm_prompt"]["system"]
    user_prompt = processing.build_llm_user_prompt(
        distance_cm=reading["distance_cm"] or 50.0,
        brightness_lux=reading["brightness_lux"] or 400.0,
        sitting_minutes=reading.get("sitting_minutes", 0),
        head_pitch=reading.get("head_pitch_deg"),
        head_roll=reading.get("head_roll_deg"),
        head_yaw=reading.get("head_yaw_deg"),
    )

    api_key, model, base_url = _get_llm_config()
    if api_key:
        try:
            text = _chat_completion(
                api_key=api_key,
                base_url=base_url,
                model=model,
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                referer="https://localhost",
                app_title="PostureCare",
            )
            return _parse_posturecare_response(text, model)
        except Exception as exc:
            logger.warning("OpenRouter failed: %s", exc)

    return _analyze_mock(reading)


def analyze_summary(system_prompt: str, user_prompt: str) -> dict:
    """Call LLM from an aggregated window summary (see insight_service.build_window_summary).

    Kept separate from analyze_warning() because that one is wired to a single
    reading's Context/Advice format; this one narrates over a summary instead.
    """
    api_key, model, base_url = _get_llm_config()
    if api_key:
        try:
            text = _chat_completion(
                api_key=api_key,
                base_url=base_url,
                model=model,
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                referer="https://localhost",
                app_title="PostureCare",
            )
            return {"summary": text.strip(), "model_name": model, "raw_text": text}
        except Exception as exc:
            logger.warning("OpenRouter failed: %s", exc)

    return _summary_mock()


def _summary_mock() -> dict:
    return {
        "summary": "Mock insight (no OpenRouter key set) — aggregated stats only, no narrative generated.",
        "model_name": MOCK_MODEL,
    }


def _chat_completion(
    api_key: str,
    base_url: str,
    model: str,
    system_prompt: str,
    user_prompt: str,
    referer: str | None = None,
    app_title: str | None = None,
) -> str:
    payload = json.dumps(
        {
            "model": model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": 0.7,
        }
    ).encode()

    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}",
    }
    if referer:
        headers["HTTP-Referer"] = referer
    if app_title:
        headers["X-Title"] = app_title

    url = f"{base_url.rstrip('/')}/chat/completions"
    req = urllib.request.Request(url, data=payload, headers=headers, method="POST")
    ctx = ssl._create_unverified_context()

    try:
        with urllib.request.urlopen(req, timeout=60, context=ctx) as resp:
            data = json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:
        body = exc.read().decode() if exc.fp else ""
        raise RuntimeError(f"LLM HTTP {exc.code}: {body}") from exc

    return data["choices"][0]["message"]["content"]


def _parse_posturecare_response(text: str, model_name: str) -> dict:
    """Parse PostureCare Context/Advice format into summary + recommendations."""
    summary = text.strip()
    recommendations: list[str] = []

    advice_match = re.search(r"Advice:\s*(.*)", text, re.IGNORECASE | re.DOTALL)
    context_match = re.search(r"Context:\s*(.*?)(?=Advice:|$)", text, re.IGNORECASE | re.DOTALL)

    if context_match:
        summary = context_match.group(1).strip()

    if advice_match:
        advice_block = advice_match.group(1).strip()
        for line in advice_block.splitlines():
            line = re.sub(r"^[-*•]\s*", "", line.strip())
            # Skip stray markdown syntax with no real content (e.g. a lone "**"
            # or "*" left over from a heading/separator the LLM emitted).
            if line and re.search(r"[A-Za-z0-9]", line):
                recommendations.append(line)

    if not recommendations:
        recommendations = [ln.strip() for ln in text.splitlines() if ln.strip()][:5]

    return {
        "summary": summary or text[:300],
        "recommendations": recommendations,
        "model_name": model_name,
        "raw_text": text,
    }


def _analyze_mock(reading: dict) -> dict:
    rules = processing.get_rules()
    recommendations: list[str] = []
    d_min = rules["distance_cm"]["target_min"]
    d_max = rules["distance_cm"]["target_max"]
    lux_min = rules["brightness_lux"]["target_min"]

    dist = reading.get("distance_cm")
    dist_val = dist if dist is not None else 50.0
    lux = reading.get("brightness_lux")
    lux_val = lux if lux is not None else 350.0

    if dist_val < d_min:
        recommendations.append(f"Move back to {d_min}-{d_max} cm from the screen.")
    elif dist_val > d_max:
        recommendations.append(f"Move closer to {d_min}-{d_max} cm from the screen.")

    if lux_val < lux_min:
        recommendations.append(f"Increase lighting to at least {lux_min} lux.")

    if reading.get("sitting_minutes", 0) > rules["sitting_minutes"]["max_continuous"]:
        recommendations.append("Stand up and stretch for 5 minutes.")

    if not recommendations:
        recommendations.append("Keep maintaining good posture and lighting.")

    summary = "; ".join(reading.get("warning_messages", []))
    return {
        "summary": f"PostureCare: {summary}",
        "recommendations": recommendations,
        "model_name": MOCK_MODEL,
    }
