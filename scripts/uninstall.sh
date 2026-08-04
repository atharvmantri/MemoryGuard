#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
#
# One-command uninstaller for MemoryGuard on macOS or Linux.
#
# Companion to ``scripts/install.sh``. By default this just runs
# ``scripts/uninstall-alpha.sh`` to remove the wrapper file, the
# ``~/.local/bin/memoryguard`` install, and the persistent rc-file
# PATH entry. Pass ``--remove-source`` to also delete the cloned source
# directory (default ``~/.local/share/memoryguard/source``).
#
# User project ``.memoryguard/`` stores are never touched.
#
# Typical usage:
#     bash ~/.local/share/memoryguard/source/scripts/uninstall.sh

set -euo pipefail

SOURCE_DIR="${MEMORYGUARD_SOURCE_DIR:-$HOME/.local/share/memoryguard/source}"
SOURCE_DIR="${SOURCE_DIR%/}"
REMOVE_SOURCE=0
POSITIONAL=()
for arg in "$@"; do
    case "$arg" in
        --remove-source)
            REMOVE_SOURCE=1
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
Usage: bash scripts/uninstall.sh [--remove-source] [--source-dir PATH]

Options:
  --remove-source         Also delete the cloned source directory.
  --source-dir PATH       Override the source directory
                          (default: \$MEMORYGUARD_SOURCE_DIR or
                          ~/.local/share/memoryguard/source).
  -h, --help              Show this help.
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
echo "==> MemoryGuard one-command uninstaller"
echo "  source dir: $SOURCE_DIR"

# ---------------------------------------------------------------------------
# Run uninstall-alpha
# ---------------------------------------------------------------------------

UNINSTALLER="$SOURCE_DIR/scripts/uninstall-alpha.sh"
if [[ ! -f "$UNINSTALLER" ]]; then
    echo
    echo "  [warn] expected $UNINSTALLER to exist; skipping wrapper uninstall" >&2
else
    echo
    echo "==> Running uninstall-alpha.sh"
    if bash "$UNINSTALLER"; then
        echo "  [ok] wrapper removed"
    else
        echo "  [warn] uninstall-alpha.sh exited non-zero" >&2
    fi
fi

# ---------------------------------------------------------------------------
# Optional: remove source dir
# ---------------------------------------------------------------------------

if [[ "$REMOVE_SOURCE" -eq 1 ]]; then
    echo
    echo "==> Removing source dir"
    if [[ -d "$SOURCE_DIR" ]]; then
        rm -rf "$SOURCE_DIR"
        echo "  [ok] removed $SOURCE_DIR"
    else
        echo "  [skip] $SOURCE_DIR does not exist"
    fi
fi

echo
echo "==> Uninstalled"
echo "  Removed the wrapper. User project .memoryguard/ stores are untouched."
if [[ "$REMOVE_SOURCE" -eq 0 ]]; then
    echo "  To also delete the cloned source, re-run with --remove-source."
fi
