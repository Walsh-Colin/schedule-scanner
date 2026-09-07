#define AppVersion "1.0.0"
[Setup]
AppId={{1B47F83B-118B-4B26-A177-84FF2D79AE21}
AppName=Schedule Scanner
AppVersion={#AppVersion}
DefaultDirName={localappdata}\Programs\Schedule Scanner
DefaultGroupName=Schedule Scanner
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
DisableWelcomePage=no
DisableDirPage=no
OutputDir=..\release
OutputBaseFilename=ScheduleScanner-Setup-v{#AppVersion}
SetupIconFile=..\schedule_scanner\images\CalGen.ico
UninstallDisplayIcon={app}\ScheduleScanner.exe
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
InfoBeforeFile=setup-info.txt

[Tasks]
Name: desktopicon; Description: "Create a desktop shortcut"; Flags: unchecked

[Files]
Source: "..\dist\ScheduleScanner\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\Schedule Scanner"; Filename: "{app}\ScheduleScanner.exe"
Name: "{autodesktop}\Schedule Scanner"; Filename: "{app}\ScheduleScanner.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\ScheduleScanner.exe"; Parameters: "--setup"; StatusMsg: "Setting up the AI reader. Complete the download in the setup window."; Flags: waituntilterminated skipifsilent
Filename: "{app}\ScheduleScanner.exe"; Description: "Launch Schedule Scanner"; Flags: nowait postinstall skipifsilent
