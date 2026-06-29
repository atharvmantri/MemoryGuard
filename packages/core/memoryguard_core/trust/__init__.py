# SPDX-License-Identifier: Apache-2.0
"""MemoryGuard Trust Engine package (OSS core).

Exposes the normalized trust signal functions and the ``clamp01`` helper that
make up the deterministic trust-scoring baseline. Higher-level scoring and
contradiction modules build on these signals.

Apache-2.0 OSS core: this package MUST NOT import from any commercial package.
"""

from __future__ import annotations

from memoryguard_core.trust.scoring import (
    CORRECTION_WEIGHT,
    NEUTRAL_CORRECTION,
    WEIGHTS,
    DeterministicTrustModel,
    compute_signals,
    score_record,
)
from memoryguard_core.trust.contradiction import (
    NEIGHBOR_LIMIT,
    SIM_THRESHOLD,
    RuleContradictionModel,
)
from memoryguard_core.trust.engine import TrustEngine
from memoryguard_core.trust.signals import (
    CONFIRM_SCALE,
    CONTRA_SCALE,
    CORRECTION_SCALE,
    HALF_LIFE_DAYS,
    SENSITIVITY_PENALTY,
    SOURCE_AUTHORITY_BASE,
    age_days,
    clamp01,
    confirmation_score,
    contradiction_penalty,
    correction_signal,
    freshness,
    reputation_factor,
    sensitivity_penalty,
    source_authority,
    unresolved,
)

__all__ = [
    "RuleContradictionModel",
    "SIM_THRESHOLD",
    "NEIGHBOR_LIMIT",
    "TrustEngine",
    "clamp01",
    "reputation_factor",
    "age_days",
    "unresolved",
    "source_authority",
    "freshness",
    "confirmation_score",
    "contradiction_penalty",
    "sensitivity_penalty",
    "correction_signal",
    "HALF_LIFE_DAYS",
    "CONFIRM_SCALE",
    "CONTRA_SCALE",
    "CORRECTION_SCALE",
    "SOURCE_AUTHORITY_BASE",
    "SENSITIVITY_PENALTY",
    # Deterministic trust scoring (task 7.2).
    "DeterministicTrustModel",
    "score_record",
    "compute_signals",
    "WEIGHTS",
    "CORRECTION_WEIGHT",
    "NEUTRAL_CORRECTION",
]
