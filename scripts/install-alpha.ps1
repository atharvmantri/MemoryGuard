# SPDX-License-Identifier: Apache-2.0
<#
.SYNOPSIS
    Installs the MemoryGuard alpha wrapper command on Windows.

.DESCRIPTION
    Sets up a lightweight `memoryguard` wrapper for the public alpha. The wrapper
    calls `uv run --project <repo> memoryguard <args>`, so users do not need to
    remember `uv run` for daily use.

    Steps:
      1. Detects the MemoryGuard repo root (the directory containing pyproject.toml).
      2. Verifies `uv` is on PATH; otherwise prints install instructions and exits.
      3. Runs `uv sync --dev` to install Python dependencies (idempotent).
      4. Writes the wrapper script(s) under %LOCALAPPDATA%\Programs\MemoryGuard\.
      5. Detects any pre-existing `memoryguard` collision on the current PATH that
         would shadow the new wrapper and prints a clear warning naming the
         blocking file and its location.
      6. By default, prepends the install dir to the user PATH and to the current
         session's PATH so the new wrapper resolves immediately. Pass
         `-NoPathUpdate` to skip both PATH writes.
      7. Smoke-tests the actual `memoryguard` command (not `uv run memoryguard`)
         to confirm PATH resolution picks up the freshly installed wrapper.

    This script is local-first and does not publish or install PyPI/npm packages.
#>

[CmdletBinding()]
param(
    [switch]$Force,
    [string]$InstallDir,
    [switch]$NoPathUpdate
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

function Get-PathDirs {
    # Returns the current process PATH as a list of directories, normalized
    # so case-insensitive comparison works on Windows.
    $path = [Environment]::GetEnvironmentVariable("Path", "Process")
    if (-not $path) { return @() }
    return $path -split ";" | Where-Object { $_ } | ForEach-Object {
        $dir = $_.TrimEnd("\")
        try {
            [System.IO.Path]::GetFullPath($dir)
        } catch {
            $dir
        }
    }
}

function Test-PathContainsDir {
    param(
        [string[]]$PathDirs,
        [string]$Dir
    )
    $normalized = (Resolve-Path -LiteralPath $Dir -ErrorAction SilentlyContinue).Path
    if (-not $normalized) {
        $normalized = $Dir.TrimEnd("\")
    }
    foreach ($p in $PathDirs) {
        if ($p -ieq $normalized) { return $true }
    }
    return $false
}

# ---------------------------------------------------------------------------
# Locate the MemoryGuard repo root
# ---------------------------------------------------------------------------

# $PSScriptRoot is the directory containing this installer script. The repo
# root is its parent (scripts/ is one level under the repo root).
$ScriptDir = $PSScriptRoot
if (-not $ScriptDir) {
    $ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
}

$RepoRoot = Resolve-Path -LiteralPath (Join-Path $ScriptDir "..") | Select-Object -ExpandProperty Path
$RepoRoot = (Resolve-Path -LiteralPath $RepoRoot).Path

$Pyproject = Join-Path $RepoRoot "pyproject.toml"
if (-not (Test-Path -LiteralPath $Pyproject)) {
    Write-Err "could not find pyproject.toml at $RepoRoot"
    Write-Err "Please run this script from inside the MemoryGuard repository."
    exit 1
}

Write-Step "MemoryGuard alpha installer"
Write-Host "  repo: $RepoRoot"

# ---------------------------------------------------------------------------
# Verify uv
# ---------------------------------------------------------------------------

$uv = Get-Command uv -ErrorAction SilentlyContinue
if (-not $uv) {
    Write-Err "uv is not on your PATH."
    Write-Host ""
    Write-Host "  Install uv first:"
    Write-Host "    irm https://astral.sh/uv/install.ps1 | iex"
    Write-Host ""
    Write-Host "  Then re-run:"
    Write-Host "    powershell -ExecutionPolicy Bypass -File scripts/install-alpha.ps1"
    exit 1
}
# PowerShell's Get-Command parses the version from FileVersionInfo, which
# is often empty for shim binaries. Ask uv directly for the canonical string.
$uvVersionOutput = & uv --version 2>$null
$uvVersion = if ($uvVersionOutput) { ($uvVersionOutput -split "\s+")[1] } else { "unknown" }
Write-Ok "uv $uvVersion found"

# ---------------------------------------------------------------------------
# Run uv sync --dev
# ---------------------------------------------------------------------------

Write-Step "Running uv sync --all-packages --dev (this may take a moment on first run)"
Push-Location -LiteralPath $RepoRoot
try {
    & uv sync --all-packages --dev
    if ($LASTEXITCODE -ne 0) {
        Write-Err "uv sync --all-packages --dev failed (exit $LASTEXITCODE)."
        exit $LASTEXITCODE
    }
}
finally {
    Pop-Location
}
Write-Ok "Python dependencies installed"

# ---------------------------------------------------------------------------
# Resolve the install dir
# ---------------------------------------------------------------------------

if (-not $InstallDir) {
    $InstallDir = Join-Path $env:LOCALAPPDATA "Programs\MemoryGuard"
}
# Resolve-Path -LiteralPath returns $null when the path does not exist
# (e.g. on first install). Keep the original $InstallDir in that case so
# the user-supplied value (or the default above) survives into the rest
# of the script; New-Item below creates the directory before any writes.
$resolved = Resolve-Path -LiteralPath $InstallDir -ErrorAction SilentlyContinue
if ($resolved) {
    $InstallDir = $resolved.Path
}

# ---------------------------------------------------------------------------
# Detect pre-existing memoryguard collisions on PATH (BEFORE we write ours)
# ---------------------------------------------------------------------------

# We look for any file in the PATH that would be hit as `memoryguard` or
# `memoryguard.<ext>` and that is NOT in our install dir. The first match on
# PATH would shadow the wrapper we are about to install.
Write-Step "Scanning PATH for existing memoryguard files"

$collisionNames = @("memoryguard", "memoryguard.exe", "memoryguard.cmd", "memoryguard.bat", "memoryguard.ps1")
$collisions = New-Object System.Collections.Generic.List[object]
$pathDirs = Get-PathDirs
foreach ($d in $pathDirs) {
    foreach ($name in $collisionNames) {
        $candidate = Join-Path $d $name
        if (Test-Path -LiteralPath $candidate) {
            # Skip the install dir we are about to populate.
            if ($d -ieq $InstallDir) { continue }
            $collisions.Add([pscustomobject]@{
                Name = $name
                Path = $candidate
                Dir = $d
            }) | Out-Null
        }
    }
}

# De-duplicate by Path (one binary on disk can match multiple names).
# Use a simple hashtable keyed by the absolute path so duplicates collapse
# regardless of object type.
$seen = @{}
$uniqueCollisions = New-Object System.Collections.Generic.List[object]
foreach ($c in $collisions) {
    if (-not $seen.ContainsKey($c.Path)) {
        $seen[$c.Path] = $true
        $uniqueCollisions.Add($c) | Out-Null
    }
}

if ($uniqueCollisions.Count -gt 0) {
    Write-Warn "found $($uniqueCollisions.Count) pre-existing `memoryguard` file(s) on PATH that will shadow the new wrapper:"
    foreach ($c in $uniqueCollisions) {
        Write-Host "    - $($c.Path)"
    }
    Write-Host ""
    Write-Host "  If you run plain `memoryguard`, Windows will pick the first match on PATH."
    Write-Host "  To make the alpha wrapper win without removing the old file:"
    Write-Host "    1. The new wrapper is at: $InstallDir\memoryguard.ps1 (and memoryguard.cmd)"
    Write-Host "    2. Either delete or rename the old file(s) above, OR"
    Write-Host "    3. Move $InstallDir ahead of every conflicting directory in PATH"
    Write-Host "       (this installer will do that below if you let it update PATH)."
    Write-Host ""
} else {
    Write-Ok "no pre-existing memoryguard files on PATH"
}

# ---------------------------------------------------------------------------
# Write the wrapper
# ---------------------------------------------------------------------------

Write-Step "Writing wrappers"
$RepoRootEscaped = $RepoRoot -replace "'", "''"

$Ps1Content = @"
# MemoryGuard alpha wrapper (auto-generated by scripts/install-alpha.ps1).
# This file is safe to delete; the matching uninstaller is scripts/uninstall-alpha.ps1.
`$ErrorActionPreference = "Stop"
`$repoRoot = '$RepoRootEscaped'
& uv run --project "`$repoRoot" memoryguard @args
exit `$LASTEXITCODE
"@

$CmdContent = @"
@echo off
REM MemoryGuard alpha wrapper (auto-generated by scripts/install-alpha.ps1).
REM This file is safe to delete; the matching uninstaller is scripts/uninstall-alpha.ps1.
"uv" run --project "$RepoRootEscaped" memoryguard %*
exit /b %ERRORLEVEL%
"@

New-Item -ItemType Directory -Force -Path $InstallDir | Out-Null

$Ps1Path = Join-Path $InstallDir "memoryguard.ps1"
$CmdPath = Join-Path $InstallDir "memoryguard.cmd"

# Force re-write so reinstalls stay in sync with the repo path.
Set-Content -LiteralPath $Ps1Path -Value $Ps1Content -Encoding UTF8
Write-Ok "wrote $Ps1Path"

# .cmd files are consumed by cmd.exe. Use the system's default text encoding
# (no BOM) so the leading `@echo off` is not prepended with a UTF-8 byte
# order mark that cmd.exe would echo as garbage.
$utf8NoBom = New-Object System.Text.UTF8Encoding $false
[System.IO.File]::WriteAllText($CmdPath, $CmdContent, $utf8NoBom)
Write-Ok "wrote $CmdPath (cmd.exe compatible)"

# ---------------------------------------------------------------------------
# Update PATH (user + session)
# ---------------------------------------------------------------------------

Write-Step "PATH"
$sessionAlreadyOnPath = Test-PathContainsDir -PathDirs (Get-PathDirs) -Dir $InstallDir

if ($NoPathUpdate) {
    Write-Warn "-NoPathUpdate was set; skipping user PATH and session PATH changes."
    if (-not $sessionAlreadyOnPath) {
        Write-Host "  Note: $InstallDir is not on PATH; `memoryguard` will not resolve in this shell."
    } else {
        Write-Ok "$InstallDir is already on PATH"
    }
} else {
    # 1. Persistent user PATH.
    $userPath = [Environment]::GetEnvironmentVariable("Path", "User")
    $userDirs = if ($userPath) { $userPath -split ";" | Where-Object { $_ } } else { @() }
    $userHas = Test-PathContainsDir -PathDirs $userDirs -Dir $InstallDir

    if ($userHas) {
        Write-Ok "$InstallDir is already on the persistent user PATH"
    } else {
        $newUserPath = if ($userPath) {
            "$InstallDir;$userPath"
        } else {
            $InstallDir
        }
        try {
            [Environment]::SetEnvironmentVariable("Path", $newUserPath, "User")
            Write-Ok "added $InstallDir to the persistent user PATH"
        } catch {
            Write-Warn "could not write the user PATH automatically: $_"
            Write-Host "    To add it manually:"
            Write-Host "      [Environment]::SetEnvironmentVariable('Path', `"$InstallDir;`$([Environment]::GetEnvironmentVariable('Path','User'))`", 'User')"
        }
    }

    # 2. Current session PATH (so the smoke test below resolves the wrapper
    #    without opening a new shell).
    if ($sessionAlreadyOnPath) {
        Write-Ok "$InstallDir is already on the current session PATH"
    } else {
        $env:Path = "$InstallDir;$env:Path"
        Write-Ok "added $InstallDir to the current session PATH (no new shell needed)"
    }
}

# ---------------------------------------------------------------------------
# Smoke test: invoke the actual wrapper command, not `uv run memoryguard`
# ---------------------------------------------------------------------------

Write-Step "Smoke test"
# First, run the underlying engine once to make sure uv sync produced a
# working tree (this catches "uv sync --dev succeeded but the engine fails
# to import" without depending on PATH).
$probe = & uv run --project $RepoRoot memoryguard --help 2>&1
if ($LASTEXITCODE -ne 0) {
    Write-Err "uv run memoryguard --help failed; the wrapper would not work either."
    Write-Host $probe
    exit $LASTEXITCODE
}
Write-Ok "uv run memoryguard --help responds"

# Now resolve the wrapper itself, the way a user would type it.
$resolved = Get-Command memoryguard -ErrorAction SilentlyContinue
if (-not $resolved) {
    if ($NoPathUpdate) {
        Write-Warn "`memoryguard` is not on PATH (-NoPathUpdate was set); skipping wrapper smoke test"
    } else {
        Write-Warn "`memoryguard` is not on PATH yet; the next shell will pick it up"
    }
} else {
    $resolvedPath = $resolved.Path
    $expectedSuffix = (Join-Path $InstallDir "memoryguard.cmd").ToLowerInvariant()
    $expectedPs1Suffix = (Join-Path $InstallDir "memoryguard.ps1").ToLowerInvariant()
    $resolvedLower = $resolvedPath.ToLowerInvariant()
    if ($resolvedLower -eq $expectedSuffix -or $resolvedLower -eq $expectedPs1Suffix) {
        Write-Ok "wrapper resolves to: $resolvedPath"
    } else {
        Write-Warn "wrapper resolves to $resolvedPath (expected $expectedSuffix or $expectedPs1Suffix)"
        Write-Host "    A different `memoryguard` on PATH is shadowing the new install."
        Write-Host "    See the collision list above for the file that needs to be removed or renamed."
    }
    # Exercise the wrapper for real.
    $wrapperOut = & $resolvedPath --help 2>&1
    if ($LASTEXITCODE -ne 0) {
        Write-Warn "the wrapper at $resolvedPath exited with $LASTEXITCODE on --help"
        Write-Host $wrapperOut
    } else {
        Write-Ok "wrapper at $resolvedPath responds to --help"
    }
}

# ---------------------------------------------------------------------------
# Done
# ---------------------------------------------------------------------------

Write-Step "Installed"
Write-Host "  Try it:"
Write-Host ""
Write-Host "    memoryguard --help" -ForegroundColor White
Write-Host "    memoryguard demo" -ForegroundColor White
Write-Host "    memoryguard doctor" -ForegroundColor White
Write-Host ""
Write-Host "  To remove the wrapper later, run:"
Write-Host "    powershell -ExecutionPolicy Bypass -File scripts/uninstall-alpha.ps1"
