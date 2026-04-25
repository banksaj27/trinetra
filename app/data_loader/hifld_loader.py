"""Download and load HIFLD datasets into the infrastructure_assets table.

Each dataset is described by a DatasetConfig that maps remote ArcGIS REST API
fields to the local schema.  The loader paginates (max 2000 records per
request) and upserts via ON CONFLICT (hifld_id) DO UPDATE so re-runs are safe.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from json import JSONDecodeError
from typing import Any

import httpx
from shapely.geometry import Point, mapping, shape
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings

logger = logging.getLogger(__name__)

PAGE_SIZE = 2000


@dataclass
class FieldMapping:
    """Maps a remote GeoJSON property name to a local column value."""
    hifld_id: str
    name: str
    name_fallback: str | None = None
    hifld_id_fallbacks: list[str] = field(default_factory=list)
    name_fallbacks: list[str] = field(default_factory=list)


@dataclass
class DatasetSource:
    url: str
    state_field: str
    fields: FieldMapping
    extra_metadata_fields: list[str] = field(default_factory=list)


@dataclass
class DatasetConfig:
    label: str
    url: str
    asset_type: str
    criticality_tier: int
    state_field: str
    fields: FieldMapping
    extra_metadata_fields: list[str] = field(default_factory=list)
    fallback_sources: list[DatasetSource] = field(default_factory=list)


DATASETS: list[DatasetConfig] = [
    DatasetConfig(
        label="Electric Substations",
        url=(
            "https://services5.arcgis.com/HDRa0B57OVrv2E1q/ArcGIS/rest/services"
            "/Electric_Substations/FeatureServer/0/query"
        ),
        asset_type="substation",
        criticality_tier=1,
        state_field="STATE",
        fields=FieldMapping(hifld_id="ID", name="NAME"),
        extra_metadata_fields=["MAX_VOLT", "MIN_VOLT", "LINES", "STATUS"],
    ),
    DatasetConfig(
        label="Hospitals",
        url=(
            "https://services2.arcgis.com/FiaPA4ga0iQKduv3/ArcGIS/rest/services"
            "/Hospitals/FeatureServer/0/query"
        ),
        asset_type="hospital",
        criticality_tier=1,
        state_field="STATE",
        fields=FieldMapping(hifld_id="ID", name="NAME"),
        extra_metadata_fields=["TYPE", "STATUS", "BEDS", "TRAUMA"],
    ),
    DatasetConfig(
        label="Cellular Towers",
        url=(
            "https://services2.arcgis.com/FiaPA4ga0iQKduv3/ArcGIS/rest/services"
            "/Cellular_Towers_in_the_United_States/FeatureServer/0/query"
        ),
        asset_type="cell_tower",
        criticality_tier=2,
        state_field="LocState",
        fields=FieldMapping(
            hifld_id="UniqSysID",
            name="LocCity",
            name_fallback="LocCounty",
            hifld_id_fallbacks=["GLOBALID", "OBJECTID"],
            name_fallbacks=["LOCCITY", "LOCCOUNTY"],
        ),
        extra_metadata_fields=["LocAdd", "LocCity", "LocCounty", "LocState", "Licensee", "Callsign"],
    ),
    DatasetConfig(
        label="Fire Stations",
        url=(
            "https://start.kanini.com/arcgis/rest/services"
            "/START_v4/MapServer/6/query"
        ),
        asset_type="fire_station",
        criticality_tier=2,
        state_field="STATE",
        fields=FieldMapping(hifld_id="OBJECTID", name="NAME"),
        extra_metadata_fields=["ADDRESS", "CITY", "STATE", "ZIPCODE", "ADMINTYPE"],
    ),
    DatasetConfig(
        label="EMS Stations",
        url=(
            "https://services2.arcgis.com/FiaPA4ga0iQKduv3/arcgis/rest/services"
            "/Emergency_Medical_Service__EMS__Stations/FeatureServer/0/query"
        ),
        asset_type="ems_station",
        criticality_tier=2,
        state_field="STATE",
        fields=FieldMapping(hifld_id="OBJECTID", name="NAME", name_fallbacks=["Name"]),
        extra_metadata_fields=[
            "TELEPHONE",
            "ADDRESS",
            "CITY",
            "STATE",
            "STATE_PROV",
            "COUNTY",
            "TYPE",
        ],
    ),
    DatasetConfig(
        label="Water Treatment Plants",
        url=(
            "https://services2.arcgis.com/FiaPA4ga0iQKduv3/arcgis/rest/services"
            "/Water_Treatment_Plants/FeatureServer/0/query"
        ),
        asset_type="water_treatment",
        criticality_tier=1,
        state_field="PRIMACY_AGENCY_CODE",
        fields=FieldMapping(hifld_id="OBJECTID", name="PWSNAME", name_fallbacks=["NAME", "Name"]),
        extra_metadata_fields=[
            "PRIMACY_AGENCY_CODE",
            "PWSID",
            "PWSNAME",
            "OWNER_TYPE_CODE",
            "POPULATION_SERVED_COUNT",
        ],
        fallback_sources=[
            DatasetSource(
                url=(
                    "https://services.arcgis.com/P3ePLMYs2RVChkJx/arcgis/rest/services"
                    "/USA_Water_Bodies/FeatureServer/0/query"
                ),
                state_field="STATE",
                fields=FieldMapping(hifld_id="OBJECTID", name="NAME"),
                extra_metadata_fields=["FEATURE", "NAME", "STATE", "SQMI"],
            )
        ],
    ),
    DatasetConfig(
        label="911 Dispatch Centers",
        url=(
            "https://services2.arcgis.com/FiaPA4ga0iQKduv3/arcgis/rest/services"
            "/PSAP_911_Service_Area_Boundaries/FeatureServer/0/query"
        ),
        asset_type="911_center",
        criticality_tier=1,
        state_field="STATE",
        fields=FieldMapping(
            hifld_id="OBJECTID",
            name="PSAP_NAME",
            name_fallbacks=["psap_name", "NAME", "name"],
        ),
        extra_metadata_fields=[
            "PSAP_NAME",
            "psap_name",
            "COUNTY",
            "county",
            "STATE",
            "state",
            "TELEPHONE",
            "telephone",
        ],
    ),
]


def _require_arcgis_json(resp: httpx.Response, label: str) -> dict[str, Any]:
    try:
        data = resp.json()
    except JSONDecodeError as exc:
        raise RuntimeError(f"{label}: ArcGIS returned HTTP {resp.status_code}") from exc

    resp.raise_for_status()
    if "error" in data:
        message = data["error"].get("message", "ArcGIS query failed")
        details = data["error"].get("details") or []
        detail_text = f" ({'; '.join(details)})" if details else ""
        raise RuntimeError(f"{label}: {message}{detail_text}")

    return data


def _feature_properties(feature: dict[str, Any]) -> dict[str, Any]:
    return feature.get("properties") or feature.get("attributes") or {}


def _get_first(props: dict[str, Any], field_names: list[str]) -> Any:
    for field_name in field_names:
        if field_name in props and props[field_name] not in (None, ""):
            return props[field_name]

    lower_to_key = {key.lower(): key for key in props}
    for field_name in field_names:
        key = lower_to_key.get(field_name.lower())
        if key and props[key] not in (None, ""):
            return props[key]

    return None


async def _fetch_dataset_geojson(
    client: httpx.AsyncClient,
    config: DatasetConfig,
    source: DatasetSource,
    region: str,
) -> list[dict[str, Any]]:
    """Paginate through the ArcGIS REST query endpoint and return all features."""
    all_features: list[dict[str, Any]] = []
    offset = 0

    while True:
        params: dict[str, Any] = {
            "where": f"{source.state_field}='{region}'",
            "outFields": "*",
            "f": "geojson",
            "resultRecordCount": PAGE_SIZE,
            "resultOffset": offset,
        }
        logger.info(
            "  Fetching %s offset=%d ...", config.label, offset
        )
        resp = await client.get(source.url, params=params, timeout=60)
        data = _require_arcgis_json(resp, config.label)

        features = data.get("features", [])
        all_features.extend(features)

        if not features or not data.get("exceededTransferLimit", False):
            break
        offset += PAGE_SIZE

    return all_features


async def _fetch_dataset_geojson_with_fallbacks(
    client: httpx.AsyncClient,
    config: DatasetConfig,
    region: str,
) -> tuple[list[dict[str, Any]], DatasetSource]:
    primary_source = DatasetSource(
        url=config.url,
        state_field=config.state_field,
        fields=config.fields,
        extra_metadata_fields=config.extra_metadata_fields,
    )
    sources = [primary_source, *config.fallback_sources]
    failures: list[str] = []

    for source in sources:
        try:
            features = await _fetch_dataset_geojson(client, config, source, region)
        except Exception as exc:
            failures.append(f"{source.url}: {exc}")
            logger.warning("  Failed to fetch %s from %s: %s", config.label, source.url, exc)
            continue
        if features:
            if source is not primary_source:
                logger.warning(
                    "  Using fallback source for %s after primary returned no usable records",
                    config.label,
                )
            return features, source
        failures.append(f"{source.url}: 0 features")
        logger.warning("  No features returned for %s from %s", config.label, source.url)

    if config.asset_type == "water_treatment":
        logger.warning(
            "Water treatment plant data unavailable from HIFLD. "
            "This means water dependency edges cannot be inferred. "
            "Manual entry via POST /api/v1/assets is required."
        )
    if failures:
        raise RuntimeError("; ".join(failures))
    return [], primary_source


async def inspect_dataset_fields(client: httpx.AsyncClient, config: DatasetConfig) -> None:
    """Print one unfiltered sample feature so dataset schemas are visible."""
    params: dict[str, Any] = {
        "where": "1=1",
        "outFields": "*",
        "f": "json",
        "resultRecordCount": 1,
        "returnGeometry": "true",
        "outSR": 4326,
    }

    print(f"\n=== {config.label} ===")
    print(f"url: {config.url}")
    try:
        resp = await client.get(config.url, params=params, timeout=60)
        data = _require_arcgis_json(resp, config.label)
    except Exception as exc:
        print(f"error: {exc}")
        return

    fields = [field.get("name") for field in data.get("fields", []) if field.get("name")]
    print("fields:")
    for field_name in fields:
        print(f"  - {field_name}")

    features = data.get("features", [])
    if not features:
        print("sample: <no features returned>")
        return

    sample = _feature_properties(features[0])
    print("sample:")
    print(json.dumps(sample, indent=2, default=str))


async def inspect_all_dataset_fields() -> None:
    async with httpx.AsyncClient() as client:
        for config in DATASETS:
            await inspect_dataset_fields(client, config)


def _extract_name(props: dict[str, Any], fields: FieldMapping) -> str:
    if fields.name == "PSAP_NAME":
        psap_name = _get_first(props, ["PSAP_NAME", "psap_name", "NAME", "name"])
        if psap_name:
            return str(psap_name).strip()
        county = _get_first(props, ["COUNTY", "county"])
        if county:
            return f"{str(county).strip()} 911 Center"

    name_fields = [fields.name, *fields.name_fallbacks]
    if fields.name_fallback:
        name_fields.append(fields.name_fallback)
    name = _get_first(props, name_fields) or ""
    return str(name).strip() or "Unknown"


def _extract_hifld_id(props: dict[str, Any], fields: FieldMapping, config: DatasetConfig) -> str | None:
    raw = _get_first(props, [fields.hifld_id, *fields.hifld_id_fallbacks])
    if raw is None or str(raw).strip() == "":
        return None
    return f"{config.asset_type}_{raw}"


def _extract_metadata(props: dict[str, Any], extra_fields: list[str]) -> dict[str, Any]:
    return {k: props[k] for k in extra_fields if k in props and props[k] is not None}


UPSERT_SQL = text("""
    INSERT INTO infrastructure_assets
        (name, asset_type, criticality_tier, geometry, service_area, metadata, hifld_id)
    VALUES
        (:name, :asset_type, :criticality_tier,
         ST_SetSRID(ST_MakePoint(:lon, :lat), 4326),
         CASE
             WHEN CAST(:service_area AS text) IS NULL THEN NULL
             ELSE ST_SetSRID(ST_GeomFromGeoJSON(CAST(:service_area AS text)), 4326)
         END,
         CAST(:metadata AS jsonb), :hifld_id)
    ON CONFLICT (hifld_id) DO UPDATE SET
        name = EXCLUDED.name,
        asset_type = EXCLUDED.asset_type,
        criticality_tier = EXCLUDED.criticality_tier,
        geometry = EXCLUDED.geometry,
        service_area = COALESCE(EXCLUDED.service_area, infrastructure_assets.service_area),
        metadata = EXCLUDED.metadata,
        updated_at = now()
""")


async def load_dataset(
    session: AsyncSession,
    client: httpx.AsyncClient,
    config: DatasetConfig,
    region: str,
) -> int:
    """Load a single HIFLD dataset into the database. Returns count inserted."""
    features, source = await _fetch_dataset_geojson_with_fallbacks(client, config, region)

    count = 0
    for feat in features:
        props = _feature_properties(feat)
        geom = feat.get("geometry")
        if geom is None:
            continue

        shapely_geom = shape(geom)
        if shapely_geom.is_empty:
            continue
        point = shapely_geom if isinstance(shapely_geom, Point) else shapely_geom.centroid

        hifld_id = _extract_hifld_id(props, source.fields, config)
        if hifld_id is None:
            continue
        name = _extract_name(props, source.fields)
        meta = _extract_metadata(props, source.extra_metadata_fields)
        meta["source_url"] = source.url
        service_area = (
            json.dumps(mapping(shapely_geom))
            if config.asset_type == "911_center" and not isinstance(shapely_geom, Point)
            else None
        )

        await session.execute(
            UPSERT_SQL,
            {
                "name": name[:255],
                "asset_type": config.asset_type,
                "criticality_tier": config.criticality_tier,
                "lon": point.x,
                "lat": point.y,
                "service_area": service_area,
                "metadata": json.dumps(meta) if meta else None,
                "hifld_id": hifld_id,
            },
        )
        count += 1

    await session.commit()
    logger.info("  Loaded %d %s assets", count, config.label)
    return count


async def load_all_datasets(session: AsyncSession, region: str | None = None) -> dict[str, int]:
    """Load every configured HIFLD dataset. Returns {label: count} summary."""
    region = region or settings.HIFLD_REGION
    summary: dict[str, int] = {}

    async with httpx.AsyncClient() as client:
        for config in DATASETS:
            try:
                n = await load_dataset(session, client, config, region)
                summary[config.label] = n
            except Exception:
                await session.rollback()
                logger.exception("Failed to load %s", config.label)
                summary[config.label] = 0

    return summary
