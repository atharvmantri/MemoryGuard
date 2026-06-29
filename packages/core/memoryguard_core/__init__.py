# SPDX-License-Identifier: Apache-2.0
"""MemoryGuard OSS core engine.

The heart of MemoryGuard: memory records, the memory store, the trust engine,
the retrieval & policy layer, and ingestion. This package is Apache-2.0 and MUST
NOT import from any commercial package.
"""

from .flags import FeatureFlags
from .engine import MemoryGuardEngine
from .bootstrap import build_local_engine
from .models import (
    SCOPE_REF_REQUIRED,
    MemoryRecord,
    MemoryStatus,
    Scope,
    Sensitivity,
    SourceType,
    ValidationError,
    clamp_trust_score,
    new_memory_record,
    validate,
)
from .context_sync import (
    CONTEXT_FILES,
    ContextFilePlan,
    ContextSyncPlan,
    ContextSyncStatus,
    approve_context_sync,
    build_context_sync_plan,
    context_status,
    format_unified_diff,
    write_pending_context_plan,
)
from .capture import (
    CaptureCandidate,
    CaptureStatus,
    approve_all_safe_candidates,
    approve_candidate,
    clear_rejected_candidates,
    extract_candidates,
    ingest_capture_file,
    list_candidates,
    reject_candidate,
)

__version__ = "0.1.0"

__all__ = [
    "SourceType",
    "Scope",
    "Sensitivity",
    "MemoryStatus",
    "MemoryRecord",
    "ValidationError",
    "validate",
    "new_memory_record",
    "clamp_trust_score",
    "SCOPE_REF_REQUIRED",
    "FeatureFlags",
    "MemoryGuardEngine",
    "build_local_engine",
    "CONTEXT_FILES",
    "ContextFilePlan",
    "ContextSyncPlan",
    "ContextSyncStatus",
    "approve_context_sync",
    "build_context_sync_plan",
    "context_status",
    "format_unified_diff",
    "write_pending_context_plan",
    "CaptureCandidate",
    "CaptureStatus",
    "extract_candidates",
    "ingest_capture_file",
    "list_candidates",
    "approve_candidate",
    "approve_all_safe_candidates",
    "reject_candidate",
    "clear_rejected_candidates",
]
