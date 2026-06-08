# verify-image-clean.ps1 -- GOSE pre-package image cleanliness gate (Task #91)
#
# USAGE
#   powershell -File verify-image-clean.ps1 -ImageGz <path-to.img.gz>         # check-only
#   powershell -File verify-image-clean.ps1 -ImageGz <path-to.img.gz> -Scrub  # check + scrub bad files
#
# WHAT IT DOES
#   Mounts the image via WSL loop-mount and ASSERTS the image is clean for shipping:
#
#   ABSENCE checks -- packaging FAILS CLOSED if any of these exist:
#     /userdata/system/gose/.oobe-done       (OOBE complete flag -> boots into dev account)
#     /userdata/system/gose/accounts.json    (user account store)
#     /userdata/system/gose/token            (owner agent-admin token)
#     /userdata/system/gose/ai_tokens.json   (per-AI provider keys)
#     /userdata/system/gose/ai_grants.json   (AI permission grants)
#     /userdata/gose-ui/ai_grants.json       (grants copy in UI dir)
#     /userdata/gose-ui/ai_requests.json     (pending permission requests)
#     /userdata/gose-ui/ai_players.json      (AI player registry)
#     /userdata/gose-ui/recent.json          (per-user recents)
#     /userdata/gose-ui/favorites.json       (per-user favorites)
#     /userdata/gose-ui/playtime.json        (per-user playtime)
#     /userdata/gose-ui/storage_offers.json  (runtime state)
#     /userdata/gose-ui/scrape_state.json    (runtime state)
#
#   HARDENING checks -- packaging FAILS CLOSED if any of these are missing/wrong:
#     system.ssh.enabled=0      in batocera.conf  (SSH must be OFF)
#     system.security.enabled=1 in batocera.conf  (random root pw + iptables)
#     system.samba.enabled=0    in batocera.conf  (Samba must be OFF)
#
# SCRUB MODE (-Scrub)
#   Safety net for the fallback case where a dev-captured disk must be shipped.
#   Works on a COPY of the image -- NEVER mutates D:\gose-vm\ or any original.
#
# PREFERRED PATH
#   Run build-gose-pc.sh first. Its output (build/gose-pc-x86_64.img.gz) will
#   have NONE of the cred files and WILL have the hardening from batocera.conf.gose.
#   Then verify + package:
#
#     powershell -File pc-image\verify-image-clean.ps1 -ImageGz pc-image\build\gose-pc-x86_64.img.gz
#     powershell -File pc-image\dist\package-bundle.ps1 -Out C:\GOSE-dist
#
# REQUIREMENTS
#   WSL2 (Ubuntu or similar) with losetup/mount/gzip -- standard on WSL2.
#   Must be run as Administrator (loop mounts need root inside WSL).

param(
  [Parameter(Mandatory)] [string]$ImageGz,
  [switch]$Scrub,
  [switch]$Quiet
)
$ErrorActionPreference = 'Stop'

function Log($m)  { if (-not $Quiet) { Write-Host "[verify-clean] $m" } }
function Fail($m) { Write-Error "[verify-clean] FAIL: $m"; exit 1 }

# ---- validate input ---------------------------------------------------------
if (-not (Test-Path $ImageGz)) {
    Fail ("Image not found: " + $ImageGz + "`n       Run build-gose-pc.sh first to produce the clean build output.")
}

$imgGzAbs = (Resolve-Path $ImageGz).Path

# Warn loudly if the source is the live dev disk.
if ($imgGzAbs -like "*gose-vm*") {
    Write-Warning "[verify-clean] WARNING: source is the dev disk ($imgGzAbs)."
    Write-Warning "              The dev disk contains owner credentials and SSH enabled."
    Write-Warning "              PREFERRED: run build-gose-pc.sh and use build/gose-pc-x86_64.img.gz."
    if (-not $Scrub) {
        Fail ("Refusing to verify-and-pass the dev disk without -Scrub. " +
              "Add -Scrub to scrub a COPY, or use the clean build output.")
    }
}

# ---- working copy setup -----------------------------------------------------
$tmpDir = Join-Path $env:TEMP ("gose-verify-" + [System.Guid]::NewGuid().ToString("N").Substring(0,8))
New-Item -ItemType Directory -Force -Path $tmpDir | Out-Null

$workImg = Join-Path $tmpDir "work.img"

# ---- confirm WSL is available -----------------------------------------------
$wslOk = $false
try {
    $wslOut = wsl --list --quiet 2>$null
    $wslOk = ($wslOut -ne $null -and $wslOut.Count -gt 0)
} catch {}
if (-not $wslOk) {
    Write-Warning "[verify-clean] WSL not found or no distro -- loop-mount checks require WSL2."
    Write-Warning "              Install WSL2 (Ubuntu) for full verification."
    if ($imgGzAbs -like "*gose-vm*") {
        Fail "Dev-disk source + no WSL = cannot verify. Use the clean build output or run from WSL."
    }
    Write-Warning "              Non-dev-disk source accepted without full mount inspection (WSL unavailable)."
    Write-Warning "              Run this script from WSL for the definitive check before shipping."
    Remove-Item -Recurse -Force $tmpDir -ErrorAction SilentlyContinue
    exit 0
}

# ---- decompress to temp img -------------------------------------------------
Log ("Decompressing " + $imgGzAbs + " -> " + $workImg + " ...")
$wslWorkImg  = (wsl wslpath -u "$workImg") -replace "`r",""
$wslSrcGz    = (wsl wslpath -u "$imgGzAbs") -replace "`r",""

wsl bash -c "gunzip -c '$wslSrcGz' > '$wslWorkImg'"
if ($LASTEXITCODE -ne 0) {
    Remove-Item -Recurse -Force $tmpDir -ErrorAction SilentlyContinue
    Fail "gunzip failed (exit $LASTEXITCODE)."
}

# ---- mount the userdata partition (p2) via WSL loop -------------------------
$wslMnt  = "/tmp/gose-verify-mnt-$$"
$loopDev = $null
$exitCode = 0

try {
    Log "Loop-mounting userdata partition (p2) ..."
    $loopOut = (wsl bash -c "losetup --show -fP '$wslWorkImg' 2>&1") -replace "`r",""
    if ($LASTEXITCODE -ne 0 -or $loopOut -notlike "/dev/loop*") {
        Fail "losetup failed: $loopOut"
    }
    $loopDev = $loopOut

    wsl bash -c "mkdir -p $wslMnt && mount ${loopDev}p2 $wslMnt" 2>&1
    if ($LASTEXITCODE -ne 0) {
        Fail "mount failed for ${loopDev}p2."
    }

    # ---- ABSENCE checks -----------------------------------------------------
    $credFiles = @(
        "system/gose/.oobe-done",
        "system/gose/accounts.json",
        "system/gose/token",
        "system/gose/ai_tokens.json",
        "system/gose/ai_grants.json",
        "gose-ui/ai_grants.json",
        "gose-ui/ai_requests.json",
        "gose-ui/ai_players.json",
        "gose-ui/recent.json",
        "gose-ui/favorites.json",
        "gose-ui/playtime.json",
        "gose-ui/storage_offers.json",
        "gose-ui/scrape_state.json"
    )

    $found = @()
    foreach ($rel in $credFiles) {
        $guestPath = "$wslMnt/$rel"
        $exists = (wsl bash -c "test -e '$guestPath' && echo yes || echo no") -replace "`r",""
        if ($exists -eq "yes") { $found += $rel }
    }

    if ($found.Count -gt 0) {
        if ($Scrub) {
            Log ("Scrubbing " + $found.Count + " cred/state file(s):")
            foreach ($rel in $found) {
                Log "  rm $rel"
                wsl bash -c "rm -f '$wslMnt/$rel'" 2>&1
            }
        } else {
            foreach ($rel in $found) {
                Write-Warning "[verify-clean]   PRESENT (must be absent): $rel"
            }
            $exitCode = 1
        }
    } else {
        Log "Absence check: PASS (no cred/OOBE/state files found)."
    }

    # ---- HARDENING checks ---------------------------------------------------
    $confPath = "$wslMnt/system/batocera.conf"
    $confExists = (wsl bash -c "test -f '$confPath' && echo yes || echo no") -replace "`r",""
    if ($confExists -ne "yes") {
        Write-Warning "[verify-clean]   batocera.conf not found -- cannot verify hardening."
        $exitCode = 1
    } else {
        $hardeningKeys = @{
            "system.ssh.enabled"      = "0"
            "system.security.enabled" = "1"
            "system.samba.enabled"    = "0"
        }

        $hardenFail = @()
        foreach ($kv in $hardeningKeys.GetEnumerator()) {
            $key  = $kv.Key
            $want = $kv.Value
            $actual = (wsl bash -c "grep -E '^${key}=' '$confPath' | tail -1 | cut -d= -f2") -replace "`r",""
            if ($actual -ne $want) {
                $hardenFail += ($key + "=" + $want + "  (found: '" + $key + "=" + $actual + "')")
            }
        }

        if ($hardenFail.Count -gt 0) {
            if ($Scrub) {
                Log "Applying hardening to batocera.conf:"
                foreach ($kv in $hardeningKeys.GetEnumerator()) {
                    $key  = $kv.Key
                    $want = $kv.Value
                    wsl bash -c "sed -i '/^${key}=/d' '$confPath'; echo '${key}=${want}' >> '$confPath'" 2>&1
                    Log ("  set " + $key + "=" + $want)
                }
            } else {
                foreach ($line in $hardenFail) {
                    Write-Warning "[verify-clean]   HARDENING MISSING: $line"
                }
                $exitCode = 1
            }
        } else {
            Log "Hardening check: PASS (ssh off, security on, samba off)."
        }
    }

    if ($exitCode -ne 0 -and -not $Scrub) {
        # Report collected failures and exit non-zero after cleanup.
        Write-Error ("[verify-clean] FAIL: image has cred/hardening problems. " +
                     "Use the clean build output or re-run with -Scrub.")
    }

} finally {
    # ---- unmount + cleanup --------------------------------------------------
    if ($loopDev) {
        wsl bash -c "umount $wslMnt 2>/dev/null; losetup -d '$loopDev' 2>/dev/null; rm -rf $wslMnt" 2>&1
    }

    if ($Scrub -and (Test-Path $tmpDir)) {
        # Re-compress the scrubbed image.
        $wslCleanGz  = (wsl wslpath -u (Join-Path $tmpDir "clean.img.gz")) -replace "`r",""
        Log "Re-compressing scrubbed image ..."
        wsl bash -c "gzip -c '$wslWorkImg' > '$wslCleanGz'" 2>&1
        $scrubOut = Join-Path (Split-Path $imgGzAbs -Parent) ("scrubbed-" + (Split-Path $imgGzAbs -Leaf))
        Copy-Item (Join-Path $tmpDir "clean.img.gz") $scrubOut -Force
        Log "Scrubbed output: $scrubOut"
        Log "Verify the scrubbed copy before packaging:"
        Log ('  powershell -File verify-image-clean.ps1 -ImageGz ' + $scrubOut)
    }

    Remove-Item -Recurse -Force $tmpDir -ErrorAction SilentlyContinue
}

if ($exitCode -ne 0) { exit $exitCode }

# ---- final verdict ----------------------------------------------------------
Log "CLEAN: image passed all absence + hardening checks. Safe to package."
Log ('  Next: powershell -File dist\package-bundle.ps1 -Out PATH')
exit 0
