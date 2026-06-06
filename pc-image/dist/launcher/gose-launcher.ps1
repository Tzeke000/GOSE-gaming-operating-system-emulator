# GOSE distribution launcher - "double-click GOSE, it boots in its own VM".
# Reuses boot-gose-vm.ps1 (the WORKING virgl/audio/BT/bridge boot, 2026-06-06); this
# script only orchestrates: find pieces -> provision if first run -> boot or focus ->
# wait for the agent -> bring the GOSE window forward. It NEVER touches the host OS;
# everything runs inside the QEMU VM (its own sandbox). No admin required to launch.
#
# Path resolution prefers BUNDLE-relative layout (a downloaded GOSE folder), then falls
# back to the dev box. Bundle layout (see ..\README.md):
#   GOSE\
#     GOSE.bat                  <- double-click entry
#     launcher\gose-launcher.ps1 (this) + boot-gose-vm.ps1 + host_bridge.py + gose.ico
#     qemu\                     <- portable QEMU (mingw64 bin: qemu-system-x86_64.exe + DLLs)
#     vm\gose-disk.img          <- the GOSE VM disk (or gose-disk.img.gz to decompress on first run)
$ErrorActionPreference = 'Stop'
$ProgressPreference    = 'SilentlyContinue'

$LauncherDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$BundleRoot  = Split-Path -Parent $LauncherDir   # the GOSE\ folder in a real download

function Say([string]$m) { Write-Host "[GOSE] $m" }

# Bring the GOSE VM window to the foreground by PID (pure PowerShell via the WSH shell -
# no P/Invoke). AppActivate is best-effort; a miss is non-fatal (the window still opened).
function Focus-Gose([int]$ProcId) {
  try { (New-Object -ComObject WScript.Shell).AppActivate($ProcId) | Out-Null } catch { }
}

function Resolve-First([string[]]$candidates) {
  foreach ($c in $candidates) { if ($c -and (Test-Path -LiteralPath $c)) { return (Resolve-Path -LiteralPath $c).Path } }
  return $null
}

# --- locate the pieces (bundle-relative first, dev box second) ---
$BootScript = Resolve-First @("$LauncherDir\boot-gose-vm.ps1", "D:\gose-vm\boot-gose-vm.ps1")
$QemuBin    = Resolve-First @("$BundleRoot\qemu", "D:\gose-build\msys64\mingw64\bin")
$Bridge     = Resolve-First @("$LauncherDir\host_bridge.py", "D:\gose-vm\host_bridge.py")
$Image      = Resolve-First @("$BundleRoot\vm\gose-disk.img", "D:\gose-vm\batocera-x86_64-43.1-20260529.img")
$ImageGz    = Resolve-First @("$BundleRoot\vm\gose-disk.img.gz")

if (-not $BootScript) { Say "FATAL: boot-gose-vm.ps1 not found next to launcher or on the dev box."; pause; exit 1 }
if (-not $QemuBin -or -not (Test-Path "$QemuBin\qemu-system-x86_64.exe")) {
  Say "FATAL: portable QEMU not found (looked in .\qemu and the dev MSYS2 bin)."; pause; exit 1
}

# --- already running? focus it instead of double-booting ---
$existing = Get-Process qemu-system-x86_64 -ErrorAction SilentlyContinue
if ($existing) {
  Say "GOSE is already running - bringing its window to the front."
  Focus-Gose $existing[0].Id
  exit 0
}

# --- first-run provisioning: if the disk is missing but a .gz ships, decompress it ---
if (-not $Image -and $ImageGz) {
  Say "First run: provisioning the GOSE disk image (decompressing, one time)..."
  $dest = Join-Path (Split-Path -Parent $ImageGz) "gose-disk.img"
  $in = [System.IO.File]::OpenRead($ImageGz)
  $out = [System.IO.File]::Create($dest)
  $gz = New-Object System.IO.Compression.GZipStream($in, [System.IO.Compression.CompressionMode]::Decompress)
  try { $gz.CopyTo($out) } finally { $gz.Dispose(); $out.Dispose(); $in.Dispose() }
  $Image = (Resolve-Path -LiteralPath $dest).Path
  Say "Provisioned: $Image"
}
if (-not $Image) {
  Say "FATAL: no GOSE disk image (.\vm\gose-disk.img or .img.gz). A real download ships one here."; pause; exit 1
}

# --- boot via the reused script, pointed at our pieces (env overrides; defaults unchanged for the dev box) ---
Say "Starting GOSE...  (image: $(Split-Path -Leaf $Image))"
$env:GOSE_QEMU_BIN = $QemuBin
$env:GOSE_IMAGE    = $Image
if ($Bridge) { $env:GOSE_BRIDGE = $Bridge }
& powershell -NoProfile -ExecutionPolicy Bypass -File $BootScript -Image $Image -QemuBin $QemuBin | Out-Host

# --- wait for the in-VM agent (token-auth TCP on 8731) to answer, then surface the window ---
Say "Waiting for GOSE to come up..."
$up = $false
for ($i = 0; $i -lt 90; $i++) {
  try {
    $c = New-Object Net.Sockets.TcpClient
    $c.Connect('127.0.0.1', 8731)
    if ($c.Connected) { $up = $true; $c.Close(); break }
  } catch { }
  Start-Sleep -Milliseconds 1000
}

$qproc = Get-Process qemu-system-x86_64 -ErrorAction SilentlyContinue | Select-Object -First 1
if ($qproc) { Focus-Gose $qproc.Id }

if ($up) { Say "GOSE is up. The VM window is yours - enjoy." }
else     { Say "GOSE window launched; the agent did not answer in 90s (the OS may still be booting)." }
