# SPDX-License-Identifier: Apache-2.0
"""Model version + artifact records for the MemoryGuard model registry (OSS).

These dataclasses are the on-disk/in-memory representation of a registered
model. They are intentionally dependency-free (standard library only) so the
OSS ``LocalFileModelRegistry`` can persist and resolve them as JSON with no
network access.

* ``ModelArtifact`` pins the concrete bytes of a model (a ``file://`` path in
  OSS/local mode, or ``s3://...`` for hosted/commercial deployments) together
  with an integrity ``sha256`` and ``size_bytes``.
* ``ModelVersion`` is the full registry entry: ``model_id``, ``task``, a semver
  ``version`` string, an eval ``metrics`` snapshot, the ``artifact``, and the
  ``created_at`` registration timestamp.

Serialization helpers (``to_dict`` / ``from_dict``) round-trip a ``ModelVersion``
through plain JSON-compatible primitives. ``created_at`` is stored as an ISO-8601
string (timezone-aware UTC), matching ``packages/core`` conventions.

Requirements: 27.2, 27.3, 21.1.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


def _utcnow() -> datetime:
    """Return the current UTC time as a timezone-aware ``datetime``."""

    return datetime.now(timezone.utc)


@dataclass
class ModelArtifact:
    """The concrete, integrity-checked bytes backing a ``ModelVersion``.

    Attributes:
        artifact_uri: ``file://`` path (OSS local) or ``s3://...`` (hosted
            commercial) locating the artifact.
        sha256: hex digest of the artifact for integrity verification.
        size_bytes: artifact size in bytes.
    """

    artifact_uri: str
    sha256: str
    size_bytes: int

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable representation."""

        return {
            "artifact_uri": self.artifact_uri,
            "sha256": self.sha256,
            "size_bytes": self.size_bytes,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ModelArtifact":
        """Reconstruct a ``ModelArtifact`` from its dict representation."""

        return cls(
            artifact_uri=data["artifact_uri"],
            sha256=data["sha256"],
            size_bytes=int(data["size_bytes"]),
        )


@dataclass
class ModelVersion:
    """A single registered model entry.

    Attributes:
        model_id: e.g. ``"reranker/heuristic"``, ``"embedder/minilm-l6"``.
        task: one of ``"embed" | "rerank" | "contradiction" | "poison" |
            "sensitive" | "trust"``.
        version: semantic version string, e.g. ``"1.2.0"``.
        metrics: eval metrics snapshot captured at registration time.
        artifact: the ``ModelArtifact`` backing this version.
        created_at: registration timestamp (timezone-aware UTC by default).
    """

    model_id: str
    task: str
    version: str
    metrics: dict
    artifact: ModelArtifact
    created_at: datetime = field(default_factory=_utcnow)

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable representation."""

        return {
            "model_id": self.model_id,
            "task": self.task,
            "version": self.version,
            "metrics": self.metrics,
            "artifact": self.artifact.to_dict(),
            "created_at": self.created_at.isoformat(),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ModelVersion":
        """Reconstruct a ``ModelVersion`` from its dict representation."""

        return cls(
            model_id=data["model_id"],
            task=data["task"],
            version=data["version"],
            metrics=dict(data.get("metrics") or {}),
            artifact=ModelArtifact.from_dict(data["artifact"]),
            created_at=datetime.fromisoformat(data["created_at"]),
        )


__all__ = ["ModelArtifact", "ModelVersion"]
