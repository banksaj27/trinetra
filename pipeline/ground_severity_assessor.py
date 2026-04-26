from __future__ import annotations

import inspect
import math
import uuid
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable

ProgressCallback = Callable[[str], Awaitable[None] | None]
SEVERITY_LABELS = ("critical", "high", "moderate", "low")


async def _notify(callback: ProgressCallback | None, detail: str) -> None:
    if callback is None:
        return
    result = callback(detail)
    if inspect.isawaitable(result):
        await result


def _placeholder_log_probabilities(value: float) -> dict[str, float]:
    """Create model-like log probabilities from a normalized sensor reading."""
    # Class anchors mimic severity bands and let us output a full distribution.
    anchors = {
        "critical": 0.92,
        "high": 0.72,
        "moderate": 0.48,
        "low": 0.2,
    }
    logits = {
        label: -((value - anchor) ** 2) / 0.02
        for label, anchor in anchors.items()
    }
    max_logit = max(logits.values())
    log_denom = max_logit + math.log(sum(math.exp(logit - max_logit) for logit in logits.values()))
    return {label: round(logit - log_denom, 6) for label, logit in logits.items()}


def _probabilities_from_log_probs(log_probabilities: dict[str, float]) -> dict[str, float]:
    return {
        label: round(math.exp(log_probabilities[label]), 6)
        for label in SEVERITY_LABELS
    }


def _nearest_sensor_value(asset: dict[str, Any], sensor_points: list[dict[str, Any]]) -> float | None:
    if not sensor_points:
        return None
    asset_lat = float(asset["latitude"])
    asset_lon = float(asset["longitude"])
    nearest = min(
        sensor_points,
        key=lambda point: (asset_lat - float(point["lat"])) ** 2 + (asset_lon - float(point["lon"])) ** 2,
    )
    return float(nearest["reading_value"])


async def assess_ground_severity(
    assets: list[dict[str, Any]],
    sensor_points: list[dict[str, Any]],
    sensor_type: str,
    progress: ProgressCallback | None = None,
) -> list[dict[str, Any]]:
    """Eye2 scaffold: replace heuristic scoring with ground-sensor ML inference."""
    observations: list[dict[str, Any]] = []
    total = len(assets)

    for index, asset in enumerate(assets, start=1):
        value = _nearest_sensor_value(asset, sensor_points)
        if value is None:
            continue
        log_probabilities = _placeholder_log_probabilities(value)
        probabilities = _probabilities_from_log_probs(log_probabilities)
        severity = max(SEVERITY_LABELS, key=lambda label: probabilities[label])
        confidence = probabilities[severity]
        timestamp = datetime.now(timezone.utc)
        observations.append(
            {
                "observation_id": f"gso_{timestamp:%Y%m%d_%H%M%S}_{uuid.uuid4().hex[:6]}",
                "asset_id": str(asset["asset_id"]),
                "asset_type": str(asset["asset_type"]),
                "severity_level": severity,
                "confidence": round(confidence, 4),
                "source": "ground-sensor",
                "source_detail": "eye2_ground_sensor_v0",
                "sensor_type": sensor_type,
                "lat": float(asset["latitude"]),
                "lon": float(asset["longitude"]),
                "timestamp": timestamp.isoformat(),
                "scenario_time": None,
                "raw": {
                    "model": "eye2_placeholder_logprob_v0",
                    "nearest_reading": round(value, 4),
                    "sensor_count": len(sensor_points),
                    "log_probabilities": {
                        label: log_probabilities[label] for label in SEVERITY_LABELS
                    },
                    "class_probabilities": {
                        label: probabilities[label] for label in SEVERITY_LABELS
                    },
                },
            }
        )
        await _notify(progress, f"Eye2 ground severity {index}/{total} assets")

    return observations
