$ErrorActionPreference = "Stop"

$demoDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$transcript = Join-Path $demoDir "transcript.txt"
$project = Join-Path ([System.IO.Path]::GetTempPath()) ("memoryguard-agent-capture-demo-" + [System.Guid]::NewGuid().ToString("N"))

New-Item -ItemType Directory -Path $project | Out-Null
Push-Location $project
try {
    memoryguard init --name agent-capture-demo | Out-Host
    memoryguard remember "This project uses FastAPI for the backend." | Out-Host
    memoryguard remember "This project uses npm." | Out-Host
    memoryguard remember "This project uses MySQL as the database." | Out-Host
    memoryguard capture file $transcript --source codex | Out-Host
    memoryguard capture pending | Out-Host
    memoryguard capture approve --all | Out-Host
    memoryguard sync | Out-Host

    $agents = Get-Content -Raw -LiteralPath (Join-Path $project "AGENTS.md")
    $agents | Out-Host
    $allContext = @(
        Get-Content -Raw -LiteralPath (Join-Path $project "AGENTS.md")
        Get-Content -Raw -LiteralPath (Join-Path $project "CLAUDE.md")
        Get-Content -Raw -LiteralPath (Join-Path $project "MEMORY.md")
        Get-Content -Raw -LiteralPath (Join-Path $project ".cursor/rules/memoryguard.mdc")
    ) -join "`n"

    if ($agents -notmatch "Backend framework: Flask") { throw "Flask was not active" }
    if ($agents -notmatch "FastAPI") { throw "FastAPI was not deprecated" }
    if ($agents -notmatch "Package manager: pnpm") { throw "pnpm was not active" }
    if ($agents -notmatch "npm") { throw "npm was not deprecated" }
    if ($agents -notmatch "Local database: SQLite") { throw "SQLite was not active" }
    if ($agents -notmatch "Cloud database: PostgreSQL") { throw "PostgreSQL was not active" }
    if ($agents -notmatch "Deployment target: Vercel") { throw "Vercel was not active" }
    if ($agents -notmatch "Test command: pnpm test") { throw "pnpm test was not present" }
    if ($allContext -match "sk-test-1234567890abcdef") { throw "Fake secret leaked" }
    foreach ($file in @("AGENTS.md", "CLAUDE.md", "MEMORY.md", ".cursor/rules/memoryguard.mdc")) {
        if (-not (Test-Path -LiteralPath (Join-Path $project $file))) {
            throw "Missing context file $file"
        }
    }

    "Agent Capture demo passed."
}
finally {
    Pop-Location
    Remove-Item -LiteralPath $project -Recurse -Force
}
