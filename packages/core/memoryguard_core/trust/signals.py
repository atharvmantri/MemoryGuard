# SPDX-License-Identifier: Apache-2.0
"""Normalized trust signal functions for the MemoryGuard Trust Engine.

Each public signal function consumes a :class:`MemoryRecord` (and ``now`` where
time matters) and returns a ``float`` in the closed interval ``[0.0, 1.0]``.
These are the OSS, dependency-free (stdlib only) building blocks of the
deterministic trust-scoring baseline described in the design's
*Trust Scoring Model -> Signal Definitions*.

Sign convention (see design):
  * ``source_authority``, ``freshness``, ``confirmation_score`` and
    ``correction_signal`` are *positive* signals: higher means more trustworthy.
  * ``contradiction_penalty`` and ``sensitivity_penalty`` are *penalty
    magnitudes*: higher means less trustworthy. They enter the weighted trust
    formula as ``(1 - penalty)``.

All functions are deterministic for fixed inputs and constants, and are
designed so that the monotonicity guarantees in Requirements 7.2/7.3 and 26
hold (e.g. more confirmations never lower ``confirmation_score``; a higher
sensitivity tier never lowers ``sensitivity_penalty``).

This module is part of the Apache-2.0 OSS core and MUST NOT import from any
commercial package. It runs entirely on-device with no external LLM API.
"""

from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import Iterable

from memoryguard_core.models import MemoryRecord, Sensitivity, SourceType

__all__ = [
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
    # Tunable constants (exported for tests / calibration).
    "HALF_LIFE_DAYS",
    "CONFIRM_SCALE",
    "CONTRA_SCALE",
    "CORRECTION_SCALE",
    "SOURCE_AUTHORITY_BASE",
    "SENSITIVITY_PENALTY",
]


# ---------------------------------------------------------------------------
# Tunable constants
# ---------------------------------------------------------------------------

#: Exponential-decay half-life (in days) used by :func:`freshness` when a record
#: has no ``expires_at``. After ``HALF_LIFE_DAYS`` days the freshness signal has
#: decayed to ``exp(-1) ~= 0.368``.
HALF_LIFE_DAYS: float = 90.0

#: Saturation scale for :func:`confirmation_score`. Larger => slower saturation.
CONFIRM_SCALE: float = 3.0

#: Saturation scale for :func:`contradiction_penalty`. ``CONTRA_SCALE`` unresolved
#: contradictions drive the penalty to ~1.0 (after clamping).
CONTRA_SCALE: float = 3.0

#: Saturation scale for :func:`correction_signal`. Larger => slower movement away
#: from the neutral midpoint as corrections accumulate.
CORRECTION_SCALE: float = 3.0

#: Base authority per :class:`SourceType` (before the reputation adjustment).
SOURCE_AUTHORITY_BASE: dict[SourceType, float] = {
    SourceType.COMMIT: 0.9,
    SourceType.FILE: 0.8,
    SourceType.API: 0.7,
    SourceType.JIRA: 0.7,
    SourceType.USER: 0.6,
    SourceType.SLACK: 0.5,
}

#: Penalty magnitude per sensitivity tier (higher tier => larger penalty).
SENSITIVITY_PENALTY: dict[Sensitivity, float] = {
    Sensitivity.PUBLIC: 0.0,
    Sensitivity.INTERNAL: 0.2,
    Sensitivity.SECRET: 0.6,
    Sensitivity.PII: 0.8,
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def clamp01(value: float) -> float:
    """Clamp ``value`` into the inclusive range ``[0.0, 1.0]``.

    Accepts any value coercible to ``float`` and always returns a ``float``.
    Used to guarantee every signal stays normalized regardless of intermediate
    arithmetic (e.g. a future timestamp, an over-long lifetime, or a reputation
    factor above ``1.0``).
    """

    numeric = float(value)
    if numeric < 0.0:
        return 0.0
    if numeric > 1.0:
        return 1.0
    return numeric


def reputation_factor(source_ref: str) -> float:
    """Return a multiplicative reputation factor for a ``source_ref``.

    This is the OSS baseline hook: it always returns ``1.0`` (neutral), so the
    base authority of the source type is used unchanged. Commercial deployments
    can swap in a reputation table (e.g. trusted repos, vetted authors) behind
    the same interface without changing :func:`source_authority`.

    The returned factor is expected to be ``>= 0.0``; :func:`source_authority`
    clamps the final result into ``[0, 1]`` defensively.
    """

    return 1.0


def _ensure_aware(value: datetime) -> datetime:
    """Return a timezone-aware ``datetime``, assuming UTC for naive inputs.

    Trust signals subtract two ``datetime`` values, which raises ``TypeError``
    in Python when one operand is naive and the other is aware. Callers may pass
    either flavor (the core factory produces aware UTC timestamps, but a user or
    external store could hand us naive ones), so we normalize defensively: a
    naive ``datetime`` is interpreted as UTC, and an aware one is left untouched.
    This keeps :func:`age_days` and :func:`freshness` robust regardless of how
    ``created_at`` / ``expires_at`` / ``now`` were constructed.
    """

    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def age_days(record: MemoryRecord, now: datetime) -> float:
    """Return the age of ``record`` in days as of ``now``.

    Computed as ``(now - record.created_at)`` in days. Both timestamps are made
    timezone-aware first (naive values are treated as UTC) so mixing naive and
    aware inputs never raises. Per the design's preconditions ``now >=
    created_at``; if a ``now`` before ``created_at`` is supplied the age is
    clamped to ``0.0`` so a "future" record never appears *fresher* than a
    brand-new one.
    """

    delta_seconds = (
        _ensure_aware(now) - _ensure_aware(record.created_at)
    ).total_seconds()
    if delta_seconds <= 0.0:
        return 0.0
    return delta_seconds / 86_400.0


def unresolved(contradicts: Iterable[str]) -> list[str]:
    """Return the list of unresolved contradiction ``memory_id`` values.

    For the OSS baseline every listed contradiction is treated as unresolved
    (there is no resolution workflow in the core engine yet). Duplicate ids are
    collapsed so the penalty reflects distinct conflicting memories. Commercial
    modules may override this to exclude reconciled/dismissed contradictions.
    """

    seen: set[str] = set()
    result: list[str] = []
    for memory_id in contradicts or []:
        if memory_id not in seen:
            seen.add(memory_id)
            result.append(memory_id)
    return result


# ---------------------------------------------------------------------------
# Signals
# ---------------------------------------------------------------------------


def source_authority(record: MemoryRecord) -> float:
    """Authority of a memory based on where it came from.

    Base authority is keyed by :class:`SourceType` (COMMIT 0.9, FILE 0.8,
    API 0.7, JIRA 0.7, USER 0.6, SLACK 0.5) and multiplied by
    :func:`reputation_factor` of the ``source_ref`` (``1.0`` in the OSS
    baseline). The product is clamped into ``[0, 1]``.
    """

    base = SOURCE_AUTHORITY_BASE.get(record.source_type, 0.5)
    return clamp01(base * reputation_factor(record.source_ref))


def freshness(record: MemoryRecord, now: datetime) -> float:
    """Recency signal in ``[0, 1]`` that never increases with age.

    * If ``expires_at`` is set, freshness decays *linearly* from ``1.0`` at
      ``created_at`` to ``0.0`` at ``expires_at`` (and stays ``0.0`` past
      expiry).
    * Otherwise freshness decays *exponentially* with a half-life of
      ``HALF_LIFE_DAYS`` days: ``exp(-age_days / HALF_LIFE_DAYS)``.

    In both cases the result is clamped into ``[0, 1]``. Because age is clamped
    to be non-negative, an older record never scores higher than a newer one.
    """

    if record.expires_at is not None:
        created = _ensure_aware(record.created_at)
        expires = _ensure_aware(record.expires_at)
        moment = _ensure_aware(now)
        life_seconds = (expires - created).total_seconds()
        if life_seconds <= 0.0:
            # Defensive: validation forbids expires_at <= created_at.
            return 0.0
        age_seconds = (moment - created).total_seconds()
        if age_seconds <= 0.0:
            return 1.0
        return clamp01(1.0 - (age_seconds / life_seconds))

    return clamp01(math.exp(-age_days(record, now) / HALF_LIFE_DAYS))


def confirmation_score(record: MemoryRecord) -> float:
    """Saturating signal that grows with the number of confirmations.

    Uses ``1 - exp(-confirmations / CONFIRM_SCALE)``: ``0.0`` at zero
    confirmations and asymptotically approaching ``1.0``. Monotonically
    non-decreasing in ``confirmations``. Negative confirmation counts (which
    validation should never produce) are floored to ``0``.
    """

    confirmations = max(0, int(record.confirmations))
    return clamp01(1.0 - math.exp(-confirmations / CONFIRM_SCALE))


def contradiction_penalty(record: MemoryRecord) -> float:
    """Penalty magnitude in ``[0, 1]`` for unresolved contradictions.

    Returns ``0.0`` when the record contradicts nothing; otherwise
    ``clamp01(len(unresolved(contradicts)) / CONTRA_SCALE)``. Adding an
    unresolved contradiction never decreases the penalty (so it never increases
    the resulting trust score).
    """

    if not record.contradicts:
        return 0.0
    return clamp01(len(unresolved(record.contradicts)) / CONTRA_SCALE)


def sensitivity_penalty(record: MemoryRecord) -> float:
    """Penalty magnitude in ``[0, 1]`` by sensitivity tier.

    PUBLIC 0.0, INTERNAL 0.2, SECRET 0.6, PII 0.8. Higher tiers carry a larger
    penalty, so raising sensitivity never increases the trust score.
    """

    return SENSITIVITY_PENALTY[record.sensitivity]


def correction_signal(record: MemoryRecord) -> float:
    """User-correction signal in ``[0, 1]`` (a *positive* trust signal).

    This is the new signal introduced by Requirement 26. It reads a recorded
    user-correction from ``record.metadata`` and maps it onto a positive signal
    where **higher means more trustworthy**:

    Convention
    ----------
    * ``0.5`` is *neutral* and is returned when no correction information is
      present (the default — corrections have not touched this memory).
    * An ``affirm`` correction *raises* the signal above ``0.5`` toward ``1.0``.
    * A ``supersede`` correction *lowers* the signal below ``0.5`` toward
      ``0.0``.

    Because this signal feeds the weighted trust sum positively, an ``affirm``
    can only raise (never lower) trust and a ``supersede`` can only lower
    (never raise) trust — satisfying Requirement 26.4.

    Metadata shape (any of the following):
    * ``metadata['correction']`` (preferred) or ``metadata['correction_kind']``:
      one of ``'affirm'`` / ``'supersede'`` (case-insensitive). Direction of the
      adjustment. ``'correction'`` takes precedence when both are present.
    * ``metadata['corrections']``: integer count of how many corrections of that
      kind have been recorded (default ``1`` when a correction kind is
      present). More corrections push the signal further from neutral, with
      saturating magnitude ``1 - exp(-count / CORRECTION_SCALE)``.

    If a correction kind is absent but a positive ``corrections`` count is
    present, the corrections are treated as affirmations (the common case of a
    re-affirmed memory). An unrecognized correction kind is treated as neutral.
    """

    metadata = record.metadata or {}

    # ``correction`` is the canonical key (per Requirement 26); ``correction_kind``
    # is accepted as a synonym for backward compatibility.
    raw_kind = metadata.get("correction")
    if raw_kind is None:
        raw_kind = metadata.get("correction_kind")
    kind = str(raw_kind).strip().lower() if raw_kind is not None else None

    # Resolve a non-negative correction count.
    raw_count = metadata.get("corrections")
    try:
        count = int(raw_count) if raw_count is not None else None
    except (TypeError, ValueError):
        count = None

    # Decide direction and effective magnitude count.
    if kind == "affirm":
        effective = count if count is not None else 1
    elif kind == "supersede":
        effective = count if count is not None else 1
    elif kind is None and count is not None and count > 0:
        # No explicit kind but corrections recorded -> treat as affirmations.
        kind = "affirm"
        effective = count
    else:
        # Absent or unrecognized -> neutral.
        return 0.5

    effective = max(0, int(effective))
    if effective == 0:
        return 0.5

    magnitude = 1.0 - math.exp(-effective / CORRECTION_SCALE)  # in (0, 1)
    if kind == "supersede":
        return clamp01(0.5 - 0.5 * magnitude)
    return clamp01(0.5 + 0.5 * magnitude)  # affirm
