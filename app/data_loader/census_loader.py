"""Load Census TIGER block-group boundaries and assign population_served.

Downloads the TIGER/Line shapefile for Puerto Rico block groups, fetches
population counts from the Census Decennial API, and spatially intersects
with each asset's service_area to compute proportional population served.
"""
from __future__ import annotations

import io
import logging
import tempfile
import zipfile
from pathlib import Path

import geopandas as gpd
import httpx
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings

logger = logging.getLogger(__name__)

TIGER_URL = "https://www2.census.gov/geo/tiger/TIGER2022/BG/tl_2022_72_bg.zip"

CENSUS_POP_URL = (
    "https://api.census.gov/data/2020/dec/dhc"
    "?get=P1_001N,GEO_ID"
    "&for=block%20group:*"
    "&in=state:72&in=county:*&in=tract:*"
)


async def _download_tiger_blockgroups(client: httpx.AsyncClient) -> gpd.GeoDataFrame:
    """Download and parse TIGER block-group shapefile for PR."""
    logger.info("  Downloading TIGER block groups ...")
    resp = await client.get(TIGER_URL, timeout=120, follow_redirects=True)
    resp.raise_for_status()

    with tempfile.TemporaryDirectory() as tmpdir:
        zpath = Path(tmpdir) / "bg.zip"
        zpath.write_bytes(resp.content)
        with zipfile.ZipFile(zpath) as zf:
            zf.extractall(tmpdir)
        shp_files = list(Path(tmpdir).glob("*.shp"))
        if not shp_files:
            raise FileNotFoundError("No .shp file in TIGER download")
        gdf = gpd.read_file(shp_files[0])

    gdf = gdf.to_crs(epsg=4326)
    return gdf


async def _fetch_population(client: httpx.AsyncClient) -> dict[str, int]:
    """Fetch block-group-level population from Census API. Returns {GEOID: pop}."""
    url = CENSUS_POP_URL
    if settings.CENSUS_API_KEY:
        url += f"&key={settings.CENSUS_API_KEY}"

    logger.info("  Fetching Census population data ...")
    resp = await client.get(url, timeout=60)
    resp.raise_for_status()
    rows = resp.json()

    pop_map: dict[str, int] = {}
    header = rows[0]
    for row in rows[1:]:
        record = dict(zip(header, row))
        state = record.get("state", "")
        county = record.get("county", "")
        tract = record.get("tract", "")
        bg = record.get("block group", "")
        geoid = f"{state}{county}{tract}{bg}"
        try:
            pop_map[geoid] = int(record.get("P1_001N", 0))
        except (ValueError, TypeError):
            pass

    return pop_map


_ASSETS_WITH_SERVICE_AREA_SQL = text("""
    SELECT id, ST_AsGeoJSON(service_area) AS sa_geojson
    FROM infrastructure_assets
    WHERE service_area IS NOT NULL
""")

_UPDATE_POP_SQL = text("""
    UPDATE infrastructure_assets
    SET population_served = :pop, updated_at = now()
    WHERE id = :id
""")


async def load_census_and_assign_population(session: AsyncSession) -> int:
    """Full census pipeline: download boundaries + pop, intersect, update assets.

    Returns number of assets updated.
    """
    async with httpx.AsyncClient() as client:
        gdf = await _download_tiger_blockgroups(client)
        pop_map = await _fetch_population(client)

    gdf["GEOID_SHORT"] = gdf["GEOID"].str.replace("^1500000US", "", regex=True)
    gdf["population"] = gdf["GEOID_SHORT"].map(pop_map).fillna(0).astype(int)

    result = await session.execute(_ASSETS_WITH_SERVICE_AREA_SQL)
    assets = result.fetchall()
    if not assets:
        logger.warning("  No assets with service areas — skipping population assignment")
        return 0

    import json
    from shapely.geometry import shape

    updated = 0
    for row in assets:
        sa_geom = shape(json.loads(row.sa_geojson))
        if sa_geom.is_empty:
            continue

        total_pop = 0
        for _, bg_row in gdf.iterrows():
            bg_geom = bg_row.geometry
            if bg_geom is None or bg_geom.is_empty:
                continue
            if not sa_geom.intersects(bg_geom):
                continue
            intersection = sa_geom.intersection(bg_geom)
            if intersection.is_empty:
                continue
            fraction = intersection.area / bg_geom.area if bg_geom.area > 0 else 0
            total_pop += int(bg_row["population"] * fraction)

        await session.execute(_UPDATE_POP_SQL, {"pop": total_pop, "id": row.id})
        updated += 1

    await session.commit()
    logger.info("  Updated population_served for %d assets", updated)
    return updated
