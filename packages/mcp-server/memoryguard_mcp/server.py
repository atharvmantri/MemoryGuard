# SPDX-License-Identifier: Apache-2.0
"""MemoryGuard OSS MCP server — tools + resources over a local engine.

This module exposes MemoryGuard to AI coding agents via the Model Context
Protocol (design *MCP Server Interface*). It wraps a local
:class:`~memoryguard_core.engine.MemoryGuardEngine` (built by
:func:`~memoryguard_core.bootstrap.build_local_engine`) and exposes:

Tools (Requirements 13.1–13.4):
  * ``memory_search`` — trust/scope/policy-filtered retrieval, returning each
    result's ``source_ref`` + ``trust_score`` + reasons (Requirement 13.2). When
    no ``min_trust`` is supplied it applies a default trust floor of ``0.5``
    (Requirement 13.6).
  * ``memory_add`` — create a memory with the supplied provenance
    (Requirement 13.3); ``source_type`` is inferred from ``source_ref`` (else
    ``api``).
  * ``memory_explain`` — return the trust rationale + provenance for a memory
    (Requirement 13.4).

Resources (Requirement 13.5):
  * ``memoryguard://project/{scope_ref}/memories`` — list memories for a scope.
  * ``memoryguard://memory/{memory_id}`` — single memory with full provenance +
    trust breakdown.

Design for testability
-----------------------
All core logic lives in plain handler functions (``tool_memory_search``,
``tool_memory_add``, ``tool_memory_explain``, ``resource_project_memories``,
``resource_memory``) that take an explicit ``engine`` argument. The MCP tool /
resource registrations in :func:`build_mcp_server` are thin wrappers that call
these functions. This means the handlers are unit-testable **without** a running
MCP transport, and the ``import mcp`` is guarded so importing this module never
hard-fails when the optional ``mcp`` dependency is absent.

This package is Apache-2.0 OSS and MUST NOT import from any commercial package.

Requirements: 13.1, 13.2, 13.3, 13.4, 13.5, 13.6.
"""

from __future__ import annotations

import os
from typing import Optional

from memoryguard_core import (
    MemoryGuardEngine,
    MemoryRecord,
    Scope,
    Sensitivity,
    SourceType,
    build_local_engine,
)
from memoryguard_core.retrieval.service import QuerySpec, RetrievedMemory

__all__ = [
    "DEFAULT_MIN_TRUST",
    "tool_memory_search",
    "tool_memory_add",
    "tool_memory_explain",
    "resource_project_memories",
    "resource_memory",
    "build_mcp_server",
    "main",
]


#: Default trust floor applied to ``memory_search`` when none is supplied
#: (Requirement 13.6).
DEFAULT_MIN_TRUST: float = 0.5

#: Environment variable naming the local SQLite store location for ``main()``.
_STORE_ENV = "MEMORYGUARD_STORE"
_DEFAULT_STORE = "memoryguard.db"

#: Map the ``scheme`` of a ``source_ref`` (e.g. ``user://alice``) to a
#: :class:`SourceType`. Anything unrecognized falls back to ``API``.
_SOURCE_SCHEME_MAP: dict[str, SourceType] = {
    "user": SourceType.USER,
    "file": SourceType.FILE,
    "repo": SourceType.FILE,
    "commit": SourceType.COMMIT,
    "slack": SourceType.SLACK,
    "jira": SourceType.JIRA,
    "api": SourceType.API,
}


# ---------------------------------------------------------------------------
# Parsing / inference helpers
# ---------------------------------------------------------------------------


def _parse_scope(value: Optional[str]) -> Optional[Scope]:
    """Parse a scope string into a :class:`Scope` (``None`` stays ``None``).

    Raises:
        ValueError: when ``value`` is a non-empty string that is not a valid
            scope name.
    """

    if value is None:
        return None
    if isinstance(value, Scope):
        return value
    text = str(value).strip().lower()
    if not text:
        return None
    try:
        return Scope(text)
    except ValueError as exc:  # pragma: no cover - exercised via tool calls
        valid = ", ".join(s.value for s in Scope)
        raise ValueError(f"invalid scope {value!r}; expected one of: {valid}") from exc


def _parse_sensitivity(value: Optional[str]) -> Sensitivity:
    """Parse a sensitivity string into a :class:`Sensitivity` (default INTERNAL).

    Raises:
        ValueError: when ``value`` is a non-empty string that is not a valid
            sensitivity tier.
    """

    if value is None:
        return Sensitivity.INTERNAL
    if isinstance(value, Sensitivity):
        return value
    text = str(value).strip().lower()
    if not text:
        return Sensitivity.INTERNAL
    try:
        return Sensitivity(text)
    except ValueError as exc:  # pragma: no cover - exercised via tool calls
        valid = ", ".join(s.value for s in Sensitivity)
        raise ValueError(
            f"invalid sensitivity {value!r}; expected one of: {valid}"
        ) from exc


def _infer_source_type(source_ref: str) -> SourceType:
    """Infer a :class:`SourceType` from a ``source_ref`` scheme, else ``API``.

    Recognizes the ``scheme://...`` prefix of common provenance refs (e.g.
    ``user://alice`` -> ``USER``, ``repo://README.md@abc`` -> ``FILE``,
    ``commit://abc`` -> ``COMMIT``). Anything without a known scheme is treated
    as having come through the API surface (``SourceType.API``).
    """

    if isinstance(source_ref, str) and "://" in source_ref:
        scheme = source_ref.split("://", 1)[0].strip().lower()
        return _SOURCE_SCHEME_MAP.get(scheme, SourceType.API)
    return SourceType.API


# ---------------------------------------------------------------------------
# Serialization helpers
# ---------------------------------------------------------------------------


def _retrieved_to_dict(item: RetrievedMemory) -> dict:
    """Serialize a :class:`RetrievedMemory` for a ``memory_search`` result.

    Each result carries its provenance (``source_ref``) and ``trust_score`` so
    the agent can cite *why* a fact is trusted (Requirement 13.2).
    """

    record = item.record
    return {
        "memory_id": record.memory_id,
        "content": record.content,
        "source_ref": record.source_ref,
        "source_type": record.source_type.value,
        "trust_score": record.trust_score,
        "scope": record.scope.value,
        "scope_ref": record.scope_ref,
        "sensitivity": record.sensitivity.value,
        "status": record.status.value,
        "relevance": item.relevance,
        "final_rank": item.final_rank,
        "reasons": list(item.reasons),
    }


def _record_summary(record: MemoryRecord) -> dict:
    """Serialize a :class:`MemoryRecord` to a compact summary dict.

    Used by the project-memories resource listing; includes provenance + trust
    so the listing is browsable and citable.
    """

    return {
        "memory_id": record.memory_id,
        "content": record.content,
        "source_ref": record.source_ref,
        "source_type": record.source_type.value,
        "trust_score": record.trust_score,
        "scope": record.scope.value,
        "scope_ref": record.scope_ref,
        "sensitivity": record.sensitivity.value,
        "status": record.status.value,
        "created_at": record.created_at.isoformat()
        if record.created_at is not None
        else None,
        "updated_at": record.updated_at.isoformat()
        if record.updated_at is not None
        else None,
    }


# ---------------------------------------------------------------------------
# Tool handlers (plain, engine-injected — unit-testable without MCP transport)
# ---------------------------------------------------------------------------


def tool_memory_search(
    engine: MemoryGuardEngine,
    query: str,
    scope: Optional[str] = None,
    scope_ref: Optional[str] = None,
    min_trust: Optional[float] = None,
    limit: int = 5,
) -> dict:
    """Retrieve relevant AND trusted memories for the current task.

    Builds a :class:`QuerySpec` and delegates to ``engine.query``; the retrieval
    service already enforces the trust + scope + policy filter, so every returned
    memory has passed it (Requirement 13.2). When ``min_trust`` is not supplied a
    default floor of ``0.5`` is applied (Requirement 13.6).

    Returns a dict with the filtered ``results`` (each including ``source_ref``
    and ``trust_score`` + reasons), the result ``count``, and the effective
    ``min_trust`` used.
    """

    floor = DEFAULT_MIN_TRUST if min_trust is None else float(min_trust)

    spec = QuerySpec(
        text=query,
        scope=_parse_scope(scope),
        scope_ref=scope_ref,
        min_trust=floor,
        limit=int(limit),
    )
    results = engine.query(spec)

    return {
        "results": [_retrieved_to_dict(item) for item in results],
        "count": len(results),
        "min_trust": floor,
    }


def tool_memory_add(
    engine: MemoryGuardEngine,
    content: str,
    source_ref: str,
    scope: str,
    scope_ref: Optional[str] = None,
    sensitivity: str = "internal",
) -> dict:
    """Store a new memory with the supplied provenance (Requirement 13.3).

    ``source_type`` is inferred from ``source_ref`` (falling back to ``api``).
    Returns the created memory's ``memory_id`` and its evaluated ``trust_score``.
    """

    parsed_scope = _parse_scope(scope)
    if parsed_scope is None:
        raise ValueError("scope is required for memory_add")

    record = engine.create_memory(
        content=content,
        source_type=_infer_source_type(source_ref),
        source_ref=source_ref,
        scope=parsed_scope,
        scope_ref=scope_ref,
        sensitivity=_parse_sensitivity(sensitivity),
    )
    return {
        "memory_id": record.memory_id,
        "trust_score": record.trust_score,
        "source_ref": record.source_ref,
        "source_type": record.source_type.value,
        "scope": record.scope.value,
        "scope_ref": record.scope_ref,
        "sensitivity": record.sensitivity.value,
        "status": record.status.value,
    }


def tool_memory_explain(engine: MemoryGuardEngine, memory_id: str) -> dict:
    """Return why a memory was trusted/used — trust rationale + provenance.

    Delegates to ``engine.explain`` (Requirement 13.4), which returns the trust
    signal breakdown, weights, provenance, lineage, and contradictions.

    Raises:
        KeyError: when ``memory_id`` does not exist.
    """

    return engine.explain(memory_id)


# ---------------------------------------------------------------------------
# Resource handlers (plain, engine-injected)
# ---------------------------------------------------------------------------


def resource_project_memories(engine: MemoryGuardEngine, scope_ref: str) -> dict:
    """List stored memories bound to ``scope_ref``.

    Backs ``memoryguard://project/{scope_ref}/memories`` (Requirement 13.5).
    Returns a browsable list of memory summaries (provenance + trust included).
    """

    records = engine.store.list(scope_ref=scope_ref)
    return {
        "scope_ref": scope_ref,
        "count": len(records),
        "memories": [_record_summary(record) for record in records],
    }


def resource_memory(engine: MemoryGuardEngine, memory_id: str) -> dict:
    """Return a single memory with full provenance + trust breakdown.

    Backs ``memoryguard://memory/{memory_id}`` (Requirement 13.5). Combines the
    record's ``content`` with the full ``engine.explain`` rationale (provenance,
    trust signals, weights, lineage, contradictions).

    Raises:
        KeyError: when ``memory_id`` does not exist.
    """

    record = engine.get(memory_id)
    if record is None:
        raise KeyError(f"resource_memory: unknown memory_id {memory_id!r}")

    explanation = engine.explain(memory_id)
    return {"content": record.content, **explanation}


# ---------------------------------------------------------------------------
# MCP server assembly (only invoked when the optional `mcp` dep is installed)
# ---------------------------------------------------------------------------


def build_mcp_server(engine: MemoryGuardEngine):
    """Build a FastMCP server registering the tools + resources over ``engine``.

    The registrations are thin wrappers that delegate to the plain handler
    functions above, so the MCP layer adds no business logic of its own. Importing
    ``mcp`` happens here (not at module import) so this module stays import-safe
    when the optional dependency is absent.

    Raises:
        ImportError: when the ``mcp`` package is not installed.
    """

    from mcp.server.fastmcp import FastMCP

    server = FastMCP("memoryguard")

    @server.tool(
        name="memory_search",
        description="Retrieve relevant AND trusted memories for the current task.",
    )
    def memory_search(  # pragma: no cover - thin MCP wrapper
        query: str,
        scope: Optional[str] = None,
        scope_ref: Optional[str] = None,
        min_trust: Optional[float] = None,
        limit: int = 5,
    ) -> dict:
        return tool_memory_search(
            engine,
            query,
            scope=scope,
            scope_ref=scope_ref,
            min_trust=min_trust,
            limit=limit,
        )

    @server.tool(
        name="memory_add",
        description="Store a new memory with provenance.",
    )
    def memory_add(  # pragma: no cover - thin MCP wrapper
        content: str,
        source_ref: str,
        scope: str,
        scope_ref: Optional[str] = None,
        sensitivity: str = "internal",
    ) -> dict:
        return tool_memory_add(
            engine,
            content,
            source_ref,
            scope,
            scope_ref=scope_ref,
            sensitivity=sensitivity,
        )

    @server.tool(
        name="memory_explain",
        description="Explain why a memory was trusted/used (rationale + provenance).",
    )
    def memory_explain(memory_id: str) -> dict:  # pragma: no cover - thin wrapper
        return tool_memory_explain(engine, memory_id)

    @server.resource("memoryguard://project/{scope_ref}/memories")
    def project_memories(scope_ref: str) -> dict:  # pragma: no cover - thin wrapper
        return resource_project_memories(engine, scope_ref)

    @server.resource("memoryguard://memory/{memory_id}")
    def memory(memory_id: str) -> dict:  # pragma: no cover - thin wrapper
        return resource_memory(engine, memory_id)

    return server


def main() -> None:
    """Console-script entry point: start the MCP stdio server.

    Builds a local engine over the SQLite store named by the ``MEMORYGUARD_STORE``
    environment variable (default ``memoryguard.db``) and serves it over MCP
    stdio. When the optional ``mcp`` dependency is not installed, prints a helpful
    message and exits without error rather than raising an import failure.
    """

    try:
        import mcp  # noqa: F401  (presence check only)
    except ImportError:
        print(
            "The 'mcp' package is required to run the MemoryGuard MCP server.\n"
            "Install it with:  pip install \"memoryguard-mcp-server[mcp]\"\n"
            "  (or:  pip install mcp )"
        )
        return

    store_path = os.environ.get(_STORE_ENV, _DEFAULT_STORE)
    engine = build_local_engine(store_path)
    server = build_mcp_server(engine)
    server.run()


if __name__ == "__main__":  # pragma: no cover
    main()
