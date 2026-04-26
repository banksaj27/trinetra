from __future__ import annotations

import asyncio
import json
import sys
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, AsyncIterator

import networkx as nx

# Make the repo root importable so eye3 can be imported as a package.
_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

try:
    from .asset_fetcher import fetch_assets
    from .damage_assessor import assess_damage
    from .event_classifier import classify_event
    from .event_sensor_selector import select_ground_sensor_type
    from .ground_sensor_fetcher import fetch_ground_sensor_data
    from .ground_severity_assessor import assess_ground_severity
    from .graph_builder import build_graph
    from .imagery_fetcher import fetch_imagery
    from .score_aggregator import aggregate_damage_level
    from .schemas import (
        DamageObservation,
        EventType,
        GroundSensorObservation,
        TweetSentimentObservation,
        PipelineResultResponse,
        PipelineRunRequest,
    )
except ImportError:  # Support `cd pipeline && uvicorn main:app`.
    from asset_fetcher import fetch_assets
    from damage_assessor import assess_damage
    from event_classifier import classify_event
    from event_sensor_selector import select_ground_sensor_type
    from ground_sensor_fetcher import fetch_ground_sensor_data
    from ground_severity_assessor import assess_ground_severity
    from graph_builder import build_graph
    from imagery_fetcher import fetch_imagery
    from score_aggregator import aggregate_damage_level
    from schemas import (
        DamageObservation,
        EventType,
        GroundSensorObservation,
        TweetSentimentObservation,
        PipelineResultResponse,
        PipelineRunRequest,
    )

# eye3 lives at the repo root — always imported via the absolute package name.
from eye3.tweet_sentiment_assessor import assess_tweet_sentiment

TOTAL_STEPS = 7
MAX_CACHE_AGE = 3600
RESULTS_CACHE: dict[str, dict[str, Any]] = {}


@dataclass
class PipelineResult:
    run_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    detected_event_type: EventType = "landslide"
    assets: list[dict[str, Any]] = field(default_factory=list)
    edges: list[dict[str, Any]] = field(default_factory=list)
    damage_observations: list[dict[str, Any]] = field(default_factory=list)
    ground_sensor_data_type: str = "multimodal"
    ground_sensor_observations: list[dict[str, Any]] = field(default_factory=list)
    tweet_sentiment_observation: dict[str, Any] | None = None

    def response_payload(self) -> dict[str, Any]:
        tweet_obs = None
        if self.tweet_sentiment_observation is not None:
            tweet_obs = TweetSentimentObservation.model_validate(self.tweet_sentiment_observation)
        response = PipelineResultResponse(
            run_id=self.run_id,
            detected_event_type=self.detected_event_type,
            assets=self.assets,
            edges=self.edges,
            damage_observations=[
                DamageObservation.model_validate(observation)
                for observation in self.damage_observations
            ],
            ground_sensor_data_type=self.ground_sensor_data_type,
            ground_sensor_observations=self.ground_sensor_observations,
            tweet_sentiment_observation=tweet_obs,
        )
        return response.model_dump()


def status(step: int, message: str, detail: str = "") -> dict[str, Any]:
    return {
        "event": "status",
        "data": {
            "step": step,
            "total_steps": TOTAL_STEPS,
            "message": message,
            "detail": detail,
        },
    }


def complete(result: PipelineResult) -> dict[str, Any]:
    return {
        "event": "complete",
        "data": {
            "redirect": f"/api/v1/results/{result.run_id}",
            "run_id": result.run_id,
            "result": result.response_payload(),
        },
    }


def sse_format(event: dict[str, Any]) -> str:
    return f"event: {event['event']}\ndata: {json.dumps(event['data'])}\n\n"


def store_result(result: PipelineResult) -> None:
    now = time.time()
    expired = [key for key, value in RESULTS_CACHE.items() if now - value["created_at"] > MAX_CACHE_AGE]
    for key in expired:
        del RESULTS_CACHE[key]

    result.damage_observations = [
        DamageObservation.model_validate(observation).model_dump()
        for observation in result.damage_observations
    ]
    result.ground_sensor_observations = [
        GroundSensorObservation.model_validate(observation).model_dump()
        for observation in result.ground_sensor_observations
    ]
    if result.tweet_sentiment_observation is not None:
        result.tweet_sentiment_observation = (
            TweetSentimentObservation.model_validate(result.tweet_sentiment_observation).model_dump()
        )
    RESULTS_CACHE[result.run_id] = {"result": result, "created_at": now}


def get_result(run_id: str) -> PipelineResult | None:
    entry = RESULTS_CACHE.get(run_id)
    if entry is None:
        return None
    if time.time() - entry["created_at"] > MAX_CACHE_AGE:
        del RESULTS_CACHE[run_id]
        return None
    return entry["result"]


async def _drain_progress(
    task: asyncio.Task,
    queue: asyncio.Queue[dict[str, Any]],
) -> AsyncIterator[dict[str, Any]]:
    while not task.done():
        try:
            yield await asyncio.wait_for(queue.get(), timeout=0.5)
        except asyncio.TimeoutError:
            continue
    while not queue.empty():
        yield queue.get_nowait()


async def run_pipeline(params: PipelineRunRequest) -> AsyncIterator[dict[str, Any]]:
    result = PipelineResult()

    # ── Step 1: Classify event type from ground-sensor datasets ───────────────
    yield status(1, "Classifying event type...", "Querying flood, earthquake, storm, wildfire datasets")
    result.detected_event_type = classify_event(
        params.latitude, params.longitude, params.disaster_date
    )
    result.ground_sensor_data_type = select_ground_sensor_type(result.detected_event_type)
    yield status(
        1,
        "Classifying event type...",
        f"Detected: {result.detected_event_type} → sensor type: {result.ground_sensor_data_type}",
    )

    # ── Step 2: Fetch infrastructure assets ───────────────────────────────────
    yield status(2, "Fetching infrastructure assets from HIFLD...", "Starting ArcGIS spatial queries")
    asset_queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()

    async def asset_progress(detail: str) -> None:
        await asset_queue.put(status(2, "Fetching infrastructure assets from HIFLD...", detail))

    asset_task = asyncio.create_task(
        fetch_assets(params.latitude, params.longitude, params.radius_km, progress=asset_progress)
    )
    async for event in _drain_progress(asset_task, asset_queue):
        yield event
    result.assets = await asset_task
    yield status(2, "Fetching infrastructure assets from HIFLD...", f"Found {len(result.assets)} total assets")

    # ── Step 3: Build dependency graph ────────────────────────────────────────
    yield status(3, "Building dependency graph...", "Inferring service areas and dependencies")
    result.edges, result.graph = build_graph(result.assets)
    edge_counts: dict[str, int] = {}
    for edge in result.edges:
        edge_counts[edge["dependency_type"]] = edge_counts.get(edge["dependency_type"], 0) + 1
    edge_summary = ", ".join(f"{count} {label}" for label, count in sorted(edge_counts.items())) or "0 edges"
    yield status(3, "Building dependency graph...", f"Generated {edge_summary}")

    # ── Step 4: Download satellite imagery ────────────────────────────────────
    yield status(4, "Downloading satellite imagery...", "Searching NAIP imagery, then Sentinel-2 if needed")
    imagery = await fetch_imagery(
        params.latitude,
        params.longitude,
        params.radius_km,
        params.disaster_date,
    )
    yield status(
        4,
        "Downloading satellite imagery...",
        (
            f"Using {imagery.source_detail}: "
            f"{len(imagery.pre_image.datasets)} pre-disaster tiles, "
            f"{len(imagery.post_image.datasets)} post-disaster tiles"
        ),
    )

    # ── Step 5: Fetch ground sensor data ──────────────────────────────────────
    yield status(
        5,
        "Fetching ground sensor data...",
        f"Loading {result.detected_event_type} sensor data for ({params.latitude:.3f}, {params.longitude:.3f})",
    )
    sensor_points = await fetch_ground_sensor_data(
        params.latitude,
        params.longitude,
        params.radius_km,
        result.detected_event_type,
        params.disaster_date,
    )
    yield status(5, "Fetching ground sensor data...", f"Retrieved {len(sensor_points)} sensor points")

    # ── Step 6: Run Eyes 1, 2 & 3 in parallel ────────────────────────────────
    yield status(6, "Running Eyes 1, 2 & 3 in parallel...", "Satellite, ground sensor, and social media running concurrently")

    eye_progress_queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()

    async def damage_progress(detail: str) -> None:
        await eye_progress_queue.put(status(6, "Eye 1 — satellite damage assessment", detail))

    async def ground_progress(detail: str) -> None:
        await eye_progress_queue.put(status(6, "Eye 2 — ground sensor severity", detail))

    async def tweet_progress(detail: str) -> None:
        await eye_progress_queue.put(status(6, "Eye 3 — Twitter sentiment", detail))

    try:
        eye1_task = asyncio.create_task(
            assess_damage(
                assets=result.assets,
                pre_image=imagery.pre_image,
                post_image=imagery.post_image,
                source_detail=imagery.source_detail,
                progress=damage_progress,
            )
        )
        eye2_task = asyncio.create_task(
            assess_ground_severity(
                assets=result.assets,
                sensor_rows=sensor_points,
                event_type=result.detected_event_type,
                sensor_type=result.ground_sensor_data_type,
                progress=ground_progress,
            )
        )
        eye3_task = asyncio.create_task(
            assess_tweet_sentiment(
                latitude=params.latitude,
                longitude=params.longitude,
                disaster_date=params.disaster_date,
                event_type=result.detected_event_type,
                progress=tweet_progress,
            )
        )

        # Wrap gather so _drain_progress has a single task to watch
        async def _gather_eyes() -> tuple[list, list, Any]:
            return await asyncio.gather(eye1_task, eye2_task, eye3_task)

        combined_task = asyncio.create_task(_gather_eyes())
        async for event in _drain_progress(combined_task, eye_progress_queue):
            yield event
        eye1_result, eye2_result, eye3_result = await combined_task
    finally:
        imagery.close()

    # ── Aggregate: combine Eye 1 base with Eyes 2 & 3 confidence modifiers ──
    # aggregate_damage_level waits for all three eyes implicitly (they are
    # already resolved by asyncio.gather above). If Eye 2 or Eye 3 produced
    # no usable output their weight is silently dropped and renormalised.
    try:
        aggregated_eye1 = aggregate_damage_level(eye1_result, eye2_result, eye3_result)
    except Exception as exc:
        # Aggregation failure must never crash the pipeline — fall back to
        # raw Eye 1 output unchanged.
        import logging
        logging.getLogger(__name__).error("score_aggregator failed, using raw Eye 1: %s", exc)
        aggregated_eye1 = eye1_result

    result.damage_observations = aggregated_eye1
    result.ground_sensor_observations = eye2_result
    result.tweet_sentiment_observation = eye3_result

    skipped = len(result.assets) - len(result.damage_observations)
    eye3_status = "found tweet" if eye3_result is not None else "no tweet found"
    yield status(
        6,
        "Running Eyes 1, 2 & 3 in parallel...",
        (
            f"Eye 1: assessed {len(result.damage_observations)} assets (skipped {skipped}); "
            f"Eye 2: {len(sensor_points)} sensor points → {len(result.ground_sensor_observations)} observations; "
            f"Eye 3: {eye3_status}"
        ),
    )

    # ── Step 7: Store result and redirect ─────────────────────────────────────
    yield status(7, "Generating map...", f"Rendering {len(result.assets)} assets with {len(result.edges)} edges")
    store_result(result)
    yield complete(result)
