# SPDX-License-Identifier: Apache-2.0
"""Tests for the rule-based contradiction detector (``RuleContradictionModel``).

Covers the documented contradiction invariants (design *Correctness Properties*)
and the canonical Flask-vs-FastAPI positive case:

* **Property 8: Symmetry** — ``detect(a, b).is_contradiction == detect(b, a).is_contradiction``
* **Property 9: Irreflexive** — ``detect(a, a).is_contradiction is False``
* **Property 10: Scope isolation** — disjoint ``scope_ref`` -> not a contradiction
* **Property 11: Confidence bounded** — ``0.0 <= confidence <= 1.0``

**Validates: Requirements 8.1, 8.4, 5.2, 23.3**

Property-based tests use Hypothesis when available and fall back to a small
deterministic example sweep otherwise, so the suite runs in minimal
environments. All checks run on-device with no external LLM API.
"""

from __future__ import annotations

from typing import Optional

import pytest

from memoryguard_core.models import (
    MemoryRecord,
    Scope,
    Sensitivity,
    SourceType,
    new_memory_record,
)
from memoryguard_core.store.base import MemoryStore
from memoryguard_core.trust.contradiction import (
    NEIGHBOR_LIMIT,
    SIM_THRESHOLD,
    RuleContradictionModel,
)
from memoryguard_models.base import ContradictionModel, ContradictionResult

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

# A shared detector instance (default LocalEmbedder is constructed lazily).
DETECTOR = RuleContradictionModel()


def _record(
    content: str,
    *,
    scope: Scope = Scope.PROJECT,
    scope_ref: Optional[str] = "proj-1",
    source_type: SourceType = SourceType.USER,
) -> MemoryRecord:
    """Build a valid, embedding-free :class:`MemoryRecord` for tests."""

    return new_memory_record(
        content=content,
        source_type=source_type,
        source_ref="user://tester",
        scope=scope,
        scope_ref=scope_ref,
    )


# ---------------------------------------------------------------------------
# Interface / identity
# ---------------------------------------------------------------------------


def test_is_contradiction_model_instance() -> None:
    """RuleContradictionModel honors the ContradictionModel interface."""

    assert isinstance(DETECTOR, ContradictionModel)


def test_model_info_identity() -> None:
    """info exposes the documented identity (task='contradiction')."""

    info = DETECTOR.info
    assert info.model_id == "contradiction/rules"
    assert info.task == "contradiction"
    assert info.version == "1.0.0"


def test_detect_returns_contradiction_result() -> None:
    """detect returns a well-formed ContradictionResult."""

    a = _record("the project uses Flask as its web framework")
    b = _record("the project uses FastAPI as its web framework")
    result = DETECTOR.detect(a, b)
    assert isinstance(result, ContradictionResult)
    assert isinstance(result.is_contradiction, bool)
    assert isinstance(result.confidence, float)


# ---------------------------------------------------------------------------
# Canonical positive case (design: "Project uses Flask" vs "uses FastAPI")
# ---------------------------------------------------------------------------


def test_flask_vs_fastapi_is_contradiction() -> None:
    """The design's Flask-vs-FastAPI pair is flagged as a contradiction.

    Validates: Requirements 8.4, 23.3.
    """

    a = _record("The project uses Flask as its web framework", scope_ref="my-app")
    b = _record("The project uses FastAPI as its web framework", scope_ref="my-app")
    result = DETECTOR.detect(a, b)
    assert result.is_contradiction is True
    assert 0.0 <= result.confidence <= 1.0
    assert result.reason  # non-empty explanation


def test_numeric_value_mismatch_is_contradiction() -> None:
    """A numeric/value mismatch on a shared subject is a contradiction."""

    a = _record("The database runs PostgreSQL 15 in production", scope_ref="my-app")
    b = _record("The database runs PostgreSQL 12 in production", scope_ref="my-app")
    result = DETECTOR.detect(a, b)
    assert result.is_contradiction is True


def test_negation_flip_is_contradiction() -> None:
    """A negation flip on otherwise-similar content is a contradiction."""

    a = _record("The service caches responses to speed up requests", scope_ref="my-app")
    b = _record(
        "The service does not cache responses to speed up requests",
        scope_ref="my-app",
    )
    result = DETECTOR.detect(a, b)
    assert result.is_contradiction is True


def test_agreeing_memories_not_contradiction() -> None:
    """Two agreeing, near-identical memories are not a contradiction."""

    a = _record("The project uses Flask as its web framework", scope_ref="my-app")
    b = _record("The project uses Flask as the web framework", scope_ref="my-app")
    result = DETECTOR.detect(a, b)
    assert result.is_contradiction is False
    assert 0.0 <= result.confidence <= 1.0


def test_unrelated_memories_not_contradiction() -> None:
    """Topically unrelated memories are not a contradiction (similarity gate)."""

    a = _record("The project uses Flask as its web framework", scope_ref="my-app")
    b = _record("Lunch is served at noon in the cafeteria", scope_ref="my-app")
    result = DETECTOR.detect(a, b)
    assert result.is_contradiction is False


# ---------------------------------------------------------------------------
# Property 10: Scope isolation (also example-based for clarity)
# ---------------------------------------------------------------------------


def test_disjoint_scope_ref_never_contradiction_example() -> None:
    """Disjoint scope_ref values are never a contradiction (Property 10)."""

    a = _record("The project uses Flask", scope_ref="app-a")
    b = _record("The project uses FastAPI", scope_ref="app-b")
    assert DETECTOR.detect(a, b).is_contradiction is False
    # Symmetric.
    assert DETECTOR.detect(b, a).is_contradiction is False


def test_different_scope_never_contradiction_example() -> None:
    """Different scopes are not comparable -> not a contradiction."""

    a = _record("uses Flask", scope=Scope.PROJECT, scope_ref="x")
    b = _record("uses FastAPI", scope=Scope.REPO, scope_ref="x")
    assert DETECTOR.detect(a, b).is_contradiction is False


# ---------------------------------------------------------------------------
# Property 9: Irreflexive
# ---------------------------------------------------------------------------


def test_irreflexive_same_object() -> None:
    """detect(a, a) is never a contradiction (Property 9)."""

    a = _record("The project uses Flask as its web framework")
    assert DETECTOR.detect(a, a).is_contradiction is False


def test_irreflexive_same_id_copy() -> None:
    """A record never contradicts a copy carrying the same memory_id."""

    a = _record("The project uses Flask as its web framework")
    b = MemoryRecord(
        memory_id=a.memory_id,
        content="The project uses FastAPI as its web framework",
        source_type=a.source_type,
        source_ref=a.source_ref,
        scope=a.scope,
        scope_ref=a.scope_ref,
    )
    assert DETECTOR.detect(a, b).is_contradiction is False


# ---------------------------------------------------------------------------
# Property-based tests (Hypothesis) with example-sweep fallback
# ---------------------------------------------------------------------------

# A pool of short phrases that mix agreeing, conflicting, and unrelated content.
_PHRASES = [
    "the project uses Flask as its web framework",
    "the project uses FastAPI as its web framework",
    "the project uses Django as its web framework",
    "the database runs PostgreSQL 15 in production",
    "the database runs MySQL 8 in production",
    "the service caches responses for speed",
    "the service does not cache responses for speed",
    "the api is public and documented",
    "the api is private and undocumented",
    "deployments happen every friday afternoon",
    "lunch is served at noon in the cafeteria",
    "the timeout is set to 0.5 seconds",
    "the timeout is set to 0.7 seconds",
    "logging is enabled for all requests",
    "logging is disabled for all requests",
]

_SCOPE_REFS = ["proj-1", "proj-2", "my-app", None]
_SCOPES = [Scope.PROJECT, Scope.GLOBAL, Scope.REPO]


def _valid_pair_kwargs(scope: Scope, scope_ref: Optional[str]) -> Optional[str]:
    """Return a usable scope_ref for ``scope`` (required for some scopes)."""

    if scope in (Scope.PROJECT, Scope.REPO, Scope.USER, Scope.SESSION):
        return scope_ref if scope_ref is not None else "proj-1"
    return scope_ref


def _check_invariants(
    detector: RuleContradictionModel,
    a: MemoryRecord,
    b: MemoryRecord,
) -> None:
    """Assert Properties 8/10/11 for a single pair (and 9 via a self-compare)."""

    r_ab = detector.detect(a, b)
    r_ba = detector.detect(b, a)

    # Property 8: Symmetry.
    assert r_ab.is_contradiction == r_ba.is_contradiction

    # Property 11: Confidence bounded.
    assert 0.0 <= r_ab.confidence <= 1.0
    assert 0.0 <= r_ba.confidence <= 1.0

    # Property 9: Irreflexive.
    assert detector.detect(a, a).is_contradiction is False

    # Property 10: Scope isolation — disjoint scope_ref => not a contradiction.
    same_scope = a.scope == b.scope
    refs_overlap = (a.scope_ref is None and b.scope_ref is None) or (
        a.scope_ref is not None
        and b.scope_ref is not None
        and str(a.scope_ref).strip() == str(b.scope_ref).strip()
    )
    if not (same_scope and refs_overlap):
        assert r_ab.is_contradiction is False


if _HAS_HYPOTHESIS:

    @st.composite
    def _records(draw: "st.DrawFn") -> MemoryRecord:
        content = draw(st.sampled_from(_PHRASES))
        scope = draw(st.sampled_from(_SCOPES))
        scope_ref = _valid_pair_kwargs(scope, draw(st.sampled_from(_SCOPE_REFS)))
        return _record(content, scope=scope, scope_ref=scope_ref)

    @settings(max_examples=200, suppress_health_check=[HealthCheck.too_slow])
    @given(a=_records(), b=_records())
    def test_contradiction_invariants_property(
        a: MemoryRecord, b: MemoryRecord
    ) -> None:
        """Properties 8, 9, 10, 11 hold across generated memory pairs."""

        _check_invariants(DETECTOR, a, b)

    @settings(max_examples=100, suppress_health_check=[HealthCheck.too_slow])
    @given(a=_records())
    def test_irreflexive_property(a: MemoryRecord) -> None:
        """Property 9: a record never contradicts itself, for any record."""

        assert DETECTOR.detect(a, a).is_contradiction is False

else:  # pragma: no cover - exercised only when Hypothesis is unavailable

    def test_contradiction_invariants_property() -> None:
        """Example-sweep fallback covering Properties 8, 9, 10, 11."""

        records = []
        for content in _PHRASES:
            for scope in _SCOPES:
                for scope_ref in _SCOPE_REFS:
                    ref = _valid_pair_kwargs(scope, scope_ref)
                    records.append(_record(content, scope=scope, scope_ref=ref))
        # Sweep a representative cross-product (bounded for runtime).
        for a in records[::3]:
            for b in records[::5]:
                _check_invariants(DETECTOR, a, b)


# ---------------------------------------------------------------------------
# Confidence bounds (explicit)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "content_a,content_b",
    [
        (
            "the project uses Flask as its web framework",
            "the project uses FastAPI as its web framework",
        ),
        (
            "the project uses Flask as its web framework",
            "lunch is served at noon",
        ),
        (
            "the project uses Flask",
            "the project uses Flask",
        ),
    ],
)
def test_confidence_is_bounded(content_a: str, content_b: str) -> None:
    """Property 11: confidence is always within [0, 1]."""

    a = _record(content_a, scope_ref="my-app")
    b = _record(content_b, scope_ref="my-app")
    assert 0.0 <= DETECTOR.detect(a, b).confidence <= 1.0


def test_sim_threshold_constant_in_unit_range() -> None:
    """The similarity threshold is a sane value in (0, 1)."""

    assert 0.0 < SIM_THRESHOLD < 1.0


# ---------------------------------------------------------------------------
# scan() over store neighbors (task 8.2)
#
# Property 12: Scan integrity — the returned list contains no duplicate
# memory_id values and never includes the candidate's own id.
#
# Validates: Requirements 8.2.
# ---------------------------------------------------------------------------


class _FakeStore(MemoryStore):
    """Minimal in-memory ``MemoryStore`` for exercising ``scan``.

    ``vector_search`` intentionally returns each record twice (and includes the
    candidate when present) so the de-duplication and self-exclusion guarantees
    are actually exercised rather than assumed.
    """

    def __init__(self, records: list[MemoryRecord]) -> None:
        self._records = records

    # -- unused-by-scan surface (abstract methods must still be implemented) --
    def add(self, record: MemoryRecord) -> MemoryRecord:  # pragma: no cover
        self._records.append(record)
        return record

    def get(self, memory_id: str) -> Optional[MemoryRecord]:  # pragma: no cover
        for r in self._records:
            if r.memory_id == memory_id:
                return r
        return None

    def update(self, record: MemoryRecord) -> MemoryRecord:  # pragma: no cover
        return record

    def soft_delete(self, memory_id: str) -> None:  # pragma: no cover
        return None

    def keyword_search(  # pragma: no cover - not used by scan
        self, query: str, limit: int
    ) -> list[tuple[MemoryRecord, float]]:
        return []

    # -- surface used by scan ---------------------------------------------
    def list(
        self,
        *,
        scope: Optional[Scope] = None,
        scope_ref: Optional[str] = None,
        status: Optional[Sensitivity] = None,
    ) -> list[MemoryRecord]:
        out: list[MemoryRecord] = []
        for r in self._records:
            if scope is not None and r.scope != scope:
                continue
            if scope_ref is not None and r.scope_ref != scope_ref:
                continue
            out.append(r)
        return out

    def vector_search(
        self, embedding: list[float], limit: int
    ) -> list[tuple[MemoryRecord, float]]:
        # Return every record twice to force duplicate ids into the candidate set.
        pairs = [(r, 1.0) for r in self._records]
        pairs = pairs + pairs
        return pairs[:limit] if limit else pairs


def _embedded_record(mid: str, content: str, embedding: Optional[list[float]]):
    """Build a record with a fixed memory_id and optional embedding."""

    return MemoryRecord(
        memory_id=mid,
        content=content,
        source_type=SourceType.USER,
        source_ref="user://tester",
        scope=Scope.PROJECT,
        scope_ref="proj-1",
        embedding=embedding,
    )


def test_scan_vector_path_detects_contradiction() -> None:
    """scan over vector neighbors flags a conflicting neighbor (Req 8.2)."""

    emb = [1.0, 0.0, 0.0]
    cand = _embedded_record("cand", "The project uses Flask", emb)
    conflict = _embedded_record("m1", "The project uses FastAPI", emb)
    agree = _embedded_record("m2", "The project uses Flask", emb)
    store = _FakeStore([cand, conflict, agree])

    results = DETECTOR.scan(cand, store)
    ids = [mid for mid, _ in results]

    assert "m1" in ids  # the conflicting neighbor is detected
    assert "m2" not in ids  # the agreeing neighbor is not
    for _mid, res in results:
        assert isinstance(res, ContradictionResult)
        assert res.is_contradiction is True


def test_scan_excludes_candidate_own_id() -> None:
    """scan never includes the candidate's own memory_id (Property 12)."""

    emb = [1.0, 0.0, 0.0]
    cand = _embedded_record("cand", "The project uses Flask", emb)
    # A self-copy with a *conflicting* body but the SAME id must still be excluded.
    self_like = _embedded_record("cand", "The project uses FastAPI", emb)
    store = _FakeStore([cand, self_like])

    ids = [mid for mid, _ in DETECTOR.scan(cand, store)]
    assert "cand" not in ids


def test_scan_returns_no_duplicate_ids() -> None:
    """scan de-duplicates neighbors even when the store yields repeats."""

    emb = [1.0, 0.0, 0.0]
    cand = _embedded_record("cand", "The project uses Flask", emb)
    conflict = _embedded_record("m1", "The project uses FastAPI", emb)
    store = _FakeStore([cand, conflict])  # vector_search returns m1 twice

    ids = [mid for mid, _ in DETECTOR.scan(cand, store)]
    assert ids == ["m1"]
    assert len(ids) == len(set(ids))


def test_scan_falls_back_to_list_without_embedding() -> None:
    """scan uses same-scope list() when the candidate has no embedding."""

    cand = _embedded_record("cand", "The project uses Flask", None)
    conflict = _embedded_record("m1", "The project uses FastAPI", None)
    store = _FakeStore([cand, conflict])

    ids = [mid for mid, _ in DETECTOR.scan(cand, store)]
    assert "cand" not in ids
    assert "m1" in ids
    assert len(ids) == len(set(ids))


def test_scan_empty_store_returns_empty_list() -> None:
    """scan returns an empty list when there are no other memories."""

    cand = _embedded_record("cand", "The project uses Flask", [1.0, 0.0, 0.0])
    store = _FakeStore([cand])
    assert DETECTOR.scan(cand, store) == []


def test_scan_neighbor_limit_is_positive() -> None:
    """The neighbor fan-out bound is a sane positive integer."""

    assert isinstance(NEIGHBOR_LIMIT, int)
    assert NEIGHBOR_LIMIT > 0
