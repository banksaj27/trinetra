# TriNetra AI — API Reference

## Overview

This API exposes the TriNetra cascade engine: given a damage observation on an
infrastructure asset, it walks the dependency graph, identifies downstream
failures, and ranks scenarios by impact. It is consumed by the website
dashboard and a Fetch.ai Agentverse agent. Both consume the same
`CascadeAnalysis` JSON.

For an interactive schema explorer, see `/docs` (Swagger UI). This document is
the prose reference — read this first, then use `/docs` to poke at things.

## Base URL and auth

| Environment | Base URL |
|-------------|----------|
| Local dev   | `http://localhost:8000` |

No authentication is required for the cascade endpoints. The only auth-gated
endpoint in the service is the admin graph rebuild (out of scope here).

## CORS

CORS is enabled wide-open for development:

| Setting             | Value     |
|---------------------|-----------|
| `allow_origins`     | `["*"]`   |
| `allow_credentials` | `true`    |
| `allow_methods`     | `["*"]`   |
| `allow_headers`     | `["*"]`   |

Any browser-based client (any origin) can call the API directly. No further
configuration is required for the website team during the demo.

> ⚠ **Production note.** `allow_origins=["*"]` is insecure for anything
> beyond a hackathon laptop. There is a `# TODO: tighten allow_origins for
> production` marker in `app/main.py` — replace `["*"]` with the explicit
> list of allowed origins before deploying anywhere real.

> **Heads-up on the response header.** Because `allow_credentials=true`,
> Starlette's CORS middleware echoes the request's `Origin` value back in
> `access-control-allow-origin` rather than literally returning `*` (the
> CORS spec forbids `*` together with credentials). This is correct
> behavior — every origin is still allowed.

---

## Endpoints

### `POST /api/v1/analysis/cascade`

Run a cascade analysis from a damage observation. Persists the result and
returns it with a generated `id`.

**Request body**

| Field            | Type             | Required | Description |
|------------------|------------------|----------|-------------|
| `observation_id` | string           | yes      | Caller-supplied id of the damage observation (from Eye 1). Echoed back as `triggered_by_observation_id`. |
| `asset_id`       | UUID             | yes      | The damaged asset (the cascade root). Must exist in the in-memory graph. |
| `asset_type`     | string           | no       | Optional asset type hint. Engine uses graph data if omitted. |
| `damage_level`   | enum             | yes      | One of `destroyed`, `major`, `minor`, `affected`, `unaffected`. |
| `confidence`     | float            | yes      | Detection confidence in `[0.0, 1.0]`. Multiplies into `priority_score`. |
| `timestamp`      | ISO-8601 datetime| yes      | When the damage was observed. |

**Example request**

```bash
curl -s -X POST http://localhost:8000/api/v1/analysis/cascade \
  -H "Content-Type: application/json" \
  -d '{
    "observation_id": "obs-demo-001",
    "asset_id": "e5baafab-8b21-4d1a-a396-d5fdc4c27343",
    "asset_type": "substation",
    "damage_level": "destroyed",
    "confidence": 0.95,
    "timestamp": "2026-04-25T18:00:00Z"
  }'
```

**Example response** (`200 OK`)

Captured against the loaded Puerto Rico HIFLD dataset for substation
`CENTRO MEDICO 1327-1359` (9 downstream hospitals).

```json
{
  "cascade_id": "cascade_20260425_180000_1c8d7e",
  "triggered_by_observation_id": "obs-demo-001",
  "root_asset_id": "e5baafab-8b21-4d1a-a396-d5fdc4c27343",
  "analysis_time": "2026-04-26T02:36:45.958655Z",
  "total_population_impacted": 9000,
  "critical_facilities_impacted": 9,
  "restoration_priority": 0,
  "priority_score": 12.5219,
  "hours_to_first_critical_failure": 72.0,
  "severity_multiplier": 1.0,
  "urgency_multiplier": 0.8333,
  "impact_summary": {
    "affected_assets": [
      {
        "asset_id": "02c958b2-03b5-4a60-bf16-faa7ac4d645a",
        "asset_type": "hospital",
        "criticality_tier": 1,
        "cascade_depth": 1,
        "dependency_type": "power",
        "failover_time_minutes": 4320,
        "time_to_failure_minutes": 4320,
        "population_served": 1000
      },
      {
        "asset_id": "7ce06447-8441-44a5-8a41-a5b05033f384",
        "asset_type": "hospital",
        "criticality_tier": 1,
        "cascade_depth": 1,
        "dependency_type": "power",
        "failover_time_minutes": 4320,
        "time_to_failure_minutes": 4320,
        "population_served": 1000
      },
      {
        "asset_id": "8cdabfbf-aca8-4150-a916-f56300302417",
        "asset_type": "hospital",
        "criticality_tier": 1,
        "cascade_depth": 1,
        "dependency_type": "power",
        "failover_time_minutes": 4320,
        "time_to_failure_minutes": 4320,
        "population_served": 1000
      },
      {
        "asset_id": "a2ceb0ae-26b7-44e3-b21e-336ee988a3c6",
        "asset_type": "hospital",
        "criticality_tier": 1,
        "cascade_depth": 1,
        "dependency_type": "power",
        "failover_time_minutes": 4320,
        "time_to_failure_minutes": 4320,
        "population_served": 1000
      },
      {
        "asset_id": "b47ff988-bb5d-41fb-97c4-ff1c5a02cadc",
        "asset_type": "hospital",
        "criticality_tier": 1,
        "cascade_depth": 1,
        "dependency_type": "power",
        "failover_time_minutes": 4320,
        "time_to_failure_minutes": 4320,
        "population_served": 1000
      },
      {
        "asset_id": "c505cba3-9101-4f95-a382-5da50e71f281",
        "asset_type": "hospital",
        "criticality_tier": 1,
        "cascade_depth": 1,
        "dependency_type": "power",
        "failover_time_minutes": 4320,
        "time_to_failure_minutes": 4320,
        "population_served": 1000
      },
      {
        "asset_id": "d09a2ae7-bc93-4595-ba73-59e986cd5ceb",
        "asset_type": "hospital",
        "criticality_tier": 1,
        "cascade_depth": 1,
        "dependency_type": "power",
        "failover_time_minutes": 4320,
        "time_to_failure_minutes": 4320,
        "population_served": 1000
      },
      {
        "asset_id": "ed72d525-26d0-4a7a-bf30-881f3e26cecf",
        "asset_type": "hospital",
        "criticality_tier": 1,
        "cascade_depth": 1,
        "dependency_type": "power",
        "failover_time_minutes": 4320,
        "time_to_failure_minutes": 4320,
        "population_served": 1000
      },
      {
        "asset_id": "eeec1473-c9a6-46a0-bce2-04a5c3cd1334",
        "asset_type": "hospital",
        "criticality_tier": 1,
        "cascade_depth": 1,
        "dependency_type": "power",
        "failover_time_minutes": 4320,
        "time_to_failure_minutes": 4320,
        "population_served": 1000
      }
    ],
    "by_asset_type": { "hospital": 9 },
    "by_cascade_depth": { "1": 9 }
  },
  "id": "d9ce0cd5-3ab3-4fea-94ec-48719fa7f875"
}
```

**Errors**

| Status | When | Body |
|--------|------|------|
| `400`  | `asset_id` is not a node in the dependency graph. | `{"detail":"Root asset 00000000-0000-0000-0000-000000000000 not in dependency graph"}` |
| `422`  | Request body fails validation (missing/invalid fields). | `{"detail":[{"type":"missing","loc":["body","asset_id"],"msg":"Field required","input":{"observation_id":"x"}}, ...]}` |

**Idempotency.** `cascade_id` is a deterministic hash of
`observation_id` + `asset_id` + `timestamp`. Re-POSTing the same payload
returns the existing record with `200` (same response body, same `id`,
no new row inserted). Safe to retry; safe to fire from a re-rendering
client.

---

### `GET /api/v1/analysis/cascade/{id}`

Fetch a previously computed cascade analysis by its persisted `id` (the UUID
returned from the POST, **not** the human-readable `cascade_id`).

**Path params**

| Param | Type | Description |
|-------|------|-------------|
| `id`  | UUID | The `id` field returned by `POST /api/v1/analysis/cascade`. |

**Example request**

```bash
curl -s http://localhost:8000/api/v1/analysis/cascade/d9ce0cd5-3ab3-4fea-94ec-48719fa7f875
```

**Example response** (`200 OK`)

Identical shape to the POST response above. The full record is rehydrated
from storage.

**Errors**

| Status | When | Body |
|--------|------|------|
| `404`  | No record with that `id`. | `{"detail":"Cascade analysis not found"}` |
| `422`  | `id` is not a valid UUID. | FastAPI validation error body. |

---

### `GET /api/v1/analysis/priorities`

Return cascade analyses ranked by `priority_score` descending, with ties
broken by `created_at` descending. Each row carries enough fields to render
a triage list without needing to refetch the full cascade.

**Query params**

| Param    | Type | Default | Bounds       | Description |
|----------|------|---------|--------------|-------------|
| `limit`  | int  | `10`    | `1..100`     | Page size. |
| `offset` | int  | `0`     | `>= 0`       | Number of rows to skip. |

**Example request**

```bash
curl -s "http://localhost:8000/api/v1/analysis/priorities?limit=10"
```

**Example response** (`200 OK`)

```json
[
  {
    "id": "d9ce0cd5-3ab3-4fea-94ec-48719fa7f875",
    "root_asset_id": "e5baafab-8b21-4d1a-a396-d5fdc4c27343",
    "priority_score": 12.5219,
    "hours_to_first_critical_failure": 72.0,
    "total_population_impacted": 9000,
    "critical_facilities_impacted": 9,
    "created_at": "2026-04-26T02:36:45.978561Z",
    "restoration_priority": 1
  },
  {
    "id": "0cb58a82-4f5c-4d75-8e2e-95b05f014300",
    "root_asset_id": "e5baafab-8b21-4d1a-a396-d5fdc4c27343",
    "priority_score": 12.5219,
    "hours_to_first_critical_failure": 72.0,
    "total_population_impacted": 9000,
    "critical_facilities_impacted": 9,
    "created_at": "2026-04-26T02:25:29.854772Z",
    "restoration_priority": 2
  },
  {
    "id": "01cfcb54-1d10-4199-88d7-193901b19b33",
    "root_asset_id": "3055f205-0dca-44c5-a24d-bb16b963020c",
    "priority_score": 9.0,
    "hours_to_first_critical_failure": 9.0,
    "total_population_impacted": 9000,
    "critical_facilities_impacted": 9,
    "created_at": "2026-04-26T02:25:10.086763Z",
    "restoration_priority": 3
  },
  {
    "id": "0fd7fbbb-c80a-4b96-b1b6-fd8f903984e1",
    "root_asset_id": "329c403b-1102-4325-bbb3-366d6dbf7868",
    "priority_score": 5.0,
    "hours_to_first_critical_failure": 5.0,
    "total_population_impacted": 5000,
    "critical_facilities_impacted": 5,
    "created_at": "2026-04-26T02:25:10.086763Z",
    "restoration_priority": 4
  },
  {
    "id": "ff2942b0-41a8-4a44-892d-6dddb8ad287c",
    "root_asset_id": "72a60590-70d3-4664-b991-7eaf93c5ee0d",
    "priority_score": 1.0,
    "hours_to_first_critical_failure": 1.0,
    "total_population_impacted": 1000,
    "critical_facilities_impacted": 1,
    "created_at": "2026-04-26T02:25:10.086763Z",
    "restoration_priority": 5
  }
]
```

`restoration_priority` is the 1-based rank within the response (it reflects
position, not a stored value — paginating with `offset` continues the
ranking).

**Errors**

| Status | When | Body |
|--------|------|------|
| `422`  | `limit`/`offset` outside bounds. | FastAPI validation error body. |

---

## Data shapes

### `CascadeAnalysis` (POST and GET-by-id response body)

| Field                              | Type                  | Description |
|------------------------------------|-----------------------|-------------|
| `id`                               | UUID                  | Storage id; pass to `GET /cascade/{id}`. |
| `cascade_id`                       | string                | Human-readable id, e.g. `cascade_20260425_180000_1c8d7e`. Stable hash of inputs. |
| `triggered_by_observation_id`      | string                | Echo of the request's `observation_id`. |
| `root_asset_id`                    | UUID                  | The damaged asset (cascade root). |
| `analysis_time`                    | ISO-8601 datetime     | When the engine ran (UTC). |
| `total_population_impacted`        | int                   | Sum of `population_served` across all affected assets. |
| `critical_facilities_impacted`     | int                   | Count of affected assets with `criticality_tier == 1`. |
| `restoration_priority`             | int                   | Always `0` here (population rank is computed by `/priorities`). |
| `priority_score`                   | float                 | Composite ranking score, higher = more urgent. See formula below. |
| `hours_to_first_critical_failure`  | float (hours)         | Hours until the first tier-1 asset fails. Falls back to overall min if no tier-1 affected. |
| `severity_multiplier`              | float                 | Driven by `damage_level`: `destroyed=1.0`, `major=0.7`, `minor=0.4`, `affected=0.2`, `unaffected=0.0`. |
| `urgency_multiplier`               | float                 | `60 / hours_to_first_critical_failure`, clamped at the urgency floor. |
| `impact_summary.affected_assets`   | `AffectedAsset[]`     | One entry per downstream failure (see below). |
| `impact_summary.by_asset_type`     | `dict[str, int]`      | Counts of affected assets grouped by `asset_type`. |
| `impact_summary.by_cascade_depth`  | `dict[str, int]`      | Counts of affected assets grouped by hop distance from root. |

### `AffectedAsset`

| Field                     | Type    | Description |
|---------------------------|---------|-------------|
| `asset_id`                | UUID    | The downstream asset that fails. |
| `asset_type`              | string  | e.g. `hospital`, `cell_tower`, `water_treatment`. |
| `criticality_tier`        | int     | `1` (most critical) … `4` (least). |
| `cascade_depth`           | int     | Hops from the root asset (root itself is not included). |
| `dependency_type`         | string  | The edge that broke: `power`, `water`, `communications`, etc. |
| `failover_time_minutes`   | int     | Minutes the asset can survive without the upstream provider. From edge data, with hardcoded fallbacks. |
| `time_to_failure_minutes` | int     | Cumulative minutes from the observation `timestamp` to this asset's failure. |
| `population_served`       | int     | People served by this asset. See population fallback note below. |

### `CascadePrioritySummary` (`/priorities` response items)

| Field                              | Type              | Description |
|------------------------------------|-------------------|-------------|
| `id`                               | UUID              | Storage id; use with `GET /cascade/{id}` to fetch full detail. |
| `root_asset_id`                    | UUID              | The damaged asset for that analysis. |
| `priority_score`                   | float             | Same field as in `CascadeAnalysis`. |
| `hours_to_first_critical_failure`  | float (hours)     | Same. |
| `total_population_impacted`        | int               | Same. |
| `critical_facilities_impacted`     | int               | Same. |
| `created_at`                       | ISO-8601 datetime | When the analysis row was persisted. |
| `restoration_priority`             | int               | 1-based rank within the response page. |

---

## Notes for consumers

**`priority_score` formula.** In plain English: score grows with the
log of the total population impacted, weighted by how critical the most
critical affected asset is, scaled by detection confidence, scaled by how
severe the damage is, and multiplied by an urgency factor that goes up the
sooner the first critical asset fails. So a destroyed substation feeding
many tier-1 hospitals that fail within hours scores far higher than a
minor blip on a low-criticality node hours away from any failure.

**`population_served = 1000` fallback.** Many HIFLD asset types
(hospitals, cell towers, …) have no real population figure in the source
data. When a downstream asset has `0` or `None`, the engine substitutes
`1000` so the cascade still produces a non-zero `priority_score` and the
ranking is meaningful for the demo. This is why every hospital in the
example response shows exactly `1000`. ⚠ subject to change once the loader
populates census-derived values.

**Root not in graph.** If `asset_id` is a valid UUID but isn't a node
in the in-memory dependency graph (deleted, never loaded, or graph not
rebuilt since a write), the POST returns `400` with
`"Root asset <uuid> not in dependency graph"`. After bulk asset edits, an
admin should call the rebuild endpoint to refresh the in-memory graph.

**Empty cascade.** If the root has no out-edges (no downstream
dependents), the response is well-formed but
`impact_summary.affected_assets` is `[]`, both grouping dicts are empty,
`total_population_impacted` is `0`, and `priority_score` is `0.0`. This is
not an error.

---

## Walkthrough — hurricane damages a substation

A hurricane just took out substation `e5baafab-…` (the
`CENTRO MEDICO 1327-1359` substation, tier-1, feeds 9 hospitals).

**1. Eye 1 emits a damage observation. The pipeline POSTs the cascade.**

```bash
curl -s -X POST http://localhost:8000/api/v1/analysis/cascade \
  -H "Content-Type: application/json" \
  -d '{
    "observation_id": "obs-demo-001",
    "asset_id": "e5baafab-8b21-4d1a-a396-d5fdc4c27343",
    "asset_type": "substation",
    "damage_level": "destroyed",
    "confidence": 0.95,
    "timestamp": "2026-04-25T18:00:00Z"
  }'
```

You get back the `CascadeAnalysis` shown above:
`priority_score = 12.5219`, 9 hospitals affected, 9 000 people impacted,
first critical failure in 72 hours (hospital generators carry 72h of fuel).
Capture the `id` field (`d9ce0cd5-…`).

**2. The website's triage panel hits `/priorities` to refresh its list.**

```bash
curl -s "http://localhost:8000/api/v1/analysis/priorities?limit=10"
```

The new analysis appears at rank 1. The list is sorted by
`priority_score` desc, so the most painful scenarios float to the top.

**3. The user clicks a row in the dashboard. The website fetches the full
record by id.**

```bash
curl -s http://localhost:8000/api/v1/analysis/cascade/d9ce0cd5-3ab3-4fea-94ec-48719fa7f875
```

Same response shape as step 1 — the dashboard renders the affected
hospitals, time-to-failure clock, and impact summary.

---

## Supporting endpoints

These exist on the same service. They are **not** the cascade contract,
but consumers may need them. See `/docs` for full schemas.

### Health

| Method | Path      | Description |
|--------|-----------|-------------|
| GET    | `/health` | Liveness probe. Returns `{"status":"ok"}`. |

### Assets — `/api/v1/assets`

Used to look up an asset's name, type, location, or criticality tier from
a UUID returned by a cascade response.

| Method | Path                              | Description |
|--------|-----------------------------------|-------------|
| GET    | `/api/v1/assets`                  | List assets as GeoJSON FeatureCollection. Filters: `asset_type`, `criticality_tier`, `bbox`, `limit`, `offset`. |
| GET    | `/api/v1/assets/{id}`             | Single asset as GeoJSON Feature. |
| GET    | `/api/v1/assets/{id}/dependencies`| Direct dependency edges for an asset. |
| POST   | `/api/v1/assets`                  | Create asset. |
| PATCH  | `/api/v1/assets/{id}`             | Update asset fields. |
| DELETE | `/api/v1/assets/{id}`             | Delete asset (cascades to dependency rows). |

### Dependencies — `/api/v1/dependencies`

| Method | Path                            | Description |
|--------|---------------------------------|-------------|
| GET    | `/api/v1/dependencies`          | List dependency edges with filters. |
| POST   | `/api/v1/dependencies`          | Create a manual dependency edge. |
| DELETE | `/api/v1/dependencies/{id}`     | Delete a dependency edge. |

### Graph — `/api/v1/graph`

| Method | Path                            | Description |
|--------|---------------------------------|-------------|
| GET    | `/api/v1/graph/stats`           | Graph summary (node/edge counts, type histograms). |
| GET    | `/api/v1/graph/neighbors/{id}`  | N-hop neighborhood subgraph around an asset. |
| POST   | `/api/v1/graph/validate`        | Detect orphans, cycles, disconnected components. |

### Loader — `/api/v1/loader`

| Method | Path                  | Description |
|--------|-----------------------|-------------|
| POST   | `/api/v1/loader/area` | Load HIFLD assets and infer dependencies for a named region. |
