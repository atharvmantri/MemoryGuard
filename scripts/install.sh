#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
#
# One-command bootstrap installer for MemoryGuard on macOS or Linux.
#
# Designed to be invoked directly from raw.githubusercontent.com with no
# cloned repo:
#
#     curl -fsSL https://raw.githubusercontent.com/atharvmantri/MemoryGuard/main/scripts/install.sh | bash
#
# Behavior:
#   1. Choose a stable source directory (default
#      ``~/.local/share/memoryguard/source``; override with
#      ``MEMORYGUARD_SOURCE_DIR`` or ``--source-dir``).
#   2. Verify prerequisites: ``git`` and ``uv`` (Node/pnpm are NOT required
#      for the CLI).
#   3. Clone the public repo into the source dir (or update it if it
#      already exists and is a MemoryGuard repo on ``main``).
#   4. Run ``uv sync --all-packages --dev`` inside the source dir.
#   5. Invoke ``scripts/install-alpha.sh`` from the source dir, passing
#      through supported flags (``--no-path-update``,
#      ``--remove-shadowing-commands``).
#   6. After this script returns, ``memoryguard`` is on PATH (either
#      freshly installed or already up to date).
#
# The clone-based ``scripts/install-alpha.sh`` remains the advanced /
# manual flow for users who already have a checkout.

set -euo pipefail

REPO_URL="https://github.com/atharvmantri/MemoryGuard.git"
SOURCE_DIR="${MEMORYGUARD_SOURCE_DIR:-$HOME/.local/share/memoryguard/source}"
SOURCE_DIR="${SOURCE_DIR%/}"
NO_PATH_UPDATE=0
REMOVE_SHADOWING=0
POSITIONAL=()
for arg in "$@"; do
    case "$arg" in
        --no-path-update)
            NO_PATH_UPDATE=1
            ;;
        --remove-shadowing-commands)
            REMOVE_SHADOWING=1
            ;;
        --source-dir=*)
            SOURCE_DIR="${arg#*=}"
            SOURCE_DIR="${SOURCE_DIR%/}"
            ;;
        --source-dir)
            shift
            SOURCE_DIR="${1:-}"
            SOURCE_DIR="${SOURCE_DIR%/}"
            ;;
        --help|-h)
            cat <<USAGE
Usage: bash scripts/install.sh [--no-path-update] [--remove-shadowing-commands]
       [--source-dir PATH]

Options:
  --no-path-update           Do not modify the user PATH or the current session.
  --remove-shadowing-commands
                             Forwarded to install-alpha.sh; reserved for
                             future use.
  --source-dir PATH          Override the source directory
                             (default: \$MEMORYGUARD_SOURCE_DIR or
                             ~/.local/share/memoryguard/source).
  -h, --help                 Show this help.
USAGE
            exit 0
            ;;
        *)
            echo "  [error] unknown argument: $arg" >&2
            exit 1
            ;;
    esac
done

echo
echo "==> MemoryGuard one-command bootstrap"
echo "  source dir: $SOURCE_DIR"

# ---------------------------------------------------------------------------
# Verify prerequisites
# ---------------------------------------------------------------------------

echo
echo "==> Verifying prerequisites"

if ! command -v git >/dev/null 2>&1; then
    echo
    echo "  [error] git is not on your PATH." >&2
    echo
    echo "  Install git first (apt / brew / dnf / pacman)." >&2
    echo "  Then re-run this script." >&2
    exit 1
fi
echo "  [ok] git $(git --version) found"

if ! command -v uv >/dev/null 2>&1; then
    echo
    echo "  [error] uv is not on your PATH." >&2
    echo
    echo "  Install uv first:" >&2
    echo "    curl -LsSf https://astral.sh/uv/install.sh | sh" >&2
    echo
    echo "  Then re-run this script." >&2
    exit 1
fi
echo "  [ok] uv $(uv --version) found"

# ---------------------------------------------------------------------------
# Clone or update the source
# ---------------------------------------------------------------------------

if [[ -d "$SOURCE_DIR" ]]; then
    if [[ ! -d "$SOURCE_DIR/.git" ]]; then
        echo
        echo "  [error] $SOURCE_DIR exists but is not a MemoryGuard git repo." >&2
        echo "  Move or delete the directory, or set MEMORYGUARD_SOURCE_DIR to a fresh path." >&2
        exit 1
    fi
    echo
    echo "==> Updating existing source at $SOURCE_DIR"
    (
        cd "$SOURCE_DIR"
        git fetch origin
        git checkout main
        if git pull --ff-only origin main; then
            echo "  [ok] pulled latest main"
        else
            echo "  [warn] git pull --ff-only failed (you may have local changes); continuing with the existing checkout."
        fi
    )
else
    echo
    echo "==> Cloning $REPO_URL -> $SOURCE_DIR"
    mkdir -p "$(dirname "$SOURCE_DIR")"
    git clone "$REPO_URL" "$SOURCE_DIR"
    echo "  [ok] cloned MemoryGuard"
fi

# ---------------------------------------------------------------------------
# uv sync
# ---------------------------------------------------------------------------

echo
echo "==> Running uv sync --all-packages --dev"
(
    cd "$SOURCE_DIR"
    uv sync --all-packages --dev
)
echo "  [ok] Python workspace ready"

# ---------------------------------------------------------------------------
# Hand off to install-alpha.sh
# ---------------------------------------------------------------------------

echo
echo "==> Installing the memoryguard wrapper"
INSTALLER="$SOURCE_DIR/scripts/install-alpha.sh"
if [[ ! -f "$INSTALLER" ]]; then
    echo "  [error] expected $INSTALLER to exist after clone/update; aborting." >&2
    exit 1
fi

INSTALL_ALPHA_ARGS=()
if [[ "$NO_PATH_UPDATE" -eq 1 ]]; then
    INSTALL_ALPHA_ARGS+=("--no-path-update")
fi
if [[ "$REMOVE_SHADOWING" -eq 1 ]]; then
    INSTALL_ALPHA_ARGS+=("--remove-shadowing-commands")
fi

if ! bash "$INSTALLER" "${INSTALL_ALPHA_ARGS[@]}"; then
    echo "  [warn] install-alpha.sh exited non-zero. Inspect the output above." >&2
    exit 1
fi

echo
echo "==> Done"
echo "  Try it from any shell:"
echo "    memoryguard --help"
echo "    memoryguard doctor"
echo "    memoryguard demo"
