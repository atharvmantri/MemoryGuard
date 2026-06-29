#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
#
# Installs the MemoryGuard alpha wrapper command on macOS or Linux.
#
# Sets up a lightweight `memoryguard` wrapper for the public alpha. The wrapper
# calls `uv run --project <repo> memoryguard <args>`, so users do not need to
# remember `uv run` for daily use.
#
# Steps:
#   1. Detects the MemoryGuard repo root (the directory containing pyproject.toml).
#   2. Verifies `uv` is on PATH; otherwise prints install instructions and exits.
#   3. Runs `uv sync --dev` to install Python dependencies (idempotent).
#   4. Writes ~/.local/bin/memoryguard and marks it executable.
#   5. Detects any pre-existing `memoryguard` collision on the current PATH
#      that would shadow the new wrapper and prints a clear warning naming
#      the blocking file and its location.
#   6. By default, prepends the install dir to the user PATH (writing to
#      ~/.bashrc, ~/.zshrc, or ~/.profile as appropriate) and to the current
#      session's PATH so the new wrapper resolves immediately. Pass
#      `--no-path-update` to skip both PATH writes.
#   7. Smoke-tests the actual `memoryguard` command (not `uv run memoryguard`)
#      to confirm PATH resolution picks up the freshly installed wrapper.
#
# This script is local-first and does not publish or install PyPI/npm packages.

set -euo pipefail

NO_PATH_UPDATE=0
FORCE=0
INSTALL_DIR="${MEMORYGUARD_INSTALL_DIR:-$HOME/.local/bin}"
POSITIONAL=()
for arg in "$@"; do
    case "$arg" in
        --no-path-update)
            NO_PATH_UPDATE=1
            ;;
        --force|-f)
            FORCE=1
            ;;
        --help|-h)
            cat <<USAGE
Usage: bash scripts/install-alpha.sh [--no-path-update] [--force]

Options:
  --no-path-update   Do not modify the user PATH or the current session PATH.
                     Only the wrapper file is written.
  --force            Reserved; current installer is idempotent.
  -h, --help         Show this help.
USAGE
            exit 0
            ;;
        *)
            POSITIONAL+=("$arg")
            ;;
    esac
done

# ---------------------------------------------------------------------------
# Locate the MemoryGuard repo root
# ---------------------------------------------------------------------------

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

if [[ ! -f "$REPO_ROOT/pyproject.toml" ]]; then
    echo "  [error] could not find pyproject.toml at $REPO_ROOT" >&2
    echo "  Please run this script from inside the MemoryGuard repository." >&2
    exit 1
fi

# Stable absolute path so the wrapper works even if the repo is moved later
# (re-run the installer to repoint the wrapper at the new location).
REPO_ROOT="$(cd "$REPO_ROOT" && pwd)"

echo
echo "==> MemoryGuard alpha installer"
echo "  repo: $REPO_ROOT"

# ---------------------------------------------------------------------------
# Verify uv
# ---------------------------------------------------------------------------

if ! command -v uv >/dev/null 2>&1; then
    echo
    echo "  [error] uv is not on your PATH." >&2
    echo
    echo "  Install uv first:" >&2
    echo "    curl -LsSf https://astral.sh/uv/install.sh | sh" >&2
    echo
    echo "  Then re-run:" >&2
    echo "    bash scripts/install-alpha.sh" >&2
    exit 1
fi
echo "  [ok] uv $(uv --version) found"

# ---------------------------------------------------------------------------
# Run uv sync --dev
# ---------------------------------------------------------------------------

echo
echo "==> Running uv sync --all-packages --dev (this may take a moment on first run)"
(
    cd "$REPO_ROOT"
    uv sync --all-packages --dev
)
echo "  [ok] Python dependencies installed"

# ---------------------------------------------------------------------------
# Detect pre-existing memoryguard collisions on PATH (BEFORE we write ours)
# ---------------------------------------------------------------------------

echo
echo "==> Scanning PATH for existing memoryguard files"

collision_found=0
collisions=""
# Iterate each PATH dir, looking for any file named `memoryguard` (with or
# without a suffix). Skip our own install dir.
IFS=':' read -ra _path_dirs <<< "$PATH"
for d in "${_path_dirs[@]}"; do
    [[ -z "$d" ]] && continue
    # Normalize trailing slashes.
    d="${d%/}"
    [[ "$d" == "$INSTALL_DIR" ]] && continue
    for name in memoryguard memoryguard.bin memoryguard.exe memoryguard.sh; do
        if [[ -e "$d/$name" ]]; then
            echo "  [warn] found pre-existing $d/$name on PATH"
            collisions="${collisions}${collisions:+$'\n'}    - $d/$name"
            collision_found=1
        fi
    done
done

if [[ "$collision_found" -eq 1 ]]; then
    echo
    echo "  A pre-existing \`memoryguard\` will shadow the alpha wrapper."
    echo "  The first match on PATH wins. To make the alpha wrapper take over:"
    echo "    1. The new wrapper is at: $INSTALL_DIR/memoryguard"
    echo "    2. Either delete or rename the files above, OR"
    echo "    3. Make sure $INSTALL_DIR appears before every conflicting"
    echo "       directory in PATH (this installer will do that below if you"
    echo "       let it update PATH)."
    echo
else
    echo "  [ok] no pre-existing memoryguard files on PATH"
fi

# ---------------------------------------------------------------------------
# Write the wrapper
# ---------------------------------------------------------------------------

echo
echo "==> Writing wrapper"
WRAPPER="$INSTALL_DIR/memoryguard"

mkdir -p "$INSTALL_DIR"

cat > "$WRAPPER" <<EOF
#!/usr/bin/env bash
# MemoryGuard alpha wrapper (auto-generated by scripts/install-alpha.sh).
# This file is safe to delete; the matching uninstaller is scripts/uninstall-alpha.sh.
set -e
exec uv run --project "$REPO_ROOT" memoryguard "\$@"
EOF

chmod +x "$WRAPPER"
echo "  [ok] wrote $WRAPPER"

# ---------------------------------------------------------------------------
# PATH: persistent user PATH + current session PATH
# ---------------------------------------------------------------------------

echo
echo "==> PATH"

# Normalize INSTALL_DIR for membership checks.
_normalized_install_dir="${INSTALL_DIR%/}"

_session_has_install=0
IFS=':' read -ra _session_dirs <<< "$PATH"
for d in "${_session_dirs[@]}"; do
    d="${d%/}"
    if [[ "$d" == "$_normalized_install_dir" ]]; then
        _session_has_install=1
        break
    fi
done

if [[ "$NO_PATH_UPDATE" -eq 1 ]]; then
    echo "  [warn] --no-path-update was set; skipping user PATH and session PATH changes."
    if [[ "$_session_has_install" -eq 0 ]]; then
        echo "    Note: $INSTALL_DIR is not on PATH; \`memoryguard\` will not resolve in this shell."
    else
        echo "  [ok] $INSTALL_DIR is already on PATH"
    fi
else
    # 1. Persistent user PATH: pick the right rc file for this shell.
    if [[ "$_session_has_install" -eq 1 ]]; then
        echo "  [ok] $INSTALL_DIR is already on the current session PATH"
    else
        export PATH="$_normalized_install_dir:$PATH"
        echo "  [ok] added $INSTALL_DIR to the current session PATH (no new shell needed)"
    fi

    # 2. Persistent user PATH: append a single guarded export line to the
    #    most appropriate rc file. Skip if it is already there.
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
            else
                _rc_file="$HOME/.profile"
                touch "$_rc_file"
            fi
            ;;
    esac
    if [[ -z "$_rc_file" ]]; then
        echo "  [warn] could not detect a shell rc file; not editing any rc"
    else
        # Guard line so re-running the installer does not stack duplicates.
        _guard="# memoryguard alpha wrapper (managed by scripts/install-alpha.sh)"
        if grep -F "$_guard" "$_rc_file" >/dev/null 2>&1; then
            echo "  [ok] $_rc_file already has the memoryguard PATH entry"
        else
            {
                echo ""
                echo "$_guard"
                echo "if [[ \":\${PATH}:\" != *\":$_normalized_install_dir:\"* ]]; then"
                echo "    export PATH=\"$_normalized_install_dir:\$PATH\""
                echo "fi"
            } >> "$_rc_file"
            echo "  [ok] added $INSTALL_DIR to $_rc_file (open a new shell or 'source' it)"
        fi
    fi
fi

# ---------------------------------------------------------------------------
# Smoke test: invoke the actual wrapper command, not `uv run memoryguard`
# ---------------------------------------------------------------------------

echo
echo "==> Smoke test"
if ! (cd "$REPO_ROOT" && uv run memoryguard --help >/dev/null 2>&1); then
    echo "  [error] uv run memoryguard --help failed; the wrapper would not work either." >&2
    exit 1
fi
echo "  [ok] uv run memoryguard --help responds"

# Now resolve the wrapper itself, the way a user would type it.
if command -v memoryguard >/dev/null 2>&1; then
    resolved_path="$(command -v memoryguard)"
    expected="$INSTALL_DIR/memoryguard"
    if [[ "$resolved_path" == "$expected" ]]; then
        echo "  [ok] wrapper resolves to: $resolved_path"
    else
        echo "  [warn] wrapper resolves to $resolved_path (expected $expected)"
        echo "         A different \`memoryguard\` on PATH is shadowing the new install."
        echo "         See the collision list above for the file that needs to be removed or renamed."
    fi
    if memoryguard --help >/dev/null 2>&1; then
        echo "  [ok] wrapper at $resolved_path responds to --help"
    else
        echo "  [warn] wrapper at $resolved_path exited non-zero on --help"
    fi
else
    if [[ "$NO_PATH_UPDATE" -eq 1 ]]; then
        echo "  [warn] \`memoryguard\` is not on PATH (--no-path-update was set); skipping wrapper smoke test"
    else
        echo "  [warn] \`memoryguard\` is not on PATH yet; open a new shell or 'source' your rc"
    fi
fi

# ---------------------------------------------------------------------------
# Done
# ---------------------------------------------------------------------------

echo
echo "==> Installed"
echo
echo "  Try it:"
echo
echo "    memoryguard --help"
echo "    memoryguard demo"
echo "    memoryguard doctor"
echo
echo "  To remove the wrapper later, run:"
echo "    bash scripts/uninstall-alpha.sh"
