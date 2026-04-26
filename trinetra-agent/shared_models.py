"""Shared uAgent message models for TriNetra multi-agent system."""
from typing import Optional
from uagents import Model


class Eye1Query(Model):
    query_type: str  # "ping" | "asset_lookup"
    asset_name: Optional[str] = None


class Eye1Response(Model):
    ok: bool
    source: str
    message: str
    data: Optional[dict] = None


class Eye2Query(Model):
    query_type: str  # "ping" | "heuristic_lookup"
    disaster_type: Optional[str] = None


class Eye2Response(Model):
    ok: bool
    source: str
    message: str
    data: Optional[dict] = None


class Eye3Query(Model):
    query_type: str  # "ping"
    payload: Optional[str] = None


class Eye3Response(Model):
    ok: bool
    source: str
    message: str
    data: Optional[dict] = None


class CascadeContext(Model):
    """Aggregated specialist agent state used to enrich cascade analysis."""
    asset_index_size: Optional[int] = None
    disaster_profile: Optional[dict] = None
    orchestration_status: Optional[str] = None
    contributing_agents: list = []
