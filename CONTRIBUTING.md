# Contributing to MemoryGuard

Thanks for helping improve MemoryGuard.

## Setup

```bash
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
