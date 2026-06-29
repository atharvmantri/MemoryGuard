# SPDX-License-Identifier: Apache-2.0
"""Unit tests for the OSS ``LocalEmbedder`` (offline, stdlib-friendly).

These tests run with no network access and no ML dependencies: they exercise the
default dependency-free deterministic hash backend.

Covers Property 25 (Embedder determinism + fixed dimension) plus supporting
behaviors:

* exactly 384-dimensional output (``dim == 384``);
* determinism: identical input under a fixed ``model_version`` -> identical
  vector;
* distinct inputs generally produce different vectors;
* model identity / version surfaces (``info``, ``model_version``);
* L2-normalization of non-empty inputs.

Validates: Requirements 21.2, 21.3, 21.4.
"""

from __future__ import annotations

import math

import pytest

from memoryguard_models import Embedder, LocalEmbedder, ModelInfo
from memoryguard_models.embedder.local_embedder import (
    DEFAULT_EMBED_DIM,
    DEFAULT_MODEL_VERSION,
)

hypothesis = pytest.importorskip("hypothesis")
from hypothesis import given, settings  # noqa: E402
from hypothesis import strategies as st  # noqa: E402


# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------

#: Arbitrary text inputs, including empty / whitespace / punctuation-only and
#: full Unicode, to exercise the embedder across the whole input space.
_text = st.text(min_size=0, max_size=200)


def test_is_embedder_instance() -> None:
    """LocalEmbedder honors the Embedder interface."""
    assert isinstance(LocalEmbedder(), Embedder)


def test_dim_is_exactly_384() -> None:
    """The dim property is exactly 384 (Requirement 21.2)."""
    assert LocalEmbedder().dim == 384
    assert DEFAULT_EMBED_DIM == 384


def test_embedding_length_is_dim() -> None:
    """Every embedding has exactly `dim` (384) elements (Requirement 21.2)."""
    emb = LocalEmbedder()
    for text in ["", "hello", "the quick brown fox jumps over the lazy dog"]:
        vec = emb.embed(text)
        assert isinstance(vec, list)
        assert len(vec) == 384
        assert all(isinstance(v, float) for v in vec)


def test_determinism_same_input_identical_vector() -> None:
    """Identical input under a fixed model_version -> identical vector.

    Property 25 / Requirement 21.3. Uses a fresh instance to prove determinism
    is not instance state but a function of (text, model_version).
    """
    text = "MemoryGuard keeps trustworthy memories"
    first = LocalEmbedder().embed(text)
    second = LocalEmbedder().embed(text)
    assert first == second


def test_distinct_inputs_generally_differ() -> None:
    """Different inputs generally produce different vectors."""
    emb = LocalEmbedder()
    a = emb.embed("alpha beta gamma")
    b = emb.embed("delta epsilon zeta")
    assert a != b


def test_many_distinct_inputs_mostly_unique() -> None:
    """A batch of distinct inputs yields overwhelmingly unique vectors."""
    emb = LocalEmbedder()
    texts = [f"unique memory number {i} about topic {i * 7}" for i in range(50)]
    vectors = [tuple(emb.embed(t)) for t in texts]
    # Allow for rare collisions but require near-total uniqueness.
    assert len(set(vectors)) >= len(texts) - 1


def test_non_empty_embedding_is_l2_normalized() -> None:
    """Non-empty inputs are L2-normalized (unit length)."""
    vec = LocalEmbedder().embed("normalize me please")
    norm = math.sqrt(sum(v * v for v in vec))
    assert norm == pytest.approx(1.0, abs=1e-9)


def test_empty_input_is_zero_vector() -> None:
    """Empty / token-less input yields a deterministic zero vector of dim 384."""
    vec = LocalEmbedder().embed("")
    assert len(vec) == 384
    assert all(v == 0.0 for v in vec)
    # Whitespace / punctuation-only inputs also produce no tokens.
    assert LocalEmbedder().embed("   !!!  ") == vec


def test_model_version_and_info() -> None:
    """model_version and info expose stable identity with task='embed'."""
    emb = LocalEmbedder()
    assert emb.model_version == DEFAULT_MODEL_VERSION
    assert emb.model_version == "embedder/hash-minilm@1.0.0"
    info = emb.info
    assert isinstance(info, ModelInfo)
    assert info.task == "embed"
    assert info.model_id == "embedder/hash-minilm"
    assert info.version == "1.0.0"


def test_different_model_version_changes_vector() -> None:
    """Determinism is scoped to model_version: a different version differs."""
    base = LocalEmbedder()
    other = LocalEmbedder(version="2.0.0")
    text = "same text different model version"
    assert other.model_version != base.model_version
    assert other.embed(text) != base.embed(text)


def test_model_version_metadata_helper() -> None:
    """The registry helper exposes identifying fields for ModelVersion."""
    meta = LocalEmbedder().model_version_metadata()
    assert meta["model_id"] == "embedder/hash-minilm"
    assert meta["task"] == "embed"
    assert meta["version"] == "1.0.0"
    assert meta["model_version"] == DEFAULT_MODEL_VERSION
    assert meta["dim"] == 384
    assert meta["backend"] == "hash"


def test_invalid_backend_rejected() -> None:
    """An unknown backend is rejected at construction."""
    with pytest.raises(ValueError):
        LocalEmbedder(backend="magic")


def test_non_string_input_rejected() -> None:
    """embed() requires a str input."""
    with pytest.raises(TypeError):
        LocalEmbedder().embed(123)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Property 25: Embedder determinism + fixed dimension (Hypothesis)
# ---------------------------------------------------------------------------


@settings(max_examples=300, deadline=None)
@given(text=_text)
def test_property25_fixed_dimension(text: str) -> None:
    """Property 25: every embedding has exactly `dim` (384) elements.

    For arbitrary text inputs the embedder returns a list of exactly
    ``embedder.dim`` floats, and ``dim`` is fixed at 384 to match the
    ``vector(384)`` storage column.

    **Validates: Requirements 21.2**
    """
    embedder = LocalEmbedder()
    vec = embedder.embed(text)
    assert isinstance(vec, list)
    assert len(vec) == embedder.dim == 384 == DEFAULT_EMBED_DIM
    assert all(isinstance(v, float) for v in vec)


@settings(max_examples=300, deadline=None)
@given(text=_text)
def test_property25_determinism_fixed_model_version(text: str) -> None:
    """Property 25: identical input under a fixed model_version is deterministic.

    Two independent embedder instances sharing the same ``model_version`` map
    identical text to byte-identical vectors, proving determinism is a function
    of ``(text, model_version)`` and not of instance/runtime state.

    **Validates: Requirements 21.3**
    """
    first = LocalEmbedder()
    second = LocalEmbedder()
    assert first.model_version == second.model_version == DEFAULT_MODEL_VERSION
    v1 = first.embed(text)
    v2 = second.embed(text)
    assert v1 == v2
    # Repeating on the same instance is also stable.
    assert first.embed(text) == v1
