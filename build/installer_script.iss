; Script de Inno Setup para la aplicación "Transcriptor de Video IA"
; Compila la salida de PyInstaller en un instalador autónomo de Windows.

[Setup]
#define AppName "Transcriptor de Video IA"
#define AppVersion "1.0.0"
#define AppPublisher "Desarrollo IA"
#define AppExeName "TranscriptorVideoIA.exe"

AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher={#AppPublisher}
DefaultDirName={autopf}\Transcriptor de Video IA
DefaultGroupName=Transcriptor de Video IA
DisableProgramGroupPage=yes
; Configurar icono para agregar/quitar programas
UninstallDisplayIcon={app}\{#AppExeName}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
; Carpeta de salida para el instalador final
OutputDir=..\dist
OutputBaseFilename=TranscriptorVideoIA_Setup

[Languages]
Name: "spanish"; MessagesFile: "compiler:Languages\Spanish.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
; Copiar todos los archivos resultantes del empaquetado de PyInstaller
Source: "..\dist\TranscriptorVideoIA\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
; Accesos directos
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExeName}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExeName}"; Tasks: desktopicon

[Run]
; Ejecución post-instalación
Description: "{cm:LaunchProgram,{#StringChange(AppName, '&', '&&')}}"; Filename: "{app}\{#AppExeName}"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
; Desinstalación limpia: Eliminar cualquier archivo residual en la carpeta de instalación
Type: filesandordirs; Name: "{app}"
