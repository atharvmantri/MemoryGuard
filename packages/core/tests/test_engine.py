# SPDX-License-Identifier: Apache-2.0
"""Unit tests for the :class:`MemoryGuardEngine` facade + OSS composition root.

Exercises the facade end to end against a fully wired, local-first engine built
by :func:`build_local_engine(":memory:")` — ``SqliteStore`` + ``LocalEmbedder``
+ ``HeuristicReranker`` + ``TrustEngine`` (deterministic scorer + rule
contradiction model) + the composite ingestion inspector, with the OSS default
``AllowAllPolicy`` and ``NullAuditSink``. No external API and no files written.

Covers:
* ``create_memory`` round-trips via ``get`` and is scored (``trust_score > 0``).
* Two contradictory ``create_memory`` calls produce mutual ``contradicts`` and a
  ``disputed`` record.
* ``ingest_path`` over a temp folder creates + evaluates records.
* ``query`` returns trusted results carrying reasons.
* ``correct_memory`` sets the prior record to ``CORRECTED`` and persists a new
  record with lineage metadata.
* ``explain`` returns provenance + a trust signal breakdown.

**Validates: Requirements 2.1, 2.6, 2.8, 6.4, 16.4**
"""

from __future__ import annotations

from pathlib import Path

import pytest

from memoryguard_core import MemoryGuardEngine, build_local_engine
from memoryguard_core.models import (
    MemoryStatus,
    Scope,
    Sensitivity,
    SourceType,
)
from memoryguard_core.retrieval.service import QuerySpec


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_engine() -> MemoryGuardEngine:
    """Build a fully wired in-memory OSS engine."""
    return build_local_engine(":memory:")


# ---------------------------------------------------------------------------
# Composition root
# ---------------------------------------------------------------------------


def test_build_local_engine_wires_oss_defaults() -> None:
    """The composition root returns a fully wired engine on OSS defaults."""
    engine = _make_engine()
    assert isinstance(engine, MemoryGuardEngine)
    # Commercial flags default off => OSS local-first selection.
    assert engine.flags.local_store is True
    assert engine.flags.cloud_store is False
    # OSS default injection interfaces are present.
    assert engine.policy is not None
    assert engine.inspector is not None
    assert engine.audit is not None


# ---------------------------------------------------------------------------
# create_memory + get (Requirements 2.1, 2.6)
# ---------------------------------------------------------------------------


def test_create_memory_round_trips_and_is_scored() -> None:
    """create_memory persists a retrievable, scored record (trust_score > 0)."""
    engine = _make_engine()
    record = engine.create_memory(
        content="The project uses PostgreSQL for persistence.",
        source_type=SourceType.USER,
        source_ref="user://alice",
        scope=Scope.PROJECT,
        scope_ref="proj-1",
    )

    fetched = engine.get(record.memory_id)
    assert fetched is not None
    assert fetched.memory_id == record.memory_id
    assert fetched.content == "The project uses PostgreSQL for persistence."
    assert fetched.source_ref == "user://alice"
    assert fetched.scope == Scope.PROJECT
    assert fetched.scope_ref == "proj-1"
    # The trust engine scored the record on creation.
    assert fetched.trust_score > 0.0
    assert fetched.embedding is not None and len(fetched.embedding) == 384


def test_create_memory_requires_provenance() -> None:
    """A blank source_ref is rejected (provenance required, Requirement 2.6)."""
    engine = _make_engine()
    with pytest.raises(Exception):
        engine.create_memory(
            content="missing provenance",
            source_type=SourceType.USER,
            source_ref="   ",
            scope=Scope.GLOBAL,
        )


# ---------------------------------------------------------------------------
# Contradiction wiring through the engine (Requirement 16.4 integration)
# ---------------------------------------------------------------------------


def test_contradictory_creates_produce_mutual_links_and_disputed() -> None:
    """Two conflicting memories get mutual contradicts + a disputed record."""
    engine = _make_engine()

    first = engine.create_memory(
        content="The default database for the billing service is PostgreSQL.",
        source_type=SourceType.COMMIT,
        source_ref="repo://billing@abc#chunk=0",
        scope=Scope.PROJECT,
        scope_ref="billing",
    )
    second = engine.create_memory(
        content="The default database for the billing service is MySQL.",
        source_type=SourceType.USER,
        source_ref="user://bob",
        scope=Scope.PROJECT,
        scope_ref="billing",
    )

    a = engine.get(first.memory_id)
    b = engine.get(second.memory_id)
    assert a is not None and b is not None

    # Mutual contradiction pointers.
    assert b.memory_id in a.contradicts
    assert a.memory_id in b.contradicts

    # Exactly one of the pair (the lower-trust record) is disputed.
    statuses = {a.status, b.status}
    assert MemoryStatus.DISPUTED in statuses


# ---------------------------------------------------------------------------
# ingest_path (Requirement 2.1 / 16.4)
# ---------------------------------------------------------------------------


def test_ingest_path_folder_creates_and_evaluates(tmp_path: Path) -> None:
    """ingest_path over a folder creates + evaluates one record per chunk."""
    engine = _make_engine()

    (tmp_path / "a.md").write_text(
        "Alpha service owns the ingestion pipeline.", encoding="utf-8"
    )
    (tmp_path / "b.txt").write_text(
        "Beta service owns the retrieval pipeline.", encoding="utf-8"
    )
    # A binary-ish/unsupported extension should be ignored.
    (tmp_path / "ignore.bin").write_bytes(b"\x00\x01\x02")

    records = engine.ingest_path(str(tmp_path), scope=Scope.REPO, scope_ref="demo-repo")

    assert len(records) >= 2
    for rec in records:
        assert rec.scope == Scope.REPO
        assert rec.scope_ref == "demo-repo"
        assert rec.trust_score > 0.0
        # Each is retrievable (was persisted + evaluated).
        assert engine.get(rec.memory_id) is not None


def test_ingest_path_single_file(tmp_path: Path) -> None:
    """ingest_path over a single file ingests + evaluates its chunk(s)."""
    engine = _make_engine()
    target = tmp_path / "notes.md"
    target.write_text("A single note about the vault mesh design.", encoding="utf-8")

    records = engine.ingest_path(str(target), scope=Scope.PROJECT, scope_ref="p1")
    assert len(records) >= 1
    assert all(r.scope == Scope.PROJECT and r.scope_ref == "p1" for r in records)


def test_ingest_path_missing_raises() -> None:
    """A non-existent path raises FileNotFoundError."""
    engine = _make_engine()
    with pytest.raises(FileNotFoundError):
        engine.ingest_path("/no/such/path/here", scope=Scope.GLOBAL)


# ---------------------------------------------------------------------------
# query (Requirements 5.x / 6.1 via the service)
# ---------------------------------------------------------------------------


def test_query_returns_trusted_results_with_reasons() -> None:
    """query returns trust-aware results, each carrying >= 1 reason."""
    engine = _make_engine()
    engine.create_memory(
        content="The retrieval service blends semantic and keyword matching.",
        source_type=SourceType.FILE,
        source_ref="file://docs/retrieval.md#chunk=0",
        scope=Scope.PROJECT,
        scope_ref="p1",
        sensitivity=Sensitivity.PUBLIC,
    )

    results = engine.query(
        QuerySpec(
            text="retrieval semantic keyword matching",
            scope=Scope.PROJECT,
            scope_ref="p1",
            min_trust=0.0,
            max_sensitivity=Sensitivity.PII,
            limit=10,
        )
    )

    assert len(results) >= 1
    top = results[0]
    assert top.record.scope == Scope.PROJECT
    assert len(top.reasons) >= 1
    assert all(isinstance(r, str) and r.strip() for r in top.reasons)
    # Ordered by final_rank descending.
    ranks = [rm.final_rank for rm in results]
    assert ranks == sorted(ranks, reverse=True)


def test_ingest_path_then_query_happy_path(tmp_path: Path) -> None:
    """End-to-end happy path: ingest a folder, then query the ingested content.

    Exercises the ``ingest_path -> query`` flow through the engine facade: files
    are ingested + evaluated, then a trust-aware query surfaces the matching
    ingested memory with provenance (``source_ref``) and at least one reason
    (Requirements 2.1, 6.1 via the retrieval service).
    """
    engine = _make_engine()

    (tmp_path / "auth.md").write_text(
        "The authentication service issues short-lived JWT access tokens.",
        encoding="utf-8",
    )
    (tmp_path / "cache.md").write_text(
        "The caching layer uses Redis with a sixty second expiry.",
        encoding="utf-8",
    )

    ingested = engine.ingest_path(
        str(tmp_path), scope=Scope.PROJECT, scope_ref="svc-1"
    )
    assert len(ingested) >= 2
    ingested_ids = {rec.memory_id for rec in ingested}

    results = engine.query(
        QuerySpec(
            text="authentication JWT access tokens",
            scope=Scope.PROJECT,
            scope_ref="svc-1",
            min_trust=0.0,
            max_sensitivity=Sensitivity.PII,
            limit=10,
        )
    )

    assert len(results) >= 1
    # Every surfaced record came from the ingest_path call and is in scope.
    for rm in results:
        assert rm.record.memory_id in ingested_ids
        assert rm.record.scope == Scope.PROJECT
        assert rm.record.scope_ref == "svc-1"
        assert rm.record.source_ref  # provenance carried through (Requirement 6.1)
        assert len(rm.reasons) >= 1
        assert all(isinstance(r, str) and r.strip() for r in rm.reasons)

    # The auth memory is the most relevant result for an auth query.
    top = results[0]
    assert "authentication" in top.record.content.lower()
    # Results are ordered by final_rank descending.
    ranks = [rm.final_rank for rm in results]
    assert ranks == sorted(ranks, reverse=True)


# ---------------------------------------------------------------------------
# correct_memory (Requirement 2.8)
# ---------------------------------------------------------------------------


def test_correct_memory_sets_lineage_and_corrected_status() -> None:
    """correct_memory marks the old record CORRECTED and links a new record."""
    engine = _make_engine()
    original = engine.create_memory(
        content="alpha beta gamma deployment guide",
        source_type=SourceType.USER,
        source_ref="user://carol",
        scope=Scope.PROJECT,
        scope_ref="p1",
        tags=["docs"],
    )

    # New content is a superset (no conflict signal => no self-contradiction).
    corrected = engine.correct_memory(
        original.memory_id,
        "alpha beta gamma deployment guide with rollback steps appended",
    )

    # New record is distinct, persisted, and carries lineage to the old id.
    assert corrected.memory_id != original.memory_id
    assert corrected.metadata.get("supersedes") == original.memory_id
    assert engine.get(corrected.memory_id) is not None
    # Provenance carried over from the prior record.
    assert corrected.source_ref == original.source_ref
    assert corrected.scope == original.scope and corrected.scope_ref == original.scope_ref
    assert corrected.tags == ["docs"]

    # Prior record transitioned to CORRECTED with a back-pointer.
    old = engine.get(original.memory_id)
    assert old is not None
    assert old.status == MemoryStatus.CORRECTED
    assert old.metadata.get("superseded_by") == corrected.memory_id


def test_correct_memory_persists_links_and_updates_timestamps() -> None:
    """Correction persists supersedes/superseded_by links + bumps updated_at.

    Verifies the full corrected lineage contract of Requirement 2.8:

    * the NEW record is persisted (retrievable) and carries a ``supersedes``
      back-link to the prior record, with ``updated_at >= created_at``;
    * the PRIOR record is ``CORRECTED``, carries a ``superseded_by`` forward-link
      to the new record, and has its ``updated_at`` advanced to ``>=`` its
      ``created_at`` (and not earlier than its original ``updated_at``).
    """
    engine = _make_engine()
    original = engine.create_memory(
        content="alpha beta gamma deployment guide",
        source_type=SourceType.USER,
        source_ref="user://dave",
        scope=Scope.PROJECT,
        scope_ref="p1",
    )
    original_updated_at = engine.get(original.memory_id).updated_at

    corrected = engine.correct_memory(
        original.memory_id,
        "alpha beta gamma deployment guide with rollback steps appended",
    )

    # NEW record: persisted with a supersedes back-link + valid timestamps.
    new_fetched = engine.get(corrected.memory_id)
    assert new_fetched is not None
    assert new_fetched.metadata.get("supersedes") == original.memory_id
    assert new_fetched.updated_at >= new_fetched.created_at

    # PRIOR record: CORRECTED, forward-linked, with an advanced updated_at.
    old = engine.get(original.memory_id)
    assert old is not None
    assert old.status == MemoryStatus.CORRECTED
    assert old.metadata.get("superseded_by") == corrected.memory_id
    assert old.updated_at >= old.created_at
    assert old.updated_at >= original_updated_at


def test_correct_memory_unknown_id_raises() -> None:
    """Correcting an unknown id raises KeyError."""
    engine = _make_engine()
    with pytest.raises(KeyError):
        engine.correct_memory("not-a-real-id", "new content")


# ---------------------------------------------------------------------------
# explain (Requirement 6.4)
# ---------------------------------------------------------------------------


def test_explain_returns_provenance_and_signal_breakdown() -> None:
    """explain returns provenance + a full trust signal breakdown."""
    engine = _make_engine()
    record = engine.create_memory(
        content="The audit log records every retrieval decision.",
        source_type=SourceType.FILE,
        source_ref="file://docs/audit.md#chunk=0",
        scope=Scope.PROJECT,
        scope_ref="p1",
    )

    explanation = engine.explain(record.memory_id)

    assert explanation["memory_id"] == record.memory_id
    assert "trust_score" in explanation

    provenance = explanation["provenance"]
    assert provenance["source_type"] == SourceType.FILE.value
    assert provenance["source_ref"] == "file://docs/audit.md#chunk=0"
    assert provenance["scope"] == Scope.PROJECT.value
    assert provenance["scope_ref"] == "p1"
    assert provenance["created_at"] is not None

    signals = explanation["signals"]
    for key in (
        "source_authority",
        "freshness",
        "confirmation_score",
        "contradiction_penalty",
        "sensitivity_penalty",
        "correction_signal",
    ):
        assert key in signals
        assert 0.0 <= signals[key] <= 1.0

    assert "weights" in explanation
    assert "contradictions" in explanation and isinstance(
        explanation["contradictions"], list
    )


def test_explain_unknown_id_raises() -> None:
    """Explaining an unknown id raises KeyError."""
    engine = _make_engine()
    with pytest.raises(KeyError):
        engine.explain("not-a-real-id")
