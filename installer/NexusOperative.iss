#define AppName "Nexus Operative"
#define AppVersion "0.47.0"
[Setup]
AppId={{BFCE93A2-A6EF-42E1-B641-DC51B2B6A7C0}
AppName={#AppName}
AppVersion={#AppVersion}
DefaultDirName={localappdata}\Programs\Nexus Operative
DefaultGroupName=Nexus Operative
OutputDir=..\release
OutputBaseFilename=NexusOperative-Setup-0.47.0
PrivilegesRequired=lowest
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
UninstallDisplayIcon={app}\NexusOperative.exe
[Files]
Source: "..\release\NexusOperative\NexusOperative.exe"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\START_HERE.txt"; DestDir: "{app}"; DestName: "README.txt"; Flags: ignoreversion
[Icons]
Name: "{autoprograms}\Nexus Operative"; Filename: "{app}\NexusOperative.exe"
Name: "{autodesktop}\Nexus Operative"; Filename: "{app}\NexusOperative.exe"; Tasks: desktopicon
[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Additional icons:"
[Run]
Filename: "{app}\NexusOperative.exe"; Description: "Launch Nexus Operative"; Flags: nowait postinstall skipifsilent
