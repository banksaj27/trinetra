import inspect
import logging
from typing import Any, Dict, List, Mapping, Optional, Sequence

from fastapi import APIRouter, HTTPException, Request, status
from sqlalchemy import bindparam, text

from app.schemas.damage import (
    DamageAssessmentRequest,
    DamageAssessmentResponse,
    PlanetaryDamageAssessmentRequest,
)
from app.services.damage_assessment import (
    DamageAssessmentError,
    DamageAssessor,
    ModelUnavailableError,
    assess_assets,
    find_planetary_image_pair,
    get_image_info,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/damage", tags=["damage"])


def _model_dump(model: Any) -> Dict[str, Any]:
    if hasattr(model, "model_dump"):
        return model.model_dump()
    return model.dict()


def _inline_assets(
    assets: Optional[Sequence[Any]],
    asset_ids: Optional[Sequence[str]],
) -> Optional[List[Dict[str, Any]]]:
    if not assets:
        return None

    asset_dicts = [_model_dump(asset) for asset in assets]
    if asset_ids:
        wanted = set(asset_ids)
        asset_dicts = [asset for asset in asset_dicts if asset["asset_id"] in wanted]
    return asset_dicts


def _get_assessor(request: Request) -> DamageAssessor:
    assessor = getattr(request.app.state, "assessor", None)
    if assessor is None:
        assessor = DamageAssessor()
        request.app.state.assessor = assessor

    if not assessor.is_available:
        detail = assessor.load_error or "Damage classifier is not loaded"
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=detail,
        )

    return assessor


async def _maybe_await(value: Any) -> Any:
    if inspect.isawaitable(value):
        return await value
    return value


async def _fetch_from_repository(
    repository: Any,
    asset_ids: Optional[Sequence[str]],
    bounds: Optional[Sequence[float]],
) -> List[Mapping[str, Any]]:
    method = getattr(repository, "fetch_assets_for_damage_assessment", None)
    if method is None:
        return []

    result = method(asset_ids=asset_ids, bounds=bounds)
    return list(await _maybe_await(result))


async def _fetch_from_session_factory(
    session_factory: Any,
    asset_ids: Optional[Sequence[str]],
    bounds: Optional[Sequence[float]],
) -> List[Mapping[str, Any]]:
    if asset_ids:
        stmt = text(
            """
            SELECT
                id::text AS asset_id,
                asset_type,
                name,
                ST_Y(
                    CASE
                        WHEN ST_SRID(geometry) = 4326 THEN geometry
                        ELSE ST_Transform(geometry, 4326)
                    END
                ) AS latitude,
                ST_X(
                    CASE
                        WHEN ST_SRID(geometry) = 4326 THEN geometry
                        ELSE ST_Transform(geometry, 4326)
                    END
                ) AS longitude
            FROM assets
            WHERE id::text IN :asset_ids
            """
        ).bindparams(bindparam("asset_ids", expanding=True))
        params = {"asset_ids": list(asset_ids)}
    else:
        if not bounds:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Image bounds are required when asset_ids is omitted",
            )
        min_lng, min_lat, max_lng, max_lat = bounds
        stmt = text(
            """
            SELECT
                id::text AS asset_id,
                asset_type,
                name,
                ST_Y(
                    CASE
                        WHEN ST_SRID(geometry) = 4326 THEN geometry
                        ELSE ST_Transform(geometry, 4326)
                    END
                ) AS latitude,
                ST_X(
                    CASE
                        WHEN ST_SRID(geometry) = 4326 THEN geometry
                        ELSE ST_Transform(geometry, 4326)
                    END
                ) AS longitude
            FROM assets
            WHERE ST_Within(
                CASE
                    WHEN ST_SRID(geometry) = 4326 THEN geometry
                    ELSE ST_Transform(geometry, 4326)
                END,
                ST_MakeEnvelope(:min_lng, :min_lat, :max_lng, :max_lat, 4326)
            )
            """
        )
        params = {
            "min_lng": min_lng,
            "min_lat": min_lat,
            "max_lng": max_lng,
            "max_lat": max_lat,
        }

    async with session_factory() as session:
        result = await session.execute(stmt, params)
        return [dict(row) for row in result.mappings().all()]


async def _load_assets(
    request: Request,
    asset_ids: Optional[Sequence[str]],
    bounds: Optional[Sequence[float]],
) -> List[Mapping[str, Any]]:
    repository = getattr(request.app.state, "asset_repository", None)
    if repository is not None:
        return await _fetch_from_repository(repository, asset_ids, bounds)

    session_factory = getattr(request.app.state, "async_session_factory", None)
    if session_factory is None:
        session_factory = getattr(request.app.state, "db_session_factory", None)

    if session_factory is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=(
                "No asset source is configured. Provide request.assets or configure "
                "app.state.asset_repository/app.state.async_session_factory."
            ),
        )

    try:
        return await _fetch_from_session_factory(session_factory, asset_ids, bounds)
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Failed to load assets for damage assessment")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to load assets: {exc}",
        ) from exc


async def _resolve_assets(
    request: Request,
    inline_assets: Optional[Sequence[Any]],
    asset_ids: Optional[Sequence[str]],
    bounds: Optional[Sequence[float]],
) -> List[Mapping[str, Any]]:
    assets = _inline_assets(inline_assets, asset_ids)
    if assets is not None:
        return assets
    return await _load_assets(request, asset_ids, bounds)


@router.post("/assess", response_model=DamageAssessmentResponse)
async def assess_damage(
    payload: DamageAssessmentRequest,
    request: Request,
) -> Dict[str, Any]:
    assessor = _get_assessor(request)

    image_for_bounds = payload.pre_image or payload.post_image
    try:
        image_info = get_image_info(image_for_bounds)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unable to read GeoTIFF bounds: {exc}",
        ) from exc

    assets = await _resolve_assets(
        request,
        payload.assets,
        payload.asset_ids,
        image_info["bounds"],
    )

    try:
        return assess_assets(
            assessor=assessor,
            pre_image=payload.pre_image,
            post_image=payload.post_image,
            assets=assets,
            crop_size_meters=payload.crop_size_meters,
            source_detail="pre_post_geotiff_assessment",
        )
    except ModelUnavailableError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
        ) from exc
    except DamageAssessmentError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc
    except Exception as exc:
        logger.exception("Damage assessment failed")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Damage assessment failed: {exc}",
        ) from exc


@router.post("/assess-from-planetary", response_model=DamageAssessmentResponse)
async def assess_damage_from_planetary(
    payload: PlanetaryDamageAssessmentRequest,
    request: Request,
) -> Dict[str, Any]:
    assessor = _get_assessor(request)

    try:
        pre_image, post_image, search_bbox = find_planetary_image_pair(
            center_lat=payload.center_lat,
            center_lng=payload.center_lng,
            radius_km=payload.radius_km,
            pre_date_range=payload.pre_date_range,
            post_date_range=payload.post_date_range,
            collection=payload.collection,
        )
        image_info = get_image_info(pre_image)
        bounds = image_info.get("bounds") or search_bbox
    except DamageAssessmentError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unable to resolve Planetary Computer imagery: {exc}",
        ) from exc

    assets = await _resolve_assets(
        request,
        payload.assets,
        payload.asset_ids,
        bounds,
    )

    try:
        return assess_assets(
            assessor=assessor,
            pre_image=pre_image,
            post_image=post_image,
            assets=assets,
            crop_size_meters=payload.crop_size_meters,
            source_detail="planetary_computer_assessment",
        )
    except ModelUnavailableError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
        ) from exc
    except DamageAssessmentError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc
    except Exception as exc:
        logger.exception("Planetary damage assessment failed")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Damage assessment failed: {exc}",
        ) from exc
