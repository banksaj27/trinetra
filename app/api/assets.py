from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.schemas.asset import (
    AssetCollection,
    AssetCreate,
    AssetDependencies,
    AssetFeature,
    AssetProperties,
    AssetUpdate,
)
from app.schemas.dependency import DependencyResponse

router = APIRouter(prefix="/api/v1/assets", tags=["assets"])

_LIST_SQL = """
    SELECT
        id, name, asset_type, criticality_tier,
        population_served, backup_power_hours,
        metadata, hifld_id, created_at, updated_at,
        ST_AsGeoJSON(geometry)::json AS geom_json,
        ST_AsGeoJSON(service_area)::json AS sa_json
    FROM infrastructure_assets
    WHERE 1=1
"""

_COUNT_SQL = """
    SELECT count(*) FROM infrastructure_assets WHERE 1=1
"""


def _build_where(
    asset_type: str | None,
    criticality_tier: int | None,
    bbox: str | None,
) -> tuple[str, dict]:
    clauses = ""
    params: dict = {}
    if asset_type:
        clauses += " AND asset_type = :asset_type"
        params["asset_type"] = asset_type
    if criticality_tier is not None:
        clauses += " AND criticality_tier = :criticality_tier"
        params["criticality_tier"] = criticality_tier
    if bbox:
        parts = [float(x) for x in bbox.split(",")]
        if len(parts) != 4:
            raise HTTPException(400, "bbox must be minx,miny,maxx,maxy")
        clauses += " AND ST_Intersects(geometry, ST_MakeEnvelope(:minx,:miny,:maxx,:maxy, 4326))"
        params["minx"], params["miny"], params["maxx"], params["maxy"] = parts
    return clauses, params


def _row_to_feature(row) -> AssetFeature:
    props = AssetProperties(
        id=row.id,
        name=row.name,
        asset_type=row.asset_type,
        criticality_tier=row.criticality_tier,
        population_served=row.population_served,
        backup_power_hours=row.backup_power_hours,
        metadata=row.metadata,
        hifld_id=row.hifld_id,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )
    return AssetFeature(
        id=row.id,
        geometry=row.geom_json,
        properties=props,
        service_area=row.sa_json,
    )


@router.get("", response_model=AssetCollection)
async def list_assets(
    asset_type: str | None = None,
    criticality_tier: int | None = None,
    bbox: str | None = Query(None, description="minx,miny,maxx,maxy"),
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
):
    clauses, params = _build_where(asset_type, criticality_tier, bbox)
    params["limit"] = limit
    params["offset"] = offset

    count_result = await db.execute(text(_COUNT_SQL + clauses), params)
    total = count_result.scalar_one()

    result = await db.execute(
        text(_LIST_SQL + clauses + " ORDER BY created_at DESC LIMIT :limit OFFSET :offset"),
        params,
    )
    features = [_row_to_feature(r) for r in result]
    return AssetCollection(features=features, total_count=total)


@router.get("/export")
async def export_assets(
    asset_type: str | None = None,
    criticality_tier: int | None = Query(None, ge=1, le=4),
    bbox: str | None = Query(None, description="minx,miny,maxx,maxy"),
    db: AsyncSession = Depends(get_db),
):
    clauses = ""
    params: dict = {}
    if asset_type:
        clauses += " AND asset_type = :asset_type"
        params["asset_type"] = asset_type
    if criticality_tier is not None:
        clauses += " AND criticality_tier = :criticality_tier"
        params["criticality_tier"] = criticality_tier
    if bbox:
        try:
            parts = [float(x) for x in bbox.split(",")]
        except ValueError as exc:
            raise HTTPException(400, "bbox must be minx,miny,maxx,maxy") from exc
        if len(parts) != 4:
            raise HTTPException(400, "bbox must be minx,miny,maxx,maxy")
        clauses += " AND ST_Within(geometry, ST_MakeEnvelope(:minx,:miny,:maxx,:maxy, 4326))"
        params["minx"], params["miny"], params["maxx"], params["maxy"] = parts

    result = await db.execute(
        text(
            """
            SELECT
                id,
                name,
                asset_type,
                ST_Y(geometry) AS latitude,
                ST_X(geometry) AS longitude
            FROM infrastructure_assets
            WHERE 1=1
            """
            + clauses
            + " ORDER BY asset_type, name"
        ),
        params,
    )

    assets = [
        {
            "asset_id": str(row.id),
            "asset_type": row.asset_type,
            "name": row.name,
            "latitude": round(row.latitude, 6),
            "longitude": round(row.longitude, 6),
        }
        for row in result
    ]

    return {
        "assets": assets,
        "total": len(assets),
        "exported_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
    }


_GET_ONE_SQL = text("""
    SELECT
        id, name, asset_type, criticality_tier,
        population_served, backup_power_hours,
        metadata, hifld_id, created_at, updated_at,
        ST_AsGeoJSON(geometry)::json AS geom_json,
        ST_AsGeoJSON(service_area)::json AS sa_json
    FROM infrastructure_assets
    WHERE id = :id
""")


@router.get("/{asset_id}", response_model=AssetFeature)
async def get_asset(asset_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    result = await db.execute(_GET_ONE_SQL, {"id": asset_id})
    row = result.first()
    if not row:
        raise HTTPException(404, "Asset not found")
    return _row_to_feature(row)


@router.post("", response_model=AssetFeature, status_code=201)
async def create_asset(body: AssetCreate, db: AsyncSession = Depends(get_db)):
    meta_json = json.dumps(body.metadata) if body.metadata else None
    result = await db.execute(
        text("""
            INSERT INTO infrastructure_assets
                (name, asset_type, criticality_tier, geometry,
                 population_served, backup_power_hours, metadata, hifld_id)
            VALUES
                (:name, :asset_type, :criticality_tier,
                 ST_SetSRID(ST_MakePoint(:lon, :lat), 4326),
                 :population_served, :backup_power_hours,
                 CAST(:metadata AS jsonb), :hifld_id)
            RETURNING id
        """),
        {
            "name": body.name,
            "asset_type": body.asset_type,
            "criticality_tier": body.criticality_tier,
            "lon": body.longitude,
            "lat": body.latitude,
            "population_served": body.population_served,
            "backup_power_hours": body.backup_power_hours,
            "metadata": meta_json,
            "hifld_id": body.hifld_id,
        },
    )
    new_id = result.scalar_one()
    await db.commit()

    row = (await db.execute(_GET_ONE_SQL, {"id": new_id})).first()
    return _row_to_feature(row)


@router.patch("/{asset_id}", response_model=AssetFeature)
async def update_asset(
    asset_id: uuid.UUID,
    body: AssetUpdate,
    db: AsyncSession = Depends(get_db),
):
    updates = body.model_dump(exclude_unset=True)
    if not updates:
        raise HTTPException(400, "No fields to update")

    set_clauses = []
    params: dict = {"id": asset_id}

    if "latitude" in updates or "longitude" in updates:
        existing = (await db.execute(_GET_ONE_SQL, {"id": asset_id})).first()
        if not existing:
            raise HTTPException(404, "Asset not found")
        lon = updates.pop("longitude", existing.geom_json["coordinates"][0])
        lat = updates.pop("latitude", existing.geom_json["coordinates"][1])
        set_clauses.append("geometry = ST_SetSRID(ST_MakePoint(:lon, :lat), 4326)")
        params["lon"] = lon
        params["lat"] = lat

    for field_name, value in updates.items():
        if field_name == "metadata":
            set_clauses.append('"metadata" = CAST(:meta AS jsonb)')
            params["meta"] = json.dumps(value) if value else None
        else:
            set_clauses.append(f"{field_name} = :{field_name}")
            params[field_name] = value

    set_clauses.append("updated_at = now()")

    sql = f"UPDATE infrastructure_assets SET {', '.join(set_clauses)} WHERE id = :id"
    result = await db.execute(text(sql), params)
    if result.rowcount == 0:
        raise HTTPException(404, "Asset not found")
    await db.commit()

    row = (await db.execute(_GET_ONE_SQL, {"id": asset_id})).first()
    return _row_to_feature(row)


@router.delete("/{asset_id}", status_code=204)
async def delete_asset(asset_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        text("DELETE FROM infrastructure_assets WHERE id = :id"), {"id": asset_id}
    )
    if result.rowcount == 0:
        raise HTTPException(404, "Asset not found")
    await db.commit()


@router.get("/{asset_id}/dependencies", response_model=AssetDependencies)
async def get_asset_dependencies(
    asset_id: uuid.UUID,
    direction: str = Query("both", pattern="^(upstream|downstream|both)$"),
    dependency_type: str | None = None,
    db: AsyncSession = Depends(get_db),
):
    check = await db.execute(
        text("SELECT 1 FROM infrastructure_assets WHERE id = :id"), {"id": asset_id}
    )
    if not check.first():
        raise HTTPException(404, "Asset not found")

    type_clause = ""
    params: dict = {"id": asset_id}
    if dependency_type:
        type_clause = " AND dependency_type = :dep_type"
        params["dep_type"] = dependency_type

    upstream: list[DependencyResponse] = []
    downstream: list[DependencyResponse] = []

    if direction in ("upstream", "both"):
        rows = await db.execute(
            text(
                "SELECT * FROM infrastructure_dependencies"
                " WHERE downstream_asset_id = :id" + type_clause
            ),
            params,
        )
        upstream = [DependencyResponse.model_validate(r, from_attributes=True) for r in rows]

    if direction in ("downstream", "both"):
        rows = await db.execute(
            text(
                "SELECT * FROM infrastructure_dependencies"
                " WHERE upstream_asset_id = :id" + type_clause
            ),
            params,
        )
        downstream = [DependencyResponse.model_validate(r, from_attributes=True) for r in rows]

    return AssetDependencies(asset_id=asset_id, upstream=upstream, downstream=downstream)
