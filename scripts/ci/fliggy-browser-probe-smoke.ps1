param(
    [string] $Origin = "北京",
    [string] $Destination = "上海",
    [string] $DepartureDate = "",
    [switch] $Headed,
    [double] $DeadlineSeconds = 30,
    [string] $OutputPath = "",
    [ValidateRange(0, 6)]
    [int] $PlannedObservation = 0,
    [ValidateRange(0, 300)]
    [double] $HeadedObservationPauseSeconds = 0
)

$ErrorActionPreference = "Stop"

$RepoRoot = Resolve-Path (Join-Path $PSScriptRoot "..\..")
$BackendDir = Join-Path $RepoRoot "apps\backend"

if ([string]::IsNullOrWhiteSpace($DepartureDate)) {
    $DepartureDate = (Get-Date).AddDays(14).ToString("yyyy-MM-dd")
}

$Utf8 = [System.Text.UTF8Encoding]::new($false)
foreach ($Value in @($Origin, $Destination, $DepartureDate)) {
    $RoundTrip = $Utf8.GetString($Utf8.GetBytes($Value))
    if ($RoundTrip -cne $Value) {
        throw "Unicode-safe preflight failed before provider access."
    }
}
if ($PlannedObservation -gt 0 -and [string]::IsNullOrWhiteSpace($OutputPath)) {
    throw "A planned diagnostic observation requires -OutputPath."
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
    if ($PlannedObservation -gt 0) {
        $Args += @("--planned-observation", "$PlannedObservation")
    }
    if ($null -ne $ResolvedOutputPath) {
        $Args += @("--evidence-output-path", $ResolvedOutputPath)
    }
    if ($HeadedObservationPauseSeconds -gt 0) {
        $Args += @("--headed-observation-pause-seconds", "$HeadedObservationPauseSeconds")
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
