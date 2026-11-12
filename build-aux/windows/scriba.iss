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
; The preflight requests an idle exit and waits for the installed images.
; Keep Restart Manager as the final standard check for files still in use.
CloseApplications=yes
RestartApplications=no

[Languages]
Name: "italian"; MessagesFile: "compiler:Languages\Italian.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[CustomMessages]
italian.AppDescription=Registra una riunione e la trascrive sul tuo computer, con le voci e i sottotitoli
english.AppDescription=Record a meeting and transcribe it on your own computer, with speakers and subtitles
italian.UpdateCloseManually=Chiudi Scriba e riprova. Non è stato possibile confermare che questa istanza sia pronta per un aggiornamento. L'installazione non è iniziata.
english.UpdateCloseManually=Quit Scriba and try again. This instance could not confirm that it is ready for an update. Installation has not started.
italian.UpdateWorkBusy=Scriba sta ancora lavorando. Attendi che finisca e riprova. L'installazione non ha modificato i file.
english.UpdateWorkBusy=Scriba is still working. Let it finish and try again. Setup has not changed the installed files.
italian.UpdateFilesBusy=I file di Scriba sono ancora in uso. Chiudi Scriba e le altre applicazioni che li usano, poi riprova. L'installazione non ha modificato i file.
english.UpdateFilesBusy=Scriba files are still in use. Close Scriba and other applications using them, then try again. Setup has not changed the installed files.

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; Flags: unchecked

[InstallDelete]
; the version before's libraries and modules (PyInstaller's _internal) go
; first: one the new version no longer ships must not stay there and be
; loaded. The preflight and CloseApplications have finished; its settings and
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
const
  GENERIC_READ_WRITE = $C0000000;
  OPEN_EXISTING = 3;
  ERROR_FILE_NOT_FOUND = 2;
  ERROR_PATH_NOT_FOUND = 3;
  SYNCHRONIZE_ACCESS = $00100000;
  PROCESS_QUERY_LIMITED_INFORMATION = $1000;
  WAIT_OBJECT_0 = 0;
  UPDATE_ACCEPTED = 1;
  UPDATE_BUSY = 2;
  PREFLIGHT_TIMEOUT_MS = 30000;

function CreateFileW(Path: String; Access, Share: DWORD; SecurityAttributes: UINT_PTR;
  Disposition, Flags: DWORD; Template: THandle): THandle;
  external 'CreateFileW@kernel32.dll stdcall setuponly';
function CloseNativeHandle(Handle: THandle): BOOL;
  external 'CloseHandle@kernel32.dll stdcall setuponly';
function GetTickCount64(): Int64;
  external 'GetTickCount64@kernel32.dll stdcall setuponly';
function GetWindowThreadProcessId(Window: HWND; var ProcessId: DWORD): DWORD;
  external 'GetWindowThreadProcessId@user32.dll stdcall setuponly';
function OpenUpdateProcess(Access: DWORD; Inherit: BOOL; ProcessId: DWORD): THandle;
  external 'OpenProcess@kernel32.dll stdcall setuponly';
function QueryFullProcessImageNameW(AppHandle: THandle; Flags: DWORD;
  Image: String; var Characters: DWORD): BOOL;
  external 'QueryFullProcessImageNameW@kernel32.dll stdcall setuponly';
function WaitForSingleObject(AppHandle: THandle; WaitMS: DWORD): DWORD;
  external 'WaitForSingleObject@kernel32.dll stdcall setuponly';
function RegisterWindowMessageW(Name: String): UINT;
  external 'RegisterWindowMessageW@user32.dll stdcall setuponly';
function SendMessageTimeoutW(Window: HWND; MessageId: UINT; WParam: UINT_PTR;
  LParam: UINT_PTR; Flags, TimeoutMS: UINT; var Reply: UINT_PTR): UINT_PTR;
  external 'SendMessageTimeoutW@user32.dll stdcall setuponly';

var
  WasRunning: Boolean;

function AppWasRunning(): Boolean;
begin
  Result := WasRunning;
end;

{ Opening an image for reading alone succeeds while it is running. Request
  write access too, but never write: OPEN_EXISTING cannot create or truncate. }
function CanReplaceInstalledFile(Path: String; var ErrorCode: DWORD): Boolean;
var
  FileHandle: THandle;
begin
  ErrorCode := 0;
  FileHandle := CreateFileW(Path, GENERIC_READ_WRITE, 0, 0, OPEN_EXISTING, 0, 0);
  if FileHandle <> THandle(-1) then begin
    CloseNativeHandle(FileHandle);
    Result := True;
  end else begin
    ErrorCode := DLLGetLastError;
    Result := (ErrorCode = ERROR_FILE_NOT_FOUND) or (ErrorCode = ERROR_PATH_NOT_FOUND);
  end;
end;

{ A class or title alone is not authority to close an unrelated application.
  Keep a synchronisation handle to this exact process, not a reusable PID. }
function InstalledWindowProcess(Window: HWND; var AppHandle: THandle): Boolean;
var
  ProcessId, Characters: DWORD;
  Candidate: THandle;
  Image: String;
begin
  Result := False;
  AppHandle := 0;
  ProcessId := 0;
  if GetWindowThreadProcessId(Window, ProcessId) = 0 then Exit;
  Candidate := OpenUpdateProcess(SYNCHRONIZE_ACCESS or PROCESS_QUERY_LIMITED_INFORMATION, False, ProcessId);
  if Candidate = 0 then Exit;
  try
    Characters := 32768;
    SetLength(Image, Characters);
    if not QueryFullProcessImageNameW(Candidate, 0, Image, Characters) then Exit;
    SetLength(Image, Characters);
    if CompareText(ExpandFileName(Image), ExpandFileName(ExpandConstant('{app}\Scriba.exe'))) <> 0 then Exit;
    AppHandle := Candidate;
    Candidate := 0;
    Result := True;
  finally
    if Candidate <> 0 then CloseNativeHandle(Candidate);
  end;
end;

{ Do not use WM_CLOSE here: the application must accept only an idle update.
  Busy is retryable; no response or an older protocol requires manual closure. }
function RequestIdleUpdate(Window: HWND): Integer;
var
  MessageId: UINT;
  Reply: UINT_PTR;
begin
  Result := 0;
  MessageId := RegisterWindowMessageW('io.github.daniel_g_carrasco.scriba.prepare-update.v1');
  if MessageId = 0 then Exit;
  Reply := 0;
  if SendMessageTimeoutW(Window, MessageId, 0, 0, $0001 or $0002, 2000, Reply) = 0 then Exit;
  if Reply = UPDATE_ACCEPTED then Result := UPDATE_ACCEPTED
  else if Reply = UPDATE_BUSY then Result := UPDATE_BUSY;
end;

function WaitForInstalledFiles(AppHandle: THandle; Deadline: Int64): Boolean;
var
  ProcessExited: Boolean;
  AppError, BrokerError: DWORD;
begin
  Result := False;
  repeat
    ProcessExited := (AppHandle = 0);
    if AppHandle <> 0 then
      ProcessExited := WaitForSingleObject(AppHandle, 0) = WAIT_OBJECT_0;
    AppError := 0;
    BrokerError := 0;
    if ProcessExited and
       CanReplaceInstalledFile(ExpandConstant('{app}\Scriba.exe'), AppError) and
       CanReplaceInstalledFile(ExpandConstant('{app}\_internal\gdbus.exe'), BrokerError) then begin
      Log('Update preflight: process exited and installed images are available');
      Result := True;
      Exit;
    end;
    if GetTickCount64() >= Deadline then begin
      Log(Format('Update preflight timed out: process exited=%d, app error=%d, broker error=%d', [Ord(ProcessExited), AppError, BrokerError]));
      Exit;
    end;
    Sleep(100);
  until False;
end;

{ PrepareToInstall runs before InstallDelete or Files. Returning an error
  leaves the installed files in place and keeps normal Restart Manager checks. }
function PrepareToInstall(var NeedsRestart: Boolean): String;
var
  Window, MainWindow: HWND;
  AppHandle: THandle;
  Deadline: Int64;
  Reply: Integer;
  AppError: DWORD;
begin
  Result := '';
  AppHandle := 0;
  Deadline := GetTickCount64() + PREFLIGHT_TIMEOUT_MS;
  try
    Window := FindWindowByClassName('io.github.daniel_g_carrasco.scriba.tray');
    if Window <> 0 then begin
      if not InstalledWindowProcess(Window, AppHandle) then begin
        Result := CustomMessage('UpdateCloseManually');
        Exit;
      end;
      WasRunning := True;
      repeat
        if WaitForSingleObject(AppHandle, 0) = WAIT_OBJECT_0 then Break;
        Reply := RequestIdleUpdate(Window);
        if Reply = UPDATE_ACCEPTED then begin
          Log('Update preflight: application accepted an idle update');
          Break;
        end;
        if Reply <> UPDATE_BUSY then begin
          Result := CustomMessage('UpdateCloseManually');
          Exit;
        end;
        if GetTickCount64() >= Deadline then begin
          Result := CustomMessage('UpdateWorkBusy');
          Exit;
        end;
        Sleep(100);
      until False;
    end else begin
      MainWindow := FindWindowByWindowName('Scriba');
      if MainWindow <> 0 then begin
        if InstalledWindowProcess(MainWindow, AppHandle) then WasRunning := True;
        Result := CustomMessage('UpdateCloseManually');
        Exit;
      end;
      if not CanReplaceInstalledFile(ExpandConstant('{app}\Scriba.exe'), AppError) then begin
        Result := CustomMessage('UpdateCloseManually');
        Exit;
      end;
    end;
    if not WaitForInstalledFiles(AppHandle, Deadline) then
      Result := CustomMessage('UpdateFilesBusy');
  finally
    if AppHandle <> 0 then CloseNativeHandle(AppHandle);
  end;
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  if CurUninstallStep = usPostUninstall then begin
    { the name and icon of its toasts, written by the app itself }
    RegDeleteKeyIncludingSubkeys(HKEY_CURRENT_USER, 'Software\Classes\AppUserModelId\io.github.daniel_g_carrasco.scriba');
  end;
end;
