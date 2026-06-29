# SPDX-License-Identifier: Apache-2.0
"""Deterministic trust-scoring baseline for the MemoryGuard Trust Engine.

This module implements :class:`DeterministicTrustModel` — the OSS default
``TrustModel`` / ``TrustScorer`` (see ``memoryguard_models.base``). It turns the
six normalized trust signals produced by
:mod:`memoryguard_core.trust.signals` into a single bounded ``trust_score`` in
``[0, 1]`` using the documented weighted-sum formula from the design's
*Trust Scoring Model*::

    trust = source_authority            * 0.35
          + freshness                   * 0.20
          + confirmation_score          * 0.20
          + (1 - contradiction_penalty) * 0.15
          + (1 - sensitivity_penalty)   * 0.10

Because every signal is in ``[0, 1]`` and the five weights sum to ``1.0``, this
*five-term* sum is itself guaranteed to lie in ``[0, 1]``; with all positive
signals maximal and both penalties zero it evaluates to **exactly 1.0**
(design Property 7 / Requirement 7.1).

User corrections (Requirement 26)
---------------------------------
``TrustSignals`` carries a sixth, *positive* ``correction_signal`` in ``[0, 1]``
(see :func:`memoryguard_core.trust.signals.correction_signal`) whose neutral
value is :data:`NEUTRAL_CORRECTION` (``0.5``). To honour Requirement 26.1
("combine deterministic rule signals ... with user corrections") **without**
breaking the bounded / all-max guarantees of the five-term formula, the
correction enters as a small, signed, *centered* adjustment::

    trust = clamp01(five_term_sum
                    + CORRECTION_WEIGHT * (correction_signal - NEUTRAL_CORRECTION))

This choice is deliberate and has these properties:

* **Neutral is a no-op.** When no correction is present the signal is ``0.5``,
  the adjustment is ``0``, and the score equals the pure five-term sum — so the
  *all-signals-maximal => exactly 1.0* guarantee (Property 7) is preserved.
* **Monotonic in the documented direction (Property 30).** An ``affirm``
  correction raises ``correction_signal`` above ``0.5`` (a non-negative
  adjustment => never lowers trust); a ``supersede`` correction lowers it below
  ``0.5`` (a non-positive adjustment => never raises trust).
* **Stays bounded.** The result is clamped into ``[0, 1]`` defensively
  regardless (Requirement 7.4); the small ``CORRECTION_WEIGHT`` keeps the
  adjustment from dominating the authority/freshness/confirmation signals.

The model is fully deterministic for fixed inputs and constants (Property 2 /
Requirement 26.3) and performs **no external LLM API calls** — it is pure
arithmetic over the local signals (Requirement 26.5). A learned/commercial
``TrustModel`` may be injected behind the same interface when the
``learned_trust_model`` flag is enabled (Requirement 26.2); this deterministic
baseline is the default when the flag is off and is selected by the
``TrustEngine`` (task 7.3).

Apache-2.0 OSS core: this module MUST NOT import from any commercial package.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Callable, Optional

from memoryguard_core.models import MemoryRecord
from memoryguard_core.trust.signals import (
    clamp01,
    confirmation_score,
    contradiction_penalty,
    correction_signal,
    freshness,
    sensitivity_penalty,
    source_authority,
)
from memoryguard_models.base import ModelInfo, TrustModel, TrustScorer, TrustSignals

__all__ = [
    "WEIGHTS",
    "CORRECTION_WEIGHT",
    "NEUTRAL_CORRECTION",
    "DeterministicTrustModel",
    "compute_signals",
    "score_record",
]

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Weights / constants (exported for tests and calibration)
# ---------------------------------------------------------------------------

#: Weights for the five-term deterministic formula (design *Trust Scoring
#: Model*). They sum to ``1.0`` so the five-term sum is bounded in ``[0, 1]``
#: without clamping. Penalties (``contradiction_penalty`` /
#: ``sensitivity_penalty``) are *penalty magnitudes* and enter the sum as
#: ``(1 - penalty)`` weighted by their entry below.
WEIGHTS: dict[str, float] = {
    "source_authority": 0.35,
    "freshness": 0.20,
    "confirmation_score": 0.20,
    "contradiction_penalty": 0.15,
    "sensitivity_penalty": 0.10,
}

#: Neutral value of the positive ``correction_signal`` (no correction recorded).
#: At this value the correction adjustment is exactly ``0`` so the five-term
#: weight-conservation guarantee (Property 7) is preserved.
NEUTRAL_CORRECTION: float = 0.5

#: Scale of the centered user-correction adjustment. The adjustment is
#: ``CORRECTION_WEIGHT * (correction_signal - NEUTRAL_CORRECTION)`` and therefore
#: lies in ``[-CORRECTION_WEIGHT/2, +CORRECTION_WEIGHT/2]``. Kept small so the
#: deterministic authority/freshness/confirmation signals stay dominant while a
#: correction still nudges trust in the documented direction.
CORRECTION_WEIGHT: float = 0.10


# ---------------------------------------------------------------------------
# Signal assembly helper
# ---------------------------------------------------------------------------


def compute_signals(record: MemoryRecord, now: datetime) -> TrustSignals:
    """Assemble the normalized :class:`TrustSignals` for ``record`` at ``now``.

    Thin, dependency-free aggregator over the individual signal functions in
    :mod:`memoryguard_core.trust.signals`. Each component is already clamped to
    ``[0, 1]`` by its producing function. Exposed at module level so both
    :func:`score_record` and the ``TrustEngine`` (task 7.3) can reuse a single
    canonical signal-computation path.
    """

    return TrustSignals(
        source_authority=source_authority(record),
        freshness=freshness(record, now),
        confirmation_score=confirmation_score(record),
        contradiction_penalty=contradiction_penalty(record),
        sensitivity_penalty=sensitivity_penalty(record),
        correction_signal=correction_signal(record),
    )


# ---------------------------------------------------------------------------
# Deterministic trust model
# ---------------------------------------------------------------------------


class DeterministicTrustModel(TrustModel):
    """OSS baseline ``TrustModel`` — the documented weighted-sum formula.

    Implements the ``TrustModel`` / ``TrustScorer`` contract. ``score`` is a
    pure, deterministic function of the supplied ``signals`` (plus the centered
    user-correction adjustment); it requires no external LLM API and is the
    default scorer when the ``learned_trust_model`` flag is off.
    """

    #: Stable identity + version for reproducibility (design Model Layer).
    _INFO = ModelInfo(model_id="trust/deterministic", task="trust", version="1.0.0")

    @property
    def info(self) -> ModelInfo:
        """Stable identity + version for reproducibility."""

        return self._INFO

    def score(
        self,
        record: MemoryRecord,
        signals: TrustSignals,
        now: datetime,
    ) -> float:
        """Return the bounded trust score in ``[0, 1]`` for ``record``.

        Computes the five-term weighted sum (penalties entering as
        ``1 - penalty``) then applies the centered user-correction adjustment.
        The raw value is clamped into ``[0, 1]``; if it ever falls outside that
        range a warning is logged (Requirement 7.4) — under the deterministic
        baseline this only happens at the boundaries via the correction nudge,
        but the guard also protects against an injected learned model.

        Args:
            record: the memory being scored (used for identity in logs; the
                numeric inputs come entirely from ``signals``).
            signals: the normalized :class:`TrustSignals` (each in ``[0, 1]``).
            now: scoring time (kept for interface symmetry / future use).

        Returns:
            The trust score, a ``float`` in ``[0.0, 1.0]``.
        """

        base = (
            signals.source_authority * WEIGHTS["source_authority"]
            + signals.freshness * WEIGHTS["freshness"]
            + signals.confirmation_score * WEIGHTS["confirmation_score"]
            + (1.0 - signals.contradiction_penalty) * WEIGHTS["contradiction_penalty"]
            + (1.0 - signals.sensitivity_penalty) * WEIGHTS["sensitivity_penalty"]
        )

        # Centered, signed user-correction adjustment. Zero at the neutral
        # midpoint so the five-term all-max => 1.0 guarantee is preserved;
        # positive for affirmations (never lowers) and negative for supersedes
        # (never raises).
        adjustment = CORRECTION_WEIGHT * (signals.correction_signal - NEUTRAL_CORRECTION)

        raw = base + adjustment

        if raw < 0.0 or raw > 1.0:
            logger.warning(
                "Computed trust score %r for memory %r is outside [0.0, 1.0]; "
                "clamping into range.",
                raw,
                getattr(record, "memory_id", "<unknown>"),
            )

        return clamp01(raw)


#: Public interface alias check — ``DeterministicTrustModel`` is a ``TrustScorer``
#: (``TrustScorer`` is an alias of ``TrustModel`` in ``memoryguard_models.base``).
assert issubclass(DeterministicTrustModel, TrustScorer)


# ---------------------------------------------------------------------------
# Convenience helper
# ---------------------------------------------------------------------------


def score_record(
    record: MemoryRecord,
    now: datetime,
    signals_provider: Optional[Callable[[MemoryRecord, datetime], TrustSignals]] = None,
    *,
    model: Optional[TrustModel] = None,
) -> float:
    """Compute, then score, a record's trust in one call.

    Convenience wrapper for callers (e.g. the ``TrustEngine`` in task 7.3) that
    have a record + time but not yet a :class:`TrustSignals`. It resolves the
    signals via ``signals_provider`` (defaulting to :func:`compute_signals`) and
    scores them with ``model`` (defaulting to a :class:`DeterministicTrustModel`).

    Args:
        record: the memory to score.
        now: scoring time, passed through to the provider and the model.
        signals_provider: optional ``(record, now) -> TrustSignals`` callable;
            allows the engine to inject corrections or a custom signal pipeline.
        model: optional ``TrustModel`` to score with (the OSS deterministic
            baseline by default).

    Returns:
        The trust score in ``[0.0, 1.0]``.
    """

    provider = signals_provider if signals_provider is not None else compute_signals
    scorer = model if model is not None else DeterministicTrustModel()
    signals = provider(record, now)
    return scorer.score(record, signals, now)
