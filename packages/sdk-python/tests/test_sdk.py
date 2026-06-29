# SPDX-License-Identifier: Apache-2.0
"""Tests for the MemoryGuard Python SDK (Requirements 11.1-11.4).

The local client is exercised end to end against an in-memory store, and the
remote client is smoke-tested against a mocked ``httpx`` transport (skipped when
``httpx`` is unavailable). Together they assert that local mode produces the
same conceptual results as the equivalent REST operations.
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


# ---------------------------------------------------------------------------
# Local client fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def mg() -> MemoryGuard:
    """A fresh local client backed by an in-memory store."""

    return MemoryGuard.local(":memory:")


# ---------------------------------------------------------------------------
# Local client — core operations
# ---------------------------------------------------------------------------


def test_local_mode_is_local(mg: MemoryGuard) -> None:
    assert mg.mode == "local"


def test_add_then_get_round_trip(mg: MemoryGuard) -> None:
    mem = mg.add(
        content="billing-svc uses PostgreSQL 15",
        source_type=SourceType.FILE,
        source_ref="repo://billing-svc/README.md@c4a1",
        scope=Scope.REPO,
        scope_ref="billing-svc",
        sensitivity=Sensitivity.INTERNAL,
    )
    assert isinstance(mem, Memory)
    assert mem.memory_id
    assert mem.content == "billing-svc uses PostgreSQL 15"
    assert mem.source_ref == "repo://billing-svc/README.md@c4a1"
    assert 0.0 <= mem.trust_score <= 1.0

    fetched = mg.get(mem.memory_id)
    assert fetched is not None
    assert fetched.memory_id == mem.memory_id
    assert fetched.content == mem.content
    assert fetched.source_ref == mem.source_ref


def test_get_missing_returns_none(mg: MemoryGuard) -> None:
    assert mg.get("00000000-0000-4000-8000-000000000000") is None


def test_add_accepts_string_enum_values(mg: MemoryGuard) -> None:
    """Enums may be passed as their string values (REST-equivalent inputs)."""

    mem = mg.add(
        content="payments-svc uses Redis for caching",
        source_type="file",
        source_ref="repo://payments-svc/NOTES.md",
        scope="repo",
        scope_ref="payments-svc",
        sensitivity="internal",
    )
    assert mem.source_type == "file"
    assert mem.scope == "repo"
    assert mem.sensitivity == "internal"


def test_query_returns_results_with_memory_and_reasons(mg: MemoryGuard) -> None:
    mg.add(
        content="billing-svc uses PostgreSQL 15 as its primary database",
        source_type=SourceType.FILE,
        source_ref="repo://billing-svc/README.md@c4a1",
        scope=Scope.REPO,
        scope_ref="billing-svc",
        sensitivity=Sensitivity.INTERNAL,
    )

    results = mg.query(
        "what database does billing use?",
        scope=Scope.REPO,
        scope_ref="billing-svc",
        min_trust=0.0,
        limit=5,
    )

    assert results, "expected at least one result"
    top = results[0]
    assert isinstance(top, QueryResult)
    # Req 11.3: results carry content, trust_score, source_ref, reasons.
    assert top.memory.content
    assert isinstance(top.memory.trust_score, float)
    assert 0.0 <= top.memory.trust_score <= 1.0
    assert top.memory.source_ref == "repo://billing-svc/README.md@c4a1"
    assert isinstance(top.reasons, list)
    assert top.reasons and all(isinstance(r, str) for r in top.reasons)


def test_query_min_trust_floor_excludes_low_trust(mg: MemoryGuard) -> None:
    mg.add(
        content="billing-svc uses PostgreSQL 15",
        source_type=SourceType.FILE,
        source_ref="repo://billing-svc/README.md",
        scope=Scope.REPO,
        scope_ref="billing-svc",
    )
    # An impossibly high trust floor yields no results.
    results = mg.query(
        "billing database",
        scope=Scope.REPO,
        scope_ref="billing-svc",
        min_trust=1.01,
    )
    assert results == []


def test_ingest_path_on_tmp_file(tmp_path, mg: MemoryGuard) -> None:
    doc = tmp_path / "notes.md"
    doc.write_text(
        "The billing service stores invoices in PostgreSQL.\n"
        "It exposes a REST API on port 8080.\n",
        encoding="utf-8",
    )

    created = mg.ingest_path(str(doc), scope=Scope.REPO, scope_ref="billing-svc")
    assert created, "ingestion should create at least one memory"
    assert all(isinstance(m, Memory) for m in created)
    assert all(m.scope == "repo" for m in created)
    assert all(m.scope_ref == "billing-svc" for m in created)
    assert all(m.source_ref for m in created)


def test_correct_creates_lineage(mg: MemoryGuard) -> None:
    mem = mg.add(
        content="billing-svc uses PostgreSQL 15",
        source_type=SourceType.FILE,
        source_ref="repo://billing-svc/README.md@c4a1",
        scope=Scope.REPO,
        scope_ref="billing-svc",
    )

    corrected = mg.correct(mem.memory_id, "billing-svc uses PostgreSQL 16")
    assert corrected.memory_id != mem.memory_id
    assert corrected.content == "billing-svc uses PostgreSQL 16"

    # The prior record is retained and transitioned out of ACTIVE (lineage).
    prior = mg.get(mem.memory_id)
    assert prior is not None
    assert prior.status == "corrected"


def test_delete_soft_deletes(mg: MemoryGuard) -> None:
    mem = mg.add(
        content="ephemeral note about the cache layer",
        source_type=SourceType.USER,
        source_ref="user://alice",
        scope=Scope.GLOBAL,
    )
    mg.delete(mem.memory_id)

    # Soft-delete: still retrievable, but marked deleted.
    after = mg.get(mem.memory_id)
    assert after is not None
    assert after.status == "deleted"


def test_contradictions_returns_conflicts_for_contradictory_pair(
    mg: MemoryGuard,
) -> None:
    first = mg.add(
        content="billing-svc uses PostgreSQL 15",
        source_type=SourceType.FILE,
        source_ref="repo://billing-svc/README.md@c4a1",
        scope=Scope.REPO,
        scope_ref="billing-svc",
    )
    second = mg.add(
        content="billing-svc uses PostgreSQL 16",
        source_type=SourceType.FILE,
        source_ref="repo://billing-svc/README.md@d9f2",
        scope=Scope.REPO,
        scope_ref="billing-svc",
    )

    conflicts = mg.contradictions(second.memory_id)
    assert conflicts, "expected a detected contradiction"
    assert all(isinstance(c, Contradiction) for c in conflicts)
    linked_ids = {c.memory_id for c in conflicts}
    assert first.memory_id in linked_ids
    target = next(c for c in conflicts if c.memory_id == first.memory_id)
    assert 0.0 <= target.confidence <= 1.0
    assert target.reason


# ---------------------------------------------------------------------------
# Remote client — smoke test against a mocked transport
# ---------------------------------------------------------------------------


@pytest.mark.skipif(not HAS_HTTPX, reason="httpx not installed")
def test_remote_client_against_mock_transport() -> None:
    import httpx

    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["auth"] = request.headers.get("Authorization")
        path = request.url.path
        method = request.method

        if method == "POST" and path == "/v1/memories":
            return httpx.Response(
                201,
                json={
                    "memory_id": "11111111-1111-4111-8111-111111111111",
                    "content": "billing-svc uses PostgreSQL 15",
                    "source_type": "file",
                    "source_ref": "repo://billing-svc/README.md@c4a1",
                    "scope": "repo",
                    "scope_ref": "billing-svc",
                    "sensitivity": "internal",
                    "status": "active",
                    "trust_score": 0.72,
                },
            )
        if method == "POST" and path == "/v1/query":
            return httpx.Response(
                200,
                json={
                    "results": [
                        {
                            "memory": {
                                "memory_id": "11111111-1111-4111-8111-111111111111",
                                "content": "billing-svc uses PostgreSQL 15",
                                "source_ref": "repo://billing-svc/README.md@c4a1",
                                "trust_score": 0.72,
                            },
                            "reasons": ["source: repo://billing-svc/README.md@c4a1"],
                        }
                    ]
                },
            )
        if method == "GET" and path.startswith("/v1/memories/"):
            return httpx.Response(
                200,
                json={
                    "memory_id": "11111111-1111-4111-8111-111111111111",
                    "content": "billing-svc uses PostgreSQL 15",
                    "source_ref": "repo://billing-svc/README.md@c4a1",
                    "trust_score": 0.72,
                },
            )
        return httpx.Response(404, json={"error": "not_found"})

    transport = httpx.MockTransport(handler)
    client = httpx.Client(base_url="https://api.example.test", transport=transport)
    mg = MemoryGuard.remote("https://api.example.test", token="secret-token", client=client)

    assert mg.mode == "remote"

    mem = mg.add(
        content="billing-svc uses PostgreSQL 15",
        source_type=SourceType.FILE,
        source_ref="repo://billing-svc/README.md@c4a1",
        scope=Scope.REPO,
        scope_ref="billing-svc",
    )
    assert mem.memory_id == "11111111-1111-4111-8111-111111111111"
    assert mem.trust_score == pytest.approx(0.72)
    # Req 11.2: bearer token is forwarded.
    assert captured["auth"] == "Bearer secret-token"

    results = mg.query("what db?", scope=Scope.REPO, scope_ref="billing-svc")
    assert len(results) == 1
    assert results[0].memory.source_ref == "repo://billing-svc/README.md@c4a1"
    assert results[0].memory.trust_score == pytest.approx(0.72)
    assert results[0].reasons == ["source: repo://billing-svc/README.md@c4a1"]

    fetched = mg.get("11111111-1111-4111-8111-111111111111")
    assert fetched is not None
    assert fetched.content == "billing-svc uses PostgreSQL 15"

    mg.close()


@pytest.mark.skipif(not HAS_HTTPX, reason="httpx not installed")
def test_remote_get_404_returns_none() -> None:
    import httpx

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"error": "not_found"})

    client = httpx.Client(
        base_url="https://api.example.test", transport=httpx.MockTransport(handler)
    )
    mg = MemoryGuard.remote("https://api.example.test", client=client)
    assert mg.get("does-not-exist") is None
    mg.close()
