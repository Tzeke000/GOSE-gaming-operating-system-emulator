# Boot the GOSE-PC VM with GPU rendering (virgl) — the WORKING setup, 2026-06-04.
# Renders Batocera/EmulationStation through the host GPU. No admin needed.
# Key: use MSYS2's qemu (has virglrenderer + ANGLE) + gl=on (DESKTOP GL).
#   gl=es (ANGLE GLES) FAILS — "Unable to create OpenGL context >= 3.0".
param(
  [string]$Image = "D:\gose-vm\batocera-x86_64-43.1-20260529.img",
  [string]$Display = "sdl,gl=on",   # sdl,gl=on confirmed working; gtk,gl=on also worth trying
  [int]$Mem = 6,
  [int]$Cpus = 4
)
$bin = "D:\gose-build\msys64\mingw64\bin"          # MSYS2 qemu (virgl-enabled)
$qemu = "$bin\qemu-system-x86_64.exe"
if (-not (Test-Path $qemu)) { Write-Error "MSYS2 qemu not found at $qemu"; exit 1 }
$env:PATH = "$bin;$env:PATH"                        # so its DLLs (virgl/epoxy/ANGLE) resolve

# kill any existing qemu + usbredir bridge first (port 8731/2222/14000 forwards conflict)
Get-Process qemu-system-x86_64 -ErrorAction SilentlyContinue | Stop-Process -Force
Get-Process usbredirect -ErrorAction SilentlyContinue | Stop-Process -Force
Start-Sleep -Seconds 2

# host bridge: feeds REAL laptop battery + internet state to the guest (10.0.2.2:8790)
$bridge = "D:\gose-vm\host_bridge.py"
if (Test-Path $bridge) {
  Get-CimInstance Win32_Process -Filter "Name='python.exe'" -ErrorAction SilentlyContinue |
    Where-Object { $_.CommandLine -like "*host_bridge.py*" } |
    ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
  Start-Process python -ArgumentList $bridge -WindowStyle Hidden
  Write-Host "Host battery/network bridge started on 127.0.0.1:8790."
}

$a = @(
  '-name','GOSE-PC','-machine','q35,accel=whpx','-cpu','qemu64','-smp',"$Cpus",'-m',"${Mem}G",
  '-drive',"file=$Image,if=virtio,format=raw",
  # host 8731->guest 8731 (GOSE agent, token-auth), host 2222->guest 22 (SSH layer inject)
  # LOOPBACK-ONLY binding (was tcp:: = all interfaces = LAN-exposed on hotel wifi, found 2026-06-06).
  # Remote access goes through tailscale serve (tailnet -> 127.0.0.1), never raw LAN. A firewall
  # block rule "GOSE VM - block LAN access to agent+SSH" exists as the second layer.
  '-netdev','user,id=net0,hostfwd=tcp:127.0.0.1:8731-:8731,hostfwd=tcp:127.0.0.1:2222-:22',
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
Start-Process -FilePath $qemu -ArgumentList $a -WorkingDirectory $bin
Write-Host "Launched. Agent reachable at 127.0.0.1:8731 (token in .mcp.json) once booted."

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
