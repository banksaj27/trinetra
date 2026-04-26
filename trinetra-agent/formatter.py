"""LLM-powered response formatter with deterministic fallbacks."""

from __future__ import annotations

import json
from typing import Any

from openai import AsyncOpenAI

import config

_SYSTEM_PROMPT = """You are a critical infrastructure cascade analyst. You receive JSON from a cascade engine and reply to a human in plain conversational English.

How to write:
- Lead with the affected location or asset name and the human cost (people impacted, critical facilities, time until first failure).
- Use the human-readable "name" field for assets. Translate time from minutes to hours or days.
- Keep it 2 to 4 sentences. Plain prose. No headers, bullets, or lists.

Never do this:
- Never include UUIDs, cascade_id, asset_id, observation_id, or any hex string in the response.
- Never use the word "cascade_id", "root_asset_id", or any other field name from the JSON.
- Never include priority scores or confidence numbers unless the user asked for ranking.
- Never invent numbers that aren't in the JSON."""


def _fallback_help() -> str:
    return (
        "I'm an infrastructure cascade impact analyst. I model how damage to one "
        "asset propagates downstream — which other assets fail, when, and how many "
        "people are affected. You can ask me three things:\n"
        "1. What the most urgent cascades are right now ('top priorities').\n"
        "2. What would happen if a specific asset is damaged ('what if [asset] is destroyed?').\n"
        "3. Details on a specific cascade by pasting its ID."
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
        return _fallback_help()
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
