; Inno Setup script — CRM Мастерская (ProMaster) 2.12
; Build:  ISCC.exe installer.iss
; Output: installer\CRM_Masterworkshop_2.12_Setup.exe

#define MyAppName "CRM Мастерская"
#define MyAppNameEn "CRM Masterworkshop"
#define MyAppVersion "2.12"
#define MyAppPublisher "A.B.A.S.O.F.F."
#define MyAppExeName "CRM_Masterworkshop.exe"
#define BuildDir "dist\CRM_Masterworkshop"

[Setup]
AppId={{8F3C2A91-5B4E-4D2A-9C7E-CRMMASTER211}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={autopf}\{#MyAppNameEn}
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
OutputDir=installer
OutputBaseFilename=CRM_Masterworkshop_{#MyAppVersion}_Setup
SetupIconFile=static\crm.ico
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
PrivilegesRequired=admin
ArchitecturesInstallIn64BitMode=x64compatible
UninstallDisplayIcon={app}\{#MyAppExeName}

[Languages]
Name: "russian"; MessagesFile: "compiler:Languages\Russian.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"

[Files]
; Программа. uploads/ и instance/ — данные, в дистрибутив не кладём.
Source: "{#BuildDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs; Excludes: "uploads\*,instance\*,*.db,*.log"

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; IconFilename: "{app}\{#MyAppExeName}"
Name: "{group}\{cm:UninstallProgram,{#MyAppName}}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon; IconFilename: "{app}\{#MyAppExeName}"

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "Запустить CRM Мастерская"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
; instance/werkplaats.db и static/uploads — данные компании, НЕ удаляем
Type: files; Name: "{app}\*.log"
Type: files; Name: "{app}\*.log.txt"
Type: filesandordirs; Name: "{app}\__pycache__"
Type: files; Name: "{app}\.secret_key"
