# SPDX-License-Identifier: Apache-2.0
"""Unit tests for MemoryGuard Context Sync."""

from __future__ import annotations

from memoryguard_core import Scope, Sensitivity, SourceType, build_local_engine
from memoryguard_core.models import MemoryStatus
from memoryguard_core.retrieval.service import QuerySpec
from memoryguard_core.context_sync import (
    START_MARKER,
    approve_context_sync,
    build_context_sync_plan,
    format_unified_diff,
    write_pending_context_plan,
)


def _engine(tmp_path):
    return build_local_engine(str(tmp_path / "store.db"))


def test_context_generation_uses_memories_and_repo_metadata(tmp_path):
    (tmp_path / "README.md").write_text(
        "# Demo\n\nDemo project for trustworthy agent memory.\n\n```bash\nuv run pytest\n```\n",
        encoding="utf-8",
    )
    engine = _engine(tmp_path)
    engine.create_memory(
        content="Use pnpm for JavaScript workspace commands.",
        source_type=SourceType.USER,
        source_ref="user://architect",
        scope=Scope.PROJECT,
        scope_ref="demo",
    )

    plan = build_context_sync_plan(tmp_path, engine.store)
    agents = next(item for item in plan.files if item.path == "AGENTS.md")

    assert "Project Overview" in agents.proposed
    assert "Demo project for trustworthy agent memory" in agents.proposed
    assert "uv run pytest" in agents.proposed
    assert "Package manager: pnpm" in agents.proposed


def test_context_generation_redacts_secrets(tmp_path):
    engine = _engine(tmp_path)
    engine.create_memory(
        content="API token: sk-abcdefghijklmnopqrstuvwxyz123456 must not be used.",
        source_type=SourceType.USER,
        source_ref="user://security",
        scope=Scope.PROJECT,
        scope_ref="demo",
        sensitivity=Sensitivity.INTERNAL,
    )

    plan = build_context_sync_plan(tmp_path, engine.store)
    text = "\n".join(item.proposed for item in plan.files)

    assert "sk-abcdefghijklmnopqrstuvwxyz123456" not in text
    assert "Sensitive memory omitted from generated context. (source: user://security)" in text


def test_context_sync_preserves_existing_file_content(tmp_path):
    existing = tmp_path / "AGENTS.md"
    existing.write_text("Human notes stay here.\n", encoding="utf-8")
    engine = _engine(tmp_path)
    engine.create_memory(
        content="Prefer focused tests for feature changes.",
        source_type=SourceType.USER,
        source_ref="user://qa",
        scope=Scope.PROJECT,
        scope_ref="demo",
    )

    plan = build_context_sync_plan(tmp_path, engine.store)
    write_pending_context_plan(plan)
    approve_context_sync(tmp_path)

    updated = existing.read_text(encoding="utf-8")
    assert "Human notes stay here." in updated
    assert START_MARKER in updated
    assert "Prefer focused tests" in updated


def test_context_diff_is_non_destructive_until_approved(tmp_path):
    existing = tmp_path / "AGENTS.md"
    existing.write_text("Original content.\n", encoding="utf-8")
    engine = _engine(tmp_path)
    engine.create_memory(
        content="The project uses FastAPI for the API.",
        source_type=SourceType.USER,
        source_ref="user://dev",
        scope=Scope.PROJECT,
        scope_ref="demo",
    )

    plan = build_context_sync_plan(tmp_path, engine.store)
    diff = format_unified_diff(plan)

    assert "--- a/AGENTS.md" in diff
    assert existing.read_text(encoding="utf-8") == "Original content.\n"


def test_context_sync_moves_superseded_decisions_to_deprecated(tmp_path):
    engine = _engine(tmp_path)
    old = engine.create_memory(
        content="This project uses FastAPI for the backend.",
        source_type=SourceType.USER,
        source_ref="user://dev",
        scope=Scope.PROJECT,
        scope_ref="demo",
    )
    new = engine.create_memory(
        content="The project has now been using Flask for backend.",
        source_type=SourceType.USER,
        source_ref="user://dev",
        scope=Scope.PROJECT,
        scope_ref="demo",
    )

    assert engine.get(old.memory_id).status == MemoryStatus.SUPERSEDED
    assert engine.get(new.memory_id).status == MemoryStatus.ACTIVE

    plan = build_context_sync_plan(tmp_path, engine.store)
    agents = next(item for item in plan.files if item.path == "AGENTS.md")

    assert "Backend framework: Flask" in agents.proposed
    deprecated = agents.proposed.split("## Deprecated/Outdated Decisions", 1)[1]
    assert "Backend framework was previously FastAPI; superseded by Flask." in deprecated


def test_context_sync_canonical_decision_rendering(tmp_path):
    engine = _engine(tmp_path)
    facts = [
        "Use pnpm not npm.",
        "Local database is SQLite.",
        "Cloud database is PostgreSQL.",
        "The frontend uses React.",
        "Deployment target is Vercel.",
        "Test command uv run pytest.",
    ]
    for fact in facts:
        engine.create_memory(
            content=fact,
            source_type=SourceType.USER,
            source_ref="user://dev",
            scope=Scope.PROJECT,
            scope_ref="demo",
        )

    plan = build_context_sync_plan(tmp_path, engine.store)
    agents = next(item for item in plan.files if item.path == "AGENTS.md")

    assert "Package manager: pnpm" in agents.proposed
    assert "Local database: SQLite" in agents.proposed
    assert "Cloud database: PostgreSQL" in agents.proposed
    assert "Frontend framework: React" in agents.proposed
    assert "Deployment target: Vercel" in agents.proposed
    assert "Test command: uv run pytest" in agents.proposed
    assert "source: user://dev" in agents.proposed


def test_context_sync_redacts_and_omits_secret_memories_from_all_files(tmp_path):
    engine = _engine(tmp_path)
    engine.create_memory(
        content=(
            "The OpenAI API key is sk-test-1234567890abcdef and the database "
            "password is hunter2."
        ),
        source_type=SourceType.USER,
        source_ref="user://me",
        scope=Scope.PROJECT,
        scope_ref="demo",
    )

    plan = build_context_sync_plan(tmp_path, engine.store)

    for file_plan in plan.files:
        assert "sk-test-1234567890abcdef" not in file_plan.proposed
        assert "hunter2" not in file_plan.proposed
        assert "Sensitive memory omitted from generated context. (source: user://me)" in (
            file_plan.proposed
        )


def test_context_sync_test_command_is_canonical(tmp_path):
    engine = _engine(tmp_path)
    engine.create_memory(
        content="The test command is pnpm test.",
        source_type=SourceType.USER,
        source_ref="user://me",
        scope=Scope.PROJECT,
        scope_ref="demo",
    )

    plan = build_context_sync_plan(tmp_path, engine.store)
    agents = next(item for item in plan.files if item.path == "AGENTS.md").proposed
    commands = agents.split("## Commands", 1)[1].split("## Architecture", 1)[0]
    coding_rules = agents.split("## Coding Rules", 1)[1].split(
        "## Important Decisions", 1
    )[0]

    assert "Test command: pnpm test" in commands
    assert "Test command: pnpm test" in agents
    assert "The test command is pnpm test." not in agents
    assert "Test command: pnpm test" not in coding_rules


def test_context_sync_package_manager_supersession_is_canonical(tmp_path):
    engine = _engine(tmp_path)
    old = engine.create_memory(
        content="This project uses npm.",
        source_type=SourceType.USER,
        source_ref="user://dev",
        scope=Scope.PROJECT,
        scope_ref="demo",
    )
    new = engine.create_memory(
        content="The project now uses pnpm instead of npm.",
        source_type=SourceType.USER,
        source_ref="user://dev",
        scope=Scope.PROJECT,
        scope_ref="demo",
    )

    assert engine.get(old.memory_id).status == MemoryStatus.SUPERSEDED
    assert engine.get(new.memory_id).status == MemoryStatus.ACTIVE

    plan = build_context_sync_plan(tmp_path, engine.store)
    agents = next(item for item in plan.files if item.path == "AGENTS.md").proposed
    tech_stack = agents.split("## Tech Stack", 1)[1].split("## Commands", 1)[0]
    important = agents.split("## Important Decisions", 1)[1].split(
        "## Deprecated/Outdated Decisions", 1
    )[0]
    deprecated = agents.split("## Deprecated/Outdated Decisions", 1)[1].split(
        "## Things Agents Must Avoid", 1
    )[0]
    coding_rules = agents.split("## Coding Rules", 1)[1].split(
        "## Important Decisions", 1
    )[0]

    assert "Package manager: pnpm" in tech_stack
    assert "Package manager: pnpm" in important
    assert "Package manager was previously npm; superseded by pnpm." in deprecated
    assert "This project uses npm." not in tech_stack
    assert "This project uses npm." not in important
    assert "This project uses npm." not in deprecated
    assert "Follow existing repository patterns" in coding_rules
    assert "Package manager: pnpm" not in coding_rules


def test_query_ranking_is_structured_decision_aware(tmp_path):
    engine = _engine(tmp_path)
    facts = [
        "Backend framework is Flask.",
        "Package manager is pnpm.",
        "This project uses MySQL as the database.",
        "Deployment target is Vercel.",
        "Frontend framework is React.",
        "The test command is pnpm test.",
    ]
    for fact in facts:
        engine.create_memory(
            content=fact,
            source_type=SourceType.USER,
            source_ref="user://me",
            scope=Scope.PROJECT,
            scope_ref="demo",
        )

    backend = engine.query(
        QuerySpec(
            text="What backend does this project use?",
            scope=Scope.PROJECT,
            scope_ref="demo",
            min_trust=0.0,
        )
    )
    package = engine.query(
        QuerySpec(
            text="What package manager does this project use?",
            scope=Scope.PROJECT,
            scope_ref="demo",
            min_trust=0.0,
        )
    )
    deployment = engine.query(
        QuerySpec(
            text="Where is this deployed?",
            scope=Scope.PROJECT,
            scope_ref="demo",
            min_trust=0.0,
        )
    )
    tests = engine.query(
        QuerySpec(
            text="How do I run tests?",
            scope=Scope.PROJECT,
            scope_ref="demo",
            min_trust=0.0,
        )
    )

    assert backend and "Flask" in backend[0].record.content
    assert "MySQL" not in backend[0].record.content
    assert package and "pnpm" in package[0].record.content
    assert "MySQL" not in package[0].record.content
    assert "Vercel" not in package[0].record.content
    assert deployment and "Vercel" in deployment[0].record.content
    assert tests and "pnpm test" in tests[0].record.content


def test_context_sync_database_split_supersedes_general_database(tmp_path):
    engine = _engine(tmp_path)
    old = engine.create_memory(
        content="This project uses MySQL as the database.",
        source_type=SourceType.USER,
        source_ref="user://me",
        scope=Scope.PROJECT,
        scope_ref="demo",
    )
    engine.create_memory(
        content="The local database is SQLite.",
        source_type=SourceType.USER,
        source_ref="user://me",
        scope=Scope.PROJECT,
        scope_ref="demo",
    )
    engine.create_memory(
        content="The cloud database is PostgreSQL.",
        source_type=SourceType.USER,
        source_ref="user://me",
        scope=Scope.PROJECT,
        scope_ref="demo",
    )

    assert engine.get(old.memory_id).status == MemoryStatus.SUPERSEDED

    plan = build_context_sync_plan(tmp_path, engine.store)
    agents = next(item for item in plan.files if item.path == "AGENTS.md").proposed
    overview = agents.split("## Project Overview", 1)[1].split("## Tech Stack", 1)[0]
    tech_stack = agents.split("## Tech Stack", 1)[1].split("## Commands", 1)[0]
    deprecated = agents.split("## Deprecated/Outdated Decisions", 1)[1].split(
        "## Things Agents Must Avoid", 1
    )[0]

    assert "MySQL" not in overview
    assert "Database: MySQL" not in tech_stack
    assert "Local database: SQLite" in tech_stack
    assert "Cloud database: PostgreSQL" in tech_stack
    assert (
        "Database was previously MySQL; superseded by local SQLite and cloud PostgreSQL."
        in deprecated
    )
