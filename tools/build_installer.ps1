param([Parameter(Mandatory=$true)][string]$PackageDirectory,[string]$Compiler='',[string]$OutputDirectory='')
$ErrorActionPreference='Stop'
$Root=Split-Path -Parent $PSScriptRoot
$Version=(Get-Content -LiteralPath (Join-Path $Root 'VERSION') -Raw).Trim()
$PackageDirectory=(Resolve-Path -LiteralPath $PackageDirectory).Path
if (-not (Test-Path -LiteralPath (Join-Path $PackageDirectory 'CuteMaple-Live2D.exe'))) { throw 'Expected application package directory.' }
if (-not $Compiler) {
    $Available=Get-Command ISCC.exe -ErrorAction SilentlyContinue
    if ($Available) { $Compiler=$Available.Source }
    else { $Compiler=Join-Path $Root 'artifacts\installer-tools\inno\ISCC.exe' }
}
if (-not $OutputDirectory) { $OutputDirectory=Join-Path $Root 'dist\installer' }
New-Item -ItemType Directory -Force -Path $OutputDirectory | Out-Null
$Items=Get-ChildItem -LiteralPath $PackageDirectory -Recurse -File | ForEach-Object { [IO.Path]::GetRelativePath($PackageDirectory,$_.FullName) }
$Items=@($Items | Where-Object { $_ -ne 'INSTALL-FILES.txt' }) + 'INSTALL-FILES.txt'
$Items | Sort-Object | Set-Content -LiteralPath (Join-Path $PackageDirectory 'INSTALL-FILES.txt') -Encoding utf8
& $Compiler ("/DAppVersion=$Version") ("/DPackageDir=$PackageDirectory") ("/DOutputDir=$OutputDirectory") (Join-Path $Root 'installer\CuteMaple.iss')
if ($LASTEXITCODE -ne 0) { throw 'Installer compilation failed.' }
$Result=Join-Path $OutputDirectory "CuteMaple-$Version-Setup-x64.exe"
$Hash=(Get-FileHash -LiteralPath $Result -Algorithm SHA256).Hash.ToLower()
"$Hash  $([IO.Path]::GetFileName($Result))" | Set-Content -LiteralPath ($Result+'.sha256.txt') -Encoding utf8
Write-Output $Result
