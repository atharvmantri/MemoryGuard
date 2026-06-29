# SPDX-License-Identifier: Apache-2.0
"""SDK-facing result types shared by the local and remote clients.

These small, transport-agnostic value objects are what every :class:`MemoryGuard`
method returns, regardless of whether the call ran against the in-process core
engine (:class:`~memoryguard.local.LocalBackend`) or the REST API
(:class:`~memoryguard.remote.RemoteBackend`). Keeping a single result shape is
what lets local mode produce the *same conceptual results* as the equivalent
REST operation (Requirement 11.4).

Field values are normalized to plain JSON-friendly types (enum *values* as
strings, ISO-8601 timestamps as strings) so a ``Memory`` built from a
:class:`~memoryguard_core.models.MemoryRecord` is indistinguishable from one
built from a REST JSON payload.

This module is part of the Apache-2.0 OSS SDK.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

__all__ = ["Memory", "QueryResult", "Contradiction"]


def _enum_value(value: Any) -> Any:
    """Return ``value.value`` for enums, otherwise ``value`` unchanged."""

    return getattr(value, "value", value)


def _iso(value: Any) -> Optional[str]:
    """Return an ISO-8601 string for a datetime, passing through str/None."""

    if value is None:
        return None
    isoformat = getattr(value, "isoformat", None)
    if callable(isoformat):
        return isoformat()
    return str(value)


@dataclass
class Memory:
    """A single memory as surfaced through the SDK.

    Carries content, ``trust_score`` and ``source_ref`` (Requirement 11.3) plus
    the rest of the canonical fields, all as plain JSON-friendly values so local
    and remote results are interchangeable.
    """

    memory_id: str
    content: str
    source_type: Optional[str]
    source_ref: str
    scope: Optional[str]
    scope_ref: Optional[str] = None
    sensitivity: Optional[str] = None
    status: Optional[str] = None
    trust_score: float = 0.0
    created_at: Optional[str] = None
    updated_at: Optional[str] = None
    contradicts: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)

    # -- builders ----------------------------------------------------------

    @classmethod
    def from_record(cls, record: Any) -> "Memory":
        """Build a :class:`Memory` from a core ``MemoryRecord`` (local mode)."""

        return cls(
            memory_id=record.memory_id,
            content=record.content,
            source_type=_enum_value(record.source_type),
            source_ref=record.source_ref,
            scope=_enum_value(record.scope),
            scope_ref=record.scope_ref,
            sensitivity=_enum_value(record.sensitivity),
            status=_enum_value(record.status),
            trust_score=float(record.trust_score),
            created_at=_iso(getattr(record, "created_at", None)),
            updated_at=_iso(getattr(record, "updated_at", None)),
            contradicts=list(getattr(record, "contradicts", []) or []),
            tags=list(getattr(record, "tags", []) or []),
        )

    @classmethod
    def from_json(cls, data: dict) -> "Memory":
        """Build a :class:`Memory` from a REST JSON object (remote mode).

        Accepts both ``snake_case`` (Python REST default) and ``camelCase`` keys
        so the client is tolerant of either serialization style.
        """

        def pick(*keys: str, default: Any = None) -> Any:
            for key in keys:
                if key in data and data[key] is not None:
                    return data[key]
            return default

        trust = pick("trust_score", "trustScore", default=0.0)
        return cls(
            memory_id=str(pick("memory_id", "memoryId", "id", default="")),
            content=str(pick("content", default="")),
            source_type=pick("source_type", "sourceType"),
            source_ref=str(pick("source_ref", "sourceRef", default="")),
            scope=pick("scope"),
            scope_ref=pick("scope_ref", "scopeRef"),
            sensitivity=pick("sensitivity"),
            status=pick("status"),
            trust_score=float(trust) if trust is not None else 0.0,
            created_at=pick("created_at", "createdAt"),
            updated_at=pick("updated_at", "updatedAt"),
            contradicts=list(pick("contradicts", default=[]) or []),
            tags=list(pick("tags", default=[]) or []),
        )


@dataclass
class QueryResult:
    """A single ranked query hit: a :class:`Memory` plus its ``reasons``.

    ``result.memory`` carries ``content`` / ``trust_score`` / ``source_ref`` and
    ``result.reasons`` carries the human-readable explanation list
    (Requirement 11.3).
    """

    memory: Memory
    reasons: list[str] = field(default_factory=list)
    relevance: float = 0.0
    final_rank: float = 0.0

    @classmethod
    def from_retrieved(cls, retrieved: Any) -> "QueryResult":
        """Build from a core ``RetrievedMemory`` (local mode)."""

        return cls(
            memory=Memory.from_record(retrieved.record),
            reasons=list(getattr(retrieved, "reasons", []) or []),
            relevance=float(getattr(retrieved, "relevance", 0.0) or 0.0),
            final_rank=float(getattr(retrieved, "final_rank", 0.0) or 0.0),
        )

    @classmethod
    def from_json(cls, data: dict) -> "QueryResult":
        """Build from a REST JSON result object (remote mode).

        Supports a nested ``{"memory": {...}, "reasons": [...]}`` shape as well
        as a flat memory object that also carries ``reasons``.
        """

        memory_data = data.get("memory")
        if isinstance(memory_data, dict):
            memory = Memory.from_json(memory_data)
        else:
            memory = Memory.from_json(data)

        reasons = data.get("reasons") or []
        relevance = data.get("relevance", data.get("relevance_score", 0.0)) or 0.0
        final_rank = data.get("final_rank", data.get("finalRank", 0.0)) or 0.0
        return cls(
            memory=memory,
            reasons=list(reasons),
            relevance=float(relevance),
            final_rank=float(final_rank),
        )


@dataclass
class Contradiction:
    """A conflicting memory linked to the one being inspected."""

    memory_id: str
    source_ref: Optional[str] = None
    reason: str = ""
    confidence: float = 0.0
    status: Optional[str] = None

    @classmethod
    def from_dict(cls, data: dict) -> "Contradiction":
        """Build from a core ``explain`` entry or a REST JSON object."""

        def pick(*keys: str, default: Any = None) -> Any:
            for key in keys:
                if key in data and data[key] is not None:
                    return data[key]
            return default

        confidence = pick("confidence", default=0.0)
        return cls(
            memory_id=str(pick("memory_id", "memoryId", "id", default="")),
            source_ref=pick("source_ref", "sourceRef"),
            reason=str(pick("reason", default="")),
            confidence=float(confidence) if confidence is not None else 0.0,
            status=pick("status"),
        )
