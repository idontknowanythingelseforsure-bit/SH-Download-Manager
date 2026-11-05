; ---------------------------------------------------------------------------
;  SH Download Manager - Windows installer (Inno Setup 6.7+)
;
;  Build:  powershell -ExecutionPolicy Bypass -File installer\build_installer.ps1
;
;  Installs to %ProgramFiles%\SH Download Manager, adds Start Menu and
;  desktop shortcuts, starts the app in the background with Windows, registers
;  the shdm:// protocol and ships the browser extension + ffmpeg.
; ---------------------------------------------------------------------------

#define AppName        "SH Download Manager"
#define AppVersion     "1.1.0"
#define AppVersionNum  "1.1.0.0"
#define AppPublisher   "SH Download Manager"
#define AppExeName     "SH Download Manager.exe"
#define AppUrl         "https://github.com/idontknowanythingelseforsure-bit/YT-Audio-Player"
#define AppId          "{{8E2C41F7-2B4B-4F6A-9C4E-5D0B7A3F21C8}"

#define Here      AddBackslash(SourcePath)
#define RepoRoot  AddBackslash(SourcePath) + "..\"

; ffmpeg is optional at build time: MP3 output is hidden when it is missing.
#ifndef FFmpegRoot
  #define FFmpegRoot "C:\ffmpeg"
#endif
#define FFmpegExe      AddBackslash(FFmpegRoot) + "bin\ffmpeg.exe"
#define FFmpegLicense  AddBackslash(FFmpegRoot) + "LICENSE.txt"

[Setup]
AppId={#AppId}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher={#AppPublisher}
AppPublisherURL={#AppUrl}
AppSupportURL={#AppUrl}
AppUpdatesURL={#AppUrl}
VersionInfoVersion={#AppVersionNum}
VersionInfoCompany={#AppPublisher}
VersionInfoDescription={#AppName} Setup
VersionInfoProductName={#AppName}
VersionInfoProductVersion={#AppVersion}
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
DisableDirPage=auto
AllowNoIcons=yes
OutputDir={#RepoRoot}
OutputBaseFilename=SH Download Manager Setup
SetupIconFile={#RepoRoot}assets\icon.ico
UninstallDisplayIcon={app}\{#AppExeName}
UninstallDisplayName={#AppName}
Compression=lzma2/ultra64
SolidCompression=yes
LZMAUseSeparateProcess=yes
WizardStyle=modern dark
WizardBackColor=#1e1e1e
WizardBackColorDynamicDark=#1e1e1e
WizardImageFile={#Here}wizard-image.png
WizardSmallImageFile={#Here}wizard-small.png
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
PrivilegesRequired=admin
PrivilegesRequiredOverridesAllowed=dialog
MinVersion=10.0
CloseApplications=no
RestartApplications=no
SetupLogging=yes
ShowLanguageDialog=no
UsePreviousTasks=yes

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Messages]
WelcomeLabel2=This will install [name/ver] on your computer.%n%nSetup adds Start Menu and desktop shortcuts, starts the app in the background with Windows so the browser extension always works, and copies the Chrome / Edge extension and the MP3 converter (ffmpeg) into the program folder.

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: checkedonce
Name: "autostart"; Description: "Start SH Download Manager in the background when Windows starts (required for the browser extension)"; GroupDescription: "Windows startup:"; Flags: checkedonce

[Files]
Source: "{#RepoRoot}{#AppExeName}"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#RepoRoot}README.md"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#Here}THIRD-PARTY-NOTICES.txt"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#RepoRoot}browser_extension\*"; DestDir: "{app}\browser_extension"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "{#Here}Add-Browser-Extension.ps1"; DestDir: "{app}\extension-setup"; Flags: ignoreversion
Source: "{#Here}Add-Browser-Extension.cmd"; DestDir: "{app}\extension-setup"; Flags: ignoreversion
#if FileExists(FFmpegExe)
Source: "{#FFmpegExe}"; DestDir: "{app}\ffmpeg"; Flags: ignoreversion
#endif
#if FileExists(FFmpegLicense)
Source: "{#FFmpegLicense}"; DestDir: "{app}\ffmpeg"; DestName: "LICENSE.txt"; Flags: ignoreversion
#endif

[Icons]
Name: "{autoprograms}\{#AppName}"; Filename: "{app}\{#AppExeName}"; WorkingDir: "{app}"; Comment: "{#AppName}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExeName}"; WorkingDir: "{app}"; Comment: "{#AppName}"; Tasks: desktopicon

[Registry]
; --- start in the background with Windows, exactly like the app's own toggle ---
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: string; ValueName: "{#AppName}"; ValueData: """{app}\{#AppExeName}"" --minimized"; Flags: uninsdeletevalue; Tasks: autostart
; --- App Paths, so "SH Download Manager" works from Win+R and other apps ---
Root: HKLM; Subkey: "Software\Microsoft\Windows\CurrentVersion\App Paths\{#AppExeName}"; ValueType: string; ValueName: ""; ValueData: "{app}\{#AppExeName}"; Flags: uninsdeletekey
Root: HKLM; Subkey: "Software\Microsoft\Windows\CurrentVersion\App Paths\{#AppExeName}"; ValueType: string; ValueName: "Path"; ValueData: "{app}"; Flags: uninsdeletekey
; --- shdm://<url encoded URL> link handler ---
Root: HKCR; Subkey: "shdm"; ValueType: string; ValueName: ""; ValueData: "URL:SH Download Manager link"; Flags: uninsdeletekey
Root: HKCR; Subkey: "shdm"; ValueType: string; ValueName: "URL Protocol"; ValueData: ""
Root: HKCR; Subkey: "shdm\DefaultIcon"; ValueType: string; ValueName: ""; ValueData: "{app}\{#AppExeName},0"
Root: HKCR; Subkey: "shdm\shell\open\command"; ValueType: string; ValueName: ""; ValueData: """{app}\{#AppExeName}"" ""%1"""; Flags: uninsdeletekey

[Run]
Filename: "{app}\{#AppExeName}"; Parameters: "--minimized"; Description: "Start {#AppName} in the background now"; Flags: nowait postinstall skipifsilent runasoriginaluser

[UninstallDelete]
Type: filesandordirs; Name: "{app}\browser_extension"
Type: filesandordirs; Name: "{app}\extension-setup"
Type: filesandordirs; Name: "{app}\ffmpeg"
Type: dirifempty; Name: "{app}"

[Code]
const
  AppExe = '{#AppExeName}';

{ Stop a running instance (including the tray-only one) before files change. }
procedure StopRunningApp();
var
  ResultCode: Integer;
begin
  Exec(ExpandConstant('{sys}\taskkill.exe'),
       '/F /T /IM "' + AppExe + '"', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
  Sleep(600);
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
  if CurStep = ssInstall then
    StopRunningApp();
end;

function InitializeUninstall(): Boolean;
begin
  StopRunningApp();
  Result := True;
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  { Keep %APPDATA%\SHDownloadManager (settings, history) unless it is empty. }
end;
