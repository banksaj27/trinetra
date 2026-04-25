from __future__ import annotations

import uuid
from time import perf_counter

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db
from app.schemas.graph import GraphStats, Neighborhood, ValidationResult
from app.services.graph_builder import graph_service
from app.services.spatial_inference import (
    generate_service_areas,
    infer_comms_edges,
    infer_power_edges,
    infer_water_edges,
)

router = APIRouter(prefix="/api/v1/graph", tags=["graph"])


class GraphRebuildResponse(BaseModel):
    nodes: int
    edges: int
    duration_ms: int


class GraphBuildEdgesResponse(BaseModel):
    service_areas_generated: dict[str, int]
    edges_created: dict[str, int]
    total_edges: int


@router.get("/stats", response_model=GraphStats)
async def graph_stats():
    return graph_service.get_stats()


@router.post("/rebuild", response_model=GraphRebuildResponse)
async def rebuild_graph(
    db: AsyncSession = Depends(get_db),
    x_admin_key: str | None = Header(default=None, alias="X-Admin-Key"),
):
    if not settings.ADMIN_KEY or x_admin_key != settings.ADMIN_KEY:
        raise HTTPException(status_code=403, detail="Invalid admin key")

    started = perf_counter()
    await graph_service.rebuild(db)
    duration_ms = int((perf_counter() - started) * 1000)

    return GraphRebuildResponse(
        nodes=graph_service.graph.number_of_nodes(),
        edges=graph_service.graph.number_of_edges(),
        duration_ms=duration_ms,
    )


@router.post("/build-edges", response_model=GraphBuildEdgesResponse)
async def build_edges(db: AsyncSession = Depends(get_db)):
    service_areas_generated = await generate_service_areas(db)
    edges_created = {
        "power": await infer_power_edges(db),
        "water": await infer_water_edges(db),
        "communications": await infer_comms_edges(db),
    }
    total_edges = (
        await db.execute(text("SELECT count(*) FROM infrastructure_dependencies"))
    ).scalar_one()

    await db.commit()
    await graph_service.rebuild(db)

    return GraphBuildEdgesResponse(
        service_areas_generated=service_areas_generated,
        edges_created=edges_created,
        total_edges=total_edges,
    )


@router.get("/neighbors/{asset_id}", response_model=Neighborhood)
async def graph_neighbors(
    asset_id: uuid.UUID,
    depth: int = Query(2, ge=1, le=10),
    direction: str = Query("both", pattern="^(upstream|downstream|both)$"),
):
    return graph_service.get_neighbors(asset_id, depth=depth, direction=direction)


@router.post("/validate", response_model=ValidationResult)
async def graph_validate():
    return graph_service.validate()
