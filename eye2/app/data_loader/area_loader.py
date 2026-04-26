"""Radius-based HIFLD asset loading.

This loader fetches infrastructure features from ArcGIS REST query endpoints
using spatial filters instead of state filters, then upserts them as asset
nodes. It intentionally does not generate service areas or dependency edges.
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

logger = logging.getLogger(__name__)

PAGE_SIZE = 2000
MAX_RADIUS_KM = 200
_LOGGED_FIRST_ARCGIS_QUERY = False


@dataclass(frozen=True)
class FieldMapping:
    """Maps a remote GeoJSON property name to a local column value."""

    hifld_id: str
    name: str
    name_fallback: str | None = None


@dataclass(frozen=True)
class AreaDatasetSource:
    url: str
    fields: FieldMapping | None = None
    hifld_id_fallbacks: list[str] = field(default_factory=list)
    name_fallbacks: list[str] = field(default_factory=list)
    extra_metadata_fields: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class AreaDatasetConfig:
    key: str
    label: str
    url: str
    asset_type: str
    criticality_tier: int
    fields: FieldMapping
    hifld_id_fallbacks: list[str] = field(default_factory=list)
    name_fallbacks: list[str] = field(default_factory=list)
    extra_metadata_fields: list[str] = field(default_factory=list)
    fallback_sources: list[AreaDatasetSource] = field(default_factory=list)


AREA_DATASETS: dict[str, AreaDatasetConfig] = {
    "substations": AreaDatasetConfig(
        key="substations",
        label="Electric Substations",
        url=(
            "https://services5.arcgis.com/HDRa0B57OVrv2E1q/ArcGIS/rest/services"
            "/Electric_Substations/FeatureServer/0/query"
        ),
        asset_type="substation",
        criticality_tier=1,
        fields=FieldMapping(hifld_id="ID", name="NAME"),
        extra_metadata_fields=["MAX_VOLT", "MIN_VOLT", "LINES", "STATUS"],
    ),
    "hospitals": AreaDatasetConfig(
        key="hospitals",
        label="Hospitals",
        url=(
            "https://services2.arcgis.com/FiaPA4ga0iQKduv3/arcgis/rest/services"
            "/Hospitals/FeatureServer/0/query"
        ),
        asset_type="hospital",
        criticality_tier=1,
        fields=FieldMapping(hifld_id="ID", name="NAME"),
        extra_metadata_fields=["TYPE", "STATUS", "BEDS", "TRAUMA"],
    ),
    "cell_towers": AreaDatasetConfig(
        key="cell_towers",
        label="Cellular Towers",
        url=(
            "https://services2.arcgis.com/FiaPA4ga0iQKduv3/arcgis/rest/services"
            "/Cellular_Towers_in_the_United_States/FeatureServer/0/query"
        ),
        asset_type="cell_tower",
        criticality_tier=2,
        fields=FieldMapping(hifld_id="GLOBALID", name="LOCCITY", name_fallback="LOCCOUNTY"),
        hifld_id_fallbacks=["UniqSysID", "OBJECTID"],
        name_fallbacks=["LocCity", "LocCounty"],
        extra_metadata_fields=[
            "LOCADDR",
            "LOCCITY",
            "LOCCOUNTY",
            "STATE_CODE",
            "LocAdd",
            "LocCity",
            "LocCounty",
            "LocState",
            "Licensee",
            "Callsign",
        ],
    ),
    "fire_stations": AreaDatasetConfig(
        key="fire_stations",
        label="Fire Stations",
        url=(
            "https://start.kanini.com/arcgis/rest/services"
            "/START_v4/MapServer/6/query"
        ),
        asset_type="fire_station",
        criticality_tier=2,
        fields=FieldMapping(hifld_id="OBJECTID", name="NAME"),
        extra_metadata_fields=["ADDRESS", "CITY", "STATE", "ZIPCODE", "ADMINTYPE"],
    ),
    "ems_stations": AreaDatasetConfig(
        key="ems_stations",
        label="EMS Stations",
        url=(
            "https://services1.arcgis.com/wQnFk5ouCfPzTlPw/arcgis/rest/services"
            "/Emergency_Medical_Service_EMS_Stations/FeatureServer/0/query"
        ),
        asset_type="ems_station",
        criticality_tier=2,
        fields=FieldMapping(hifld_id="OBJECTID", name="NAME"),
        hifld_id_fallbacks=["FID"],
        name_fallbacks=["Name"],
        extra_metadata_fields=[
            "TELEPHONE",
            "ADDRESS",
            "CITY",
            "STATE",
            "COUNTY",
            "ZIP",
            "NAICSDESCR",
        ],
    ),
    "water_treatment": AreaDatasetConfig(
        key="water_treatment",
        label="Community Water Systems (SDWIS)",
        url=(
            "https://services.arcgis.com/cJ9YHowT8TU7DUyn/arcgis/rest/services"
            "/Community_Water_Systems_June_8_2024_Pts/FeatureServer/447/query"
        ),
        asset_type="water_treatment",
        criticality_tier=1,
        fields=FieldMapping(hifld_id="OBJECTID", name="PWS_NAME"),
        name_fallbacks=["PWSNAME", "NAME", "Name"],
        extra_metadata_fields=[
            "PWSID",
            "PWS_TYPE",
            "POPULATION_SERVED_COUNT",
            "SOURCE_WATER_TYPE",
            "PRIMACY_AGENCY",
            "EPA_REGION",
            "COUNTY_SERVED",
            "CITY_SERVED",
        ],
        fallback_sources=[
            AreaDatasetSource(
                url=(
                    "https://services.arcgis.com/P3ePLMYs2RVChkJx/arcgis/rest/services"
                    "/USA_Water_Bodies/FeatureServer/0/query"
                ),
                fields=FieldMapping(hifld_id="OBJECTID", name="NAME"),
                extra_metadata_fields=["FEATURE", "NAME", "STATE", "SQMI"],
            )
        ],
    ),
    "nine11_centers": AreaDatasetConfig(
        key="nine11_centers",
        label="911 Centers",
        url=(
            "https://services.arcgis.com/XG15cJAlne2vxtgt/arcgis/rest/services"
            "/911_Master_PSAP_Registry/FeatureServer/0/query"
        ),
        asset_type="911_center",
        criticality_tier=1,
        fields=FieldMapping(hifld_id="ObjectId", name="PSAP_Name"),
        hifld_id_fallbacks=["OBJECTID", "PSAP_ID"],
        name_fallbacks=["PSAP_NAME", "psap_name", "NAME", "name"],
        extra_metadata_fields=[
            "PSAP_ID",
            "PSAP_Name",
            "County",
            "City",
            "State",
            "Date_Last_Modified",
        ],
    ),
    "shelters": AreaDatasetConfig(
        key="shelters",
        label="Shelters",
        url=(
            "https://gis.fema.gov/arcgis/rest/services"
            "/NSS/FEMA_NSS/FeatureServer/5/query"
        ),
        asset_type="shelter",
        criticality_tier=2,
        fields=FieldMapping(hifld_id="shelter_id", name="shelter_name"),
        hifld_id_fallbacks=["objectid"],
        extra_metadata_fields=[
            "address_1",
            "city",
            "county_parish",
            "state",
            "zip",
            "evacuation_capacity",
            "post_impact_capacity",
            "shelter_status_code",
            "facility_type",
        ],
    ),
    "police_stations": AreaDatasetConfig(
        key="police_stations",
        label="Police Stations",
        url=(
            "https://services9.arcgis.com/FF3qnCUixr5w9JQi/arcgis/rest/services"
            "/US_HIFLD_Assets/FeatureServer/1/query"
        ),
        asset_type="police_station",
        criticality_tier=2,
        fields=FieldMapping(hifld_id="OBJECTID", name="Name"),
        extra_metadata_fields=["AssetType", "FacilityType", "water_level"],
    ),
}

DATASET_ALIASES = {
    "psap": "nine11_centers",
    "911_centers": "nine11_centers",
}

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


def _selected_configs(dataset_keys: list[str] | None) -> list[AreaDatasetConfig]:
    if not dataset_keys:
        return list(AREA_DATASETS.values())

    normalized_keys = [DATASET_ALIASES.get(key, key) for key in dataset_keys]
    unknown = sorted(set(normalized_keys) - set(AREA_DATASETS))
    if unknown:
        known = ", ".join(sorted(AREA_DATASETS))
        raise ValueError(f"Unknown datasets: {', '.join(unknown)}. Valid datasets: {known}")

    ordered_unique_keys = list(dict.fromkeys(normalized_keys))
    return [AREA_DATASETS[key] for key in ordered_unique_keys]


def _build_spatial_query_params(
    latitude: float,
    longitude: float,
    radius_km: float,
    offset: int,
) -> dict[str, Any]:
    radius_meters = int(radius_km * 1000)
    return {
        "geometry": json.dumps({"x": longitude, "y": latitude}),
        "geometryType": "esriGeometryPoint",
        "distance": radius_meters,
        "units": "esriSRUnit_Meter",
        "spatialRel": "esriSpatialRelIntersects",
        "inSR": 4326,
        "outSR": 4326,
        "outFields": "*",
        "f": "geojson",
        "resultRecordCount": PAGE_SIZE,
        "resultOffset": offset,
    }


def _log_first_arcgis_query(client: httpx.AsyncClient, url: str, params: dict[str, Any]) -> None:
    global _LOGGED_FIRST_ARCGIS_QUERY
    if _LOGGED_FIRST_ARCGIS_QUERY:
        return
    request = client.build_request("GET", url, params=params)
    logger.info("[loader] First ArcGIS query: %s", request.url)
    logger.info("[loader] First ArcGIS params: %s", params)
    _LOGGED_FIRST_ARCGIS_QUERY = True


def _exceeded_transfer_limit(data: dict[str, Any]) -> bool:
    return bool(
        data.get("exceededTransferLimit")
        or data.get("properties", {}).get("exceededTransferLimit")
    )


async def check_dataset_health(client: httpx.AsyncClient, name: str, url: str) -> dict:
    """Hit the endpoint with returnCountOnly=true and log the result."""

    params: dict[str, Any] = {
        "f": "json",
        "where": "1=1",
        "returnCountOnly": "true",
    }
    try:
        resp = await client.get(url, params=params, timeout=30)
        data = resp.json()
        resp.raise_for_status()
    except Exception as exc:
        return {
            "dataset": name,
            "url": url,
            "count": None,
            "ok": False,
            "error": str(exc),
        }

    if "error" in data:
        error = data["error"]
        message = error.get("message", "ArcGIS query failed") if isinstance(error, dict) else str(error)
        return {
            "dataset": name,
            "url": url,
            "count": None,
            "ok": False,
            "error": message,
        }

    count = data.get("count")
    ok = isinstance(count, int) and count > 0
    return {
        "dataset": name,
        "url": url,
        "count": count if isinstance(count, int) else None,
        "ok": ok,
        "error": None if ok else "0 records" if count == 0 else "missing count",
    }


def _log_dataset_health(health: list[dict]) -> None:
    logger.info("[loader] Dataset health check:")
    for result in health:
        dataset = result["dataset"]
        count = result.get("count")
        if result.get("ok"):
            logger.info("  %-17s ✓ %8s total records", dataset, f"{count:,}")
        else:
            logger.warning(
                "  %-17s ✗ ERROR %s",
                dataset,
                result.get("error") or "unknown",
            )


def _sources_for_config(config: AreaDatasetConfig) -> list[AreaDatasetSource]:
    primary_source = AreaDatasetSource(
        url=config.url,
        fields=config.fields,
        hifld_id_fallbacks=config.hifld_id_fallbacks,
        name_fallbacks=config.name_fallbacks,
        extra_metadata_fields=config.extra_metadata_fields,
    )
    return [primary_source, *config.fallback_sources]


async def check_config_health(
    client: httpx.AsyncClient,
    config: AreaDatasetConfig,
) -> dict:
    first_failure: dict | None = None
    for source in _sources_for_config(config):
        result = await check_dataset_health(client, config.key, source.url)
        if result.get("ok"):
            return result
        if first_failure is None:
            first_failure = result
    return first_failure or {
        "dataset": config.key,
        "url": config.url,
        "count": None,
        "ok": False,
        "error": "no sources configured",
    }


async def _fetch_dataset_geojson(
    client: httpx.AsyncClient,
    config: AreaDatasetConfig,
    source: AreaDatasetSource,
    latitude: float,
    longitude: float,
    radius_km: float,
) -> list[dict[str, Any]]:
    """Paginate through an ArcGIS REST spatial query and return all features."""

    all_features: list[dict[str, Any]] = []
    offset = 0

    while True:
        params = _build_spatial_query_params(latitude, longitude, radius_km, offset)
        logger.info("  Fetching %s offset=%d ...", config.label, offset)
        _log_first_arcgis_query(client, source.url, params)
        resp = await client.get(source.url, params=params, timeout=60)
        try:
            data = resp.json()
        except JSONDecodeError as exc:
            raise RuntimeError(
                f"{config.label}: ArcGIS returned HTTP {resp.status_code}"
            ) from exc
        resp.raise_for_status()
        if "error" in data:
            message = data["error"].get("message", "ArcGIS query failed")
            raise RuntimeError(f"{config.label}: {message}")

        features = data.get("features", [])
        all_features.extend(features)

        if not features or not _exceeded_transfer_limit(data):
            break
        offset += PAGE_SIZE

    return all_features


async def _fetch_dataset_geojson_with_fallbacks(
    client: httpx.AsyncClient,
    config: AreaDatasetConfig,
    latitude: float,
    longitude: float,
    radius_km: float,
) -> tuple[list[dict[str, Any]], AreaDatasetSource]:
    sources = _sources_for_config(config)
    failures: list[str] = []

    for source in sources:
        try:
            features = await _fetch_dataset_geojson(
                client, config, source, latitude, longitude, radius_km
            )
        except Exception as exc:
            failures.append(f"{source.url}: {exc}")
            logger.warning("  Failed to fetch %s from %s: %s", config.label, source.url, exc)
            continue
        if features:
            if source is not sources[0]:
                logger.warning(
                    "  Using fallback source for %s after primary returned no usable records",
                    config.label,
                )
            return features, source
        failures.append(f"{source.url}: 0 features")
        logger.warning("  No features returned for %s from %s", config.label, source.url)

    if config.key == "water_treatment":
        logger.warning(
            "Water treatment plant data unavailable from HIFLD. "
            "This means water dependency edges cannot be inferred. "
            "Manual entry via POST /api/v1/assets is required."
        )
    if failures:
        raise RuntimeError("; ".join(failures))
    return [], primary_source


async def inspect_area_dataset_fields() -> None:
    """Print one unfiltered sample row from each area-loader dataset."""
    async with httpx.AsyncClient() as client:
        for config in AREA_DATASETS.values():
            await inspect_url(client, config)
            params: dict[str, Any] = {
                "where": "1=1",
                "outFields": "*",
                "f": "json",
                "resultRecordCount": 1,
                "returnGeometry": "true",
                "outSR": 4326,
            }
            print(f"\n=== Area Loader: {config.label} ({config.key}) ===")
            print(f"url: {config.url}")
            try:
                resp = await client.get(config.url, params=params, timeout=60)
                data = resp.json()
                if "error" in data:
                    print(f"error: {data['error']}")
                    continue
            except Exception as exc:
                print(f"error: {exc}")
                continue

            fields = [field.get("name") for field in data.get("fields", []) if field.get("name")]
            print("fields:")
            for field_name in fields:
                print(f"  - {field_name}")

            features = data.get("features", [])
            if not features:
                print("sample: <no features returned>")
                continue

            props = features[0].get("properties") or features[0].get("attributes") or {}
            print("sample:")
            print(json.dumps(props, indent=2, default=str))


async def inspect_url(client: httpx.AsyncClient, config: AreaDatasetConfig) -> int | None:
    """Log whether a dataset URL is alive and how many records it exposes."""
    params: dict[str, Any] = {
        "f": "json",
        "where": "1=1",
        "returnCountOnly": "true",
    }
    try:
        resp = await client.get(config.url, params=params, timeout=60)
        data = resp.json()
        resp.raise_for_status()
    except Exception as exc:
        logger.warning("Dataset URL check failed for %s (%s): %s", config.label, config.url, exc)
        return None

    if "error" in data:
        logger.warning(
            "Dataset URL check returned ArcGIS error for %s (%s): %s",
            config.label,
            config.url,
            data["error"],
        )
        return None

    count = data.get("count")
    logger.info("Dataset URL check: %s has %s records (%s)", config.label, count, config.url)
    return count if isinstance(count, int) else None


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


def _extract_name(props: dict[str, Any], config: AreaDatasetConfig) -> str:
    if config.key == "cell_towers":
        city = str(_get_first(props, ["LOCCITY", "LocCity"]) or "").strip()
        county = str(_get_first(props, ["LOCCOUNTY", "LocCounty"]) or "").strip()
        if city and county:
            return f"{city}, {county} cell tower"
        if city or county:
            return f"{city or county} cell tower"

    if config.key == "nine11_centers":
        psap_name = _get_first(props, ["PSAP_Name", "PSAP_NAME", "psap_name", "NAME", "name"])
        if psap_name:
            return str(psap_name).strip()
        county = _get_first(props, ["County", "COUNTY", "county"])
        if county:
            return f"{str(county).strip()} 911 Center"

    name_fields = [config.fields.name, *config.name_fallbacks]
    if config.fields.name_fallback:
        name_fields.append(config.fields.name_fallback)
    name = _get_first(props, name_fields) or ""
    return str(name).strip() or "Unknown"


def _extract_hifld_id(props: dict[str, Any], config: AreaDatasetConfig) -> str | None:
    raw = _get_first(props, [config.fields.hifld_id, *config.hifld_id_fallbacks])
    if raw is None or str(raw).strip() == "":
        return None
    return f"{config.asset_type}_{str(raw).strip()}"


def _extract_metadata(props: dict[str, Any], extra_fields: list[str]) -> dict[str, Any]:
    return {k: props[k] for k in extra_fields if k in props and props[k] is not None}


async def load_area_dataset(
    session: AsyncSession,
    client: httpx.AsyncClient,
    config: AreaDatasetConfig,
    latitude: float,
    longitude: float,
    radius_km: float,
) -> int:
    """Load one HIFLD dataset within a radius. Returns the upserted row count."""

    features, source = await _fetch_dataset_geojson_with_fallbacks(
        client, config, latitude, longitude, radius_km
    )

    count = 0
    for feat in features:
        props = feat.get("properties", {})
        geom = feat.get("geometry")
        if geom is None:
            continue

        shapely_geom = shape(geom)
        if shapely_geom.is_empty:
            continue
        point = shapely_geom if isinstance(shapely_geom, Point) else shapely_geom.centroid

        hifld_id = _extract_hifld_id(props, config)
        if hifld_id is None:
            continue

        meta = _extract_metadata(props, source.extra_metadata_fields)
        meta["source_dataset"] = config.key
        meta["source_url"] = source.url
        service_area = (
            json.dumps(mapping(shapely_geom))
            if config.key == "nine11_centers" and not isinstance(shapely_geom, Point)
            else None
        )

        await session.execute(
            UPSERT_SQL,
            {
                "name": _extract_name(props, config)[:255],
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


async def load_area_datasets(
    session: AsyncSession,
    latitude: float,
    longitude: float,
    radius_km: float,
    dataset_keys: list[str] | None = None,
) -> dict[str, int]:
    """Load selected HIFLD datasets within a radius. Returns {dataset_key: count}."""

    configs = _selected_configs(dataset_keys)
    summary: dict[str, int] = {}

    async with httpx.AsyncClient() as client:
        health = [
            await check_config_health(client, config)
            for config in AREA_DATASETS.values()
        ]
        _log_dataset_health(health)
        healthy_keys = {result["dataset"] for result in health if result.get("ok")}

        for config in configs:
            if config.key not in healthy_keys:
                logger.warning("  Skipping %s because health check failed", config.label)
                summary[config.key] = 0
                continue

            try:
                n = await load_area_dataset(
                    session=session,
                    client=client,
                    config=config,
                    latitude=latitude,
                    longitude=longitude,
                    radius_km=radius_km,
                )
            except Exception as exc:
                logger.warning(
                    "  Skipping %s after load failure from %s: %s",
                    config.label,
                    config.url,
                    exc,
                )
                await session.rollback()
                n = 0
            summary[config.key] = n

    return summary
