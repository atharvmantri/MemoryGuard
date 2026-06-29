# SPDX-License-Identifier: Apache-2.0
"""Two-stage retrieval orchestration + policy filtering (OSS core).

Implements the design's *Component: Retrieval & Policy Layer* end to end, tying
together the Stage-1 hybrid candidate gathering, the injected Stage-2
``Reranker``, the policy filter, the :func:`final_rank` blend, and the audit
emission into a single :class:`RetrievalService.query` call.

Pipeline (per the design's *two-stage pipeline*)
------------------------------------------------
1. **Stage 1 — hybrid candidates**: :func:`gather_candidates` unions semantic +
   keyword + recency signals into a de-duplicated candidate set (gathering more
   than ``limit`` so later filtering still has headroom).
2. **Stage 2 — reranking**: the injected ``Reranker`` (OSS default
   ``HeuristicReranker``) scores each ``(query, candidate, ctx)`` triple,
   emitting ``relevance_score``, ``trust_score``, ``should_use_memory`` and a
   short ``reason``. ``ctx`` carries a single ``now`` instant and the
   ``embedder`` (so the reranker can compute semantic similarity).
3. **should_use_memory gate**: candidates the reranker marks unusable are
   dropped (recorded as rejected).
4. **Policy filter**: scope containment (``scope`` / ``scope_ref`` when
   specified), exclusion of ``expired`` / ``deleted`` status, the sensitivity
   ceiling (``record.sensitivity <= max_sensitivity`` under the tier ranking
   ``public < internal < secret < pii``), the ``min_trust`` floor against the
   reranker's ``trust_score``, and an optional injected ``PolicyProvider``
   (whose reasons are recorded on rejection).
5. **Final rank + order**: survivors get a ``final_rank`` via :func:`final_rank`
   (relevance/trust blend) and are ordered by it **descending**.
6. **Explainability**: every returned :class:`RetrievedMemory` carries at least
   one human-readable reason (reranker reason + any policy reasons + signal
   notes).
7. **Audit**: exactly **one** audit event per query is emitted via the injected
   ``AuditSink`` (default :class:`NullAuditSink`, so tests write no files),
   carrying a generated ``query_id``, per-result reasons, the used ids, and the
   rejected ids.

The result list is truncated to ``spec.limit``.

Boundary
--------
This module is part of the Apache-2.0 OSS core. It imports the ``Reranker``
``RerankResult`` *interfaces* from ``memoryguard_models`` (the OSS model layer)
and never imports a commercial package. Commercial policy / audit behavior is
injected through the core ``PolicyProvider`` / ``AuditSink`` interfaces.

Requirements: 4.4, 4.5, 5.1, 5.2, 5.3, 5.4, 5.5, 6.1, 6.3, 9.1, 22.1, 22.4,
22.5.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

from memoryguard_core.audit.hooks import AuditSink, NullAuditSink
from memoryguard_core.decisions import extract_decision, infer_query_decision_keys
from memoryguard_core.models import MemoryRecord, MemoryStatus, Scope, Sensitivity
from memoryguard_core.retrieval.hybrid import gather_candidates
from memoryguard_core.retrieval.policy_filter import AllowAllPolicy, PolicyProvider
from memoryguard_core.retrieval.ranker import (
    DEFAULT_RANK_WEIGHTS,
    RankWeights,
    final_rank as compute_final_rank,
)
from memoryguard_core.store import MemoryStore
from memoryguard_core.secrets import contains_secret

__all__ = [
    "QuerySpec",
    "RetrievedMemory",
    "RetrievalService",
]


# ---------------------------------------------------------------------------
# Sensitivity tier ranking (public < internal < secret < pii)
# ---------------------------------------------------------------------------

#: Ordinal rank of each sensitivity tier, lowest (most open) first. A record is
#: within the query ceiling when its tier rank is ``<=`` the ceiling's rank.
_SENSITIVITY_RANK: dict[Sensitivity, int] = {
    Sensitivity.PUBLIC: 0,
    Sensitivity.INTERNAL: 1,
    Sensitivity.SECRET: 2,
    Sensitivity.PII: 3,
}

#: Statuses that are always excluded from retrieval results.
_EXCLUDED_STATUSES = frozenset(
    {MemoryStatus.EXPIRED, MemoryStatus.DELETED, MemoryStatus.SUPERSEDED, MemoryStatus.OUTDATED}
)


def _sensitivity_rank(value: Sensitivity) -> int:
    """Return the ordinal rank of ``value`` (unknown tiers sort highest)."""
    return _SENSITIVITY_RANK.get(value, max(_SENSITIVITY_RANK.values()) + 1)


# ---------------------------------------------------------------------------
# Public dataclasses
# ---------------------------------------------------------------------------


@dataclass
class QuerySpec:
    """A trust-aware retrieval request.

    Attributes:
        text: the natural-language query.
        scope: optional scope the results must be contained within.
        scope_ref: optional scope reference the results must match.
        min_trust: trust floor; only memories whose (reranker) ``trust_score`` is
            ``>= min_trust`` are returned (Requirement 5.1).
        max_sensitivity: sensitivity ceiling; memories whose tier exceeds this are
            excluded (Requirements 5.3, 9.1). Defaults to ``INTERNAL``.
        limit: maximum number of results to return (Requirement 4.5).
        policy_ctx: opaque context passed to the injected ``PolicyProvider``
            (e.g. actor, workspace).
    """

    text: str
    scope: Optional[Scope] = None
    scope_ref: Optional[str] = None
    min_trust: float = 0.0
    max_sensitivity: Sensitivity = Sensitivity.INTERNAL
    limit: int = 10
    policy_ctx: dict = field(default_factory=dict)


@dataclass
class RetrievedMemory:
    """A single surfaced memory with its scores and explanation.

    Attributes:
        record: the surfaced :class:`MemoryRecord` (carries provenance +
            ``trust_score`` — Requirement 6.1).
        relevance: the reranker's ``relevance_score`` in ``[0, 1]``.
        final_rank: the combined relevance/trust score used for ordering.
        reasons: at least one human-readable reason the memory was surfaced
            (Requirements 5.4, 6.1).
    """

    record: MemoryRecord
    relevance: float
    final_rank: float
    reasons: list[str]


# ---------------------------------------------------------------------------
# RetrievalService
# ---------------------------------------------------------------------------


class RetrievalService:
    """Orchestrates the two-stage retrieval pipeline and policy filtering.

    Args:
        store: the :class:`MemoryStore` backend (e.g. ``SqliteStore``).
        embedder: object exposing ``embed(text) -> list[float]`` (e.g.
            ``LocalEmbedder``); shared by Stage 1 and the reranker so query and
            stored memories are embedded by the same model.
        reranker: the Stage-2 ``Reranker`` (OSS default ``HeuristicReranker``).
        policy: optional injected :class:`PolicyProvider` (default
            :class:`AllowAllPolicy`).
        audit: optional injected :class:`AuditSink` (default
            :class:`NullAuditSink`, so no files are written during tests). Pass
            the redaction-safe ``LocalJsonlAuditSink`` to persist audit lines.
        rank_weights: relevance/trust :class:`RankWeights` for the ``final_rank``
            blend (default ``0.5`` / ``0.5``).
    """

    def __init__(
        self,
        store: MemoryStore,
        embedder: object,
        reranker: object,
        *,
        policy: Optional[PolicyProvider] = None,
        audit: Optional[AuditSink] = None,
        rank_weights: RankWeights = DEFAULT_RANK_WEIGHTS,
    ) -> None:
        self.store = store
        self.embedder = embedder
        self.reranker = reranker
        self.policy: PolicyProvider = policy if policy is not None else AllowAllPolicy()
        self.audit: AuditSink = audit if audit is not None else NullAuditSink()
        self.rank_weights = rank_weights

    # -- public API --------------------------------------------------------

    def query(self, spec: QuerySpec) -> list[RetrievedMemory]:
        """Run the full pipeline for ``spec`` and return ranked results.

        Returns at most ``spec.limit`` :class:`RetrievedMemory` objects ordered
        by ``final_rank`` descending. Emits exactly one audit event per call.
        """
        query_id = str(uuid.uuid4())

        if spec.limit <= 0:
            self._emit_audit(query_id, spec, used=[], rejected_ids=[])
            return []

        now = datetime.now(timezone.utc)
        intent_keys = infer_query_decision_keys(spec.text)

        # --- Stage 1: gather more than `limit` so filtering has headroom. ---
        gather_k = max(spec.limit * 5, spec.limit + 20)
        candidates = gather_candidates(
            self.store,
            spec.text,
            self.embedder,
            gather_k,
            scope=spec.scope,
            scope_ref=spec.scope_ref,
        )
        record_by_id: dict[str, MemoryRecord] = {
            c.memory_id: c.record for c in candidates
        }

        # --- Stage 2: rerank candidate records. ----------------------------
        records = [c.record for c in candidates]
        rerank_results = self.reranker.rerank(
            spec.text,
            records,
            ctx={"now": now, "embedder": self.embedder},
        )

        used: list[RetrievedMemory] = []
        rejected_ids: list[str] = []

        for rr in rerank_results:
            record = record_by_id.get(rr.memory_id)
            if record is None:  # defensive: reranker returned an unknown id
                continue

            decision = extract_decision(record.content)
            if intent_keys and decision is not None and decision.key not in intent_keys:
                rejected_ids.append(record.memory_id)
                continue
            if intent_keys and decision is not None and decision.key in intent_keys:
                rr.relevance_score = max(rr.relevance_score, 0.98)
                rr.reason = f"structured decision match ({decision.key}); {rr.reason}"

            # Step 3: drop candidates the reranker says not to use.
            if not rr.should_use_memory:
                rejected_ids.append(record.memory_id)
                continue

            # Step 4: policy filter.
            allowed, policy_reasons = self._passes_policy(record, rr, spec)
            if not allowed:
                rejected_ids.append(record.memory_id)
                continue

            # Step 5: combine into a final rank.
            rank = compute_final_rank(
                rr.relevance_score, rr.trust_score, self.rank_weights
            )

            # Step 6: assemble human-readable reasons (>= 1 guaranteed).
            reasons = self._build_reasons(record, rr, spec, policy_reasons)

            used.append(
                RetrievedMemory(
                    record=record,
                    relevance=rr.relevance_score,
                    final_rank=rank,
                    reasons=reasons,
                )
            )

        # --- Order by final_rank descending (stable, deterministic). -------
        used.sort(key=lambda rm: rm.final_rank, reverse=True)

        # --- Truncate to the requested limit. ------------------------------
        results = used[: spec.limit]

        # --- Step 7: emit exactly one audit event for this query. ----------
        self._emit_audit(query_id, spec, used=results, rejected_ids=rejected_ids)

        return results

    # -- policy filter -----------------------------------------------------

    def _passes_policy(
        self,
        record: MemoryRecord,
        rerank_result: object,
        spec: QuerySpec,
    ) -> tuple[bool, list[str]]:
        """Apply the full policy filter; return ``(allowed, policy_reasons)``.

        Enforces, in order: scope containment, status exclusion
        (expired/deleted), the sensitivity ceiling, the ``min_trust`` floor
        (against the reranker ``trust_score``), and the optional injected
        ``PolicyProvider``. ``policy_reasons`` carries the provider's reasons
        (recorded whether it allows or rejects).
        """
        # Scope containment (Requirement 5.2): match scope/scope_ref when given.
        if spec.scope is not None and record.scope != spec.scope:
            return False, []
        if spec.scope_ref is not None and record.scope_ref != spec.scope_ref:
            return False, []

        # Status exclusion (Requirement 5.3): never surface expired/deleted.
        if record.status in _EXCLUDED_STATUSES:
            return False, []

        if contains_secret(record.content):
            return False, []

        # Sensitivity ceiling (Requirements 5.3, 9.1).
        if _sensitivity_rank(record.sensitivity) > _sensitivity_rank(
            spec.max_sensitivity
        ):
            return False, []

        # Trust floor (Requirement 5.1): reranker trust must clear min_trust.
        if rerank_result.trust_score < spec.min_trust:
            return False, []

        # Optional commercial policy provider (Requirement 5.5).
        allowed, policy_reasons = self.policy.evaluate(record, spec.policy_ctx)
        if not allowed:
            return False, list(policy_reasons)

        return True, list(policy_reasons)

    # -- reasons / audit ---------------------------------------------------

    @staticmethod
    def _build_reasons(
        record: MemoryRecord,
        rerank_result: object,
        spec: QuerySpec,
        policy_reasons: list[str],
    ) -> list[str]:
        """Compose the per-result reasons (reranker + policy + signal notes).

        Always returns at least one reason (Requirements 5.4, 6.1), even if the
        reranker's reason were empty and the policy added none.
        """
        reasons: list[str] = []

        rr_reason = getattr(rerank_result, "reason", "")
        if isinstance(rr_reason, str) and rr_reason.strip():
            reasons.append(rr_reason.strip())

        reasons.extend(r for r in policy_reasons if isinstance(r, str) and r.strip())

        # Signal notes — provenance + the gates this record cleared.
        reasons.append(f"source: {record.source_ref}")
        reasons.append(
            f"trust {rerank_result.trust_score:.2f} >= min_trust {spec.min_trust:.2f}"
        )
        reasons.append(
            f"sensitivity '{record.sensitivity.value}' within ceiling "
            f"'{spec.max_sensitivity.value}'"
        )
        if spec.scope is not None:
            scope_ref = record.scope_ref if record.scope_ref is not None else "-"
            reasons.append(f"in scope {record.scope.value}/{scope_ref}")

        # Guarantee non-emptiness defensively.
        if not reasons:
            reasons.append("surfaced by trust-aware retrieval")
        return reasons

    def _emit_audit(
        self,
        query_id: str,
        spec: QuerySpec,
        *,
        used: list[RetrievedMemory],
        rejected_ids: list[str],
    ) -> None:
        """Emit exactly one audit event for this query (Requirement 6.3).

        The event carries the ``query_id``, the per-result reasons, the used
        memory ids, and the rejected memory ids. It is passed to the injected
        ``AuditSink``; the OSS ``LocalJsonlAuditSink`` redacts secret values, so
        no secret content is ever written (Requirement 6.5).
        """
        used_ids = [rm.record.memory_id for rm in used]
        reasons = {rm.record.memory_id: rm.reasons for rm in used}

        event = {
            "event": "retrieval_query",
            "query_id": query_id,
            "query": spec.text,
            "scope": spec.scope.value if spec.scope is not None else None,
            "scope_ref": spec.scope_ref,
            "min_trust": spec.min_trust,
            "max_sensitivity": spec.max_sensitivity.value,
            "limit": spec.limit,
            "used_ids": used_ids,
            "rejected_ids": rejected_ids,
            "reasons": reasons,
        }
        self.audit.record(event)
