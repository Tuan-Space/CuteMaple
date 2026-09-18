param([switch]$Animation)
$ErrorActionPreference='Stop'
$root=Split-Path -Parent $PSScriptRoot
$model=Join-Path $root 'assets/authoring/model/Maple.cmo3'
if(-not (Test-Path -LiteralPath $model)) {throw 'Model missing. Download the complete Source.zip or run git lfs pull.'}
if($Animation) {
 $python=Join-Path $root '.venv-authoring/Scripts/python.exe'
 if(-not (Test-Path -LiteralPath $python)) {throw 'Run setup-authoring.ps1 first.'}
 & $python (Join-Path $root 'tools/prepare_editor_project.py') --root $root
 if($LASTEXITCODE -ne 0) {throw 'Animation relink failed'}
 Invoke-Item -LiteralPath (Join-Path $root 'assets/authoring/model/Maple-native-saved.can3')
} else {Invoke-Item -LiteralPath $model}
