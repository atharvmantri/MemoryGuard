[CmdletBinding()]
param(
    [ValidateSet('main')]
    [string]$Ref = 'main',
    [switch]$KeepTemp
)

$ErrorActionPreference = 'Stop'
$repoUrl = 'https://github.com/atharvmantri/MemoryGuard.git'
$assetRoot = (Resolve-Path -LiteralPath (Split-Path -Parent $MyInvocation.MyCommand.Path)).Path
$runRoot = Join-Path (Split-Path -Parent $assetRoot) '.memoryguard-alpha-verification'
$runId = Get-Date -Format 'yyyyMMdd-HHmmss-fffffff'
$checkout = Join-Path $runRoot "MemoryGuard-$runId"

function Require-Command {
    param([Parameter(Mandatory)][string]$Name)

    if (-not (Get-Command $Name -ErrorAction SilentlyContinue)) {
        throw "Required command '$Name' was not found on PATH."
    }
}

function Invoke-Checked {
    param(
        [Parameter(Mandatory)][string]$Label,
        [Parameter(Mandatory)][scriptblock]$Action
    )

    Write-Host "==> $Label"
    & $Action
    if ($LASTEXITCODE -ne 0) {
        throw "'$Label' failed with exit code $LASTEXITCODE."
    }
}

try {
    Require-Command -Name 'git'
    Require-Command -Name 'uv'
    New-Item -ItemType Directory -Force -Path $runRoot | Out-Null

    Invoke-Checked -Label "Clone MemoryGuard $Ref into a temporary checkout" -Action {
        & git clone --quiet --depth 1 --branch $Ref $repoUrl $checkout
    }

    $commit = (& git -C $checkout rev-parse HEAD).Trim()
    if ($commit -notmatch '^[0-9a-f]{40}$') {
        throw 'The checkout did not resolve to a commit.'
    }

    Invoke-Checked -Label 'Run CLI help smoke test' -Action {
        & uv run --project $checkout memoryguard --help | Out-Null
    }

    Invoke-Checked -Label 'Run local doctor check' -Action {
        & uv run --project $checkout memoryguard doctor
    }

    Invoke-Checked -Label 'Run the deterministic local demo' -Action {
        & uv run --project $checkout memoryguard demo
    }

    Write-Host ''
    Write-Host "PASS: MemoryGuard public alpha $Ref is runnable at commit $commit"
    Write-Host 'No credentials, PATH changes, project writes, or payment services were used.'
}
finally {
    if (-not $KeepTemp -and (Test-Path -LiteralPath $checkout)) {
        $resolvedCheckout = (Resolve-Path -LiteralPath $checkout).Path
        $resolvedRunRoot = (Resolve-Path -LiteralPath $runRoot).Path
        if (-not $resolvedCheckout.StartsWith($resolvedRunRoot + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)) {
            throw "Refusing cleanup outside the verification directory: $resolvedCheckout"
        }
        Remove-Item -LiteralPath $resolvedCheckout -Recurse -Force
    }
}
