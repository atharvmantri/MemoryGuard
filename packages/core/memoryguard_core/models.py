# SPDX-License-Identifier: Apache-2.0
"""Core data models, enums, and validation for MemoryGuard.

Defines the canonical :class:`MemoryRecord` schema (identical in local and cloud
modes), the supporting enums, a :class:`ValidationError`, a ``validate`` routine
enforcing every rule from the design's *Data Models* section, and a
:func:`new_memory_record` factory that assigns a UUIDv4 identity and timestamps.

This module is dependency-free (Python standard library only) and is part of the
Apache-2.0 OSS core. It MUST NOT import from any commercial package.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Optional

__all__ = [
    "SourceType",
    "Scope",
    "Sensitivity",
    "MemoryStatus",
    "MemoryRecord",
    "ValidationError",
    "validate",
    "new_memory_record",
    "clamp_trust_score",
    "SCOPE_REF_REQUIRED",
]


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class SourceType(str, Enum):
    """Where a memory came from."""

    USER = "user"
    FILE = "file"
    COMMIT = "commit"
    SLACK = "slack"  # commercial connector
    JIRA = "jira"  # commercial connector
    API = "api"


class Scope(str, Enum):
    """The visibility boundary of a memory."""

    GLOBAL = "global"
    ORG = "org"
    PROJECT = "project"
    REPO = "repo"
    USER = "user"
    SESSION = "session"


class Sensitivity(str, Enum):
    """The data-sensitivity tier of a memory."""

    PUBLIC = "public"
    INTERNAL = "internal"
    SECRET = "secret"
    PII = "pii"


class MemoryStatus(str, Enum):
    """The lifecycle state of a memory."""

    ACTIVE = "active"
    CORRECTED = "corrected"
    SUPERSEDED = "superseded"
    OUTDATED = "outdated"
    EXPIRED = "expired"
    DELETED = "deleted"
    DISPUTED = "disputed"


# Scopes that require a bound ``scope_ref`` identifier.
SCOPE_REF_REQUIRED: frozenset[Scope] = frozenset(
    {Scope.PROJECT, Scope.REPO, Scope.USER, Scope.SESSION}
)


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class ValidationError(ValueError):
    """Raised when a :class:`MemoryRecord` violates a validation rule."""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _utcnow() -> datetime:
    """Return the current UTC time as a timezone-aware ``datetime``."""

    return datetime.now(timezone.utc)


def clamp_trust_score(value: float) -> float:
    """Clamp ``value`` into the inclusive range ``[0.0, 1.0]``.

    Used on write so a record's ``trust_score`` is always in range. Returns a
    ``float`` even if a coercible value (e.g. an ``int``) is supplied.
    """

    numeric = float(value)
    if numeric < 0.0:
        return 0.0
    if numeric > 1.0:
        return 1.0
    return numeric


def _is_uuid4(candidate: str) -> bool:
    """Return ``True`` when ``candidate`` is a canonical UUIDv4 string."""

    if not isinstance(candidate, str) or not candidate:
        return False
    try:
        parsed = uuid.UUID(candidate)
    except (ValueError, AttributeError, TypeError):
        return False
    # Reject non-version-4 UUIDs and any non-canonical formatting.
    return parsed.version == 4 and str(parsed) == candidate.lower()


# ---------------------------------------------------------------------------
# MemoryRecord
# ---------------------------------------------------------------------------


@dataclass
class MemoryRecord:
    """The canonical memory schema — identical in local and cloud modes.

    See the design's *Data Models* section. ``trust_score`` is a ranking signal
    in ``[0.0, 1.0]`` (clamped on write via :meth:`__post_init__`), not a measure
    of absolute truth.
    """

    memory_id: str  # UUIDv4, stable identity
    content: str  # the remembered text
    source_type: SourceType
    source_ref: str  # e.g. "repo://README.md@commit123", "user://alice"
    scope: Scope
    scope_ref: Optional[str] = None  # which project/repo/user/session id the scope binds to
    created_at: datetime = field(default_factory=_utcnow)
    updated_at: datetime = field(default_factory=_utcnow)
    expires_at: Optional[datetime] = None
    trust_score: float = 0.0  # 0.0..1.0 ranking signal (not absolute truth)
    sensitivity: Sensitivity = Sensitivity.INTERNAL
    status: MemoryStatus = MemoryStatus.ACTIVE
    contradicts: list[str] = field(default_factory=list)  # memory_ids of conflicting memories
    tags: list[str] = field(default_factory=list)
    confirmations: int = 0  # times re-observed/confirmed (feeds confirmation_score)
    embedding: Optional[list[float]] = None  # vector; stored separately in pgvector
    metadata: dict = field(default_factory=dict)  # extensible bag for connectors/enterprise

    def __post_init__(self) -> None:
        # Clamp trust_score into range on write so the invariant holds at all times.
        self.trust_score = clamp_trust_score(self.trust_score)

    def validate(self) -> "MemoryRecord":
        """Validate this record against every design rule.

        Returns the record itself for chaining. Raises :class:`ValidationError`
        on the first violated rule.
        """

        return validate(self)


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def validate(record: MemoryRecord) -> MemoryRecord:
    """Enforce every :class:`MemoryRecord` validation rule from the design.

    Rules:
      * ``memory_id`` is a non-empty UUIDv4.
      * ``content`` is non-empty after trimming.
      * ``0.0 <= trust_score <= 1.0`` (clamped on write; re-checked here).
      * ``updated_at >= created_at``.
      * If ``expires_at`` is set, ``expires_at > created_at``.
      * ``contradicts`` MUST NOT contain ``memory_id`` (no self-contradiction).
      * ``scope_ref`` is required when ``scope`` is one of
        ``{project, repo, user, session}``.

    Returns ``record`` for chaining; raises :class:`ValidationError` otherwise.
    """

    # memory_id: non-empty UUIDv4.
    if not _is_uuid4(record.memory_id):
        raise ValidationError(
            "memory_id must be a non-empty UUIDv4 string; "
            f"got {record.memory_id!r}"
        )

    # Provenance: source_type / source_ref must be present and well-typed.
    if not isinstance(record.source_type, SourceType):
        raise ValidationError(
            f"source_type must be a SourceType; got {record.source_type!r}"
        )
    if not isinstance(record.source_ref, str) or not record.source_ref.strip():
        raise ValidationError("source_ref must be a non-empty string (provenance required)")

    # content: non-empty after trimming.
    if not isinstance(record.content, str) or not record.content.strip():
        raise ValidationError("content must be non-empty after trimming")

    # scope must be a Scope.
    if not isinstance(record.scope, Scope):
        raise ValidationError(f"scope must be a Scope; got {record.scope!r}")

    # sensitivity / status must be the correct enum types.
    if not isinstance(record.sensitivity, Sensitivity):
        raise ValidationError(
            f"sensitivity must be a Sensitivity; got {record.sensitivity!r}"
        )
    if not isinstance(record.status, MemoryStatus):
        raise ValidationError(f"status must be a MemoryStatus; got {record.status!r}")

    # trust_score: 0.0..1.0 inclusive (defensive; normally clamped on write).
    if not 0.0 <= record.trust_score <= 1.0:
        raise ValidationError(
            f"trust_score must be within [0.0, 1.0]; got {record.trust_score!r}"
        )

    # Timestamps: updated_at >= created_at.
    if not isinstance(record.created_at, datetime):
        raise ValidationError("created_at must be a datetime")
    if not isinstance(record.updated_at, datetime):
        raise ValidationError("updated_at must be a datetime")
    if record.updated_at < record.created_at:
        raise ValidationError(
            "updated_at must be greater than or equal to created_at "
            f"(updated_at={record.updated_at!r}, created_at={record.created_at!r})"
        )

    # expires_at, if set, must be strictly after created_at.
    if record.expires_at is not None:
        if not isinstance(record.expires_at, datetime):
            raise ValidationError("expires_at must be a datetime or None")
        if record.expires_at <= record.created_at:
            raise ValidationError(
                "expires_at must be greater than created_at "
                f"(expires_at={record.expires_at!r}, created_at={record.created_at!r})"
            )

    # No self-contradiction.
    if record.memory_id in record.contradicts:
        raise ValidationError("contradicts must not contain the record's own memory_id")

    # scope_ref required for project/repo/user/session scopes.
    if record.scope in SCOPE_REF_REQUIRED:
        if record.scope_ref is None or not str(record.scope_ref).strip():
            raise ValidationError(
                f"scope_ref is required when scope is {record.scope.value!r}"
            )

    return record


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


def new_memory_record(
    *,
    content: str,
    source_type: SourceType,
    source_ref: str,
    scope: Scope,
    scope_ref: Optional[str] = None,
    expires_at: Optional[datetime] = None,
    trust_score: float = 0.0,
    sensitivity: Sensitivity = Sensitivity.INTERNAL,
    status: MemoryStatus = MemoryStatus.ACTIVE,
    contradicts: Optional[list[str]] = None,
    tags: Optional[list[str]] = None,
    confirmations: int = 0,
    embedding: Optional[list[float]] = None,
    metadata: Optional[dict] = None,
    now: Optional[datetime] = None,
    validate_record: bool = True,
) -> MemoryRecord:
    """Create a new :class:`MemoryRecord` with a fresh UUIDv4 id and timestamps.

    Assigns a UUIDv4 ``memory_id`` and sets ``created_at`` / ``updated_at`` to the
    same instant (so ``updated_at >= created_at`` holds). ``trust_score`` is
    clamped on construction. When ``validate_record`` is ``True`` (default) the
    resulting record is validated before it is returned.
    """

    created = now if now is not None else _utcnow()
    record = MemoryRecord(
        memory_id=str(uuid.uuid4()),
        content=content,
        source_type=source_type,
        source_ref=source_ref,
        scope=scope,
        scope_ref=scope_ref,
        created_at=created,
        updated_at=created,
        expires_at=expires_at,
        trust_score=trust_score,
        sensitivity=sensitivity,
        status=status,
        contradicts=list(contradicts) if contradicts is not None else [],
        tags=list(tags) if tags is not None else [],
        confirmations=confirmations,
        embedding=list(embedding) if embedding is not None else None,
        metadata=dict(metadata) if metadata is not None else {},
    )
    if validate_record:
        validate(record)
    return record
