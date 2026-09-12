param([ValidateRange(1, 16)][int]$Jobs = 4)
$ErrorActionPreference = 'Stop'
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$Python = Join-Path $ProjectRoot '.venv\Scripts\python.exe'
$BuildId = Get-Date -Format 'yyyyMMdd-HHmmss'
$Stage = Join-Path $ProjectRoot ('.build\cleaner-v5-' + $BuildId)
New-Item -ItemType Directory -Path $Stage -Force | Out-Null
$Modules = @('cleanup_helper.py', 'cleanup_protocol.py', 'cleanup_process.py',
             'memory_cleaner.py', 'pet_core.py', 'resource_monitor.py', 'diagnostics.py')
$Hashes = [ordered]@{}
foreach ($Module in $Modules) {
    Copy-Item -LiteralPath (Join-Path $ProjectRoot $Module) -Destination $Stage
    $Hashes[$Module] = (Get-FileHash -LiteralPath (Join-Path $Stage $Module) -Algorithm SHA256).Hash
}
$Snapshot = [ordered]@{createdAt=(Get-Date).ToUniversalTime().ToString('o');
    revision=(& git -C $ProjectRoot rev-parse HEAD); sources=$Hashes;
    purpose='Isolated compiled v5 cleaner acceptance; no modification of v4 distribution';
    releaseStatus='UNVERIFIED'}
$Snapshot | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath (Join-Path $Stage 'SOURCE-SNAPSHOT.json') -Encoding utf8
$env:NUITKA_CACHE_DIR = Join-Path $ProjectRoot '.build\nuitka-cache'
$env:NUITKA_CACHE_DIR_DOWNLOADS = Join-Path $ProjectRoot '.build\downloads'
Push-Location $Stage
try {
    & $Python -m nuitka --mode=standalone --nofollow-import-to=PySide6 `
        --windows-console-mode=disable --output-dir=out --output-filename=CuteMaple-Cleaner.exe `
        --report=compilation-report.xml --assume-yes-for-downloads "--jobs=$Jobs" cleanup_helper.py
    if ($LASTEXITCODE -ne 0) {throw 'V5 cleaner compilation failed'}
} finally {Pop-Location}
$Executable = Join-Path $Stage 'out\cleanup_helper.dist\CuteMaple-Cleaner.exe'
if (-not (Test-Path -LiteralPath $Executable)) {throw 'Compiled helper is missing'}
[pscustomobject]@{executable=$Executable; snapshot=(Join-Path $Stage 'SOURCE-SNAPSHOT.json');
    sha256=(Get-FileHash -LiteralPath $Executable -Algorithm SHA256).Hash} | ConvertTo-Json
