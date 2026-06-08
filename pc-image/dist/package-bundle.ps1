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
# NOTE: copies the WHOLE MSYS2 mingw64 bin (~680 files, hundreds of MB) — intentionally over-broad.
# TODO: trim to the actual qemu-system-x86_64.exe DLL closure for a lean ship. Verified-safe to DROP:
#   - All other QEMU emulators: qemu-system-aarch64*, qemu-system-arm*, qemu-system-i386*, etc.
#     (GOSE only needs qemu-system-x86_64.exe + its paired qemu-system-x86_64w.exe GUI variant)
#   - Other QEMU utilities: qemu-edid.exe, qemu-ga.exe, qemu-nbd.exe, qemu-storage-daemon.exe, etc.
#   - Non-QEMU executables: the ~600 other .exe/.py/.sh tools in mingw64\bin (adig, avif*, bzip2,
#     glib-compile-schemas, gnutls-cli, lz4, ffmpeg, python, etc.) — none are invoked by the launcher.
# The DLL closure that MUST ship (run `ldd qemu-system-x86_64.exe` on the MSYS2 side to verify):
#   SDL2.dll, libvirglrenderer-1.dll, libepoxy-0.dll, libglib-2.0-0.dll, libgnutls-30.dll,
#   libusbredirhost-1.dll, libusbredirparser-1.dll, libpixman-1-0.dll, libffi-8.dll, libiconv-2.dll,
#   libpcre2-8-0.dll, libzstd.dll, liblzma-5.dll, liblz4.dll, liblzo2-2.dll, libp11-kit-0.dll.
# Safe implementation: copy only qemu-system-x86_64*.exe + usbredirect.exe + all *.dll from $QemuBin.
# DO NOT trim blindly — confirm the closure with ldd/Dependency Walker before shipping a trimmed bundle.
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
