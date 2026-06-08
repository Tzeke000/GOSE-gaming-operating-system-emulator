# Assemble the downloadable GOSE bundle into -Out by copying the canonical pieces into the
# dist layout. Run after a real image build. The committed dist holds only the launcher glue
# + icon; this script pulls in the runtime scripts, portable QEMU, and the image at PACKAGE time.
#
#   powershell -File package-bundle.ps1 -Out C:\path\to\GOSE            # full
#   powershell -File package-bundle.ps1 -Out C:\path\to\GOSE -NoImage   # skip the multi-GB image
#   powershell -File package-bundle.ps1 -Out C:\path\to\GOSE -SkipVerify # skip cleanliness gate (UNSAFE — dev/CI only)
#
# IMAGE SOURCE (Task #91)
#   -ImageGz now defaults to the CLEAN BUILD output (pc-image/build/gose-pc-x86_64.img.gz),
#   NOT the hand-built dev disk at D:\gose-vm\. This is correct: the dev disk has SSH on,
#   the owner token, and .oobe-done baked in — see docs/33 and the pre-mortem (#91).
#
#   BEFORE PACKAGING: run build-gose-pc.sh on a Linux host to produce the clean image.
#   The build bakes the full GOSE shell from repo sources (no dev-disk copy) and the
#   hardened batocera.conf.gose (SSH off, security on, Samba off) — docs/32.
#
#   If you must ship a dev-captured disk (fallback), pass -ImageGz explicitly AND run
#   ..\verify-image-clean.ps1 -ImageGz <path> -Scrub first to produce a scrubbed copy,
#   then pass THAT path here. Never pass the D:\gose-vm\ disk without scrubbing.
#
# CLEANLINESS GATE (fail-closed)
#   Before copying the image, this script invokes verify-image-clean.ps1 to assert:
#     - no cred/OOBE files in the image (/userdata/system/gose/token, .oobe-done, etc.)
#     - batocera.conf has SSH off + security on + Samba off
#   Packaging FAILS if the gate fails. Use -SkipVerify only in CI where the verify step
#   ran separately and the image is already confirmed clean.
param(
  [Parameter(Mandatory)] [string]$Out,
  [string]$QemuBin = "D:\gose-build\msys64\mingw64\bin",
  [string]$HostScripts = "$PSScriptRoot\..\gose-vm-host",
  # Default: clean build output from build-gose-pc.sh. Override only to pass a scrubbed
  # copy of a dev-captured disk (see docs/33). Do NOT use D:\gose-vm\*.img.gz directly.
  [string]$ImageGz = "$PSScriptRoot\..\build\gose-pc-x86_64.img.gz",
  [switch]$NoImage,
  [switch]$SkipVerify  # UNSAFE: skip the cleanliness gate. Use only when verify-image-clean ran separately.
)
$ErrorActionPreference = 'Stop'
function Step($m){ Write-Host "[package] $m" }

# ---- cleanliness gate (Task #91) --------------------------------------------
if ($NoImage) {
    Step "SKIP image (-NoImage); skipping cleanliness gate — verify manually before shipping."
} elseif ($SkipVerify) {
    Write-Warning "[package] -SkipVerify set — cleanliness gate BYPASSED. Ensure verify-image-clean.ps1 was run separately."
} else {
    $verifyScript = "$PSScriptRoot\..\verify-image-clean.ps1"
    if (-not (Test-Path $verifyScript)) {
        Write-Error "[package] verify-image-clean.ps1 not found at $verifyScript — cannot gate image cleanliness."
        exit 1
    }
    if (-not (Test-Path $ImageGz)) {
        Write-Error "[package] Image not found: $ImageGz`n  Run build-gose-pc.sh on a Linux host first."
        exit 1
    }
    Step "cleanliness gate: verify-image-clean.ps1 -ImageGz $ImageGz"
    & powershell -NonInteractive -File $verifyScript -ImageGz $ImageGz
    if ($LASTEXITCODE -ne 0) {
        Write-Error "[package] Cleanliness gate FAILED (exit $LASTEXITCODE). Packaging aborted — image is NOT safe to ship."
        exit 1
    }
    Step "cleanliness gate: PASSED"
}
# -----------------------------------------------------------------------------

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
else { Write-Warning "QEMU bin not found at $QemuBin -- qemu\ left empty." }

if ($NoImage) {
  Step "SKIP image (-NoImage); place gose-disk.img.gz in $Out\vm before shipping."
} elseif (Test-Path $ImageGz) {
  Step "image (clean build) -> vm\gose-disk.img.gz (decompressed on first run by the launcher)"
  Copy-Item $ImageGz "$Out\vm\gose-disk.img.gz" -Force
} else {
  # Fail-closed: if the clean build image is absent, refuse to produce an
  # incomplete bundle silently. The caller must either run build-gose-pc.sh
  # first or pass -NoImage explicitly.
  Write-Error "[package] Image not found: $ImageGz`n  Run build-gose-pc.sh on a Linux host to produce the clean build output,`n  OR pass -NoImage to assemble the bundle without the image (manual placement needed)."
  exit 1
}
Step "done -> $Out  (double-click GOSE.bat)"
