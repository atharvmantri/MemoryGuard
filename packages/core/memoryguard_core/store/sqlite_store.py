# SPDX-License-Identifier: Apache-2.0
"""SQLite-backed :class:`MemoryStore` for MemoryGuard local mode (Phase 1).

``SqliteStore`` persists :class:`~memoryguard_core.models.MemoryRecord` instances
to a SQLite database (a file path or the special ``":memory:"`` database). On
first initialization it applies ``infra/migrations/sqlite/0001_init.sql`` to
create the ``memories``, ``memory_contradictions``, ``memory_embeddings`` and
``memory_fts`` tables. Initialization is idempotent — an already-migrated
database is left untouched.

Storage notes
-------------
* Datetimes are persisted as ISO-8601 strings (timezone-aware values are
  normalized to UTC). ``tags`` and ``metadata`` are stored as JSON.
* Embeddings live in the separate ``memory_embeddings`` table as a packed
  ``float32`` BLOB plus their dimension. Cosine similarity is computed in Python
  (SQLite has no native vector type).
* ``memory_fts`` is an *external-content* FTS5 index over ``memories``; this
  module keeps it in sync explicitly on every write.

This module is part of the Apache-2.0 OSS core and is standard-library only. It
MUST NOT import from any commercial package.
"""

from __future__ import annotations

import json
import math
import sqlite3
from array import array
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from memoryguard_core.models import (
    MemoryRecord,
    MemoryStatus,
    Scope,
    Sensitivity,
    SourceType,
    validate,
)
from memoryguard_core.store.base import MemoryStore

__all__ = ["SqliteStore"]


# Relative location of the canonical migration within the repository.
_MIGRATION_RELATIVE = Path("infra") / "migrations" / "sqlite" / "0001_init.sql"

# Embedded fallback schema, used only when the migration file cannot be located
# on disk (e.g. an installed package without the repo's ``infra/`` tree). Kept in
# sync with infra/migrations/sqlite/0001_init.sql.
_FALLBACK_SCHEMA = """
CREATE TABLE memories (
    memory_id     TEXT PRIMARY KEY,
    content       TEXT NOT NULL,
    source_type   TEXT NOT NULL,
    source_ref    TEXT NOT NULL,
    scope         TEXT NOT NULL,
    scope_ref     TEXT,
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL,
    expires_at    TEXT,
    trust_score   REAL NOT NULL DEFAULT 0.0,
    sensitivity   TEXT NOT NULL DEFAULT 'internal',
    status        TEXT NOT NULL DEFAULT 'active',
    confirmations INTEGER NOT NULL DEFAULT 0,
    tags          TEXT NOT NULL DEFAULT '[]',
    metadata      TEXT NOT NULL DEFAULT '{}',
    CHECK (trust_score >= 0.0 AND trust_score <= 1.0)
);

CREATE TABLE memory_contradictions (
    memory_id      TEXT NOT NULL REFERENCES memories(memory_id) ON DELETE CASCADE,
    contradicts_id TEXT NOT NULL REFERENCES memories(memory_id) ON DELETE CASCADE,
    detected_at    TEXT NOT NULL,
    reason         TEXT,
    PRIMARY KEY (memory_id, contradicts_id)
);

CREATE TABLE memory_embeddings (
    memory_id     TEXT PRIMARY KEY REFERENCES memories(memory_id) ON DELETE CASCADE,
    dim           INTEGER NOT NULL,
    vector        BLOB NOT NULL
);

CREATE VIRTUAL TABLE memory_fts USING fts5(
    content, tags, content='memories', content_rowid='rowid'
);

CREATE INDEX idx_memories_scope ON memories(scope, scope_ref);
CREATE INDEX idx_memories_status ON memories(status);
CREATE INDEX idx_memories_expires ON memories(expires_at);
"""


def _find_migration_sql() -> str:
    """Return the migration SQL, preferring the on-disk repo file.

    Walks parent directories from this module looking for
    ``infra/migrations/sqlite/0001_init.sql``; falls back to the embedded schema
    when the file is not present (e.g. an installed wheel).
    """

    here = Path(__file__).resolve()
    for parent in here.parents:
        candidate = parent / _MIGRATION_RELATIVE
        if candidate.is_file():
            return candidate.read_text(encoding="utf-8")
    return _FALLBACK_SCHEMA


# ---------------------------------------------------------------------------
# Serialization helpers
# ---------------------------------------------------------------------------


def _iso(value: Optional[datetime]) -> Optional[str]:
    """Serialize a datetime to an ISO-8601 string (UTC for aware values)."""

    if value is None:
        return None
    if value.tzinfo is not None:
        value = value.astimezone(timezone.utc)
    return value.isoformat()


def _from_iso(value: Optional[str]) -> Optional[datetime]:
    """Parse an ISO-8601 string back into a datetime (or ``None``)."""

    if value is None:
        return None
    return datetime.fromisoformat(value)


def _pack_embedding(embedding: list[float]) -> bytes:
    """Pack a vector into a little-endian-agnostic ``float32`` BLOB."""

    return array("f", [float(x) for x in embedding]).tobytes()


def _unpack_embedding(blob: bytes) -> list[float]:
    """Unpack a ``float32`` BLOB back into a list of floats."""

    arr = array("f")
    arr.frombytes(blob)
    return list(arr)


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    """Cosine similarity of two equal-length vectors (0.0 if either is zero)."""

    dot = 0.0
    norm_a = 0.0
    norm_b = 0.0
    for x, y in zip(a, b):
        dot += x * y
        norm_a += x * x
        norm_b += y * y
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return dot / (math.sqrt(norm_a) * math.sqrt(norm_b))


def _escape_fts_query(query: str) -> str:
    """Build a safe FTS5 MATCH expression from arbitrary user text.

    Each whitespace-delimited token is wrapped in double quotes (an FTS5 phrase),
    with embedded double quotes doubled per FTS5 string rules. This neutralizes
    FTS5 operators and special characters so the query can never raise a syntax
    error. Returns an empty string when the query has no usable tokens.
    """

    tokens = query.split()
    quoted = ['"' + token.replace('"', '""') + '"' for token in tokens if token]
    return " ".join(quoted)


class SqliteStore(MemoryStore):
    """A :class:`MemoryStore` backed by SQLite (local mode).

    Parameters
    ----------
    db_path:
        Path to the SQLite database file, or ``":memory:"`` for an ephemeral
        in-memory database that lives for the lifetime of this store.
    """

    def __init__(self, db_path: str = ":memory:") -> None:
        self.db_path = db_path
        # A single long-lived connection: required so a ":memory:" database
        # survives across calls.
        self._conn = sqlite3.connect(db_path)
        self._conn.row_factory = sqlite3.Row
        # Enforce referential integrity (must be set per connection).
        self._conn.execute("PRAGMA foreign_keys = ON;")
        self._init_schema()

    # ------------------------------------------------------------------
    # Initialization
    # ------------------------------------------------------------------

    def _init_schema(self) -> None:
        """Apply the migration once; idempotent if tables already exist."""

        row = self._conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='memories';"
        ).fetchone()
        if row is not None:
            return  # already migrated
        self._conn.executescript(_find_migration_sql())
        self._conn.commit()

    def close(self) -> None:
        """Close the underlying connection."""

        self._conn.close()

    # ------------------------------------------------------------------
    # Writes
    # ------------------------------------------------------------------

    def add(self, record: MemoryRecord) -> MemoryRecord:
        validate(record)
        try:
            with self._conn:
                cur = self._conn.execute(
                    """
                    INSERT INTO memories (
                        memory_id, content, source_type, source_ref, scope, scope_ref,
                        created_at, updated_at, expires_at, trust_score, sensitivity,
                        status, confirmations, tags, metadata
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    self._record_columns(record),
                )
                rowid = cur.lastrowid
                self._sync_fts_insert(rowid, record)
                self._write_embedding(record)
                self._write_contradictions(record)
        except sqlite3.IntegrityError as exc:
            raise ValueError(
                f"memory_id {record.memory_id!r} already exists or violates a constraint"
            ) from exc
        return record

    def update(self, record: MemoryRecord) -> MemoryRecord:
        validate(record)
        existing = self._conn.execute(
            "SELECT rowid, content, tags FROM memories WHERE memory_id = ?;",
            (record.memory_id,),
        ).fetchone()
        if existing is None:
            raise ValueError(
                f"cannot update: no memory with memory_id {record.memory_id!r}"
            )
        rowid = existing["rowid"]
        try:
            with self._conn:
                self._conn.execute(
                    """
                    UPDATE memories SET
                        content = ?, source_type = ?, source_ref = ?, scope = ?,
                        scope_ref = ?, created_at = ?, updated_at = ?, expires_at = ?,
                        trust_score = ?, sensitivity = ?, status = ?, confirmations = ?,
                        tags = ?, metadata = ?
                    WHERE memory_id = ?
                    """,
                    self._record_columns(record)[1:] + (record.memory_id,),
                )
                # Refresh FTS: delete the old indexed row, then re-insert.
                self._sync_fts_delete(rowid, existing["content"], existing["tags"])
                self._sync_fts_insert(rowid, record)
                # Refresh embedding.
                self._conn.execute(
                    "DELETE FROM memory_embeddings WHERE memory_id = ?;",
                    (record.memory_id,),
                )
                self._write_embedding(record)
                # Refresh contradictions.
                self._conn.execute(
                    "DELETE FROM memory_contradictions WHERE memory_id = ?;",
                    (record.memory_id,),
                )
                self._write_contradictions(record)
        except sqlite3.IntegrityError as exc:
            raise ValueError(
                f"update of memory_id {record.memory_id!r} violates a constraint"
            ) from exc
        return record

    def soft_delete(self, memory_id: str) -> None:
        with self._conn:
            self._conn.execute(
                "UPDATE memories SET status = ? WHERE memory_id = ?;",
                (MemoryStatus.DELETED.value, memory_id),
            )

    # ------------------------------------------------------------------
    # Reads
    # ------------------------------------------------------------------

    def get(self, memory_id: str) -> Optional[MemoryRecord]:
        row = self._conn.execute(
            "SELECT * FROM memories WHERE memory_id = ?;", (memory_id,)
        ).fetchone()
        if row is None:
            return None
        return self._hydrate(row)

    def list(
        self,
        *,
        scope: Optional[Scope] = None,
        scope_ref: Optional[str] = None,
        status: Optional[MemoryStatus] = None,
    ) -> list[MemoryRecord]:
        clauses: list[str] = []
        params: list[object] = []
        if scope is not None:
            clauses.append("scope = ?")
            params.append(scope.value)
        if scope_ref is not None:
            clauses.append("scope_ref = ?")
            params.append(scope_ref)
        if status is not None:
            clauses.append("status = ?")
            params.append(status.value)
        sql = "SELECT * FROM memories"
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY rowid;"
        rows = self._conn.execute(sql, params).fetchall()
        return [self._hydrate(row) for row in rows]

    def keyword_search(self, query: str, limit: int) -> list[tuple[MemoryRecord, float]]:
        if limit <= 0:
            return []
        # Quote each token as an FTS5 phrase so operator-like / special input
        # (e.g. unbalanced quotes, AND/OR, parentheses) can never raise an FTS5
        # syntax error. An empty/whitespace-only query yields no matches.
        match = _escape_fts_query(query)
        if not match:
            return []
        rows = self._conn.execute(
            """
            SELECT m.*, bm25(memory_fts) AS rank
            FROM memory_fts
            JOIN memories m ON m.rowid = memory_fts.rowid
            WHERE memory_fts MATCH ?
            ORDER BY rank
            LIMIT ?
            """,
            (match, limit),
        ).fetchall()
        # bm25 returns lower (more negative) for better matches; negate so a
        # larger score means more relevant, preserving descending-relevance order.
        return [(self._hydrate(row), -float(row["rank"])) for row in rows]

    def vector_search(
        self, embedding: list[float], limit: int
    ) -> list[tuple[MemoryRecord, float]]:
        # An empty query embedding (or non-positive limit) has nothing to compare
        # against -> no results. Stored vectors whose dimension differs from the
        # query's are skipped (the retrieval layer surfaces an explicit
        # DimensionMismatchError when that conflict is relevant).
        query_dim = len(embedding)
        if query_dim == 0 or limit <= 0:
            return []
        rows = self._conn.execute(
            """
            SELECT m.*, e.dim AS emb_dim, e.vector AS emb_vector
            FROM memory_embeddings e
            JOIN memories m ON m.memory_id = e.memory_id
            """
        ).fetchall()
        scored: list[tuple[MemoryRecord, float]] = []
        for row in rows:
            stored_dim = int(row["emb_dim"])
            if stored_dim != query_dim:
                # Dimension mismatch: skip rather than raise so a single
                # incompatible vector cannot break search.
                continue
            candidate_vec = _unpack_embedding(row["emb_vector"])
            score = _cosine_similarity(embedding, candidate_vec)
            scored.append((self._hydrate(row), score))
        scored.sort(key=lambda pair: pair[1], reverse=True)
        return scored[:limit]

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _record_columns(record: MemoryRecord) -> tuple:
        """Return the ``memories`` column tuple in INSERT order."""

        return (
            record.memory_id,
            record.content,
            record.source_type.value,
            record.source_ref,
            record.scope.value,
            record.scope_ref,
            _iso(record.created_at),
            _iso(record.updated_at),
            _iso(record.expires_at),
            float(record.trust_score),
            record.sensitivity.value,
            record.status.value,
            int(record.confirmations),
            json.dumps(record.tags),
            json.dumps(record.metadata),
        )

    def _sync_fts_insert(self, rowid: int, record: MemoryRecord) -> None:
        self._conn.execute(
            "INSERT INTO memory_fts(rowid, content, tags) VALUES (?, ?, ?);",
            (rowid, record.content, json.dumps(record.tags)),
        )

    def _sync_fts_delete(self, rowid: int, content: str, tags: str) -> None:
        # External-content FTS5 tables are kept in sync with the 'delete' command.
        self._conn.execute(
            "INSERT INTO memory_fts(memory_fts, rowid, content, tags) "
            "VALUES ('delete', ?, ?, ?);",
            (rowid, content, tags),
        )

    def _write_embedding(self, record: MemoryRecord) -> None:
        if record.embedding is None:
            return
        self._conn.execute(
            "INSERT INTO memory_embeddings(memory_id, dim, vector) VALUES (?, ?, ?);",
            (record.memory_id, len(record.embedding), _pack_embedding(record.embedding)),
        )

    def _write_contradictions(self, record: MemoryRecord) -> None:
        if not record.contradicts:
            return
        detected = _iso(datetime.now(timezone.utc))
        self._conn.executemany(
            "INSERT INTO memory_contradictions(memory_id, contradicts_id, detected_at, reason) "
            "VALUES (?, ?, ?, NULL);",
            [(record.memory_id, cid, detected) for cid in record.contradicts],
        )

    def _load_embedding(self, memory_id: str) -> Optional[list[float]]:
        row = self._conn.execute(
            "SELECT vector FROM memory_embeddings WHERE memory_id = ?;", (memory_id,)
        ).fetchone()
        if row is None:
            return None
        return _unpack_embedding(row["vector"])

    def _load_contradictions(self, memory_id: str) -> list[str]:
        rows = self._conn.execute(
            "SELECT contradicts_id FROM memory_contradictions "
            "WHERE memory_id = ? ORDER BY rowid;",
            (memory_id,),
        ).fetchall()
        return [row["contradicts_id"] for row in rows]

    def _hydrate(self, row: sqlite3.Row) -> MemoryRecord:
        """Reconstruct a full :class:`MemoryRecord` from a ``memories`` row."""

        memory_id = row["memory_id"]
        return MemoryRecord(
            memory_id=memory_id,
            content=row["content"],
            source_type=SourceType(row["source_type"]),
            source_ref=row["source_ref"],
            scope=Scope(row["scope"]),
            scope_ref=row["scope_ref"],
            created_at=_from_iso(row["created_at"]),
            updated_at=_from_iso(row["updated_at"]),
            expires_at=_from_iso(row["expires_at"]),
            trust_score=row["trust_score"],
            sensitivity=Sensitivity(row["sensitivity"]),
            status=MemoryStatus(row["status"]),
            contradicts=self._load_contradictions(memory_id),
            tags=json.loads(row["tags"]),
            confirmations=row["confirmations"],
            embedding=self._load_embedding(memory_id),
            metadata=json.loads(row["metadata"]),
        )
