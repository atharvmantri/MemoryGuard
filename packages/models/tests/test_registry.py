# SPDX-License-Identifier: Apache-2.0
"""Unit tests for the OSS ``LocalFileModelRegistry`` (offline, stdlib only).

These tests run with no network access against a temporary on-disk JSON index.
They exercise deterministic version resolution:

* ``resolve(model_id)`` (``version=None``) returns the highest registered semver
  regardless of registration order;
* ``resolve(model_id, version)`` returns that exact version;
* resolving an absent ``model_id`` or ``version`` raises a clear error;
* the index round-trips through disk (register persists; a fresh registry over
  the same path resolves it).

These cover Property 32 (model registry version resolution determinism).

Validates: Requirements 27.2, 27.3, 21.1.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from memoryguard_models import (
    LocalFileModelRegistry,
    ModelArtifact,
    ModelVersion,
)
from memoryguard_models.registry import (
    ModelVersionNotFoundError,
    semver_key,
)


def _make_version(model_id: str, version: str, task: str = "rerank") -> ModelVersion:
    """Build a ``ModelVersion`` fixture with a deterministic artifact."""

    return ModelVersion(
        model_id=model_id,
        task=task,
        version=version,
        metrics={"score": 0.9},
        artifact=ModelArtifact(
            artifact_uri=f"file:///models/{model_id}/{version}.bin",
            sha256="0" * 64,
            size_bytes=1024,
        ),
        created_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
    )


def _registry(tmp_path: Path) -> LocalFileModelRegistry:
    """Return a registry rooted at an isolated temp index file."""

    return LocalFileModelRegistry(index_path=tmp_path / "models" / "index.json")


# ---------------------------------------------------------------------------
# resolve(version=None) -> highest semver
# ---------------------------------------------------------------------------


def test_resolve_latest_returns_highest_semver(tmp_path: Path) -> None:
    reg = _registry(tmp_path)
    # Register out of order to prove resolution is independent of order.
    reg.register(_make_version("reranker/heuristic", "1.0.0"))
    reg.register(_make_version("reranker/heuristic", "1.2.0"))
    reg.register(_make_version("reranker/heuristic", "1.10.0"))
    reg.register(_make_version("reranker/heuristic", "1.2.5"))

    latest = reg.resolve("reranker/heuristic")

    # 1.10.0 > 1.2.5 numerically (not lexically).
    assert latest.version == "1.10.0"


def test_resolve_latest_is_deterministic_across_orderings(tmp_path: Path) -> None:
    versions = ["0.9.0", "2.0.0", "1.5.3", "2.0.0-rc.1", "1.5.10"]

    # Forward registration order.
    reg_a = LocalFileModelRegistry(index_path=tmp_path / "a" / "index.json")
    for v in versions:
        reg_a.register(_make_version("embedder/minilm-l6", v))

    # Reversed registration order.
    reg_b = LocalFileModelRegistry(index_path=tmp_path / "b" / "index.json")
    for v in reversed(versions):
        reg_b.register(_make_version("embedder/minilm-l6", v))

    assert reg_a.resolve("embedder/minilm-l6").version == "2.0.0"
    assert reg_b.resolve("embedder/minilm-l6").version == "2.0.0"


def test_normal_version_outranks_its_prerelease(tmp_path: Path) -> None:
    reg = _registry(tmp_path)
    reg.register(_make_version("trust/deterministic", "1.0.0-rc.1"))
    reg.register(_make_version("trust/deterministic", "1.0.0"))

    assert reg.resolve("trust/deterministic").version == "1.0.0"


# ---------------------------------------------------------------------------
# resolve(explicit version) -> exact
# ---------------------------------------------------------------------------


def test_resolve_explicit_version_is_exact(tmp_path: Path) -> None:
    reg = _registry(tmp_path)
    reg.register(_make_version("reranker/heuristic", "1.0.0"))
    reg.register(_make_version("reranker/heuristic", "2.3.1"))

    mv = reg.resolve("reranker/heuristic", "1.0.0")

    assert mv.version == "1.0.0"
    assert mv.model_id == "reranker/heuristic"
    assert mv.artifact.artifact_uri.endswith("1.0.0.bin")


# ---------------------------------------------------------------------------
# missing -> raises
# ---------------------------------------------------------------------------


def test_resolve_unknown_model_id_raises(tmp_path: Path) -> None:
    reg = _registry(tmp_path)
    reg.register(_make_version("reranker/heuristic", "1.0.0"))

    with pytest.raises(ModelVersionNotFoundError):
        reg.resolve("does/not-exist")


def test_resolve_missing_explicit_version_raises(tmp_path: Path) -> None:
    reg = _registry(tmp_path)
    reg.register(_make_version("reranker/heuristic", "1.0.0"))

    with pytest.raises(ModelVersionNotFoundError):
        reg.resolve("reranker/heuristic", "9.9.9")


def test_resolve_empty_registry_raises(tmp_path: Path) -> None:
    reg = _registry(tmp_path)

    with pytest.raises(ModelVersionNotFoundError):
        reg.resolve("anything")


# ---------------------------------------------------------------------------
# persistence + round-trip
# ---------------------------------------------------------------------------


def test_register_persists_to_disk_and_reloads(tmp_path: Path) -> None:
    index_path = tmp_path / "models" / "index.json"
    reg = LocalFileModelRegistry(index_path=index_path)
    reg.register(_make_version("poison/rules", "1.4.2"))

    assert index_path.exists()

    # A fresh registry over the same path resolves the persisted entry.
    reloaded = LocalFileModelRegistry(index_path=index_path)
    mv = reloaded.resolve("poison/rules")

    assert mv.version == "1.4.2"
    assert mv.metrics == {"score": 0.9}
    assert mv.created_at == datetime(2024, 1, 1, tzinfo=timezone.utc)


def test_reregister_same_version_replaces_entry(tmp_path: Path) -> None:
    reg = _registry(tmp_path)
    reg.register(_make_version("sensitive/basic", "1.0.0"))

    updated = _make_version("sensitive/basic", "1.0.0")
    updated.metrics = {"score": 0.99}
    reg.register(updated)

    mv = reg.resolve("sensitive/basic", "1.0.0")
    assert mv.metrics == {"score": 0.99}

    # Only a single entry for that id+version remains.
    matches = [e for e in reg._load_entries() if e["model_id"] == "sensitive/basic"]
    assert len(matches) == 1


def test_versions_isolated_by_model_id(tmp_path: Path) -> None:
    reg = _registry(tmp_path)
    reg.register(_make_version("reranker/heuristic", "3.0.0"))
    reg.register(_make_version("embedder/minilm-l6", "1.0.0"))

    assert reg.resolve("reranker/heuristic").version == "3.0.0"
    assert reg.resolve("embedder/minilm-l6").version == "1.0.0"


# ---------------------------------------------------------------------------
# semver comparator
# ---------------------------------------------------------------------------


def test_semver_key_orders_numerically_not_lexically() -> None:
    assert semver_key("1.10.0") > semver_key("1.2.0")
    assert semver_key("2.0.0") > semver_key("1.99.99")
    assert semver_key("1.0.0") > semver_key("1.0.0-alpha")
    assert semver_key("1.0.0-beta") > semver_key("1.0.0-alpha")


def test_semver_key_rejects_invalid_version() -> None:
    with pytest.raises(ValueError):
        semver_key("1.0")
    with pytest.raises(ValueError):
        semver_key("not-a-version")
