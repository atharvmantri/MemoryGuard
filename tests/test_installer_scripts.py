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
    assert "uv sync --dev" in text
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
    assert "uv sync --dev" in text
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
# Wrapper file generation (in a temp dir, NOT the real install dir)
# ---------------------------------------------------------------------------


def _make_fake_repo(tmp_path: Path) -> Path:
    """Create a minimal fake repo with pyproject.toml so installer scripts
    accept it. Avoids any side effects on the real repo."""
    repo = tmp_path / "fake-mg"
    repo.mkdir()
    (repo / "pyproject.toml").write_text("[project]\nname = 'fake'\n", encoding="utf-8")
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
