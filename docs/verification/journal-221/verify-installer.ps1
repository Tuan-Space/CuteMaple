param([Parameter(Mandatory=$true)][string]$Installer,[Parameter(Mandatory=$true)][string]$Package)
$ErrorActionPreference='Stop'
$Root='F:\cutemaple\CuteMaple-Live2D\artifacts\journal-221'
$Target=Join-Path $Root '中文 空格安装'
if (Test-Path -LiteralPath $Target) {throw 'Fresh installation directory required'}
$Resolved=[IO.Path]::GetFullPath($Target)
if (-not $Resolved.StartsWith($Root+'\',[StringComparison]::OrdinalIgnoreCase)) {throw 'Unsafe target'}
$UninstallKey='HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\{D4B7E46A-8336-48B5-99C0-286F10DF7862}_is1'
if (Test-Path $UninstallKey) {throw 'Existing installation registration found; do not overwrite it for testing'}
$Run='HKCU:\Software\Microsoft\Windows\CurrentVersion\Run'
$OldRun=Get-ItemPropertyValue -Path $Run -Name '美腻枫' -ErrorAction SilentlyContinue
$Links=@((Join-Path ([Environment]::GetFolderPath('Desktop')) '美腻枫.lnk'),(Join-Path ([Environment]::GetFolderPath('Programs')) '美腻枫.lnk'))
$Saved=@{}
foreach ($Link in $Links) {if(Test-Path -LiteralPath $Link){$Saved[$Link]=[IO.File]::ReadAllBytes($Link)}else{$Saved[$Link]=$null}}
$Before=@(Get-Process CuteMaple-Live2D -ErrorAction SilentlyContinue | Select-Object Id,Path)
$Result=[ordered]@{freshInstall=$false;uninstalled=$false;packageFilesMatched=$false;existingProcessesPreserved=$false;settingsRestored=$false}
try {
 $Args='/VERYSILENT /SUPPRESSMSGBOXES /NORESTART /NOCLOSEAPPLICATIONS /NORESTARTAPPLICATIONS /DIR="'+$Target+'" /TASKS="desktopicon,autostart" /LOG="'+$Root+'\install.log"'
 $P=Start-Process -FilePath $Installer -ArgumentList $Args -WindowStyle Hidden -PassThru -Wait
 if($P.ExitCode -ne 0){throw ('Install failed: '+$P.ExitCode)}
 $Result.freshInstall=$true
 foreach($File in Get-ChildItem -LiteralPath $Package -Recurse -File){
   $Relative=[IO.Path]::GetRelativePath($Package,$File.FullName);$Installed=Join-Path $Target $Relative
   if((Get-FileHash -LiteralPath $Installed).Hash -ne (Get-FileHash -LiteralPath $File.FullName).Hash){throw ('Mismatch: '+$Relative)}
 }
 $Result.packageFilesMatched=$true
 $Result.desktopShortcut=Test-Path -LiteralPath $Links[0]
 $Result.autostart=(Get-ItemPropertyValue -Path $Run -Name '美腻枫') -eq ('"'+(Join-Path $Target 'CuteMaple-Live2D.exe')+'"')
 $P=Start-Process -FilePath (Join-Path $Target 'unins000.exe') -ArgumentList ('/VERYSILENT /SUPPRESSMSGBOXES /NORESTART /LOG="'+$Root+'\uninstall.log"') -WindowStyle Hidden -PassThru -Wait
 if($P.ExitCode -ne 0){throw ('Uninstall failed: '+$P.ExitCode)}
 $Result.uninstalled= -not (Test-Path -LiteralPath (Join-Path $Target 'CuteMaple-Live2D.exe'))
} finally {
 if($null -ne $OldRun){Set-ItemProperty -Path $Run -Name '美腻枫' -Value $OldRun}else{Remove-ItemProperty -Path $Run -Name '美腻枫' -ErrorAction SilentlyContinue}
 foreach($Link in $Links){if($null -ne $Saved[$Link]){[IO.File]::WriteAllBytes($Link,$Saved[$Link])}elseif(Test-Path -LiteralPath $Link){Remove-Item -LiteralPath $Link}}
 $NowRun=Get-ItemPropertyValue -Path $Run -Name '美腻枫' -ErrorAction SilentlyContinue
 $Result.settingsRestored=$NowRun -eq $OldRun
 $Result.existingProcessesPreserved=@($Before | Where-Object {-not (Get-Process -Id $_.Id -ErrorAction SilentlyContinue)}).Count -eq 0
 $Result.runningBefore=$Before
 $Result | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath (Join-Path $Root 'installer-report.json') -Encoding utf8
}
$Result | ConvertTo-Json -Depth 5
