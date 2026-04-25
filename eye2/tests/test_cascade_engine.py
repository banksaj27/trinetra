"""Tests for app/services/cascade_engine.run_cascade."""

import uuid
from datetime import datetime, timezone

import networkx as nx
import pytest

from app.services.cascade_engine import DamageObservation, run_cascade


class FakeGraphService:
    """Stand-in for GraphService — engine only reads `_graph` and `_node_attrs`."""

    def __init__(self):
        self._graph: nx.DiGraph = nx.DiGraph()
        self._node_attrs: dict[uuid.UUID, dict] = {}


@pytest.fixture
def graph_fixture():
    """Build the Caguas substation cascade graph with a power/water diamond."""
    svc = FakeGraphService()

    NODE_IDS = {
        "substation_caguas": uuid.uuid4(),
        "hospital_caguas_regional": uuid.uuid4(),
        "cell_tower_pr_087": uuid.uuid4(),
        "water_pump_caguas": uuid.uuid4(),
        "shelter_caguas": uuid.uuid4(),
    }

    node_specs = {
        "substation_caguas": {
            "asset_type": "substation",
            "criticality_tier": 2,
            "population_served": 0,
            "name": "Substation Caguas",
        },
        "hospital_caguas_regional": {
            "asset_type": "hospital",
            "criticality_tier": 1,
            "population_served": 18000,
            "name": "Hospital Caguas Regional",
        },
        "cell_tower_pr_087": {
            "asset_type": "cell_tower",
            "criticality_tier": 2,
            "population_served": 12000,
            "name": "Cell Tower PR-087",
        },
        "water_pump_caguas": {
            "asset_type": "water_treatment",
            "criticality_tier": 2,
            "population_served": 0,
            "name": "Water Pump Caguas",
        },
        "shelter_caguas": {
            "asset_type": "shelter",
            "criticality_tier": 2,
            "population_served": 2000,
            "name": "Shelter Caguas",
        },
    }

    for key, attrs in node_specs.items():
        nid = NODE_IDS[key]
        svc._graph.add_node(nid)
        svc._node_attrs[nid] = attrs

    edges = [
        ("substation_caguas", "hospital_caguas_regional", "power", 0),
        ("substation_caguas", "cell_tower_pr_087", "power", 0),
        ("substation_caguas", "water_pump_caguas", "power", 0),
        ("water_pump_caguas", "hospital_caguas_regional", "water", 240),
        ("water_pump_caguas", "shelter_caguas", "water", 240),
    ]
    for upstream, downstream, dep_type, failover in edges:
        svc._graph.add_edge(
            NODE_IDS[upstream],
            NODE_IDS[downstream],
            dependency_type=dep_type,
            criticality="critical",
            failover_time_minutes=failover,
        )

    return svc, NODE_IDS


def _make_observation(asset_id, damage_level, confidence=0.9):
    return DamageObservation(
        observation_id="obs-test",
        asset_id=asset_id,
        damage_level=damage_level,
        confidence=confidence,
        timestamp=datetime.now(timezone.utc),
    )


def test_unaffected_returns_empty_cascade(graph_fixture):
    svc, ids = graph_fixture
    obs = _make_observation(ids["substation_caguas"], "unaffected", confidence=0.9)

    result = run_cascade(obs, svc)

    assert result.priority_score == 0.0
    assert result.total_population_impacted == 0
    assert result.impact_summary.affected_assets == []


def test_destroyed_substation_full_cascade(graph_fixture):
    svc, ids = graph_fixture
    obs = _make_observation(ids["substation_caguas"], "destroyed", confidence=0.94)

    result = run_cascade(obs, svc)

    assert result.total_population_impacted == 32000
    assert result.critical_facilities_impacted == 1
    assert result.priority_score > 0

    by_type = result.impact_summary.by_asset_type
    assert by_type.get("hospital") == 1
    assert by_type.get("cell_tower") == 1
    assert by_type.get("water_treatment") == 1
    assert by_type.get("shelter") == 1

    cell_tower = next(
        a for a in result.impact_summary.affected_assets
        if a.asset_id == ids["cell_tower_pr_087"]
    )
    assert cell_tower.time_to_failure_minutes == 240


def test_minor_damage_caps_depth_to_one(graph_fixture):
    svc, ids = graph_fixture
    obs = _make_observation(ids["substation_caguas"], "minor", confidence=0.9)

    result = run_cascade(obs, svc)

    affected_ids = {a.asset_id for a in result.impact_summary.affected_assets}
    assert ids["hospital_caguas_regional"] in affected_ids
    assert ids["cell_tower_pr_087"] in affected_ids
    assert ids["water_pump_caguas"] in affected_ids
    assert ids["shelter_caguas"] not in affected_ids


def test_diamond_keeps_shortest_failure_time(graph_fixture):
    svc, ids = graph_fixture
    obs = _make_observation(ids["substation_caguas"], "destroyed", confidence=0.9)

    result = run_cascade(obs, svc)

    hospital_entries = [
        a for a in result.impact_summary.affected_assets
        if a.asset_id == ids["hospital_caguas_regional"]
    ]
    assert len(hospital_entries) == 1
    hospital = hospital_entries[0]
    assert hospital.time_to_failure_minutes == 300
    assert hospital.cascade_depth == 2


def test_root_asset_missing_raises(graph_fixture):
    svc, _ = graph_fixture
    bogus_id = uuid.uuid4()
    obs = _make_observation(bogus_id, "destroyed", confidence=0.9)

    with pytest.raises(ValueError):
        run_cascade(obs, svc)


def test_priority_score_scales_with_confidence(graph_fixture):
    svc, ids = graph_fixture

    low = run_cascade(
        _make_observation(ids["substation_caguas"], "destroyed", confidence=0.5),
        svc,
    )
    high = run_cascade(
        _make_observation(ids["substation_caguas"], "destroyed", confidence=0.95),
        svc,
    )

    expected_ratio = 0.95 / 0.5
    actual_ratio = high.priority_score / low.priority_score
    assert abs(actual_ratio - expected_ratio) / expected_ratio < 0.01


def test_affected_assets_sorted_by_ttf(graph_fixture):
    svc, ids = graph_fixture
    obs = _make_observation(ids["substation_caguas"], "destroyed", confidence=0.9)

    result = run_cascade(obs, svc)

    ttfs = [a.time_to_failure_minutes for a in result.impact_summary.affected_assets]
    assert ttfs == sorted(ttfs)
