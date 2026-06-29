# SPDX-License-Identifier: Apache-2.0
"""MemoryGuard Retrieval & Policy Layer (OSS core).

Implements the design's *two-stage pipeline*:

* **Stage 1 — hybrid candidate gathering** (``hybrid.py``): union of semantic
  (vector), keyword (full-text), and recency signals, blended into a first-stage
  score and de-duplicated by ``memory_id``.
* **Policy + ingestion injection interfaces** (``policy_filter.py``): the
  ``PolicyProvider`` / ``IngestionInspector`` contracts and their OSS defaults.
* **Final-rank blend** (``ranker.py``) + **Stage-2 orchestration / policy
  filtering** (``service.py``): the ``RetrievalService.query`` pipeline reranks
  candidates, applies the policy filter, computes ``final_rank``, orders results
  descending, attaches per-result reasons, and emits one audit event.

Exports are merged additively across tasks: each task that lands a module in this
package appends its public symbols here without removing those added by another.
"""

from __future__ import annotations

from memoryguard_core.retrieval.hybrid import (
    Candidate,
    DimensionMismatchError,
    W_KEYWORD,
    W_RECENCY,
    W_VECTOR,
    gather_candidates,
)
from memoryguard_core.retrieval.policy_filter import (
    AllowAllPolicy,
    IngestionInspector,
    NoOpInspector,
    PolicyProvider,
)
from memoryguard_core.retrieval.ranker import (
    DEFAULT_RANK_WEIGHTS,
    RankWeights,
    final_rank,
)
from memoryguard_core.retrieval.service import (
    QuerySpec,
    RetrievalService,
    RetrievedMemory,
)

__all__ = [
    # Stage 1 — hybrid candidate gathering (task 10.2)
    "gather_candidates",
    "Candidate",
    "DimensionMismatchError",
    "W_VECTOR",
    "W_KEYWORD",
    "W_RECENCY",
    # Policy + ingestion injection interfaces (task 10.1)
    "PolicyProvider",
    "AllowAllPolicy",
    "IngestionInspector",
    "NoOpInspector",
    # Final-rank blend (task 10.3)
    "final_rank",
    "RankWeights",
    "DEFAULT_RANK_WEIGHTS",
    # Stage 2 + policy orchestration (task 10.3)
    "RetrievalService",
    "QuerySpec",
    "RetrievedMemory",
]
