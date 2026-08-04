# Getting Started

This guide walks through local setup and the Phase 1 quickstart developer flow:

```
init → add → ingest → query → show → contradictions → mcp → dashboard
```

Every step runs locally against the open-source core with **no commercial module and
no cloud service**, and with **no required external LLM API**. A machine with zero
network access can complete the entire flow.

## Prerequisites

- **Python 3.11+** — for `packages/core`, `packages/cli`, `packages/mcp-server`,
  `packages/sdk-python`, the model layer, and `apps/api`.
- **Node.js 18.18+** with **pnpm 9** — for the TypeScript SDK and the local dashboard.
- **Git** — for repository ingestion.

The local store is a single SQLite database file. No external database or LLM service
is required.

## Install

There is no PyPI or npm package to install during the public alpha. The
supported alpha path is the one-command bootstrap that clones (or updates) the
public repo into a stable source directory, runs `uv sync --all-packages
--dev` against it, and writes the `memoryguard` wrapper to your `PATH` in one
shot.

**Windows (PowerShell):**

```powershell
irm https://raw.githubusercontent.com/atharvmantri/MemoryGuard/main/scripts/install.ps1 | iex
```

**macOS / Linux:**

```bash
curl -fsSL https://raw.githubusercontent.com/atharvmantri/MemoryGuard/main/scripts/install.sh | bash
```

Prerequisites: `git` and `uv`. The installer prints clear install instructions
for either missing tool and exits non-zero rather than partially installing.
Node and pnpm are **not** required for the CLI &mdash; they are only needed
for the local dashboard and the TypeScript SDK.

After the bootstrap finishes, the wrapper is on your `PATH` and
`memoryguard doctor` plus `memoryguard demo` work from any shell, including
protected cwds like `C:\Windows\System32`.

The bootstrap clones into a stable location
(`%LOCALAPPDATA%\MemoryGuard\source` on Windows, `~/.local/share/memoryguard/source`
on macOS / Linux; override with `MEMORYGUARD_SOURCE_DIR` or `--source-dir`).
Re-running the bootstrap performs a `git pull --ff-only` against `main` and
re-installs the wrapper, so the same one-liner updates an existing alpha
install.

To uninstall the wrapper (and the user-PATH entry the installer added):

```powershell
# Windows
powershell -ExecutionPolicy Bypass -File "$env:LOCALAPPDATA\MemoryGuard\source\scripts\uninstall.ps1"

# macOS / Linux
bash ~/.local/share/memoryguard/source/scripts/uninstall.sh
```

Add `-RemoveSource` / `--remove-source` to also delete the cloned source dir.
User project `.memoryguard/` stores are never touched.

### Manual / advanced install (from a clone)

If you would rather work from a checkout (e.g. to inspect the source while
debugging), the per-repo install script is still supported:

```bash
git clone https://github.com/atharvmantri/MemoryGuard.git
cd MemoryGuard

# Windows (PowerShell)
powershell -ExecutionPolicy Bypass -File scripts/install-alpha.ps1

# macOS / Linux
bash scripts/install-alpha.sh
```

The per-repo installer does the same job as the one-line bootstrap on the
already-cloned tree: it verifies `uv`, runs `uv sync --all-packages --dev`,
writes the `memoryguard` wrapper, detects any pre-existing `memoryguard`
files on `PATH` (e.g. a stale `memoryguard.exe` from a prior Python install)
and warns about the collision. Pass `-RemoveShadowingCommands` /
`--remove-shadowing-commands` to move the offending file out of the way.
Pass `-NoPathUpdate` / `--no-path-update` to skip the `PATH` writes entirely.

### Advanced: running from source without the wrapper

If you would rather not install the wrapper, every command also works directly
with `uv run` from the repo root:

```bash
# Python packages (core, cli, mcp-server, sdk-python, models, model-serving, api)
uv sync --dev

# JS/TS packages (sdk-ts, local-dashboard, web)
pnpm install

uv run memoryguard init ./my-app
```

This is the path used during active development; see [`CONTRIBUTING.md`](../CONTRIBUTING.md)
for the full dev loop.

## Quickstart developer flow

The example uses a project called `my-app`. Each step maps directly to the Phase 1
acceptance criteria.

### 1. `init` — create a local store

```bash
memoryguard init ./my-app
```

Creates a local SQLite memory store plus config. `init` is idempotent: re-running it
preserves an existing store. If the path is unwritable, it fails with a descriptive
error and leaves no partial store behind.

### 2. `add` — add a memory manually

```bash
memoryguard add "We use pnpm, not npm" \
  --source-type user --source-ref user://lead \
  --scope project --scope-ref my-app
```

Provenance (`--source-type`, `--source-ref`) and scope are required — no memory exists
without a recorded source and scope. Optional flags: `--sensitivity`, `--expires`,
`--tag`.

### 3. `ingest` — ingest a file, folder, or git repo

```bash
memoryguard ingest ./my-app --scope repo --scope-ref my-app
```

Content is chunked, embedded on-device via the `LocalEmbedder`, and stored as per-chunk
records carrying provenance (and a commit reference for git repos where available).
Unreadable files are skipped and recorded rather than aborting the run. Ingested
content is treated strictly as data and is never executed.

### 4. `query` — trust-aware retrieval

```bash
memoryguard query "which package manager?" \
  --scope project --scope-ref my-app --min-trust 0.5 --limit 5
```

Runs the two-stage hybrid pipeline (semantic + keyword + recency, then reranking),
then filters by scope, expiry/status, sensitivity ceiling, the `min_trust` floor, and
policy. Results display provenance, trust score, and the reasons each memory was
surfaced.

### 5. `show` — provenance + trust breakdown

```bash
memoryguard show MEMORY_ID
```

Displays the memory content, source, scope, full trust signal breakdown, and any
detected contradictions.

### 6. `contradictions` — inspect conflicts

```bash
memoryguard contradictions MEMORY_ID
```

Lists contradictions detected against the memory. When two memories conflict, mutual
`contradicts` pointers are set and the lower-trust record transitions to `disputed`;
unresolved contradictions never raise trust.

### 7. `mcp` — serve AI coding agents

```bash
memoryguard mcp
```

Starts the MCP server (stdio) exposing `memory_search`, `memory_add`, and
`memory_explain`, plus the `memoryguard://project/{scope_ref}/memories` and
`memoryguard://memory/{memory_id}` resources. `memory_search` returns only
trust/scope/policy-passing memories, each with its `source_ref` and `trust_score`, and
applies a default trust floor of `0.5` when none is supplied.

### 8. `dashboard` — browse visually

```bash
memoryguard dashboard
```

Launches the local dashboard (Vault Mesh brand): a memories list (content,
`source_ref`, scope, trust meter, status, contradiction badge), a memory detail view
(full provenance, trust signal breakdown, contradiction links, lineage), a trust-aware
query playground, and a store status panel showing counts and `mode = local`.

## Other useful commands

```bash
memoryguard list [--scope ...] [--status active]   # list/filter memories
memoryguard correct MEMORY_ID "NEW CONTENT"        # correct a memory (lineage preserved)
memoryguard rm MEMORY_ID                           # soft-delete (auditable)
memoryguard status                                 # store stats, active flags, mode
memoryguard doctor                                 # check the local install
memoryguard --remote https://api.example.com ...   # target the REST API instead of local
```

## Running with Docker Compose

For a containerized local environment, a Docker Compose configuration is provided
under `infra/docker/` (`docker-compose.yml`). It wires up the REST API (`apps/api`) and
applies the SQLite migrations for local development:

```bash
docker compose -f infra/docker/docker-compose.yml up
```

This brings up the OSS REST routes (memories CRUD, trust-aware query, path ingestion,
contradictions, projects, health) so the CLI (`--remote`), SDKs, and dashboard can
target a running API. In local mode the API binds to `127.0.0.1` and is unauthenticated
by design; binding to any non-local address requires commercial cloud auth to be
enabled. See [`security.md`](./security.md).

## Next steps

- [`local-first.md`](./local-first.md) — how MemoryGuard runs fully on-device with no
  required external LLM API.
- [`security.md`](./security.md) — sensitivity tiers, provenance integrity,
  soft-delete, at-rest encryption, and secret handling.
- [`open-core.md`](./open-core.md) — what is open source vs commercial and why.
