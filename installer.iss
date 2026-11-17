; Instalador Windows do NIMAGE VIDEO STUDIO (Inno Setup 6)
#define AppName "NIMAGE VIDEO STUDIO"
#define AppVersion "1.0.0"

[Setup]
AppId={{6E7B1D2A-5C3F-4E8B-9A41-NIMAGEVIDEO01}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher=Nimage
DefaultDirName={autopf}\Nimage Video Studio
DefaultGroupName=Nimage Video Studio
OutputBaseFilename=NimageVideoStudio-Instalador-{#AppVersion}
SetupIconFile=nimage_studio\resources\icone.ico
UninstallDisplayIcon={app}\NimageVideoStudio.exe
Compression=lzma2
SolidCompression=yes
ArchitecturesInstallIn64BitMode=x64compatible
PrivilegesRequiredOverridesAllowed=dialog
WizardStyle=modern

[Languages]
Name: "ptbr"; MessagesFile: "compiler:Languages\BrazilianPortuguese.isl"

[Tasks]
Name: "desktopicon"; Description: "Criar atalho na Área de Trabalho"; GroupDescription: "Atalhos:"

[Files]
Source: "dist\NimageVideoStudio\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\NimageVideoStudio.exe"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\NimageVideoStudio.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\NimageVideoStudio.exe"; Description: "Abrir o {#AppName}"; Flags: nowait postinstall skipifsilent
