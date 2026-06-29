# SPDX-License-Identifier: Apache-2.0
"""Unit / contract tests for memoryguard_core.store.sqlite_store.SqliteStore.

Covers the Phase-1 SQLite backend against the MemoryStore contract and the
design's store properties:

* Property 20 — round-trip fidelity: ``add`` then ``get`` returns an equal record.
* Property 21 — soft-delete invariant: after ``soft_delete`` status is DELETED and
  the record is still retrievable by id.

Plus: not-found returns ``None``, unique ``memory_id`` enforcement, the
``trust_score`` CHECK constraint at the DB level, ``list`` filters, FTS5
keyword search, and Python-side cosine ``vector_search``.

Requirements: 1.1, 1.3, 1.4, 2.1, 2.2, 2.9, 9.3.

Standard library + pytest only (no Hypothesis). Embeddings use float32-exact
values so blob packing round-trips byte-for-byte.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

import pytest

from memoryguard_core.models import (
    MemoryStatus,
    Scope,
    Sensitivity,
    SourceType,
    new_memory_record,
)
from memoryguard_core.store import MemoryStore, SqliteStore


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


@pytest.fixture()
def store() -> SqliteStore:
    """An in-memory SqliteStore with the schema applied."""

    s = SqliteStore(":memory:")
    yield s
    s.close()


def _make(**overrides):
    """Build a valid MemoryRecord via the factory, with overrides."""

    kwargs = {
        "content": "the api key rotates every 30 days",
        "source_type": SourceType.USER,
        "source_ref": "user://alice",
        "scope": Scope.GLOBAL,
    }
    kwargs.update(overrides)
    return new_memory_record(**kwargs)


# ---------------------------------------------------------------------------
# Construction
# ---------------------------------------------------------------------------


def test_subclass_of_memory_store(store: SqliteStore) -> None:
    assert isinstance(store, MemoryStore)


def test_creates_database_file(tmp_path) -> None:
    db_path = tmp_path / "memguard.db"
    s = SqliteStore(str(db_path))
    try:
        assert db_path.exists()
    finally:
        s.close()


def test_foreign_keys_enabled(store: SqliteStore) -> None:
    cur = store._conn.execute("PRAGMA foreign_keys")
    assert cur.fetchone()[0] == 1


# ---------------------------------------------------------------------------
# Property 20 — round-trip fidelity
# ---------------------------------------------------------------------------


def test_round_trip_fidelity_simple(store: SqliteStore) -> None:
    rec = _make()
    stored = store.add(rec)
    fetched = store.get(stored.memory_id)
    assert fetched == stored


def test_round_trip_with_all_fields(store: SqliteStore) -> None:
    created = datetime(2024, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
    rec = _make(
        content="rich record",
        source_type=SourceType.FILE,
        source_ref="repo://README.md@commit123",
        scope=Scope.PROJECT,
        scope_ref="proj-42",
        trust_score=0.75,
        sensitivity=Sensitivity.SECRET,
        status=MemoryStatus.ACTIVE,
        tags=["alpha", "beta"],
        confirmations=3,
        embedding=[0.5, -0.25, 0.75, 0.0],  # float32-exact
        metadata={"k": "v", "n": 7, "nested": {"x": [1, 2]}},
        now=created,
    )
    stored = store.add(rec)
    fetched = store.get(stored.memory_id)
    assert fetched == stored
    assert fetched.embedding == [0.5, -0.25, 0.75, 0.0]
    assert fetched.tags == ["alpha", "beta"]
    assert fetched.metadata == {"k": "v", "n": 7, "nested": {"x": [1, 2]}}


def test_round_trip_preserves_contradicts(store: SqliteStore) -> None:
    target = store.add(_make(content="x is true"))
    rec = _make(content="x is false", contradicts=[target.memory_id])
    stored = store.add(rec)
    fetched = store.get(stored.memory_id)
    assert fetched.contradicts == [target.memory_id]
    assert fetched == stored


def test_round_trip_no_embedding_is_none(store: SqliteStore) -> None:
    stored = store.add(_make(embedding=None))
    assert store.get(stored.memory_id).embedding is None


# ---------------------------------------------------------------------------
# Not-found contract & uniqueness
# ---------------------------------------------------------------------------


def test_get_missing_returns_none(store: SqliteStore) -> None:
    assert store.get("f47ac10b-58cc-4372-a567-0e02b2c3d479") is None


def test_duplicate_memory_id_rejected(store: SqliteStore) -> None:
    rec = _make()
    store.add(rec)
    with pytest.raises(ValueError):
        store.add(rec)


# ---------------------------------------------------------------------------
# trust_score CHECK constraint (DB level)
# ---------------------------------------------------------------------------


def test_trust_score_check_rejected_at_db(store: SqliteStore) -> None:
    # Bypass model validation to exercise the SQLite CHECK (trust_score in [0,1]).
    with pytest.raises(sqlite3.IntegrityError):
        store._conn.execute(
            "INSERT INTO memories "
            "(memory_id, content, source_type, source_ref, scope, "
            "created_at, updated_at, trust_score) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "f47ac10b-58cc-4372-a567-0e02b2c3d479",
                "bad",
                "user",
                "user://x",
                "global",
                "2024-01-01T00:00:00+00:00",
                "2024-01-01T00:00:00+00:00",
                2.0,
            ),
        )


# ---------------------------------------------------------------------------
# Property 21 — soft-delete invariant
# ---------------------------------------------------------------------------


def test_soft_delete_keeps_record_retrievable(store: SqliteStore) -> None:
    stored = store.add(_make())
    store.soft_delete(stored.memory_id)
    fetched = store.get(stored.memory_id)
    assert fetched is not None
    assert fetched.status == MemoryStatus.DELETED


def test_soft_delete_listed_only_with_status_filter(store: SqliteStore) -> None:
    stored = store.add(_make())
    store.soft_delete(stored.memory_id)
    deleted = store.list(status=MemoryStatus.DELETED)
    assert [r.memory_id for r in deleted] == [stored.memory_id]
    active = store.list(status=MemoryStatus.ACTIVE)
    assert stored.memory_id not in [r.memory_id for r in active]


# ---------------------------------------------------------------------------
# update
# ---------------------------------------------------------------------------


def test_update_persists_changes(store: SqliteStore) -> None:
    stored = store.add(_make(content="old", embedding=[0.5, 0.5]))
    stored.content = "new content"
    stored.trust_score = 0.9
    stored.embedding = [0.25, -0.25]
    store.update(stored)
    fetched = store.get(stored.memory_id)
    assert fetched.content == "new content"
    assert fetched.trust_score == 0.9
    assert fetched.embedding == [0.25, -0.25]


def test_update_unknown_raises(store: SqliteStore) -> None:
    rec = _make()  # never added
    with pytest.raises(ValueError):
        store.update(rec)


# ---------------------------------------------------------------------------
# list filters
# ---------------------------------------------------------------------------


def test_list_filters_by_scope_and_scope_ref(store: SqliteStore) -> None:
    a = store.add(_make(scope=Scope.PROJECT, scope_ref="p1", content="a"))
    b = store.add(_make(scope=Scope.PROJECT, scope_ref="p2", content="b"))
    store.add(_make(scope=Scope.GLOBAL, content="c"))

    proj = store.list(scope=Scope.PROJECT)
    assert {r.memory_id for r in proj} == {a.memory_id, b.memory_id}

    p1 = store.list(scope=Scope.PROJECT, scope_ref="p1")
    assert [r.memory_id for r in p1] == [a.memory_id]


def test_list_no_filters_returns_all(store: SqliteStore) -> None:
    store.add(_make(content="one"))
    store.add(_make(content="two"))
    assert len(store.list()) == 2


# ---------------------------------------------------------------------------
# keyword_search (FTS5)
# ---------------------------------------------------------------------------


def test_keyword_search_matches_and_ranks(store: SqliteStore) -> None:
    db = store.add(_make(content="the database connection pool is exhausted"))
    store.add(_make(content="the weather is sunny today"))

    results = store.keyword_search("database", limit=10)
    assert len(results) == 1
    record, score = results[0]
    assert record.memory_id == db.memory_id
    assert isinstance(score, float)


def test_keyword_search_handles_special_chars(store: SqliteStore) -> None:
    store.add(_make(content="normal text here"))
    # Must not raise an FTS5 syntax error on operator-like input.
    assert store.keyword_search('"unbalanced (AND OR', limit=5) == []


def test_keyword_search_zero_limit(store: SqliteStore) -> None:
    store.add(_make(content="anything"))
    assert store.keyword_search("anything", limit=0) == []


# ---------------------------------------------------------------------------
# vector_search (cosine in Python)
# ---------------------------------------------------------------------------


def test_vector_search_orders_by_cosine_similarity(store: SqliteStore) -> None:
    near = store.add(_make(content="near", embedding=[1.0, 0.0, 0.0]))
    mid = store.add(_make(content="mid", embedding=[0.5, 0.5, 0.0]))
    far = store.add(_make(content="far", embedding=[0.0, 0.0, 1.0]))

    results = store.vector_search([1.0, 0.0, 0.0], limit=3)
    ordered = [r.memory_id for r, _ in results]
    assert ordered[0] == near.memory_id
    assert ordered == [near.memory_id, mid.memory_id, far.memory_id]
    # Top score is (near) cosine == 1.0.
    assert results[0][1] == pytest.approx(1.0)


def test_vector_search_respects_limit(store: SqliteStore) -> None:
    store.add(_make(content="a", embedding=[1.0, 0.0]))
    store.add(_make(content="b", embedding=[0.0, 1.0]))
    store.add(_make(content="c", embedding=[1.0, 1.0]))
    assert len(store.vector_search([1.0, 0.0], limit=2)) == 2


def test_vector_search_skips_records_without_embeddings(store: SqliteStore) -> None:
    with_vec = store.add(_make(content="has vec", embedding=[1.0, 0.0]))
    store.add(_make(content="no vec", embedding=None))
    results = store.vector_search([1.0, 0.0], limit=10)
    assert [r.memory_id for r, _ in results] == [with_vec.memory_id]


def test_vector_search_empty_query_returns_empty(store: SqliteStore) -> None:
    store.add(_make(embedding=[1.0, 0.0]))
    assert store.vector_search([], limit=5) == []
