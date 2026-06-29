# SPDX-License-Identifier: Apache-2.0
"""Unit tests for the MemoryGuard MCP server tools and resources.

These exercise the plain handler functions directly against a local engine built
by ``build_local_engine(":memory:")`` — no running MCP transport is required
(the ``mcp`` package need not be installed to run these tests).

Validates: Requirements 13.2, 13.3, 13.4, 13.5, 13.6
"""

from __future__ import annotations

import pytest

from memoryguard_core import build_local_engine

from memoryguard_mcp.server import (
    DEFAULT_MIN_TRUST,
    resource_memory,
    resource_project_memories,
    tool_memory_add,
    tool_memory_explain,
    tool_memory_search,
)


@pytest.fixture()
def engine():
    """A fresh, fully-local in-memory engine for each test."""

    return build_local_engine(":memory:")


# ---------------------------------------------------------------------------
# memory_add + memory_search (Requirements 13.2, 13.3, 13.6)
# ---------------------------------------------------------------------------


def test_add_then_search_returns_with_provenance_and_trust(engine):
    added = tool_memory_add(
        engine,
        content="The deploy command for this service is `make deploy`.",
        source_ref="user://alice",
        scope="project",
        scope_ref="app1",
    )
    assert added["memory_id"]
    assert isinstance(added["trust_score"], float)
    assert added["source_ref"] == "user://alice"
    # source_type inferred from the user:// scheme.
    assert added["source_type"] == "user"

    # Explicit low floor guarantees the memory is retrievable so we can assert
    # the result shape carries provenance + trust (Requirement 13.2).
    result = tool_memory_search(
        engine,
        query="deploy command",
        scope="project",
        scope_ref="app1",
        min_trust=0.0,
        limit=5,
    )
    assert result["count"] >= 1
    found = [r for r in result["results"] if r["memory_id"] == added["memory_id"]]
    assert found, "added memory should be retrievable in its scope"
    hit = found[0]
    assert hit["source_ref"] == "user://alice"
    assert isinstance(hit["trust_score"], float)
    assert hit["content"]
    assert hit["reasons"], "each result must include at least one reason"


def test_search_default_min_trust_floor_is_half(engine):
    tool_memory_add(
        engine,
        content="Service A talks to Service B over gRPC.",
        source_ref="user://bob",
        scope="project",
        scope_ref="app1",
    )

    # No min_trust supplied -> default floor of 0.5 (Requirement 13.6).
    result = tool_memory_search(
        engine,
        query="service communication",
        scope="project",
        scope_ref="app1",
    )
    assert result["min_trust"] == DEFAULT_MIN_TRUST == 0.5
    # Every surfaced memory must clear the default floor.
    assert all(r["trust_score"] >= 0.5 for r in result["results"])


def test_search_returns_only_filter_passing_results_with_provenance(engine):
    """memory_search surfaces ONLY trust/scope-passing memories, each with
    provenance (source_ref) + trust_score (Requirement 13.2).

    Mixes three memories in one query's reach: a high-trust in-scope memory
    (should pass), a below-floor in-scope memory (trust filter must drop), and
    an out-of-scope memory (scope filter must drop). The trust floor is applied
    against the reranker trust, which mirrors the stored ``trust_score`` when
    set, so dropping the stored score below the default 0.5 floor deterministically
    excludes it.
    """

    passing = tool_memory_add(
        engine,
        content="The API gateway routes requests to backend microservices.",
        source_ref="commit://abc123",
        scope="project",
        scope_ref="app1",
    )
    below_floor = tool_memory_add(
        engine,
        content="The API gateway also validates auth tokens for microservices.",
        source_ref="user://gina",
        scope="project",
        scope_ref="app1",
    )
    out_of_scope = tool_memory_add(
        engine,
        content="The API gateway configuration for the app2 microservices.",
        source_ref="commit://def456",
        scope="project",
        scope_ref="app2",
    )

    # Drive the below_floor memory's stored trust to 0.4: above the reranker's
    # usability floor but below the default min_trust floor of 0.5.
    demoted = engine.get(below_floor["memory_id"])
    demoted.trust_score = 0.4
    engine.store.update(demoted)

    # Default floor (0.5) — only the high-trust, in-scope memory should survive.
    result = tool_memory_search(
        engine,
        query="API gateway microservices",
        scope="project",
        scope_ref="app1",
    )

    ids = {r["memory_id"] for r in result["results"]}
    # Trust filter: the 0.4-trust memory must NOT be returned (Requirement 13.2).
    assert below_floor["memory_id"] not in ids
    # Scope filter: the app2 memory must NOT leak into app1 results.
    assert out_of_scope["memory_id"] not in ids
    # The passing high-trust, in-scope memory IS returned.
    assert passing["memory_id"] in ids

    # Every returned result carries provenance + trust and clears the floor.
    assert result["results"], "expected at least the passing memory"
    for hit in result["results"]:
        assert hit["source_ref"], "each result must carry provenance (source_ref)"
        assert isinstance(hit["trust_score"], float)
        assert hit["trust_score"] >= result["min_trust"]
        assert hit["reasons"], "each result must include at least one reason"


def test_search_default_floor_excludes_below_half_trust(engine):
    """The default 0.5 trust floor is enforced, not just reported (Req 13.6).

    A memory whose stored trust is 0.4 is retrievable with an explicit
    ``min_trust=0.0`` but is filtered out when no ``min_trust`` is supplied
    (default floor 0.5), proving the floor is actually applied.
    """

    added = tool_memory_add(
        engine,
        content="The billing service charges customers through Stripe.",
        source_ref="user://frank",
        scope="project",
        scope_ref="app1",
    )

    # 0.4 sits above the reranker usability floor but below the default 0.5 floor.
    record = engine.get(added["memory_id"])
    record.trust_score = 0.4
    engine.store.update(record)

    # Explicit floor of 0.0 -> the 0.4-trust memory is retrievable.
    low = tool_memory_search(
        engine,
        query="billing service Stripe charges",
        scope="project",
        scope_ref="app1",
        min_trust=0.0,
        limit=5,
    )
    assert added["memory_id"] in {r["memory_id"] for r in low["results"]}, (
        "memory should be retrievable below the default floor when min_trust=0.0"
    )

    # No min_trust supplied -> default floor of 0.5 excludes the 0.4-trust memory.
    default = tool_memory_search(
        engine,
        query="billing service Stripe charges",
        scope="project",
        scope_ref="app1",
    )
    assert default["min_trust"] == DEFAULT_MIN_TRUST == 0.5
    assert added["memory_id"] not in {r["memory_id"] for r in default["results"]}, (
        "default 0.5 floor must exclude a 0.4-trust memory"
    )


def test_search_excludes_out_of_scope(engine):
    added = tool_memory_add(
        engine,
        content="Feature flags are stored in config/flags.yaml.",
        source_ref="user://carol",
        scope="project",
        scope_ref="app1",
    )

    # Query a *different* scope_ref — the app1 memory must not leak across scopes.
    other = tool_memory_search(
        engine,
        query="feature flags",
        scope="project",
        scope_ref="other-app",
        min_trust=0.0,
        limit=5,
    )
    ids = {r["memory_id"] for r in other["results"]}
    assert added["memory_id"] not in ids


# ---------------------------------------------------------------------------
# memory_explain (Requirement 13.4)
# ---------------------------------------------------------------------------


def test_explain_returns_rationale_and_provenance(engine):
    added = tool_memory_add(
        engine,
        content="Primary database is PostgreSQL 16.",
        source_ref="user://dave",
        scope="project",
        scope_ref="app1",
    )

    explanation = tool_memory_explain(engine, added["memory_id"])
    assert explanation["memory_id"] == added["memory_id"]
    assert "trust_score" in explanation
    # Provenance block carries the source_ref + scope binding.
    provenance = explanation["provenance"]
    assert provenance["source_ref"] == "user://dave"
    assert provenance["scope"] == "project"
    assert provenance["scope_ref"] == "app1"
    # Trust rationale: normalized signals + the weights used.
    assert set(explanation["signals"]) >= {
        "source_authority",
        "freshness",
        "confirmation_score",
        "contradiction_penalty",
        "sensitivity_penalty",
        "correction_signal",
    }
    assert "weights" in explanation


def test_explain_unknown_memory_raises(engine):
    with pytest.raises(KeyError):
        tool_memory_explain(engine, "00000000-0000-4000-8000-000000000000")


# ---------------------------------------------------------------------------
# Resources (Requirement 13.5)
# ---------------------------------------------------------------------------


def test_resource_project_memories_lists_scope(engine):
    a = tool_memory_add(
        engine,
        content="Memory one for app1.",
        source_ref="user://alice",
        scope="project",
        scope_ref="app1",
    )
    b = tool_memory_add(
        engine,
        content="Memory two for app1.",
        source_ref="user://bob",
        scope="project",
        scope_ref="app1",
    )
    # A memory in a different scope_ref must not appear in app1's listing.
    tool_memory_add(
        engine,
        content="Unrelated memory for app2.",
        source_ref="user://carol",
        scope="project",
        scope_ref="app2",
    )

    listing = resource_project_memories(engine, "app1")
    assert listing["scope_ref"] == "app1"
    ids = {m["memory_id"] for m in listing["memories"]}
    assert {a["memory_id"], b["memory_id"]} <= ids
    assert listing["count"] == len(listing["memories"])
    # Each summary carries provenance + trust.
    for m in listing["memories"]:
        assert m["source_ref"]
        assert "trust_score" in m


def test_resource_memory_returns_full_provenance_and_trust(engine):
    added = tool_memory_add(
        engine,
        content="The cache TTL is 300 seconds.",
        source_ref="user://erin",
        scope="project",
        scope_ref="app1",
    )

    resource = resource_memory(engine, added["memory_id"])
    assert resource["memory_id"] == added["memory_id"]
    assert resource["content"] == "The cache TTL is 300 seconds."
    assert resource["provenance"]["source_ref"] == "user://erin"
    assert "signals" in resource
    assert "weights" in resource
    assert "trust_score" in resource


def test_resource_memory_unknown_raises(engine):
    with pytest.raises(KeyError):
        resource_memory(engine, "00000000-0000-4000-8000-000000000000")
