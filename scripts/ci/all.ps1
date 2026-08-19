param()

$ErrorActionPreference = "Continue"

$Scripts = @(
    @{ Name = "backend"; Path = Join-Path $PSScriptRoot "backend.ps1" },
    @{ Name = "frontend"; Path = Join-Path $PSScriptRoot "frontend.ps1" },
    @{ Name = "postgres"; Path = Join-Path $PSScriptRoot "postgres.ps1" }
)

$Results = @()

foreach ($Script in $Scripts) {
    Write-Host "==> run-all-and-aggregate: $($Script.Name)"
    $global:LASTEXITCODE = 0
    try {
        & $Script.Path
        $ExitCode = $LASTEXITCODE
        if (-not $?) {
            $ExitCode = 1
        }
    }
    catch {
        Write-Error $_
        $ExitCode = 1
    }
    if ($null -eq $ExitCode -or $ExitCode -eq "") {
        $ExitCode = 0
    }
    $Results += [pscustomobject]@{
        Name = $Script.Name
        ExitCode = $ExitCode
    }
}

Write-Host "==> aggregate summary"
$HasFailure = $false
foreach ($Result in $Results) {
    if ($Result.ExitCode -eq 0) {
        Write-Host "PASS $($Result.Name)"
    }
    else {
        Write-Host "FAIL $($Result.Name) exit=$($Result.ExitCode)"
        $HasFailure = $true
    }
}

if ($HasFailure) {
    Write-Host "Overall FAIL"
    exit 1
}

Write-Host "Overall PASS"
