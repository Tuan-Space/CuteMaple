param([switch]$SmokeTest, [switch]$Headless)
$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$Python = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $Python)) { throw "Create .venv and install config/requirements-build.txt first." }
$Names = @("APPDATA", "MEINIFENG_DISABLE_AUTOSTART", "MEINIFENG_DISABLE_CLEAN_TASK",
           "MEINIFENG_SMOKE_TEST", "CUTEMAPLE_DISABLE_ACTIVITY", "QT_QPA_PLATFORM")
$Before = @{}
foreach ($Name in $Names) { $Before[$Name] = [Environment]::GetEnvironmentVariable($Name, "Process") }
try {
    $env:APPDATA = Join-Path $ProjectRoot ".build\dev-profile"
    $env:MEINIFENG_DISABLE_AUTOSTART = "1"
    $env:MEINIFENG_DISABLE_CLEAN_TASK = "1"
    if ($SmokeTest) {
        $env:MEINIFENG_SMOKE_TEST = "1"
        $env:CUTEMAPLE_DISABLE_ACTIVITY = "1"
    }
    if ($Headless) { $env:QT_QPA_PLATFORM = "offscreen" }
    Push-Location $ProjectRoot
    try { & $Python main.py } finally { Pop-Location }
    if ($LASTEXITCODE -ne 0) { throw "Development launch failed ($LASTEXITCODE)." }
} finally {
    foreach ($Name in $Names) { [Environment]::SetEnvironmentVariable($Name, $Before[$Name], "Process") }
}
