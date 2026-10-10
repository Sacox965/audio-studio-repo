; Script de Inno Setup para Audio Studio
; Se compila automaticamente desde construir_instalador.bat

[Setup]
AppName=Audio Studio
AppVersion=1.0
AppPublisher=Audio Studio
DefaultDirName={autopf}\Audio Studio
DefaultGroupName=Audio Studio
OutputDir=salida
OutputBaseFilename=AudioStudio_Instalador
Compression=lzma2
SolidCompression=yes
PrivilegesRequired=lowest
WizardStyle=modern
UninstallDisplayName=Audio Studio

[Languages]
Name: "spanish"; MessagesFile: "compiler:Languages\Spanish.isl"

[Tasks]
Name: "desktopicon"; Description: "Crear un acceso directo en el escritorio"; GroupDescription: "Accesos directos:"

[Files]
Source: "dist\AudioStudio\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs ignoreversion

[Icons]
Name: "{group}\Audio Studio"; Filename: "{app}\AudioStudio.exe"
Name: "{autodesktop}\Audio Studio"; Filename: "{app}\AudioStudio.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\AudioStudio.exe"; Description: "Abrir Audio Studio"; Flags: nowait postinstall skipifsilent
