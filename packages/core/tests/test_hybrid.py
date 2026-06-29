# SPDX-License-Identifier: Apache-2.0
"""Tests for Stage-1 hybrid candidate gathering (``retrieval/hybrid.py``).

Covers the design's *Stage 1 — hybrid candidate gathering* behavior and the
relevant acceptance criteria:

* vector + keyword + recency signals merge into a **unique** candidate set keyed
  by ``memory_id`` (de-duplication) — Requirement 4.1.
* each result carries the documented component scores (``vector_sim``,
  ``keyword_score``, ``recency``, ``first_stage_score``), all bounded in
  ``[0, 1]``, with ``first_stage_score`` equal to the documented weighted blend.
* a stored embedding whose dimension differs from the query embedding raises
  :class:`DimensionMismatchError` rather than returning incorrect results —
  Requirement 4.3.

All checks run on-device against ``SqliteStore(":memory:")`` and the default
``LocalEmbedder`` (no external API). Property-based checks use Hypothesis when
available and fall back to a deterministic example sweep otherwise.

**Validates: Requirements 4.1, 4.2, 4.3, 22.1**
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional

import pytest

from memoryguard_core.models import (
    MemoryRecord,
    Scope,
    SourceType,
    new_memory_record,
)
from memoryguard_core.retrieval import (
    Candidate,
    DimensionMismatchError,
    W_KEYWORD,
    W_RECENCY,
    W_VECTOR,
    gather_candidates,
)
from memoryguard_core.store import SqliteStore
from memoryguard_models.embedder.local_embedder import LocalEmbedder

# Optional Hypothesis support (graceful fallback to example-based testing).
try:  # pragma: no cover - import guard
    from hypothesis import given, settings
    from hypothesis import strategies as st

    _HAS_HYPOTHESIS = True
except Exception:  # pragma: no cover - Hypothesis not installed
    _HAS_HYPOTHESIS = False


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


@pytest.fixture()
def embedder() -> LocalEmbedder:
    """Default OSS embedder (deterministic 384-dim hash backend)."""
    return LocalEmbedder()


@pytest.fixture()
def store() -> SqliteStore:
    """A fresh in-memory SQLite store."""
    return SqliteStore(":memory:")


def _add(
    store: SqliteStore,
    embedder: LocalEmbedder,
    content: str,
    *,
    scope: Scope = Scope.PROJECT,
    scope_ref: Optional[str] = "proj-1",
    created_at: Optional[datetime] = None,
    embedding: Optional[list[float]] = None,
) -> MemoryRecord:
    """Create + persist a record, embedding ``content`` by default."""
    record = new_memory_record(
        content=content,
        source_type=SourceType.USER,
        source_ref="user://tester",
        scope=scope,
        scope_ref=scope_ref,
        now=created_at,
        embedding=embedding if embedding is not None else embedder.embed(content),
    )
    return store.add(record)


# ---------------------------------------------------------------------------
# Merge / de-duplication (Requirement 4.1)
# ---------------------------------------------------------------------------


def test_merge_dedups_by_memory_id(store: SqliteStore, embedder: LocalEmbedder) -> None:
    """A memory found via multiple signals appears exactly once in the result."""
    r1 = _add(store, embedder, "deploy the service with docker compose")
    r2 = _add(store, embedder, "the database uses postgres with pgvector")
    r3 = _add(store, embedder, "frontend is built with react and tailwind")

    # This query overlaps r1 both semantically and on keywords ("docker"),
    # so r1 is gathered by vector AND keyword AND recency — must still be unique.
    candidates = gather_candidates(store, "how do we deploy docker", embedder, limit=10)

    ids = [c.memory_id for c in candidates]
    assert len(ids) == len(set(ids)), "candidates must be de-duplicated by memory_id"

    # All three stored, in-scope records are reachable (recency seeds the union).
    assert set(ids) == {r1.memory_id, r2.memory_id, r3.memory_id}

    # Each id maps to exactly one Candidate carrying the record.
    by_id = {c.memory_id: c for c in candidates}
    assert by_id[r1.memory_id].record.memory_id == r1.memory_id


def test_results_carry_component_scores(store: SqliteStore, embedder: LocalEmbedder) -> None:
    """Each candidate carries bounded component scores and the blended score."""
    _add(store, embedder, "alpha memory about kubernetes ingress")
    _add(store, embedder, "beta memory about service mesh routing")

    candidates = gather_candidates(store, "kubernetes ingress", embedder, limit=10)
    assert candidates, "expected at least one candidate"

    for c in candidates:
        assert isinstance(c, Candidate)
        for component in (c.vector_sim, c.keyword_score, c.recency, c.first_stage_score):
            assert 0.0 <= component <= 1.0
        expected = (
            W_VECTOR * c.vector_sim
            + W_KEYWORD * c.keyword_score
            + W_RECENCY * c.recency
        )
        assert c.first_stage_score == pytest.approx(expected)


def test_results_sorted_descending_and_limited(
    store: SqliteStore, embedder: LocalEmbedder
) -> None:
    """Results are ordered by ``first_stage_score`` desc and capped at ``limit``."""
    for i in range(6):
        _add(store, embedder, f"memory number {i} about retrieval ranking")

    candidates = gather_candidates(store, "retrieval ranking", embedder, limit=3)
    assert len(candidates) <= 3
    scores = [c.first_stage_score for c in candidates]
    assert scores == sorted(scores, reverse=True)


def test_keyword_signal_contributes(store: SqliteStore, embedder: LocalEmbedder) -> None:
    """A record matching query keywords gets a non-zero keyword component."""
    target = _add(store, embedder, "the secret handshake protocol uses zebra tokens")
    _add(store, embedder, "completely unrelated note about lunch plans")

    candidates = gather_candidates(store, "zebra tokens handshake", embedder, limit=10)
    by_id = {c.memory_id: c for c in candidates}
    assert by_id[target.memory_id].keyword_score > 0.0


def test_recency_prefers_newer(store: SqliteStore, embedder: LocalEmbedder) -> None:
    """The newer record receives a higher recency component than the older one."""
    base = datetime(2023, 1, 1, tzinfo=timezone.utc)
    older = _add(store, embedder, "older shared topic note", created_at=base)
    newer = _add(
        store,
        embedder,
        "newer shared topic note",
        created_at=base + timedelta(days=30),
    )

    candidates = gather_candidates(store, "shared topic note", embedder, limit=10)
    by_id = {c.memory_id: c for c in candidates}
    assert by_id[newer.memory_id].recency > by_id[older.memory_id].recency


def test_scope_narrowing(store: SqliteStore, embedder: LocalEmbedder) -> None:
    """Optional scope/scope_ref narrows the Stage-1 candidate pool."""
    keep = _add(store, embedder, "scoped note one", scope=Scope.PROJECT, scope_ref="proj-A")
    _add(store, embedder, "scoped note two", scope=Scope.PROJECT, scope_ref="proj-B")

    candidates = gather_candidates(
        store, "scoped note", embedder, limit=10, scope=Scope.PROJECT, scope_ref="proj-A"
    )
    ids = {c.memory_id for c in candidates}
    assert ids == {keep.memory_id}


def test_empty_store_returns_empty(store: SqliteStore, embedder: LocalEmbedder) -> None:
    """No stored memories yields no candidates."""
    assert gather_candidates(store, "anything", embedder, limit=5) == []


def test_non_positive_limit_returns_empty(
    store: SqliteStore, embedder: LocalEmbedder
) -> None:
    """A non-positive limit short-circuits to an empty result."""
    _add(store, embedder, "some memory")
    assert gather_candidates(store, "some", embedder, limit=0) == []
    assert gather_candidates(store, "some", embedder, limit=-3) == []


# ---------------------------------------------------------------------------
# Dimension mismatch (Requirement 4.3)
# ---------------------------------------------------------------------------


def test_dimension_mismatch_raises(store: SqliteStore, embedder: LocalEmbedder) -> None:
    """A stored embedding of differing dimension surfaces an explicit conflict."""
    # Persist a record whose embedding has the wrong dimension (8 != 384).
    _add(store, embedder, "memory with a mismatched embedding", embedding=[0.1] * 8)

    with pytest.raises(DimensionMismatchError) as excinfo:
        gather_candidates(store, "mismatched embedding", embedder, limit=10)

    err = excinfo.value
    assert err.query_dim == embedder.dim
    assert err.stored_dim == 8


def test_matching_dimensions_do_not_raise(
    store: SqliteStore, embedder: LocalEmbedder
) -> None:
    """Equal-dimension embeddings compare cleanly without raising."""
    _add(store, embedder, "well formed memory one")
    _add(store, embedder, "well formed memory two")
    # Should not raise.
    gather_candidates(store, "well formed", embedder, limit=10)


# ---------------------------------------------------------------------------
# Property-based checks (Hypothesis optional)
# ---------------------------------------------------------------------------


def _check_bounded_and_unique(contents: list[str]) -> None:
    store = SqliteStore(":memory:")
    embedder = LocalEmbedder()
    for text in contents:
        _add(store, embedder, text)

    candidates = gather_candidates(store, contents[0], embedder, limit=len(contents) + 5)

    ids = [c.memory_id for c in candidates]
    assert len(ids) == len(set(ids))  # de-duplicated
    for c in candidates:
        assert 0.0 <= c.first_stage_score <= 1.0
        expected = (
            W_VECTOR * c.vector_sim
            + W_KEYWORD * c.keyword_score
            + W_RECENCY * c.recency
        )
        assert c.first_stage_score == pytest.approx(expected)


if _HAS_HYPOTHESIS:

    @settings(max_examples=40, deadline=None)
    @given(
        st.lists(
            st.text(alphabet="abcdefghijklmnopqrstuvwxyz ", min_size=3, max_size=40),
            min_size=1,
            max_size=8,
        )
    )
    def test_first_stage_score_bounded_and_dedup(contents: list[str]) -> None:
        """first_stage_score stays in [0,1] and candidates de-duplicate."""
        # Ensure non-empty, distinct-enough content (store requires non-empty).
        cleaned = [c.strip() or "fallback content" for c in contents]
        _check_bounded_and_unique(cleaned)

else:  # pragma: no cover - deterministic fallback when Hypothesis is absent

    def test_first_stage_score_bounded_and_dedup() -> None:
        """Deterministic fallback for the bounded + dedup property."""
        _check_bounded_and_unique(
            ["alpha note", "beta note", "gamma note", "alpha note again"]
        )
