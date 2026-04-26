"""LLM-powered intent classifier for the TriNetra cascade agent."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Literal

from openai import AsyncOpenAI

import config

Intent = Literal["priorities", "cascade", "lookup", "help"]

_UUID_RE = re.compile(
    r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b",
    re.IGNORECASE,
)
_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.IGNORECASE | re.MULTILINE)
_VALID_DAMAGE = {"destroyed", "major", "minor", "none"}

_SYSTEM_PROMPT = """You are an intent classifier for TriNetra, an agent that \
analyzes infrastructure cascade failures in Puerto Rico.

Classify the user's message into exactly one of these intents:

1. "priorities" — user wants top/ranked/most-urgent cascades currently in the
   system. Examples: "what's the worst right now", "top 5 priorities",
   "show me the most critical cascades".
   Fields: limit (int, default 10).

2. "cascade" — user wants to simulate damage to a named asset. Examples:
   "what if the Bayamón substation is destroyed", "simulate Hospital Centro
   going down", "model the failure of cell tower 12".
   Fields: asset_query (string, the name/phrase identifying the asset),
   damage_level (one of: destroyed, major, minor, none — default destroyed).

3. "lookup" — user references a specific cascade by UUID. Example: "tell me
   about cascade c9f4e2a1-...".
   Fields: cascade_id (string UUID).

4. "help" — greetings, off-topic chatter, or "what can you do" questions.

Respond with ONLY a JSON object, no prose, no markdown fences. Schema:
{
  "intent": "priorities" | "cascade" | "lookup" | "help",
  "asset_query": string | null,
  "damage_level": "destroyed" | "major" | "minor" | "none" | null,
  "cascade_id": string | null,
  "limit": integer | null
}

Use null for fields that do not apply to the chosen intent."""


@dataclass
class RoutedQuery:
    intent: Intent
    params: dict[str, Any] = field(default_factory=dict)
    raw_text: str = ""


_client: AsyncOpenAI | None = None


def _get_client() -> AsyncOpenAI:
    global _client
    if _client is None:
        _client = AsyncOpenAI(
            api_key=config.ASI_API_KEY,
            base_url=config.ASI_BASE_URL,
        )
    return _client


def _keyword_fallback(text: str) -> RoutedQuery:
    lower = text.lower()
    if any(kw in lower for kw in ("priorit", "top", "rank", "worst", "urgent", "critical")):
        return RoutedQuery("priorities", {"limit": 10}, text)
    if any(kw in lower for kw in ("what if", "simulate", "destroy", "fail", "knock out")):
        return RoutedQuery(
            "cascade",
            {"asset_query": text.strip(), "damage_level": "destroyed"},
            text,
        )
    return RoutedQuery("help", {}, text)


def _parse_llm_json(content: str) -> dict[str, Any]:
    cleaned = _FENCE_RE.sub("", content).strip()
    data = json.loads(cleaned)
    if not isinstance(data, dict):
        raise ValueError("LLM response was not a JSON object")
    return data


def _build_query(data: dict[str, Any], raw: str) -> RoutedQuery:
    intent = data.get("intent")
    if intent not in ("priorities", "cascade", "lookup", "help"):
        raise ValueError(f"Invalid intent: {intent!r}")

    params: dict[str, Any] = {}

    if intent == "priorities":
        limit = data.get("limit")
        params["limit"] = max(2, min(int(limit), 50)) if isinstance(limit, (int, float)) and limit > 1 else 10

    elif intent == "cascade":
        asset_query = data.get("asset_query") or raw.strip()
        if not isinstance(asset_query, str) or not asset_query.strip():
            raise ValueError("cascade intent missing asset_query")
        damage = data.get("damage_level")
        if not isinstance(damage, str) or damage not in _VALID_DAMAGE:
            damage = "destroyed"
        params["asset_query"] = asset_query.strip()
        params["damage_level"] = damage

    elif intent == "lookup":
        cascade_id = data.get("cascade_id")
        if not isinstance(cascade_id, str) or not _UUID_RE.search(cascade_id):
            match = _UUID_RE.search(raw)
            if not match:
                raise ValueError("lookup intent missing valid cascade_id")
            cascade_id = match.group(0)
        params["cascade_id"] = cascade_id

    return RoutedQuery(intent, params, raw)


async def route(text: str) -> RoutedQuery:
    """Classify a user message into a structured intent."""
    raw = text or ""

    uuid_match = _UUID_RE.search(raw)
    if uuid_match:
        return RoutedQuery("lookup", {"cascade_id": uuid_match.group(0)}, raw)

    try:
        client = _get_client()
        response = await client.chat.completions.create(
            model=config.ASI_MODEL,
            temperature=0.0,
            max_tokens=300,
            messages=[
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": raw},
            ],
        )
        content = response.choices[0].message.content or ""
        data = _parse_llm_json(content)
        return _build_query(data, raw)
    except Exception:
        return _keyword_fallback(raw)


if __name__ == "__main__":
    import asyncio

    _TEST_QUERIES = [
        "what's the highest priority cascade right now?",
        "what if the Bayamón substation is destroyed?",
        "hello",
    ]

    async def _run_tests() -> None:
        for q in _TEST_QUERIES:
            result = await route(q)
            print(f"Query : {q!r}")
            print(f"Result: intent={result.intent!r}  params={result.params}")
            print()

    asyncio.run(_run_tests())
