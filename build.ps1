$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$BuildRoot = Join-Path ([System.IO.Path]::GetTempPath()) "meinifeng-nuitka-build"
$Stage = Join-Path $BuildRoot ("src-" + [Guid]::NewGuid().ToString("N"))
$Venv = Join-Path $BuildRoot ".venv"
$Python = Join-Path $Venv "Scripts\python.exe"
$NuitkaCache = Join-Path $BuildRoot "nuitka-cache"

New-Item -ItemType Directory -Force -Path $BuildRoot, $Stage, $NuitkaCache | Out-Null
$env:NUITKA_CACHE_DIR = $NuitkaCache
$ExistingDownloads = Join-Path $env:LOCALAPPDATA "Nuitka\Nuitka\Cache\downloads"
if (Test-Path -LiteralPath $ExistingDownloads) {
    # Reuse an already downloaded compiler while keeping writable caches isolated.
    $env:NUITKA_CACHE_DIR_DOWNLOADS = $ExistingDownloads
}
if (-not (Test-Path -LiteralPath $Python)) {
    python -m venv $Venv
}

& $Python -m pip install -r (Join-Path $ProjectRoot "requirements-build.txt")

Copy-Item -LiteralPath (Join-Path $ProjectRoot "main.py") -Destination $Stage -Force
Copy-Item -LiteralPath (Join-Path $ProjectRoot "pet_app.py") -Destination $Stage -Force
Copy-Item -LiteralPath (Join-Path $ProjectRoot "pet_core.py") -Destination $Stage -Force
Copy-Item -LiteralPath (Join-Path $ProjectRoot "resource_monitor.py") -Destination $Stage -Force
Copy-Item -LiteralPath (Join-Path $ProjectRoot "memory_cleaner.py") -Destination $Stage -Force
Copy-Item -LiteralPath (Join-Path $ProjectRoot "monitor_ui.py") -Destination $Stage -Force
$StageAssets = Join-Path $Stage "assets"
New-Item -ItemType Directory -Force -Path $StageAssets | Out-Null
Copy-Item -LiteralPath (Join-Path $ProjectRoot "assets\sprites_v2") -Destination $StageAssets -Recurse -Force
Copy-Item -LiteralPath (Join-Path $ProjectRoot "assets\fonts") -Destination $StageAssets -Recurse -Force
Copy-Item -LiteralPath (Join-Path $ProjectRoot "assets\icon.png") -Destination $StageAssets -Force
$IconSource = Get-ChildItem -LiteralPath (Join-Path $ProjectRoot "assets") -Filter "*.ico" -File | Select-Object -First 1
if (-not $IconSource) {
    throw "assets 目录中未找到 ico 图标"
}
Copy-Item -LiteralPath $IconSource.FullName -Destination (Join-Path $StageAssets "app.ico") -Force
$AppBaseName = [System.IO.Path]::GetFileNameWithoutExtension($IconSource.Name)

# Some sandboxed Windows installations expose Nuitka's downloaded MinGW cache
# through a path too long for nested SDK includes. Mirror only the headers to a
# short temp path and advertise it to GCC; on normal systems this block is inert.
$ShortInclude = Join-Path $BuildRoot "mingw-include"
if (-not (Test-Path -LiteralPath $ShortInclude)) {
    $IncludeCandidates = @(
        Get-ChildItem -Path (Join-Path $env:LOCALAPPDATA "Packages\*\LocalCache\Local\Nuitka\Nuitka\Cache\downloads\gcc\x86_64\*\mingw64\x86_64-w64-mingw32\include") -Directory -ErrorAction SilentlyContinue
        Get-ChildItem -Path (Join-Path $env:LOCALAPPDATA "Nuitka\Nuitka\Cache\downloads\gcc\x86_64\*\mingw64\x86_64-w64-mingw32\include") -Directory -ErrorAction SilentlyContinue
    )
    $SourceInclude = $IncludeCandidates | Sort-Object LastWriteTime -Descending | Select-Object -First 1
    if ($SourceInclude) {
        New-Item -ItemType Directory -Force -Path $ShortInclude | Out-Null
        Copy-Item -Path (Join-Path $SourceInclude.FullName "*") -Destination $ShortInclude -Recurse -Force
    }
}
if (Test-Path -LiteralPath $ShortInclude) {
    $env:C_INCLUDE_PATH = $ShortInclude
    $env:CPLUS_INCLUDE_PATH = $ShortInclude
}

$Dist = Join-Path $ProjectRoot "dist"
New-Item -ItemType Directory -Force -Path $Dist | Out-Null

Push-Location $Stage
try {
    & $Python -m nuitka `
        --mode=onefile `
        --onefile-no-compression `
        --enable-plugin=pyside6 `
        --windows-console-mode=disable `
        --windows-icon-from-ico="assets\app.ico" `
        --include-data-dir="assets\sprites_v2=assets\sprites_v2" `
        --include-data-dir="assets\fonts=assets\fonts" `
        --include-data-files="assets\icon.png=assets\icon.png" `
        --output-dir="dist" `
        --assume-yes-for-downloads `
        main.py
    if ($LASTEXITCODE -ne 0) {
        throw "Nuitka 构建失败，退出码 $LASTEXITCODE"
    }
}
finally {
    Pop-Location
}

$BuiltExe = Join-Path $Stage "dist\main.exe"
$Exe = Join-Path $Dist ($AppBaseName + ".exe")
if (Test-Path -LiteralPath $BuiltExe) {
    Copy-Item -LiteralPath $BuiltExe -Destination $Exe -Force
}
if (-not (Test-Path -LiteralPath $Exe)) {
    throw "构建未产生 $Exe"
}
if ((Get-Item -LiteralPath $Exe).Length -lt 5MB) {
    throw "生成文件体积异常，可能缺少 onefile 负载：$Exe"
}
Get-Item -LiteralPath $Exe | Select-Object FullName, Length, LastWriteTime
$Hash = (Get-FileHash -Algorithm SHA256 -LiteralPath $Exe).Hash
Set-Content -LiteralPath (Join-Path $Dist "SHA256.txt") -Value ($Hash + "  " + $AppBaseName + ".exe") -Encoding utf8
Get-FileHash -Algorithm SHA256 -LiteralPath $Exe
