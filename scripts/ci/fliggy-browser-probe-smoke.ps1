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
    [double] $HeadedObservationPauseSeconds = 0,
    [ValidateSet("", "current_click", "pointer_mouse")]
    [string] $DestinationActivationProbe = ""
)

$ErrorActionPreference = "Stop"

$RepoRoot = Resolve-Path (Join-Path $PSScriptRoot "..\..")
$BackendDir = Join-Path $RepoRoot "apps\backend"

if ([string]::IsNullOrWhiteSpace($DepartureDate)) {
    $DepartureDate = (Get-Date).AddDays(14).ToString("yyyy-MM-dd")
}

$Utf8 = [System.Text.UTF8Encoding]::new($false)
$env:PYTHONIOENCODING = "utf-8"
$OutputEncoding = $Utf8
[Console]::OutputEncoding = $Utf8
foreach ($Value in @($Origin, $Destination, $DepartureDate)) {
    $RoundTrip = $Utf8.GetString($Utf8.GetBytes($Value))
    if ($RoundTrip -cne $Value) {
        throw "Unicode-safe preflight failed before provider access."
    }
}
if ($PlannedObservation -gt 0 -and [string]::IsNullOrWhiteSpace($OutputPath)) {
    throw "A planned diagnostic observation requires -OutputPath."
}
if ($PlannedObservation -gt 0) {
    $ExpectedOrigin = -join ([char]0x5317, [char]0x4EAC)
    $ExpectedDestination = -join ([char]0x4E0A, [char]0x6D77)
    if (
        $Origin -cne $ExpectedOrigin -or
        $Destination -cne $ExpectedDestination -or
        $DepartureDate -cne "2026-09-14"
    ) {
        throw "Planned observation query identity preflight failed before provider access."
    }
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
        $Args += @("--output-json", $ResolvedOutputPath)
    }
    if ($HeadedObservationPauseSeconds -gt 0) {
        $Args += @("--headed-observation-pause-seconds", "$HeadedObservationPauseSeconds")
    }
    if (-not [string]::IsNullOrWhiteSpace($DestinationActivationProbe)) {
        $Args += @("--destination-activation-probe", $DestinationActivationProbe)
    }
    if ($null -eq $ResolvedOutputPath) {
        uv run python @Args
    }
    else {
        $ConsoleOutputPath = "$ResolvedOutputPath.console.log"
        uv run python @Args *>&1 | Tee-Object -FilePath $ConsoleOutputPath
    }
    $ExitCode = $LASTEXITCODE
}
finally {
    Pop-Location
}
exit $ExitCode
