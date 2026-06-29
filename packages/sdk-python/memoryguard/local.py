# SPDX-License-Identifier: Apache-2.0
"""Local SDK backend — talks directly to the in-process core engine.

:class:`LocalBackend` wraps a :class:`~memoryguard_core.engine.MemoryGuardEngine`
built by :func:`~memoryguard_core.bootstrap.build_local_engine` and adapts its
operations onto the SDK's uniform result types. It runs entirely on-device with
no network I/O (Requirement 11.1) and returns the same conceptual results as the
equivalent REST call (Requirement 11.4).

This module is part of the Apache-2.0 OSS SDK.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Optional, Union

from memoryguard_core import (
    Scope,
    Sensitivity,
    SourceType,
    build_local_engine,
)
from memoryguard_core.retrieval.service import QuerySpec

from .models import Contradiction, Memory, QueryResult

__all__ = ["LocalBackend"]

ScopeLike = Union[Scope, str]
SourceTypeLike = Union[SourceType, str]
SensitivityLike = Union[Sensitivity, str]


def _coerce_source_type(value: SourceTypeLike) -> SourceType:
    return value if isinstance(value, SourceType) else SourceType(value)


def _coerce_scope(value: ScopeLike) -> Scope:
    return value if isinstance(value, Scope) else Scope(value)


def _coerce_sensitivity(value: SensitivityLike) -> Sensitivity:
    return value if isinstance(value, Sensitivity) else Sensitivity(value)


class LocalBackend:
    """SDK backend backed by the local core engine.

    Args:
        path: SQLite store location — a filesystem path or ``":memory:"``.
        audit_path: optional path for the local JSONL audit log.
        flags: optional :class:`~memoryguard_core.flags.FeatureFlags` snapshot.
    """

    mode = "local"

    def __init__(
        self,
        path: str,
        *,
        audit_path: Optional[str] = None,
        flags: Any = None,
    ) -> None:
        self.engine = build_local_engine(path, flags=flags, audit_path=audit_path)

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
        record = self.engine.create_memory(
            content=content,
            source_type=_coerce_source_type(source_type),
            source_ref=source_ref,
            scope=_coerce_scope(scope),
            scope_ref=scope_ref,
            sensitivity=_coerce_sensitivity(sensitivity),
            expires_at=expires_at,
            tags=tags,
        )
        return Memory.from_record(record)

    def get(self, memory_id: str) -> Optional[Memory]:
        record = self.engine.get(memory_id)
        return Memory.from_record(record) if record is not None else None

    def query(
        self,
        text: str,
        scope: Optional[ScopeLike] = None,
        scope_ref: Optional[str] = None,
        min_trust: float = 0.0,
        limit: int = 10,
        max_sensitivity: SensitivityLike = Sensitivity.INTERNAL,
    ) -> list[QueryResult]:
        spec = QuerySpec(
            text=text,
            scope=_coerce_scope(scope) if scope is not None else None,
            scope_ref=scope_ref,
            min_trust=min_trust,
            max_sensitivity=_coerce_sensitivity(max_sensitivity),
            limit=limit,
        )
        results = self.engine.query(spec)
        return [QueryResult.from_retrieved(rm) for rm in results]

    def ingest_path(
        self,
        path: str,
        scope: ScopeLike,
        scope_ref: Optional[str] = None,
    ) -> list[Memory]:
        records = self.engine.ingest_path(
            path,
            scope=_coerce_scope(scope),
            scope_ref=scope_ref,
        )
        return [Memory.from_record(r) for r in records]

    def correct(self, memory_id: str, new_content: str) -> Memory:
        record = self.engine.correct_memory(memory_id, new_content)
        return Memory.from_record(record)

    def delete(self, memory_id: str) -> None:
        self.engine.store.soft_delete(memory_id)

    def contradictions(self, memory_id: str) -> list[Contradiction]:
        explanation = self.engine.explain(memory_id)
        entries = explanation.get("contradictions", []) or []
        return [Contradiction.from_dict(entry) for entry in entries]
