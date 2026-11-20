; Inno Setup script for the Windows installer.  Built by CI:
;   iscc /DAppVersion=1.0.42 /DSourceDir=dist\Plagiarism /DOutputDir=release packaging\windows\plagcheck.iss
; Installs per user (no administrator rights needed).

#ifndef AppVersion
  #define AppVersion "1.0.0"
#endif
#ifndef SourceDir
  #define SourceDir "..\..\dist\Plagiarism"
#endif
#ifndef OutputDir
  #define OutputDir "..\..\release"
#endif

[Setup]
AppId={{7C1E3B52-6F0B-4C53-9E1D-3A9B7E2D4F10}
AppName=Plagiarism
AppVersion={#AppVersion}
AppVerName=Plagiarism {#AppVersion}
AppPublisher=Artūras Paleičikas
AppPublisherURL=https://paleicikas.github.io/plagiarism/
AppSupportURL=https://github.com/paleicikas/plagiarism/issues
AppUpdatesURL=https://github.com/paleicikas/plagiarism/releases
DefaultDirName={localappdata}\Programs\Plagiarism
DefaultGroupName=Plagiarism
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
OutputDir={#OutputDir}
OutputBaseFilename=Plagiarism-{#AppVersion}-windows-x64-setup
SetupIconFile=..\icons\icon.ico
UninstallDisplayIcon={app}\Plagiarism.exe
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
CloseApplications=yes

[Languages]
Name: "en"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"

[Files]
Source: "{#SourceDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\Plagiarism"; Filename: "{app}\Plagiarism.exe"
Name: "{autodesktop}\Plagiarism"; Filename: "{app}\Plagiarism.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\Plagiarism.exe"; Description: "{cm:LaunchProgram,Plagiarism}"; Flags: nowait postinstall skipifsilent

[InstallDelete]
; remove files of the previous version (the folder layout of a PyInstaller build changes between versions)
Type: filesandordirs; Name: "{app}\_internal"
