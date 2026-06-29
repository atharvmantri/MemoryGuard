# SPDX-License-Identifier: Apache-2.0
"""The :class:`MemoryGuard` client — one surface over local or remote backends.

``MemoryGuard`` is the single entry point of the Python SDK. It is constructed
through one of two classmethods and then exposes the same conceptual operations
regardless of where they run (Requirement 11.4):

* :meth:`MemoryGuard.local` — runs directly against the in-process core engine
  (Requirement 11.1).
* :meth:`MemoryGuard.remote` — talks to the REST API with an optional bearer
  token (Requirement 11.2).

Operations: :meth:`add`, :meth:`get`, :meth:`query`, :meth:`ingest_path`,
:meth:`correct`, :meth:`delete`, :meth:`contradictions`. Query results carry
``content``, ``trust_score``, ``source_ref`` and ``reasons`` (Requirement 11.3).

This module is part of the Apache-2.0 OSS SDK.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Optional, Union

from memoryguard_core import Scope, Sensitivity, SourceType

from .local import LocalBackend
from .models import Contradiction, Memory, QueryResult
from .remote import RemoteBackend

__all__ = ["MemoryGuard"]

ScopeLike = Union[Scope, str]
SourceTypeLike = Union[SourceType, str]
SensitivityLike = Union[Sensitivity, str]


class MemoryGuard:
    """Unified client wrapping a local or remote backend.

    Construct with :meth:`local` or :meth:`remote`; do not instantiate directly.
    """

    def __init__(self, backend: Any) -> None:
        self._backend = backend

    # -- constructors ------------------------------------------------------

    @classmethod
    def local(
        cls,
        path: str,
        *,
        audit_path: Optional[str] = None,
        flags: Any = None,
    ) -> "MemoryGuard":
        """Create a client backed by the local core engine (Requirement 11.1).

        Args:
            path: SQLite store location — a filesystem path or ``":memory:"``.
            audit_path: optional path for the local JSONL audit log.
            flags: optional :class:`~memoryguard_core.flags.FeatureFlags`.
        """

        return cls(LocalBackend(path, audit_path=audit_path, flags=flags))

    @classmethod
    def remote(
        cls,
        base_url: str,
        token: Optional[str] = None,
        *,
        client: Any = None,
        timeout: float = 30.0,
    ) -> "MemoryGuard":
        """Create a REST client (Requirement 11.2).

        Args:
            base_url: base URL of the MemoryGuard REST API.
            token: optional bearer token for ``Authorization``.
            client: optional pre-built ``httpx.Client`` (for testing).
            timeout: request timeout in seconds.
        """

        return cls(
            RemoteBackend(base_url, token, client=client, timeout=timeout)
        )

    # -- properties --------------------------------------------------------

    @property
    def mode(self) -> str:
        """``"local"`` or ``"remote"`` depending on the active backend."""

        return getattr(self._backend, "mode", "unknown")

    @property
    def backend(self) -> Any:
        """The underlying backend (e.g. for closing a remote client)."""

        return self._backend

    # -- operations --------------------------------------------------------

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
        """Add a memory and return it (Requirements 11.1, 11.2)."""

        return self._backend.add(
            content=content,
            source_type=source_type,
            source_ref=source_ref,
            scope=scope,
            scope_ref=scope_ref,
            sensitivity=sensitivity,
            expires_at=expires_at,
            tags=tags,
        )

    def get(self, memory_id: str) -> Optional[Memory]:
        """Fetch a memory by id, or ``None`` when absent."""

        return self._backend.get(memory_id)

    def query(
        self,
        text: str,
        scope: Optional[ScopeLike] = None,
        scope_ref: Optional[str] = None,
        min_trust: float = 0.0,
        limit: int = 10,
        **kwargs: Any,
    ) -> list[QueryResult]:
        """Run a trust-aware query and return ranked results (Requirement 11.3).

        Each result exposes ``.memory`` (with ``content`` / ``trust_score`` /
        ``source_ref``) and ``.reasons``.
        """

        return self._backend.query(
            text=text,
            scope=scope,
            scope_ref=scope_ref,
            min_trust=min_trust,
            limit=limit,
            **kwargs,
        )

    def ingest_path(
        self,
        path: str,
        scope: ScopeLike,
        scope_ref: Optional[str] = None,
    ) -> list[Memory]:
        """Ingest a file, folder, or repository and return created memories."""

        return self._backend.ingest_path(path, scope=scope, scope_ref=scope_ref)

    def correct(self, memory_id: str, new_content: str) -> Memory:
        """Record a corrected lineage and return the new memory."""

        return self._backend.correct(memory_id, new_content)

    def delete(self, memory_id: str) -> None:
        """Soft-delete a memory by id."""

        self._backend.delete(memory_id)

    def contradictions(self, memory_id: str) -> list[Contradiction]:
        """Return the conflicts linked to ``memory_id``."""

        return self._backend.contradictions(memory_id)

    def close(self) -> None:
        """Release backend resources (no-op for the local backend)."""

        close = getattr(self._backend, "close", None)
        if callable(close):
            close()

    def __enter__(self) -> "MemoryGuard":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()
