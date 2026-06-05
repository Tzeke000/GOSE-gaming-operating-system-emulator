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

# kill any existing qemu first (port 8731/2222 forwards conflict)
Get-Process qemu-system-x86_64 -ErrorAction SilentlyContinue | Stop-Process -Force
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
  '-netdev','user,id=net0,hostfwd=tcp::8731-:8731,hostfwd=tcp::2222-:22',
  '-device','virtio-net-pci,netdev=net0',
  '-device','virtio-vga-gl','-display',$Display,     # virgl GPU passthrough via host OpenGL
  # host audio passthrough: speakers + mic (hda-duplex = playback + capture) via DirectSound
  '-audiodev','dsound,id=snd0',
  '-device','intel-hda','-device','hda-duplex,audiodev=snd0',
  # USB passthrough hub ready (attach specific host devices with: -device usb-host,vendorid=0x..,productid=0x..)
  '-device','qemu-xhci,id=xhci',
  # Bluetooth radio (RZ616 Bluetooth Adapter, VID_13D3/PID_3607) handed to the guest via USBDk so the
  # GOSE Bluetooth page drives the laptop's real BT (bluez in-guest). Requires USBDk installed on host.
  '-device','usb-host,vendorid=0x13d3,productid=0x3607'
)
Write-Host "Booting GOSE-PC VM (virgl/$Display) via MSYS2 qemu..."
Start-Process -FilePath $qemu -ArgumentList $a -WorkingDirectory $bin
Write-Host "Launched. Agent reachable at 127.0.0.1:8731 (token in .mcp.json) once booted."
