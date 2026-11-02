; Inno Setup 6. Per-user install (no administrator needed): the agent is a normal program in the signed-in user's session.
#define AppName "Sesh Agent"
#define AppVersion "0.1.0"
#define AppExe "SeshAgent.exe"

[Setup]
AppId={{8B1E5C7A-3F0D-4C2B-9A55-5E3E0B6D7A41}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher=Sesh
DefaultDirName={autopf}\Sesh Agent
DefaultGroupName=Sesh Agent
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
AppMutex=SeshAgentSingleInstance
CloseApplications=yes
OutputDir=..\dist\installer
OutputBaseFilename=SeshAgentSetup
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
UninstallDisplayIcon={app}\{#AppExe}

[Tasks]
Name: "startup"; Description: "Start Sesh Agent automatically when I sign in to Windows"; Flags: checkedonce
Name: "desktopicon"; Description: "Create a &desktop shortcut"; Flags: unchecked

[Files]
Source: "..\dist\SeshAgent\*"; DestDir: "{app}"; Flags: recursesubdirs ignoreversion

[Icons]
Name: "{group}\Sesh Agent"; Filename: "{app}\{#AppExe}"
Name: "{group}\Uninstall Sesh Agent"; Filename: "{uninstallexe}"
Name: "{autodesktop}\Sesh Agent"; Filename: "{app}\{#AppExe}"; Tasks: desktopicon

[Registry]
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: string; ValueName: "SeshAgent"; ValueData: """{app}\{#AppExe}"""; Flags: uninsdeletevalue; Tasks: startup

[Run]
Filename: "{app}\{#AppExe}"; Description: "Start Sesh Agent now"; Flags: postinstall nowait skipifsilent

[Code]
// Program files are always removed on uninstall. The per-user data folder (agent.db with the encrypted credential and
// the job-recovery records, plus logs) is kept unless the user agrees to remove it. Upgrades never touch it.
procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var Dir: String;
begin
  if CurUninstallStep = usPostUninstall then begin
    Dir := ExpandConstant('{localappdata}\SeshAgent');
    if DirExists(Dir) then
      if MsgBox('Also delete Sesh Agent''s saved settings, credential and logs on this computer?' + #13#10 +
                'Choose No to keep them (for example if you plan to reinstall).', mbConfirmation, MB_YESNO or MB_DEFBUTTON2) = IDYES then
        DelTree(Dir, True, True, True);
  end;
end;
