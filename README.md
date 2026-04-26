# TriNetra

TriNetra is a disaster response intelligence system. It ingests data from three independent sensing layers ("eyes"), aggregates them through a cascading failure engine, and delivers outputs through two channels: a web dashboard and a conversational agent on Agentverse.

---

### Agentverse Agent
TriNetra is a multi-agent system where a main cascade orchestrator coordinates with three specialist agents, Eye 1 (asset detection), Eye 2 (disaster heuristics), and Eye 3 (cascade orchestration), all registered as uAgents on Fetch.ai's Agentverse. When a user query arrives via ASI:One, the orchestrator dispatches coordination messages to all three Eye agents and assembles a unified cascade context from their responses. That context is then used to enrich the cascade engine's analysis, which traces dependent failures across 617 real infrastructure assets. The Eye agents communicate with the orchestrator, ensuring a consistent contract across the network. The result is returned to the user as natural-language prose, with a coordination banner confirming the activation of all our agents. 

Chat session link: https://asi1.ai/shared-chat/a8776059-8258-4237-ad38-6faa1722e9bb

Links to our agents:

@trinetraai: https://agentverse.ai/agents/details/agent1q0s8yaewr3tvznfrzv8xe0t95aqnrfrv3jjfu3hjj8mqesx6t94qu4mxhk7/profile

@trinetra-eye1: https://agentverse.ai/agents/details/agent1qwngtn9jy6ktv4ltf4k0j2asm69tvjccwnrpsn7dy3aup7thxw7d5vtj5xs/profile

@trinetra-eye2: https://agentverse.ai/agents/details/agent1qdd3hdhlvcxy665urxa6kqzyga8jre7jc3l05v7qtedmx69v3ueg26s2pr3/profile

@trinetra-eye3: https://agentverse.ai/agents/details/agent1qwkpaz8l5t4fxlq9uncv4fk5gtnmgk87neuxdwkzygu4cze327kjjzl6cr6/profile

---

## Architecture

```
Eye 1 (Satellite)   Eye 2 (Infrastructure Graph)   Eye 3 (Social Media)
        \                        |                        /
         \                       |                       /
          +---------> Cascading Engine <---------------+
                            |
               +------------+------------+
               |                         |
            UI / Dashboard           Agentverse Agent
```

---

## The Three Eyes

### Eye 1 — Satellite Damage Assessment
Processes pre- and post-disaster satellite imagery (NAIP, Sentinel-2) using a PyTorch model to detect and classify infrastructure damage. Outputs damage levels per asset tile.

### Eye 2 — Infrastructure Dependency Graph
Maintains a geo-located graph of critical infrastructure assets (substations, hospitals, water plants, cell towers, etc.) stored in PostgreSQL/PostGIS. Knows which assets depend on which, and feeds this structure into the cascading engine.

### Eye 3 — Social Media Signals
Pulls real-time posts from Twitter/X and runs sentiment analysis to surface ground-truth disaster signals. Acts as a live verification layer alongside satellite and graph data.

---

## The Cascading Engine

The cascading engine sits at the center of TriNetra. It accepts a damage observation from any of the three eyes and walks the infrastructure dependency graph to determine downstream failures.

For a given damaged asset it:
- Propagates failure up to 5 hops downstream depending on damage severity
- Computes estimated time-to-failure for each affected asset
- Scores each affected asset by population impacted, criticality tier, and urgency
- Returns a ranked list of affected assets and recommended response priorities

---

## Outputs

### UI / Dashboard
The Eye 2 FastAPI service exposes REST endpoints consumed by a web dashboard. Operators can query cascade analyses, browse asset status, and view prioritized response lists in real time.

### Agentverse Agent
A Fetch.ai uAgent running on Agentverse provides a conversational interface to the same cascade engine. Users query it in natural language via ASI:One Chat Protocol. An intent classifier routes requests, calls the Eye 2 API, and returns plain-language summaries of cascade impact and priorities.

---

## Running the System

Each component runs as an independent service:

| Component | Directory | How to run |
|-----------|-----------|------------|
| Satellite eye | `eye1/` | `uvicorn app.main:app` |
| Graph + cascade engine | `eye2/` | `uvicorn app.main:app` |
| Social media eye | `eye3/` | `uvicorn app.main:app` |
| Pipeline orchestrator | `pipeline/` | `uvicorn main:app` |
| Agentverse agent | `trinetra-agent/` | `python agent.py` |

Copy `.env.example` to `.env` in each directory and fill in API keys before starting.
