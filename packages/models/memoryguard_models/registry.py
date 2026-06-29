# SPDX-License-Identifier: Apache-2.0
"""Local file-based model registry (OSS default).

``LocalFileModelRegistry`` is the OSS ``ModelRegistry`` implementation: a JSON
index on disk with deterministic version resolution and **no network access**.

* ``register(mv)`` persists a ``ModelVersion`` to the JSON index. Re-registering
  the same ``(model_id, version)`` replaces the prior entry.
* ``resolve(model_id, version)``:
    - with an explicit ``version`` returns that exact ``ModelVersion`` (raising
      ``ModelVersionNotFoundError`` if it is absent), and
    - with ``version=None`` returns the highest registered semantic version for
      ``model_id``, computed via a small dependency-free semver comparator so
      resolution is deterministic regardless of registration order.

Standard library only (``json``, ``pathlib``, ``dataclasses`` via
``versioning``). Requirements: 27.2, 27.3, 21.1.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

from memoryguard_models.base import ModelRegistry
from memoryguard_models.versioning import ModelVersion

#: Default on-disk location for the registry index, under a local
#: ``.memoryguard/models`` directory relative to the current working directory.
DEFAULT_INDEX_PATH = Path(".memoryguard") / "models" / "index.json"


class ModelVersionNotFoundError(LookupError):
    """Raised when a requested ``model_id``/``version`` is not registered."""


# ---------------------------------------------------------------------------
# Minimal, dependency-free semantic-version comparison
# ---------------------------------------------------------------------------


def _identifier_key(identifier: str) -> tuple[int, int, str]:
    """Return a sort key for a single prerelease identifier.

    Per SemVer precedence rules: numeric identifiers have lower precedence than
    alphanumeric ones and are compared numerically; alphanumeric identifiers are
    compared lexically in ASCII order.
    """

    if identifier.isdigit():
        return (0, int(identifier), "")
    return (1, 0, identifier)


def semver_key(version: str) -> tuple:
    """Return a totally-ordered comparison key for a semver ``version`` string.

    Supports ``MAJOR.MINOR.PATCH`` with an optional ``-prerelease`` suffix and an
    optional ``+build`` metadata suffix (build metadata is ignored for
    precedence, per SemVer). A normal version outranks any of its prereleases
    (e.g. ``1.0.0`` > ``1.0.0-rc.1``).

    Raises:
        ValueError: if ``version`` is not a valid ``MAJOR.MINOR.PATCH`` core.
    """

    if not isinstance(version, str) or not version:
        raise ValueError(f"invalid semantic version: {version!r}")

    # Strip build metadata (does not affect precedence).
    core_and_pre = version.split("+", 1)[0]

    # Split optional prerelease.
    if "-" in core_and_pre:
        core, prerelease = core_and_pre.split("-", 1)
    else:
        core, prerelease = core_and_pre, ""

    parts = core.split(".")
    if len(parts) != 3:
        raise ValueError(f"invalid semantic version: {version!r}")
    try:
        major, minor, patch = (int(p) for p in parts)
    except ValueError as exc:  # non-numeric core component
        raise ValueError(f"invalid semantic version: {version!r}") from exc
    if major < 0 or minor < 0 or patch < 0:
        raise ValueError(f"invalid semantic version: {version!r}")

    if prerelease == "":
        # No prerelease => highest precedence for this core version.
        pre_key: tuple = (1,)
    else:
        identifiers = tuple(_identifier_key(i) for i in prerelease.split("."))
        # (0, ...) sorts below (1,) so any prerelease < the normal version.
        pre_key = (0, identifiers)

    return (major, minor, patch, pre_key)


class LocalFileModelRegistry(ModelRegistry):
    """OSS default registry: JSON index on disk, deterministic, no network."""

    def __init__(self, index_path: Optional[Path | str] = None) -> None:
        """Create a registry backed by ``index_path`` (default local dir)."""

        self._index_path = Path(index_path) if index_path is not None else DEFAULT_INDEX_PATH

    @property
    def index_path(self) -> Path:
        """Path to the JSON index file backing this registry."""

        return self._index_path

    # -- persistence ------------------------------------------------------

    def _load_entries(self) -> list[dict]:
        """Load raw entry dicts from disk (empty list if the index is absent)."""

        if not self._index_path.exists():
            return []
        with self._index_path.open("r", encoding="utf-8") as fh:
            data = json.load(fh)
        versions = data.get("versions", []) if isinstance(data, dict) else []
        return list(versions)

    def _write_entries(self, entries: list[dict]) -> None:
        """Persist raw entry dicts to disk, creating parent dirs as needed."""

        self._index_path.parent.mkdir(parents=True, exist_ok=True)
        with self._index_path.open("w", encoding="utf-8") as fh:
            json.dump({"versions": entries}, fh, indent=2, sort_keys=True)

    # -- ModelRegistry contract ------------------------------------------

    def register(self, mv: ModelVersion) -> None:
        """Register ``mv`` in the JSON index, replacing any same id+version."""

        entries = self._load_entries()
        entries = [
            e
            for e in entries
            if not (e.get("model_id") == mv.model_id and e.get("version") == mv.version)
        ]
        entries.append(mv.to_dict())
        self._write_entries(entries)

    def resolve(self, model_id: str, version: str | None = None) -> ModelVersion:
        """Resolve ``model_id`` to a ``ModelVersion`` (latest if ``version`` is None).

        Raises:
            ModelVersionNotFoundError: if no matching version is registered.
        """

        candidates = [
            ModelVersion.from_dict(e)
            for e in self._load_entries()
            if e.get("model_id") == model_id
        ]

        if not candidates:
            raise ModelVersionNotFoundError(
                f"no registered versions for model_id={model_id!r}"
            )

        if version is not None:
            for mv in candidates:
                if mv.version == version:
                    return mv
            raise ModelVersionNotFoundError(
                f"model_id={model_id!r} has no registered version={version!r}"
            )

        # version is None: deterministically return the highest semver. Ties on
        # the semver key (e.g. equivalent build metadata) break on the version
        # string for a fully deterministic result.
        return max(candidates, key=lambda mv: (semver_key(mv.version), mv.version))


__all__ = [
    "LocalFileModelRegistry",
    "ModelVersionNotFoundError",
    "DEFAULT_INDEX_PATH",
    "semver_key",
]
