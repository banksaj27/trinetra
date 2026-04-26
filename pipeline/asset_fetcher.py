from __future__ import annotations

import inspect
import logging
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

import httpx
from shapely.geometry import Point, shape

logger = logging.getLogger(__name__)

PAGE_SIZE = 2000
ProgressCallback = Callable[[str], Awaitable[None] | None]


@dataclass(frozen=True)
class FieldMapping:
    hifld_id: str
    name: str
    name_fallback: str | None = None


@dataclass(frozen=True)
class DatasetConfig:
    key: str
    label: str
    url: str
    asset_type: str
    criticality_tier: int
    fields: FieldMapping
    hifld_id_fallbacks: list[str] = field(default_factory=list)
    name_fallbacks: list[str] = field(default_factory=list)
    extra_metadata_fields: list[str] = field(default_factory=list)


DATASETS: dict[str, DatasetConfig] = {
    "substations": DatasetConfig(
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
    "hospitals": DatasetConfig(
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
    "cell_towers": DatasetConfig(
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
    "fire_stations": DatasetConfig(
        key="fire_stations",
        label="Fire Stations",
        url="https://start.kanini.com/arcgis/rest/services/START_v4/MapServer/6/query",
        asset_type="fire_station",
        criticality_tier=2,
        fields=FieldMapping(hifld_id="OBJECTID", name="NAME"),
        extra_metadata_fields=["ADDRESS", "CITY", "STATE", "ZIPCODE", "ADMINTYPE"],
    ),
    "ems_stations": DatasetConfig(
        key="ems_stations",
        label="EMS Stations",
        url=(
            "https://services1.arcgis.com/wQnFk5ouCfPzTlPw/arcgis/rest/services"
            "/Emergency_Medical_Service_EMS_Stations/FeatureServer/0/query"
        ),
        asset_type="ems_station",
        criticality_tier=2,
        fields=FieldMapping(hifld_id="OBJECTID", name="NAME"),
        name_fallbacks=["Name"],
        hifld_id_fallbacks=["FID"],
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
    "water_treatment": DatasetConfig(
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
    ),
    "shelters": DatasetConfig(
        key="shelters",
        label="Shelters",
        url="https://gis.fema.gov/arcgis/rest/services/NSS/FEMA_NSS/FeatureServer/5/query",
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
    "police_stations": DatasetConfig(
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
    "911_centers": DatasetConfig(
        key="911_centers",
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
}


async def _notify(callback: ProgressCallback | None, detail: str) -> None:
    if callback is None:
        return
    result = callback(detail)
    if inspect.isawaitable(result):
        await result


def _build_spatial_query_params(lat: float, lng: float, radius_km: float, offset: int) -> dict[str, Any]:
    return {
        "geometry": f"{lng},{lat}",
        "geometryType": "esriGeometryPoint",
        "distance": int(radius_km * 1000),
        "units": "esriSRUnit_Meter",
        "spatialRel": "esriSpatialRelIntersects",
        "inSR": 4326,
        "outSR": 4326,
        "outFields": "*",
        "f": "geojson",
        "resultRecordCount": PAGE_SIZE,
        "resultOffset": offset,
    }


def _exceeded_transfer_limit(data: dict[str, Any]) -> bool:
    return bool(data.get("exceededTransferLimit") or data.get("properties", {}).get("exceededTransferLimit"))


async def _fetch_dataset_geojson(
    client: httpx.AsyncClient,
    config: DatasetConfig,
    lat: float,
    lng: float,
    radius_km: float,
) -> list[dict[str, Any]]:
    features: list[dict[str, Any]] = []
    offset = 0

    while True:
        params = _build_spatial_query_params(lat, lng, radius_km, offset)
        response = await client.get(config.url, params=params, timeout=60)
        data = response.json()
        response.raise_for_status()

        if "error" in data:
            message = data["error"].get("message", "ArcGIS query failed")
            raise RuntimeError(f"{config.label}: {message}")

        page = data.get("features", [])
        features.extend(page)

        if not page or not _exceeded_transfer_limit(data):
            return features
        offset += PAGE_SIZE


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


def _extract_name(props: dict[str, Any], config: DatasetConfig) -> str:
    if config.key == "cell_towers":
        city = str(_get_first(props, ["LOCCITY", "LocCity"]) or "").strip()
        county = str(_get_first(props, ["LOCCOUNTY", "LocCounty"]) or "").strip()
        if city and county:
            return f"{city}, {county} cell tower"
        if city or county:
            return f"{city or county} cell tower"

    if config.key == "911_centers":
        psap_name = _get_first(props, ["PSAP_Name", "PSAP_NAME", "psap_name", "NAME", "name"])
        if psap_name:
            return str(psap_name).strip()
        county = _get_first(props, ["COUNTY", "county"])
        if county:
            return f"{str(county).strip()} 911 Center"

    name_fields = [config.fields.name, *config.name_fallbacks]
    if config.fields.name_fallback:
        name_fields.append(config.fields.name_fallback)
    return str(_get_first(props, name_fields) or "Unknown").strip() or "Unknown"


def _extract_original_id(props: dict[str, Any], config: DatasetConfig) -> str | None:
    raw = _get_first(props, [config.fields.hifld_id, *config.hifld_id_fallbacks])
    if raw is None or str(raw).strip() == "":
        return None
    return str(raw).strip().strip("{}")


def _extract_metadata(props: dict[str, Any], config: DatasetConfig) -> dict[str, Any]:
    return {field_name: props[field_name] for field_name in config.extra_metadata_fields if props.get(field_name) is not None}


def _feature_to_asset(feature: dict[str, Any], config: DatasetConfig) -> dict[str, Any] | None:
    props = feature.get("properties") or feature.get("attributes") or {}
    geometry = feature.get("geometry")
    if not geometry:
        return None

    geom = shape(geometry)
    if geom.is_empty:
        return None
    point = geom if isinstance(geom, Point) else geom.centroid

    original_id = _extract_original_id(props, config)
    if original_id is None:
        return None

    metadata = _extract_metadata(props, config)
    metadata["source_dataset"] = config.key
    metadata["source_url"] = config.url

    return {
        "asset_id": f"hifld_{config.key}_{original_id}",
        "asset_type": config.asset_type,
        "name": _extract_name(props, config)[:255],
        "latitude": float(point.y),
        "longitude": float(point.x),
        "criticality_tier": config.criticality_tier,
        "metadata": metadata,
    }


async def fetch_assets(
    lat: float,
    lng: float,
    radius_km: float,
    progress: ProgressCallback | None = None,
) -> list[dict[str, Any]]:
    """Query HIFLD ArcGIS endpoints for all supported asset types within radius."""
    assets: list[dict[str, Any]] = []
    seen_ids: set[str] = set()

    async with httpx.AsyncClient() as client:
        for config in DATASETS.values():
            await _notify(progress, f"Querying {config.label}...")
            try:
                features = await _fetch_dataset_geojson(client, config, lat, lng, radius_km)
            except Exception as exc:
                logger.warning("Skipping %s after query failure: %s", config.label, exc)
                await _notify(progress, f"{config.label} unavailable: {exc}")
                continue

            count_before = len(assets)
            for feature in features:
                asset = _feature_to_asset(feature, config)
                if asset is None or asset["asset_id"] in seen_ids:
                    continue
                seen_ids.add(asset["asset_id"])
                assets.append(asset)

            await _notify(progress, f"Found {len(assets) - count_before} {config.label.lower()}")

    return assets

