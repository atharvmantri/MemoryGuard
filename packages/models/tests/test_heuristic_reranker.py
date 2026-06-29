# SPDX-License-Identifier: Apache-2.0
"""Property + unit tests for the OSS ``HeuristicReranker`` (offline).

These tests run with no network access and no ML dependencies. They exercise the
deterministic heuristic reranker both with and without an embedder in ``ctx``
(the OSS ``LocalEmbedder`` is itself offline + deterministic).

Covers:

* **Property 23: Reranker output bounds** -- ``relevance_score`` and
  ``trust_score`` in ``[0.0, 1.0]``, ``should_use_memory`` is a ``bool``, and
  ``reason`` is a non-empty string for every candidate.
* **Property 24: Reranker determinism (fixed model version)** -- identical
  inputs under a fixed model version yield identical output (values + order).
* **Property 33: Reranker order stability** -- for a fixed model version,
  reranking is a stable, repeatable ordering with a *deterministic tie-breaker*:
  re-running on the same candidate set yields the same order, the combined score
  is non-increasing down the list, candidates that tie on the combined score are
  ordered by the deterministic ``memory_id`` tie-breaker, and the output order is
  invariant to the input order (no nondeterministic reshuffling).

Validates: Requirements 22.2, 22.3.
"""

from __future__ import annotations

import random
from datetime import datetime, timedelta, timezone

import pytest

from memoryguard_core.models import (
    MemoryRecord,
    MemoryStatus,
    Scope,
    Sensitivity,
    SourceType,
)
from memoryguard_models import HeuristicReranker, LocalEmbedder, ModelInfo, RerankResult
from memoryguard_models.base import Reranker

hypothesis = pytest.importorskip("hypothesis")
from hypothesis import given, settings  # noqa: E402
from hypothesis import strategies as st  # noqa: E402


# ---------------------------------------------------------------------------
# Fixed reference instants (keep `now` an explicit input for determinism).
# ---------------------------------------------------------------------------

CREATED_AT = datetime(2024, 1, 1, tzinfo=timezone.utc)
NOW = datetime(2024, 6, 1, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------

_text = st.text(
    alphabet=st.characters(min_codepoint=32, max_codepoint=126),
    min_size=0,
    max_size=60,
)


@st.composite
def memory_records(draw: st.DrawFn) -> MemoryRecord:
    """Generate a valid, varied :class:`MemoryRecord` for reranking."""
    memory_id = str(draw(st.uuids(version=4)))
    content = draw(st.text(min_size=1, max_size=80)) or "memory"
    source_type = draw(st.sampled_from(list(SourceType)))
    sensitivity = draw(st.sampled_from(list(Sensitivity)))
    status = draw(st.sampled_from(list(MemoryStatus)))
    trust_score = draw(st.floats(min_value=0.0, max_value=1.0))
    confirmations = draw(st.integers(min_value=0, max_value=50))

    # Contradiction ids distinct from the record's own id.
    contradicts = [
        str(uid)
        for uid in draw(st.lists(st.uuids(version=4), max_size=5))
        if str(uid) != memory_id
    ]

    has_expiry = draw(st.booleans())
    expires_at = CREATED_AT + timedelta(days=draw(st.integers(1, 365))) if has_expiry else None

    return MemoryRecord(
        memory_id=memory_id,
        content=content,
        source_type=source_type,
        source_ref="repo://fixture",
        scope=Scope.GLOBAL,
        scope_ref="fixture",
        created_at=CREATED_AT,
        updated_at=CREATED_AT,
        expires_at=expires_at,
        trust_score=trust_score,
        sensitivity=sensitivity,
        status=status,
        contradicts=contradicts,
        confirmations=confirmations,
    )


# ---------------------------------------------------------------------------
# Basic interface / identity
# ---------------------------------------------------------------------------


def test_is_reranker_instance() -> None:
    """HeuristicReranker honors the Reranker interface."""
    assert isinstance(HeuristicReranker(), Reranker)


def test_info_identity() -> None:
    """info exposes a stable identity with task='rerank'."""
    info = HeuristicReranker().info
    assert isinstance(info, ModelInfo)
    assert info.task == "rerank"
    assert info.model_id == "reranker/heuristic"
    assert info.version == "1.0.0"


# ---------------------------------------------------------------------------
# Property 23: output bounds
# ---------------------------------------------------------------------------


def _assert_bounds(results: list[RerankResult]) -> None:
    for r in results:
        assert isinstance(r, RerankResult)
        assert isinstance(r.relevance_score, float)
        assert 0.0 <= r.relevance_score <= 1.0
        assert isinstance(r.trust_score, float)
        assert 0.0 <= r.trust_score <= 1.0
        assert isinstance(r.should_use_memory, bool)
        assert isinstance(r.reason, str)
        assert r.reason  # non-empty


@settings(max_examples=200, deadline=None)
@given(query=_text, candidates=st.lists(memory_records(), max_size=8))
def test_property23_output_bounds_lexical(query: str, candidates: list) -> None:
    """Property 23: bounded, well-formed outputs without an embedder."""
    reranker = HeuristicReranker()
    results = reranker.rerank(query, candidates, {"now": NOW})
    assert len(results) == len(candidates)
    _assert_bounds(results)
    # Every candidate is represented exactly once (multiset equality).
    assert sorted(r.memory_id for r in results) == sorted(c.memory_id for c in candidates)


@settings(max_examples=120, deadline=None)
@given(query=_text, candidates=st.lists(memory_records(), max_size=6))
def test_property23_output_bounds_with_embedder(query: str, candidates: list) -> None:
    """Property 23: bounds also hold with the semantic (embedder) path."""
    reranker = HeuristicReranker()
    ctx = {"now": NOW, "embedder": LocalEmbedder()}
    results = reranker.rerank(query, candidates, ctx)
    assert len(results) == len(candidates)
    _assert_bounds(results)


def test_property23_empty_candidates_returns_empty() -> None:
    """No candidates -> no results (still bounded/valid)."""
    assert HeuristicReranker().rerank("anything", [], {"now": NOW}) == []


# ---------------------------------------------------------------------------
# Property 24: determinism per fixed model version
# ---------------------------------------------------------------------------


@settings(max_examples=200, deadline=None)
@given(query=_text, candidates=st.lists(memory_records(), max_size=8))
def test_property24_determinism_lexical(query: str, candidates: list) -> None:
    """Property 24: identical inputs -> identical output (values + order)."""
    ctx = {"now": NOW}
    first = HeuristicReranker().rerank(query, candidates, ctx)
    second = HeuristicReranker().rerank(query, candidates, ctx)
    assert first == second


@settings(max_examples=120, deadline=None)
@given(query=_text, candidates=st.lists(memory_records(), max_size=6))
def test_property24_determinism_with_embedder(query: str, candidates: list) -> None:
    """Property 24: determinism holds on the semantic path too."""
    first = HeuristicReranker().rerank(query, candidates, {"now": NOW, "embedder": LocalEmbedder()})
    second = HeuristicReranker().rerank(query, candidates, {"now": NOW, "embedder": LocalEmbedder()})
    assert first == second


# ---------------------------------------------------------------------------
# Targeted unit tests for decision logic
# ---------------------------------------------------------------------------


def _record(**overrides) -> MemoryRecord:
    base = dict(
        memory_id="00000000-0000-4000-8000-000000000001",
        content="the quick brown fox jumps over the lazy dog",
        source_type=SourceType.COMMIT,
        source_ref="repo://README.md",
        scope=Scope.GLOBAL,
        scope_ref=None,
        created_at=CREATED_AT,
        updated_at=CREATED_AT,
        trust_score=0.9,
        sensitivity=Sensitivity.PUBLIC,
        status=MemoryStatus.ACTIVE,
    )
    base.update(overrides)
    return MemoryRecord(**base)


def test_deleted_candidate_is_not_used() -> None:
    """A deleted candidate is never surfaced regardless of scores."""
    rec = _record(status=MemoryStatus.DELETED)
    [result] = HeuristicReranker().rerank("quick brown fox", [rec], {"now": NOW})
    assert result.should_use_memory is False
    assert "deleted" in result.reason


def test_expired_candidate_is_not_used() -> None:
    """An expired candidate is never surfaced."""
    rec = _record(status=MemoryStatus.EXPIRED)
    [result] = HeuristicReranker().rerank("quick brown fox", [rec], {"now": NOW})
    assert result.should_use_memory is False


def test_relevant_trusted_candidate_is_used() -> None:
    """A strongly relevant, high-trust, active candidate is surfaced."""
    rec = _record()
    [result] = HeuristicReranker().rerank("quick brown fox", [rec], {"now": NOW})
    assert result.should_use_memory is True
    assert result.relevance_score > 0.0
    assert result.trust_score == pytest.approx(0.9)
    assert result.reason


def test_irrelevant_query_not_used() -> None:
    """Zero lexical overlap drops below the relevance floor -> skipped."""
    rec = _record(content="alpha beta gamma delta")
    [result] = HeuristicReranker().rerank("zzzz nomatch", [rec], {"now": NOW})
    assert result.relevance_score == 0.0
    assert result.should_use_memory is False


def test_zero_trust_recomputed_from_signals() -> None:
    """When trust_score is 0, trust is recomputed from core signals (>0)."""
    rec = _record(trust_score=0.0, confirmations=10)
    [result] = HeuristicReranker().rerank("quick brown fox", [rec], {"now": NOW})
    assert result.trust_score > 0.0


def test_results_ranked_by_combined_score_descending() -> None:
    """Results come back in a stable, descending combined-score order."""
    strong = _record(
        memory_id="00000000-0000-4000-8000-00000000000a",
        content="quick brown fox",
        trust_score=0.95,
    )
    weak = _record(
        memory_id="00000000-0000-4000-8000-00000000000b",
        content="completely unrelated content here",
        trust_score=0.1,
    )
    results = HeuristicReranker().rerank("quick brown fox", [weak, strong], {"now": NOW})
    combined = [0.5 * r.relevance_score + 0.5 * r.trust_score for r in results]
    assert combined == sorted(combined, reverse=True)
    assert results[0].memory_id == strong.memory_id


def test_custom_thresholds_are_respected() -> None:
    """Raising trust_min can flip an otherwise-usable candidate to skipped."""
    rec = _record(trust_score=0.5)
    strict = HeuristicReranker(trust_min=0.8)
    [result] = strict.rerank("quick brown fox", [rec], {"now": NOW})
    assert result.should_use_memory is False


def test_default_now_does_not_crash_without_ctx() -> None:
    """rerank tolerates an empty ctx (defaults now=utcnow, no embedder)."""
    rec = _record(trust_score=0.0)
    [result] = HeuristicReranker().rerank("quick brown fox", [rec], {})
    assert 0.0 <= result.trust_score <= 1.0
    assert result.reason


# ---------------------------------------------------------------------------
# Property 33: reranker order stability (deterministic tie-breaker)
# ---------------------------------------------------------------------------


def _combined(result: RerankResult) -> float:
    """The combined ranking score used by the reranker's ordering."""
    return 0.5 * result.relevance_score + 0.5 * result.trust_score


@settings(max_examples=200, deadline=None)
@given(query=_text, candidates=st.lists(memory_records(), max_size=8))
def test_property33_combined_score_non_increasing(query: str, candidates: list) -> None:
    """Property 33: results are ordered by combined score descending.

    Validates: Requirements 22.2, 22.3
    """
    results = HeuristicReranker().rerank(query, candidates, {"now": NOW})
    combined = [_combined(r) for r in results]
    assert combined == sorted(combined, reverse=True)


@settings(max_examples=200, deadline=None)
@given(query=_text, candidates=st.lists(memory_records(), max_size=8))
def test_property33_ties_broken_by_memory_id(query: str, candidates: list) -> None:
    """Property 33: candidates tying on combined score are ordered by memory_id.

    The deterministic ``memory_id`` tie-breaker guarantees a well-defined,
    repeatable order with no nondeterministic reshuffling.

    Validates: Requirements 22.2, 22.3
    """
    results = HeuristicReranker().rerank(query, candidates, {"now": NOW})
    for prev, curr in zip(results, results[1:]):
        # Equal combined score => memory_id strictly ascending (the tie-breaker).
        if _combined(prev) == _combined(curr):
            assert prev.memory_id <= curr.memory_id


@settings(max_examples=200, deadline=None)
@given(
    query=_text,
    candidates=st.lists(memory_records(), max_size=8, unique_by=lambda r: r.memory_id),
    seed=st.integers(min_value=0, max_value=2**32 - 1),
)
def test_property33_order_invariant_to_input_order(
    query: str, candidates: list, seed: int
) -> None:
    """Property 33: shuffling the input yields the same output ordering.

    Because the ordering uses a deterministic tie-breaker (combined score then
    ``memory_id``), the result order does not depend on the order candidates are
    supplied in -- i.e. no nondeterministic reshuffling across runs.

    Validates: Requirements 22.2, 22.3
    """
    reranker = HeuristicReranker()
    baseline = reranker.rerank(query, candidates, {"now": NOW})

    shuffled = list(candidates)
    random.Random(seed).shuffle(shuffled)
    reshuffled = reranker.rerank(query, shuffled, {"now": NOW})

    # Same ranked sequence of memory_ids regardless of input order.
    assert [r.memory_id for r in reshuffled] == [r.memory_id for r in baseline]
    # And the full results agree (values too), not just the ordering.
    assert reshuffled == baseline


@settings(max_examples=120, deadline=None)
@given(query=_text, candidates=st.lists(memory_records(), max_size=8))
def test_property33_repeated_runs_same_order(query: str, candidates: list) -> None:
    """Property 33: re-running on the same set yields the identical order.

    Validates: Requirements 22.2, 22.3
    """
    reranker = HeuristicReranker()
    ctx = {"now": NOW}
    first = [r.memory_id for r in reranker.rerank(query, candidates, ctx)]
    second = [r.memory_id for r in reranker.rerank(query, candidates, ctx)]
    assert first == second


def test_property33_explicit_tie_broken_by_memory_id() -> None:
    """Identical-scoring candidates come back in memory_id-ascending order.

    Three records share content/trust/everything except their ids, so they tie
    on the combined score; the deterministic tie-breaker orders them by id.

    Validates: Requirements 22.2, 22.3
    """
    ids = [
        "00000000-0000-4000-8000-0000000000c3",
        "00000000-0000-4000-8000-0000000000a1",
        "00000000-0000-4000-8000-0000000000b2",
    ]
    candidates = [_record(memory_id=i, content="quick brown fox") for i in ids]
    results = HeuristicReranker().rerank("quick brown fox", candidates, {"now": NOW})

    # All tie on combined score...
    combined = {_combined(r) for r in results}
    assert len(combined) == 1
    # ...so they are returned strictly sorted by memory_id (input order ignored).
    assert [r.memory_id for r in results] == sorted(ids)
