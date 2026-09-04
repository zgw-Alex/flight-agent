param()

$ErrorActionPreference = "Stop"

$RepoRoot = Resolve-Path (Join-Path $PSScriptRoot "..\..")
$BackendDir = Join-Path $RepoRoot "apps\backend"
$FrontendDir = Join-Path $RepoRoot "apps\frontend"
$LogDir = Join-Path $RepoRoot "tmp\e2e"
$BackendLog = Join-Path $LogDir "backend"
$FrontendLog = Join-Path $LogDir "frontend"
$BackendUrl = "http://127.0.0.1:8000"
$FrontendUrl = "http://127.0.0.1:5173"
$BackendProcess = $null
$FrontendProcess = $null

function Resolve-PowerShellExecutable {
    if (-not [string]::IsNullOrWhiteSpace([System.Environment]::ProcessPath)) {
        return [System.Environment]::ProcessPath
    }

    $CurrentProcess = Get-Process -Id $PID
    if (-not [string]::IsNullOrWhiteSpace($CurrentProcess.Path)) {
        return $CurrentProcess.Path
    }

    $PwshCommand = Get-Command pwsh -ErrorAction SilentlyContinue
    if ($null -ne $PwshCommand -and -not [string]::IsNullOrWhiteSpace($PwshCommand.Source)) {
        return $PwshCommand.Source
    }

    throw "Unable to resolve a PowerShell 7 executable for E2E child process launch"
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

function Wait-Http {
    param(
        [Parameter(Mandatory = $true)]
        [string] $Url,
        [int] $TimeoutSeconds = 45
    )

    $Deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    while ((Get-Date) -lt $Deadline) {
        try {
            $Response = Invoke-WebRequest $Url -UseBasicParsing -TimeoutSec 3
            if ($Response.StatusCode -ge 200 -and $Response.StatusCode -lt 500) {
                return
            }
        }
        catch {
            Start-Sleep -Milliseconds 500
        }
    }
    throw "Timed out waiting for $Url"
}

function Start-HiddenProcess {
    param(
        [Parameter(Mandatory = $true)]
        [string] $WorkingDirectory,
        [Parameter(Mandatory = $true)]
        [string] $Command,
        [Parameter(Mandatory = $true)]
        [string] $LogPath
    )

    $StartProcessParams = @{
        FilePath = Resolve-PowerShellExecutable
        ArgumentList = @("-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", $Command)
        WorkingDirectory = $WorkingDirectory
        RedirectStandardOutput = "$LogPath.out.log"
        RedirectStandardError = "$LogPath.err.log"
        PassThru = $true
    }

    if ($IsWindows) {
        $StartProcessParams["WindowStyle"] = "Hidden"
    }

    return Start-Process @StartProcessParams
}

New-Item -ItemType Directory -Force -Path $LogDir | Out-Null

try {
    Push-Location $BackendDir
    try {
        Invoke-Step "e2e backend install" { uv sync --frozen }
    }
    finally {
        Pop-Location
    }

    Push-Location $FrontendDir
    try {
        Invoke-Step "e2e frontend install" { pnpm install --frozen-lockfile }
        Invoke-Step "e2e browser install" { pnpm exec playwright install chromium }
    }
    finally {
        Pop-Location
    }

    $BackendProcess = Start-HiddenProcess `
        -WorkingDirectory $BackendDir `
        -Command "uv run uvicorn flight_agent.bootstrap.app:app --host 127.0.0.1 --port 8000" `
        -LogPath $BackendLog
    Wait-Http "$BackendUrl/healthz"

    $env:VITE_BACKEND_URL = $BackendUrl
    $FrontendProcess = Start-HiddenProcess `
        -WorkingDirectory $FrontendDir `
        -Command "pnpm dev --host 127.0.0.1 --port 5173" `
        -LogPath $FrontendLog
    Wait-Http $FrontendUrl

    Push-Location $FrontendDir
    try {
        $env:E2E_FRONTEND_URL = $FrontendUrl
        Invoke-Step "browser e2e" { pnpm test:e2e }
    }
    finally {
        Pop-Location
    }
}
finally {
    if ($null -ne $FrontendProcess -and -not $FrontendProcess.HasExited) {
        Stop-Process -Id $FrontendProcess.Id -Force
    }
    if ($null -ne $BackendProcess -and -not $BackendProcess.HasExited) {
        Stop-Process -Id $BackendProcess.Id -Force
    }
}
