# SPDX-License-Identifier: Apache-2.0
"""Final-rank blending for the Retrieval & Policy Layer (OSS core).

This module implements the **final rank** step of the design's *two-stage
pipeline* (see design's *Component: Retrieval & Policy Layer* — "Final rank +
audit"). After Stage-2 reranking emits a ``relevance_score`` and a
``trust_score`` per surviving candidate, the :class:`RetrievalService` orders
results by a single combined score, the ``final_rank``:

    final_rank = relevance * w_r + trust * w_t

This is exactly the design's documented blend
(``relevance * w_r + trust_score * w_t``). The default weights are ``0.5`` /
``0.5`` so relevance and trust contribute equally; deployments may retune them
via :class:`RankWeights` while keeping the contract (sorted by ``final_rank``
descending, every result explained) unchanged.

Bounds
------
Inputs are clamped to ``[0, 1]`` defensively, and when the weights sum to
``1.0`` the blended output also lands in ``[0, 1]``. Non-normalized weights are
permitted (the blend stays a monotone, non-decreasing function of both inputs),
but the OSS default keeps ``w_r + w_t == 1.0``.

This module is dependency-free (Python standard library only) and part of the
Apache-2.0 OSS core. It MUST NOT import from any commercial package.

Requirements: 4.4 (combine relevance and ``trust_score`` into a ``final_rank``
before ordering), 5.1 (order returned memories by ``final_rank`` descending),
22.4 (final rank from the reranker's combined relevance + trust).
"""

from __future__ import annotations

from dataclasses import dataclass

__all__ = [
    "RankWeights",
    "DEFAULT_RANK_WEIGHTS",
    "final_rank",
    "W_RELEVANCE",
    "W_TRUST",
]


#: Default weight on the relevance component of the final rank blend.
W_RELEVANCE = 0.5

#: Default weight on the trust component of the final rank blend.
W_TRUST = 0.5


@dataclass(frozen=True)
class RankWeights:
    """Weights for blending relevance and trust into a ``final_rank``.

    Attributes:
        relevance: weight ``w_r`` applied to the relevance component.
        trust: weight ``w_t`` applied to the trust component.

    The OSS default (:data:`DEFAULT_RANK_WEIGHTS`) uses ``0.5`` / ``0.5`` so the
    two signals contribute equally and a blend of two values in ``[0, 1]`` stays
    in ``[0, 1]``. Both weights must be non-negative.
    """

    relevance: float = W_RELEVANCE
    trust: float = W_TRUST

    def __post_init__(self) -> None:
        if self.relevance < 0.0 or self.trust < 0.0:
            raise ValueError("RankWeights must be non-negative")


#: OSS default rank weights (equal 0.5 / 0.5 relevance / trust blend).
DEFAULT_RANK_WEIGHTS = RankWeights()


def _clamp01(value: float) -> float:
    """Clamp ``value`` into the inclusive range ``[0.0, 1.0]``."""
    numeric = float(value)
    if numeric < 0.0:
        return 0.0
    if numeric > 1.0:
        return 1.0
    return numeric


def final_rank(
    relevance: float,
    trust: float,
    weights: RankWeights = DEFAULT_RANK_WEIGHTS,
) -> float:
    """Blend ``relevance`` and ``trust`` into a single ``final_rank`` score.

    Computes ``relevance * w_r + trust * w_t`` after clamping both inputs to
    ``[0, 1]``. With the default (and any) normalized weights the result lies in
    ``[0, 1]``; the blend is non-decreasing in both inputs (raising relevance or
    trust never lowers the rank), which keeps ordering well-behaved.

    Args:
        relevance: Stage-2 relevance score (clamped to ``[0, 1]``).
        trust: Stage-2 trust score (clamped to ``[0, 1]``).
        weights: relevance/trust :class:`RankWeights` (default ``0.5`` / ``0.5``).

    Returns:
        The combined ``final_rank`` as a ``float``.
    """
    r = _clamp01(relevance)
    t = _clamp01(trust)
    return r * weights.relevance + t * weights.trust
