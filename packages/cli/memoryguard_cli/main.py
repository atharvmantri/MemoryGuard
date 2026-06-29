# SPDX-License-Identifier: Apache-2.0
"""The ``memoryguard`` command-line interface (Typer + rich).

Implements the design's *CLI Command Interface*. By default every command runs
against the **local** OSS engine (built over a project's ``.memoryguard`` SQLite
store via :func:`memoryguard_core.build_local_engine`). The global
``--remote <url>`` option routes operations to the REST API instead
(Requirement 10.7).

Commands: ``init``, ``add``, ``ingest``, ``query``, ``show``, ``list``,
``contradictions``, ``correct``, ``rm``, ``dashboard``, ``mcp``, ``status``.

This module is the console-script entry point declared in
``packages/cli/pyproject.toml`` as ``memoryguard = memoryguard_cli.main:app``.

Requirements: 1.1, 1.2, 1.5, 10.1–10.7.
"""

from __future__ import annotations

import re
import tempfile
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.table import Table

from memoryguard_core import (
    CONTEXT_FILES,
    Scope,
    Sensitivity,
    SourceType,
    MemoryStatus,
    build_local_engine,
)
from memoryguard_core.retrieval.service import QuerySpec
from memoryguard_core.context_sync import (
    approve_context_sync,
    build_context_sync_plan,
    context_status,
    format_unified_diff,
    write_pending_context_plan,
)
from memoryguard_core.capture import (
    CaptureStatus,
    approve_all_safe_candidates,
    approve_candidate,
    clear_rejected_candidates,
    ingest_capture_file,
    list_candidates,
    reject_candidate,
)

from .config import StoreInitError, init_store, load_config, store_exists
from .remote import RemoteClient, RemoteError

app = typer.Typer(
    name="memoryguard",
    help=(
        "MemoryGuard keeps AI coding-agent context files current and secret-safe.\n\n"
        "Basic:\n"
        "  memoryguard init\n"
        '  memoryguard remember "This project uses Flask for the backend."\n'
        "  memoryguard sync\n\n"
        "Agent capture:\n"
        "  memoryguard capture file ./codex-session.txt --source codex\n"
        "  memoryguard capture approve --all\n"
        "  memoryguard sync"
    ),
    no_args_is_help=True,
    add_completion=False,
)

console = Console()
err_console = Console(stderr=True)
context_app = typer.Typer(
    name="context",
    help="Generate and maintain AI-agent context files from local MemoryGuard state.",
    no_args_is_help=True,
)
app.add_typer(context_app, name="context")
capture_app = typer.Typer(
    name="capture",
    help=(
        "Extract proposed memories from local agent transcripts, then approve "
        "them before they become project memory."
    ),
    no_args_is_help=True,
)
app.add_typer(capture_app, name="capture")


# ---------------------------------------------------------------------------
# CLI state + global options
# ---------------------------------------------------------------------------


@dataclass
class CLIState:
    """Holds global options resolved by the root callback."""

    remote: Optional[str] = None
    token: Optional[str] = None
    store: Optional[Path] = None

    @property
    def is_remote(self) -> bool:
        return self.remote is not None

    @property
    def mode(self) -> str:
        return "remote" if self.is_remote else "local"


@app.callback()
def _root(
    ctx: typer.Context,
    remote: Optional[str] = typer.Option(
        None,
        "--remote",
        metavar="URL",
        help="Operate against the REST API at URL instead of the local engine.",
    ),
    token: Optional[str] = typer.Option(
        None,
        "--token",
        help="Bearer token for an authenticated remote API.",
        envvar="MEMORYGUARD_TOKEN",
    ),
    store: Optional[Path] = typer.Option(
        None,
        "--store",
        metavar="PATH",
        help="Project directory containing the .memoryguard store "
        "(defaults to discovery from the current directory).",
    ),
) -> None:
    """Resolve global options into the shared :class:`CLIState`."""
    ctx.obj = CLIState(remote=remote, token=token, store=store)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _fail(message: str) -> "typer.Exit":
    """Print ``message`` to stderr in red and return an exit(1) to raise."""
    err_console.print(f"[bold red]Error:[/bold red] {message}")
    return typer.Exit(code=1)


def _iso(value: Optional[datetime]) -> Optional[str]:
    return value.isoformat() if isinstance(value, datetime) else None


def _record_to_dict(record) -> dict:
    """Normalize a :class:`MemoryRecord` to the REST ``MemoryResponse`` shape."""
    return {
        "memory_id": record.memory_id,
        "content": record.content,
        "source_type": record.source_type.value,
        "source_ref": record.source_ref,
        "scope": record.scope.value,
        "scope_ref": record.scope_ref,
        "created_at": _iso(record.created_at),
        "updated_at": _iso(record.updated_at),
        "expires_at": _iso(record.expires_at),
        "trust_score": record.trust_score,
        "sensitivity": record.sensitivity.value,
        "status": record.status.value,
        "contradicts": list(record.contradicts),
        "tags": list(record.tags),
    }


_DURATION_RE = re.compile(r"^\s*(\d+)\s*([smhdw])\s*$", re.IGNORECASE)
_DURATION_FACTORS = {"s": 1, "m": 60, "h": 3600, "d": 86400, "w": 604800}


def _parse_expires(value: Optional[str]) -> Optional[datetime]:
    """Parse ``--expires`` as a relative duration (e.g. ``30d``) or ISO datetime."""
    if value is None:
        return None
    match = _DURATION_RE.match(value)
    if match:
        amount = int(match.group(1))
        factor = _DURATION_FACTORS[match.group(2).lower()]
        return datetime.now(timezone.utc) + timedelta(seconds=amount * factor)
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise typer.BadParameter(
            f"invalid --expires value {value!r}; use e.g. '30d', '12h', or an ISO date"
        ) from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _truncate(text: str, width: int = 60) -> str:
    text = " ".join(str(text).split())
    return text if len(text) <= width else text[: width - 1] + "…"


def _project_scope_ref(cfg) -> str:
    return str(cfg.project_name or cfg.root.name or "project")


def _get_engine(state: CLIState):
    """Build the local engine for the resolved project store."""
    cfg = load_config(state.store)
    engine = build_local_engine(str(cfg.db_path))
    return engine, cfg


def _get_remote(state: CLIState) -> RemoteClient:
    """Return a REST client, preferring the official SDK when importable."""
    # Honor the official Python SDK remote client when available; otherwise use
    # the bundled thin httpx client. The SDK is optional, so any failure here
    # falls back transparently.
    try:  # pragma: no cover - SDK is optional / not installed in tests
        from memoryguard import MemoryGuard  # type: ignore

        if hasattr(MemoryGuard, "remote"):
            sdk = MemoryGuard.remote(state.remote, token=state.token)
            return _SdkRemoteAdapter(sdk)
    except Exception:
        pass
    return RemoteClient(state.remote, token=state.token)


# ---------------------------------------------------------------------------
# init
# ---------------------------------------------------------------------------


@app.command()
def init(
    path: Path = typer.Argument(
        Path("."),
        help="Project directory in which to create the local store.",
    ),
    name: Optional[str] = typer.Option(
        None, "--name", help="Project name (defaults to the directory name)."
    ),
) -> None:
    """Create a local SQLite store + project config (idempotent)."""
    try:
        existed = store_exists(path)
        cfg, created = init_store(path, project_name=name)
    except StoreInitError as exc:
        raise _fail(str(exc))

    if not created:
        console.print(
            f"[yellow]store already exists[/yellow] for project "
            f"[bold]{cfg.project_name}[/bold] at {cfg.config_file}"
        )
        return

    # Materialize the SQLite store so the project is immediately usable.
    try:
        build_local_engine(str(cfg.db_path))
    except Exception as exc:  # pragma: no cover - defensive
        raise _fail(f"store config written but the SQLite store failed to initialize: {exc}")

    _ = existed  # (kept for clarity; `created` already conveys the outcome)
    console.print(
        f"[green]initialized[/green] MemoryGuard store for project "
        f"[bold]{cfg.project_name}[/bold]"
    )
    console.print(f"  config: {cfg.config_file}")
    console.print(f"  store:  {cfg.db_path}")


# ---------------------------------------------------------------------------
# add
# ---------------------------------------------------------------------------


@app.command()
def add(
    ctx: typer.Context,
    content: str = typer.Argument(..., help="The memory content to store."),
    source_type: SourceType = typer.Option(
        SourceType.USER, "--source-type", help="Provenance source type."
    ),
    source_ref: str = typer.Option(
        ..., "--source-ref", help="Provenance reference (e.g. user://me)."
    ),
    scope: Scope = typer.Option(..., "--scope", help="Visibility scope."),
    scope_ref: Optional[str] = typer.Option(
        None, "--scope-ref", help="Scope reference (required for project/repo/user/session)."
    ),
    sensitivity: Sensitivity = typer.Option(
        Sensitivity.INTERNAL, "--sensitivity", help="Sensitivity tier."
    ),
    expires: Optional[str] = typer.Option(
        None, "--expires", help="Expiry as a duration (e.g. 30d) or ISO datetime."
    ),
    tag: Optional[list[str]] = typer.Option(
        None, "--tag", help="Tag to attach (repeatable)."
    ),
) -> None:
    """Add a memory manually and print its id + trust score."""
    state: CLIState = ctx.obj
    expires_at = _parse_expires(expires)
    tags = list(tag) if tag else []

    if state.is_remote:
        client = _get_remote(state)
        payload = {
            "content": content,
            "source_type": source_type.value,
            "source_ref": source_ref,
            "scope": scope.value,
            "scope_ref": scope_ref,
            "sensitivity": sensitivity.value,
            "expires_at": _iso(expires_at),
            "tags": tags,
        }
        try:
            created = client.create_memory(payload)
        except RemoteError as exc:
            raise _fail(str(exc))
        finally:
            client.close()
        memory_id = created.get("memory_id", "?")
        trust = created.get("trust_score", 0.0)
    else:
        try:
            engine, _ = _get_engine(state)
            record = engine.create_memory(
                content=content,
                source_type=source_type,
                source_ref=source_ref,
                scope=scope,
                scope_ref=scope_ref,
                sensitivity=sensitivity,
                expires_at=expires_at,
                tags=tags,
            )
        except (StoreInitError, ValueError) as exc:
            raise _fail(str(exc))
        memory_id = record.memory_id
        trust = record.trust_score

    console.print(
        f"[green]added[/green] memory [bold]{memory_id}[/bold] "
        f"(trust {float(trust):.2f})"
    )


# ---------------------------------------------------------------------------
# remember
# ---------------------------------------------------------------------------


@app.command()
def remember(
    ctx: typer.Context,
    content: str = typer.Argument(..., help="The project fact to remember."),
) -> None:
    """Friendly alias for adding a project-scoped user memory."""

    state: CLIState = ctx.obj
    if state.is_remote:
        raise _fail("remember is local-only in this release")
    try:
        engine, cfg = _get_engine(state)
        record = engine.create_memory(
            content=content,
            source_type=SourceType.USER,
            source_ref="user://me",
            scope=Scope.PROJECT,
            scope_ref=_project_scope_ref(cfg),
            sensitivity=Sensitivity.INTERNAL,
        )
    except (StoreInitError, ValueError) as exc:
        raise _fail(str(exc))
    console.print(
        f"[green]remembered[/green] memory [bold]{record.memory_id}[/bold] "
        f"(trust {record.trust_score:.2f})"
    )


# ---------------------------------------------------------------------------
# ingest
# ---------------------------------------------------------------------------


@app.command()
def ingest(
    ctx: typer.Context,
    path: Path = typer.Argument(..., help="File, folder, or git repo to ingest."),
    scope: Scope = typer.Option(..., "--scope", help="Visibility scope."),
    scope_ref: Optional[str] = typer.Option(
        None, "--scope-ref", help="Scope reference."
    ),
) -> None:
    """Ingest a file/folder/repo and print the number of memories created."""
    state: CLIState = ctx.obj

    if state.is_remote:
        client = _get_remote(state)
        payload = {
            "path": str(path),
            "scope": scope.value,
            "scope_ref": scope_ref,
        }
        try:
            result = client.ingest_path(payload)
        except RemoteError as exc:
            raise _fail(str(exc))
        finally:
            client.close()
        count = result.get("count")
        if count is None:
            count = len(result.get("memories", result.get("results", [])))
    else:
        try:
            engine, _ = _get_engine(state)
            created = engine.ingest_path(str(path), scope=scope, scope_ref=scope_ref)
        except (StoreInitError, FileNotFoundError, ValueError) as exc:
            raise _fail(str(exc))
        count = len(created)

    console.print(f"[green]ingested[/green] {count} memor{'y' if count == 1 else 'ies'}")


# ---------------------------------------------------------------------------
# query
# ---------------------------------------------------------------------------


@app.command()
def query(
    ctx: typer.Context,
    text: str = typer.Argument(..., help="The natural-language query."),
    scope: Optional[Scope] = typer.Option(None, "--scope", help="Restrict to scope."),
    scope_ref: Optional[str] = typer.Option(
        None, "--scope-ref", help="Restrict to scope reference."
    ),
    min_trust: float = typer.Option(0.0, "--min-trust", help="Trust floor [0..1]."),
    limit: int = typer.Option(5, "--limit", help="Maximum results."),
) -> None:
    """Run a trust-aware query and print results as a table."""
    state: CLIState = ctx.obj
    rows: list[dict] = []

    if state.is_remote:
        client = _get_remote(state)
        payload = {
            "text": text,
            "scope": scope.value if scope else None,
            "scope_ref": scope_ref,
            "min_trust": min_trust,
            "limit": limit,
        }
        try:
            response = client.query(payload)
        except RemoteError as exc:
            raise _fail(str(exc))
        finally:
            client.close()
        for item in response.get("results", []):
            memory = item.get("memory", {})
            rows.append(
                {
                    "content": memory.get("content", ""),
                    "source_ref": memory.get("source_ref", ""),
                    "trust_score": memory.get("trust_score", item.get("final_rank", 0.0)),
                    "reasons": item.get("reasons", []),
                }
            )
    else:
        try:
            engine, _ = _get_engine(state)
            results = engine.query(
                QuerySpec(
                    text=text,
                    scope=scope,
                    scope_ref=scope_ref,
                    min_trust=min_trust,
                    limit=limit,
                )
            )
        except (StoreInitError, ValueError) as exc:
            raise _fail(str(exc))
        for rm in results:
            rows.append(
                {
                    "content": rm.record.content,
                    "source_ref": rm.record.source_ref,
                    "trust_score": rm.record.trust_score,
                    "reasons": rm.reasons,
                }
            )

    if not rows:
        console.print("[yellow]no matching memories[/yellow]")
        return

    table = Table(title=f'query: "{text}"', show_lines=True)
    table.add_column("Content", overflow="fold")
    table.add_column("Source", style="cyan", overflow="fold")
    table.add_column("Trust", justify="right")
    table.add_column("Reasons", overflow="fold")
    for row in rows:
        table.add_row(
            _truncate(row["content"], 80),
            str(row["source_ref"]),
            f"{float(row['trust_score']):.2f}",
            "; ".join(str(r) for r in row["reasons"][:4]),
        )
    console.print(table)


# ---------------------------------------------------------------------------
# show
# ---------------------------------------------------------------------------


@app.command()
def show(
    ctx: typer.Context,
    memory_id: str = typer.Argument(..., help="The memory id to show."),
) -> None:
    """Show content + source + scope + trust breakdown + contradictions."""
    state: CLIState = ctx.obj

    if state.is_remote:
        client = _get_remote(state)
        try:
            memory = client.get(memory_id)
            if memory is None:
                raise _fail(f"memory {memory_id!r} not found")
            contradictions = client.contradictions(memory_id)
        except RemoteError as exc:
            raise _fail(str(exc))
        finally:
            client.close()
        content = memory.get("content", "")
        provenance = {
            "source_type": memory.get("source_type"),
            "source_ref": memory.get("source_ref"),
            "scope": memory.get("scope"),
            "scope_ref": memory.get("scope_ref"),
        }
        trust_score = memory.get("trust_score", 0.0)
        status = memory.get("status")
        sensitivity = memory.get("sensitivity")
        signals = memory.get("signals")
    else:
        try:
            engine, _ = _get_engine(state)
            record = engine.get(memory_id)
            if record is None:
                raise _fail(f"memory {memory_id!r} not found")
            explanation = engine.explain(memory_id)
        except StoreInitError as exc:
            raise _fail(str(exc))
        content = record.content
        provenance = explanation["provenance"]
        trust_score = explanation["trust_score"]
        status = explanation["status"]
        sensitivity = explanation["sensitivity"]
        signals = explanation["signals"]
        contradictions = explanation["contradictions"]

    console.print(f"[bold]{memory_id}[/bold]")
    console.print(f"  content:     {content}")
    console.print(f"  source:      {provenance.get('source_type')} {provenance.get('source_ref')}")
    console.print(f"  scope:       {provenance.get('scope')}/{provenance.get('scope_ref')}")
    console.print(f"  status:      {status}")
    console.print(f"  sensitivity: {sensitivity}")
    console.print(f"  trust score: [bold]{float(trust_score):.2f}[/bold]")

    if signals:
        table = Table(title="trust signals", show_header=True)
        table.add_column("Signal", style="cyan")
        table.add_column("Value", justify="right")
        for key, value in signals.items():
            try:
                table.add_row(key, f"{float(value):.3f}")
            except (TypeError, ValueError):
                table.add_row(key, str(value))
        console.print(table)

    _print_contradictions(contradictions)


# ---------------------------------------------------------------------------
# list
# ---------------------------------------------------------------------------


@app.command(name="list")
def list_memories(
    ctx: typer.Context,
    scope: Optional[Scope] = typer.Option(None, "--scope", help="Filter by scope."),
    status: Optional[MemoryStatus] = typer.Option(
        None, "--status", help="Filter by status."
    ),
) -> None:
    """List stored memories as a table."""
    state: CLIState = ctx.obj
    rows: list[dict] = []

    if state.is_remote:
        client = _get_remote(state)
        try:
            memories = client.list(
                scope=scope.value if scope else None,
                status=status.value if status else None,
            )
        except RemoteError as exc:
            raise _fail(str(exc))
        finally:
            client.close()
        rows = [
            {
                "memory_id": m.get("memory_id", ""),
                "content": m.get("content", ""),
                "scope": m.get("scope", ""),
                "scope_ref": m.get("scope_ref"),
                "trust_score": m.get("trust_score", 0.0),
                "status": m.get("status", ""),
            }
            for m in memories
        ]
    else:
        try:
            engine, _ = _get_engine(state)
            records = engine.store.list(scope=scope, status=status)
        except StoreInitError as exc:
            raise _fail(str(exc))
        rows = [
            {
                "memory_id": r.memory_id,
                "content": r.content,
                "scope": r.scope.value,
                "scope_ref": r.scope_ref,
                "trust_score": r.trust_score,
                "status": r.status.value,
            }
            for r in records
        ]

    if not rows:
        console.print("[yellow]no memories[/yellow]")
        return

    table = Table(title="memories", show_lines=False)
    table.add_column("ID", style="cyan", overflow="fold")
    table.add_column("Content", overflow="fold")
    table.add_column("Scope")
    table.add_column("Trust", justify="right")
    table.add_column("Status")
    for row in rows:
        scope_label = row["scope"]
        if row.get("scope_ref"):
            scope_label = f"{row['scope']}/{row['scope_ref']}"
        table.add_row(
            row["memory_id"],
            _truncate(row["content"], 60),
            scope_label,
            f"{float(row['trust_score']):.2f}",
            row["status"],
        )
    console.print(table)


# ---------------------------------------------------------------------------
# contradictions
# ---------------------------------------------------------------------------


@app.command()
def contradictions(
    ctx: typer.Context,
    memory_id: Optional[str] = typer.Argument(None, help="The memory id to inspect."),
) -> None:
    """List detected contradictions for one memory, or all contradiction pairs."""
    state: CLIState = ctx.obj

    if state.is_remote:
        if memory_id is None:
            raise _fail("remote contradiction listing requires a memory id")
        client = _get_remote(state)
        try:
            items = client.contradictions(memory_id)
        except RemoteError as exc:
            raise _fail(str(exc))
        finally:
            client.close()
    else:
        try:
            engine, _ = _get_engine(state)
            if memory_id is None:
                rows = []
                for record in engine.store.list():
                    for other_id in record.contradicts:
                        rows.append(
                            {
                                "memory_id": record.memory_id,
                                "source_ref": record.source_ref,
                                "status": record.status.value,
                                "reason": f"contradicts {other_id}",
                                "confidence": None,
                            }
                        )
                _print_contradictions(rows, standalone=True)
                return
            if engine.get(memory_id) is None:
                raise _fail(f"memory {memory_id!r} not found")
            items = engine.explain(memory_id)["contradictions"]
        except StoreInitError as exc:
            raise _fail(str(exc))

    _print_contradictions(items, standalone=True)


def _print_contradictions(items: list[dict], *, standalone: bool = False) -> None:
    """Render a contradictions table (or a friendly empty message)."""
    if not items:
        console.print("[green]no contradictions[/green]")
        return
    table = Table(title="contradictions", show_lines=True)
    table.add_column("Memory ID", style="cyan", overflow="fold")
    table.add_column("Source", overflow="fold")
    table.add_column("Confidence", justify="right")
    table.add_column("Reason", overflow="fold")
    for item in items:
        confidence = item.get("confidence")
        conf_str = f"{float(confidence):.2f}" if confidence is not None else "-"
        table.add_row(
            str(item.get("memory_id", "")),
            str(item.get("source_ref", "-")),
            conf_str,
            str(item.get("reason", "")),
        )
    console.print(table)


# ---------------------------------------------------------------------------
# capture
# ---------------------------------------------------------------------------


_CAPTURE_SOURCES = {"codex", "claude-code", "cursor", "text"}


def _capture_source(value: str) -> str:
    if value not in _CAPTURE_SOURCES:
        raise typer.BadParameter(
            "source must be one of: codex, claude-code, cursor, text"
        )
    if value == "codex":
        return "codex_transcript"
    if value == "claude-code":
        return "claude_code_transcript"
    if value == "cursor":
        return "cursor_transcript"
    return "text_file"


def _print_candidates(candidates) -> None:
    if not candidates:
        console.print("[yellow]no capture candidates[/yellow]")
        return
    table = Table(title="capture candidates", show_lines=True)
    table.add_column("ID", style="cyan", overflow="fold")
    table.add_column("Status")
    table.add_column("Candidate", overflow="fold")
    table.add_column("Conf", justify="right")
    table.add_column("Source", overflow="fold")
    table.add_column("Sensitivity")
    for candidate in candidates:
        text = candidate.canonical_content or candidate.content
        table.add_row(
            candidate.id,
            candidate.status.value,
            _truncate(text, 72),
            f"{candidate.confidence:.2f}",
            candidate.source_ref,
            candidate.sensitivity.value,
        )
    console.print(table)


@capture_app.command("file")
def capture_file(
    ctx: typer.Context,
    path: Path = typer.Argument(..., help="Transcript or text log to capture."),
    source: str = typer.Option(
        "text",
        "--source",
        help="Transcript source: codex, claude-code, cursor, or text.",
    ),
) -> None:
    """Extract pending memory candidates from a local transcript/text file."""

    state: CLIState = ctx.obj
    if state.is_remote:
        raise _fail("capture is local-only in this release")
    try:
        _, cfg = _get_engine(state)
        candidates = ingest_capture_file(
            cfg.root,
            path,
            source_type=_capture_source(source),
        )
    except (StoreInitError, OSError, ValueError) as exc:
        raise _fail(str(exc))
    _print_candidates(candidates)
    console.print(
        "Next: run `memoryguard capture pending`, then "
        "`memoryguard capture approve --all`, then `memoryguard sync`."
    )


@capture_app.command("pending")
def capture_pending(ctx: typer.Context) -> None:
    """Show pending capture candidates awaiting approval."""

    state: CLIState = ctx.obj
    if state.is_remote:
        raise _fail("capture is local-only in this release")
    try:
        _, cfg = _get_engine(state)
        candidates = list_candidates(cfg.root, status=CaptureStatus.PENDING)
    except StoreInitError as exc:
        raise _fail(str(exc))
    _print_candidates(candidates)


@capture_app.command("approve")
def capture_approve(
    ctx: typer.Context,
    candidate_id: Optional[str] = typer.Argument(
        None, help="Candidate id to approve."
    ),
    all_: bool = typer.Option(
        False, "--all", help="Approve all safe high-confidence pending candidates."
    ),
) -> None:
    """Approve one candidate, or all safe candidates with ``--all``."""

    state: CLIState = ctx.obj
    if state.is_remote:
        raise _fail("capture is local-only in this release")
    if not all_ and not candidate_id:
        raise _fail("provide a candidate_id or use --all")
    try:
        engine, cfg = _get_engine(state)
        if all_:
            approved = approve_all_safe_candidates(
                cfg.root,
                engine,
                scope_ref=_project_scope_ref(cfg),
            )
            console.print(
                f"[green]approved[/green] {len(approved)} capture candidate"
                f"{'' if len(approved) == 1 else 's'}"
            )
            return
        candidate, memory_id = approve_candidate(
            cfg.root,
            engine,
            str(candidate_id),
            scope_ref=_project_scope_ref(cfg),
        )
    except (StoreInitError, KeyError, ValueError) as exc:
        raise _fail(str(exc).strip("'\""))
    detail = f" -> memory {memory_id}" if memory_id else ""
    console.print(f"[green]approved[/green] {candidate.id}{detail}")


@capture_app.command("reject")
def capture_reject(
    ctx: typer.Context,
    candidate_id: str = typer.Argument(..., help="Candidate id to reject."),
) -> None:
    """Reject one capture candidate."""

    state: CLIState = ctx.obj
    if state.is_remote:
        raise _fail("capture is local-only in this release")
    try:
        _, cfg = _get_engine(state)
        candidate = reject_candidate(cfg.root, candidate_id)
    except (StoreInitError, KeyError) as exc:
        raise _fail(str(exc).strip("'\""))
    console.print(f"[green]rejected[/green] {candidate.id}")


@capture_app.command("clear-rejected")
def capture_clear_rejected(ctx: typer.Context) -> None:
    """Clear rejected capture candidates from the local queue."""

    state: CLIState = ctx.obj
    if state.is_remote:
        raise _fail("capture is local-only in this release")
    try:
        _, cfg = _get_engine(state)
        removed = clear_rejected_candidates(cfg.root)
    except StoreInitError as exc:
        raise _fail(str(exc))
    console.print(f"[green]cleared[/green] {removed} rejected candidate(s)")


# ---------------------------------------------------------------------------
# resolve
# ---------------------------------------------------------------------------


@app.command()
def resolve(
    ctx: typer.Context,
    old_id: str = typer.Argument(..., help="The outdated memory id."),
    superseded_by: str = typer.Option(
        ..., "--superseded-by", help="The newer memory id that supersedes old_id."
    ),
) -> None:
    """Resolve a conflict by marking an older memory as superseded."""

    state: CLIState = ctx.obj
    if state.is_remote:
        raise _fail("resolve is local-only in this release")
    try:
        engine, _ = _get_engine(state)
        old, new = engine.resolve_supersession(old_id, superseded_by)
    except (StoreInitError, KeyError, ValueError) as exc:
        raise _fail(str(exc).strip("'\""))
    console.print(
        f"[green]resolved[/green] {old.memory_id} -> superseded by "
        f"[bold]{new.memory_id}[/bold]"
    )


# ---------------------------------------------------------------------------
# correct
# ---------------------------------------------------------------------------


@app.command()
def correct(
    ctx: typer.Context,
    memory_id: str = typer.Argument(..., help="The memory id to correct."),
    new_content: str = typer.Argument(..., help="The corrected content."),
) -> None:
    """Correct a memory (lineage preserved) and print the new id."""
    state: CLIState = ctx.obj

    if state.is_remote:
        client = _get_remote(state)
        try:
            created = client.correct(memory_id, new_content)
        except RemoteError as exc:
            raise _fail(str(exc))
        finally:
            client.close()
        new_id = created.get("memory_id", "?")
    else:
        try:
            engine, _ = _get_engine(state)
            record = engine.correct_memory(memory_id, new_content)
        except (StoreInitError, KeyError, ValueError) as exc:
            raise _fail(str(exc).strip("'\""))
        new_id = record.memory_id

    console.print(
        f"[green]corrected[/green] [bold]{memory_id}[/bold] -> new memory "
        f"[bold]{new_id}[/bold] (old marked corrected)"
    )


# ---------------------------------------------------------------------------
# rm
# ---------------------------------------------------------------------------


@app.command()
def rm(
    ctx: typer.Context,
    memory_id: str = typer.Argument(..., help="The memory id to soft-delete."),
) -> None:
    """Soft-delete a memory (status -> deleted; still retained)."""
    state: CLIState = ctx.obj

    if state.is_remote:
        client = _get_remote(state)
        try:
            client.delete(memory_id)
        except RemoteError as exc:
            raise _fail(str(exc))
        finally:
            client.close()
    else:
        try:
            engine, _ = _get_engine(state)
            if engine.get(memory_id) is None:
                raise _fail(f"memory {memory_id!r} not found")
            engine.store.soft_delete(memory_id)
        except StoreInitError as exc:
            raise _fail(str(exc))

    console.print(f"[green]soft-deleted[/green] memory [bold]{memory_id}[/bold]")


# ---------------------------------------------------------------------------
# context sync
# ---------------------------------------------------------------------------


def _context_plan(ctx: typer.Context):
    state: CLIState = ctx.obj
    if state.is_remote:
        raise _fail("context sync is local-only; omit --remote and use a local store")
    try:
        engine, cfg = _get_engine(state)
    except StoreInitError as exc:
        raise _fail(str(exc))
    return build_context_sync_plan(cfg.root, engine.store), cfg


def _print_context_diff(plan) -> None:
    diff = format_unified_diff(plan)
    if diff:
        console.print(diff)
    else:
        console.print("[green]context files are up to date[/green]")


def _prepare_context_sync(ctx: typer.Context, *, yes: bool, label: str) -> None:
    plan, cfg = _context_plan(ctx)
    _print_context_diff(plan)
    if not plan.has_changes:
        return

    if yes:
        write_pending_context_plan(plan)
        count, files = approve_context_sync(cfg.root)
        console.print(
            f"[green]{label} applied[/green] {count} file"
            f"{'' if count == 1 else 's'}: {', '.join(files)}"
        )
        return

    write_pending_context_plan(plan)
    console.print(
        "[yellow]pending approval[/yellow] "
        "Run `memoryguard context approve` to write these changes, or rerun with --yes."
    )


@app.command()
def sync(ctx: typer.Context) -> None:
    """Generate and approve all MemoryGuard context files in one command."""

    plan, cfg = _context_plan(ctx)
    write_pending_context_plan(plan)
    count, files = approve_context_sync(cfg.root)
    if count:
        console.print(
            f"[green]sync applied[/green] {count} file"
            f"{'' if count == 1 else 's'}: {', '.join(files)}"
        )
    else:
        console.print("[green]context files are up to date[/green]")


@context_app.command("generate")
def context_generate(
    ctx: typer.Context,
    yes: bool = typer.Option(False, "--yes", "-y", help="Write files without prompting."),
) -> None:
    """Generate agent context files, showing the diff before any write."""

    _prepare_context_sync(ctx, yes=yes, label="context generation")


@context_app.command("update")
def context_update(
    ctx: typer.Context,
    yes: bool = typer.Option(False, "--yes", "-y", help="Write files without prompting."),
) -> None:
    """Refresh generated agent context files from current memories and repo metadata."""

    _prepare_context_sync(ctx, yes=yes, label="context update")


@context_app.command("diff")
def context_diff(ctx: typer.Context) -> None:
    """Show the pending context diff, or compute the current diff."""

    state: CLIState = ctx.obj
    if state.is_remote:
        raise _fail("context sync is local-only; omit --remote and use a local store")
    try:
        engine, cfg = _get_engine(state)
    except StoreInitError as exc:
        raise _fail(str(exc))
    status_view = context_status(cfg.root, engine.store)
    if status_view.pending_diff:
        console.print(status_view.pending_diff)
        return
    _print_context_diff(build_context_sync_plan(cfg.root, engine.store))


@context_app.command("approve")
def context_approve(ctx: typer.Context) -> None:
    """Apply the last pending context diff."""

    state: CLIState = ctx.obj
    if state.is_remote:
        raise _fail("context sync is local-only; omit --remote and use a local store")
    try:
        _, cfg = _get_engine(state)
        count, files = approve_context_sync(cfg.root)
    except (StoreInitError, FileNotFoundError) as exc:
        raise _fail(str(exc))
    console.print(
        f"[green]approved[/green] {count} file{'' if count == 1 else 's'}"
        + (f": {', '.join(files)}" if files else "")
    )


@context_app.command("watch")
def context_watch(
    ctx: typer.Context,
    yes: bool = typer.Option(False, "--yes", "-y", help="Apply detected changes immediately."),
    once: bool = typer.Option(False, "--once", help="Run one watch cycle and exit."),
    interval: float = typer.Option(5.0, "--interval", min=1.0, help="Polling interval in seconds."),
) -> None:
    """Watch for context drift and maintain a pending diff or approved files."""

    console.print("[green]watching[/green] MemoryGuard context state")
    while True:
        plan, cfg = _context_plan(ctx)
        if plan.has_changes:
            if yes:
                write_pending_context_plan(plan)
                count, files = approve_context_sync(cfg.root)
                console.print(f"[green]updated[/green] {count} file(s): {', '.join(files)}")
            else:
                write_pending_context_plan(plan)
                console.print(
                    "[yellow]pending context diff[/yellow] "
                    "Run `memoryguard context approve` to apply it."
                )
        elif once:
            console.print("[green]context files are up to date[/green]")
        if once:
            return
        time.sleep(interval)


# ---------------------------------------------------------------------------
# dashboard
# ---------------------------------------------------------------------------


@app.command()
def dashboard(ctx: typer.Context) -> None:
    """Print instructions to launch the local dashboard.

    Long-running servers are started manually, so this command prints the
    commands rather than blocking.
    """
    state: CLIState = ctx.obj
    console.print("[bold]MemoryGuard local dashboard[/bold]")
    console.print("The dashboard is a Vite app served from packages/local-dashboard.")
    console.print("Start the API and the dashboard in separate terminals:\n")
    console.print("  1. API:        [cyan]uvicorn memoryguard_api.main:app --host 127.0.0.1 --port 8000[/cyan]")
    console.print("  2. Dashboard:  [cyan]pnpm --filter @memoryguard/local-dashboard dev[/cyan]")
    console.print("\nThen open the printed local URL (default http://localhost:5173).")
    if not state.is_remote:
        try:
            _, cfg = _get_engine(state)
            console.print(f"\nActive store: {cfg.db_path}")
        except StoreInitError:
            console.print(
                "\n[yellow]No local store found — run `memoryguard init` first.[/yellow]"
            )


# ---------------------------------------------------------------------------
# mcp
# ---------------------------------------------------------------------------


@app.command()
def mcp(ctx: typer.Context) -> None:
    """Start the MCP server (stdio), or print instructions if unavailable."""
    entry = _resolve_mcp_entry()
    if entry is not None:
        console.print("[green]starting MemoryGuard MCP server (stdio)…[/green]")
        entry()
        return

    console.print("[bold]MemoryGuard MCP server[/bold]")
    console.print(
        "The MCP server exposes memory_search / memory_add / memory_explain to "
        "AI coding agents over stdio."
    )
    console.print("Start it with:\n")
    console.print("  [cyan]python -m memoryguard_mcp[/cyan]")
    console.print(
        "\nThen register the stdio command in your agent's MCP configuration."
    )


@app.command()
def demo(ctx: typer.Context) -> None:
    """Run the Agent Capture demo in a temporary project."""

    state: CLIState = ctx.obj
    if state.is_remote:
        raise _fail("demo is local-only in this release")

    transcript_text = _demo_transcript_text()
    with tempfile.TemporaryDirectory(prefix="memoryguard-agent-capture-demo-") as tmp:
        project = Path(tmp)
        transcript = project / "codex-session.txt"
        transcript.write_text(transcript_text, encoding="utf-8")
        engine = None
        try:
            cfg, _created = init_store(project, project_name="agent-capture-demo")
            engine = build_local_engine(str(cfg.db_path))
            for fact in (
                "This project uses FastAPI for the backend.",
                "This project uses npm.",
                "This project uses MySQL as the database.",
            ):
                engine.create_memory(
                    content=fact,
                    source_type=SourceType.USER,
                    source_ref="user://me",
                    scope=Scope.PROJECT,
                    scope_ref=_project_scope_ref(cfg),
                    sensitivity=Sensitivity.INTERNAL,
                )
            candidates = ingest_capture_file(
                cfg.root,
                transcript,
                source_type="codex_transcript",
            )
            approved = approve_all_safe_candidates(
                cfg.root,
                engine,
                scope_ref=_project_scope_ref(cfg),
            )
            plan = build_context_sync_plan(cfg.root, engine.store)
            write_pending_context_plan(plan)
            count, _files = approve_context_sync(cfg.root)
            agents = (cfg.root / "AGENTS.md").read_text(encoding="utf-8")
            context_text = "\n".join(
                (cfg.root / rel_path).read_text(encoding="utf-8")
                for rel_path in CONTEXT_FILES
            )
            _assert_demo_passed(agents, context_text, cfg.root)
        except Exception as exc:
            raise _fail(f"demo failed: {exc}")
        finally:
            if engine is not None and hasattr(engine.store, "close"):
                engine.store.close()

    console.print(
        "[green]Agent Capture demo passed.[/green] "
        f"Extracted {len(candidates)} candidates, approved {len(approved)}, "
        f"wrote {count} context files."
    )


def _resolve_mcp_entry():
    """Return a callable MCP stdio entry point if the server package exposes one."""
    try:  # pragma: no cover - depends on optional mcp-server package state
        import memoryguard_mcp  # type: ignore

        for attr in ("serve", "main", "run", "run_stdio"):
            entry = getattr(memoryguard_mcp, attr, None)
            if callable(entry):
                return entry
        try:
            from memoryguard_mcp.server import main as server_main  # type: ignore

            return server_main
        except Exception:
            return None
    except Exception:
        return None


def _demo_transcript_text() -> str:
    repo_root = Path(__file__).resolve().parents[3]
    fixture = repo_root / "examples" / "agent-capture-demo" / "transcript.txt"
    if fixture.is_file():
        return fixture.read_text(encoding="utf-8")
    return (
        "We moved from FastAPI to Flask.\n"
        "Use pnpm instead of npm.\n"
        "Local dev uses SQLite but production uses PostgreSQL.\n"
        "Deploy this on Vercel.\n"
        "Tests run with pnpm test.\n"
        "Frontend is React.\n"
        "Do not add external LLM API dependencies.\n"
        "The previous MySQL plan is dead.\n"
        "The API key is sk-test-1234567890abcdef.\n"
    )


def _assert_demo_passed(agents: str, context_text: str, root: Path) -> None:
    required = (
        "Backend framework: Flask",
        "FastAPI",
        "Package manager: pnpm",
        "npm",
        "Local database: SQLite",
        "Cloud database: PostgreSQL",
        "Deployment target: Vercel",
        "Test command: pnpm test",
    )
    for item in required:
        if item not in agents:
            raise ValueError(f"missing demo assertion: {item}")
    if "sk-test-1234567890abcdef" in context_text:
        raise ValueError("fake secret leaked into generated context")
    for rel_path in CONTEXT_FILES:
        if not (root / rel_path).is_file():
            raise ValueError(f"missing context file: {rel_path}")


# ---------------------------------------------------------------------------
# status
# ---------------------------------------------------------------------------


@app.command()
def status(ctx: typer.Context) -> None:
    """Show the active project, memory counts, context files, and next step."""
    state: CLIState = ctx.obj

    console.print(f"[bold]mode:[/bold] {state.mode}")

    if state.is_remote:
        console.print(f"[bold]remote:[/bold] {state.remote}")
        client = _get_remote(state)
        try:
            health = client.health()
        except RemoteError as exc:
            raise _fail(str(exc))
        finally:
            client.close()
        flags = health.get("flags", {})
        counts = health.get("counts") or health.get("stats") or {}
        _print_status(flags, counts)
        return

    try:
        engine, cfg = _get_engine(state)
    except StoreInitError as exc:
        raise _fail(str(exc))

    console.print(f"[bold]project:[/bold] {cfg.project_name}")
    console.print(f"[bold]root:[/bold] {cfg.root}")
    console.print(f"[bold]store:[/bold] {cfg.db_path}")

    active_memories = len(engine.store.list(status=MemoryStatus.ACTIVE))
    pending_candidates = len(list_candidates(cfg.root, status=CaptureStatus.PENDING))
    rejected_candidates = len(list_candidates(cfg.root, status=CaptureStatus.REJECTED))
    deprecated_statuses = {
        MemoryStatus.CORRECTED,
        MemoryStatus.DISPUTED,
        MemoryStatus.EXPIRED,
        MemoryStatus.OUTDATED,
        MemoryStatus.SUPERSEDED,
    }
    deprecated_memories = sum(
        len(engine.store.list(status=status)) for status in deprecated_statuses
    )

    ctable = Table(title="project status", show_header=True)
    ctable.add_column("Metric", style="cyan")
    ctable.add_column("Value", justify="right")
    ctable.add_row("active memories", str(active_memories))
    ctable.add_row("pending capture candidates", str(pending_candidates))
    ctable.add_row("rejected capture candidates", str(rejected_candidates))
    ctable.add_row("superseded/deprecated memories", str(deprecated_memories))
    console.print(ctable)

    ftable = Table(title="context files", show_header=True)
    ftable.add_column("File", style="cyan", overflow="fold")
    ftable.add_column("Exists", justify="center")
    missing_context = False
    for rel_path in CONTEXT_FILES:
        exists = (cfg.root / rel_path).is_file()
        missing_context = missing_context or not exists
        ftable.add_row(rel_path, "yes" if exists else "no")
    console.print(ftable)

    if active_memories == 0:
        next_command = 'memoryguard remember "This project uses ..."'
    elif pending_candidates > 0:
        next_command = "memoryguard capture pending"
    elif missing_context:
        next_command = "memoryguard sync"
    else:
        next_command = "memoryguard status"
    console.print(f"[bold]suggested next command:[/bold] {next_command}")


def _print_status(flags: dict, counts: dict) -> None:
    """Render the counts table and the active feature-flag table."""
    if counts:
        ctable = Table(title="store stats", show_header=True)
        ctable.add_column("Metric", style="cyan")
        ctable.add_column("Count", justify="right")
        for key, value in counts.items():
            ctable.add_row(str(key), str(value))
        console.print(ctable)

    if flags:
        active = sorted(name for name, on in flags.items() if on)
        inactive = sorted(name for name, on in flags.items() if not on)
        ftable = Table(title="feature flags", show_header=True)
        ftable.add_column("State", style="cyan")
        ftable.add_column("Flags", overflow="fold")
        ftable.add_row("[green]active[/green]", ", ".join(active) or "-")
        ftable.add_row("[dim]inactive[/dim]", ", ".join(inactive) or "-")
        console.print(ftable)


# ---------------------------------------------------------------------------
# Optional SDK-backed remote adapter
# ---------------------------------------------------------------------------


class _SdkRemoteAdapter:  # pragma: no cover - exercised only when SDK installed
    """Adapt the official Python SDK remote client to the RemoteClient surface.

    Used only when ``from memoryguard import MemoryGuard`` is importable. All
    results are normalized to plain dicts/lists so the command layer renders
    SDK-backed and httpx-backed remote results identically.
    """

    def __init__(self, sdk) -> None:
        self._sdk = sdk

    @staticmethod
    def _to_dict(obj):
        if obj is None or isinstance(obj, (dict, list)):
            return obj
        from dataclasses import asdict, is_dataclass

        if is_dataclass(obj):
            return asdict(obj)
        if hasattr(obj, "__dict__"):
            return dict(vars(obj))
        return obj

    def create_memory(self, payload: dict) -> dict:
        return self._to_dict(self._sdk.add(**payload))

    def get(self, memory_id: str):
        return self._to_dict(self._sdk.get(memory_id))

    def list(self, *, scope=None, scope_ref=None, status=None) -> list:
        results = self._sdk.list(scope=scope, scope_ref=scope_ref, status=status)
        return [self._to_dict(r) for r in (results or [])]

    def query(self, payload: dict) -> dict:
        return self._to_dict(self._sdk.query(**payload))

    def ingest_path(self, payload: dict) -> dict:
        return self._to_dict(self._sdk.ingest_path(**payload))

    def contradictions(self, memory_id: str) -> list:
        results = self._sdk.contradictions(memory_id)
        return [self._to_dict(r) for r in (results or [])]

    def correct(self, memory_id: str, new_content: str) -> dict:
        return self._to_dict(self._sdk.correct(memory_id, new_content))

    def delete(self, memory_id: str) -> None:
        self._sdk.delete(memory_id)

    def health(self) -> dict:
        return self._to_dict(self._sdk.health()) or {}

    def close(self) -> None:
        closer = getattr(self._sdk, "close", None)
        if callable(closer):
            closer()


if __name__ == "__main__":  # pragma: no cover
    app()
