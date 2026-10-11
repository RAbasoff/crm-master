; ProMaster — установка на сервер компании
; Сборка: ISCC.exe server_setup.iss  (из папки СЕРВЕР)

#define MyAppName "ProMaster Server"
#define MyAppVersion "2.13.0"
#define MyAppPublisher "A.B.A.S.O.F.F."
#define SrcDir "CRM_Server"

[Setup]
AppId={{B7E4C1A2-9F3D-4E8B-A1C2-PROMASTER212}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={autopf}\ProMasterServer
DefaultGroupName=ProMaster
DisableProgramGroupPage=yes
OutputDir=.
OutputBaseFilename=ProMaster_Server_Setup_{#MyAppVersion}
SetupIconFile=..\static\crm.ico
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
PrivilegesRequired=admin
ArchitecturesInstallIn64BitMode=x64compatible
UninstallDisplayIcon={app}\app.py

[Languages]
Name: "russian"; MessagesFile: "compiler:Languages\Russian.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[Files]
Source: "{#SrcDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Dirs]
Name: "{app}\instance"
Name: "{app}\static\uploads"
Name: "{app}\backups"

[Icons]
Name: "{group}\Инструкция по установке"; Filename: "{app}\README_SERVER.txt"
Name: "{group}\Удалить ProMaster Server"; Filename: "{uninstallexe}"

[Run]
Filename: "notepad.exe"; Parameters: """{app}\README_SERVER.txt"""; Description: "Открыть инструкцию"; Flags: postinstall nowait shellexec skipifsilent

[Code]
function InitializeSetup(): Boolean;
begin
  Result := True;
end;

// Данные (instance, uploads, backups) при удалении НЕ трогаем
[UninstallDelete]
Type: files; Name: "{app}\*.log"
Type: filesandordirs; Name: "{app}\__pycache__"
