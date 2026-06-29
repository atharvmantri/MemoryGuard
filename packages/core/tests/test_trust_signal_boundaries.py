# SPDX-License-Identifier: Apache-2.0
"""Boundary-input unit tests for individual trust signal functions.

Task 7.6 — exercises each signal function from
``memoryguard_core.trust.signals`` at its edges and asserts every signal stays
within its documented range. These are plain ``pytest`` example/boundary tests
(no Hypothesis), complementing the property-based coverage in
``test_trust_signals.py``.

Validated behaviors (boundary cases):
* ``clamp01`` at and beyond both ends of ``[0, 1]`` (Requirement 7.4 — out-of-range
  values are clamped into range; Requirement 7.3 — signals remain normalized).
* ``source_authority`` for every :class:`SourceType` stays in ``[0, 1]``.
* ``freshness`` exactly at ``created_at`` (==1.0), at expiry and after expiry
  (==0.0).
* ``confirmation_score`` at zero/min, a large max, and a floored-negative count.
* ``contradiction_penalty`` with zero and many contradictions.
* ``sensitivity_penalty`` for every tier.
* ``correction_signal`` for affirm / supersede / neutral / none.

Every assertion additionally checks the returned value lies within ``[0.0, 1.0]``
(Requirement 7.3) so the weighted trust formula (Requirement 7.4) always receives
normalized inputs.
"""

from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone

import pytest

from memoryguard_core.models import (
    MemoryRecord,
    MemoryStatus,
    Scope,
    Sensitivity,
    SourceType,
)
from memoryguard_core.trust.signals import (
    CONFIRM_SCALE,
    CONTRA_SCALE,
    HALF_LIFE_DAYS,
    SENSITIVITY_PENALTY,
    SOURCE_AUTHORITY_BASE,
    clamp01,
    confirmation_score,
    contradiction_penalty,
    correction_signal,
    freshness,
    sensitivity_penalty,
    source_authority,
)

VALID_UUID4 = "f47ac10b-58cc-4372-a567-0e02b2c3d479"
CREATED = datetime(2024, 1, 1, 12, 0, 0, tzinfo=timezone.utc)


def _make(**overrides) -> MemoryRecord:
    """Build a valid-enough MemoryRecord for signal computation."""

    kwargs = {
        "memory_id": VALID_UUID4,
        "content": "remember this fact",
        "source_type": SourceType.USER,
        "source_ref": "user://alice",
        "scope": Scope.GLOBAL,
        "scope_ref": None,
        "created_at": CREATED,
        "updated_at": CREATED,
        "expires_at": None,
        "trust_score": 0.5,
        "sensitivity": Sensitivity.INTERNAL,
        "status": MemoryStatus.ACTIVE,
    }
    kwargs.update(overrides)
    return MemoryRecord(**kwargs)


def _assert_unit_interval(value: float) -> None:
    """Every signal must stay normalized in the closed range ``[0, 1]``."""

    assert isinstance(value, float)
    assert 0.0 <= value <= 1.0


# ---------------------------------------------------------------------------
# clamp01 — at and beyond [0, 1]
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "value,expected",
    [
        # Far below the lower bound.
        (-1000.0, 0.0),
        # Just below the lower bound.
        (-1e-9, 0.0),
        # Exactly the lower bound.
        (0.0, 0.0),
        # Interior.
        (0.5, 0.5),
        # Exactly the upper bound.
        (1.0, 1.0),
        # Just above the upper bound.
        (1.0 + 1e-9, 1.0),
        # Far above the upper bound.
        (1000.0, 1.0),
    ],
)
def test_clamp01_boundaries(value, expected):
    """Validates: Requirements 7.4"""
    result = clamp01(value)
    assert result == expected
    _assert_unit_interval(result)


def test_clamp01_accepts_int_and_returns_float():
    """Validates: Requirements 7.4"""
    assert clamp01(2) == 1.0
    assert clamp01(-3) == 0.0
    assert isinstance(clamp01(0), float)


# ---------------------------------------------------------------------------
# source_authority — every source tier
# ---------------------------------------------------------------------------


def test_source_authority_each_source_type_in_range():
    """Validates: Requirements 7.3"""
    for source_type in SourceType:
        value = source_authority(_make(source_type=source_type))
        assert value == pytest.approx(SOURCE_AUTHORITY_BASE[source_type])
        _assert_unit_interval(value)


# ---------------------------------------------------------------------------
# freshness — created_at boundary, at expiry, after expiry
# ---------------------------------------------------------------------------


def test_freshness_exactly_at_created_at_is_one_exponential():
    """Validates: Requirements 7.3"""
    value = freshness(_make(expires_at=None), CREATED)
    assert value == pytest.approx(1.0)
    _assert_unit_interval(value)


def test_freshness_exactly_at_created_at_is_one_with_expiry():
    """Validates: Requirements 7.3"""
    expires = CREATED + timedelta(days=10)
    value = freshness(_make(expires_at=expires), CREATED)
    assert value == pytest.approx(1.0)
    _assert_unit_interval(value)


def test_freshness_exactly_at_expiry_is_zero():
    """Validates: Requirements 7.3"""
    expires = CREATED + timedelta(days=10)
    value = freshness(_make(expires_at=expires), expires)
    assert value == pytest.approx(0.0)
    _assert_unit_interval(value)


def test_freshness_after_expiry_is_zero():
    """Validates: Requirements 7.3"""
    expires = CREATED + timedelta(days=10)
    value = freshness(_make(expires_at=expires), expires + timedelta(days=365))
    assert value == 0.0
    _assert_unit_interval(value)


def test_freshness_at_half_life_decays_to_exp_minus_one():
    """Validates: Requirements 7.3"""
    value = freshness(_make(expires_at=None), CREATED + timedelta(days=HALF_LIFE_DAYS))
    assert value == pytest.approx(math.exp(-1.0), rel=1e-6)
    _assert_unit_interval(value)


# ---------------------------------------------------------------------------
# confirmation_score — zero/min, large max, floored negative
# ---------------------------------------------------------------------------


def test_confirmation_score_zero_is_minimum():
    """Validates: Requirements 7.3"""
    value = confirmation_score(_make(confirmations=0))
    assert value == pytest.approx(0.0)
    _assert_unit_interval(value)


def test_confirmation_score_one_confirmation():
    """Validates: Requirements 7.3"""
    value = confirmation_score(_make(confirmations=1))
    assert value == pytest.approx(1.0 - math.exp(-1.0 / CONFIRM_SCALE))
    _assert_unit_interval(value)


def test_confirmation_score_large_count_saturates_below_one():
    """Validates: Requirements 7.3"""
    value = confirmation_score(_make(confirmations=10_000))
    assert 0.999 < value <= 1.0
    _assert_unit_interval(value)


def test_confirmation_score_negative_count_floored_to_zero():
    """Validates: Requirements 7.3"""
    value = confirmation_score(_make(confirmations=-5))
    assert value == pytest.approx(0.0)
    _assert_unit_interval(value)


# ---------------------------------------------------------------------------
# contradiction_penalty — zero and many
# ---------------------------------------------------------------------------


def test_contradiction_penalty_zero_contradictions():
    """Validates: Requirements 7.3"""
    value = contradiction_penalty(_make(contradicts=[]))
    assert value == 0.0
    _assert_unit_interval(value)


def test_contradiction_penalty_single_contradiction():
    """Validates: Requirements 7.3"""
    value = contradiction_penalty(_make(contradicts=["m1"]))
    assert value == pytest.approx(1.0 / CONTRA_SCALE)
    _assert_unit_interval(value)


def test_contradiction_penalty_many_contradictions_clamped_to_one():
    """Validates: Requirements 7.3"""
    many = [f"m{i}" for i in range(100)]
    value = contradiction_penalty(_make(contradicts=many))
    assert value == 1.0
    _assert_unit_interval(value)


def test_contradiction_penalty_dedupes_duplicate_ids():
    """Validates: Requirements 7.3"""
    # Three references but only one distinct contradiction.
    value = contradiction_penalty(_make(contradicts=["dup", "dup", "dup"]))
    assert value == pytest.approx(1.0 / CONTRA_SCALE)
    _assert_unit_interval(value)


# ---------------------------------------------------------------------------
# sensitivity_penalty — each tier
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "tier,expected",
    [
        (Sensitivity.PUBLIC, 0.0),
        (Sensitivity.INTERNAL, 0.2),
        (Sensitivity.SECRET, 0.6),
        (Sensitivity.PII, 0.8),
    ],
)
def test_sensitivity_penalty_each_tier(tier, expected):
    """Validates: Requirements 7.3"""
    value = sensitivity_penalty(_make(sensitivity=tier))
    assert value == pytest.approx(expected)
    assert value == pytest.approx(SENSITIVITY_PENALTY[tier])
    _assert_unit_interval(value)


# ---------------------------------------------------------------------------
# correction_signal — affirm / supersede / neutral / none
# ---------------------------------------------------------------------------


def test_correction_signal_none_metadata_is_neutral():
    """Validates: Requirements 7.3"""
    value = correction_signal(_make(metadata={}))
    assert value == pytest.approx(0.5)
    _assert_unit_interval(value)


def test_correction_signal_explicit_none_values_are_neutral():
    """Validates: Requirements 7.3"""
    value = correction_signal(
        _make(metadata={"correction": None, "corrections": None})
    )
    assert value == pytest.approx(0.5)
    _assert_unit_interval(value)


def test_correction_signal_affirm_above_neutral():
    """Validates: Requirements 7.3"""
    value = correction_signal(_make(metadata={"correction": "affirm"}))
    assert value > 0.5
    _assert_unit_interval(value)


def test_correction_signal_supersede_below_neutral():
    """Validates: Requirements 7.3"""
    value = correction_signal(_make(metadata={"correction": "supersede"}))
    assert value < 0.5
    _assert_unit_interval(value)


def test_correction_signal_unknown_kind_is_neutral():
    """Validates: Requirements 7.3"""
    value = correction_signal(_make(metadata={"correction": "definitely-not-a-kind"}))
    assert value == pytest.approx(0.5)
    _assert_unit_interval(value)


def test_correction_signal_zero_count_with_kind_is_neutral():
    """Validates: Requirements 7.3"""
    value = correction_signal(
        _make(metadata={"correction": "affirm", "corrections": 0})
    )
    assert value == pytest.approx(0.5)
    _assert_unit_interval(value)
