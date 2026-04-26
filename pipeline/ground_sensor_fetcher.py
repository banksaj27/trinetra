from __future__ import annotations

import asyncio
from typing import Any

try:
    from .imagery_fetcher import bbox_from_center
except ImportError:  # Support `cd pipeline && uvicorn main:app`.
    from imagery_fetcher import bbox_from_center


def _within_bbox(lat: float, lng: float, bbox: list[float]) -> bool:
    min_lng, min_lat, max_lng, max_lat = bbox
    return min_lat <= lat <= max_lat and min_lng <= lng <= max_lng


def _fetch_ground_sensor_data_sync(
    latitude: float,
    longitude: float,
    radius_km: float,
    sensor_type: str,
) -> list[dict[str, Any]]:
    """Placeholder Eye2 data fetch with explicit location filtering by bbox."""
    bbox = bbox_from_center(latitude, longitude, radius_km)
    # TODO: Replace with live sensor provider query (USGS/NOAA/local IoT broker).
    candidate_points = [
        {
            "sensor_id": f"{sensor_type}-001",
            "sensor_type": sensor_type,
            "lat": latitude + 0.01,
            "lon": longitude + 0.01,
            "reading_value": 0.82,
            "reading_unit": "normalized",
            "timestamp": None,
        },
        {
            "sensor_id": f"{sensor_type}-002",
            "sensor_type": sensor_type,
            "lat": latitude - 0.018,
            "lon": longitude - 0.013,
            "reading_value": 0.54,
            "reading_unit": "normalized",
            "timestamp": None,
        },
        {
            "sensor_id": f"{sensor_type}-outside",
            "sensor_type": sensor_type,
            "lat": latitude + 1.5,
            "lon": longitude + 1.5,
            "reading_value": 0.15,
            "reading_unit": "normalized",
            "timestamp": None,
        },
    ]
    return [point for point in candidate_points if _within_bbox(point["lat"], point["lon"], bbox)]


async def fetch_ground_sensor_data(
    latitude: float,
    longitude: float,
    radius_km: float,
    sensor_type: str,
) -> list[dict[str, Any]]:
    return await asyncio.to_thread(
        _fetch_ground_sensor_data_sync,
        latitude,
        longitude,
        radius_km,
        sensor_type,
    )
