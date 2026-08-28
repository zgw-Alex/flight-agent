param(
    [int] $TimeoutSeconds = 90
)

$ErrorActionPreference = "Stop"

$RepoRoot = Resolve-Path (Join-Path $PSScriptRoot "..\..")
$Failure = $null

function Test-LoopbackPortAvailable {
    param(
        [Parameter(Mandatory = $true)]
        [int] $Port
    )

    $listener = $null
    try {
        $listener = [System.Net.Sockets.TcpListener]::new([System.Net.IPAddress]::Parse("127.0.0.1"), $Port)
        $listener.Start()
        return $true
    }
    catch {
        return $false
    }
    finally {
        if ($null -ne $listener) {
            $listener.Stop()
        }
    }
}

function Select-PostgresHostPort {
    if (-not [string]::IsNullOrWhiteSpace($env:POSTGRES_PORT)) {
        return [int] $env:POSTGRES_PORT
    }
    if (Test-LoopbackPortAvailable -Port 55432) {
        return 55432
    }
    foreach ($Port in 55433..55641) {
        if (Test-LoopbackPortAvailable -Port $Port) {
            return $Port
        }
    }
    throw "No available loopback port found for postgres CI"
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
        $env:POSTGRES_PORT = [string] (Select-PostgresHostPort)
        Write-Host "==> postgres host port $env:POSTGRES_PORT"
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
