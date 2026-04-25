"""TriNetra Eye 3 — cascade engine.

This module owns downstream-failure propagation for the dependency graph
built by ``app/services/graph_builder.py``. The engine produces a
``CascadeAnalysis`` JSON record matching architecture doc Section 4.1.5.

This file contains only the data contracts (Pydantic v2 models), module
constants, and the ``get_failover_minutes`` helper. Traversal, time-to-
failure propagation, and priority scoring live in subsequent additions.
"""

import hashlib
import logging
import math
import uuid
from collections import Counter, deque
from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Output contracts (architecture doc §4.1.5)
# ---------------------------------------------------------------------------


class AffectedAsset(BaseModel):
    asset_id: uuid.UUID
    asset_type: str
    criticality_tier: int
    cascade_depth: int
    dependency_type: str
    failover_time_minutes: int
    time_to_failure_minutes: int
    population_served: int


class ImpactSummary(BaseModel):
    affected_assets: list[AffectedAsset]
    by_asset_type: dict[str, int]
    by_cascade_depth: dict[str, int]


class CascadeAnalysis(BaseModel):
    cascade_id: str
    triggered_by_observation_id: str
    root_asset_id: uuid.UUID
    analysis_time: datetime
    total_population_impacted: int
    critical_facilities_impacted: int
    restoration_priority: int
    priority_score: float
    impact_summary: ImpactSummary


# ---------------------------------------------------------------------------
# Input contract
# ---------------------------------------------------------------------------


class DamageObservation(BaseModel):
    model_config = ConfigDict(extra="ignore")

    observation_id: str
    asset_id: uuid.UUID
    asset_type: str | None = None
    damage_level: Literal["destroyed", "major", "minor", "affected", "unaffected"]
    confidence: float = Field(ge=0.0, le=1.0)
    timestamp: datetime


# ---------------------------------------------------------------------------
# Engine constants
# ---------------------------------------------------------------------------


MAX_CASCADE_DEPTH: int = 5

DAMAGE_LEVEL_CONFIG: dict[str, dict] = {
    "destroyed":  {"propagate": True,  "max_depth": 5, "severity_multiplier": 1.0},
    "major":      {"propagate": True,  "max_depth": 5, "severity_multiplier": 0.7},
    "minor":      {"propagate": True,  "max_depth": 1, "severity_multiplier": 0.4},
    "affected":   {"propagate": True,  "max_depth": 1, "severity_multiplier": 0.2},
    "unaffected": {"propagate": False, "max_depth": 0, "severity_multiplier": 0.0},
}

CRITICALITY_WEIGHTS: dict[int, float] = {1: 4.0, 2: 2.0, 3: 1.0, 4: 0.5}

# Compensates for spatial inference writing 0 for most edges. Keyed by
# (dependency_type, downstream asset_type) -> minutes the downstream asset
# can survive without the upstream provider.
FAILOVER_DEFAULTS: dict[str, dict[str, int]] = {
    "power": {
        "hospital": 4320,
        "shelter": 1440,
        "cell_tower": 240,
        "water_treatment": 60,
        "fire_station": 720,
        "police_station": 720,
    },
    "water": {
        "hospital": 240,
        "shelter": 240,
        "fire_station": 120,
    },
    "communications": {},
}

FAILOVER_DEFAULT_FALLBACK: int = 0

URGENCY_MIN_HOURS: float = 15.0


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def get_failover_minutes(edge_data: dict, downstream_asset_type: str) -> int:
    """Return failover minutes for an edge, falling back to defaults.

    Spatial inference writes ``0`` for most edges (treated as "unset"
    sentinel), so respect explicit positive values from ``edge_data`` and
    otherwise consult ``FAILOVER_DEFAULTS``.
    """
    explicit = edge_data.get("failover_time_minutes")
    if isinstance(explicit, int) and explicit > 0:
        return explicit

    dep_type = edge_data.get("dependency_type")
    by_dep = FAILOVER_DEFAULTS.get(dep_type, {}) if dep_type else {}
    default = by_dep.get(downstream_asset_type)
    if default is not None:
        return default

    return FAILOVER_DEFAULT_FALLBACK


# ---------------------------------------------------------------------------
# Engine
# ---------------------------------------------------------------------------


def _traverse_cascade(
    root_id: uuid.UUID,
    graph_service,
    max_depth: int,
    root_failure_time: datetime,
) -> list[dict]:
    """BFS downstream from ``root_id``, propagating time-to-failure.

    Returns one dict per affected downstream node (root excluded). On
    diamond paths, keeps the shorter time-to-failure (binding constraint).
    """
    del root_failure_time  # reserved for future absolute-timestamp output
    G = graph_service._graph
    nodes = graph_service._node_attrs

    visited: dict[uuid.UUID, dict] = {}
    queue: deque = deque()
    queue.append((root_id, 0, 0, None))

    while queue:
        node_id, depth, ttf, _dep_in = queue.popleft()
        if depth >= max_depth:
            continue

        for _, child_id, edge_data in G.out_edges(node_id, data=True):
            if child_id == root_id:
                continue

            child_attrs = nodes.get(child_id)
            if child_attrs is None:
                logger.warning(
                    "Cascade traversal: node %s missing from _node_attrs", child_id
                )
                continue

            child_asset_type = child_attrs.get("asset_type", "")
            edge_failover = get_failover_minutes(edge_data, child_asset_type)
            child_ttf = ttf + edge_failover
            child_depth = depth + 1
            dep_type = edge_data.get("dependency_type", "unknown")

            existing = visited.get(child_id)
            if existing is not None and existing["time_to_failure_minutes"] <= child_ttf:
                continue

            entry = {
                "asset_id": child_id,
                "asset_type": child_asset_type,
                "criticality_tier": child_attrs.get("criticality_tier", 4),
                "cascade_depth": child_depth,
                "dependency_type": dep_type,
                "failover_time_minutes": edge_failover,
                "time_to_failure_minutes": child_ttf,
                "population_served": child_attrs.get("population_served") or 0,
            }

            if existing is not None:
                existing.update(entry)
            else:
                visited[child_id] = entry

            queue.append((child_id, child_depth, child_ttf, dep_type))

    return list(visited.values())


def _aggregate_impact(affected: list[dict]) -> ImpactSummary:
    by_asset_type = Counter(a["asset_type"] for a in affected)
    by_cascade_depth = Counter(str(a["cascade_depth"]) for a in affected)
    sorted_assets = sorted(affected, key=lambda a: a["time_to_failure_minutes"])
    return ImpactSummary(
        affected_assets=[AffectedAsset(**a) for a in sorted_assets],
        by_asset_type=dict(by_asset_type),
        by_cascade_depth=dict(by_cascade_depth),
    )


def _compute_priority_score(
    affected: list[dict],
    confidence: float,
    severity_multiplier: float,
) -> float:
    if not affected:
        return 0.0

    total_pop = sum(a["population_served"] for a in affected)
    max_criticality_tier = min(a["criticality_tier"] for a in affected)
    criticality_weight = CRITICALITY_WEIGHTS.get(max_criticality_tier, 1.0)

    tier1_ttfs = [
        a["time_to_failure_minutes"] for a in affected if a["criticality_tier"] == 1
    ]
    if tier1_ttfs:
        first_critical_ttf_min = min(tier1_ttfs)
    else:
        first_critical_ttf_min = min(a["time_to_failure_minutes"] for a in affected)

    hours_to_first = max(URGENCY_MIN_HOURS, first_critical_ttf_min / 60.0)
    urgency = 60.0 / hours_to_first

    score = (
        math.log10(1 + total_pop)
        * criticality_weight
        * confidence
        * severity_multiplier
        * urgency
    )
    return round(score, 4)


def run_cascade(observation, graph_service) -> CascadeAnalysis:
    """Compute downstream cascade for a single damage observation."""
    if not isinstance(observation, DamageObservation):
        observation = DamageObservation.model_validate(observation)

    cfg = DAMAGE_LEVEL_CONFIG[observation.damage_level]
    ts = observation.timestamp
    short_hash = hashlib.sha1(observation.observation_id.encode()).hexdigest()[:6]
    cascade_id = f"cascade_{ts:%Y%m%d_%H%M%S}_{short_hash}"
    analysis_time = datetime.now(timezone.utc)

    if not cfg["propagate"]:
        return CascadeAnalysis(
            cascade_id=cascade_id,
            triggered_by_observation_id=observation.observation_id,
            root_asset_id=observation.asset_id,
            analysis_time=analysis_time,
            total_population_impacted=0,
            critical_facilities_impacted=0,
            restoration_priority=0,
            priority_score=0.0,
            impact_summary=ImpactSummary(
                affected_assets=[], by_asset_type={}, by_cascade_depth={}
            ),
        )

    if observation.asset_id not in graph_service._graph:
        raise ValueError(
            f"Root asset {observation.asset_id} not in dependency graph"
        )

    affected = _traverse_cascade(
        root_id=observation.asset_id,
        graph_service=graph_service,
        max_depth=cfg["max_depth"],
        root_failure_time=observation.timestamp,
    )

    impact = _aggregate_impact(affected)
    critical_facilities_impacted = sum(
        1 for a in affected if a["criticality_tier"] == 1
    )
    total_population_impacted = sum(a["population_served"] for a in affected)
    priority_score = _compute_priority_score(
        affected, observation.confidence, cfg["severity_multiplier"]
    )

    return CascadeAnalysis(
        cascade_id=cascade_id,
        triggered_by_observation_id=observation.observation_id,
        root_asset_id=observation.asset_id,
        analysis_time=analysis_time,
        total_population_impacted=total_population_impacted,
        critical_facilities_impacted=critical_facilities_impacted,
        restoration_priority=0,
        priority_score=priority_score,
        impact_summary=impact,
    )
