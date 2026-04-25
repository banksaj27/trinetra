from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from math import cos, radians
import sys
from typing import Any

try:
    from .config import REPO_ROOT
except ImportError:  # Support `cd pipeline && uvicorn main:app`.
    from config import REPO_ROOT


MAX_STAC_ITEMS = 100


@dataclass
class ImagerySet:
    """Holds multiple rasterio datasets covering an area."""

    datasets: list[Any]
    collection: str
    selected_date: date

    def crop(
        self,
        lat: float,
        lng: float,
        crop_size_meters: float = 100,
        chip_size: int = 128,
    ) -> Any | None:
        eye1_root = REPO_ROOT / "eye1"
        if str(eye1_root) not in sys.path:
            sys.path.insert(0, str(eye1_root))

        from app.services.damage_assessment import crop_building

        for src in self.datasets:
            crop = crop_building(src, lat, lng, crop_size_meters, chip_size=chip_size)
            if crop is not None:
                return crop
        return None

    def close(self) -> None:
        for dataset in self.datasets:
            dataset.close()


@dataclass
class ImageryPair:
    pre_image: ImagerySet
    post_image: ImagerySet
    collection: str

    @property
    def source_detail(self) -> str:
        pre = self.pre_image.selected_date.strftime("%Y%m%d")
        post = self.post_image.selected_date.strftime("%Y%m%d")
        return f"{self.collection}_pre{pre}_post{post}"

    def close(self) -> None:
        self.pre_image.close()
        self.post_image.close()


def bbox_from_center(lat: float, lng: float, radius_km: float) -> list[float]:
    delta_lat = radius_km / 111.0
    meters_per_degree_lng = max(111.0 * cos(radians(lat)), 1e-6)
    delta_lng = radius_km / meters_per_degree_lng
    return [lng - delta_lng, lat - delta_lat, lng + delta_lng, lat + delta_lat]


def derive_date_ranges(disaster_date: str) -> tuple[str, str, date]:
    disaster = date.fromisoformat(disaster_date)
    try:
        pre_start = disaster.replace(year=disaster.year - 2)
    except ValueError:
        pre_start = disaster.replace(year=disaster.year - 2, day=28)
    pre_end = disaster - timedelta(days=1)
    try:
        post_end = disaster.replace(year=disaster.year + 1)
    except ValueError:
        post_end = disaster.replace(year=disaster.year + 1, day=28)
    return f"{pre_start.isoformat()}/{pre_end.isoformat()}", f"{disaster.isoformat()}/{post_end.isoformat()}", disaster


def _item_datetime(item: Any) -> datetime:
    if item.datetime is not None:
        return item.datetime.replace(tzinfo=None)

    for key in ("datetime", "start_datetime", "end_datetime"):
        raw = item.properties.get(key)
        if raw:
            return datetime.fromisoformat(str(raw).replace("Z", "+00:00")).replace(tzinfo=None)
    raise ValueError(f"STAC item {item.id} does not include an imagery datetime")


def _cloud_cover(item: Any) -> float:
    return float(item.properties.get("eo:cloud_cover") or 0)


def _sort_items(items: list[Any], disaster: date, kind: str) -> list[Any]:
    if kind == "pre":
        return sorted(
            items,
            key=lambda item: (
                (disaster - _item_datetime(item).date()).days,
                _cloud_cover(item),
            ),
        )
    if kind == "post":
        return sorted(
            items,
            key=lambda item: (
                (_item_datetime(item).date() - disaster).days,
                _cloud_cover(item),
            ),
        )
    raise ValueError(f"Unknown imagery sort kind: {kind}")


def _asset_href(item: Any, asset_keys: tuple[str, ...]) -> str:
    for key in asset_keys:
        asset = item.assets.get(key)
        if asset is not None:
            return asset.href
    available = ", ".join(sorted(item.assets))
    raise ValueError(f"STAC item {item.id} is missing RGB asset keys {asset_keys}; available: {available}")


def _search_collection(
    catalog: Any,
    collection_id: str,
    bbox: list[float],
    date_range: str,
    max_items: int = MAX_STAC_ITEMS,
) -> list[Any]:
    kwargs: dict[str, Any] = {
        "collections": [collection_id],
        "bbox": bbox,
        "datetime": date_range,
        "max_items": max_items,
    }
    if collection_id == "sentinel-2-l2a":
        kwargs["query"] = {"eo:cloud_cover": {"lt": 60}}

    return list(catalog.search(**kwargs).items())


def _open_imagery_set(
    items: list[Any],
    collection_label: str,
    asset_keys: tuple[str, ...],
    pc: Any,
    rasterio: Any,
    disaster: date,
    kind: str,
) -> ImagerySet:
    sorted_items = _sort_items(items, disaster, kind)
    datasets = [
        rasterio.open(pc.sign(_asset_href(item, asset_keys)))
        for item in sorted_items
    ]
    if not datasets:
        raise ValueError(f"No {collection_label} datasets to open")

    return ImagerySet(
        datasets=datasets,
        collection=collection_label,
        selected_date=_item_datetime(sorted_items[0]).date(),
    )


def _fetch_imagery_sync(
    lat: float,
    lng: float,
    radius_km: float,
    disaster_date: str,
) -> ImageryPair:
    import planetary_computer as pc
    import rasterio
    from pystac_client import Client

    catalog = Client.open(
        "https://planetarycomputer.microsoft.com/api/stac/v1",
        modifier=pc.sign_inplace,
    )
    bbox = bbox_from_center(lat, lng, radius_km)
    pre_date_range, post_date_range, disaster = derive_date_ranges(disaster_date)

    attempts = (
        ("naip", "naip", ("image",)),
        ("sentinel2", "sentinel-2-l2a", ("visual",)),
    )
    failures: list[str] = []

    for source_label, collection_id, asset_keys in attempts:
        pre_items = _search_collection(catalog, collection_id, bbox, pre_date_range)
        post_items = _search_collection(catalog, collection_id, bbox, post_date_range)

        if not pre_items or not post_items:
            failures.append(
                f"{source_label}: pre results {len(pre_items)}, post results {len(post_items)}"
            )
            continue

        print(
            f"Found {len(pre_items)} pre-disaster tiles, "
            f"{len(post_items)} post-disaster tiles for {source_label}"
        )

        return ImageryPair(
            pre_image=_open_imagery_set(pre_items, source_label, asset_keys, pc, rasterio, disaster, "pre"),
            post_image=_open_imagery_set(post_items, source_label, asset_keys, pc, rasterio, disaster, "post"),
            collection=source_label,
        )

    raise ValueError(
        "No usable Planetary Computer imagery found for this area/date range. "
        + " | ".join(failures)
        + ". Try wider date ranges or a location covered by NAIP/Sentinel-2."
    )


async def fetch_imagery(
    lat: float,
    lng: float,
    radius_km: float,
    disaster_date: str,
) -> ImageryPair:
    """Fetch pre/post RGB imagery from NAIP, falling back to Sentinel-2 L2A."""
    return await asyncio.to_thread(
        _fetch_imagery_sync,
        lat,
        lng,
        radius_km,
        disaster_date,
    )

