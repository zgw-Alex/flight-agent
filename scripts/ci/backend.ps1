param()

$ErrorActionPreference = "Stop"

$RepoRoot = Resolve-Path (Join-Path $PSScriptRoot "..\..")
$BackendDir = Join-Path $RepoRoot "apps\backend"
$BackendTestsDir = Join-Path $RepoRoot "tests/backend"

if ([string]::IsNullOrWhiteSpace($env:UV_CACHE_DIR)) {
    $env:UV_CACHE_DIR = Join-Path $RepoRoot ".uv-cache"
}

if ([string]::IsNullOrWhiteSpace($env:RUFF_CACHE_DIR)) {
    $env:RUFF_CACHE_DIR = Join-Path $RepoRoot ".ruff-cache"
}

function Invoke-Step {
    param(
        [Parameter(Mandatory = $true)]
        [string] $Name,
        [Parameter(Mandatory = $true)]
        [scriptblock] $Command
    )

    Write-Host "==> $Name"
    & $Command
    if ($LASTEXITCODE -ne 0) {
        throw "$Name failed with exit code $LASTEXITCODE"
    }
}

Push-Location $BackendDir
try {
    Invoke-Step "backend install" { uv sync --frozen }
    Invoke-Step "backend ruff" { uv run ruff check . }
    Invoke-Step "backend pyright" { uv run pyright }
    Invoke-Step "backend pytest" { uv run pytest $BackendTestsDir }
}
finally {
    Pop-Location
}
