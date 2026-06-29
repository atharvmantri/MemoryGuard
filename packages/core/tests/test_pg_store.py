# SPDX-License-Identifier: Apache-2.0
"""Unit + contract tests for memoryguard_core.store.pg_store.PostgresStore.

Two test groups:

* **Pure helper unit tests** — exercise the driver-free serialization helpers
  (``_format_vector``, ``_parse_vector``, ``_normalize_dt``,
  ``_cosine_similarity``) and verify the module imports without ``psycopg``.
  These always run.
* **Backend contract tests** — run ``PostgresStore`` against a live PostgreSQL
  server (with the ``vector`` + ``pg_trgm`` extensions) covering the same
  MemoryStore contract as the SQLite backend: round-trip fidelity (Property 20),
  soft-delete invariant (Property 21), not-found, uniqueness, list filters,
  trigram keyword search, and pgvector vector search. These **skip** gracefully
  when ``psycopg`` is unavailable or no server is reachable, so the suite never
  fails in an environment without PostgreSQL.

Point the contract tests at a server via ``MEMORYGUARD_TEST_PG_DSN`` (a libpq
URL/DSN). The target database must allow ``CREATE EXTENSION vector`` and
``CREATE EXTENSION pg_trgm``.

Requirements: 18.1, 18.4, 18.6, 2.1, 2.2, 2.9.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone

import pytest

from memoryguard_core.models import (
    MemoryStatus,
    Scope,
    Sensitivity,
    SourceType,
    new_memory_record,
)
from memoryguard_core.store.pg_store import (
    DEFAULT_EMBEDDING_DIM,
    PostgresStore,
    _cosine_similarity,
    _format_vector,
    _normalize_dt,
    _parse_vector,
)


# ---------------------------------------------------------------------------
# Pure helper unit tests (no driver / DB required)
# ---------------------------------------------------------------------------


def test_postgres_store_is_memory_store_subclass() -> None:
    from memoryguard_core.store.base import MemoryStore

    assert issubclass(PostgresStore, MemoryStore)


def test_format_vector_basic() -> None:
    assert _format_vector([0.5, -0.25, 0.75]) == "[0.5,-0.25,0.75]"


def test_format_vector_coerces_ints() -> None:
    assert _format_vector([1, 2, 3]) == "[1.0,2.0,3.0]"


def test_parse_vector_from_text() -> None:
    assert _parse_vector("[0.5,-0.25,0.75]") == [0.5, -0.25, 0.75]


def test_parse_vector_from_sequence() -> None:
    assert _parse_vector([1, 2, 3]) == [1.0, 2.0, 3.0]


def test_parse_vector_none_and_empty() -> None:
    assert _parse_vector(None) is None
    assert _parse_vector("") is None
    assert _parse_vector("[]") == []


def test_format_parse_round_trip() -> None:
    vec = [0.5, -0.25, 0.75, 0.0]  # float32-exact
    assert _parse_vector(_format_vector(vec)) == vec


def test_normalize_dt_naive_becomes_utc() -> None:
    naive = datetime(2024, 1, 1, 12, 0, 0)
    out = _normalize_dt(naive)
    assert out.tzinfo is not None
    assert out == datetime(2024, 1, 1, 12, 0, 0, tzinfo=timezone.utc)


def test_normalize_dt_none() -> None:
    assert _normalize_dt(None) is None


def test_cosine_similarity_identical_is_one() -> None:
    assert _cosine_similarity([1.0, 0.0], [1.0, 0.0]) == pytest.approx(1.0)


def test_cosine_similarity_zero_vector_is_zero() -> None:
    assert _cosine_similarity([0.0, 0.0], [1.0, 1.0]) == 0.0


def test_default_embedding_dim_matches_local_embedder() -> None:
    assert DEFAULT_EMBEDDING_DIM == 384


# ---------------------------------------------------------------------------
# Backend contract tests (skipped without a reachable PostgreSQL server)
# ---------------------------------------------------------------------------

_PG_DSN = os.environ.get("MEMORYGUARD_TEST_PG_DSN")


def _pg_skip_reason() -> str:
    """Return '' when a usable PostgreSQL backend is reachable, else a reason."""

    if not _PG_DSN:
        return "MEMORYGUARD_TEST_PG_DSN not set (no PostgreSQL target configured)"
    try:
        import psycopg  # noqa: F401
    except ImportError:
        return "psycopg driver not installed"
    try:
        conn = psycopg.connect(_PG_DSN, connect_timeout=3)
        conn.close()
    except Exception as exc:  # pragma: no cover - environment-dependent
        return f"PostgreSQL not reachable: {exc}"
    return ""


_SKIP_REASON = _pg_skip_reason()

# Only the live-backend contract tests below are gated on a reachable server;
# the pure helper unit tests above always run. The `store` fixture performs the
# skip, so every test that requests it is skipped when no server is reachable.


def _make(**overrides):
    kwargs = {
        "content": "the api key rotates every 30 days",
        "source_type": SourceType.USER,
        "source_ref": "user://alice",
        "scope": Scope.GLOBAL,
    }
    kwargs.update(overrides)
    return new_memory_record(**kwargs)


@pytest.fixture()
def store():
    """A PostgresStore on the configured server, truncated before each test.

    Skips the requesting test when no PostgreSQL backend is reachable so the
    suite never fails in an environment without PostgreSQL.
    """

    if _SKIP_REASON:
        pytest.skip(_SKIP_REASON)
    s = PostgresStore(_PG_DSN)
    with s._conn.cursor() as cur:
        cur.execute("TRUNCATE memories, memory_contradictions CASCADE")
    s._conn.commit()
    yield s
    s.close()


def _emb(*values: float) -> list[float]:
    """Build a DEFAULT_EMBEDDING_DIM vector with leading components set."""

    vec = [0.0] * DEFAULT_EMBEDDING_DIM
    for i, v in enumerate(values):
        vec[i] = v
    return vec


# --- round-trip (Property 20) ----------------------------------------------


def test_round_trip_simple(store) -> None:
    rec = _make()
    stored = store.add(rec)
    assert store.get(stored.memory_id) == stored


def test_round_trip_all_fields(store) -> None:
    created = datetime(2024, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
    rec = _make(
        content="rich record",
        source_type=SourceType.FILE,
        source_ref="repo://README.md@commit123",
        scope=Scope.PROJECT,
        scope_ref="proj-42",
        trust_score=0.75,
        sensitivity=Sensitivity.SECRET,
        tags=["alpha", "beta"],
        confirmations=3,
        embedding=_emb(0.5, -0.25, 0.75),
        metadata={"k": "v", "n": 7, "nested": {"x": [1, 2]}},
        now=created,
    )
    stored = store.add(rec)
    fetched = store.get(stored.memory_id)
    assert fetched == stored
    assert fetched.tags == ["alpha", "beta"]
    assert fetched.metadata == {"k": "v", "n": 7, "nested": {"x": [1, 2]}}
    assert fetched.embedding[:3] == [0.5, -0.25, 0.75]


def test_round_trip_preserves_contradicts(store) -> None:
    target = store.add(_make(content="x is true"))
    stored = store.add(_make(content="x is false", contradicts=[target.memory_id]))
    fetched = store.get(stored.memory_id)
    assert fetched.contradicts == [target.memory_id]


def test_round_trip_no_embedding_is_none(store) -> None:
    stored = store.add(_make(embedding=None))
    assert store.get(stored.memory_id).embedding is None


# --- not-found & uniqueness -------------------------------------------------


def test_get_missing_returns_none(store) -> None:
    assert store.get("f47ac10b-58cc-4372-a567-0e02b2c3d479") is None


def test_duplicate_memory_id_rejected(store) -> None:
    rec = _make()
    store.add(rec)
    with pytest.raises(ValueError):
        store.add(rec)


# --- soft-delete (Property 21) ---------------------------------------------


def test_soft_delete_keeps_record_retrievable(store) -> None:
    stored = store.add(_make())
    store.soft_delete(stored.memory_id)
    fetched = store.get(stored.memory_id)
    assert fetched is not None
    assert fetched.status == MemoryStatus.DELETED


def test_soft_delete_listed_only_with_status_filter(store) -> None:
    stored = store.add(_make())
    store.soft_delete(stored.memory_id)
    deleted = store.list(status=MemoryStatus.DELETED)
    assert [r.memory_id for r in deleted] == [stored.memory_id]
    active_ids = [r.memory_id for r in store.list(status=MemoryStatus.ACTIVE)]
    assert stored.memory_id not in active_ids


# --- update -----------------------------------------------------------------


def test_update_persists_changes(store) -> None:
    stored = store.add(_make(content="old", embedding=_emb(0.5, 0.5)))
    stored.content = "new content"
    stored.trust_score = 0.9
    stored.embedding = _emb(0.25, -0.25)
    store.update(stored)
    fetched = store.get(stored.memory_id)
    assert fetched.content == "new content"
    assert fetched.trust_score == 0.9
    assert fetched.embedding[:2] == [0.25, -0.25]


def test_update_unknown_raises(store) -> None:
    with pytest.raises(ValueError):
        store.update(_make())


# --- list filters -----------------------------------------------------------


def test_list_filters_by_scope_and_scope_ref(store) -> None:
    a = store.add(_make(scope=Scope.PROJECT, scope_ref="p1", content="a"))
    b = store.add(_make(scope=Scope.PROJECT, scope_ref="p2", content="b"))
    store.add(_make(scope=Scope.GLOBAL, content="c"))

    proj = store.list(scope=Scope.PROJECT)
    assert {r.memory_id for r in proj} == {a.memory_id, b.memory_id}

    p1 = store.list(scope=Scope.PROJECT, scope_ref="p1")
    assert [r.memory_id for r in p1] == [a.memory_id]


def test_list_no_filters_returns_all(store) -> None:
    store.add(_make(content="one"))
    store.add(_make(content="two"))
    assert len(store.list()) == 2


# --- keyword search (pg_trgm) ----------------------------------------------


def test_keyword_search_matches(store) -> None:
    db = store.add(_make(content="the database connection pool is exhausted"))
    store.add(_make(content="the weather is sunny today and warm"))
    results = store.keyword_search("database connection", limit=10)
    ids = [r.memory_id for r, _ in results]
    assert db.memory_id in ids
    assert all(isinstance(score, float) for _, score in results)


def test_keyword_search_zero_limit(store) -> None:
    store.add(_make(content="anything"))
    assert store.keyword_search("anything", limit=0) == []


def test_keyword_search_empty_query(store) -> None:
    store.add(_make(content="anything"))
    assert store.keyword_search("   ", limit=5) == []


# --- vector search (pgvector) ----------------------------------------------


def test_vector_search_orders_by_cosine_similarity(store) -> None:
    near = store.add(_make(content="near", embedding=_emb(1.0, 0.0, 0.0)))
    mid = store.add(_make(content="mid", embedding=_emb(0.5, 0.5, 0.0)))
    far = store.add(_make(content="far", embedding=_emb(0.0, 0.0, 1.0)))

    results = store.vector_search(_emb(1.0, 0.0, 0.0), limit=3)
    ordered = [r.memory_id for r, _ in results]
    assert ordered[0] == near.memory_id
    assert ordered == [near.memory_id, mid.memory_id, far.memory_id]
    assert results[0][1] == pytest.approx(1.0, abs=1e-4)


def test_vector_search_respects_limit(store) -> None:
    store.add(_make(content="a", embedding=_emb(1.0, 0.0)))
    store.add(_make(content="b", embedding=_emb(0.0, 1.0)))
    store.add(_make(content="c", embedding=_emb(1.0, 1.0)))
    assert len(store.vector_search(_emb(1.0, 0.0), limit=2)) == 2


def test_vector_search_skips_records_without_embeddings(store) -> None:
    with_vec = store.add(_make(content="has vec", embedding=_emb(1.0, 0.0)))
    store.add(_make(content="no vec", embedding=None))
    results = store.vector_search(_emb(1.0, 0.0), limit=10)
    assert [r.memory_id for r, _ in results] == [with_vec.memory_id]


def test_vector_search_empty_query_returns_empty(store) -> None:
    store.add(_make(embedding=_emb(1.0, 0.0)))
    assert store.vector_search([], limit=5) == []


def test_vector_search_dimension_mismatch_returns_empty(store) -> None:
    store.add(_make(embedding=_emb(1.0, 0.0)))
    # Query dimension != column dimension -> no results (parallels SQLite skip).
    assert store.vector_search([1.0, 0.0, 0.0], limit=5) == []
