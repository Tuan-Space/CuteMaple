param(
    [ValidateSet("Directory")]
    [string]$Mode = "Directory",
    [switch]$InstallDependencies,
    [switch]$SkipTests,
    [string]$CandidateLabel = "",
    [ValidateRange(1, 32)]
    [int]$Jobs = 4
)
$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$AppVersion = (Get-Content -LiteralPath (Join-Path $ProjectRoot 'VERSION') -Raw).Trim()
$BuildRoot = Join-Path $ProjectRoot ".build"
$BuildId = Get-Date -Format "yyyyMMdd-HHmmss"
$Stage = Join-Path $BuildRoot ("stage-" + $BuildId)
$Python = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
$env:NUITKA_CACHE_DIR = Join-Path $BuildRoot "nuitka-cache"
$env:NUITKA_CACHE_DIR_DOWNLOADS = Join-Path $BuildRoot "downloads"
New-Item -ItemType Directory -Force -Path $Stage, $env:NUITKA_CACHE_DIR, $env:NUITKA_CACHE_DIR_DOWNLOADS | Out-Null
if (-not (Test-Path -LiteralPath $Python)) {
    & python -m venv (Join-Path $ProjectRoot ".venv")
    if ($LASTEXITCODE -ne 0) { throw "Cannot create project virtual environment." }
    $InstallDependencies = $true
}
if ($InstallDependencies) {
    & $Python -m pip install -r (Join-Path $ProjectRoot "requirements-build.txt")
    if ($LASTEXITCODE -ne 0) { throw "Build dependency installation failed." }
}
$ValidationArguments = @((Join-Path $ProjectRoot "tools\validate_release.py"), $ProjectRoot)
& $Python @ValidationArguments
if ($LASTEXITCODE -ne 0) {
    throw "Runtime inventory incomplete. Export Maple's model and all required motions first."
}
if (-not $SkipTests) {
    $TestEvidence = Join-Path $Stage "regression"
    & $Python (Join-Path $ProjectRoot "tools\run_regression.py") --project-root $ProjectRoot --output-dir $TestEvidence
    if ($LASTEXITCODE -ne 0) { throw "Tests failed; no package produced." }
}
$RuntimeModules = @("main.py", "pet_app.py", "pet_core.py", "resource_monitor.py", "memory_cleaner.py", "cleanup_check.py",
                    "monitor_ui.py", "interaction_ui.py", "locomotion.py", "live2d_host.py", "desktop_activity.py", "audio_probe.py", "audio_process.py", "pet_reactions.py", "runtime_check.py", "desktop_check.py", "diagnostics.py", "cleanup_protocol.py", "cleanup_process.py", "cleanup_session.py",
                    "journal_recurrence.py", "journal_store.py", "journal_library.py", "journal_reminders.py", "journal_media.py", "journal_editors.py", "journal_ui.py", "journal_design.py", "journal_service.py", "journal_install.py", "journal_check.py")
foreach ($Module in $RuntimeModules) {
    Copy-Item -LiteralPath (Join-Path $ProjectRoot $Module) -Destination $Stage
}
Copy-Item -LiteralPath (Join-Path $ProjectRoot 'VERSION') -Destination $Stage
$StageAssets = Join-Path $Stage "assets"
New-Item -ItemType Directory -Force -Path $StageAssets | Out-Null
foreach ($Folder in @("fonts")) {
    Copy-Item -LiteralPath (Join-Path $ProjectRoot ("assets\" + $Folder)) -Destination $StageAssets -Recurse
}
Copy-Item -LiteralPath (Join-Path $ProjectRoot "assets\icon.png") -Destination $StageAssets
$Icon = Get-ChildItem -LiteralPath (Join-Path $ProjectRoot "assets") -Filter "*.ico" -File | Select-Object -First 1
if (-not $Icon) { throw "Missing application icon." }
Copy-Item -LiteralPath $Icon.FullName -Destination (Join-Path $StageAssets "app.ico")
$ModelSource = Join-Path $ProjectRoot "assets\live2d\Maple"
$ModelTarget = Join-Path $StageAssets "live2d\Maple"
New-Item -ItemType Directory -Force -Path $ModelTarget | Out-Null
if (Test-Path -LiteralPath $ModelSource) {
    $ModelRoot = (Resolve-Path -LiteralPath $ModelSource).Path
    foreach ($File in Get-ChildItem -LiteralPath $ModelSource -Recurse -File) {
        if ($File.Extension.ToLowerInvariant() -notin @(".json", ".moc3", ".png", ".jpg", ".jpeg", ".wav", ".mp3")) { continue }
        $Relative = [System.IO.Path]::GetRelativePath($ModelRoot, $File.FullName)
        $Destination = Join-Path $ModelTarget $Relative
        New-Item -ItemType Directory -Force -Path (Split-Path -Parent $Destination) | Out-Null
        Copy-Item -LiteralPath $File.FullName -Destination $Destination
    }
}
$StageWeb = Join-Path $Stage "web"
New-Item -ItemType Directory -Force -Path $StageWeb | Out-Null
Copy-Item -LiteralPath (Join-Path $ProjectRoot "web\dist") -Destination $StageWeb -Recurse
$AppBaseName = "CuteMaple-Live2D"
$Arguments = @(
    "-m", "nuitka", "--mode=standalone", "--enable-plugin=pyside6",
    "--include-module=PySide6.QtWebEngineCore", "--include-module=PySide6.QtWebEngineWidgets",
    "--include-module=PySide6.QtWebChannel", "--include-module=PySide6.QtNetwork",
    "--include-module=PySide6.QtMultimedia", "--include-qt-plugins=multimedia", "--include-package=lunar_python", "--include-package=tzdata", "--include-package-data=tzdata",
    "--windows-console-mode=disable", "--windows-icon-from-ico=assets\app.ico",
    "--include-data-dir=assets/fonts=assets/fonts",
    "--include-data-dir=assets/live2d=assets/live2d",
    "--include-data-files=assets/icon.png=assets/icon.png",
    "--include-data-dir=web/dist=web/dist",
    "--include-data-files=VERSION=VERSION",
    "--output-dir=out", "--output-filename=$AppBaseName.exe",
    "--file-version=$AppVersion", "--product-version=$AppVersion", "--report=compilation-report.xml", "--assume-yes-for-downloads", "--jobs=$Jobs"
)
$Arguments += "main.py"
$SourceHashes = [ordered]@{}
foreach ($Module in $RuntimeModules) {
    $SourceHashes[$Module] = (Get-FileHash -LiteralPath (Join-Path $Stage $Module) -Algorithm SHA256).Hash
}
$ResourceHashes = [ordered]@{}
foreach ($Folder in @($StageAssets, (Join-Path $StageWeb "dist"))) {
    foreach ($File in Get-ChildItem -LiteralPath $Folder -Recurse -File) {
        $Relative = [System.IO.Path]::GetRelativePath($Stage, $File.FullName)
        $ResourceHashes[$Relative] = (Get-FileHash -LiteralPath $File.FullName -Algorithm SHA256).Hash
    }
}
$Snapshot = [ordered]@{
    frozenAt = (Get-Date).ToUniversalTime().ToString("o"); candidateLabel = $CandidateLabel
    sourceModulesSha256 = $SourceHashes; runtimeResourcesSha256 = $ResourceHashes
    python = $Python; mainCompilerArguments = $Arguments
    cleanupImplementation = "cpp-msvc"
    cleanupBuildScript = "tools/build_native_cleaner.ps1"
}
$Snapshot | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath (Join-Path $Stage "SOURCE-SNAPSHOT.json") -Encoding utf8
Push-Location $Stage
try {
    & $Python @Arguments
    if ($LASTEXITCODE -ne 0) { throw "Nuitka build failed ($LASTEXITCODE)." }
} finally {
    Pop-Location
}
$Standalone = Join-Path $Stage "out\main.dist"
if (-not (Test-Path -LiteralPath $Standalone)) {
    $Standalone = (Get-ChildItem -LiteralPath (Join-Path $Stage "out") -Directory -Filter "*.dist" | Select-Object -First 1).FullName
}
if (-not $Standalone) { throw "Nuitka standalone dependency directory is missing." }
$NativeOutput = Join-Path $Stage 'native-cleaner'
& (Join-Path $ProjectRoot 'tools/build_native_cleaner.ps1') -OutputDirectory $NativeOutput
$Helper = Join-Path $NativeOutput 'CuteMaple-Cleaner.exe'
if (-not (Test-Path -LiteralPath $Helper)) { throw 'Native cleaner missing.' }
$HelperTarget = Join-Path $Standalone 'cleaner'
New-Item -ItemType Directory -Force -Path $HelperTarget | Out-Null
Copy-Item -LiteralPath $Helper -Destination $HelperTarget
Copy-Item -LiteralPath (Join-Path $NativeOutput 'native-build.json') -Destination $HelperTarget
$Licenses = Join-Path $Standalone 'licenses'
New-Item -ItemType Directory -Force -Path $Licenses | Out-Null
foreach ($Dependency in @('lunar-python-1.4.8','tzdata-2025.2','nlohmann-json-3.11.3')) {
    $LicenseTarget = Join-Path $Licenses $Dependency
    New-Item -ItemType Directory -Force -Path $LicenseTarget | Out-Null
    Get-ChildItem -LiteralPath (Join-Path $ProjectRoot ('third_party\'+$Dependency)) -File -Filter 'LICENSE*' | Copy-Item -Destination $LicenseTarget
}
$PackagedValidation = @((Join-Path $ProjectRoot "tools\validate_release.py"), $Standalone, "--packaged")
& $Python @PackagedValidation
if ($LASTEXITCODE -ne 0) { throw "Packaged QtWebEngine/runtime inventory failed." }
$Output = Join-Path $ProjectRoot ("dist\" + $AppBaseName + "-" + $Mode + "-" + $BuildId)
New-Item -ItemType Directory -Force -Path $Output | Out-Null
Copy-Item -LiteralPath $Standalone -Destination (Join-Path $Output $AppBaseName) -Recurse
$Executable = Join-Path $Output ($AppBaseName + "\" + $AppBaseName + ".exe")
if (-not (Test-Path -LiteralPath $Executable)) { throw "Expected application executable is missing." }
Copy-Item -LiteralPath (Join-Path $Stage "compilation-report.xml") -Destination $Output
Copy-Item -LiteralPath (Join-Path $NativeOutput "native-build.json") -Destination $Output
Copy-Item -LiteralPath (Join-Path $ProjectRoot 'third_party/nlohmann-json-3.11.3/LICENSE.MIT') -Destination (Join-Path $Output 'nlohmann-json-LICENSE.txt')
Copy-Item -LiteralPath (Join-Path $Stage "SOURCE-SNAPSHOT.json") -Destination $Output
$SourceRevision = "source-archive"
$SourceDirty = $false
if (Test-Path -LiteralPath (Join-Path $ProjectRoot ".git")) {
    $SourceRevision = (& git -C $ProjectRoot rev-parse HEAD)
    $SourceDirty = [bool](& git -C $ProjectRoot status --porcelain)
}
$Manifest = [ordered]@{
    product = $AppBaseName; mode = $Mode; version = (Get-Content -LiteralPath (Join-Path $ProjectRoot "VERSION") -Raw).Trim()
    builtAt = (Get-Date).ToString("o"); sourceRevision = $SourceRevision
    sourceHasUncommittedChanges = $SourceDirty
    buildStage = $Stage
    releaseStatus = "UNVERIFIED"
    candidateLabel = $CandidateLabel
    normalDesktopRunVerified = $false
    securityReviewStatus = "pending"
    privilegedCleanup = "separate cleaner/CuteMaple-Cleaner.exe; explicit user authorization only"
    modelValidation = "runtime inventory passed; visual QA remains required"
}
$Manifest | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $Output "BUILD-STATUS.json") -Encoding utf8
if ($CandidateLabel) {
    ("CANDIDATE ONLY - NOT A FINAL RELEASE`n" + $CandidateLabel + "`nNo ordinary desktop soak or default-protection release verdict is implied.") |
        Set-Content -LiteralPath (Join-Path $Output "CANDIDATE-NOT-FINAL.txt") -Encoding utf8
}
$Hashes = foreach ($File in Get-ChildItem -LiteralPath $Output -Recurse -File) {
    if ($File.Name -eq "SHA256.txt") { continue }
    $Hash = (Get-FileHash -Algorithm SHA256 -LiteralPath $File.FullName).Hash
    $Relative = [System.IO.Path]::GetRelativePath($Output, $File.FullName)
    "$Hash  $Relative"
}
$Hashes | Set-Content -LiteralPath (Join-Path $Output "SHA256.txt") -Encoding utf8
Write-Output ("Package: " + $Output)
Write-Output ("Executable: " + $Executable)
