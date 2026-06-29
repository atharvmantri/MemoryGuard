#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
#
# Removes the MemoryGuard alpha wrapper command from macOS or Linux.
#
# Deletes the wrapper file written by scripts/install-alpha.sh. Does not
# touch the MemoryGuard repository, the .venv, the uv cache, or any project
# `.memoryguard/` stores.
#
# Override the install dir with MEMORYGUARD_INSTALL_DIR (or pass it as $1) if
# you installed the wrapper somewhere other than the default ~/.local/bin.

set -euo pipefail

INSTALL_DIR="${1:-${MEMORYGUARD_INSTALL_DIR:-$HOME/.local/bin}}"

echo
echo "==> MemoryGuard alpha uninstaller"
echo "  install dir: $INSTALL_DIR"

WRAPPER="$INSTALL_DIR/memoryguard"
removed=0

if [[ -f "$WRAPPER" ]]; then
    rm -f "$WRAPPER"
    echo "  [ok] removed $WRAPPER"
    removed=$((removed + 1))
else
    echo "  [skip] no wrapper at $WRAPPER"
fi

# Tidy up the install dir itself if it is now empty.
if [[ -d "$INSTALL_DIR" ]] && [[ -z "$(ls -A "$INSTALL_DIR" 2>/dev/null || true)" ]]; then
    rmdir "$INSTALL_DIR" 2>/dev/null || true
    echo "  [ok] removed empty $INSTALL_DIR"
fi

if [[ $removed -eq 0 ]]; then
    echo "  [warn] no wrapper files found at $INSTALL_DIR" >&2
    echo "  (If you installed with MEMORYGUARD_INSTALL_DIR, pass the same path here.)" >&2
fi

# PATH note
case ":${PATH}:" in
    *":$INSTALL_DIR:"*)
        echo
        echo "==> PATH"
        echo "  $INSTALL_DIR is still listed in your PATH."
        echo "  Remove it from your shell rc if you no longer want it on PATH, e.g.:"
        echo "    sed -i.bak 's#export PATH=\"$INSTALL_DIR:\$PATH\"##' ~/.bashrc"
        ;;
esac

echo
echo "==> Uninstalled"
echo "  Removed the wrapper file only. Your MemoryGuard repo, .venv, and any"
echo "  project .memoryguard/ stores are untouched."
