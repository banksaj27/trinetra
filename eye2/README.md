# TriNetra AI — Infrastructure Dependency Graph

Dependency graph backend for the TriNetra disaster impact intelligence platform.
Loads real infrastructure data (substations, hospitals, water plants, cell towers)
from HIFLD open datasets, stores them as geo-located assets in PostgreSQL + PostGIS,
infers dependency edges through spatial relationships, and exposes the graph
through a FastAPI REST API.

## Quick Start

### 1. Start PostGIS

```bash
cd trinetra
docker compose up -d
```

### 2. Install Python dependencies

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 3. Configure environment

```bash
cp .env.example .env
# Edit .env if your database connection differs
```

Census population loading works without an API key but may hit rate limits.
Register for a free key at <https://api.census.gov/data/key_signup.html> and set
`CENSUS_API_KEY` in `.env`.

### 4. Run database migrations

```bash
alembic upgrade head
```

### 5. Load data

**Demo mode** (synthetic data, no internet required):

```bash
python -m app.data_loader.load_all --demo
```

**Real HIFLD data** (downloads from ArcGIS REST APIs):

```bash
python -m app.data_loader.load_all --region PR
```

Add `--clear-existing` to wipe and reload.

### 6. Start the API server

```bash
uvicorn app.main:app --reload
```

API docs available at <http://localhost:8000/docs>.

## API Overview

### Assets — `/api/v1/assets`

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/v1/assets` | List assets (filter by `asset_type`, `criticality_tier`, `bbox`) |
| GET | `/api/v1/assets/{id}` | Single asset as GeoJSON Feature |
| POST | `/api/v1/assets` | Create asset |
| PATCH | `/api/v1/assets/{id}` | Update asset fields |
| DELETE | `/api/v1/assets/{id}` | Delete asset (cascades to dependencies) |
| GET | `/api/v1/assets/{id}/dependencies` | Dependencies for an asset |

### Dependencies — `/api/v1/dependencies`

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/v1/dependencies` | List dependencies with filters |
| POST | `/api/v1/dependencies` | Create manual dependency edge |
| DELETE | `/api/v1/dependencies/{id}` | Delete dependency edge |

### Graph — `/api/v1/graph`

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/v1/graph/stats` | Graph summary statistics |
| GET | `/api/v1/graph/neighbors/{id}` | N-hop neighborhood subgraph |
| POST | `/api/v1/graph/validate` | Detect orphans, cycles, disconnected components |

## Asset Types

`substation`, `hospital`, `water_treatment`, `cell_tower`, `shelter`,
`fire_station`, `police_station`, `school`, `wastewater`, `fuel_depot`,
`data_center`, `ems_station`

## Dependency Types

`power`, `water`, `communications`, `road_access`, `fuel`

## Project Structure

```
trinetra/
├── alembic/               # Database migrations
├── app/
│   ├── main.py            # FastAPI entry point
│   ├── config.py          # Settings (pydantic-settings)
│   ├── database.py        # Async SQLAlchemy engine
│   ├── models/            # SQLAlchemy ORM models
│   ├── schemas/           # Pydantic v2 request/response schemas
│   ├── api/               # FastAPI route handlers
│   ├── services/          # Graph builder, spatial inference
│   └── data_loader/       # HIFLD loader, Census loader, demo generator
├── docker-compose.yml     # PostGIS 16 + PostGIS 3.4
├── requirements.txt
└── README.md
```
