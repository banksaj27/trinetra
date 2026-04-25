import logging
import math
import os
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

import numpy as np
import rasterio
from PIL import Image
from pyproj import Transformer
from rasterio.warp import transform_bounds
from rasterio.windows import from_bounds

logger = logging.getLogger(__name__)

MODEL_LABELS = {
    0: "no-damage",
    1: "minor-damage",
    2: "major-damage",
    3: "destroyed",
}

LABEL_TO_DB = {
    "no-damage": "unaffected",
    "minor-damage": "minor",
    "major-damage": "major",
    "destroyed": "destroyed",
}

CHIP_SIZE = 128
DEFAULT_BATCH_SIZE = 32


class DamageAssessmentError(Exception):
    """Base exception for damage assessment failures."""


class ModelUnavailableError(DamageAssessmentError):
    """Raised when the model is not loaded."""


def load_geotiff(path_or_url: str):
    """Load a GeoTIFF from a local path or HTTP URL."""
    return rasterio.open(path_or_url)


def _asset_value(asset: Mapping[str, Any], *names: str) -> Any:
    for name in names:
        if name in asset and asset[name] is not None:
            return asset[name]
    return None


def _crop_half_extents(src, lat: float, crop_size_meters: float) -> Tuple[float, float]:
    half_meters = crop_size_meters / 2
    crs = src.crs

    if crs is not None and getattr(crs, "is_geographic", False):
        latitude_radians = math.radians(lat)
        meters_per_degree_lon = max(111_320 * math.cos(latitude_radians), 1e-6)
        half_x = half_meters / meters_per_degree_lon
        half_y = half_meters / 110_540
        return half_x, half_y

    return half_meters, half_meters


def _normalize_rgb(data: np.ndarray) -> np.ndarray:
    if np.ma.isMaskedArray(data):
        data = data.filled(0)

    if data.dtype == np.uint8:
        normalized = data.astype(np.float32) / 255.0
    elif data.dtype == np.uint16:
        normalized = data.astype(np.float32) / 10_000.0
    else:
        normalized = data.astype(np.float32)
        max_value = float(np.nanmax(normalized)) if normalized.size else 0.0
        if max_value > 255:
            normalized = normalized / 10_000.0
        elif max_value > 1:
            normalized = normalized / 255.0

    return np.clip(np.nan_to_num(normalized, nan=0.0), 0.0, 1.0)


def crop_building(
    src,
    lat: float,
    lng: float,
    crop_size_meters: float = 100,
    chip_size: int = CHIP_SIZE,
) -> Optional[np.ndarray]:
    """
    Crop a square region around a lat/lng point from a rasterio dataset.

    Returns an RGB array shaped as (3, chip_size, chip_size), normalized to 0-1.
    """
    if src.count < 3:
        logger.warning("Skipping crop because image has fewer than 3 bands")
        return None

    image_crs = src.crs or "EPSG:4326"
    transformer = Transformer.from_crs("EPSG:4326", image_crs, always_xy=True)
    x, y = transformer.transform(lng, lat)

    if not (
        min(src.bounds.left, src.bounds.right) <= x <= max(src.bounds.left, src.bounds.right)
        and min(src.bounds.bottom, src.bounds.top) <= y <= max(src.bounds.bottom, src.bounds.top)
    ):
        return None

    half_x, half_y = _crop_half_extents(src, lat, crop_size_meters)
    window = from_bounds(
        x - half_x,
        y - half_y,
        x + half_x,
        y + half_y,
        src.transform,
    )

    if window.width <= 0 or window.height <= 0:
        return None

    data = src.read([1, 2, 3], window=window, boundless=False, masked=True)
    if data.shape[1] == 0 or data.shape[2] == 0:
        return None

    normalized = _normalize_rgb(data)
    hwc = np.transpose(normalized, (1, 2, 0))
    image = Image.fromarray((hwc * 255).astype(np.uint8))
    image = image.resize((chip_size, chip_size), Image.BILINEAR)

    arr = np.array(image, dtype=np.float32) / 255.0
    return np.transpose(arr, (2, 0, 1))


def get_image_info(path_or_url: str) -> Dict[str, Any]:
    with load_geotiff(path_or_url) as src:
        crs = src.crs or "EPSG:4326"
        bounds = transform_bounds(crs, "EPSG:4326", *src.bounds, densify_pts=21)
        resolution = _resolution_meters(src, bounds)

    return {
        "bounds": [round(float(value), 8) for value in bounds],
        "resolution_meters": round(float(resolution), 4) if resolution is not None else None,
    }


def _resolution_meters(src, wgs84_bounds: Sequence[float]) -> Optional[float]:
    if not src.res:
        return None

    x_res = abs(float(src.res[0]))
    y_res = abs(float(src.res[1]))

    if src.crs is not None and getattr(src.crs, "is_geographic", False):
        min_lng, min_lat, max_lng, max_lat = wgs84_bounds
        center_lat = (min_lat + max_lat) / 2
        meters_per_degree_lon = 111_320 * math.cos(math.radians(center_lat))
        x_meters = x_res * meters_per_degree_lon
        y_meters = y_res * 110_540
        return (x_meters + y_meters) / 2

    return (x_res + y_res) / 2


class DamageAssessor:
    def __init__(self, model_path: str = "app/ml/best_model.pth") -> None:
        self.model_path = model_path
        self.device = None
        self.model = None
        self.load_error: Optional[str] = None
        self._torch = None
        self._load_model()

    @property
    def is_available(self) -> bool:
        return self.model is not None and self._torch is not None

    def _load_model(self) -> None:
        if not os.path.exists(self.model_path):
            self.load_error = f"Model weights not found at {self.model_path}"
            logger.warning("%s; damage assessment endpoints will return 503", self.load_error)
            return

        try:
            import torch

            from app.ml.model import DamageClassifier

            self._torch = torch
            self.device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
            self.model = DamageClassifier(pretrained=False)

            try:
                checkpoint = torch.load(
                    self.model_path,
                    map_location=self.device,
                    weights_only=False,
                )
            except TypeError:
                checkpoint = torch.load(self.model_path, map_location=self.device)

            state_dict = checkpoint.get("model_state_dict", checkpoint)
            self.model.load_state_dict(state_dict)
            self.model.to(self.device)
            self.model.eval()
            logger.info("Loaded damage classifier from %s on %s", self.model_path, self.device)
        except Exception as exc:  # pragma: no cover - startup guard
            self.model = None
            self.load_error = str(exc)
            logger.exception("Failed to load damage classifier")

    def ensure_available(self) -> None:
        if not self.is_available:
            detail = self.load_error or "Damage classifier is not loaded"
            raise ModelUnavailableError(detail)

    def assess_buildings(
        self,
        pre_crops: Sequence[np.ndarray],
        post_crops: Sequence[np.ndarray],
    ) -> List[Tuple[str, float]]:
        return [
            (result["label"], result["confidence"])
            for result in self.assess_crops(pre_crops, post_crops)
        ]

    def assess_crops(
        self,
        pre_crops: Sequence[np.ndarray],
        post_crops: Sequence[np.ndarray],
    ) -> List[Dict[str, Any]]:
        self.ensure_available()

        if len(pre_crops) != len(post_crops):
            raise ValueError("pre_crops and post_crops must have the same length")

        if not pre_crops:
            return []

        inputs = [
            np.concatenate([pre, post], axis=0)
            for pre, post in zip(pre_crops, post_crops)
        ]
        batch = self._torch.tensor(np.array(inputs), dtype=self._torch.float32).to(self.device)

        with self._torch.no_grad():
            logits = self.model(batch)
            probabilities = self._torch.softmax(logits, dim=1)
            confidences, predictions = probabilities.max(dim=1)

        probability_rows = probabilities.cpu().numpy()
        results = []
        for pred, confidence, probs in zip(
            predictions.cpu().numpy(),
            confidences.cpu().numpy(),
            probability_rows,
        ):
            label = MODEL_LABELS[int(pred)]
            results.append(
                {
                    "label": label,
                    "damage_level": LABEL_TO_DB[label],
                    "confidence": float(confidence),
                    "class_probabilities": {
                        MODEL_LABELS[index]: round(float(prob), 4)
                        for index, prob in enumerate(probs)
                    },
                }
            )

        return results


def build_observation(
    asset: Mapping[str, Any],
    result: Mapping[str, Any],
    source_detail: str = "satellite_assessment",
) -> Dict[str, Any]:
    timestamp = datetime.now(timezone.utc)
    lat = _asset_value(asset, "latitude", "lat")
    lon = _asset_value(asset, "longitude", "lon", "lng")

    return {
        "observation_id": (
            f"obs_{timestamp.strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}"
        ),
        "asset_id": str(_asset_value(asset, "asset_id", "id")),
        "asset_type": _asset_value(asset, "asset_type", "type"),
        "damage_level": result["damage_level"],
        "confidence": round(float(result["confidence"]), 4),
        "source": "imagery",
        "source_detail": source_detail,
        "lat": lat,
        "lon": lon,
        "timestamp": timestamp.isoformat(),
        "raw": {
            "model": "efficientnet_b4_xbd",
            "chip_size": CHIP_SIZE,
            "input_channels": 6,
            "model_label": result["label"],
            "class_probabilities": result.get("class_probabilities", {}),
        },
    }


def _empty_summary(skipped: int = 0) -> Dict[str, int]:
    return {
        "total_assessed": 0,
        "destroyed": 0,
        "major-damage": 0,
        "minor-damage": 0,
        "no-damage": 0,
        "skipped": skipped,
    }


def _flush_batch(
    assessor: DamageAssessor,
    assets: Sequence[Mapping[str, Any]],
    pre_crops: Sequence[np.ndarray],
    post_crops: Sequence[np.ndarray],
    observations: List[Dict[str, Any]],
    summary: Dict[str, int],
    source_detail: str,
) -> None:
    if not assets:
        return

    results = assessor.assess_crops(pre_crops, post_crops)
    for asset, result in zip(assets, results):
        summary["total_assessed"] += 1
        summary[result["label"]] += 1
        observations.append(build_observation(asset, result, source_detail=source_detail))


def assess_assets(
    assessor: DamageAssessor,
    pre_image: Optional[str],
    post_image: str,
    assets: Sequence[Mapping[str, Any]],
    crop_size_meters: float = 100,
    batch_size: int = DEFAULT_BATCH_SIZE,
    source_detail: str = "pre_post_geotiff_assessment",
) -> Dict[str, Any]:
    assessor.ensure_available()

    if not pre_image:
        pre_image = post_image
        logger.warning(
            "No pre-disaster image provided; duplicating post-disaster image for assessment"
        )

    image_info = get_image_info(pre_image)
    observations: List[Dict[str, Any]] = []
    summary = _empty_summary()

    batch_assets: List[Mapping[str, Any]] = []
    pre_batch: List[np.ndarray] = []
    post_batch: List[np.ndarray] = []

    with load_geotiff(pre_image) as pre_src, load_geotiff(post_image) as post_src:
        for asset in assets:
            lat = _asset_value(asset, "latitude", "lat")
            lon = _asset_value(asset, "longitude", "lon", "lng")
            if lat is None or lon is None:
                summary["skipped"] += 1
                continue

            pre_crop = crop_building(pre_src, float(lat), float(lon), crop_size_meters)
            post_crop = crop_building(post_src, float(lat), float(lon), crop_size_meters)
            if pre_crop is None or post_crop is None:
                summary["skipped"] += 1
                continue

            batch_assets.append(asset)
            pre_batch.append(pre_crop)
            post_batch.append(post_crop)

            if len(batch_assets) >= batch_size:
                _flush_batch(
                    assessor,
                    batch_assets,
                    pre_batch,
                    post_batch,
                    observations,
                    summary,
                    source_detail,
                )
                batch_assets, pre_batch, post_batch = [], [], []

    _flush_batch(
        assessor,
        batch_assets,
        pre_batch,
        post_batch,
        observations,
        summary,
        source_detail,
    )

    assessment_id = f"assess_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}"
    return {
        "assessment_id": assessment_id,
        "image_info": {
            "pre_image": pre_image,
            "post_image": post_image,
            **image_info,
        },
        "summary": summary,
        "observations": observations,
    }


def bbox_from_center(center_lat: float, center_lng: float, radius_km: float) -> List[float]:
    delta_lat = radius_km / 111.32
    meters_per_degree_lon = max(111.32 * math.cos(math.radians(center_lat)), 1e-6)
    delta_lng = radius_km / meters_per_degree_lon
    return [
        center_lng - delta_lng,
        center_lat - delta_lat,
        center_lng + delta_lng,
        center_lat + delta_lat,
    ]


def _cloud_cover(item: Any) -> float:
    value = item.properties.get("eo:cloud_cover")
    return float(value) if value is not None else 0.0


def _best_asset_href(item: Any) -> str:
    priority_keys = ("image", "visual", "visual_10m")
    for key in priority_keys:
        asset = item.assets.get(key)
        if asset is not None and asset.href:
            return asset.href

    for asset in item.assets.values():
        media_type = asset.media_type or ""
        href = asset.href or ""
        if "tiff" in media_type.lower() or href.lower().endswith((".tif", ".tiff")):
            return href

    raise DamageAssessmentError(f"No GeoTIFF asset found for STAC item {item.id}")


def find_planetary_image(
    bbox: Sequence[float],
    date_range: str,
    collection: str = "naip",
) -> str:
    try:
        import planetary_computer
        from pystac_client import Client
    except ImportError as exc:
        raise DamageAssessmentError(
            "pystac-client and planetary-computer are required for Planetary Computer imagery"
        ) from exc

    catalog = Client.open("https://planetarycomputer.microsoft.com/api/stac/v1")
    search = catalog.search(
        collections=[collection],
        bbox=list(bbox),
        datetime=date_range,
        limit=50,
    )
    items = sorted(list(search.items()), key=_cloud_cover)
    if not items:
        raise DamageAssessmentError(
            f"No Planetary Computer items found for collection={collection} "
            f"date_range={date_range}"
        )

    href = _best_asset_href(items[0])
    return planetary_computer.sign_url(href)


def find_planetary_image_pair(
    center_lat: float,
    center_lng: float,
    radius_km: float,
    pre_date_range: str,
    post_date_range: str,
    collection: str = "naip",
) -> Tuple[str, str, List[float]]:
    bbox = bbox_from_center(center_lat, center_lng, radius_km)
    pre_image = find_planetary_image(bbox, pre_date_range, collection)
    post_image = find_planetary_image(bbox, post_date_range, collection)
    return pre_image, post_image, bbox
