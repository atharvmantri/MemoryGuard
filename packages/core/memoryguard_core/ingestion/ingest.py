# SPDX-License-Identifier: Apache-2.0
"""Manual add and file/folder ingestion for the Ingestion Layer (OSS core).

This module implements the core ingestion entry points:

* :func:`add_memory` — manually add a single memory with full provenance and
  lifecycle metadata, embedding the content and running an optional
  :class:`~memoryguard_core.retrieval.policy_filter.IngestionInspector`.
* :func:`ingest_file` — read a text file, chunk it, and create one embedded
  memory record per chunk with a ``file://`` ``source_ref``.
* :func:`ingest_folder` — walk a folder and ingest every supported text file,
  skipping binaries and unreadable files.
* :class:`Ingestor` — a small convenience wrapper that binds a store + embedder
  (+ optional inspector) and accumulates ingestion failures.

Every created record carries provenance (``source_type``, ``source_ref``), the
requested ``scope``/``scope_ref``, and an embedding from the configured
``Embedder``. Files that cannot be read or decoded are skipped and recorded as
failures rather than crashing the run (Requirement 3.7).

This module is part of the Apache-2.0 OSS core. It depends only on the Python
standard library, the core models/store, and the injected ``Embedder`` /
``IngestionInspector`` interfaces. It MUST NOT import from any commercial package.

Requirements: 3.1, 3.2, 3.3, 3.5, 3.6, 3.7.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional, Protocol

from memoryguard_core.ingestion.chunking import (
    DEFAULT_MAX_CHARS,
    DEFAULT_OVERLAP,
    chunk_text,
)
from memoryguard_core.models import (
    MemoryRecord,
    Scope,
    Sensitivity,
    SourceType,
    new_memory_record,
)

__all__ = [
    "Ingestor",
    "IngestionFailure",
    "add_memory",
    "ingest_file",
    "ingest_folder",
    "DEFAULT_TEXT_EXTENSIONS",
]

logger = logging.getLogger("memoryguard.ingestion")


# ---------------------------------------------------------------------------
# Structural typing for the injected dependencies (avoids importing the models
# package, keeping the OSS core free of a hard dependency on packages/models).
# ---------------------------------------------------------------------------


class _Embedder(Protocol):
    """Structural type for the injected embedder (``Embedder`` interface)."""

    def embed(self, text: str) -> list[float]: ...


class _Store(Protocol):
    """Structural type for the injected store (``MemoryStore`` interface)."""

    def add(self, record: MemoryRecord) -> MemoryRecord: ...


class _Inspector(Protocol):
    """Structural type for the injected ingestion inspector."""

    def inspect(self, record: MemoryRecord) -> MemoryRecord: ...


#: A callable that builds a ``source_ref`` for a chunk of a file:
#: ``(resolved_path, chunk_index) -> source_ref``.
SourceRefBuilder = Callable[[Path, int], str]


#: Default set of text/code file extensions ingested by :func:`ingest_folder`.
DEFAULT_TEXT_EXTENSIONS: frozenset[str] = frozenset(
    {
        # docs / config
        ".md",
        ".markdown",
        ".rst",
        ".txt",
        ".text",
        ".json",
        ".yaml",
        ".yml",
        ".toml",
        ".cfg",
        ".ini",
        ".env",
        ".csv",
        ".tsv",
        ".xml",
        ".html",
        ".htm",
        ".css",
        # common code
        ".py",
        ".pyi",
        ".js",
        ".jsx",
        ".mjs",
        ".cjs",
        ".ts",
        ".tsx",
        ".java",
        ".kt",
        ".go",
        ".rs",
        ".rb",
        ".php",
        ".c",
        ".h",
        ".cc",
        ".cpp",
        ".hpp",
        ".cs",
        ".swift",
        ".scala",
        ".sh",
        ".bash",
        ".zsh",
        ".sql",
        ".r",
        ".lua",
        ".pl",
    }
)


# ---------------------------------------------------------------------------
# Failure records
# ---------------------------------------------------------------------------


@dataclass
class IngestionFailure:
    """A recorded ingestion failure for a single path.

    Attributes:
        path: the path that failed (as supplied / resolved).
        error: a short human-readable reason (e.g. ``"UnicodeDecodeError: ..."``).
    """

    path: str
    error: str


def _record_failure(
    failures: Optional[list[IngestionFailure]],
    path: Path,
    exc: Exception,
) -> None:
    """Append a failure to ``failures`` (if provided) and log a warning."""

    reason = f"{type(exc).__name__}: {exc}"
    logger.warning("ingestion: skipping %s (%s)", path, reason)
    if failures is not None:
        failures.append(IngestionFailure(path=str(path), error=reason))


# ---------------------------------------------------------------------------
# Manual add (Requirement 3.1)
# ---------------------------------------------------------------------------


def add_memory(
    store: _Store,
    embedder: _Embedder,
    *,
    content: str,
    source_type: SourceType,
    source_ref: str,
    scope: Scope,
    scope_ref: Optional[str] = None,
    sensitivity: Sensitivity = Sensitivity.INTERNAL,
    expires_at: Optional[datetime] = None,
    tags: Optional[list[str]] = None,
    inspector: Optional[_Inspector] = None,
) -> MemoryRecord:
    """Create, embed, inspect, and persist a single memory record.

    Builds a validated :class:`MemoryRecord` via
    :func:`~memoryguard_core.models.new_memory_record` with the supplied
    provenance and lifecycle metadata, computes its embedding with ``embedder``,
    runs the optional ``inspector`` hook, persists it via ``store.add``, and
    returns the stored record.

    Args:
        store: the ``MemoryStore`` to persist into.
        embedder: the ``Embedder`` used to compute the content embedding.
        content: the remembered text (must be non-empty after trimming).
        source_type: provenance source type.
        source_ref: provenance source reference (must be non-empty).
        scope: the visibility scope.
        scope_ref: the scope binding id (required for project/repo/user/session).
        sensitivity: the data-sensitivity tier (default ``INTERNAL``).
        expires_at: optional expiry (must be greater than ``created_at``).
        tags: optional tags.
        inspector: optional ingestion inspector run before persistence.

    Returns:
        The persisted :class:`MemoryRecord`.
    """

    record = new_memory_record(
        content=content,
        source_type=source_type,
        source_ref=source_ref,
        scope=scope,
        scope_ref=scope_ref,
        sensitivity=sensitivity,
        expires_at=expires_at,
        tags=tags,
    )
    # Compute the embedding for retrieval (Requirement 3.6).
    record.embedding = embedder.embed(record.content)
    # Optional ingestion inspection hook (poison/PII flagging in commercial builds).
    if inspector is not None:
        record = inspector.inspect(record)
    return store.add(record)


# ---------------------------------------------------------------------------
# File ingestion (Requirements 3.2, 3.5, 3.6, 3.7)
# ---------------------------------------------------------------------------


def _default_file_source_ref(resolved: Path, chunk_index: int) -> str:
    """Default ``file://<abspath>#chunk=<i>`` source reference for a chunk."""

    return f"file://{resolved.as_posix()}#chunk={chunk_index}"


def ingest_file(
    store: _Store,
    embedder: _Embedder,
    path: str | Path,
    *,
    scope: Scope,
    scope_ref: Optional[str] = None,
    inspector: Optional[_Inspector] = None,
    source_type: SourceType = SourceType.FILE,
    source_ref_builder: Optional[SourceRefBuilder] = None,
    sensitivity: Sensitivity = Sensitivity.INTERNAL,
    tags: Optional[list[str]] = None,
    max_chars: int = DEFAULT_MAX_CHARS,
    overlap: int = DEFAULT_OVERLAP,
    failures: Optional[list[IngestionFailure]] = None,
) -> list[MemoryRecord]:
    """Read, chunk, embed, and persist one memory record per chunk of a file.

    The file is read as UTF-8 text and split via
    :func:`~memoryguard_core.ingestion.chunking.chunk_text`. Each chunk becomes a
    memory record whose ``source_ref`` identifies the file (default
    ``file://<abspath>#chunk=<i>``) with the supplied ``scope``/``scope_ref`` and
    an embedding.

    If the file cannot be read or decoded, it is skipped: the failure is recorded
    (in ``failures`` and the log) and an empty list is returned — the run does not
    crash (Requirement 3.7).

    Returns:
        The list of persisted records (one per chunk); ``[]`` on read/decode
        failure or empty content.
    """

    file_path = Path(path)
    try:
        resolved = file_path.resolve()
        text = resolved.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError, ValueError) as exc:
        _record_failure(failures, file_path, exc)
        return []

    builder = source_ref_builder or _default_file_source_ref
    records: list[MemoryRecord] = []
    for index, chunk in enumerate(chunk_text(text, max_chars=max_chars, overlap=overlap)):
        record = add_memory(
            store,
            embedder,
            content=chunk,
            source_type=source_type,
            source_ref=builder(resolved, index),
            scope=scope,
            scope_ref=scope_ref,
            sensitivity=sensitivity,
            tags=tags,
            inspector=inspector,
        )
        records.append(record)
    return records


# ---------------------------------------------------------------------------
# Folder ingestion (Requirements 3.3, 3.5, 3.6, 3.7)
# ---------------------------------------------------------------------------


def _normalize_extensions(extensions: Optional[set[str] | frozenset[str] | list[str]]) -> frozenset[str]:
    """Normalize a set of extensions to lowercase, dot-prefixed form."""

    if extensions is None:
        return DEFAULT_TEXT_EXTENSIONS
    normalized: set[str] = set()
    for ext in extensions:
        ext = ext.lower()
        normalized.add(ext if ext.startswith(".") else f".{ext}")
    return frozenset(normalized)


def ingest_folder(
    store: _Store,
    embedder: _Embedder,
    path: str | Path,
    *,
    scope: Scope,
    scope_ref: Optional[str] = None,
    inspector: Optional[_Inspector] = None,
    extensions: Optional[set[str] | frozenset[str] | list[str]] = None,
    source_type: SourceType = SourceType.FILE,
    source_ref_builder: Optional[SourceRefBuilder] = None,
    sensitivity: Sensitivity = Sensitivity.INTERNAL,
    max_chars: int = DEFAULT_MAX_CHARS,
    overlap: int = DEFAULT_OVERLAP,
    failures: Optional[list[IngestionFailure]] = None,
) -> list[MemoryRecord]:
    """Recursively ingest the supported text files within a folder.

    Walks ``path`` in a deterministic (sorted) order and ingests every regular
    file whose extension is in ``extensions`` (default
    :data:`DEFAULT_TEXT_EXTENSIONS`). Binary files (by extension) are skipped, and
    any file that cannot be read/decoded is skipped and recorded as a failure
    (Requirement 3.7).

    Returns:
        The combined list of persisted records across all ingested files.
    """

    base = Path(path)
    if not base.is_dir():
        _record_failure(failures, base, NotADirectoryError(str(base)))
        return []

    allowed = _normalize_extensions(extensions)
    records: list[MemoryRecord] = []
    for candidate in sorted(base.rglob("*"), key=lambda p: p.as_posix()):
        if not candidate.is_file():
            continue
        if candidate.suffix.lower() not in allowed:
            continue
        records.extend(
            ingest_file(
                store,
                embedder,
                candidate,
                scope=scope,
                scope_ref=scope_ref,
                inspector=inspector,
                source_type=source_type,
                source_ref_builder=source_ref_builder,
                sensitivity=sensitivity,
                max_chars=max_chars,
                overlap=overlap,
                failures=failures,
            )
        )
    return records


# ---------------------------------------------------------------------------
# Convenience wrapper
# ---------------------------------------------------------------------------


@dataclass
class Ingestor:
    """Binds a store + embedder (+ optional inspector) for repeated ingestion.

    Accumulates :class:`IngestionFailure` entries in :attr:`failures` so callers
    can inspect skipped files after a run. Each method mirrors the corresponding
    module-level function.
    """

    store: _Store
    embedder: _Embedder
    inspector: Optional[_Inspector] = None
    failures: list[IngestionFailure] = field(default_factory=list)

    def add_memory(
        self,
        *,
        content: str,
        source_type: SourceType,
        source_ref: str,
        scope: Scope,
        scope_ref: Optional[str] = None,
        sensitivity: Sensitivity = Sensitivity.INTERNAL,
        expires_at: Optional[datetime] = None,
        tags: Optional[list[str]] = None,
    ) -> MemoryRecord:
        """Manually add a single memory (see :func:`add_memory`)."""

        return add_memory(
            self.store,
            self.embedder,
            content=content,
            source_type=source_type,
            source_ref=source_ref,
            scope=scope,
            scope_ref=scope_ref,
            sensitivity=sensitivity,
            expires_at=expires_at,
            tags=tags,
            inspector=self.inspector,
        )

    def ingest_file(
        self,
        path: str | Path,
        *,
        scope: Scope,
        scope_ref: Optional[str] = None,
        max_chars: int = DEFAULT_MAX_CHARS,
        overlap: int = DEFAULT_OVERLAP,
    ) -> list[MemoryRecord]:
        """Ingest a single file (see :func:`ingest_file`)."""

        return ingest_file(
            self.store,
            self.embedder,
            path,
            scope=scope,
            scope_ref=scope_ref,
            inspector=self.inspector,
            max_chars=max_chars,
            overlap=overlap,
            failures=self.failures,
        )

    def ingest_folder(
        self,
        path: str | Path,
        *,
        scope: Scope,
        scope_ref: Optional[str] = None,
        extensions: Optional[set[str] | frozenset[str] | list[str]] = None,
        max_chars: int = DEFAULT_MAX_CHARS,
        overlap: int = DEFAULT_OVERLAP,
    ) -> list[MemoryRecord]:
        """Ingest a folder of supported files (see :func:`ingest_folder`)."""

        return ingest_folder(
            self.store,
            self.embedder,
            path,
            scope=scope,
            scope_ref=scope_ref,
            inspector=self.inspector,
            extensions=extensions,
            max_chars=max_chars,
            overlap=overlap,
            failures=self.failures,
        )
