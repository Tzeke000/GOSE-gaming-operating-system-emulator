# Create a "GOSE" desktop shortcut (.lnk) that double-click-launches GOSE.bat with the
# GOSE Core icon. No admin needed. Re-runnable (overwrites the existing shortcut).
# Usage:  powershell -NoProfile -ExecutionPolicy Bypass -File make-shortcut.ps1 [-Dest <folder>]
param(
  [string]$Dest = [Environment]::GetFolderPath('Desktop')
)
$ErrorActionPreference = 'Stop'
$here   = Split-Path -Parent $MyInvocation.MyCommand.Path
$bat    = Join-Path $here 'GOSE.bat'
$icon   = Join-Path $here 'launcher\gose.ico'
if (-not (Test-Path $bat))  { throw "GOSE.bat not found at $bat" }
if (-not (Test-Path $icon)) { throw "icon not found at $icon" }

$lnkPath = Join-Path $Dest 'GOSE.lnk'
$ws = New-Object -ComObject WScript.Shell
$sc = $ws.CreateShortcut($lnkPath)
$sc.TargetPath       = $bat
$sc.WorkingDirectory = $here
$sc.IconLocation     = "$icon,0"
$sc.Description      = 'Start GOSE — the AI-drivable gaming OS, in its own VM'
$sc.WindowStyle      = 7   # minimized console; the GOSE VM window is what the user sees
$sc.Save()
Write-Host "Created shortcut: $lnkPath"
Write-Host "  -> target: $bat"
Write-Host "  -> icon:   $icon"
