# Contributing to MemoryGuard

Thanks for helping improve MemoryGuard.

## Setup

```bash
# Alpha wrapper: a one-time installer gives you the `memoryguard` command.
bash scripts/install-alpha.sh   # macOS / Linux
# or, on Windows:
# powershell -ExecutionPolicy Bypass -File scripts/install-alpha.ps1

# From source, the same workspace is wired up with `uv` and `pnpm`:
uv sync --dev
pnpm install
uv run pytest
pnpm test
```

If `.venv` is broken, recreate it:

```powershell
Remove-Item -Recurse -Force .\.venv -ErrorAction SilentlyContinue
uv sync --dev
uv run pytest
```

```bash
rm -rf .venv
uv sync --dev
uv run pytest
```

## Local CLI Loop

With the alpha wrapper installed, you can use the `memoryguard` command
directly:

```bash
memoryguard doctor
memoryguard init
memoryguard remember "This project uses Flask for the backend."
memoryguard sync
memoryguard status
```

If you would rather not install the wrapper (e.g. for active development):

```bash
uv run memoryguard init
uv run memoryguard remember "This project uses Flask for the backend."
uv run memoryguard sync
uv run memoryguard status
```

## Guidelines

- Keep the core workflow local-first.
- Do not add required external LLM/API dependencies.
- Do not log or render secret values.
- Add tests for new behavior.
- Keep changes small and easy to review.
