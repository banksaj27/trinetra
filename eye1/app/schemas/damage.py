from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class AssetForAssessment(BaseModel):
    asset_id: str
    asset_type: str
    name: Optional[str] = None
    latitude: float
    longitude: float


class DamageAssessmentRequest(BaseModel):
    pre_image: Optional[str] = None
    post_image: str
    crop_size_meters: float = Field(default=100, gt=0)
    asset_ids: Optional[List[str]] = None
    assets: Optional[List[AssetForAssessment]] = None


class PlanetaryDamageAssessmentRequest(BaseModel):
    center_lat: float
    center_lng: float
    radius_km: float = Field(gt=0)
    pre_date_range: str
    post_date_range: str
    collection: str = "naip"
    crop_size_meters: float = Field(default=100, gt=0)
    asset_ids: Optional[List[str]] = None
    assets: Optional[List[AssetForAssessment]] = None


class DamageObservationRaw(BaseModel):
    model: str
    chip_size: int
    input_channels: int
    model_label: str
    class_probabilities: Dict[str, float]


class DamageObservation(BaseModel):
    observation_id: str
    asset_id: str
    asset_type: Optional[str] = None
    damage_level: str
    confidence: float
    source: str
    source_detail: str
    lat: float
    lon: float
    timestamp: str
    raw: DamageObservationRaw


class ImageInfo(BaseModel):
    pre_image: str
    post_image: str
    bounds: List[float]
    resolution_meters: Optional[float] = None


class DamageAssessmentResponse(BaseModel):
    assessment_id: str
    image_info: ImageInfo
    summary: Dict[str, int]
    observations: List[DamageObservation]


class ErrorResponse(BaseModel):
    detail: Any
