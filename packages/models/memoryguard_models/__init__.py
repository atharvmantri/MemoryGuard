# SPDX-License-Identifier: Apache-2.0
"""MemoryGuard model layer for the OSS public alpha.

Stable interfaces plus local deterministic model implementations used by the
MemoryGuard CLI and core engine.
"""

from memoryguard_models.base import (
    ContradictionClassifier,
    ContradictionModel,
    ContradictionResult,
    Embedder,
    EmbeddingProvider,
    InferenceRunner,
    LoadedModel,
    MemoryGuardModel,
    ModelInfo,
    ModelRegistry,
    ModelServingAPI,
    PoisonDetector,
    PoisonResult,
    Reranker,
    RerankResult,
    SensitiveDataDetector,
    SensitiveResult,
    TrustModel,
    TrustScorer,
    TrustSignals,
)
from memoryguard_models.embedder import LocalEmbedder, register_local_embedder
from memoryguard_models.loader import ModelLoader
from memoryguard_models.poison_detector import BasicPoisonDetector
from memoryguard_models.registry import LocalFileModelRegistry
from memoryguard_models.reranker import HeuristicReranker
from memoryguard_models.sensitive_data import BasicSensitiveDataDetector
from memoryguard_models.versioning import ModelArtifact, ModelVersion

__version__ = "0.1.0"

__all__ = [
    "ModelInfo",
    "MemoryGuardModel",
    "Embedder",
    "EmbeddingProvider",
    "LocalEmbedder",
    "register_local_embedder",
    "RerankResult",
    "Reranker",
    "HeuristicReranker",
    "ContradictionResult",
    "ContradictionModel",
    "ContradictionClassifier",
    "PoisonResult",
    "PoisonDetector",
    "BasicPoisonDetector",
    "SensitiveResult",
    "SensitiveDataDetector",
    "BasicSensitiveDataDetector",
    "TrustSignals",
    "TrustModel",
    "TrustScorer",
    "LoadedModel",
    "InferenceRunner",
    "ModelServingAPI",
    "ModelRegistry",
    "ModelArtifact",
    "ModelVersion",
    "LocalFileModelRegistry",
    "ModelLoader",
]
