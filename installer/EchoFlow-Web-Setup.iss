; ============================================================================
; EchoFlow-Web-Setup.iss
; Lightweight "web" bootstrapper for Echo Flow. Ships as a tiny (a few MB)
; per-user installer that DOWNLOADS the full daemon payload from the matching
; GitHub release at install time, verifies its SHA256, extracts it, and wires
; the same autostart / shortcuts / uninstall as the full offline installer.
;
; Why: the full installer bundles the entire ML/audio stack (hundreds of MB).
; This bootstrapper keeps the initial download tiny and streams the payload in
; with a progress bar + integrity check.
;
; Shares AppId + install location with EchoFlow-Daemon.iss, so it installs the
; SAME product (one entry in Apps & Features), not a second copy.
;
; Build (CI injects version + payload URL + hash):
;     iscc /DMyAppVersion=0.2.1 ^
;          /DPayloadUrl=https://github.com/JOhnsonKC201/Echo_FLOW/releases/download/v0.2.1/EchoFlow-Daemon-Payload-0.2.1.zip ^
;          /DPayloadSha256=<hash> installer\EchoFlow-Web-Setup.iss
;
; Output:
;     installer\Output\EchoFlow-Web-Setup-<version>.exe
;
; Requires Inno Setup 6.1+ for the native download API (CreateDownloadPage).
; ============================================================================

#define MyAppName        "Echo Flow"
#ifndef MyAppVersion
  #define MyAppVersion   "0.2.0"
#endif
#define MyAppPublisher   "Echo Flow"
#define MyAppExeName     "EchoFlow-Daemon.exe"
; Same AppId as the full installer — both resolve to the one installed product.
#define MyAppId          "{{A2F8D6F0-9B7E-4B6F-9D6C-ECHOFLOWDMN01}}"

; Payload location + integrity. CI passes these; the fallbacks let a local
; `iscc installer\EchoFlow-Web-Setup.iss` build a working bootstrapper that
; pulls the matching release (an empty hash skips verification).
#ifndef PayloadUrl
  #define PayloadUrl     "https://github.com/JOhnsonKC201/Echo_FLOW/releases/download/v" + MyAppVersion + "/EchoFlow-Daemon-Payload-" + MyAppVersion + ".zip"
#endif
#ifndef PayloadSha256
  #define PayloadSha256  ""
#endif

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
WizardStyle=modern
OutputDir=Output
OutputBaseFilename=EchoFlow-Web-Setup-{#MyAppVersion}
SetupIconFile=..\assets\icon.ico
UninstallDisplayIcon={app}\{#MyAppExeName}
UninstallDisplayName={#MyAppName} (Web Installer)
CloseApplications=force
RestartApplications=no

; ----------------------------------------------------------------------------
; Code signing (optional — see installer\SIGNING.md / installer\sign.ps1).
; ----------------------------------------------------------------------------
; SignTool=signtool sign /fd SHA256 /tr http://timestamp.digicert.com /td SHA256 $f
; SignedUninstaller=yes

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon";  Description: "Create a &desktop shortcut";                                    GroupDescription: "Additional shortcuts:"
Name: "startmenu";    Description: "Create a &Start Menu shortcut";                                  GroupDescription: "Additional shortcuts:"
Name: "autostart";    Description: "Start Echo Flow automatically when I log in (recommended)";      GroupDescription: "Startup:"
Name: "launchnow";    Description: "Launch the Echo Flow daemon when setup finishes";                GroupDescription: "Startup:"

[Icons]
Name: "{userprograms}\{#MyAppName}";            Filename: "{app}\{#MyAppExeName}"; IconFilename: "{app}\assets\icon.ico"; Tasks: startmenu
Name: "{userdesktop}\{#MyAppName}";             Filename: "{app}\{#MyAppExeName}"; IconFilename: "{app}\assets\icon.ico"; Tasks: desktopicon
Name: "{userprograms}\Uninstall {#MyAppName}";  Filename: "{uninstallexe}"

[Registry]
; Auto-start on user login (HKCU — no admin needed), pointed at the extracted
; daemon exe.
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; \
    ValueType: string; ValueName: "EchoFlow"; ValueData: """{app}\{#MyAppExeName}"""; \
    Flags: uninsdeletevalue; Tasks: autostart

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "Launch {#MyAppName} now"; \
    Flags: nowait postinstall skipifsilent; Tasks: launchnow

[UninstallRun]
; Stop a running daemon before removing files so the uninstall can complete.
Filename: "powershell.exe"; Parameters: "-NoProfile -WindowStyle Hidden -Command ""Get-Process -Name 'EchoFlow-Daemon' -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue"""; \
    RunOnceId: "StopEchoFlowDaemon"; Flags: runhidden

[UninstallDelete]
; The payload is extracted at install time and is NOT tracked by Inno's file
; list, so remove the whole per-user install dir on uninstall. User data lives
; separately in %LOCALAPPDATA%\EchoFlow and is intentionally left untouched.
Type: filesandordirs; Name: "{app}"

[Code]
var
  DownloadPage: TDownloadWizardPage;

function OnDownloadProgress(const Url, FileName: String; const Progress, ProgressMax: Int64): Boolean;
begin
  Result := True;
end;

procedure InitializeWizard;
begin
  DownloadPage := CreateDownloadPage(
    'Downloading Echo Flow',
    'Setup is fetching the Echo Flow application files. This one-time download ' +
    'is a few hundred MB; later updates are incremental.',
    @OnDownloadProgress);
end;

function NextButtonClick(CurPageID: Integer): Boolean;
begin
  if CurPageID = wpReady then begin
    DownloadPage.Clear;
    // An empty hash (local fallback builds) skips verification; CI always
    // passes the real SHA256 so the payload is integrity-checked.
    DownloadPage.Add('{#PayloadUrl}', 'EchoFlow-Payload.zip', '{#PayloadSha256}');
    DownloadPage.Show;
    try
      try
        DownloadPage.Download;
        Result := True;
      except
        if DownloadPage.AbortedByUser then
          Result := False
        else begin
          SuppressibleMsgBox(AddPeriod(GetExceptionMessage), mbCriticalError, MB_OK, IDOK);
          Result := False;
        end;
      end;
    finally
      DownloadPage.Hide;
    end;
  end else
    Result := True;
end;

procedure CurStepChanged(CurStep: TSetupStep);
var
  ResultCode: Integer;
  Params: String;
begin
  // Extract at ssInstall (after the wpReady download, before [Icons] are
  // created) so the shortcut targets + icon exist when Inno wires them.
  if CurStep = ssInstall then begin
    ForceDirectories(ExpandConstant('{app}'));
    Params := '-NoProfile -ExecutionPolicy Bypass -Command "' +
      'Expand-Archive -LiteralPath ''' + ExpandConstant('{tmp}\EchoFlow-Payload.zip') + ''' ' +
      '-DestinationPath ''' + ExpandConstant('{app}') + ''' -Force"';
    if (not Exec('powershell.exe', Params, '', SW_HIDE, ewWaitUntilTerminated, ResultCode)) or (ResultCode <> 0) then
      RaiseException('Could not extract the Echo Flow payload (exit ' +
        IntToStr(ResultCode) + '). The install is incomplete.');
  end;
end;
