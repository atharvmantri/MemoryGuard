# SPDX-License-Identifier: Apache-2.0
"""The :class:`TrustEngine` — trust scoring + contradiction wiring (OSS core).

``TrustEngine`` is the integration point the design's *Component: TrustEngine*
describes. It ties together two model-layer components behind their stable
interfaces:

* a :class:`~memoryguard_models.base.TrustModel` / ``TrustScorer`` (OSS default
  :class:`~memoryguard_core.trust.scoring.DeterministicTrustModel`) that turns
  normalized signals into a bounded ``trust_score``; and
* a :class:`~memoryguard_models.base.ContradictionModel` / ``ContradictionClassifier``
  (OSS default :class:`~memoryguard_core.trust.contradiction.RuleContradictionModel`)
  that decides whether two memories conflict and scans the store for conflicts.

The engine exposes three operations:

* :meth:`compute_signals` — build the normalized :class:`TrustSignals` for a
  record at a point in time (delegates to ``scoring.compute_signals``).
* :meth:`score` — return the bounded, deterministic ``trust_score`` for a record
  (delegates to the active ``TrustModel``).
* :meth:`evaluate` — the full lifecycle step run after a record is persisted:
  score the record, scan the store for contradictions, wire up **mutual**
  ``contradicts`` pointers, transition the **lower-trust** record of each
  conflicting pair to ``DISPUTED``, persist everything through the store, and
  return the updated record. Contradictions are treated as **first-class state**
  (recorded on the records and in ``memory_contradictions``), never as errors.

Requirements: 7.1, 7.3, 26.1, 26.2, 26.4 (trust scoring + correction signal) and
8.3, 8.5, 23.1 (mutual linkage, disputed transition, contradictions never raise
trust).

This module is part of the Apache-2.0 OSS core and MUST NOT import from any
commercial package. It runs entirely on-device with no external LLM API.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from memoryguard_core.decisions import extract_decision, has_supersession_cue
from memoryguard_core.models import MemoryRecord, MemoryStatus
from memoryguard_core.store.base import MemoryStore
from memoryguard_core.trust.contradiction import RuleContradictionModel
from memoryguard_core.trust.scoring import (
    DeterministicTrustModel,
    compute_signals as _compute_signals,
)
from memoryguard_models.base import ContradictionModel, TrustModel, TrustSignals

__all__ = ["TrustEngine"]


def _utcnow() -> datetime:
    """Return the current UTC time as a timezone-aware ``datetime``.

    Matches the timezone-aware timestamps produced by
    ``memoryguard_core.models`` so age/freshness arithmetic stays consistent.
    """

    return datetime.now(timezone.utc)


class TrustEngine:
    """Compute trust scores and detect/record contradictions for memories.

    Args:
        trust_model: the active :class:`TrustModel` (``TrustScorer``). Defaults
            to the OSS :class:`DeterministicTrustModel`. A learned commercial
            model can be injected behind the same interface at the composition
            root without changing this engine.
        contradiction_model: the active :class:`ContradictionModel`
            (``ContradictionClassifier``). Defaults to the OSS
            :class:`RuleContradictionModel`.
    """

    def __init__(
        self,
        trust_model: Optional[TrustModel] = None,
        contradiction_model: Optional[ContradictionModel] = None,
    ) -> None:
        self._trust_model: TrustModel = (
            trust_model if trust_model is not None else DeterministicTrustModel()
        )
        self._contradiction_model: ContradictionModel = (
            contradiction_model
            if contradiction_model is not None
            else RuleContradictionModel()
        )

    # -- accessors ---------------------------------------------------------

    @property
    def trust_model(self) -> TrustModel:
        """The active trust model (OSS deterministic default unless injected)."""

        return self._trust_model

    @property
    def contradiction_model(self) -> ContradictionModel:
        """The active contradiction model (OSS rule-based default unless injected)."""

        return self._contradiction_model

    # -- scoring -----------------------------------------------------------

    def compute_signals(
        self, record: MemoryRecord, now: Optional[datetime] = None
    ) -> TrustSignals:
        """Return the normalized :class:`TrustSignals` for ``record`` at ``now``.

        Delegates to ``memoryguard_core.trust.scoring.compute_signals``. When
        ``now`` is omitted the current UTC time is used.
        """

        if now is None:
            now = _utcnow()
        return _compute_signals(record, now)

    def score(self, record: MemoryRecord, now: Optional[datetime] = None) -> float:
        """Return the bounded ``trust_score`` in ``[0, 1]`` for ``record``.

        Builds the record's signals (incorporating the user-correction signal,
        Requirement 26.1) and delegates to the active ``TrustModel``. Output is
        deterministic and bounded; the model clamps defensively (Requirement
        7.4). When ``now`` is omitted the current UTC time is used.
        """

        if now is None:
            now = _utcnow()
        signals = _compute_signals(record, now)
        return self._trust_model.score(record, signals, now)

    # -- full lifecycle evaluation ----------------------------------------

    def evaluate(self, record: MemoryRecord, store: MemoryStore) -> MemoryRecord:
        """Score ``record``, detect contradictions, persist, and return it.

        Steps (design *Component: TrustEngine* + Requirements 8.3/8.5/23.1):

        1. Scan ``store`` for memories that contradict ``record`` via the active
           contradiction model.
        2. Add the conflicting ids to ``record.contradicts`` (de-duplicated, no
           self-reference) so the contradiction penalty enters the score.
        3. Compute and set ``record.trust_score`` (``now = utcnow``). Because the
           penalty enters the weighted formula as ``1 - penalty``, an unresolved
           contradiction can only **lower** the score, never raise it.
        4. For each conflicting memory: set the **mutual** ``contradicts``
           pointer back to ``record``, re-score it, transition the **lower-trust**
           record of the pair to ``DISPUTED``, and persist it via
           :meth:`MemoryStore.update` (which writes ``memory_contradictions``).
        5. Persist ``record`` via :meth:`MemoryStore.update` and return it.

        The record is expected to already exist in ``store`` (the ingestion flow
        persists before evaluating); links and status are persisted through
        ``store.update``. Contradictions are recorded as first-class state, not
        raised as errors.
        """

        now = _utcnow()

        # 1. Scan the store for conflicts with the candidate.
        conflicts = self._contradiction_model.scan(record, store)

        # 2. Record mutual pointers on the candidate (dedup, never self).
        for other_id, _result in conflicts:
            if other_id != record.memory_id and other_id not in record.contradicts:
                record.contradicts.append(other_id)

        # 3. Score the candidate now that its contradicts set is final. The
        #    contradiction penalty guarantees this never exceeds the
        #    no-contradiction score (Requirement 8.3 / 23.1).
        record.trust_score = self.score(record, now)
        if record.updated_at < now:
            record.updated_at = now

        # 4. Wire each conflicting memory: mutual link + re-score + disputed
        #    transition on the lower-trust record of the pair.
        for other_id, _result in conflicts:
            other = store.get(other_id)
            if other is None:
                # Neighbor vanished between scan and update; skip safely.
                continue

            if record.memory_id not in other.contradicts:
                other.contradicts.append(record.memory_id)

            # Re-score the conflicting memory with its new mutual link so its
            # own contradiction penalty applies (never raises its trust).
            other.trust_score = self.score(other, now)
            if other.updated_at < now:
                other.updated_at = now

            if self._candidate_supersedes(record, other):
                other.status = MemoryStatus.SUPERSEDED
                other.metadata["superseded_by"] = record.memory_id
                supersedes = record.metadata.get("supersedes", [])
                if not isinstance(supersedes, list):
                    supersedes = [supersedes]
                if other.memory_id not in supersedes:
                    supersedes.append(other.memory_id)
                record.metadata["supersedes"] = supersedes
            elif self._candidate_supersedes(other, record):
                record.status = MemoryStatus.SUPERSEDED
                record.metadata["superseded_by"] = other.memory_id
                supersedes = other.metadata.get("supersedes", [])
                if not isinstance(supersedes, list):
                    supersedes = [supersedes]
                if record.memory_id not in supersedes:
                    supersedes.append(record.memory_id)
                other.metadata["supersedes"] = supersedes
            # Otherwise transition the lower-trust record to DISPUTED. On a tie
            # the candidate is treated as lower. Only ACTIVE records are moved.
            elif record.trust_score <= other.trust_score:
                if record.status == MemoryStatus.ACTIVE:
                    record.status = MemoryStatus.DISPUTED
            else:
                if other.status == MemoryStatus.ACTIVE:
                    other.status = MemoryStatus.DISPUTED

            store.update(other)

        # 5. Persist the candidate (trust_score, contradicts, status).
        store.update(record)
        return record

    @staticmethod
    def _candidate_supersedes(newer: MemoryRecord, older: MemoryRecord) -> bool:
        """Whether ``newer`` should supersede ``older`` for a conflicting fact."""

        if newer.created_at < older.created_at:
            return False
        if newer.scope != older.scope or newer.scope_ref != older.scope_ref:
            return False
        newer_decision = extract_decision(newer.content)
        older_decision = extract_decision(older.content)
        if (
            newer_decision is None
            or older_decision is None
            or newer_decision.value.lower() == older_decision.value.lower()
        ):
            return False
        same_decision = newer_decision.key == older_decision.key
        database_split = (
            older_decision.key == "database"
            and newer_decision.key in {"database_local", "database_cloud"}
        )
        if not same_decision and not database_split:
            return False
        explicit_update = has_supersession_cue(newer.content)
        high_trust_user = newer.source_type.value == "user" and newer.trust_score >= 0.45
        return database_split or explicit_update or high_trust_user
