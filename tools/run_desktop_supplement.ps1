#requires -Version 7.0
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$Executable,
    [Parameter(Mandatory = $true)][string]$ReportRoot,
    [Parameter(Mandatory = $true)][string]$BaseReport,
    [ValidateRange(20, 1800)][int]$DurationSeconds = 300
)
$ErrorActionPreference = 'Stop'
if ($Executable -match '20260908-190153|20260908-190759') {
    throw 'This artifact is withdrawn after protection detections; do not read or run it.'
}
$Identity = [Security.Principal.WindowsIdentity]::GetCurrent()
$Principal = [Security.Principal.WindowsPrincipal]::new($Identity)
if ($Principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw 'Run this observation from an ordinary non-administrator PowerShell session.'
}
$Executable = (Resolve-Path -LiteralPath $Executable).Path
$BaseReport = (Resolve-Path -LiteralPath $BaseReport).Path
$BundleRoot = Split-Path -Parent $Executable
$ReportRoot = [IO.Path]::GetFullPath($ReportRoot)
if ($ReportRoot.StartsWith($BundleRoot.TrimEnd('\') + '\', [StringComparison]::OrdinalIgnoreCase) -or
        $ReportRoot.Equals($BundleRoot, [StringComparison]::OrdinalIgnoreCase)) {
    throw 'Supplementary evidence must be outside the frozen distribution directory.'
}
if ((Test-Path -LiteralPath $ReportRoot) -and (Get-ChildItem -LiteralPath $ReportRoot -Force | Select-Object -First 1)) {
    throw 'Use a new empty report directory; earlier evidence must remain unchanged.'
}
$Forbidden = @('MEINIFENG_DISABLE_RUNTIME','MEINIFENG_DISABLE_GLOBAL_ACTIVITY','MEINIFENG_DISABLE_AUDIO',
    'MEINIFENG_SMOKE_TEST','CUTEMAPLE_DISABLE_ACTIVITY','MEINIFENG_DISABLE_AUTOSTART','MEINIFENG_DISABLE_CLEAN_TASK',
    'QT_QPA_PLATFORM','QT_QUICK_BACKEND','QT_OPENGL','QTWEBENGINE_CHROMIUM_FLAGS','QTWEBENGINE_DISABLE_SANDBOX',
    'QTWEBENGINEPROCESS_PATH','QT_PLUGIN_PATH')
$Inherited = @($Forbidden | Where-Object { [Environment]::GetEnvironmentVariable($_,'Process') })
if ($Inherited.Count) { throw ('Ordinary desktop observation rejects inherited overrides: ' + ($Inherited -join ', ')) }
$Base = Get-Content -LiteralPath $BaseReport -Raw | ConvertFrom-Json -AsHashtable
if ($Base.completed -ne $true -or $Base.scenario -ne 'full' -or $Base.durationSeconds -lt 3600 -or
    $Base.exitCode -ne 0 -or $Base.candidateSmokeOnly -ne $false -or $Base.technicalPassed -ne $true -or
    $Base.launchEvidence.successfulLaunches -lt 20 -or @($Base.launchEvidence.runs).Count -lt 20 -or
    -not $Base.assetSha256 -or $Base.assetSha256.Count -eq 0) {
    throw 'Base report must preserve a completed successful technical 60-minute run and at least 20 launches. Manual checks may remain pending.'
}
foreach ($Launch in $Base.launchEvidence.runs) {
    if ($Launch.passed -ne $true -or $Launch.assetMatch -ne $true -or $Launch.exitCode -ne 0 -or
        $Launch.durationSeconds -lt 20 -or $Launch.executableSha256 -ne $Base.executableSha256) {
        throw 'Base repeated-launch evidence is incomplete or identifies another executable.'
    }
}
function Get-Digest([string]$Path) { (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLowerInvariant() }
function Write-Json($Value,[string]$Path) { ConvertTo-Json -InputObject $Value -Depth 32 | Set-Content -LiteralPath $Path -Encoding utf8 }
function Read-Assets {
    $Values = @{}
    foreach ($Relative in @('assets/live2d/Maple','web/dist')) {
        $Directory = Join-Path $BundleRoot $Relative
        if (-not (Test-Path -LiteralPath $Directory -PathType Container)) { throw "Missing runtime directory: $Relative" }
        foreach ($File in Get-ChildItem -LiteralPath $Directory -Recurse -File) {
            $Name = [IO.Path]::GetRelativePath($BundleRoot,$File.FullName).Replace('\','/')
            $Values[$Name] = Get-Digest $File.FullName
        }
    }
    if (-not $Values.Count) { throw 'The model/frontend inventory is empty.' }
    return $Values
}
function Read-Package {
    $Values = @{}
    foreach ($File in Get-ChildItem -LiteralPath $BundleRoot -Recurse -File) {
        if ($File.Attributes -band [IO.FileAttributes]::ReparsePoint) {
            throw "The frozen distribution contains a linked file: $($File.FullName)"
        }
        $Name = [IO.Path]::GetRelativePath($BundleRoot, $File.FullName).Replace('\', '/')
        $Values[$Name] = Get-Digest $File.FullName
    }
    return $Values
}
New-Item -ItemType Directory -Force -Path $ReportRoot | Out-Null
$Started = Get-Date
$Owned = @{}
$Detections = [Collections.Generic.List[object]]::new()
$Seen = @{}
$ProtectionSamples = [Collections.Generic.List[object]]::new()
$Failure = $null
$Process = $null
$ProcessStarted = $false
$ExecutableBefore = $null; $ExecutableAfter = $null
$AssetsBefore = @{}; $AssetsAfter = @{}
$PackageBefore = @{}; $PackageAfter = @{}
$PackageUnchanged = $false
$ProtectionBefore = $null; $ProtectionAfter = $null
$BaseHash = Get-Digest $BaseReport
$Profile = Join-Path $ReportRoot 'profile'
$ReportPath = Join-Path $ReportRoot 'desktop-report.json'
$Orphans = @(); $NativeLogs = @(); $ApplicationEvents = @()
$Execution = [ordered]@{pid=$null; startedAt=$null; finishedAt=$null; exitCode=$null;
    requestedDurationSeconds=$DurationSeconds; scenario='startup'; ownedOrphansTerminated=0}

function Read-Protection {
    $State = Get-MpComputerStatus -ErrorAction Stop
    $Value = [ordered]@{}
    foreach ($Name in @('AntivirusEnabled','RealTimeProtectionEnabled','BehaviorMonitorEnabled','OnAccessProtectionEnabled')) {
        $Value[$Name] = $State.$Name
        if ($State.$Name -ne $true) { throw "Default protection is unavailable: $Name. No bypass or retry is permitted." }
    }
    $Value.AMRunningMode = $State.AMRunningMode
    return $Value
}
function Read-Events([string]$Log,[int[]]$Ids,[datetime]$Since) {
    $Problems = @()
    $Entries = @(Get-WinEvent -FilterHashtable @{LogName=$Log; Id=$Ids; StartTime=$Since} -ErrorAction SilentlyContinue -ErrorVariable Problems)
    foreach ($Problem in $Problems) { if ($Problem.FullyQualifiedErrorId -notlike 'NoMatchingEventsFound*') { throw $Problem } }
    foreach ($Entry in $Entries) {
        $Raw = $Entry.ToXml()
        $Matched = $Raw -match [regex]::Escape($BundleRoot + '\')
        $MatchBasis = if ($Matched) { 'bundle-path' } else { $null }
        foreach ($Record in $Owned.Values) {
            if ($Entry.TimeCreated.ToUniversalTime() -lt [datetime]::Parse($Record.createdAt).ToUniversalTime()) { continue }
            if ($Raw -match ('process:_pid:' + $Record.id + '[,;]')) { $Matched=$true; if (-not $MatchBasis) { $MatchBasis='owned-pid' } }
        }
        [xml]$Xml = $Raw
        $Data = @{}; $Index=0
        foreach ($Field in $Xml.Event.EventData.Data) {
            $Name = [string]$Field.Name
            if (-not $Name) { $Name='Data'+$Index }
            $Data[$Name]=[string]$Field.'#text'; $Index++
            if ($Name -match '^(ProcessId|AppProcessId|FaultingProcessId)$') {
                try {
                    $Identifier = if ($Data[$Name] -match '^0x') { [Convert]::ToInt32($Data[$Name].Substring(2),16) } else { [int]$Data[$Name] }
                    foreach ($Record in $Owned.Values) {
                        if ($Record.id -eq $Identifier -and $Entry.TimeCreated.ToUniversalTime() -ge [datetime]::Parse($Record.createdAt).ToUniversalTime()) {
                            $Matched=$true; if (-not $MatchBasis) { $MatchBasis='owned-pid' }
                        }
                    }
                } catch { }
            }
        }
        # WER 1001 can provide only P1=app.exe. Preserve this bounded candidate
        # for review without claiming it identifies this exact path or PID.
        # Defender matching deliberately does not use this name-only fallback.
        if (-not $Matched -and $Log -eq 'Application' -and
            $Raw -match [regex]::Escape([IO.Path]::GetFileName($Executable))) {
            $Matched=$true; $MatchBasis='application-name-only-candidate'
        }
        if ($Matched) {
            [pscustomobject]@{time=$Entry.TimeCreated.ToUniversalTime().ToString('o'); id=$Entry.Id; recordId=$Entry.RecordId;
                provider=$Entry.ProviderName; data=$Data; path=$Data['Path']; process=$Data['Process Name']; threat=$Data['Threat Name'];
                matchBasis=$MatchBasis; associationRequiresReview=($MatchBasis -eq 'application-name-only-candidate')}
        }
    }
}
function Assert-ProtectionEvents([datetime]$Since) {
    $Found = @(Read-Events 'Microsoft-Windows-Windows Defender/Operational' @(1116,1117,1118,1119,1121,1122) $Since)
    foreach ($Entry in $Found) {
        if (-not $Seen.ContainsKey([string]$Entry.recordId)) { $Seen[[string]$Entry.recordId]=$true; $Detections.Add($Entry) }
    }
    if ($Found.Count) { throw 'Protection detected this bundle or an owned process. Observation stopped; do not restore or retry it.' }
}
function Track-Children([int]$ParentIdentifier,[hashtable]$Visited) {
    if ($Visited.ContainsKey($ParentIdentifier)) { return }
    $Visited[$ParentIdentifier]=$true
    foreach ($Child in @(Get-CimInstance Win32_Process -Filter ('ParentProcessId = '+$ParentIdentifier) -ErrorAction Stop)) {
        $Created=$Child.CreationDate.ToUniversalTime().ToString('o')
        $Key=[string]$Child.ProcessId+'|'+$Created
        $Owned[$Key]=[pscustomobject]@{id=[int]$Child.ProcessId; parentId=$ParentIdentifier; createdAt=$Created; path=$Child.ExecutablePath}
        Track-Children ([int]$Child.ProcessId) $Visited
    }
}
function Stop-OwnedChildren {
    $Stopped=@()
    foreach ($Record in $Owned.Values) {
        if ($Record.id -eq $Execution.pid) { continue }
        $Child=Get-Process -Id $Record.id -ErrorAction SilentlyContinue
        if ($null -eq $Child) { continue }
        $Expected=[datetime]::Parse($Record.createdAt).ToUniversalTime()
        if ([math]::Abs(($Child.StartTime.ToUniversalTime()-$Expected).TotalMilliseconds) -gt 2) { continue }
        if ($Child.WaitForExit(2000)) { continue }
        # The retained Process object and create time identify this launch's
        # actual descendant; a later unrelated reuse of its PID is skipped.
        $Child.Kill(); $null=$Child.WaitForExit(2000); $Stopped+=$Record
    }
    return $Stopped
}
try {
    $ProtectionBefore=Read-Protection
    Assert-ProtectionEvents (Get-Date).AddDays(-14)
    $ExecutableBefore=Get-Digest $Executable
    if ($ExecutableBefore -ne $Base.executableSha256) { throw 'Executable hash differs from the completed base run.' }
    $AssetsBefore=Read-Assets
    $PackageBefore=Read-Package
    if ($Base.packageSha256) {
        if ($Base.packageUnchanged -ne $true -or $Base.packageSha256.Count -ne $PackageBefore.Count) {
            throw 'The full distribution inventory differs from the completed base run.'
        }
        foreach ($Name in $Base.packageSha256.Keys) {
            if ($PackageBefore[$Name] -ne $Base.packageSha256[$Name]) { throw "Base distribution file differs: $Name" }
        }
    }
    foreach ($Name in $Base.assetSha256.Keys) {
        if ($AssetsBefore[$Name] -ne $Base.assetSha256[$Name]) { throw "Base runtime resource differs: $Name" }
    }
    $Start=[Diagnostics.ProcessStartInfo]::new()
    $Start.FileName=$Executable; $Start.WorkingDirectory=$BundleRoot
    $Start.UseShellExecute=$false; $Start.CreateNoWindow=$true
    $Start.WindowStyle=[Diagnostics.ProcessWindowStyle]::Hidden
    $Start.RedirectStandardOutput=$true; $Start.RedirectStandardError=$true
    $Start.Environment['MEINIFENG_PROFILE_DIRECTORY']=$Profile
    foreach ($Argument in @('--verify-desktop','--report',$ReportPath,'--profile',$Profile,'--duration',[string]$DurationSeconds,'--scenario','startup')) {
        $Start.ArgumentList.Add($Argument)
    }
    $Process=[Diagnostics.Process]::new(); $Process.StartInfo=$Start
    if (-not $Process.Start()) { throw 'Unable to start the ordinary supplementary observation.' }
    $ProcessStarted=$true
    $Execution.pid=$Process.Id; $Execution.startedAt=$Process.StartTime.ToUniversalTime().ToString('o')
    $Owned[[string]$Process.Id+'|'+$Execution.startedAt]=[pscustomobject]@{id=$Process.Id; parentId=$PID; createdAt=$Execution.startedAt; path=$Executable}
    $Stdout=$Process.StandardOutput.ReadToEndAsync(); $Stderr=$Process.StandardError.ReadToEndAsync()
    $Deadline=(Get-Date).AddSeconds($DurationSeconds+120)
    $PollAfter=Get-Date; $StateAfter=Get-Date; $ProgressAfter=(Get-Date).AddSeconds(55)
    while (-not $Process.WaitForExit(1000)) {
        if ((Get-Date) -ge $PollAfter) { Track-Children $Process.Id @{}; Assert-ProtectionEvents $Started; $PollAfter=(Get-Date).AddSeconds(3) }
        if ((Get-Date) -ge $StateAfter) {
            $Sample=Read-Protection
            $ProtectionSamples.Add(@{at=(Get-Date).ToUniversalTime().ToString('o'); state=$Sample})
            foreach ($Name in $ProtectionBefore.Keys) { if ($Sample[$Name] -ne $ProtectionBefore[$Name]) { throw 'Protection state changed during observation.' } }
            $StateAfter=(Get-Date).AddSeconds(15)
        }
        if ((Get-Date) -ge $ProgressAfter) { Write-Host 'Supplementary ordinary desktop observation is running; real interactions remain user driven.'; $ProgressAfter=(Get-Date).AddSeconds(55) }
        if ((Get-Date) -ge $Deadline) { throw 'Observation exceeded its duration and bounded shutdown allowance.' }
    }
} catch { $Failure=$_.Exception.Message }
finally {
    if ($ProcessStarted) {
        try {
            if (-not $Process.HasExited) { $Process.Kill($true); $null=$Process.WaitForExit(5000) }
            $Orphans=@(Stop-OwnedChildren)
            $Execution.exitCode=$(if ($Process.HasExited) { $Process.ExitCode } else { $null })
        } catch { if (-not $Failure) { $Failure='Owned shutdown failed: '+$_.Exception.Message } }
        $Execution.finishedAt=(Get-Date).ToUniversalTime().ToString('o')
        $Execution.ownedOrphansTerminated=$Orphans.Count
        if ($Stdout -and $Stdout.Wait(2000)) { $Stdout.Result | Set-Content -LiteralPath (Join-Path $ReportRoot 'stdout.txt') -Encoding utf8 }
        if ($Stderr -and $Stderr.Wait(2000)) { $Stderr.Result | Set-Content -LiteralPath (Join-Path $ReportRoot 'stderr.txt') -Encoding utf8 }
    }
    if ($Process) { $Process.Dispose() }
    try { Assert-ProtectionEvents $Started } catch { if (-not $Failure) { $Failure=$_.Exception.Message } }
    try { $ProtectionAfter=Read-Protection } catch { if (-not $Failure) { $Failure=$_.Exception.Message } }
    # After a detection, keep event evidence but never reopen the flagged bundle.
    if (-not $Detections.Count) {
        try { $ExecutableAfter=Get-Digest $Executable; $AssetsAfter=Read-Assets; $PackageAfter=Read-Package } catch { if (-not $Failure) { $Failure=$_.Exception.Message } }
    }
    try { $ApplicationEvents=@(Read-Events 'Application' @(1000,1001,1002,1026) $Started) } catch { if (-not $Failure) { $Failure=$_.Exception.Message } }
}
$Ended=Get-Date
$ProtectionUnchanged=$null -ne $ProtectionBefore -and $null -ne $ProtectionAfter
if ($ProtectionUnchanged) { foreach ($Name in $ProtectionBefore.Keys) { if ($ProtectionBefore[$Name] -ne $ProtectionAfter[$Name]) { $ProtectionUnchanged=$false } } }
if (-not $ProtectionUnchanged -and -not $Failure) { $Failure='Default protection state was not preserved.' }
if ($ExecutableBefore -ne $ExecutableAfter -or $AssetsBefore.Count -ne $AssetsAfter.Count) { if (-not $Failure) { $Failure='Executable or runtime inventory changed.' } }
foreach ($Name in $AssetsBefore.Keys) { if ($AssetsBefore[$Name] -ne $AssetsAfter[$Name] -and -not $Failure) { $Failure="Runtime changed: $Name" } }
$PackageUnchanged = $PackageBefore.Count -gt 0 -and $PackageBefore.Count -eq $PackageAfter.Count
foreach ($Name in $PackageBefore.Keys) { if ($PackageBefore[$Name] -ne $PackageAfter[$Name]) { $PackageUnchanged=$false } }
if (-not $PackageUnchanged -and -not $Failure) { $Failure='The complete distribution changed during observation.' }
if ((Get-Digest $BaseReport) -ne $BaseHash -and -not $Failure) { $Failure='The immutable base report changed during supplementary observation.' }
if ($Orphans.Count -and -not $Failure) { $Failure='Owned child cleanup was required; this is not a clean run.' }
if (Test-Path -LiteralPath $ReportPath) {
    $RawReport=Get-Content -LiteralPath $ReportPath -Raw | ConvertFrom-Json -AsHashtable
    if ($Execution.exitCode -ne 0 -or $RawReport.passed -ne $true -or $RawReport.completed -ne $true -or
        $RawReport.pid -ne $Execution.pid -or $RawReport.exitCode -ne 0 -or $RawReport.runtimeActive -ne $true -or
        $RawReport.requestedDurationSeconds -ne $DurationSeconds -or $RawReport.durationSeconds -lt $DurationSeconds -or
        $RawReport.checks.cleanExit -ne $true -or $RawReport.errors.Count -ne 0 -or $RawReport.graphicsOverrides.Count -ne 0 -or
        $RawReport.offscreen -ne $false -or -not $RawReport.assetSha256 -or
        -not [string]::Equals([IO.Path]::GetFullPath([string]$RawReport.profile),$Profile,[StringComparison]::OrdinalIgnoreCase) -or
        $RawReport.nativeProvidersActive -ne $true -or $RawReport.nativeProvidersObserved -ne $true -or
        $RawReport.ordinaryPetWindow -ne $true -or $RawReport.defaultGraphicsBackend -ne $true -or
        $RawReport.qtPlatform -ne 'windows' -or $RawReport.scenario -ne 'startup' -or
        $RawReport.executableSha256 -ne $ExecutableBefore -or $RawReport.audioChildExited -ne $true) {
        if (-not $Failure) { $Failure='Supplementary application run did not meet ordinary native/provider/exit requirements.' }
    }
    foreach ($Name in $RawReport.assetSha256.Keys) { if ($RawReport.assetSha256[$Name] -ne $AssetsBefore[$Name] -and -not $Failure) { $Failure="App loaded a different runtime resource: $Name" } }
} elseif (-not $Failure) { $Failure='No application report was produced.' }
$LogRoot=Join-Path $Profile 'logs'
if (Test-Path -LiteralPath $LogRoot) {
    foreach ($File in Get-ChildItem -LiteralPath $LogRoot -Filter '*.fault.log' -File) {
        if ($File.Length -eq 0) { continue }
        $Content=Get-Content -LiteralPath $File.FullName -Raw
        $NativeLogs+=@{run='supplement'; path=[IO.Path]::GetRelativePath($ReportRoot,$File.FullName).Replace('\','/'); bytes=$File.Length; sha256=(Get-Digest $File.FullName);
            exceptionCodes=@([regex]::Matches($Content,'code (0x[0-9a-fA-F]+)') | ForEach-Object { $_.Groups[1].Value.ToLowerInvariant() } | Select-Object -Unique);
            observation='Native exception text requires independent review of code, exit, WER, and subsequent activity; no automatic approval.'}
    }
}
Write-Json @($Owned.Values) (Join-Path $ReportRoot 'owned-processes.json')
Write-Json $Orphans (Join-Path $ReportRoot 'owned-orphans-terminated.json')
Write-Json @{pid=$Execution.pid; exitCode=$Execution.exitCode; ownedOrphansTerminated=$Orphans.Count; reportProduced=(Test-Path -LiteralPath $ReportPath)} (Join-Path $ReportRoot 'process-summary.json')
Write-Json @{nativeExceptionObserved=$NativeLogs.Count -gt 0; logs=$NativeLogs; reviewStatus=$(if ($NativeLogs.Count) {'pending'} else {'not-observed'})} (Join-Path $ReportRoot 'native-fault-review.json')
$Security=@{status=$(if ($Detections.Count) {'detected'} elseif ($Failure -or -not $ProtectionUnchanged) {'incomplete'} else {'no-detections-observed'});
    protectionUnchanged=$ProtectionUnchanged; protectionBefore=$ProtectionBefore; protectionAfter=$ProtectionAfter;
    observationStart=$Started.ToUniversalTime().ToString('o'); observationEnd=$Ended.ToUniversalTime().ToString('o'); checkedAt=(Get-Date).ToUniversalTime().ToString('o'); detections=$Detections.ToArray()}
Write-Json $ProtectionSamples.ToArray() (Join-Path $ReportRoot 'protection-samples.json')
$Files=@{}
foreach ($File in Get-ChildItem -LiteralPath $ReportRoot -Recurse -File) {
    $Files[[IO.Path]::GetRelativePath($ReportRoot,$File.FullName).Replace('\','/')]=Get-Digest $File.FullName
}
$Evidence=[ordered]@{schemaVersion=1; reportKind='cutemaple-desktop-supplement';
    status=$(if ($Detections.Count) {'DETECTED-DO-NOT-RUN'} elseif ($Failure) {'FAILED'} else {'RECORDED-NOT-RELEASE'}); errors=@();
    baseReport=@{path=$BaseReport; sha256=$BaseHash}; executable=@{path=$Executable; sha256Before=$ExecutableBefore; sha256After=$ExecutableAfter};
    assetSha256Before=$AssetsBefore; assetSha256After=$AssetsAfter; reportRoot=$ReportRoot; execution=$Execution;
    packageSha256Before=$PackageBefore; packageSha256After=$PackageAfter; packageUnchanged=$PackageUnchanged;
    profileIsolated=$true; securityObservation=$Security; files=$Files; nativeExceptionObserved=$NativeLogs.Count -gt 0; nativeExceptionLogs=$NativeLogs;
    applicationEvents=@{observationStart=$Started.ToUniversalTime().ToString('o'); observationEnd=$Ended.ToUniversalTime().ToString('o');
        checkedAt=(Get-Date).ToUniversalTime().ToString('o'); scopeExecutablePath=$Executable;
        processIds=@($Owned.Values | ForEach-Object {$_.id} | Select-Object -Unique); matchingEvents=$ApplicationEvents}}
if ($Failure) { $Evidence.errors=@($Failure) }
Write-Json $Evidence (Join-Path $ReportRoot 'SUPPLEMENT-EVIDENCE.json')
Write-Host ('Supplementary observation: '+$Evidence.status+'; '+(Join-Path $ReportRoot 'SUPPLEMENT-EVIDENCE.json'))
if ($Failure) { throw $Failure }
