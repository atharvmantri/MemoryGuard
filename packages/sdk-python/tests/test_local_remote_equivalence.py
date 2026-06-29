# SPDX-License-Identifier: Apache-2.0
"""Local vs remote conceptual-equivalence tests for the Python SDK.

These tests assert Requirement 11.4 — *"WHEN the Python_SDK operates in local
mode, THE Python_SDK SHALL produce the same conceptual results as the equivalent
REST_API operations"* — and Requirement 11.3 — results carry ``content``,
``trust_score``, ``source_ref`` and ``reasons``.

Strategy
--------
A single in-memory core engine is built once and shared by both clients:

* ``MemoryGuard.local(...)`` is pointed at that exact engine, and
* ``MemoryGuard.remote(...)`` talks to the **real** FastAPI app (built by
  ``create_app()``) whose ``get_engine`` dependency is overridden to return the
  *same* engine, reached over an in-process ``httpx.ASGITransport``.

Because both modes operate on the same store, running the same operation
(add/get/query/correct/delete/contradictions) through either path must yield
results whose conceptual fields (content, trust_score, source_ref, scope,
sensitivity, status, reasons) match field-by-field. This is the strongest form
of "same conceptual results".

The tests are skipped when ``httpx`` or the API package is unavailable.
"""

from __future__ import annotations

import importlib.util

import pytest

from memoryguard import (
    Contradiction,
    Memory,
    MemoryGuard,
    QueryResult,
    Scope,
    Sensitivity,
    SourceType,
)

HAS_HTTPX = importlib.util.find_spec("httpx") is not None
HAS_API = importlib.util.find_spec("memoryguard_api") is not None

pytestmark = pytest.mark.skipif(
    not (HAS_HTTPX and HAS_API),
    reason="requires httpx and the memoryguard_api package",
)


# ---------------------------------------------------------------------------
# Shared-engine fixture: local + remote over ONE in-memory store
# ---------------------------------------------------------------------------


@pytest.fixture()
def clients():
    """Yield ``(local, remote)`` clients backed by the same in-memory engine."""

    from fastapi.testclient import TestClient

    from memoryguard_core import build_local_engine

    from memoryguard_api.deps import get_engine
    from memoryguard_api.main import create_app

    # One shared engine/store for both modes. The store keeps a single long-lived
    # SQLite connection (required so a ``:memory:`` DB survives across calls).
    # FastAPI runs the sync route handlers in a threadpool, while the local client
    # calls on the main thread, so the shared connection must permit cross-thread
    # use. Calls are fully serialized in these tests (each request is awaited
    # before the next), so disabling SQLite's thread check is safe here and keeps
    # the engine, store, and API entirely real.
    import sqlite3
    from unittest import mock

    _real_connect = sqlite3.connect

    def _thread_safe_connect(*args, **kwargs):  # noqa: ANN002, ANN003
        kwargs.setdefault("check_same_thread", False)
        return _real_connect(*args, **kwargs)

    with mock.patch("sqlite3.connect", _thread_safe_connect):
        engine = build_local_engine(":memory:")

    # Real app, with the engine dependency overridden to the shared engine.
    app = create_app()
    app.dependency_overrides[get_engine] = lambda: engine

    # Local client pointed at the SAME engine (skip its own build).
    local = MemoryGuard.local(":memory:")
    local.backend.engine = engine

    # Remote client talking to the real app in-process. ``TestClient`` is a
    # synchronous ``httpx.Client`` that drives the ASGI app, so it slots
    # directly into the SDK's injectable ``client`` slot (its base_url is
    # ``http://testserver``).
    http_client = TestClient(app)
    remote = MemoryGuard.remote("http://testserver", token="test-token", client=http_client)

    try:
        yield local, remote
    finally:
        remote.close()
        http_client.close()
        app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# Field-by-field comparison helpers
# ---------------------------------------------------------------------------

# Conceptual memory fields that must match across modes (excludes server-set
# identity/timestamps which legitimately differ between distinct records).
_MEMORY_FIELDS = (
    "content",
    "source_type",
    "source_ref",
    "scope",
    "scope_ref",
    "sensitivity",
    "status",
)


def _assert_memory_conceptually_equal(
    a: Memory, b: Memory, *, same_identity: bool = True
) -> None:
    """Assert two ``Memory`` objects carry the same conceptual content.

    When ``same_identity`` is true (same underlying record) ``memory_id`` and
    ``trust_score`` must match exactly; otherwise only the descriptive fields and
    a (deterministically equal) ``trust_score`` are compared.
    """

    assert isinstance(a, Memory) and isinstance(b, Memory)
    for field in _MEMORY_FIELDS:
        assert getattr(a, field) == getattr(b, field), (
            f"field {field!r} differs: local={getattr(a, field)!r} "
            f"remote={getattr(b, field)!r}"
        )
    # trust_score is deterministic for identical inputs in either mode.
    assert a.trust_score == pytest.approx(b.trust_score)
    # Req 11.3: every memory carries content, trust_score, source_ref.
    assert a.content and b.content
    assert a.source_ref and b.source_ref
    assert 0.0 <= a.trust_score <= 1.0 and 0.0 <= b.trust_score <= 1.0
    if same_identity:
        assert a.memory_id == b.memory_id


def _seed_kwargs(
    content: str,
    source_ref: str,
    *,
    scope_ref: str = "billing-svc",
) -> dict:
    return dict(
        content=content,
        source_type=SourceType.FILE,
        source_ref=source_ref,
        scope=Scope.REPO,
        scope_ref=scope_ref,
        sensitivity=Sensitivity.INTERNAL,
    )


# ---------------------------------------------------------------------------
# add / get
# ---------------------------------------------------------------------------


def test_get_is_equivalent_across_modes(clients) -> None:
    """A record added once is read identically through local and remote."""

    local, remote = clients
    created = local.add(**_seed_kwargs("billing-svc uses PostgreSQL 15", "repo://b/README.md@c4a1"))

    via_local = local.get(created.memory_id)
    via_remote = remote.get(created.memory_id)

    assert via_local is not None and via_remote is not None
    # Same underlying record -> identical identity, content, trust, provenance.
    _assert_memory_conceptually_equal(via_local, via_remote, same_identity=True)
    assert via_local.memory_id == created.memory_id


def test_add_yields_equivalent_results_across_modes(clients) -> None:
    """``add`` of identical inputs produces conceptually equal memories."""

    local, remote = clients
    # Distinct source_refs avoid an incidental contradiction between the two.
    local_mem = local.add(**_seed_kwargs("payments-svc uses Redis for caching", "repo://p/A.md"))
    remote_mem = remote.add(**_seed_kwargs("payments-svc uses Redis for caching", "repo://p/A.md"))

    # Different records (distinct ids) but the same conceptual content + trust.
    assert local_mem.memory_id != remote_mem.memory_id
    _assert_memory_conceptually_equal(local_mem, remote_mem, same_identity=False)


def test_get_missing_returns_none_across_modes(clients) -> None:
    local, remote = clients
    missing = "00000000-0000-4000-8000-000000000000"
    assert local.get(missing) is None
    assert remote.get(missing) is None


# ---------------------------------------------------------------------------
# query
# ---------------------------------------------------------------------------


def test_query_results_are_equivalent_across_modes(clients) -> None:
    """The same query yields the same ranked content/trust/source_ref/reasons."""

    local, remote = clients
    local.add(
        **_seed_kwargs(
            "billing-svc uses PostgreSQL 15 as its primary database",
            "repo://billing-svc/README.md@c4a1",
        )
    )

    query_kwargs = dict(
        text="what database does billing use?",
        scope=Scope.REPO,
        scope_ref="billing-svc",
        min_trust=0.0,
        limit=5,
    )
    local_results = local.query(**query_kwargs)
    remote_results = remote.query(**query_kwargs)

    assert local_results, "expected at least one local result"
    assert len(local_results) == len(remote_results)

    for lr, rr in zip(local_results, remote_results):
        assert isinstance(lr, QueryResult) and isinstance(rr, QueryResult)
        _assert_memory_conceptually_equal(lr.memory, rr.memory, same_identity=True)
        # Req 11.3: results carry reasons, and they match across modes.
        assert lr.reasons and rr.reasons
        assert all(isinstance(r, str) for r in lr.reasons)
        assert lr.reasons == rr.reasons
        assert lr.relevance == pytest.approx(rr.relevance)
        assert lr.final_rank == pytest.approx(rr.final_rank)


def test_query_min_trust_floor_is_equivalent_across_modes(clients) -> None:
    """An impossibly high trust floor returns nothing in either mode."""

    local, remote = clients
    local.add(**_seed_kwargs("billing-svc uses PostgreSQL 15", "repo://billing-svc/README.md"))

    floor_kwargs = dict(
        text="billing database",
        scope=Scope.REPO,
        scope_ref="billing-svc",
        min_trust=1.01,
    )
    assert local.query(**floor_kwargs) == []
    assert remote.query(**floor_kwargs) == []


# ---------------------------------------------------------------------------
# correct  (exercises the remote PATCH body — must match UpdateMemoryRequest)
# ---------------------------------------------------------------------------


def test_correct_via_remote_matches_local_semantics(clients) -> None:
    """Remote ``correct`` works against the real API and matches local lineage.

    This is the regression guard for the PATCH body bug: the API's
    ``UpdateMemoryRequest`` expects a ``content`` field, so the remote client
    must send ``{"content": ...}`` (not ``{"new_content": ...}``).
    """

    local, remote = clients
    original = local.add(**_seed_kwargs("billing-svc uses PostgreSQL 15", "repo://billing-svc/README.md@c4a1"))

    # Correct through the REMOTE client (hits the real PATCH route).
    corrected = remote.correct(original.memory_id, "billing-svc uses PostgreSQL 16")

    assert isinstance(corrected, Memory)
    assert corrected.memory_id != original.memory_id
    assert corrected.content == "billing-svc uses PostgreSQL 16"
    assert corrected.source_ref == original.source_ref

    # Prior record is retained and transitioned out of ACTIVE (corrected lineage),
    # observable identically through both modes since they share the store.
    prior_local = local.get(original.memory_id)
    prior_remote = remote.get(original.memory_id)
    assert prior_local is not None and prior_remote is not None
    assert prior_local.status == "corrected"
    _assert_memory_conceptually_equal(prior_local, prior_remote, same_identity=True)

    # The corrected record is visible and identical across modes.
    corrected_local = local.get(corrected.memory_id)
    assert corrected_local is not None
    _assert_memory_conceptually_equal(corrected_local, corrected, same_identity=True)


def test_correct_local_and_remote_produce_equivalent_records(clients) -> None:
    """Correcting equivalent records via each mode yields equivalent results.

    Each correction runs in its own isolated scope so the shared store's
    contradiction detection cannot cross-link them; the corrected records are
    then compared on every conceptual field except the intentionally-different
    ``scope_ref``.
    """

    local, remote = clients
    a = local.add(**_seed_kwargs("service alpha uses MySQL 5.7", "repo://alpha/SVC.md", scope_ref="corr-local"))
    b = remote.add(**_seed_kwargs("service alpha uses MySQL 5.7", "repo://alpha/SVC.md", scope_ref="corr-remote"))

    local_corrected = local.correct(a.memory_id, "service alpha uses MySQL 8.0")
    remote_corrected = remote.correct(b.memory_id, "service alpha uses MySQL 8.0")

    assert local_corrected.content == remote_corrected.content == "service alpha uses MySQL 8.0"
    assert local_corrected.status == remote_corrected.status == "active"
    for field in ("content", "source_type", "source_ref", "scope", "sensitivity", "status"):
        assert getattr(local_corrected, field) == getattr(remote_corrected, field)
    assert local_corrected.trust_score == pytest.approx(remote_corrected.trust_score)


# ---------------------------------------------------------------------------
# delete
# ---------------------------------------------------------------------------


def test_delete_via_remote_soft_deletes_and_is_visible_locally(clients) -> None:
    local, remote = clients
    mem = local.add(**_seed_kwargs("ephemeral note about the cache layer", "repo://x/N.md", scope_ref="x"))

    remote.delete(mem.memory_id)

    # Soft-delete: still retrievable, marked deleted, identical across modes.
    after_local = local.get(mem.memory_id)
    after_remote = remote.get(mem.memory_id)
    assert after_local is not None and after_remote is not None
    assert after_local.status == "deleted"
    _assert_memory_conceptually_equal(after_local, after_remote, same_identity=True)


# ---------------------------------------------------------------------------
# contradictions
# ---------------------------------------------------------------------------


def test_contradictions_are_equivalent_across_modes(clients) -> None:
    """A contradictory pair yields the same linked conflicts in both modes."""

    local, remote = clients
    first = local.add(**_seed_kwargs("billing-svc uses PostgreSQL 15", "repo://billing-svc/README.md@c4a1"))
    second = local.add(**_seed_kwargs("billing-svc uses PostgreSQL 16", "repo://billing-svc/README.md@d9f2"))

    local_conflicts = local.contradictions(second.memory_id)
    remote_conflicts = remote.contradictions(second.memory_id)

    assert local_conflicts, "expected a detected contradiction"
    assert all(isinstance(c, Contradiction) for c in local_conflicts + remote_conflicts)

    # Same linked memory ids surfaced in both modes.
    local_ids = {c.memory_id for c in local_conflicts}
    remote_ids = {c.memory_id for c in remote_conflicts}
    assert first.memory_id in local_ids
    assert local_ids == remote_ids

    # And the per-link conceptual fields match.
    local_by_id = {c.memory_id: c for c in local_conflicts}
    remote_by_id = {c.memory_id: c for c in remote_conflicts}
    for mem_id, lc in local_by_id.items():
        rc = remote_by_id[mem_id]
        assert lc.reason == rc.reason
        assert lc.confidence == pytest.approx(rc.confidence)
        assert 0.0 <= lc.confidence <= 1.0
        assert lc.source_ref == rc.source_ref
