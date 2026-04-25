"""Generate synthetic infrastructure assets for testing without HIFLD downloads.

Creates ~200 random assets inside Puerto Rico's bounding box, inserts them,
then delegates to the standard service-area and edge-inference pipelines.
"""
from __future__ import annotations

import json
import logging
import random
import uuid

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

PR_LAT_MIN, PR_LAT_MAX = 17.9, 18.5
PR_LON_MIN, PR_LON_MAX = -67.3, -65.6

DEMO_SPEC: list[tuple[str, int, int]] = [
    # (asset_type, count, criticality_tier)
    ("substation", 20, 1),
    ("water_treatment", 10, 1),
    ("hospital", 50, 1),
    ("cell_tower", 80, 2),
    ("fire_station", 20, 2),
    ("ems_station", 10, 2),
    ("shelter", 10, 3),
]

_INSERT_SQL = text("""
    INSERT INTO infrastructure_assets
        (name, asset_type, criticality_tier, geometry, backup_power_hours, metadata, hifld_id)
    VALUES
        (:name, :asset_type, :criticality_tier,
         ST_SetSRID(ST_MakePoint(:lon, :lat), 4326),
         :backup_power_hours, CAST(:metadata AS jsonb), :hifld_id)
    ON CONFLICT (hifld_id) DO UPDATE SET
        name = EXCLUDED.name,
        updated_at = now()
""")


def _random_point() -> tuple[float, float]:
    return (
        random.uniform(PR_LON_MIN, PR_LON_MAX),
        random.uniform(PR_LAT_MIN, PR_LAT_MAX),
    )


async def generate_demo_data(session: AsyncSession) -> dict[str, int]:
    """Insert synthetic assets. Returns {asset_type: count}."""
    summary: dict[str, int] = {}

    for asset_type, count, tier in DEMO_SPEC:
        for i in range(count):
            lon, lat = _random_point()
            demo_id = f"demo_{asset_type}_{i}"
            name = f"Demo {asset_type.replace('_', ' ').title()} #{i + 1}"

            backup_hours = 0.0
            if asset_type == "hospital":
                backup_hours = random.choice([0, 4, 8, 12, 24, 48])

            meta = {"source": "demo_generator", "index": i}

            await session.execute(
                _INSERT_SQL,
                {
                    "name": name,
                    "asset_type": asset_type,
                    "criticality_tier": tier,
                    "lon": lon,
                    "lat": lat,
                    "backup_power_hours": backup_hours,
                    "metadata": json.dumps(meta),
                    "hifld_id": demo_id,
                },
            )
        summary[asset_type] = count

    await session.commit()
    total = sum(summary.values())
    logger.info("  Generated %d demo assets", total)
    return summary
