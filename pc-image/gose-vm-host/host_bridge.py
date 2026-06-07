#!/usr/bin/env python3
# GOSE host bridge — serves the REAL laptop battery + internet state to the guest.
# The guest reaches the host via QEMU's slirp gateway, so in-VM code fetches
# http://10.0.2.2:8790/ and gets live host data. On a desktop PC with no battery,
# has_battery=False so the UI simply hides the battery readout (like Windows does).
import http.server, socketserver, json, socket, subprocess, re, os, tempfile, glob, math
try:
    import psutil
except Exception:
    psutil = None

# ---- Wi-Fi: drive the laptop's REAL wireless via netsh so GOSE can scan/join networks ----
def _netsh(args):
    return subprocess.run(["netsh", "wlan"] + args, capture_output=True, text=True,
                          timeout=14, errors="replace").stdout

def _xml(s):
    return (s or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

_WIFI_WPA = ('<?xml version="1.0"?>\n<WLANProfile xmlns="http://www.microsoft.com/networking/WLAN/profile/v1">'
    '<name>{ssid}</name><SSIDConfig><SSID><name>{ssid}</name></SSID></SSIDConfig>'
    '<connectionType>ESS</connectionType><connectionMode>auto</connectionMode>'
    '<MSM><security><authEncryption><authentication>WPA2PSK</authentication><encryption>AES</encryption>'
    '<useOneX>false</useOneX></authEncryption><sharedKey><keyType>passPhrase</keyType>'
    '<protected>false</protected><keyMaterial>{pw}</keyMaterial></sharedKey></security></MSM></WLANProfile>')
_WIFI_OPEN = ('<?xml version="1.0"?>\n<WLANProfile xmlns="http://www.microsoft.com/networking/WLAN/profile/v1">'
    '<name>{ssid}</name><SSIDConfig><SSID><name>{ssid}</name></SSID></SSIDConfig>'
    '<connectionType>ESS</connectionType><connectionMode>auto</connectionMode>'
    '<MSM><security><authEncryption><authentication>open</authentication><encryption>none</encryption>'
    '<useOneX>false</useOneX></authEncryption></security></MSM></WLANProfile>')

def wifi_status():
    try:
        out = _netsh(["show", "interfaces"])
        def g(k):
            m = re.search(r"^\s*" + k + r"\s*:\s*(.+?)\s*$", out, re.M); return m.group(1) if m else None
        state = (g("State") or "").lower()
        return {"ok": True, "connected": "connected" in state, "ssid": g("SSID"),
                "signal": g("Signal"), "radio": bool(out.strip())}
    except Exception as e:
        return {"ok": False, "error": str(e)}

def wifi_scan():
    try:
        out = _netsh(["show", "networks", "mode=bssid"])
        nets, cur = [], None
        for line in out.splitlines():
            m = re.match(r"\s*SSID\s+\d+\s*:\s*(.*)$", line)
            if m:
                cur = {"ssid": m.group(1).strip(), "auth": "", "signal": None}; nets.append(cur); continue
            if cur is None:
                continue
            a = re.match(r"\s*Authentication\s*:\s*(.+?)\s*$", line)
            if a and not cur["auth"]:
                cur["auth"] = a.group(1).strip()
            s = re.match(r"\s*Signal\s*:\s*(\d+)%", line)
            if s and cur["signal"] is None:
                cur["signal"] = int(s.group(1))
        st = wifi_status()
        seen, uniq = set(), []
        for n in nets:
            if n["ssid"] and n["ssid"] not in seen:
                seen.add(n["ssid"]); n["secure"] = ("open" not in n["auth"].lower()); uniq.append(n)
        uniq.sort(key=lambda n: -(n["signal"] or 0))
        return {"ok": True, "networks": uniq, "current": st.get("ssid") if st.get("ok") else None}
    except Exception as e:
        return {"ok": False, "error": str(e)}

def wifi_connect(ssid, password):
    if not ssid:
        return {"ok": False, "error": "no ssid"}
    try:
        r = _netsh(["connect", "name=" + ssid, "ssid=" + ssid])
        if "completed successfully" in r.lower():
            return {"ok": True, "connecting": ssid}
        prof = (_WIFI_WPA if password else _WIFI_OPEN).format(ssid=_xml(ssid), pw=_xml(password))
        fn = os.path.join(tempfile.gettempdir(), "gose_wifi.xml")
        open(fn, "w", encoding="utf-8").write(prof)
        _netsh(["add", "profile", "filename=" + fn, "user=all"])
        try: os.remove(fn)
        except Exception: pass
        r2 = _netsh(["connect", "name=" + ssid, "ssid=" + ssid])
        return {"ok": "completed successfully" in r2.lower(), "connecting": ssid, "detail": r2.strip()[:160]}
    except Exception as e:
        return {"ok": False, "error": str(e)}

def wifi_disconnect():
    try:
        _netsh(["disconnect"]); return {"ok": True}
    except Exception as e:
        return {"ok": False, "error": str(e)}

def screencap():
    # capture the QEMU window from the HOST (PrintWindow grabs GL game frames the guest can't).
    # capture.ps1 saves as JPEG when -Out ends in .jpg (System.Drawing infers from extension).
    fn = os.path.join(tempfile.gettempdir(), "gose_scr.jpg")
    try:
        subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
                        "-File", "D:\\gose-vm\\capture.ps1", "-Out", fn],
                       capture_output=True, text=True, timeout=10)
        with open(fn, "rb") as f:
            return f.read()
    except Exception:
        return None

_net = {"online": False, "every": 0}
_gpu = {"v": None, "every": 0}

def gpu():
    # main GPU (NVIDIA dGPU) via nvidia-smi — Task-Manager-style live load/mem/temp.
    # cache between polls so we don't spawn nvidia-smi every request.
    _gpu["every"] = (_gpu["every"] + 1) % 2
    if _gpu["every"] != 1 and _gpu["v"] is not None:
        return _gpu["v"]
    try:
        out = subprocess.run(
            ["nvidia-smi", "--query-gpu=utilization.gpu,memory.used,memory.total,temperature.gpu,name",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=3)
        u, mu, mt, t, name = [x.strip() for x in out.stdout.strip().splitlines()[0].split(",", 4)]
        _gpu["v"] = {"gpu_pct": int(float(u)), "gpu_mem_used_mb": int(float(mu)),
                     "gpu_mem_total_mb": int(float(mt)), "gpu_temp_c": int(float(t)), "gpu_name": name}
    except Exception:
        _gpu["v"] = {"gpu_pct": None}
    return _gpu["v"]

def online():
    # cache: only probe the network every ~5th call to keep polls snappy
    _net["every"] = (_net["every"] + 1) % 5
    if _net["every"] != 1:
        return _net["online"]
    try:
        # privacy: use Windows' OWN connectivity assessment (Network List Manager) — it contacts
        # NO third party. (Was: connect to 8.8.8.8:53 = phoning Google DNS on every poll.)
        out = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "[bool]((Get-NetConnectionProfile).IPv4Connectivity -contains 'Internet')"],
            capture_output=True, text=True, timeout=3)
        _net["online"] = "True" in out.stdout
    except Exception:
        _net["online"] = False
    return _net["online"]

def battery_ps():
    # fallback when psutil is missing/None: read battery via PowerShell (CIM).
    # EstimatedRunTime is MINUTES remaining on battery; 0x7fffffff (71582788) = unknown/charging.
    try:
        out = subprocess.run(["powershell", "-NoProfile", "-Command",
            "$b=Get-CimInstance Win32_Battery; if($b){\"$($b.EstimatedChargeRemaining),$($b.BatteryStatus),$($b.EstimatedRunTime)\"}else{'none'}"],
            capture_output=True, text=True, timeout=4).stdout.strip()
        if out and out != "none":
            parts = out.split(",")
            pct, status = parts[0], parts[1]
            charging = status.strip() in ("2", "3", "6", "7", "8")
            secs = None
            if not charging and len(parts) > 2:
                try:
                    mins = int(parts[2])
                    if 0 < mins < 60 * 48:   # ignore the unknown sentinel
                        secs = mins * 60
                except Exception:
                    secs = None
            # BatteryStatus 2 = AC/charging; 1 = discharging
            return {"has_battery": True, "battery_pct": int(pct), "charging": charging,
                    "secs_left": secs, "battery_source": "host:Win32_Battery"}
    except Exception:
        pass
    return None

def info():
    out = {"ok": True, "online": online(), "has_battery": False,
           "battery_pct": None, "charging": None, "secs_left": None,
           "battery_source": None}
    got = False
    if psutil:
        try:
            b = psutil.sensors_battery()
            if b is not None:
                secs = None
                if b.secsleft is not None and b.secsleft >= 0 and not b.power_plugged:
                    secs = int(b.secsleft)
                out.update(has_battery=True, battery_pct=round(b.percent),
                           charging=bool(b.power_plugged), secs_left=secs,
                           battery_source="host:psutil"); got = True
        except Exception:
            pass
    if not got:
        bp = battery_ps()
        if bp:
            out.update(bp)
    out.update(gpu())
    return out

# Prefer a winget-installed ffmpeg for the current user; fall back to PATH.
_ffmpeg_hits = glob.glob(os.path.join(
    os.path.expandvars(r"%LOCALAPPDATA%"), "Microsoft", "WinGet", "Packages",
    "Gyan.FFmpeg*", "*", "bin", "ffmpeg.exe"))
FFMPEG = _ffmpeg_hits[0] if _ffmpeg_hits else "ffmpeg"

def qemu_title():
    # gdigrab needs the exact window title; it carries a "- Press Ctrl-Alt-G..." suffix, so read it live
    try:
        return subprocess.run(["powershell", "-NoProfile", "-Command",
            "(Get-Process qemu-system-x86_64 -EA SilentlyContinue | "
            "Where-Object {$_.MainWindowTitle} | Select-Object -First 1).MainWindowTitle"],
            capture_output=True, text=True, timeout=5).stdout.strip()
    except Exception:
        return ""

# ---- Clip buffer (instant replay): a rolling gdigrab segmenter on the host ----
_clip = {"proc": None, "dir": None}

def clip_start():
    if _clip["proc"] and _clip["proc"].poll() is None:
        return {"ok": True, "running": True}
    title = qemu_title()
    if not title:
        return {"ok": False, "error": "no window"}
    d = os.path.join(tempfile.gettempdir(), "gose_replay")
    os.makedirs(d, exist_ok=True)
    for f in glob.glob(os.path.join(d, "seg*.ts")):
        try: os.remove(f)
        except Exception: pass
    cmd = [FFMPEG, "-loglevel", "error", "-f", "gdigrab", "-framerate", "30", "-i", "title=" + title,
           "-vf", "scale=1280:-2", "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
           "-g", "30", "-f", "segment", "-segment_time", "3", "-segment_wrap", "60",
           "-reset_timestamps", "1", os.path.join(d, "seg%03d.ts")]
    try:
        _clip["proc"] = subprocess.Popen(cmd); _clip["dir"] = d
        return {"ok": True, "running": True}
    except Exception as e:
        return {"ok": False, "error": str(e)}

def clip_stop():
    p = _clip["proc"]
    if p and p.poll() is None:
        try: p.terminate()
        except Exception: pass
    _clip["proc"] = None
    return {"ok": True, "running": False}

def clip_status():
    return {"ok": True, "running": bool(_clip["proc"] and _clip["proc"].poll() is None)}

def clip_save(seconds):
    if not (_clip["proc"] and _clip["proc"].poll() is None):
        return None
    segs = sorted(glob.glob(os.path.join(_clip["dir"], "seg*.ts")), key=os.path.getmtime)
    if len(segs) < 2:
        return None
    segs = segs[:-1]   # drop the in-progress newest segment
    n = min(len(segs), int(math.ceil(seconds / 3.0)) + 1)
    use = segs[-n:]
    listf = os.path.join(_clip["dir"], "list.txt")
    with open(listf, "w") as f:
        for s in use:
            f.write("file '%s'\n" % s.replace("\\", "/"))
    out = os.path.join(tempfile.gettempdir(), "gose_clip.mp4")
    try: os.remove(out)
    except Exception: pass
    try:
        subprocess.run([FFMPEG, "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", listf,
                        "-c", "copy", "-movflags", "+faststart", "-y", out],
                       capture_output=True, text=True, timeout=40)
        if os.path.isfile(out) and os.path.getsize(out) > 2000:
            with open(out, "rb") as f:
                return f.read()
    except Exception:
        pass
    return None

# ---- Real host PERFORMANCE telemetry: CPU/RAM/GPU/temps/uptime ----
# The guest's own perf stats are VM-fake (it only sees its 4 vCPUs). These read the
# REAL laptop. psutil is preferred but the runtime interpreter (MSYS2 python) has no
# psutil, so CPU/RAM/uptime fall back to ctypes (dependency-free, exact). GPU reuses
# nvidia-smi (gpu()); CPU temp via ACPI thermal zone (WMI through PowerShell, cached).
import ctypes, threading, time
from ctypes import wintypes

_k32 = ctypes.windll.kernel32
_perf = {"cpu_pct": None, "cores": None}

class _MEMORYSTATUSEX(ctypes.Structure):
    _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]

def _mem():
    m = _MEMORYSTATUSEX(); m.dwLength = ctypes.sizeof(_MEMORYSTATUSEX)
    _k32.GlobalMemoryStatusEx(ctypes.byref(m))
    return m.ullTotalPhys, m.ullTotalPhys - m.ullAvailPhys, m.dwMemoryLoad

def _cpu_times():
    # GetSystemTimes: kernel time INCLUDES idle, so busy = (kernel+user) - idle
    i = wintypes.FILETIME(); ke = wintypes.FILETIME(); u = wintypes.FILETIME()
    _k32.GetSystemTimes(ctypes.byref(i), ctypes.byref(ke), ctypes.byref(u))
    f = lambda ft: (ft.dwHighDateTime << 32) | ft.dwLowDateTime
    return f(i), f(ke), f(u)

def _cpu_delta(a, b):
    idle = b[0] - a[0]; total = (b[1] - a[1]) + (b[2] - a[2])
    return round((total - idle) / total * 100, 1) if total > 0 else None

def _cpu_sampler():
    # background: keep a ~1s rolling CPU% so /perf needs no per-request interval
    if psutil:
        while True:
            try:
                _perf["cpu_pct"] = psutil.cpu_percent(interval=1.0)
                _perf["cores"] = psutil.cpu_percent(percpu=True)
            except Exception:
                time.sleep(1.0)
    else:
        prev = _cpu_times()
        while True:
            time.sleep(1.0)
            cur = _cpu_times()
            _perf["cpu_pct"] = _cpu_delta(prev, cur)
            prev = cur

_temp = {"v": None, "ts": 0.0}

def cpu_temp():
    # ACPI thermal zone via WMI (PowerShell). Not strictly a CPU core sensor — it's the
    # mainboard/CPU ACPI zone, the only temp this laptop exposes without OpenHardwareMonitor.
    now = time.time()
    if _temp["ts"] and now - _temp["ts"] < 8:
        return _temp["v"]
    _temp["ts"] = now
    try:
        out = subprocess.run(["powershell", "-NoProfile", "-Command",
            "$t=(Get-CimInstance -Namespace root/wmi -ClassName MSAcpi_ThermalZoneTemperature "
            "-EA Stop).CurrentTemperature; if($t){($t|Measure-Object -Maximum).Maximum}"],
            capture_output=True, text=True, timeout=5).stdout.strip()
        _temp["v"] = round(int(out.splitlines()[0]) / 10.0 - 273.15, 1) if out else None
    except Exception:
        _temp["v"] = None
    return _temp["v"]

def perf():
    total, used, load = _mem()
    g = gpu()
    cpu = _perf["cpu_pct"]
    if cpu is None and not psutil:   # not sampled yet — take a quick blocking sample
        a = _cpu_times(); time.sleep(0.08); cpu = _cpu_delta(a, _cpu_times())
    return {
        "ok": True,
        "cpu_pct": cpu,
        "cpu_cores": _perf["cores"],            # per-core list when psutil present, else null
        "mem_used": used, "mem_total": total,   # bytes
        "mem_used_mb": round(used / 1048576), "mem_total_mb": round(total / 1048576),
        "mem_pct": load,
        "gpu_pct": g.get("gpu_pct"),
        "gpu_mem": g.get("gpu_mem_used_mb"),    # alias: MB in use
        "gpu_mem_used_mb": g.get("gpu_mem_used_mb"), "gpu_mem_total_mb": g.get("gpu_mem_total_mb"),
        "gpu_name": g.get("gpu_name"),
        "cpu_temp_c": cpu_temp(),               # ACPI thermal zone (see cpu_temp note)
        "gpu_temp_c": g.get("gpu_temp_c"),
        "uptime_s": round(_k32.GetTickCount64() / 1000),
    }

# ---- Laptop BRIGHTNESS: drive the internal panel via the Windows WMI brightness API ----
def brightness_get():
    try:
        out = subprocess.run(["powershell", "-NoProfile", "-Command",
            "(Get-CimInstance -Namespace root/WMI -ClassName WmiMonitorBrightness "
            "-EA Stop).CurrentBrightness"],
            capture_output=True, text=True, timeout=6)
        s = out.stdout.strip()
        if s:
            return {"ok": True, "level": int(s.splitlines()[0])}
        return {"ok": False, "error": "WmiMonitorBrightness returned no data "
                "(display does not support WMI brightness — e.g. external/desktop monitor)"}
    except Exception as e:
        return {"ok": False, "error": str(e)}

def brightness_set(level):
    try:
        lvl = max(0, min(100, int(level)))
    except Exception:
        return {"ok": False, "error": "level must be an integer 0-100"}
    try:
        r = subprocess.run(["powershell", "-NoProfile", "-Command",
            "$m=Get-CimInstance -Namespace root/WMI -ClassName WmiMonitorBrightnessMethods -EA Stop; "
            "Invoke-CimMethod -InputObject $m -MethodName WmiSetBrightness "
            "-Arguments @{Timeout=1; Brightness=%d} | Out-Null; 'ok'" % lvl],
            capture_output=True, text=True, timeout=8)
        if "ok" in (r.stdout or ""):
            return {"ok": True, "level": lvl}
        return {"ok": False, "error": ((r.stderr or r.stdout) or "set failed").strip()[:200]}
    except Exception as e:
        return {"ok": False, "error": str(e)}

# ---- USB passthrough: list host USB devices + claim/release them into the GOSE VM ----
# Each device is streamed into the VM over a usb-redir channel opened by boot-gose-vm.ps1
# (one device per channel). 14000 = Bluetooth radio, claimed at boot (not in the free pool).
# 14001-14004 = claim-on-demand pool. We capture a device with the SAME tool/flags as the BT
# bridge: `usbredirect --device <vid>:<pid> --to 127.0.0.1:<port> -k` (USBDk backend).
_USBREDIR_BIN = r"D:\gose-build\msys64\mingw64\bin\usbredirect.exe"
if not os.path.isfile(_USBREDIR_BIN):
    _USBREDIR_BIN = "usbredirect"
USB_BT_VIDPID = "13d3:3607"          # Bluetooth radio — already passed through at boot
USB_BT_PORT = 14000
USB_POOL_PORTS = [14001, 14002, 14003, 14004]

_usb_claims = {}                     # vid:pid -> {"port": int, "proc": Popen}
_usb_lock = threading.Lock()

# Never steal these from the laptop: the BT radio (already passed), the USBDk virtual device,
# and the host's own pointer/keyboard. Built-in kbd/touchpad are I2C-HID (not USB) so they
# don't enumerate here at all; the only USB host-input seen on this laptop is the Logitech
# LIGHTSPEED receiver, caught by the name filter below.
_USB_PROTECT_VIDPID = {USB_BT_VIDPID, "2b23:cafe"}
_USB_HIDE_VIDPID = {"2b23:cafe"}     # USBDk's own filter device — infra, not a peripheral
_USB_PROTECT_NAME_RE = re.compile(r"receiver|keyboard|mouse|touch ?pad|trackpad|usbdk", re.I)

# Known game controllers — flagged specially. A DualSense enumerates as a generic "USB Input
# Device" (HIDClass) with no controller-ish name, so the vid:pid table is the reliable signal.
_USB_CONTROLLERS = {
    "054c:0ce6": "Sony DualSense (PS5)",
    "054c:0df2": "Sony DualSense Edge (PS5)",
    "054c:05c4": "Sony DualShock 4 (v1)",
    "054c:09cc": "Sony DualShock 4 (v2)",
    "045e:028e": "Xbox 360 Controller",
    "045e:02ea": "Xbox One Controller",
    "045e:0b12": "Xbox Series Controller",
    "045e:0b13": "Xbox Series Controller (Bluetooth)",
    "057e:2009": "Nintendo Switch Pro Controller",
    "28de:1142": "Steam Controller",
    "0079:0006": "Generic USB Gamepad",
}

def _usb_class(cls, name, vidpid):
    if vidpid in _USB_CONTROLLERS:
        return "controller"
    c = (cls or "").lower(); n = (name or "").lower()
    if any(k in n for k in ("controller", "gamepad", "joystick", "dualsense", "dualshock", "xbox", "joy-con")):
        return "controller"
    if c in ("media", "audioendpoint", "audioprocessingobject") or \
       any(k in n for k in ("audio", "headset", "headphone", "microphone", " mic", "speaker", "dac")):
        return "audio"
    if c in ("usbstor", "scsiadapter", "diskdrive", "wpd", "volume") or \
       "mass storage" in n or "uas" in n or "flash" in n:
        return "storage"
    return "other"

def _usb_list_raw():
    # Enumerate USB-bus devices (present + cached) with VID/PID via PnP. ConvertTo-Json so we parse
    # structured data, not screen-scraped tables. Status 'OK' == currently present/plugged in.
    ps = (
        "Get-PnpDevice | Where-Object { $_.InstanceId -match 'USB\\\\VID_([0-9A-Fa-f]{4})&PID_([0-9A-Fa-f]{4})' } | "
        "ForEach-Object { $m=[regex]::Match($_.InstanceId,'VID_([0-9A-Fa-f]{4})&PID_([0-9A-Fa-f]{4})'); "
        "[PSCustomObject]@{ vid=$m.Groups[1].Value.ToLower(); pid=$m.Groups[2].Value.ToLower(); "
        "cls=$_.Class; name=$_.FriendlyName; present=($_.Status -eq 'OK') } } | "
        "Sort-Object vid,pid -Unique | ConvertTo-Json -Compress"
    )
    out = subprocess.run(["powershell", "-NoProfile", "-Command", ps],
                         capture_output=True, text=True, timeout=15, errors="replace").stdout.strip()
    if not out:
        return []
    data = json.loads(out)
    return data if isinstance(data, list) else [data]

def _usb_reap():
    # drop claims whose usbredirect process has exited
    for k in list(_usb_claims):
        p = _usb_claims[k]["proc"]
        if p.poll() is not None:
            _usb_claims.pop(k, None)

def usb_list():
    try:
        raw = _usb_list_raw()
    except Exception as e:
        return {"ok": False, "error": "USB enumeration failed: " + str(e)}
    with _usb_lock:
        _usb_reap()
        used_ports = {c["port"] for c in _usb_claims.values()}
        claimed_vp = set(_usb_claims)
        devs = []
        for d in raw:
            vid = (d.get("vid") or "").lower(); pid = (d.get("pid") or "").lower()
            if not vid or not pid:
                continue
            vp = vid + ":" + pid
            if vp in _USB_HIDE_VIDPID:
                continue
            name = d.get("name") or "USB device"
            klass = _usb_class(d.get("cls"), name, vp)
            present = bool(d.get("present"))
            protected = (vp in _USB_PROTECT_VIDPID) or bool(_USB_PROTECT_NAME_RE.search(name))
            channel = None; reason = None
            if vp == USB_BT_VIDPID:
                claimed = True; channel = USB_BT_PORT
                reason = "Bluetooth radio — already passed through to GOSE"
            elif vp in claimed_vp:
                claimed = True; channel = _usb_claims[vp]["port"]
            else:
                claimed = False
            if protected and vp != USB_BT_VIDPID:
                reason = "host input/system device — protected, never claimable"
            if klass == "controller" and vp != USB_BT_VIDPID and not claimed:
                # Controllers go through INPUT PASSTHROUGH (pad_passthrough.py →
                # agent input.pt_*): millisecond latency. usb-redir for a 1 kHz pad
                # measured 4-7 s of input lag — never offer that path for pads.
                reason = "controllers use input passthrough (instant); USB claim deprecated for pads"
            claimable = (present and not protected and not claimed
                         and not (klass == "controller" and vp != USB_BT_VIDPID))
            devs.append({"vid": vid, "pid": pid, "id": vp, "name": name, "class": klass,
                         "is_controller": klass == "controller", "present": present,
                         "claimed": claimed, "protected": protected, "claimable": claimable,
                         "channel": channel, "reason": reason,
                         "known_as": _USB_CONTROLLERS.get(vp)})
        free = [p for p in USB_POOL_PORTS if p not in used_ports]
    devs.sort(key=lambda x: (not x["is_controller"], not x["claimable"], not x["present"], x["name"].lower()))
    return {"ok": True, "devices": devs,
            "channels": {"bt": USB_BT_PORT, "pool": USB_POOL_PORTS, "free": free}}

def _port_listening(port):
    # is QEMU serving this usb-redir channel? (only true after a reboot with the channel pool)
    try:
        s = socket.create_connection(("127.0.0.1", port), 0.6); s.close(); return True
    except Exception:
        return False

def usb_claim(vid, pid):
    vid = (vid or "").lower(); pid = (pid or "").lower()
    if not re.fullmatch(r"[0-9a-f]{4}", vid) or not re.fullmatch(r"[0-9a-f]{4}", pid):
        return {"ok": False, "error": "vid and pid must each be 4 hex digits"}
    vp = vid + ":" + pid
    if vp in _USB_PROTECT_VIDPID:
        return {"ok": False, "error": "device is protected (host input / already passed) and cannot be claimed"}
    if vp in _USB_CONTROLLERS:
        # enforce what usb_list advertises: pads ride input passthrough, not usb-redir
        return {"ok": False, "error": "controllers use input passthrough (instant); "
                                      "USB claim deprecated for pads"}
    with _usb_lock:
        _usb_reap()
        if vp in _usb_claims:
            return {"ok": True, "vidpid": vp, "channel": _usb_claims[vp]["port"], "already": True}
        used = {c["port"] for c in _usb_claims.values()}
        free = [p for p in USB_POOL_PORTS if p not in used]
        if not free:
            return {"ok": False, "error": "no free usb-redir channel (all %d in use)" % len(USB_POOL_PORTS)}
        # The channel must actually be open (QEMU listening). It only is after a reboot with the
        # channel-pool boot script — fail fast & clearly rather than hang if it isn't.
        port = next((p for p in free if _port_listening(p)), None)
        if port is None:
            return {"ok": False, "needs_reboot": True,
                    "error": "usb-redir pool channels (14001-14004) are not open yet — "
                             "reboot the VM with the channel-pool boot script to open them"}
        log = os.path.join(tempfile.gettempdir(), "gose_usbredir_%s_%s.log" % (vid, pid))
        try:
            lf = open(log, "ab")
            proc = subprocess.Popen([_USBREDIR_BIN, "--device", vp, "--to", "127.0.0.1:%d" % port, "-k"],
                                    stdout=lf, stderr=subprocess.STDOUT)
        except Exception as e:
            return {"ok": False, "error": "failed to launch usbredirect: " + str(e)}
        _usb_claims[vp] = {"port": port, "proc": proc}
    return {"ok": True, "vidpid": vp, "channel": port}

def usb_release(vid, pid):
    vid = (vid or "").lower(); pid = (pid or "").lower(); vp = vid + ":" + pid
    with _usb_lock:
        c = _usb_claims.pop(vp, None)
    if not c:
        return {"ok": True, "released": False, "note": "device was not claimed by the host bridge"}
    try:
        c["proc"].terminate()
        try: c["proc"].wait(timeout=3)
        except Exception: c["proc"].kill()
    except Exception:
        pass
    return {"ok": True, "released": True, "vidpid": vp, "freed_channel": c["port"]}

class H(http.server.BaseHTTPRequestHandler):
    def _send(self, obj):
        body = json.dumps(obj).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers(); self.wfile.write(body)

    def do_GET(self):
        route = self.path.split("?")[0]
        if route == "/wifi/scan":
            return self._send(wifi_scan())
        if route == "/wifi/status":
            return self._send(wifi_status())
        if route == "/screencap":
            img = screencap()
            if not img:
                self.send_response(503); self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers(); return
            self.send_response(200)
            self.send_header("Content-Type", "image/jpeg")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(img)))
            self.end_headers(); self.wfile.write(img); return
        if route == "/stream":
            # live MJPEG of the QEMU window (incl games) — so an AI/viewer can watch in real time.
            # on-demand: ffmpeg starts when requested, dies when the client disconnects.
            title = qemu_title()
            if not title:
                self.send_response(503); self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers(); return
            cmd = [FFMPEG, "-loglevel", "quiet", "-f", "gdigrab", "-framerate", "12",
                   "-i", "title=" + title, "-vf", "scale=960:-2", "-f", "mpjpeg", "-q:v", "8", "pipe:1"]
            try:
                proc = subprocess.Popen(cmd, stdout=subprocess.PIPE)
            except Exception:
                self.send_response(500); self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers(); return
            self.send_response(200)
            self.send_header("Content-Type", "multipart/x-mixed-replace;boundary=ffmpeg")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            try:
                while True:
                    chunk = proc.stdout.read(16384)
                    if not chunk:
                        break
                    self.wfile.write(chunk)
            except Exception:
                pass
            finally:
                try: proc.kill()
                except Exception: pass
            return
        if route == "/perf":
            return self._send(perf())
        if route == "/usb":
            return self._send(usb_list())
        if route == "/brightness":
            return self._send(brightness_get())
        if route == "/clip/status":
            return self._send(clip_status())
        if route == "/clip/save":
            from urllib.parse import urlparse, parse_qs
            secs = int(parse_qs(urlparse(self.path).query).get("seconds", ["30"])[0])
            data = clip_save(secs)
            if not data:
                self.send_response(503); self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers(); return
            self.send_response(200)
            self.send_header("Content-Type", "video/mp4")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers(); self.wfile.write(data); return
        return self._send(info())

    def do_POST(self):
        route = self.path.split("?")[0]
        if route == "/clip/start":
            return self._send(clip_start())
        if route == "/clip/stop":
            return self._send(clip_stop())
        try:
            n = int(self.headers.get("Content-Length", 0))
            payload = json.loads(self.rfile.read(n).decode() or "{}")
        except Exception:
            payload = {}
        if route == "/wifi/connect":
            return self._send(wifi_connect(payload.get("ssid"), payload.get("password")))
        if route == "/wifi/disconnect":
            return self._send(wifi_disconnect())
        if route == "/usb/claim":
            return self._send(usb_claim(payload.get("vid"), payload.get("pid")))
        if route == "/usb/release":
            return self._send(usb_release(payload.get("vid"), payload.get("pid")))
        if route == "/brightness":
            return self._send(brightness_set(payload.get("level")))
        self._send({"ok": False, "error": "unknown"})

    def log_message(self, *a):
        pass

class Bridge(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True   # a long-lived /stream must not block battery/gpu/wifi/screencap

if __name__ == "__main__":
    print("GOSE host bridge on 127.0.0.1:8790 (guest sees 10.0.2.2:8790) [threaded]")
    threading.Thread(target=_cpu_sampler, daemon=True).start()   # rolling host CPU%
    Bridge(("127.0.0.1", 8790), H).serve_forever()
