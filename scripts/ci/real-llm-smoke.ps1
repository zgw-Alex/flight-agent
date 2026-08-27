param()

$ErrorActionPreference = "Stop"

$RepoRoot = Resolve-Path (Join-Path $PSScriptRoot "..\..")
$BackendDir = Join-Path $RepoRoot "apps\backend"

Push-Location $BackendDir
$ExitCode = 0
try {
    uv run python (Join-Path $RepoRoot "scripts\smoke\deepseek_smoke.py")
    $ExitCode = $LASTEXITCODE
}
finally {
    Pop-Location
}
exit $ExitCode
