"""
Eye 3 — Twitter sentiment classifier.

Fetches the latest tweet about the disaster event and classifies its sentiment
as a structural damage level using cardiffnlp/twitter-roberta-base-sentiment.

Sentiment → damage class mapping:
  negative (LABEL_0) → major-damage
  neutral  (LABEL_1) → minor-damage
  positive (LABEL_2) → no-damage

Output keys:
  eye_3_damage_class  — "no-damage" | "minor-damage" | "major-damage"
  eye_3_class_probs   — log-probabilities for the three damage classes
"""

from __future__ import annotations

import asyncio
import inspect
import logging
import math
import uuid
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Optional

_log = logging.getLogger(__name__)

try:
    from .tweet_fetcher import fetch_latest_tweet
except ImportError:
    from tweet_fetcher import fetch_latest_tweet

ProgressCallback = Callable[[str], Awaitable[None] | None]

_MODEL_NAME = "cardiffnlp/twitter-roberta-base-sentiment"
_pipeline = None

_LABEL_TO_DAMAGE: dict[str, str] = {
    "LABEL_0": "major-damage",
    "LABEL_1": "minor-damage",
    "LABEL_2": "no-damage",
}

EYE3_CLASSES = ("no-damage", "minor-damage", "major-damage")


def _load_pipeline():
    global _pipeline
    if _pipeline is None:
        from transformers import pipeline as hf_pipeline
        _pipeline = hf_pipeline(
            "text-classification",
            model=_MODEL_NAME,
            return_all_scores=True,
        )


def _preprocess(text: str) -> str:
    tokens = []
    for t in text.split():
        if t.startswith("@") and len(t) > 1:
            t = "@user"
        elif t.startswith("http"):
            t = "http"
        tokens.append(t)
    return " ".join(tokens)


def _classify_text(text: str) -> tuple[str, dict[str, float]]:
    _load_pipeline()
    preprocessed = _preprocess(text)
    scores = _pipeline(preprocessed, truncation=True, max_length=512)[0]

    label_to_prob = {item["label"]: item["score"] for item in scores}

    log_probs: dict[str, float] = {}
    for label, damage_class in _LABEL_TO_DAMAGE.items():
        prob = max(label_to_prob.get(label, 1e-9), 1e-9)
        log_probs[damage_class] = math.log(prob)

    damage_class = max(log_probs, key=log_probs.get)
    return damage_class, log_probs


async def _notify(callback: ProgressCallback | None, detail: str) -> None:
    if callback is None:
        return
    result = callback(detail)
    if inspect.isawaitable(result):
        await result


async def assess_tweet_sentiment(
    latitude: float,
    longitude: float,
    disaster_date: str,
    event_type: str,
    progress: ProgressCallback | None = None,
) -> Optional[dict[str, Any]]:
    """
    Eye 3: fetch the latest disaster-related tweet and classify its sentiment.

    Parameters
    ----------
    latitude, longitude : Coordinates of the disaster epicentre (stored in output).
    disaster_date       : ISO date string used to bound the tweet search window.
    event_type          : Detected event type used as the primary search keyword.
    progress            : Async progress callback.

    Returns
    -------
    A single observation dict, or None if no tweet was found or any error occurred.
    Never raises — any unhandled failure returns None so the aggregator drops Eye 3's
    weight cleanly.
    """
    try:
        return await _assess_tweet_sentiment_impl(
            latitude, longitude, disaster_date, event_type, progress
        )
    except Exception as exc:
        _log.error(
            "Eye 3 assess_tweet_sentiment failed unexpectedly — "
            "returning None so aggregation proceeds without Eye 3: %s",
            exc,
            exc_info=True,
        )
        return None


async def _assess_tweet_sentiment_impl(
    latitude: float,
    longitude: float,
    disaster_date: str,
    event_type: str,
    progress: ProgressCallback | None,
) -> Optional[dict[str, Any]]:
    await _notify(progress, f"Eye 3 — searching Twitter for '{event_type}' tweets near {disaster_date}")

    try:
        tweet_text = await fetch_latest_tweet(event_type, disaster_date)
    except Exception as exc:
        await _notify(progress, f"Eye 3 — tweet fetch failed: {exc}")
        return None

    if tweet_text is None:
        await _notify(progress, "Eye 3 — no tweets found, skipping")
        return None

    await _notify(progress, f"Eye 3 — running sentiment analysis on tweet ({len(tweet_text)} chars)")

    try:
        damage_class, log_probs = await asyncio.to_thread(_classify_text, tweet_text)
    except Exception as exc:
        await _notify(progress, f"Eye 3 — sentiment model failed: {exc}")
        return None

    confidence = round(math.exp(log_probs[damage_class]), 4)
    timestamp = datetime.now(timezone.utc)

    return {
        "observation_id": f"eye3_{timestamp:%Y%m%d_%H%M%S}_{uuid.uuid4().hex[:6]}",
        "eye_3_damage_class": damage_class,
        "eye_3_class_probs": {cls: round(log_probs[cls], 6) for cls in EYE3_CLASSES},
        "confidence": confidence,
        "source": "social-media",
        "source_detail": "twitter_roberta_sentiment_v1",
        "tweet_text": tweet_text,
        "query": event_type,
        "lat": latitude,
        "lon": longitude,
        "timestamp": timestamp.isoformat(),
        "raw": {
            "model": _MODEL_NAME,
            "event_type": event_type,
            "tweet_text": tweet_text,
            "eye_3_damage_class": damage_class,
            "eye_3_class_probs": {cls: round(log_probs[cls], 6) for cls in EYE3_CLASSES},
        },
    }
