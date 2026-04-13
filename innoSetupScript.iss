[Setup]
AppName=Music Grabber
AppVersion=1.0.0
DefaultDirName={autopf}\MusicGrabber
DefaultGroupName=TeamGrab
UninstallDisplayIcon={app}\MusicGrabber.exe
Compression=lzma
SolidCompression=yes
OutputDir=userdocs:Inno Setup Outputs
OutputBaseFilename=MusicGrabber_Setup_v1.0.0

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
Source: "D:\Development\Music Grabber\dist\MusicGrabber.exe"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\Music Grabber"; Filename: "{app}\MusicGrabber.exe"
Name: "{autodesktop}\Music Grabber"; Filename: "{app}\MusicGrabber.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\MusicGrabber.exe"; Description: "{cm:LaunchProgram,Music Grabber}"; Flags: nowait postinstall skipifsilent