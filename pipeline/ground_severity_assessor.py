"""
Eye 2 ground-sensor severity assessor.

For each infrastructure asset, applies the event-type-specific heuristic
function to every matching sensor row fetched from the ground dataset, aggregates
the resulting probability distributions (arithmetic mean across rows), and
produces a per-asset observation with the final damage classification.

Output keys per observation:
  eye_2_damage_class  — "no-damage" | "minor-damage" | "major-damage" | "destroyed"
  eye_2_class_probs   — log-probabilities for each of the four damage classes
"""

from __future__ import annotations

import inspect
import logging
import math
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable

log = logging.getLogger(__name__)

# Make the repo root importable so the eye2 heuristics package can be found.
_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from eye2.eye_2_disaster_hueristics.earthquake_hueristics import classify_earthquake
from eye2.eye_2_disaster_hueristics.flood_hueristics import classify_flood
from eye2.eye_2_disaster_hueristics.landslide_hueristics import classify_landslide
from eye2.eye_2_disaster_hueristics.storm_hueristics import classify_storm
from eye2.eye_2_disaster_hueristics.wildfire_hueristics import classify_wildfire

ProgressCallback = Callable[[str], Awaitable[None] | None]

DAMAGE_CLASSES = ("no-damage", "minor-damage", "major-damage", "destroyed")

_DAMAGE_TO_SEVERITY = {
    "no-damage":    "low",
    "minor-damage": "moderate",
    "major-damage": "high",
    "destroyed":    "critical",
}

_HEURISTIC_DISPATCH: dict[str, Callable[[dict], tuple[str, dict[str, float]]]] = {
    "earthquake": classify_earthquake,
    "flood":      classify_flood,
    "storm":      classify_storm,
    "wildfire":   classify_wildfire,
    "landslide":  classify_landslide,
}

# Fallback log-probs used when no sensor rows matched (slight no-damage bias).
_NO_DATA_LOG_PROBS: dict[str, float] = {
    "no-damage":    math.log(0.55),
    "minor-damage": math.log(0.25),
    "major-damage": math.log(0.13),
    "destroyed":    math.log(0.07),
}


async def _notify(callback: ProgressCallback | None, detail: str) -> None:
    if callback is None:
        return
    result = callback(detail)
    if inspect.isawaitable(result):
        await result


def _aggregate_log_probs(sensor_rows: list[dict[str, Any]], event_type: str) -> dict[str, float]:
    """
    Run the heuristic on every sensor row, then return the arithmetic-mean
    probability distribution converted back to log-probabilities.
    """
    classify_fn = _HEURISTIC_DISPATCH.get(event_type)
    if classify_fn is None or not sensor_rows:
        return _NO_DATA_LOG_PROBS

    # Accumulate raw probabilities across all rows.
    prob_sums: dict[str, float] = {cls: 0.0 for cls in DAMAGE_CLASSES}
    n_valid = 0

    for row in sensor_rows:
        try:
            _, log_probs = classify_fn(row)
        except Exception:
            continue
        for cls in DAMAGE_CLASSES:
            prob_sums[cls] += math.exp(log_probs.get(cls, -1e9))
        n_valid += 1

    if n_valid == 0:
        return _NO_DATA_LOG_PROBS

    avg_probs = {cls: prob_sums[cls] / n_valid for cls in DAMAGE_CLASSES}

    # Re-normalise (floating-point arithmetic may cause tiny deviation from 1.0).
    total = sum(avg_probs.values())
    avg_probs = {cls: avg_probs[cls] / total for cls in DAMAGE_CLASSES}

    return {cls: math.log(max(p, 1e-9)) for cls, p in avg_probs.items()}


async def assess_ground_severity(
    assets: list[dict[str, Any]],
    sensor_rows: list[dict[str, Any]],
    event_type: str,
    sensor_type: str,
    progress: ProgressCallback | None = None,
) -> list[dict[str, Any]]:
    """
    Produce one Eye 2 observation per asset using the aggregated heuristic result.

    The same aggregated damage classification is applied to every asset, since
    the ground-sensor data describes the event (not individual buildings).

    Parameters
    ----------
    assets      : Infrastructure asset dicts from the graph step.
    sensor_rows : Matching CSV rows returned by fetch_ground_sensor_data.
    event_type  : Detected event type (determines which heuristic to call).
    sensor_type : FEMA/USGS sensor category string (stored in output metadata).
    progress    : Async progress callback.

    Returns
    -------
    List of observation dicts, one per asset, each containing
    ``eye_2_damage_class`` and ``eye_2_class_probs``.
    Returns an empty list on any unhandled error so the aggregator can safely
    drop Eye 2's weight rather than crashing the pipeline.
    """
    try:
        return await _assess_ground_severity_impl(
            assets, sensor_rows, event_type, sensor_type, progress
        )
    except Exception as exc:
        log.error(
            "Eye 2 assess_ground_severity failed unexpectedly — "
            "returning empty list so aggregation can proceed without Eye 2: %s",
            exc,
            exc_info=True,
        )
        return []


async def _assess_ground_severity_impl(
    assets: list[dict[str, Any]],
    sensor_rows: list[dict[str, Any]],
    event_type: str,
    sensor_type: str,
    progress: ProgressCallback | None,
) -> list[dict[str, Any]]:
    # Aggregate once across all matching rows — same result for every asset.
    log_probs = _aggregate_log_probs(sensor_rows, event_type)
    damage_class = max(log_probs, key=log_probs.get)
    severity_level = _DAMAGE_TO_SEVERITY[damage_class]
    confidence = round(math.exp(log_probs[damage_class]), 4)

    observations: list[dict[str, Any]] = []
    total = len(assets)

    for index, asset in enumerate(assets, start=1):
        timestamp = datetime.now(timezone.utc)
        observations.append(
            {
                "observation_id": f"gso_{timestamp:%Y%m%d_%H%M%S}_{uuid.uuid4().hex[:6]}",
                "asset_id":       str(asset["asset_id"]),
                "asset_type":     str(asset["asset_type"]),
                # ── Eye 2 damage classification output ──────────────────────
                "eye_2_damage_class": damage_class,
                "eye_2_class_probs":  {cls: round(log_probs[cls], 6) for cls in DAMAGE_CLASSES},
                # ── Legacy severity field (derived from damage class) ────────
                "severity_level": severity_level,
                "confidence":     confidence,
                "source":         "ground-sensor",
                "source_detail":  f"eye2_heuristic_{event_type}_v1",
                "sensor_type":    sensor_type,
                "lat":            float(asset["latitude"]),
                "lon":            float(asset["longitude"]),
                "timestamp":      timestamp.isoformat(),
                "scenario_time":  None,
                "raw": {
                    "model":             f"eye2_{event_type}_heuristic_v1",
                    "event_type":        event_type,
                    "matched_row_count": len(sensor_rows),
                    "eye_2_damage_class": damage_class,
                    "eye_2_class_probs": {cls: round(log_probs[cls], 6) for cls in DAMAGE_CLASSES},
                },
            }
        )
        if index % 10 == 0 or index == total:
            await _notify(progress, f"Eye 2 scored {index}/{total} assets [{event_type}]")

    return observations
