# SPDX-License-Identifier: Apache-2.0
"""Tests for memoryguard_core.trust.signals normalized trust signals.

Covers the OSS, dependency-free trust signal building blocks from the design's
*Trust Scoring Model -> Signal Definitions* and the new ``correction_signal``
introduced by Requirement 26.

Validated behaviors:
* Every signal returns a value in the closed interval ``[0.0, 1.0]``
  (Requirements 7.3 — normalized signals feed the weighted formula; 26.1 — the
  correction signal is one of those normalized inputs).
* The per-signal monotonicity guarantees the design relies on (more
  confirmations never lowers ``confirmation_score``; more unresolved
  contradictions never lowers ``contradiction_penalty``; a higher sensitivity
  tier never lowers ``sensitivity_penalty``; greater age never raises
  ``freshness``).
* The ``correction_signal`` directional convention: ``affirm`` >= neutral
  ``0.5`` >= ``supersede`` (so it can feed the trust model directionally per
  Requirement 26.4).
* Timezone robustness: naive and aware timestamps may be mixed freely.

Uses pytest + Hypothesis (already used elsewhere in this package).
"""

from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone

import pytest
from hypothesis import given
from hypothesis import strategies as st

from memoryguard_core.models import (
    MemoryRecord,
    MemoryStatus,
    Scope,
    Sensitivity,
    SourceType,
)
from memoryguard_core.trust.signals import (
    CONTRA_SCALE,
    HALF_LIFE_DAYS,
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


# ---------------------------------------------------------------------------
# clamp01
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "value,expected",
    [
        (-1.0, 0.0),
        (0.0, 0.0),
        (0.5, 0.5),
        (1.0, 1.0),
        (2.5, 1.0),
        (-0.0001, 0.0),
        (1.0001, 1.0),
    ],
)
def test_clamp01_examples(value, expected):
    assert clamp01(value) == expected


@given(st.floats(allow_nan=False, allow_infinity=False, width=32))
def test_clamp01_always_in_unit_interval(x):
    """Validates: Requirements 7.3"""
    result = clamp01(x)
    assert 0.0 <= result <= 1.0


def test_clamp01_accepts_int_returns_float():
    assert clamp01(5) == 1.0
    assert isinstance(clamp01(0), float)


# ---------------------------------------------------------------------------
# source_authority
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "source_type,expected",
    [
        (SourceType.COMMIT, 0.9),
        (SourceType.FILE, 0.8),
        (SourceType.API, 0.7),
        (SourceType.JIRA, 0.7),
        (SourceType.USER, 0.6),
        (SourceType.SLACK, 0.5),
    ],
)
def test_source_authority_base_table(source_type, expected):
    record = _make(source_type=source_type)
    assert source_authority(record) == pytest.approx(expected)


def test_reputation_factor_is_neutral_in_oss():
    assert reputation_factor("user://alice") == 1.0


def test_source_authority_in_unit_interval_for_all_source_types():
    """Validates: Requirements 7.3"""
    for source_type in SourceType:
        record = _make(source_type=source_type)
        value = source_authority(record)
        assert 0.0 <= value <= 1.0


# ---------------------------------------------------------------------------
# freshness
# ---------------------------------------------------------------------------


def test_freshness_is_one_at_creation_exponential():
    record = _make(expires_at=None)
    assert freshness(record, CREATED) == pytest.approx(1.0)


def test_freshness_exponential_half_life():
    record = _make(expires_at=None)
    now = CREATED + timedelta(days=HALF_LIFE_DAYS)
    assert freshness(record, now) == pytest.approx(math.exp(-1.0), rel=1e-6)


def test_freshness_linear_decay_with_expiry():
    expires = CREATED + timedelta(days=10)
    record = _make(expires_at=expires)
    midpoint = CREATED + timedelta(days=5)
    assert freshness(record, midpoint) == pytest.approx(0.5)
    # At/after expiry, freshness is floored at 0.
    assert freshness(record, expires) == pytest.approx(0.0)
    assert freshness(record, expires + timedelta(days=3)) == 0.0


def test_freshness_future_now_clamped_to_one():
    record = _make(expires_at=None)
    past = CREATED - timedelta(days=5)
    assert freshness(record, past) == pytest.approx(1.0)


@given(
    age=st.integers(min_value=0, max_value=4000),
    extra=st.integers(min_value=1, max_value=4000),
)
def test_freshness_non_increasing_with_age(age, extra):
    """Validates: Requirements 7.3"""
    record = _make(expires_at=None)
    younger = freshness(record, CREATED + timedelta(days=age))
    older = freshness(record, CREATED + timedelta(days=age + extra))
    assert older <= younger + 1e-12
    assert 0.0 <= older <= 1.0
    assert 0.0 <= younger <= 1.0


# ---------------------------------------------------------------------------
# confirmation_score
# ---------------------------------------------------------------------------


def test_confirmation_score_zero_at_zero():
    assert confirmation_score(_make(confirmations=0)) == pytest.approx(0.0)


def test_confirmation_score_saturates_below_one():
    value = confirmation_score(_make(confirmations=1000))
    assert 0.99 < value <= 1.0


@given(
    base=st.integers(min_value=0, max_value=500),
    extra=st.integers(min_value=0, max_value=500),
)
def test_confirmation_score_monotonic_non_decreasing(base, extra):
    """Validates: Requirements 7.3"""
    low = confirmation_score(_make(confirmations=base))
    high = confirmation_score(_make(confirmations=base + extra))
    assert high >= low - 1e-12
    assert 0.0 <= low <= 1.0
    assert 0.0 <= high <= 1.0


# ---------------------------------------------------------------------------
# contradiction_penalty / unresolved
# ---------------------------------------------------------------------------


def test_contradiction_penalty_zero_when_none():
    assert contradiction_penalty(_make(contradicts=[])) == 0.0


def test_unresolved_dedupes():
    assert unresolved(["a", "a", "b"]) == ["a", "b"]
    assert unresolved([]) == []


def test_contradiction_penalty_scales_with_count():
    one = contradiction_penalty(_make(contradicts=["m1"]))
    assert one == pytest.approx(1.0 / CONTRA_SCALE)


@given(n=st.integers(min_value=0, max_value=50))
def test_contradiction_penalty_in_unit_interval(n):
    """Validates: Requirements 7.3"""
    ids = [f"m{i}" for i in range(n)]
    value = contradiction_penalty(_make(contradicts=ids))
    assert 0.0 <= value <= 1.0


@given(
    base=st.integers(min_value=0, max_value=20),
    extra=st.integers(min_value=1, max_value=20),
)
def test_contradiction_penalty_non_decreasing(base, extra):
    """Validates: Requirements 7.3"""
    low = contradiction_penalty(_make(contradicts=[f"a{i}" for i in range(base)]))
    high = contradiction_penalty(
        _make(contradicts=[f"a{i}" for i in range(base + extra)])
    )
    assert high >= low - 1e-12


# ---------------------------------------------------------------------------
# sensitivity_penalty
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
def test_sensitivity_penalty_table(tier, expected):
    assert sensitivity_penalty(_make(sensitivity=tier)) == pytest.approx(expected)


def test_sensitivity_penalty_increases_with_tier():
    """Validates: Requirements 7.3"""
    order = [
        Sensitivity.PUBLIC,
        Sensitivity.INTERNAL,
        Sensitivity.SECRET,
        Sensitivity.PII,
    ]
    values = [sensitivity_penalty(_make(sensitivity=t)) for t in order]
    assert values == sorted(values)
    assert all(0.0 <= v <= 1.0 for v in values)


# ---------------------------------------------------------------------------
# correction_signal (Requirement 26)
# ---------------------------------------------------------------------------


def test_correction_signal_neutral_when_absent():
    """Validates: Requirements 26.1"""
    assert correction_signal(_make(metadata={})) == pytest.approx(0.5)


def test_correction_signal_affirm_raises_above_neutral():
    """Validates: Requirements 26.1"""
    value = correction_signal(_make(metadata={"correction": "affirm"}))
    assert value > 0.5
    assert 0.0 <= value <= 1.0


def test_correction_signal_supersede_lowers_below_neutral():
    """Validates: Requirements 26.1"""
    value = correction_signal(_make(metadata={"correction": "supersede"}))
    assert value < 0.5
    assert 0.0 <= value <= 1.0


def test_correction_signal_case_insensitive():
    assert correction_signal(_make(metadata={"correction": "AFFIRM"})) > 0.5
    assert correction_signal(_make(metadata={"correction": "Supersede"})) < 0.5


def test_correction_signal_synonym_key():
    """``correction_kind`` is accepted as a synonym for ``correction``."""
    assert correction_signal(_make(metadata={"correction_kind": "affirm"})) > 0.5


def test_correction_signal_unknown_kind_is_neutral():
    assert correction_signal(_make(metadata={"correction": "maybe"})) == pytest.approx(0.5)


def test_correction_signal_bare_count_treated_as_affirm():
    assert correction_signal(_make(metadata={"corrections": 2})) > 0.5


def test_correction_signal_more_corrections_move_further_from_neutral():
    one = correction_signal(_make(metadata={"correction": "supersede", "corrections": 1}))
    many = correction_signal(_make(metadata={"correction": "supersede", "corrections": 10}))
    assert many <= one


@given(
    kind=st.sampled_from(["affirm", "supersede", "other", None]),
    count=st.integers(min_value=0, max_value=100),
)
def test_correction_signal_in_unit_interval(kind, count):
    """Validates: Requirements 26.1"""
    metadata = {"corrections": count}
    if kind is not None:
        metadata["correction"] = kind
    value = correction_signal(_make(metadata=metadata))
    assert 0.0 <= value <= 1.0


# ---------------------------------------------------------------------------
# Timezone robustness
# ---------------------------------------------------------------------------


def test_freshness_handles_naive_and_aware_mix():
    """Naive and aware timestamps may be mixed without raising."""
    naive_created = datetime(2024, 1, 1, 12, 0, 0)  # no tzinfo
    record = _make(created_at=naive_created, updated_at=naive_created)
    aware_now = datetime(2024, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
    # Same instant interpreted as UTC -> freshness ~ 1.0, no TypeError.
    assert freshness(record, aware_now) == pytest.approx(1.0)


def test_age_days_handles_naive_and_aware_mix():
    naive_created = datetime(2024, 1, 1, 12, 0, 0)
    record = _make(created_at=naive_created, updated_at=naive_created)
    aware_now = datetime(2024, 1, 2, 12, 0, 0, tzinfo=timezone.utc)
    assert age_days(record, aware_now) == pytest.approx(1.0)


def test_freshness_expiry_handles_naive_timestamps():
    naive_created = datetime(2024, 1, 1, 12, 0, 0)
    naive_expires = datetime(2024, 1, 11, 12, 0, 0)
    record = _make(created_at=naive_created, updated_at=naive_created, expires_at=naive_expires)
    aware_mid = datetime(2024, 1, 6, 12, 0, 0, tzinfo=timezone.utc)
    assert freshness(record, aware_mid) == pytest.approx(0.5)
