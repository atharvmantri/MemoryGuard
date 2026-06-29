# SPDX-License-Identifier: Apache-2.0
"""CLI tests for the ``memoryguard`` command (Typer ``CliRunner``).

Covers the local-engine command surface end to end against a temporary project
store: ``init`` idempotency + unwritable-path safety, ``add`` then ``show``,
``query`` output, ``list``, ``status`` (flags + mode), and ``rm`` soft-delete.

Requirements: 1.1, 1.2, 1.5, 10.1–10.6.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from typer.testing import CliRunner

from memoryguard_cli.config import config_dir
from memoryguard_cli.main import app

runner = CliRunner()

# Wide console so rich tables/lines don't soft-wrap ids/content during capture.
_ENV = {"COLUMNS": "200", "TERM": "dumb"}

_ADDED_RE = re.compile(r"added memory (\S+)")


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


def _text(result) -> str:
    """Return stdout + stderr text from an invocation result."""
    out = result.stdout or ""
    try:
        out += result.stderr or ""
    except (ValueError, AttributeError):  # stderr not captured separately
        pass
    return out


def _init(project) -> None:
    result = runner.invoke(app, ["init", str(project)], env=_ENV)
    assert result.exit_code == 0, _text(result)


def _add(project, content: str, *, scope_ref: str = "demo") -> str:
    result = runner.invoke(
        app,
        [
            "--store",
            str(project),
            "add",
            content,
            "--source-type",
            "user",
            "--source-ref",
            "user://me",
            "--scope",
            "project",
            "--scope-ref",
            scope_ref,
        ],
        env=_ENV,
    )
    assert result.exit_code == 0, _text(result)
    match = _ADDED_RE.search(result.stdout)
    assert match, f"could not find memory id in output: {result.stdout!r}"
    return match.group(1)


def _add_repo(project, content: str, *, source_ref: str, scope_ref: str = "billing-svc") -> str:
    """Add a memory in REPO scope (used to set up a contradictory pair)."""
    result = runner.invoke(
        app,
        [
            "--store",
            str(project),
            "add",
            content,
            "--source-type",
            "file",
            "--source-ref",
            source_ref,
            "--scope",
            "repo",
            "--scope-ref",
            scope_ref,
        ],
        env=_ENV,
    )
    assert result.exit_code == 0, _text(result)
    match = _ADDED_RE.search(result.stdout)
    assert match, f"could not find memory id in output: {result.stdout!r}"
    return match.group(1)


def _collapse(text: str) -> str:
    """Collapse rich table borders/whitespace so values can be matched stably.

    Turns e.g. ``"│ total │     2 │"`` into ``" total 2 "`` so a count row can be
    asserted without depending on exact table glyphs or column widths.
    """
    return re.sub(r"[^0-9A-Za-z]+", " ", text)


def test_help_contains_happy_paths():
    result = runner.invoke(app, ["--help"], env=_ENV)
    assert result.exit_code == 0, _text(result)
    assert "MemoryGuard keeps AI coding-agent context files current and secret-safe" in (
        result.stdout
    )
    assert "memoryguard doctor" in result.stdout
    assert "memoryguard init" in result.stdout
    assert 'memoryguard remember "This project uses Flask for the backend."' in result.stdout
    assert "memoryguard sync" in result.stdout
    assert "memoryguard capture file ./codex-session.txt --source codex" in result.stdout
    assert "memoryguard capture approve --all" in result.stdout


def test_uv_test_venv_is_ignored():
    gitignore = (_repo_root() / ".gitignore").read_text(encoding="utf-8")
    assert ".uv-test-venv/" in gitignore


# ---------------------------------------------------------------------------
# init
# ---------------------------------------------------------------------------


def test_init_creates_store(tmp_path):
    project = tmp_path / "proj"
    result = runner.invoke(app, ["init", str(project)], env=_ENV)
    assert result.exit_code == 0, _text(result)
    assert "initialized" in result.stdout.lower()
    assert (config_dir(project) / "config.json").is_file()


def test_init_defaults_to_current_directory(tmp_path, monkeypatch):
    project = tmp_path / "proj"
    project.mkdir()
    monkeypatch.chdir(project)

    result = runner.invoke(app, ["init"], env=_ENV)

    assert result.exit_code == 0, _text(result)
    assert "initialized" in result.stdout.lower()
    assert (project / ".memoryguard" / "config.json").is_file()


def test_init_is_idempotent_and_preserves_store(tmp_path):
    project = tmp_path / "proj"

    first = runner.invoke(app, ["init", str(project)], env=_ENV)
    assert first.exit_code == 0, _text(first)

    # Add a memory so we can prove the store is preserved across a re-init.
    memory_id = _add(project, "We use pnpm not npm")

    second = runner.invoke(app, ["init", str(project)], env=_ENV)
    assert second.exit_code == 0, _text(second)
    assert "store already exists" in second.stdout.lower()

    # The previously added memory must still be present (store preserved).
    show = runner.invoke(app, ["--store", str(project), "show", memory_id], env=_ENV)
    assert show.exit_code == 0, _text(show)
    assert "pnpm" in show.stdout


def test_init_reinit_preserves_config_file_bytes(tmp_path):
    # Re-running init must not rewrite or alter the existing project config
    # (idempotency, Requirement 1.2): the store is preserved untouched.
    project = tmp_path / "proj"

    first = runner.invoke(app, ["init", str(project)], env=_ENV)
    assert first.exit_code == 0, _text(first)
    cfg_file = config_dir(project) / "config.json"
    original = cfg_file.read_bytes()

    second = runner.invoke(app, ["init", str(project)], env=_ENV)
    assert second.exit_code == 0, _text(second)
    assert "store already exists" in second.stdout.lower()
    # No new "initialized" store should be reported on the second run.
    assert "initialized" not in second.stdout.lower()
    # Config file is byte-for-byte identical (existing store preserved).
    assert cfg_file.read_bytes() == original


def test_init_unwritable_path_leaves_no_partial_store(tmp_path):
    # Make the parent a *file* so creating a store directory beneath it fails.
    blocker = tmp_path / "blocker"
    blocker.write_text("not a directory", encoding="utf-8")
    target = blocker / "proj"

    result = runner.invoke(app, ["init", str(target)], env=_ENV)
    assert result.exit_code == 1
    # No partial store directory should remain.
    assert not config_dir(target).exists()


# ---------------------------------------------------------------------------
# add + show
# ---------------------------------------------------------------------------


def test_add_then_show(tmp_path):
    project = tmp_path / "proj"
    _init(project)

    memory_id = _add(project, "We use pnpm not npm")

    show = runner.invoke(app, ["--store", str(project), "show", memory_id], env=_ENV)
    assert show.exit_code == 0, _text(show)
    assert "pnpm" in show.stdout
    assert "trust score" in show.stdout.lower()
    assert "user://me" in show.stdout
    # Trust signal breakdown should be rendered.
    assert "source_authority" in show.stdout


def test_show_unknown_memory_fails(tmp_path):
    project = tmp_path / "proj"
    _init(project)

    result = runner.invoke(
        app, ["--store", str(project), "show", "does-not-exist"], env=_ENV
    )
    assert result.exit_code == 1


def test_show_displays_trust_breakdown_and_contradictions(tmp_path):
    # Two memories about the same repo subject with a numeric/value mismatch
    # form a contradictory pair; `show` must render the trust breakdown AND the
    # detected contradictions linking the conflicting memory (Requirement 10.2).
    project = tmp_path / "proj"
    _init(project)

    first = _add_repo(
        project,
        "billing-svc uses PostgreSQL 15",
        source_ref="repo://billing-svc/README.md@c4a1",
    )
    second = _add_repo(
        project,
        "billing-svc uses PostgreSQL 16",
        source_ref="repo://billing-svc/README.md@d9f2",
    )

    show = runner.invoke(app, ["--store", str(project), "show", second], env=_ENV)
    assert show.exit_code == 0, _text(show)
    out = show.stdout

    # Content + provenance + scope are displayed.
    assert "PostgreSQL 16" in out
    assert "billing-svc" in out

    # Trust breakdown: the overall score plus the per-signal table.
    assert "trust score" in out.lower()
    assert "trust signals" in out.lower()
    assert "source_authority" in out

    # Detected contradictions: a contradictions table linking the first memory.
    assert "contradictions" in out.lower()
    assert first in out


def test_show_reports_no_contradictions_for_isolated_memory(tmp_path):
    # A lone memory has no conflicts; `show` reports the (empty) contradictions
    # state explicitly rather than omitting the section (Requirement 10.2).
    project = tmp_path / "proj"
    _init(project)
    memory_id = _add(project, "We use pnpm not npm")

    show = runner.invoke(app, ["--store", str(project), "show", memory_id], env=_ENV)
    assert show.exit_code == 0, _text(show)
    assert "no contradictions" in show.stdout.lower()


# ---------------------------------------------------------------------------
# query
# ---------------------------------------------------------------------------


def test_query_output(tmp_path):
    project = tmp_path / "proj"
    _init(project)
    _add(project, "We use pnpm not npm")

    result = runner.invoke(
        app,
        [
            "--store",
            str(project),
            "query",
            "pnpm",
            "--scope",
            "project",
            "--scope-ref",
            "demo",
            "--min-trust",
            "0.0",
        ],
        env=_ENV,
    )
    assert result.exit_code == 0, _text(result)
    assert "pnpm" in result.stdout


# ---------------------------------------------------------------------------
# list
# ---------------------------------------------------------------------------


def test_list_shows_added_memory(tmp_path):
    project = tmp_path / "proj"
    _init(project)
    _add(project, "We use pnpm not npm")

    result = runner.invoke(app, ["--store", str(project), "list"], env=_ENV)
    assert result.exit_code == 0, _text(result)
    assert "pnpm" in result.stdout
    assert "active" in result.stdout


# ---------------------------------------------------------------------------
# status
# ---------------------------------------------------------------------------


def test_status_shows_project_health(tmp_path):
    project = tmp_path / "proj"
    _init(project)

    result = runner.invoke(app, ["--store", str(project), "status"], env=_ENV)
    assert result.exit_code == 0, _text(result)
    out = result.stdout.lower()
    assert "mode" in out and "local" in out
    assert "project" in out
    assert "root" in out
    assert "store" in out
    assert "project status" in out
    assert "active memories" in out
    assert "pending capture candidates" in out
    assert "rejected capture candidates" in out
    assert "superseded deprecated memories" in _collapse(result.stdout).lower()
    assert "context files" in out
    assert "AGENTS.md" in result.stdout
    assert "CLAUDE.md" in result.stdout
    assert "MEMORY.md" in result.stdout
    assert ".cursor/rules/memoryguard.mdc" in result.stdout
    assert "suggested next command" in out
    assert 'memoryguard remember "This project uses ..."' in result.stdout


def test_status_counts_reflect_added_memories(tmp_path):
    # Two added memories => active memories 2.
    project = tmp_path / "proj"
    _init(project)
    _add(project, "first memory fact")
    _add(project, "second memory fact")

    result = runner.invoke(app, ["--store", str(project), "status"], env=_ENV)
    assert result.exit_code == 0, _text(result)
    out = result.stdout.lower()
    assert "project status" in out

    collapsed = _collapse(result.stdout)
    assert re.search(r"\bactive memories 2\b", collapsed), result.stdout
    assert "memoryguard sync" in result.stdout


def test_status_counts_capture_queue_and_suggests_pending(tmp_path):
    project = tmp_path / "proj"
    _init(project)
    _add(project, "Frontend is React.")
    transcript = project / "codex-session.txt"
    transcript.write_text(
        "Deploy this on Vercel.\nTests run with pnpm test.\n",
        encoding="utf-8",
    )
    captured = runner.invoke(
        app,
        ["--store", str(project), "capture", "file", str(transcript), "--source", "codex"],
        env=_ENV,
    )
    assert captured.exit_code == 0, _text(captured)
    first_id = re.search(
        r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
        captured.stdout,
    ).group(0)

    rejected = runner.invoke(
        app,
        ["--store", str(project), "capture", "reject", first_id],
        env=_ENV,
    )
    assert rejected.exit_code == 0, _text(rejected)

    result = runner.invoke(app, ["--store", str(project), "status"], env=_ENV)
    assert result.exit_code == 0, _text(result)
    collapsed = _collapse(result.stdout).lower()
    assert re.search(r"\bpending capture candidates 1\b", collapsed), result.stdout
    assert re.search(r"\brejected capture candidates 1\b", collapsed), result.stdout
    assert "memoryguard capture pending" in result.stdout


def test_status_remote_mode_reports_remote_flags_and_mode(tmp_path, monkeypatch):
    # In remote mode `status` reports mode=remote, the remote URL, and renders
    # the flags + counts returned by the API's health endpoint
    # (Requirements 10.6 mode + 10.7 remote routing). The network boundary is
    # substituted so the rendering path is exercised without a live server.
    import memoryguard_cli.main as cli_main

    class _FakeRemote:
        def health(self) -> dict:
            return {
                "flags": {"local_store": True, "cloud_sync": False},
                "counts": {"total": 3, "active": 3},
            }

        def close(self) -> None:
            pass

    monkeypatch.setattr(cli_main, "_get_remote", lambda state: _FakeRemote())

    result = runner.invoke(
        app, ["--remote", "http://localhost:8000", "status"], env=_ENV
    )
    assert result.exit_code == 0, _text(result)
    out = result.stdout.lower()

    # Mode + remote target.
    assert "mode" in out and "remote" in out
    assert "http://localhost:8000" in result.stdout

    # Feature flags from health are rendered (active + inactive).
    assert "feature flags" in out
    assert "local_store" in result.stdout
    assert "cloud_sync" in result.stdout

    # Store stats from health are rendered.
    assert "store stats" in out
    assert re.search(r"\btotal 3\b", _collapse(result.stdout)), result.stdout


# ---------------------------------------------------------------------------
# rm (soft-delete)
# ---------------------------------------------------------------------------


def test_rm_soft_deletes(tmp_path):
    project = tmp_path / "proj"
    _init(project)
    memory_id = _add(project, "We use pnpm not npm")

    rm = runner.invoke(app, ["--store", str(project), "rm", memory_id], env=_ENV)
    assert rm.exit_code == 0, _text(rm)
    assert "soft-deleted" in rm.stdout.lower()

    # Still retrievable, now with status deleted (soft-delete contract).
    show = runner.invoke(app, ["--store", str(project), "show", memory_id], env=_ENV)
    assert show.exit_code == 0, _text(show)
    assert "deleted" in show.stdout.lower()


def test_rm_unknown_memory_fails(tmp_path):
    project = tmp_path / "proj"
    _init(project)
    result = runner.invoke(app, ["--store", str(project), "rm", "nope"], env=_ENV)
    assert result.exit_code == 1


# ---------------------------------------------------------------------------
# correct (lineage)
# ---------------------------------------------------------------------------


def test_correct_creates_new_memory(tmp_path):
    project = tmp_path / "proj"
    _init(project)
    memory_id = _add(project, "billing uses PostgreSQL 15")

    result = runner.invoke(
        app,
        ["--store", str(project), "correct", memory_id, "billing uses PostgreSQL 16"],
        env=_ENV,
    )
    assert result.exit_code == 0, _text(result)
    assert "corrected" in result.stdout.lower()

    # The original memory transitions to the corrected lineage state.
    show = runner.invoke(app, ["--store", str(project), "show", memory_id], env=_ENV)
    assert show.exit_code == 0, _text(show)
    assert "corrected" in show.stdout.lower()


# ---------------------------------------------------------------------------
# context sync
# ---------------------------------------------------------------------------


def test_context_generate_requires_approval_before_write(tmp_path):
    project = tmp_path / "proj"
    _init(project)
    _add(project, "Use pnpm for JavaScript commands")

    result = runner.invoke(
        app, ["--store", str(project), "context", "generate"], env=_ENV
    )

    assert result.exit_code == 0, _text(result)
    assert "pending approval" in result.stdout.lower()
    assert "--- a/AGENTS.md" in result.stdout
    assert not (project / "AGENTS.md").exists()


def test_context_approve_writes_pending_files(tmp_path):
    project = tmp_path / "proj"
    _init(project)
    _add(project, "Use pnpm for JavaScript commands")

    generated = runner.invoke(
        app, ["--store", str(project), "context", "generate"], env=_ENV
    )
    assert generated.exit_code == 0, _text(generated)

    approved = runner.invoke(
        app, ["--store", str(project), "context", "approve"], env=_ENV
    )
    assert approved.exit_code == 0, _text(approved)
    assert "approved" in approved.stdout.lower()
    assert "Package manager: pnpm" in (project / "AGENTS.md").read_text(encoding="utf-8")


def test_context_update_yes_writes_immediately(tmp_path):
    project = tmp_path / "proj"
    _init(project)
    _add(project, "Prefer uv run pytest for Python tests")

    result = runner.invoke(
        app, ["--store", str(project), "context", "update", "--yes"], env=_ENV
    )

    assert result.exit_code == 0, _text(result)
    assert (project / "CLAUDE.md").is_file()
    assert "Prefer uv run pytest" in (project / "MEMORY.md").read_text(encoding="utf-8")


def test_resolve_marks_old_memory_superseded(tmp_path):
    project = tmp_path / "proj"
    _init(project)
    old_id = _add(project, "This project uses FastAPI for the backend")
    new_id = _add(project, "The project has now been using Flask for backend")

    result = runner.invoke(
        app,
        ["--store", str(project), "resolve", old_id, "--superseded-by", new_id],
        env=_ENV,
    )

    assert result.exit_code == 0, _text(result)
    show_old = runner.invoke(app, ["--store", str(project), "show", old_id], env=_ENV)
    assert show_old.exit_code == 0, _text(show_old)
    assert "superseded" in show_old.stdout.lower()


def test_capture_file_pending_and_approve_all(tmp_path):
    project = tmp_path / "proj"
    _init(project)
    _add(project, "This project uses FastAPI for the backend")
    _add(project, "This project uses npm.")
    transcript = project / "codex-session.txt"
    transcript.write_text(
        "\n".join(
            [
                "We moved from FastAPI to Flask.",
                "Use pnpm instead of npm.",
                "Local dev uses SQLite but production uses PostgreSQL.",
                "Deploy this on Vercel.",
                "Tests run with pnpm test.",
                "The API key is sk-test-1234567890abcdef.",
            ]
        ),
        encoding="utf-8",
    )

    captured = runner.invoke(
        app,
        ["--store", str(project), "capture", "file", str(transcript), "--source", "codex"],
        env=_ENV,
    )
    assert captured.exit_code == 0, _text(captured)
    assert "Backend framework: Flask" in captured.stdout
    assert "Package manager: pnpm" in captured.stdout
    assert "sk-test-1234567890abcdef" not in captured.stdout
    assert "Next: run `memoryguard capture pending`" in captured.stdout
    assert "`memoryguard capture approve --all`" in captured.stdout
    assert "`memoryguard sync`" in captured.stdout

    pending = runner.invoke(app, ["--store", str(project), "capture", "pending"], env=_ENV)
    assert pending.exit_code == 0, _text(pending)
    assert "Deployment target: Vercel" in pending.stdout

    approved = runner.invoke(
        app,
        ["--store", str(project), "capture", "approve", "--all"],
        env=_ENV,
    )
    assert approved.exit_code == 0, _text(approved)
    assert "approved" in approved.stdout.lower()

    listed = runner.invoke(app, ["--store", str(project), "list"], env=_ENV)
    assert listed.exit_code == 0, _text(listed)
    assert "Flask" in listed.stdout
    assert "pnpm" in listed.stdout
    assert "sk-test-1234567890abcdef" not in listed.stdout


def test_sync_and_remember_aliases(tmp_path):
    project = tmp_path / "proj"
    _init(project)

    remembered = runner.invoke(
        app,
        ["--store", str(project), "remember", "Frontend is React."],
        env=_ENV,
    )
    assert remembered.exit_code == 0, _text(remembered)
    assert "remembered" in remembered.stdout.lower()

    synced = runner.invoke(app, ["--store", str(project), "sync"], env=_ENV)
    assert synced.exit_code == 0, _text(synced)
    assert (project / "AGENTS.md").is_file()
    assert (project / "CLAUDE.md").is_file()
    assert (project / "MEMORY.md").is_file()
    assert (project / ".cursor" / "rules" / "memoryguard.mdc").is_file()
    assert "Frontend framework: React" in (project / "AGENTS.md").read_text(
        encoding="utf-8"
    )


def test_demo_runs_in_temporary_project(tmp_path):
    result = runner.invoke(app, ["demo"], env=_ENV)
    assert result.exit_code == 0, _text(result)
    assert "Agent Capture demo passed." in result.stdout
    assert "approved" in result.stdout


# ---------------------------------------------------------------------------
# doctor
# ---------------------------------------------------------------------------


def test_doctor_outside_project_exits_zero_in_lenient_mode(tmp_path, monkeypatch):
    # Outside a project, the store check warns, but CLI/Python/uv/etc. should
    # pass. In the default (lenient) mode the command should still exit 0 so
    # that a brand-new user running `memoryguard doctor` before
    # `memoryguard init` does not see a scary non-zero status.
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(app, ["doctor"], env=_ENV)
    assert result.exit_code == 0, _text(result)
    out = result.stdout
    assert "MemoryGuard doctor" in out
    assert "MemoryGuard CLI" in out
    assert "running v" in out
    assert "Python" in out
    assert "uv" in out
    # Store row: the absence of a project is the expected alpha state.
    assert "no store found" in out.lower()
    # Context files and pending candidates should be marked skipped (no store).
    assert "skipped (no store)" in out.lower()
    # Git row is present.
    assert "Git" in out
    # The wrapper is honest about Node/pnpm being dev-only.
    assert "Node.js / pnpm (dev only)" in out
    # Verdict line is friendly: warnings did not flip the exit code.
    assert "lenient mode" in out.lower()


def test_doctor_outside_project_strict_exits_nonzero(tmp_path, monkeypatch):
    # Same setup as the lenient test, but with --strict, warnings flip the
    # exit code to 1. This is the diagnostic / CI mode.
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(app, ["doctor", "--strict"], env=_ENV)
    assert result.exit_code == 1, _text(result)
    out = result.stdout
    assert "no store found" in out.lower()
    # The strict-mode verdict line is explicit about why we failed.
    assert "strict" in out.lower()
    assert "warnings" in out.lower()


def test_doctor_inside_initialized_project_runs(tmp_path):
    # Inside an initialized project, the store row should pass and the verdict
    # may still be warn if the context files haven't been generated yet — but
    # the command should not error and should not report "no store found".
    project = tmp_path / "proj"
    _init(project)

    result = runner.invoke(app, ["--store", str(project), "doctor"], env=_ENV)
    # Lenient mode: 0. Strict mode: 1 (context-files warn). We are not
    # passing --strict here, so the expected exit is 0.
    assert result.exit_code == 0, _text(result)
    out = result.stdout
    assert "MemoryGuard doctor" in out
    assert "running v" in out
    # Store check resolves to "local store at ..." rather than "no store found".
    assert "local store at" in out
    assert "no store found" not in out.lower()
    # Pending candidates row should be present (zero is fine for a fresh init).
    assert "pending capture candidates" in out.lower()


def test_doctor_exit_zero_when_store_and_context_files_present(tmp_path):
    # Full pass: a project with a generated context block should make the
    # context-files row pass. Pending candidates may still be 0, store row is
    # pass, and there should be no real errors. Exit code is therefore 0.
    project = tmp_path / "proj"
    _init(project)
    # Add a memory and sync so the context files exist.
    _add(project, "Frontend framework: React.")
    sync = runner.invoke(app, ["--store", str(project), "sync"], env=_ENV)
    assert sync.exit_code == 0, _text(sync)

    result = runner.invoke(app, ["--store", str(project), "doctor"], env=_ENV)
    assert result.exit_code == 0, _text(result)
    out = result.stdout
    # Store + context-files rows are explicit "pass" cells.
    assert "pass" in out
    # The context-files row should report "all 4 present" once sync wrote them.
    assert "all 4 present" in out
    # The friendly "all checks passed" verdict is rendered on a full pass.
    assert "all checks passed" in out.lower()


def test_doctor_warns_when_context_files_missing_but_exits_zero(tmp_path):
    # Initialized project with no sync yet: context-files row is warn, but
    # the lenient exit code is still 0. --strict flips it to 1.
    project = tmp_path / "proj"
    _init(project)

    result = runner.invoke(app, ["--store", str(project), "doctor"], env=_ENV)
    assert result.exit_code == 0, _text(result)
    out = result.stdout
    # Context-files row reports missing files and suggests `memoryguard sync`.
    assert "missing" in out.lower()
    assert "memoryguard sync" in out

    # Strict mode flips the same warning to a non-zero exit.
    strict = runner.invoke(
        app, ["--store", str(project), "doctor", "--strict"], env=_ENV
    )
    assert strict.exit_code == 1, _text(strict)


def test_help_mentions_doctor_command():
    result = runner.invoke(app, ["--help"], env=_ENV)
    assert result.exit_code == 0, _text(result)
    # `doctor` is registered as a top-level command and should be in --help.
    assert "doctor" in result.stdout


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-v"]))
