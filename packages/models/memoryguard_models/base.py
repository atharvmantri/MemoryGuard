# SPDX-License-Identifier: Apache-2.0
"""MemoryGuard model-layer interfaces and result dataclasses (OSS).

This module defines the *stable* contracts for every task-specific intelligence
component MemoryGuard's core layers call into: embeddings, two-stage reranking,
contradiction classification, poison / sensitive-data inspection, trust scoring,
local inference serving, and the model registry.

Design invariants encoded here:

* Every model exposes a ``ModelInfo`` (``info`` property) for reproducibility.
* OSS defaults (rules / heuristics / small on-device models) satisfy these
  contracts with **no external LLM API**; learned / hosted commercial models are
  injected behind the *same* interfaces and gated by feature flags.
* ``packages/models`` imports only from ``packages/core`` (and the standard
  library); it never imports a commercial package.

``ModelVersion`` / ``ModelArtifact`` are defined in ``versioning.py`` (a later
task). To avoid a hard runtime dependency on that module while keeping accurate
type information, they are referenced here via ``TYPE_CHECKING`` + string
annotations only.

Public interface aliases (per design "Public Interface Names"):
``EmbeddingProvider`` -> ``Embedder``, ``ContradictionClassifier`` ->
``ContradictionModel``, ``TrustScorer`` -> ``TrustModel``,
``ModelServingAPI`` is the hosted-serving contract adapted by the
``InferenceRunner`` pipeline.

Requirements: 21.1, 22.2, 26.1, 28.4.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Any, Optional

if TYPE_CHECKING:  # pragma: no cover - typing only
    # ``MemoryRecord`` / ``Sensitivity`` are used solely in (string) annotations
    # here -- ``from __future__ import annotations`` keeps them lazy -- so they
    # are imported for typing only. Importing them at runtime would eagerly
    # trigger ``memoryguard_core``'s package initialization (engine -> trust ->
    # scoring -> back into this module), creating a circular import whenever
    # ``memoryguard_models`` is imported before ``memoryguard_core``.
    from memoryguard_core.models import MemoryRecord, Sensitivity

    # ``ModelVersion`` / ``ModelArtifact`` live in ``versioning.py``.
    from memoryguard_models.versioning import ModelVersion


# ---------------------------------------------------------------------------
# Shared model identity
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ModelInfo:
    """Stable identity + version for a model, enabling reproducibility.

    Attributes:
        model_id: e.g. ``"reranker/heuristic"``, ``"poison/rules"``.
        task: one of ``"embed" | "rerank" | "contradiction" | "poison" |
            "sensitive" | "trust"``.
        version: semver string, e.g. ``"1.0.0"``.
    """

    model_id: str
    task: str
    version: str


class MemoryGuardModel(ABC):
    """Base contract shared by every model-layer component."""

    @property
    @abstractmethod
    def info(self) -> ModelInfo:
        """Stable identity + version for reproducibility."""
        ...


# ---------------------------------------------------------------------------
# Embedder (alias: EmbeddingProvider)
# ---------------------------------------------------------------------------


class Embedder(ABC):
    """Converts text into a fixed-dimension vector embedding.

    The OSS default (``LocalEmbedder``) runs on-device with no external API and
    is deterministic for identical input within a given ``model_version``. The
    ``model_version`` pins vector compatibility so stored vectors can be matched
    to the model that produced them.
    """

    @abstractmethod
    def embed(self, text: str) -> list[float]:
        """Return the embedding vector for ``text``."""
        ...

    @property
    @abstractmethod
    def dim(self) -> int:
        """Embedding dimensionality (e.g. 384)."""
        ...

    @property
    @abstractmethod
    def model_version(self) -> str:
        """e.g. ``"embedder/minilm-l6@1.0.0"``; pins vector compatibility."""
        ...


#: Public interface alias. ``EmbeddingProvider`` refines/extends ``Embedder``;
#: the OSS default ``LocalEmbedder`` satisfies both names.
EmbeddingProvider = Embedder


# ---------------------------------------------------------------------------
# Reranker (Stage-2 ranking)
# ---------------------------------------------------------------------------


@dataclass
class RerankResult:
    """A reranker's judgement for a single candidate memory.

    Attributes:
        memory_id: id of the candidate memory being judged.
        relevance_score: 0..1 relevance to the query.
        trust_score: 0..1 trust judgement.
        should_use_memory: binary decision whether to surface the memory.
        reason: non-empty, short human-readable explanation.
    """

    memory_id: str
    relevance_score: float
    trust_score: float
    should_use_memory: bool
    reason: str


class Reranker(MemoryGuardModel):
    """Second-stage ranker over ``(query, candidate, ctx)``.

    The OSS default (``HeuristicReranker``) is a deterministic rule-based blend
    of similarity, source authority, freshness, contradictions, and sensitivity.
    A learned commercial reranker plugs in behind the same interface.
    """

    @abstractmethod
    def rerank(
        self,
        query: str,
        candidates: list[MemoryRecord],
        ctx: dict,
    ) -> list[RerankResult]:
        """Score each candidate and return per-candidate ``RerankResult``s."""
        ...


# ---------------------------------------------------------------------------
# Contradiction model (alias: ContradictionClassifier)
# ---------------------------------------------------------------------------


@dataclass
class ContradictionResult:
    """Outcome of comparing two memories for contradiction.

    Attributes:
        is_contradiction: whether the two memories conflict.
        reason: optional short explanation.
        confidence: 0..1 confidence in the decision.
    """

    is_contradiction: bool
    reason: Optional[str]
    confidence: float


class ContradictionModel(MemoryGuardModel):
    """Decides whether two memories conflict.

    Implementations MUST preserve the documented invariants: symmetry,
    irreflexivity, scope isolation, and bounded confidence. The OSS default
    (``RuleContradictionModel``) is the heuristic pipeline; a learned classifier
    swaps in behind the same interface and feeds the ``TrustEngine`` identically.
    """

    @abstractmethod
    def detect(
        self,
        candidate: MemoryRecord,
        existing: MemoryRecord,
    ) -> ContradictionResult:
        """Return whether ``candidate`` and ``existing`` contradict."""
        ...


#: Public interface alias for ``ContradictionModel``.
ContradictionClassifier = ContradictionModel


# ---------------------------------------------------------------------------
# Poison detector (also implements core IngestionInspector)
# ---------------------------------------------------------------------------


@dataclass
class PoisonResult:
    """Outcome of inspecting a memory for poisoning / injection.

    Attributes:
        is_poisoned: whether suspicious/malicious content was detected.
        categories: e.g. ``["prompt_injection", "unsafe_instruction",
            "poisoning"]``.
        severity: 0..1 severity magnitude.
        reason: short human-readable explanation.
    """

    is_poisoned: bool
    categories: list[str]
    severity: float
    reason: str


class PoisonDetector(MemoryGuardModel):
    """Detects prompt injection, unsafe instructions, and poisoning attempts.

    The OSS default (``BasicPoisonDetector``) uses rules/heuristics + known
    injection fixtures and runs locally with no external API. It also implements
    the core ``IngestionInspector`` so it runs at ingestion to flag content,
    downgrade trust, or route to review.
    """

    @abstractmethod
    def inspect_content(self, record: MemoryRecord) -> PoisonResult:
        """Inspect ``record`` and return a ``PoisonResult``."""
        ...


# ---------------------------------------------------------------------------
# Sensitive-data detector (also implements core IngestionInspector)
# ---------------------------------------------------------------------------


@dataclass
class SensitiveResult:
    """Outcome of inspecting a memory for secrets / PII / internal info.

    Attributes:
        has_sensitive: whether sensitive content was detected.
        detected: e.g. ``["aws_key", "email", "password"]``.
        suggested_sensitivity: tier to assign (e.g. ``Sensitivity.PII`` /
            ``Sensitivity.SECRET``).
        reason: short human-readable explanation.
    """

    has_sensitive: bool
    detected: list[str]
    suggested_sensitivity: Sensitivity
    reason: str


class SensitiveDataDetector(MemoryGuardModel):
    """Detects secrets, API keys, passwords, PII, and internal company info.

    The OSS default (``BasicSensitiveDataDetector``) uses regex/rules and runs
    locally with no external API. It also implements the core
    ``IngestionInspector`` and can elevate the ``sensitivity`` tier.
    """

    @abstractmethod
    def inspect_content(self, record: MemoryRecord) -> SensitiveResult:
        """Inspect ``record`` and return a ``SensitiveResult``."""
        ...


# ---------------------------------------------------------------------------
# Trust model (alias: TrustScorer)
# ---------------------------------------------------------------------------


@dataclass
class TrustSignals:
    """Normalized trust signals (each in ``[0, 1]``).

    Attributes:
        source_authority: by source_type + source_ref reputation.
        freshness: decays with age toward expiry.
        confirmation_score: grows with confirmations.
        contradiction_penalty: fraction of unresolved contradictions (penalty
            magnitude; enters the score as ``1 - penalty``).
        sensitivity_penalty: higher sensitivity -> larger penalty (enters as
            ``1 - penalty``).
        correction_signal: NEW. 0..1 effect of user corrections
            (supersede / affirm).
    """

    source_authority: float
    freshness: float
    confirmation_score: float
    contradiction_penalty: float
    sensitivity_penalty: float
    correction_signal: float


class TrustModel(MemoryGuardModel):
    """Computes a bounded, deterministic trust score from signals.

    Augments or replaces signal computation; output stays bounded in ``[0, 1]``
    and deterministic per model version. The OSS baseline
    (``DeterministicTrustModel``) is the documented weighted-sum formula; a
    learned commercial model can be blended or swapped behind the same contract
    while remaining bounded and monotonic w.r.t. the documented signal
    directions.
    """

    @abstractmethod
    def score(
        self,
        record: MemoryRecord,
        signals: TrustSignals,
        now: datetime,
    ) -> float:
        """Return the trust score in ``[0, 1]`` for ``record`` given signals."""
        ...


#: Public interface alias for ``TrustModel`` (the ``TrustEngine`` selects one).
TrustScorer = TrustModel


# ---------------------------------------------------------------------------
# Inference serving
# ---------------------------------------------------------------------------

#: A loaded, ready-to-run model handle. Opaque to the contract; concrete runners
#: define the actual representation. ``Any`` keeps the OSS interface flexible.
LoadedModel = Any


class InferenceRunner(ABC):
    """Loads and runs model artifacts behind a stable serving contract.

    The OSS default (``LocalInferenceRunner``) runs models on-device and MUST
    make no network calls (the no-external-LLM property). A commercial
    ``CloudInferenceAPI`` adapts a hosted serving API into this same pipeline.
    """

    @abstractmethod
    def load(self, mv: "ModelVersion") -> "LoadedModel":
        """Load the artifact described by ``mv`` and return a runnable handle."""
        ...

    @abstractmethod
    def run(self, loaded: "LoadedModel", inputs: list[dict]) -> list[dict]:
        """Run ``loaded`` over ``inputs`` and return per-input output dicts."""
        ...


class ModelServingAPI(ABC):
    """Cloud model-serving API CONTRACT for hosted advanced models.

    Interface defined now (OSS, in ``packages/model-serving``); the real hosted
    implementation arrives in the cloud phase (flag: ``model_serving``) and is
    adapted by ``CloudInferenceAPI``. NEVER required for core behavior --
    ``LocalInferenceRunner`` serves every OSS model offline.
    """

    @abstractmethod
    def predict(
        self,
        task: str,
        model_id: str,
        version: str | None,
        inputs: list[dict],
    ) -> list[dict]:
        """Serve a prediction for the named model/version over ``inputs``."""
        ...

    @abstractmethod
    def health(self) -> dict:
        """Return serving liveness/readiness + served model versions."""
        ...


# ---------------------------------------------------------------------------
# Model registry
# ---------------------------------------------------------------------------


class ModelRegistry(ABC):
    """Resolves model ids/versions to concrete ``ModelVersion`` records.

    The OSS default (``LocalFileModelRegistry``) is a JSON index on disk with
    deterministic resolution and no network access. ``resolve`` with
    ``version=None`` returns the latest registered version deterministically
    (highest semver).
    """

    @abstractmethod
    def register(self, mv: "ModelVersion") -> None:
        """Register a ``ModelVersion`` in the index."""
        ...

    @abstractmethod
    def resolve(self, model_id: str, version: str | None = None) -> "ModelVersion":
        """Resolve ``model_id`` (latest if ``version is None``) to a ``ModelVersion``."""
        ...


__all__ = [
    "ModelInfo",
    "MemoryGuardModel",
    "Embedder",
    "EmbeddingProvider",
    "RerankResult",
    "Reranker",
    "ContradictionResult",
    "ContradictionModel",
    "ContradictionClassifier",
    "PoisonResult",
    "PoisonDetector",
    "SensitiveResult",
    "SensitiveDataDetector",
    "TrustSignals",
    "TrustModel",
    "TrustScorer",
    "LoadedModel",
    "InferenceRunner",
    "ModelServingAPI",
    "ModelRegistry",
]
