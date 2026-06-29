# SPDX-License-Identifier: Apache-2.0
"""On-device default embedder (OSS).

``LocalEmbedder`` is the OSS default behind the ``Embedder`` /
``EmbeddingProvider`` interface. It satisfies the model-layer invariants for
Phase 1:

* **Fixed dimension** -- every embedding has exactly 384 dimensions
  (``dim == 384``), matching the ``vector(384)`` storage column.
* **Determinism per model version** -- identical input under a fixed
  ``model_version`` always yields a byte-identical vector.
* **Local-first / offline** -- the default backend depends only on the Python
  standard library (``hashlib`` + ``math``) and makes **no** network or external
  API call.

Backends
--------
* ``"hash"`` (default): a dependency-free, deterministic feature-hashing
  embedder. Token n-grams are hashed into a fixed-width float vector which is
  then L2-normalized. This guarantees determinism, offline operation, and a
  fixed dimension without requiring heavy ML libraries, so Phase 1 runs fully
  offline (including tests).
* ``"sentence-transformers"`` (optional): used only when explicitly requested
  *and* the ``sentence-transformers`` package is installed. The import is gated
  so that importing this module never fails when the optional dependency is
  absent.

``LocalEmbedder`` is the **OSS default** local embedder. A higher-fidelity
sentence-transformer / ONNX embedder can be swapped in behind this same
``Embedder`` / ``EmbeddingProvider`` interface later, selected by the model
layer's ``advanced_embeddings`` feature flag (see
``memoryguard_models.loader.TASK_COMMERCIAL_FLAGS``) — no change to the core or
to this contract is required.

Registry + serving integration
-------------------------------
This module also provides helpers so the OSS default embedder can be made
discoverable through the model layer:

* :meth:`LocalEmbedder.to_model_version` builds the embedder's
  :class:`~memoryguard_models.versioning.ModelVersion` registry entry.
* :meth:`LocalEmbedder.register` registers that ``ModelVersion`` in a
  :class:`~memoryguard_models.registry.LocalFileModelRegistry` and, optionally,
  registers the embedder as an in-process callable with a
  ``LocalInferenceRunner``-style serving runner so the embedder can be resolved
  and served entirely on-device with no artifact files.
* :func:`register_local_embedder` is a module-level convenience wrapping the
  above for the OSS composition root.

The serving runner is accepted *structurally* (it only needs a
``register_callable(model_id, fn, version=...)`` method): ``packages/models``
never imports ``packages/model-serving`` so the model layer stays
dependency-light and the open-core boundary is preserved.

Requirements: 21.2 (exactly 384-dim, on-device, no external API), 21.3
(determinism per model version), 21.4 (embeddings associated with the producing
``model_version``), 3.6, 4.2.
"""

from __future__ import annotations

import hashlib
import math
import re
from typing import TYPE_CHECKING, Any, Optional, Protocol

from memoryguard_models.base import Embedder, ModelInfo
from memoryguard_models.versioning import ModelArtifact, ModelVersion

if TYPE_CHECKING:  # pragma: no cover - typing only
    from memoryguard_models.registry import LocalFileModelRegistry


class _SupportsRegisterCallable(Protocol):
    """Structural type for a serving runner that accepts in-process callables.

    ``LocalInferenceRunner`` (in ``packages/model-serving``) satisfies this. It
    is referenced structurally so ``packages/models`` never imports the serving
    package, keeping the model layer dependency-light.
    """

    def register_callable(
        self, model_id: str, fn: Any, *, version: Optional[str] = None
    ) -> None: ...

# ---------------------------------------------------------------------------
# Module-level identity constants
# ---------------------------------------------------------------------------

#: The fixed embedding dimensionality for the OSS default embedder. Matches the
#: ``vector(384)`` column in the store schema.
DEFAULT_EMBED_DIM = 384

#: Stable model id for the default deterministic hash backend.
DEFAULT_MODEL_ID = "embedder/hash-minilm"

#: Semver for the default deterministic hash backend.
DEFAULT_VERSION = "1.0.0"

#: ``model_version`` string pinning vector compatibility for the default backend.
DEFAULT_MODEL_VERSION = f"{DEFAULT_MODEL_ID}@{DEFAULT_VERSION}"

#: Input-dict key the serving adapter (:meth:`LocalEmbedder.predict`) reads text
#: from when the embedder is served via ``LocalInferenceRunner``.
EMBED_INPUT_KEY = "text"

#: Number of independent hash projections accumulated per token n-gram. More
#: projections reduce index collisions and make distinct inputs differ more
#: reliably, at a small constant cost.
_HASHES_PER_GRAM = 3

#: Maximum n-gram length (unigrams + bigrams by default) used as features.
_MAX_NGRAM = 2

#: Tokenizer: lowercase alphanumeric runs. Deterministic and dependency-free.
_TOKEN_RE = re.compile(r"[a-z0-9]+")


def _tokens(text: str) -> list[str]:
    """Tokenize ``text`` into lowercase alphanumeric tokens (deterministic)."""
    return _TOKEN_RE.findall(text.lower())


def _ngrams(tokens: list[str], max_n: int = _MAX_NGRAM) -> list[str]:
    """Return unigrams through ``max_n``-grams for ``tokens`` (order-stable)."""
    grams: list[str] = list(tokens)
    for n in range(2, max_n + 1):
        for i in range(len(tokens) - n + 1):
            grams.append(" ".join(tokens[i : i + n]))
    return grams


class LocalEmbedder(Embedder):
    """Default OSS on-device embedder (384-dim, deterministic, offline).

    Args:
        model_id: stable model identifier (default ``"embedder/hash-minilm"``).
        version: semver string for the model (default ``"1.0.0"``).
        dim: embedding dimensionality; must be ``384`` for the default backend
            to honor the store schema (kept configurable for future backends).
        backend: ``"hash"`` (default, dependency-free) or
            ``"sentence-transformers"`` (optional, requires the package).
        sentence_transformer_model: model name to load when
            ``backend == "sentence-transformers"`` (e.g.
            ``"sentence-transformers/all-MiniLM-L6-v2"``).
        hashes_per_gram: number of hash projections per token n-gram (hash
            backend only).

    The default construction (no args) yields the deterministic hash backend,
    which is the path exercised by tests so Phase 1 runs without network or ML
    dependencies.
    """

    def __init__(
        self,
        *,
        model_id: str = DEFAULT_MODEL_ID,
        version: str = DEFAULT_VERSION,
        dim: int = DEFAULT_EMBED_DIM,
        backend: str = "hash",
        sentence_transformer_model: Optional[str] = None,
        hashes_per_gram: int = _HASHES_PER_GRAM,
    ) -> None:
        if dim <= 0:
            raise ValueError("dim must be a positive integer")
        if hashes_per_gram <= 0:
            raise ValueError("hashes_per_gram must be a positive integer")
        if backend not in ("hash", "sentence-transformers"):
            raise ValueError(
                "backend must be 'hash' or 'sentence-transformers', "
                f"got {backend!r}"
            )

        self._model_id = model_id
        self._version = version
        self._dim = dim
        self._backend = backend
        self._hashes_per_gram = hashes_per_gram
        self._st_model_name = sentence_transformer_model
        self._st_model = None  # lazily loaded sentence-transformers handle

        if backend == "sentence-transformers":
            self._init_sentence_transformers()

    # -- optional backend --------------------------------------------------

    def _init_sentence_transformers(self) -> None:
        """Load the optional ``sentence-transformers`` backend.

        Gated so that this module imports cleanly without the dependency. Only
        invoked when ``backend == "sentence-transformers"`` is explicitly
        requested; raises a clear error if the package is missing.
        """
        if not self._st_model_name:
            raise ValueError(
                "sentence_transformer_model is required when "
                "backend='sentence-transformers'"
            )
        try:
            from sentence_transformers import SentenceTransformer  # type: ignore
        except ImportError as exc:  # pragma: no cover - optional dependency path
            raise ImportError(
                "The 'sentence-transformers' package is not installed. Install "
                "it to use backend='sentence-transformers', or use the default "
                "backend='hash' (no extra dependencies)."
            ) from exc

        model = SentenceTransformer(self._st_model_name)  # pragma: no cover
        st_dim = model.get_sentence_embedding_dimension()  # pragma: no cover
        if st_dim != self._dim:  # pragma: no cover
            raise ValueError(
                f"sentence-transformers model {self._st_model_name!r} produces "
                f"{st_dim}-dim embeddings, expected {self._dim}."
            )
        self._st_model = model  # pragma: no cover

    # -- Embedder interface ------------------------------------------------

    def embed(self, text: str) -> list[float]:
        """Return the L2-normalized embedding vector for ``text``.

        Deterministic for identical ``text`` under a fixed ``model_version``.
        The returned list always has exactly ``self.dim`` elements.
        """
        if not isinstance(text, str):
            raise TypeError(f"text must be a str, got {type(text).__name__}")

        if self._backend == "sentence-transformers":
            return self._embed_sentence_transformers(text)  # pragma: no cover
        return self._embed_hash(text)

    def _embed_hash(self, text: str) -> list[float]:
        """Deterministic feature-hashing embedding (stdlib only)."""
        vec = [0.0] * self._dim
        prefix = f"{self.model_version}\x00".encode("utf-8")
        for gram in _ngrams(_tokens(text)):
            gram_bytes = gram.encode("utf-8")
            for k in range(self._hashes_per_gram):
                digest = hashlib.sha256(
                    prefix + k.to_bytes(2, "big") + b"\x00" + gram_bytes
                ).digest()
                idx = int.from_bytes(digest[0:4], "big") % self._dim
                sign = 1.0 if (digest[4] & 1) else -1.0
                vec[idx] += sign
        return _l2_normalize(vec)

    def _embed_sentence_transformers(self, text: str) -> list[float]:  # pragma: no cover
        """Embed via the optional sentence-transformers backend."""
        assert self._st_model is not None
        raw = self._st_model.encode(text, normalize_embeddings=True)
        return [float(x) for x in list(raw)]

    @property
    def dim(self) -> int:
        """Embedding dimensionality (384 for the OSS default)."""
        return self._dim

    @property
    def model_version(self) -> str:
        """``"<model_id>@<version>"``; pins vector compatibility."""
        return f"{self._model_id}@{self._version}"

    @property
    def info(self) -> ModelInfo:
        """Stable identity + version for reproducibility (``task="embed"``)."""
        return ModelInfo(model_id=self._model_id, task="embed", version=self._version)

    # -- registry helper ---------------------------------------------------

    def model_version_metadata(self) -> dict:
        """Return a registry-friendly metadata dict for this embedder.

        Identifying fields useful for constructing/registering a
        ``ModelVersion`` (or for diagnostics) without requiring the caller to
        import ``versioning``. See :meth:`to_model_version` for the full
        registry entry.
        """
        return {
            "model_id": self._model_id,
            "task": "embed",
            "version": self._version,
            "model_version": self.model_version,
            "dim": self._dim,
            "backend": self._backend,
        }

    # -- model-layer serving adapter --------------------------------------

    def predict(self, item: dict) -> dict:
        """Serve one embedding as a ``LocalInferenceRunner`` callable.

        Maps an input dict ``{"text": <str>}`` to an output dict
        ``{"embedding": [...], "dim": <int>, "model_version": <str>}``. This is
        the per-input ``predict_fn`` contract used by ``LocalInferenceRunner``
        so the embedder can be served on-device with no artifact files.

        Args:
            item: an input dict carrying the text under the ``"text"`` key.

        Raises:
            TypeError: if ``item`` is not a dict.
            KeyError: if ``item`` has no ``"text"`` key.
        """
        if not isinstance(item, dict):
            raise TypeError(f"item must be a dict, got {type(item).__name__}")
        if EMBED_INPUT_KEY not in item:
            raise KeyError(
                f"embed input dict requires a {EMBED_INPUT_KEY!r} key; "
                f"got keys {sorted(item)!r}"
            )
        return {
            "embedding": self.embed(item[EMBED_INPUT_KEY]),
            "dim": self._dim,
            "model_version": self.model_version,
        }

    # -- registry / serving registration ----------------------------------

    def to_model_version(self, *, metrics: Optional[dict] = None) -> ModelVersion:
        """Build the :class:`ModelVersion` registry entry for this embedder.

        The OSS default embedder is an in-process model (no artifact file): its
        ``ModelArtifact`` uses an ``inproc://`` URI and a deterministic
        ``sha256`` over the ``model_version`` identity. The ``LocalInferenceRunner``
        resolves it via the in-process callable registered by :meth:`register`
        (the URI is never fetched; no network access is performed).

        Args:
            metrics: optional eval-metrics snapshot to attach (merged over the
                default identifying metrics).
        """
        identity = self.model_version.encode("utf-8")
        sha256 = hashlib.sha256(identity).hexdigest()
        default_metrics: dict[str, Any] = {
            "dim": self._dim,
            "backend": self._backend,
            "deterministic": True,
            "on_device": True,
            "external_api": False,
        }
        if metrics:
            default_metrics.update(metrics)
        return ModelVersion(
            model_id=self._model_id,
            task="embed",
            version=self._version,
            metrics=default_metrics,
            artifact=ModelArtifact(
                artifact_uri=f"inproc://{self.model_version}",
                sha256=sha256,
                size_bytes=len(identity),
            ),
        )

    def register(
        self,
        registry: "LocalFileModelRegistry",
        runner: Optional[_SupportsRegisterCallable] = None,
        *,
        metrics: Optional[dict] = None,
    ) -> ModelVersion:
        """Register this embedder in ``registry`` (and optionally a serving ``runner``).

        Persists the embedder's :class:`ModelVersion` in the
        ``LocalFileModelRegistry`` so it is resolvable by ``model_id`` /
        ``model_version`` (making the OSS default embedder discoverable through
        the model layer). When a ``runner`` is supplied, the embedder is also
        registered as an in-process callable (version-pinned) so
        ``runner.load(mv)`` serves it on-device with no artifact files.

        Args:
            registry: the local file registry to register the ``ModelVersion`` in.
            runner: optional ``LocalInferenceRunner``-style serving runner. Only
                a ``register_callable(model_id, fn, version=...)`` method is
                required; the serving package is never imported here.
            metrics: optional eval-metrics snapshot for the ``ModelVersion``.

        Returns:
            The registered :class:`ModelVersion`.
        """
        mv = self.to_model_version(metrics=metrics)
        registry.register(mv)
        if runner is not None:
            runner.register_callable(self._model_id, self.predict, version=self._version)
        return mv


def _l2_normalize(vec: list[float]) -> list[float]:
    """Return the L2-normalized copy of ``vec`` (zero vector stays zero)."""
    norm = math.sqrt(sum(v * v for v in vec))
    if norm == 0.0:
        return vec
    return [v / norm for v in vec]


def register_local_embedder(
    registry: "LocalFileModelRegistry",
    runner: Optional[_SupportsRegisterCallable] = None,
    *,
    embedder: Optional[LocalEmbedder] = None,
    metrics: Optional[dict] = None,
) -> ModelVersion:
    """Register the OSS default embedder in ``registry`` (+ optional ``runner``).

    Convenience for the OSS composition root: ensures the ``LocalFileModelRegistry``
    holds the OSS default embedder's :class:`ModelVersion`, and—when a serving
    ``runner`` is supplied—that the runner can serve it on-device with no
    artifact files.

    Args:
        registry: the local file registry to register into.
        runner: optional ``LocalInferenceRunner``-style serving runner.
        embedder: the embedder to register (defaults to a fresh
            :class:`LocalEmbedder` with the OSS hash backend).
        metrics: optional eval-metrics snapshot for the ``ModelVersion``.

    Returns:
        The registered :class:`ModelVersion`.
    """
    emb = embedder if embedder is not None else LocalEmbedder()
    return emb.register(registry, runner, metrics=metrics)


__all__ = [
    "LocalEmbedder",
    "register_local_embedder",
    "DEFAULT_EMBED_DIM",
    "DEFAULT_MODEL_ID",
    "DEFAULT_VERSION",
    "DEFAULT_MODEL_VERSION",
    "EMBED_INPUT_KEY",
]
