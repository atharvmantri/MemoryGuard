# SPDX-License-Identifier: Apache-2.0
"""MemoryGuard OSS Python SDK.

``MemoryGuard.local()`` talks directly to the core engine; ``MemoryGuard.remote()``
talks to the REST API with an optional bearer token. Both expose the same
conceptual surface — ``add``, ``get``, ``query``, ``ingest_path``, ``correct``,
``delete``, ``contradictions`` — and return results carrying ``content``,
``trust_score``, ``source_ref`` and ``reasons`` (Requirements 11.1–11.4).

The core enums :class:`Scope`, :class:`SourceType`, and :class:`Sensitivity` are
re-exported for convenience::

    from memoryguard import MemoryGuard, Scope, SourceType, Sensitivity

This package is Apache-2.0 OSS.
"""

from memoryguard_core import Scope, Sensitivity, SourceType

from .client import MemoryGuard
from .models import Contradiction, Memory, QueryResult
from .remote import RemoteError

__version__ = "0.1.0"

__all__ = [
    "MemoryGuard",
    "Memory",
    "QueryResult",
    "Contradiction",
    "RemoteError",
    "Scope",
    "SourceType",
    "Sensitivity",
]
