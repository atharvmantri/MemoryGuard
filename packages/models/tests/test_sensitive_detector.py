# SPDX-License-Identifier: Apache-2.0
"""Unit + property tests for the OSS ``BasicSensitiveDataDetector`` (offline).

These tests run with no network access and no ML dependencies. They exercise the
deterministic regex/rules sensitive-data detector against the labeled eval set
in ``packages/evals/.../sensitive_fixtures.jsonl``.

Covers:

* **Property 28: Sensitive-data detector flags secrets + sets tier** -- every
  known-secret fixture (label 1) is flagged (``has_sensitive is True``) and
  assigned an elevated ``Sensitivity.SECRET`` / ``Sensitivity.PII`` tier
  matching its fixture label; benign fixtures (label 0) are not flagged; and
  overall precision and recall on the set meet the evals thresholds. This is
  checked both directly and through the OSS ``BasicEvaluationHarness``.
* **Property 34: Detector outputs are bounded and well-formed** --
  ``has_sensitive`` is a ``bool``, ``detected`` is a list of strings,
  ``suggested_sensitivity`` is a valid ``Sensitivity``, and ``reason`` is
  non-empty. This holds for the bundled fixtures and across arbitrary input
  content (adversarial included) via Hypothesis, so the detector can never emit
  a malformed verdict that would corrupt sensitivity tagging.
* The ``IngestionInspector`` contract: ``inspect(record)`` elevates the
  record's ``sensitivity`` to the suggested tier without downgrading, and
  annotates ``metadata['sensitive']``.

Validates: Requirements 25.1, 25.2, 25.3, 25.4, 25.5, 9.1.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from memoryguard_core.models import (
    Scope,
    Sensitivity,
    SourceType,
    new_memory_record,
)
from memoryguard_models import BasicSensitiveDataDetector, ModelInfo
from memoryguard_models.base import SensitiveDataDetector, SensitiveResult

# The evals harness + fixtures are part of the OSS workspace; skip cleanly if a
# minimal environment hasn't installed them.
evals = pytest.importorskip("memoryguard_evals")
from memoryguard_evals import (  # noqa: E402
    SENSITIVE_PRECISION_MIN,
    SENSITIVE_RECALL_MIN,
    SENSITIVE_TIER_ACCURACY_MIN,
    BasicEvaluationHarness,
    load_sensitive_fixtures,
)

# Hypothesis powers the arbitrary-input bounds check for Property 34; skip
# cleanly when a minimal environment hasn't installed it.
hypothesis = pytest.importorskip("hypothesis")
from hypothesis import given, settings  # noqa: E402
from hypothesis import strategies as st  # noqa: E402

_VALID_TIERS = set(Sensitivity)

# ---------------------------------------------------------------------------
# Fixture loading
# ---------------------------------------------------------------------------

_REPO_ROOT = Path(__file__).resolve().parents[3]
_FIXTURES = (
    _REPO_ROOT
    / "packages"
    / "evals"
    / "memoryguard_evals"
    / "fixtures"
    / "sensitive_fixtures.jsonl"
)

_ELEVATED = (Sensitivity.SECRET, Sensitivity.PII)


def _load_fixtures() -> list[dict]:
    """Parse the JSONL eval set, skipping comment (``#``) and blank lines."""

    rows: list[dict] = []
    for line in _FIXTURES.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        rows.append(json.loads(stripped))
    return rows


FIXTURES = _load_fixtures()
SECRET_FIXTURES = [fx for fx in FIXTURES if fx["label"] == 1]
BENIGN_FIXTURES = [fx for fx in FIXTURES if fx["label"] == 0]


def _record(content: str, sensitivity: Sensitivity = Sensitivity.PUBLIC):
    """Build a valid PUBLIC-scoped record carrying ``content``."""

    return new_memory_record(
        content=content,
        source_type=SourceType.USER,
        source_ref="user://tester",
        scope=Scope.GLOBAL,
        sensitivity=sensitivity,
    )


@pytest.fixture()
def detector() -> BasicSensitiveDataDetector:
    return BasicSensitiveDataDetector()


# ---------------------------------------------------------------------------
# Sanity: fixtures present and well-formed
# ---------------------------------------------------------------------------


def test_fixture_set_loaded() -> None:
    assert FIXTURES, f"no fixtures loaded from {_FIXTURES}"
    assert SECRET_FIXTURES, "expected at least one label=1 fixture"
    assert BENIGN_FIXTURES, "expected at least one label=0 fixture"


def test_detector_identity(detector: BasicSensitiveDataDetector) -> None:
    info = detector.info
    assert isinstance(info, ModelInfo)
    assert info.task == "sensitive"
    assert info.model_id == "sensitive/rules"
    assert info.version == "1.0.0"
    assert isinstance(detector, SensitiveDataDetector)


# ---------------------------------------------------------------------------
# Property 28: secrets flagged + elevated tier (every known-secret fixture)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("fx", SECRET_FIXTURES, ids=[fx["id"] for fx in SECRET_FIXTURES])
def test_known_secret_fixture_flagged_and_tiered(
    detector: BasicSensitiveDataDetector, fx: dict
) -> None:
    """Validates: Requirements 25.1, 25.2 (Property 28)."""

    res = detector.inspect_content(_record(fx["content"]))
    assert res.has_sensitive is True, f"{fx['id']} not flagged: {fx['content']!r}"
    assert res.suggested_sensitivity in _ELEVATED
    # The suggested tier matches the fixture's labeled tier.
    expected = Sensitivity(fx["suggested_sensitivity"])
    assert res.suggested_sensitivity == expected, (
        f"{fx['id']}: expected {expected}, got {res.suggested_sensitivity}; "
        f"detected={res.detected}"
    )
    assert res.detected, "detected list must be non-empty when has_sensitive"
    assert res.reason


@pytest.mark.parametrize("fx", BENIGN_FIXTURES, ids=[fx["id"] for fx in BENIGN_FIXTURES])
def test_benign_fixture_not_flagged(
    detector: BasicSensitiveDataDetector, fx: dict
) -> None:
    """Validates: Requirement 25.1 (no false positives on benign text)."""

    res = detector.inspect_content(_record(fx["content"]))
    assert res.has_sensitive is False, f"{fx['id']} falsely flagged: {res.detected}"
    assert res.detected == []


def test_precision_recall_above_threshold(
    detector: BasicSensitiveDataDetector,
) -> None:
    """Overall precision and recall on the eval set are >= 0.8.

    Validates: Requirements 25.1, 25.2 (Property 28).
    """

    tp = fp = fn = tn = 0
    for fx in FIXTURES:
        flagged = detector.inspect_content(_record(fx["content"])).has_sensitive
        is_secret = fx["label"] == 1
        if flagged and is_secret:
            tp += 1
        elif flagged and not is_secret:
            fp += 1
        elif not flagged and is_secret:
            fn += 1
        else:
            tn += 1

    precision = tp / (tp + fp) if (tp + fp) else 1.0
    recall = tp / (tp + fn) if (tp + fn) else 1.0
    assert precision >= 0.8, f"precision {precision:.3f} < 0.8 (fp={fp})"
    assert recall >= 0.8, f"recall {recall:.3f} < 0.8 (fn={fn})"


# ---------------------------------------------------------------------------
# Targeted unit tests per category
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "content, category, tier",
    [
        ("key AKIAIOSFODNN7EXAMPLE deploy", "aws_access_key_id", Sensitivity.SECRET),
        ("aws_secret_access_key = abc/def123", "aws_secret_access_key", Sensitivity.SECRET),
        ("DB_PASSWORD=hunter2 in env", "password", Sensitivity.SECRET),
        ("token ghp_EXAMPLEEXAMPLEEXAMPLEEXAMPLEEX1234", "github_token", Sensitivity.SECRET),
        (
            "-----BEGIN RSA PRIVATE KEY----- x -----END RSA PRIVATE KEY-----",
            "private_key",
            Sensitivity.SECRET,
        ),
        (
            "https://hooks.slack.com/services/T0/B0/XYZ",
            "slack_webhook",
            Sensitivity.SECRET,
        ),
        ("Authorization: Bearer eyJhbG.payload.sig", "bearer_token", Sensitivity.SECRET),
        ("reach me at a.b@example.com", "email", Sensitivity.PII),
        ("ssn 123-45-6789 on file", "ssn", Sensitivity.PII),
        ("card 4111 1111 1111 1111 exp", "credit_card", Sensitivity.PII),
    ],
)
def test_each_category_detected(
    detector: BasicSensitiveDataDetector,
    content: str,
    category: str,
    tier: Sensitivity,
) -> None:
    """Validates: Requirement 25.1 (each rule category fires)."""

    res = detector.inspect_content(_record(content))
    assert res.has_sensitive is True
    assert category in res.detected, f"{category} missing from {res.detected}"
    assert res.suggested_sensitivity == tier


def test_secret_dominates_pii_when_both_present(
    detector: BasicSensitiveDataDetector,
) -> None:
    res = detector.inspect_content(
        _record("email a@b.com and AKIAIOSFODNN7EXAMPLE")
    )
    assert res.has_sensitive is True
    assert "email" in res.detected and "aws_access_key_id" in res.detected
    assert res.suggested_sensitivity == Sensitivity.SECRET


# ---------------------------------------------------------------------------
# Internal company information (treated as secret-class) -- Requirement 25.1
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "content",
    [
        "This document is CONFIDENTIAL and must not be shared",
        "Company Confidential: Q3 roadmap details",
        "For internal use only - do not circulate",
        "Marked INTERNAL ONLY by legal",
        "Proprietary and confidential design notes",
        "This contains a trade secret formula",
        "Do not distribute this deck outside the company",
        "See the runbook at wiki.acme.internal/oncall",
        "Deploy target is buildhost.corp for releases",
    ],
)
def test_internal_company_info_detected_as_secret(
    detector: BasicSensitiveDataDetector, content: str
) -> None:
    """Validates: Requirements 25.1, 25.2 (internal company info -> secret)."""

    res = detector.inspect_content(_record(content))
    assert res.has_sensitive is True, f"not flagged: {content!r}"
    assert "internal_info" in res.detected
    assert res.suggested_sensitivity == Sensitivity.SECRET


@pytest.mark.parametrize(
    "content",
    [
        "We use SQLite locally and PostgreSQL in the cloud deployment",
        "Documentation explains the open-core licensing boundary",
        "Reranking happens in stage two of the retrieval pipeline",
    ],
)
def test_benign_text_not_flagged_as_internal_info(
    detector: BasicSensitiveDataDetector, content: str
) -> None:
    """Internal-info rules must not trip on ordinary technical prose."""

    res = detector.inspect_content(_record(content))
    assert "internal_info" not in res.detected


def test_inspect_elevates_internal_info_to_secret(
    detector: BasicSensitiveDataDetector,
) -> None:
    """Validates: Requirement 25.4 (elevate internal info at ingestion)."""

    record = _record(
        "CONFIDENTIAL: see wiki.acme.internal", sensitivity=Sensitivity.INTERNAL
    )
    out = detector.inspect(record)
    assert out.sensitivity == Sensitivity.SECRET
    assert out.metadata["sensitive"]["suggested_sensitivity"] == "secret"
    assert "internal_info" in out.metadata["sensitive"]["detected"]


def test_empty_content_not_flagged(detector: BasicSensitiveDataDetector) -> None:
    res = detector.inspect_content(_record("just some ordinary notes here"))
    assert res.has_sensitive is False


def test_determinism(detector: BasicSensitiveDataDetector) -> None:
    content = "DB_PASSWORD=hunter2 and ssn 123-45-6789"
    first = detector.inspect_content(_record(content))
    second = detector.inspect_content(_record(content))
    assert first == second


# ---------------------------------------------------------------------------
# IngestionInspector contract: elevate-without-downgrade + metadata annotation
# ---------------------------------------------------------------------------


def test_inspect_elevates_to_secret(detector: BasicSensitiveDataDetector) -> None:
    """Validates: Requirement 25.4 (elevate sensitivity at ingestion)."""

    record = _record("DB_PASSWORD=hunter2", sensitivity=Sensitivity.INTERNAL)
    out = detector.inspect(record)
    assert out.sensitivity == Sensitivity.SECRET
    annotation = out.metadata["sensitive"]
    assert annotation["has_sensitive"] is True
    assert annotation["suggested_sensitivity"] == "secret"
    assert "password" in annotation["detected"]


def test_inspect_elevates_to_pii(detector: BasicSensitiveDataDetector) -> None:
    record = _record("contact a.b@example.com", sensitivity=Sensitivity.PUBLIC)
    out = detector.inspect(record)
    assert out.sensitivity == Sensitivity.PII
    assert out.metadata["sensitive"]["suggested_sensitivity"] == "pii"


def test_inspect_never_downgrades(detector: BasicSensitiveDataDetector) -> None:
    """A PII record with only an email stays PII (suggested PII == current)."""

    record = _record("email a.b@example.com", sensitivity=Sensitivity.PII)
    out = detector.inspect(record)
    assert out.sensitivity == Sensitivity.PII


def test_inspect_benign_leaves_record_untouched(
    detector: BasicSensitiveDataDetector,
) -> None:
    record = _record("ordinary architecture notes", sensitivity=Sensitivity.INTERNAL)
    out = detector.inspect(record)
    assert out.sensitivity == Sensitivity.INTERNAL
    assert "sensitive" not in out.metadata


def test_inspect_returns_serializable_metadata(
    detector: BasicSensitiveDataDetector,
) -> None:
    record = _record("token ghp_EXAMPLEEXAMPLEEXAMPLEEXAMPLEEX1234")
    out = detector.inspect(record)
    # The annotation must round-trip through JSON (no enum/object leakage).
    encoded = json.dumps(out.metadata["sensitive"])
    assert isinstance(encoded, str)


# ---------------------------------------------------------------------------
# Property 28 via the OSS eval harness (Req 25.3, 9.1)
# ---------------------------------------------------------------------------


def test_property28_harness_meets_precision_recall_and_tier_accuracy() -> None:
    """Property 28 through the evals harness: the real detector meets the
    configured sensitive-data precision / recall / tier-accuracy thresholds.

    Validates: Requirements 25.2, 25.3, 9.1 (Property 28).
    """

    harness = BasicEvaluationHarness()
    dataset = load_sensitive_fixtures()
    detector = BasicSensitiveDataDetector()

    metrics = harness.evaluate_sensitive(detector, dataset)
    assert metrics.support == len(dataset)
    assert metrics.precision >= SENSITIVE_PRECISION_MIN, (
        f"precision {metrics.precision:.3f} < {SENSITIVE_PRECISION_MIN} "
        f"(fp={metrics.false_positives})"
    )
    assert metrics.recall >= SENSITIVE_RECALL_MIN, (
        f"recall {metrics.recall:.3f} < {SENSITIVE_RECALL_MIN} "
        f"(fn={metrics.false_negatives})"
    )
    assert harness.sensitive_meets_thresholds(metrics)

    tier_acc = harness.sensitive_tier_accuracy(detector, dataset)
    assert 0.0 <= tier_acc <= 1.0
    assert tier_acc >= SENSITIVE_TIER_ACCURACY_MIN, (
        f"tier accuracy {tier_acc:.3f} < {SENSITIVE_TIER_ACCURACY_MIN}"
    )


def test_property28_every_known_secret_fixture_flagged_via_harness() -> None:
    """Req 25.3: every known-secret fixture (label 1) is flagged.

    Validates: Requirements 25.3, 9.1 (Property 28).
    """

    detector = BasicSensitiveDataDetector()
    dataset = load_sensitive_fixtures()

    missed: list[str] = []
    for rec in dataset:
        if int(rec.get("label", 0)) != 1:
            continue
        res = detector.inspect_content(_record(rec["content"]))
        if not res.has_sensitive:
            missed.append(rec.get("id", rec["content"][:40]))

    assert not missed, f"unflagged known-secret fixtures: {missed}"


# ---------------------------------------------------------------------------
# Property 34: detector outputs are bounded and well-formed
# ---------------------------------------------------------------------------


def _raw_record(content: str, sensitivity: Sensitivity = Sensitivity.PUBLIC):
    """Build a record wrapping arbitrary ``content`` with validation relaxed.

    Property 34 inspects the detector's robustness across *arbitrary* input
    (empty / whitespace / adversarial), so record-level validation (which
    rejects empty content) is intentionally skipped here -- the detector must
    still return a well-formed verdict.
    """

    return new_memory_record(
        content=content,
        source_type=SourceType.USER,
        source_ref="user://tester",
        scope=Scope.GLOBAL,
        sensitivity=sensitivity,
        validate_record=False,
    )


def _assert_well_formed(res: SensitiveResult) -> None:
    """Assert a SensitiveResult satisfies Property 34's well-formedness."""

    assert isinstance(res, SensitiveResult)
    # has_sensitive is a strict bool.
    assert isinstance(res.has_sensitive, bool)
    # detected is a list of strings.
    assert isinstance(res.detected, list)
    assert all(isinstance(name, str) for name in res.detected)
    # suggested_sensitivity is a valid Sensitivity tier.
    assert res.suggested_sensitivity in _VALID_TIERS
    # reason is a non-empty string.
    assert isinstance(res.reason, str)
    assert len(res.reason) >= 1
    # Internal consistency: flagged <=> non-empty detected, and a flagged
    # verdict always assigns an elevated tier.
    if res.has_sensitive:
        assert res.detected, "has_sensitive True but detected is empty"
        assert res.suggested_sensitivity in _ELEVATED
    else:
        assert res.detected == []


def test_property34_outputs_well_formed_on_fixtures(
    detector: BasicSensitiveDataDetector,
) -> None:
    """Property 34: every fixture (secret + benign) yields a well-formed result.

    Validates: Requirements 25.1, 25.2 (Property 34).
    """

    samples = [fx["content"] for fx in FIXTURES]
    samples += ["", "   ", "an ordinary note about the build pipeline"]
    for content in samples:
        _assert_well_formed(detector.inspect_content(_raw_record(content)))


@settings(max_examples=400, deadline=None)
@given(content=st.text())
def test_property34_outputs_bounded_and_well_formed_arbitrary(content: str) -> None:
    """Property 34: bounded + well-formed across arbitrary input content.

    The detector can never emit an out-of-range or malformed verdict, even on
    adversarial / random input, so sensitivity tagging is never corrupted.

    Validates: Requirements 25.1, 25.2 (Property 34).
    """

    detector = BasicSensitiveDataDetector()
    _assert_well_formed(detector.inspect_content(_raw_record(content)))


@settings(max_examples=200, deadline=None)
@given(
    content=st.text(),
    start_tier=st.sampled_from(list(Sensitivity)),
)
def test_property34_well_formed_for_any_starting_tier(
    content: str, start_tier: Sensitivity
) -> None:
    """Property 34: well-formed regardless of the record's starting tier.

    When nothing is flagged the suggested tier falls back to the record's
    current tier, which must still be a valid ``Sensitivity``.

    Validates: Requirements 25.1, 25.2 (Property 34).
    """

    detector = BasicSensitiveDataDetector()
    res = detector.inspect_content(_raw_record(content, sensitivity=start_tier))
    _assert_well_formed(res)
    if not res.has_sensitive:
        assert res.suggested_sensitivity == start_tier
