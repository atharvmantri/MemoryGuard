# SPDX-License-Identifier: Apache-2.0
"""Tests for the alpha install/uninstall wrapper scripts.

These tests are deliberately filesystem-only. They never modify the real
user PATH, never touch ``%LOCALAPPDATA%`` or ``~/.local/bin``, and never
invoke the install scripts in a way that could leak wrappers onto the host.
The PowerShell tests work on any platform that has ``pwsh`` available; if
``pwsh`` is missing, the PowerShell test is skipped (the bash tests cover
the same contract for macOS/Linux).
"""

from __future__ import annotations

import os
import platform
import re
import shutil
import stat
import subprocess
from pathlib import Path
from typing import Optional

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]


def _bash_executable() -> Optional[str]:
    """Locate a real bash on this host.

    On macOS/Linux, ``shutil.which('bash')`` finds the system bash. On Windows
    we look in common install locations (Git for Windows) because the default
    ``bash`` is a WSL launcher that does not run shell scripts directly.
    """
    found = shutil.which("bash")
    if found and "system32" not in found.lower():
        return found
    if platform.system() == "Windows":
        candidates = [
            r"C:\Program Files\Git\bin\bash.exe",
            r"C:\Program Files (x86)\Git\bin\bash.exe",
            r"C:\Windows\System32\bash.exe",
        ]
        for c in candidates:
            if os.path.isfile(c):
                # Only use it if it actually behaves like a bash shell.
                try:
                    probe = subprocess.run(
                        [c, "-c", "echo ok"], capture_output=True, text=True, timeout=5
                    )
                    if probe.returncode == 0 and "ok" in probe.stdout:
                        return c
                except (OSError, subprocess.SubprocessError):
                    continue
    return None


# ---------------------------------------------------------------------------
# Installer script presence + content
# ---------------------------------------------------------------------------


def _read(rel: str) -> str:
    return (REPO_ROOT / rel).read_text(encoding="utf-8")


def test_install_scripts_exist():
    for rel in (
        "scripts/install-alpha.sh",
        "scripts/install-alpha.ps1",
        "scripts/uninstall-alpha.sh",
        "scripts/uninstall-alpha.ps1",
    ):
        path = REPO_ROOT / rel
        assert path.is_file(), f"missing installer script: {rel}"


def test_install_alpha_sh_mentions_uv_and_wrapper():
    text = _read("scripts/install-alpha.sh")
    # Must verify uv before doing anything destructive.
    assert "uv" in text
    # Must install the workspace packages, not just the dev group, so the
    # resulting `memoryguard` import works after install.
    assert "uv sync" in text
    assert "--all-packages" in text
    assert "--dev" in text
    # Must write a wrapper to ~/.local/bin (default install dir).
    assert ".local/bin" in text
    assert 'memoryguard' in text
    # Must call uv run with --project pointing at the repo.
    assert "uv run --project" in text
    # Must mention smoke-testing the install.
    assert "memoryguard --help" in text or "memoryguard demo" in text
    # Must not pretend to publish packages.
    assert "pip install" not in text.lower()
    assert "npm install" not in text.lower()


def test_install_alpha_ps1_mentions_uv_and_wrapper():
    text = _read("scripts/install-alpha.ps1")
    assert "uv" in text
    # Must install the workspace packages, not just the dev group.
    assert "uv sync" in text
    assert "--all-packages" in text
    assert "--dev" in text
    # Default install dir: %LOCALAPPDATA%\Programs\MemoryGuard.
    assert "LOCALAPPDATA" in text
    assert "memoryguard.ps1" in text
    # cmd.exe shim for older shells.
    assert "memoryguard.cmd" in text
    # Must call uv run with --project pointing at the repo.
    assert "uv run --project" in text
    # Must not pretend to publish packages.
    assert "pip install" not in text.lower()
    assert "npm install" not in text.lower()


def test_uninstall_alpha_sh_removes_wrapper_only():
    text = _read("scripts/uninstall-alpha.sh")
    # Must remove the wrapper file in $INSTALL_DIR.
    assert "rm" in text
    assert "memoryguard" in text
    # The uninstaller must not call `rm -rf` on anything (we only ever need
    # to remove a single file path and an empty dir).
    assert "rm -rf" not in text
    # Must not target the repo, the .venv, or a project .memoryguard dir.
    # Only the wrapper path ($INSTALL_DIR/memoryguard) should be removed.
    rm_lines = [
        line.strip()
        for line in text.splitlines()
        if re.match(r"^\s*rm(\s|$)", line)
    ]
    for line in rm_lines:
        # Allow `rm -f "$WRAPPER"` (variable, not a literal path) and
        # `rmdir "$INSTALL_DIR"`. Anything else is suspicious.
        if "WRAPPER" in line:
            continue
        if "INSTALL_DIR" in line and "rmdir" in line:
            continue
        # Reject literal paths that look like the repo or a project store.
        for forbidden in ("pyproject.toml", "REPO_ROOT", ".memoryguard", "/repo"):
            assert forbidden not in line, (
                f"uninstall-alpha.sh has suspicious rm line: {line!r}"
            )


def test_uninstall_alpha_ps1_removes_wrapper_only():
    text = _read("scripts/uninstall-alpha.ps1")
    # Must remove the wrapper file(s) under %LOCALAPPDATA%\Programs\MemoryGuard.
    assert "Remove-Item" in text
    assert "memoryguard.ps1" in text
    assert "memoryguard.cmd" in text
    # Must not target the repo or a project .memoryguard dir.
    assert "pyproject.toml" not in text
    # The script may mention .venv in the user-facing banner copy (it's
    # telling the user the .venv is left alone) but must not use it as a
    # Remove-Item path.
    for line in text.splitlines():
        if "Remove-Item" in line:
            for forbidden in ("pyproject.toml", ".venv", "REPO", ".memoryguard"):
                assert forbidden not in line, (
                    f"uninstall-alpha.ps1 has suspicious Remove-Item line: {line!r}"
                )


def test_install_alpha_sh_embeds_absolute_repo_path():
    # When the script writes the wrapper, it must bake the absolute repo
    # path into the wrapper so that the wrapper survives moving the script
    # out of the repo.
    text = _read("scripts/install-alpha.sh")
    assert "REPO_ROOT=" in text
    # Must be a realpath resolution, not a relative path.
    assert 'cd "$SCRIPT_DIR' in text
    assert 'cd "$REPO_ROOT" && pwd' in text


def test_install_alpha_ps1_uses_repo_path_variable():
    text = _read("scripts/install-alpha.ps1")
    # Must resolve the repo root from $PSScriptRoot.
    assert "PSScriptRoot" in text
    assert "RepoRoot" in text or "REPO_ROOT" in text or "REPO" in text.upper()
    # The wrapper must embed the repo path so the user can move the script
    # out of the repo and the wrapper still works (until the next install).
    assert "REPO_ROOT" in text or "RepoRoot" in text


# ---------------------------------------------------------------------------
# PATH handling + collision detection + NoPathUpdate option
# ---------------------------------------------------------------------------


def test_install_alpha_sh_supports_no_path_update():
    text = _read("scripts/install-alpha.sh")
    # The bash script must accept a flag that skips both the user PATH and
    # the session PATH changes. POSIX-style --flag-name is the convention.
    assert "--no-path-update" in text
    # The flag must actually gate the user-PATH and session-PATH writes.
    assert "NO_PATH_UPDATE" in text


def test_install_alpha_ps1_supports_no_path_update():
    text = _read("scripts/install-alpha.ps1")
    # PowerShell uses param([switch]$NoPathUpdate).
    assert "NoPathUpdate" in text
    assert "-NoPathUpdate" in text


def test_install_alpha_sh_detects_collision():
    text = _read("scripts/install-alpha.sh")
    # The installer must scan PATH for an existing `memoryguard` and warn
    # about anything that would shadow the new wrapper.
    assert "memoryguard" in text
    assert "PATH" in text
    # Look for the collision-related wording.
    low = text.lower()
    assert "collision" in low or "shadow" in low or "pre-existing" in low


def test_install_alpha_ps1_detects_collision():
    text = _read("scripts/install-alpha.ps1")
    low = text.lower()
    # The installer scans PATH for any pre-existing `memoryguard` file that
    # is not in our install dir and warns the user.
    assert "scanning path" in low
    assert "collision" in low or "pre-existing" in low
    # The collision check looks for at least `memoryguard` and
    # `memoryguard.exe` so it catches Python 3.12's global pip shim.
    assert "memoryguard.exe" in text


def test_install_alpha_sh_updates_user_path():
    text = _read("scripts/install-alpha.sh")
    # The installer must write to the user rc file (bashrc/zshrc/profile)
    # so the wrapper resolves in new shells. The line is appended under a
    # guard so re-running the installer does not stack duplicates.
    low = text.lower()
    assert ".bashrc" in low or ".zshrc" in low or ".profile" in low
    # Idempotency: must check for a guard line before appending.
    assert "guard" in low


def test_install_alpha_ps1_updates_user_path():
    text = _read("scripts/install-alpha.ps1")
    # The installer must use [Environment]::SetEnvironmentVariable to add
    # the install dir to the persistent user PATH.
    assert "SetEnvironmentVariable" in text
    assert "'Path'" in text or '"Path"' in text
    assert "User" in text


def test_install_alpha_sh_updates_session_path():
    text = _read("scripts/install-alpha.sh")
    # Session PATH update: `export PATH=...` must run in the current shell.
    assert "export PATH=" in text


def test_install_alpha_ps1_updates_session_path():
    text = _read("scripts/install-alpha.ps1")
    # Session PATH update: `$env:Path = ...` must run in the current shell.
    assert '$env:Path' in text


def test_install_alpha_sh_smoke_test_resolves_wrapper():
    text = _read("scripts/install-alpha.sh")
    # The smoke test must invoke the actual `memoryguard` command (not just
    # `uv run memoryguard`) so it catches PATH shadowing by an older
    # `memoryguard` binary.
    assert "command -v memoryguard" in text
    # And it must compare the resolved path against the expected install
    # dir so it can warn the user when a different memoryguard wins on PATH.
    assert "INSTALL_DIR/memoryguard" in text


def test_install_alpha_ps1_smoke_test_resolves_wrapper():
    text = _read("scripts/install-alpha.ps1")
    # PowerShell equivalent: `Get-Command memoryguard` resolves the
    # wrapper, and the path is compared to the expected suffix.
    assert "Get-Command memoryguard" in text
    # Path comparison against expected ps1 / cmd suffix.
    assert "memoryguard.ps1" in text
    assert "memoryguard.cmd" in text


def test_uninstall_alpha_sh_cleans_user_path():
    text = _read("scripts/uninstall-alpha.sh")
    # The bash uninstaller must strip the installer's rc-file entry by
    # removing the guard block (not just suggesting the user do it).
    assert "guard" in text.lower()
    # Use perl to do the block deletion (avoids depending on GNU sed -i).
    assert "perl" in text


def test_uninstall_alpha_ps1_cleans_user_path():
    text = _read("scripts/uninstall-alpha.ps1")
    # The PowerShell uninstaller must remove the install dir from the
    # persistent user PATH, not just leave a note.
    assert "SetEnvironmentVariable" in text
    assert "'Path'" in text or '"Path"' in text
    assert "User" in text


# ---------------------------------------------------------------------------
# Wrapper file generation (in a temp dir, NOT the real install dir)
# ---------------------------------------------------------------------------


def _make_fake_repo(tmp_path: Path) -> Path:
    """Create a minimal fake repo with pyproject.toml so installer scripts
    accept it. Avoids any side effects on the real repo."""
    repo = tmp_path / "fake-mg"
    repo.mkdir()
    (repo / "pyproject.toml").write_text(
        '[project]\nname = "fake"\nversion = "0.0.0"\n', encoding="utf-8"
    )
    (repo / "scripts").mkdir()
    return repo


def test_uninstall_alpha_sh_dry_run_removes_target(tmp_path, monkeypatch):
    # Drive the uninstaller in a temp install dir without touching PATH.
    # The uninstaller is a short bash script: we extract the rm command by
    # running it with a redirected wrapper path.
    fake_repo = _make_fake_repo(tmp_path)
    install_dir = tmp_path / "wrappers"
    install_dir.mkdir()
    wrapper = install_dir / "memoryguard"
    wrapper.write_text("#!/usr/bin/env bash\necho stub\n", encoding="utf-8")
    wrapper.chmod(0o755)

    # Run the uninstaller pointing at our fake install dir.
    script = REPO_ROOT / "scripts" / "uninstall-alpha.sh"
    bash = _bash_executable()
    if bash is None:
        pytest.skip("bash not available on this host")
    result = subprocess.run(
        [str(bash), str(script), str(install_dir)],
        capture_output=True,
        text=True,
        check=False,
        shell=False,
    )
    assert result.returncode == 0, result.stderr
    # The wrapper must be gone, but the fake repo must be untouched.
    assert not wrapper.exists()
    assert fake_repo.is_dir()
    assert (fake_repo / "pyproject.toml").is_file()


# ---------------------------------------------------------------------------
# Windows PowerShell uninstaller in pwsh (skipped if pwsh is unavailable)
# ---------------------------------------------------------------------------


_PWSH = shutil.which("pwsh") or shutil.which("powershell")


@pytest.mark.skipif(_PWSH is None, reason="PowerShell not available on this host")
def test_uninstall_alpha_ps1_dry_run_removes_target(tmp_path):
    fake_repo = _make_fake_repo(tmp_path)
    install_dir = tmp_path / "wrappers"
    install_dir.mkdir()
    ps1 = install_dir / "memoryguard.ps1"
    cmd = install_dir / "memoryguard.cmd"
    ps1.write_text("Write-Output 'stub'\n", encoding="utf-8")
    cmd.write_text("@echo off\r\necho stub\r\n", encoding="utf-8")

    script = REPO_ROOT / "scripts" / "uninstall-alpha.ps1"
    result = subprocess.run(
        [_PWSH, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script), "-InstallDir", str(install_dir)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert not ps1.exists()
    assert not cmd.exists()
    # Repo must be untouched.
    assert fake_repo.is_dir()
    assert (fake_repo / "pyproject.toml").is_file()


# ---------------------------------------------------------------------------
# End-to-end: a "fake old memoryguard" on PATH triggers the collision
# warning in the installer's output (no real PATH mutation).
# ---------------------------------------------------------------------------


def test_install_alpha_sh_reports_collision_when_old_binary_on_path(tmp_path, monkeypatch):
    # Lay down a fake old memoryguard somewhere on PATH and run the bash
    # installer against a fake repo. The installer should warn about the
    # collision but still write the new wrapper. We must not let the installer
    # actually touch the real user PATH: --no-path-update is the gate.
    bash = _bash_executable()
    if bash is None:
        pytest.skip("bash not available on this host")

    # 1. Create a fake "stale" memoryguard on a temp PATH directory.
    fake_old = tmp_path / "oldbin"
    fake_old.mkdir()
    stale = fake_old / "memoryguard"
    stale.write_text("#!/usr/bin/env bash\necho stale\n", encoding="utf-8")
    stale.chmod(0o755)

    # 2. Create a fake repo with pyproject.toml and a scripts/ dir.
    fake_repo = _make_fake_repo(tmp_path)

    # 3. Copy the install script into the fake repo.
    install_dest = fake_repo / "scripts" / "install-alpha.sh"
    install_dest.parent.mkdir(parents=True, exist_ok=True)
    install_dest.write_text((REPO_ROOT / "scripts" / "install-alpha.sh").read_text(encoding="utf-8"))
    install_dest.chmod(0o755)

    # 4. Run the installer with --no-path-update. Prepend the fake-old dir to
    #    PATH so the collision scan finds the stale binary.
    env = os.environ.copy()
    env["PATH"] = f"{fake_old}{os.pathsep}{env.get('PATH', '')}"
    env["MEMORYGUARD_INSTALL_DIR"] = str(tmp_path / "wrappers")
    result = subprocess.run(
        [str(bash), str(install_dest), "--no-path-update"],
        capture_output=True,
        text=True,
        check=False,
        env=env,
        cwd=str(fake_repo),
    )
    assert result.returncode == 0, result.stderr
    # The collision warning must appear in stdout.
    assert "stale" not in result.stdout  # we did not actually run the stale one
    assert "pre-existing" in result.stdout.lower() or "shadow" in result.stdout.lower()
    # The new wrapper must have been written.
    new_wrapper = Path(env["MEMORYGUARD_INSTALL_DIR"]) / "memoryguard"
    assert new_wrapper.is_file()
    new_wrapper.unlink()


@pytest.mark.skipif(_PWSH is None, reason="PowerShell not available on this host")
def test_install_alpha_ps1_reports_collision_when_old_binary_on_path(tmp_path):
    # Lay down a fake "stale" memoryguard on PATH and run the PowerShell
    # installer against a fake repo with -NoPathUpdate so we never touch
    # the real user PATH. The installer must warn about the collision.
    fake_old = tmp_path / "oldbin"
    fake_old.mkdir()
    (fake_old / "memoryguard.exe").write_text("@echo off\r\necho stale\r\n", encoding="utf-8")

    fake_repo = _make_fake_repo(tmp_path)
    install_dest = fake_repo / "scripts" / "install-alpha.ps1"
    install_dest.parent.mkdir(parents=True, exist_ok=True)
    install_dest.write_text((REPO_ROOT / "scripts" / "install-alpha.ps1").read_text(encoding="utf-8"))

    install_dir = tmp_path / "wrappers"
    env = os.environ.copy()
    # Prepend the fake-old dir to PATH so the collision scan finds the
    # stale binary. Do not modify the real user PATH.
    env["PATH"] = f"{fake_old}{os.pathsep}{env.get('PATH', '')}"
    # The installer is being run from a different (fake) repo, so make sure
    # the test runner's VIRTUAL_ENV (if any) does not leak in.
    env.pop("VIRTUAL_ENV", None)
    # Override LOCALAPPDATA so the installer's persistent-PATH write hits a
    # throwaway dir even if the test ever runs without -NoPathUpdate.
    env["LOCALAPPDATA"] = str(tmp_path / "localappdata")

    result = subprocess.run(
        [
            _PWSH,
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(install_dest),
            "-NoPathUpdate",
            "-InstallDir",
            str(install_dir),
        ],
        capture_output=True,
        text=True,
        check=False,
        env=env,
        cwd=str(fake_repo),
    )
    # The smoke test inside the installer runs `uv run memoryguard --help`
    # against the fake repo, which has no workspace packages, so it will
    # fail. That is fine for this test: we only care that the collision
    # scan ran and the new wrapper was written.
    if result.returncode != 0:
        assert "pre-existing" in result.stdout.lower() or "shadow" in result.stdout.lower(), (
            f"installer exited {result.returncode} without the collision warning;\n"
            f"--- stdout ---\n{result.stdout}\n--- stderr ---\n{result.stderr}"
        )
    # The collision warning must appear in stdout.
    low = result.stdout.lower()
    assert "pre-existing" in low or "shadow" in low, (
        f"no collision warning found in installer output:\n{result.stdout}"
    )
    # The new wrapper must have been written.
    assert (install_dir / "memoryguard.ps1").is_file()
    assert (install_dir / "memoryguard.cmd").is_file()


# ---------------------------------------------------------------------------
# platform gating
# ---------------------------------------------------------------------------


def test_install_alpha_sh_is_executable():
    if platform.system() == "Windows":
        pytest.skip("POSIX executable bit is a no-op on Windows")
    path = REPO_ROOT / "scripts" / "install-alpha.sh"
    mode = path.stat().st_mode
    assert mode & stat.S_IXUSR, "install-alpha.sh must be executable by the user"


def test_uninstall_alpha_sh_is_executable():
    if platform.system() == "Windows":
        pytest.skip("POSIX executable bit is a no-op on Windows")
    path = REPO_ROOT / "scripts" / "uninstall-alpha.sh"
    mode = path.stat().st_mode
    assert mode & stat.S_IXUSR, "uninstall-alpha.sh must be executable by the user"
