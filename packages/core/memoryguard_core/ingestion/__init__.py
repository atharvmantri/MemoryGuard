# SPDX-License-Identifier: Apache-2.0
"""MemoryGuard Ingestion Layer (OSS core).

Accepts memories from users, files, folders, and git repositories; normalizes
content; attaches provenance + lifecycle metadata; chunks content; and embeds
each record via the configured ``Embedder``. Optional
:class:`~memoryguard_core.retrieval.policy_filter.IngestionInspector` hooks run at
ingestion time (commercial poison/PII detection plugs in here).

This package is Apache-2.0 and MUST NOT import from any commercial package.
"""

from __future__ import annotations

from memoryguard_core.ingestion.chunking import (
    DEFAULT_MAX_CHARS,
    DEFAULT_OVERLAP,
    chunk_text,
)
from memoryguard_core.ingestion.ingest import (
    DEFAULT_TEXT_EXTENSIONS,
    IngestionFailure,
    Ingestor,
    add_memory,
    ingest_file,
    ingest_folder,
)
from memoryguard_core.ingestion.repo_ingest import (
    WORKTREE_REF,
    ingest_repo,
    read_commit,
)

__all__ = [
    # chunking
    "chunk_text",
    "DEFAULT_MAX_CHARS",
    "DEFAULT_OVERLAP",
    # manual add + file/folder ingestion
    "add_memory",
    "ingest_file",
    "ingest_folder",
    "Ingestor",
    "IngestionFailure",
    "DEFAULT_TEXT_EXTENSIONS",
    # repo ingestion
    "ingest_repo",
    "read_commit",
    "WORKTREE_REF",
]

# Merge-safe re-export of the inspector composition helper from task 12.3, which
# may land concurrently as ``inspectors.py``. Import it when present without
# failing if it is not yet available.
try:  # pragma: no cover - presence depends on concurrent task 12.3
    from memoryguard_core.ingestion.inspectors import (  # type: ignore  # noqa: F401
        CompositeIngestionInspector,
    )

    __all__.append("CompositeIngestionInspector")
except ImportError:
    pass
