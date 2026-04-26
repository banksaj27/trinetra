"""LLM-powered response formatter with deterministic fallbacks."""

from __future__ import annotations

import json
from typing import Any

from openai import AsyncOpenAI

import config

_HELP_TEXT = (
    "I'm TriNetra, a disaster impact analyst for Puerto Rico infrastructure. "
    "I track 617 real assets — substations, hospitals, cell towers, and water "
    "treatment plants — and model how damage cascades downstream. You can ask "
    "me three things: (1) what the most urgent cascades are right now "
    "('top priorities'), (2) what would happen if a specific asset were hit "
    "('what if the Bayamón substation is destroyed?'), or (3) details on a "
    "specific cascade by pasting its UUID."
)

_SYSTEM_PROMPT = (
    "You are TriNetra, a disaster impact analyst presenting cascade analysis "
    "from real Puerto Rico infrastructure. Compose 2-5 sentences (up to 8 "
    "for complex results). Lead with the most urgent number. Name specific "
    "assets. Use human time units (hours/days, not minutes). Include "
    "cascade_id when relevant. Plain prose only — no markdown headers, "
    "bullets, or JSON. Don't invent numbers."
)

_PAYLOAD_LIMIT = 6000

_client: AsyncOpenAI | None = None


def _get_client() -> AsyncOpenAI:
    global _client
    if _client is None:
        _client = AsyncOpenAI(
            api_key=config.ASI_API_KEY,
            base_url=config.ASI_BASE_URL,
        )
    return _client


def _format_minutes(m: float | int | None) -> str:
    if m is None:
        return "unknown time"
    minutes = float(m)
    if minutes <= 0:
        return "immediately"
    if minutes < 60:
        return f"{int(round(minutes))} minutes"
    hours = minutes / 60
    if hours < 48:
        return f"{hours:.1f} hours"
    return f"{hours / 24:.1f} days"


def _root_name(affected: list[dict[str, Any]]) -> str:
    if affected and isinstance(affected[0], dict):
        return str(affected[0].get("name") or affected[0].get("asset_id") or "unknown asset")
    return "unknown asset"


def _fallback_priorities(items: list[dict[str, Any]]) -> str:
    if not items:
        return "No active cascades are currently ranked in the system."
    n = min(len(items), 5)
    lines = [f"Top {n} disaster cascades by priority:"]
    for i, c in enumerate(items[:n], 1):
        affected = c.get("affected_assets") or []
        name = _root_name(affected)
        score = c.get("priority_score", 0.0)
        total = c.get("total_affected", len(affected))
        pop = c.get("total_population_impacted", 0) or 0
        cid = str(c.get("cascade_id") or c.get("id") or "")
        lines.append(
            f"{i}. {name} — priority {score:.2f}, {total} assets, "
            f"{pop:,} people. ID: {cid[:8]}..."
        )
    return "\n".join(lines)


def _fallback_cascade(c: dict[str, Any]) -> str:
    affected = c.get("affected_assets") or []
    root = _root_name(affected)
    total = c.get("total_affected", len(affected))
    pop = c.get("total_population_impacted", 0) or 0
    score = c.get("priority_score", 0.0)
    cid = str(c.get("cascade_id") or c.get("id") or "")[:8]

    downstream = [a for a in affected if isinstance(a, dict) and (a.get("depth") or 0) > 0]
    downstream.sort(key=lambda a: a.get("failure_time_minutes") or 0)
    if downstream:
        first = downstream[0]
        first_line = (
            f" The first downstream failure is {first.get('name', 'an asset')} in "
            f"{_format_minutes(first.get('failure_time_minutes'))}."
        )
    else:
        first_line = ""

    return (
        f"Damage to {root} cascades into {total} affected assets impacting "
        f"{pop:,} people, with a priority score of {score:.2f}.{first_line} "
        f"Cascade ID: {cid}..."
    )


async def _llm_format(kind: str, data: Any, user_question: str) -> str:
    payload = json.dumps(data, default=str)[:_PAYLOAD_LIMIT]
    client = _get_client()
    response = await client.chat.completions.create(
        model=config.ASI_MODEL,
        temperature=0.3,
        max_tokens=500,
        messages=[
            {"role": "system", "content": _SYSTEM_PROMPT},
            {
                "role": "user",
                "content": (
                    f"User asked: {user_question}\n\n"
                    f"Result kind: {kind}\nResult JSON:\n{payload}"
                ),
            },
        ],
    )
    text = (response.choices[0].message.content or "").strip()
    if not text:
        raise ValueError("Empty LLM response")
    return text


async def format_response(kind: str, data: Any, user_question: str = "") -> str:
    if kind == "help":
        return _HELP_TEXT
    if kind == "error":
        return f"I couldn't reach the cascade engine: {data}"
    if kind == "no_match":
        return (
            f"I couldn't find an asset matching '{data}'. Try names like "
            "'Bayamón substation', 'Centro Medico', or paste a cascade UUID."
        )

    try:
        return await _llm_format(kind, data, user_question)
    except Exception:
        if kind == "priorities" and isinstance(data, list):
            return _fallback_priorities(data)
        if kind in ("cascade", "lookup") and isinstance(data, dict):
            return _fallback_cascade(data)
        return "I got a result back but couldn't format it."
