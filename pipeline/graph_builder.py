from __future__ import annotations

from collections import defaultdict
from typing import Any

import networkx as nx
from shapely.geometry import MultiPoint, Point
from shapely.ops import voronoi_diagram

WATER_CONSUMERS = {"hospital", "shelter", "fire_station"}
COMMS_CONSUMERS = {
    "hospital",
    "911_center",
    "shelter",
    "fire_station",
    "ems_station",
    "police_station",
}
COMMS_RADIUS_DEGREES = 0.1


def _criticality(asset: dict[str, Any]) -> str:
    tier = int(asset.get("criticality_tier") or 0)
    if tier == 1:
        return "critical"
    if tier == 2:
        return "degraded_ops"
    return "convenience"


def _point(asset: dict[str, Any]) -> Point:
    return Point(float(asset["longitude"]), float(asset["latitude"]))


def _add_edge(
    edges: list[dict[str, Any]],
    graph: nx.DiGraph,
    seen: set[tuple[str, str, str]],
    upstream: dict[str, Any],
    downstream: dict[str, Any],
    dependency_type: str,
) -> None:
    key = (upstream["asset_id"], downstream["asset_id"], dependency_type)
    if upstream["asset_id"] == downstream["asset_id"] or key in seen:
        return

    seen.add(key)
    edge = {
        "upstream_id": upstream["asset_id"],
        "downstream_id": downstream["asset_id"],
        "dependency_type": dependency_type,
        "criticality": _criticality(downstream),
    }
    edges.append(edge)
    graph.add_edge(upstream["asset_id"], downstream["asset_id"], **edge)


def _group_by_type(assets: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for asset in assets:
        grouped[asset["asset_type"]].append(asset)
    return grouped


def _assign_by_voronoi(
    providers: list[dict[str, Any]],
    consumers: list[dict[str, Any]],
) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    if not providers:
        return []
    if len(providers) == 1:
        return [(providers[0], consumer) for consumer in consumers]

    provider_points = [(_point(provider), provider) for provider in providers]
    all_points = [_point(asset) for asset in [*providers, *consumers]]
    envelope = MultiPoint(all_points).convex_hull.buffer(0.1) if all_points else None
    regions = voronoi_diagram(MultiPoint([point for point, _ in provider_points]), envelope=envelope)

    cells: list[tuple[dict[str, Any], Any]] = []
    for polygon in regions.geoms:
        for point, provider in provider_points:
            if polygon.covers(point):
                cells.append((provider, polygon))
                break

    assignments: list[tuple[dict[str, Any], dict[str, Any]]] = []
    for consumer in consumers:
        consumer_point = _point(consumer)
        assigned_provider = None
        for provider, polygon in cells:
            if polygon.covers(consumer_point):
                assigned_provider = provider
                break
        if assigned_provider is None:
            assigned_provider = min(providers, key=lambda provider: consumer_point.distance(_point(provider)))
        assignments.append((assigned_provider, consumer))
    return assignments


def build_graph(assets: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], nx.DiGraph]:
    """Build dependency edges from asset locations using in-memory spatial inference."""
    graph = nx.DiGraph()
    edges: list[dict[str, Any]] = []
    seen_edges: set[tuple[str, str, str]] = set()

    for asset in assets:
        graph.add_node(asset["asset_id"], **asset)

    by_type = _group_by_type(assets)
    substations = by_type.get("substation", [])
    water_plants = by_type.get("water_treatment", [])
    cell_towers = by_type.get("cell_tower", [])

    power_consumers = [asset for asset in assets if asset["asset_type"] != "substation"]
    for substation, consumer in _assign_by_voronoi(substations, power_consumers):
        _add_edge(edges, graph, seen_edges, substation, consumer, "power")

    water_consumers = [asset for asset in assets if asset["asset_type"] in WATER_CONSUMERS]
    for water_plant, consumer in _assign_by_voronoi(water_plants, water_consumers):
        _add_edge(edges, graph, seen_edges, water_plant, consumer, "water")

    for asset in assets:
        if asset["asset_type"] not in COMMS_CONSUMERS:
            continue
        asset_point = _point(asset)
        for tower in cell_towers:
            if asset_point.distance(_point(tower)) <= COMMS_RADIUS_DEGREES:
                _add_edge(edges, graph, seen_edges, tower, asset, "communications")

    return edges, graph

