param(
    [int] $TimeoutSeconds = 90
)

$ErrorActionPreference = "Stop"

$RepoRoot = Resolve-Path (Join-Path $PSScriptRoot "..\..")
$Failure = $null

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

function Get-PostgresContainerId {
    $containerId = docker compose ps -q postgres
    if ($LASTEXITCODE -ne 0) {
        throw "docker compose ps failed while locating postgres container"
    }
    if ([string]::IsNullOrWhiteSpace($containerId)) {
        throw "postgres container was not created"
    }
    return $containerId.Trim()
}

Push-Location $RepoRoot
try {
    try {
        Invoke-Step "postgres compose config" { docker compose config }
        Invoke-Step "postgres startup" { docker compose up -d postgres }

        $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
        $containerId = Get-PostgresContainerId
        $lastStatus = "unknown"

        Write-Host "==> postgres bounded health polling"
        while ((Get-Date) -lt $deadline) {
            $lastStatus = docker inspect --format "{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}" $containerId
            if ($LASTEXITCODE -ne 0) {
                throw "docker inspect failed while polling postgres health"
            }
            $lastStatus = $lastStatus.Trim()
            if ($lastStatus -eq "healthy") {
                break
            }
            Start-Sleep -Seconds 2
        }

        if ($lastStatus -ne "healthy") {
            throw "postgres did not become healthy before ${TimeoutSeconds}s deadline; last status: $lastStatus"
        }

        Invoke-Step "postgres readiness" {
            docker compose exec -T postgres pg_isready -U flight_agent -d flight_agent
        }
    }
    catch {
        $Failure = $_
        Write-Host "==> postgres failure evidence"
        docker compose ps -a
        docker compose logs --tail 80 postgres
    }
    finally {
        Write-Host "==> postgres cleanup"
        try {
            docker compose down
        }
        catch {
            Write-Warning "postgres cleanup failed: $($_.Exception.Message)"
        }
    }

    if ($null -ne $Failure) {
        throw $Failure
    }
}
finally {
    Pop-Location
}
