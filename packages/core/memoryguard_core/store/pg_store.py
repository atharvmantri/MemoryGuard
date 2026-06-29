# SPDX-License-Identifier: Apache-2.0
"""PostgreSQL + pgvector :class:`MemoryStore` for MemoryGuard cloud mode.

``PostgresStore`` is the cloud-mode counterpart to ``SqliteStore``. It persists
:class:`~memoryguard_core.models.MemoryRecord` instances to a PostgreSQL database
using the ``pgvector`` and ``pg_trgm`` extensions and honors the **exact same**
:class:`~memoryguard_core.store.base.MemoryStore` contract and validation rules
as the SQLite backend:

* CRUD with :func:`~memoryguard_core.models.validate` enforced on every write.
* Soft-delete: ``soft_delete`` sets ``status = DELETED`` and keeps the record
  retrievable via :meth:`get` (never hard-drops by default).
* Keyword search via the ``pg_trgm`` trigram similarity operator (GIN-indexed).
* Vector search via native ``pgvector`` cosine distance (ivfflat-indexed),
  returning cosine *similarity* scores in descending order.

Schema notes
------------
The backend's schema is ``infra/migrations/postgres/0001_init.sql``:

* Embeddings are stored **inline** in the ``memories`` table as a
  ``vector(384)`` column (dimension matches the OSS ``LocalEmbedder``). pgvector
  stores single-precision (``float4``) components, so embeddings round-trip at
  ``float32`` precision — identical to the SQLite backend's packed-``float32``
  storage.
* ``tags`` is a native ``TEXT[]`` array; ``metadata`` is ``JSONB``; timestamps
  are ``TIMESTAMPTZ`` (timezone-aware, normalized to UTC on read).
* ``contradicts`` pointers are stored in the ``memory_contradictions`` table,
  exactly as in the SQLite backend.

Open-core boundary
------------------
This module is part of the Apache-2.0 OSS core. It MUST NOT import from any
commercial package. The third-party ``psycopg`` (PostgreSQL driver) dependency
is imported **lazily** inside :class:`PostgresStore` so the OSS core remains
importable — and the SQLite backend fully usable — when ``psycopg`` is not
installed. ``PostgresStore`` is only constructed when the ``cloud_store`` feature
flag selects the PostgreSQL backend.
"""

from __future__ import annotations

import math
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

__all__ = ["PostgresStore"]


# Relative location of the canonical Postgres migration within the repository.
_MIGRATION_RELATIVE = Path("infra") / "migrations" / "postgres" / "0001_init.sql"

# Dimension of the inline ``vector(384)`` embedding column. Matches the OSS
# LocalEmbedder. A query embedding whose dimension differs from this cannot be
# compared against the fixed-width column, so :meth:`PostgresStore.vector_search`
# returns no results for it (parallels the SQLite backend's per-row skip).
DEFAULT_EMBEDDING_DIM = 384


# ---------------------------------------------------------------------------
# Pure serialization helpers (no DB / driver required — unit-testable)
# ---------------------------------------------------------------------------


def _format_vector(embedding: list[float]) -> str:
    """Render a vector as a pgvector text literal, e.g. ``"[0.5,-0.25,0.75]"``.

    Components are emitted with full ``float`` precision; pgvector parses the
    literal and stores single-precision (``float4``) values.
    """

    return "[" + ",".join(repr(float(x)) for x in embedding) + "]"


def _parse_vector(value: object) -> Optional[list[float]]:
    """Parse a pgvector column value back into a ``list[float]`` (or ``None``).

    Accepts the textual form pgvector returns when its Python adapter is not
    registered (``"[1,2,3]"``) as well as an already-parsed sequence (e.g. a
    list/tuple or numpy array when ``pgvector.psycopg`` is registered).
    """

    if value is None:
        return None
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        text = text.strip("[]")
        if not text:
            return []
        return [float(part) for part in text.split(",")]
    # Already a sequence (list/tuple/ndarray): coerce element-wise.
    return [float(x) for x in value]


def _normalize_dt(value: Optional[datetime]) -> Optional[datetime]:
    """Normalize a datetime read from the DB to a UTC, timezone-aware value."""

    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    """Cosine similarity of two equal-length vectors (0.0 if either is zero).

    Used only as a defensive fallback for scoring; the primary search path uses
    pgvector's native ``<=>`` cosine-distance operator.
    """

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


def _find_migration_sql() -> Optional[str]:
    """Return the Postgres migration SQL from the repo tree, or ``None``.

    Walks parent directories looking for
    ``infra/migrations/postgres/0001_init.sql``. Returns ``None`` when it cannot
    be located (e.g. an installed wheel without the repo's ``infra/`` tree), in
    which case the caller is expected to have applied the migration out-of-band.
    """

    here = Path(__file__).resolve()
    for parent in here.parents:
        candidate = parent / _MIGRATION_RELATIVE
        if candidate.is_file():
            return candidate.read_text(encoding="utf-8")
    return None


class PostgresStore(MemoryStore):
    """A :class:`MemoryStore` backed by PostgreSQL + pgvector (cloud mode).

    Parameters
    ----------
    dsn:
        PostgreSQL connection string (libpq DSN or URL), e.g.
        ``"postgresql://user:pass@host:5432/dbname"``. Ignored when an existing
        ``connection`` is supplied.
    connection:
        An optional pre-opened ``psycopg`` connection to use instead of opening a
        new one from ``dsn`` (useful for tests and connection pooling).
    embedding_dim:
        Dimension of the inline embedding column (default ``384``, matching the
        ``0001_init.sql`` schema). Query embeddings of a different dimension yield
        no vector-search results.
    apply_migration:
        When ``True`` (default), apply ``0001_init.sql`` if the ``memories`` table
        does not yet exist. Set ``False`` when migrations are managed externally.

    Notes
    -----
    ``psycopg`` (v3) is imported lazily here so the OSS core stays importable
    without the PostgreSQL driver installed.
    """

    def __init__(
        self,
        dsn: Optional[str] = None,
        *,
        connection: Optional[object] = None,
        embedding_dim: int = DEFAULT_EMBEDDING_DIM,
        apply_migration: bool = True,
    ) -> None:
        try:
            import psycopg
            from psycopg.rows import dict_row
            from psycopg.types.json import Jsonb
        except ImportError as exc:  # pragma: no cover - exercised only w/o driver
            raise ImportError(
                "PostgresStore requires the 'psycopg' package (PostgreSQL driver). "
                "Install it with `pip install psycopg[binary]` to use the cloud "
                "store backend; the SQLite backend has no such dependency."
            ) from exc

        self._psycopg = psycopg
        self._dict_row = dict_row
        self._Jsonb = Jsonb
        self.embedding_dim = int(embedding_dim)

        if connection is not None:
            self._conn = connection
        else:
            if dsn is None:
                raise ValueError("PostgresStore requires a dsn or an existing connection")
            self._conn = psycopg.connect(dsn)

        if apply_migration:
            self._init_schema()

    # ------------------------------------------------------------------
    # Initialization
    # ------------------------------------------------------------------

    def _table_exists(self, name: str) -> bool:
        with self._conn.cursor() as cur:
            cur.execute("SELECT to_regclass(%s)", (f"public.{name}",))
            row = cur.fetchone()
        return row is not None and row[0] is not None

    def _init_schema(self) -> None:
        """Apply ``0001_init.sql`` once; idempotent if ``memories`` exists."""

        if self._table_exists("memories"):
            return
        sql = _find_migration_sql()
        if sql is None:  # pragma: no cover - depends on deployment layout
            raise RuntimeError(
                "Postgres migration 0001_init.sql not found and apply_migration "
                "was requested; apply migrations out-of-band or pass "
                "apply_migration=False."
            )
        with self._conn.cursor() as cur:
            cur.execute(sql)
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
            with self._conn.transaction():
                with self._conn.cursor() as cur:
                    cur.execute(
                        """
                        INSERT INTO memories (
                            memory_id, content, source_type, source_ref, scope,
                            scope_ref, created_at, updated_at, expires_at,
                            trust_score, sensitivity, status, confirmations,
                            tags, embedding, metadata
                        ) VALUES (
                            %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                            %s, %s::vector, %s
                        )
                        """,
                        self._record_params(record),
                    )
                    self._write_contradictions(cur, record)
        except self._psycopg.errors.UniqueViolation as exc:
            raise ValueError(
                f"memory_id {record.memory_id!r} already exists"
            ) from exc
        except self._psycopg.errors.IntegrityError as exc:
            raise ValueError(
                f"add of memory_id {record.memory_id!r} violates a constraint"
            ) from exc
        return record

    def update(self, record: MemoryRecord) -> MemoryRecord:
        validate(record)
        if self.get(record.memory_id) is None:
            raise ValueError(
                f"cannot update: no memory with memory_id {record.memory_id!r}"
            )
        params = self._record_params(record)
        # UPDATE order: all columns except memory_id, then memory_id in WHERE.
        update_params = params[1:] + (record.memory_id,)
        try:
            with self._conn.transaction():
                with self._conn.cursor() as cur:
                    cur.execute(
                        """
                        UPDATE memories SET
                            content = %s, source_type = %s, source_ref = %s,
                            scope = %s, scope_ref = %s, created_at = %s,
                            updated_at = %s, expires_at = %s, trust_score = %s,
                            sensitivity = %s, status = %s, confirmations = %s,
                            tags = %s, embedding = %s::vector, metadata = %s
                        WHERE memory_id = %s
                        """,
                        update_params,
                    )
                    # Refresh contradiction pointers for this record.
                    cur.execute(
                        "DELETE FROM memory_contradictions WHERE memory_id = %s",
                        (record.memory_id,),
                    )
                    self._write_contradictions(cur, record)
        except self._psycopg.errors.IntegrityError as exc:
            raise ValueError(
                f"update of memory_id {record.memory_id!r} violates a constraint"
            ) from exc
        return record

    def soft_delete(self, memory_id: str) -> None:
        with self._conn.transaction():
            with self._conn.cursor() as cur:
                cur.execute(
                    "UPDATE memories SET status = %s WHERE memory_id = %s",
                    (MemoryStatus.DELETED.value, memory_id),
                )

    # ------------------------------------------------------------------
    # Reads
    # ------------------------------------------------------------------

    def get(self, memory_id: str) -> Optional[MemoryRecord]:
        with self._conn.cursor(row_factory=self._dict_row) as cur:
            cur.execute(
                "SELECT * FROM memories WHERE memory_id = %s", (memory_id,)
            )
            row = cur.fetchone()
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
            clauses.append("scope = %s")
            params.append(scope.value)
        if scope_ref is not None:
            clauses.append("scope_ref = %s")
            params.append(scope_ref)
        if status is not None:
            clauses.append("status = %s")
            params.append(status.value)
        sql = "SELECT * FROM memories"
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        # Stable, deterministic ordering across calls (no rowid in Postgres).
        sql += " ORDER BY created_at, memory_id"
        with self._conn.cursor(row_factory=self._dict_row) as cur:
            cur.execute(sql, params)
            rows = cur.fetchall()
        return [self._hydrate(row) for row in rows]

    def keyword_search(self, query: str, limit: int) -> list[tuple[MemoryRecord, float]]:
        if limit <= 0:
            return []
        if not query or not query.strip():
            return []
        # pg_trgm trigram match: the `%` operator (GIN-indexed via
        # idx_mem_content_trgm) gates candidates by the configured similarity
        # threshold; `similarity()` provides the descending relevance score.
        # `%%` is the escaped literal `%` operator under psycopg's %s paramstyle.
        with self._conn.cursor(row_factory=self._dict_row) as cur:
            cur.execute(
                """
                SELECT *, similarity(content, %s) AS score
                FROM memories
                WHERE content %% %s
                ORDER BY score DESC, memory_id
                LIMIT %s
                """,
                (query, query, limit),
            )
            rows = cur.fetchall()
        return [(self._hydrate(row), float(row["score"])) for row in rows]

    def vector_search(
        self, embedding: list[float], limit: int
    ) -> list[tuple[MemoryRecord, float]]:
        query_dim = len(embedding)
        # No comparable target (empty query / non-positive limit), or a query
        # dimension that cannot match the fixed-width column -> no results.
        if query_dim == 0 or limit <= 0 or query_dim != self.embedding_dim:
            return []
        literal = _format_vector(embedding)
        # Native pgvector cosine distance via `<=>` (ivfflat idx_mem_embedding).
        # Score is cosine *similarity* = 1 - cosine_distance, ordered descending.
        with self._conn.cursor(row_factory=self._dict_row) as cur:
            cur.execute(
                """
                SELECT *, 1 - (embedding <=> %s::vector) AS score
                FROM memories
                WHERE embedding IS NOT NULL
                ORDER BY embedding <=> %s::vector
                LIMIT %s
                """,
                (literal, literal, limit),
            )
            rows = cur.fetchall()
        return [(self._hydrate(row), float(row["score"])) for row in rows]

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _record_params(self, record: MemoryRecord) -> tuple:
        """Return the ``memories`` column tuple in INSERT order.

        ``metadata`` is wrapped in :class:`psycopg.types.json.Jsonb`; the
        embedding is rendered as a pgvector text literal (or ``None`` for the
        ``NULL`` cast); ``tags`` is passed as a native list for ``TEXT[]``.
        """

        embedding_literal = (
            _format_vector(record.embedding) if record.embedding is not None else None
        )
        return (
            record.memory_id,
            record.content,
            record.source_type.value,
            record.source_ref,
            record.scope.value,
            record.scope_ref,
            record.created_at,
            record.updated_at,
            record.expires_at,
            float(record.trust_score),
            record.sensitivity.value,
            record.status.value,
            int(record.confirmations),
            list(record.tags),
            embedding_literal,
            self._Jsonb(record.metadata),
        )

    def _write_contradictions(self, cur: object, record: MemoryRecord) -> None:
        if not record.contradicts:
            return
        detected = datetime.now(timezone.utc)
        cur.executemany(
            "INSERT INTO memory_contradictions "
            "(memory_id, contradicts_id, detected_at, reason) "
            "VALUES (%s, %s, %s, NULL)",
            [(record.memory_id, cid, detected) for cid in record.contradicts],
        )

    def _load_contradictions(self, memory_id: str) -> list[str]:
        with self._conn.cursor() as cur:
            cur.execute(
                "SELECT contradicts_id FROM memory_contradictions "
                "WHERE memory_id = %s ORDER BY detected_at, contradicts_id",
                (memory_id,),
            )
            rows = cur.fetchall()
        return [str(row[0]) for row in rows]

    def _hydrate(self, row: dict) -> MemoryRecord:
        """Reconstruct a full :class:`MemoryRecord` from a ``memories`` row."""

        memory_id = str(row["memory_id"])
        tags = row["tags"]
        metadata = row["metadata"]
        return MemoryRecord(
            memory_id=memory_id,
            content=row["content"],
            source_type=SourceType(row["source_type"]),
            source_ref=row["source_ref"],
            scope=Scope(row["scope"]),
            scope_ref=row["scope_ref"],
            created_at=_normalize_dt(row["created_at"]),
            updated_at=_normalize_dt(row["updated_at"]),
            expires_at=_normalize_dt(row["expires_at"]),
            trust_score=float(row["trust_score"]),
            sensitivity=Sensitivity(row["sensitivity"]),
            status=MemoryStatus(row["status"]),
            contradicts=self._load_contradictions(memory_id),
            tags=list(tags) if tags is not None else [],
            confirmations=int(row["confirmations"]),
            embedding=_parse_vector(row["embedding"]),
            metadata=dict(metadata) if metadata is not None else {},
        )
