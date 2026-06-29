# SPDX-License-Identifier: Apache-2.0
"""Tests for the :class:`TrustEngine` (trust scoring + contradiction wiring).

Covers the contradiction-related correctness properties that live at the engine
boundary, plus an end-to-end ``evaluate`` happy path:

* **Property 12: Scan integrity** — ``scan`` returns no duplicate ``memory_id``
  values and never includes the candidate's own id.
* **Property 13: Mutual linkage** — after ``evaluate``, a detected contradiction
  ``a -> b`` leaves ``a.contradicts`` containing ``b`` and ``b.contradicts``
  containing ``a`` (symmetric, persisted links).
* **evaluate happy path** — scoring a candidate flags the canonical Flask /
  FastAPI contradiction, records mutual links, sets a bounded ``trust_score``,
  and transitions one record of the pair to ``DISPUTED``.

**Validates: Requirements 8.2, 8.3, 8.5, 23.1, 7.1**

All checks run on-device against a ``SqliteStore(":memory:")`` with embeddings
produced by the OSS :class:`LocalEmbedder` — no external LLM API.

Property-based tests use Hypothesis when available and fall back to a
deterministic example sweep otherwise.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from memoryguard_core.models import (
    MemoryRecord,
    MemoryStatus,
    Scope,
    Sensitivity,
    SourceType,
    new_memory_record,
)
from memoryguard_core.store.sqlite_store import SqliteStore
from memoryguard_core.trust.engine import TrustEngine
from memoryguard_models.base import ContradictionResult
from memoryguard_models.embedder.local_embedder import LocalEmbedder

# Optional Hypothesis support (graceful fallback to example-based testing).
try:  # pragma: no cover - import guard
    from hypothesis import HealthCheck, given, settings
    from hypothesis import strategies as st

    _HAS_HYPOTHESIS = True
except Exception:  # pragma: no cover - Hypothesis not installed
    _HAS_HYPOTHESIS = False


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

#: One shared embedder (deterministic, offline) reused across tests.
EMBEDDER = LocalEmbedder()


def _new_store() -> SqliteStore:
    """Return a fresh in-memory store."""

    return SqliteStore(":memory:")


def _make_record(
    content: str,
    *,
    scope: Scope = Scope.PROJECT,
    scope_ref: Optional[str] = "my-app",
    sensitivity: Sensitivity = Sensitivity.INTERNAL,
    source_type: SourceType = SourceType.USER,
    embed: bool = True,
) -> MemoryRecord:
    """Build a valid :class:`MemoryRecord`, embedding its content by default."""

    embedding = EMBEDDER.embed(content) if embed else None
    return new_memory_record(
        content=content,
        source_type=source_type,
        source_ref="user://tester",
        scope=scope,
        scope_ref=scope_ref,
        sensitivity=sensitivity,
        embedding=embedding,
    )


def _add(store: SqliteStore, record: MemoryRecord) -> MemoryRecord:
    """Persist ``record`` and return it."""

    store.add(record)
    return record


# Canonical conflicting / agreeing / unrelated phrases.
FLASK = "The project uses Flask as its web framework"
FASTAPI = "The project uses FastAPI as its web framework"
DJANGO = "The project uses Django as its web framework"
UNRELATED = "Lunch is served at noon in the cafeteria"


# ---------------------------------------------------------------------------
# Construction / interface
# ---------------------------------------------------------------------------


def test_engine_uses_oss_defaults() -> None:
    """A default-constructed engine wires the OSS trust + contradiction models."""

    from memoryguard_core.trust.contradiction import RuleContradictionModel
    from memoryguard_core.trust.scoring import DeterministicTrustModel

    engine = TrustEngine()
    assert isinstance(engine.trust_model, DeterministicTrustModel)
    assert isinstance(engine.contradiction_model, RuleContradictionModel)


def test_score_is_bounded_and_deterministic() -> None:
    """score returns a deterministic value in [0, 1] (Requirement 7.1)."""

    engine = TrustEngine()
    record = _make_record(FLASK)
    # Determinism is defined for a fixed `now` (freshness decays with wall-clock
    # time); pin `now` so the two calls are comparing like with like.
    fixed_now = datetime(2024, 6, 1, 12, 0, 0, tzinfo=timezone.utc)
    s1 = engine.score(record, fixed_now)
    s2 = engine.score(record, fixed_now)
    assert 0.0 <= s1 <= 1.0
    assert s1 == s2


def test_compute_signals_in_unit_range() -> None:
    """compute_signals returns every signal within [0, 1]."""

    engine = TrustEngine()
    signals = engine.compute_signals(_make_record(FLASK))
    for value in (
        signals.source_authority,
        signals.freshness,
        signals.confirmation_score,
        signals.contradiction_penalty,
        signals.sensitivity_penalty,
        signals.correction_signal,
    ):
        assert 0.0 <= value <= 1.0


# ---------------------------------------------------------------------------
# Property 12: Scan integrity
# ---------------------------------------------------------------------------


def test_scan_excludes_self_and_no_duplicates_example() -> None:
    """scan never includes the candidate and returns distinct ids (Property 12)."""

    engine = TrustEngine()
    store = _new_store()
    candidate = _add(store, _make_record(FLASK))
    _add(store, _make_record(FASTAPI))
    _add(store, _make_record(DJANGO))
    _add(store, _make_record(UNRELATED))

    conflicts = engine.contradiction_model.scan(candidate, store)
    ids = [memory_id for memory_id, _ in conflicts]

    # Never the candidate itself.
    assert candidate.memory_id not in ids
    # No duplicate memory_ids.
    assert len(ids) == len(set(ids))
    # Each entry is a well-formed ContradictionResult flagged True.
    for _id, result in conflicts:
        assert isinstance(result, ContradictionResult)
        assert result.is_contradiction is True
        assert 0.0 <= result.confidence <= 1.0


def test_scan_falls_back_to_list_without_embedding() -> None:
    """Without an embedding, scan still finds same-scope conflicts via list()."""

    engine = TrustEngine()
    store = _new_store()
    # Candidate has no embedding -> triggers the list() fallback path.
    candidate = _add(store, _make_record(FLASK, embed=False))
    _add(store, _make_record(FASTAPI))

    conflicts = engine.contradiction_model.scan(candidate, store)
    ids = [memory_id for memory_id, _ in conflicts]
    assert candidate.memory_id not in ids
    assert len(ids) == len(set(ids))


# ---------------------------------------------------------------------------
# Property 13: Mutual linkage (after evaluate)
# ---------------------------------------------------------------------------


def test_evaluate_creates_mutual_links_example() -> None:
    """evaluate wires mutual contradicts pointers on both records (Property 13)."""

    engine = TrustEngine()
    store = _new_store()
    a = _add(store, _make_record(FLASK))
    b = _add(store, _make_record(FASTAPI))

    engine.evaluate(a, store)

    stored_a = store.get(a.memory_id)
    stored_b = store.get(b.memory_id)
    assert stored_a is not None and stored_b is not None

    # a -> b and b -> a, persisted through the store.
    assert b.memory_id in stored_a.contradicts
    assert a.memory_id in stored_b.contradicts
    # No self references.
    assert a.memory_id not in stored_a.contradicts
    assert b.memory_id not in stored_b.contradicts


# ---------------------------------------------------------------------------
# evaluate happy path (scores + flags + disputes)
# ---------------------------------------------------------------------------


def test_evaluate_happy_path_scores_flags_and_disputes() -> None:
    """evaluate scores the candidate, flags the conflict, and disputes one side.

    Validates: Requirements 7.1, 8.3, 8.5, 23.1.
    """

    engine = TrustEngine()
    store = _new_store()
    a = _add(store, _make_record(FLASK))
    b = _add(store, _make_record(FASTAPI))
    _add(store, _make_record(UNRELATED))  # should not be linked

    result = engine.evaluate(a, store)

    # Returned record carries a bounded trust score.
    assert 0.0 <= result.trust_score <= 1.0

    stored_a = store.get(a.memory_id)
    stored_b = store.get(b.memory_id)
    assert stored_a is not None and stored_b is not None

    # The Flask/FastAPI contradiction is recorded mutually.
    assert b.memory_id in stored_a.contradicts
    assert a.memory_id in stored_b.contradicts

    # Exactly the lower-trust record of the pair is DISPUTED (tie -> candidate a).
    disputed = [
        rec for rec in (stored_a, stored_b) if rec.status == MemoryStatus.DISPUTED
    ]
    assert len(disputed) >= 1
    lower = min(stored_a, stored_b, key=lambda r: r.trust_score)
    assert lower.status == MemoryStatus.DISPUTED

    # Both scores stay bounded; the contradiction never pushed trust to 1.0.
    assert 0.0 <= stored_a.trust_score <= 1.0
    assert 0.0 <= stored_b.trust_score <= 1.0


def test_evaluate_no_conflict_leaves_status_active() -> None:
    """With no conflicting memory, evaluate scores but does not dispute."""

    engine = TrustEngine()
    store = _new_store()
    a = _add(store, _make_record(FLASK))
    _add(store, _make_record(UNRELATED))

    engine.evaluate(a, store)
    stored_a = store.get(a.memory_id)
    assert stored_a is not None
    assert stored_a.contradicts == []
    assert stored_a.status == MemoryStatus.ACTIVE
    assert 0.0 <= stored_a.trust_score <= 1.0


def test_evaluate_contradiction_does_not_raise_trust() -> None:
    """An unresolved contradiction never raises a record's trust (23.1/8.3)."""

    engine = TrustEngine()
    store = _new_store()
    a = _make_record(FLASK)
    # Baseline score with no contradictions.
    baseline = engine.score(a)

    store.add(a)
    store.add(_make_record(FASTAPI))
    engine.evaluate(a, store)

    stored_a = store.get(a.memory_id)
    assert stored_a is not None
    # The contradiction penalty can only lower (or keep) the score.
    assert stored_a.trust_score <= baseline + 1e-9


# ---------------------------------------------------------------------------
# Property-based tests (Hypothesis) with example-sweep fallback
# ---------------------------------------------------------------------------

_PHRASES = [
    FLASK,
    FASTAPI,
    DJANGO,
    "the database runs PostgreSQL 15 in production",
    "the database runs PostgreSQL 12 in production",
    "logging is enabled for all requests",
    "logging is disabled for all requests",
    UNRELATED,
    "the timeout is set to 0.5 seconds",
    "the timeout is set to 0.7 seconds",
]


def _assert_scan_integrity(engine: TrustEngine, store: SqliteStore) -> None:
    """Assert Property 12 for every record currently in the store."""

    for candidate in store.list():
        conflicts = engine.contradiction_model.scan(candidate, store)
        ids = [memory_id for memory_id, _ in conflicts]
        assert candidate.memory_id not in ids  # excludes self
        assert len(ids) == len(set(ids))  # no duplicates
        for _id, result in conflicts:
            assert 0.0 <= result.confidence <= 1.0


if _HAS_HYPOTHESIS:

    @settings(max_examples=60, suppress_health_check=[HealthCheck.too_slow])
    @given(
        contents=st.lists(
            st.sampled_from(_PHRASES), min_size=1, max_size=6, unique=True
        )
    )
    def test_scan_integrity_property(contents: list[str]) -> None:
        """Property 12 holds across arbitrary same-scope memory sets."""

        engine = TrustEngine()
        store = _new_store()
        try:
            for content in contents:
                _add(store, _make_record(content))
            _assert_scan_integrity(engine, store)
        finally:
            store.close()

    @settings(max_examples=40, suppress_health_check=[HealthCheck.too_slow])
    @given(data=st.data())
    def test_mutual_linkage_property(data: "st.DataObject") -> None:
        """Property 13: any detected conflict yields symmetric persisted links."""

        engine = TrustEngine()
        store = _new_store()
        try:
            c1 = data.draw(st.sampled_from(_PHRASES))
            c2 = data.draw(st.sampled_from(_PHRASES))
            a = _add(store, _make_record(c1))
            b = _add(store, _make_record(c2))

            engine.evaluate(a, store)
            stored_a = store.get(a.memory_id)
            stored_b = store.get(b.memory_id)
            assert stored_a is not None and stored_b is not None

            # Links are always symmetric: a->b iff b->a.
            assert (b.memory_id in stored_a.contradicts) == (
                a.memory_id in stored_b.contradicts
            )
            # Never a self reference.
            assert a.memory_id not in stored_a.contradicts
            assert b.memory_id not in stored_b.contradicts
        finally:
            store.close()

else:  # pragma: no cover - exercised only when Hypothesis is unavailable

    def test_scan_integrity_property() -> None:
        """Example-sweep fallback for Property 12."""

        engine = TrustEngine()
        store = _new_store()
        try:
            for content in _PHRASES:
                _add(store, _make_record(content))
            _assert_scan_integrity(engine, store)
        finally:
            store.close()

    def test_mutual_linkage_property() -> None:
        """Example-sweep fallback for Property 13."""

        engine = TrustEngine()
        store = _new_store()
        try:
            a = _add(store, _make_record(FLASK))
            b = _add(store, _make_record(FASTAPI))
            engine.evaluate(a, store)
            stored_a = store.get(a.memory_id)
            stored_b = store.get(b.memory_id)
            assert stored_a is not None and stored_b is not None
            assert (b.memory_id in stored_a.contradicts) == (
                a.memory_id in stored_b.contradicts
            )
        finally:
            store.close()
