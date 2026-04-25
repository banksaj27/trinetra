from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel


class GraphStats(BaseModel):
    total_assets: int
    total_edges: int
    assets_by_type: dict[str, int]
    edges_by_type: dict[str, int]
    avg_in_degree: float
    avg_out_degree: float
    last_rebuilt_at: datetime | None = None


class NeighborNode(BaseModel):
    id: uuid.UUID
    name: str
    asset_type: str
    criticality_tier: int


class NeighborEdge(BaseModel):
    upstream_id: uuid.UUID
    downstream_id: uuid.UUID
    dependency_type: str
    criticality: str


class Neighborhood(BaseModel):
    center_id: uuid.UUID
    depth: int
    nodes: list[NeighborNode]
    edges: list[NeighborEdge]


class ValidationResult(BaseModel):
    orphan_assets: list[uuid.UUID]
    cycles: list[list[uuid.UUID]]
    cycles_truncated: bool
    disconnected_components: int
