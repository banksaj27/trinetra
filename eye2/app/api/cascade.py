from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.cascade_analysis import CascadeAnalysisRecord
from app.services.cascade_engine import (
    CascadeAnalysis,
    DamageObservation,
    run_cascade,
)
from app.services.graph_builder import graph_service

router = APIRouter(prefix="/api/v1/analysis", tags=["cascade"])


class StoredCascadeAnalysis(CascadeAnalysis):
    id: uuid.UUID


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
    await db.commit()
    await db.refresh(record)

    return StoredCascadeAnalysis(id=record.id, **result.model_dump())


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
