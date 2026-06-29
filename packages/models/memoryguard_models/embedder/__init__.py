# SPDX-License-Identifier: Apache-2.0
"""OSS embedder integration for MemoryGuard.

Exposes the on-device :class:`LocalEmbedder`, the default ``Embedder`` /
``EmbeddingProvider`` implementation. The default backend is a dependency-free,
deterministic feature-hashing embedder that runs fully offline and produces
exactly 384-dimensional vectors. An optional ``sentence-transformers`` backend
may be used when explicitly configured and installed, but it is never required
to import this package.

Requirements: 21.2, 21.3, 21.4, 3.6, 4.2.
"""

from memoryguard_models.embedder.local_embedder import (
    DEFAULT_EMBED_DIM,
    DEFAULT_MODEL_ID,
    DEFAULT_MODEL_VERSION,
    EMBED_INPUT_KEY,
    LocalEmbedder,
    register_local_embedder,
)

__all__ = [
    "LocalEmbedder",
    "register_local_embedder",
    "DEFAULT_EMBED_DIM",
    "DEFAULT_MODEL_ID",
    "DEFAULT_MODEL_VERSION",
    "EMBED_INPUT_KEY",
]
