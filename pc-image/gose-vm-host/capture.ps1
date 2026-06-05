param([string]$Out = "D:\gose-vm\gose-shot.png")
Add-Type -AssemblyName System.Drawing
$sig = @'
using System;
using System.Runtime.InteropServices;
public class Win {
  [DllImport("user32.dll")] public static extern bool PrintWindow(IntPtr h, IntPtr dc, uint f);
  [DllImport("user32.dll")] public static extern bool GetWindowRect(IntPtr h, out RECT r);
  [DllImport("user32.dll")] public static extern bool ShowWindow(IntPtr h, int n);
  [DllImport("user32.dll")] public static extern bool IsIconic(IntPtr h);
  [StructLayout(LayoutKind.Sequential)] public struct RECT { public int L,T,R,B; }
}
'@
if (-not ([System.Management.Automation.PSTypeName]'Win').Type) {
  Add-Type -TypeDefinition $sig
}
# find the QEMU SDL window
$p = Get-Process qemu-system-x86_64 -ErrorAction SilentlyContinue | Where-Object { $_.MainWindowHandle -ne 0 } | Select-Object -First 1
if (-not $p) { Write-Output "NO_QEMU_WINDOW"; exit 1 }
$h = $p.MainWindowHandle
if ([Win]::IsIconic($h)) { [void][Win]::ShowWindow($h, 9); Start-Sleep -Milliseconds 600 }  # 9 = SW_RESTORE
$r = New-Object Win+RECT
[void][Win]::GetWindowRect($h, [ref]$r)
$w = $r.R - $r.L; $ht = $r.B - $r.T
if ($w -le 0 -or $ht -le 0) { Write-Output "BAD_RECT $w x $ht"; exit 1 }
$bmp = New-Object System.Drawing.Bitmap($w, $ht)
$g = [System.Drawing.Graphics]::FromImage($bmp)
$dc = $g.GetHdc()
[void][Win]::PrintWindow($h, $dc, 2)  # 2 = PW_RENDERFULLCONTENT
$g.ReleaseHdc($dc); $g.Dispose()
$bmp.Save($Out, [System.Drawing.Imaging.ImageFormat]::Png)
$bmp.Dispose()
Write-Output "SAVED $Out ($w x $ht) title='$($p.MainWindowTitle)'"
