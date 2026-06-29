# SPDX-License-Identifier: Apache-2.0
"""The abstract :class:`MemoryStore` backend contract.

A ``MemoryStore`` persists and retrieves :class:`~memoryguard_core.models.MemoryRecord`
instances independently of the concrete backend. Phase 1 ships ``SqliteStore``
(local mode); a later phase adds ``PostgresStore`` (cloud mode). Both honor the
exact contract defined here, including the same validation rules and the
soft-delete semantics described below.

This module is part of the Apache-2.0 OSS core and is standard-library only. It
MUST NOT import from any commercial package.

Not-found contract
-------------------
There is no dedicated "not found" exception in this contract. :meth:`MemoryStore.get`
returns ``None`` when no record exists for the supplied ``memory_id``. Callers
distinguish "missing" from "present" by checking for ``None`` rather than by
catching an exception.

Soft-delete contract
--------------------
:meth:`MemoryStore.soft_delete` performs a *logical* delete: it sets the record's
``status`` to :attr:`~memoryguard_core.models.MemoryStatus.DELETED` and persists
that change. It MUST NOT hard-drop the row by default. Consequences:

* After ``soft_delete``, :meth:`get` still returns the record (now with
  ``status == MemoryStatus.DELETED``); the data is retained for audit and lineage.
* :meth:`list` returns soft-deleted records only when explicitly asked for them
  via ``status=MemoryStatus.DELETED`` (or when no ``status`` filter narrows them
  out — implementations document their default; the retrieval/policy layer is
  responsible for excluding deleted records from query results).
* Hard deletion (physically removing rows) is an explicit, separate operation and
  is never the default behavior of ``soft_delete``.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Optional

from memoryguard_core.models import MemoryRecord, MemoryStatus, Scope

__all__ = ["MemoryStore"]


class MemoryStore(ABC):
    """Persist and retrieve memory records, independent of backend.

    Implementations: ``SqliteStore`` (Phase 1) and ``PostgresStore`` (later).
    Both honor this contract and the :class:`MemoryRecord` validation rules.
    """

    @abstractmethod
    def add(self, record: MemoryRecord) -> MemoryRecord:
        """Persist a new ``record`` and return the stored record.

        The record MUST satisfy the :class:`MemoryRecord` validation rules.
        Implementations enforce global uniqueness of ``memory_id`` within the
        store.
        """
        ...

    @abstractmethod
    def get(self, memory_id: str) -> Optional[MemoryRecord]:
        """Return the record identified by ``memory_id``.

        Returns ``None`` when no such record exists (the not-found contract).
        Soft-deleted records are still returned (with ``status == DELETED``).
        """
        ...

    @abstractmethod
    def update(self, record: MemoryRecord) -> MemoryRecord:
        """Persist changes to an existing ``record`` and return it.

        The record MUST satisfy the :class:`MemoryRecord` validation rules.
        Implementations update ``updated_at`` semantics per the design and
        preserve ``updated_at >= created_at``.
        """
        ...

    @abstractmethod
    def soft_delete(self, memory_id: str) -> None:
        """Logically delete a record by id.

        Sets ``status`` to :attr:`MemoryStatus.DELETED` and persists it. Never
        hard-drops the row by default — the record remains retrievable via
        :meth:`get` for audit and lineage. See the module-level *Soft-delete
        contract* for full semantics.
        """
        ...

    @abstractmethod
    def list(
        self,
        *,
        scope: Optional[Scope] = None,
        scope_ref: Optional[str] = None,
        status: Optional[MemoryStatus] = None,
    ) -> list[MemoryRecord]:
        """Return records matching the optional filters.

        Any filter left as ``None`` is not applied. When all filters are ``None``
        the implementation returns its full set per its documented default.
        """
        ...

    @abstractmethod
    def keyword_search(self, query: str, limit: int) -> list[tuple[MemoryRecord, float]]:
        """Keyword/full-text search.

        Returns up to ``limit`` ``(record, score)`` pairs ordered by descending
        relevance score, where ``score`` is the backend's keyword-relevance signal.
        """
        ...

    @abstractmethod
    def vector_search(
        self, embedding: list[float], limit: int
    ) -> list[tuple[MemoryRecord, float]]:
        """Vector similarity search.

        Returns up to ``limit`` ``(record, score)`` pairs ordered by descending
        similarity, where ``score`` is the cosine similarity between ``embedding``
        and each record's stored embedding.
        """
        ...
