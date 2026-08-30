param(
    [string] $Origin = "北京",
    [string] $Destination = "上海",
    [string] $DepartureDate = "",
    [switch] $Headed,
    [double] $DeadlineSeconds = 30
)

$ErrorActionPreference = "Stop"

$RepoRoot = Resolve-Path (Join-Path $PSScriptRoot "..\..")
$BackendDir = Join-Path $RepoRoot "apps\backend"

if ([string]::IsNullOrWhiteSpace($DepartureDate)) {
    $DepartureDate = (Get-Date).AddDays(14).ToString("yyyy-MM-dd")
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
    uv run python @Args
    $ExitCode = $LASTEXITCODE
}
finally {
    Pop-Location
}
exit $ExitCode
