#!/usr/bin/env python3
"""
GOSE Companion — Windows system-tray app (#25) + mobile web server (#74).

Tray features:
  - GOSE icon in the system tray; tooltip shows VM live/stopped + what's playing.
  - Menu: Open GOSE UI | ROM Drop (file picker -> SFTP to VM) | Mobile Server
    toggle | VM Status (submenu) | Quit.
  - ROM Drop auto-detects the target system from file extension (same logic as
    Batocera's es_systems.cfg parser); lets user pick system if ambiguous.

Mobile server (port 8792, off by default):
  - Phone-friendly web app served over LAN/tailnet.
  - View library (pulls /games.json from VM), push a ROM, basic status.
  - Enable/disable from the tray menu.

Dependencies (all installable with pip, stdlib fallbacks where possible):
  pystray        — tray icon (Windows/macOS/Linux)
  Pillow         — tray image rendering (pystray dep anyway)
  paramiko       — SFTP ROM upload
  (tkinter       — stdlib file picker, always present on CPython Windows)

Run: py -3.11 gose_companion.py
     py -3.11 gose_companion.py --no-tray   # headless mobile-only (CI/test)
"""
from __future__ import annotations
import argparse, http.server, io, json, logging, os, re, socket, sys
import threading, time, traceback, urllib.request
from pathlib import Path

LOG = logging.getLogger("companion")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

# ---------------------------------------------------------------------------
# Config / defaults
# ---------------------------------------------------------------------------
VM_AGENT    = os.environ.get("GOSE_AGENT_URL", "http://127.0.0.1:8731")
VM_TOKEN    = os.environ.get("GOSE_TOKEN", "***REMOVED-DEV-TOKEN***")
VM_SSH_HOST = os.environ.get("GOSE_SSH_HOST", "127.0.0.1")
VM_SSH_PORT = int(os.environ.get("GOSE_SSH_PORT", "2222"))
VM_SSH_USER = os.environ.get("GOSE_SSH_USER", "root")
VM_SSH_PASS = os.environ.get("GOSE_SSH_PASS", "linux")
MOBILE_PORT = int(os.environ.get("GOSE_MOBILE_PORT", "8792"))

ROMS_DIR    = "/userdata/roms"    # path inside the VM guest

# Extension -> system mapping: authoritative source is es_systems.cfg in the VM.
# We seed a minimal table here so drag-drop works even before the VM boots
# (the live map is fetched from /games.json systems on first use).
_EXT_SYS_BUILTIN: dict[str, list[str]] = {
    ".nes": ["nes"],    ".fds": ["nes"],
    ".smc": ["snes"],   ".sfc": ["snes"],   ".fig": ["snes"],
    ".gb":  ["gb"],     ".gbc": ["gbc"],
    ".gba": ["gba"],
    ".md":  ["megadrive"], ".gen": ["megadrive"], ".smd": ["megadrive"],
    ".gg":  ["gamegear"],
    ".sms": ["mastersystem"],
    ".pce": ["pcengine"],
    ".n64": ["n64"], ".z64": ["n64"], ".v64": ["n64"],
    ".nds": ["nds"],
    ".iso": ["psx", "ps2"],
    ".bin": ["psx", "megadrive", "snes"],   # ambiguous — needs folder hint
    ".cue": ["psx"],
    ".chd": ["psx", "dreamcast", "arcade"],
    ".zip": ["arcade", "mame"],   # ambiguous
    ".7z":  ["arcade", "mame"],
    ".gdi": ["dreamcast"],
    ".cdi": ["dreamcast"],
    ".a26": ["atari2600"],
    ".a78": ["atari7800"],
    ".lnx": ["lynx"],
    ".ngp": ["ngp"],
    ".ngc": ["ngpc"],
    ".ws":  ["wonderswan"],
    ".wsc": ["wonderswancolor"],
    ".vb":  ["virtualboy"],
    ".ngage":["ngage"],
    ".3ds": ["3ds"],
    ".cia": ["3ds"],
    ".nsp": ["switch"],
    ".xci": ["switch"],
    ".rpx": ["wiiu"],
    ".wad": ["wii"],
    ".gcm": ["gc"],
    ".img": ["amiga"],
    ".adf": ["amiga"],
    ".tap": ["zxspectrum", "c64"],
    ".t64": ["c64"],
    ".d64": ["c64"],
    ".prg": ["c64"],
    ".x68": ["x68000"],
    ".rom": ["msx"],
    ".mx1": ["msx"],
    ".mx2": ["msx"],
    ".dsk": ["amiga", "atarist"],
    ".st":  ["atarist"],
    ".tzx": ["zxspectrum"],
    ".pzx": ["zxspectrum"],
    ".j64": ["jaguar"],
    ".jag": ["jaguar"],
}

# Human-friendly display names for system ids
_SYS_NAMES: dict[str, str] = {
    "nes": "NES", "snes": "SNES", "gb": "Game Boy", "gbc": "Game Boy Color",
    "gba": "Game Boy Advance", "megadrive": "Mega Drive / Genesis",
    "gamegear": "Game Gear", "mastersystem": "Master System",
    "pcengine": "PC Engine", "n64": "Nintendo 64", "nds": "Nintendo DS",
    "psx": "PlayStation 1", "ps2": "PlayStation 2", "dreamcast": "Dreamcast",
    "arcade": "Arcade / MAME", "mame": "MAME", "atari2600": "Atari 2600",
    "atari7800": "Atari 7800", "lynx": "Atari Lynx", "ngp": "Neo Geo Pocket",
    "ngpc": "Neo Geo Pocket Color", "wonderswan": "WonderSwan",
    "wonderswancolor": "WonderSwan Color", "virtualboy": "Virtual Boy",
    "3ds": "Nintendo 3DS", "switch": "Nintendo Switch", "wiiu": "Wii U",
    "wii": "Wii", "gc": "GameCube", "amiga": "Amiga", "c64": "Commodore 64",
    "zxspectrum": "ZX Spectrum", "atarist": "Atari ST", "jaguar": "Jaguar",
    "msx": "MSX", "x68000": "X68000",
}

# ---------------------------------------------------------------------------
# VM agent helpers
# ---------------------------------------------------------------------------
def _agent_get(path: str, timeout: int = 4) -> dict:
    """GET from the VM agent (JSON). Returns {} on any error."""
    try:
        req = urllib.request.Request(
            VM_AGENT + path,
            headers={"X-GOSE-Token": VM_TOKEN} if VM_TOKEN else {},
        )
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read())
    except Exception:
        return {}


def _vm_server_get(path: str, timeout: int = 8) -> dict:
    """GET from the in-VM UI server (127.0.0.1:8780 inside the guest) via SSH exec.
    Used for endpoints that only the in-VM server exposes (e.g. /games.json).
    Returns {} on any error.
    """
    try:
        import paramiko
        cli = paramiko.SSHClient()
        cli.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        cli.connect(VM_SSH_HOST, port=VM_SSH_PORT,
                    username=VM_SSH_USER, password=VM_SSH_PASS,
                    look_for_keys=False, allow_agent=False, timeout=10)
        _, out, _ = cli.exec_command(f"curl -s http://127.0.0.1:8780{path}",
                                      timeout=timeout)
        data = json.loads(out.read().decode())
        cli.close()
        return data
    except Exception:
        return {}


def vm_live() -> bool:
    """True if the agent is reachable."""
    try:
        s = socket.create_connection(
            (VM_AGENT.split("://")[1].split(":")[0],
             int(VM_AGENT.split(":")[-1])), timeout=1.0)
        s.close()
        return True
    except Exception:
        return False


def vm_status_summary() -> str:
    """One-liner for the tray tooltip."""
    if not vm_live():
        return "GOSE — VM offline"
    st = _agent_get("/game/running", timeout=3)
    if st.get("running"):
        game = st.get("name") or st.get("game") or "a game"
        return f"GOSE — playing: {game}"
    st2 = _agent_get("/status.json", timeout=3)
    mem = st2.get("mem", {})
    if mem:
        avail = mem.get("MemAvailable", 0) // 1024
        return f"GOSE — idle | {avail} MB free"
    return "GOSE — online"


# ---------------------------------------------------------------------------
# Extension -> system resolution (uses live VM library when available)
# ---------------------------------------------------------------------------
_live_ext_map: dict[str, list[str]] | None = None
_live_ext_map_ts = 0.0
_LIVE_CACHE_SECS = 60.0


def _refresh_live_map():
    global _live_ext_map, _live_ext_map_ts
    if time.time() - _live_ext_map_ts < _LIVE_CACHE_SECS:
        return
    data = _agent_get("/games.json", timeout=5)
    if not data.get("systems"):
        return
    m: dict[str, list[str]] = {}
    for sys_entry in data["systems"]:
        sid = sys_entry.get("system", "")
        for g in sys_entry.get("games", []):
            ext = os.path.splitext(g.get("file", ""))[1].lower()
            if ext and sid:
                m.setdefault(ext, [])
                if sid not in m[ext]:
                    m[ext].append(sid)
    if m:
        _live_ext_map = m
        _live_ext_map_ts = time.time()


def guess_system(filepath: str) -> list[str]:
    """Return a list of candidate system ids for this file (most-likely first).
    Empty list = unrecognised extension."""
    _refresh_live_map()
    ext = Path(filepath).suffix.lower()
    # Parent folder name as strong hint (Batocera layout: roms/<system>/<file>)
    parent = Path(filepath).parent.name.lower()
    live = _live_ext_map or {}
    candidates: list[str] = list(live.get(ext, [])) or list(_EXT_SYS_BUILTIN.get(ext, []))
    if parent in _SYS_NAMES and parent in candidates:
        candidates = [parent] + [c for c in candidates if c != parent]
    elif parent in _SYS_NAMES:
        candidates = [parent] + candidates
    return candidates


# ---------------------------------------------------------------------------
# ROM upload via SFTP
# ---------------------------------------------------------------------------
def sftp_upload_rom(local_path: str, system: str,
                    progress_cb=None) -> tuple[bool, str]:
    """Upload a ROM to /userdata/roms/<system>/ on the VM via SFTP.

    Returns (ok, message). progress_cb(bytes_done, total) called if provided.
    """
    try:
        import paramiko
    except ImportError:
        return False, "paramiko not installed — run: pip install paramiko"

    filename = os.path.basename(local_path)
    remote_dir = f"{ROMS_DIR}/{system}"
    remote_path = f"{remote_dir}/{filename}"

    try:
        cli = paramiko.SSHClient()
        cli.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        cli.connect(VM_SSH_HOST, port=VM_SSH_PORT,
                    username=VM_SSH_USER, password=VM_SSH_PASS,
                    look_for_keys=False, allow_agent=False, timeout=15)
        sftp = cli.open_sftp()

        # Ensure the target directory exists
        try:
            sftp.stat(remote_dir)
        except FileNotFoundError:
            sftp.mkdir(remote_dir)

        # Upload with progress
        total = os.path.getsize(local_path)
        transferred = [0]

        def _cb(done, _total):
            transferred[0] = done
            if progress_cb:
                progress_cb(done, total)

        sftp.put(local_path, remote_path, callback=_cb)
        sftp.close()
        cli.close()
        LOG.info("ROM uploaded: %s -> %s", filename, remote_path)
        return True, f"Uploaded {filename} to {system} ({total // 1024} KB)"
    except Exception as e:
        LOG.error("SFTP upload failed: %s", e)
        return False, f"Upload failed: {e}"


# ---------------------------------------------------------------------------
# Mobile web server (#74)
# ---------------------------------------------------------------------------
_MOBILE_HTML_DIR = Path(__file__).parent / "gose_companion_mobile"
_MOBILE_HTML_DIR.mkdir(exist_ok=True)

MOBILE_INDEX = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,maximum-scale=1">
<meta name="apple-mobile-web-app-capable" content="yes">
<title>GOSE</title>
<style>
:root{--accent:#5cd0ff;--bg:#07080f;--surface:#0f1018;--text:#eaf0ff;--muted:#8b92b0;--line:#ffffff18}
*{box-sizing:border-box;-webkit-tap-highlight-color:transparent}
html,body{margin:0;min-height:100%;background:var(--bg);color:var(--text);font-family:system-ui,sans-serif}
body{padding:0 0 32px}
.header{display:flex;align-items:center;gap:10px;padding:14px 16px;background:var(--surface);
  border-bottom:1px solid var(--line);position:sticky;top:0;z-index:10}
.header h1{margin:0;font-size:17px;font-weight:700;flex:1}
.dot{width:9px;height:9px;border-radius:50%;flex:none}
.dot.on{background:#3dffb0}.dot.off{background:#ff5a6e}
.badge{font-size:11px;padding:2px 8px;border-radius:20px;background:var(--line);color:var(--muted)}
.section{padding:14px 16px 0}
.section h2{margin:0 0 10px;font-size:13px;text-transform:uppercase;letter-spacing:.08em;color:var(--muted)}
.card{background:var(--surface);border:1px solid var(--line);border-radius:14px;overflow:hidden;margin-bottom:10px}
.row{display:flex;align-items:center;padding:11px 14px;gap:12px;border-bottom:1px solid var(--line)}
.row:last-child{border-bottom:none}
.row .label{flex:1;font-size:14px}
.row .val{font-size:13px;color:var(--muted)}
.sys-pill{display:inline-block;font-size:11px;padding:2px 8px;border-radius:20px;background:#5cd0ff18;
  color:var(--accent);border:1px solid #5cd0ff26;margin:0 3px 4px 0}
.game-row{padding:10px 14px;border-bottom:1px solid var(--line);display:flex;align-items:center;gap:10px}
.game-row:last-child{border-bottom:none}
.game-name{font-size:14px;flex:1;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.game-sys{font-size:11px;color:var(--muted)}
.btn{display:block;width:100%;padding:13px;border-radius:12px;border:none;background:var(--accent);
  color:#07080f;font-size:15px;font-weight:700;cursor:pointer;margin-top:8px}
.btn:active{opacity:.75}
.btn.sec{background:var(--surface);color:var(--text);border:1px solid var(--line)}
.upload-zone{border:2px dashed var(--line);border-radius:14px;padding:24px 16px;text-align:center;
  color:var(--muted);font-size:13px;transition:border-color .15s}
.upload-zone.drag{border-color:var(--accent);color:var(--accent)}
.progress-bar{height:4px;border-radius:4px;background:var(--line);margin-top:10px;overflow:hidden}
.progress-fill{height:100%;background:var(--accent);width:0%;transition:width .2s}
.msg{margin-top:10px;font-size:13px;color:var(--muted);text-align:center;min-height:18px}
.msg.ok{color:#3dffb0}.msg.err{color:#ff5a6e}
select,input[type=text]{background:var(--surface);border:1px solid var(--line);color:var(--text);
  border-radius:9px;padding:8px 12px;font-size:14px;width:100%;margin-bottom:8px}
</style>
</head>
<body>
<div class="header">
  <span class="dot" id="dot"></span>
  <h1>GOSE</h1>
  <span class="badge" id="badge">...</span>
</div>

<div class="section" id="sec-status">
  <h2>Status</h2>
  <div class="card">
    <div class="row"><span class="label">Playing</span><span class="val" id="s-playing">—</span></div>
    <div class="row"><span class="label">Memory free</span><span class="val" id="s-mem">—</span></div>
    <div class="row"><span class="label">Uptime</span><span class="val" id="s-uptime">—</span></div>
  </div>
</div>

<div class="section" id="sec-library">
  <h2>Library</h2>
  <div class="card" id="lib-card">
    <div class="row"><span class="label" style="color:var(--muted)">Loading...</span></div>
  </div>
</div>

<div class="section">
  <h2>ROM Drop</h2>
  <div class="card" style="padding:14px">
    <div class="upload-zone" id="drop-zone">
      <div style="font-size:22px;margin-bottom:6px">+</div>
      <div>Tap to pick a ROM file</div>
      <div style="font-size:11px;margin-top:4px">or drag &amp; drop</div>
      <input type="file" id="file-input" style="display:none">
    </div>
    <select id="sys-select" style="display:none;margin-top:10px"></select>
    <div class="progress-bar" id="progress-bar" style="display:none">
      <div class="progress-fill" id="progress-fill"></div>
    </div>
    <div class="msg" id="upload-msg"></div>
    <button class="btn" id="upload-btn" style="display:none">Upload to GOSE</button>
  </div>
</div>

<script>
const $ = id => document.getElementById(id);

// ---- status polling ----
async function fetchJSON(url) {
  try { const r = await fetch(url, {signal: AbortSignal.timeout(4000)}); return await r.json(); }
  catch { return null; }
}

function fmtUptime(s) {
  if (!s) return '—';
  const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60);
  return h > 0 ? `${h}h ${m}m` : `${m}m`;
}

async function refreshStatus() {
  const live = await fetchJSON('/api/live');
  const isLive = live && live.ok;
  $('dot').className = 'dot ' + (isLive ? 'on' : 'off');
  $('badge').textContent = isLive ? 'online' : 'offline';
  if (!isLive) {
    $('s-playing').textContent = '—';
    $('s-mem').textContent = '—';
    $('s-uptime').textContent = '—';
    return;
  }
  const [run, st] = await Promise.all([fetchJSON('/api/running'), fetchJSON('/api/status')]);
  $('s-playing').textContent = run && run.running ? (run.name || run.game || 'yes') : '—';
  if (st && st.mem) {
    const avail = Math.round(st.mem.MemAvailable / 1024);
    const total = Math.round(st.mem.MemTotal / 1024);
    $('s-mem').textContent = `${avail} / ${total} MB`;
  }
  $('s-uptime').textContent = fmtUptime(st && st.uptime_s);
}

async function refreshLibrary() {
  const data = await fetchJSON('/api/games');
  const card = $('lib-card');
  if (!data || !data.systems || data.systems.length === 0) {
    card.innerHTML = '<div class="row"><span class="label" style="color:var(--muted)">Library unavailable</span></div>';
    return;
  }
  let html = '';
  for (const sys of data.systems) {
    html += `<div class="row"><span class="label">${sys.name || sys.system}</span>
      <span class="val">${sys.games.length} game${sys.games.length === 1 ? '' : 's'}</span></div>`;
    // show first 4 games inline
    for (const g of sys.games.slice(0, 4)) {
      html += `<div class="game-row">
        <span class="game-name">${g.name}</span>
        <span class="game-sys">${sys.system}</span>
      </div>`;
    }
    if (sys.games.length > 4) {
      html += `<div class="row" style="justify-content:center">
        <span class="val">+ ${sys.games.length - 4} more</span></div>`;
    }
  }
  card.innerHTML = html;
}

// ---- initial load + poll ----
refreshStatus();
refreshLibrary();
setInterval(refreshStatus, 8000);
setInterval(refreshLibrary, 30000);

// ---- ROM drop / upload ----
let _file = null;
const dropZone = $('drop-zone');
const fileInput = $('file-input');
const sysSelect = $('sys-select');
const uploadBtn = $('upload-btn');
const uploadMsg = $('upload-msg');
const progressBar = $('progress-bar');
const progressFill = $('progress-fill');

dropZone.addEventListener('click', () => fileInput.click());
fileInput.addEventListener('change', e => { if (e.target.files[0]) handleFile(e.target.files[0]); });

dropZone.addEventListener('dragover', e => { e.preventDefault(); dropZone.classList.add('drag'); });
dropZone.addEventListener('dragleave', () => dropZone.classList.remove('drag'));
dropZone.addEventListener('drop', e => {
  e.preventDefault(); dropZone.classList.remove('drag');
  if (e.dataTransfer.files[0]) handleFile(e.dataTransfer.files[0]);
});

async function handleFile(f) {
  _file = f;
  uploadMsg.textContent = '';
  uploadMsg.className = 'msg';
  progressBar.style.display = 'none';
  const ext = f.name.substring(f.name.lastIndexOf('.')).toLowerCase();
  const guess = await fetchJSON('/api/guess_system?ext=' + encodeURIComponent(ext));
  const systems = (guess && guess.systems) || [];
  sysSelect.innerHTML = systems.length
    ? systems.map(s => `<option value="${s.id}">${s.name}</option>`).join('')
      + '<option value="_other">Other (type below)...</option>'
    : '<option value="_other">Select system...</option>';
  sysSelect.style.display = 'block';
  uploadBtn.style.display = 'block';
  uploadMsg.textContent = f.name + (systems.length ? ` — ${systems.length} system match(es)` : ' — unknown extension');
}

uploadBtn.addEventListener('click', async () => {
  if (!_file) return;
  let sys = sysSelect.value;
  if (sys === '_other') {
    sys = prompt('System ID (e.g. nes, snes, psx, n64):');
    if (!sys) return;
  }
  uploadBtn.disabled = true;
  uploadMsg.textContent = 'Uploading...';
  uploadMsg.className = 'msg';
  progressBar.style.display = 'block';
  progressFill.style.width = '0%';

  const form = new FormData();
  form.append('file', _file);
  form.append('system', sys);

  const xhr = new XMLHttpRequest();
  xhr.open('POST', '/api/upload_rom');
  xhr.upload.onprogress = e => {
    if (e.lengthComputable)
      progressFill.style.width = Math.round(e.loaded / e.total * 100) + '%';
  };
  xhr.onload = () => {
    uploadBtn.disabled = false;
    progressFill.style.width = '100%';
    let res;
    try { res = JSON.parse(xhr.responseText); } catch { res = {ok: false, error: xhr.responseText}; }
    uploadMsg.textContent = res.ok ? (res.message || 'Uploaded.') : (res.error || 'Upload failed');
    uploadMsg.className = 'msg ' + (res.ok ? 'ok' : 'err');
  };
  xhr.onerror = () => {
    uploadBtn.disabled = false;
    uploadMsg.textContent = 'Network error';
    uploadMsg.className = 'msg err';
  };
  xhr.send(form);
});
</script>
</body>
</html>
"""


class _MobileHandler(http.server.BaseHTTPRequestHandler):
    """Tiny HTTP server — serves the mobile web UI + proxies key VM endpoints."""

    def log_message(self, *a):
        pass   # suppress per-request noise; errors go to LOG

    def _send(self, code: int, ctype: str, body: bytes):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, obj: dict, code: int = 200):
        self._send(code, "application/json", json.dumps(obj).encode())

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_GET(self):
        path = self.path.split("?")[0]
        if path in ("/", "/index.html"):
            self._send(200, "text/html; charset=utf-8",
                       MOBILE_INDEX.encode("utf-8"))
            return

        if path == "/api/live":
            self._json({"ok": vm_live()})
            return

        if path == "/api/status":
            self._json(_agent_get("/status.json"))
            return

        if path == "/api/running":
            self._json(_agent_get("/game/running"))
            return

        if path == "/api/games":
            # The full library (with system names + game lists) lives in the
            # in-VM UI server (:8780). Fall back to the agent path if SSH fails.
            data = _vm_server_get("/games.json")
            if not data.get("systems"):
                data = _agent_get("/games.json", timeout=8)
            self._json(data)
            return

        if path == "/api/guess_system":
            from urllib.parse import urlparse, parse_qs
            qs = parse_qs(urlparse(self.path).query)
            ext = (qs.get("ext") or [""])[0].lower()
            if not ext.startswith("."):
                ext = "." + ext
            cands = guess_system("file" + ext)   # fake path with just extension
            systems = [{"id": s, "name": _SYS_NAMES.get(s, s)} for s in cands]
            self._json({"ext": ext, "systems": systems})
            return

        self._json({"error": "not found"}, 404)

    def do_POST(self):
        path = self.path.split("?")[0]

        if path == "/api/upload_rom":
            try:
                self._handle_rom_upload()
            except Exception as e:
                LOG.error("ROM upload error: %s\n%s", e, traceback.format_exc())
                self._json({"ok": False, "error": str(e)}, 500)
            return

        self._json({"error": "not found"}, 404)

    def _handle_rom_upload(self):
        """Parse multipart/form-data ROM upload, SFTP it to the VM."""
        ctype = self.headers.get("Content-Type", "")
        cl = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(cl)

        # Extract boundary
        m = re.search(r"boundary=([^\s;]+)", ctype)
        if not m:
            self._json({"ok": False, "error": "no multipart boundary"}, 400)
            return
        boundary = ("--" + m.group(1)).encode()

        # Parse parts: look for 'file' and 'system' fields
        parts = raw.split(boundary)
        file_data: bytes | None = None
        filename: str = "rom.bin"
        system: str = ""

        for part in parts:
            if not part.strip() or part.strip() == b"--":
                continue
            # Split headers from body at first blank line
            if b"\r\n\r\n" in part:
                headers_raw, body = part.split(b"\r\n\r\n", 1)
            elif b"\n\n" in part:
                headers_raw, body = part.split(b"\n\n", 1)
            else:
                continue
            body = body.rstrip(b"\r\n")
            hdr = headers_raw.decode("utf-8", "replace")
            name_m = re.search(r'name="([^"]+)"', hdr)
            if not name_m:
                continue
            field = name_m.group(1)
            if field == "system":
                system = body.decode("utf-8", "replace").strip()
            elif field == "file":
                fn_m = re.search(r'filename="([^"]+)"', hdr)
                filename = fn_m.group(1) if fn_m else "rom.bin"
                file_data = body

        if file_data is None:
            self._json({"ok": False, "error": "no file in upload"}, 400)
            return
        if not system:
            # last-resort guess from filename extension
            cands = guess_system(filename)
            system = cands[0] if cands else "ports"

        # Write to a temp file, then SFTP upload
        import tempfile
        # Write the upload to a temp file named exactly as the original ROM so
        # sftp_upload_rom uses the right destination filename in the VM.
        tmp_dir = tempfile.mkdtemp()
        tmp_path = os.path.join(tmp_dir, filename)
        try:
            with open(tmp_path, "wb") as f:
                f.write(file_data)
            ok, msg = sftp_upload_rom(tmp_path, system)
            self._json({"ok": ok, "message": msg, "system": system, "file": filename})
        finally:
            try:
                import shutil
                shutil.rmtree(tmp_dir, ignore_errors=True)
            except Exception:
                pass


_mobile_server: http.server.HTTPServer | None = None
_mobile_thread: threading.Thread | None = None
_mobile_lock = threading.Lock()


def mobile_start(port: int | None = None) -> tuple[bool, str]:
    """Start the mobile web server. Returns (ok, address_or_error)."""
    global _mobile_server, _mobile_thread
    port = port if port is not None else MOBILE_PORT
    with _mobile_lock:
        if _mobile_server is not None:
            return True, f"already running on port {port}"
        try:
            srv = http.server.ThreadingHTTPServer(("0.0.0.0", port),
                                                  _MobileHandler)
            srv.daemon_threads = True
            _mobile_server = srv
        except OSError as e:
            return False, f"Could not bind port {port}: {e}"

        def _serve():
            LOG.info("Mobile server listening on :%d", port)
            try:
                srv.serve_forever()
            except Exception:
                pass

        t = threading.Thread(target=_serve, daemon=True, name="mobile-srv")
        t.start()
        _mobile_thread = t

        # Find LAN IP for display
        try:
            s = socket.socket()
            s.connect(("8.8.8.8", 80))
            ip = s.getsockname()[0]
            s.close()
        except Exception:
            ip = "localhost"
        return True, f"http://{ip}:{MOBILE_PORT}"


def mobile_stop() -> bool:
    global _mobile_server, _mobile_thread
    with _mobile_lock:
        if _mobile_server is None:
            return False
        try:
            _mobile_server.shutdown()
        except Exception:
            pass
        _mobile_server = None
        _mobile_thread = None
        return True


def mobile_running() -> bool:
    with _mobile_lock:
        return _mobile_server is not None


# ---------------------------------------------------------------------------
# ROM picker (tkinter) — shared between tray action and any caller
# ---------------------------------------------------------------------------
def pick_and_upload_rom(parent_window=None) -> str | None:
    """Open a file picker, detect system, SFTP upload. Returns status message."""
    try:
        import tkinter as tk
        from tkinter import filedialog, messagebox, simpledialog
    except ImportError:
        return "tkinter not available"

    root = tk.Tk()
    root.withdraw()
    root.attributes("-topmost", True)

    filetypes = [
        ("ROM files", "*.nes *.snes *.smc *.sfc *.gba *.gb *.gbc *.md *.gen "
                      "*.n64 *.z64 *.iso *.bin *.cue *.chd *.zip *.7z *.psx "
                      "*.nds *.gdi *.a26 *.ws *.wsc *.nsp *.xci *.dsk *.st "
                      "*.tap *.d64 *.prg *.tzx"),
        ("All files", "*.*"),
    ]
    path = filedialog.askopenfilename(
        title="Pick a ROM to send to GOSE",
        filetypes=filetypes,
        parent=root,
    )
    if not path:
        root.destroy()
        return None

    if not vm_live():
        messagebox.showerror("GOSE offline",
                             "The GOSE VM is not reachable.\nBoot the VM first.",
                             parent=root)
        root.destroy()
        return "VM offline"

    cands = guess_system(path)
    if not cands:
        system = simpledialog.askstring(
            "System", "System ID (e.g. nes, snes, psx, n64):",
            parent=root)
        if not system:
            root.destroy()
            return "Cancelled"
    elif len(cands) == 1:
        system = cands[0]
    else:
        # Multiple candidates — let user choose via dialog
        import tkinter.ttk as ttk
        dlg = tk.Toplevel(root)
        dlg.title("Pick system")
        dlg.attributes("-topmost", True)
        dlg.resizable(False, False)
        tk.Label(dlg, text=f"Multiple systems match {Path(path).suffix}.\nChoose one:",
                 padx=16, pady=10).pack()
        var = tk.StringVar(value=cands[0])
        for c in cands:
            tk.Radiobutton(dlg, text=_SYS_NAMES.get(c, c),
                           variable=var, value=c, padx=16).pack(anchor="w")
        tk.Button(dlg, text="OK", command=dlg.destroy, padx=16, pady=6).pack(pady=8)
        root.wait_window(dlg)
        system = var.get()

    # Upload with a simple progress window
    prog = tk.Toplevel(root)
    prog.title("Uploading...")
    prog.attributes("-topmost", True)
    prog.resizable(False, False)
    lbl = tk.Label(prog, text=f"Uploading {Path(path).name}...", padx=20, pady=14)
    lbl.pack()

    try:
        import tkinter.ttk as ttk
        bar = ttk.Progressbar(prog, length=300, mode="determinate")
        bar.pack(padx=20, pady=(0, 14))

        def _progress(done, total):
            if total:
                bar["value"] = done / total * 100
            prog.update()
    except Exception:
        _progress = None

    root.update()
    ok, msg = sftp_upload_rom(path, system, progress_cb=_progress)
    prog.destroy()

    if ok:
        messagebox.showinfo("ROM uploaded", msg, parent=root)
    else:
        messagebox.showerror("Upload failed", msg, parent=root)

    root.destroy()
    return msg


# ---------------------------------------------------------------------------
# System tray (#25)
# ---------------------------------------------------------------------------
def _make_icon_image(size: int = 64) -> "PIL.Image.Image":
    """Generate a simple GOSE gem icon for the tray (no asset file needed)."""
    from PIL import Image, ImageDraw
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    cx, cy = size // 2, size // 2
    r = size // 2 - 2
    # Draw a hexagon gem shape
    import math
    pts = [(cx + r * math.cos(math.pi / 2 + i * math.pi / 3),
            cy + r * math.sin(math.pi / 2 + i * math.pi / 3)) for i in range(6)]
    d.polygon(pts, fill=(0x5c, 0xd0, 0xff, 240))
    # Inner darker facet
    r2 = r * 0.6
    pts2 = [(cx + r2 * math.cos(math.pi / 2 + i * math.pi / 3),
             cy + r2 * math.sin(math.pi / 2 + i * math.pi / 3)) for i in range(6)]
    d.polygon(pts2, fill=(0x07, 0x25, 0x50, 230))
    return img


def _tray_status_label() -> str:
    return vm_status_summary()


def run_tray():
    """Entry point for the tray app (blocking until quit)."""
    try:
        import pystray
        from PIL import Image
    except ImportError:
        LOG.error("pystray and/or Pillow not installed. Run: pip install pystray Pillow")
        sys.exit(1)

    icon_img = _make_icon_image(64)

    # Build the context menu (rebuilt on every open so status is fresh)
    def make_menu():
        mobile_label = (
            f"Stop Mobile Server" if mobile_running()
            else "Start Mobile Server"
        )
        return pystray.Menu(
            pystray.MenuItem("Open GOSE UI",    action_open_ui, default=True),
            pystray.MenuItem("ROM Drop...",      action_rom_drop),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem(mobile_label,       action_toggle_mobile),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem(_tray_status_label, pystray.Menu(
                pystray.MenuItem("Refresh", action_refresh_status),
            )),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Quit",             action_quit),
        )

    icon = pystray.Icon("GOSE Companion", icon_img, "GOSE Companion", menu=make_menu())

    def action_open_ui(icon, item):
        import webbrowser
        webbrowser.open(VM_AGENT + "/")

    def action_rom_drop(icon, item):
        t = threading.Thread(target=pick_and_upload_rom, daemon=True)
        t.start()

    def action_toggle_mobile(icon, item):
        if mobile_running():
            mobile_stop()
            icon.notify("Mobile server stopped.")
            LOG.info("Mobile server stopped.")
        else:
            ok, addr = mobile_start()
            if ok:
                icon.notify(f"Mobile server started.\n{addr}")
                LOG.info("Mobile server at %s", addr)
            else:
                icon.notify(f"Mobile server failed: {addr}")
                LOG.error("Mobile server failed: %s", addr)
        icon.menu = make_menu()

    def action_refresh_status(icon, item):
        icon.title = vm_status_summary()

    def action_quit(icon, item):
        mobile_stop()
        icon.stop()

    # Background: refresh tooltip every 10s
    def _tooltip_loop():
        while True:
            time.sleep(10)
            try:
                icon.title = vm_status_summary()
            except Exception:
                pass

    threading.Thread(target=_tooltip_loop, daemon=True, name="tooltip").start()

    LOG.info("GOSE Companion tray started. Right-click the tray icon.")
    icon.run()


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser(description="GOSE Companion — tray + mobile server")
    ap.add_argument("--no-tray", action="store_true",
                    help="Headless mode: start mobile server only (no tray icon)")
    ap.add_argument("--mobile", action="store_true",
                    help="Also start mobile server immediately (tray mode)")
    ap.add_argument("--port", type=int, default=MOBILE_PORT,
                    help=f"Mobile server port (default {MOBILE_PORT})")
    args = ap.parse_args()

    if args.no_tray:
        # Headless: just run the mobile server (useful for CI or remote-only)
        ok, addr = mobile_start(port=args.port)
        if not ok:
            LOG.error("Mobile server failed: %s", addr)
            sys.exit(1)
        LOG.info("GOSE mobile companion at %s  (Ctrl-C to stop)", addr)
        try:
            while True:
                time.sleep(3600)
        except KeyboardInterrupt:
            pass
        return

    if args.mobile:
        ok, addr = mobile_start(port=args.port)
        if ok:
            LOG.info("Mobile server pre-started: %s", addr)
        else:
            LOG.warning("Mobile server failed to pre-start: %s", addr)

    run_tray()


if __name__ == "__main__":
    main()
