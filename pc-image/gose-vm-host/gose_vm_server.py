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
           "/system/backup": (6, 120), "/system/restore": (4, 120), "/system/factory_reset": (3, 300),
           "/sys/perf": (60, 60), "/widgets/store": (30, 60), "/widgets/steam": (30, 60),
           "/storage/import": (12, 60), "/storage/detected": (30, 60)}

_SKIPDIRS = {"images", "videos", "manuals", "media", "downloaded_images", "downloaded_media"}
# .disabled = store-placeholder marker (Game.ext.disabled). splitext only strips the LAST
# suffix, so the enabled file ("Game.cannonball") keeps its real extension and still lists.
_SKIPEXT = {".txt", ".xml", ".cfg", ".dat", ".jpg", ".jpeg", ".png", ".mp4", ".srm", ".state",
            ".disabled"}
# engine/runtime data shipped alongside roms that is NOT itself a game
# (prboom.wad is PrBoom's resource wad; game IWADs like doom1_shareware.wad stay listed)
_ENGINE_DATA = {("prboom", "prboom.wad")}
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

def _sys_exts(system):
    """Launchable rom extensions for a system, from es_systems.cfg (via the cached
    _ext_sys_map — same source ES uses). e.g. pygame -> {'.pygame'}."""
    ext_map, _ = _ext_sys_map()
    return {e for e, syss in ext_map.items() if system in syss}

def _dir_game_rom(system, dirpath):
    """Directory-shaped games (e.g. pygame: roms/pygame/pygun/pygun.pygame): the launchable
    rom is a recognized entry file ONE level down (extension from es_systems.cfg). Prefers
    an entry named after the directory. Returns the entry's full path, or None (not a game)."""
    exts = _sys_exts(system)
    if not exts:
        return None
    try:
        names = sorted(os.listdir(dirpath))
    except Exception:
        return None
    dirname = os.path.basename(dirpath).lower()
    best = None
    for f in names:
        stem, ext = os.path.splitext(f)
        if ext.lower() in exts and os.path.isfile(os.path.join(dirpath, f)):
            if stem.lower() == dirname:
                return os.path.join(dirpath, f)
            best = best or os.path.join(dirpath, f)
    return best

def list_games():
    try:
        favset = _fav_set()
        systems = []
        for sysname in sorted(os.listdir(ROMS)):
            d = os.path.join(ROMS, sysname)
            if not os.path.isdir(d):
                continue
            games = []
            for f in os.listdir(d):
                if f.startswith(".") or f in _SKIPDIRS or "gamelist" in f:
                    continue
                if (sysname, f.lower()) in _ENGINE_DATA:
                    continue
                p = os.path.join(d, f)
                if os.path.isdir(p):
                    rom = _dir_game_rom(sysname, p)   # directory-shaped game?
                    if not rom:
                        continue
                    stem = os.path.splitext(os.path.basename(rom))[0]
                else:
                    if os.path.splitext(f)[1].lower() in _SKIPEXT:
                        continue
                    stem = os.path.splitext(f)[0]
                games.append({"name": stem, "img": _game_img(sysname, stem),
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
    # art lookup: stem match is CASE-INSENSITIVE (mrboom.png vs stem "MrBoom") and bare
    # .jpg/.jpeg are accepted (sdlpop.jpg). Single source — Library/recents/widgets all use this.
    d = os.path.join(ROMS, system, "images")
    try:
        names = {n.lower(): n for n in os.listdir(d)}
    except Exception:
        return None
    for suf in ("-image.png", "-thumb.png", "-image.jpg", ".png", ".jpg", ".jpeg"):
        n = names.get((game + suf).lower())
        if n:
            return os.path.join(d, n)
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

# Privacy: scraping cover art sends the user's ROM filenames (= their game library) to a
# third-party art server (thumbnails.libretro.com). So the AUTO pass is OFF by default —
# it runs only if the user opted in (this flag file exists). The manual "Scrape" button in
# the Library always works on demand. (A Privacy/Settings toggle creates/removes this flag.)
SCRAPE_AUTO_FLAG = "/userdata/system/gose/scrape_auto"

def auto_scrape_boot():
    # Background, one-shot per boot: fill in any missing cover art automatically. OPT-IN only
    # (privacy — see SCRAPE_AUTO_FLAG above). Cheap on reboot thanks to the scrape_state manifest.
    try:
        if not os.path.exists(SCRAPE_AUTO_FLAG):
            LOG.info("auto-scrape skipped (opt-in only; create %s to enable). Manual Scrape still works.",
                     SCRAPE_AUTO_FLAG)
            return
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

# Electron/Chromium flatpaks need a private D-Bus session bus AND --no-sandbox when
# run as root (GOSE runs as root): without them Electron insta-crashes
# ("Running as root without --no-sandbox is not supported" / "A connection to the
# bus can't be made"). Firefox (Gecko) and Steam DON'T need this and would
# misparse --no-sandbox, so only the known Chromium/Electron apps get wrapped.
ELECTRON_FLATPAKS = {
    "md.obsidian.Obsidian", "com.discordapp.Discord", "com.spotify.Client",
    "com.google.Chrome", "org.chromium.Chromium",
}

# VLC hard-refuses to run as uid 0 ("VLC is not supposed to be run as root. Sorry.")
# and exits — there is NO official root-allow flag/env, so the supported fix is to run
# it as an UNPRIVILEGED user. GOSE's shell is root, so we drop to Batocera's standard
# non-root account (`batocera`, uid 1000, always present) for VLC only. Batocera
# deliberately makes the flatpak system install world-readable (batocera-flatpak-update:
# `chmod -R a+rX .../binaries`), which is what makes a non-root `flatpak run` possible;
# we add the few writable dirs a non-root flatpak still needs. We do NOT patch the VLC
# binary (fragile, lost on flatpak update) and do NOT loosen the root shell.
VLC_FLATPAK = "org.videolan.VLC"
VLC_USER = "batocera"
VLC_UID = "1000"
VLC_HOME = "/userdata/system/.gose/vlc-home"           # user-owned HOME (flatpak user-install + cache)
# flatpak forces a SYSTEM-installed app's per-app data under the system install's data
# dir regardless of $HOME, so this exact path must be writable by the non-root user.
VLC_APPDATA = "/userdata/saves/flatpak/data/.var/app/" + VLC_FLATPAK

def _vlc_nonroot_cmd():
    # A root sh-script (run by _spawn, which is root) that idempotently preps the
    # unprivileged user's writable dirs + the per-boot runtime dir (/run is tmpfs →
    # reset every boot), then su-drops to launch VLC's GUI with display/audio/flatpak
    # env wired. Idempotent + self-healing, so it works on a fresh image and after a
    # reboot without depending on boot-script ordering.
    inner = (
        "export HOME=" + VLC_HOME +
        " XDG_RUNTIME_DIR=/run/user/" + VLC_UID +
        " XDG_DATA_HOME=" + VLC_HOME + "/.local/share" +
        " XDG_CACHE_HOME=" + VLC_HOME + "/.cache" +
        " FLATPAK_USER_DIR=" + VLC_HOME + "/.local/share/flatpak" +
        " DISPLAY=:0 PULSE_SERVER=unix:/run/pulse/native; "
        "exec flatpak run " + VLC_FLATPAK
    )
    return (
        "mkdir -p " + VLC_HOME + "/.local/share/flatpak " + VLC_HOME + "/.cache "
        + VLC_APPDATA + " /run/user/" + VLC_UID + " 2>/dev/null; "
        "chown -R " + VLC_UID + ":" + VLC_UID + " " + VLC_HOME + " " + VLC_APPDATA
        + " /run/user/" + VLC_UID + " 2>/dev/null; "
        "chmod 755 /run/user 2>/dev/null; chmod 700 /run/user/" + VLC_UID + " 2>/dev/null; "
        "chmod a+rx /userdata /userdata/saves /userdata/saves/flatpak 2>/dev/null; "
        "exec su -s /bin/sh " + VLC_USER + " -c '" + inner + "'"
    )

def _wrap_flatpak_run(cmd):
    # cmd like "flatpak run <appid> [extra...]". For Electron/Chromium apps, give a
    # session bus (dbus-run-session) + --no-sandbox so the Apps-page tile actually
    # opens a window instead of crashing. VLC refuses to run as root → run it as an
    # unprivileged user instead. Everything else is returned untouched.
    parts = cmd.split()
    if len(parts) >= 3 and parts[0] == "flatpak" and parts[1] == "run":
        if parts[2] == VLC_FLATPAK:
            return _vlc_nonroot_cmd()
        if parts[2] in ELECTRON_FLATPAKS:
            return "dbus-run-session -- " + cmd + " --no-sandbox"
    return cmd

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
    {"id": "md.obsidian.Obsidian", "name": "Obsidian", "desc": "Markdown notes & knowledge vault", "cat": "Productivity", "icon": "file-text"},
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
            if "gamelist" in f or f.startswith("."):
                continue
            if os.path.isdir(os.path.join(d, f)):
                if f in _SKIPDIRS:
                    continue
                r = _dir_game_rom(system, os.path.join(d, f))   # directory-shaped game
                if r and os.path.splitext(os.path.basename(r))[0] == game:
                    return os.path.basename(r)
                continue
            if os.path.splitext(f)[0] == game and os.path.splitext(f)[1].lower() not in _SKIPEXT:
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

# EV_KEY ranges that mean "this node has real PAD buttons" — mirrors the bridge's
# GAMEPAD_BTN_RANGES (gose-pad-nav.py): BTN_MISC..BTN_MOUSE, BTN_JOYSTICK..BTN_DIGI,
# BTN_DPAD_*. Mouse/digitizer/keyboard/switch codes fall outside on purpose.
_PAD_BTN_RANGES = ((0x100, 0x110), (0x120, 0x140), (0x220, 0x224))

def _blk_has_pad_buttons(blk):
    """True if this /proc/bus/input/devices block advertises at least one real
    pad button. A composite pad's sibling nodes (Motion Sensors / Touchpad /
    Headset Jack) also expose js* handlers and 'Controller' names but have no
    pad buttons — before this check the DualSense Motion Sensors node showed
    up as a Hub controller (settable as OS-admin!) and claimed a game player
    slot, shifting every AI seat (+ the same disease the bridge's is_gamepad
    fix cured, 2026-06-07). KEY= words are 64-bit (x86_64 guest), most
    significant first."""
    m = re.search(r"^B: KEY=([0-9a-fA-F ]+)\s*$", blk, re.M)
    if not m:
        return False
    bits = 0
    for w in m.group(1).split():
        bits = (bits << 64) | int(w, 16)
    return any((bits >> c) & 1 for lo, hi in _PAD_BTN_RANGES for c in range(lo, hi))

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
        if not _blk_has_pad_buttons(blk):
            continue          # sensor/touchpad sibling node: not a player, and SDL
                              # won't count it as a joystick either (keep idx aligned)
        all_js.append(int(jss[0]))
        if not evs:
            continue
        entry = (int(jss[0]), "/dev/input/event" + evs[0])
        name_m = re.search(r'Name="([^"]*)"', blk)
        name = name_m.group(1) if name_m else "pad"
        if "gose-passthrough" in blk:
            # Host-pad PASSTHROUGH (uinput mirror of the human's physical pad).
            # Goes to the PHYS list — it IS a human player, lowest player slot —
            # with its REAL GUID (pt_open recreated the real vendor/product/version,
            # so the kernel-id GUID matches the launcher DB's entry for that pad).
            ids = re.search(r"Bus=(\w+) Vendor=(\w+) Product=(\w+) Version=(\w+)", blk)
            guid = (_sdl_guid(*(int(x, 16) for x in ids.groups())) if ids else _XBOX_GUID)
            phys.append(entry + (guid, name))
        elif "py-evdev-uinput" in blk:
            # AI seat pad. Bind with the Xbox-360 GUID (identity → real button maps),
            # but report its OWN name ("AI virtual controller N") to the launcher; the
            # bind keys off the GUID, not the name, so this is purely cosmetic/legible.
            virt.append(entry + (_XBOX_GUID, name))
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
        if "gamelist" in f or f.startswith("."):
            continue
        p = os.path.join(d, f)
        if os.path.isdir(p):
            if f in _SKIPDIRS:
                continue
            r = _dir_game_rom(system, p)   # directory-shaped game: launch its entry file
            if r and os.path.splitext(os.path.basename(r))[0] == game:
                rom = r; break
            continue
        if os.path.splitext(f)[0] == game and os.path.splitext(f)[1].lower() not in _SKIPEXT:
            rom = p; break
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
        argv = ["/bin/sh", "-c", _wrap_flatpak_run(cmd)]   # emulatorlauncher / flatpak run / etc.
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

# ---- battery / power ----
# Real handheld hardware exposes a battery under /sys/class/power_supply/BAT*.
# The dev VM has none, so we source the laptop's REAL battery via the host bridge
# (a live, moving number to test against). battery_source is always honest about which.
BAT_OVERRIDE_F = "/tmp/gose-bat-override"   # test hook: {"battery_pct":N,"charging":bool}

def _local_battery():
    import glob as _glob
    for d in sorted(_glob.glob("/sys/class/power_supply/BAT*")):
        try:
            cap = int(open(os.path.join(d, "capacity")).read().strip())
        except Exception:
            continue
        try:
            status = open(os.path.join(d, "status")).read().strip()
        except Exception:
            status = ""
        charging = status in ("Charging", "Full", "Not charging")
        secs = None
        try:   # time-to-empty estimate from charge_now / current_now (or energy_now / power_now)
            if not charging:
                now = full = rate = None
                for nm, rt in (("charge_now", "current_now"), ("energy_now", "power_now")):
                    if os.path.isfile(os.path.join(d, nm)) and os.path.isfile(os.path.join(d, rt)):
                        now = int(open(os.path.join(d, nm)).read().strip())
                        rate = int(open(os.path.join(d, rt)).read().strip())
                        break
                if now is not None and rate and rate > 0:
                    secs = int(now / rate * 3600)
        except Exception:
            secs = None
        return {"has_battery": True, "battery_pct": cap, "charging": charging,
                "secs_left": secs, "battery_source": "local:" + os.path.basename(d)}
    return None

def battery_info():
    # 0) test override (QA: force a low value without draining a laptop) — honest source
    try:
        if os.path.isfile(BAT_OVERRIDE_F):
            o = json.loads(open(BAT_OVERRIDE_F).read() or "{}")
            return {"ok": True, "has_battery": True,
                    "battery_pct": o.get("battery_pct"), "charging": bool(o.get("charging")),
                    "secs_left": o.get("secs_left"), "battery_source": "override:test"}
    except Exception:
        pass
    # 1) real local battery (handheld hardware)
    lb = _local_battery()
    if lb:
        lb["ok"] = True
        return lb
    # 2) dev VM: the laptop's real battery via the host bridge
    h = host_info()
    if h.get("has_battery"):
        return {"ok": True, "has_battery": True,
                "battery_pct": h.get("battery_pct"), "charging": h.get("charging"),
                "secs_left": h.get("secs_left"),
                "battery_source": h.get("battery_source") or "host:laptop"}
    return {"ok": True, "has_battery": False, "battery_pct": None, "charging": None,
            "secs_left": None, "battery_source": None}

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

POWER_LOG_F = "/userdata/gose-ui/power_actions.log"

def _power_log(msg):
    try:
        with open(POWER_LOG_F, "a") as f:
            f.write(time.strftime("%Y-%m-%dT%H:%M:%S ") + msg + "\n")
    except Exception:
        pass

def sys_power(action):
    # "suspend" is the canonical name; "sleep" kept as an alias.
    if action == "suspend":
        action = "sleep"
    cmds = {"sleep": ["/bin/sh", "-c", "systemctl suspend 2>/dev/null || echo mem > /sys/power/state"],
            "restart": ["/bin/sh", "-c", "batocera-es-swissknife --reboot 2>/dev/null || reboot"],
            "shutdown": ["/bin/sh", "-c", "batocera-es-swissknife --shutdown 2>/dev/null || poweroff"]}
    if action not in cmds:
        _power_log("REJECTED bad action=%r" % action)
        return {"ok": False, "error": "bad action"}
    # On hardware without a real battery (the dev VM), suspend can't truly ACPI-sleep —
    # writing 'mem' to /sys/power/state would hang the guest. Log + no-op so the action
    # PATH is verifiable; real suspend is [needs hardware].
    if action == "sleep" and _local_battery() is None:
        _power_log("INVOKED action=suspend simulated=yes (no local battery; VM cannot ACPI-suspend)")
        return {"ok": True, "action": "suspend", "simulated": True,
                "note": "[needs hardware] VM has no battery; real ACPI-suspend not attempted"}
    try:
        _power_log("INVOKED action=%s simulated=no" % action)
        _spawn(cmds[action]); return {"ok": True, "action": action}
    except Exception as e:
        _power_log("ERROR action=%s err=%s" % (action, e))
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

# ---- Peripherals: unified view of USB (host bridge) + Wi-Fi (host bridge) + Bluetooth (guest) ----
def peripherals():
    # USB list comes from the host bridge (it sees the laptop's real USB tree + drives usbredirect);
    # Wi-Fi status likewise; Bluetooth is passed THROUGH so we read it in-guest via bluetoothctl.
    # Tolerate the host bridge being down — the page still renders BT + a clear "USB offline".
    usb = host_bridge("/usb", timeout=8)
    if not (isinstance(usb, dict) and usb.get("ok")):
        usb = {"ok": False, "devices": [],
               "error": (usb or {}).get("error", "host bridge unreachable")}
    wifi = host_bridge("/wifi/status", timeout=6)
    if not isinstance(wifi, dict):
        wifi = {"ok": False}
    try:
        bt = bt_status()
    except Exception as e:
        bt = {"ok": False, "error": str(e)}
    return {"ok": True, "usb": usb, "wifi": wifi, "bluetooth": bt}

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

# ---- AI permission grants (the owner-approval, UAC-style model — a human grants a tier; an AI can REQUEST
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
            if payload.get("pair"):
                # OOBE / first pairing: keep an observe-tier roster entry WITH a token so the AI is
                # identifiable and appears in the AI Hub. (An anonymous observe AI needs no grant; a
                # *paired* observe AI does, so it shows up and can later be elevated by the owner.)
                # This is still the safe default tier — it never self-elevates (docs/16).
                prev = g.get(name, {})
                g[name] = {"tier": "observe", "granted_at": int(time.time()), "expires": None,
                           "seat": None, "paired_via": payload.get("via", "oobe"),
                           "token": prev.get("token") or secrets.token_hex(16)}
            else:
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

# ---- First-boot / OOBE (docs/25) -------------------------------------------------------
# A flag file decides whether the kiosk lands on the first-boot wizard or the desktop.
# Completing the wizard WRITES the flag, persists the owner account, applies the privacy
# defaults (opt-IN only — docs/24), and optionally issues the first AI pairing token.
# Reset = remove the flag (also done by factory reset) -> next boot re-runs the wizard.
OOBE_DONE_FLAG = "/userdata/system/gose/.oobe-done"
ACCOUNTS_F = "/userdata/system/gose/accounts.json"

def _accounts_load():
    try:
        return json.load(open(ACCOUNTS_F))
    except Exception:
        return {"users": []}

def oobe_status():
    done = os.path.exists(OOBE_DONE_FLAG)
    info = {}
    if done:
        try:
            info = json.load(open(OOBE_DONE_FLAG)) or {}
        except Exception:
            info = {}
    acc = _accounts_load()
    owner = next((u for u in acc.get("users", []) if u.get("role") == "owner"), None)
    return {"ok": True, "done": done, "completed_at": info.get("completed_at"), "owner": owner}

def _apply_oobe_privacy(privacy):
    # Everything OFF by default, opt-IN only (docs/24). Only the box-art scrape has a real
    # server-side effect today (the scrape_auto flag, read by auto_scrape_boot); the other
    # choices are recorded so the rest of the OS can honor them as those features land.
    try:
        os.makedirs(os.path.dirname(SCRAPE_AUTO_FLAG), exist_ok=True)
        if privacy.get("boxart_scrape"):
            with open(SCRAPE_AUTO_FLAG, "w") as f:
                f.write("1")                            # explicit opt-IN created the flag
        elif os.path.exists(SCRAPE_AUTO_FLAG):
            os.remove(SCRAPE_AUTO_FLAG)                 # default OFF -> ensure the flag is absent
    except Exception as e:
        LOG.warning("oobe privacy apply failed: %s", e)

def oobe_complete(payload):
    p = payload or {}
    acct = p.get("account") or {}
    username = (acct.get("username") or "owner").strip()[:32] or "owner"
    display = (acct.get("display") or username).strip()[:48]
    # The owner account = the canonical account store the lock screen reads later. Passwords/PINs
    # are NOT stored in cleartext here; real hashing lands with the auth backend (docs/24 §1.5) —
    # OOBE records only that they were set.
    users = [{"username": username, "display": display, "role": "owner",
              "accent": acct.get("accent") or "#5cd0ff",
              "has_password": bool(acct.get("has_password")), "has_pin": bool(acct.get("has_pin")),
              "created_at": int(time.time())}]
    write_json_atomic(ACCOUNTS_F, {"users": users,
                                   "device_name": (p.get("device_name") or "GOSE").strip()[:48],
                                   "locale": p.get("locale"), "keyboard": p.get("keyboard"),
                                   "timezone": p.get("timezone"), "theme": p.get("theme")})
    _apply_oobe_privacy(p.get("privacy") or {})
    paired = None
    ai = p.get("ai") or {}
    if (ai.get("name") or "").strip():
        paired = ai_grant({"name": ai["name"].strip()[:32], "tier": "observe",
                           "pair": True, "via": "oobe"})
    info = {"completed_at": int(time.time()), "owner": username}
    try:
        os.makedirs(os.path.dirname(OOBE_DONE_FLAG), exist_ok=True)
        write_json_atomic(OOBE_DONE_FLAG, info)
    except Exception as e:
        return {"ok": False, "error": "could not write first-boot flag: %s" % e}
    LOG.info("OOBE complete: owner=%s device=%s ai=%s", username, p.get("device_name"),
             ai.get("name") or "(none)")
    return {"ok": True, "owner": username, "ai_paired": bool(paired and paired.get("ok")),
            "ai_name": (ai.get("name") or "").strip() or None,
            "ai_token": (paired or {}).get("token")}

def oobe_reset(payload=None):
    removed = []
    try:
        if os.path.exists(OOBE_DONE_FLAG):
            os.remove(OOBE_DONE_FLAG); removed.append(".oobe-done")
    except Exception as e:
        LOG.warning("oobe reset flag failed: %s", e)
    if (payload or {}).get("wipe_account"):
        try:
            if os.path.exists(ACCOUNTS_F):
                os.remove(ACCOUNTS_F); removed.append("accounts.json")
        except Exception as e:
            LOG.warning("oobe reset accounts failed: %s", e)
    LOG.info("OOBE reset: removed=%s", removed)
    return {"ok": True, "removed": removed, "note": "next boot will re-run the first-boot wizard"}

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
    try:
        reset += oobe_reset({"wipe_account": True}).get("removed", [])   # back to first-boot wizard
    except Exception as e:
        LOG.warning("reset oobe failed: %s", e)
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

# ===== STORAGE AUTO-IMPORT (docs/25 §5.3): detect ROMs on inserted SD/USB -> offer -> import =====
# REUSE, not reinvent: Batocera's stock removable-storage stack already does the mounting. The base
# udev rule 99-external-storage.rules -> batocera-storage-udev -> batocera-storage-manager mounts ANY
# inserted partition (with a filesystem) under /media/<label> and skips the system/boot/userdata LUNs.
# GOSE does NOT duplicate that mount logic. A PARALLEL GOSE udev rule fires gose-storage-handler.sh,
# which waits for Batocera's mount to appear, then POSTs /storage/detected here. We scan the mount,
# classify ROM-shaped files by extension (parsed from es_systems.cfg, the same source ES uses), and
# offer to COPY them into /userdata/roms/<system>. COPY (not symlink / not Batocera's mergerfs union):
# removable media that gets pulled out must never break the Library or leave dangling links -- a copy
# makes the games permanently the user's, present after the card is gone. (Batocera's own roms-on-USB
# feature is a mergerfs union that vanishes on eject; that's the wrong contract for "add to Library".)
STORAGE_STATE_F = "/userdata/gose-ui/storage_offers.json"
_STORAGE_LOCK = threading.Lock()
_STORAGE_ABORT = set()          # vol_ids whose device was pulled mid-import -> stop copying
_EXT_SYS_CACHE = {"map": None, "names": None}

# extensions too generic to classify alone (need a system-named parent folder as the hint)
_AMBIG_EXT = {".zip", ".7z", ".bin", ".cue", ".iso", ".img", ".chd", ".rom", ".cso", ".m3u", ".pbp"}
# obvious non-ROM file types skipped while scanning
_NONROM_EXT = {".txt", ".xml", ".cfg", ".dat", ".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp",
               ".svg", ".mp4", ".mov", ".mkv", ".webm", ".avi", ".srm", ".state", ".sav", ".nfo",
               ".md", ".pdf", ".log", ".json", ".ini", ".db", ".html", ".css", ".js", ".ico",
               ".lnk", ".sys", ".inf", ".exe", ".dll", ".tmp", ".part"}
_SCAN_SKIPDIRS = {"system volume information", "$recycle.bin", ".trash-1000", ".spotlight-v100",
                  ".fseventsd", "images", "videos", "media", "manuals", "downloaded_images",
                  "downloaded_media", ".git", "bios", "saves", "cheats", "screenshots"}
_SCAN_MAX_FILES = 5000
_SCAN_MAX_DEPTH = 6

def _ext_sys_map():
    """Parse es_systems.cfg -> ({ext: set(system_ids)}, {system_id: fullname}). Cached (single source)."""
    if _EXT_SYS_CACHE["map"] is not None:
        return _EXT_SYS_CACHE["map"], _EXT_SYS_CACHE["names"]
    ext_map, names = {}, {}
    try:
        txt = open(_ES).read()
        for block in re.findall(r"<system>.*?</system>", txt, re.S):
            nm = re.search(r"<name>([^<]+)</name>", block)
            if not nm:
                continue
            sysid = nm.group(1).strip()
            full = re.search(r"<fullname>([^<]+)</fullname>", block)
            names[sysid] = full.group(1).strip() if full else sysid
            exts = re.search(r"<extension>([^<]+)</extension>", block)
            if exts:
                for e in exts.group(1).split():
                    e = e.strip().lower()
                    if e.startswith("."):
                        ext_map.setdefault(e, set()).add(sysid)
    except Exception:
        pass
    _EXT_SYS_CACHE["map"], _EXT_SYS_CACHE["names"] = ext_map, names
    return ext_map, names

def _sys_fullname(sysid):
    _, names = _ext_sys_map()
    return _SYS_EMU.get(sysid) or names.get(sysid) or _SYS.get(sysid) or sysid

def _classify_rom(path, ext_map, known):
    """Map a file to a system id, or None. A system-named parent folder is the strongest signal
    (matches Batocera's roms/<system>/ layout + how people organise cards); a uniquely-owned
    extension is next; ambiguous extensions with no folder hint are NOT guessed."""
    ext = os.path.splitext(path)[1].lower()
    if not ext or ext in _NONROM_EXT:
        return None
    parent = os.path.basename(os.path.dirname(path)).lower()
    cands = ext_map.get(ext)
    if parent in known and (not cands or parent in cands or ext in _AMBIG_EXT):
        return parent
    if cands and len(cands) == 1:
        return next(iter(cands))
    return None   # ambiguous, no usable hint -> leave unclassified

def _is_external_mount(rp):
    # only ever touch volumes Batocera mounted under /media/<name> (never the OS/data/boot disks)
    return bool(rp) and rp.startswith("/media/") and rp != "/media" and os.path.isdir(rp)

def scan_volume(mount):
    """Walk a mounted external volume; classify ROM-shaped files by system. Bounded + safe."""
    rp = os.path.realpath(mount or "")
    if not _is_external_mount(rp):
        return {"ok": False, "error": "refused: not an external /media mount"}
    ext_map, names = _ext_sys_map()
    known = set(names.keys())
    by_sys, ambiguous, total = {}, 0, 0
    base_depth = rp.rstrip("/").count("/")
    try:
        for root, dirs, files in os.walk(rp):
            if root.rstrip("/").count("/") - base_depth >= _SCAN_MAX_DEPTH:
                dirs[:] = []
            dirs[:] = [d for d in dirs if d.lower() not in _SCAN_SKIPDIRS and not d.startswith(".")]
            for f in files:
                if total >= _SCAN_MAX_FILES:
                    break
                if f.startswith("."):
                    continue
                ext = os.path.splitext(f)[1].lower()
                if not ext or ext in _NONROM_EXT:
                    continue
                p = os.path.join(root, f)
                try:
                    sz = os.path.getsize(p)
                except OSError:
                    continue
                sysid = _classify_rom(p, ext_map, known)
                if sysid:
                    by_sys.setdefault(sysid, []).append({"file": f, "path": p, "size": sz})
                    total += 1
                elif ext in ext_map or ext in _AMBIG_EXT:
                    ambiguous += 1
                    total += 1
            if total >= _SCAN_MAX_FILES:
                break
    except Exception as e:
        return {"ok": False, "error": str(e)}
    systems = [{"system": s, "name": _sys_fullname(s), "count": len(v),
                "games": sorted([{"name": os.path.splitext(g["file"])[0], "file": g["file"],
                                  "path": g["path"], "size": g["size"]} for g in v],
                                key=lambda g: g["name"].lower())}
               for s, v in sorted(by_sys.items())]
    return {"ok": True, "mount": rp, "systems": systems,
            "rom_count": sum(s["count"] for s in systems), "ambiguous": ambiguous}

def _load_storage_state():
    try:
        st = json.load(open(STORAGE_STATE_F))
    except Exception:
        st = {}
    st.setdefault("pending", {})
    st.setdefault("dismissed", [])
    st.setdefault("imported", [])
    return st

def storage_register(payload):
    """udev-handler insert hook: scan the freshly-mounted volume + record a pending offer."""
    payload = payload or {}
    mount = payload.get("mount", "")
    vol_id = payload.get("vol_id") or payload.get("dev") or mount
    dev = payload.get("dev", "")
    rp = os.path.realpath(mount or "")
    label = payload.get("label") or os.path.basename(rp.rstrip("/")) or vol_id
    if not _is_external_mount(rp):
        return {"ok": False, "error": "ignored: not an external /media mount", "mount": mount}
    scan = scan_volume(rp)
    if not scan.get("ok"):
        return scan
    with _STORAGE_LOCK:
        st = _load_storage_state()
        _STORAGE_ABORT.discard(vol_id)
        if scan["rom_count"] == 0:
            st["pending"].pop(vol_id, None)
            write_json_atomic(STORAGE_STATE_F, st)
            return {"ok": True, "vol_id": vol_id, "rom_count": 0, "note": "no ROMs found"}
        if vol_id in st["imported"]:
            return {"ok": True, "vol_id": vol_id, "rom_count": scan["rom_count"],
                    "note": "already imported (debounced)"}
        st["pending"][vol_id] = {"vol_id": vol_id, "label": label, "mount": rp, "dev": dev,
                                 "systems": scan["systems"], "rom_count": scan["rom_count"],
                                 "ambiguous": scan["ambiguous"], "t": int(time.time())}
        if vol_id in st["dismissed"]:
            st["dismissed"].remove(vol_id)
        write_json_atomic(STORAGE_STATE_F, st)
    LOG.info("STORAGE detect %s (%s) at %s: %d roms in %d systems",
             label, vol_id, rp, scan["rom_count"], len(scan["systems"]))
    return {"ok": True, "vol_id": vol_id, "rom_count": scan["rom_count"],
            "systems": [s["system"] for s in scan["systems"]]}

def storage_pending():
    """Freshest live, un-dismissed offer(s) -- the home-page poller's source. Read-only/cheap."""
    st = _load_storage_state()
    offers = [off for vid, off in st["pending"].items()
              if vid not in st["dismissed"] and os.path.ismount(off.get("mount", ""))]
    offers.sort(key=lambda o: -o.get("t", 0))
    return {"ok": True, "offers": offers, "offer": (offers[0] if offers else None)}

def storage_import(payload):
    """Copy ROMs from the offer's volume into /userdata/roms/<system>. Collision-safe; aborts
    cleanly if the card is pulled mid-copy."""
    payload = payload or {}
    vol_id = payload.get("vol_id")
    want_all = bool(payload.get("all"))
    want_systems = set(payload.get("systems") or [])
    st = _load_storage_state()
    off = st["pending"].get(vol_id)
    if not off:
        return {"ok": False, "error": "no pending offer for that volume"}
    mount = off["mount"]
    if not os.path.ismount(mount):
        return {"ok": False, "error": "drive was removed"}
    _STORAGE_ABORT.discard(vol_id)
    imported = skipped = 0
    errors, by_system, aborted = [], {}, False
    for s in off["systems"]:
        if not want_all and s["system"] not in want_systems:
            continue
        sysdir = os.path.join(ROMS, s["system"])
        try:
            os.makedirs(sysdir, exist_ok=True)
        except Exception as e:
            errors.append("%s: %s" % (s["system"], e))
            continue
        sysreal = os.path.realpath(sysdir)
        for g in s["games"]:
            if vol_id in _STORAGE_ABORT or not os.path.ismount(mount):
                aborted = True
                break
            src = g["path"]
            if not os.path.isfile(src):
                skipped += 1
                continue
            dest = os.path.join(sysdir, g["file"])
            if os.path.isfile(dest):
                try:
                    if os.path.getsize(dest) == g["size"]:
                        skipped += 1   # identical file already in the Library
                        continue
                except OSError:
                    pass
                stem, ext = os.path.splitext(g["file"])
                n = 2
                while os.path.isfile(os.path.join(sysdir, "%s (%d)%s" % (stem, n, ext))):
                    n += 1
                dest = os.path.join(sysdir, "%s (%d)%s" % (stem, n, ext))
            # hard confinement: the write target must live directly inside the system's roms dir
            if not os.path.realpath(os.path.dirname(dest)).startswith(sysreal):
                errors.append("%s: unsafe destination" % g["file"])
                continue
            try:
                tmp = dest + ".part"
                shutil.copyfile(src, tmp)
                os.replace(tmp, dest)
                imported += 1
                by_system[s["system"]] = by_system.get(s["system"], 0) + 1
            except Exception as e:
                errors.append("%s: %s" % (g["file"], e))
                try:
                    os.remove(dest + ".part")
                except OSError:
                    pass
        if aborted:
            break
    with _STORAGE_LOCK:
        st = _load_storage_state()
        if not aborted:
            st["pending"].pop(vol_id, None)
            if vol_id not in st["imported"]:
                st["imported"].append(vol_id)
        write_json_atomic(STORAGE_STATE_F, st)
    LOG.info("STORAGE import %s: +%d skipped=%d errors=%d aborted=%s",
             vol_id, imported, skipped, len(errors), aborted)
    return {"ok": True, "vol_id": vol_id, "imported": imported, "skipped": skipped,
            "errors": errors, "by_system": by_system, "aborted": aborted}

def storage_dismiss(payload):
    vol_id = (payload or {}).get("vol_id")
    with _STORAGE_LOCK:
        st = _load_storage_state()
        if vol_id and vol_id not in st["dismissed"]:
            st["dismissed"].append(vol_id)
        write_json_atomic(STORAGE_STATE_F, st)
    return {"ok": True, "dismissed": vol_id}

def storage_removed(payload):
    """udev-handler remove hook: drop offers for the gone device + abort any in-flight import."""
    payload = payload or {}
    dev = payload.get("dev", "")
    vol_id = payload.get("vol_id")
    with _STORAGE_LOCK:
        st = _load_storage_state()
        gone = [vid for vid, off in st["pending"].items()
                if vid == vol_id or (dev and off.get("dev") == dev)
                or not os.path.ismount(off.get("mount", ""))]
        for vid in gone:
            st["pending"].pop(vid, None)
            _STORAGE_ABORT.add(vid)                # stop a copy that's running for this volume
            if vid in st["dismissed"]:
                st["dismissed"].remove(vid)        # a re-inserted dismissed card re-prompts
            # NOTE: 'imported' is kept across removal so a re-inserted, already-imported card
            # is debounced (docs/25 §5.3) rather than nagging again.
        write_json_atomic(STORAGE_STATE_F, st)
    LOG.info("STORAGE remove dev=%s vol=%s -> cleared %s", dev, vol_id, gone)
    return {"ok": True, "removed": gone}

# ===== Desktop-widget data layer + controller registry + host proxies (docs/16 wave) =====
# The widgets/controllers consume these; nothing here mutates game state, it derives views over the
# already-tracked playtime.json / recent.json + the live /proc input devices + host_bridge.

# ---- playtime/recent rollups (per-game -> per-system) ----
def _system_playtime():
    """Aggregate per-game playtime (playtime.json keys 'system/game' -> secs) up to the SYSTEM."""
    agg = {}
    for key, secs in _playtime().items():
        s = key.split("/", 1)[0]
        agg[s] = agg.get(s, 0) + (secs or 0)
    return agg

def _recent_rows():
    try:
        rec = json.load(open(RECENT_F))
        return rec if isinstance(rec, list) else []
    except Exception:
        return []

def _system_recency():
    """From recent.json (newest-first): {system: last_played_ts} + the systems in recency order."""
    last, order = {}, []
    for r in _recent_rows():
        s = r.get("system")
        if s and s not in last:
            last[s] = r.get("t", 0); order.append(s)
    return last, order

def _system_repr_game(system):
    """A launchable game to represent a system tile: most-recent, else most-played, else first ROM."""
    for r in _recent_rows():
        if r.get("system") == system and r.get("game"):
            return r["game"]
    best, bestsecs = None, -1
    for key, secs in _playtime().items():
        s, _, g = key.partition("/")
        if s == system and (secs or 0) > bestsecs:
            best, bestsecs = g, secs or 0
    if best:
        return best
    d = os.path.join(ROMS, system)
    try:
        for f in sorted(os.listdir(d)):
            if f.startswith(".") or f in _SKIPDIRS or "gamelist" in f or os.path.isdir(os.path.join(d, f)):
                continue
            if os.path.splitext(f)[1].lower() in _SKIPEXT:
                continue
            return os.path.splitext(f)[0]
    except Exception:
        pass
    return None

def _sysname(system):
    return _SYS_EMU.get(system, _SYS.get(system, system))

def _emulator_item(system, playtime_s, last_played):
    game = _system_repr_game(system)
    # launch_hint = the exact body to POST to /launch (system+game) so the tile is startable
    return {"system": system, "name": _sysname(system), "core": _effective_default(system),
            "playtime_s": int(playtime_s or 0), "last_played": last_played,
            "launch_hint": ({"system": system, "game": game} if game else None)}

def widgets_emulators():
    agg = _system_playtime()
    last, order = _system_recency()
    top_systems = sorted(agg.keys(), key=lambda s: -agg[s])[:5]
    return {"ok": True,
            "top": [_emulator_item(s, agg.get(s, 0), last.get(s)) for s in top_systems],
            "recent": [_emulator_item(s, agg.get(s, 0), last.get(s)) for s in order[:3]]}

def _library_item(system, game, playtime_s, last_played):
    # the system+game fields ARE the /launch body, so the tile is directly startable
    return {"system": system, "game": game, "name": game, "sysname": _sysname(system),
            "img": _game_img(system, game), "playtime_s": int(playtime_s or 0),
            "last_played": last_played}

def widgets_library(limit=6):
    pt = _playtime(); rec = _recent_rows()
    recent = []
    for r in rec[:limit]:
        s, g = r.get("system"), r.get("game")
        if s and g:
            recent.append(_library_item(s, g, pt.get(s + "/" + g, 0), r.get("t")))
    last_map = {(r.get("system"), r.get("game")): r.get("t") for r in rec}
    top = []
    for key, secs in sorted(pt.items(), key=lambda kv: -(kv[1] or 0))[:limit]:
        s, _, g = key.partition("/")
        if s and g:
            top.append(_library_item(s, g, secs, last_map.get((s, g))))
    return {"ok": True, "recent": recent, "top": top}

def widgets_store():
    # a small sample for each store section to populate the widget
    apps = store_catalog().get("apps", [])[:4]
    emus = [{"system": e["system"], "name": e["name"], "default": e.get("default"),
             "has_games": e.get("has_games")} for e in emulators_list().get("systems", [])[:4]]
    games = games_catalog().get("games", [])[:4]
    return {"ok": True, "apps": apps, "emulators": emus, "games": games}

STEAM_APPID = "com.valvesoftware.Steam"

def _steam_loginusers():
    import glob as _glob
    seen, cands = set(), []
    for home in ("/userdata/home", "/userdata/system", os.path.expanduser("~"), "/root"):
        if not home or home in seen:
            continue
        seen.add(home)
        cands += _glob.glob(home + "/.var/app/" + STEAM_APPID + "/**/config/loginusers.vdf",
                            recursive=True)
    for p in cands:
        try:
            return p, open(p, encoding="utf-8", errors="replace").read()
        except Exception:
            continue
    return None, None

# Steam-SPECIFIC cmdline tokens. NOT a bare "steam" substring — that would match the HTTP client
# requesting /widgets/steam (the URL contains "steam") or a `pgrep -f steam` self-match. These tokens
# (flatpak app id + steam's own helper) only appear in a real Steam process tree.
_STEAM_PROC_TOKENS = ("valvesoftware.steam", "steamwebhelper", "/steam/steam.sh", "steam_app_")
_STEAM_ARGV0 = ("steam", "steam.sh", "steamwebhelper")

def _steam_running():
    """Real-Steam detection via a /proc scan for Steam-specific markers (flatpak app id / helper /
    argv0). Avoids the false positives that a bare 'steam' substring or `pgrep -f` produce."""
    me = os.getpid()
    for pid in os.listdir("/proc"):
        if not pid.isdigit() or int(pid) == me:
            continue
        try:
            raw = open("/proc/%s/cmdline" % pid, "rb").read()
        except Exception:
            continue
        if not raw:
            continue
        parts = raw.split(b"\x00")
        cl = raw.replace(b"\x00", b" ").decode("utf-8", "replace").lower()
        if any(tok in cl for tok in _STEAM_PROC_TOKENS):
            return True
        argv0 = os.path.basename(parts[0].decode("utf-8", "replace")).lower()
        if argv0 in _STEAM_ARGV0:
            return True
    return False

def widgets_steam():
    installed = STEAM_APPID in store_installed()
    try:
        running = _steam_running()
    except Exception:
        running = False
    logged_in, user = False, None
    _path, txt = _steam_loginusers()
    if txt:
        try:
            # loginusers.vdf: per-steamid blocks; the active account has MostRecent "1"
            for m in re.finditer(r'"(\d{8,})"\s*\{(.*?)\}', txt, re.S):
                if re.search(r'"MostRecent"\s*"1"', m.group(2)):
                    logged_in = True
                    pm = re.search(r'"PersonaName"\s*"([^"]*)"', m.group(2))
                    user = pm.group(1) if pm else None
                    break
            if not logged_in and re.search(r'"\d{8,}"', txt):
                logged_in = True   # an account exists but none flagged MostRecent — best-effort
                pm = re.search(r'"PersonaName"\s*"([^"]*)"', txt)
                user = pm.group(1) if pm else None
        except Exception:
            pass
    out = {"ok": True, "installed": installed, "running": running, "logged_in": logged_in,
           "recent_games": []}   # recent_games: not derivable without localconfig parse — best-effort
    if user:
        out["user"] = user
    return out

# ---- Controller registry: who's connected + which one drives the OS menus ----
OS_ADMIN_F = "/userdata/system/gose/os_admin_controller.json"

def _parse_controllers():
    """Every connected gamepad from /proc/bus/input/devices (anything exposing a js* handler)."""
    try:
        txt = open("/proc/bus/input/devices").read()
    except Exception:
        return []
    pads = []
    for blk in txt.split("\n\n"):
        js = re.search(r"\bjs(\d+)\b", blk)
        if not js:
            continue                              # not a joystick/gamepad
        if not _blk_has_pad_buttons(blk):
            continue                              # buttonless sibling node (motion/touchpad):
                                                  # not a controller a user/admin can hold
        ev = re.search(r"\bevent(\d+)\b", blk)
        name_m = re.search(r'Name="([^"]*)"', blk)
        phys_m = re.search(r"Phys=(\S*)", blk)
        sys_m = re.search(r"Sysfs=(\S+)", blk)
        ids = re.search(r"Bus=(\w+) Vendor=(\w+) Product=(\w+) Version=(\w+)", blk)
        phys = phys_m.group(1) if phys_m else ""
        sysfs = sys_m.group(1) if sys_m else ""
        bus = int(ids.group(1), 16) if ids else 0
        guid = _sdl_guid(*(int(x, 16) for x in ids.groups())) if ids else _XBOX_GUID
        # Passthrough pads (host_bridge-side pad_passthrough.py mirroring a PHYSICAL
        # pad onto guest uinput, phys="gose-passthrough") are ALSO uinput but are NOT
        # "virtual": they're the human's controller — first-class player, admin-eligible.
        if "gose-passthrough" in phys:
            source = "passthrough"
        elif "py-evdev-uinput" in phys:
            source = "virtual"
        else:
            source = "bluetooth" if bus == 0x05 else "native"
        pads.append({"id": os.path.basename(sysfs) or ("js" + js.group(1)),
                     "name": name_m.group(1) if name_m else "Controller", "guid": guid,
                     "source": source,
                     "path": ("/dev/input/event" + ev.group(1)) if ev else None,
                     "js": int(js.group(1)), "is_dev": False})
    pads.sort(key=lambda p: p["js"])
    seen_virt = False
    for p in pads:
        # the dev virtual pad = the first/original (seat 1) uinput pad created at agent startup
        if p["source"] == "virtual" and not seen_virt:
            p["is_dev"] = True; seen_virt = True
    return pads

def _os_admin_load():
    try:
        return (json.load(open(OS_ADMIN_F)) or {}).get("id")
    except Exception:
        return None

def _default_admin_id(pads):
    # default = first non-virtual controller, else the dev virtual pad, else the first pad
    for p in pads:
        if p["source"] != "virtual":
            return p["id"]
    for p in pads:
        if p["is_dev"]:
            return p["id"]
    return pads[0]["id"] if pads else None

def _effective_admin(pads):
    stored = _os_admin_load()
    ids = {p["id"] for p in pads}
    return (stored if stored in ids else _default_admin_id(pads)), stored

def controllers_list():
    pads = _parse_controllers()
    admin, stored = _effective_admin(pads)
    for p in pads:
        p["is_os_admin"] = (p["id"] == admin)
    return {"ok": True, "controllers": pads, "admin": admin,
            "admin_explicit": (stored if any(p["id"] == stored for p in pads) else None)}

def controllers_admin_get():
    pads = _parse_controllers()
    admin, stored = _effective_admin(pads)
    return {"ok": True, "id": admin, "explicit": stored, "default": _default_admin_id(pads)}

def controllers_admin_set(payload):
    cid = (payload or {}).get("id")
    if not cid:
        return {"ok": False, "error": "id required"}
    if cid not in {p["id"] for p in _parse_controllers()}:
        return {"ok": False, "error": "no connected controller with id '%s'" % cid}
    try:
        os.makedirs(os.path.dirname(OS_ADMIN_F), exist_ok=True)
        write_json_atomic(OS_ADMIN_F, {"id": cid, "t": int(time.time())})
    except Exception as e:
        return {"ok": False, "error": str(e)}
    LOG.info("OS admin controller set: %s", cid)
    return {"ok": True, "id": cid}

# ---- Host-bridge proxies: real laptop perf + brightness (tolerate the bridge being down) ----
def sys_perf_host():
    r = host_bridge("/perf", timeout=4)
    return r if (isinstance(r, dict) and r.get("ok")) else {"ok": False}

def sys_brightness_host(level=None):
    if level is None:
        r = host_bridge("/brightness", timeout=4)
        if isinstance(r, dict) and r.get("ok"):
            return r
        loc = sys_brightness()                       # bare-metal fallback (VM has no backlight)
        return loc if loc.get("ok") else {"ok": False}
    try:
        level = max(0, min(100, int(level)))
    except (TypeError, ValueError):
        return {"ok": False, "error": "level must be 0-100"}
    r = host_bridge("/brightness", {"level": level}, timeout=4)
    if isinstance(r, dict) and r.get("ok"):
        return r
    loc = sys_brightness(level)
    return loc if loc.get("ok") else {"ok": False}

# ===================== WINDOWING SPINE — docs/23 §4 / §9 Phase 0 =====================
# ONE merged WINDOW REGISTRY over both window kinds (docs/23 §4.1):
#   * web windows    — iframes in WinBox frames inside the kiosk WebView. The shell-side
#     WM (assets/gose-wm.js) is the source of truth: it POSTs its full window list to
#     /windows/sync (on every change + a heartbeat) and this server caches it. If the
#     heartbeat stops (kiosk navigated away / crashed) the cached web windows go stale
#     and are dropped from GET /windows.
#   * native windows — real X windows (Steam / emulators), discovered live from the X
#     server. DEVIATION from docs/23 (§4.1 says `wmctrl -l` / _NET_CLIENT_LIST): wmctrl
#     is NOT on this Batocera image — only xdotool — so discovery is `xdotool search
#     --onlyvisible` + getwindowname/getwindowpid/getwindowgeometry. Same EWMH data,
#     different tool. (Consequence: iconified native windows drop out of the visible
#     list — tracking them across minimize is a Phase 2 refinement.)
# Verb dispatch = POST /wm/<verb> (docs/23 §4.2):
#   * native target → xdotool / signals, executed immediately;
#   * web target    → queued; the shell WM drains the queue (piggybacked on the
#     /windows/sync response, or GET /wm/poll) and performs the op in-page. This is the
#     server→WebView transport: the kiosk polls, because nothing can push into it.
#
# The WM SEMANTIC-EVENT VOCABULARY (docs/23 §7/§9 Phase 0). The pad bridge's WM modal
# layer (chunk B) will POST these to /wm/event as {"event": "wm.next"}; they map onto
# the uniform verbs below. Defined now so the vocabulary is fixed even though chunk A
# only exercises a subset (focus/min/close/open + the registry).
WM_EVENTS = {
    "wm.next":     ("next", {}),        # cycle focus forward (carousel step)
    "wm.prev":     ("prev", {}),        # cycle focus backward
    "wm.focus":    ("focus", {}),       # focus a specific window (needs id)
    "wm.snap":     ("snap", {}),        # snap to a zone (needs id + zone)
    "wm.min":      ("minimize", {}),    # minimize ("act out" tier 0 — still live)
    "wm.suspend":  ("suspend", {}),     # pause: web=queued to shell, native=SIGSTOP (RAM kept)
    "wm.free":     ("free", {}),        # release: web=teardown to descriptor, native=SIGTERM/KILL
    "wm.overview": ("overview", {}),    # all-windows grid
    # ---- chunk B: the pad bridge's WM modal layer (docs/23 §7) posts these ----
    "wm.carousel": ("carousel", {}),    # hold-Guide → open the window carousel
    "wm.select":   ("select", {}),      # A / Guide-release → take the highlighted choice
    "wm.cancel":   ("cancel", {}),      # B → close the open WM modal
    "wm.left":     ("left", {}),        # d-pad while a WM modal is open
    "wm.right":    ("right", {}),
    "wm.up":       ("up", {}),
    "wm.down":     ("down", {}),
    "wm.snapmode": ("snapmode", {}),    # L2+d-pad → Snap Layout chooser (§4.3)
    "wm.act":      ("act", {}),         # X → act-out the highlighted/focused window (tier per shell)
}
# Uniform verbs (docs/23 §4.2) + registry/launch ops (open/winify are how web windows
# come into being; they're shell-queue-only, not in the §4.2 table).
WM_VERBS_WEB_ONLY = {"open", "winify", "next", "prev", "overview", "resummon",
                     "carousel", "select", "cancel", "left", "right", "up", "down",
                     "snapmode", "act"}
WM_VERBS = {"focus", "move", "resize", "maximize", "restore", "minimize", "snap",
            "close", "suspend", "free"} | WM_VERBS_WEB_ONLY

_WM_LOCK = threading.Lock()
_WM_CV = threading.Condition(_WM_LOCK)  # long-poll wakeup: command queued -> waiter returns NOW
_WEB_WINS = {"ts": 0.0, "list": [], "inst": None, "ui": None}  # shell cache + page instance + modal/UI state
_WM_QUEUE = collections.deque(maxlen=64)  # commands pending for the shell web-WM
WEB_STALE_S = 15                        # no /windows/sync in this long => cache is stale

# The kiosk + Guide overlay are SHELL SURFACES, not windows — excluded from the registry
# (the design's "switch back to the shell" targets them by name, not through the list).
_SHELL_WIN_TITLES = {"GOSE", "GOSE Overlay"}

def _xdo(args, timeout=6):
    """Run xdotool against the GOSE display; returns (rc, stdout, stderr)."""
    r = subprocess.run(["/bin/sh", "-c", "DISPLAY=:0 xdotool " + args],
                       capture_output=True, text=True, timeout=timeout)
    return r.returncode, (r.stdout or "").strip(), (r.stderr or "").strip()

def _screen_size():
    try:
        rc, out, _ = _xdo("getdisplaygeometry")
        w, h = out.split()
        return int(w), int(h)
    except Exception:
        return 1920, 1080

def _snap_rect(zone, margin_top=0):
    """Computed zone rects for a 16:9 screen (docs/23 §4.3): halves, quarters,
    thirds columns, and the ChromeOS-style Partial (main ⅔ + side ⅓)."""
    W, H = _screen_size()
    y0, hh = margin_top, H - margin_top
    Z = {
        "left":   (0, y0, W // 2, hh),         "right": (W // 2, y0, W - W // 2, hh),
        "top":    (0, y0, W, hh // 2),         "bottom": (0, y0 + hh // 2, W, hh - hh // 2),
        "tl":     (0, y0, W // 2, hh // 2),    "tr": (W // 2, y0, W - W // 2, hh // 2),
        "bl":     (0, y0 + hh // 2, W // 2, hh - hh // 2),
        "br":     (W // 2, y0 + hh // 2, W - W // 2, hh - hh // 2),
        "col-l":  (0, y0, W // 3, hh),         "col-c": (W // 3, y0, W // 3, hh),
        "col-r":  (2 * W // 3, y0, W - 2 * W // 3, hh),
        "main":   (0, y0, 2 * W // 3, hh),     "side": (2 * W // 3, y0, W - 2 * W // 3, hh),
        "full":   (0, y0, W, hh),
    }
    return Z.get(zone)

def native_windows(include_shell=False):
    """Live native X windows via xdotool (wmctrl is not on the image — see header).
    Batched: xdotool command-chaining (`search ... getwindowname %@`) gets every
    window's name/geometry in ONE spawn each instead of 3-4 spawns per window —
    GET /windows was costing seconds under load the per-window way."""
    wins = []
    try:
        rc, out, _ = _xdo("search --onlyvisible --name '.'")
        xids = [x for x in out.split() if x.isdigit()]
    except Exception:
        return wins
    if not xids:
        return wins
    active = None
    try:
        rc, a, _ = _xdo("getactivewindow")
        active = a if a.isdigit() else None
    except Exception:
        pass
    # names: one line per window, same order as the search output
    names = {}
    try:
        _, o, _ = _xdo("search --onlyvisible --name '.' getwindowname %@")
        lines = o.splitlines()
        if len(lines) == len(xids):
            names = dict(zip(xids, lines))
    except Exception:
        pass
    # geometry: --shell blocks are self-keyed by WINDOW=<xid> (alignment-safe)
    geoms = {}
    try:
        _, o, _ = _xdo("search --onlyvisible --name '.' getwindowgeometry --shell %@")
        cur = None
        for ln in o.splitlines():
            if "=" not in ln:
                continue
            k, v = ln.split("=", 1)
            if k == "WINDOW":
                cur = v.strip(); geoms[cur] = {}
            elif cur and k in ("X", "Y", "WIDTH", "HEIGHT") and v.lstrip("-").isdigit():
                geoms[cur][{"X": "x", "Y": "y", "WIDTH": "w", "HEIGHT": "h"}[k]] = int(v)
    except Exception:
        pass
    for xid in xids:
        try:
            title = names.get(xid)
            if title is None:
                _, title, _ = _xdo("getwindowname %s" % xid, timeout=4)   # fallback path
            shell = title in _SHELL_WIN_TITLES
            if shell and not include_shell:
                continue
            pid_s = ""
            if not shell:    # pid lookup only for real windows (the expensive leftovers)
                _, pid_s, _ = _xdo("getwindowpid %s" % xid, timeout=4)
            wins.append({"id": "xwin-%s" % xid, "kind": "native", "xid": int(xid),
                         "pid": int(pid_s) if pid_s.isdigit() else None,
                         "title": title or "(untitled)", "icon": "gamepad-2",
                         "geom": geoms.get(xid, {}), "state": "normal", "group": None,
                         "shell": shell, "focused": (xid == active)})
        except Exception:
            continue
    return wins

def web_windows():
    """The kiosk shell's web-window list (cache), dropped when the heartbeat goes stale."""
    with _WM_LOCK:
        fresh = (time.time() - _WEB_WINS["ts"]) < WEB_STALE_S
        return (list(_WEB_WINS["list"]) if fresh else []), fresh

def windows_merged():
    web, fresh = web_windows()
    native = native_windows()
    focus = next((w["id"] for w in web + native if w.get("focused")), None)
    return {"ok": True, "windows": web + native, "web_fresh": fresh,
            "shell_inst": _WEB_WINS.get("inst"), "ui": _WEB_WINS.get("ui"),
            "nav": _WEB_WINS.get("nav"), "focus": focus, "ts": round(time.time(), 2)}

def windows_sync(payload):
    """Shell WM → server: replace the web-window cache; drain pending commands back."""
    lst = payload.get("windows")
    cmds = []
    with _WM_LOCK:
        if isinstance(lst, list):
            _WEB_WINS["list"] = [w for w in lst if isinstance(w, dict) and w.get("id")]
            _WEB_WINS["ts"] = time.time()
            # modal/UI state mirror ({modal, sel, zone, ...} or null) — lets a text-first
            # verifier (or the bridge) see what the carousel/snap chooser is showing
            # without a screenshot.
            _WEB_WINS["ui"] = payload.get("ui") if isinstance(payload.get("ui"), dict) else None
            # live widget nav-zone order + current focus (docs/25 §5b/§5c) — verification surface
            _WEB_WINS["nav"] = payload.get("nav") if isinstance(payload.get("nav"), (list, dict)) else None
            inst = payload.get("inst")
            if inst and inst != _WEB_WINS.get("inst"):
                LOG.info("WM shell instance: %s (was %s)", inst, _WEB_WINS.get("inst"))
                _WEB_WINS["inst"] = inst
        while _WM_QUEUE:
            cmds.append(_WM_QUEUE.popleft())
    return {"ok": True, "commands": cmds}

def wm_poll(wait_s=0.0):
    """Drain queued shell commands. With wait_s > 0 this is a LONG-POLL: the request
    parks until a command arrives (or the wait expires), so the shell holds one
    hanging GET open and a pad-bridge /wm/event reaches the page in milliseconds
    instead of riding the 4s sync heartbeat (which stretches badly under load —
    the chunk-A perf finding). Server is threaded, so parking a request is safe."""
    deadline = time.time() + max(0.0, min(float(wait_s or 0), 25.0))
    cmds = []
    with _WM_CV:
        while not _WM_QUEUE and time.time() < deadline:
            _WM_CV.wait(timeout=max(0.05, deadline - time.time()))
        while _WM_QUEUE:
            cmds.append(_WM_QUEUE.popleft())
    return {"ok": True, "commands": cmds}

def _wm_queue(cmd):
    with _WM_CV:
        _WM_QUEUE.append(cmd)
        _WM_CV.notify_all()
    return {"ok": True, "queued": True, "cmd": cmd}

def _wm_native(verb, win, payload):
    """Execute a §4.2 verb on a native X window (xdotool / signals; wmctrl absent)."""
    xid, pid = win["xid"], win.get("pid")
    try:
        if verb == "focus":
            _xdo("windowactivate %d" % xid)
        elif verb == "move":
            _xdo("windowmove %d %d %d" % (xid, int(payload.get("x", 0)), int(payload.get("y", 0))))
        elif verb == "resize":
            _xdo("windowsize %d %d %d" % (xid, int(payload.get("w", 800)), int(payload.get("h", 600))))
        elif verb == "maximize":
            _xdo("windowstate --add MAXIMIZED_VERT %d" % xid)
            _xdo("windowstate --add MAXIMIZED_HORZ %d" % xid)
        elif verb == "restore":
            _xdo("windowstate --remove MAXIMIZED_VERT %d" % xid)
            _xdo("windowstate --remove MAXIMIZED_HORZ %d" % xid)
        elif verb == "minimize":
            _xdo("windowminimize %d" % xid)
        elif verb == "snap":
            rect = _snap_rect(payload.get("zone", ""))
            if not rect:
                return {"ok": False, "error": "unknown zone '%s'" % payload.get("zone")}
            _xdo("windowstate --remove MAXIMIZED_VERT %d" % xid)
            _xdo("windowstate --remove MAXIMIZED_HORZ %d" % xid)
            _xdo("windowmove %d %d %d" % (xid, rect[0], rect[1]))
            _xdo("windowsize %d %d %d" % (xid, rect[2], rect[3]))
        elif verb == "close":
            if not pid:
                return {"ok": False, "error": "no pid for window"}
            os.kill(pid, 15)                       # SIGTERM — the taskman path
        elif verb == "suspend":
            if not pid:
                return {"ok": False, "error": "no pid for window"}
            os.kill(pid, 19)                       # SIGSTOP — Switch-style quick-resume tier
        elif verb == "free":
            if not pid:
                return {"ok": False, "error": "no pid for window"}
            os.kill(pid, int(payload.get("sig", 15)))   # TERM default; sig:9 for a hard free
        else:
            return {"ok": False, "error": "verb '%s' not supported on native windows" % verb}
        return {"ok": True, "kind": "native", "verb": verb, "id": win["id"]}
    except Exception as e:
        return {"ok": False, "error": str(e)}

def wm_dispatch(verb, payload):
    """POST /wm/<verb> — route a uniform window op to the right world (docs/23 §4.2)."""
    if verb == "event":                                  # semantic event from the pad bridge
        ev = WM_EVENTS.get(payload.get("event", ""))
        if not ev:
            return {"ok": False, "error": "unknown event", "events": sorted(WM_EVENTS)}
        verb = ev[0]
    if verb not in WM_VERBS:
        return {"ok": False, "error": "unknown verb '%s'" % verb, "verbs": sorted(WM_VERBS)}
    # web-only / launch / no-target verbs always go to the shell WM queue
    if verb in WM_VERBS_WEB_ONLY:
        cmd = {"verb": verb}
        for k in ("id", "url", "title", "icon", "zone"):
            if payload.get(k) is not None:
                cmd[k] = payload[k]
        return _wm_queue(cmd)
    wid = payload.get("id")
    if not wid:
        return {"ok": False, "error": "id required for '%s'" % verb}
    # resolve target: web cache first, then live native list
    web, _ = web_windows()
    if any(w.get("id") == wid for w in web):
        cmd = {"verb": verb, "id": wid}
        for k in ("x", "y", "w", "h", "zone", "sig"):
            if payload.get(k) is not None:
                cmd[k] = payload[k]
        return _wm_queue(cmd)
    for w in native_windows(include_shell=True):
        if w["id"] == wid or str(w["xid"]) == str(wid):
            if w.get("shell"):
                return {"ok": False, "error": "'%s' is a shell surface, not a window" % w["title"]}
            return _wm_native(verb, w, payload)
    return {"ok": False, "error": "no window '%s'" % wid}
# =================== end windowing spine ===================

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
            for k in ("online", "gpu_pct", "gpu_mem_used_mb", "gpu_mem_total_mb",
                      "gpu_temp_c", "gpu_name"):
                if k in h:
                    st[k] = h[k]
            b = battery_info()   # local BAT* > host laptop > override; honest source
            for k in ("has_battery", "battery_pct", "charging", "secs_left", "battery_source"):
                st[k] = b.get(k)
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
        if route == "/oobe/status":
            return self._json(oobe_status())
        if route == "/storage.json":
            return self._json(storage_info())
        if route == "/storage/pending":
            return self._json(storage_pending())
        if route == "/procs.json":
            return self._json(procs_info())
        if route == "/windows":
            return self._json(windows_merged())
        if route == "/wm/poll":
            try:
                wait_s = float(self._qs().get("wait", 0))
            except Exception:
                wait_s = 0.0
            return self._json(wm_poll(wait_s))
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
            return self._json(sys_brightness_host())
        if route == "/sys/perf":
            return self._json(sys_perf_host())
        if route == "/sys/battery":
            return self._json(battery_info())
        if route == "/widgets/emulators":
            return self._json(widgets_emulators())
        if route == "/widgets/library":
            return self._json(widgets_library())
        if route == "/widgets/store":
            return self._json(widgets_store())
        if route == "/widgets/steam":
            return self._json(widgets_steam())
        if route == "/controllers":
            return self._json(controllers_list())
        if route == "/controllers/admin":
            return self._json(controllers_admin_get())
        if route == "/net/scan":
            return self._json(host_bridge("/wifi/scan"))
        if route == "/net/wifi":
            return self._json(host_bridge("/wifi/status"))
        if route == "/capture/buffer":
            return self._json(host_bridge("/clip/status"))
        if route == "/bt/status":
            return self._json(bt_status())
        if route == "/peripherals":
            return self._json(peripherals())
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
        if route in ("/peripherals/usb/claim", "/peripherals/usb/release"):
            try:
                n = int(self.headers.get("Content-Length", 0))
                payload = json.loads(self.rfile.read(n).decode() or "{}")
            except Exception:
                payload = {}
            sub = "/usb/claim" if route.endswith("claim") else "/usb/release"
            return self._json(host_bridge(sub, payload, timeout=20))
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
        if route == "/windows/sync" or route.startswith("/wm/"):
            try:
                n = int(self.headers.get("Content-Length", 0))
                payload = json.loads(self.rfile.read(n).decode() or "{}") if n else {}
            except Exception:
                payload = {}
            if route == "/windows/sync":
                return self._json(windows_sync(payload))
            return self._json(wm_dispatch(route[4:], payload))
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
        if route == "/controllers/admin":
            try:
                n = int(self.headers.get("Content-Length", 0))
                payload = json.loads(self.rfile.read(n).decode() or "{}") if n else {}
            except Exception:
                payload = {}
            return self._json(controllers_admin_set(payload))
        if route in ("/storage/detected", "/storage/import", "/storage/dismiss", "/storage/removed"):
            try:
                n = int(self.headers.get("Content-Length", 0))
                payload = json.loads(self.rfile.read(n).decode() or "{}") if n else {}
            except Exception:
                payload = {}
            if route == "/storage/detected":
                return self._json(storage_register(payload))
            if route == "/storage/import":
                return self._json(storage_import(payload))
            if route == "/storage/dismiss":
                return self._json(storage_dismiss(payload))
            return self._json(storage_removed(payload))
        if route in ("/oobe/complete", "/oobe/reset"):
            try:
                n = int(self.headers.get("Content-Length", 0))
                payload = json.loads(self.rfile.read(n).decode() or "{}") if n else {}
            except Exception:
                payload = {}
            if route == "/oobe/complete":
                return self._json(oobe_complete(payload))
            return self._json(oobe_reset(payload))
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
                    return self._json(sys_brightness_host(
                        payload.get("level", payload.get("value"))))
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
