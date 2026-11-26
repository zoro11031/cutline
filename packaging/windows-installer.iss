#ifndef Payload
  #error Pass /DPayload=<directory containing launcher.py>
#endif
#ifndef Version
  #error Pass /DVersion=<Cutline version>
#endif

[Setup]
AppId=Cutline.Inno
AppName=Cutline
AppVersion={#Version}
AppPublisher=Cutline contributors
AppPublisherURL=https://github.com/zoro11031/cutline
DefaultDirName={code:DefaultDirectory}
DefaultGroupName=Cutline
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
WizardStyle=modern dynamic
DisableWelcomePage=yes
Compression=lzma2/max
SolidCompression=yes
SetupIconFile={#Payload}\cutline.ico
LicenseFile={#Payload}\LICENSE
UninstallDisplayIcon={app}\app\cutline.ico
OutputBaseFilename=Cutline-{#Version}-Windows-x86_64-Setup
CloseApplications=yes
RestartApplications=no

[Tasks]
Name: startmenu; Description: "Create a Start menu shortcut"
Name: desktopicon; Description: "Create a desktop shortcut"; Flags: unchecked

[Files]
Source: "{#Payload}\*"; DestDir: "{app}\app"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{userprograms}\Cutline"; Filename: "{app}\app\pythonw.exe"; Parameters: """{app}\app\launcher.py"""; WorkingDir: "{app}\app"; IconFilename: "{app}\app\cutline.ico"; Tasks: startmenu
Name: "{userdesktop}\Cutline"; Filename: "{app}\app\pythonw.exe"; Parameters: """{app}\app\launcher.py"""; WorkingDir: "{app}\app"; IconFilename: "{app}\app\cutline.ico"; Tasks: desktopicon

[Run]
Filename: "{app}\app\pythonw.exe"; Parameters: """{app}\app\launcher.py"""; WorkingDir: "{app}\app"; Description: "Launch Cutline"; Flags: nowait postinstall skipifsilent

[Code]
const
  LegacyKey = 'Software\Microsoft\Windows\CurrentVersion\Uninstall\Cutline';
var
  LegacyDir: String;
  LegacyMoved, Finished: Boolean;

function DefaultDirectory(Param: String): String;
begin
  Result := ExpandConstant('{localappdata}\Programs\Cutline');
  if RegQueryStringValue(HKCU64, LegacyKey, 'InstallLocation', LegacyDir)
    and FileExists(AddBackslash(LegacyDir) + 'Uninstall.exe')
    and (FileExists(AddBackslash(LegacyDir) + 'app\launcher.py') or
         FileExists(AddBackslash(LegacyDir) + 'app.inno-previous\launcher.py')) then
    Result := LegacyDir
  else
    LegacyDir := '';
end;

procedure InitializeWizard;
var
  Tasks: String;
begin
  if LegacyDir <> '' then begin
    Tasks := '';
    if FileExists(ExpandConstant('{userprograms}\Cutline.lnk')) then
      Tasks := 'startmenu';
    if FileExists(ExpandConstant('{userdesktop}\Cutline.lnk')) then begin
      if Tasks <> '' then Tasks := Tasks + ',';
      Tasks := Tasks + 'desktopicon';
    end;
    WizardSelectTasks(Tasks);
  end;
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
var
  InstallDir, OldApp, Backup: String;
begin
  Result := '';
  if LegacyDir = '' then
    Exit;
  InstallDir := ExpandConstant('{app}');
  if CompareText(RemoveBackslashUnlessRoot(InstallDir), RemoveBackslashUnlessRoot(LegacyDir)) <> 0 then begin
    Result := 'Choose the existing Cutline folder to upgrade: ' + LegacyDir;
    Exit;
  end;
  OldApp := AddBackslash(LegacyDir) + 'app';
  Backup := OldApp + '.inno-previous';
  if DirExists(Backup) then begin
    if DirExists(OldApp) or not FileExists(Backup + '\launcher.py') or not RenameFile(Backup, OldApp) then begin
      Result := 'Setup found an unfinished Cutline upgrade in ' + LegacyDir + '.';
      Exit;
    end;
  end;
  if not RenameFile(OldApp, Backup) then
    Result := 'Close Cutline and try again.'
  else
    LegacyMoved := True;
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
  if (CurStep = ssPostInstall) and LegacyMoved then begin
    Finished := True;
    DeleteFile(AddBackslash(LegacyDir) + 'Uninstall.exe');
    RegDeleteKeyIncludingSubkeys(HKCU64, LegacyKey);
    DelTree(AddBackslash(LegacyDir) + 'app.inno-previous', True, True, True);
  end;
end;

procedure DeinitializeSetup;
begin
  if LegacyMoved and not Finished then begin
    DelTree(AddBackslash(LegacyDir) + 'app', True, True, True);
    RenameFile(AddBackslash(LegacyDir) + 'app.inno-previous', AddBackslash(LegacyDir) + 'app');
  end;
end;
