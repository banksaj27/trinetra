from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.schemas.dependency import DependencyCreate, DependencyList, DependencyResponse

router = APIRouter(prefix="/api/v1/dependencies", tags=["dependencies"])


@router.get("", response_model=DependencyList)
async def list_dependencies(
    dependency_type: str | None = None,
    upstream_asset_id: uuid.UUID | None = None,
    downstream_asset_id: uuid.UUID | None = None,
    inferred: bool | None = None,
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
):
    where = "WHERE 1=1"
    params: dict = {}
    if dependency_type:
        where += " AND dependency_type = :dep_type"
        params["dep_type"] = dependency_type
    if upstream_asset_id:
        where += " AND upstream_asset_id = :up_id"
        params["up_id"] = upstream_asset_id
    if downstream_asset_id:
        where += " AND downstream_asset_id = :down_id"
        params["down_id"] = downstream_asset_id
    if inferred is not None:
        where += " AND inferred = :inferred"
        params["inferred"] = inferred

    count_result = await db.execute(
        text(f"SELECT count(*) FROM infrastructure_dependencies {where}"), params
    )
    total = count_result.scalar_one()

    params["limit"] = limit
    params["offset"] = offset
    result = await db.execute(
        text(
            f"SELECT * FROM infrastructure_dependencies {where}"
            " ORDER BY created_at DESC LIMIT :limit OFFSET :offset"
        ),
        params,
    )
    items = [DependencyResponse.model_validate(r, from_attributes=True) for r in result]
    return DependencyList(items=items, total_count=total)


@router.post("", response_model=DependencyResponse, status_code=201)
async def create_dependency(body: DependencyCreate, db: AsyncSession = Depends(get_db)):
    if body.upstream_asset_id == body.downstream_asset_id:
        raise HTTPException(400, "upstream and downstream assets must differ")

    for asset_id in (body.upstream_asset_id, body.downstream_asset_id):
        check = await db.execute(
            text("SELECT 1 FROM infrastructure_assets WHERE id = :id"),
            {"id": asset_id},
        )
        if not check.first():
            raise HTTPException(404, f"Asset {asset_id} not found")

    result = await db.execute(
        text("""
            INSERT INTO infrastructure_dependencies
                (upstream_asset_id, downstream_asset_id, dependency_type,
                 criticality, failover_time_minutes, inferred, confidence)
            VALUES
                (:up, :down, :dep_type, :crit, :failover, FALSE, 1.0)
            RETURNING *
        """),
        {
            "up": body.upstream_asset_id,
            "down": body.downstream_asset_id,
            "dep_type": body.dependency_type,
            "crit": body.criticality,
            "failover": body.failover_time_minutes,
        },
    )
    row = result.first()
    await db.commit()
    return DependencyResponse.model_validate(row, from_attributes=True)


@router.get("/geo")
async def get_dependencies_geojson(
    dependency_type: str | None = None,
    db: AsyncSession = Depends(get_db),
):
    where = ""
    params: dict = {}
    if dependency_type:
        where = "WHERE dep.dependency_type = :dependency_type"
        params["dependency_type"] = dependency_type

    result = await db.execute(
        text(f"""
            SELECT
                dep.id,
                dep.upstream_asset_id,
                dep.downstream_asset_id,
                dep.dependency_type,
                dep.criticality,
                dep.failover_time_minutes,
                upstream.name AS upstream_name,
                upstream.asset_type AS upstream_type,
                downstream.name AS downstream_name,
                downstream.asset_type AS downstream_type,
                ST_X(upstream.geometry) AS upstream_lon,
                ST_Y(upstream.geometry) AS upstream_lat,
                ST_X(downstream.geometry) AS downstream_lon,
                ST_Y(downstream.geometry) AS downstream_lat
            FROM infrastructure_dependencies dep
            JOIN infrastructure_assets upstream
                ON upstream.id = dep.upstream_asset_id
            JOIN infrastructure_assets downstream
                ON downstream.id = dep.downstream_asset_id
            {where}
            ORDER BY dep.dependency_type, dep.created_at DESC
        """),
        params,
    )

    features = []
    for row in result:
        features.append(
            {
                "type": "Feature",
                "id": str(row.id),
                "geometry": {
                    "type": "LineString",
                    "coordinates": [
                        [row.upstream_lon, row.upstream_lat],
                        [row.downstream_lon, row.downstream_lat],
                    ],
                },
                "properties": {
                    "id": str(row.id),
                    "upstream_asset_id": str(row.upstream_asset_id),
                    "upstream_name": row.upstream_name,
                    "upstream_type": row.upstream_type,
                    "downstream_asset_id": str(row.downstream_asset_id),
                    "downstream_name": row.downstream_name,
                    "downstream_type": row.downstream_type,
                    "dependency_type": row.dependency_type,
                    "criticality": row.criticality,
                    "failover_time_minutes": row.failover_time_minutes,
                },
            }
        )

    return {
        "type": "FeatureCollection",
        "features": features,
    }


@router.delete("/{dep_id}", status_code=204)
async def delete_dependency(dep_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        text("DELETE FROM infrastructure_dependencies WHERE id = :id"),
        {"id": dep_id},
    )
    if result.rowcount == 0:
        raise HTTPException(404, "Dependency not found")
    await db.commit()
