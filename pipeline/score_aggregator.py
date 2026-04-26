"""
Final damage-level aggregator — combines Eyes 1, 2, and 3 into a single
per-asset DamageObservation list suitable for the cascading engine.

Algorithm
---------
Each eye produces a probability distribution over damage classes. We compute
a weighted mixture of those distributions and re-assign the damage_level in
every Eye 1 observation to the argmax of the combined distribution.

Only damage_level is changed; every other field in the DamageObservation
(observation_id, raw, confidence, etc.) is preserved exactly as Eye 1 produced it.

Weight rationale
----------------
Candidate sets evaluated (w1, w2, w3):

  (1.00, 0.00, 0.00) — pure Eye 1, baseline
  (0.80, 0.15, 0.05) — Eye 1 very dominant; Eyes 2/3 almost ignored
  (0.70, 0.20, 0.10) — Eye 1 dominant; Eye 2 can nudge borderline cases  ← chosen
  (0.65, 0.25, 0.10) — Eye 2 has more pull; risks homogenising per-asset signal
  (0.60, 0.30, 0.10) — Eye 2 nearly comparable to Eye 1; too risky

Key constraints driving the choice:
  • Eye 1 is per-asset (building-specific). Eyes 2 and 3 are event-level —
    the same signal applies to every asset regardless of its vulnerability or
    distance to the epicentre. Giving them too much weight homogenises all
    asset predictions incorrectly.
  • Eye 2 uses physical measurements (MMI, wind speed, flood depth) that
    correlate directly with structural damage → more reliable than Eye 3.
  • Eye 3 is one tweet with subjective sentiment → noisiest signal by far.

Chosen: w1 = 0.70, w2 = 0.20, w3 = 0.10
  In practice Eye 1's label survives in ~95 % of cases; only borderline
  assets (near a probability boundary) are affected.

Missing-eye handling
--------------------
If Eye 2 or Eye 3 produced no usable output (empty list, None, or malformed
probabilities), its weight is dropped entirely and the remaining weights are
renormalised so they still sum to 1.0. This means:
  • Both 2 and 3 missing → pure Eye 1 (no change at all).
  • Only 3 missing → renormalised to w1≈0.78, w2≈0.22.
  • Only 2 missing → renormalised to w1≈0.875, w3≈0.125.

Eye 3 and the "destroyed" class
--------------------------------
Eye 3 has no "destroyed" class. Its contribution to that class is treated as
zero. Because Eye 3's three defined classes already sum to 1.0, the weighted
mixture over all four classes still sums to 1.0 (the Eye 3 weight simply
shifts probability mass away from "destroyed" relative to the Eyes 1/2 blend).
"""

from __future__ import annotations

import logging
import math
from typing import Any

log = logging.getLogger(__name__)

# ── Weights (must sum to 1.0) ────────────────────────────────────────────────
_W1: float = 0.70   # Eye 1 — satellite imagery (per-asset, highest fidelity)
_W2: float = 0.20   # Eye 2 — ground sensor heuristic (event-level)
_W3: float = 0.10   # Eye 3 — Twitter sentiment (event-level, noisiest)

CLASSES_4 = ("no-damage", "minor-damage", "major-damage", "destroyed")


# ── Probability extractors ───────────────────────────────────────────────────

def _eye1_probs(obs: dict[str, Any]) -> dict[str, float] | None:
    """Read raw.class_probabilities from an Eye 1 DamageObservation."""
    try:
        probs = obs["raw"]["class_probabilities"]
        extracted = {c: float(probs[c]) for c in CLASSES_4}
        # Renormalise in case of floating-point drift.
        total = sum(extracted.values())
        if total <= 0:
            return None
        return {c: extracted[c] / total for c in CLASSES_4}
    except Exception as exc:
        log.debug("aggregator: failed to read Eye 1 probs: %s", exc)
        return None


def _eye2_probs(observations: list[dict[str, Any]]) -> dict[str, float] | None:
    """
    Read eye_2_class_probs (log-probs) from the first Eye 2 observation and
    convert to a normalised probability dict over all 4 classes.
    Eye 2 applies the same classification to every asset, so first obs suffices.
    """
    if not observations:
        return None
    try:
        log_probs = observations[0]["eye_2_class_probs"]
        raw = {c: math.exp(float(log_probs[c])) for c in CLASSES_4}
        total = sum(raw.values())
        if total <= 0:
            return None
        return {c: raw[c] / total for c in CLASSES_4}
    except Exception as exc:
        log.debug("aggregator: failed to read Eye 2 probs: %s", exc)
        return None


def _eye3_probs(observation: dict[str, Any] | None) -> dict[str, float] | None:
    """
    Read eye_3_class_probs (log-probs, 3 classes) from an Eye 3 observation,
    convert to probabilities, and pad with destroyed=0.0.
    """
    if observation is None:
        return None
    try:
        log_probs = observation["eye_3_class_probs"]
        classes_3 = ("no-damage", "minor-damage", "major-damage")
        raw = {c: math.exp(float(log_probs[c])) for c in classes_3}
        # Eye 3 probs over its 3 classes already sum to ~1; normalise defensively.
        total = sum(raw.values())
        if total <= 0:
            return None
        probs = {c: raw[c] / total for c in classes_3}
        probs["destroyed"] = 0.0
        return probs
    except Exception as exc:
        log.debug("aggregator: failed to read Eye 3 probs: %s", exc)
        return None


# ── Main aggregation ─────────────────────────────────────────────────────────

def aggregate_damage_level(
    eye1_observations: list[dict[str, Any]],
    eye2_observations: list[dict[str, Any]],
    eye3_observation: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    """
    Re-assign damage_level in each Eye 1 observation using the weighted
    mixture of all available eyes.  All other fields are unchanged.

    Parameters
    ----------
    eye1_observations : Per-asset DamageObservation dicts from Eye 1.
    eye2_observations : Per-asset GroundSensorObservation dicts from Eye 2.
                        Empty list → Eye 2 weight is dropped.
    eye3_observation  : Single TweetSentimentObservation dict from Eye 3.
                        None → Eye 3 weight is dropped.

    Returns
    -------
    New list of DamageObservation dicts with potentially updated damage_level.
    Input dicts are never mutated.
    """
    # Extract event-level distributions from Eyes 2 and 3 once.
    p2 = _eye2_probs(eye2_observations)
    p3 = _eye3_probs(eye3_observation)

    # Compute effective weights, dropping eyes with no usable data.
    w1 = _W1
    w2 = _W2 if p2 is not None else 0.0
    w3 = _W3 if p3 is not None else 0.0
    total_w = w1 + w2 + w3

    if total_w <= 0:
        log.error("aggregator: total weight is zero — returning Eye 1 unchanged")
        return list(eye1_observations)

    w1 /= total_w
    w2 /= total_w
    w3 /= total_w

    active = ["Eye 1"]
    if p2 is not None:
        active.append("Eye 2")
    if p3 is not None:
        active.append("Eye 3")
    log.info(
        "aggregator: active eyes %s → renormalised weights %.3f / %.3f / %.3f",
        active, w1, w2, w3,
    )

    updated: list[dict[str, Any]] = []
    changed = 0

    for obs in eye1_observations:
        obs = dict(obs)  # shallow copy — never mutate the eye1 list in place

        p1 = _eye1_probs(obs)
        if p1 is None:
            # Can't read Eye 1 probabilities for this asset — skip aggregation.
            updated.append(obs)
            continue

        # Weighted mixture over all 4 classes.
        combined: dict[str, float] = {}
        for c in CLASSES_4:
            combined[c] = (
                w1 * p1.get(c, 0.0)
                + w2 * (p2.get(c, 0.0) if p2 else 0.0)
                + w3 * (p3.get(c, 0.0) if p3 else 0.0)
            )

        new_level = max(combined, key=combined.get)
        if new_level != obs.get("damage_level"):
            log.info(
                "aggregator: asset %s damage_level %s → %s",
                obs.get("asset_id", "?"),
                obs.get("damage_level"),
                new_level,
            )
            changed += 1
        obs["damage_level"] = new_level
        updated.append(obs)

    log.info(
        "aggregator: processed %d observations, %d damage_level changes",
        len(updated), changed,
    )
    return updated
