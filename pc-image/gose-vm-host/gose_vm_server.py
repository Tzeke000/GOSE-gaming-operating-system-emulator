#!/usr/bin/env python3
# In-VM server: serves the GOSE UI + /status.json with REAL telemetry from the
# local agent (127.0.0.1:8731 = loopback in-guest = no token needed).
import http.server, socketserver, json, socket, functools, os, urllib.request, mimetypes, shutil, subprocess, threading, collections, time, secrets
import logging, logging.handlers, traceback, re
ROOT = "/userdata/gose-ui"
FS_ROOT = "/userdata"   # Files app is rooted here (the data partition)
ROMS = "/userdata/roms"
# the agent now requires a token even on loopback (set via GOSE_AGENT_TOKEN)
TOKEN = os.environ.get("GOSE_AGENT_TOKEN") or "***REMOVED-DEV-TOKEN***"

# ---- production hardening: logging / error-tracking / rate-limit / atomic writes / version ----
VERSION = {"version": "0.6", "build": "2026-06-05", "base": "Batocera 43.1 (x86_64)"}
START_T = time.time()
LOG = logging.getLogger("gose")
LOG.setLevel(logging.INFO)
try:
    _h = logging.handlers.RotatingFileHandler(ROOT + "/gose.log", maxBytes=524288, backupCount=2)
    _h.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    LOG.addHandler(_h)
except Exception:
    pass

def write_json_atomic(path, obj):
    # crash-safe write: temp + atomic rename, so a crash mid-write can't corrupt the file
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(obj, f)
        f.flush(); os.fsync(f.fileno())
    os.replace(tmp, path)

_RL = {}
_RL_LOCK = threading.Lock()
def rate_ok(key, limit, window):
    # simple sliding-window limiter on expensive endpoints (capture/scan/launch/install)
    now = time.time()
    with _RL_LOCK:
        q = _RL.setdefault(key, [])
        while q and q[0] < now - window:
            q.pop(0)
        if len(q) >= limit:
            return False
        q.append(now); return True

_LIMITS = {"/capture/shot": (20, 60), "/capture/clip": (8, 60), "/capture/buffer": (20, 60),
           "/net/scan": (10, 60), "/launch": (30, 60), "/store/install": (20, 60),
           "/splice/cut": (10, 120), "/fs/op": (60, 60), "/scrape": (6, 120),
           "/store/uninstall": (15, 60), "/ai/request": (6, 60),
           "/emulators/install": (10, 60), "/emulators/uninstall": (15, 60),
           "/games/install": (12, 60),
           "/game/screenshot": (30, 60), "/game/record/toggle": (12, 60),
           "/system/backup": (6, 120), "/system/restore": (4, 120), "/system/factory_reset": (3, 300)}

_SKIPDIRS = {"images", "videos", "manuals", "media", "downloaded_images", "downloaded_media"}
_SKIPEXT = {".txt", ".xml", ".cfg", ".dat", ".jpg", ".jpeg", ".png", ".mp4", ".srm", ".state"}
# friendly names for common systems
_SYS = {"nes": "NES", "snes": "SNES", "megadrive": "Genesis", "gba": "Game Boy Advance",
        "gb": "Game Boy", "gbc": "Game Boy Color", "n64": "Nintendo 64", "psx": "PlayStation",
        "ps2": "PlayStation 2", "psp": "PSP", "gamecube": "GameCube", "dreamcast": "Dreamcast",
        "c64": "Commodore 64", "pcengine": "PC Engine", "nds": "Nintendo DS", "mame": "Arcade"}

_THEME_LOGOS = "/usr/share/emulationstation/themes/es-theme-carbon/art/logos"

def system_logo_path(system):
    # console/emulator art for the Library — carbon theme ships a logo per system. Prefer the white
    # "-w" variant (reads well on GOSE's dark UI), then plain svg, then png.
    if not system:
        return None
    for name in (system + "-w.svg", system + ".svg", system + ".png"):
        p = os.path.join(_THEME_LOGOS, name)
        if os.path.isfile(p):
            return p
    return None

def list_games():
    try:
        favset = _fav_set()
        systems = []
        for sysname in sorted(os.listdir(ROMS)):
            d = os.path.join(ROMS, sysname)
            if not os.path.isdir(d):
                continue
            imgdir = os.path.join(d, "images")
            def find_img(stem):
                for suf in ("-image.png", "-thumb.png", "-image.jpg", ".png"):
                    p = os.path.join(imgdir, stem + suf)
                    if os.path.isfile(p):
                        return p
                return None
            games = []
            for f in os.listdir(d):
                if f.startswith(".") or f in _SKIPDIRS or "gamelist" in f:
                    continue
                p = os.path.join(d, f)
                if os.path.isdir(p):
                    continue
                if os.path.splitext(f)[1].lower() in _SKIPEXT:
                    continue
                stem = os.path.splitext(f)[0]
                games.append({"name": stem, "img": find_img(stem),
                              "fav": (sysname, stem) in favset})
            if games:
                systems.append({"system": sysname, "name": _SYS.get(sysname, sysname),
                                "logo": ("/syslogo?system=" + sysname) if system_logo_path(sysname) else None,
                                "games": sorted(games, key=lambda g: g["name"].lower())})
        return {"ok": True, "systems": systems,
                "total": sum(len(s["games"]) for s in systems)}
    except Exception as e:
        return {"ok": False, "error": str(e)}

RECENT_F = "/userdata/gose-ui/recent.json"

def _game_img(system, game):
    d = os.path.join(ROMS, system, "images")
    for suf in ("-image.png", "-thumb.png", "-image.jpg", ".png"):
        p = os.path.join(d, game + suf)
        if os.path.isfile(p):
            return p
    return None

PLAYTIME_F = "/userdata/gose-ui/playtime.json"

def record_recent(system, game):
    # remember launched games (newest first, deduped) + play count, so the Library/home can show
    # "recently played" + usage stats
    try:
        try:
            rec = json.load(open(RECENT_F))
        except Exception:
            rec = []
        plays = next((r.get("plays", 0) for r in rec
                      if r.get("system") == system and r.get("game") == game), 0)
        rec = [r for r in rec if not (r.get("system") == system and r.get("game") == game)]
        rec.insert(0, {"system": system, "game": game, "name": game, "img": _game_img(system, game),
                       "sysname": _SYS.get(system, system), "t": int(time.time()), "plays": plays + 1})
        write_json_atomic(RECENT_F, rec[:24])
    except Exception:
        pass

def _playtime():
    try:
        return json.load(open(PLAYTIME_F))
    except Exception:
        return {}

def recent_games():
    try:
        rec = json.load(open(RECENT_F)); pt = _playtime(); favset = _fav_set()
        for r in rec:
            r["secs"] = pt.get(r.get("system", "") + "/" + r.get("game", ""), 0)
            r["fav"] = (r.get("system", ""), r.get("game", "")) in favset
        return {"ok": True, "games": rec}
    except Exception:
        return {"ok": True, "games": []}

# ---- game art scraper: libretro-thumbnails (NO API key) for ROMs missing cover art ----
_LIBRETRO_SYS = {
    "nes": "Nintendo - Nintendo Entertainment System",
    "snes": "Nintendo - Super Nintendo Entertainment System",
    "n64": "Nintendo - Nintendo 64", "gb": "Nintendo - Game Boy",
    "gbc": "Nintendo - Game Boy Color", "gba": "Nintendo - Game Boy Advance",
    "nds": "Nintendo - Nintendo DS", "virtualboy": "Nintendo - Virtual Boy",
    "megadrive": "Sega - Mega Drive - Genesis", "genesis": "Sega - Mega Drive - Genesis",
    "mastersystem": "Sega - Master System - Mark III", "gamegear": "Sega - Game Gear",
    "segacd": "Sega - Mega-CD - Sega CD", "sega32x": "Sega - 32X", "saturn": "Sega - Saturn",
    "psx": "Sony - PlayStation", "psp": "Sony - PlayStation Portable",
    "pcengine": "NEC - PC Engine - TurboGrafx 16", "atari2600": "Atari - 2600",
    "atari7800": "Atari - 7800", "lynx": "Atari - Lynx", "c64": "Commodore - 64",
    "amiga": "Commodore - Amiga", "neogeo": "SNK - Neo Geo", "dreamcast": "Sega - Dreamcast",
    "wonderswan": "Bandai - WonderSwan", "mame": "MAME", "arcade": "MAME",
}

def _scrape_one(sysname, game):
    import urllib.parse
    # libretro thumbnails use No-Intro names (with region tags). Real ROM sets already match the name
    # as-is; for tag-less names, try common region tags. Also try a tag-stripped fallback.
    cands = [game]
    base = re.sub(r"\s*[\(\[].*?[\)\]]", "", game).strip()
    if base and base != game:
        cands.append(base)
    if "(" not in game:   # tag-less filename → try the standard No-Intro region tags
        for tag in [" (USA)", " (World)", " (Europe)", " (Japan, USA)", " (USA, Europe)", " (Japan)"]:
            cands.append(game + tag)
    for nm in cands:
        url = "https://thumbnails.libretro.com/%s/Named_Boxarts/%s.png" % (
            urllib.parse.quote(sysname), urllib.parse.quote(nm))
        try:
            with urllib.request.urlopen(url, timeout=12) as r:
                data = r.read()
            if data and len(data) > 1000:
                return data
        except Exception:
            continue
    return None

SCRAPE_STATE_F = "/userdata/gose-ui/scrape_state.json"

def _scrape_state():
    try:
        return json.load(open(SCRAPE_STATE_F))
    except Exception:
        return {}

def scrape_system(system, force=False, state=None):
    # Pull cover art from libretro-thumbnails for any game missing it. Art is written to disk
    # (/userdata/roms/<sys>/images/<game>-image.png) so it persists across reboots — once scraped,
    # it's there every load. A scrape_state manifest records ok/miss per game so the auto pass
    # (force=False) doesn't re-hit the network for known-missing titles on every boot, while still
    # picking up newly-added games. Manual scrape (S in Library) passes force=True to retry misses.
    sysname = _LIBRETRO_SYS.get(system)
    if not sysname:
        return {"ok": False, "error": "no thumbnail source for '%s'" % system}
    d = os.path.join(ROMS, system)
    imgd = os.path.join(d, "images")
    os.makedirs(imgd, exist_ok=True)
    own_state = state is None
    if state is None:
        state = _scrape_state()
    scraped, missed, skipped = 0, 0, 0
    try:
        files = os.listdir(d)
    except Exception as e:
        return {"ok": False, "error": str(e)}
    for f in files:
        if f.startswith(".") or f in _SKIPDIRS or "gamelist" in f or os.path.isdir(os.path.join(d, f)):
            continue
        if os.path.splitext(f)[1].lower() in _SKIPEXT:
            continue
        game = os.path.splitext(f)[0]
        key = system + "/" + game
        if _game_img(system, game):   # already has art on disk
            state[key] = "ok"; skipped += 1; continue
        if not force and state.get(key):   # already attempted (ok/miss) — don't re-network on boot
            skipped += 1; continue
        data = _scrape_one(sysname, game)
        if data:
            with open(os.path.join(imgd, game + "-image.png"), "wb") as out:
                out.write(data)
            scraped += 1; state[key] = "ok"
        else:
            missed += 1; state[key] = "miss"
    if own_state:
        write_json_atomic(SCRAPE_STATE_F, state)
    LOG.info("scrape %s: +%d art, %d missed, %d skipped", system, scraped, missed, skipped)
    return {"ok": True, "system": system, "scraped": scraped, "missed": missed, "had_art": skipped}

def auto_scrape_boot():
    # Background, one-shot per boot: fill in any missing cover art automatically so the Library is
    # populated without the user pressing Scrape. Cheap on reboot thanks to the scrape_state manifest.
    try:
        time.sleep(20)   # let the boot settle before touching the network
        state = _scrape_state()
        total = 0
        for sysname in sorted(os.listdir(ROMS)):
            if not os.path.isdir(os.path.join(ROMS, sysname)) or sysname not in _LIBRETRO_SYS:
                continue
            r = scrape_system(sysname, force=False, state=state)
            total += r.get("scraped", 0)
            write_json_atomic(SCRAPE_STATE_F, state)   # checkpoint after each system
            time.sleep(0.5)
        LOG.info("auto-scrape pass complete: +%d new covers", total)
    except Exception as e:
        LOG.warning("auto-scrape failed: %s", e)

def storage_info():
    # WizTree-style: disk totals + size of each top-level /userdata folder
    import subprocess
    out = {"ok": True, "items": []}
    try:
        st = os.statvfs("/userdata")
        total = st.f_blocks * st.f_frsize; free = st.f_bavail * st.f_frsize
        out.update(total=total, free=free, used=total - free)
        r = subprocess.run(["du", "-sb", "--", *[
            os.path.join("/userdata", d) for d in sorted(os.listdir("/userdata"))
            if os.path.isdir(os.path.join("/userdata", d)) and not d.startswith(".")]],
            capture_output=True, text=True, timeout=40)
        for line in r.stdout.strip().splitlines():
            parts = line.split("\t", 1)
            if len(parts) == 2:
                out["items"].append({"name": os.path.basename(parts[1]), "bytes": int(parts[0])})
        out["items"].sort(key=lambda x: -x["bytes"])
    except Exception as e:
        out = {"ok": False, "error": str(e)}
    return out

_TEXT_EXT = {".txt", ".md", ".cfg", ".conf", ".ini", ".log", ".json", ".xml", ".sh", ".py",
             ".js", ".css", ".html", ".yml", ".yaml", ".csv", ".gamelist"}
_IMG_EXT = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".svg"}
_AUD_EXT = {".wav", ".mp3", ".ogg", ".flac", ".m4a", ".aac", ".opus"}
_VID_EXT2 = {".mp4", ".mov", ".mkv", ".webm", ".avi", ".m4v", ".ts"}
_RUN_EXT = {".appimage", ".exe", ".msi", ".bat", ".flatpakref", ".sh", ".desktop"}

def _safe(p):
    # confine the Files app to FS_ROOT; reject traversal outside it
    rp = os.path.realpath(p or FS_ROOT)
    return rp if (rp == FS_ROOT or rp.startswith(FS_ROOT + "/")) else None

# ---- OS-protection: never let the Files app / terminal destroy boot-critical paths ----
# A safety net so the OS can't be broken by deleting system files (NOT a hardened sandbox —
# that's the landrun/bubblewrap work in the security wave; this stops the careless/accidental case).
PROTECTED_PREFIXES = ("/usr", "/bin", "/sbin", "/lib", "/lib64", "/boot", "/etc", "/sys",
                      "/proc", "/dev", "/run", "/var",
                      "/userdata/gose-ui", "/userdata/system/gose")   # the GOSE shell + agent/tokens
PROTECTED_EXACT = {"/", "/userdata", "/userdata/system", "/userdata/system/batocera.conf"}

def _is_protected(path):
    try:
        p = os.path.realpath(path)
    except Exception:
        return True   # unresolvable → refuse (fail safe)
    if p in PROTECTED_EXACT:
        return True
    return any(p == pre or p.startswith(pre + "/") for pre in PROTECTED_PREFIXES)

_DESTRUCTIVE = re.compile(r'\b(rm|rmdir|unlink|shred|srm|mv|dd|mkfs\w*|wipefs|truncate|fdisk|sgdisk|parted)\b')
_CATASTROPHIC = re.compile(r'rm\s+-[a-z]*\s*(/|/\*)(\s|$)|:\(\)\s*\{\s*:\|:\s*&\s*\}|mkfs|dd\s+of=/dev|>\s*/dev/sd')

def _cmd_is_dangerous(cmd, cwd="/"):
    """Heuristic: block obviously-destructive shell commands aimed at boot-critical paths."""
    c = cmd or ""
    if _CATASTROPHIC.search(c):
        return "that command could brick the OS"
    if _DESTRUCTIVE.search(c):
        for tok in re.findall(r'(/[^\s\'";|&)]*)', c):     # absolute protected targets
            if _is_protected(tok):
                return "it touches a protected system path (%s)" % tok
        if _is_protected(cwd):                              # relative destructive op in a protected dir
            return "it's a destructive command inside a protected directory (%s)" % cwd
    return None

def _kind(name, isdir):
    if isdir:
        return "dir"
    ext = os.path.splitext(name)[1].lower()
    if ext in _RUN_EXT:
        return "run"
    if ext in _IMG_EXT:
        return "image"
    if ext in _AUD_EXT:
        return "audio"
    if ext in _VID_EXT2:
        return "video"
    if ext in _TEXT_EXT:
        return "text"
    return "file"

def fs_list(path):
    d = _safe(path)
    if not d or not os.path.isdir(d):
        return {"ok": False, "error": "not a folder"}
    dirs, files = [], []
    try:
        for name in os.listdir(d):
            full = os.path.join(d, name)
            isdir = os.path.isdir(full)
            (dirs if isdir else files).append(name)
    except Exception as e:
        return {"ok": False, "error": str(e)}
    # Folder sizes are NOT computed here — du over a tree (saves/ alone is GBs of
    # Steam data) took seconds and stalled the whole listing. Files get their size
    # from a cheap stat; folders report bytes=None and the UI fills them in lazily
    # via /fs/sizes after the list is already on screen.
    entries = []
    for name in sorted(dirs, key=str.lower):
        entries.append({"name": name, "isdir": True, "kind": "dir", "bytes": None})
    for name in sorted(files, key=str.lower):
        full = os.path.join(d, name)
        try:
            b = os.path.getsize(full)
        except Exception:
            b = 0
        entries.append({"name": name, "isdir": False, "kind": _kind(name, False), "bytes": b})
    return {"ok": True, "path": d, "parent": (None if d == FS_ROOT else os.path.dirname(d)),
            "entries": entries}

def fs_sizes(path):
    # lazy folder sizes — called after the listing renders so it never blocks the UI
    d = _safe(path)
    if not d or not os.path.isdir(d):
        return {"ok": False, "error": "not a folder"}
    try:
        dirs = [x for x in os.listdir(d) if os.path.isdir(os.path.join(d, x))]
    except Exception as e:
        return {"ok": False, "error": str(e)}
    sizes = {}
    if dirs:
        try:
            r = subprocess.run(["du", "-sb", "--"] + [os.path.join(d, x) for x in dirs],
                               capture_output=True, text=True, timeout=60)
            for line in r.stdout.strip().splitlines():
                p = line.split("\t", 1)
                if len(p) == 2:
                    sizes[os.path.basename(p[1])] = int(p[0])
        except Exception:
            pass
    return {"ok": True, "path": d, "sizes": sizes}

USER_HOME = "/userdata/home"   # Windows-style user profile root
_QUICK = ["Desktop", "Documents", "Downloads", "Pictures", "Music", "Videos"]

def ensure_user_dirs():
    # create the friendly user folders if missing (idempotent) so "This PC" always
    # has Desktop/Documents/Downloads/Pictures/Music/Videos like a fresh Windows
    try:
        for d in _QUICK:
            os.makedirs(os.path.join(USER_HOME, d), exist_ok=True)
    except Exception:
        pass

def fs_places():
    # the "This PC" landing: Quick-access user folders + the drive (which holds all
    # the system files in one place) + a Games shortcut
    ensure_user_dirs()
    drive = {"name": "GOSE (C:)", "path": FS_ROOT}
    try:
        st = os.statvfs("/userdata")
        total = st.f_blocks * st.f_frsize; free = st.f_bavail * st.f_frsize
        drive.update(total=total, free=free, used=total - free)
    except Exception:
        pass
    return {"ok": True,
            "quick": [{"name": n, "path": os.path.join(USER_HOME, n)} for n in _QUICK],
            "games": {"name": "Games", "path": ROMS},
            "drive": drive}

def fs_read(path):
    f = _safe(path)
    if not f or not os.path.isfile(f):
        return {"ok": False, "error": "not a file"}
    try:
        with open(f, "rb") as fh:
            data = fh.read(262144)  # cap 256KB
        return {"ok": True, "path": f, "truncated": os.path.getsize(f) > 262144,
                "text": data.decode("utf-8", "replace")}
    except Exception as e:
        return {"ok": False, "error": str(e)}

def fs_op(payload):
    op = payload.get("op"); src = _safe(payload.get("path"))
    if not src:
        return {"ok": False, "error": "bad path"}
    if op in ("delete", "move", "write") and _is_protected(src):
        return {"ok": False, "error": "protected: that's a system file GOSE needs — can't delete or change it"}
    try:
        if op == "delete":
            if os.path.isdir(src):
                shutil.rmtree(src)
            else:
                os.remove(src)
        elif op in ("copy", "move"):
            dst = _safe(payload.get("dest"))
            if not dst:
                return {"ok": False, "error": "bad dest"}
            target = os.path.join(dst, os.path.basename(src)) if os.path.isdir(dst) else dst
            if op == "copy":
                (shutil.copytree if os.path.isdir(src) else shutil.copy2)(src, target)
            else:
                shutil.move(src, target)
        elif op == "mkdir":
            os.makedirs(os.path.join(src, payload.get("name", "New folder")), exist_ok=True)
        elif op == "write":
            with open(src, "w", encoding="utf-8") as fh:
                fh.write(payload.get("text", ""))
        else:
            return {"ok": False, "error": "unknown op"}
        return {"ok": True}
    except Exception as e:
        return {"ok": False, "error": str(e)}

_PROC_LAST = {"total": None, "procs": {}}

def procs_info():
    # Task-Manager data straight from /proc (portable to any Linux incl. the Odin 2).
    # CPU% is computed statefully across polls using jiffies — no blocking sleep.
    try:
        pg = os.sysconf("SC_PAGE_SIZE"); ncpu = os.sysconf("SC_NPROCESSORS_ONLN")
        with open("/proc/stat") as f:
            total = sum(int(x) for x in f.readline().split()[1:])
        mem = {}
        for line in open("/proc/meminfo"):
            k, v = line.split(":", 1)
            if k in ("MemTotal", "MemFree", "MemAvailable", "Buffers", "Cached", "SwapTotal", "SwapFree"):
                mem[k] = int(v.split()[0]) * 1024
        memtotal = mem.get("MemTotal", 1)
        cur = {}
        for pid in os.listdir("/proc"):
            if not pid.isdigit():
                continue
            try:
                c = open("/proc/%s/stat" % pid).read()
                after = c[c.rindex(")") + 2:].split()
                ut = int(after[11]) + int(after[12])    # utime + stime (fields 14,15)
                state = after[0]
                comm = open("/proc/%s/comm" % pid).read().strip()
                try:
                    rss = int(open("/proc/%s/statm" % pid).read().split()[1]) * pg
                except Exception:
                    rss = 0
                cur[pid] = (ut, comm, rss, state)
            except Exception:
                pass
        dt = (total - _PROC_LAST["total"]) if _PROC_LAST["total"] else 0
        rows = []
        for pid, (ut, comm, rss, state) in cur.items():
            ut0 = _PROC_LAST["procs"].get(pid, ut)
            cpu = (ut - ut0) / dt * ncpu * 100 if dt > 0 else 0.0
            rows.append({"pid": int(pid), "name": comm, "cpu": round(max(0, cpu), 1),
                         "rss": rss, "mem_pct": round(rss / memtotal * 100, 1) if memtotal else 0,
                         "state": state})
        _PROC_LAST["total"] = total
        _PROC_LAST["procs"] = {p: v[0] for p, v in cur.items()}
        rows.sort(key=lambda r: (-r["cpu"], -r["rss"]))
        return {"ok": True, "procs": rows[:80], "count": len(rows), "ncpu": ncpu,
                "mem": {k: mem.get(k) for k in mem}}
    except Exception as e:
        return {"ok": False, "error": str(e)}

def proc_kill(pid, sig):
    try:
        os.kill(int(pid), int(sig))
        return {"ok": True}
    except Exception as e:
        return {"ok": False, "error": str(e)}

_VID_EXT = {".mp4", ".mkv", ".mov", ".avi", ".webm", ".ts", ".m4v"}

def splice_videos():
    vids = []
    for root, dirs, files in os.walk(FS_ROOT):
        if "/gose-ui" in root:
            continue
        for f in files:
            if os.path.splitext(f)[1].lower() in _VID_EXT:
                p = os.path.join(root, f)
                try:
                    vids.append({"path": p, "name": f, "bytes": os.path.getsize(p)})
                except Exception:
                    pass
        if len(vids) >= 200:
            break
    return {"ok": True, "videos": vids}

def splice_probe(path):
    f = _safe(path)
    if not f or not os.path.isfile(f):
        return {"ok": False, "error": "not a file"}
    try:
        r = subprocess.run(["ffprobe", "-v", "quiet", "-print_format", "json",
                            "-show_format", "-show_streams", f], capture_output=True, text=True, timeout=30)
        info = json.loads(r.stdout or "{}")
        dur = float(info.get("format", {}).get("duration", 0) or 0)
        v = next((s for s in info.get("streams", []) if s.get("codec_type") == "video"), {})
        return {"ok": True, "duration": dur, "width": v.get("width"), "height": v.get("height"),
                "vcodec": v.get("codec_name"), "size": int(info.get("format", {}).get("size", 0) or 0)}
    except Exception as e:
        return {"ok": False, "error": str(e)}

def splice_cut(payload):
    src = _safe(payload.get("path"))
    if not src or not os.path.isfile(src):
        return {"ok": False, "error": "bad input"}
    try:
        start = max(0.0, float(payload.get("start", 0)))
        end = float(payload.get("end", 0))
        dur = end - start
        if dur <= 0:
            return {"ok": False, "error": "end must be after start"}
        base, ext = os.path.splitext(src)
        out = "%s_clip%s" % (base, ext)
        n = 1
        while os.path.exists(out):
            out = "%s_clip%d%s" % (base, n, ext); n += 1
        # lossless: stream-copy, -ss before -i = fast keyframe seek (snaps to nearest keyframe)
        cmd = ["ffmpeg", "-y", "-ss", "%.3f" % start, "-i", src, "-t", "%.3f" % dur,
               "-c", "copy", "-avoid_negative_ts", "make_zero", out]
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
        if r.returncode != 0 or not os.path.exists(out):
            return {"ok": False, "error": (r.stderr or "ffmpeg failed")[-300:]}
        return {"ok": True, "out": out, "name": os.path.basename(out), "bytes": os.path.getsize(out)}
    except Exception as e:
        return {"ok": False, "error": str(e)}

_APPS = {
    "steam": ["flatpak", "run", "com.valvesoftware.Steam"],
}

_STORE = [
    {"id": "com.google.Chrome", "name": "Google Chrome", "desc": "Google's web browser — download & run anything", "cat": "Internet", "icon": "globe"},
    {"id": "org.chromium.Chromium", "name": "Chromium", "desc": "Open-source Chrome", "cat": "Internet", "icon": "globe"},
    {"id": "org.mozilla.firefox", "name": "Firefox", "desc": "Web browser", "cat": "Internet", "icon": "globe"},
    {"id": "com.usebottles.bottles", "name": "Bottles", "desc": "Run Windows .exe apps & installers (Wine)", "cat": "Windows", "icon": "wrench"},
    {"id": "com.heroicgameslauncher.hgl", "name": "Heroic", "desc": "Epic & GOG games launcher", "cat": "Games", "icon": "gamepad-2"},
    {"id": "org.videolan.VLC", "name": "VLC", "desc": "Plays any media file", "cat": "Media", "icon": "play"},
    {"id": "com.spotify.Client", "name": "Spotify", "desc": "Music streaming", "cat": "Media", "icon": "volume-2"},
    {"id": "com.discordapp.Discord", "name": "Discord", "desc": "Voice & text chat", "cat": "Social", "icon": "users"},
    {"id": "net.lutris.Lutris", "name": "Lutris", "desc": "Game launcher & manager", "cat": "Games", "icon": "gamepad-2"},
    {"id": "com.valvesoftware.Steam", "name": "Steam", "desc": "Valve's game store", "cat": "Games", "icon": "gamepad-2"},
    {"id": "org.gimp.GIMP", "name": "GIMP", "desc": "Image editor", "cat": "Creative", "icon": "palette"},
    {"id": "org.kde.kdenlive", "name": "Kdenlive", "desc": "Video editor", "cat": "Creative", "icon": "scissors"},
    {"id": "com.obsproject.Studio", "name": "OBS Studio", "desc": "Record & stream", "cat": "Creative", "icon": "monitor"},
    {"id": "org.blender.Blender", "name": "Blender", "desc": "3D creation suite", "cat": "Creative", "icon": "sparkles"},
]

_FLATPAK_APP = "/userdata/saves/flatpak/binaries/app"
_ICON_CACHE = {}

def app_icon_path(appid):
    import glob
    if appid in _ICON_CACHE:
        return _ICON_CACHE[appid]
    base = os.path.join(_FLATPAK_APP, appid)
    path = None
    if os.path.isdir(base):
        cands = glob.glob(base + "/**/icons/**/" + appid + ".png", recursive=True) or \
                glob.glob(base + "/**/" + appid + ".png", recursive=True) or \
                glob.glob(base + "/**/" + appid + ".svg", recursive=True)
        if cands:
            path = next((c for c in cands if "128" in c), None) or \
                   next((c for c in cands if "256" in c), None) or cands[0]
    _ICON_CACHE[appid] = path
    return path

def installed_apps():
    # installed flatpak apps → launchable tiles in the Apps launcher
    try:
        r = subprocess.run(["flatpak", "list", "--app", "--columns=application,name"],
                           capture_output=True, text=True, timeout=20)
        apps = []
        for line in r.stdout.splitlines():
            parts = line.split("\t")
            if len(parts) >= 2 and parts[0].strip():
                apps.append({"id": parts[0].strip(), "name": parts[1].strip()})
        return {"ok": True, "apps": apps}
    except Exception as e:
        return {"ok": False, "error": str(e), "apps": []}

# ---- download queue: one install at a time, with retry (survives wifi blips) ----
_Q = {"pending": collections.deque(), "current": None, "done": [], "failed": []}
_QLOCK = threading.Lock()

def _name_for(appid):
    return next((a["name"] for a in _STORE if a["id"] == appid), appid)

def _queue_worker():
    while True:
        appid = None
        with _QLOCK:
            if _Q["pending"]:
                appid = _Q["pending"].popleft(); _Q["current"] = appid
        if not appid:
            time.sleep(2); continue
        ok = False
        for attempt in range(5):
            try:
                r = subprocess.run(["flatpak", "install", "-y", "--noninteractive", "flathub", appid],
                                   capture_output=True, text=True, timeout=3600)
                if r.returncode == 0:
                    ok = True; break
            except Exception:
                pass
            time.sleep(8)   # ride through transient network errors
        with _QLOCK:
            _Q["current"] = None
            (_Q["done"] if ok else _Q["failed"]).append(appid)

def queue_state():
    with _QLOCK:
        return {"ok": True,
                "current": _Q["current"], "current_name": _name_for(_Q["current"]) if _Q["current"] else None,
                "pending": [{"id": i, "name": _name_for(i)} for i in _Q["pending"]],
                "done": _Q["done"][-30:], "failed": _Q["failed"][-30:]}

def store_installed():
    try:
        r = subprocess.run(["flatpak", "list", "--app", "--columns=application"],
                           capture_output=True, text=True, timeout=20)
        return set(x.strip() for x in r.stdout.splitlines() if x.strip())
    except Exception:
        return set()

def store_catalog():
    inst = store_installed()
    with _QLOCK:
        cur = _Q["current"]; pend = set(_Q["pending"])
    cat = []
    for a in _STORE:
        d = dict(a); d["installed"] = a["id"] in inst
        d["installing"] = (a["id"] == cur)
        d["queued"] = (a["id"] in pend)
        cat.append(d)
    return {"ok": True, "apps": cat}

def store_uninstall(appid):
    if not appid:
        return {"ok": False, "error": "no id"}
    try:
        r = subprocess.run(["flatpak", "uninstall", "-y", "--noninteractive", appid],
                           capture_output=True, text=True, timeout=300)
        with _QLOCK:
            _Q["done"] = [x for x in _Q["done"] if x != appid]
        return {"ok": r.returncode == 0, "id": appid,
                "error": (r.stderr or "uninstall failed")[-200:] if r.returncode != 0 else None}
    except Exception as e:
        return {"ok": False, "error": str(e)}

def store_install(appid):
    if not any(a["id"] == appid for a in _STORE):
        return {"ok": False, "error": "unknown app"}
    with _QLOCK:
        if appid == _Q["current"] or appid in _Q["pending"]:
            return {"ok": True, "queued": appid, "note": "already in queue"}
        _Q["pending"].append(appid)
    return {"ok": True, "queued": appid}

def _spawn(argv):
    env = dict(os.environ); env.setdefault("DISPLAY", ":0")
    logf = open("/userdata/gose-ui/launch.log", "ab")
    subprocess.Popen(argv, env=env, stdout=logf, stderr=subprocess.STDOUT,
                     stdin=subprocess.DEVNULL, start_new_session=True)

# ---- per-game options: core selection + common tweaks (saved to batocera.conf per-game) ----
BCONF = "/userdata/system/batocera.conf"
_ES = "/usr/share/emulationstation/es_systems.cfg"

def _rom_file(system, game):
    d = os.path.join(ROMS, system)
    try:
        for f in os.listdir(d):
            if os.path.splitext(f)[0] == game and os.path.splitext(f)[1].lower() not in _SKIPEXT and "gamelist" not in f:
                return f
    except Exception:
        pass
    return None

def system_cores(system):
    try:
        txt = open(_ES).read()
        for block in re.findall(r"<system>.*?</system>", txt, re.S):
            nm = re.search(r"<name>([^<]+)</name>", block)
            if nm and nm.group(1).strip() == system:
                cores = [c.strip() for c in re.findall(r"<core[^>]*>([^<]+)</core>", block)]
                dft = re.findall(r'<core[^>]*default="true"[^>]*>([^<]+)</core>', block)
                return {"cores": cores, "default": (dft[0].strip() if dft else (cores[0] if cores else None))}
        return {"cores": [], "default": None}
    except Exception:
        return {"cores": [], "default": None}

def _bconf_get(key):
    try:
        for line in open(BCONF):
            s = line.strip()
            if s.startswith(key + "="):
                return s.split("=", 1)[1]
    except Exception:
        pass
    return None

def _bconf_set(key, val):
    try:
        lines = open(BCONF).read().splitlines()
    except Exception:
        lines = []
    out, done = [], False
    for l in lines:
        st = l.strip()
        if st.startswith(key + "=") or st.startswith("#" + key + "="):
            if not done:
                out.append(key + "=" + val); done = True
        else:
            out.append(l)
    if not done:
        out.append(key + "=" + val)
    tmp = BCONF + ".tmp"
    with open(tmp, "w") as f:
        f.write("\n".join(out) + "\n"); f.flush(); os.fsync(f.fileno())
    os.replace(tmp, BCONF)

def _gkey(system, game):
    rom = _rom_file(system, game)
    return '%s["%s"]' % (system, rom) if rom else system

def game_options(system, game):
    sc = system_cores(system)
    k = _gkey(system, game)
    core = _bconf_get(k + ".core") or _bconf_get(system + ".core") or sc["default"]
    return {"ok": True, "cores": sc["cores"], "default": sc["default"], "core": core,
            "ratio": _bconf_get(k + ".ratio") or "auto",
            "shaders": _bconf_get(k + ".shaders") or "none",
            "rewind": _bconf_get(k + ".rewind") or "0"}

def set_game_options(payload):
    system = payload.get("system"); game = payload.get("game")
    if not system or not game:
        return {"ok": False, "error": "system+game required"}
    k = _gkey(system, game)
    for key in ("core", "ratio", "shaders", "rewind"):
        if key in payload and payload[key] is not None:
            _bconf_set("%s.%s" % (k, key), str(payload[key]))
    LOG.info("game options set: %s", k)
    return {"ok": True, "key": k}

# Our AI virtual controllers present as Xbox 360 pads (agent input.py). EmulationStation normally
# tells Batocera's launcher which controllers exist; since the GOSE shell replaced ES, we must pass
# those per-player args ourselves or the pad is detected-but-unbound (= AI can't actually play). The
# Xbox-360 GUID matches es_input.cfg so the launcher generates real button mappings.
_XBOX_GUID = "030000005e0400008e02000010010000"   # "Microsoft Xbox 360 pad" (in es_input.cfg)

_NON_PADS = ("batocera hotkeys", "evmapy")   # uinput helpers that expose js but aren't players

def _sdl_guid(bus, vendor, product, version):
    """SDL2 joystick GUID from the kernel I: line ids (LE u16 fields, zero crc/driver),
    matching the format in es_input.cfg / gamecontrollerdb (e.g. the Xbox 360 constant)."""
    def le(v):
        return "%02x%02x" % (v & 0xFF, (v >> 8) & 0xFF)
    return le(bus) + "0000" + le(vendor) + "0000" + le(product) + "0000" + le(version) + "0000"

def _virtual_pad_args(max_players=5):
    """Build emulatorlauncher -pN controller args (the job EmulationStation used to do).
    Players = HUMAN physical pads first, then our uinput virtual pads (AI seats, identified
    by phys 'py-evdev-uinput'), so a human always lands on the lowest player slot when one
    is plugged in. NOTE: a physical pad's GUID must exist in the launcher's controller DB to
    generate binds — true for common pads (same constraint ES had); the AI pads guarantee it
    by masquerading as Xbox 360."""
    try:
        txt = open("/proc/bus/input/devices").read()
    except Exception:
        return []
    all_js, virt, phys = [], [], []
    for blk in txt.split("\n\n"):
        jss = re.findall(r"js(\d+)", blk); evs = re.findall(r"event(\d+)", blk)
        if not jss:
            continue
        all_js.append(int(jss[0]))
        if not evs:
            continue
        entry = (int(jss[0]), "/dev/input/event" + evs[0])
        name_m = re.search(r'Name="([^"]*)"', blk)
        name = name_m.group(1) if name_m else "pad"
        if "py-evdev-uinput" in blk:
            virt.append(entry + (_XBOX_GUID, "Microsoft Xbox 360 pad"))
        elif not any(s in name.lower() for s in _NON_PADS):
            ids = re.search(r"Bus=(\w+) Vendor=(\w+) Product=(\w+) Version=(\w+)", blk)
            guid = (_sdl_guid(*(int(x, 16) for x in ids.groups())) if ids else _XBOX_GUID)
            phys.append(entry + (guid, name))
    all_js = sorted(set(all_js)); virt.sort(); phys.sort()
    args = []
    for n, (js, path, guid, name) in enumerate((phys + virt)[:max_players], start=1):
        idx = all_js.index(js) if js in all_js else (n - 1)
        args += ["-p%dindex" % n, str(idx), "-p%dguid" % n, guid,
                 "-p%dname" % n, name, "-p%ddevicepath" % n, path,
                 "-p%dnbbuttons" % n, "11", "-p%dnbhats" % n, "1", "-p%dnbaxes" % n, "6"]
    return args

def launch_game(system, game):
    d = os.path.join(ROMS, system)
    if not os.path.isdir(d):
        return {"ok": False, "error": "unknown system"}
    rom = None
    for f in os.listdir(d):
        if os.path.splitext(f)[0] == game and os.path.splitext(f)[1].lower() not in _SKIPEXT and "gamelist" not in f:
            rom = os.path.join(d, f); break
    if not rom:
        return {"ok": False, "error": "rom not found for " + game}
    try:
        _spawn(["emulatorlauncher"] + _virtual_pad_args() + ["-system", system, "-rom", rom])
        record_recent(system, game)
        return {"ok": True, "rom": rom}
    except Exception as e:
        return {"ok": False, "error": str(e)}

_TERM = {"cwd": "/userdata"}

def term_exec(cmd):
    import shlex
    cmd = (cmd or "").strip()
    if not cmd:
        return {"ok": True, "out": "", "cwd": _TERM["cwd"]}
    danger = _cmd_is_dangerous(cmd, _TERM["cwd"])
    if danger:
        return {"ok": False, "out": "⛔ Blocked by OS-protection: %s.\n"
                "(GOSE guards boot-critical system files so the OS can't be broken.)" % danger,
                "cwd": _TERM["cwd"], "code": 1}
    # CMD / PowerShell-style aliases so familiar commands work (it's bash underneath)
    prelude = ('dir(){ ls -la "$@"; }; copy(){ cp -r "$@"; }; move(){ mv "$@"; }; '
               'del(){ rm "$@"; }; erase(){ rm "$@"; }; type(){ cat "$@"; }; '
               'ipconfig(){ ip a "$@"; }; ver(){ uname -a; }; ')
    script = "cd %s && { %s%s ; } ; pwd" % (shlex.quote(_TERM["cwd"]), prelude, cmd)
    try:
        r = subprocess.run(["/bin/sh", "-c", script], capture_output=True, text=True, timeout=30)
        lines = r.stdout.rstrip("\n").split("\n")
        if lines and lines[-1].startswith("/") and os.path.isdir(lines[-1]):
            _TERM["cwd"] = lines[-1]; body = "\n".join(lines[:-1])
        else:
            body = r.stdout
        return {"ok": True, "out": (body + r.stderr)[-20000:], "cwd": _TERM["cwd"], "code": r.returncode}
    except subprocess.TimeoutExpired:
        return {"ok": False, "out": "(command timed out after 30s)", "cwd": _TERM["cwd"]}
    except Exception as e:
        return {"ok": False, "out": str(e), "cwd": _TERM["cwd"]}

def run_file(f):
    # run a downloaded app/installer like double-clicking on Windows
    import shlex
    p = _safe(f) if (f or "").startswith("/userdata") else f
    if not p or not os.path.isfile(p):
        return {"ok": False, "error": "file not found"}
    ext = os.path.splitext(p)[1].lower(); q = shlex.quote(p)
    if ext == ".appimage":
        cmd = ["/bin/sh", "-c", "chmod +x %s && exec %s" % (q, q)]
    elif ext == ".flatpakref":
        cmd = ["flatpak", "install", "-y", p]
    elif ext in (".exe", ".msi", ".bat"):
        if not shutil.which("wine"):
            return {"ok": False, "error": "Install Bottles from the Store to run Windows .exe"}
        cmd = ["/bin/sh", "-c", "wine %s" % q]
    elif ext == ".sh":
        cmd = ["/bin/sh", q]
    else:
        return {"ok": False, "error": "GOSE can't run %s files yet" % (ext or "these")}
    try:
        _spawn(cmd); return {"ok": True, "ran": os.path.basename(p)}
    except Exception as e:
        return {"ok": False, "error": str(e)}

def launch_app(payload):
    # spawn a native app on the GOSE display; when it exits, the kiosk (fullscreen
    # underneath) is revealed = back to GOSE. Used by Library game tiles, Apps, etc.
    if payload.get("file"):
        return run_file(payload["file"])
    if payload.get("system") and payload.get("game"):
        return launch_game(payload["system"], payload["game"])
    app = payload.get("app"); cmd = payload.get("cmd")
    if app and app in _APPS:
        argv = _APPS[app]
    elif cmd:
        danger = _cmd_is_dangerous(cmd)
        if danger:
            return {"ok": False, "error": "blocked by OS-protection: %s" % danger}
        argv = ["/bin/sh", "-c", cmd]   # e.g. emulatorlauncher / retroarch invocations
    else:
        return {"ok": False, "error": "no app or cmd"}
    try:
        _spawn(argv)
        return {"ok": True, "launched": app or cmd}
    except Exception as e:
        return {"ok": False, "error": str(e)}

def net_info():
    # real network state via connman (Batocera's manager) — works on the Odin 2 too
    try:
        svc = subprocess.run(["connmanctl", "services"], capture_output=True, text=True, timeout=10).stdout
        conn = None; typ = None; online = False
        for line in svc.splitlines():
            if not line.strip():
                continue
            state = line[:4]; rest = line[4:].strip()
            if not rest:
                continue
            toks = rest.split(); sid = toks[-1]; name = " ".join(toks[:-1]) or sid
            if "*" in state:    # connected (favorite)
                conn = name; online = ("O" in state) or ("R" in state)
                typ = "wifi" if sid.startswith("wifi") else ("ethernet" if sid.startswith("ethernet") else "net")
                break
        tech = subprocess.run(["connmanctl", "technologies"], capture_output=True, text=True, timeout=10).stdout
        has_wifi = "wifi" in tech.lower()
        return {"ok": True, "connection": conn, "type": typ, "online": online, "has_wifi": has_wifi}
    except Exception as e:
        return {"ok": False, "error": str(e)}

def host_info():
    # real laptop battery + internet from the host bridge (QEMU gateway = host)
    try:
        with urllib.request.urlopen("http://10.0.2.2:8790/", timeout=2) as r:
            return json.loads(r.read().decode())
    except Exception:
        return {}

def health():
    # production health check: are the moving parts alive?
    return {"ok": True, "version": VERSION["version"], "uptime_s": round(time.time() - START_T),
            "agent": agent_status().get("ok", False), "host_bridge": bool(host_info())}

def host_bridge(path, body=None, timeout=16):
    # proxy to the host bridge (Wi-Fi uses the laptop's real radio via netsh on Windows)
    try:
        url = "http://10.0.2.2:8790" + path
        if body is not None:
            req = urllib.request.Request(url, data=json.dumps(body).encode(),
                                         headers={"Content-Type": "application/json"})
        else:
            req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode())
    except Exception as e:
        return {"ok": False, "error": "host bridge unreachable: " + str(e)}

def agent_status():
    try:
        s = socket.create_connection(("127.0.0.1", 8731), 4); s.settimeout(4)
        req = json.dumps({"id": 1, "op": "system.status", "args": {}, "token": TOKEN})
        s.sendall((req + "\n").encode())
        buf = b""
        while b"\n" not in buf:
            d = s.recv(65536)
            if not d:
                break
            buf += d
        s.close()
        resp = json.loads(buf.split(b"\n", 1)[0])
        if not resp.get("ok"):
            return {"ok": False, "error": resp.get("error", "agent error")}
        r = resp.get("result", {})
        mem = r.get("mem") or {}; total = mem.get("MemTotal") or 0
        avail = mem.get("MemAvailable") or 0; used = max(0, total - avail)
        cpu = r.get("cpu") or {}; la = (cpu.get("loadavg") or [0])[0]; cnt = cpu.get("count") or 1
        return {"ok": True, "cpu_pct": min(100, round(la / cnt * 100)),
                "mem_pct": round(used / total * 100) if total else 0,
                "mem_used_gb": round(used / 1048576, 1), "mem_total_gb": round(total / 1048576, 1),
                "temp_c": r.get("temp_c"), "uptime_s": round(r.get("uptime_s") or 0)}
    except Exception as e:
        return {"ok": False, "error": str(e)}

# ---- system quick-settings (Guide overlay): volume / brightness / power / performance ----
def _audio_card():
    # the default ALSA mixer isn't always attached ("Host is down"); pin to the first real card
    try:
        for line in open("/proc/asound/cards"):
            line = line.strip()
            if line and line[0].isdigit():
                return line.split()[0]
    except Exception:
        pass
    return "0"

def sys_audio(set_vol=None, set_mute=None):
    import re
    card = _audio_card()
    try:
        sc = subprocess.run(["amixer", "-c", card, "scontrols"], capture_output=True, text=True, timeout=5).stdout
        cands = [c for c in ("Master", "PCM", "Speaker", "Headphone") if "'%s'" % c in sc] or ["Master"]
        # pick the control that actually reports a volume % (Master is sometimes switch-only)
        ctrl, vol, g = cands[0], None, ""
        for c in cands:
            gg = subprocess.run(["amixer", "-c", card, "-M", "get", c], capture_output=True, text=True, timeout=5).stdout
            mm = re.search(r"\[(\d+)%\]", gg)
            if mm:
                ctrl, vol, g = c, int(mm.group(1)), gg; break
        if set_vol is not None:
            subprocess.run(["amixer", "-c", card, "-M", "set", ctrl, "%d%%" % int(set_vol)],
                           capture_output=True, text=True, timeout=5)
        if set_mute is not None:
            subprocess.run(["amixer", "-c", card, "set", ctrl, "mute" if set_mute else "unmute"],
                           capture_output=True, text=True, timeout=5)
        g = subprocess.run(["amixer", "-c", card, "-M", "get", ctrl], capture_output=True, text=True, timeout=5).stdout
        m = re.search(r"\[(\d+)%\]", g)
        return {"ok": True, "volume": int(m.group(1)) if m else None,
                "mute": "[off]" in g, "control": ctrl, "card": card}
    except Exception as e:
        return {"ok": False, "error": str(e)}

def _bl_dir():
    base = "/sys/class/backlight"
    try:
        ds = sorted(os.listdir(base))
        return os.path.join(base, ds[0]) if ds else None
    except Exception:
        return None

def sys_brightness(set_val=None):
    d = _bl_dir()
    if not d:
        return {"ok": False, "has": False, "error": "no backlight (VM/desktop)"}
    try:
        mx = int(open(os.path.join(d, "max_brightness")).read())
        if set_val is not None:
            v = max(1, min(mx, int(round(int(set_val) / 100.0 * mx))))
            open(os.path.join(d, "brightness"), "w").write(str(v))
        cur = int(open(os.path.join(d, "brightness")).read())
        return {"ok": True, "has": True, "value": round(cur / mx * 100)}
    except Exception as e:
        return {"ok": False, "has": True, "error": str(e)}

def sys_power(action):
    cmds = {"sleep": ["/bin/sh", "-c", "systemctl suspend 2>/dev/null || echo mem > /sys/power/state"],
            "restart": ["/bin/sh", "-c", "batocera-es-swissknife --reboot 2>/dev/null || reboot"],
            "shutdown": ["/bin/sh", "-c", "batocera-es-swissknife --shutdown 2>/dev/null || poweroff"]}
    if action not in cmds:
        return {"ok": False, "error": "bad action"}
    try:
        _spawn(cmds[action]); return {"ok": True, "action": action}
    except Exception as e:
        return {"ok": False, "error": str(e)}

def sys_perf(mode):
    import glob
    gov = {"battery": "powersave", "balanced": "ondemand", "performance": "performance"}.get(mode)
    if not gov:
        return {"ok": False, "error": "bad mode"}
    n = 0
    for p in glob.glob("/sys/devices/system/cpu/cpu*/cpufreq/scaling_governor"):
        try:
            open(p, "w").write(gov); n += 1
        except Exception:
            pass
    return {"ok": True, "mode": mode, "applied": n}   # applied=0 on a VM w/o cpufreq; UI keeps the pref

# ---- Bluetooth (pairing UI lives in Settings → Network, next to Wi-Fi) ----
def _bt(args, t=8):
    return subprocess.run(["bluetoothctl"] + args, capture_output=True, text=True, timeout=t).stdout

def bt_status():
    try:
        show = _bt(["show"], 6)
        if "No default controller" in show or "Controller" not in show:
            return {"ok": True, "adapter": False, "devices": []}
        powered = "Powered: yes" in show
        devs = []
        for line in _bt(["devices"], 6).splitlines():
            parts = line.split(" ", 2)
            if len(parts) >= 3 and parts[0] == "Device":
                mac, name = parts[1], parts[2]
                info = _bt(["info", mac], 6)
                devs.append({"mac": mac, "name": name,
                             "connected": "Connected: yes" in info, "paired": "Paired: yes" in info,
                             "kind": ("audio" if "Audio" in info else ("input" if "Input" in info or "HID" in info else "other"))})
        devs.sort(key=lambda d: (not d["connected"], not d["paired"], d["name"].lower()))
        return {"ok": True, "adapter": True, "powered": powered, "devices": devs}
    except Exception as e:
        return {"ok": False, "error": str(e)}

def bt_action(payload):
    act, mac = payload.get("action"), payload.get("mac")
    try:
        if act == "power":
            _bt(["power", "on" if payload.get("on", True) else "off"], 8)
        elif act == "scan":
            subprocess.run(["bluetoothctl", "--timeout", "8", "scan", "on"],
                           capture_output=True, text=True, timeout=16)
        elif act == "pair" and mac:
            _bt(["pair", mac], 25); _bt(["trust", mac], 8); _bt(["connect", mac], 20)
        elif act == "connect" and mac:
            _bt(["connect", mac], 20)
        elif act == "disconnect" and mac:
            _bt(["disconnect", mac], 15)
        elif act == "remove" and mac:
            _bt(["remove", mac], 10)
        else:
            return {"ok": False, "error": "bad action"}
        return bt_status()
    except Exception as e:
        return {"ok": False, "error": str(e)}

# ---- AI players: real presence registry (an AI joins/heartbeats; the hub reflects who's live) ----
AI_F = "/userdata/gose-ui/ai_players.json"
_AI_LOCK = threading.Lock()

def _ai_load():
    try:
        return json.load(open(AI_F))
    except Exception:
        return {}

def ai_players():
    now = time.time()
    out = []
    for name, info in _ai_load().items():
        out.append({"name": name, "online": (now - info.get("t", 0)) < 30,
                    "mode": info.get("mode", "watching"), "since": info.get("since"),
                    "tier": ai_tier(name)})
    out.sort(key=lambda p: (not p["online"], p["name"]))
    return {"ok": True, "players": out}

def ai_join(payload):
    name = payload.get("name")
    if not name:
        return {"ok": False, "error": "name required"}
    with _AI_LOCK:
        reg = _ai_load(); now = time.time()
        prev = reg.get(name, {})
        reg[name] = {"t": now, "since": prev.get("since", int(now)),
                     "mode": payload.get("mode", "watching")}
        write_json_atomic(AI_F, reg)
    LOG.info("AI join: %s (%s)", name, payload.get("mode", "watching"))
    return {"ok": True, "name": name}

def ai_leave(payload):
    name = payload.get("name")
    with _AI_LOCK:
        reg = _ai_load()
        if reg.pop(name, None) is not None:
            write_json_atomic(AI_F, reg)
    LOG.info("AI leave: %s", name)
    return {"ok": True}

# ---- AI permission grants (Zeke's UAC-style model — a human grants a tier; an AI can REQUEST
#      but never self-elevate; revoke is instant. Human-facing surface = AI Hub + widget. See docs/16.
#      This is the persisted grant store; per-tool token enforcement (Capframe/macaroons) is the next phase. ----
AI_GRANTS_F = "/userdata/gose-ui/ai_grants.json"
AI_TIERS = ["observe", "play", "admin"]   # observe = read-only (default), play = games, admin = full OS

def _ai_grants_load():
    try:
        return json.load(open(AI_GRANTS_F))
    except Exception:
        return {}

def ai_tier(name):
    """Effective tier for an AI: stored grant (honoring optional expiry) or the safe default 'observe'."""
    g = _ai_grants_load().get(name)
    if not g:
        return "observe"
    exp = g.get("expires")
    if exp and time.time() > exp:
        return "observe"          # an expired grant silently falls back to observe
    t = g.get("tier", "observe")
    return t if t in AI_TIERS else "observe"

# The agent (port 8731) reads this token->tier map and ENFORCES it. Granting here writes it,
# so a Hub grant actually gates what that AI can do — the full loop, end to end. (docs/16)
AI_TOKENS_F = os.environ.get("GOSE_AGENT_AI_TOKENS", "/userdata/system/gose/ai_tokens.json")

def _sync_ai_tokens(g):
    """Rebuild the agent's token->{name,tier[,seat]} map from the grants so grant/revoke
    enforces at once. seat pins a play-tier AI to one controller seat (agent _pin_seat)."""
    toks = {rec["token"]: ({"name": name, "tier": rec["tier"], "seat": rec["seat"]}
                           if rec.get("seat") else {"name": name, "tier": rec["tier"]})
            for name, rec in g.items() if rec.get("token") and rec.get("tier") in AI_TIERS}
    try:
        os.makedirs(os.path.dirname(AI_TOKENS_F), exist_ok=True)
        write_json_atomic(AI_TOKENS_F, toks)
    except Exception as e:
        LOG.warning("ai_tokens sync failed: %s", e)

def ai_grants():
    g = _ai_grants_load()
    return {"ok": True, "grants": {n: {"tier": ai_tier(n), "granted_at": g[n].get("granted_at"),
                                       "expires": g[n].get("expires"), "token": g[n].get("token"),
                                       "seat": g[n].get("seat")} for n in g}}

def ai_grant(payload):
    name, tier = payload.get("name"), payload.get("tier")
    if not name or tier not in AI_TIERS:
        return {"ok": False, "error": "name + valid tier required"}
    with _AI_LOCK:
        g = _ai_grants_load()
        if tier == "observe":
            g.pop(name, None)     # observe is the floor — drop the grant (== revoke)
        else:
            prev = g.get(name, {})
            days = payload.get("expires_days")    # None/0 = permanent until revoked (the default)
            seat = payload.get("seat")            # optional controller seat (1-4) — pins the AI to it
            try:
                seat = int(seat) if seat else None
                if seat is not None and not 1 <= seat <= 4:
                    seat = None
            except (TypeError, ValueError):
                seat = None
            g[name] = {"tier": tier, "granted_at": int(time.time()),
                       "expires": (int(time.time()) + int(days) * 86400) if days else None,
                       "seat": seat,
                       "token": prev.get("token") or secrets.token_hex(16)}  # stable per-AI token
        write_json_atomic(AI_GRANTS_F, g)
        _sync_ai_tokens(g)        # <-- push the token->tier map to the agent so it enforces NOW
    LOG.info("AI grant: %s -> %s (token issued + enforced)", name, tier)
    return {"ok": True, "name": name, "tier": tier, "token": g.get(name, {}).get("token"),
            "seat": g.get(name, {}).get("seat")}

def ai_revoke(payload):
    name = payload.get("name")
    with _AI_LOCK:
        g = _ai_grants_load()
        existed = g.pop(name, None) is not None
        if existed:
            write_json_atomic(AI_GRANTS_F, g)
            _sync_ai_tokens(g)    # <-- token vanishes from the agent's map → access dies immediately
    LOG.info("AI revoke: %s -> observe (token removed)", name)
    return {"ok": True, "name": name, "tier": "observe", "revoked": existed}

# ---- AI pairing requests: an unauthenticated AI may ASK for a tier; the owner approves or
#      denies it in the AI Hub. A request NEVER grants anything by itself — approval is the
#      owner calling /ai/grant. Stored separate from grants; rate-limited at the route. ----
AI_REQUESTS_F = "/userdata/gose-ui/ai_requests.json"
_AI_REQ_MAX = 8         # pending cap — a stranger can't flood the owner's screen

def _ai_requests_load():
    try:
        return json.load(open(AI_REQUESTS_F))
    except Exception:
        return {}

def ai_request(payload):
    name = (payload.get("name") or "").strip()[:32]
    tier = payload.get("tier")
    if not name or not re.match(r"^[\w][\w .\-]*$", name):
        return {"ok": False, "error": "name required (letters/digits/space/.-_, max 32)"}
    if tier not in AI_TIERS:
        return {"ok": False, "error": "tier must be one of %s" % AI_TIERS}
    with _AI_LOCK:
        reqs = _ai_requests_load()
        if name not in reqs and len(reqs) >= _AI_REQ_MAX:
            return {"ok": False, "error": "too many pending requests — ask the owner to clear some"}
        reqs[name] = {"tier": tier, "ts": int(time.time())}
        write_json_atomic(AI_REQUESTS_F, reqs)
    LOG.info("AI pairing request: %s asks for %s (pending owner approval)", name, tier)
    return {"ok": True, "name": name, "tier": tier, "pending": True}

def ai_requests():
    reqs = _ai_requests_load()
    out = [{"name": n, "tier": r.get("tier", "observe"), "ts": r.get("ts")} for n, r in reqs.items()]
    out.sort(key=lambda r: r["ts"] or 0)
    return {"ok": True, "requests": out}

def ai_request_clear(payload):
    name = (payload.get("name") or "").strip()[:32]
    with _AI_LOCK:
        reqs = _ai_requests_load()
        existed = reqs.pop(name, None) is not None
        if existed:
            write_json_atomic(AI_REQUESTS_F, reqs)
    return {"ok": True, "name": name, "cleared": existed}

# ---- AI audit: the agent appends one JSON line per guest-AI op (allowed or denied) to
#      ai_audit.jsonl; this just tails it for the Hub's Activity strip. ----
AI_AUDIT_F = "/userdata/system/gose/ai_audit.jsonl"

def ai_audit(limit=100):
    try:
        limit = max(1, min(int(limit), 500))
    except (TypeError, ValueError):
        limit = 100
    entries = []
    try:
        with open(AI_AUDIT_F, "rb") as fh:
            fh.seek(0, 2)
            fh.seek(max(0, fh.tell() - 96 * 1024))   # tail only — never load a big file whole
            lines = fh.read().decode("utf-8", "replace").splitlines()
        for ln in lines[-limit:]:
            try:
                e = json.loads(ln)
                if isinstance(e, dict):
                    entries.append(e)
            except Exception:
                pass                                  # torn first line after the seek — skip
    except OSError:
        pass                                          # no audit file yet — empty is honest
    return {"ok": True, "entries": entries[-limit:]}

# ---- Screenshot (works anywhere, incl. GL games — frame comes from the host) ----
def capture_shot(payload):
    import time as _t
    src = (payload or {}).get("source")
    os.makedirs("/userdata/home/Pictures", exist_ok=True)
    fn = "/userdata/home/Pictures/GOSE_%s.jpg" % _t.strftime("%Y%m%d_%H%M%S")
    try:
        frozen = "/userdata/gose-ui/_gbg.jpg"
        if src == "frozen" and os.path.isfile(frozen):
            shutil.copy(frozen, fn)   # the game frame the Guide already grabbed (no overlay in it)
        else:
            with urllib.request.urlopen("http://10.0.2.2:8790/screencap", timeout=12) as r:
                data = r.read()
            if not data or len(data) < 2000:
                return {"ok": False, "error": "capture failed"}
            with open(fn, "wb") as f:
                f.write(data)
        return {"ok": True, "path": fn, "name": os.path.basename(fn)}
    except Exception as e:
        return {"ok": False, "error": str(e)}

def capture_clip(seconds):
    # pull the last-N-seconds clip from the host's replay buffer → save into Videos
    import time as _t
    try:
        url = "http://10.0.2.2:8790/clip/save?seconds=%d" % int(seconds)
        with urllib.request.urlopen(url, timeout=45) as r:
            ct = r.headers.get("Content-Type", ""); data = r.read()
        if "video" not in ct or len(data) < 2000:
            return {"ok": False, "error": "Replay buffer isn't running (turn it on first)"}
        os.makedirs("/userdata/home/Videos", exist_ok=True)
        fn = "/userdata/home/Videos/GOSE_clip_%s.mp4" % _t.strftime("%Y%m%d_%H%M%S")
        with open(fn, "wb") as f:
            f.write(data)
        return {"ok": True, "path": fn, "name": os.path.basename(fn)}
    except Exception as e:
        return {"ok": False, "error": str(e)}

# ---- Guide overlay control (the over-game panel) ----
def guide_toggle():
    try:
        subprocess.run(["pkill", "-USR1", "-f", "overlay_window.py"], capture_output=True, text=True, timeout=5)
        return {"ok": True}
    except Exception as e:
        return {"ok": False, "error": str(e)}

_GAME_PATS = ["retroarch", "emulatorlauncher", "ppsspp", "pcsx", "dolphin-emu", "mupen64",
              "duckstation", "flycast", "mednafen", "melonds", "scummvm", "bwrap", "glxgears"]

def _nci(cmd, want_reply=False, timeout=1.0):
    """Send a RetroArch Network Command Interface message over UDP 55355.
    want_reply reads one datagram back (for GET_STATUS); else fire-and-forget."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.settimeout(timeout)
    try:
        s.sendto(cmd.encode(), ("127.0.0.1", 55355))
        if not want_reply:
            return None
        data, _ = s.recvfrom(8192)
        return data.decode("utf-8", "replace").strip()
    except socket.timeout:
        return None
    except Exception:
        return None
    finally:
        s.close()

def _resume_game():
    # hide the Guide → resumes the (SIGSTOP'd) game so RetroArch processes the queued NCI packet
    try: subprocess.run(["pkill", "-USR2", "-f", "overlay_window.py"], capture_output=True, timeout=4)
    except Exception: pass

def game_state(action):
    # save/load emulator state via RetroArch's NCI (UDP 55355), to the CURRENT slot.
    # Works while the game is SIGSTOP'd (Guide open): the packet queues until the game resumes.
    cmd = {"save": "SAVE_STATE", "load": "LOAD_STATE"}.get(action)
    if not cmd:
        return {"ok": False, "error": "bad action"}
    _nci(cmd); _resume_game()
    return {"ok": True, "action": action}

def game_slot(direction):
    # step the active save slot (RetroArch shows the slot # on-screen). NCI has no "set slot N",
    # so next/prev is the reliable primitive — same as RetroArch's own F6/F7.
    cmd = {"next": "STATE_SLOT_PLUS", "prev": "STATE_SLOT_MINUS"}.get(direction)
    if not cmd:
        return {"ok": False, "error": "bad direction"}
    _nci(cmd); _resume_game()
    return {"ok": True, "direction": direction}

def game_running():
    # GET_STATUS → "GET_STATUS PLAYING <system>,<game>,crc32=<hex>" (or no/empty reply when idle)
    r = _nci("GET_STATUS", want_reply=True, timeout=1.2)
    if not r or "PLAYING" not in r.upper():
        return {"ok": True, "running": False, "raw": r}
    after = r.split("PLAYING", 1)[1].strip()
    parts = [p.strip() for p in after.split(",")]
    crc = next((p.split("=", 1)[1] for p in parts if p.startswith("crc32=")), "")
    return {"ok": True, "running": True,
            "system": parts[0] if parts else "", "game": parts[1] if len(parts) > 1 else "", "crc32": crc}

def game_state_slots(system, game):
    # list savestate slots on disk for a ROM: /userdata/saves/<system>/<game>.state[N] (+ .png thumb).
    import glob, re
    if not system or not game:
        gr = game_running()
        system = system or gr.get("system", "")
        game = game or gr.get("game", "")
    out = []
    d = os.path.join("/userdata/saves", system or "")
    if game and os.path.isdir(d):
        for f in glob.glob(glob.escape(os.path.join(d, game)) + ".state*"):
            if f.endswith(".png"):
                continue
            m = re.search(r"\.state(\d*)$", f)
            if not m:
                continue
            png = f + ".png"
            out.append({"slot": int(m.group(1)) if m.group(1) else 0,
                        "mtime": int(os.path.getmtime(f)), "size": os.path.getsize(f),
                        "thumb_path": png if os.path.isfile(png) else None})
        out.sort(key=lambda x: x["slot"])
    return {"ok": True, "system": system, "game": game, "slots": out}

# ---- Screenshots / recording / gallery (player-facing capture via RetroArch NCI) ----
SHOTS_DIR = "/userdata/screenshots"
GALLERY_DIRS = (SHOTS_DIR, "/userdata/home/Pictures", "/userdata/home/Videos")
_IMG_EXT = (".png", ".jpg", ".jpeg", ".webp", ".bmp")
_VID_EXT = (".mp4", ".mkv", ".webm", ".avi", ".mov")
_recording = {"on": False}   # RetroArch RECORDING_TOGGLE has no status query → track the toggle

def _gallery_url(path):
    from urllib.parse import quote
    return "/fs/file?path=" + quote(path)

def game_screenshot():
    # RetroArch NCI SCREENSHOT → writes a PNG into screenshot_directory (/userdata/screenshots).
    # Snapshot the dir before/after so we return the EXACT new file (NCI is fire-and-forget).
    os.makedirs(SHOTS_DIR, exist_ok=True)
    try: before = set(os.listdir(SHOTS_DIR))
    except Exception: before = set()
    _nci("SCREENSHOT"); _resume_game()
    newf = None
    for _ in range(25):                      # poll up to ~2.5s for the PNG to land on disk
        time.sleep(0.1)
        try: now = set(os.listdir(SHOTS_DIR))
        except Exception: now = before
        added = [f for f in (now - before) if os.path.splitext(f)[1].lower() in _IMG_EXT]
        if added:
            newf = max(added, key=lambda f: os.path.getmtime(os.path.join(SHOTS_DIR, f)))
            break
    if not newf:
        return {"ok": False, "error": "no screenshot produced (is a game running?)"}
    p = os.path.join(SHOTS_DIR, newf)
    return {"ok": True, "name": newf, "path": p, "url": _gallery_url(p)}

def game_record_toggle():
    # RetroArch NCI RECORDING_TOGGLE → start/stop an .mkv in recording_output_directory.
    gr = game_running()
    if not gr.get("running") and not _recording["on"]:
        return {"ok": False, "error": "no game running"}
    _nci("RECORDING_TOGGLE"); _resume_game()
    _recording["on"] = not _recording["on"]
    return {"ok": True, "recording": _recording["on"]}

def game_gallery():
    # list captured screenshots + clips across the capture dirs (path-confined), newest first
    items = []
    for d in GALLERY_DIRS:
        try: names = os.listdir(d)
        except Exception: continue
        for f in names:
            ext = os.path.splitext(f)[1].lower()
            kind = "image" if ext in _IMG_EXT else ("video" if ext in _VID_EXT else None)
            if not kind:
                continue
            p = os.path.join(d, f)
            if not _safe(p) or not os.path.isfile(p):
                continue
            try: stt = os.stat(p)
            except Exception: continue
            items.append({"name": f, "path": p, "kind": kind, "size": stt.st_size,
                          "mtime": int(stt.st_mtime), "url": _gallery_url(p)})
    items.sort(key=lambda x: x["mtime"], reverse=True)
    return {"ok": True, "items": items, "recording": _recording["on"]}

# ---- Favorites (player-pinned games) ----
FAVORITES_F = "/userdata/gose-ui/favorites.json"

def _fav_load():
    try:
        v = json.load(open(FAVORITES_F))
        return v if isinstance(v, list) else []
    except Exception:
        return []

def _fav_set():
    return {(r.get("system", ""), r.get("game", "")) for r in _fav_load()}

def game_favorite(payload):
    system = (payload or {}).get("system", ""); game = (payload or {}).get("game", "")
    if not system or not game:
        return {"ok": False, "error": "system+game required"}
    on = (payload or {}).get("on", True)
    favs = [r for r in _fav_load() if not (r.get("system") == system and r.get("game") == game)]
    if on:
        favs.insert(0, {"system": system, "game": game, "t": int(time.time())})
    write_json_atomic(FAVORITES_F, favs)
    return {"ok": True, "favorite": bool(on), "count": len(favs)}

def favorites_json():
    out = []
    for r in _fav_load():
        s, g = r.get("system", ""), r.get("game", "")
        out.append({"system": s, "game": g, "name": g, "sysname": _SYS.get(s, s),
                    "img": _game_img(s, g), "fav": True})
    return {"ok": True, "games": out}

# ---- stranger's-hands resilience: boot-success counter + backup / restore / factory reset (gap J1/J2) ----
# Boot counter: the watchdog INCREMENTS .boot_attempts every time it (re)starts the UI server; this
# server CLEARS it the moment it serves the home page (proof the UI booted far enough to render).
# A crash-loop that never reaches home lets the count climb -> watchdog trips safe mode at the threshold.
BOOT_ATTEMPTS_F = ROOT + "/.boot_attempts"
BACKUP_DIR = "/userdata/backups"
# What a backup captures (relative to /userdata): the whole GOSE UI/state dir minus caches/logs,
# plus the AI account tokens + audit. NEVER roms, NEVER saves, NEVER the OS.
_BACKUP_INCLUDE = ["gose-ui", "system/gose/ai_tokens.json", "system/gose/ai_audit.jsonl"]
_BACKUP_EXCLUDE = ["gose-ui/*.log", "gose-ui/*.log.*", "gose-ui/__pycache__",
                   "gose-ui/*.tmp", "gose-ui/.boot_attempts", "gose-ui/.safe_mode",
                   "gose-ui/_stream_test.bin", "gose-ui/_render_common.pyc"]
# Factory reset wipes these GOSE state files back to defaults (grants handled separately via the
# agent-sync path). ROMs (/userdata/roms) and saves (/userdata/saves) are deliberately untouched.
_RESET_DEFAULTS = [
    (ROOT + "/favorites.json", []),
    (ROOT + "/recent.json", []),
    (ROOT + "/playtime.json", {}),
    (ROOT + "/ai_requests.json", {}),
]

def clear_boot_attempts():
    try:
        write_json_atomic(BOOT_ATTEMPTS_F, 0)
        return True
    except Exception:
        return False

def gose_backup(reason="manual"):
    """Atomic tar.gz of GOSE UI/state under /userdata/backups. Excludes logs/caches; never roms/saves."""
    try:
        os.makedirs(BACKUP_DIR, exist_ok=True)
        members = [m for m in _BACKUP_INCLUDE if os.path.exists("/userdata/" + m)]
        if not members:
            return {"ok": False, "error": "nothing to back up"}
        name = "gose-" + time.strftime("%Y%m%d-%H%M%S") + ".tar.gz"
        final = os.path.join(BACKUP_DIR, name)
        tmp = os.path.join(BACKUP_DIR, ".tmp-" + name)
        cmd = ["tar", "-czf", tmp, "-C", "/userdata"]
        for ex in _BACKUP_EXCLUDE:
            cmd.append("--exclude=" + ex)
        cmd += members
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
        # GNU tar returns 1 for benign "file changed as we read it" warnings; treat tmp existence as truth
        if not os.path.exists(tmp) or r.returncode > 1:
            try: os.remove(tmp)
            except Exception: pass
            return {"ok": False, "error": "tar failed: " + (r.stderr or "")[:200]}
        os.replace(tmp, final)
        size = os.path.getsize(final)
        LOG.info("BACKUP %s (%d bytes, reason=%s)", name, size, reason)
        return {"ok": True, "file": name, "path": final, "size": size, "reason": reason}
    except Exception as e:
        LOG.error("backup failed: %s", e)
        return {"ok": False, "error": str(e)}

def gose_backups():
    out = []
    try:
        for f in sorted(os.listdir(BACKUP_DIR), reverse=True):
            if not f.endswith(".tar.gz") or f.startswith(".tmp-"):
                continue
            p = os.path.join(BACKUP_DIR, f)
            try:
                st = os.stat(p)
                out.append({"file": f, "path": p, "size": st.st_size, "mtime": int(st.st_mtime)})
            except Exception:
                pass
    except FileNotFoundError:
        pass
    return {"ok": True, "backups": out, "dir": BACKUP_DIR}

def gose_restore(payload):
    """Restore GOSE state from a backup in /userdata/backups. Path-confined + member-validated."""
    f = (payload or {}).get("file") or ""
    base = os.path.basename(f)
    if not base or base != f or not base.endswith(".tar.gz"):
        return {"ok": False, "error": "invalid backup file"}
    path = os.path.join(BACKUP_DIR, base)
    if os.path.realpath(os.path.dirname(path)) != os.path.realpath(BACKUP_DIR) or not os.path.isfile(path):
        return {"ok": False, "error": "backup not found"}
    try:
        lst = subprocess.run(["tar", "-tzf", path], capture_output=True, text=True, timeout=60)
        if lst.returncode != 0:
            return {"ok": False, "error": "cannot read archive"}
        members = [m for m in lst.stdout.splitlines() if m.strip()]
        for m in members:
            mm = m.lstrip("./")
            if ".." in mm.split("/") or mm.startswith("/"):
                return {"ok": False, "error": "unsafe path in archive: " + m}
            if not (mm == "gose-ui" or mm.startswith("gose-ui/") or
                    mm in ("system/gose/ai_tokens.json", "system/gose/ai_audit.jsonl",
                           "system/gose", "system/gose/")):
                return {"ok": False, "error": "archive escapes GOSE state: " + m}
        ex = subprocess.run(["tar", "-xzf", path, "-C", "/userdata"],
                            capture_output=True, text=True, timeout=180)
        if ex.returncode > 1:
            return {"ok": False, "error": "extract failed: " + (ex.stderr or "")[:200]}
        LOG.info("RESTORE from %s (%d members)", base, len(members))
        return {"ok": True, "file": base, "members": len(members),
                "note": "restart the UI server to load any restored code"}
    except Exception as e:
        return {"ok": False, "error": str(e)}

def gose_factory_reset(payload):
    """Wipe GOSE config/state to defaults (accounts/grants/favorites/recent/playtime). Makes a
    safety backup first. PRESERVES roms + saves + OS. Requires an explicit confirm token."""
    confirm = (payload or {}).get("confirm")
    if confirm not in (True, "RESET", "reset", "true"):
        return {"ok": False, "error": "factory reset requires confirm token (confirm: 'RESET')"}
    safety = gose_backup(reason="pre-factory-reset")
    reset = []
    for path, default in _RESET_DEFAULTS:
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            write_json_atomic(path, default)
            reset.append(os.path.basename(path))
        except Exception as e:
            LOG.warning("reset %s failed: %s", path, e)
    try:
        with _AI_LOCK:
            write_json_atomic(AI_GRANTS_F, {})
            _sync_ai_tokens({})        # empties /userdata/system/gose/ai_tokens.json + tells the agent
        reset += ["ai_grants.json", "ai_tokens.json"]
    except Exception as e:
        LOG.warning("reset grants failed: %s", e)
    LOG.info("FACTORY RESET reset=%s safety_backup=%s", reset, safety.get("file"))
    return {"ok": True, "reset": reset, "safety_backup": safety.get("file"),
            "safety_ok": safety.get("ok", False),
            "note": "roms + saves preserved; theme/prefs are browser-local (localStorage) and unaffected"}

# ---- FPS overlay toggle (RetroArch on-screen FPS counter) ----
# IMPORTANT: batocera's configgen REGENERATES retroarchcustom.cfg from source on every launch,
# so editing fps_show there is clobbered. The authoritative source configgen reads is the
# EmulationStation setting <bool name="DrawFramerate"> in es_settings.cfg (Emulator.py reads it
# at launch and writes fps_show into the per-launch retroarchcustom.cfg). We toggle THAT, plus
# mirror fps_show into the live custom cfg so a /game/fps GET reflects state between launches.
ES_SETTINGS = "/userdata/system/configs/emulationstation/es_settings.cfg"
RA_CFG = "/userdata/system/configs/retroarch/retroarchcustom.cfg"

def _fps_state():
    try:
        m = re.search(r'<bool\s+name="DrawFramerate"\s+value="(\w+)"', open(ES_SETTINGS).read())
    except Exception:
        m = None
    return bool(m and m.group(1).lower() == "true")

def fps_get():
    return {"ok": True, "on": _fps_state()}

def fps_set(on):
    on = bool(on)
    val = "true" if on else "false"
    # 1) the authoritative source: es_settings.cfg DrawFramerate (configgen reads this at launch)
    try:
        txt = open(ES_SETTINGS).read()
    except Exception as e:
        return {"ok": False, "error": str(e)}
    node = '<bool name="DrawFramerate" value="%s" />' % val
    if re.search(r'<bool\s+name="DrawFramerate"', txt):
        txt = re.sub(r'<bool\s+name="DrawFramerate"\s+value="\w+"\s*/>', node, txt, count=1)
    elif "</config>" in txt:
        txt = txt.replace("</config>", "\t" + node + "\n</config>", 1)
    else:
        txt = txt.rstrip("\n") + "\n" + node + "\n"
    try:
        tmp = ES_SETTINGS + ".tmp"
        with open(tmp, "w") as f:
            f.write(txt); f.flush(); os.fsync(f.fileno())
        os.replace(tmp, ES_SETTINGS)
    except Exception as e:
        return {"ok": False, "error": str(e)}
    # 2) best-effort mirror into the live custom cfg (regenerated next launch anyway)
    try:
        c = open(RA_CFG).read()
        cval = '"true"' if on else '"false"'
        if re.search(r'^\s*fps_show\s*=', c, re.M):
            c = re.sub(r'^(\s*fps_show\s*=).*$', lambda m: m.group(1) + " " + cval, c, count=1, flags=re.M)
            with open(RA_CFG, "w") as f:
                f.write(c)
    except Exception:
        pass
    return {"ok": True, "on": on, "note": "applies on next game launch"}

def game_exit():
    # exit the running game/app back to the GOSE desktop: kill known launchers, hide the
    # overlay, and raise the kiosk to the front
    killed = []
    for pat in _GAME_PATS:
        try:
            if subprocess.run(["pkill", "-f", pat], capture_output=True, text=True, timeout=5).returncode == 0:
                killed.append(pat)
        except Exception:
            pass
    try: subprocess.run(["pkill", "-USR2", "-f", "overlay_window.py"], capture_output=True, text=True, timeout=5)
    except Exception: pass
    try:
        subprocess.run(["/bin/sh", "-c",
                        "DISPLAY=:0 xdotool search --name '^GOSE$' | tail -1 | xargs -r xdotool windowactivate"],
                       capture_output=True, text=True, timeout=8)
    except Exception: pass
    return {"ok": True, "killed": killed}

# ===== Emulator Store: license-aware libretro-core catalog + install/swap (docs/19, docs/20) =====
# The license audit (docs/19) is canonical: 11 cores are non-commercial (EXCLUDE from a paid
# build), 3 need review (EXCLUDE from the paid base until resolved), the rest are commercial-OK.
# The store lets a user (re)install any excluded core for PERSONAL use, or any other libretro
# core from libretro's own buildbot. Installs are confined to the libretro dir.
LIBRETRO_DIR = "/usr/lib/libretro"
BUILDBOT_BASE = "https://buildbot.libretro.com/nightly/linux/x86_64/latest/"

# Verbatim per-core license strings from docs/19 §2 (read off the device's .info files).
_CORE_LICENSE = {
    "81": "GPLv3", "a5200": "GPLv2", "arduous": "GPLv3", "atari800": "GPLv2",
    "beetle-saturn": "GPLv2", "bennugd": "GPLv3", "bk": "BSD", "blastem": "GPLv3",
    "bluemsx": "GPLv2", "boom3": "GPLv2", "boom3_xp": "GPLv2", "bsnes_hd": "GPLv3",
    "bsnes": "GPLv3", "cap32": "GPLv2", "desmume": "GPLv2", "dice": "GPLv3",
    "dolphin": "GPLv2+", "dosbox_pure": "GPLv2", "easyrpg": "GPLv3", "emuscv": "GPLv3",
    "fake08": "MIT", "fbneo": "Non-commercial", "fceumm": "GPLv2", "flycast": "GPLv2",
    "fmsx": "Non-commercial", "freechaf": "GPLv3", "freeintv": "GPLv3", "fuse": "GPLv3",
    "gambatte": "GPLv2", "gearcoleco": "GPLv3", "gearsystem": "GPLv3",
    "genesisplusgx-expanded": "Non-commercial", "genesisplusgx-wide": "Non-commercial",
    "genesisplusgx": "Non-commercial", "gw": "zlib", "handy": "Zlib", "hatari": "GPLv2",
    "hatarib": "(unverified — empty license field)", "holani": "GPLv3", "kronos": "GPLv2",
    "lowresnx": "zlib", "lutro": "MIT", "mame078plus": "MAME Noncommercial", "mame": "GPLv2+",
    "mednafen_lynx": "Zlib|GPLv2", "mednafen_ngp": "GPLv2", "mednafen_psx": "GPLv2",
    "mednafen_supergrafx": "GPLv2", "mednafen_wswan": "GPLv2", "melonds": "GPLv3",
    "melondsds": "GPLv3+", "mesen-s": "GPLv3", "mesen": "GPLv3", "mgba": "MPLv2.0",
    "minivmac": "GPLv2", "mrboom": "MIT", "mupen64plus-next": "GPLv2", "neocd": "LGPLv3",
    "nestopia": "GPLv2", "np2kai": "MIT", "nxengine": "GPLv3", "o2em": "Artistic License",
    "opera": "LGPL/Non-commercial", "parallel_n64": "GPLv2", "pce_fast": "GPLv2", "pce": "GPLv2",
    "pcfx": "GPLv2", "pcsx2": "GPL", "pcsx_rearmed": "GPLv2", "pd777": "MIT", "picodrive": "MAME",
    "play": "MIT", "pokemini": "GPLv3", "potator": "Public Domain", "ppsspp": "GPLv2",
    "prboom": "GPLv2", "prosystem": "GPLv2", "puae2021": "GPLv2", "puae": "GPLv2",
    "px68k": "Custom Non-Commercial", "quasi88": "BSD 3-Clause and MAME non-commercial",
    "reminiscence": "GPLv3", "same_cdi": "GPLv2+", "sameduck": "MIT", "scummvm": "GPLv3",
    "smsplus": "GPLv2", "snes9x": "Non-commercial", "snes9x_next": "Non-commercial",
    "stella": "GPLv2", "superbroswar": "GPLv2", "swanstation": "GPLv3", "tgbdual": "GPLv2",
    "theodore": "GPLv3", "tic80": "MIT", "tyrquake": "GPLv2", "uzem": "MIT", "vb": "GPLv2",
    "vba-m": "GPLv2", "vecx": "GPLv3", "vice_x128": "GPLv2", "vice_x64": "GPLv2",
    "vice_x64sc": "GPLv2", "vice_xpet": "GPLv2", "vice_xplus4": "GPLv2", "vice_xscpu64": "GPLv2",
    "vice_xvic": "GPLv2", "vircon32": "3-clause BSD", "virtualjaguar": "GPLv3",
    "vitaquake2-rogue": "GPLv2", "vitaquake2-xatrix": "GPLv2", "vitaquake2-zaero": "GPLv2",
    "vitaquake2": "GPLv2", "wasm4": "ISC", "x1": "BSD", "xrick": "GPLv3", "yabasanshiro": "GPLv2",
    "zc210": "(unverified — empty license field)",
}
# The 11 non-commercial blockers (docs/19 §1) + the 3 review cores (§3) — both kept OUT of a paid
# base image; the store re-adds them at the user's discretion for personal use.
_EXCLUDE_CORES = {"snes9x", "snes9x_next", "genesisplusgx", "genesisplusgx-expanded",
                  "genesisplusgx-wide", "fbneo", "fmsx", "mame078plus", "opera", "px68k", "quasi88"}
_REVIEW_CORES = {"picodrive", "hatarib", "zc210"}
# Clean commercial-OK default core per system (docs/19 §5B) — the swap the paid build ships with.
_CORE_SWAP = {"snes": "bsnes", "megadrive": "blastem", "mastersystem": "gearsystem",
              "gamegear": "gearsystem", "sg1000": "gearsystem", "neogeo": "mame", "arcade": "mame"}
# Systems with NO clean commercial-OK libretro core on the image → store-only personal-use add-ons.
_NO_CLEAN_SWAP = {"megacd": "Sega CD", "sega32x": "Sega 32X", "3do": "3DO", "x68000": "Sharp X68000",
                  "pc88": "PC-8801", "fbneo": "FinalBurn Neo arcade"}
# Systems the store always surfaces (audit-relevant) on top of any the user has ROMs for.
_AUDIT_SYSTEMS = set(_CORE_SWAP) | set(_NO_CLEAN_SWAP) | {"msx1", "msx2", "msxturbor", "colecovision"}
_CORE_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_+\-]{1,63}$")
_SYS_EMU = dict(_SYS, mastersystem="Master System", gamegear="Game Gear", sg1000="SG-1000",
                neogeo="Neo Geo", arcade="Arcade", megacd="Sega CD", sega32x="Sega 32X",
                segacd="Sega CD", fbneo="FinalBurn Neo", msx1="MSX", msx2="MSX2",
                msxturbor="MSX turbo R", colecovision="ColecoVision", x68000="Sharp X68000",
                pc88="PC-8801", **{"3do": "3DO"})

def _commercial_ok(core):
    return core not in _EXCLUDE_CORES and core not in _REVIEW_CORES

def _verdict(core):
    if core in _EXCLUDE_CORES:
        return "personal-use"   # non-commercial: fine for the user, not for a paid build
    if core in _REVIEW_CORES:
        return "review"
    return "commercial-ok"

def _installed_cores():
    try:
        suf = "_libretro.so"
        return {f[:-len(suf)] for f in os.listdir(LIBRETRO_DIR) if f.endswith(suf)}
    except Exception:
        return set()

def _effective_default(system):
    # what configgen will actually use: an explicit batocera.conf override, else the es_systems default
    return _bconf_get(system + ".core") or system_cores(system)["default"]

def emulators_list():
    installed = _installed_cores()
    rom_dirs = set()
    try:
        rom_dirs = {s for s in os.listdir(ROMS) if os.path.isdir(os.path.join(ROMS, s))}
    except Exception:
        pass
    systems = []
    for sysname in sorted(rom_dirs | _AUDIT_SYSTEMS):
        sc = system_cores(sysname)
        if not sc["cores"]:
            continue                       # not a real system on this image
        eff = _effective_default(sysname)
        cores = [{"core": c, "license": _CORE_LICENSE.get(c, "unknown"),
                  "verdict": _verdict(c), "commercial_ok": _commercial_ok(c),
                  "installed": c in installed, "active": c == eff} for c in sc["cores"]]
        systems.append({"system": sysname, "name": _SYS_EMU.get(sysname, sysname),
                        "default": eff, "default_commercial_ok": _commercial_ok(eff),
                        "clean_default": _CORE_SWAP.get(sysname),
                        "no_clean": _NO_CLEAN_SWAP.get(sysname),
                        "has_games": sysname in rom_dirs, "cores": cores})
    return {"ok": True, "systems": systems, "buildbot": BUILDBOT_BASE,
            "excluded": sorted(_EXCLUDE_CORES), "review": sorted(_REVIEW_CORES), "swap": _CORE_SWAP}

def emulator_install(payload):
    core = (payload or {}).get("core", "").strip()
    if not _CORE_NAME_RE.match(core):
        return {"ok": False, "error": "invalid core name"}
    out_path = os.path.join(LIBRETRO_DIR, core + "_libretro.so")
    # hard confinement: the resolved write target must live directly inside the libretro dir
    if os.path.realpath(out_path) != out_path or not out_path.startswith(LIBRETRO_DIR + "/"):
        return {"ok": False, "error": "refused: path escapes the libretro directory"}
    if os.path.isfile(out_path):
        return {"ok": True, "core": core, "installed": True, "already": True,
                "source": "bundled", "note": "already installed"}
    url = BUILDBOT_BASE + core + "_libretro.so.zip"
    try:
        with urllib.request.urlopen(url, timeout=90) as r:
            data = r.read()
    except Exception as e:
        return {"ok": False, "error": "libretro buildbot fetch failed: %s" % e, "url": url}
    import io, zipfile
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
        member = next((n for n in zf.namelist() if n.endswith(".so")), None)
        if not member:
            return {"ok": False, "error": "no .so found in the downloaded archive (is '%s' a real core?)" % core}
        blob = zf.read(member)
    except Exception as e:
        return {"ok": False, "error": "unzip failed: %s" % e}
    if len(blob) < 4096:
        return {"ok": False, "error": "downloaded core looks corrupt (%d bytes)" % len(blob)}
    try:
        tmp = out_path + ".tmp"
        with open(tmp, "wb") as f:
            f.write(blob); f.flush(); os.fsync(f.fileno())
        os.chmod(tmp, 0o755)
        os.replace(tmp, out_path)
    except Exception as e:
        return {"ok": False, "error": "write failed: %s" % e}
    LOG.info("EMULATOR INSTALL %s -> %s (%d bytes from buildbot)", core, out_path, len(blob))
    return {"ok": True, "core": core, "installed": True, "bytes": len(blob),
            "source": "libretro buildbot", "path": out_path, "commercial_ok": _commercial_ok(core)}

def emulator_set_default(payload):
    system = (payload or {}).get("system", "").strip()
    core = (payload or {}).get("core", "").strip()
    if not system or not _CORE_NAME_RE.match(core):
        return {"ok": False, "error": "system + valid core required"}
    if core not in _installed_cores():
        return {"ok": False, "error": "%s isn't installed — install it first" % core}
    sc = system_cores(system)
    warn = None if (not sc["cores"] or core in sc["cores"]) else \
        "%s isn't listed for %s in es_systems; configgen may ignore it" % (core, system)
    _bconf_set(system + ".core", core)
    LOG.info("EMULATOR DEFAULT %s.core=%s", system, core)
    return {"ok": True, "system": system, "core": core, "warn": warn,
            "commercial_ok": _commercial_ok(core)}

def emulator_uninstall(payload):
    core = (payload or {}).get("core", "").strip()
    if not _CORE_NAME_RE.match(core):
        return {"ok": False, "error": "invalid core name"}
    p = os.path.join(LIBRETRO_DIR, core + "_libretro.so")
    if os.path.realpath(p) != p or not p.startswith(LIBRETRO_DIR + "/") or not os.path.isfile(p):
        return {"ok": False, "error": "core not installed"}
    using = _core_default_users(core)
    if using and not (payload or {}).get("force"):
        return {"ok": False, "error": "%s is the default core for: %s — set another default first "
                "(or pass force:true)" % (core, ", ".join(using))}
    try:
        os.remove(p)
    except Exception as e:
        return {"ok": False, "error": str(e)}
    LOG.info("EMULATOR UNINSTALL %s", core)
    return {"ok": True, "core": core, "removed": True}

def _core_default_users(core):
    # which shown systems currently resolve their default to this core (so we never strand a system)
    out = []
    try:
        rom_dirs = {s for s in os.listdir(ROMS) if os.path.isdir(os.path.join(ROMS, s))}
    except Exception:
        rom_dirs = set()
    for system in sorted(rom_dirs | _AUDIT_SYSTEMS):
        if system_cores(system)["cores"] and _effective_default(system) == core:
            out.append(system)
    return out

def apply_core_swap():
    # Write the clean commercial-OK default core for every system that has one (the paid-build swap).
    # Merge-safe (per-key), and skips a system if its clean core isn't installed on this image.
    installed = _installed_cores()
    applied, skipped = {}, {}
    for system, core in _CORE_SWAP.items():
        sc = system_cores(system)
        if not sc["cores"]:
            skipped[system] = "no such system on this image"; continue
        if core not in installed:
            skipped[system] = "clean core '%s' not installed" % core; continue
        was = _effective_default(system)
        _bconf_set(system + ".core", core)
        applied[system] = {"core": core, "was": was}
    LOG.info("CORE SWAP applied=%s skipped=%s", list(applied), list(skipped))
    return {"ok": True, "applied": applied, "skipped": skipped, "no_clean_swap": _NO_CLEAN_SWAP}

# ===== Games Store: curated FREE / homebrew downloads (no commercial ROMs) =====
# Every entry is a genuinely free, redistributable game with a verified direct download URL.
# Installs are confined to /userdata/roms/<system>/ — no path escapes, no arbitrary URLs.
# "direct" writes the fetched bytes as <dest>; "zip" extracts the named member as <dest>.
GAMES_CATALOG = [
    {"id": "freedoom1", "name": "Freedoom: Phase 1", "system": "prboom",
     "desc": "Free, complete Doom-engine IWAD (single-player). BSD-style licensed.",
     "license": "Freedoom (BSD-style)", "cat": "FPS", "dest": "freedoom1.wad",
     "kind": "zip", "member": "freedoom-0.13.0/freedoom1.wad",
     "url": "https://github.com/freedoom/freedoom/releases/download/v0.13.0/freedoom-0.13.0.zip"},
    {"id": "freedoom2", "name": "Freedoom: Phase 2", "system": "prboom",
     "desc": "Free Doom II-style IWAD with 32 levels. BSD-style licensed.",
     "license": "Freedoom (BSD-style)", "cat": "FPS", "dest": "freedoom2.wad",
     "kind": "zip", "member": "freedoom-0.13.0/freedoom2.wad",
     "url": "https://github.com/freedoom/freedoom/releases/download/v0.13.0/freedoom-0.13.0.zip"},
    {"id": "ucity", "name": "µCity", "system": "gbc",
     "desc": "Open-source SimCity-style city builder for Game Boy Color.",
     "license": "GPLv3", "cat": "Strategy", "dest": "ucity.gbc",
     "kind": "direct",
     "url": "https://github.com/AntonioND/ucity/releases/download/v1.3/ucity.gbc"},
    {"id": "libbet", "name": "Libbet and the Magic Floor", "system": "gb",
     "desc": "Homebrew puzzle game for the original Game Boy.",
     "license": "zlib", "cat": "Puzzle", "dest": "libbet.gb",
     "kind": "direct",
     "url": "https://github.com/pinobatch/libbet/releases/download/v0.08/libbet.gb"},
]
_GAMES_BY_ID = {g["id"]: g for g in GAMES_CATALOG}

def _game_dest_path(g):
    d = os.path.join(ROMS, g["system"])
    return os.path.join(d, g["dest"])

def games_catalog():
    out = []
    for g in GAMES_CATALOG:
        p = _game_dest_path(g)
        e = {k: g[k] for k in ("id", "name", "system", "desc", "license", "cat")}
        e["sysname"] = _SYS_EMU.get(g["system"], _SYS.get(g["system"], g["system"]))
        e["installed"] = os.path.isfile(p)
        out.append(e)
    return {"ok": True, "games": out,
            "note": "Curated free & homebrew games with verified download links. "
                    "No commercial ROMs are distributed."}

def games_install(payload):
    gid = (payload or {}).get("id", "").strip()
    g = _GAMES_BY_ID.get(gid)
    if not g:
        return {"ok": False, "error": "unknown game id"}
    sysdir = os.path.join(ROMS, g["system"])
    out_path = _game_dest_path(g)
    # hard confinement: resolved write target must live directly inside the system's roms dir
    if os.path.realpath(out_path) != out_path or not out_path.startswith(sysdir + os.sep):
        return {"ok": False, "error": "refused: path escapes the roms directory"}
    if os.path.isfile(out_path):
        return {"ok": True, "id": gid, "installed": True, "already": True,
                "path": out_path, "note": "already installed"}
    try:
        os.makedirs(sysdir, exist_ok=True)
        with urllib.request.urlopen(g["url"], timeout=120) as r:
            data = r.read()
    except Exception as e:
        return {"ok": False, "error": "download failed: %s" % e, "url": g["url"]}
    if g["kind"] == "zip":
        import io, zipfile
        try:
            zf = zipfile.ZipFile(io.BytesIO(data))
            blob = zf.read(g["member"])
        except Exception as e:
            return {"ok": False, "error": "extract failed: %s" % e}
    else:
        blob = data
    if len(blob) < 256:
        return {"ok": False, "error": "downloaded game looks corrupt (%d bytes)" % len(blob)}
    try:
        tmp = out_path + ".tmp"
        with open(tmp, "wb") as f:
            f.write(blob); f.flush(); os.fsync(f.fileno())
        os.replace(tmp, out_path)
    except Exception as e:
        return {"ok": False, "error": "write failed: %s" % e}
    LOG.info("GAME INSTALL %s -> %s (%d bytes)", gid, out_path, len(blob))
    return {"ok": True, "id": gid, "installed": True, "bytes": len(blob), "path": out_path}

def games_uninstall(payload):
    gid = (payload or {}).get("id", "").strip()
    g = _GAMES_BY_ID.get(gid)
    if not g:
        return {"ok": False, "error": "unknown game id"}
    out_path = _game_dest_path(g)
    sysdir = os.path.join(ROMS, g["system"])
    if os.path.realpath(out_path) != out_path or not out_path.startswith(sysdir + os.sep) \
            or not os.path.isfile(out_path):
        return {"ok": False, "error": "not installed"}
    try:
        os.remove(out_path)
    except Exception as e:
        return {"ok": False, "error": str(e)}
    LOG.info("GAME UNINSTALL %s", gid)
    return {"ok": True, "id": gid, "removed": True}

class H(http.server.SimpleHTTPRequestHandler):
    # static assets (fonts/icons/css/brand art) — only served from the static dir
    _CACHE_EXT = (".woff2", ".woff", ".ttf", ".svg", ".png", ".jpg", ".jpeg",
                  ".gif", ".webp", ".css")

    def end_headers(self):
        # Single source of Cache-Control. Static assets never change at runtime →
        # cache them hard so screens snap open instead of re-downloading megabytes of
        # fonts/icons every navigation (that re-fetch was the file-manager slowness +
        # part of the flash). HTML/JS/JSON stay no-store so code pushes take effect.
        p = self.path.split("?")[0].lower()
        if p.endswith(self._CACHE_EXT):
            self.send_header("Cache-Control", "public, max-age=604800")
        else:
            self.send_header("Cache-Control", "no-store")
        http.server.SimpleHTTPRequestHandler.end_headers(self)

    def _qs(self):
        from urllib.parse import urlparse, parse_qs, unquote
        q = parse_qs(urlparse(self.path).query)
        return {k: unquote(v[0]) for k, v in q.items()}

    def _json(self, payload):
        b = json.dumps(payload).encode()
        self.send_response(200); self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(b))); self.end_headers(); self.wfile.write(b)

    def do_GET(self):
        self._wrap(self._route_get)

    def do_POST(self):
        self._wrap(self._route_post)

    def _wrap(self, fn):
        # every request: rate-limit expensive routes, run, log timing, catch+log errors (never crash)
        t0 = time.time()
        route = self.path.split("?")[0]
        if route in _LIMITS and not rate_ok(route, *_LIMITS[route]):
            LOG.warning("RATE-LIMIT %s", route)
            return self._json({"ok": False, "error": "rate limited — slow down"})
        try:
            fn()
            LOG.info("%s %s %dms", self.command, self.path, int((time.time() - t0) * 1000))
        except Exception:
            LOG.error("%s %s FAILED\n%s", self.command, self.path, traceback.format_exc())
            try: self._json({"ok": False, "error": "internal error"})
            except Exception: pass

    def _route_get(self):
        route = self.path.split("?")[0]
        if route == "/health":
            return self._json(health())
        if route == "/version":
            return self._json(VERSION)
        if route == "/boot/ok":
            # explicit "this boot is good" signal (also fired implicitly when home is served below)
            return self._json({"ok": clear_boot_attempts()})
        if route == "/boot/status":
            try:
                n = int(open(BOOT_ATTEMPTS_F).read().strip() or "0")
            except Exception:
                n = 0
            return self._json({"ok": True, "attempts": n})
        if route == "/system/backups":
            return self._json(gose_backups())
        if route == "/status.json":
            st = agent_status(); h = host_info()
            for k in ("battery_pct", "charging", "has_battery", "online",
                      "gpu_pct", "gpu_mem_used_mb", "gpu_mem_total_mb", "gpu_temp_c", "gpu_name"):
                if k in h:
                    st[k] = h[k]
            return self._json(st)
        if route == "/games.json":
            return self._json(list_games())
        if route == "/game/running":
            return self._json(game_running())
        if route == "/game/gallery":
            return self._json(game_gallery())
        if route == "/game/fps":
            return self._json(fps_get())
        if route == "/favorites.json":
            return self._json(favorites_json())
        if route == "/game/state/slots":
            from urllib.parse import parse_qs
            q = parse_qs(self.path.split("?", 1)[1] if "?" in self.path else "")
            return self._json(game_state_slots((q.get("system") or [""])[0], (q.get("game") or [""])[0]))
        if route == "/recent.json":
            return self._json(recent_games())
        if route == "/ai/players":
            return self._json(ai_players())
        if route == "/ai/grants":
            return self._json(ai_grants())
        if route == "/ai/requests":
            return self._json(ai_requests())
        if route == "/ai/audit":
            return self._json(ai_audit(self._qs().get("limit", 100)))
        if route == "/game/options":
            q = self._qs()
            return self._json(game_options(q.get("system", ""), q.get("game", "")))
        if route == "/storage.json":
            return self._json(storage_info())
        if route == "/procs.json":
            return self._json(procs_info())
        if route == "/splice/videos":
            return self._json(splice_videos())
        if route == "/splice/probe":
            return self._json(splice_probe(self._qs().get("path")))
        if route == "/store/catalog":
            return self._json(store_catalog())
        if route == "/emulators":
            return self._json(emulators_list())
        if route == "/games/catalog":
            return self._json(games_catalog())
        if route == "/queue.json":
            return self._json(queue_state())
        if route == "/net.json":
            return self._json(net_info())
        if route == "/sys/audio":
            return self._json(sys_audio())
        if route == "/sys/brightness":
            return self._json(sys_brightness())
        if route == "/net/scan":
            return self._json(host_bridge("/wifi/scan"))
        if route == "/net/wifi":
            return self._json(host_bridge("/wifi/status"))
        if route == "/capture/buffer":
            return self._json(host_bridge("/clip/status"))
        if route == "/bt/status":
            return self._json(bt_status())
        if route == "/apps.json":
            return self._json(installed_apps())
        if route == "/syslogo":
            p = system_logo_path(self._qs().get("system", ""))
            if not p or not os.path.isfile(p):
                self.send_error(404); return
            ctype = "image/svg+xml" if p.endswith(".svg") else (mimetypes.guess_type(p)[0] or "image/png")
            try:
                with open(p, "rb") as fh:
                    data = fh.read()
                self.send_response(200); self.send_header("Content-Type", ctype)
                self.send_header("Cache-Control", "max-age=86400")
                self.send_header("Content-Length", str(len(data))); self.end_headers(); self.wfile.write(data)
            except Exception:
                self.send_error(500)
            return
        if route == "/appicon":
            p = app_icon_path(self._qs().get("id", ""))
            if not p or not os.path.isfile(p):
                self.send_error(404); return
            ctype = mimetypes.guess_type(p)[0] or "image/png"
            try:
                with open(p, "rb") as fh:
                    data = fh.read()
                self.send_response(200); self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(data))); self.end_headers(); self.wfile.write(data)
            except Exception:
                self.send_error(500)
            return
        if route == "/fs/list":
            return self._json(fs_list(self._qs().get("path")))
        if route == "/fs/sizes":
            return self._json(fs_sizes(self._qs().get("path")))
        if route == "/fs/places":
            return self._json(fs_places())
        if route == "/fs/read":
            return self._json(fs_read(self._qs().get("path")))
        if route == "/fs/file":
            f = _safe(self._qs().get("path"))
            if not f or not os.path.isfile(f):
                self.send_error(404); return
            ctype = mimetypes.guess_type(f)[0] or "application/octet-stream"
            try:
                with open(f, "rb") as fh:
                    data = fh.read()
                self.send_response(200); self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(data))); self.end_headers()
                self.wfile.write(data)
            except Exception:
                self.send_error(500)
            return
        # serving the home page is proof the UI booted far enough to render -> clear the crash counter
        if route in ("/gose-home.html", "/", "/index.html"):
            clear_boot_attempts()
        return super().do_GET()

    def _route_post(self):
        route = self.path.split("?")[0]
        if route in ("/store/install", "/store/uninstall"):
            try:
                n = int(self.headers.get("Content-Length", 0))
                payload = json.loads(self.rfile.read(n).decode() or "{}")
                if route == "/store/uninstall":
                    return self._json(store_uninstall(payload.get("id")))
                return self._json(store_install(payload.get("id")))
            except Exception as e:
                return self._json({"ok": False, "error": str(e)})
        if route in ("/emulators/install", "/emulators/default", "/emulators/uninstall", "/emulators/swap"):
            try:
                n = int(self.headers.get("Content-Length", 0))
                payload = json.loads(self.rfile.read(n).decode() or "{}") if n else {}
            except Exception:
                payload = {}
            if route == "/emulators/install":
                return self._json(emulator_install(payload))
            if route == "/emulators/default":
                return self._json(emulator_set_default(payload))
            if route == "/emulators/uninstall":
                return self._json(emulator_uninstall(payload))
            return self._json(apply_core_swap())
        if route in ("/games/install", "/games/uninstall"):
            try:
                n = int(self.headers.get("Content-Length", 0))
                payload = json.loads(self.rfile.read(n).decode() or "{}") if n else {}
            except Exception:
                payload = {}
            if route == "/games/install":
                return self._json(games_install(payload))
            return self._json(games_uninstall(payload))
        if route == "/term/exec":
            try:
                n = int(self.headers.get("Content-Length", 0))
                payload = json.loads(self.rfile.read(n).decode() or "{}")
                return self._json(term_exec(payload.get("cmd")))
            except Exception as e:
                return self._json({"ok": False, "out": str(e)})
        if route in ("/system/backup", "/system/restore", "/system/factory_reset"):
            try:
                n = int(self.headers.get("Content-Length", 0))
                payload = json.loads(self.rfile.read(n).decode() or "{}") if n else {}
            except Exception:
                payload = {}
            if route == "/system/backup":
                return self._json(gose_backup("manual"))
            if route == "/system/restore":
                return self._json(gose_restore(payload))
            return self._json(gose_factory_reset(payload))
        if route == "/guide/toggle":
            return self._json(guide_toggle())
        if route == "/game/exit":
            return self._json(game_exit())
        if route == "/game/screenshot":
            return self._json(game_screenshot())
        if route == "/game/record/toggle":
            return self._json(game_record_toggle())
        if route in ("/game/favorite", "/game/fps"):
            try:
                n = int(self.headers.get("Content-Length", 0))
                payload = json.loads(self.rfile.read(n).decode() or "{}") if n else {}
            except Exception:
                payload = {}
            if route == "/game/favorite":
                return self._json(game_favorite(payload))
            return self._json(fps_set(payload.get("on")))
        if route == "/game/savestate":
            return self._json(game_state("save"))
        if route == "/game/loadstate":
            return self._json(game_state("load"))
        if route == "/game/slot":
            try:
                n = int(self.headers.get("Content-Length", 0))
                payload = json.loads(self.rfile.read(n).decode() or "{}") if n else {}
            except Exception:
                payload = {}
            return self._json(game_slot(payload.get("dir") or payload.get("direction")))
        if route in ("/scrape", "/game/options"):
            try:
                n = int(self.headers.get("Content-Length", 0))
                payload = json.loads(self.rfile.read(n).decode() or "{}") if n else {}
            except Exception:
                payload = {}
            if route == "/game/options":
                return self._json(set_game_options(payload))
            return self._json(scrape_system(payload.get("system", ""), force=True))
        if route in ("/ai/join", "/ai/heartbeat", "/ai/leave", "/ai/grant", "/ai/revoke",
                     "/ai/request", "/ai/request/clear"):
            try:
                n = int(self.headers.get("Content-Length", 0))
                payload = json.loads(self.rfile.read(n).decode() or "{}") if n else {}
            except Exception:
                payload = {}
            if route == "/ai/grant":
                return self._json(ai_grant(payload))
            if route == "/ai/revoke":
                return self._json(ai_revoke(payload))
            if route == "/ai/request":
                return self._json(ai_request(payload))
            if route == "/ai/request/clear":
                return self._json(ai_request_clear(payload))
            return self._json(ai_leave(payload) if route == "/ai/leave" else ai_join(payload))
        if route in ("/capture/shot", "/capture/buffer", "/capture/clip"):
            try:
                n = int(self.headers.get("Content-Length", 0))
                payload = json.loads(self.rfile.read(n).decode() or "{}") if n else {}
            except Exception:
                payload = {}
            if route == "/capture/shot":
                return self._json(capture_shot(payload))
            if route == "/capture/buffer":
                return self._json(host_bridge("/clip/start" if payload.get("on") else "/clip/stop", {}))
            return self._json(capture_clip(payload.get("seconds", 30)))
        if route in ("/net/connect", "/net/disconnect"):
            try:
                n = int(self.headers.get("Content-Length", 0))
                payload = json.loads(self.rfile.read(n).decode() or "{}")
            except Exception:
                payload = {}
            if route == "/net/connect":
                return self._json(host_bridge("/wifi/connect", payload, timeout=30))
            return self._json(host_bridge("/wifi/disconnect", {}, timeout=20))
        if route in ("/fs/op", "/proc/kill", "/splice/cut", "/launch",
                     "/sys/audio", "/sys/brightness", "/sys/power", "/sys/perf", "/bt"):
            try:
                n = int(self.headers.get("Content-Length", 0))
                payload = json.loads(self.rfile.read(n).decode() or "{}")
                if route == "/fs/op":
                    return self._json(fs_op(payload))
                if route == "/splice/cut":
                    return self._json(splice_cut(payload))
                if route == "/launch":
                    return self._json(launch_app(payload))
                if route == "/sys/audio":
                    return self._json(sys_audio(payload.get("volume"), payload.get("mute")))
                if route == "/sys/brightness":
                    return self._json(sys_brightness(payload.get("value")))
                if route == "/sys/power":
                    return self._json(sys_power(payload.get("action")))
                if route == "/sys/perf":
                    return self._json(sys_perf(payload.get("mode")))
                if route == "/bt":
                    return self._json(bt_action(payload))
                return self._json(proc_kill(payload.get("pid"), payload.get("sig", 15)))
            except Exception as e:
                return self._json({"ok": False, "error": str(e)})
        self.send_error(404)

    def log_message(self, *a):
        pass

class Server(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True

h = functools.partial(H, directory=ROOT)
ensure_user_dirs()   # Desktop/Documents/Downloads/Pictures/Music/Videos exist on boot
threading.Thread(target=_queue_worker, daemon=True).start()   # download queue: one install at a time
threading.Thread(target=auto_scrape_boot, daemon=True).start()   # auto-fill missing cover art on boot
print("serving GOSE UI + live /status.json on 127.0.0.1:8780 (threaded)")
# threaded: a slow /fs/sizes (du) or the 4s agent socket no longer blocks page loads
Server(("127.0.0.1", 8780), h).serve_forever()
