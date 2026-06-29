# SPDX-License-Identifier: Apache-2.0
"""Project-local pending approval queue for capture candidates."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

from memoryguard_core.capture.models import CaptureCandidate, CaptureStatus, utcnow
from memoryguard_core.capture.pipeline import extract_candidates
from memoryguard_core.decisions import extract_decision
from memoryguard_core.engine import MemoryGuardEngine
from memoryguard_core.models import MemoryStatus, Scope, Sensitivity, SourceType
from memoryguard_core.secrets import contains_secret

__all__ = [
    "ingest_capture_file",
    "list_candidates",
    "approve_candidate",
    "approve_all_safe_candidates",
    "reject_candidate",
    "clear_rejected_candidates",
]

QUEUE_REL = Path(".memoryguard") / "capture" / "candidates.json"


def ingest_capture_file(
    root: Path,
    path: Path,
    *,
    source_type: str,
) -> list[CaptureCandidate]:
    """Extract candidates from ``path`` and persist them as pending."""

    root = root.expanduser().resolve()
    path = path.expanduser().resolve()
    text = path.read_text(encoding="utf-8", errors="replace")
    try:
        rel = path.relative_to(root)
        source_ref = str(rel).replace("\\", "/")
    except ValueError:
        source_ref = str(path)
    candidates = extract_candidates(text, source_type=source_type, source_ref=source_ref)
    existing = _read_queue(root)
    existing.extend(candidates)
    _write_queue(root, existing)
    return candidates


def list_candidates(
    root: Path,
    *,
    status: Optional[CaptureStatus] = None,
) -> list[CaptureCandidate]:
    """List locally persisted capture candidates."""

    candidates = _read_queue(root)
    if status is None:
        return candidates
    return [candidate for candidate in candidates if candidate.status == status]


def approve_candidate(
    root: Path,
    engine: MemoryGuardEngine,
    candidate_id: str,
    *,
    scope_ref: str,
) -> tuple[CaptureCandidate, Optional[str]]:
    """Approve one pending candidate into normal MemoryGuard memory."""

    candidates = _read_queue(root)
    candidate = _find(candidates, candidate_id)
    if candidate.status != CaptureStatus.PENDING:
        raise ValueError(f"candidate {candidate_id!r} is not pending")
    if candidate.sensitivity in {Sensitivity.SECRET, Sensitivity.PII} or contains_secret(
        candidate.content
    ):
        raise ValueError(f"candidate {candidate_id!r} is sensitive and cannot be approved")

    memory_id: Optional[str] = None
    if candidate.metadata.get("capture_action") == "mark_outdated":
        _mark_matching_memories_outdated(engine, str(candidate.value or ""))
    else:
        record = engine.create_memory(
            content=candidate.content,
            source_type=SourceType.FILE,
            source_ref=candidate.source_ref,
            scope=Scope.PROJECT,
            scope_ref=scope_ref,
            sensitivity=Sensitivity.INTERNAL,
            tags=["capture"],
        )
        record.metadata["capture_candidate_id"] = candidate.id
        record.metadata["capture_source_type"] = candidate.source_type
        record.metadata["capture_evidence"] = candidate.evidence
        if candidate.decision_key:
            record.metadata["decision_key"] = candidate.decision_key
        if candidate.value:
            record.metadata["decision_value"] = candidate.value
        if candidate.supersedes_value:
            record.metadata["supersedes_value"] = candidate.supersedes_value
        engine.store.update(record)
        memory_id = record.memory_id

    candidate.status = CaptureStatus.APPROVED
    candidate.metadata["approved_at"] = utcnow().isoformat()
    if memory_id is not None:
        candidate.metadata["memory_id"] = memory_id
    _write_queue(root, candidates)
    return candidate, memory_id


def approve_all_safe_candidates(
    root: Path,
    engine: MemoryGuardEngine,
    *,
    scope_ref: str,
    min_confidence: float = 0.70,
) -> list[tuple[CaptureCandidate, Optional[str]]]:
    """Approve pending non-sensitive candidates above the confidence threshold."""

    approved: list[tuple[CaptureCandidate, Optional[str]]] = []
    for candidate in list_candidates(root, status=CaptureStatus.PENDING):
        if candidate.confidence < min_confidence:
            continue
        if candidate.sensitivity in {Sensitivity.SECRET, Sensitivity.PII}:
            continue
        if contains_secret(candidate.content) or contains_secret(candidate.evidence):
            continue
        approved.append(
            approve_candidate(root, engine, candidate.id, scope_ref=scope_ref)
        )
    return approved


def reject_candidate(root: Path, candidate_id: str) -> CaptureCandidate:
    """Reject one pending candidate."""

    candidates = _read_queue(root)
    candidate = _find(candidates, candidate_id)
    candidate.status = CaptureStatus.REJECTED
    candidate.metadata["rejected_at"] = utcnow().isoformat()
    _write_queue(root, candidates)
    return candidate


def clear_rejected_candidates(root: Path) -> int:
    """Delete rejected candidates from the local queue."""

    candidates = _read_queue(root)
    kept = [candidate for candidate in candidates if candidate.status != CaptureStatus.REJECTED]
    removed = len(candidates) - len(kept)
    _write_queue(root, kept)
    return removed


def _read_queue(root: Path) -> list[CaptureCandidate]:
    path = _queue_path(root)
    if not path.is_file():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return []
    return [CaptureCandidate.from_json(item) for item in data.get("candidates", [])]


def _write_queue(root: Path, candidates: list[CaptureCandidate]) -> None:
    path = _queue_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"candidates": [candidate.to_json() for candidate in candidates]}
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _queue_path(root: Path) -> Path:
    return root.expanduser().resolve() / QUEUE_REL


def _find(candidates: list[CaptureCandidate], candidate_id: str) -> CaptureCandidate:
    for candidate in candidates:
        if candidate.id == candidate_id:
            return candidate
    raise KeyError(f"unknown capture candidate {candidate_id!r}")


def _mark_matching_memories_outdated(engine: MemoryGuardEngine, value: str) -> None:
    if not value:
        return
    needle = value.lower()
    now = utcnow()
    for record in engine.store.list():
        decision = extract_decision(record.content)
        content_match = needle in record.content.lower()
        decision_match = decision is not None and decision.value.lower() == needle
        if not content_match and not decision_match:
            continue
        if record.status == MemoryStatus.DELETED:
            continue
        record.status = MemoryStatus.OUTDATED
        record.metadata["outdated_by_capture"] = True
        record.updated_at = max(record.updated_at, now)
        record.trust_score = engine.trust_engine.score(record, now)
        engine.store.update(record)
