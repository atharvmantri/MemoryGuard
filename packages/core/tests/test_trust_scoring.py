# SPDX-License-Identifier: Apache-2.0
"""Property-based + unit tests for the deterministic trust-scoring model.

Covers the design's *Correctness Properties* for trust scoring as implemented by
:class:`memoryguard_core.trust.scoring.DeterministicTrustModel` and the
``score_record`` helper:

* **Property 1: Bounded output**          — Validates: Requirements 7.1
* **Property 2: Determinism**             — Validates: Requirements 7.1
* **Property 3: Monotonic in confirmations** — Validates: Requirements 7.2
* **Property 4: Contradiction penalty**   — Validates: Requirements 7.2, 8.3
* **Property 5: Sensitivity monotonicity** — Validates: Requirements 7.2
* **Property 6: Freshness decay**         — Validates: Requirements 7.2
* **Property 7: Weight conservation**     — Validates: Requirements 7.1
* **Property 29: Augmented trust remains bounded, deterministic, monotonic**
  — Validates: Requirements 26.3, 7.1, 7.2
* **Property 30 (partial): User-correction signal monotonicity**
  — Validates: Requirements 26.4

The model combines the five-signal weighted base with a bounded correction
adjustment (see ``scoring.py``); these tests assert boundedness, determinism,
weight conservation at the neutral-correction point, monotonicity in each
documented signal direction, and correction monotonicity.

Hypothesis drives the property tests; equivalent plain-``pytest`` example-based
fallbacks run regardless so the key cases are covered even without Hypothesis.
"""

from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone


from memoryguard_core.models import (
    Scope,
    Sensitivity,
    SourceType,
    new_memory_record,
)
from memoryguard_core.trust.scoring import (
    CORRECTION_WEIGHT,
    NEUTRAL_CORRECTION,
    WEIGHTS,
    DeterministicTrustModel,
    compute_signals,
    score_record,
)
from memoryguard_models.base import ModelInfo, TrustSignals

try:  # Hypothesis is optional; plain fallbacks below always run.
    from hypothesis import given, settings
    from hypothesis import strategies as st

    HAVE_HYPOTHESIS = True
except ImportError:  # pragma: no cover - exercised only without hypothesis
    HAVE_HYPOTHESIS = False


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

MODEL = DeterministicTrustModel()
NOW = datetime(2024, 6, 1, 12, 0, 0, tzinfo=timezone.utc)
TOL = 1e-9


def make_signals(
    *,
    source_authority: float = 0.5,
    freshness: float = 0.5,
    confirmation_score: float = 0.5,
    contradiction_penalty: float = 0.0,
    sensitivity_penalty: float = 0.0,
    correction_signal: float = NEUTRAL_CORRECTION,
) -> TrustSignals:
    """Build a :class:`TrustSignals` with neutral defaults and overrides."""

    return TrustSignals(
        source_authority=source_authority,
        freshness=freshness,
        confirmation_score=confirmation_score,
        contradiction_penalty=contradiction_penalty,
        sensitivity_penalty=sensitivity_penalty,
        correction_signal=correction_signal,
    )


def make_record(**overrides):
    """A valid global-scope record (id/timestamps from the factory)."""

    kwargs = dict(
        content="remember this fact",
        source_type=SourceType.USER,
        source_ref="user://alice",
        scope=Scope.GLOBAL,
        now=NOW,
    )
    kwargs.update(overrides)
    return new_memory_record(**kwargs)


if HAVE_HYPOTHESIS:
    unit = st.floats(min_value=0.0, max_value=1.0, allow_nan=False, allow_infinity=False)

    signals_strategy = st.builds(
        make_signals,
        source_authority=unit,
        freshness=unit,
        confirmation_score=unit,
        contradiction_penalty=unit,
        sensitivity_penalty=unit,
        correction_signal=unit,
    )


# ===========================================================================
# Model identity
# ===========================================================================


def test_model_info_identity():
    info = MODEL.info
    assert isinstance(info, ModelInfo)
    assert info.model_id == "trust/deterministic"
    assert info.task == "trust"
    assert info.version == "1.0.0"


# ===========================================================================
# Property 1: Bounded output  (Requirements 7.1)
# ===========================================================================


if HAVE_HYPOTHESIS:

    @settings(max_examples=300)
    @given(signals=signals_strategy)
    def test_property_1_bounded(signals):
        """**Validates: Requirements 7.1** — score always lies in [0, 1]."""

        s = MODEL.score(make_record(), signals, NOW)
        assert 0.0 <= s <= 1.0


def test_property_1_bounded_examples():
    """**Validates: Requirements 7.1** — boundary signal combinations stay in [0,1]."""

    extremes = [0.0, 0.5, 1.0]
    for sa in extremes:
        for fr in extremes:
            for cs in extremes:
                for cp in extremes:
                    for sp in extremes:
                        for corr in extremes:
                            s = MODEL.score(
                                make_record(),
                                make_signals(
                                    source_authority=sa,
                                    freshness=fr,
                                    confirmation_score=cs,
                                    contradiction_penalty=cp,
                                    sensitivity_penalty=sp,
                                    correction_signal=corr,
                                ),
                                NOW,
                            )
                            assert 0.0 <= s <= 1.0


# ===========================================================================
# Property 2: Determinism  (Requirements 7.1)
# ===========================================================================


if HAVE_HYPOTHESIS:

    @settings(max_examples=200)
    @given(signals=signals_strategy)
    def test_property_2_determinism(signals):
        """**Validates: Requirements 7.1** — identical inputs => identical score."""

        r = make_record()
        assert MODEL.score(r, signals, NOW) == MODEL.score(r, signals, NOW)


def test_property_2_determinism_example():
    """**Validates: Requirements 7.1** — repeated calls are equal."""

    r = make_record()
    sig = make_signals(source_authority=0.7, freshness=0.3, confirmation_score=0.9)
    assert MODEL.score(r, sig, NOW) == MODEL.score(r, sig, NOW)
    # score_record is deterministic too.
    assert score_record(r, NOW) == score_record(r, NOW)


# ===========================================================================
# Property 3: Monotonic in confirmations  (Requirements 7.2)
# ===========================================================================


if HAVE_HYPOTHESIS:

    @settings(max_examples=300)
    @given(
        base=signals_strategy,
        bump=st.floats(min_value=0.0, max_value=1.0, allow_nan=False, allow_infinity=False),
    )
    def test_property_3_monotonic_confirmations(base, bump):
        """**Validates: Requirements 7.2** — raising confirmation_score never lowers score."""

        higher = make_signals(
            source_authority=base.source_authority,
            freshness=base.freshness,
            confirmation_score=min(1.0, base.confirmation_score + bump),
            contradiction_penalty=base.contradiction_penalty,
            sensitivity_penalty=base.sensitivity_penalty,
            correction_signal=base.correction_signal,
        )
        r = make_record()
        assert MODEL.score(r, higher, NOW) >= MODEL.score(r, base, NOW) - TOL


def test_property_3_monotonic_confirmations_record():
    """**Validates: Requirements 7.2** — more confirmations never lowers score_record."""

    low = make_record(confirmations=1)
    high = new_memory_record(
        content=low.content,
        source_type=low.source_type,
        source_ref=low.source_ref,
        scope=low.scope,
        confirmations=10,
        now=NOW,
    )
    assert score_record(high, NOW) >= score_record(low, NOW) - TOL


# ===========================================================================
# Property 4: Contradiction penalty  (Requirements 7.2, 8.3)
# ===========================================================================


if HAVE_HYPOTHESIS:

    @settings(max_examples=300)
    @given(
        base=signals_strategy,
        bump=st.floats(min_value=0.0, max_value=1.0, allow_nan=False, allow_infinity=False),
    )
    def test_property_4_contradiction_penalty(base, bump):
        """**Validates: Requirements 7.2, 8.3** — more contradiction penalty never raises score."""

        worse = make_signals(
            source_authority=base.source_authority,
            freshness=base.freshness,
            confirmation_score=base.confirmation_score,
            contradiction_penalty=min(1.0, base.contradiction_penalty + bump),
            sensitivity_penalty=base.sensitivity_penalty,
            correction_signal=base.correction_signal,
        )
        r = make_record()
        assert MODEL.score(r, worse, NOW) <= MODEL.score(r, base, NOW) + TOL


def test_property_4_contradiction_penalty_record():
    """**Validates: Requirements 7.2, 8.3** — adding an unresolved contradiction never raises score."""

    clean = make_record()
    contra = new_memory_record(
        content=clean.content,
        source_type=clean.source_type,
        source_ref=clean.source_ref,
        scope=clean.scope,
        contradicts=["9f8b7c6d-1234-4abc-8def-1234567890ab"],
        now=NOW,
    )
    assert score_record(contra, NOW) <= score_record(clean, NOW) + TOL


# ===========================================================================
# Property 5: Sensitivity monotonicity  (Requirements 7.2)
# ===========================================================================


def test_property_5_sensitivity_monotonicity_record():
    """**Validates: Requirements 7.2** — public >= internal >= secret >= pii in score."""

    tiers = [
        Sensitivity.PUBLIC,
        Sensitivity.INTERNAL,
        Sensitivity.SECRET,
        Sensitivity.PII,
    ]
    scores = [
        score_record(make_record(sensitivity=t), NOW) for t in tiers
    ]
    for lower, higher in zip(scores, scores[1:]):
        # Increasing tier index => higher sensitivity => not greater score.
        assert higher <= lower + TOL
    # Strict end-to-end check from the property statement.
    assert score_record(make_record(sensitivity=Sensitivity.PII), NOW) <= (
        score_record(make_record(sensitivity=Sensitivity.PUBLIC), NOW) + TOL
    )


if HAVE_HYPOTHESIS:

    @settings(max_examples=300)
    @given(
        base=signals_strategy,
        bump=st.floats(min_value=0.0, max_value=1.0, allow_nan=False, allow_infinity=False),
    )
    def test_property_5_sensitivity_signal(base, bump):
        """**Validates: Requirements 7.2** — larger sensitivity penalty never raises score."""

        worse = make_signals(
            source_authority=base.source_authority,
            freshness=base.freshness,
            confirmation_score=base.confirmation_score,
            contradiction_penalty=base.contradiction_penalty,
            sensitivity_penalty=min(1.0, base.sensitivity_penalty + bump),
            correction_signal=base.correction_signal,
        )
        r = make_record()
        assert MODEL.score(r, worse, NOW) <= MODEL.score(r, base, NOW) + TOL


# ===========================================================================
# Property 6: Freshness decay  (Requirements 7.2)
# ===========================================================================


if HAVE_HYPOTHESIS:

    @settings(max_examples=200)
    @given(
        d1=st.floats(min_value=0.0, max_value=400.0, allow_nan=False, allow_infinity=False),
        extra=st.floats(min_value=0.0, max_value=400.0, allow_nan=False, allow_infinity=False),
    )
    def test_property_6_freshness_decay(d1, extra):
        """**Validates: Requirements 7.2** — an older read is never more trusted."""

        r = make_record(confirmations=3)
        earlier = NOW + timedelta(days=d1)
        later = earlier + timedelta(days=extra)
        assert score_record(r, later) <= score_record(r, earlier) + TOL


def test_property_6_freshness_decay_example():
    """**Validates: Requirements 7.2** — score does not increase as the record ages."""

    r = make_record(confirmations=3)
    t0 = NOW
    t1 = NOW + timedelta(days=30)
    t2 = NOW + timedelta(days=365)
    s0, s1, s2 = score_record(r, t0), score_record(r, t1), score_record(r, t2)
    assert s1 <= s0 + TOL
    assert s2 <= s1 + TOL


# ===========================================================================
# Property 7: Weight conservation  (Requirements 7.1)
# ===========================================================================


def test_property_7_weight_conservation():
    """**Validates: Requirements 7.1** — all-max signals + zero penalties + neutral correction => 1.0."""

    all_max = make_signals(
        source_authority=1.0,
        freshness=1.0,
        confirmation_score=1.0,
        contradiction_penalty=0.0,
        sensitivity_penalty=0.0,
        correction_signal=NEUTRAL_CORRECTION,
    )
    assert math.isclose(MODEL.score(make_record(), all_max, NOW), 1.0, abs_tol=TOL)


def test_weights_sum_to_one():
    """Sanity: the five primary weights sum to exactly 1.0 (boundedness guarantee)."""

    assert math.isclose(sum(WEIGHTS.values()), 1.0, abs_tol=TOL)


def test_property_7_holds_for_affirm_at_max():
    """At the all-max base, even a full affirm keeps the score at exactly 1.0 (bounded)."""

    affirmed = make_signals(
        source_authority=1.0,
        freshness=1.0,
        confirmation_score=1.0,
        contradiction_penalty=0.0,
        sensitivity_penalty=0.0,
        correction_signal=1.0,
    )
    assert math.isclose(MODEL.score(make_record(), affirmed, NOW), 1.0, abs_tol=TOL)


# ===========================================================================
# Property 29: Augmented trust remains bounded + deterministic
# (Requirements 26.3, 7.1, 7.2)
# ===========================================================================


if HAVE_HYPOTHESIS:

    @settings(max_examples=300)
    @given(signals=signals_strategy)
    def test_property_29_bounded_deterministic(signals):
        """**Validates: Requirements 26.3, 7.1** — augmented score bounded + deterministic."""

        r = make_record()
        s = MODEL.score(r, signals, NOW)
        assert 0.0 <= s <= 1.0
        assert MODEL.score(r, signals, NOW) == s


def test_property_29_examples():
    """**Validates: Requirements 26.3, 7.1** — corrections keep the score in [0,1], deterministic."""

    r = make_record()
    for corr in (0.0, 0.25, 0.5, 0.75, 1.0):
        sig = make_signals(correction_signal=corr)
        s = MODEL.score(r, sig, NOW)
        assert 0.0 <= s <= 1.0
        assert MODEL.score(r, sig, NOW) == s


def _bump_signal(base: TrustSignals, field: str, value: float) -> TrustSignals:
    """Return a copy of ``base`` with a single signal ``field`` set to ``value``."""

    fields = dict(
        source_authority=base.source_authority,
        freshness=base.freshness,
        confirmation_score=base.confirmation_score,
        contradiction_penalty=base.contradiction_penalty,
        sensitivity_penalty=base.sensitivity_penalty,
        correction_signal=base.correction_signal,
    )
    fields[field] = value
    return make_signals(**fields)


if HAVE_HYPOTHESIS:

    @settings(max_examples=400)
    @given(
        base=signals_strategy,
        bump=st.floats(min_value=0.0, max_value=1.0, allow_nan=False, allow_infinity=False),
    )
    def test_property_29_monotonic_in_documented_directions(base, bump):
        """**Validates: Requirements 26.3, 7.2** — with the correction signal incorporated,
        the augmented score preserves monotonicity in every documented signal direction.

        For an arbitrary base (including an arbitrary, non-neutral correction):
        raising a positive signal (source_authority / freshness / confirmation_score /
        correction_signal) never lowers the score, and raising a penalty signal
        (contradiction_penalty / sensitivity_penalty) never raises it.
        """

        r = make_record()
        s_base = MODEL.score(r, base, NOW)

        # Positive signals: increasing never lowers the score.
        for field, current in (
            ("source_authority", base.source_authority),
            ("freshness", base.freshness),
            ("confirmation_score", base.confirmation_score),
            ("correction_signal", base.correction_signal),
        ):
            higher = _bump_signal(base, field, min(1.0, current + bump))
            assert MODEL.score(r, higher, NOW) >= s_base - TOL

        # Penalty signals: increasing never raises the score.
        for field, current in (
            ("contradiction_penalty", base.contradiction_penalty),
            ("sensitivity_penalty", base.sensitivity_penalty),
        ):
            worse = _bump_signal(base, field, min(1.0, current + bump))
            assert MODEL.score(r, worse, NOW) <= s_base + TOL


def test_property_29_monotonic_in_documented_directions_example():
    """**Validates: Requirements 26.3, 7.2** — monotonicity holds while a correction is active."""

    r = make_record()
    # A base with an active (affirming) correction incorporated.
    base = make_signals(
        source_authority=0.5,
        freshness=0.5,
        confirmation_score=0.5,
        contradiction_penalty=0.2,
        sensitivity_penalty=0.2,
        correction_signal=0.7,
    )
    s_base = MODEL.score(r, base, NOW)
    # Positive signals up => not lower.
    assert MODEL.score(r, _bump_signal(base, "source_authority", 0.9), NOW) >= s_base - TOL
    assert MODEL.score(r, _bump_signal(base, "freshness", 0.9), NOW) >= s_base - TOL
    assert MODEL.score(r, _bump_signal(base, "confirmation_score", 0.9), NOW) >= s_base - TOL
    assert MODEL.score(r, _bump_signal(base, "correction_signal", 1.0), NOW) >= s_base - TOL
    # Penalties up => not higher.
    assert MODEL.score(r, _bump_signal(base, "contradiction_penalty", 0.9), NOW) <= s_base + TOL
    assert MODEL.score(r, _bump_signal(base, "sensitivity_penalty", 0.9), NOW) <= s_base + TOL


# ===========================================================================
# Property 30 (partial): User-correction signal monotonicity (Requirements 26.4)
# ===========================================================================


def test_property_30_affirm_does_not_lower():
    """**Validates: Requirements 26.4** — affirm (correction > 0.5) never lowers trust."""

    neutral = make_signals(correction_signal=NEUTRAL_CORRECTION)
    affirm = make_signals(correction_signal=1.0)
    r = make_record()
    assert MODEL.score(r, affirm, NOW) >= MODEL.score(r, neutral, NOW) - TOL


def test_property_30_supersede_does_not_raise():
    """**Validates: Requirements 26.4** — supersede (correction < 0.5) never raises trust."""

    neutral = make_signals(correction_signal=NEUTRAL_CORRECTION)
    supersede = make_signals(correction_signal=0.0)
    r = make_record()
    assert MODEL.score(r, supersede, NOW) <= MODEL.score(r, neutral, NOW) + TOL


if HAVE_HYPOTHESIS:

    @settings(max_examples=300)
    @given(
        base=signals_strategy,
        c_lo=st.floats(min_value=0.0, max_value=1.0, allow_nan=False, allow_infinity=False),
        delta=st.floats(min_value=0.0, max_value=1.0, allow_nan=False, allow_infinity=False),
    )
    def test_property_30_monotonic_in_correction(base, c_lo, delta):
        """**Validates: Requirements 26.4** — score is non-decreasing in correction_signal."""

        c_hi = min(1.0, c_lo + delta)
        lo = make_signals(
            source_authority=base.source_authority,
            freshness=base.freshness,
            confirmation_score=base.confirmation_score,
            contradiction_penalty=base.contradiction_penalty,
            sensitivity_penalty=base.sensitivity_penalty,
            correction_signal=c_lo,
        )
        hi = make_signals(
            source_authority=base.source_authority,
            freshness=base.freshness,
            confirmation_score=base.confirmation_score,
            contradiction_penalty=base.contradiction_penalty,
            sensitivity_penalty=base.sensitivity_penalty,
            correction_signal=c_hi,
        )
        r = make_record()
        assert MODEL.score(r, hi, NOW) >= MODEL.score(r, lo, NOW) - TOL


def test_property_30_record_level_corrections():
    """**Validates: Requirements 26.4** — affirm/supersede via metadata move trust correctly."""

    neutral = score_record(make_record(), NOW)
    affirmed = score_record(make_record(metadata={"correction_kind": "affirm", "corrections": 3}), NOW)
    superseded = score_record(
        make_record(metadata={"correction_kind": "supersede", "corrections": 3}), NOW
    )
    assert affirmed >= neutral - TOL
    assert superseded <= neutral + TOL


def _five_term_sum(signals: TrustSignals) -> float:
    """The pure five-term weighted base (no correction adjustment), per the design formula."""

    return (
        signals.source_authority * WEIGHTS["source_authority"]
        + signals.freshness * WEIGHTS["freshness"]
        + signals.confirmation_score * WEIGHTS["confirmation_score"]
        + (1.0 - signals.contradiction_penalty) * WEIGHTS["contradiction_penalty"]
        + (1.0 - signals.sensitivity_penalty) * WEIGHTS["sensitivity_penalty"]
    )


if HAVE_HYPOTHESIS:

    @settings(max_examples=300)
    @given(base=signals_strategy)
    def test_property_30_neutral_correction_is_noop(base):
        """**Validates: Requirements 26.4, 7.1** — a neutral correction (0.5) is a no-op.

        With ``correction_signal == NEUTRAL_CORRECTION`` the augmented score equals the
        pure five-term weighted sum (clamped), so a memory with no recorded correction
        scores exactly as the deterministic five-signal baseline would.
        """

        neutral = _bump_signal(base, "correction_signal", NEUTRAL_CORRECTION)
        expected = min(1.0, max(0.0, _five_term_sum(neutral)))
        assert math.isclose(MODEL.score(make_record(), neutral, NOW), expected, abs_tol=TOL)


def test_property_30_neutral_correction_is_noop_example():
    """**Validates: Requirements 26.4, 7.1** — neutral correction preserves the five-term result."""

    sig = make_signals(
        source_authority=0.8,
        freshness=0.6,
        confirmation_score=0.4,
        contradiction_penalty=0.2,
        sensitivity_penalty=0.1,
        correction_signal=NEUTRAL_CORRECTION,
    )
    expected = _five_term_sum(sig)
    assert math.isclose(MODEL.score(make_record(), sig, NOW), expected, abs_tol=TOL)


def test_property_30_neutral_correction_matches_no_metadata_record():
    """**Validates: Requirements 26.4** — a record with no correction metadata is a five-term no-op.

    ``compute_signals`` yields the neutral correction (0.5) when no correction is
    recorded, so ``score_record`` equals the pure five-term sum of those signals.
    """

    record = make_record(confirmations=2)
    signals = compute_signals(record, NOW)
    assert math.isclose(signals.correction_signal, NEUTRAL_CORRECTION, abs_tol=TOL)
    expected = min(1.0, max(0.0, _five_term_sum(signals)))
    assert math.isclose(score_record(record, NOW), expected, abs_tol=TOL)


# ===========================================================================
# compute_signals sanity
# ===========================================================================


def test_compute_signals_returns_bounded_trustsignals():
    sigs = compute_signals(make_record(confirmations=2), NOW)
    assert isinstance(sigs, TrustSignals)
    for value in (
        sigs.source_authority,
        sigs.freshness,
        sigs.confirmation_score,
        sigs.contradiction_penalty,
        sigs.sensitivity_penalty,
        sigs.correction_signal,
    ):
        assert 0.0 <= value <= 1.0
    # No correction metadata => neutral correction signal.
    assert math.isclose(sigs.correction_signal, NEUTRAL_CORRECTION, abs_tol=TOL)


def test_correction_weight_in_unit_interval():
    """The correction weight must be in [0, 1] so boundedness holds structurally."""

    assert 0.0 <= CORRECTION_WEIGHT <= 1.0
