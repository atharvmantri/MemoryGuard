# SPDX-License-Identifier: Apache-2.0
"""Data models for pending capture candidates."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Optional

from memoryguard_core.models import Sensitivity

__all__ = ["CaptureStatus", "CaptureCandidate", "utcnow"]


class CaptureStatus(str, Enum):
    """Approval lifecycle for extracted memories."""

    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


def utcnow() -> datetime:
    """Return a timezone-aware UTC timestamp."""

    return datetime.now(timezone.utc)


@dataclass
class CaptureCandidate:
    """A proposed durable project memory extracted from a transcript."""

    id: str
    content: str
    source_type: str
    source_ref: str
    evidence: str
    confidence: float
    sensitivity: Sensitivity
    created_at: datetime = field(default_factory=utcnow)
    status: CaptureStatus = CaptureStatus.PENDING
    canonical_content: Optional[str] = None
    decision_key: Optional[str] = None
    value: Optional[str] = None
    supersedes_value: Optional[str] = None
    metadata: dict = field(default_factory=dict)

    @classmethod
    def new(
        cls,
        *,
        content: str,
        source_type: str,
        source_ref: str,
        evidence: str,
        confidence: float,
        sensitivity: Sensitivity,
        canonical_content: Optional[str] = None,
        decision_key: Optional[str] = None,
        value: Optional[str] = None,
        supersedes_value: Optional[str] = None,
        metadata: Optional[dict] = None,
    ) -> "CaptureCandidate":
        """Build a pending candidate with a fresh id."""

        return cls(
            id=str(uuid.uuid4()),
            content=content,
            canonical_content=canonical_content,
            decision_key=decision_key,
            value=value,
            supersedes_value=supersedes_value,
            source_type=source_type,
            source_ref=source_ref,
            evidence=evidence,
            confidence=max(0.0, min(1.0, float(confidence))),
            sensitivity=sensitivity,
            metadata=dict(metadata or {}),
        )

    def to_json(self) -> dict:
        """Serialize for the local JSON queue."""

        return {
            "id": self.id,
            "content": self.content,
            "canonical_content": self.canonical_content,
            "decision_key": self.decision_key,
            "value": self.value,
            "supersedes_value": self.supersedes_value,
            "source_type": self.source_type,
            "source_ref": self.source_ref,
            "evidence": self.evidence,
            "confidence": self.confidence,
            "sensitivity": self.sensitivity.value,
            "created_at": self.created_at.isoformat(),
            "status": self.status.value,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_json(cls, data: dict) -> "CaptureCandidate":
        """Hydrate a candidate from the local JSON queue."""

        return cls(
            id=str(data["id"]),
            content=str(data["content"]),
            canonical_content=data.get("canonical_content"),
            decision_key=data.get("decision_key"),
            value=data.get("value"),
            supersedes_value=data.get("supersedes_value"),
            source_type=str(data["source_type"]),
            source_ref=str(data["source_ref"]),
            evidence=str(data.get("evidence", "")),
            confidence=float(data.get("confidence", 0.0)),
            sensitivity=Sensitivity(str(data.get("sensitivity", Sensitivity.INTERNAL.value))),
            created_at=datetime.fromisoformat(str(data["created_at"])),
            status=CaptureStatus(str(data.get("status", CaptureStatus.PENDING.value))),
            metadata=dict(data.get("metadata") or {}),
        )
