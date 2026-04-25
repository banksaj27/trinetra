from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

AssetType = Literal[
    "substation",
    "hospital",
    "water_treatment",
    "cell_tower",
    "shelter",
    "fire_station",
    "police_station",
    "911_center",
    "school",
    "wastewater",
    "fuel_depot",
    "data_center",
    "ems_station",
]


class AssetCreate(BaseModel):
    name: str = Field(..., max_length=255)
    asset_type: AssetType
    criticality_tier: int = Field(..., ge=1, le=4)
    latitude: float = Field(..., ge=-90, le=90)
    longitude: float = Field(..., ge=-180, le=180)
    population_served: int = Field(default=0, ge=0)
    backup_power_hours: float = Field(default=0.0, ge=0)
    metadata: dict[str, Any] | None = None
    hifld_id: str | None = None


class AssetUpdate(BaseModel):
    name: str | None = Field(default=None, max_length=255)
    asset_type: AssetType | None = None
    criticality_tier: int | None = Field(default=None, ge=1, le=4)
    latitude: float | None = Field(default=None, ge=-90, le=90)
    longitude: float | None = Field(default=None, ge=-180, le=180)
    population_served: int | None = Field(default=None, ge=0)
    backup_power_hours: float | None = Field(default=None, ge=0)
    metadata: dict[str, Any] | None = None


class AssetProperties(BaseModel):
    id: uuid.UUID
    name: str
    asset_type: str
    criticality_tier: int
    population_served: int
    backup_power_hours: float
    metadata: dict[str, Any] | None = None
    hifld_id: str | None = None
    created_at: datetime
    updated_at: datetime


class AssetFeature(BaseModel):
    """GeoJSON Feature representation of an asset."""
    type: Literal["Feature"] = "Feature"
    id: uuid.UUID
    geometry: dict[str, Any]
    properties: AssetProperties
    service_area: dict[str, Any] | None = None


class AssetCollection(BaseModel):
    """GeoJSON FeatureCollection of assets."""
    type: Literal["FeatureCollection"] = "FeatureCollection"
    features: list[AssetFeature]
    total_count: int


class AssetDependencies(BaseModel):
    asset_id: uuid.UUID
    upstream: list[DependencyResponse] = []
    downstream: list[DependencyResponse] = []


# Avoid circular import — the forward reference is resolved below.
from app.schemas.dependency import DependencyResponse  # noqa: E402

AssetDependencies.model_rebuild()
