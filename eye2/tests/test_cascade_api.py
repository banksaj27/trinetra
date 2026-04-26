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
