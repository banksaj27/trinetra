from __future__ import annotations

import asyncio
import base64
import inspect
import sys
import uuid
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
from typing import Any, Awaitable, Callable

import numpy as np
from PIL import Image

try:
    from .config import REPO_ROOT, get_settings
    from .schemas import DamageObservation
except ImportError:  # Support `cd pipeline && uvicorn main:app`.
    from config import REPO_ROOT, get_settings
    from schemas import DamageObservation

EYE1_ROOT = REPO_ROOT / "eye1"
if str(EYE1_ROOT) not in sys.path:
    sys.path.insert(0, str(EYE1_ROOT))

from app.services.damage_assessment import (  # noqa: E402
    CHIP_SIZE,
    DEFAULT_BATCH_SIZE,
    MODEL_LABELS,
    DamageAssessor,
    crop_building,
)

ProgressCallback = Callable[[str], Awaitable[None] | None]

_ASSESSORS: dict[str, DamageAssessor] = {}
DISPLAY_CROP_SIZE_METERS = 500.0
DISPLAY_CHIP_SIZE = 512


def _extract_state_dict(checkpoint: Any) -> dict[str, Any]:
    if not isinstance(checkpoint, dict):
        return checkpoint

    for key in ("model_state_dict", "state_dict", "model", "net", "network"):
        value = checkpoint.get(key)
        if isinstance(value, dict):
            return value

    return checkpoint


def _strip_state_dict_prefixes(state_dict: dict[str, Any]) -> dict[str, Any]:
    prefixes = ("_orig_mod.", "module.", "model.")
    cleaned: dict[str, Any] = {}
    for key, value in state_dict.items():
        clean_key = key
        changed = True
        while changed:
            changed = False
            for prefix in prefixes:
                if clean_key.startswith(prefix):
                    clean_key = clean_key[len(prefix) :]
                    changed = True
        cleaned[clean_key] = value
    return cleaned


def _checkpoint_model_name(checkpoint: Any) -> str:
    if isinstance(checkpoint, dict):
        config = checkpoint.get("config")
        if isinstance(config, dict) and config.get("model"):
            return str(config["model"])
    return "efficientnet_b4"


class PipelineDamageAssessor(DamageAssessor):
    """Damage assessor that accepts wrapped PyTorch checkpoint key prefixes."""

    def _load_model(self) -> None:
        if not Path(self.model_path).exists():
            self.load_error = f"Model weights not found at {self.model_path}"
            return

        try:
            import torch
            import torch.nn as nn
            import timm

            self._torch = torch
            self.device = torch.device("cuda" if torch.cuda.is_available() else "cpu")

            try:
                checkpoint = torch.load(
                    self.model_path,
                    map_location=self.device,
                    weights_only=False,
                )
            except TypeError:
                checkpoint = torch.load(self.model_path, map_location=self.device)

            model_name = _checkpoint_model_name(checkpoint)
            backbone = timm.create_model(
                model_name,
                pretrained=False,
                num_classes=0,
                in_chans=6,
            )
            self.model = nn.Sequential()
            self.model.add_module("backbone", backbone)
            self.model.add_module(
                "classifier",
                nn.Sequential(
                    nn.Dropout(0.3),
                    nn.Linear(backbone.num_features, 512),
                    nn.ReLU(),
                    nn.Dropout(0.15),
                    nn.Linear(512, 4),
                ),
            )

            state_dict = _strip_state_dict_prefixes(_extract_state_dict(checkpoint))
            self.model.load_state_dict(state_dict)
            self.model.to(self.device)
            self.model.eval()
        except Exception as exc:  # pragma: no cover - startup guard
            self.model = None
            self.load_error = str(exc)


async def _notify(callback: ProgressCallback | None, detail: str) -> None:
    if callback is None:
        return
    result = callback(detail)
    if inspect.isawaitable(result):
        await result


def load_model(model_path: str | None = None) -> DamageAssessor:
    path = str(Path(model_path or get_settings().MODEL_PATH).resolve())
    assessor = _ASSESSORS.get(path)
    if assessor is None:
        assessor = PipelineDamageAssessor(model_path=path)
        _ASSESSORS[path] = assessor
    return assessor


def crop_to_base64(crop_array: np.ndarray) -> str:
    """Convert a normalized channels-first crop to a base64 PNG string."""
    img_array = (crop_array.transpose(1, 2, 0) * 255).clip(0, 255).astype(np.uint8)
    image = Image.fromarray(img_array)
    buffer = BytesIO()
    image.save(buffer, format="PNG")
    return base64.b64encode(buffer.getvalue()).decode("utf-8")


def _crop_from_image(
    image: Any,
    lat: float,
    lng: float,
    crop_size_meters: float,
    chip_size: int = CHIP_SIZE,
) -> np.ndarray | None:
    if hasattr(image, "crop"):
        return image.crop(lat, lng, crop_size_meters, chip_size=chip_size)
    return crop_building(image, lat, lng, crop_size_meters, chip_size=chip_size)


def _build_observation(
    asset: dict[str, Any],
    result: dict[str, Any],
    source_detail: str,
    pre_display_crop: np.ndarray | None,
    post_display_crop: np.ndarray | None,
    include_crop_images: bool,
) -> dict[str, Any]:
    timestamp = datetime.now(timezone.utc)
    label = str(result["label"])
    probabilities = {
        MODEL_LABELS[index]: round(float(result.get("class_probabilities", {}).get(MODEL_LABELS[index], 0.0)), 4)
        for index in sorted(MODEL_LABELS)
    }

    raw: dict[str, Any] = {
        "model": "efficientnet_b4_xbd",
        "chip_size": CHIP_SIZE,
        "input_channels": 6,
        "class_probabilities": probabilities,
    }
    if include_crop_images and pre_display_crop is not None and post_display_crop is not None:
        raw["pre_crop_b64"] = crop_to_base64(pre_display_crop)
        raw["post_crop_b64"] = crop_to_base64(post_display_crop)

    observation = DamageObservation(
        observation_id=f"obs_{timestamp:%Y%m%d_%H%M%S}_{uuid.uuid4().hex[:6]}",
        asset_id=str(asset["asset_id"]),
        asset_type=str(asset["asset_type"]),
        damage_level=label,
        confidence=round(float(result["confidence"]), 4),
        source="imagery",
        source_detail=source_detail,
        lat=float(asset["latitude"]),
        lon=float(asset["longitude"]),
        timestamp=timestamp.isoformat(),
        scenario_time=None,
        raw=raw,
    )
    return observation.model_dump()


async def assess_damage(
    assets: list[dict[str, Any]],
    pre_image: Any,
    post_image: Any,
    source_detail: str,
    progress: ProgressCallback | None = None,
    batch_size: int = DEFAULT_BATCH_SIZE,
    crop_size_meters: float | None = None,
) -> list[dict[str, Any]]:
    """Crop every asset and return xBD-label damage observations in the exact API shape."""
    settings = get_settings()
    assessor = load_model(settings.MODEL_PATH)
    assessor.ensure_available()

    crop_size = crop_size_meters or settings.DEFAULT_CROP_SIZE_METERS
    include_crop_images = settings.INCLUDE_CROP_IMAGES
    observations: list[dict[str, Any]] = []
    total_batches = max((len(assets) + batch_size - 1) // batch_size, 1)

    for batch_index, batch_start in enumerate(range(0, len(assets), batch_size), start=1):
        batch_assets = assets[batch_start : batch_start + batch_size]
        pre_crops = []
        post_crops = []
        pre_display_crops = []
        post_display_crops = []
        valid_assets = []

        for asset in batch_assets:
            pre_crop = _crop_from_image(
                pre_image,
                float(asset["latitude"]),
                float(asset["longitude"]),
                crop_size,
            )
            post_crop = _crop_from_image(
                post_image,
                float(asset["latitude"]),
                float(asset["longitude"]),
                crop_size,
            )
            if pre_crop is None or post_crop is None:
                continue

            pre_display_crop = (
                _crop_from_image(
                    pre_image,
                    float(asset["latitude"]),
                    float(asset["longitude"]),
                    DISPLAY_CROP_SIZE_METERS,
                    chip_size=DISPLAY_CHIP_SIZE,
                )
                if include_crop_images
                else None
            )
            post_display_crop = (
                _crop_from_image(
                    post_image,
                    float(asset["latitude"]),
                    float(asset["longitude"]),
                    DISPLAY_CROP_SIZE_METERS,
                    chip_size=DISPLAY_CHIP_SIZE,
                )
                if include_crop_images
                else None
            )

            pre_crops.append(pre_crop)
            post_crops.append(post_crop)
            pre_display_crops.append(pre_display_crop)
            post_display_crops.append(post_display_crop)
            valid_assets.append(asset)

        if valid_assets:
            results = await asyncio.to_thread(assessor.assess_crops, pre_crops, post_crops)
            observations.extend(
                _build_observation(
                    asset,
                    result,
                    source_detail,
                    pre_display_crop,
                    post_display_crop,
                    include_crop_images,
                )
                for asset, result, pre_display_crop, post_display_crop in zip(
                    valid_assets,
                    results,
                    pre_display_crops,
                    post_display_crops,
                )
            )

        await _notify(
            progress,
            f"Processing batch {batch_index}/{total_batches} ({min(batch_start + len(batch_assets), len(assets))}/{len(assets)} assets)",
        )

    return observations

