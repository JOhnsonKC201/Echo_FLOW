; ============================================================================
; EchoFlow-Daemon.iss
; Inno Setup script for the FULL Echo Flow product (background daemon + the
; bundled dashboard launcher). Per-user install, no admin required.
;
; Build:
;     iscc installer\EchoFlow-Daemon.iss
;
; Output:
;     installer\Output\EchoFlow-Daemon-Setup-<version>.exe
;
; Companion to installer\EchoFlow.iss (dashboard-only shell). This is the
; one most end users should run — it installs the actual product.
; ============================================================================

#define MyAppName        "Echo Flow"
; Version is injected by CI via `iscc /DMyAppVersion=<ver>`; the default below
; is only the fallback for local manual builds.
#ifndef MyAppVersion
  #define MyAppVersion   "0.2.0"
#endif
#define MyAppPublisher   "Echo Flow"
#define MyAppExeName     "EchoFlow-Daemon.exe"
#define MyAppId          "{{A2F8D6F0-9B7E-4B6F-9D6C-ECHOFLOWDMN01}}"

[Setup]
AppId={#MyAppId}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppVerName={#MyAppName} {#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={localappdata}\Programs\EchoFlow
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
ArchitecturesInstallIn64BitMode=x64
Compression=lzma2/ultra
SolidCompression=yes
WizardStyle=modern
OutputDir=Output
OutputBaseFilename=EchoFlow-Daemon-Setup-{#MyAppVersion}
SetupIconFile=..\assets\icon.ico
UninstallDisplayIcon={app}\{#MyAppExeName}
UninstallDisplayName={#MyAppName} (Daemon)
CloseApplications=force
RestartApplications=no

; ----------------------------------------------------------------------------
; Code signing (optional — uncomment after configuring a SignTool in Inno).
; See installer\SIGNING.md and installer\sign.ps1 for details.
; ----------------------------------------------------------------------------
; SignTool=signtool sign /fd SHA256 /tr http://timestamp.digicert.com /td SHA256 $f
; SignedUninstaller=yes

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon";  Description: "Create a &desktop shortcut";                                                                  GroupDescription: "Additional shortcuts:"
Name: "startmenu";    Description: "Create a &Start Menu shortcut";                                                                GroupDescription: "Additional shortcuts:"
Name: "autostart";    Description: "Start Echo Flow automatically when I log in (recommended)";                                    GroupDescription: "Startup:"
Name: "launchnow";    Description: "Launch the Echo Flow daemon when setup finishes";                                              GroupDescription: "Startup:"

[Files]
; One-folder PyInstaller bundle from EchoFlow-Daemon.spec.
Source: "..\dist\EchoFlow-Daemon\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "..\README.md";              DestDir: "{app}"; Flags: ignoreversion
Source: "..\CHANGELOG.md";           DestDir: "{app}"; Flags: ignoreversion skipifsourcedoesntexist

[Icons]
Name: "{userprograms}\{#MyAppName}";        Filename: "{app}\{#MyAppExeName}"; IconFilename: "{app}\assets\icon.ico"; Tasks: startmenu
Name: "{userdesktop}\{#MyAppName}";         Filename: "{app}\{#MyAppExeName}"; IconFilename: "{app}\assets\icon.ico"; Tasks: desktopicon
Name: "{userprograms}\Uninstall {#MyAppName}"; Filename: "{uninstallexe}"

[Registry]
; Auto-start on user login (HKCU — no admin needed). Pointed at the
; bundled daemon exe, which is now the silent equivalent of run_silent.vbs.
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; \
    ValueType: string; ValueName: "EchoFlow"; ValueData: """{app}\{#MyAppExeName}"""; \
    Flags: uninsdeletevalue; Tasks: autostart

[Run]
; First-run launch — fire-and-forget so the installer wizard can close.
Filename: "{app}\{#MyAppExeName}"; Description: "Launch {#MyAppName} now"; \
    Flags: nowait postinstall skipifsilent; Tasks: launchnow

[UninstallRun]
; Stop a running daemon before removing files so the uninstall can complete
; without "file in use" errors. Errors are tolerated — daemon may not be up.
Filename: "powershell.exe"; Parameters: "-NoProfile -WindowStyle Hidden -Command ""Get-Process -Name 'EchoFlow-Daemon' -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue"""; \
    RunOnceId: "StopEchoFlowDaemon"; Flags: runhidden

[UninstallDelete]
; Leave user data (%LOCALAPPDATA%\EchoFlow\) alone on uninstall — that's
; the user's history, config, and learned vocabulary. They can wipe it
; manually if they want a clean slate.
Type: filesandordirs; Name: "{app}\__pycache__"
