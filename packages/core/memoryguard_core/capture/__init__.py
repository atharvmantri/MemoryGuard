# SPDX-License-Identifier: Apache-2.0
"""Local-first agent transcript capture for MemoryGuard."""

from .models import CaptureCandidate, CaptureStatus
from .pipeline import extract_candidates
from .queue import (
    approve_all_safe_candidates,
    approve_candidate,
    clear_rejected_candidates,
    ingest_capture_file,
    list_candidates,
    reject_candidate,
)

__all__ = [
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
