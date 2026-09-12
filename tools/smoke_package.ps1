[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$Executable,
    [Parameter(Mandatory = $true)][string]$ReportRoot
)
$ErrorActionPreference = "Stop"
if ($Executable -match '20260908-190153|20260908-190759') {
    throw "This build is withdrawn after Windows protection detections. Do not run or read its binaries."
}
$Executable = (Resolve-Path -LiteralPath $Executable).Path
$ReportRoot = [System.IO.Path]::GetFullPath($ReportRoot)
New-Item -ItemType Directory -Force -Path $ReportRoot | Out-Null
$EnvironmentNames = @("APPDATA", "MEINIFENG_PROFILE_DIRECTORY", "MEINIFENG_DISABLE_AUTOSTART", "MEINIFENG_DISABLE_CLEAN_TASK",
                       "MEINIFENG_SMOKE_TEST", "CUTEMAPLE_DISABLE_ACTIVITY", "QT_QPA_PLATFORM")
$Previous = @{}
foreach ($Name in $EnvironmentNames) { $Previous[$Name] = [Environment]::GetEnvironmentVariable($Name, "Process") }

function Invoke-CheckedProcess([string[]]$ProcessArguments, [string]$LogName, [string]$Program = $Executable) {
    $StartInfo = [System.Diagnostics.ProcessStartInfo]::new()
    $StartInfo.FileName = $Program
    $StartInfo.WorkingDirectory = Split-Path -Parent $Program
    $StartInfo.UseShellExecute = $false
    $StartInfo.CreateNoWindow = $true
    $StartInfo.WindowStyle = [System.Diagnostics.ProcessWindowStyle]::Hidden
    $StartInfo.RedirectStandardError = $true
    $StartInfo.RedirectStandardOutput = $true
    foreach ($Argument in $ProcessArguments) { $StartInfo.ArgumentList.Add($Argument) }
    $Process = [System.Diagnostics.Process]::new()
    $Process.StartInfo = $StartInfo
    if (-not $Process.Start()) { throw "Could not start packaged executable." }
    $StandardOutput = $Process.StandardOutput.ReadToEndAsync()
    $StandardError = $Process.StandardError.ReadToEndAsync()
    if (-not $Process.WaitForExit(60000)) {
        $Process.Kill($true)
        throw "Packaged verification exceeded 60 seconds."
    }
    $StandardOutput.Result | Set-Content -LiteralPath (Join-Path $ReportRoot "$LogName.stdout.txt") -Encoding utf8
    $StandardError.Result | Set-Content -LiteralPath (Join-Path $ReportRoot "$LogName.stderr.txt") -Encoding utf8
    $ExitCode = $Process.ExitCode
    $Process.Dispose()
    if ($ExitCode -ne 0) { throw "Packaged $LogName failed with exit code $ExitCode; see $ReportRoot." }
    return $ExitCode
}

try {
    $env:APPDATA = Join-Path $ReportRoot "profile"
    $env:MEINIFENG_PROFILE_DIRECTORY = Join-Path $ReportRoot "profile\美腻枫"
    $env:MEINIFENG_DISABLE_AUTOSTART = "1"
    $env:MEINIFENG_DISABLE_CLEAN_TASK = "1"
    $env:CUTEMAPLE_DISABLE_ACTIVITY = "1"
    $env:QT_QPA_PLATFORM = "offscreen"
    $env:MEINIFENG_SMOKE_TEST = "1"
    $AppExit = Invoke-CheckedProcess -ProcessArguments @() -LogName "application-smoke"
    $env:MEINIFENG_SMOKE_TEST = $null
    $RuntimeReport = Join-Path $ReportRoot "packaged-runtime.json"
    $RuntimeExit = Invoke-CheckedProcess -ProcessArguments @("--verify-live2d", "--report", $RuntimeReport, "--timeout", "40") -LogName "runtime-smoke"
    $Runtime = Get-Content -LiteralPath $RuntimeReport -Raw | ConvertFrom-Json
    if (-not $Runtime.passed) { throw "Packaged runtime report did not pass." }
    $Helper = Join-Path (Split-Path -Parent $Executable) "cleaner\CuteMaple-Cleaner.exe"
    $HelperReport = Join-Path $ReportRoot "helper-runtime.json"
    $HelperExit = Invoke-CheckedProcess -ProcessArguments @("--diagnose", "--report", $HelperReport) -LogName "helper-smoke" -Program $Helper
    $HelperResult = Get-Content -LiteralPath $HelperReport -Raw | ConvertFrom-Json
    if ($HelperResult.qtImported -ne $false -or $HelperResult.privilegedOperationPerformed -ne $false) {
        throw "Independent helper diagnostic failed the no-Qt/no-system-operation boundary."
    }
    $Result = [ordered]@{
        executable = $Executable
        sha256 = (Get-FileHash -LiteralPath $Executable -Algorithm SHA256).Hash
        bytes = (Get-Item -LiteralPath $Executable).Length
        applicationSmokeExit = $AppExit
        helperSmokeExit = $HelperExit
        helperSha256 = (Get-FileHash -LiteralPath $Helper -Algorithm SHA256).Hash
        helperDiagnosticPassed = $true
        runtimeSmokeExit = $RuntimeExit
        live2dPassed = $Runtime.passed
        nativeIdleCycles = $Runtime.nativeIdleCycles
        nativeLandingFinished = $Runtime.nativeLandingFinished
        states = $Runtime.states.Count
        visiblePixels = $Runtime.visiblePixels
        errors = $Runtime.errors
        verificationScope = "offscreen-renderer-and-short-startup-only"
        normalDesktopRunVerified = $false
        securityReviewStatus = "not-assessed"
        sensorsEnabled = $false
        startupAndCleanupTaskCreationEnabled = $false
        applicationSmokeDurationSeconds = 1.6
        isolatedProfile = $env:APPDATA
        isolatedSettingsDirectory = $env:MEINIFENG_PROFILE_DIRECTORY
        completedAt = (Get-Date).ToString("o")
    }
    $Result | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath (Join-Path $ReportRoot "smoke-summary.json") -Encoding utf8
    $Result | ConvertTo-Json -Depth 6
} finally {
    foreach ($Name in $EnvironmentNames) { [Environment]::SetEnvironmentVariable($Name, $Previous[$Name], "Process") }
}
