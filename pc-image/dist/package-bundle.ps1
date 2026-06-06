# Assemble the downloadable GOSE bundle into -Out by copying the canonical pieces into the
# dist layout. Run after a real image build. The committed dist holds only the launcher glue
# + icon; this script pulls in the runtime scripts, portable QEMU, and the image at PACKAGE time.
#
#   powershell -File package-bundle.ps1 -Out C:\path\to\GOSE            # full
#   powershell -File package-bundle.ps1 -Out C:\path\to\GOSE -NoImage   # skip the multi-GB image
param(
  [Parameter(Mandatory)] [string]$Out,
  [string]$QemuBin = "D:\gose-build\msys64\mingw64\bin",
  [string]$HostScripts = "$PSScriptRoot\..\gose-vm-host",
  [string]$ImageGz = "D:\gose-vm\batocera-x86_64-43.1-20260529.img.gz",
  [switch]$NoImage
)
$ErrorActionPreference = 'Stop'
function Step($m){ Write-Host "[package] $m" }

New-Item -ItemType Directory -Force -Path $Out, "$Out\launcher", "$Out\qemu", "$Out\vm" | Out-Null

Step "launcher glue + icon"
Copy-Item "$PSScriptRoot\GOSE.bat","$PSScriptRoot\make-shortcut.ps1","$PSScriptRoot\README.md" $Out -Force
Copy-Item "$PSScriptRoot\launcher\gose-launcher.ps1","$PSScriptRoot\launcher\gose.ico" "$Out\launcher" -Force

Step "runtime scripts from gose-vm-host (canonical source)"
Copy-Item "$HostScripts\boot-gose-vm.ps1","$HostScripts\host_bridge.py" "$Out\launcher" -Force

Step "portable QEMU from $QemuBin"
# NOTE: copies the whole MSYS2 mingw64 bin (over-broad ~hundreds of MB). TODO: trim to the
# qemu-system-x86_64 DLL closure (SDL2, virglrenderer, epoxy, glib, gnutls, usbredir...) for a lean ship.
if (Test-Path $QemuBin) { Copy-Item "$QemuBin\*" "$Out\qemu" -Recurse -Force }
else { Write-Warning "QEMU bin not found at $QemuBin — qemu\ left empty." }

if ($NoImage) {
  Step "SKIP image (-NoImage); place gose-disk.img(.gz) in $Out\vm before shipping."
} elseif (Test-Path $ImageGz) {
  Step "image (compressed) -> vm\gose-disk.img.gz (decompressed on first run by the launcher)"
  Copy-Item $ImageGz "$Out\vm\gose-disk.img.gz" -Force
} else {
  Write-Warning "Image gz not found at $ImageGz — vm\ left empty."
}
Step "done -> $Out  (double-click GOSE.bat)"
