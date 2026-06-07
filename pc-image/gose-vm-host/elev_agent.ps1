# GOSE elevated agent — a controllable admin "session" for the AI agent.
# The owner launches this ONCE (one UAC approval, via elev_launch.bat). It then runs elevated and executes
# whatever command-files the AI agent drops in D:\gose-vm\elev\, writing their output back. The agent kills it by
# dropping a stop.flag (or just closing it). Scoped to a local file queue; nothing remote can reach it.
$ErrorActionPreference = "Continue"
$dir = "D:\gose-vm\elev"
New-Item -ItemType Directory -Force $dir | Out-Null
$principal = New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent())
$elev = $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)

function Beat($extra) {
  "pid=$PID`nelevated=$elev`nstamp=$(Get-Date -Format o)`n$extra" | Set-Content "$dir\alive.txt" -Encoding utf8
}
Beat "state=ready"

while ($true) {
  if (Test-Path "$dir\stop.flag") {
    Remove-Item "$dir\stop.flag" -Force -ErrorAction SilentlyContinue
    Beat "state=stopped"
    break
  }
  $req = Get-ChildItem "$dir\req_*.ps1" -ErrorAction SilentlyContinue | Sort-Object Name | Select-Object -First 1
  if ($req) {
    $out = [IO.Path]::ChangeExtension($req.FullName, ".out")
    Beat ("state=running " + $req.Name)
    try {
      $result = & $req.FullName *>&1 | Out-String
      "$result`n[exit=$LASTEXITCODE]" | Set-Content $out -Encoding utf8
    } catch {
      "[error] $($_ | Out-String)" | Set-Content $out -Encoding utf8
    }
    Remove-Item $req.FullName -Force -ErrorAction SilentlyContinue
    Beat ("state=done " + $req.Name)
  } else {
    Beat "state=idle"
    Start-Sleep -Seconds 1
  }
}
