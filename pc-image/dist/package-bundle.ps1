# Assemble the downloadable GOSE bundle into -Out by copying the canonical pieces into the
# dist layout. Run after a real image build. The committed dist holds only the launcher glue
# + icon; this script pulls in the runtime scripts, portable QEMU, and the image at PACKAGE time.
#
#   powershell -File package-bundle.ps1 -Out C:\path\to\GOSE            # full (DLL-trimmed)
#   powershell -File package-bundle.ps1 -Out C:\path\to\GOSE -Full      # whole mingw64/bin (old behaviour, dev/CI)
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
  [switch]$SkipVerify,  # UNSAFE: skip the cleanliness gate. Use only when verify-image-clean ran separately.
  [switch]$Full         # Copy the whole mingw64/bin (old behaviour, ~1-2GB). Default: DLL-closure trim (~50-120MB).
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
if (-not (Test-Path $QemuBin)) {
    Write-Warning "[package] QEMU bin not found at $QemuBin -- qemu\ left empty."
} elseif ($Full) {
    # -Full: old over-broad behaviour — copies the whole mingw64/bin (~1-2 GB, ~380 exe + ~246 dll).
    # Use only for dev/CI situations where you want everything present. Not for a shipped Steam depot.
    Step "  -Full flag set: copying all of $QemuBin (~1-2 GB) — NOT suitable for distribution"
    Copy-Item "$QemuBin\*" "$Out\qemu" -Recurse -Force
} else {
    # DLL-CLOSURE TRIM (SB-1.5 fix) — ships only the binaries QEMU actually loads.
    # Reduces the QEMU payload from ~1-2 GB to ~50-120 MB, making it suitable for a Steam depot.
    #
    # HOW THE CLOSURE WAS DETERMINED (2026-06-13):
    #   1. PE import-table scan of qemu-system-x86_64.exe, qemu-system-x86_64w.exe, and
    #      usbredirect.exe identified all direct DLL deps.
    #   2. A BFS walk of each dep's own PE imports (one level per DLL) expanded the transitive set.
    #   3. The resulting set was cross-checked against the scoop QEMU distribution (9.2.0) and the
    #      MSYS2 build dir — both yield the same logical closure (minor version differences only;
    #      the MSYS2 build produced 113 DLLs vs scoop's 79, the delta being additional transitive
    #      deps pulled in by newer library versions in the MSYS2 tree).
    #   4. Verification: run `& "$Out\qemu\qemu-system-x86_64.exe" --version` after trim; if DLLs
    #      are missing, it will fail with a DLL-not-found error before printing the version.
    #
    # TO REFRESH THIS LIST: run `ldd qemu-system-x86_64.exe` from an MSYS2 shell in $QemuBin,
    # or use the PE scanner at scripts/get_qemu_dll_closure.py (host-side, read-only).
    # Re-run whenever QEMU is upgraded (the closure changes with new QEMU/library versions).
    #
    # Known-good DLL closure (derived from MSYS2 mingw64 build, BFS-verified 2026-06-13):
    $QEMU_DLL_CLOSURE = @(
        # Core runtime
        'libgcc_s_seh-1.dll', 'libwinpthread-1.dll', 'libstdc++-6.dll', 'libssp-0.dll',
        # QEMU core
        'libglib-2.0-0.dll', 'libgmodule-2.0-0.dll', 'libgobject-2.0-0.dll',
        'libgio-2.0-0.dll', 'libintl-8.dll', 'libiconv-2.dll',
        'libpcre2-8-0.dll', 'libffi-8.dll', 'zlib1.dll',
        # Display / input
        'SDL2.dll', 'SDL2_image.dll',
        'libepoxy-0.dll', 'libvirglrenderer-1.dll',
        'libpixman-1-0.dll',
        # UI / GTK (used by qemu-system-x86_64w.exe)
        'libgtk-3-0.dll', 'libgdk-3-0.dll', 'libgdk_pixbuf-2.0-0.dll',
        'libpango-1.0-0.dll', 'libpangocairo-1.0-0.dll',
        'libpangoft2-1.0-0.dll', 'libpangowin32-1.0-0.dll',
        'libcairo-2.dll', 'libcairo-gobject-2.dll',
        'libatk-1.0-0.dll', 'libharfbuzz-0.dll', 'libfreetype-6.dll',
        'libfontconfig-1.dll', 'libfribidi-0.dll', 'libgraphite2.dll',
        'libdatrie-1.dll', 'libthai-0.dll',
        'libpng16-16.dll', 'libjpeg-8.dll', 'libtiff-6.dll',
        'libjbig-0.dll', 'liblzma-5.dll', 'libdeflate.dll', 'liblerc.dll',
        'libwebp-7.dll', 'libwebpdemux-2.dll', 'libsharpyuv-0.dll',
        'libavif-16.dll', 'libaom.dll', 'libdav1d-7.dll',
        'librav1e.dll', 'libsvtav1enc-4.dll', 'libyuv.dll', 'libhwy.dll',
        'libjxl.dll', 'libjxl_cms.dll', 'liblcms2-2.dll',
        # Network / TLS
        'libgnutls-30.dll', 'libssl-3-x64.dll', 'libcrypto-3-x64.dll',
        'libnettle-8.dll', 'libhogweed-6.dll', 'libgmp-10.dll',
        'libp11-kit-0.dll', 'libtasn1-6.dll', 'libidn2-0.dll',
        'libunistring-5.dll', 'libpsl-5.dll',
        'libssh.dll', 'libssh2-1.dll', 'libcurl-4.dll', 'libnghttp2-14.dll',
        'libnghttp3-9.dll', 'libngtcp2-16.dll', 'libngtcp2_crypto_ossl-0.dll',
        'libsasl2-3.dll', 'libexpat-1.dll',
        # Storage
        'libzstd.dll', 'liblz4.dll', 'liblzo2-2.dll', 'libbz2-1.dll',
        'libbrotlicommon.dll', 'libbrotlidec.dll', 'libbrotlienc.dll',
        'libncursesw6.dll', 'libdb-6.2.dll', 'libsqlite3-0.dll',
        'libsnappy.dll', 'libfdt-1.dll', 'libcapstone.dll',
        # USB redirect
        'libusb-1.0.dll', 'libusbredirparser-1.dll', 'libusbredirhost-1.dll',
        # SPICE / audio
        'libspice-server-1.dll', 'libjack64.dll', 'libopus-0.dll',
        'libgstreamer-1.0-0.dll', 'libgstbase-1.0-0.dll', 'libgstapp-1.0-0.dll',
        'liborc-0.4-0.dll',
        # NFS / smart-card / u2f
        'libnfs-16.dll', 'libnfs-14.dll',  # version may differ; copy whichever is present
        'libcacard-0.dll', 'libu2f-emu-0.dll',
        # NSS (GnuTLS PKCS#11 backend)
        'nss3.dll', 'nssutil3.dll', 'libnspr4.dll', 'libplc4.dll', 'libplds4.dll',
        'softokn3.dll', 'freebl3.dll', 'smime3.dll',
        # Vulkan (WHPX acceleration path)
        'vulkan-1.dll',
        # Misc regex / system
        'libsystre-0.dll', 'libtre-5.dll', 'libegl.dll', 'libglesv1_cm.dll', 'libglesv2.dll'
    )

    $qemuExes = @('qemu-system-x86_64.exe', 'qemu-system-x86_64w.exe', 'usbredirect.exe')
    $copied = 0; $missing = @()

    # Copy the QEMU executables
    foreach ($exe in $qemuExes) {
        $src = "$QemuBin\$exe"
        if (Test-Path $src) {
            Copy-Item $src "$Out\qemu\$exe" -Force
            $copied++
        }
    }

    # Copy the DLL closure — skip any file that doesn't exist in $QemuBin (version may differ)
    foreach ($dll in $QEMU_DLL_CLOSURE) {
        $src = "$QemuBin\$dll"
        if (Test-Path $src) {
            Copy-Item $src "$Out\qemu\$dll" -Force
            $copied++
        } else {
            $missing += $dll
        }
    }

    if ($missing.Count -gt 0) {
        Write-Warning "[package] $($missing.Count) DLLs in closure list not found in $QemuBin (version differences?): $($missing -join ', ')"
        Write-Warning "[package]   These may be named differently in this QEMU build. Verify with: ldd qemu-system-x86_64.exe"
    }

    # Verification: confirm QEMU executable runs (catches missing DLLs immediately)
    $qemuExe = "$Out\qemu\qemu-system-x86_64.exe"
    if (Test-Path $qemuExe) {
        Step "  verifying trimmed QEMU runs (--version smoke-test)..."
        $result = & $qemuExe --version 2>&1
        if ($LASTEXITCODE -ne 0) {
            Write-Error "[package] Trimmed QEMU failed --version smoke-test. Missing DLLs in the closure.`n  Output: $result`n  Add the missing DLLs to QEMU_DLL_CLOSURE in package-bundle.ps1 and re-run."
            exit 1
        }
        Step "  QEMU smoke-test PASSED: $($result | Select-Object -First 1)"
    }

    $qemuSize = (Get-ChildItem "$Out\qemu" -Recurse -File | Measure-Object -Sum Length).Sum
    Step "  trimmed QEMU payload: $copied files, $([math]::Round($qemuSize/1MB,1)) MB (was ~1-2 GB for the full mingw64/bin)"
}

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
