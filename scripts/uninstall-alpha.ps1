# SPDX-License-Identifier: Apache-2.0
<#
.SYNOPSIS
    Removes the MemoryGuard alpha wrapper command from Windows.

.DESCRIPTION
    Deletes the wrapper files written by scripts/install-alpha.ps1. Does not
    touch the MemoryGuard repository, the .venv, the uv cache, or any project
    `.memoryguard/` stores.

    The default install location is %LOCALAPPDATA%\Programs\MemoryGuard, but
    the installer accepts a custom -InstallDir; this uninstaller defaults to
    the same standard path. Override with -InstallDir if you used a custom one.
#>

[CmdletBinding()]
param(
    [string]$InstallDir
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

if (-not $InstallDir) {
    $InstallDir = Join-Path $env:LOCALAPPDATA "Programs\MemoryGuard"
}

Write-Step "MemoryGuard alpha uninstaller"
Write-Host "  install dir: $InstallDir"

$Ps1Path = Join-Path $InstallDir "memoryguard.ps1"
$CmdPath = Join-Path $InstallDir "memoryguard.cmd"
$removed = 0

if (Test-Path -LiteralPath $Ps1Path) {
    Remove-Item -LiteralPath $Ps1Path -Force
    Write-Ok "removed $Ps1Path"
    $removed += 1
} else {
    Write-Host "  [skip] no wrapper at $Ps1Path"
}

if (Test-Path -LiteralPath $CmdPath) {
    Remove-Item -LiteralPath $CmdPath -Force
    Write-Ok "removed $CmdPath"
    $removed += 1
} else {
    Write-Host "  [skip] no wrapper at $CmdPath"
}

# Tidy up the install dir itself if it is now empty.
if ((Test-Path -LiteralPath $InstallDir) -and -not (Get-ChildItem -LiteralPath $InstallDir -Force | Select-Object -First 1)) {
    Remove-Item -LiteralPath $InstallDir -Force
    Write-Ok "removed empty $InstallDir"
}

if ($removed -eq 0) {
    Write-Warn "no wrapper files found at $InstallDir"
    Write-Host "  (If you installed with a custom -InstallDir, pass the same path here.)"
}

# Remove the install dir from the user PATH if we (or a previous install
# of this installer) added it there. The current session PATH is left alone:
# removing it from $env:Path would not change anything visible to this
# shell, and other shells in flight have their own PATH.
Write-Step "PATH"
$userPath = [Environment]::GetEnvironmentVariable("Path", "User")
if ($userPath) {
    $parts = $userPath -split ";" | Where-Object { $_ -and ($_ -ine $InstallDir) }
    $newPath = ($parts -join ";")
    if ($newPath -ne $userPath) {
        try {
            [Environment]::SetEnvironmentVariable("Path", $newPath, "User")
            Write-Ok "removed $InstallDir from the persistent user PATH"
        } catch {
            Write-Warn "could not update the user PATH automatically: $_"
            Write-Host "    To remove it manually:"
            Write-Host "      [Environment]::SetEnvironmentVariable('Path', '$newPath', 'User')"
        }
    } else {
        Write-Host "  [skip] $InstallDir was not on the persistent user PATH"
    }
} else {
    Write-Host "  [skip] user PATH was empty; nothing to remove"
}

Write-Step "Uninstalled"
Write-Host "  Removed wrapper files only. Your MemoryGuard repo, .venv, and any"
Write-Host "  project .memoryguard/ stores are untouched."
