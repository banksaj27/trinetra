# NOTE: Buffer radii and ST_DWithin thresholds are specified in degrees.
# At San Jose latitudes, 0.1 degree is roughly 9-11km depending on direction.
#
# For production use outside PR, switch to:
#   - ST_DWithin(geom::geography, geom::geography, <meters>) for distance queries
#   - ST_Transform to a local projected CRS (e.g., EPSG:32620 UTM 20N for Caribbean)
#     for buffer operations
# See: https://postgis.net/docs/ST_DWithin.html

"""Service area generation and spatial dependency-edge inference.

Service areas are computed in Python (Voronoi / buffer), then written back to
the ``service_area`` column.  Dependency edges are inferred with PostGIS spatial
queries executed directly on the database.
"""
from __future__ import annotations

import json
import logging
import uuid

from shapely.geometry import MultiPoint, Point, Polygon, mapping
from shapely.ops import voronoi_diagram
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Service-area generation
# ---------------------------------------------------------------------------

VORONOI_TYPES = ("substation", "water_treatment")
SINGLE_PROVIDER_BUFFER_DEG = 0.5
CLIP_BUFFER_DEG = 0.1

_FETCH_POINTS_SQL = text("""
    SELECT id, ST_X(geometry) AS lon, ST_Y(geometry) AS lat
    FROM infrastructure_assets
    WHERE asset_type = :asset_type
""")

_UPDATE_SERVICE_AREA_SQL = text("""
    UPDATE infrastructure_assets
    SET service_area = ST_SetSRID(ST_GeomFromGeoJSON(:geojson), 4326),
        updated_at = now()
    WHERE id = :id
""")


async def _fetch_points(
    session: AsyncSession,
    asset_type: str,
) -> list[tuple[uuid.UUID, Point]]:
    result = await session.execute(_FETCH_POINTS_SQL, {"asset_type": asset_type})
    return [(row.id, Point(row.lon, row.lat)) for row in result]


def _build_voronoi_areas(
    points: list[tuple[uuid.UUID, Point]],
    clip_envelope: Polygon,
) -> list[tuple[uuid.UUID, Polygon]]:
    """Compute Voronoi polygons for a set of asset points, clipped to the region."""
    if len(points) == 1:
        uid, pt = points[0]
        return [(uid, pt.buffer(SINGLE_PROVIDER_BUFFER_DEG))]

    mp = MultiPoint([p for _, p in points])
    regions = voronoi_diagram(mp, envelope=clip_envelope)

    assignments: list[tuple[uuid.UUID, Polygon]] = []
    for uid, pt in points:
        for poly in regions.geoms:
            if poly.covers(pt):
                clipped = poly.intersection(clip_envelope)
                if not clipped.is_empty:
                    assignments.append((uid, clipped))
                break
        else:
            assignments.append((uid, pt.buffer(SINGLE_PROVIDER_BUFFER_DEG)))

    return assignments


async def generate_service_areas(session: AsyncSession) -> dict[str, int]:
    """Generate Voronoi service areas for provider asset types."""
    try:
        await session.execute(text("SELECT 1"))
    except Exception:
        await session.rollback()

    all_points_result = await session.execute(
        text("SELECT ST_X(geometry) AS lon, ST_Y(geometry) AS lat FROM infrastructure_assets")
    )
    all_coords = [(r.lon, r.lat) for r in all_points_result]

    if not all_coords:
        logger.warning("No assets found — skipping service area generation")
        return {}

    clip_envelope = MultiPoint([Point(lon, lat) for lon, lat in all_coords]).convex_hull.buffer(
        CLIP_BUFFER_DEG
    )

    summary: dict[str, int] = {}

    for asset_type in VORONOI_TYPES:
        points = await _fetch_points(session, asset_type)
        if not points:
            logger.warning("No %s providers found — skipping service-area generation", asset_type)
            continue
        assignments = _build_voronoi_areas(points, clip_envelope)
        for uid, poly in assignments:
            await session.execute(
                _UPDATE_SERVICE_AREA_SQL,
                {"geojson": json.dumps(mapping(poly)), "id": uid},
            )
        summary[asset_type] = len(assignments)
        logger.info("  Generated %d Voronoi service areas for %s", len(assignments), asset_type)

    return summary


# ---------------------------------------------------------------------------
# Spatial edge inference
# ---------------------------------------------------------------------------

_INFER_POWER_SQL = text("""
    INSERT INTO infrastructure_dependencies
        (upstream_asset_id, downstream_asset_id, dependency_type,
         criticality, failover_time_minutes, inferred, confidence)
    SELECT DISTINCT ON (asset.id)
        sub.id,
        asset.id,
        'power',
        CASE
            WHEN asset.criticality_tier = 1 THEN 'critical'
            WHEN asset.criticality_tier = 2 THEN 'degraded_ops'
            ELSE 'convenience'
        END,
        0,
        TRUE,
        0.7
    FROM infrastructure_assets asset
    JOIN infrastructure_assets sub
        ON sub.asset_type = 'substation'
        AND sub.service_area IS NOT NULL
        AND ST_Within(asset.geometry, sub.service_area)
    WHERE asset.asset_type != 'substation'
    ORDER BY asset.id, ST_Distance(asset.geometry, sub.geometry)
    ON CONFLICT ON CONSTRAINT uq_dependency_edge DO NOTHING
""")

_INFER_WATER_SQL = text("""
    INSERT INTO infrastructure_dependencies
        (upstream_asset_id, downstream_asset_id, dependency_type,
         criticality, failover_time_minutes, inferred, confidence)
    SELECT DISTINCT ON (asset.id)
        wtp.id,
        asset.id,
        'water',
        CASE
            WHEN asset.criticality_tier = 1 THEN 'critical'
            WHEN asset.criticality_tier = 2 THEN 'degraded_ops'
            ELSE 'convenience'
        END,
        240,
        TRUE,
        0.7
    FROM infrastructure_assets asset
    JOIN infrastructure_assets wtp
        ON wtp.asset_type = 'water_treatment'
        AND wtp.service_area IS NOT NULL
        AND ST_Within(asset.geometry, wtp.service_area)
    WHERE asset.asset_type IN ('hospital', 'shelter', 'fire_station')
    ORDER BY asset.id, ST_Distance(asset.geometry, wtp.geometry)
    ON CONFLICT ON CONSTRAINT uq_dependency_edge DO NOTHING
""")

# TODO: use geography type for accurate distance at any latitude
_INFER_COMMS_SQL = text("""
    INSERT INTO infrastructure_dependencies
        (upstream_asset_id, downstream_asset_id, dependency_type,
         criticality, failover_time_minutes, inferred, confidence)
    SELECT
        tower.id,
        asset.id,
        'communications',
        CASE
            WHEN asset.criticality_tier = 1 THEN 'critical'
            ELSE 'degraded_ops'
        END,
        0,
        TRUE,
        0.6
    FROM infrastructure_assets asset
    JOIN infrastructure_assets tower
        ON tower.asset_type = 'cell_tower'
        AND ST_DWithin(asset.geometry, tower.geometry, 0.1)
    WHERE asset.asset_type IN (
        'hospital',
        '911_center',
        'shelter',
        'fire_station',
        'ems_station',
        'police_station'
    )
        AND asset.id != tower.id
    ON CONFLICT ON CONSTRAINT uq_dependency_edge DO NOTHING
""")

# TODO: road_access inference requires OSM routing data — not yet implemented.


async def infer_power_edges(session: AsyncSession) -> int:
    """Create power dependency edges using substation Voronoi service areas."""
    result = await session.execute(_INFER_POWER_SQL)
    return result.rowcount


async def infer_water_edges(session: AsyncSession) -> int:
    """Create water dependency edges using water-treatment Voronoi service areas."""
    result = await session.execute(_INFER_WATER_SQL)
    return result.rowcount


async def infer_comms_edges(session: AsyncSession) -> int:
    """Create communications dependency edges from every nearby cell tower."""
    result = await session.execute(_INFER_COMMS_SQL)
    return result.rowcount


async def infer_dependency_edges(session: AsyncSession) -> dict[str, int]:
    """Run all spatial inference queries and return {type: rows_inserted}."""
    summary = {
        "power": await infer_power_edges(session),
        "water": await infer_water_edges(session),
        "communications": await infer_comms_edges(session),
    }

    for label, count in summary.items():
        logger.info("  Inferred %d %s dependency edges", count, label)

    return summary
