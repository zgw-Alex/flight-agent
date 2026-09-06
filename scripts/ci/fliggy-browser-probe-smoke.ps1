param(
    [string] $Origin = "北京",
    [string] $Destination = "上海",
    [string] $DepartureDate = "",
    [switch] $Headed,
    [double] $DeadlineSeconds = 30,
    [string] $OutputPath = ""
)

$ErrorActionPreference = "Stop"

$RepoRoot = Resolve-Path (Join-Path $PSScriptRoot "..\..")
$BackendDir = Join-Path $RepoRoot "apps\backend"

if ([string]::IsNullOrWhiteSpace($DepartureDate)) {
    $DepartureDate = (Get-Date).AddDays(14).ToString("yyyy-MM-dd")
}

$ResolvedOutputPath = $null
if (-not [string]::IsNullOrWhiteSpace($OutputPath)) {
    $OutputParent = Split-Path -Parent $OutputPath
    if (-not [string]::IsNullOrWhiteSpace($OutputParent)) {
        New-Item -ItemType Directory -Force -Path $OutputParent | Out-Null
    }
    $ResolvedOutputPath = $ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath($OutputPath)
}

Push-Location $BackendDir
$ExitCode = 0
try {
    $Args = @(
        (Join-Path $RepoRoot "scripts\smoke\fliggy_browser_probe.py"),
        "--origin", $Origin,
        "--destination", $Destination,
        "--departure-date", $DepartureDate,
        "--deadline-seconds", "$DeadlineSeconds",
        "--experiment-run-id", "m9-bp5-u1-opt-in-smoke"
    )
    if ($Headed) {
        $Args += "--headed"
    }
    if ($null -eq $ResolvedOutputPath) {
        uv run python @Args
    }
    else {
        uv run python @Args *>&1 | Tee-Object -FilePath $ResolvedOutputPath
    }
    $ExitCode = $LASTEXITCODE
}
finally {
    Pop-Location
}
exit $ExitCode
