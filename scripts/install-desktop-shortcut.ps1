# Run from PowerShell after installing FRIDAY in the repository's .venv.
# This creates only a shortcut; it does not register startup or background services.
[CmdletBinding()]
param(
    [ValidateRange(-1, 10000)]
    [int]$InputDevice = -1
)

$ErrorActionPreference = 'Stop'
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$pythonw = Join-Path $projectRoot '.venv\Scripts\pythonw.exe'
if (-not (Test-Path -LiteralPath $pythonw -PathType Leaf)) {
    throw "Missing .venv\Scripts\pythonw.exe. Create the virtual environment and install FRIDAY first."
}

$desktop = [Environment]::GetFolderPath([Environment+SpecialFolder]::DesktopDirectory)
if ([string]::IsNullOrWhiteSpace($desktop) -or -not (Test-Path -LiteralPath $desktop)) {
    throw "Cannot resolve the Windows Desktop folder."
}

$shortcutPath = Join-Path $desktop 'FRIDAY.lnk'
$shell = New-Object -ComObject WScript.Shell
$shortcut = $shell.CreateShortcut($shortcutPath)
$shortcut.TargetPath = $pythonw
$shortcut.Arguments = '-m friday desktop'
if ($InputDevice -ge 0) {
    $shortcut.Arguments += " --input-device $InputDevice"
}
$shortcut.WorkingDirectory = $projectRoot
$shortcut.Description = 'FRIDAY voice assistant (starts disconnected)'
$shortcut.IconLocation = Join-Path $projectRoot 'assets\friday.ico'
$shortcut.Save()

Write-Host "Created FRIDAY desktop shortcut: $shortcutPath"
Write-Host 'The shortcut uses pythonw.exe (no PowerShell window).'
Write-Host 'FRIDAY does not auto-connect or start the scheduler.'
