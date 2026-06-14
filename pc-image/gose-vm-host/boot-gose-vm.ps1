# Boot the GOSE-PC VM with GPU rendering (virgl) — the WORKING setup, 2026-06-04.
# Renders Batocera/EmulationStation through the host GPU. No admin needed.
# Key: use MSYS2's qemu (has virglrenderer + ANGLE) + gl=on (DESKTOP GL).
#   gl=es (ANGLE GLES) FAILS — "Unable to create OpenGL context >= 3.0".
param(
  # Image / qemu / bridge default to the dev box but honor env overrides so the
  # distribution launcher can point them at bundle-relative paths (additive, 2026-06-06).
  [string]$Image = $(if ($env:GOSE_IMAGE) { $env:GOSE_IMAGE } else { "D:\gose-vm\batocera-x86_64-43.1-20260529.img" }),
  [string]$Display = "sdl,gl=on",   # sdl,gl=on confirmed working; gtk,gl=on also worth trying
  [int]$Mem = 6,
  [int]$Cpus = 4,
  [string]$QemuBin = $(if ($env:GOSE_QEMU_BIN) { $env:GOSE_QEMU_BIN } else { "D:\gose-build\msys64\mingw64\bin" }),
  # Companion tray (#25 + #74): pass -Companion or set GOSE_COMPANION=1 to auto-start.
  # Requires: pip install pystray Pillow paramiko  (see requirements-companion.txt)
  [switch]$Companion = ($env:GOSE_COMPANION -eq '1')
)
$bin = $QemuBin                                    # MSYS2 qemu (virgl-enabled); overridable for the portable bundle
# Prefer the Windows-subsystem variant (qemu-system-x86_64w.exe) — it has no attached console
# window of its own, so the only window the user sees is the GOSE VM. Fall back to the console
# build if the windowed variant is absent (dev/CI setups that only have the .exe).
$qemuW = "$bin\qemu-system-x86_64w.exe"
$qemuC = "$bin\qemu-system-x86_64.exe"
$qemu  = if (Test-Path $qemuW) { $qemuW } else { $qemuC }
if (-not (Test-Path $qemu)) { Write-Error "MSYS2 qemu not found at $bin"; exit 1 }
$env:PATH = "$bin;$env:PATH"                        # so its DLLs (virgl/epoxy/ANGLE) resolve
# Override the SDL window title so the QEMU window shows "GOSE" rather than "QEMU (GOSE-PC)".
# Must be set before the process inherits the environment.
$env:SDL_VIDEO_WINDOW_TITLE = 'GOSE'

# kill any existing GOSE qemu + usbredir bridge first (port 8731/2222/14000 forwards conflict).
# Match THIS VM by its "-name GOSE-PC" cmdline flag — a global kill would stop other QEMU VMs.
# Check both the windowed (.w.exe preferred on launch) and the console build (.exe fallback).
Get-CimInstance Win32_Process -ErrorAction SilentlyContinue `
  -Filter "Name='qemu-system-x86_64w.exe' OR Name='qemu-system-x86_64.exe'" |
  Where-Object { $_.CommandLine -like '*-name GOSE-PC*' } |
  ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
Get-Process usbredirect -ErrorAction SilentlyContinue | Stop-Process -Force
Start-Sleep -Seconds 2

# host bridge: feeds REAL laptop battery + internet state to the guest (10.0.2.2:8790)
$bridge = $(if ($env:GOSE_BRIDGE) { $env:GOSE_BRIDGE } else { "D:\gose-vm\host_bridge.py" })
if (Test-Path $bridge) {
  Get-CimInstance Win32_Process -Filter "Name='python.exe'" -ErrorAction SilentlyContinue |
    Where-Object { $_.CommandLine -like "*host_bridge.py*" } |
    ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
  Start-Process python -ArgumentList $bridge -WindowStyle Hidden
  Write-Host "Host battery/network bridge started on 127.0.0.1:8790."
}

# controller passthrough: forwards REAL host pads into the guest as input events
# (agent input.pt_* -> uinput). Replaces usb-redir for controllers — usb-redir on a
# 1 kHz pad (DualSense) measured 4-7 s of input lag; this path is milliseconds.
# pad_passthrough_watch.py (the watchdog) supervises pad_passthrough.py and relaunches
# it within ~1s of any exit (crash, last-pad-unplug, etc.) — boot launches the
# WATCHDOG, not the daemon directly. Kill both on restart so ports + pt devices are clean.
# TODO (future): wrap with WinSW for survive-reboot service semantics (see watch file).
$padptWatch = $(if ($env:GOSE_PADPT_WATCH) { $env:GOSE_PADPT_WATCH } else { "D:\gose-vm\pad_passthrough_watch.py" })
$padptDaemon = $(if ($env:GOSE_PADPT) { $env:GOSE_PADPT } else { "D:\gose-vm\pad_passthrough.py" })
if (Test-Path $padptWatch) {
  # Kill existing watchdog + daemon (both cmdline patterns) before relaunching.
  Get-CimInstance Win32_Process -Filter "Name='python.exe' OR Name='py.exe'" -ErrorAction SilentlyContinue |
    Where-Object { $_.CommandLine -like "*pad_passthrough*" } |
    ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
  Start-Process py -ArgumentList '-3.11', $padptWatch -WindowStyle Minimized
  Write-Host "Controller passthrough watchdog started (auto-restarts daemon; host pads -> guest uinput)."
} elseif (Test-Path $padptDaemon) {
  # Fallback: watchdog not present — start daemon directly (no auto-restart).
  Get-CimInstance Win32_Process -Filter "Name='python.exe' OR Name='py.exe'" -ErrorAction SilentlyContinue |
    Where-Object { $_.CommandLine -like "*pad_passthrough.py*" } |
    ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
  Start-Process py -ArgumentList '-3.11', $padptDaemon -WindowStyle Minimized
  Write-Host "Controller passthrough started (daemon only — watchdog not found)."
}

$a = @(
  '-name','GOSE-PC','-machine','q35,accel=whpx','-cpu','qemu64','-smp',"$Cpus",'-m',"${Mem}G",
  '-drive',"file=$Image,if=virtio,format=raw",
  # host 8731->guest 8731 (GOSE agent, token-auth), host 2222->guest 22 (SSH layer inject)
  # LOOPBACK-ONLY binding (was tcp:: = all interfaces = LAN-exposed on hotel wifi, found 2026-06-06).
  # Remote access goes through tailscale serve (tailnet -> 127.0.0.1), never raw LAN. A firewall
  # block rule "GOSE VM - block LAN access to agent+SSH" exists as the second layer.
  # 8780 = the GOSE UI server, forwarded 127.0.0.1-only so the HOST browser can reach the in-VM
  # UI + the /upload ROM-drop page (http://127.0.0.1:8780/gose-upload.html). Host-loopback only,
  # never the LAN (same posture as 8731/2222). Added 2026-06-11 for host->guest ROM upload.
  '-netdev','user,id=net0,hostfwd=tcp:127.0.0.1:8731-:8731,hostfwd=tcp:127.0.0.1:2222-:22,hostfwd=tcp:127.0.0.1:8780-:8780',
  '-device','virtio-net-pci,netdev=net0',
  '-device','virtio-vga-gl','-display',$Display,     # virgl GPU passthrough via host OpenGL
  # host audio passthrough: speakers + mic (hda-duplex = playback + capture) via DirectSound
  '-audiodev','dsound,id=snd0',
  '-device','intel-hda','-device','hda-duplex,audiodev=snd0',
  # USB passthrough hub ready (attach specific host devices with: -device usb-host,vendorid=0x..,productid=0x..)
  '-device','qemu-xhci,id=xhci',
  # Bluetooth radio (RZ616 / MediaTek MT7922, VID_13D3/PID_3607). NOTE: plain `-device usb-host`
  # does NOT work on Windows here — libusb's WinUSB backend returns LIBUSB_ERROR_ACCESS (-5) on this
  # composite device (usbccgp-bound). Instead QEMU serves a usb-redir socket and `usbredirect` (which
  # uses the USBDk backend) captures the radio and streams it in. Verified 2026-06-05: hci0 UP RUNNING,
  # real MT7922 firmware loads, BlueZ scans + finds nearby devices. usbredirect is launched below.
  '-chardev','socket,id=usbredir0,host=127.0.0.1,port=14000,server=on,wait=off',
  '-device','usb-redir,chardev=usbredir0,id=usbredirdev',
  # PERIPHERAL PASSTHROUGH POOL (added 2026-06-06): 4 spare usb-redir channels for claim-on-demand
  # (game controllers like a PS5 pad, USB audio, USB storage). One device per channel. No device is
  # attached at boot — host_bridge.py runs `usbredirect --device <vid>:<pid> --to 127.0.0.1:140NN -k`
  # to capture a host device into a free channel when the user claims it from the Peripherals UI.
  # Same xhci hub as the BT channel above (no explicit bus= → QEMU attaches to qemu-xhci). Ports
  # 14001-14004 mirror the working 14000 pattern exactly; usbredirect attaches later, post-boot.
  '-chardev','socket,id=usbredir1,host=127.0.0.1,port=14001,server=on,wait=off',
  '-device','usb-redir,chardev=usbredir1,id=usbredirdev1',
  '-chardev','socket,id=usbredir2,host=127.0.0.1,port=14002,server=on,wait=off',
  '-device','usb-redir,chardev=usbredir2,id=usbredirdev2',
  '-chardev','socket,id=usbredir3,host=127.0.0.1,port=14003,server=on,wait=off',
  '-device','usb-redir,chardev=usbredir3,id=usbredirdev3',
  '-chardev','socket,id=usbredir4,host=127.0.0.1,port=14004,server=on,wait=off',
  '-device','usb-redir,chardev=usbredir4,id=usbredirdev4'
)
Write-Host "Booting GOSE-PC VM (virgl/$Display) via MSYS2 qemu..."
# 2026-06-13: capture QEMU stderr + verify it actually STAYS up, with one display
# fallback. Before, QEMU was Start-Process'd with NO output capture and NO survival
# check, so a failed GL-context init just vanished silently — "I press GOSE and nothing
# pops up", zero trace (the 2026-06-13 no-show Zeke hit). Now: log stderr to qemu.err.log,
# and if QEMU exits within ~4s, retry ONCE on gtk,gl=on so a transient sdl/GL init failure
# self-heals instead of leaving a dead icon.
$qemuErr = Join-Path $PSScriptRoot 'qemu.err.log'
function Start-GoseQemu([string]$displayValue) {
  $args2 = $a.Clone()                              # copy so we can swap -display per attempt
  for ($i = 0; $i -lt $args2.Count; $i++) {
    if ($args2[$i] -eq '-display') { $args2[$i + 1] = $displayValue }
  }
  return Start-Process -FilePath $qemu -ArgumentList $args2 -WorkingDirectory $bin -PassThru -RedirectStandardError $qemuErr
}
$script:GoseVmProcess = Start-GoseQemu $Display
Start-Sleep -Seconds 4
if ($script:GoseVmProcess.HasExited) {
  $errTxt = (Get-Content $qemuErr -Raw -ErrorAction SilentlyContinue)
  Write-Warning "QEMU exited immediately on '$Display' (rc=$($script:GoseVmProcess.ExitCode)). stderr:`n$errTxt"
  Write-Host "Retrying once with gtk,gl=on ..."
  $script:GoseVmProcess = Start-GoseQemu 'gtk,gl=on'
  Start-Sleep -Seconds 4
  if ($script:GoseVmProcess.HasExited) {
    $errTxt2 = (Get-Content $qemuErr -Raw -ErrorAction SilentlyContinue)
    Write-Error "QEMU failed to start on both '$Display' and 'gtk,gl=on'. See $qemuErr`n$errTxt2"
    exit 1
  }
  Write-Host "GOSE came up on the gtk,gl=on fallback (pid $($script:GoseVmProcess.Id))."
}
Write-Host "Launched (pid $($script:GoseVmProcess.Id)). Agent reachable at 127.0.0.1:8731 once booted."

# Bridge the laptop's Bluetooth radio into the VM via USBDk (usb-redir). Wait for QEMU's
# usbredir socket (14000) to listen, then connect usbredirect to it. Requires USBDk installed.
$btVidPid = '13d3:3607'
$redirReady = $false
for ($i=0; $i -lt 20; $i++) {
  try { $t=New-Object Net.Sockets.TcpClient; $t.Connect('127.0.0.1',14000); if($t.Connected){$redirReady=$true;$t.Close();break} } catch {}
  Start-Sleep -Milliseconds 500
}
if ($redirReady) {
  Start-Process -FilePath "$bin\usbredirect.exe" `
    -ArgumentList '--device',$btVidPid,'--to','127.0.0.1:14000','-k' `
    -WorkingDirectory $bin -WindowStyle Hidden `
    -RedirectStandardError "D:\gose-vm\usbredirect.log" -RedirectStandardOutput "D:\gose-vm\usbredirect.out"
  Write-Host "Bluetooth bridge started (usbredirect $btVidPid -> usb-redir:14000). Guest gets hci0 once it enumerates."
} else {
  Write-Warning "usbredir socket (14000) never came up - Bluetooth NOT bridged. Check QEMU launched."
}

# GOSE Companion tray (#25 + #74): system tray app + optional mobile web server.
# Pass -Companion (or set GOSE_COMPANION=1) when booting to auto-start the tray.
$companionScript = $(if ($env:GOSE_COMPANION_SCRIPT) { $env:GOSE_COMPANION_SCRIPT } `
  else { Join-Path $PSScriptRoot 'gose_companion.py' })
if ($Companion -and (Test-Path $companionScript)) {
  Get-CimInstance Win32_Process -Filter "Name='python.exe' OR Name='py.exe'" -ErrorAction SilentlyContinue |
    Where-Object { $_.CommandLine -like "*gose_companion.py*" } |
    ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
  Start-Process py -ArgumentList '-3.11', $companionScript -WindowStyle Normal
  Write-Host "GOSE Companion tray started. (pystray + Pillow + paramiko required)"
} elseif ($Companion) {
  Write-Warning "Companion not started: $companionScript not found."
}
