"""In-memory NetworkX graph built from the database.

Loaded once at startup and rebuilt on demand after mutations.
"""
from __future__ import annotations

import itertools
import logging
import uuid
from collections import defaultdict
from datetime import UTC, datetime

import networkx as nx
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.schemas.graph import (
    GraphStats,
    Neighborhood,
    NeighborEdge,
    NeighborNode,
    ValidationResult,
)

logger = logging.getLogger(__name__)

MAX_CYCLES = 100


class GraphService:
    def __init__(self) -> None:
        self._graph: nx.DiGraph = nx.DiGraph()
        self._node_attrs: dict[uuid.UUID, dict] = {}
        self.last_rebuilt_at: datetime | None = None

    @property
    def graph(self) -> nx.DiGraph:
        return self._graph

    async def build(self, session: AsyncSession) -> None:
        """(Re-)load the full graph from the database."""
        await self.rebuild(session)

    async def rebuild(self, db: AsyncSession) -> None:
        """Clear and reload the entire DiGraph from the database."""
        self._graph.clear()
        self._node_attrs.clear()

        G = nx.DiGraph()

        asset_rows = await db.execute(
            text("SELECT id, name, asset_type, criticality_tier FROM infrastructure_assets")
        )
        node_attrs: dict[uuid.UUID, dict] = {}
        for row in asset_rows:
            G.add_node(row.id)
            node_attrs[row.id] = {
                "name": row.name,
                "asset_type": row.asset_type,
                "criticality_tier": row.criticality_tier,
            }

        dep_rows = await db.execute(
            text(
                "SELECT upstream_asset_id, downstream_asset_id, dependency_type, criticality"
                " FROM infrastructure_dependencies"
            )
        )
        for row in dep_rows:
            G.add_edge(
                row.upstream_asset_id,
                row.downstream_asset_id,
                dependency_type=row.dependency_type,
                criticality=row.criticality,
            )

        self._graph = G
        self._node_attrs = node_attrs
        self.last_rebuilt_at = datetime.now(UTC)
        logger.info(
            "Graph built: %d nodes, %d edges", G.number_of_nodes(), G.number_of_edges()
        )

    def get_stats(self) -> GraphStats:
        G = self._graph
        assets_by_type: dict[str, int] = defaultdict(int)
        for attrs in self._node_attrs.values():
            assets_by_type[attrs["asset_type"]] += 1

        edges_by_type: dict[str, int] = defaultdict(int)
        for _, _, data in G.edges(data=True):
            edges_by_type[data.get("dependency_type", "unknown")] += 1

        n = G.number_of_nodes() or 1
        return GraphStats(
            total_assets=G.number_of_nodes(),
            total_edges=G.number_of_edges(),
            assets_by_type=dict(assets_by_type),
            edges_by_type=dict(edges_by_type),
            avg_in_degree=sum(d for _, d in G.in_degree()) / n,
            avg_out_degree=sum(d for _, d in G.out_degree()) / n,
            last_rebuilt_at=self.last_rebuilt_at,
        )

    def get_neighbors(
        self,
        asset_id: uuid.UUID,
        depth: int = 2,
        direction: str = "both",
    ) -> Neighborhood:
        G = self._graph
        if asset_id not in G:
            return Neighborhood(center_id=asset_id, depth=depth, nodes=[], edges=[])

        visited: set[uuid.UUID] = {asset_id}
        frontier = {asset_id}
        collected_edges: list[tuple[uuid.UUID, uuid.UUID, dict]] = []

        for _ in range(depth):
            next_frontier: set[uuid.UUID] = set()
            for node in frontier:
                if direction in ("downstream", "both"):
                    for _, succ, data in G.out_edges(node, data=True):
                        collected_edges.append((node, succ, data))
                        if succ not in visited:
                            visited.add(succ)
                            next_frontier.add(succ)
                if direction in ("upstream", "both"):
                    for pred, _, data in G.in_edges(node, data=True):
                        collected_edges.append((pred, node, data))
                        if pred not in visited:
                            visited.add(pred)
                            next_frontier.add(pred)
            frontier = next_frontier

        nodes = []
        for nid in visited:
            attrs = self._node_attrs.get(nid, {})
            nodes.append(
                NeighborNode(
                    id=nid,
                    name=attrs.get("name", ""),
                    asset_type=attrs.get("asset_type", ""),
                    criticality_tier=attrs.get("criticality_tier", 4),
                )
            )

        seen_edges: set[tuple[uuid.UUID, uuid.UUID, str]] = set()
        edges = []
        for u, v, data in collected_edges:
            key = (u, v, data.get("dependency_type", ""))
            if key not in seen_edges:
                seen_edges.add(key)
                edges.append(
                    NeighborEdge(
                        upstream_id=u,
                        downstream_id=v,
                        dependency_type=data.get("dependency_type", ""),
                        criticality=data.get("criticality", ""),
                    )
                )

        return Neighborhood(center_id=asset_id, depth=depth, nodes=nodes, edges=edges)

    def validate(self) -> ValidationResult:
        G = self._graph

        orphans = [n for n in G.nodes() if G.degree(n) == 0]

        cycles = list(itertools.islice(nx.simple_cycles(G), MAX_CYCLES))
        cycles_truncated = len(cycles) == MAX_CYCLES

        undirected = G.to_undirected()
        components = nx.number_connected_components(undirected)

        return ValidationResult(
            orphan_assets=orphans,
            cycles=cycles,
            cycles_truncated=cycles_truncated,
            disconnected_components=components,
        )


graph_service = GraphService()
