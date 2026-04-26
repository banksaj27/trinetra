# TriNetra AI — Cascade Engine (Eye 3)

## What this repo is

TriNetra AI is a disaster impact intelligence platform. When infrastructure
takes damage, the system walks a dependency graph to identify downstream
failures and ranks them for emergency responders.

This codebase owns **Eye 3 — the cascade engine**. Eyes 1 (CV damage
detection) and 2 (dependency graph construction) are upstream and already
exist. By the time data reaches the cascade engine, damage has been
detected, geo-correlated to a known asset, and the dependency graph is
populated in PostGIS and loaded into memory as a NetworkX DiGraph.

## Repo layout

The repository now contains three sibling directories at its root:
`eye1/` (CV damage detection), `eye2/` (this project — infrastructure
graph + cascade engine), and `pipeline/` (the unified pipeline that
orchestrates both). This `CLAUDE.md` covers `eye2/` specifically; the
broader repo has more outside this directory.

## My role in this repo

I build and maintain the cascade engine: BFS traversal of the dependency
graph, time-to-failure propagation, impact aggregation, and priority
scoring. I produce one output — a `CascadeAnalysis` JSON record — that is
consumed by both the website dashboard and a Fetch.ai Agentverse agent.
I do not build two outputs. I do not modify the graph; I read from it.

## Architecture in one paragraph

The engine is a pure function: `run_cascade(observation, graph_service) ->
CascadeAnalysis`. It reads `app/services/graph_builder.py:GraphService`
which exposes `self._graph` (networkx.DiGraph with full edge data) and
`self._node_attrs` (dict[uuid.UUID, dict]) — graph nodes are stored as
raw dicts with keys `asset_type`, `criticality_tier`, `population_served`,
`name`. There is no `NodeData` type. Edges go upstream → downstream
(provider → dependent). Cascade traverses `out_edges` from the damaged
asset. Output schema is fixed by Section 4.1.5 of the architecture doc and
must not drift.

## Source of truth

`TriNetra_AI_Architecture_Document_docx.pdf` (v1.0, April 2026) defines
data models, API contracts, and the `CascadeAnalysis` output shape. When
the doc and the code disagree, the doc wins unless I've explicitly noted
an exception below.

## What's already built (don't rebuild)

- PostGIS schema, Alembic migrations, async SQLAlchemy stack
- HIFLD data loader, Census loader, area loader, demo data generator
- Voronoi service-area inference for substations and water plants
- Power / water / communications dependency edge inference
- `GraphService` with in-memory DiGraph, n-hop neighbor traversal, graph
  validation, rebuild endpoint
- FastAPI app with asset CRUD, dependency CRUD, graph stats, debug map
- Docker Compose (PostGIS 16-3.4), pydantic-settings config

## Known gaps (accepted for now, don't fix unprompted)

- No `road_access` or `fuel` dependency inference
- Communications distance uses degree approximation, not geodesic meters
- `failover_time_minutes` from spatial inference is mostly 0 — the engine
  compensates with a hardcoded `FAILOVER_DEFAULTS` table keyed by
  `(dependency_type, downstream_asset_type)`
- Existing `GraphService.get_neighbors` discards per-node depth and edge
  metadata — the cascade engine writes its own richer traversal over
  `self._graph` directly rather than reusing it
- No auth beyond `X-Admin-Key` on rebuild
- Frontend is a debug Mapbox map only

## Code style

- Python 3.11+, async throughout, type hints everywhere
- Pydantic v2 for all data contracts
- `logging.getLogger(__name__)` per module, no print statements
- Service modules go in `app/services/`, one file per service unless the
  file exceeds ~400 lines
- Match existing patterns in `graph_builder.py` for new services
- No new dependencies without a clear reason — networkx, sqlalchemy,
  pydantic, fastapi, asyncpg are the load-bearing libraries

## What's done (cumulative)

- **Step 3 — API wiring**: `POST /api/v1/analysis/cascade` and
  `GET /api/v1/analysis/cascade/{id}` are live in
  `app/api/cascade.py`. The endpoint is idempotent: re-posting the same
  `observation_id` + `asset_id` + `timestamp` returns the existing record
  (200) instead of erroring.
- **Step 3 — persistence**: `cascade_analyses` table exists, Alembic
  migration applied. `cascade_id` column has a UNIQUE constraint —
  duplicate detection relies on it.
- **Step 4 — PR data loaded**: Puerto Rico HIFLD substation dataset is
  loaded and verified. `GraphService` builds the live DiGraph from it.
  Cascade scoring runs against real node/edge data.
- **Step 4 — priorities endpoint**: `GET /api/v1/analysis/priorities`
  returns `CascadePrioritySummary` list sorted by `priority_score`.
- **CORS**: middleware added in `main.py` so the dashboard frontend can
  call the API from a browser.
- **Tests**: `tests/test_cascade_engine.py` (unit) and
  `tests/test_cascade_api.py` (integration) both pass. The integration
  suite hits a real async SQLite DB and includes idempotency coverage.
- **API reference doc**: `docs/API.md` documents all endpoints with
  request/response examples captured against the PR dataset.

## What's next — Agentverse integration (Step 5)

The `CascadeAnalysis` JSON produced by `POST /cascade` is the payload the
Fetch.ai Agentverse agent will consume. Next work:

1. Stand up the Agentverse agent that polls `/priorities` and acts on the
   top-ranked cascade.
2. Wire the agent's address into the cascade engine so completed analyses
   can be pushed (or the agent can pull on a schedule).
3. End-to-end smoke test: damage observation in → agent receives ranked
   cascade out.

Nothing in `cascade_engine.py`, `graph_builder.py`, or the Alembic
migrations should need to change for Step 5.

## Things to never do

- Never modify `graph_builder.py`, the dependency inference SQL, or the
  HIFLD loader without explicit instruction
- Never add a second output format — the dashboard and Agentverse both
  consume the same `CascadeAnalysis` JSON
- Never deviate from the Section 4.1.5 schema for `CascadeAnalysis`
- Never invent fields on the output that aren't in the architecture doc,
  with the single exception of `priority_score` (float) which we add for
  internal ranking
- Never write code that assumes the graph is empty or that edges have
  realistic `failover_time_minutes` — always go through the engine's
  `get_failover_minutes()` helper