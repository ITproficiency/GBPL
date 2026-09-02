"""
PostureCare LLM — prompts aligned with LLMs.py.

Uses OpenRouter (default: openrouter/free) with system + user messages.
"""

from __future__ import annotations

import json
import logging
import re
import urllib.error
import urllib.request

import config
import processing

MOCK_MODEL = "mock-advisor-v1"
logger = logging.getLogger(__name__)


EXPLAIN_SYSTEM = (
    'You are the AI coach for "PostureCare". Explain the current ergonomic state. '
    "Do not diagnose disease. The readings may already be within targets — "
    "do not invent a problem or urge a change unless a listed value is outside its target. "
    "If all listed readings are in range, say that clearly.\n\n"
    "Output format:\n"
    "- Context: What the current state is (not a fabricated issue).\n"
    "- Advice: If in-target, one line that no change is needed; otherwise short bullets.\n"
    "- Spoken: One short English sentence."
)


def analyze_warning(reading: dict, mode: str = "advice") -> dict:
    """Call LLM from a processed reading dict (from processing.process_reading)."""
    mode = "explain" if mode == "explain" else "advice"
    rules = processing.get_rules()
    system_prompt = EXPLAIN_SYSTEM if mode == "explain" else rules["llm_prompt"]["system"]
    flags = reading.get("flag_set") or [
        e.get("flag") for e in (reading.get("events") or []) if e.get("flag")
    ]
    flags = [str(f) for f in flags if f]
    user_prompt = processing.build_llm_user_prompt(
        distance_cm=reading.get("distance_cm"),
        brightness_lux=reading.get("brightness_lux"),
        sitting_minutes=reading.get("sitting_minutes", 0),
        head_pitch=reading.get("head_pitch_deg"),
        head_roll=reading.get("head_roll_deg"),
        head_yaw=reading.get("head_yaw_deg"),
        mode=mode,
        flag_set=flags,
        warning_messages=reading.get("warning_messages") or [],
    )

    if config.OPENROUTER_API_KEY:
        try:
            text = _chat_completion(
                api_key=config.OPENROUTER_API_KEY,
                base_url=config.OPENROUTER_BASE_URL,
                model=config.OPENROUTER_MODEL,
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                referer="https://localhost",
                app_title="PostureCare",
            )
            parsed = _parse_posturecare_response(text, config.OPENROUTER_MODEL)
            if not parsed.get("spoken_line"):
                if mode == "explain" and not flags:
                    parsed["spoken_line"] = "You are within target range."
                else:
                    parsed["spoken_line"] = _spoken_from_flags(flags)
            return parsed
        except Exception as exc:
            logger.warning("OpenRouter failed: %s", exc)

    return _analyze_mock(reading, mode=mode)


def analyze_summary(system_prompt: str, user_prompt: str) -> dict:
    """Call LLM from an aggregated window summary (see insight_service.build_window_summary).

    Kept separate from analyze_warning() because that one is wired to a single
    reading's Context/Advice format; this one narrates over a summary instead.
    """
    if config.OPENROUTER_API_KEY:
        try:
            text = _chat_completion(
                api_key=config.OPENROUTER_API_KEY,
                base_url=config.OPENROUTER_BASE_URL,
                model=config.OPENROUTER_MODEL,
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                referer="https://localhost",
                app_title="PostureCare",
            )
            return {"summary": text.strip(), "model_name": config.OPENROUTER_MODEL, "raw_text": text}
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

    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            data = json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:
        body = exc.read().decode() if exc.fp else ""
        raise RuntimeError(f"LLM HTTP {exc.code}: {body}") from exc

    return data["choices"][0]["message"]["content"]


def _spoken_from_flags(flags: list[str]) -> str:
    import notification_governor

    return notification_governor.spoken_line_for_flags(flags)


def _parse_spoken_line(text: str) -> str | None:
    match = re.search(r"^Spoken:\s*(.+)$", text, re.IGNORECASE | re.MULTILINE)
    if not match:
        return None
    line = match.group(1).strip().strip("\"'")
    return line or None


def _parse_posturecare_response(text: str, model_name: str) -> dict:
    """Parse PostureCare Context/Advice/Spoken format into summary + recommendations."""
    summary = text.strip()
    recommendations: list[str] = []
    spoken_line = _parse_spoken_line(text)

    advice_match = re.search(r"Advice:\s*(.*?)(?=^Spoken:|\Z)", text, re.IGNORECASE | re.DOTALL | re.MULTILINE)
    context_match = re.search(r"Context:\s*(.*?)(?=Advice:|$)", text, re.IGNORECASE | re.DOTALL)

    if context_match:
        summary = context_match.group(1).strip()

    if advice_match:
        advice_block = advice_match.group(1).strip()
        for line in advice_block.splitlines():
            line = re.sub(r"^[-*•]\s*", "", line.strip())
            if re.match(r"^Spoken:\s*", line, re.IGNORECASE):
                continue
            # Skip stray markdown syntax with no real content (e.g. a lone "**"
            # or "*" left over from a heading/separator the LLM emitted).
            if line and re.search(r"[A-Za-z0-9]", line):
                recommendations.append(line)

    if not recommendations:
        recommendations = [
            ln.strip()
            for ln in text.splitlines()
            if ln.strip() and not re.match(r"^(Context|Advice|Spoken):", ln.strip(), re.IGNORECASE)
        ][:5]

    return {
        "summary": summary or text[:300],
        "recommendations": recommendations,
        "spoken_line": spoken_line,
        "model_name": model_name,
        "raw_text": text,
    }


def _analyze_mock(reading: dict, mode: str = "advice") -> dict:
    flags = reading.get("flag_set") or [
        e.get("flag") for e in (reading.get("events") or []) if e.get("flag")
    ]
    flags = [str(f) for f in flags if f]

    if mode == "explain" and not flags:
        return {
            "summary": "Current readings are within PostureCare targets.",
            "recommendations": ["No adjustment needed right now."],
            "spoken_line": "You are within target range.",
            "model_name": MOCK_MODEL,
        }

    rules = processing.get_rules()
    recommendations: list[str] = []
    d_min = rules["distance_cm"]["target_min"]
    d_max = rules["distance_cm"]["target_max"]
    lux_min = rules["brightness_lux"]["target_min"]

    dist = reading.get("distance_cm")
    lux = reading.get("brightness_lux")

    if dist is not None and dist < d_min:
        recommendations.append(f"Move back to {d_min}-{d_max} cm from the screen.")
    elif dist is not None and dist > d_max:
        recommendations.append(f"Move closer to {d_min}-{d_max} cm from the screen.")

    if lux is not None and lux < lux_min:
        recommendations.append(f"Increase lighting to at least {lux_min} lux.")

    spoken_line = _spoken_from_flags(flags) if flags else "You are within target range."

    if not recommendations:
        recommendations.append(spoken_line if flags else "No adjustment needed right now.")

    summary = "; ".join(reading.get("warning_messages", []))
    if mode == "explain" and (not summary or "within PostureCare targets" in summary):
        summary = "Current readings are within PostureCare targets."
        spoken_line = spoken_line if flags else "You are within target range."
    return {
        "summary": f"PostureCare: {summary}" if summary else spoken_line,
        "recommendations": recommendations,
        "spoken_line": spoken_line,
        "model_name": MOCK_MODEL,
    }
