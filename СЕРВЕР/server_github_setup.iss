; ProMaster — одноразовая установка «обновлятора с GitHub» + код
; После этой установки сервер сам тянет обновления с GitHub (без новых EXE).
; Сборка: ISCC.exe server_github_setup.iss  (из папки СЕРВЕР)

#define MyAppName "ProMaster Server"
#define MyAppVersion "2.15.4"
#define MyAppPublisher "A.B.A.S.O.F.F."
#define SrcDir "CRM_Server"

[Setup]
AppId={{B7E4C1A2-9F3D-4E8B-A1C2-PROMASTER2140}
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
; код + github_updater
Source: "{#SrcDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
; готовый EXE-апдейтер (если собран)
Source: "ProMaster_GitHub_Updater.exe"; DestDir: "{app}"; Flags: ignoreversion skipifsourcedoesntexist

[Dirs]
Name: "{app}\instance"
Name: "{app}\static\uploads"
Name: "{app}\backups"
Name: "{app}\logs"

[Icons]
Name: "{group}\Обновление с GitHub"; Filename: "{app}\update_from_github.bat"
Name: "{group}\ProMaster Updater (exe)"; Filename: "{app}\ProMaster_GitHub_Updater.exe"
Name: "{group}\Инструкция"; Filename: "{app}\README_SERVER.txt"
Name: "{group}\Удалить ProMaster Server"; Filename: "{uninstallexe}"

[Run]
Filename: "{app}\update_from_github.bat"; Description: "Проверить обновления с GitHub сейчас"; Flags: postinstall nowait shellexec skipifsilent
Filename: "notepad.exe"; Parameters: """{app}\README_SERVER.txt"""; Description: "Открыть инструкцию"; Flags: postinstall nowait shellexec skipifsilent

[Code]
// Данные (instance, uploads, backups) при удалении НЕ трогаем
[UninstallDelete]
Type: files; Name: "{app}\*.log"
Type: filesandordirs; Name: "{app}\__pycache__"
