param([string]$OutputDirectory = '', [switch]$TestBuild)
$ErrorActionPreference = 'Stop'
$ProjectRoot = Split-Path -Parent $PSScriptRoot
if (-not $OutputDirectory) { $OutputDirectory = Join-Path $ProjectRoot 'artifacts\native-cleaner' }
$OutputDirectory = [IO.Path]::GetFullPath($OutputDirectory)
New-Item -ItemType Directory -Force -Path $OutputDirectory | Out-Null
$AppVersion = (Get-Content -LiteralPath (Join-Path $ProjectRoot 'VERSION') -Raw).Trim()
$VersionQuad = $AppVersion.Replace('.', ',') + ',0'
@("#define MAPLE_VERSION `"$AppVersion`"", "#define MAPLE_VERSION_FILE `"$AppVersion.0`"", "#define MAPLE_VERSION_QUAD $VersionQuad") | Set-Content -LiteralPath (Join-Path $OutputDirectory 'maple_version.h') -Encoding ascii
$VsWhere = Join-Path ${env:ProgramFiles(x86)} 'Microsoft Visual Studio\Installer\vswhere.exe'
$Installation = & $VsWhere -latest -products '*' -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -property installationPath
if (-not $Installation) { throw 'MSVC x64 Build Tools and Windows SDK are required.' }
$Setup = Join-Path $Installation 'VC\Auxiliary\Build\vcvars64.bat'
$EnvironmentLines = & $env:ComSpec /d /s /c "`"`"$Setup`" >nul && set`""
if ($LASTEXITCODE -ne 0) { throw 'Cannot initialize MSVC.' }
$Saved = @{}
foreach ($Name in @('PATH','INCLUDE','LIB','LIBPATH')) {
    $Saved[$Name] = [Environment]::GetEnvironmentVariable($Name,'Process')
    $Line = $EnvironmentLines | Where-Object { $_.StartsWith($Name+'=',[StringComparison]::OrdinalIgnoreCase) } | Select-Object -First 1
    if (-not $Line) { throw "MSVC variable missing: $Name" }
    [Environment]::SetEnvironmentVariable($Name,$Line.Substring($Name.Length+1),'Process')
}
try {
    & rc.exe /nologo ('/I'+$OutputDirectory) ('/fo'+(Join-Path $OutputDirectory 'version.res')) (Join-Path $ProjectRoot 'native\cleaner\version.rc')
    if ($LASTEXITCODE -ne 0) { throw 'Native version resource failed.' }
    $Name = if ($TestBuild) { 'CuteMaple-Cleaner-Test.exe' } else { 'CuteMaple-Cleaner.exe' }
    $Arguments = @('/nologo','/std:c++17','/O2','/EHsc','/W4','/MT','/utf-8','/DUNICODE','/D_UNICODE','/D_WIN32_WINNT=0x0A00','/DPSAPI_VERSION=1','/guard:cf',('/Fo'+(Join-Path $OutputDirectory 'cleaner.obj')),('/Fe'+(Join-Path $OutputDirectory $Name)))
    if ($TestBuild) { $Arguments += '/DMAPLE_CLEANER_TEST' }
    $Arguments += ('/I'+$OutputDirectory)
    $Arguments += @((Join-Path $ProjectRoot 'native\cleaner\cleaner.cpp'),'/link',(Join-Path $OutputDirectory 'version.res'),'/Brepro','/SUBSYSTEM:WINDOWS','/DYNAMICBASE','/NXCOMPAT','/HIGHENTROPYVA','/guard:cf','advapi32.lib','bcrypt.lib','psapi.lib','shell32.lib')
    & cl.exe @Arguments
    if ($LASTEXITCODE -ne 0) { throw 'Native cleaner compilation failed.' }
    $Inputs = @('VERSION','native/cleaner/cleaner.cpp','native/cleaner/platform.h','native/cleaner/version.rc','third_party/nlohmann-json-3.11.3/json.hpp','third_party/nlohmann-json-3.11.3/LICENSE.MIT','tools/build_native_cleaner.ps1')
    $Hashes = [ordered]@{}
    foreach ($InputFile in $Inputs) { $Hashes[$InputFile] = (Get-FileHash -LiteralPath (Join-Path $ProjectRoot $InputFile)).Hash.ToLower() }
    @{version=$AppVersion;implementation='cpp-msvc';testBuild=[bool]$TestBuild;executable=$Name;sha256=(Get-FileHash -LiteralPath (Join-Path $OutputDirectory $Name)).Hash.ToLower();inputs=$Hashes;compiler=(Get-Command cl.exe).Source;arguments=$Arguments} | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath (Join-Path $OutputDirectory 'native-build.json') -Encoding utf8
} finally {
    foreach ($Name in $Saved.Keys) { [Environment]::SetEnvironmentVariable($Name,$Saved[$Name],'Process') }
}
