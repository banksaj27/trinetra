from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

DependencyType = Literal["power", "water", "communications", "road_access", "fuel"]
CriticalityLevel = Literal["critical", "degraded_ops", "convenience"]


class DependencyCreate(BaseModel):
    upstream_asset_id: uuid.UUID
    downstream_asset_id: uuid.UUID
    dependency_type: DependencyType
    criticality: CriticalityLevel
    failover_time_minutes: int = Field(default=0, ge=0)


class DependencyResponse(BaseModel):
    id: uuid.UUID
    upstream_asset_id: uuid.UUID
    downstream_asset_id: uuid.UUID
    dependency_type: str
    criticality: str
    failover_time_minutes: int
    inferred: bool
    confidence: float
    created_at: datetime

    model_config = {"from_attributes": True}


class DependencyList(BaseModel):
    items: list[DependencyResponse]
    total_count: int
