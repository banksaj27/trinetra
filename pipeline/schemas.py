from __future__ import annotations

from datetime import date
from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator


DamageLevel = Literal["destroyed", "major-damage", "minor-damage", "no-damage"]
DependencyType = Literal["power", "water", "communications"]
EventType = Literal["earthquake", "flood", "storm", "wildfire", "landslide"]
GroundSensorType = Literal[
    "seismic",
    "hydrology",
    "air_quality",
    "weather_station",
    "soil_moisture",
    "multimodal",
]


class PipelineRunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    latitude: float
    longitude: float
    radius_km: float = Field(gt=0)
    disaster_date: str

    @field_validator("disaster_date")
    @classmethod
    def _validate_disaster_date(cls, value: str) -> str:
        date.fromisoformat(value)
        return value


class DamageObservation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    observation_id: str
    asset_id: str
    asset_type: str
    damage_level: DamageLevel
    confidence: float
    source: Literal["imagery"] = "imagery"
    source_detail: str
    lat: float
    lon: float
    timestamp: str
    scenario_time: Optional[str] = None
    raw: dict[str, Any]

    @field_validator("confidence")
    @classmethod
    def _round_confidence(cls, value: float) -> float:
        return round(float(value), 4)

    @field_validator("raw")
    @classmethod
    def _validate_raw(cls, value: dict[str, Any]) -> dict[str, Any]:
        required_raw_keys = {"model", "chip_size", "input_channels", "class_probabilities"}
        optional_raw_keys = {"pre_crop_b64", "post_crop_b64"}
        allowed_raw_keys = required_raw_keys | optional_raw_keys
        if not required_raw_keys.issubset(value) or set(value) - allowed_raw_keys:
            raise ValueError(
                f"raw must contain {sorted(required_raw_keys)} and only optional crop preview keys"
            )

        probabilities = value.get("class_probabilities")
        if not isinstance(probabilities, dict):
            raise ValueError("raw.class_probabilities must be an object")

        expected_probability_keys = {"no-damage", "minor-damage", "major-damage", "destroyed"}
        if set(probabilities) != expected_probability_keys:
            raise ValueError(
                "raw.class_probabilities must contain exactly no-damage, minor-damage, major-damage, destroyed"
            )

        value["model"] = str(value["model"])
        value["chip_size"] = int(value["chip_size"])
        value["input_channels"] = int(value["input_channels"])
        value["class_probabilities"] = {
            label: round(float(probabilities[label]), 4)
            for label in ("no-damage", "minor-damage", "major-damage", "destroyed")
        }
        for key in optional_raw_keys:
            if key in value and value[key] is not None:
                value[key] = str(value[key])
        return value


class GroundSensorObservation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    observation_id: str
    asset_id: str
    asset_type: str
    eye_2_damage_class: DamageLevel
    eye_2_class_probs: dict[str, float]
    severity_level: Literal["critical", "high", "moderate", "low"]
    confidence: float
    source: Literal["ground-sensor"] = "ground-sensor"
    source_detail: str
    sensor_type: GroundSensorType
    lat: float
    lon: float
    timestamp: str
    scenario_time: Optional[str] = None
    raw: dict[str, Any]

    @field_validator("confidence")
    @classmethod
    def _round_confidence(cls, value: float) -> float:
        return round(float(value), 4)

    @field_validator("eye_2_class_probs")
    @classmethod
    def _validate_eye2_probs(cls, value: dict) -> dict:
        expected = {"no-damage", "minor-damage", "major-damage", "destroyed"}
        if set(value) != expected:
            raise ValueError(f"eye_2_class_probs must contain exactly: {sorted(expected)}")
        return {k: round(float(v), 6) for k, v in value.items()}


class AssetRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    asset_id: str
    asset_type: str
    name: str
    latitude: float
    longitude: float
    criticality_tier: int
    metadata: dict[str, Any] = Field(default_factory=dict)


class DependencyEdge(BaseModel):
    model_config = ConfigDict(extra="forbid")

    upstream_id: str
    downstream_id: str
    dependency_type: DependencyType
    criticality: str


class TweetSentimentObservation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    observation_id: str
    eye_3_damage_class: Literal["no-damage", "minor-damage", "major-damage"]
    eye_3_class_probs: dict[str, float]
    confidence: float
    source: Literal["social-media"] = "social-media"
    source_detail: str
    tweet_text: str
    query: str
    lat: float
    lon: float
    timestamp: str
    raw: dict[str, Any]

    @field_validator("confidence")
    @classmethod
    def _round_confidence(cls, value: float) -> float:
        return round(float(value), 4)

    @field_validator("eye_3_class_probs")
    @classmethod
    def _validate_eye3_probs(cls, value: dict) -> dict:
        expected = {"no-damage", "minor-damage", "major-damage"}
        if set(value) != expected:
            raise ValueError(f"eye_3_class_probs must contain exactly: {sorted(expected)}")
        return {k: round(float(v), 6) for k, v in value.items()}


class PipelineResultResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_id: str
    detected_event_type: EventType
    assets: list[AssetRecord]
    edges: list[DependencyEdge]
    damage_observations: list[DamageObservation]
    ground_sensor_data_type: GroundSensorType
    ground_sensor_observations: list[GroundSensorObservation]
    tweet_sentiment_observation: Optional[TweetSentimentObservation] = None

