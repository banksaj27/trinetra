from __future__ import annotations

import asyncio
import json
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, AsyncIterator

import networkx as nx

try:
    from .asset_fetcher import fetch_assets
    from .damage_assessor import assess_damage
    from .graph_builder import build_graph
    from .imagery_fetcher import fetch_imagery
    from .schemas import DamageObservation, PipelineResultResponse, PipelineRunRequest
except ImportError:  # Support `cd pipeline && uvicorn main:app`.
    from asset_fetcher import fetch_assets
    from damage_assessor import assess_damage
    from graph_builder import build_graph
    from imagery_fetcher import fetch_imagery
    from schemas import DamageObservation, PipelineResultResponse, PipelineRunRequest

TOTAL_STEPS = 5
MAX_CACHE_AGE = 3600
RESULTS_CACHE: dict[str, dict[str, Any]] = {}


@dataclass
class PipelineResult:
    run_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    assets: list[dict[str, Any]] = field(default_factory=list)
    edges: list[dict[str, Any]] = field(default_factory=list)
    damage_observations: list[dict[str, Any]] = field(default_factory=list)
    graph: nx.DiGraph = field(default_factory=nx.DiGraph)

    def response_payload(self) -> dict[str, Any]:
        response = PipelineResultResponse(
            run_id=self.run_id,
            assets=self.assets,
            edges=self.edges,
            damage_observations=[
                DamageObservation.model_validate(observation) for observation in self.damage_observations
            ],
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

    yield status(1, "Fetching infrastructure assets from HIFLD...", "Starting ArcGIS spatial queries")
    asset_queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()

    async def asset_progress(detail: str) -> None:
        await asset_queue.put(status(1, "Fetching infrastructure assets from HIFLD...", detail))

    asset_task = asyncio.create_task(
        fetch_assets(params.latitude, params.longitude, params.radius_km, progress=asset_progress)
    )
    async for event in _drain_progress(asset_task, asset_queue):
        yield event
    result.assets = await asset_task
    yield status(1, "Fetching infrastructure assets from HIFLD...", f"Found {len(result.assets)} total assets")

    yield status(2, "Building dependency graph...", "Inferring service areas and dependencies")
    result.edges, result.graph = build_graph(result.assets)
    edge_counts: dict[str, int] = {}
    for edge in result.edges:
        edge_counts[edge["dependency_type"]] = edge_counts.get(edge["dependency_type"], 0) + 1
    edge_summary = ", ".join(f"{count} {label}" for label, count in sorted(edge_counts.items())) or "0 edges"
    yield status(2, "Building dependency graph...", f"Generated {edge_summary}")

    yield status(3, "Downloading satellite imagery...", "Searching NAIP imagery, then Sentinel-2 if needed")
    imagery = await fetch_imagery(
        params.latitude,
        params.longitude,
        params.radius_km,
        params.disaster_date,
    )
    yield status(
        3,
        "Downloading satellite imagery...",
        (
            f"Using {imagery.source_detail}: "
            f"{len(imagery.pre_image.datasets)} pre-disaster tiles, "
            f"{len(imagery.post_image.datasets)} post-disaster tiles"
        ),
    )

    yield status(4, "Running damage assessment...", "Cropping imagery around each asset")
    damage_queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()

    async def damage_progress(detail: str) -> None:
        await damage_queue.put(status(4, "Running damage assessment...", detail))

    try:
        damage_task = asyncio.create_task(
            assess_damage(
                assets=result.assets,
                pre_image=imagery.pre_image,
                post_image=imagery.post_image,
                source_detail=imagery.source_detail,
                progress=damage_progress,
            )
        )
        async for event in _drain_progress(damage_task, damage_queue):
            yield event
        result.damage_observations = await damage_task
    finally:
        imagery.close()

    skipped = len(result.assets) - len(result.damage_observations)
    yield status(
        4,
        "Running damage assessment...",
        f"Assessed {len(result.damage_observations)} assets; skipped {skipped}",
    )

    yield status(5, "Generating map...", f"Rendering {len(result.assets)} assets with {len(result.edges)} edges")
    store_result(result)
    yield complete(result)

