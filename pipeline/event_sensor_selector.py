from __future__ import annotations

from .schemas import EventType, GroundSensorType

EVENT_TO_SENSOR_TYPE: dict[EventType, GroundSensorType] = {
    "earthquake": "seismic",
    "flood":      "hydrology",
    "storm":      "weather_station",
    "wildfire":   "air_quality",
    "landslide":  "soil_moisture",
}


def select_ground_sensor_type(event_type: EventType) -> GroundSensorType:
    return EVENT_TO_SENSOR_TYPE.get(event_type, "multimodal")
