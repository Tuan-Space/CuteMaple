param()
$ErrorActionPreference = 'Stop'
$ProjectRoot = Split-Path -Parent $PSScriptRoot
& (Join-Path $PSScriptRoot 'build_native_cleaner.ps1') -OutputDirectory (Join-Path $ProjectRoot 'artifacts/native-cleaner')
