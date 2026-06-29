# Release Notes

## 0.1.0 Public OSS Alpha

MemoryGuard public alpha focuses on:

- local-first memory storage,
- Context Sync for AGENTS.md, CLAUDE.md, MEMORY.md, and Cursor rules,
- Agent Capture MVP with pending approval,
- deterministic supersession of obvious outdated decisions,
- secret-safe generated context,
- a friendly CLI happy path with one-step setup.

### Alpha install

There is no PyPI or npm package to install during alpha. The supported flow
is clone + the one-time `scripts/install-alpha.{ps1,sh}` installer, which
sets up a thin `memoryguard` wrapper on your `PATH` and runs `uv sync --dev`
under the hood. After the installer finishes, you can run `memoryguard`,
`memoryguard demo`, `memoryguard doctor`, `memoryguard init`, etc. directly
from any shell.
