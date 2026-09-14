# MemoryGuard

**MemoryGuard keeps AI coding-agent context files current and secret-safe.**

**Stop re-explaining your project to AI coding agents.**

MemoryGuard is a local-first developer CLI that turns durable project facts into generated context files for coding agents. It helps keep `AGENTS.md`, `CLAUDE.md`, `MEMORY.md`, and Cursor rules aligned with the current truth of your project, while redacting secret-looking values before they reach generated context.

## Before / After

Before:

- "This project uses FastAPI."
- "This project uses npm."
- "API key: sk-test-..." (fake example)

After MemoryGuard:

- "Backend framework: Flask"
- "Package manager: pnpm"
- "FastAPI was previously used; superseded by Flask."
- "Sensitive memory omitted from generated context."

## The Problem

AI coding agents forget project decisions between sessions. Humans end up re-explaining the stack, commands, constraints, and gotchas. Worse, old facts can linger in context files after the project changes, and raw transcripts can accidentally contain secrets.

## What MemoryGuard Does

- Stores approved project memories locally.
- Detects obvious supersession, such as FastAPI -> Flask or npm -> pnpm.
- Captures candidate memories from local coding-agent transcripts.
- Keeps candidates pending until you approve or reject them.
- Generates context files for common coding-agent tools.
- Redacts common secret-looking values before display, storage, and rendering.
- Runs without a cloud service or required external LLM API.

## 2-Minute Demo (one-line install)

**Windows (PowerShell):**

```powershell
irm https://raw.githubusercontent.com/atharvmantri/MemoryGuard/main/scripts/install.ps1 | iex
```

**macOS / Linux:**

```bash
curl -fsSL https://raw.githubusercontent.com/atharvmantri/MemoryGuard/main/scripts/install.sh | bash
```

After the one-line install finishes, run `memoryguard doctor` then
`memoryguard demo`. The demo runs in a temporary project and proves
transcript capture, pending approval, safe approval, context sync,
supersession, and fake-secret non-leakage.

Prerequisites: `git` and `uv` (Node/pnpm are **not** required for the CLI).
The installer prints clear install instructions for either missing tool and
exits non-zero rather than partially installing.

**Uninstall:**

```powershell
# Windows
powershell -ExecutionPolicy Bypass -File "$env:LOCALAPPDATA\MemoryGuard\source\scripts\uninstall.ps1"

# macOS / Linux
bash ~/.local/share/memoryguard/source/scripts/uninstall.sh
```

Add `-RemoveSource` / `--remove-source` to also delete the cloned source dir.
User project `.memoryguard/` stores are never touched.

## Quickstart (manual / advanced)

If you would rather work from a clone (e.g. to inspect the source while
debugging), the per-repo install script is still supported:

```bash
git clone https://github.com/atharvmantri/MemoryGuard.git
cd MemoryGuard

# Windows (PowerShell)
powershell -ExecutionPolicy Bypass -File scripts/install-alpha.ps1

# macOS / Linux
bash scripts/install-alpha.sh
```

After the installer finishes:

```bash
memoryguard init
memoryguard remember "This project uses Flask for the backend."
memoryguard sync
memoryguard status
```

The per-repo installer (`scripts/install-alpha.{ps1,sh}`) does the same job
as the one-line bootstrap: it verifies `uv`, runs `uv sync
--all-packages --dev`, writes the `memoryguard` wrapper to your `PATH`,
detects any pre-existing `memoryguard` files on `PATH` (e.g. a stale
`memoryguard.exe` from a prior Python install) and warns about the
collision. Pass `-RemoveShadowingCommands` / `--remove-shadowing-commands`
to move the offending file out of the way automatically. Pass
`-NoPathUpdate` / `--no-path-update` to skip the `PATH` writes entirely.

### Troubleshooting PATH

If `memoryguard` does not resolve in a new shell, check which one Windows or
POSIX is finding first:

```powershell
# Windows PowerShell
Get-Command memoryguard -All

# cmd.exe
where memoryguard
```

Expected: `%LOCALAPPDATA%\Programs\MemoryGuard\memoryguard.cmd` (or
`memoryguard.ps1`).

If a stale `memoryguard.exe` from a prior Python install (e.g.
`C:\Users\<you>\AppData\Local\Programs\Python\Python312\Scripts\memoryguard.exe`)
still wins on `PATH`, re-run the install with `-RemoveShadowingCommands`:

```powershell
powershell -ExecutionPolicy Bypass -File "$env:LOCALAPPDATA\MemoryGuard\source\scripts\install-alpha.ps1" -RemoveShadowingCommands
```

```bash
bash ~/.local/share/memoryguard/source/scripts/install-alpha.sh --remove-shadowing-commands
```

The flag moves the offending file out of the way to
`memoryguard.disabled-by-memoryguard` in the same directory, so the alpha
wrapper is now first on `PATH`.

### Verified public alpha one-line install

The raw GitHub URLs below are live on `main` and were verified on 2026-09-14.
Use them when you want the alpha wrapper directly:

```powershell
# Windows (PowerShell)
irm https://raw.githubusercontent.com/atharvmantri/MemoryGuard/main/scripts/install-alpha.ps1 | iex
```

```bash
# macOS / Linux
curl -fsSL https://raw.githubusercontent.com/atharvmantri/MemoryGuard/main/scripts/install-alpha.sh | bash
```

The clone + run local script flow above remains available for contributors who
want to inspect or modify the source before installing.

## Agent Capture Demo

Capture a local transcript or plain text log:

```bash
memoryguard capture file ./codex-session.txt --source codex
memoryguard capture pending
memoryguard capture approve --all
memoryguard sync
```

Or run the scripted demo:

```powershell
powershell -ExecutionPolicy Bypass -File examples\agent-capture-demo\run-demo.ps1
```

```bash
bash examples/agent-capture-demo/run-demo.sh
```

## Commands

```bash
memoryguard init
memoryguard remember "This project uses Flask for the backend."
memoryguard capture file ./codex-session.txt --source codex
memoryguard capture pending
memoryguard capture approve --all
memoryguard capture reject <candidate_id>
memoryguard capture clear-rejected
memoryguard sync
memoryguard status
memoryguard doctor
memoryguard demo
```

## What Files It Generates

MemoryGuard Context Sync writes managed blocks to:

- `AGENTS.md`
- `CLAUDE.md`
- `MEMORY.md`
- `.cursor/rules/memoryguard.mdc`

Generated context is derived from approved memories and lightweight repo metadata. You can rerun `memoryguard sync` whenever project truth changes.

## Why Not Just Write AGENTS.md Manually?

Manual context files are useful, but they drift. MemoryGuard adds a small local workflow around them:

- capture candidate facts from transcripts,
- approve only durable project facts,
- mark old decisions as superseded,
- omit secret-looking content,
- regenerate several agent context formats consistently.

## Local-First / No Cloud / No Required LLM API

The public alpha runs locally. The default store is SQLite under `.memoryguard/`. The extractor is deterministic and rules-first. No external LLM API is required for the core workflow.

## Secret Safety

MemoryGuard redacts common secret-looking values before displaying capture candidates, storing approved memories, or rendering context files. Do not treat this as a complete security product: review pending candidates and generated files before relying on them.

## Comparison With projectmem

projectmem is stronger today for event-sourced memory, pre-commit warnings, cross-project memory, and judgment workflows.

MemoryGuard has a different focus: context-file sync for `AGENTS.md`, `CLAUDE.md`, `MEMORY.md`, and Cursor rules. MemoryGuard's wedge is current project truth, supersession of outdated decisions, and secret-safe generated context. Different focus, not fake superiority.

## Roadmap

- Better transcript adapters for Codex, Claude Code, and Cursor.
- Richer review UI for pending candidates.
- Stronger local extractors behind the same deterministic candidate interface.
- More context-file targets.
- Cleaner package installation for public alpha users.

## OSS vs Future Hosted Cloud

This public alpha is the local-first OSS CLI and libraries. A hosted/cloud product may arrive later, but it is not included here and is not required for the OSS workflow.

## License

Apache-2.0. See [LICENSE](./LICENSE).
