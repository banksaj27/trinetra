"""Integration test for /api/v1/analysis/cascade.

Runs against the project's configured Postgres (settings.DATABASE_URL).
Skipped if the database isn't reachable so the suite still works without
docker-compose up.
"""

import uuid
from datetime import datetime, timezone

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.config import settings
from app.database import get_db
from app.main import app
from app.models.cascade_analysis import CascadeAnalysisRecord
from app.services.graph_builder import graph_service


@pytest.fixture
async def db_engine():
    engine = create_async_engine(settings.DATABASE_URL)
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
    except Exception as exc:
        await engine.dispose()
        pytest.skip(f"Postgres not reachable: {exc}")
    yield engine
    await engine.dispose()


@pytest.fixture
async def client(db_engine):
    session_factory = async_sessionmaker(db_engine, expire_on_commit=False)

    async def _override_get_db():
        async with session_factory() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    app.dependency_overrides[get_db] = _override_get_db

    saved_graph = graph_service._graph.copy()
    saved_attrs = dict(graph_service._node_attrs)
    graph_service._graph.clear()
    graph_service._node_attrs.clear()

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac

    graph_service._graph.clear()
    graph_service._node_attrs.clear()
    graph_service._graph.update(saved_graph)
    graph_service._node_attrs.update(saved_attrs)
    app.dependency_overrides.pop(get_db, None)


def _seed_graph():
    root_id = uuid.uuid4()
    hospital_id = uuid.uuid4()
    graph_service._graph.add_node(root_id)
    graph_service._graph.add_node(hospital_id)
    graph_service._node_attrs[root_id] = {
        "asset_type": "substation",
        "criticality_tier": 2,
        "population_served": 0,
        "name": "Substation Test",
    }
    graph_service._node_attrs[hospital_id] = {
        "asset_type": "hospital",
        "criticality_tier": 1,
        "population_served": 5000,
        "name": "Hospital Test",
    }
    graph_service._graph.add_edge(
        root_id,
        hospital_id,
        dependency_type="power",
        criticality="critical",
        failover_time_minutes=0,
    )
    return root_id


async def test_post_then_get_round_trip(client):
    root_id = _seed_graph()
    payload = {
        "observation_id": "obs-api-test-1",
        "asset_id": str(root_id),
        "damage_level": "destroyed",
        "confidence": 0.9,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }

    post_resp = await client.post("/api/v1/analysis/cascade", json=payload)
    assert post_resp.status_code == 200, post_resp.text
    posted = post_resp.json()

    assert "id" in posted
    assert posted["triggered_by_observation_id"] == "obs-api-test-1"
    assert posted["root_asset_id"] == str(root_id)
    assert posted["impact_summary"]["affected_assets"], "expected one downstream"
    assert posted["impact_summary"]["affected_assets"][0]["asset_type"] == "hospital"

    get_resp = await client.get(f"/api/v1/analysis/cascade/{posted['id']}")
    assert get_resp.status_code == 200, get_resp.text
    fetched = get_resp.json()

    assert fetched == posted


async def test_post_is_idempotent(client, db_engine):
    root_id = _seed_graph()
    payload = {
        "observation_id": "obs-api-idempotent-1",
        "asset_id": str(root_id),
        "damage_level": "destroyed",
        "confidence": 0.9,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }

    first = await client.post("/api/v1/analysis/cascade", json=payload)
    assert first.status_code == 200, first.text
    second = await client.post("/api/v1/analysis/cascade", json=payload)
    assert second.status_code == 200, second.text

    assert first.json() == second.json()

    cascade_id = first.json()["cascade_id"]
    session_factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with session_factory() as session:
        count = (
            await session.execute(
                text("SELECT COUNT(*) FROM cascade_analyses WHERE cascade_id = :cid"),
                {"cid": cascade_id},
            )
        ).scalar_one()
    assert count == 1


async def test_get_unknown_id_returns_404(client):
    resp = await client.get(f"/api/v1/analysis/cascade/{uuid.uuid4()}")
    assert resp.status_code == 404


async def test_post_unknown_root_returns_400(client):
    payload = {
        "observation_id": "obs-api-test-bad",
        "asset_id": str(uuid.uuid4()),
        "damage_level": "destroyed",
        "confidence": 0.9,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    resp = await client.post("/api/v1/analysis/cascade", json=payload)
    assert resp.status_code == 400


async def _seed_priority_rows(db_engine, scores):
    session_factory = async_sessionmaker(db_engine, expire_on_commit=False)
    inserted_ids = []
    async with session_factory() as session:
        await session.execute(text("TRUNCATE cascade_analyses"))
        for score in scores:
            record = CascadeAnalysisRecord(
                cascade_id=f"cascade-priority-{uuid.uuid4()}",
                observation_id=f"obs-priority-{score}",
                root_asset_id=uuid.uuid4(),
                analysis_time=datetime.now(timezone.utc),
                total_population_impacted=int(score * 1000),
                critical_facilities_impacted=int(score),
                restoration_priority=0,
                priority_score=score,
                hours_to_first_critical_failure=score,
                severity_multiplier=1.0,
                urgency_multiplier=1.0,
                cascade={"priority_score": score},
            )
            session.add(record)
        await session.commit()
        for r in (
            await session.execute(
                text(
                    "SELECT id, priority_score FROM cascade_analyses ORDER BY priority_score DESC"
                )
            )
        ).all():
            inserted_ids.append(r)
    return inserted_ids


async def test_priorities_sorted_with_rank(client, db_engine):
    await _seed_priority_rows(db_engine, [9.0, 5.0, 1.0])

    resp = await client.get("/api/v1/analysis/priorities")
    assert resp.status_code == 200, resp.text
    items = resp.json()

    assert len(items) == 3
    assert [item["priority_score"] for item in items] == [9.0, 5.0, 1.0]
    assert [item["restoration_priority"] for item in items] == [1, 2, 3]
    expected_keys = {
        "id",
        "root_asset_id",
        "priority_score",
        "hours_to_first_critical_failure",
        "total_population_impacted",
        "critical_facilities_impacted",
        "created_at",
        "restoration_priority",
    }
    assert set(items[0].keys()) == expected_keys


async def test_priorities_limit_and_offset(client, db_engine):
    await _seed_priority_rows(db_engine, [9.0, 5.0, 1.0])

    resp = await client.get("/api/v1/analysis/priorities?limit=1")
    assert resp.status_code == 200
    items = resp.json()
    assert len(items) == 1
    assert items[0]["priority_score"] == 9.0
    assert items[0]["restoration_priority"] == 1

    resp = await client.get("/api/v1/analysis/priorities?limit=1&offset=1")
    assert resp.status_code == 200
    items = resp.json()
    assert len(items) == 1
    assert items[0]["priority_score"] == 5.0
    assert items[0]["restoration_priority"] == 2

    resp = await client.get("/api/v1/analysis/priorities?limit=2&offset=1")
    assert resp.status_code == 200
    items = resp.json()
    assert len(items) == 2
    assert [item["priority_score"] for item in items] == [5.0, 1.0]
    assert [item["restoration_priority"] for item in items] == [2, 3]
