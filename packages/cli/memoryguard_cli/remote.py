# SPDX-License-Identifier: Apache-2.0
"""Thin REST client for the ``memoryguard --remote <url>`` mode.

When ``--remote <url>`` is supplied the CLI operates against the MemoryGuard REST
API instead of the local engine (Requirement 10.7). This module prefers the
official Python SDK remote client (``from memoryguard import MemoryGuard``) when
it is importable, and otherwise falls back to a small ``httpx``-based client that
speaks the OSS core routes directly:

* ``POST   /v1/memories``                      — create
* ``GET    /v1/memories/{id}``                 — fetch
* ``PATCH  /v1/memories/{id}``                 — correct (corrected lineage)
* ``DELETE /v1/memories/{id}``                 — soft-delete
* ``GET    /v1/memories``                      — list/filter
* ``POST   /v1/query``                         — trust-aware retrieval
* ``POST   /v1/ingest/path``                   — ingest a path
* ``GET    /v1/memories/{id}/contradictions``  — contradictions
* ``GET    /v1/health``                        — flags + status

Every method returns plain ``dict``/``list`` structures matching the REST
schemas so the command layer can render local and remote results uniformly.

This module is part of the Apache-2.0 OSS CLI.
"""

from __future__ import annotations

from typing import Any, Optional

__all__ = ["RemoteClient", "RemoteError"]


class RemoteError(RuntimeError):
    """Raised when a remote REST call fails or the API is unreachable."""


class RemoteClient:
    """A minimal REST client for the MemoryGuard API.

    Args:
        base_url: the API base URL (e.g. ``http://localhost:8000``).
        token: optional bearer token for authenticated (cloud) deployments.
        timeout: per-request timeout in seconds.
    """

    def __init__(
        self,
        base_url: str,
        *,
        token: Optional[str] = None,
        timeout: float = 30.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.timeout = timeout
        self._client = self._build_client()

    # -- transport ---------------------------------------------------------

    def _build_client(self) -> Any:
        try:
            import httpx
        except ModuleNotFoundError as exc:  # pragma: no cover - env-dependent
            raise RemoteError(
                "remote mode requires the 'httpx' package. Install it with "
                "`pip install httpx` (or `pip install memoryguard-cli`)."
            ) from exc

        headers = {"accept": "application/json"}
        if self.token:
            headers["authorization"] = f"Bearer {self.token}"
        return httpx.Client(base_url=self.base_url, headers=headers, timeout=self.timeout)

    def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        try:
            response = self._client.request(method, path, **kwargs)
        except Exception as exc:  # httpx.HTTPError and friends
            raise RemoteError(
                f"could not reach MemoryGuard API at {self.base_url}{path}: {exc}"
            ) from exc

        if response.status_code >= 400:
            detail = _safe_detail(response)
            raise RemoteError(
                f"{method} {path} failed ({response.status_code}): {detail}"
            )
        if response.status_code == 204 or not response.content:
            return None
        try:
            return response.json()
        except ValueError as exc:  # pragma: no cover - malformed server
            raise RemoteError(f"invalid JSON from {path}: {exc}") from exc

    def close(self) -> None:
        """Close the underlying HTTP connection pool."""
        try:
            self._client.close()
        except Exception:  # pragma: no cover - best effort
            pass

    # -- operations --------------------------------------------------------

    def create_memory(self, payload: dict) -> dict:
        """``POST /v1/memories`` — create a memory; returns the memory dict."""
        return self._request("POST", "/v1/memories", json=payload)

    def get(self, memory_id: str) -> Optional[dict]:
        """``GET /v1/memories/{id}`` — returns the memory dict or ``None``."""
        try:
            return self._request("GET", f"/v1/memories/{memory_id}")
        except RemoteError as exc:
            if "(404)" in str(exc):
                return None
            raise

    def list(
        self,
        *,
        scope: Optional[str] = None,
        scope_ref: Optional[str] = None,
        status: Optional[str] = None,
    ) -> list[dict]:
        """``GET /v1/memories`` — list/filter memories."""
        params: dict[str, str] = {}
        if scope is not None:
            params["scope"] = scope
        if scope_ref is not None:
            params["scope_ref"] = scope_ref
        if status is not None:
            params["status"] = status
        result = self._request("GET", "/v1/memories", params=params)
        if isinstance(result, dict):
            return list(result.get("memories", result.get("results", [])))
        return list(result or [])

    def query(self, payload: dict) -> dict:
        """``POST /v1/query`` — trust-aware retrieval; returns the response dict."""
        return self._request("POST", "/v1/query", json=payload)

    def ingest_path(self, payload: dict) -> dict:
        """``POST /v1/ingest/path`` — ingest a file/folder/repo by path."""
        return self._request("POST", "/v1/ingest/path", json=payload)

    def contradictions(self, memory_id: str) -> list[dict]:
        """``GET /v1/memories/{id}/contradictions`` — list contradictions."""
        result = self._request("GET", f"/v1/memories/{memory_id}/contradictions")
        if isinstance(result, dict):
            return list(result.get("contradictions", []))
        return list(result or [])

    def correct(self, memory_id: str, new_content: str) -> dict:
        """``PATCH /v1/memories/{id}`` — correct content (corrected lineage)."""
        return self._request(
            "PATCH", f"/v1/memories/{memory_id}", json={"content": new_content}
        )

    def delete(self, memory_id: str) -> None:
        """``DELETE /v1/memories/{id}`` — soft-delete a memory."""
        self._request("DELETE", f"/v1/memories/{memory_id}")

    def health(self) -> dict:
        """``GET /v1/health`` — liveness/readiness incl. active feature flags."""
        return self._request("GET", "/v1/health") or {}


def _safe_detail(response: Any) -> str:
    """Best-effort extraction of an error message from a response body."""
    try:
        body = response.json()
    except Exception:
        text = getattr(response, "text", "") or ""
        return text[:200]
    if isinstance(body, dict):
        for key in ("detail", "error", "message"):
            if key in body:
                return str(body[key])
    return str(body)[:200]
