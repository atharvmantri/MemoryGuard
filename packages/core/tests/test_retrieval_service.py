# SPDX-License-Identifier: Apache-2.0
"""Property tests for the two-stage ``RetrievalService`` (``retrieval/service.py``).

Exercises the design's *Component: Retrieval & Policy Layer* end to end against
``SqliteStore(":memory:")`` + the default ``LocalEmbedder`` + the OSS
``HeuristicReranker`` (no external API, no files written — the service defaults
to ``NullAuditSink``). Covers the design's correctness properties:

* **Property 14 — Trust floor**: every returned memory's ``trust_score`` is
  ``>= min_trust``.
* **Property 15 — Scope containment**: when a ``scope`` / ``scope_ref`` is
  specified, every returned memory matches it.
* **Property 16 — No expired/deleted leakage**: no returned memory has status
  ``expired`` or ``deleted``.
* **Property 17 — Sensitivity ceiling**: every returned memory's sensitivity tier
  is ``<= max_sensitivity`` under ``public < internal < secret < pii``.
* **Property 18 — Ranking order**: results are ordered by ``final_rank``
  descending.
* **Property 19 — Explainability**: every returned memory carries ``>= 1``
  human-readable reason.

**Validates: Requirements 5.1, 5.2, 5.3, 5.4, 6.1, 9.1**
"""

from __future__ import annotations

from typing import Optional


from memoryguard_core.models import (
    MemoryStatus,
    Scope,
    Sensitivity,
    SourceType,
    new_memory_record,
)
from memoryguard_core.retrieval import (
    QuerySpec,
    RetrievalService,
    RetrievedMemory,
)
from memoryguard_core.retrieval.service import _sensitivity_rank
from memoryguard_core.store import SqliteStore
from memoryguard_models.embedder.local_embedder import LocalEmbedder
from memoryguard_models.reranker.heuristic import HeuristicReranker

# Optional Hypothesis support (graceful fallback to example-based testing).
try:  # pragma: no cover - import guard
    from hypothesis import given, settings
    from hypothesis import strategies as st

    _HAS_HYPOTHESIS = True
except Exception:  # pragma: no cover - Hypothesis not installed
    _HAS_HYPOTHESIS = False


# ---------------------------------------------------------------------------
# Shared vocabulary + helpers
# ---------------------------------------------------------------------------

# A small fixed vocabulary so generated content reliably overlaps the query
# (the query is built from the same words), giving non-trivial relevance.
_VOCAB = [
    "alpha",
    "beta",
    "gamma",
    "delta",
    "retrieval",
    "ranking",
    "trust",
    "scope",
    "memory",
    "vault",
]

_QUERY = "alpha beta retrieval ranking trust memory"

_ALL_SENSITIVITIES = [
    Sensitivity.PUBLIC,
    Sensitivity.INTERNAL,
    Sensitivity.SECRET,
    Sensitivity.PII,
]

_ALL_STATUSES = [
    MemoryStatus.ACTIVE,
    MemoryStatus.CORRECTED,
    MemoryStatus.EXPIRED,
    MemoryStatus.DELETED,
    MemoryStatus.DISPUTED,
]


def _make_service(
    specs: list[dict],
) -> tuple[RetrievalService, SqliteStore, LocalEmbedder]:
    """Build a service over a fresh in-memory store seeded from ``specs``.

    Each spec dict may carry: ``content`` (str), ``scope`` (Scope), ``scope_ref``
    (str|None), ``trust`` (float), ``sensitivity`` (Sensitivity), ``status``
    (MemoryStatus). Content is embedded with the shared ``LocalEmbedder``.
    """
    embedder = LocalEmbedder()
    store = SqliteStore(":memory:")
    for spec in specs:
        content = spec.get("content") or "alpha memory"
        record = new_memory_record(
            content=content,
            source_type=SourceType.USER,
            source_ref="user://tester",
            scope=spec.get("scope", Scope.PROJECT),
            scope_ref=spec.get("scope_ref", "proj-1"),
            trust_score=spec.get("trust", 0.8),
            sensitivity=spec.get("sensitivity", Sensitivity.PUBLIC),
            status=spec.get("status", MemoryStatus.ACTIVE),
            embedding=embedder.embed(content),
        )
        store.add(record)
    service = RetrievalService(store, embedder, HeuristicReranker())
    return service, store, embedder


def _content_from_indices(indices: list[int]) -> str:
    """Build content from vocabulary ``indices`` (always non-empty)."""
    words = [_VOCAB[i % len(_VOCAB)] for i in indices] or ["alpha"]
    return " ".join(words)


# ---------------------------------------------------------------------------
# Core property checks (plain Python; reused by Hypothesis + fallbacks)
# ---------------------------------------------------------------------------


def _check_trust_floor(specs: list[dict], min_trust: float) -> None:
    """Property 14: returned memories all clear the trust floor."""
    service, _store, _embedder = _make_service(specs)
    spec = QuerySpec(
        text=_QUERY,
        min_trust=min_trust,
        max_sensitivity=Sensitivity.PII,  # never exclude on sensitivity here
        limit=50,
    )
    results = service.query(spec)
    for rm in results:
        # Records are seeded with trust_score > 0, so the reranker's trust judgement
        # equals the record's trust_score; the floor must therefore hold on it.
        assert rm.record.trust_score >= min_trust - 1e-9, (
            f"{rm.record.memory_id} trust {rm.record.trust_score} < min_trust {min_trust}"
        )


def _check_scope_containment(
    specs: list[dict], scope: Scope, scope_ref: Optional[str]
) -> None:
    """Property 15: returned memories match the requested scope/scope_ref."""
    service, _store, _embedder = _make_service(specs)
    spec = QuerySpec(
        text=_QUERY,
        scope=scope,
        scope_ref=scope_ref,
        max_sensitivity=Sensitivity.PII,
        limit=50,
    )
    results = service.query(spec)
    for rm in results:
        assert rm.record.scope == scope
        if scope_ref is not None:
            assert rm.record.scope_ref == scope_ref


def _check_no_expired_deleted(specs: list[dict]) -> None:
    """Property 16: no expired/deleted memory is ever surfaced."""
    service, _store, _embedder = _make_service(specs)
    spec = QuerySpec(text=_QUERY, max_sensitivity=Sensitivity.PII, limit=50)
    results = service.query(spec)
    for rm in results:
        assert rm.record.status not in (MemoryStatus.EXPIRED, MemoryStatus.DELETED)


def _check_sensitivity_ceiling(specs: list[dict], ceiling: Sensitivity) -> None:
    """Property 17: returned memories never exceed the sensitivity ceiling."""
    service, _store, _embedder = _make_service(specs)
    spec = QuerySpec(text=_QUERY, max_sensitivity=ceiling, limit=50)
    results = service.query(spec)
    for rm in results:
        assert _sensitivity_rank(rm.record.sensitivity) <= _sensitivity_rank(ceiling)


def _check_ranking_order(specs: list[dict]) -> None:
    """Property 18: results are ordered by final_rank descending."""
    service, _store, _embedder = _make_service(specs)
    spec = QuerySpec(text=_QUERY, max_sensitivity=Sensitivity.PII, limit=50)
    results = service.query(spec)
    ranks = [rm.final_rank for rm in results]
    assert ranks == sorted(ranks, reverse=True)


def _check_explainability(specs: list[dict]) -> None:
    """Property 19: every result carries at least one reason."""
    service, _store, _embedder = _make_service(specs)
    spec = QuerySpec(text=_QUERY, max_sensitivity=Sensitivity.PII, limit=50)
    results = service.query(spec)
    for rm in results:
        assert isinstance(rm, RetrievedMemory)
        assert len(rm.reasons) >= 1
        assert all(isinstance(r, str) and r.strip() for r in rm.reasons)


# ---------------------------------------------------------------------------
# Deterministic example tests (always run, even without Hypothesis)
# ---------------------------------------------------------------------------


def test_trust_floor_example() -> None:
    specs = [
        {"content": "alpha retrieval ranking", "trust": 0.9},
        {"content": "beta trust memory", "trust": 0.5},
        {"content": "gamma retrieval trust", "trust": 0.2},
    ]
    _check_trust_floor(specs, min_trust=0.5)


def test_scope_containment_example() -> None:
    specs = [
        {"content": "alpha retrieval", "scope": Scope.PROJECT, "scope_ref": "p1"},
        {"content": "beta ranking", "scope": Scope.PROJECT, "scope_ref": "p2"},
        {"content": "gamma trust memory", "scope": Scope.GLOBAL, "scope_ref": None},
    ]
    _check_scope_containment(specs, Scope.PROJECT, "p1")


def test_no_expired_deleted_example() -> None:
    specs = [
        {"content": "alpha retrieval", "status": MemoryStatus.ACTIVE},
        {"content": "beta ranking", "status": MemoryStatus.EXPIRED},
        {"content": "gamma trust", "status": MemoryStatus.DELETED},
        {"content": "delta memory", "status": MemoryStatus.DISPUTED},
    ]
    _check_no_expired_deleted(specs)


def test_sensitivity_ceiling_example() -> None:
    specs = [
        {"content": "alpha retrieval", "sensitivity": Sensitivity.PUBLIC},
        {"content": "beta ranking", "sensitivity": Sensitivity.INTERNAL},
        {"content": "gamma trust", "sensitivity": Sensitivity.SECRET},
        {"content": "delta memory", "sensitivity": Sensitivity.PII},
    ]
    _check_sensitivity_ceiling(specs, Sensitivity.INTERNAL)


def test_ranking_order_example() -> None:
    specs = [
        {"content": "alpha retrieval ranking trust memory", "trust": 0.9},
        {"content": "beta retrieval ranking", "trust": 0.6},
        {"content": "gamma trust", "trust": 0.4},
        {"content": "delta memory alpha", "trust": 0.8},
    ]
    _check_ranking_order(specs)


def test_explainability_example() -> None:
    specs = [
        {"content": "alpha retrieval ranking trust", "trust": 0.9},
        {"content": "beta memory", "trust": 0.7},
    ]
    _check_explainability(specs)


def test_policy_provider_rejection_recorded() -> None:
    """Requirement 5.5: an injected PolicyProvider can reject + give reasons."""
    from memoryguard_core.retrieval import PolicyProvider

    class _RejectAll(PolicyProvider):
        def evaluate(self, record, ctx):
            return False, ["blocked by test policy"]

    embedder = LocalEmbedder()
    store = SqliteStore(":memory:")
    rec = new_memory_record(
        content="alpha retrieval ranking trust memory",
        source_type=SourceType.USER,
        source_ref="user://tester",
        scope=Scope.PROJECT,
        scope_ref="proj-1",
        trust_score=0.9,
        sensitivity=Sensitivity.PUBLIC,
        embedding=embedder.embed("alpha retrieval ranking trust memory"),
    )
    store.add(rec)
    service = RetrievalService(store, embedder, HeuristicReranker(), policy=_RejectAll())
    results = service.query(QuerySpec(text=_QUERY, max_sensitivity=Sensitivity.PII))
    assert results == []


def test_limit_caps_results() -> None:
    """Requirement 4.5: no more than ``limit`` results are returned."""
    specs = [
        {"content": f"alpha retrieval ranking trust memory item {i}", "trust": 0.8}
        for i in range(8)
    ]
    service, _store, _embedder = _make_service(specs)
    results = service.query(QuerySpec(text=_QUERY, max_sensitivity=Sensitivity.PII, limit=3))
    assert len(results) <= 3


def test_audit_event_emitted_once() -> None:
    """Requirement 6.3: exactly one audit event per query, with ids + reasons."""

    class _CapturingSink:
        def __init__(self) -> None:
            self.events: list[dict] = []

        def record(self, event: dict) -> None:
            self.events.append(event)

    embedder = LocalEmbedder()
    store = SqliteStore(":memory:")
    rec = new_memory_record(
        content="alpha retrieval ranking trust memory",
        source_type=SourceType.USER,
        source_ref="user://tester",
        scope=Scope.PROJECT,
        scope_ref="proj-1",
        trust_score=0.9,
        sensitivity=Sensitivity.PUBLIC,
        embedding=embedder.embed("alpha retrieval ranking trust memory"),
    )
    store.add(rec)
    sink = _CapturingSink()
    service = RetrievalService(store, embedder, HeuristicReranker(), audit=sink)
    service.query(QuerySpec(text=_QUERY, max_sensitivity=Sensitivity.PII))

    assert len(sink.events) == 1
    event = sink.events[0]
    assert "query_id" in event and event["query_id"]
    assert "used_ids" in event and "rejected_ids" in event and "reasons" in event


# ---------------------------------------------------------------------------
# Hypothesis property tests (smart generators constrained to the input space)
# ---------------------------------------------------------------------------

if _HAS_HYPOTHESIS:

    # A content generator: 1..5 vocabulary indices -> overlapping content.
    _content_st = st.lists(
        st.integers(min_value=0, max_value=len(_VOCAB) - 1),
        min_size=1,
        max_size=5,
    ).map(_content_from_indices)

    # Trust seeded strictly > 0 so the reranker's trust == record.trust_score.
    _trust_st = st.floats(min_value=0.05, max_value=1.0)

    def _spec_st(
        *,
        scope: Optional[Scope] = None,
        scope_ref_choices: Optional[list[str]] = None,
        sensitivity: bool = False,
        status: bool = False,
    ) -> "st.SearchStrategy":
        """Build a strategy producing one record-spec dict."""
        fields = {
            "content": _content_st,
            "trust": _trust_st,
        }
        if scope is not None:
            fields["scope"] = st.just(scope)
        if scope_ref_choices is not None:
            fields["scope_ref"] = st.sampled_from(scope_ref_choices)
        if sensitivity:
            fields["sensitivity"] = st.sampled_from(_ALL_SENSITIVITIES)
        if status:
            fields["status"] = st.sampled_from(_ALL_STATUSES)
        return st.fixed_dictionaries(fields)

    @settings(max_examples=40, deadline=None)
    @given(
        specs=st.lists(_spec_st(), min_size=1, max_size=8),
        min_trust=st.floats(min_value=0.0, max_value=1.0),
    )
    def test_property_14_trust_floor(specs: list[dict], min_trust: float) -> None:
        """Property 14: every returned memory clears ``min_trust``."""
        _check_trust_floor(specs, min_trust)

    @settings(max_examples=40, deadline=None)
    @given(
        specs=st.lists(
            _spec_st(scope=Scope.PROJECT, scope_ref_choices=["p1", "p2", "p3"]),
            min_size=1,
            max_size=8,
        )
    )
    def test_property_15_scope_containment(specs: list[dict]) -> None:
        """Property 15: returned memories match the queried scope/scope_ref."""
        _check_scope_containment(specs, Scope.PROJECT, "p1")

    @settings(max_examples=40, deadline=None)
    @given(specs=st.lists(_spec_st(status=True), min_size=1, max_size=8))
    def test_property_16_no_expired_deleted(specs: list[dict]) -> None:
        """Property 16: expired/deleted memories never leak into results."""
        _check_no_expired_deleted(specs)

    @settings(max_examples=40, deadline=None)
    @given(
        specs=st.lists(_spec_st(sensitivity=True), min_size=1, max_size=8),
        ceiling=st.sampled_from(_ALL_SENSITIVITIES),
    )
    def test_property_17_sensitivity_ceiling(
        specs: list[dict], ceiling: Sensitivity
    ) -> None:
        """Property 17: returned memories never exceed the sensitivity ceiling."""
        _check_sensitivity_ceiling(specs, ceiling)

    @settings(max_examples=40, deadline=None)
    @given(specs=st.lists(_spec_st(sensitivity=True, status=True), min_size=1, max_size=8))
    def test_property_18_ranking_order(specs: list[dict]) -> None:
        """Property 18: results are ordered by ``final_rank`` descending."""
        _check_ranking_order(specs)

    @settings(max_examples=40, deadline=None)
    @given(specs=st.lists(_spec_st(), min_size=1, max_size=8))
    def test_property_19_explainability(specs: list[dict]) -> None:
        """Property 19: every returned memory carries >= 1 reason."""
        _check_explainability(specs)

else:  # pragma: no cover - deterministic fallback when Hypothesis is absent

    def test_property_14_19_fallback() -> None:
        """Deterministic fallback covering Properties 14-19 without Hypothesis."""
        specs = [
            {"content": "alpha retrieval ranking", "trust": 0.9,
             "sensitivity": Sensitivity.PUBLIC, "status": MemoryStatus.ACTIVE,
             "scope": Scope.PROJECT, "scope_ref": "p1"},
            {"content": "beta trust memory", "trust": 0.5,
             "sensitivity": Sensitivity.SECRET, "status": MemoryStatus.EXPIRED,
             "scope": Scope.PROJECT, "scope_ref": "p2"},
            {"content": "gamma retrieval trust", "trust": 0.3,
             "sensitivity": Sensitivity.PII, "status": MemoryStatus.DELETED,
             "scope": Scope.PROJECT, "scope_ref": "p1"},
        ]
        _check_trust_floor(specs, 0.5)
        _check_scope_containment(specs, Scope.PROJECT, "p1")
        _check_no_expired_deleted(specs)
        _check_sensitivity_ceiling(specs, Sensitivity.INTERNAL)
        _check_ranking_order(specs)
        _check_explainability(specs)
