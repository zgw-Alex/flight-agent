param()

$ErrorActionPreference = "Stop"

$RepoRoot = Resolve-Path (Join-Path $PSScriptRoot "..\..")
$BackendDir = Join-Path $RepoRoot "apps\backend"

if (-not $env:DEEPSEEK_DEFAULT_MODEL) {
    $env:DEEPSEEK_DEFAULT_MODEL = "deepseek-v4-pro"
}

Push-Location $BackendDir
$ExitCode = 0
try {
    uv run python (Join-Path $RepoRoot "scripts\smoke\deepseek_explanation_smoke.py")
    $ExitCode = $LASTEXITCODE
}
finally {
    Pop-Location
}
exit $ExitCode
