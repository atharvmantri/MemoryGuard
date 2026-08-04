# SPDX-License-Identifier: Apache-2.0
<#
.SYNOPSIS
    One-command uninstaller for MemoryGuard on Windows.

.DESCRIPTION
    Companion to ``scripts/install.ps1``. By default this just runs
    ``scripts/uninstall-alpha.ps1`` to remove the wrapper files, the
    ``%LOCALAPPDATA%\Programs\MemoryGuard\memoryguard.{ps1,cmd}`` install,
    and the persistent user-PATH entry. Pass ``-RemoveSource`` to also
    delete the cloned source directory (default
    ``%LOCALAPPDATA%\MemoryGuard\source``).

    User project ``.memoryguard/`` stores are never touched.

    Typical usage:
        powershell -ExecutionPolicy Bypass -File "$env:LOCALAPPDATA\MemoryGuard\source\scripts\uninstall.ps1"
#>

[CmdletBinding()]
param(
    [string]$SourceDir,
    [switch]$RemoveSource
)

$ErrorActionPreference = "Stop"

function Write-Step {
    param([string]$Message)
    Write-Host ""
    Write-Host "==> $Message" -ForegroundColor Cyan
}

function Write-Ok {
    param([string]$Message)
    Write-Host "  [ok] $Message" -ForegroundColor Green
}

function Write-Warn {
    param([string]$Message)
    Write-Host "  [warn] $Message" -ForegroundColor Yellow
}

if (-not $SourceDir) {
    $SourceDir = $env:MEMORYGUARD_SOURCE_DIR
}
if (-not $SourceDir) {
    $SourceDir = Join-Path $env:LOCALAPPDATA "MemoryGuard\source"
}
$SourceDir = (Resolve-Path -LiteralPath $SourceDir -ErrorAction SilentlyContinue).Path
if (-not $SourceDir) {
    $SourceDir = Join-Path $env:LOCALAPPDATA "MemoryGuard\source"
}

Write-Step "MemoryGuard one-command uninstaller"
Write-Host "  source dir: $SourceDir"

# ---------------------------------------------------------------------------
# Run uninstall-alpha
# ---------------------------------------------------------------------------

$Uninstaller = Join-Path $SourceDir "scripts\uninstall-alpha.ps1"
if (-not (Test-Path -LiteralPath $Uninstaller)) {
    Write-Warn "expected $Uninstaller to exist; skipping wrapper uninstall"
} else {
    Write-Step "Running uninstall-alpha.ps1"
    & powershell -NoProfile -ExecutionPolicy Bypass -File $Uninstaller
    if ($LASTEXITCODE -ne 0) {
        Write-Warn "uninstall-alpha.ps1 exited with $LASTEXITCODE"
    } else {
        Write-Ok "wrapper removed"
    }
}

# ---------------------------------------------------------------------------
# Optional: remove source dir
# ---------------------------------------------------------------------------

if ($RemoveSource) {
    Write-Step "Removing source dir"
    if (Test-Path -LiteralPath $SourceDir) {
        Remove-Item -LiteralPath $SourceDir -Recurse -Force
        Write-Ok "removed $SourceDir"
    } else {
        Write-Host "  [skip] $SourceDir does not exist"
    }
}

Write-Step "Uninstalled"
Write-Host "  Removed the wrapper. User project .memoryguard/ stores are untouched."
if (-not $RemoveSource) {
    Write-Host "  To also delete the cloned source, re-run with -RemoveSource."
}
