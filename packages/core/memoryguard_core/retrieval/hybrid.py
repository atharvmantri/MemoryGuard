# SPDX-License-Identifier: Apache-2.0
"""Stage-1 hybrid candidate gathering for the Retrieval & Policy Layer.

This module implements the **first stage** of the design's *two-stage pipeline*
(see design's *Component: Retrieval & Policy Layer*). Stage 1 produces a unified
candidate set from three complementary relevance signals:

* **Semantic similarity** — cosine similarity between the query embedding and each
  stored memory's embedding (via the injected :class:`Embedder` and
  ``store.vector_search``).
* **Keyword match** — full-text relevance from ``store.keyword_search`` (FTS5/bm25).
* **Recency** — newer ``created_at`` is preferred, seeded from ``store.list``.

The three per-signal scores are each normalized to ``[0.0, 1.0]`` and blended into
a single ``first_stage_score`` using fixed, documented weights. Candidates are
de-duplicated by ``memory_id`` so a memory found via multiple signals appears once,
carrying every component score.

Stage 1 intentionally does **not** apply trust floors, scope containment, expiry,
status, sensitivity ceilings, or policy — those belong to Stage 2 / the policy
filter (task 10.3). The optional ``scope`` / ``scope_ref`` arguments here are a
lightweight candidate-pool narrowing only.

Dimension-mismatch handling (Requirement 4.3)
---------------------------------------------
The query is embedded with the same embedder used for stored memories
(Requirement 4.2), so embeddings compared for semantic similarity must share a
dimension. ``store.vector_search`` silently skips stored vectors whose dimension
differs from the query's. To avoid *silently* returning incorrect results, this
module surfaces an explicit :class:`DimensionMismatchError` whenever it can
*detect* that a candidate memory's stored embedding dimension differs from the
query embedding dimension (Requirement 4.3 — "signal a dimension-mismatch conflict
rather than returning incorrect results").

This module is part of the Apache-2.0 OSS core. It depends only on the Python
standard library plus the injected :class:`Embedder` and :class:`MemoryStore`
interfaces. It MUST NOT import from any commercial package.

Requirements: 4.1 (semantic + keyword + recency), 4.2 (same embedder, equal-dim
comparison), 4.3 (dimension-mismatch conflict), 22.1 (Stage-1 hybrid candidate
gathering feeding the reranker).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import timezone
from typing import Optional, Protocol, runtime_checkable

from memoryguard_core.models import MemoryRecord, Scope
from memoryguard_core.store import MemoryStore

__all__ = [
    "DimensionMismatchError",
    "Candidate",
    "gather_candidates",
    "W_VECTOR",
    "W_KEYWORD",
    "W_RECENCY",
]


# ---------------------------------------------------------------------------
# Blend weights (documented)
# ---------------------------------------------------------------------------

#: Weight of the (normalized) semantic-similarity signal in the first-stage score.
#: Semantic relevance is the primary signal: it captures meaning regardless of
#: phrasing, which is the core promise of hybrid retrieval.
W_VECTOR = 0.6

#: Weight of the (normalized) keyword-match signal. Keyword/full-text matching
#: complements embeddings for exact terms, identifiers, and rare tokens.
W_KEYWORD = 0.3

#: Weight of the (normalized) recency signal. Recency is a light tiebreaker that
#: gently prefers fresher memories without overpowering relevance.
W_RECENCY = 0.1

# The weights sum to 1.0, so a blend of three values each in [0, 1] also lands in
# [0, 1]. Keep this invariant if the weights are ever retuned.
assert abs((W_VECTOR + W_KEYWORD + W_RECENCY) - 1.0) < 1e-9


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class DimensionMismatchError(ValueError):
    """Raised when a stored embedding's dimension differs from the query's.

    Surfaced by Stage-1 gathering when it can *detect* that the embedder's output
    dimension is inconsistent with a stored embedding's dimension, so the caller
    receives an explicit conflict instead of silently incorrect results
    (Requirement 4.3). Maps to a ``409``-style conflict at the API boundary.
    """

    def __init__(
        self,
        query_dim: int,
        stored_dim: int,
        memory_id: Optional[str] = None,
    ) -> None:
        self.query_dim = query_dim
        self.stored_dim = stored_dim
        self.memory_id = memory_id
        where = f" (memory_id={memory_id!r})" if memory_id else ""
        super().__init__(
            "embedding dimension mismatch: query embedding has "
            f"{query_dim} dimensions but a stored embedding has {stored_dim}"
            f"{where}. The query must be embedded with the same model_version "
            "as the stored memories."
        )


# ---------------------------------------------------------------------------
# Embedder protocol (structural — avoids importing the models package directly)
# ---------------------------------------------------------------------------


@runtime_checkable
class _EmbedderLike(Protocol):
    """Structural type for the injected embedder.

    Mirrors the ``Embedder`` interface in ``memoryguard_models.base`` without a
    hard import, keeping the OSS core decoupled from a concrete model package at
    type-check time. Any object exposing ``embed(text) -> list[float]`` works.
    """

    def embed(self, text: str) -> list[float]: ...


# ---------------------------------------------------------------------------
# Candidate
# ---------------------------------------------------------------------------


@dataclass
class Candidate:
    """A single Stage-1 candidate with its component scores.

    Each component score is normalized to ``[0.0, 1.0]`` (see module docstring),
    and ``first_stage_score`` is their weighted blend. The reranker (Stage 2)
    consumes these candidates; it may use the components and/or the blended score.

    Attributes:
        record: the candidate :class:`MemoryRecord`.
        vector_sim: normalized semantic similarity in ``[0, 1]`` (cosine remapped
            from ``[-1, 1]``); ``0.0`` when the record has no comparable embedding.
        keyword_score: normalized keyword relevance in ``[0, 1]``; ``0.0`` when the
            record was not returned by keyword search.
        recency: normalized recency in ``[0, 1]`` (newest among candidates -> ``1.0``).
        first_stage_score: ``W_VECTOR*vector_sim + W_KEYWORD*keyword_score +
            W_RECENCY*recency``, in ``[0, 1]``.
    """

    record: MemoryRecord
    vector_sim: float = 0.0
    keyword_score: float = 0.0
    recency: float = 0.0
    first_stage_score: float = 0.0

    @property
    def memory_id(self) -> str:
        """Convenience accessor for the underlying record id."""
        return self.record.memory_id


# ---------------------------------------------------------------------------
# Internal scoring accumulator
# ---------------------------------------------------------------------------


@dataclass
class _Accum:
    """Per-memory accumulator of raw signal values during merge."""

    record: MemoryRecord
    raw_vector: Optional[float] = None  # cosine similarity in [-1, 1]
    raw_keyword: Optional[float] = None  # backend keyword relevance (unbounded+)
    found_by: set[str] = field(default_factory=set)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _norm(vec: list[float]) -> float:
    """Euclidean norm of ``vec``."""
    return math.sqrt(sum(v * v for v in vec))


def _cosine(a: list[float], b: list[float]) -> float:
    """Cosine similarity of two equal-length, non-empty vectors.

    Returns ``0.0`` when either vector has zero magnitude (undefined direction).
    Callers guarantee ``len(a) == len(b)`` before calling.
    """
    na = _norm(a)
    nb = _norm(b)
    if na == 0.0 or nb == 0.0:
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    return dot / (na * nb)


def _created_at_epoch(record: MemoryRecord) -> float:
    """Return ``created_at`` as a UTC POSIX timestamp (tz-naive treated as UTC)."""
    created = record.created_at
    if created.tzinfo is None:
        created = created.replace(tzinfo=timezone.utc)
    return created.timestamp()


def _min_max_normalize(value: float, lo: float, hi: float) -> float:
    """Min-max normalize ``value`` into ``[0, 1]`` given observed ``lo``/``hi``.

    When ``hi == lo`` (all candidates equal on this signal) every candidate is
    treated as maximally scoring (``1.0``) so a degenerate spread never zeroes an
    otherwise-present signal.
    """
    if hi <= lo:
        return 1.0
    return (value - lo) / (hi - lo)


# ---------------------------------------------------------------------------
# Stage-1 gathering
# ---------------------------------------------------------------------------


def gather_candidates(
    store: MemoryStore,
    query_text: str,
    embedder: _EmbedderLike,
    limit: int,
    *,
    scope: Optional[Scope] = None,
    scope_ref: Optional[str] = None,
) -> list[Candidate]:
    """Gather and merge Stage-1 hybrid retrieval candidates.

    Pipeline:
      1. Embed ``query_text`` with ``embedder`` (the same embedder used for stored
         memories — Requirement 4.2).
      2. Pull semantic candidates via ``store.vector_search(query_embedding, limit)``.
      3. Pull keyword candidates via ``store.keyword_search(query_text, limit)``.
      4. Seed recency candidates from ``store.list(scope, scope_ref)`` (newest first).
      5. Merge all candidates into a unique set keyed by ``memory_id``, computing
         three component scores (each normalized to ``[0, 1]``) and a blended
         ``first_stage_score`` (weights: ``W_VECTOR``/``W_KEYWORD``/``W_RECENCY``).
      6. Return candidates sorted by ``first_stage_score`` descending (with a stable
         tiebreak), truncated to ``limit``.

    Args:
        store: the :class:`MemoryStore` backend (e.g. ``SqliteStore``).
        query_text: the natural-language query.
        embedder: object exposing ``embed(text) -> list[float]`` (e.g.
            ``LocalEmbedder``).
        limit: maximum number of candidates to return (also the per-signal pool
            size). ``limit <= 0`` yields an empty list.
        scope: optional scope to narrow the candidate pool (Stage-1 convenience;
            full scope containment is enforced later in the policy filter).
        scope_ref: optional scope reference, applied with ``scope``.

    Returns:
        A de-duplicated list of :class:`Candidate`, ordered by ``first_stage_score``
        descending, length ``<= limit``.

    Raises:
        DimensionMismatchError: if a candidate's stored embedding dimension differs
            from the query embedding dimension (Requirement 4.3).
        TypeError: if ``query_text`` is not a ``str``.
    """
    if not isinstance(query_text, str):
        raise TypeError(f"query_text must be a str, got {type(query_text).__name__}")
    if limit <= 0:
        return []

    query_embedding = embedder.embed(query_text)
    query_dim = len(query_embedding)

    # --- gather raw candidates from each signal ---------------------------
    accums: dict[str, _Accum] = {}

    def _ensure(record: MemoryRecord) -> _Accum:
        acc = accums.get(record.memory_id)
        if acc is None:
            acc = _Accum(record=record)
            accums[record.memory_id] = acc
        return acc

    # Signal A: semantic / vector search.
    for record, _store_sim in store.vector_search(query_embedding, limit):
        _ensure(record).found_by.add("vector")

    # Signal B: keyword / full-text search.
    for record, kw_score in store.keyword_search(query_text, limit):
        acc = _ensure(record)
        acc.found_by.add("keyword")
        # Keep the strongest keyword score if a record appears more than once.
        if acc.raw_keyword is None or kw_score > acc.raw_keyword:
            acc.raw_keyword = float(kw_score)

    # Signal C: recency — seed the newest records in (optional) scope.
    recency_pool = store.list(scope=scope, scope_ref=scope_ref)
    recency_pool.sort(key=_created_at_epoch, reverse=True)
    for record in recency_pool[:limit]:
        _ensure(record).found_by.add("recency")

    if not accums:
        return []

    # --- optional Stage-1 scope narrowing ---------------------------------
    if scope is not None:
        for mem_id in list(accums.keys()):
            rec = accums[mem_id].record
            if rec.scope != scope or (scope_ref is not None and rec.scope_ref != scope_ref):
                del accums[mem_id]
        if not accums:
            return []

    # --- compute raw semantic similarity for every candidate --------------
    # Records carry their stored embedding; computing cosine here lets every
    # candidate (not only vector hits) receive a semantic score, and is where we
    # can DETECT a dimension mismatch and surface it explicitly (Req 4.3).
    for acc in accums.values():
        stored = acc.record.embedding
        if not stored:
            continue
        if len(stored) != query_dim:
            raise DimensionMismatchError(
                query_dim=query_dim,
                stored_dim=len(stored),
                memory_id=acc.record.memory_id,
            )
        acc.raw_vector = _cosine(query_embedding, stored)

    # --- normalize each signal to [0, 1] over the candidate set -----------
    keyword_values = [a.raw_keyword for a in accums.values() if a.raw_keyword is not None]
    kw_lo = min(keyword_values) if keyword_values else 0.0
    kw_hi = max(keyword_values) if keyword_values else 0.0

    created_values = [_created_at_epoch(a.record) for a in accums.values()]
    rec_lo = min(created_values)
    rec_hi = max(created_values)

    candidates: list[Candidate] = []
    for acc in accums.values():
        # Vector: remap cosine [-1, 1] -> [0, 1]; absent embedding -> 0.0.
        if acc.raw_vector is None:
            vector_norm = 0.0
        else:
            vector_norm = max(0.0, min(1.0, (acc.raw_vector + 1.0) / 2.0))

        # Keyword: min-max over observed keyword scores; no keyword hit -> 0.0.
        if acc.raw_keyword is None:
            keyword_norm = 0.0
        else:
            keyword_norm = _min_max_normalize(acc.raw_keyword, kw_lo, kw_hi)

        # Recency: min-max over candidate created_at (newest -> 1.0).
        recency_norm = _min_max_normalize(_created_at_epoch(acc.record), rec_lo, rec_hi)

        first_stage = (
            W_VECTOR * vector_norm
            + W_KEYWORD * keyword_norm
            + W_RECENCY * recency_norm
        )

        candidates.append(
            Candidate(
                record=acc.record,
                vector_sim=vector_norm,
                keyword_score=keyword_norm,
                recency=recency_norm,
                first_stage_score=first_stage,
            )
        )

    # --- order by blended score (stable, deterministic tiebreak) ----------
    candidates.sort(
        key=lambda c: (
            c.first_stage_score,
            _created_at_epoch(c.record),
            c.record.memory_id,
        ),
        reverse=True,
    )
    return candidates[:limit]
