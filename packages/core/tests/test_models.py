# SPDX-License-Identifier: Apache-2.0
"""Unit tests for memoryguard_core.models MemoryRecord validation.

Covers the validation edge cases from Requirements 2.3, 2.4, 2.5, and 2.7:

* 2.3 — provenance required (``source_type`` / ``source_ref``)
* 2.4 — ``content`` non-empty after trimming
* 2.5 — ``scope_ref`` required for ``project/repo/user/session`` scopes
* 2.7 — lifecycle timestamps (``expires_at`` / ``updated_at`` ordering)

Plus supporting invariants exercised by the same code path: trust-score
clamping, self-contradiction rejection, UUIDv4 identity, and the
``new_memory_record`` factory's identity/timestamp guarantees.

These tests use only the Python standard library + pytest (no Hypothesis).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from memoryguard_core.models import (
    MemoryRecord,
    MemoryStatus,
    Scope,
    Sensitivity,
    SourceType,
    ValidationError,
    clamp_trust_score,
    new_memory_record,
    validate,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

# A canonical UUIDv4 string used wherever a valid id is needed.
VALID_UUID4 = "f47ac10b-58cc-4372-a567-0e02b2c3d479"


def _base_kwargs(**overrides):
    """Return constructor kwargs for a valid MemoryRecord, with overrides.

    The base record is deliberately valid so each test can flip exactly one
    field to isolate the rule under test.
    """

    created = datetime(2024, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
    kwargs = {
        "memory_id": VALID_UUID4,
        "content": "remember this fact",
        "source_type": SourceType.USER,
        "source_ref": "user://alice",
        "scope": Scope.GLOBAL,
        "scope_ref": None,
        "created_at": created,
        "updated_at": created,
        "expires_at": None,
        "trust_score": 0.5,
        "sensitivity": Sensitivity.INTERNAL,
        "status": MemoryStatus.ACTIVE,
    }
    kwargs.update(overrides)
    return kwargs


def _make(**overrides) -> MemoryRecord:
    """Construct a MemoryRecord from the valid base + overrides."""

    return MemoryRecord(**_base_kwargs(**overrides))


def test_base_record_is_valid():
    """Sanity check: the unmodified base record passes validation."""

    record = _make()
    assert validate(record) is record
    # The instance-method form should agree.
    assert record.validate() is record


# ---------------------------------------------------------------------------
# Requirement 2.3 — provenance required
# ---------------------------------------------------------------------------


def test_missing_source_ref_empty_string_raises():
    with pytest.raises(ValidationError):
        validate(_make(source_ref=""))


def test_missing_source_ref_whitespace_raises():
    with pytest.raises(ValidationError):
        validate(_make(source_ref="   "))


def test_missing_source_ref_none_raises():
    with pytest.raises(ValidationError):
        validate(_make(source_ref=None))


def test_invalid_source_type_raises():
    # A bare string is not a SourceType enum member.
    with pytest.raises(ValidationError):
        validate(_make(source_type="user"))


def test_valid_provenance_passes():
    record = _make(source_type=SourceType.FILE, source_ref="repo://README.md@abc123")
    assert validate(record) is record


# ---------------------------------------------------------------------------
# Requirement 2.4 — content non-empty after trimming
# ---------------------------------------------------------------------------


def test_empty_content_raises():
    with pytest.raises(ValidationError):
        validate(_make(content=""))


def test_whitespace_only_content_raises():
    with pytest.raises(ValidationError):
        validate(_make(content="   \n\t  "))


def test_content_with_surrounding_whitespace_is_valid():
    # Non-empty after trimming -> acceptable.
    record = _make(content="  hello  ")
    assert validate(record) is record


# ---------------------------------------------------------------------------
# Requirement 2.5 — scope_ref required for project/repo/user/session
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "scope",
    [Scope.PROJECT, Scope.REPO, Scope.USER, Scope.SESSION],
)
def test_scope_requiring_ref_missing_raises(scope):
    with pytest.raises(ValidationError):
        validate(_make(scope=scope, scope_ref=None))


@pytest.mark.parametrize(
    "scope",
    [Scope.PROJECT, Scope.REPO, Scope.USER, Scope.SESSION],
)
def test_scope_requiring_ref_whitespace_raises(scope):
    with pytest.raises(ValidationError):
        validate(_make(scope=scope, scope_ref="   "))


@pytest.mark.parametrize(
    "scope",
    [Scope.PROJECT, Scope.REPO, Scope.USER, Scope.SESSION],
)
def test_scope_requiring_ref_present_is_valid(scope):
    record = _make(scope=scope, scope_ref="proj-123")
    assert validate(record) is record


@pytest.mark.parametrize("scope", [Scope.GLOBAL, Scope.ORG])
def test_global_and_org_valid_without_scope_ref(scope):
    record = _make(scope=scope, scope_ref=None)
    assert validate(record) is record


# ---------------------------------------------------------------------------
# Requirement 2.7 — lifecycle timestamp ordering
# ---------------------------------------------------------------------------


def test_expires_at_equal_to_created_at_raises():
    created = datetime(2024, 1, 1, tzinfo=timezone.utc)
    with pytest.raises(ValidationError):
        validate(_make(created_at=created, updated_at=created, expires_at=created))


def test_expires_at_before_created_at_raises():
    created = datetime(2024, 1, 1, tzinfo=timezone.utc)
    earlier = created - timedelta(days=1)
    with pytest.raises(ValidationError):
        validate(_make(created_at=created, updated_at=created, expires_at=earlier))


def test_expires_at_after_created_at_is_valid():
    created = datetime(2024, 1, 1, tzinfo=timezone.utc)
    later = created + timedelta(days=1)
    record = _make(created_at=created, updated_at=created, expires_at=later)
    assert validate(record) is record


def test_expires_at_none_is_valid():
    record = _make(expires_at=None)
    assert validate(record) is record


def test_updated_at_before_created_at_raises():
    created = datetime(2024, 1, 1, tzinfo=timezone.utc)
    stale = created - timedelta(seconds=1)
    with pytest.raises(ValidationError):
        validate(_make(created_at=created, updated_at=stale))


def test_updated_at_equal_to_created_at_is_valid():
    created = datetime(2024, 1, 1, tzinfo=timezone.utc)
    record = _make(created_at=created, updated_at=created)
    assert validate(record) is record


def test_updated_at_after_created_at_is_valid():
    created = datetime(2024, 1, 1, tzinfo=timezone.utc)
    record = _make(created_at=created, updated_at=created + timedelta(hours=1))
    assert validate(record) is record


# ---------------------------------------------------------------------------
# Self-contradiction
# ---------------------------------------------------------------------------


def test_self_contradiction_raises():
    with pytest.raises(ValidationError):
        validate(_make(contradicts=[VALID_UUID4]))


def test_contradiction_with_other_id_is_valid():
    other = "9f8b7c6d-1234-4abc-8def-1234567890ab"
    record = _make(contradicts=[other])
    assert validate(record) is record


# ---------------------------------------------------------------------------
# trust_score clamping on construction
# ---------------------------------------------------------------------------


def test_trust_score_above_one_is_clamped():
    record = _make(trust_score=5.0)
    assert record.trust_score == 1.0


def test_trust_score_below_zero_is_clamped():
    record = _make(trust_score=-1.0)
    assert record.trust_score == 0.0


def test_trust_score_in_range_is_preserved():
    record = _make(trust_score=0.42)
    assert record.trust_score == 0.42


def test_clamp_trust_score_helper_boundaries():
    assert clamp_trust_score(-0.01) == 0.0
    assert clamp_trust_score(0.0) == 0.0
    assert clamp_trust_score(1.0) == 1.0
    assert clamp_trust_score(1.01) == 1.0
    # An int coerces to float and is clamped.
    assert clamp_trust_score(5) == 1.0


# ---------------------------------------------------------------------------
# memory_id must be a UUIDv4
# ---------------------------------------------------------------------------


def test_non_uuid_memory_id_raises():
    with pytest.raises(ValidationError):
        validate(_make(memory_id="not-a-uuid"))


def test_empty_memory_id_raises():
    with pytest.raises(ValidationError):
        validate(_make(memory_id=""))


def test_uuid_v1_memory_id_raises():
    # A version-1 UUID is structurally a UUID but not v4 -> rejected.
    uuid_v1 = "a8098c1a-f86e-11da-bd1a-00112444be1e"
    with pytest.raises(ValidationError):
        validate(_make(memory_id=uuid_v1))


# ---------------------------------------------------------------------------
# new_memory_record factory
# ---------------------------------------------------------------------------


def test_new_memory_record_produces_valid_uuid4():
    import uuid

    record = new_memory_record(
        content="hello world",
        source_type=SourceType.USER,
        source_ref="user://alice",
        scope=Scope.GLOBAL,
    )
    parsed = uuid.UUID(record.memory_id)
    assert parsed.version == 4
    assert str(parsed) == record.memory_id


def test_new_memory_record_timestamps_ordered():
    record = new_memory_record(
        content="hello",
        source_type=SourceType.USER,
        source_ref="user://alice",
        scope=Scope.GLOBAL,
    )
    assert record.updated_at >= record.created_at


def test_new_memory_record_clamps_trust_score():
    record = new_memory_record(
        content="hello",
        source_type=SourceType.USER,
        source_ref="user://alice",
        scope=Scope.GLOBAL,
        trust_score=9.0,
    )
    assert record.trust_score == 1.0


def test_new_memory_record_requires_scope_ref_for_bound_scope():
    with pytest.raises(ValidationError):
        new_memory_record(
            content="hello",
            source_type=SourceType.USER,
            source_ref="user://alice",
            scope=Scope.PROJECT,
        )


def test_new_memory_record_with_scope_ref_for_bound_scope_is_valid():
    record = new_memory_record(
        content="hello",
        source_type=SourceType.USER,
        source_ref="user://alice",
        scope=Scope.REPO,
        scope_ref="repo-42",
    )
    assert record.scope is Scope.REPO
    assert record.scope_ref == "repo-42"


def test_new_memory_record_generates_unique_ids():
    kwargs = dict(
        content="hello",
        source_type=SourceType.USER,
        source_ref="user://alice",
        scope=Scope.GLOBAL,
    )
    ids = {new_memory_record(**kwargs).memory_id for _ in range(50)}
    assert len(ids) == 50


def test_new_memory_record_rejects_empty_content():
    with pytest.raises(ValidationError):
        new_memory_record(
            content="   ",
            source_type=SourceType.USER,
            source_ref="user://alice",
            scope=Scope.GLOBAL,
        )


def test_new_memory_record_skip_validation_flag():
    # validate_record=False should bypass validation (still constructs).
    record = new_memory_record(
        content="   ",
        source_type=SourceType.USER,
        source_ref="user://alice",
        scope=Scope.GLOBAL,
        validate_record=False,
    )
    assert record.content == "   "
