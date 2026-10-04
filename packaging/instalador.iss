; Instalador de Windows (Inno Setup). Se instala para el usuario actual (sin pedir administrador, así las actualizaciones no se traban);
; los DATOS viven en C:\Predio y el instalador NUNCA los toca (ni al actualizar ni al desinstalar).
#define Nombre "Caja del Predio"
#ifndef Version
  #define Version "1.0.0"
#endif

[Setup]
AppId={{6F0C2B1E-5B4D-4B8E-9B77-7A2D3F6A9C11}
AppName={#Nombre}
AppVersion={#Version}
AppPublisher=Predio
DefaultDirName={autopf}\{#Nombre}
DefaultGroupName={#Nombre}
OutputDir=..\dist
OutputBaseFilename=Instalar-Caja-del-Predio-{#Version}
Compression=lzma2
SolidCompression=yes
ArchitecturesInstallIn64BitMode=x64compatible
PrivilegesRequired=lowest
DisableProgramGroupPage=yes
UninstallDisplayIcon={app}\Predio.exe
SetupIconFile=predio.ico
WizardStyle=modern
ShowLanguageDialog=no
LanguageDetectionMethod=none

[Languages]
Name: "es"; MessagesFile: "compiler:Languages\Spanish.isl"

[Tasks]
Name: "escritorio"; Description: "Crear un ícono en el escritorio"; Flags: checkedonce

[Files]
Source: "..\dist\Predio\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs ignoreversion

[Icons]
Name: "{autodesktop}\{#Nombre}"; Filename: "{app}\Predio.exe"; Tasks: escritorio
Name: "{autodesktop}\{#Nombre} (demostración)"; Filename: "{app}\Predio.exe"; Parameters: "--demo"; Tasks: escritorio; Comment: "Datos de ejemplo, separados de los reales. Sirve para mostrar el programa."
Name: "{group}\{#Nombre}"; Filename: "{app}\Predio.exe"
Name: "{group}\{#Nombre} (demostración)"; Filename: "{app}\Predio.exe"; Parameters: "--demo"
Name: "{group}\Carpeta de datos (C:\Predio)"; Filename: "{sys}\explorer.exe"; Parameters: "C:\Predio"

[Run]
Filename: "{app}\Predio.exe"; Description: "Abrir {#Nombre} ahora"; Flags: nowait postinstall skipifsilent

; Tras una actualización automática (sin pantallas) se vuelve a abrir el programa solo
Filename: "{app}\Predio.exe"; Flags: nowait; Check: WizardSilent

[UninstallRun]
; Nada: los datos de C:\Predio se conservan siempre.
