# SPDX-License-Identifier: Apache-2.0
"""Shared ``MemoryStore`` contract + property test suite (cross-backend).

This module runs the **same** assertions against **both** store backends —
``SqliteStore`` (local mode) and ``PostgresStore`` (cloud mode) — to prove they
honor one identical :class:`~memoryguard_core.store.base.MemoryStore` contract.
It is the cross-backend consolidation of the per-backend suites in
``test_sqlite_store.py`` / ``test_sqlite_store_properties.py`` (SQLite) and
``test_pg_store.py`` (Postgres), and it implements the design's

* **Property 20: Round-trip fidelity** — ``add`` then ``get`` returns an equal
  record (modulo server-set fields).
* **Property 21: Soft-delete invariant** — after ``soft_delete`` ``status`` is
  ``DELETED`` and the record is still retrievable by id.
* **Property 22: Cross-backend consistency** — ``trust_score`` is persisted
  within ``[0, 1]`` and every contract behavior is identical across SQLite and
  Postgres.

**Validates: Requirements 2.1, 2.2, 7.1, 18.1.**

Backends under test
-------------------
The suite is parametrized over two backends:

* ``sqlite`` — a fresh in-memory ``SqliteStore`` per test/example. Always runs.
* ``postgres`` — a ``PostgresStore`` on the server named by the
  ``MEMORYGUARD_TEST_PG_DSN`` environment variable (a libpq URL/DSN; the database
  must allow ``CREATE EXTENSION vector`` and ``CREATE EXTENSION pg_trgm``). This
  parametrization **skips gracefully** — exactly like ``test_pg_store.py`` — when
  ``psycopg`` is unavailable or no server is reachable, so the suite never fails
  in an environment without PostgreSQL.

Cross-backend embedding note
---------------------------
The Postgres schema stores embeddings inline as a fixed-width ``vector(384)``
column, while SQLite stores arbitrary-length packed ``float32`` blobs. To keep a
*single* set of assertions meaningful for both, every embedding used here is
exactly :data:`DEFAULT_EMBEDDING_DIM` (384) wide and uses ``float32``-exact
component values, so it round-trips byte-for-byte on SQLite and at ``float4``
precision on pgvector — identically.
"""

from __future__ import annotations

import dataclasses
import os
from datetime import datetime, timedelta, timezone
from typing import Callable

import pytest

from memoryguard_core.models import (
    SCOPE_REF_REQUIRED,
    MemoryStatus,
    Scope,
    Sensitivity,
    SourceType,
    new_memory_record,
)
from memoryguard_core.store import MemoryStore, SqliteStore
from memoryguard_core.store.pg_store import DEFAULT_EMBEDDING_DIM, PostgresStore

try:  # Hypothesis is optional; example-based fallbacks below run when absent.
    from hypothesis import HealthCheck, given, settings
    from hypothesis import strategies as st

    HAVE_HYPOTHESIS = True
except ImportError:  # pragma: no cover - exercised only without hypothesis
    HAVE_HYPOTHESIS = False


# ---------------------------------------------------------------------------
# PostgreSQL availability (graceful skip, mirroring test_pg_store.py)
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


_PG_SKIP_REASON = _pg_skip_reason()


# ---------------------------------------------------------------------------
# Backend abstraction — one handle the shared assertions speak to
# ---------------------------------------------------------------------------


class _Backend:
    """A live store under test plus dimension-aware embedding helpers.

    ``store`` is a concrete :class:`MemoryStore`; ``embedding_dim`` is the width
    embeddings must have for this backend (both backends use
    :data:`DEFAULT_EMBEDDING_DIM` here so a single embedding shape works for all).
    """

    def __init__(self, name: str, store: MemoryStore, embedding_dim: int) -> None:
        self.name = name
        self.store = store
        self.embedding_dim = embedding_dim

    def emb(self, *values: float) -> list[float]:
        """Build an ``embedding_dim``-wide vector with leading components set."""

        vec = [0.0] * self.embedding_dim
        for i, v in enumerate(values):
            vec[i] = v
        return vec

    def close(self) -> None:
        self.store.close()


def _make_sqlite_backend() -> _Backend:
    """Fresh in-memory SQLite backend with the schema applied."""

    return _Backend("sqlite", SqliteStore(":memory:"), DEFAULT_EMBEDDING_DIM)


def _make_postgres_backend() -> _Backend:
    """Fresh-state Postgres backend (tables truncated) on the configured server."""

    store = PostgresStore(_PG_DSN)
    with store._conn.cursor() as cur:
        cur.execute("TRUNCATE memories, memory_contradictions CASCADE")
    store._conn.commit()
    return _Backend("postgres", store, store.embedding_dim)


# Factory params shared by the property tests. The Postgres factory is marked
# ``skipif`` so it skips (never fails) when no server is reachable.
_BACKEND_FACTORIES: list = [
    pytest.param(_make_sqlite_backend, id="sqlite"),
    pytest.param(
        _make_postgres_backend,
        id="postgres",
        marks=pytest.mark.skipif(
            bool(_PG_SKIP_REASON), reason=_PG_SKIP_REASON or "postgres unavailable"
        ),
    ),
]


@pytest.fixture(
    params=[
        pytest.param("sqlite", id="sqlite"),
        pytest.param(
            "postgres",
            id="postgres",
            marks=pytest.mark.skipif(
                bool(_PG_SKIP_REASON),
                reason=_PG_SKIP_REASON or "postgres unavailable",
            ),
        ),
    ]
)
def backend(request) -> _Backend:
    """Yield a live backend, set up fresh and torn down per example test.

    The ``postgres`` parametrization additionally calls :func:`pytest.skip` when
    no server is reachable, so the suite never fails without PostgreSQL.
    """

    if request.param == "postgres" and _PG_SKIP_REASON:
        pytest.skip(_PG_SKIP_REASON)
    b = _make_sqlite_backend() if request.param == "sqlite" else _make_postgres_backend()
    try:
        yield b
    finally:
        b.close()


def _make(**overrides):
    """Build a valid :class:`MemoryRecord` via the factory, with overrides."""

    kwargs = {
        "content": "the api key rotates every 30 days",
        "source_type": SourceType.USER,
        "source_ref": "user://alice",
        "scope": Scope.GLOBAL,
    }
    kwargs.update(overrides)
    return new_memory_record(**kwargs)


# ===========================================================================
# Contract — example-based assertions run against BOTH backends
# ===========================================================================


# --- the contract type ------------------------------------------------------


def test_store_is_memory_store(backend: _Backend) -> None:
    assert isinstance(backend.store, MemoryStore)


# --- Property 20 / 22 — round-trip fidelity --------------------------------


def test_round_trip_simple(backend: _Backend) -> None:
    rec = _make()
    stored = backend.store.add(rec)
    assert backend.store.get(stored.memory_id) == stored


def test_round_trip_all_fields(backend: _Backend) -> None:
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
        embedding=backend.emb(0.5, -0.25, 0.75),  # float32-exact
        metadata={"k": "v", "n": 7, "nested": {"x": [1, 2]}},
        now=created,
    )
    stored = backend.store.add(rec)
    fetched = backend.store.get(stored.memory_id)
    assert fetched == stored
    assert fetched.tags == ["alpha", "beta"]
    assert fetched.metadata == {"k": "v", "n": 7, "nested": {"x": [1, 2]}}
    assert fetched.embedding[:3] == [0.5, -0.25, 0.75]


def test_round_trip_preserves_contradicts(backend: _Backend) -> None:
    target = backend.store.add(_make(content="x is true"))
    stored = backend.store.add(
        _make(content="x is false", contradicts=[target.memory_id])
    )
    fetched = backend.store.get(stored.memory_id)
    assert fetched.contradicts == [target.memory_id]


def test_round_trip_no_embedding_is_none(backend: _Backend) -> None:
    stored = backend.store.add(_make(embedding=None))
    assert backend.store.get(stored.memory_id).embedding is None


# --- Property 22 — trust_score persisted within [0, 1] cross-backend -------


def test_trust_score_persisted_in_range(backend: _Backend) -> None:
    for ts in (0.0, 0.25, 0.5, 0.999, 1.0):
        stored = backend.store.add(_make(content=f"trust {ts}", trust_score=ts))
        fetched = backend.store.get(stored.memory_id)
        assert fetched.trust_score == pytest.approx(ts)
        assert 0.0 <= fetched.trust_score <= 1.0


# --- not-found & uniqueness -------------------------------------------------


def test_get_missing_returns_none(backend: _Backend) -> None:
    assert backend.store.get("f47ac10b-58cc-4372-a567-0e02b2c3d479") is None


def test_duplicate_memory_id_rejected(backend: _Backend) -> None:
    rec = _make()
    backend.store.add(rec)
    with pytest.raises(ValueError):
        backend.store.add(rec)


# --- Property 21 / 22 — soft-delete invariant ------------------------------


def test_soft_delete_keeps_record_retrievable(backend: _Backend) -> None:
    stored = backend.store.add(_make())
    backend.store.soft_delete(stored.memory_id)
    fetched = backend.store.get(stored.memory_id)
    assert fetched is not None
    assert fetched.status == MemoryStatus.DELETED


def test_soft_delete_listed_only_with_status_filter(backend: _Backend) -> None:
    stored = backend.store.add(_make())
    backend.store.soft_delete(stored.memory_id)
    deleted = backend.store.list(status=MemoryStatus.DELETED)
    assert [r.memory_id for r in deleted] == [stored.memory_id]
    active_ids = [r.memory_id for r in backend.store.list(status=MemoryStatus.ACTIVE)]
    assert stored.memory_id not in active_ids


# --- update -----------------------------------------------------------------


def test_update_persists_changes(backend: _Backend) -> None:
    stored = backend.store.add(_make(content="old", embedding=backend.emb(0.5, 0.5)))
    stored.content = "new content"
    stored.trust_score = 0.9
    stored.embedding = backend.emb(0.25, -0.25)
    backend.store.update(stored)
    fetched = backend.store.get(stored.memory_id)
    assert fetched.content == "new content"
    assert fetched.trust_score == pytest.approx(0.9)
    assert fetched.embedding[:2] == [0.25, -0.25]


def test_update_unknown_raises(backend: _Backend) -> None:
    with pytest.raises(ValueError):
        backend.store.update(_make())  # never added


# --- list filters -----------------------------------------------------------


def test_list_filters_by_scope_and_scope_ref(backend: _Backend) -> None:
    a = backend.store.add(_make(scope=Scope.PROJECT, scope_ref="p1", content="a"))
    b = backend.store.add(_make(scope=Scope.PROJECT, scope_ref="p2", content="b"))
    backend.store.add(_make(scope=Scope.GLOBAL, content="c"))

    proj = backend.store.list(scope=Scope.PROJECT)
    assert {r.memory_id for r in proj} == {a.memory_id, b.memory_id}

    p1 = backend.store.list(scope=Scope.PROJECT, scope_ref="p1")
    assert [r.memory_id for r in p1] == [a.memory_id]


def test_list_filters_by_status(backend: _Backend) -> None:
    keep = backend.store.add(_make(content="keep"))
    gone = backend.store.add(_make(content="gone"))
    backend.store.soft_delete(gone.memory_id)

    active_ids = {r.memory_id for r in backend.store.list(status=MemoryStatus.ACTIVE)}
    assert active_ids == {keep.memory_id}


def test_list_no_filters_returns_all(backend: _Backend) -> None:
    backend.store.add(_make(content="one"))
    backend.store.add(_make(content="two"))
    assert len(backend.store.list()) == 2


# --- keyword search ---------------------------------------------------------


def test_keyword_search_matches(backend: _Backend) -> None:
    db = backend.store.add(_make(content="the database connection pool is exhausted"))
    backend.store.add(_make(content="the weather is sunny today and warm"))
    results = backend.store.keyword_search("database connection", limit=10)
    ids = [r.memory_id for r, _ in results]
    assert db.memory_id in ids
    assert all(isinstance(score, float) for _, score in results)


def test_keyword_search_zero_limit(backend: _Backend) -> None:
    backend.store.add(_make(content="anything"))
    assert backend.store.keyword_search("anything", limit=0) == []


def test_keyword_search_empty_query(backend: _Backend) -> None:
    backend.store.add(_make(content="anything"))
    assert backend.store.keyword_search("   ", limit=5) == []


# --- vector search ----------------------------------------------------------


def test_vector_search_orders_by_cosine_similarity(backend: _Backend) -> None:
    near = backend.store.add(_make(content="near", embedding=backend.emb(1.0, 0.0, 0.0)))
    mid = backend.store.add(_make(content="mid", embedding=backend.emb(0.5, 0.5, 0.0)))
    far = backend.store.add(_make(content="far", embedding=backend.emb(0.0, 0.0, 1.0)))

    results = backend.store.vector_search(backend.emb(1.0, 0.0, 0.0), limit=3)
    ordered = [r.memory_id for r, _ in results]
    assert ordered[0] == near.memory_id
    assert ordered == [near.memory_id, mid.memory_id, far.memory_id]
    assert results[0][1] == pytest.approx(1.0, abs=1e-4)


def test_vector_search_respects_limit(backend: _Backend) -> None:
    backend.store.add(_make(content="a", embedding=backend.emb(1.0, 0.0)))
    backend.store.add(_make(content="b", embedding=backend.emb(0.0, 1.0)))
    backend.store.add(_make(content="c", embedding=backend.emb(1.0, 1.0)))
    assert len(backend.store.vector_search(backend.emb(1.0, 0.0), limit=2)) == 2


def test_vector_search_skips_records_without_embeddings(backend: _Backend) -> None:
    with_vec = backend.store.add(_make(content="has vec", embedding=backend.emb(1.0, 0.0)))
    backend.store.add(_make(content="no vec", embedding=None))
    results = backend.store.vector_search(backend.emb(1.0, 0.0), limit=10)
    assert [r.memory_id for r, _ in results] == [with_vec.memory_id]


def test_vector_search_empty_query_returns_empty(backend: _Backend) -> None:
    backend.store.add(_make(embedding=backend.emb(1.0, 0.0)))
    assert backend.store.vector_search([], limit=5) == []


# ===========================================================================
# Property 22 — Hypothesis properties run against BOTH backends
# ===========================================================================


if HAVE_HYPOTHESIS:
    _EMBED_DIM = DEFAULT_EMBEDDING_DIM

    # Text that is non-empty after trimming.
    _nonblank_text = st.text(min_size=1, max_size=120).filter(lambda s: s.strip() != "")

    # JSON-safe metadata values (round-trip exactly through JSON / JSONB).
    _json_scalar = st.one_of(
        st.none(),
        st.booleans(),
        st.integers(min_value=-(10**6), max_value=10**6),
        st.text(max_size=40),
    )
    _metadata = st.dictionaries(
        keys=st.text(min_size=1, max_size=20), values=_json_scalar, max_size=5
    )
    _tags = st.lists(st.text(max_size=20), max_size=5)

    # Up to 12 leading float32-exact components, padded to the fixed embedding
    # width so the SAME embedding shape is valid for both backends.
    _leading_floats = st.lists(
        st.floats(width=32, allow_nan=False, allow_infinity=False),
        min_size=1,
        max_size=12,
    )

    _utc_datetimes = st.datetimes(
        min_value=datetime(2000, 1, 1),
        max_value=datetime(2100, 1, 1),
        timezones=st.just(timezone.utc),
    )

    @st.composite
    def memory_records(draw):
        """Draw an arbitrary valid :class:`MemoryRecord` valid for both backends.

        ``contradicts`` is left empty (the ``memory_contradictions`` foreign keys
        require referenced rows to exist; an isolated record cannot reference
        other ids). Embeddings are ``None`` or exactly ``_EMBED_DIM`` wide.
        """

        scope = draw(st.sampled_from(list(Scope)))
        if scope in SCOPE_REF_REQUIRED:
            scope_ref = draw(_nonblank_text)
        else:
            scope_ref = draw(st.one_of(st.none(), _nonblank_text))

        created = draw(_utc_datetimes)
        expires_at = draw(
            st.one_of(
                st.none(),
                st.integers(min_value=1, max_value=10**7).map(
                    lambda secs: created + timedelta(seconds=secs)
                ),
            )
        )

        leading = draw(st.one_of(st.none(), _leading_floats))
        if leading is None:
            embedding = None
        else:
            embedding = leading + [0.0] * (_EMBED_DIM - len(leading))

        return new_memory_record(
            content=draw(_nonblank_text),
            source_type=draw(st.sampled_from(list(SourceType))),
            source_ref=draw(_nonblank_text),
            scope=scope,
            scope_ref=scope_ref,
            expires_at=expires_at,
            trust_score=draw(
                st.floats(
                    min_value=0.0, max_value=1.0, allow_nan=False, allow_infinity=False
                )
            ),
            sensitivity=draw(st.sampled_from(list(Sensitivity))),
            status=draw(st.sampled_from(list(MemoryStatus))),
            tags=draw(_tags),
            confirmations=draw(st.integers(min_value=0, max_value=10**6)),
            embedding=embedding,
            metadata=draw(_metadata),
            now=created,
        )

    @pytest.mark.parametrize("make_backend", _BACKEND_FACTORIES)
    @settings(
        max_examples=75,
        deadline=None,
        suppress_health_check=[HealthCheck.too_slow],
    )
    @given(record=memory_records())
    def test_property_22_round_trip_fidelity(make_backend: Callable, record):
        """**Validates: Requirements 2.1, 18.1** — add(rec) then get(id) == rec,
        identically on every backend. A fresh-state backend is used per example.
        """

        backend = make_backend()
        try:
            stored = backend.store.add(record)
            assert backend.store.get(stored.memory_id) == stored
            # Property 22: trust_score persisted within [0, 1].
            assert 0.0 <= backend.store.get(stored.memory_id).trust_score <= 1.0
        finally:
            backend.close()

    @pytest.mark.parametrize("make_backend", _BACKEND_FACTORIES)
    @settings(
        max_examples=75,
        deadline=None,
        suppress_health_check=[HealthCheck.too_slow],
    )
    @given(record=memory_records())
    def test_property_22_soft_delete_invariant(make_backend: Callable, record):
        """**Validates: Requirements 2.2, 18.1** — after soft_delete: status
        DELETED, still retrievable, all other fields preserved — on every backend.
        """

        backend = make_backend()
        try:
            stored = backend.store.add(record)
            backend.store.soft_delete(stored.memory_id)
            fetched = backend.store.get(stored.memory_id)
            assert fetched is not None
            assert fetched.status == MemoryStatus.DELETED
            assert fetched == dataclasses.replace(stored, status=MemoryStatus.DELETED)
        finally:
            backend.close()


def test_property_22_cross_backend_fallback():
    """**Validates: Requirements 2.1, 2.2, 18.1** — example-based round-trip +
    soft-delete on every available backend (runs when Hypothesis is absent too).
    """

    factories = [_make_sqlite_backend]
    if not _PG_SKIP_REASON:
        factories.append(_make_postgres_backend)

    for make_backend in factories:
        backend = make_backend()
        try:
            stored = backend.store.add(
                _make(
                    content="cross-backend record",
                    scope=Scope.PROJECT,
                    scope_ref="proj-1",
                    trust_score=0.5,
                    embedding=backend.emb(0.5, -0.25, 0.75),
                )
            )
            assert backend.store.get(stored.memory_id) == stored
            backend.store.soft_delete(stored.memory_id)
            fetched = backend.store.get(stored.memory_id)
            assert fetched is not None
            assert fetched.status == MemoryStatus.DELETED
        finally:
            backend.close()
