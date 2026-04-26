from __future__ import annotations

from collections import defaultdict, deque
from typing import Any


FAILED_LEVELS = {"destroyed", "major-damage"}
PROVIDER_TYPES = {"substation", "water_treatment", "cell_tower"}
SEVERITY_RANK = {
    "destroyed": 4,
    "major-damage": 3,
    "minor-damage": 2,
    "no-damage": 1,
    "skipped": 0,
}


def _edge_key(edge: dict[str, Any]) -> str:
    return f"{edge['upstream_id']}|{edge['downstream_id']}|{edge['dependency_type']}"


def compute_cascade_summary(
    assets: list[dict[str, Any]],
    edges: list[dict[str, Any]],
    damage_observations: list[dict[str, Any]],
) -> dict[str, Any]:
    """Summarize downstream reachability from failed infrastructure providers."""
    assets_by_id = {asset["asset_id"]: asset for asset in assets}
    damage_by_id = {
        observation["asset_id"]: observation.get("damage_level", "skipped")
        for observation in damage_observations
    }

    outgoing: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for edge in edges:
        outgoing[edge["upstream_id"]].append(edge)

    roots: list[dict[str, Any]] = []
    all_affected_ids: set[str] = set()
    all_edge_keys: set[str] = set()
    dependency_counts: dict[str, int] = defaultdict(int)

    for asset in assets:
        asset_id = asset["asset_id"]
        damage_level = damage_by_id.get(asset_id, "skipped")
        if (
            damage_level not in FAILED_LEVELS
            or asset.get("asset_type") not in PROVIDER_TYPES
            or not outgoing.get(asset_id)
        ):
            continue

        visited_depth: dict[str, int] = {asset_id: 0}
        affected_ids: set[str] = set()
        root_edge_keys: set[str] = set()
        queue: deque[str] = deque([asset_id])

        while queue:
            upstream_id = queue.popleft()
            next_depth = visited_depth[upstream_id] + 1
            for edge in outgoing.get(upstream_id, []):
                downstream_id = edge["downstream_id"]
                edge_key = _edge_key(edge)
                root_edge_keys.add(edge_key)

                if downstream_id not in visited_depth:
                    visited_depth[downstream_id] = next_depth
                    affected_ids.add(downstream_id)
                    queue.append(downstream_id)

        if not affected_ids:
            continue

        for edge_key in root_edge_keys:
            all_edge_keys.add(edge_key)
        all_affected_ids.update(affected_ids)

        root_dependency_counts: dict[str, int] = defaultdict(int)
        for edge in edges:
            if _edge_key(edge) in root_edge_keys:
                root_dependency_counts[edge["dependency_type"]] += 1
                dependency_counts[edge["dependency_type"]] += 1

        tier_1_ids = sorted(
            affected_id
            for affected_id in affected_ids
            if int(assets_by_id.get(affected_id, {}).get("criticality_tier") or 0) == 1
        )
        max_depth = max(visited_depth.values(), default=0)

        roots.append(
            {
                "asset_id": asset_id,
                "name": asset.get("name") or "Unnamed asset",
                "asset_type": asset.get("asset_type") or "unknown",
                "damage_level": damage_level,
                "affected_asset_ids": sorted(affected_ids),
                "affected_count": len(affected_ids),
                "tier_1_downstream_count": len(tier_1_ids),
                "tier_1_downstream_asset_ids": tier_1_ids,
                "max_depth": max_depth,
                "edge_keys": sorted(root_edge_keys),
                "dependency_counts": dict(sorted(root_dependency_counts.items())),
            }
        )

    roots.sort(
        key=lambda root: (
            root["affected_count"],
            root["tier_1_downstream_count"],
            SEVERITY_RANK.get(root["damage_level"], 0),
        ),
        reverse=True,
    )

    affected_by_type: dict[str, int] = defaultdict(int)
    for asset_id in all_affected_ids:
        asset = assets_by_id.get(asset_id)
        if asset is not None:
            affected_by_type[asset.get("asset_type") or "unknown"] += 1

    tier_1_affected_ids = sorted(
        asset_id
        for asset_id in all_affected_ids
        if int(assets_by_id.get(asset_id, {}).get("criticality_tier") or 0) == 1
    )

    return {
        "root_count": len(roots),
        "affected_asset_ids": sorted(all_affected_ids),
        "affected_count": len(all_affected_ids),
        "tier_1_affected_count": len(tier_1_affected_ids),
        "tier_1_affected_asset_ids": tier_1_affected_ids,
        "max_depth": max((root["max_depth"] for root in roots), default=0),
        "dependency_counts": dict(sorted(dependency_counts.items())),
        "affected_by_type": dict(sorted(affected_by_type.items())),
        "cascade_edge_keys": sorted(all_edge_keys),
        "roots": roots,
        "root_asset_ids": [root["asset_id"] for root in roots],
    }
