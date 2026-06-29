# SPDX-License-Identifier: Apache-2.0
"""Tests for the Agent Capture MVP."""

from __future__ import annotations

from memoryguard_core import Scope, SourceType, build_local_engine
from memoryguard_core.capture import (
    CaptureStatus,
    approve_all_safe_candidates,
    approve_candidate,
    extract_candidates,
    ingest_capture_file,
    list_candidates,
)
from memoryguard_core.models import MemoryStatus, Sensitivity


def _engine(tmp_path):
    store_dir = tmp_path / ".memoryguard"
    store_dir.mkdir(parents=True, exist_ok=True)
    return build_local_engine(str(store_dir / "store.db"))


DEMO_TEXT = """\
Codex: The old notes mention FastAPI.
User: We moved from FastAPI to Flask.
Assistant: Use pnpm instead of npm.
User: Local dev uses SQLite but production uses PostgreSQL.
Assistant: Deploy this on Vercel.
User: Tests run with pnpm test.
Assistant: Frontend is React.
User: Do not add external LLM API dependencies.
Assistant: Maybe try Redis for a minute as a temporary idea.
User: The previous MySQL plan is dead.
User: The API key is sk-test-1234567890abcdef.
"""


def test_extract_candidates_from_transcript_examples() -> None:
    candidates = extract_candidates(
        DEMO_TEXT,
        source_type="codex_transcript",
        source_ref="transcript.txt",
    )
    rendered = "\n".join(c.canonical_content or c.content for c in candidates)

    assert "Backend framework: Flask" in rendered
    assert "Package manager: pnpm" in rendered
    assert "Local database: SQLite" in rendered
    assert "Cloud database: PostgreSQL" in rendered
    assert "Deployment target: Vercel" in rendered
    assert "Test command: pnpm test" in rendered
    assert "Frontend framework: React" in rendered
    assert "Do not add external LLM API dependencies." in rendered
    assert "MySQL is deprecated/outdated." in rendered
    assert "Redis" not in rendered
    assert "sk-test-1234567890abcdef" not in rendered

    backend = next(c for c in candidates if c.decision_key == "backend_framework")
    package = next(c for c in candidates if c.decision_key == "package_manager")
    secret = next(c for c in candidates if c.sensitivity == Sensitivity.SECRET)

    assert backend.value == "Flask"
    assert backend.supersedes_value == "FastAPI"
    assert package.value == "pnpm"
    assert package.supersedes_value == "npm"
    assert secret.status == CaptureStatus.PENDING
    assert secret.metadata["capture_action"] == "omit_sensitive"


def test_pending_candidate_persistence(tmp_path) -> None:
    transcript = tmp_path / "transcript.txt"
    transcript.write_text("Frontend is React.\nTests run with pnpm test.\n", encoding="utf-8")

    candidates = ingest_capture_file(tmp_path, transcript, source_type="codex_transcript")
    pending = list_candidates(tmp_path, status=CaptureStatus.PENDING)

    assert len(candidates) == 2
    assert [c.id for c in pending] == [c.id for c in candidates]
    assert all("#L" in c.source_ref for c in pending)


def test_approve_candidate_creates_memory(tmp_path) -> None:
    engine = _engine(tmp_path)
    transcript = tmp_path / "transcript.txt"
    transcript.write_text("Deploy this on Vercel.\n", encoding="utf-8")
    [candidate] = ingest_capture_file(tmp_path, transcript, source_type="codex_transcript")

    approved, memory_id = approve_candidate(
        tmp_path,
        engine,
        candidate.id,
        scope_ref="demo",
    )
    record = engine.get(str(memory_id))

    assert approved.status == CaptureStatus.APPROVED
    assert record is not None
    assert record.content == "Deployment target is Vercel."
    assert record.metadata["capture_candidate_id"] == candidate.id
    assert record.metadata["capture_source_type"] == "codex_transcript"


def test_approve_candidate_triggers_supersession(tmp_path) -> None:
    engine = _engine(tmp_path)
    old = engine.create_memory(
        content="This project uses FastAPI for the backend.",
        source_type=SourceType.USER,
        source_ref="user://me",
        scope=Scope.PROJECT,
        scope_ref="demo",
    )
    transcript = tmp_path / "transcript.txt"
    transcript.write_text("We moved from FastAPI to Flask.\n", encoding="utf-8")
    [candidate] = ingest_capture_file(tmp_path, transcript, source_type="codex_transcript")

    approve_candidate(tmp_path, engine, candidate.id, scope_ref="demo")

    assert engine.get(old.memory_id).status == MemoryStatus.SUPERSEDED


def test_sensitive_candidate_does_not_leak_or_approve_all(tmp_path) -> None:
    engine = _engine(tmp_path)
    transcript = tmp_path / "transcript.txt"
    transcript.write_text(
        "The API key is sk-test-1234567890abcdef.\nFrontend is React.\n",
        encoding="utf-8",
    )
    ingest_capture_file(tmp_path, transcript, source_type="codex_transcript")

    approved = approve_all_safe_candidates(tmp_path, engine, scope_ref="demo")
    memories = engine.store.list(scope=Scope.PROJECT, scope_ref="demo")
    queue_text = (tmp_path / ".memoryguard" / "capture" / "candidates.json").read_text(
        encoding="utf-8"
    )

    assert len(approved) == 1
    assert len(memories) == 1
    assert "React" in memories[0].content
    assert "sk-test-1234567890abcdef" not in queue_text
    assert "sk-test-1234567890abcdef" not in "\n".join(m.content for m in memories)
