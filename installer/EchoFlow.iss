; EchoFlow.iss — Inno Setup script for Echo Flow desktop installer
;
; Builds a single EchoFlow-Setup.exe that installs per-user (no admin) into
; %LOCALAPPDATA%\EchoFlow, registers Start Menu and (optionally) desktop
; shortcuts, optionally auto-launches at login via HKCU\...\Run, and
; ships a clean uninstaller.
;
; Usage:
;   1. Build the app (PyInstaller or Nuitka). The source folder must contain
;      EchoFlow.exe + all DLLs/data alongside it.
;   2. Edit SourceDir below if your build output differs (default points at
;      the PyInstaller one-folder output).
;   3. Run: iscc installer\EchoFlow.iss
;   4. Find Output\EchoFlow-Setup.exe.

#define MyAppName       "Echo Flow"
#define MyAppVersion    "0.1.0"
#define MyAppPublisher  "Echo Flow"
#define MyAppURL        "https://github.com/JOhnsonKC201/echo-flow"
#define MyAppExeName    "EchoFlow.exe"

; --- Source: switch between PyInstaller and Nuitka outputs ----------------
; PyInstaller default:
#define SourceDir       "..\dist\EchoFlow"
; Nuitka alternative (uncomment to use):
; #define SourceDir     "..\dist_nuitka\app.dist"

[Setup]
AppId={{C0F3E1B2-7A4D-4F1A-9E3B-EF0AA1A2B3C4}}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppVerName={#MyAppName} {#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
AppSupportURL={#MyAppURL}
AppUpdatesURL={#MyAppURL}

; Per-user install — no admin rights required, no UAC prompt.
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog

DefaultDirName={localappdata}\{#MyAppName}
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
DisableDirPage=no
UninstallDisplayIcon={app}\{#MyAppExeName}
UninstallDisplayName={#MyAppName} {#MyAppVersion}

OutputDir=Output
OutputBaseFilename=EchoFlow-Setup
SetupIconFile=..\assets\icon.ico

Compression=lzma2/ultra64
SolidCompression=yes
WizardStyle=modern

ArchitecturesAllowed=x64
ArchitecturesInstallIn64BitMode=x64

; ---- For code signing (see installer\SIGNING.md) -------------------------
; SignTool=signtool
; SignedUninstaller=yes

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon";  Description: "Create a &desktop shortcut";          GroupDescription: "Additional shortcuts:"; Flags: unchecked
Name: "autostart";    Description: "Launch {#MyAppName} when I sign in";  GroupDescription: "Startup:";              Flags: unchecked

[Files]
; Recursively copy the entire build output folder.
Source: "{#SourceDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#MyAppName}";        Filename: "{app}\{#MyAppExeName}"
Name: "{group}\Uninstall {#MyAppName}"; Filename: "{uninstallexe}"
Name: "{userdesktop}\{#MyAppName}";  Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Registry]
; Optional auto-launch on user login (HKCU — no admin needed).
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; \
    ValueType: string; ValueName: "{#MyAppName}"; ValueData: """{app}\{#MyAppExeName}"""; \
    Flags: uninsdeletevalue; Tasks: autostart

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "Launch {#MyAppName}"; \
    Flags: nowait postinstall skipifsilent

[UninstallDelete]
; Clean up runtime caches/logs the app writes into its install dir.
Type: filesandordirs; Name: "{app}\logs"
Type: filesandordirs; Name: "{app}\__pycache__"
