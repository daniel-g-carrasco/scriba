; Inno Setup script for Scriba.
; Compiled by the CI with:  ISCC.exe /DAppVersion=X.Y.Z build-aux\windows\scriba.iss
; Expects the PyInstaller output in dist\Scriba (repository root).
;
; The settings (%APPDATA%\Scriba) and the app's own files
; (%LOCALAPPDATA%\Scriba) stay when the app is uninstalled: they are the
; user's, and a reinstall goes on from them.

#ifndef AppVersion
  #define AppVersion "dev"
#endif

[Setup]
AppId={{5432B023-8199-4141-B7D5-F463478371B7}
AppName=Scriba
AppVersion={#AppVersion}
AppPublisher=Daniel Grasso
AppPublisherURL=https://github.com/daniel-g-carrasco/scriba
AppSupportURL=https://github.com/daniel-g-carrasco/scriba/issues
AppUpdatesURL=https://github.com/daniel-g-carrasco/scriba/releases
DefaultDirName={autopf}\Scriba
DefaultGroupName=Scriba
DisableProgramGroupPage=yes
; Keep /NOICONS effective for isolated installs even with the group page hidden.
AllowNoIcons=yes
; per-user install by default (no UAC); the dialog lets the user elevate,
; and the command line (/CURRENTUSER, as the app's own update passes it)
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog commandline
OutputDir=..\..\dist
OutputBaseFilename=scriba-setup-v{#AppVersion}-windows-x64
SetupIconFile=scriba.ico
UninstallDisplayIcon={app}\Scriba.exe
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
; an update closes the running app (CloseApplications asks the Restart
; Manager; the app saves its state when its window closes)
CloseApplications=yes
RestartApplications=no

[Languages]
Name: "italian"; MessagesFile: "compiler:Languages\Italian.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[CustomMessages]
italian.AppDescription=Registra una riunione e la trascrive sul tuo computer, con le voci e i sottotitoli
english.AppDescription=Record a meeting and transcribe it on your own computer, with speakers and subtitles

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; Flags: unchecked

[InstallDelete]
; the version before's libraries and modules (PyInstaller's _internal) go
; first: one the new version no longer ships must not stay there and be
; loaded. The app has quit by then (CloseApplications); its settings and
; data are not in {app}, the uninstaller is not in _internal
Type: filesandordirs; Name: "{app}\_internal"

[Files]
Source: "..\..\dist\Scriba\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs ignoreversion

[Icons]
; the AppUserModelID the app sets for itself: its toasts, its taskbar button
Name: "{group}\Scriba"; Filename: "{app}\Scriba.exe"; Comment: "{cm:AppDescription}"; AppUserModelID: "io.github.daniel_g_carrasco.scriba"
Name: "{autodesktop}\Scriba"; Filename: "{app}\Scriba.exe"; Tasks: desktopicon; AppUserModelID: "io.github.daniel_g_carrasco.scriba"

[Run]
Filename: "{app}\Scriba.exe"; Description: "{cm:LaunchProgram,Scriba}"; Flags: nowait postinstall skipifsilent; Check: not AppWasRunning
; an update (from the app's own dialog, /SILENT) starts again the app it closed
Filename: "{app}\Scriba.exe"; Flags: nowait runasoriginaluser; Check: AppWasRunning

[Code]
var
  WasRunning: Boolean;

function AppWasRunning(): Boolean;
begin
  Result := WasRunning;
end;

{ Was the app running when Setup started? The Restart Manager closes it
  (CloseApplications); this only remembers whether to start it again. }
function PrepareToInstall(var NeedsRestart: Boolean): String;
begin
  WasRunning := CheckForMutexes('') or (FindWindowByWindowName('Scriba') <> 0);
  Result := '';
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  if CurUninstallStep = usPostUninstall then begin
    { the name and icon of its toasts, written by the app itself }
    RegDeleteKeyIncludingSubkeys(HKEY_CURRENT_USER, 'Software\Classes\AppUserModelId\io.github.daniel_g_carrasco.scriba');
  end;
end;
