# SPDX-License-Identifier: Apache-2.0
"""Property-based tests for the OSS ``LocalFileModelRegistry`` (offline, stdlib).

These Hypothesis properties complement the example-based tests in
``test_registry.py``. They run with no network access against temporary on-disk
JSON indexes, exercising deterministic version resolution across arbitrary
(but valid) semver inputs and arbitrary registration orderings.

Covers:

* **Property 32: Model registry version resolution determinism** --
  ``resolve(model_id, version=None)`` always returns the highest registered
  semantic version, ``resolve(model_id, version=exact)`` returns that exact
  version, and resolution is deterministic regardless of registration order.

Validates: Requirements 27.3.
"""

from __future__ import annotations

import random
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import pytest

from memoryguard_models import (
    LocalFileModelRegistry,
    ModelArtifact,
    ModelVersion,
)
from memoryguard_models.registry import semver_key

hypothesis = pytest.importorskip("hypothesis")
from hypothesis import given, settings  # noqa: E402
from hypothesis import strategies as st  # noqa: E402


CREATED_AT = datetime(2024, 1, 1, tzinfo=timezone.utc)
MODEL_ID = "reranker/heuristic"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_version(version: str, model_id: str = MODEL_ID) -> ModelVersion:
    """Build a deterministic ``ModelVersion`` fixture for ``version``."""

    return ModelVersion(
        model_id=model_id,
        task="rerank",
        version=version,
        metrics={"score": 0.9},
        artifact=ModelArtifact(
            artifact_uri=f"file:///models/{model_id}/{version}.bin",
            sha256="0" * 64,
            size_bytes=1024,
        ),
        created_at=CREATED_AT,
    )


# ---------------------------------------------------------------------------
# Strategies: generate valid, distinct semver strings constrained to the input
# space the registry actually accepts (MAJOR.MINOR.PATCH with optional, valid
# prerelease identifiers; no build metadata needed for precedence).
# ---------------------------------------------------------------------------

_component = st.integers(min_value=0, max_value=25)

# Prerelease identifiers: numeric (no leading zeros, kept small) or alphanumeric.
_numeric_ident = st.integers(min_value=0, max_value=50).map(str)
_alpha_ident = st.text(
    alphabet="abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ",
    min_size=1,
    max_size=4,
)
_prerelease = st.lists(
    st.one_of(_numeric_ident, _alpha_ident), min_size=1, max_size=3
).map(lambda ids: "-" + ".".join(ids))


@st.composite
def semver_strings(draw: st.DrawFn) -> str:
    """Draw a single valid semantic version string."""

    major = draw(_component)
    minor = draw(_component)
    patch = draw(_component)
    suffix = draw(st.one_of(st.just(""), _prerelease))
    return f"{major}.{minor}.{patch}{suffix}"


def _distinct_versions() -> st.SearchStrategy[list[str]]:
    """Draw a non-empty list of distinct, valid semver strings."""

    return st.lists(semver_strings(), min_size=1, max_size=8, unique=True)


# ---------------------------------------------------------------------------
# Property 32
# ---------------------------------------------------------------------------


@settings(max_examples=200, deadline=None)
@given(versions=_distinct_versions(), seed=st.integers(min_value=0, max_value=2**32 - 1))
def test_resolve_latest_is_highest_semver_regardless_of_order(
    versions: list[str], seed: int
) -> None:
    """resolve(None) returns the highest semver, independent of insert order.

    Validates: Requirements 27.3
    """

    shuffled = list(versions)
    random.Random(seed).shuffle(shuffled)

    with tempfile.TemporaryDirectory() as tmp:
        reg = LocalFileModelRegistry(index_path=Path(tmp) / "index.json")
        for v in shuffled:
            reg.register(_make_version(v))

        resolved = reg.resolve(MODEL_ID)

        # The resolved version's semver key dominates every registered version.
        resolved_key = semver_key(resolved.version)
        for v in versions:
            assert resolved_key >= semver_key(v)

        # And it is itself one of the registered versions.
        assert resolved.version in set(versions)


@settings(max_examples=200, deadline=None)
@given(versions=_distinct_versions())
def test_resolve_explicit_version_is_exact(versions: list[str]) -> None:
    """resolve(model_id, v) returns exactly the requested version ``v``.

    Validates: Requirements 27.3
    """

    with tempfile.TemporaryDirectory() as tmp:
        reg = LocalFileModelRegistry(index_path=Path(tmp) / "index.json")
        for v in versions:
            reg.register(_make_version(v))

        for v in versions:
            mv = reg.resolve(MODEL_ID, v)
            assert mv.version == v
            assert mv.model_id == MODEL_ID


@settings(max_examples=200, deadline=None)
@given(
    versions=_distinct_versions(),
    seed_a=st.integers(min_value=0, max_value=2**32 - 1),
    seed_b=st.integers(min_value=0, max_value=2**32 - 1),
)
def test_resolution_is_deterministic_across_two_registries(
    versions: list[str], seed_a: int, seed_b: int
) -> None:
    """Two registries built from the same set in different orders agree.

    Validates: Requirements 27.3
    """

    order_a = list(versions)
    order_b = list(versions)
    random.Random(seed_a).shuffle(order_a)
    random.Random(seed_b).shuffle(order_b)

    with tempfile.TemporaryDirectory() as tmp_a, tempfile.TemporaryDirectory() as tmp_b:
        reg_a = LocalFileModelRegistry(index_path=Path(tmp_a) / "index.json")
        reg_b = LocalFileModelRegistry(index_path=Path(tmp_b) / "index.json")
        for v in order_a:
            reg_a.register(_make_version(v))
        for v in order_b:
            reg_b.register(_make_version(v))

        # Same registry resolves identically when called repeatedly (idempotent),
        assert reg_a.resolve(MODEL_ID).version == reg_a.resolve(MODEL_ID).version
        # and two independently-ordered registries resolve to the same latest.
        assert reg_a.resolve(MODEL_ID).version == reg_b.resolve(MODEL_ID).version
