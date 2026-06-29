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

## 2-Minute Demo

Clone, run the one-time alpha installer, then `memoryguard demo`:

```bash
git clone https://github.com/atharvmantri/MemoryGuard.git
cd MemoryGuard

# Windows (PowerShell)
powershell -ExecutionPolicy Bypass -File scripts/install-alpha.ps1

# macOS / Linux
bash scripts/install-alpha.sh

memoryguard doctor
memoryguard demo
```

The demo runs in a temporary project and proves transcript capture, pending approval, safe approval, context sync, supersession, and fake-secret non-leakage.

## Quickstart

There is no PyPI or npm package to install during alpha. The supported flow
is clone + the one-time `scripts/install-alpha.{ps1,sh}` installer, which
sets up a thin `memoryguard` wrapper on your `PATH` and runs `uv sync --dev`
under the hood. Node/pnpm are only needed for the local dashboard and
TypeScript SDK — not for the CLI.

After the installer finishes:

```bash
memoryguard init
memoryguard remember "This project uses Flask for the backend."
memoryguard sync
memoryguard status
```

### Available after this update is pushed and verified

Once this update is pushed to `main` on the public repo and the raw GitHub
URLs below are verified to resolve, a one-line install will be available:

```powershell
# Windows (PowerShell)
irm https://raw.githubusercontent.com/atharvmantri/MemoryGuard/main/scripts/install-alpha.ps1 | iex
```

```bash
# macOS / Linux
curl -fsSL https://raw.githubusercontent.com/atharvmantri/MemoryGuard/main/scripts/install-alpha.sh | bash
```

Until those raw URLs are verified live from `raw.githubusercontent.com`,
the one-liner is **not** the recommended quickstart. Use the clone + run
local script flow above.

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
