# Getting Started

MemoryGuard is a local-first developer CLI. During the public alpha there is
no PyPI or npm package to install — the supported path is a clone plus a
one-time installer script that writes a thin `memoryguard` wrapper on your
`PATH`.

## Install

```bash
git clone https://github.com/atharvmantri/MemoryGuard.git
cd MemoryGuard

# Windows (PowerShell)
powershell -ExecutionPolicy Bypass -File scripts/install-alpha.ps1

# macOS / Linux
bash scripts/install-alpha.sh
```

The installer runs `uv sync --dev` and writes a wrapper that calls
`uv run --project <repo> memoryguard ...`, so daily use does not need
`uv run` in front of every command.

## Daily use

```bash
memoryguard doctor
memoryguard init
memoryguard remember "This project uses Flask for the backend."
memoryguard sync
memoryguard status
```

If you would rather run from source without the wrapper, prefix with `uv run`:

```bash
uv run memoryguard init
uv run memoryguard remember "This project uses Flask for the backend."
uv run memoryguard sync
```

## Uninstalling the wrapper

```powershell
# Windows
powershell -ExecutionPolicy Bypass -File scripts/uninstall-alpha.ps1
```

```bash
# macOS / Linux
bash scripts/uninstall-alpha.sh
```

The uninstaller removes the wrapper files only — your clone, the `.venv`,
and any project `.memoryguard/` stores are left untouched.

## Available after this update is pushed and verified

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
