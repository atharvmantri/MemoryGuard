# SPDX-License-Identifier: Apache-2.0
"""Property test for model-backed contradiction invariants (Property 26).

This suite verifies the documented contradiction invariants **against the
``ContradictionModel`` / ``ContradictionClassifier`` interface** rather than any
single concrete subclass. The property is parametrized over every available
``ContradictionModel`` implementation so that:

* the OSS default (``RuleContradictionModel``) is always covered, and
* a future learned classifier (``LearnedContradictionModel`` behind the
  ``learned_contradiction`` flag, design *MM.2*) is automatically pulled in via
  :func:`_discover_models` the moment it exists -- no test edit required.

A minimal, genuinely interface-conformant ``_ReferenceContradictionModel`` is
also included so the parametrization exercises the *interface contract* across
more than one implementation today, proving the assertions key on the
``ContradictionModel`` API surface and not on ``RuleContradictionModel``
internals.

Documented invariants (design *Property 26: Model-backed contradiction preserves
invariants*) that MUST hold for **any** ``ContradictionModel``:

* **Symmetry** -- ``detect(a, b).is_contradiction == detect(b, a).is_contradiction``
* **Irreflexive** -- ``detect(a, a).is_contradiction is False``
* **Scope isolation** -- disjoint ``scope_ref`` (or different ``scope``) ->
  ``is_contradiction is False``
* **Bounded confidence** -- ``0.0 <= confidence <= 1.0``

**Validates: Requirements 23.2, 8.1**

Per repo convention these property tests use Hypothesis when available and fall
back to a deterministic example sweep otherwise, so the suite still runs in
minimal environments. All checks run on-device with no external LLM API.
"""

from __future__ import annotations

from typing import Optional

import pytest

from memoryguard_core.models import (
    SCOPE_REF_REQUIRED,
    MemoryRecord,
    Scope,
    SourceType,
    new_memory_record,
)
from memoryguard_core.trust.contradiction import RuleContradictionModel
from memoryguard_models.base import (
    ContradictionModel,
    ContradictionResult,
    ModelInfo,
)

# Optional Hypothesis support (graceful fallback to example-based testing).
try:  # pragma: no cover - import guard
    from hypothesis import HealthCheck, given, settings
    from hypothesis import strategies as st

    _HAS_HYPOTHESIS = True
except Exception:  # pragma: no cover - Hypothesis not installed
    _HAS_HYPOTHESIS = False


# ---------------------------------------------------------------------------
# A second, minimal interface-conformant model
# ---------------------------------------------------------------------------


class _ReferenceContradictionModel(ContradictionModel):
    """A minimal, genuinely invariant-preserving ``ContradictionModel``.

    This is *not* a copy of ``RuleContradictionModel``; it is an independent,
    deliberately simple implementation whose only job is to honor the interface
    contract. Including it alongside the rule model proves Property 26 is checked
    against the ``ContradictionModel`` interface, not a single subclass. It also
    models the shape a learned classifier must satisfy when it is injected behind
    the same interface (design *MM.2*).

    Decision logic (symmetric / order-independent by construction):

    * irreflexive: a record never contradicts itself (by ``memory_id``);
    * scope isolation: different ``scope`` or disjoint ``scope_ref`` -> never a
      contradiction;
    * otherwise: flag a contradiction when the two contents share at least one
      token yet differ as token *sets* (a symmetric, set-based signal), with a
      confidence derived from a clamped, symmetric token-overlap ratio.
    """

    @property
    def info(self) -> ModelInfo:
        return ModelInfo(
            model_id="contradiction/reference-stub",
            task="contradiction",
            version="0.0.0",
        )

    @staticmethod
    def _tokens(text: str) -> set[str]:
        return {tok for tok in text.lower().split() if tok}

    @staticmethod
    def _refs_overlap(a: MemoryRecord, b: MemoryRecord) -> bool:
        ra, rb = a.scope_ref, b.scope_ref
        if ra is None and rb is None:
            return True
        if ra is None or rb is None:
            return False
        return str(ra).strip() == str(rb).strip()

    def detect(
        self,
        candidate: MemoryRecord,
        existing: MemoryRecord,
    ) -> ContradictionResult:
        # Irreflexive.
        if candidate.memory_id == existing.memory_id:
            return ContradictionResult(False, "same memory (irreflexive)", 0.0)

        # Scope isolation: must share scope AND have overlapping scope_ref.
        if candidate.scope != existing.scope or not self._refs_overlap(
            candidate, existing
        ):
            return ContradictionResult(False, "not comparable (scope isolation)", 0.0)

        a = self._tokens(candidate.content)
        b = self._tokens(existing.content)
        union = a | b
        # Symmetric: overlap and set-equality do not depend on argument order.
        overlap = len(a & b) / len(union) if union else 0.0
        is_contra = bool((a & b) and a != b)
        confidence = max(0.0, min(1.0, overlap))
        reason = "shared subject with differing content" if is_contra else "no conflict"
        return ContradictionResult(is_contra, reason, confidence)


# ---------------------------------------------------------------------------
# Models under test (interface-level parametrization)
# ---------------------------------------------------------------------------


def _discover_models() -> list[ContradictionModel]:
    """Return every available ``ContradictionModel`` implementation to test.

    Always includes the OSS default ``RuleContradictionModel`` and the in-repo
    reference stub. A learned classifier is appended automatically if/when it
    becomes importable (design *MM.2* -- ``LearnedContradictionModel`` behind the
    ``learned_contradiction`` flag), so this property keeps guarding the
    interface contract as new implementations land, with no edit here.
    """

    models: list[ContradictionModel] = [
        RuleContradictionModel(),
        _ReferenceContradictionModel(),
    ]

    try:  # pragma: no cover - exercised once the learned model exists
        from memoryguard_commercial.models.contradiction import (  # type: ignore
            LearnedContradictionModel,
        )

        models.append(LearnedContradictionModel())
    except Exception:  # pragma: no cover - learned model not yet implemented
        pass

    return models


MODELS_UNDER_TEST: list[ContradictionModel] = _discover_models()
_MODEL_IDS = [m.info.model_id for m in MODELS_UNDER_TEST]


# ---------------------------------------------------------------------------
# Record generation helpers
# ---------------------------------------------------------------------------

# A pool of short phrases mixing agreeing, conflicting, and unrelated content so
# both contradictions and non-contradictions are exercised across models.
_PHRASES = [
    "the project uses Flask as its web framework",
    "the project uses FastAPI as its web framework",
    "the project uses Django as its web framework",
    "the database runs PostgreSQL 15 in production",
    "the database runs MySQL 8 in production",
    "the service caches responses for speed",
    "the service does not cache responses for speed",
    "the api is public and documented",
    "the api is private and undocumented",
    "deployments happen every friday afternoon",
    "lunch is served at noon in the cafeteria",
    "the timeout is set to 0.5 seconds",
    "the timeout is set to 0.7 seconds",
    "logging is enabled for all requests",
    "logging is disabled for all requests",
]

# Includes ``None`` and several distinct refs so disjoint-ref pairs (which must
# never be contradictions) are generated frequently.
_SCOPE_REFS = ["proj-1", "proj-2", "my-app", None]
_SCOPES = [Scope.PROJECT, Scope.GLOBAL, Scope.REPO]


def _valid_scope_ref(scope: Scope, scope_ref: Optional[str]) -> Optional[str]:
    """Return a scope_ref usable for ``scope`` (required for some scopes)."""

    if scope in SCOPE_REF_REQUIRED:
        return scope_ref if scope_ref is not None else "proj-1"
    return scope_ref


def _record(
    content: str,
    *,
    scope: Scope = Scope.PROJECT,
    scope_ref: Optional[str] = "proj-1",
    source_type: SourceType = SourceType.USER,
) -> MemoryRecord:
    """Build a valid, embedding-free :class:`MemoryRecord` for tests."""

    return new_memory_record(
        content=content,
        source_type=source_type,
        source_ref="user://tester",
        scope=scope,
        scope_ref=scope_ref,
    )


def _comparable(a: MemoryRecord, b: MemoryRecord) -> bool:
    """Whether two records are comparable: same scope AND overlapping scope_ref."""

    same_scope = a.scope == b.scope
    refs_overlap = (a.scope_ref is None and b.scope_ref is None) or (
        a.scope_ref is not None
        and b.scope_ref is not None
        and str(a.scope_ref).strip() == str(b.scope_ref).strip()
    )
    return same_scope and refs_overlap


def _assert_property_26(
    model: ContradictionModel,
    a: MemoryRecord,
    b: MemoryRecord,
) -> None:
    """Assert Property 26 invariants for ``model`` on a single pair.

    Verified purely through the ``ContradictionModel.detect`` interface.
    """

    r_ab = model.detect(a, b)
    r_ba = model.detect(b, a)

    assert isinstance(r_ab, ContradictionResult)
    assert isinstance(r_ba, ContradictionResult)

    # Symmetry.
    assert r_ab.is_contradiction == r_ba.is_contradiction

    # Bounded confidence (both orderings).
    assert 0.0 <= r_ab.confidence <= 1.0
    assert 0.0 <= r_ba.confidence <= 1.0

    # Irreflexive.
    assert model.detect(a, a).is_contradiction is False
    assert model.detect(b, b).is_contradiction is False

    # Scope isolation: disjoint scope_ref (or different scope) -> not a contradiction.
    if not _comparable(a, b):
        assert r_ab.is_contradiction is False
        assert r_ba.is_contradiction is False


# ---------------------------------------------------------------------------
# Interface sanity: every model under test honors the contract
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("model", MODELS_UNDER_TEST, ids=_MODEL_IDS)
def test_model_under_test_implements_interface(model: ContradictionModel) -> None:
    """Each model under test is a ``ContradictionModel`` with a contradiction task."""

    assert isinstance(model, ContradictionModel)
    assert model.info.task == "contradiction"


def test_oss_default_is_under_test() -> None:
    """The OSS default ``RuleContradictionModel`` is always covered."""

    assert any(isinstance(m, RuleContradictionModel) for m in MODELS_UNDER_TEST)


# ---------------------------------------------------------------------------
# Property 26 (Hypothesis) with example-sweep fallback
# ---------------------------------------------------------------------------


if _HAS_HYPOTHESIS:

    @st.composite
    def _records(draw: "st.DrawFn") -> MemoryRecord:
        content = draw(st.sampled_from(_PHRASES))
        scope = draw(st.sampled_from(_SCOPES))
        scope_ref = _valid_scope_ref(scope, draw(st.sampled_from(_SCOPE_REFS)))
        return _record(content, scope=scope, scope_ref=scope_ref)

    @pytest.mark.parametrize("model", MODELS_UNDER_TEST, ids=_MODEL_IDS)
    @settings(max_examples=200, suppress_health_check=[HealthCheck.too_slow])
    @given(a=_records(), b=_records())
    def test_property_26_model_backed_contradiction_preserves_invariants(
        model: ContradictionModel,
        a: MemoryRecord,
        b: MemoryRecord,
    ) -> None:
        """**Property 26** — invariants hold for any ``ContradictionModel``.

        **Validates: Requirements 23.2, 8.1**
        """

        _assert_property_26(model, a, b)

    @pytest.mark.parametrize("model", MODELS_UNDER_TEST, ids=_MODEL_IDS)
    @settings(max_examples=100, suppress_health_check=[HealthCheck.too_slow])
    @given(a=_records())
    def test_property_26_irreflexive(
        model: ContradictionModel, a: MemoryRecord
    ) -> None:
        """**Property 26 (irreflexive)** — a record never contradicts itself.

        **Validates: Requirements 23.2, 8.1**
        """

        assert model.detect(a, a).is_contradiction is False

else:  # pragma: no cover - exercised only when Hypothesis is unavailable

    @pytest.mark.parametrize("model", MODELS_UNDER_TEST, ids=_MODEL_IDS)
    def test_property_26_model_backed_contradiction_preserves_invariants(
        model: ContradictionModel,
    ) -> None:
        """Example-sweep fallback covering Property 26 for each model.

        **Validates: Requirements 23.2, 8.1**
        """

        records = [
            _record(content, scope=scope, scope_ref=_valid_scope_ref(scope, ref))
            for content in _PHRASES
            for scope in _SCOPES
            for ref in _SCOPE_REFS
        ]
        # Sweep a bounded, representative cross-product.
        for a in records[::3]:
            for b in records[::5]:
                _assert_property_26(model, a, b)


# ---------------------------------------------------------------------------
# Explicit interface-level examples (Property 26 corner cases)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("model", MODELS_UNDER_TEST, ids=_MODEL_IDS)
def test_property_26_disjoint_scope_ref_never_contradiction(
    model: ContradictionModel,
) -> None:
    """Disjoint ``scope_ref`` is never a contradiction, for any model (symmetric)."""

    a = _record("the project uses Flask as its web framework", scope_ref="app-a")
    b = _record("the project uses FastAPI as its web framework", scope_ref="app-b")
    assert model.detect(a, b).is_contradiction is False
    assert model.detect(b, a).is_contradiction is False


@pytest.mark.parametrize("model", MODELS_UNDER_TEST, ids=_MODEL_IDS)
def test_property_26_confidence_bounded_examples(
    model: ContradictionModel,
) -> None:
    """Confidence stays within ``[0, 1]`` across representative pairs, for any model."""

    pairs = [
        (
            "the project uses Flask as its web framework",
            "the project uses FastAPI as its web framework",
        ),
        ("the project uses Flask", "lunch is served at noon"),
        ("the project uses Flask", "the project uses Flask"),
    ]
    for ca, cb in pairs:
        a = _record(ca, scope_ref="my-app")
        b = _record(cb, scope_ref="my-app")
        assert 0.0 <= model.detect(a, b).confidence <= 1.0
