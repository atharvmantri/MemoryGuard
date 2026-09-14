#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
#
# Removes the MemoryGuard alpha wrapper command from macOS or Linux.
#
# Deletes the wrapper file written by scripts/install-alpha.sh. Does not
# touch the MemoryGuard repository, the .venv, the uv cache, or any project
# `.memoryguard/` stores.
#
# Also strips the guard block that this installer's install script writes to
# the most appropriate shell rc file (~/.bashrc, ~/.zshrc, or ~/.profile).
#
# Override the install dir with MEMORYGUARD_INSTALL_DIR (or pass it as $1) if
# you installed the wrapper somewhere other than the default ~/.local/bin.

set -euo pipefail

INSTALL_DIR="${1:-${MEMORYGUARD_INSTALL_DIR:-$HOME/.local/bin}}"
_normalized_install_dir="${INSTALL_DIR%/}"

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

# Strip the installer's PATH guard block from the most appropriate rc file.
# Pick by SHELL first (matches what the installer picked), then fall back
# to whatever rc file we can find.
echo
echo "==> PATH"
_rc_file=""
case "${SHELL:-}" in
    */zsh)
        _rc_file="$HOME/.zshrc"
        ;;
    */bash|*)
        if [[ -f "$HOME/.bashrc" ]]; then
            _rc_file="$HOME/.bashrc"
        elif [[ -f "$HOME/.bash_profile" ]]; then
            _rc_file="$HOME/.bash_profile"
        elif [[ -f "$HOME/.profile" ]]; then
            _rc_file="$HOME/.profile"
        fi
        ;;
esac
_guard="# memoryguard alpha wrapper (managed by scripts/install-alpha.sh)"
if [[ -n "$_rc_file" ]] && grep -F "$_guard" "$_rc_file" >/dev/null 2>&1; then
    # Remove the four-line block (blank line, guard, conditional, fi) using
    # perl so we don't depend on GNU sed's -i semantics. The block is
    # always the same shape because the installer writes it.
    if perl -0777 -ne 's/\n*# memoryguard alpha wrapper \(managed by scripts\/install-alpha\.sh\)\nif \[\[ ":.+:" != \*"[^\n]*"\n    export PATH="[^\n]*"\nfi\n*//g; print' "$_rc_file" > "$_rc_file.tmp" \
       && mv "$_rc_file.tmp" "$_rc_file"; then
        echo "  [ok] removed the memoryguard PATH entry from $_rc_file"
    else
        echo "  [warn] could not edit $_rc_file automatically"
        echo "    To remove the entry manually, delete the block guarded by:"
        echo "      $_guard"
    fi
elif [[ -n "$_rc_file" ]]; then
    echo "  [skip] no memoryguard PATH entry in $_rc_file"
else
    echo "  [warn] could not detect a shell rc file; not editing any"
fi

echo
echo "==> Uninstalled"
echo "  Removed the wrapper file only. Your MemoryGuard repo, .venv, and any"
echo "  project .memoryguard/ stores are untouched."
