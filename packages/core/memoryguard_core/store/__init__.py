# SPDX-License-Identifier: Apache-2.0
"""MemoryGuard memory store package.

Exposes the abstract :class:`MemoryStore` backend contract. Concrete backends
honor this contract:

* :class:`SqliteStore` — local mode (Phase 1), standard-library only.
* :class:`PostgresStore` — cloud mode (PostgreSQL + pgvector), selected behind
  the ``cloud_store`` feature flag.

Importing :class:`PostgresStore` here is safe even when its third-party
``psycopg`` driver is not installed: the driver is imported lazily only when a
``PostgresStore`` is constructed, so the OSS core (and the SQLite backend)
remain usable with no PostgreSQL dependency.
"""

from __future__ import annotations

from memoryguard_core.store.base import MemoryStore
from memoryguard_core.store.pg_store import PostgresStore
from memoryguard_core.store.sqlite_store import SqliteStore

__all__ = ["MemoryStore", "SqliteStore", "PostgresStore"]
