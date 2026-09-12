[CmdletBinding()]
param(
    [string]$Python = "python",
    [switch]$SkipInstall
)

$ErrorActionPreference = "Stop"
$projectRoot = $PSScriptRoot
$authoringPython = Join-Path $projectRoot ".venv-authoring\Scripts\python.exe"

if (-not (Test-Path -LiteralPath $authoringPython)) {
    & $Python -c "import sys; assert sys.version_info[:2] == (3, 13), 'Authoring lock requires Python 3.13'"
    if ($LASTEXITCODE -ne 0) { throw "Select a Python 3.13 interpreter with -Python." }
    & $Python -m venv (Join-Path $projectRoot ".venv-authoring")
    if ($LASTEXITCODE -ne 0) { throw "Could not create authoring environment." }
}

if (-not $SkipInstall) {
    & $authoringPython -m pip install -r (Join-Path $projectRoot "requirements-authoring.txt")
    if ($LASTEXITCODE -ne 0) { throw "Authoring dependency installation failed." }
}

& $authoringPython (Join-Path $projectRoot "tools\authoring\verify_setup.py")
if ($LASTEXITCODE -ne 0) { throw "Authoring source/dependency verification failed." }
Write-Host "Authoring environment ready: $authoringPython"
Write-Host "No artwork was regenerated. See tools/authoring/README.md for the explicit authoring steps."
