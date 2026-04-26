"""Async httpx wrapper around the TriNetra cascade FastAPI."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

import httpx

import config


class TPIError(Exception):
    """Raised on network errors or non-2xx responses from the TriNetra API."""


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _unwrap_list(data: Any, *keys: str) -> list[dict[str, Any]]:
    """Accept either a bare list or {key: [...]} envelopes."""
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        for key in keys:
            value = data.get(key)
            if isinstance(value, list):
                return value
    return []


class TriNetraClient:
    """Shared async client for the TriNetra cascade FastAPI."""

    def __init__(
        self,
        base_url: str | None = None,
        timeout: float | None = None,
    ) -> None:
        self._base_url = (base_url or config.TRINETRA_API_BASE).rstrip("/")
        self._timeout = timeout if timeout is not None else config.TRINETRA_API_TIMEOUT
        self._client: httpx.AsyncClient | None = None

    def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                base_url=self._base_url,
                timeout=self._timeout,
            )
        return self._client

    async def close(self) -> None:
        if self._client is not None and not self._client.is_closed:
            await self._client.aclose()
        self._client = None

    async def __aenter__(self) -> "TriNetraClient":
        return self

    async def __aexit__(self, *_exc: Any) -> None:
        await self.close()

    async def _request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        client = self._get_client()
        try:
            response = await client.request(method, path, **kwargs)
        except httpx.HTTPError as exc:
            raise TPIError(f"Network error calling {method} {path}: {exc}") from exc
        return response

    @staticmethod
    def _raise_for_status(response: httpx.Response, path: str) -> None:
        if response.is_success:
            return
        body = response.text[:300]
        raise TPIError(
            f"{response.request.method} {path} returned "
            f"{response.status_code}: {body}"
        )

    async def get_priorities(self, limit: int = 10) -> list[dict[str, Any]]:
        path = "/api/v1/analysis/priorities"
        response = await self._request("GET", path, params={"limit": limit})
        self._raise_for_status(response, path)
        return _unwrap_list(response.json(), "priorities", "items", "results")

    async def get_cascade(self, cascade_id: str) -> dict[str, Any]:
        path = f"/api/v1/analysis/cascade/{cascade_id}"
        response = await self._request("GET", path)
        self._raise_for_status(response, path)
        data = response.json()
        if not isinstance(data, dict):
            raise TPIError(f"GET {path} returned non-object payload")
        return data

    async def post_cascade(
        self,
        asset_id: str,
        *,
        damage_level: str = "destroyed",
        confidence: float = 0.9,
        observation_id: str | None = None,
        timestamp: str | None = None,
    ) -> dict[str, Any]:
        path = "/api/v1/analysis/cascade"
        payload = {
            "observation_id": observation_id or f"agent-{uuid.uuid4().hex[:8]}",
            "asset_id": asset_id,
            "damage_level": damage_level,
            "confidence": confidence,
            "timestamp": timestamp or _utc_now_iso(),
        }
        response = await self._request("POST", path, json=payload)
        self._raise_for_status(response, path)
        data = response.json()
        if not isinstance(data, dict):
            raise TPIError(f"POST {path} returned non-object payload")
        return data

    async def search_assets(self, query: str, limit: int = 5) -> list[dict[str, Any]]:
        path = "/api/v1/assets/search"
        response = await self._request(
            "GET", path, params={"q": query, "limit": limit}
        )
        if response.status_code == 404:
            return []
        self._raise_for_status(response, path)
        return _unwrap_list(response.json(), "assets", "results", "items")
