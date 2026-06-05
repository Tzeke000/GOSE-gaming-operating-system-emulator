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
        s = socket.create_connection(("8.8.8.8", 53), 1.2); s.close()
        _net["online"] = True
    except Exception:
        _net["online"] = False
    return _net["online"]

def battery_ps():
    # fallback when psutil is missing/None: read battery via PowerShell (CIM)
    try:
        out = subprocess.run(["powershell", "-NoProfile", "-Command",
            "$b=Get-CimInstance Win32_Battery; if($b){\"$($b.EstimatedChargeRemaining),$($b.BatteryStatus)\"}else{'none'}"],
            capture_output=True, text=True, timeout=4).stdout.strip()
        if out and out != "none":
            pct, status = out.split(",")
            # BatteryStatus 2 = AC/charging; 1 = discharging
            return {"has_battery": True, "battery_pct": int(pct), "charging": status.strip() in ("2", "3", "6", "7", "8")}
    except Exception:
        pass
    return None

def info():
    out = {"ok": True, "online": online(), "has_battery": False,
           "battery_pct": None, "charging": None}
    got = False
    if psutil:
        try:
            b = psutil.sensors_battery()
            if b is not None:
                out.update(has_battery=True, battery_pct=round(b.percent),
                           charging=bool(b.power_plugged)); got = True
        except Exception:
            pass
    if not got:
        bp = battery_ps()
        if bp:
            out.update(bp)
    out.update(gpu())
    return out

FFMPEG = (r"C:\Users\Tzeke\AppData\Local\Microsoft\WinGet\Packages"
          r"\Gyan.FFmpeg_Microsoft.Winget.Source_8wekyb3d8bbwe\ffmpeg-8.1-full_build\bin\ffmpeg.exe")
if not os.path.isfile(FFMPEG):
    FFMPEG = "ffmpeg"

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
        self._send({"ok": False, "error": "unknown"})

    def log_message(self, *a):
        pass

class Bridge(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True   # a long-lived /stream must not block battery/gpu/wifi/screencap

if __name__ == "__main__":
    print("GOSE host bridge on 127.0.0.1:8790 (guest sees 10.0.2.2:8790) [threaded]")
    Bridge(("127.0.0.1", 8790), H).serve_forever()
