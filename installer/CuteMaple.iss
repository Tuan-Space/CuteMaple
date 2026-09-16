#ifndef AppVersion
  #error AppVersion must be supplied by build_installer.ps1
#endif
#ifndef PackageDir
  #error PackageDir must be supplied by build_installer.ps1
#endif
[Setup]
AppId={{D4B7E46A-8336-48B5-99C0-286F10DF7862}
AppName=美腻枫 CuteMaple
AppVersion={#AppVersion}
AppPublisher=Tuan-Space
AppPublisherURL=https://github.com/Tuan-Space/CuteMaple
DefaultDirName={localappdata}\Programs\CuteMaple
DefaultGroupName=美腻枫
DisableProgramGroupPage=yes
DisableDirPage=no
UsePreviousAppDir=yes
UsePreviousTasks=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
WizardStyle=modern
Compression=lzma2/normal
SolidCompression=yes
OutputDir={#OutputDir}
OutputBaseFilename=CuteMaple-{#AppVersion}-Setup-x64
UninstallDisplayIcon={app}\CuteMaple-Live2D.exe
CloseApplications=yes
RestartApplications=no
SetupLogging=yes

[Languages]
Name: "chinesesimp"; MessagesFile: "ChineseSimplified.isl"

[Tasks]
Name: "desktopicon"; Description: "创建桌面快捷方式"; GroupDescription: "快捷方式："
Name: "autostart"; Description: "登录 Windows 时启动美腻枫"; GroupDescription: "启动选项："; Flags: unchecked

[Files]
Source: "{#PackageDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\美腻枫"; Filename: "{app}\CuteMaple-Live2D.exe"; WorkingDir: "{app}"
Name: "{autodesktop}\美腻枫"; Filename: "{app}\CuteMaple-Live2D.exe"; WorkingDir: "{app}"; Tasks: desktopicon

[Registry]
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: string; ValueName: "美腻枫"; ValueData: """{app}\CuteMaple-Live2D.exe"""; Flags: uninsdeletevalue; Tasks: autostart

[Run]
Filename: "{app}\CuteMaple-Live2D.exe"; Description: "启动美腻枫"; Flags: nowait postinstall skipifsilent

[Code]
var OldManifest: TArrayOfString;
function GetFileAttributesW(Name: String): LongWord;
  external 'GetFileAttributesW@kernel32.dll stdcall';

function PrepareToInstall(var NeedsRestart: Boolean): String;
var Code: Integer; OldExe: String;
begin
  Result := '';
  OldExe := ExpandConstant('{app}\CuteMaple-Live2D.exe');
  if FileExists(OldExe) then begin
    if not Exec(OldExe, '--request-quit', ExpandConstant('{app}'), SW_HIDE, ewWaitUntilTerminated, Code) then
      Result := '无法让旧程序安全退出，请保存笔记并关闭美腻枫后重试。'
    else if Code <> 0 then
      Result := '笔记或录音尚未保存，请在旧程序中处理后重试。';
  end;
  LoadStringsFromFile(ExpandConstant('{app}\INSTALL-FILES.txt'), OldManifest);
end;

function SafeOwnedPath(Relative: String): Boolean;
var Candidate, Root, Parent: String; Attrs: LongWord;
begin
  Result := False;
  if (Pos('..', Relative) > 0) or (Pos(':', Relative) > 0) or (Copy(Relative,1,1)='\') or (Copy(Relative,1,1)='/') then Exit;
  Root := AddBackslash(ExpandConstant('{app}'));
  Candidate := ExpandFileName(Root + Relative);
  if CompareText(Copy(Candidate,1,Length(Root)),Root) <> 0 then Exit;
  Parent := Candidate;
  while Length(Parent) >= Length(Root)-1 do begin
    Attrs := GetFileAttributesW(Parent);
    if (Attrs <> $FFFFFFFF) and ((Attrs and $400) <> 0) then Exit;
    Parent := ExtractFileDir(Parent);
    if Parent='' then Exit;
  end;
  Result := True;
end;

procedure CurStepChanged(CurStep: TSetupStep);
var Current: TArrayOfString; I,J: Integer; Found: Boolean;
begin
  if CurStep=ssPostInstall then begin
    if not WizardIsTaskSelected('autostart') then
      RegDeleteValue(HKCU,'Software\Microsoft\Windows\CurrentVersion\Run','美腻枫');
    if not WizardIsTaskSelected('desktopicon') then
      DeleteFile(ExpandConstant('{autodesktop}\美腻枫.lnk'));
    if LoadStringsFromFile(ExpandConstant('{app}\INSTALL-FILES.txt'),Current) then
      for I:=0 to GetArrayLength(OldManifest)-1 do begin
        Found:=False;
        for J:=0 to GetArrayLength(Current)-1 do
          if CompareText(OldManifest[I],Current[J])=0 then Found:=True;
        if not Found and SafeOwnedPath(OldManifest[I]) then
          DeleteFile(ExpandConstant('{app}\')+OldManifest[I]);
      end;
    SaveStringToFile(ExpandConstant('{app}\installed.json'),'{"installed":true,"version":"{#AppVersion}"}',False);
  end;
end;

[UninstallDelete]
Type: files; Name: "{app}\installed.json"
