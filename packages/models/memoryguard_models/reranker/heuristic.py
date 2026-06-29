# SPDX-License-Identifier: Apache-2.0
"""Deterministic, on-device default reranker (OSS).

``HeuristicReranker`` is the OSS Stage-2 default behind the :class:`Reranker`
interface. For each ``(query, candidate, ctx)`` triple it produces a
:class:`RerankResult` carrying:

* ``relevance_score`` in ``[0, 1]`` -- a deterministic blend of lexical
  similarity (token Jaccard) and, when an embedder is supplied via ``ctx``,
  semantic cosine similarity.
* ``trust_score`` in ``[0, 1]`` -- the candidate's persisted ``trust_score``
  when present, otherwise a quick deterministic blend of the core trust signals
  (source authority + freshness + confirmations, attenuated by contradiction and
  sensitivity penalties).
* ``should_use_memory`` -- ``True`` only when relevance and trust both clear
  configurable floors and the candidate is not deleted/expired.
* ``reason`` -- a non-empty, short, human-readable explanation.

Design invariants (the "MemoryGuard Reranker" + "Retrieval & Policy Layer"
sections of the design):

* **Output bounds** (Property 23): both scores are clamped to ``[0, 1]``,
  ``should_use_memory`` is a ``bool``, and ``reason`` is always non-empty.
* **Determinism per model version** (Property 24): identical inputs under a
  fixed model version yield identical output. ``now`` is resolved once per
  ``rerank`` call (from ``ctx['now']`` when provided) so every candidate in a
  call is judged against the same instant.
* **Order stability**: results are returned in a stable, repeatable ranked
  order (by combined score descending, with ``memory_id`` as a deterministic
  tie-breaker).
* **Local-first / offline**: depends only on the standard library and
  ``packages/core`` trust signals; it makes no network or external-LLM call.

Requirements: 22.2 (bounded outputs + non-empty reason), 22.3 (determinism per
model version), 22.5 (heuristic reranker is the OSS default behind the
``Reranker`` interface).
"""

from __future__ import annotations

import math
import re
from datetime import datetime, timezone
from typing import Optional

from memoryguard_core.models import MemoryRecord, MemoryStatus
from memoryguard_core.trust.signals import (
    clamp01,
    confirmation_score,
    contradiction_penalty,
    freshness,
    sensitivity_penalty,
    source_authority,
)
from memoryguard_models.base import ModelInfo, Reranker, RerankResult

# ---------------------------------------------------------------------------
# Module-level identity + tunable constants
# ---------------------------------------------------------------------------

#: Stable model id for the OSS heuristic reranker.
DEFAULT_MODEL_ID = "reranker/heuristic"

#: Semver for the OSS heuristic reranker.
DEFAULT_VERSION = "1.0.0"

#: Default minimum relevance for ``should_use_memory`` to be ``True``.
DEFAULT_REL_MIN = 0.1

#: Default minimum trust for ``should_use_memory`` to be ``True``.
DEFAULT_TRUST_MIN = 0.3

#: Relevance blend weight on the semantic (embedder) component when an embedder
#: is available. The lexical component takes the complementary weight.
_SEMANTIC_WEIGHT = 0.6

#: Trust blend weights for the recomputed (signal-based) fallback. They sum to
#: ``1.0`` so the positive component stays in ``[0, 1]`` before penalties.
_W_AUTHORITY = 0.4
_W_FRESHNESS = 0.3
_W_CONFIRM = 0.3

#: Statuses that always force ``should_use_memory`` to ``False``.
_EXCLUDED_STATUSES = frozenset({MemoryStatus.DELETED, MemoryStatus.EXPIRED})

#: Tokenizer: lowercase alphanumeric runs. Deterministic and dependency-free.
_TOKEN_RE = re.compile(r"[a-z0-9]+")


def _tokens(text: str) -> list[str]:
    """Tokenize ``text`` into lowercase alphanumeric tokens (deterministic)."""
    if not isinstance(text, str):
        return []
    return _TOKEN_RE.findall(text.lower())


def _jaccard(query_tokens: set[str], content_tokens: set[str]) -> float:
    """Token-set Jaccard similarity in ``[0, 1]`` (0 when either side empty)."""
    if not query_tokens or not content_tokens:
        return 0.0
    intersection = len(query_tokens & content_tokens)
    if intersection == 0:
        return 0.0
    union = len(query_tokens | content_tokens)
    return intersection / union


def _cosine(a: list[float], b: list[float]) -> float:
    """Cosine similarity of two equal-length vectors, mapped to ``[0, 1]``.

    Raw cosine lives in ``[-1, 1]``; it is affinely mapped to ``[0, 1]`` via
    ``(cos + 1) / 2`` so it composes with the lexical signal. Returns ``0.0``
    for empty, length-mismatched, or zero-magnitude vectors.
    """
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = 0.0
    norm_a = 0.0
    norm_b = 0.0
    for x, y in zip(a, b):
        dot += x * y
        norm_a += x * x
        norm_b += y * y
    if norm_a <= 0.0 or norm_b <= 0.0:
        return 0.0
    cos = dot / (math.sqrt(norm_a) * math.sqrt(norm_b))
    return clamp01((cos + 1.0) / 2.0)


class HeuristicReranker(Reranker):
    """OSS default Stage-2 reranker: deterministic rule-based blend.

    Args:
        model_id: stable model identifier (default ``"reranker/heuristic"``).
        version: semver string for the model (default ``"1.0.0"``).
        rel_min: minimum ``relevance_score`` required for ``should_use_memory``
            (default ``0.1``).
        trust_min: minimum ``trust_score`` required for ``should_use_memory``
            (default ``0.3``).
        semantic_weight: blend weight on the semantic component when an embedder
            is supplied via ``ctx`` (default ``0.6``); the lexical component
            takes ``1 - semantic_weight``.

    The ``ctx`` dict passed to :meth:`rerank` may carry:

    * ``ctx['embedder']``: an object exposing ``embed(text) -> list[float]``
      (e.g. the OSS ``LocalEmbedder``). When present it enables the semantic
      similarity component; otherwise relevance falls back to lexical overlap.
    * ``ctx['now']``: a ``datetime`` used for freshness; defaults to
      ``datetime.now(timezone.utc)`` resolved once per call.
    """

    def __init__(
        self,
        *,
        model_id: str = DEFAULT_MODEL_ID,
        version: str = DEFAULT_VERSION,
        rel_min: float = DEFAULT_REL_MIN,
        trust_min: float = DEFAULT_TRUST_MIN,
        semantic_weight: float = _SEMANTIC_WEIGHT,
    ) -> None:
        if not 0.0 <= rel_min <= 1.0:
            raise ValueError("rel_min must be within [0.0, 1.0]")
        if not 0.0 <= trust_min <= 1.0:
            raise ValueError("trust_min must be within [0.0, 1.0]")
        if not 0.0 <= semantic_weight <= 1.0:
            raise ValueError("semantic_weight must be within [0.0, 1.0]")

        self._model_id = model_id
        self._version = version
        self._rel_min = float(rel_min)
        self._trust_min = float(trust_min)
        self._semantic_weight = float(semantic_weight)

    # -- Reranker interface ------------------------------------------------

    @property
    def info(self) -> ModelInfo:
        """Stable identity + version for reproducibility (``task="rerank"``)."""
        return ModelInfo(model_id=self._model_id, task="rerank", version=self._version)

    def rerank(
        self,
        query: str,
        candidates: list[MemoryRecord],
        ctx: dict,
    ) -> list[RerankResult]:
        """Score every candidate and return ranked, bounded ``RerankResult``s.

        Deterministic for fixed inputs under a fixed model version. Results are
        returned in a stable ranked order: combined score (``0.5*relevance +
        0.5*trust``) descending, then ``memory_id`` ascending as a deterministic
        tie-breaker.
        """
        ctx = ctx or {}
        now = self._resolve_now(ctx)
        embedder = ctx.get("embedder")

        # Resolve the query side once per call (determinism + efficiency).
        query_tokens = set(_tokens(query))
        query_vec: Optional[list[float]] = None
        if embedder is not None:
            query_vec = self._safe_embed(embedder, query)

        results: list[RerankResult] = []
        for candidate in candidates:
            relevance = self._relevance_score(
                query_tokens, query_vec, embedder, candidate
            )
            trust = self._trust_score(candidate, now)
            usable = self._should_use(relevance, trust, candidate)
            reason = self._build_reason(relevance, trust, candidate, usable)
            results.append(
                RerankResult(
                    memory_id=candidate.memory_id,
                    relevance_score=relevance,
                    trust_score=trust,
                    should_use_memory=usable,
                    reason=reason,
                )
            )

        # Stable, repeatable ranked order with a deterministic tie-breaker.
        results.sort(
            key=lambda r: (
                -(0.5 * r.relevance_score + 0.5 * r.trust_score),
                r.memory_id,
            )
        )
        return results

    # -- relevance ---------------------------------------------------------

    def _relevance_score(
        self,
        query_tokens: set[str],
        query_vec: Optional[list[float]],
        embedder: object,
        candidate: MemoryRecord,
    ) -> float:
        """Blend lexical Jaccard with optional semantic cosine, in ``[0, 1]``."""
        lexical = _jaccard(query_tokens, set(_tokens(candidate.content)))

        if embedder is None or query_vec is None:
            return clamp01(lexical)

        candidate_vec = candidate.embedding
        if not candidate_vec or len(candidate_vec) != len(query_vec):
            # Fall back to embedding the content so a missing/mismatched stored
            # vector never crashes scoring (stays deterministic per embedder).
            candidate_vec = self._safe_embed(embedder, candidate.content)

        semantic = _cosine(query_vec, candidate_vec)
        blended = self._semantic_weight * semantic + (1.0 - self._semantic_weight) * lexical
        return clamp01(blended)

    @staticmethod
    def _safe_embed(embedder: object, text: str) -> list[float]:
        """Call ``embedder.embed(text)`` defensively, returning ``[]`` on error."""
        embed = getattr(embedder, "embed", None)
        if embed is None:
            return []
        try:
            vec = embed(text)
        except Exception:  # pragma: no cover - defensive; embedder is untrusted ctx
            return []
        if not isinstance(vec, (list, tuple)):
            return []
        return [float(v) for v in vec]

    # -- trust -------------------------------------------------------------

    def _trust_score(self, candidate: MemoryRecord, now: datetime) -> float:
        """Use the persisted ``trust_score`` if set, else a signal blend.

        Both paths are bounded to ``[0, 1]`` and deterministic for fixed inputs.
        """
        if candidate.trust_score > 0.0:
            return clamp01(candidate.trust_score)
        return self._signal_trust(candidate, now)

    @staticmethod
    def _signal_trust(candidate: MemoryRecord, now: datetime) -> float:
        """Quick deterministic blend of the core trust signals in ``[0, 1]``.

        ``positive = wa*authority + wf*freshness + wc*confirmations`` (weights
        sum to 1, so ``positive`` is in ``[0, 1]``), attenuated multiplicatively
        by the contradiction and sensitivity penalties so the result stays
        bounded and never rises when a penalty grows.
        """
        authority = source_authority(candidate)
        fresh = freshness(candidate, now)
        confirm = confirmation_score(candidate)
        positive = (
            _W_AUTHORITY * authority + _W_FRESHNESS * fresh + _W_CONFIRM * confirm
        )
        contra = contradiction_penalty(candidate)
        sens = sensitivity_penalty(candidate)
        return clamp01(positive * (1.0 - contra) * (1.0 - sens))

    # -- decision + reason -------------------------------------------------

    def _should_use(
        self, relevance: float, trust: float, candidate: MemoryRecord
    ) -> bool:
        """Binary surface decision: clears both floors and is not dead."""
        if candidate.status in _EXCLUDED_STATUSES:
            return False
        return relevance >= self._rel_min and trust >= self._trust_min

    @staticmethod
    def _build_reason(
        relevance: float,
        trust: float,
        candidate: MemoryRecord,
        usable: bool,
    ) -> str:
        """Compose a short, non-empty, human-readable explanation."""
        parts: list[str] = []

        if relevance >= 0.6:
            parts.append("strong relevance match")
        elif relevance >= 0.25:
            parts.append("moderate relevance match")
        elif relevance > 0.0:
            parts.append("weak relevance match")
        else:
            parts.append("no relevance overlap")

        if trust >= 0.7:
            parts.append("high trust")
        elif trust >= 0.4:
            parts.append("medium trust")
        else:
            parts.append("low trust")

        if candidate.contradicts:
            parts.append("has contradictions")

        if candidate.status in _EXCLUDED_STATUSES:
            parts.append(f"excluded ({candidate.status.value})")

        decision = "surface memory" if usable else "skip memory"
        reason = "; ".join(parts) + f" -> {decision}"
        # Guarantee non-emptiness even under unexpected inputs.
        return reason or "scored"

    # -- helpers -----------------------------------------------------------

    @staticmethod
    def _resolve_now(ctx: dict) -> datetime:
        """Return ``ctx['now']`` when a valid datetime, else ``utcnow``."""
        now = ctx.get("now")
        if isinstance(now, datetime):
            return now
        return datetime.now(timezone.utc)


__all__ = [
    "HeuristicReranker",
    "DEFAULT_MODEL_ID",
    "DEFAULT_VERSION",
    "DEFAULT_REL_MIN",
    "DEFAULT_TRUST_MIN",
]
