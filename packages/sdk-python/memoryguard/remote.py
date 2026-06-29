# SPDX-License-Identifier: Apache-2.0
"""Remote SDK backend — a thin REST client over the MemoryGuard API.

:class:`RemoteBackend` issues HTTP requests to the FastAPI routes
(``/v1/memories``, ``/v1/query``, ``/v1/ingest/path``,
``/v1/memories/{id}``, ``/v1/memories/{id}/contradictions``) and adapts the
JSON responses onto the SDK's uniform result types, so it returns the same
conceptual results as the local engine (Requirement 11.4). An optional bearer
``token`` is sent as an ``Authorization`` header (Requirement 11.2).

Transport
---------
``httpx`` is used when available (declared as a dependency). When ``httpx`` is
not importable the client transparently falls back to the standard-library
``urllib``. Tests may inject a ready-made ``httpx`` client (e.g. one built on
``httpx.MockTransport``) via the ``client`` argument to exercise the request
shaping without a live server.

This module is part of the Apache-2.0 OSS SDK.
"""

from __future__ import annotations

import json as _json
from datetime import datetime
from typing import Any, Optional, Union

from memoryguard_core import Scope, Sensitivity, SourceType

from .models import Contradiction, Memory, QueryResult

__all__ = ["RemoteBackend", "RemoteError"]

ScopeLike = Union[Scope, str]
SourceTypeLike = Union[SourceType, str]
SensitivityLike = Union[Sensitivity, str]


class RemoteError(RuntimeError):
    """Raised when the REST API returns a non-success status code."""

    def __init__(self, status_code: int, message: str) -> None:
        super().__init__(f"HTTP {status_code}: {message}")
        self.status_code = status_code
        self.message = message


def _enum_value(value: Any) -> Any:
    return getattr(value, "value", value)


class RemoteBackend:
    """REST client backend for ``MemoryGuard.remote``.

    Args:
        base_url: base URL of the MemoryGuard REST API (e.g.
            ``"http://localhost:8000"``).
        token: optional bearer token sent as ``Authorization: Bearer <token>``.
        client: optional pre-built ``httpx.Client`` (used by tests to inject a
            mock transport). When provided it is used as-is.
        timeout: request timeout in seconds (httpx transport only).
    """

    mode = "remote"

    def __init__(
        self,
        base_url: str,
        token: Optional[str] = None,
        *,
        client: Any = None,
        timeout: float = 30.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.token = token
        self._timeout = timeout
        self._transport = self._build_transport(client)

    # -- transport ---------------------------------------------------------

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        return headers

    def _build_transport(self, client: Any) -> "_Transport":
        if client is not None:
            return _HttpxTransport(client, self._headers, owns_client=False)
        try:
            import httpx  # noqa: F401
        except Exception:
            return _UrllibTransport(self.base_url, self._headers, self._timeout)

        import httpx

        http_client = httpx.Client(base_url=self.base_url, timeout=self._timeout)
        return _HttpxTransport(http_client, self._headers, owns_client=True)

    def close(self) -> None:
        """Close the underlying HTTP client (if this backend owns one)."""

        self._transport.close()

    def __enter__(self) -> "RemoteBackend":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # -- write -------------------------------------------------------------

    def add(
        self,
        content: str,
        source_type: SourceTypeLike,
        source_ref: str,
        scope: ScopeLike,
        scope_ref: Optional[str] = None,
        sensitivity: SensitivityLike = Sensitivity.INTERNAL,
        expires_at: Optional[datetime] = None,
        tags: Optional[list[str]] = None,
    ) -> Memory:
        body = {
            "content": content,
            "source_type": _enum_value(source_type),
            "source_ref": source_ref,
            "scope": _enum_value(scope),
            "scope_ref": scope_ref,
            "sensitivity": _enum_value(sensitivity),
            "tags": list(tags) if tags is not None else [],
        }
        if expires_at is not None:
            body["expires_at"] = (
                expires_at.isoformat()
                if isinstance(expires_at, datetime)
                else expires_at
            )
        data = self._request("POST", "/v1/memories", json=body)
        return Memory.from_json(_unwrap(data, "memory"))

    def get(self, memory_id: str) -> Optional[Memory]:
        data = self._request(
            "GET", f"/v1/memories/{memory_id}", allow_404=True
        )
        if data is None:
            return None
        return Memory.from_json(_unwrap(data, "memory"))

    def query(
        self,
        text: str,
        scope: Optional[ScopeLike] = None,
        scope_ref: Optional[str] = None,
        min_trust: float = 0.0,
        limit: int = 10,
        max_sensitivity: Optional[SensitivityLike] = None,
    ) -> list[QueryResult]:
        body: dict[str, Any] = {
            "text": text,
            "scope": _enum_value(scope) if scope is not None else None,
            "scope_ref": scope_ref,
            "min_trust": min_trust,
            "limit": limit,
        }
        if max_sensitivity is not None:
            body["max_sensitivity"] = _enum_value(max_sensitivity)
        data = self._request("POST", "/v1/query", json=body)
        items = _unwrap_list(data, "results")
        return [QueryResult.from_json(item) for item in items]

    def ingest_path(
        self,
        path: str,
        scope: ScopeLike,
        scope_ref: Optional[str] = None,
    ) -> list[Memory]:
        body = {
            "path": path,
            "scope": _enum_value(scope),
            "scope_ref": scope_ref,
        }
        data = self._request("POST", "/v1/ingest/path", json=body)
        items = _unwrap_list(data, "memories")
        return [Memory.from_json(item) for item in items]

    def correct(self, memory_id: str, new_content: str) -> Memory:
        data = self._request(
            "PATCH",
            f"/v1/memories/{memory_id}",
            json={"content": new_content},
        )
        return Memory.from_json(_unwrap(data, "memory"))

    def delete(self, memory_id: str) -> None:
        self._request("DELETE", f"/v1/memories/{memory_id}", expect_body=False)

    def contradictions(self, memory_id: str) -> list[Contradiction]:
        data = self._request("GET", f"/v1/memories/{memory_id}/contradictions")
        items = _unwrap_list(data, "contradictions")
        return [Contradiction.from_dict(item) for item in items]

    # -- request plumbing --------------------------------------------------

    def _request(
        self,
        method: str,
        path: str,
        *,
        json: Optional[dict] = None,
        allow_404: bool = False,
        expect_body: bool = True,
    ) -> Any:
        status, payload = self._transport.request(method, path, json)
        if status == 404 and allow_404:
            return None
        if status < 200 or status >= 300:
            raise RemoteError(status, _error_message(payload))
        if not expect_body:
            return None
        return payload


# ---------------------------------------------------------------------------
# Response helpers
# ---------------------------------------------------------------------------


def _unwrap(data: Any, key: str) -> dict:
    """Return ``data[key]`` when wrapped, else ``data`` itself."""

    if isinstance(data, dict) and isinstance(data.get(key), dict):
        return data[key]
    return data if isinstance(data, dict) else {}


def _unwrap_list(data: Any, key: str) -> list:
    """Return a list from either a bare list or a ``{key: [...]}`` envelope."""

    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        value = data.get(key)
        if isinstance(value, list):
            return value
        # Common alternatives.
        for alt in ("results", "items", "data"):
            if isinstance(data.get(alt), list):
                return data[alt]
    return []


def _error_message(payload: Any) -> str:
    if isinstance(payload, dict):
        for key in ("error", "detail", "message"):
            if key in payload:
                return str(payload[key])
        return _json.dumps(payload)
    return str(payload)


# ---------------------------------------------------------------------------
# Transports
# ---------------------------------------------------------------------------


class _Transport:
    """Minimal transport contract: ``request`` returns ``(status, payload)``."""

    def request(
        self, method: str, path: str, json: Optional[dict]
    ) -> tuple[int, Any]:  # pragma: no cover - interface
        raise NotImplementedError

    def close(self) -> None:  # pragma: no cover - interface
        ...


class _HttpxTransport(_Transport):
    """Transport backed by an ``httpx.Client`` (real or mock-transport based)."""

    def __init__(self, client: Any, headers_fn: Any, *, owns_client: bool) -> None:
        self._client = client
        self._headers_fn = headers_fn
        self._owns_client = owns_client

    def request(
        self, method: str, path: str, json: Optional[dict]
    ) -> tuple[int, Any]:
        response = self._client.request(
            method, path, json=json, headers=self._headers_fn()
        )
        try:
            payload = response.json()
        except Exception:
            payload = response.text
        return response.status_code, payload

    def close(self) -> None:
        if self._owns_client:
            self._client.close()


class _UrllibTransport(_Transport):
    """Standard-library fallback transport (no third-party dependency)."""

    def __init__(self, base_url: str, headers_fn: Any, timeout: float) -> None:
        self._base_url = base_url
        self._headers_fn = headers_fn
        self._timeout = timeout

    def request(
        self, method: str, path: str, json: Optional[dict]
    ) -> tuple[int, Any]:
        import urllib.error
        import urllib.request

        url = self._base_url + path
        body = None if json is None else _json.dumps(json).encode("utf-8")
        req = urllib.request.Request(url=url, data=body, method=method)
        for key, value in self._headers_fn().items():
            req.add_header(key, value)
        try:
            with urllib.request.urlopen(req, timeout=self._timeout) as resp:
                raw = resp.read().decode("utf-8") if resp.length != 0 else ""
                return resp.status, _safe_json(raw)
        except urllib.error.HTTPError as exc:  # non-2xx
            raw = exc.read().decode("utf-8") if exc.fp is not None else ""
            return exc.code, _safe_json(raw)

    def close(self) -> None:
        ...


def _safe_json(raw: str) -> Any:
    if not raw:
        return None
    try:
        return _json.loads(raw)
    except Exception:
        return raw
