# SPDX-License-Identifier: Apache-2.0
"""The :class:`MemoryGuardEngine` facade — the single core integration point.

``MemoryGuardEngine`` ties the four core layers together behind one small,
stable surface used by the CLI, the MCP server, the Python SDK, and the REST API
(design *Component: MemoryGuardEngine (facade)*). It wires:

* a :class:`~memoryguard_core.store.base.MemoryStore` (e.g. ``SqliteStore``),
* an ``Embedder`` (e.g. ``LocalEmbedder``),
* a :class:`~memoryguard_core.trust.engine.TrustEngine` (scoring + contradictions),
* a :class:`~memoryguard_core.retrieval.service.RetrievalService` (two-stage,
  trust-aware retrieval), and
* the three commercial-injection interfaces — :class:`AuditSink`,
  :class:`PolicyProvider`, :class:`IngestionInspector` — plus the
  :class:`~memoryguard_core.flags.FeatureFlags` snapshot.

Operations
----------
* :meth:`create_memory` — manually add a memory (embed + inspect + persist), then
  score it and detect contradictions via the trust engine (Requirements 2.1,
  2.6, 6.4).
* :meth:`ingest_path` — ingest a file, folder, or git repository (detected
  automatically), then evaluate each created record for trust + contradictions
  (Requirement 2.1, 16.4).
* :meth:`query` — delegate a trust-aware :class:`QuerySpec` to the retrieval
  service.
* :meth:`correct_memory` — record a corrected lineage: the prior record becomes
  ``CORRECTED`` (and is marked superseded so its correction signal lowers its
  trust), while a new re-embedded record carrying the new content is persisted,
  linked, and evaluated (Requirement 2.8).
* :meth:`get` — fetch a record by id (or ``None``).
* :meth:`explain` — return a memory's provenance, trust signal breakdown, and
  contradictions (Requirement 6.4).

This module is part of the Apache-2.0 OSS core. Commercial behavior enters only
through the injected ``AuditSink`` / ``PolicyProvider`` / ``IngestionInspector``
interfaces; this module imports **no** commercial package and runs entirely
on-device with no external LLM API.

Requirements: 2.1, 2.6, 2.8, 6.4, 16.4.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from memoryguard_core.audit.hooks import AuditSink, NullAuditSink
from memoryguard_core.flags import FeatureFlags
from memoryguard_core.ingestion import (
    add_memory,
    ingest_file,
    ingest_folder,
    ingest_repo,
)
from memoryguard_core.models import (
    MemoryRecord,
    MemoryStatus,
    Scope,
    Sensitivity,
    SourceType,
    new_memory_record,
)
from memoryguard_core.retrieval.policy_filter import (
    AllowAllPolicy,
    IngestionInspector,
    NoOpInspector,
    PolicyProvider,
)
from memoryguard_core.retrieval.service import (
    QuerySpec,
    RetrievalService,
    RetrievedMemory,
)
from memoryguard_core.store.base import MemoryStore
from memoryguard_core.trust.engine import TrustEngine
from memoryguard_core.trust.scoring import WEIGHTS

__all__ = ["MemoryGuardEngine"]


def _utcnow() -> datetime:
    """Return the current UTC time as a timezone-aware ``datetime``."""

    return datetime.now(timezone.utc)


class MemoryGuardEngine:
    """The core facade integrating store, embedder, trust, retrieval + injection.

    Args:
        store: the :class:`MemoryStore` backend.
        embedder: an object exposing ``embed(text) -> list[float]`` (e.g.
            ``LocalEmbedder``); shared by ingestion and retrieval.
        trust_engine: the :class:`TrustEngine` used to score records and detect
            contradictions.
        retrieval: the :class:`RetrievalService` that answers queries.
        flags: the active :class:`FeatureFlags` snapshot.
        audit: injected :class:`AuditSink` (OSS default :class:`NullAuditSink`).
        policy: injected :class:`PolicyProvider` (OSS default
            :class:`AllowAllPolicy`).
        inspector: injected :class:`IngestionInspector` run at ingestion time
            (OSS default :class:`NoOpInspector`).
    """

    def __init__(
        self,
        store: MemoryStore,
        embedder: object,
        trust_engine: TrustEngine,
        retrieval: RetrievalService,
        flags: FeatureFlags,
        *,
        audit: Optional[AuditSink] = None,
        policy: Optional[PolicyProvider] = None,
        inspector: Optional[IngestionInspector] = None,
    ) -> None:
        self.store = store
        self.embedder = embedder
        self.trust_engine = trust_engine
        self.retrieval = retrieval
        self.flags = flags
        self.audit: AuditSink = audit if audit is not None else NullAuditSink()
        self.policy: PolicyProvider = policy if policy is not None else AllowAllPolicy()
        self.inspector: IngestionInspector = (
            inspector if inspector is not None else NoOpInspector()
        )

    # -- create ------------------------------------------------------------

    def create_memory(
        self,
        content: str,
        source_type: SourceType,
        source_ref: str,
        scope: Scope,
        scope_ref: Optional[str] = None,
        sensitivity: Sensitivity = Sensitivity.INTERNAL,
        expires_at: Optional[datetime] = None,
        tags: Optional[list[str]] = None,
    ) -> MemoryRecord:
        """Create, embed, inspect, persist, and evaluate a single memory.

        Uses :func:`~memoryguard_core.ingestion.add_memory` to build a validated
        record with full provenance, compute its embedding, run the injected
        ingestion inspector, and persist it. The persisted record is then scored
        and checked for contradictions via :meth:`TrustEngine.evaluate` (which
        wires mutual ``contradicts`` pointers and disputed transitions and
        persists the result). Returns the evaluated record.
        """

        record = add_memory(
            self.store,
            self.embedder,
            content=content,
            source_type=source_type,
            source_ref=source_ref,
            scope=scope,
            scope_ref=scope_ref,
            sensitivity=sensitivity,
            expires_at=expires_at,
            tags=tags,
            inspector=self.inspector,
        )
        evaluated = self.trust_engine.evaluate(record, self.store)
        enforced, _allowed = self._enforce_ingest_policy(evaluated)
        return enforced

    # -- ingest ------------------------------------------------------------

    def ingest_path(
        self,
        path: str,
        scope: Scope,
        scope_ref: Optional[str] = None,
    ) -> list[MemoryRecord]:
        """Ingest a file, folder, or git repository at ``path`` and evaluate it.

        Detection:

        * a regular file -> :func:`ingest_file`;
        * a directory containing a ``.git`` entry (a repository) ->
          :func:`ingest_repo` (attaches repo + commit provenance);
        * any other directory -> :func:`ingest_folder`.

        The injected inspector runs on every created record during ingestion.
        Each created record is then evaluated for trust + contradictions via
        :meth:`TrustEngine.evaluate` (sequentially, so later records can be
        linked to earlier ones). Returns the list of evaluated records.

        Raises:
            FileNotFoundError: when ``path`` is neither an existing file nor an
                existing directory.
        """

        target = Path(path)

        if target.is_dir():
            if (target / ".git").exists():
                created = ingest_repo(
                    self.store,
                    self.embedder,
                    target,
                    scope=scope,
                    scope_ref=scope_ref,
                    inspector=self.inspector,
                )
            else:
                created = ingest_folder(
                    self.store,
                    self.embedder,
                    target,
                    scope=scope,
                    scope_ref=scope_ref,
                    inspector=self.inspector,
                )
        elif target.is_file():
            created = ingest_file(
                self.store,
                self.embedder,
                target,
                scope=scope,
                scope_ref=scope_ref,
                inspector=self.inspector,
            )
        else:
            raise FileNotFoundError(f"ingest_path: no such file or directory: {path!r}")

        evaluated = [self.trust_engine.evaluate(record, self.store) for record in created]
        # Apply ingest policy enforcement: rejected records are soft-deleted
        # (excluded) and their reasons recorded in audit. With the OSS default
        # AllowAllPolicy every record is allowed (no-op) and the list is unchanged.
        allowed: list[MemoryRecord] = []
        for record in evaluated:
            enforced, ok = self._enforce_ingest_policy(record)
            if ok:
                allowed.append(enforced)
        return allowed

    def _enforce_ingest_policy(
        self, record: MemoryRecord
    ) -> tuple[MemoryRecord, bool]:
        """Apply the injected :class:`PolicyProvider` to a freshly ingested record.

        Implements policy enforcement on the **ingest** path (Requirements 5.5,
        18.4, 18.5): the active org schema policies decide whether the record may
        be used. With the OSS default :class:`AllowAllPolicy` this is a no-op —
        every record is allowed and nothing is written to the audit sink, so the
        OSS local-first path is completely unaffected.

        When a commercial provider rejects the record the record is **excluded**
        by soft-deleting it (status -> ``deleted``, so it never surfaces in
        retrieval while remaining preserved for audit) and the provider's reasons
        are recorded in the audit decision via the injected :class:`AuditSink`.
        The provider's workspace is resolved from the record's
        ``metadata['workspace_id']`` stamp (written by the workspace-isolation
        layer) when present.

        Returns ``(record, allowed)`` where ``record`` is the original record
        when allowed, or the soft-deleted record when rejected.
        """

        ctx: dict = {"phase": "ingest"}
        metadata = getattr(record, "metadata", None)
        if isinstance(metadata, dict):
            workspace_id = metadata.get("workspace_id")
            if workspace_id is not None and str(workspace_id).strip():
                ctx["workspace_id"] = str(workspace_id).strip()

        allowed, reasons = self.policy.evaluate(record, ctx)
        if allowed:
            return record, True

        # Rejected: exclude the record and record the provider's reasons.
        self.store.soft_delete(record.memory_id)
        rejected = self.store.get(record.memory_id) or record
        self.audit.record(
            {
                "event": "ingest_policy_decision",
                "memory_id": record.memory_id,
                "allowed": False,
                "reasons": list(reasons),
                "source_ref": record.source_ref,
                "scope": record.scope.value,
                "scope_ref": record.scope_ref,
                "sensitivity": record.sensitivity.value,
            }
        )
        return rejected, False

    # -- query -------------------------------------------------------------

    def query(self, spec: QuerySpec) -> list[RetrievedMemory]:
        """Run a trust-aware query by delegating to the retrieval service."""

        return self.retrieval.query(spec)

    # -- correct -----------------------------------------------------------

    def correct_memory(self, memory_id: str, new_content: str) -> MemoryRecord:
        """Record a corrected lineage for ``memory_id`` with ``new_content``.

        Per Requirement 2.8 this never mutates the prior content in place.
        Instead it:

        1. Loads the prior record (raising :class:`KeyError` if absent).
        2. Builds a NEW record carrying ``new_content`` (re-embedded), the prior
           record's provenance / scope / sensitivity / tags / expiry, and a
           ``metadata['supersedes']`` pointer to the old id. The new record is
           inspected and persisted.
        3. Transitions the prior record to ``CORRECTED``, records
           ``metadata['superseded_by']`` and a ``supersede`` correction kind (so
           the trust ``correction_signal`` lowers the superseded record's
           score), re-scores it, bumps its ``updated_at``, and persists it.
        4. Evaluates the new record (trust + contradictions) and returns it.
        """

        old = self.store.get(memory_id)
        if old is None:
            raise KeyError(f"correct_memory: unknown memory_id {memory_id!r}")

        now = _utcnow()

        # 2. Build + persist the new corrected record with a lineage pointer.
        new_record = new_memory_record(
            content=new_content,
            source_type=old.source_type,
            source_ref=old.source_ref,
            scope=old.scope,
            scope_ref=old.scope_ref,
            sensitivity=old.sensitivity,
            expires_at=old.expires_at,
            tags=list(old.tags),
            metadata={"supersedes": old.memory_id},
            now=now,
        )
        new_record.embedding = self.embedder.embed(new_record.content)
        new_record = self.inspector.inspect(new_record)
        self.store.add(new_record)

        # 3. Transition the prior record into the corrected lineage.
        old.status = MemoryStatus.CORRECTED
        old.metadata["superseded_by"] = new_record.memory_id
        old.metadata["correction_kind"] = "supersede"
        if old.updated_at < now:
            old.updated_at = now
        # Re-score the superseded record so its correction signal applies.
        old.trust_score = self.trust_engine.score(old, now)
        self.store.update(old)

        # 4. Evaluate the new record (score + contradiction detection).
        return self.trust_engine.evaluate(new_record, self.store)

    def resolve_supersession(self, old_id: str, new_id: str) -> tuple[MemoryRecord, MemoryRecord]:
        """Mark ``old_id`` as superseded by ``new_id`` and preserve lineage."""

        old = self.store.get(old_id)
        new = self.store.get(new_id)
        if old is None:
            raise KeyError(f"resolve_supersession: unknown old memory_id {old_id!r}")
        if new is None:
            raise KeyError(f"resolve_supersession: unknown new memory_id {new_id!r}")
        if old.memory_id == new.memory_id:
            raise ValueError("a memory cannot supersede itself")

        now = _utcnow()
        old.status = MemoryStatus.SUPERSEDED
        old.metadata["superseded_by"] = new.memory_id
        if new.memory_id not in old.contradicts:
            old.contradicts.append(new.memory_id)
        if old.updated_at < now:
            old.updated_at = now

        supersedes = new.metadata.get("supersedes", [])
        if not isinstance(supersedes, list):
            supersedes = [supersedes]
        if old.memory_id not in supersedes:
            supersedes.append(old.memory_id)
        new.metadata["supersedes"] = supersedes
        if old.memory_id not in new.contradicts:
            new.contradicts.append(old.memory_id)
        if new.status in {MemoryStatus.DISPUTED, MemoryStatus.SUPERSEDED, MemoryStatus.OUTDATED}:
            new.status = MemoryStatus.ACTIVE
        if new.updated_at < now:
            new.updated_at = now

        old.trust_score = self.trust_engine.score(old, now)
        new.trust_score = self.trust_engine.score(new, now)
        self.store.update(old)
        self.store.update(new)
        return old, new

    # -- get ---------------------------------------------------------------

    def get(self, memory_id: str) -> Optional[MemoryRecord]:
        """Return the record for ``memory_id`` (or ``None`` if absent)."""

        return self.store.get(memory_id)

    # -- explain -----------------------------------------------------------

    def explain(self, memory_id: str) -> dict:
        """Return provenance + a trust signal breakdown + contradictions.

        Implements Requirement 6.4. The returned dict carries:

        * ``memory_id`` and the current ``trust_score`` / ``status`` /
          ``sensitivity``;
        * ``provenance`` — ``source_type``, ``source_ref``, ``scope``,
          ``scope_ref``, ``created_at``, ``updated_at``;
        * ``signals`` — the normalized trust signals from
          :meth:`TrustEngine.compute_signals`, plus the weighted ``weights`` used
          by the deterministic model;
        * ``lineage`` — any ``supersedes`` / ``superseded_by`` pointers; and
        * ``contradictions`` — per linked memory, the detector's reason +
          confidence.

        Raises:
            KeyError: when ``memory_id`` does not exist.
        """

        record = self.store.get(memory_id)
        if record is None:
            raise KeyError(f"explain: unknown memory_id {memory_id!r}")

        now = _utcnow()
        signals = self.trust_engine.compute_signals(record, now)

        contradictions: list[dict] = []
        for other_id in record.contradicts:
            other = self.store.get(other_id)
            if other is None:
                contradictions.append(
                    {"memory_id": other_id, "reason": "linked memory not found"}
                )
                continue
            result = self.trust_engine.contradiction_model.detect(record, other)
            contradictions.append(
                {
                    "memory_id": other_id,
                    "source_ref": other.source_ref,
                    "status": other.status.value,
                    "reason": result.reason,
                    "confidence": result.confidence,
                }
            )

        lineage = {
            "supersedes": record.metadata.get("supersedes"),
            "superseded_by": record.metadata.get("superseded_by"),
        }

        return {
            "memory_id": record.memory_id,
            "trust_score": record.trust_score,
            "status": record.status.value,
            "sensitivity": record.sensitivity.value,
            "provenance": {
                "source_type": record.source_type.value,
                "source_ref": record.source_ref,
                "scope": record.scope.value,
                "scope_ref": record.scope_ref,
                "created_at": record.created_at.isoformat()
                if record.created_at is not None
                else None,
                "updated_at": record.updated_at.isoformat()
                if record.updated_at is not None
                else None,
            },
            "signals": {
                "source_authority": signals.source_authority,
                "freshness": signals.freshness,
                "confirmation_score": signals.confirmation_score,
                "contradiction_penalty": signals.contradiction_penalty,
                "sensitivity_penalty": signals.sensitivity_penalty,
                "correction_signal": signals.correction_signal,
            },
            "weights": dict(WEIGHTS),
            "lineage": lineage,
            "contradictions": contradictions,
        }
