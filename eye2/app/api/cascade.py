from __future__ import annotations

import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.cascade_analysis import CascadeAnalysisRecord
from app.services.cascade_engine import (
    AffectedAsset,
    CascadeAnalysis,
    DamageObservation,
    run_cascade,
)
from app.services.graph_builder import graph_service

router = APIRouter(prefix="/api/v1/analysis", tags=["cascade"])


class StoredCascadeAnalysis(CascadeAnalysis):
    id: uuid.UUID


class PriorityAffectedAsset(AffectedAsset):
    name: str


class CascadePrioritySummary(BaseModel):
    id: uuid.UUID
    root_asset_id: uuid.UUID
    root_asset_name: str
    priority_score: float
    hours_to_first_critical_failure: float
    total_population_impacted: int
    critical_facilities_impacted: int
    created_at: datetime
    restoration_priority: int
    affected_assets: list[PriorityAffectedAsset]


def _lookup_name(asset_id: uuid.UUID | str) -> str:
    try:
        key = asset_id if isinstance(asset_id, uuid.UUID) else uuid.UUID(str(asset_id))
    except (ValueError, TypeError):
        return ""
    attrs = graph_service._node_attrs.get(key) or {}
    return str(attrs.get("name") or "")


@router.post("/cascade", response_model=StoredCascadeAnalysis)
async def create_cascade(
    observation: DamageObservation,
    db: AsyncSession = Depends(get_db),
) -> StoredCascadeAnalysis:
    try:
        result = run_cascade(observation, graph_service)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    record = CascadeAnalysisRecord(
        cascade_id=result.cascade_id,
        observation_id=result.triggered_by_observation_id,
        root_asset_id=result.root_asset_id,
        analysis_time=result.analysis_time,
        total_population_impacted=result.total_population_impacted,
        critical_facilities_impacted=result.critical_facilities_impacted,
        restoration_priority=result.restoration_priority,
        priority_score=result.priority_score,
        hours_to_first_critical_failure=result.hours_to_first_critical_failure,
        severity_multiplier=result.severity_multiplier,
        urgency_multiplier=result.urgency_multiplier,
        cascade=result.model_dump(mode="json"),
    )
    db.add(record)
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        record = (
            await db.execute(
                select(CascadeAnalysisRecord).where(
                    CascadeAnalysisRecord.cascade_id == result.cascade_id
                )
            )
        ).scalar_one()
    else:
        await db.refresh(record)

    analysis = CascadeAnalysis.model_validate(record.cascade)
    return StoredCascadeAnalysis(id=record.id, **analysis.model_dump())


@router.get("/priorities", response_model=list[CascadePrioritySummary])
async def list_priorities(
    limit: int = Query(10, ge=1, le=100),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
) -> list[CascadePrioritySummary]:
    stmt = (
        select(CascadeAnalysisRecord)
        .order_by(
            CascadeAnalysisRecord.priority_score.desc(),
            CascadeAnalysisRecord.created_at.desc(),
        )
        .limit(limit)
        .offset(offset)
    )
    rows = (await db.execute(stmt)).scalars().all()
    summaries: list[CascadePrioritySummary] = []
    for i, r in enumerate(rows):
        cascade_payload = r.cascade or {}
        raw_affected = (
            cascade_payload.get("impact_summary", {}).get("affected_assets", [])
            or cascade_payload.get("affected_assets", [])
            or []
        )
        affected = [
            PriorityAffectedAsset(
                **a,
                name=_lookup_name(a.get("asset_id")),
            )
            for a in raw_affected
        ]
        summaries.append(
            CascadePrioritySummary(
                id=r.id,
                root_asset_id=r.root_asset_id,
                root_asset_name=_lookup_name(r.root_asset_id),
                priority_score=r.priority_score,
                hours_to_first_critical_failure=r.hours_to_first_critical_failure,
                total_population_impacted=r.total_population_impacted,
                critical_facilities_impacted=r.critical_facilities_impacted,
                created_at=r.created_at,
                restoration_priority=offset + i + 1,
                affected_assets=affected,
            )
        )
    return summaries


@router.get("/cascade/{cascade_id}", response_model=StoredCascadeAnalysis)
async def get_cascade(
    cascade_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> StoredCascadeAnalysis:
    record = await db.get(CascadeAnalysisRecord, cascade_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Cascade analysis not found")

    analysis = CascadeAnalysis.model_validate(record.cascade)
    return StoredCascadeAnalysis(id=record.id, **analysis.model_dump())
