[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$Executable,
    [Parameter(Mandatory = $true)][string]$ReportRoot,
    [switch]$CandidateSmoke,
    [ValidateRange(20, 100)][int]$LaunchCount = 20,
    [ValidateRange(3600, 86400)][int]$DurationSeconds = 3600
)
$ErrorActionPreference = "Stop"
if ($Executable -match '20260908-190153|20260908-190759') {
    throw "This artifact is withdrawn after protection detections; it must not be read or run."
}
$Executable = (Resolve-Path -LiteralPath $Executable).Path
$BundleRoot = Split-Path -Parent $Executable
if (-not (Test-Path -LiteralPath (Join-Path $BundleRoot "assets\live2d\Maple\Maple.model3.json"))) {
    throw "Visible QA requires the new directory application with adjacent runtime assets."
}
$ReportRoot = [System.IO.Path]::GetFullPath($ReportRoot)
if ($ReportRoot.StartsWith($BundleRoot.TrimEnd('\') + '\', [StringComparison]::OrdinalIgnoreCase) -or
        $ReportRoot.Equals($BundleRoot, [StringComparison]::OrdinalIgnoreCase)) {
    throw "QA evidence must be outside the frozen distribution directory."
}
if ((Test-Path -LiteralPath $ReportRoot) -and (Get-ChildItem -LiteralPath $ReportRoot -Force | Select-Object -First 1)) {
    throw "Use a new empty report directory so previous evidence cannot be overwritten."
}
$ForbiddenOverrides = @("MEINIFENG_DISABLE_RUNTIME", "MEINIFENG_DISABLE_GLOBAL_ACTIVITY", "MEINIFENG_DISABLE_AUDIO",
    "MEINIFENG_SMOKE_TEST", "CUTEMAPLE_DISABLE_ACTIVITY", "MEINIFENG_DISABLE_AUTOSTART", "MEINIFENG_DISABLE_CLEAN_TASK",
    "QT_QPA_PLATFORM", "QT_QUICK_BACKEND", "QT_OPENGL", "QTWEBENGINE_CHROMIUM_FLAGS", "QTWEBENGINE_DISABLE_SANDBOX",
    "QTWEBENGINEPROCESS_PATH", "QT_PLUGIN_PATH")
$PresentOverrides = @($ForbiddenOverrides | Where-Object { [Environment]::GetEnvironmentVariable($_, "Process") })
if ($PresentOverrides.Count) { throw ("Normal desktop QA rejects inherited overrides: " + ($PresentOverrides -join ", ")) }
New-Item -ItemType Directory -Force -Path $ReportRoot | Out-Null
$ObservedStart = Get-Date
$Owned = @{}
$SeenEvents = @{}
$Detections = [System.Collections.Generic.List[object]]::new()
$Launches = [System.Collections.Generic.List[object]]::new()
$NativeFaultLogs = [System.Collections.Generic.List[object]]::new()
$CurrentProcess = $null
$Failure = $null
$FullReport = $null
$ExecutableHash = $null
$AssetHashes = @{}
$PackageHashes = @{}
$PackageUnchanged = $false

function Write-JsonFile($Value, [string]$Path) {
    $Value | ConvertTo-Json -Depth 24 | Set-Content -LiteralPath $Path -Encoding utf8
}

function Read-ProtectionState {
    $State = Get-MpComputerStatus -ErrorAction Stop
    $Result = [ordered]@{}
    foreach ($Name in @("AntivirusEnabled", "RealTimeProtectionEnabled", "BehaviorMonitorEnabled", "OnAccessProtectionEnabled")) {
        $Result[$Name] = $State.$Name
        if ($State.$Name -ne $true) { throw "Default protection unavailable: $Name. No QA application will be started." }
    }
    $Result.AMRunningMode = $State.AMRunningMode
    return $Result
}

function Read-RelevantDetectionEvents([datetime]$Since) {
    $QueryErrors = @()
    $Events = @(Get-WinEvent -FilterHashtable @{
        LogName = "Microsoft-Windows-Windows Defender/Operational"
        Id = @(1116, 1117, 1118, 1119, 1121, 1122); StartTime = $Since
    } -ErrorAction SilentlyContinue -ErrorVariable QueryErrors)
    foreach ($Problem in $QueryErrors) {
        if ($Problem.FullyQualifiedErrorId -notlike "NoMatchingEventsFound*") { throw $Problem }
    }
    foreach ($Entry in $Events) {
        $Raw = $Entry.ToXml()
        $MatchesBundle = $Raw -match [regex]::Escape($BundleRoot)
        $MatchesOwned = $false
        foreach ($KnownId in $Owned.Keys) {
            if ($Raw -match ("process:_pid:" + $KnownId + "[,;]")) { $MatchesOwned = $true; break }
        }
        if (-not ($MatchesBundle -or $MatchesOwned)) { continue }
        [xml]$Xml = $Raw
        $Data = @{}
        foreach ($Field in $Xml.Event.EventData.Data) { $Data[$Field.Name] = [string]$Field.'#text' }
        [pscustomobject]@{
            time = $Entry.TimeCreated.ToUniversalTime().ToString("o"); id = $Entry.Id; recordId = $Entry.RecordId
            threat = $Data["Threat Name"]; path = $Data["Path"]; process = $Data["Process Name"]
            action = $Data["Action Name"]; error = $Data["Error Code"]
        }
    }
}

function Assert-NoDetections([datetime]$Since) {
    $Found = @(Read-RelevantDetectionEvents $Since)
    foreach ($Entry in $Found) {
        if (-not $SeenEvents.ContainsKey([string]$Entry.recordId)) {
            $SeenEvents[[string]$Entry.recordId] = $true
            $Detections.Add($Entry)
        }
    }
    if ($Found.Count) { throw "Protection detected this bundle or an owned process. QA stopped; do not retry or restore it." }
}

function Read-AssetHashes {
    $Values = @{}
    $ModelRoot = Join-Path $BundleRoot "assets\live2d\Maple"
    foreach ($File in Get-ChildItem -LiteralPath $ModelRoot -Recurse -File) {
        $Relative = [System.IO.Path]::GetRelativePath($BundleRoot, $File.FullName).Replace('\', '/')
        $Values[$Relative] = (Get-FileHash -LiteralPath $File.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
    }
    foreach ($Relative in @("web/dist/app.js", "web/dist/build-manifest.json", "web/dist/live2dcubismcore.min.js")) {
        $Values[$Relative] = (Get-FileHash -LiteralPath (Join-Path $BundleRoot $Relative) -Algorithm SHA256).Hash.ToLowerInvariant()
    }
    return $Values
}

function Read-PackageHashes {
    $Values = @{}
    foreach ($File in Get-ChildItem -LiteralPath $BundleRoot -Recurse -File) {
        if ($File.Attributes -band [System.IO.FileAttributes]::ReparsePoint) {
            throw "QA distribution contains a linked file: $($File.FullName)"
        }
        $Relative = [System.IO.Path]::GetRelativePath($BundleRoot, $File.FullName).Replace('\', '/')
        $Values[$Relative] = (Get-FileHash -LiteralPath $File.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
    }
    return $Values
}

function Assert-SameBundle {
    Assert-NoDetections $ObservedStart
    if ((Get-FileHash -LiteralPath $Executable -Algorithm SHA256).Hash.ToLowerInvariant() -ne $ExecutableHash) {
        throw "Executable changed during QA."
    }
    $CurrentAssets = Read-AssetHashes
    if ($CurrentAssets.Count -ne $AssetHashes.Count) { throw "Runtime asset inventory changed during QA." }
    foreach ($Name in $AssetHashes.Keys) {
        if ($CurrentAssets[$Name] -ne $AssetHashes[$Name]) { throw "Runtime asset changed during QA: $Name" }
    }
    Assert-NoDetections $ObservedStart
}

function Track-OwnedChildren([int]$ParentIdentifier) {
    foreach ($Child in @(Get-CimInstance Win32_Process -Filter ("ParentProcessId = " + $ParentIdentifier) -ErrorAction Stop)) {
        $Identifier = [int]$Child.ProcessId
        if (-not $Owned.ContainsKey($Identifier) -or
                $Owned[$Identifier].createdAt -ne $Child.CreationDate.ToUniversalTime().ToString("o")) {
            $Owned[$Identifier] = [pscustomobject]@{id = $Identifier; parentId = $ParentIdentifier
                createdAt = $Child.CreationDate.ToUniversalTime().ToString("o"); path = $Child.ExecutablePath}
        }
        Track-OwnedChildren $Identifier
    }
}

function Stop-OwnedOrphans {
    $Stopped = @()
    foreach ($Identifier in @($Owned.Keys)) {
        $Record = $Owned[$Identifier]
        $Process = Get-Process -Id $Identifier -ErrorAction SilentlyContinue
        if ($null -eq $Process) { continue }
        # A recycled PID must never be terminated. Only the exact process seen
        # as a descendant of this QA launch can be stopped.
        $ExpectedStart = [datetime]::Parse($Record.createdAt).ToUniversalTime()
        if ([math]::Abs(($Process.StartTime.ToUniversalTime() - $ExpectedStart).TotalMilliseconds) -gt 2) { continue }
        if ($Process.WaitForExit(2000)) { continue }
        Stop-Process -Id $Identifier -Force -ErrorAction Stop
        $Stopped += $Record
    }
    return $Stopped
}

function Read-NativeFaultEvidence([string]$Profile, [string]$RunName) {
    $Logs = Join-Path $Profile "logs"
    if (-not (Test-Path -LiteralPath $Logs)) { return }
    foreach ($File in Get-ChildItem -LiteralPath $Logs -Filter "*.fault.log" -File) {
        if ($File.Length -eq 0) { continue }
        $Content = Get-Content -LiteralPath $File.FullName -Raw
        [ordered]@{run = $RunName; path = [System.IO.Path]::GetRelativePath($ReportRoot, $File.FullName)
            bytes = $File.Length; sha256 = (Get-FileHash -LiteralPath $File.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
            exceptionCodes = @([regex]::Matches($Content, 'code (0x[0-9a-fA-F]+)') | ForEach-Object { $_.Groups[1].Value.ToLowerInvariant() } | Select-Object -Unique)
            observation = "Native exception text was recorded; the wording alone does not establish process termination. Review exit, WER, and subsequent activity."}
    }
}

function Invoke-VisibleRun([string]$Name, [string]$Scenario, [int]$Seconds) {
    Assert-SameBundle
    $RunRoot = Join-Path $ReportRoot $Name
    New-Item -ItemType Directory -Path $RunRoot | Out-Null
    $Profile = Join-Path $RunRoot "profile"
    $Report = Join-Path $RunRoot "desktop-report.json"
    $Start = [System.Diagnostics.ProcessStartInfo]::new()
    $Start.FileName = $Executable
    $Start.WorkingDirectory = $BundleRoot
    $Start.UseShellExecute = $false
    $Start.CreateNoWindow = $true
    $Start.WindowStyle = [System.Diagnostics.ProcessWindowStyle]::Hidden
    $Start.RedirectStandardOutput = $true
    $Start.RedirectStandardError = $true
    $Start.Environment["MEINIFENG_PROFILE_DIRECTORY"] = $Profile
    foreach ($Argument in @("--verify-desktop", "--report", $Report, "--profile", $Profile,
                            "--duration", [string]$Seconds, "--scenario", $Scenario)) { $Start.ArgumentList.Add($Argument) }
    $Process = [System.Diagnostics.Process]::new()
    $Process.StartInfo = $Start
    $script:CurrentProcess = $Process
    if (-not $Process.Start()) { throw "Unable to start the new QA executable." }
    $StandardOutput = $Process.StandardOutput.ReadToEndAsync()
    $StandardError = $Process.StandardError.ReadToEndAsync()
    $Deadline = (Get-Date).AddSeconds($Seconds + 120)
    $PollAfter = Get-Date
    $ProgressAfter = (Get-Date).AddSeconds(60)
    try {
        while (-not $Process.WaitForExit(1000)) {
            if ((Get-Date) -ge $PollAfter) {
                Track-OwnedChildren $Process.Id
                Assert-NoDetections $ObservedStart
                $PollAfter = (Get-Date).AddSeconds(3)
            }
            if ((Get-Date) -ge $ProgressAfter) {
                Write-Host ("Visible QA " + $Name + " is running; deadline " + $Deadline.ToString("HH:mm:ss"))
                $ProgressAfter = (Get-Date).AddSeconds(60)
            }
            if ((Get-Date) -ge $Deadline) { throw "Visible QA exceeded its duration and shutdown allowance." }
        }
    } finally {
        if (-not $Process.HasExited) { $Process.Kill($true); $null = $Process.WaitForExit(5000) }
        $Orphans = @(Stop-OwnedOrphans)
        if ($StandardOutput.Wait(2000)) { $StandardOutput.Result | Set-Content -LiteralPath (Join-Path $RunRoot "stdout.txt") -Encoding utf8 }
        if ($StandardError.Wait(2000)) { $StandardError.Result | Set-Content -LiteralPath (Join-Path $RunRoot "stderr.txt") -Encoding utf8 }
        Write-JsonFile $Orphans (Join-Path $RunRoot "owned-orphans-terminated.json")
        $RunFaultLogs = @(Read-NativeFaultEvidence $Profile $Name)
        foreach ($Fault in $RunFaultLogs) { $NativeFaultLogs.Add($Fault) }
        Write-JsonFile @{nativeExceptionObserved = $RunFaultLogs.Count -gt 0; logs = $RunFaultLogs
            reviewStatus = $(if ($RunFaultLogs.Count) { "pending" } else { "not-observed" })} (Join-Path $RunRoot "native-fault-review.json")
        Write-JsonFile @{pid = $Process.Id; exitCode = $(if ($Process.HasExited) { $Process.ExitCode } else { $null })
            ownedOrphansTerminated = $Orphans.Count; reportProduced = (Test-Path -LiteralPath $Report)} (Join-Path $RunRoot "process-summary.json")
        $script:CurrentProcess = $null
    }
    Assert-NoDetections $ObservedStart
    $ExitCode = $Process.ExitCode
    $Process.Dispose()
    if (-not (Test-Path -LiteralPath $Report)) { throw "Visible QA produced no report; inspect entry/fault logs and Windows events." }
    $Result = Get-Content -LiteralPath $Report -Raw | ConvertFrom-Json -AsHashtable
    if ($ExitCode -ne 0 -or $Result.passed -ne $true -or $Result.nativeProvidersActive -ne $true -or $Orphans.Count) {
        throw "Visible run failed or required orphan cleanup: $Name. See its report and logs."
    }
    if ($Result.executableSha256 -ne $ExecutableHash) { throw "QA report does not match the actual executable." }
    foreach ($Asset in $Result.assetSha256.Keys) {
        if ($Result.assetSha256[$Asset] -ne $AssetHashes[$Asset]) { throw "QA report loaded another runtime asset: $Asset" }
    }
    Assert-SameBundle
    return $Result
}

$ProtectionBefore = $null
$ProtectionAfter = $null
try {
    $ProtectionBefore = Read-ProtectionState
    Assert-NoDetections (Get-Date).AddDays(-14)
    $ExecutableHash = (Get-FileHash -LiteralPath $Executable -Algorithm SHA256).Hash.ToLowerInvariant()
    $AssetHashes = Read-AssetHashes
    $PackageHashes = Read-PackageHashes
    Assert-NoDetections $ObservedStart
    $RunsToExecute = if ($CandidateSmoke) { 1 } else { $LaunchCount }
    for ($Index = 1; $Index -le $RunsToExecute; $Index++) {
        $Name = "launch-" + $Index.ToString("00")
        $Result = Invoke-VisibleRun $Name "startup" 20
        $Launches.Add([ordered]@{passed = $true; executableSha256 = $ExecutableHash; assetMatch = $true
            report = "$Name/desktop-report.json"; exitCode = $Result.exitCode; durationSeconds = $Result.durationSeconds})
        Write-Host ("Completed visible launch " + $Index + "/" + $RunsToExecute)
    }
    $FullReport = if ($CandidateSmoke) { $Result } else { Invoke-VisibleRun "soak" "full" $DurationSeconds }
    $ProtectionAfter = Read-ProtectionState
    Assert-NoDetections $ObservedStart
    $FinalPackageHashes = Read-PackageHashes
    if ($FinalPackageHashes.Count -ne $PackageHashes.Count) { throw "Distribution file inventory changed during QA." }
    foreach ($Name in $PackageHashes.Keys) {
        if ($FinalPackageHashes[$Name] -ne $PackageHashes[$Name]) { throw "Distribution file changed during QA: $Name" }
    }
    $PackageUnchanged = $true
} catch {
    $Failure = $_.Exception.Message
    # Reading relevant event metadata is allowed after a detection; the
    # executable is not reopened, restored, or retried.
    try { Assert-NoDetections $ObservedStart } catch { }
    try { $ProtectionAfter = Read-ProtectionState } catch { }
} finally {
    if ($CurrentProcess -and -not $CurrentProcess.HasExited) { $CurrentProcess.Kill($true) }
    $Remaining = @(Stop-OwnedOrphans)
    if ($Remaining.Count -and -not $Failure) { $Failure = "Owned processes remained after the final run." }
}
$ObservedEnd = Get-Date
$ProtectionUnchanged = $null -ne $ProtectionBefore -and $null -ne $ProtectionAfter
if ($ProtectionUnchanged) {
    foreach ($Name in $ProtectionBefore.Keys) {
        if ($ProtectionBefore[$Name] -ne $ProtectionAfter[$Name]) { $ProtectionUnchanged = $false }
    }
}
$Security = [ordered]@{
    status = $(if ($Detections.Count) { "detected" } elseif ($Failure -or -not $ProtectionUnchanged) { "incomplete" } else { "no-detections-observed" })
    protectionUnchanged = $ProtectionUnchanged; protectionBefore = $ProtectionBefore; protectionAfter = $ProtectionAfter
    observationStart = $ObservedStart.ToUniversalTime().ToString("o"); observationEnd = $ObservedEnd.ToUniversalTime().ToString("o")
    checkedAt = (Get-Date).ToUniversalTime().ToString("o"); detections = $Detections.ToArray()
}
$Aggregate = if ($FullReport) { $FullReport } else { @{schemaVersion = 1; checks = @{}; errors = @(); passed = $false; durationSeconds = 0} }
$Aggregate.executableSha256 = $ExecutableHash
$Aggregate.profileIsolated = $true
$Aggregate.launchEvidence = @{successfulLaunches = $Launches.Count; requiredLaunches = $LaunchCount; runs = $Launches.ToArray()}
$Aggregate.securityObservation = $Security
$Aggregate.assetSha256 = $AssetHashes
$Aggregate.packageSha256 = $PackageHashes
$Aggregate.packageUnchanged = $PackageUnchanged
$Aggregate.nativeExceptionObserved = $NativeFaultLogs.Count -gt 0
$Aggregate.nativeExceptionLogs = $NativeFaultLogs.ToArray()
$Aggregate.nativeExceptionReview = @{status = $(if ($NativeFaultLogs.Count) { "pending" } else { "not-observed" })}
$Aggregate.checks.repeatedLaunch = $Launches.Count -ge $LaunchCount
$Aggregate.technicalPassed = $null -eq $Failure -and $FullReport -and $FullReport.passed -and $PackageUnchanged
$Aggregate.allRequiredChecksPassed = $Aggregate.checks.Count -ge 8 -and @($Aggregate.checks.Values | Where-Object { $_ -ne $true }).Count -eq 0
$Aggregate.passed = [bool]($Aggregate.technicalPassed -and $Aggregate.allRequiredChecksPassed -and
    -not $Aggregate.nativeExceptionObserved -and $Security.status -eq "no-detections-observed")
$Aggregate.releaseStatus = $(if ($Aggregate.passed) { "DESKTOP-CHECKS-PASSED" } elseif ($Detections.Count) { "DETECTED-DO-NOT-RUN" } else { "PENDING" })
$Aggregate.candidateSmokeOnly = [bool]$CandidateSmoke
$Aggregate.candidateSmokePassed = [bool]($CandidateSmoke -and $Aggregate.technicalPassed -and $Security.status -eq "no-detections-observed")
if ($Aggregate.candidateSmokePassed) { $Aggregate.releaseStatus = "CANDIDATE-SMOKE-PASSED-NOT-RELEASE" }
if ($Failure) { $Aggregate.errors = @($Aggregate.errors) + $Failure }
Write-JsonFile $Aggregate (Join-Path $ReportRoot "NORMAL-DESKTOP-QA.json")
Write-JsonFile @($Owned.Values) (Join-Path $ReportRoot "owned-processes.json")
Write-Host ("Desktop QA status: " + $Aggregate.releaseStatus + "; report: " + (Join-Path $ReportRoot "NORMAL-DESKTOP-QA.json"))
if ($Failure) { throw $Failure }
if (-not ($Aggregate.passed -or $Aggregate.candidateSmokePassed)) { exit 2 }
