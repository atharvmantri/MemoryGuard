# SPDX-License-Identifier: Apache-2.0
<#
.SYNOPSIS
    One-command bootstrap installer for MemoryGuard on Windows.

.DESCRIPTION
    This script is designed to be invoked directly from
    raw.githubusercontent.com with no cloned repo:

        irm https://raw.githubusercontent.com/atharvmantri/MemoryGuard/main/scripts/install.ps1 | iex

    Behavior:
      1. Choose a stable source directory (default
         ``%LOCALAPPDATA%\MemoryGuard\source``; override with
         ``$env:MEMORYGUARD_SOURCE_DIR`` or ``-SourceDir``).
      2. Verify prerequisites: ``git`` and ``uv`` (Node/pnpm are NOT required
         for the CLI).
      3. Clone the public repo into the source dir (or update it if it
         already exists and is a MemoryGuard repo on ``main``).
      4. Run ``uv sync --all-packages --dev`` inside the source dir.
      5. Invoke ``scripts/install-alpha.ps1`` from the source dir, passing
         through any supported flags (``-NoPathUpdate``,
         ``-RemoveShadowingCommands``).
      6. After this script returns, ``memoryguard`` is on PATH (either
         freshly installed or already up to date).

    The clone-based ``scripts/install-alpha.ps1`` remains the advanced /
    manual flow for users who already have a checkout.
#>

[CmdletBinding()]
param(
    [string]$SourceDir,
    [switch]$NoPathUpdate,
    [switch]$RemoveShadowingCommands
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

function Write-Err {
    param([string]$Message)
    Write-Host "  [error] $Message" -ForegroundColor Red
}

# ---------------------------------------------------------------------------
# Resolve the source directory
# ---------------------------------------------------------------------------

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

Write-Step "MemoryGuard one-command bootstrap"
Write-Host "  source dir: $SourceDir"

# ---------------------------------------------------------------------------
# Verify prerequisites: git + uv
# ---------------------------------------------------------------------------

Write-Step "Verifying prerequisites"

$git = Get-Command git -ErrorAction SilentlyContinue
if (-not $git) {
    Write-Err "git is not on your PATH."
    Write-Host ""
    Write-Host "  Install git first:"
    Write-Host "    winget install --id Git.Git -e --source winget"
    Write-Host "    or: https://git-scm.com/download/win"
    Write-Host ""
    Write-Host "  Then re-run this script."
    exit 1
}
Write-Ok "git $($git.Version) found"

$uv = Get-Command uv -ErrorAction SilentlyContinue
if (-not $uv) {
    Write-Err "uv is not on your PATH."
    Write-Host ""
    Write-Host "  Install uv first:"
    Write-Host "    irm https://astral.sh/uv/install.ps1 | iex"
    Write-Host ""
    Write-Host "  Then re-run this script."
    exit 1
}
$uvVersionOutput = & uv --version 2>$null
$uvVersion = if ($uvVersionOutput) { ($uvVersionOutput -split "\s+")[1] } else { "unknown" }
Write-Ok "uv $uvVersion found"

# ---------------------------------------------------------------------------
# Clone or update the repo
# ---------------------------------------------------------------------------

$RepoUrl = "https://github.com/atharvmantri/MemoryGuard.git"
$GitDir = Join-Path $SourceDir ".git"

if (Test-Path -LiteralPath $SourceDir) {
    if (-not (Test-Path -LiteralPath $GitDir)) {
        Write-Err "$SourceDir exists but is not a MemoryGuard git repo."
        Write-Host "  Move or delete the directory, or set MEMORYGUARD_SOURCE_DIR to a fresh path."
        exit 1
    }
    Write-Step "Updating existing source at $SourceDir"
    Push-Location -LiteralPath $SourceDir
    try {
        & git fetch origin
        if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
        & git checkout main
        if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
        & git pull --ff-only origin main
        if ($LASTEXITCODE -ne 0) {
            Write-Warn "git pull --ff-only failed (you may have local changes); continuing with the existing checkout."
        } else {
            Write-Ok "pulled latest main"
        }
    }
    finally {
        Pop-Location
    }
} else {
    Write-Step "Cloning $RepoUrl -> $SourceDir"
    New-Item -ItemType Directory -Force -Path (Split-Path -Parent $SourceDir) | Out-Null
    & git clone $RepoUrl $SourceDir
    if ($LASTEXITCODE -ne 0) {
        Write-Err "git clone failed (exit $LASTEXITCODE)."
        exit $LASTEXITCODE
    }
    Write-Ok "cloned MemoryGuard"
}

# ---------------------------------------------------------------------------
# uv sync
# ---------------------------------------------------------------------------

Write-Step "Running uv sync --all-packages --dev"
Push-Location -LiteralPath $SourceDir
try {
    & uv sync --all-packages --dev
    if ($LASTEXITCODE -ne 0) {
        Write-Err "uv sync failed (exit $LASTEXITCODE)."
        exit $LASTEXITCODE
    }
}
finally {
    Pop-Location
}
Write-Ok "Python workspace ready"

# ---------------------------------------------------------------------------
# Hand off to install-alpha.ps1
# ---------------------------------------------------------------------------

Write-Step "Installing the memoryguard wrapper"
$Installer = Join-Path $SourceDir "scripts\install-alpha.ps1"
if (-not (Test-Path -LiteralPath $Installer)) {
    Write-Err "expected $Installer to exist after clone/update; aborting."
    exit 1
}

# Build the argument list as a plain array (not a hashtable) so that
# switch parameters round-trip through the child powershell process as
# real booleans, not strings.
$pwshArgs = @("-NoProfile", "-ExecutionPolicy", "Bypass", "-File", $Installer)
if ($NoPathUpdate) { $pwshArgs += "-NoPathUpdate" }
if ($RemoveShadowingCommands) { $pwshArgs += "-RemoveShadowingCommands" }

& powershell @pwshArgs
$exitCode = $LASTEXITCODE

if ($exitCode -ne 0) {
    Write-Warn "install-alpha.ps1 exited with $exitCode. Inspect the output above."
    exit $exitCode
}

Write-Step "Done"
Write-Host "  Try it from any shell:"
Write-Host "    memoryguard --help"
Write-Host "    memoryguard doctor"
Write-Host "    memoryguard demo"
