# Auto-runs when the elevated agent starts (Zeke's one UAC click via elev_launch.bat).
# Silent-installs USBDk so QEMU can pass the laptop's Bluetooth radio into the GOSE VM.
$ErrorActionPreference = "Continue"
$msi = "D:\gose-vm\UsbDk_1.0.22_x64.msi"
$log = "D:\gose-vm\elev\usbdk_install.log"
"== USBDk install =="
"msi present: $(Test-Path $msi)  size: $((Get-Item $msi -ErrorAction SilentlyContinue).Length)"
$p = Start-Process msiexec.exe -ArgumentList "/i `"$msi`" /qn /norestart /l*v `"$log`"" -Wait -PassThru
"msiexec exit: $($p.ExitCode)   (0 = ok, 3010 = ok-reboot-suggested)"
"-- UsbDk service --"
Get-Service -Name "UsbDk*" -ErrorAction SilentlyContinue | Select-Object Name, Status, StartType | Format-Table -Auto | Out-String
"-- UsbDkController device list --"
$ctl = "C:\Program Files\UsbDk Runtime Library\UsbDkController.exe"
if (Test-Path $ctl) { & $ctl -n 2>&1 | Out-String } else { "UsbDkController.exe NOT found at expected path" }
"== done =="
