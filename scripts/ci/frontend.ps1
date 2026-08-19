param()

$ErrorActionPreference = "Stop"

$RepoRoot = Resolve-Path (Join-Path $PSScriptRoot "..\..")
$FrontendDir = Join-Path $RepoRoot "apps\frontend"

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

Push-Location $FrontendDir
try {
    Invoke-Step "frontend install" { pnpm install --frozen-lockfile }
    Invoke-Step "frontend lint" { pnpm lint }
    Invoke-Step "frontend typecheck" { pnpm typecheck }
    Invoke-Step "frontend test" { pnpm test }
    Invoke-Step "frontend build" { pnpm build }
}
finally {
    Pop-Location
}
