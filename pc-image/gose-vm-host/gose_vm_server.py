#!/usr/bin/env python3
# In-VM server: serves the GOSE UI + /status.json with REAL telemetry from the
# local agent (127.0.0.1:8731 = loopback in-guest = no token needed).
import http.server, socketserver, json, socket, functools, os, urllib.request, mimetypes, shutil, subprocess, threading, collections, time, secrets, ipaddress
import logging, logging.handlers, traceback, re, hashlib, struct, zlib
ROOT = "/userdata/gose-ui"
FS_ROOT = "/userdata"   # Files app is rooted here (the data partition)
ROMS = "/userdata/roms"
# the agent now requires a token even on loopback (set via GOSE_AGENT_TOKEN).
# SECURITY: never hardcode the token. Resolve it in order: (1) GOSE_AGENT_TOKEN env,
# (2) a gitignored .env (dev convenience), (3) the per-install token file (canonical;
# the agent reads the SAME file, mode 600), (4) first-boot generate a unique token +
# persist it. So every device gets its OWN secret and nothing sensitive lives in the repo.
TOKEN_FILE = os.environ.get("GOSE_TOKEN_FILE", "/userdata/system/gose/token")

def _env_file_token(path):
    try:
        with open(path) as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                if k.strip() == "GOSE_AGENT_TOKEN":
                    return v.strip().strip('"').strip("'") or None
    except OSError:
        pass
    return None

def _load_agent_token():
    t = (os.environ.get("GOSE_AGENT_TOKEN") or "").strip()
    if t:
        return t
    # dev: a gitignored .env next to this file or under the gose dir
    for p in (os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"),
              "/userdata/gose-ui/.env", "/userdata/system/gose/.env"):
        v = _env_file_token(p)
        if v:
            return v
    # canonical per-install secret file (shared with the agent)
    try:
        with open(TOKEN_FILE) as f:
            t = f.read().strip()
            if t:
                return t
    except OSError:
        pass
    # last resort (first boot before custom.sh ran / standalone): generate + persist
    t = secrets.token_hex(16)
    try:
        os.makedirs(os.path.dirname(TOKEN_FILE), exist_ok=True)
        fd = os.open(TOKEN_FILE, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as f:
            f.write(t)
    except FileExistsError:  # a concurrent writer won the race — use theirs
        try:
            with open(TOKEN_FILE) as f:
                t = f.read().strip() or t
        except OSError:
            pass
    except OSError:
        pass
    return t

TOKEN = _load_agent_token()

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
           "/net/scan": (10, 60), "/net/connections": (30, 60), "/launch": (30, 60), "/store/install": (20, 60),
           "/netplay/host": (10, 60), "/netplay/join": (10, 60),
           "/splice/cut": (10, 120), "/fs/op": (60, 60), "/scrape": (6, 120), "/game/scrape": (20, 60),
           "/store/uninstall": (15, 60), "/ai/request": (6, 60),
           "/emulators/install": (10, 60), "/emulators/uninstall": (15, 60),
           "/games/install": (12, 60),
           "/game/screenshot": (30, 60), "/game/record/toggle": (12, 60),
           "/system/backup": (6, 120), "/system/restore": (4, 120), "/system/factory_reset": (3, 300),
           "/sys/perf": (60, 60), "/widgets/store": (30, 60), "/widgets/steam": (30, 60),
           "/storage/import": (12, 60), "/storage/detected": (30, 60),
           "/rom/check": (60, 60),
           "/storage/breakdown": (30, 60), "/storage/group": (60, 60), "/storage/delete": (30, 60),
           "/store/sources/add": (6, 60), "/store/sources/preview": (10, 60),
           "/store/sources/refresh": (10, 60),
           "/diag/bundle": (4, 120),
           # coarse backstop only — the real brute-force guard is the 5-try/30s PIN lockout
           "/auth/pin": (30, 60), "/auth/pin/set": (10, 60)}

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
        "c64": "Commodore 64", "pcengine": "PC Engine", "nds": "Nintendo DS", "mame": "Arcade",
        "tyrian": "Tyrian"}

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
        pt = _playstats()   # per-game playtime for library cards
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
                entry = pt.get(sysname + "/" + stem)
                total_secs = (entry.get("total_secs", 0) if isinstance(entry, dict)
                              else int(entry) if isinstance(entry, (int, float)) else 0)
                last_played = entry.get("last_played") if isinstance(entry, dict) else None
                games.append({"name": stem, "img": _game_img(sysname, stem),
                              "fav": (sysname, stem) in favset,
                              "playtime_s": total_secs, "last_played": last_played})
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
# Richer per-game stats store (total_secs, last_played epoch, sessions count).
# Paired with PLAYTIME_F (kept for backward compat with all existing consumers).
PLAYSTATS_F = "/userdata/system/gose/playstats.json"
_STATS_LOCK = threading.Lock()

# In-flight session: set on launch_game, finalized on game_exit / watcher / next launch.
_SESSION = {"system": None, "game": None, "t": None}
_SESSION_LOCK = threading.Lock()

def _playstats():
    """Load playstats.json; migrate from legacy playtime.json if playstats is empty/absent."""
    try:
        d = json.load(open(PLAYSTATS_F))
        if isinstance(d, dict) and d:
            return d
    except Exception:
        pass
    # migration: seed from the flat {key: secs} store so existing data isn't lost
    legacy = {}
    try:
        legacy = json.load(open(PLAYTIME_F))
    except Exception:
        pass
    if not isinstance(legacy, dict):
        legacy = {}
    migrated = {}
    for k, secs in legacy.items():
        if isinstance(secs, (int, float)) and secs > 0:
            migrated[k] = {"total_secs": int(secs), "last_played": None, "sessions": 1}
    return migrated

def _record_session(system, game, secs):
    """Accumulate one session into PLAYSTATS_F (atomic) and keep PLAYTIME_F in sync."""
    if not system or not game or secs <= 0:
        return
    secs = min(int(secs), 86400 * 365)   # cap at 1 year (sane ceiling)
    key = system + "/" + game
    try:
        os.makedirs("/userdata/system/gose", exist_ok=True)
    except Exception:
        pass
    with _STATS_LOCK:
        try:
            pt = _playstats()
            entry = pt.get(key) or {"total_secs": 0, "last_played": None, "sessions": 0}
            if not isinstance(entry, dict):
                entry = {"total_secs": int(entry) if isinstance(entry, (int, float)) else 0,
                         "last_played": None, "sessions": 0}
            entry["total_secs"] = entry.get("total_secs", 0) + secs
            entry["last_played"] = int(time.time())
            entry["sessions"] = entry.get("sessions", 0) + 1
            pt[key] = entry
            write_json_atomic(PLAYSTATS_F, pt)
            # keep legacy playtime.json in sync (total_secs only — existing consumers unchanged)
            try:
                flat = json.load(open(PLAYTIME_F))
                if not isinstance(flat, dict):
                    flat = {}
            except Exception:
                flat = {}
            flat[key] = entry["total_secs"]
            write_json_atomic(PLAYTIME_F, flat)
        except Exception as e:
            LOG.warning("playstats write failed: %s", e)

def _session_start(system, game):
    """Mark the start of a new session; finalize any running one first."""
    with _SESSION_LOCK:
        _finalize_session_locked()
        _SESSION["system"] = system
        _SESSION["game"] = game
        _SESSION["t"] = time.time()

def _finalize_session_locked():
    """Finalize the in-flight session (caller must hold _SESSION_LOCK)."""
    if _SESSION["t"] is None:
        return
    elapsed = time.time() - _SESSION["t"]
    sys_, game_ = _SESSION["system"], _SESSION["game"]
    _SESSION["system"] = _SESSION["game"] = _SESSION["t"] = None
    if sys_ and game_ and elapsed >= 1:
        _record_session(sys_, game_, elapsed)

def _finalize_session():
    """Finalize the in-flight session (public, acquires lock)."""
    with _SESSION_LOCK:
        _finalize_session_locked()

def _game_stats_all():
    """Return the full playstats dict annotated with human fields."""
    pt = _playstats()
    total_secs = sum((v.get("total_secs", 0) if isinstance(v, dict) else 0) for v in pt.values())
    games = []
    for key, entry in pt.items():
        if not isinstance(entry, dict):
            continue
        s, _, g = key.partition("/")
        games.append({
            "key": key, "system": s, "game": g,
            "total_secs": entry.get("total_secs", 0),
            "last_played": entry.get("last_played"),
            "sessions": entry.get("sessions", 0),
        })
    games.sort(key=lambda x: -x["total_secs"])
    return {"ok": True, "games": games,
            "total_secs": total_secs,
            "total_hours": round(total_secs / 3600, 2)}

def _game_stats_one(system, game):
    """Return playstats for a single game (0 if never played)."""
    key = system + "/" + game
    pt = _playstats()
    entry = pt.get(key)
    if isinstance(entry, dict):
        return {"ok": True, "key": key, "system": system, "game": game,
                "total_secs": entry.get("total_secs", 0),
                "last_played": entry.get("last_played"),
                "sessions": entry.get("sessions", 0)}
    return {"ok": True, "key": key, "system": system, "game": game,
            "total_secs": 0, "last_played": None, "sessions": 0}

# Background session watcher: polls game_running every 10s; finalizes session when the
# game is no longer running. Handles SIGKILL (no clean exit path) best-effort.
def _session_watcher():
    import time as _time
    while True:
        _time.sleep(10)
        try:
            with _SESSION_LOCK:
                if _SESSION["t"] is None:
                    continue
            gr = game_running()
            if not gr.get("running"):
                _finalize_session()
        except Exception:
            pass

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
        rec = json.load(open(RECENT_F)); pt = _playstats(); favset = _fav_set()
        for r in rec:
            sysn, gamen = r.get("system", ""), r.get("game", "")
            entry = pt.get(sysn + "/" + gamen)
            r["secs"] = (entry.get("total_secs", 0) if isinstance(entry, dict)
                         else int(entry) if isinstance(entry, (int, float)) else 0)
            r["playtime_s"] = r["secs"]   # canonical field; secs kept for compat
            r["sessions"] = entry.get("sessions", 0) if isinstance(entry, dict) else 0
            r["last_played"] = entry.get("last_played") if isinstance(entry, dict) else None
            r["fav"] = (sysn, gamen) in favset
            # save-state thumbnail (task 53): the "where you left off" picture for resume
            # cards; additive + null when no state exists, so old consumers are unaffected.
            r["state_thumb"] = state_thumb_url(sysn, gamen)
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
    """Fetch box-art bytes for ONE game from libretro-thumbnails (keyless, no account). Returns
    (data, net_failed): bytes on success; (None, False) = a CLEAN 'no match' (every candidate name
    404'd — the expected, non-error result for homebrew titles the database never indexed);
    (None, True) = a NETWORK problem (DNS/timeout/refused/5xx/429) — 'couldn't reach the scraper'.
    Telling the two apart is what lets the UI say 'no art found' vs 'try again' honestly."""
    import urllib.parse, urllib.error, socket
    # libretro thumbnails use No-Intro names (with region tags). Real ROM sets already match the name
    # as-is; for tag-less names, try common region tags. Also try a tag-stripped fallback.
    cands = [game]
    base = re.sub(r"\s*[\(\[].*?[\)\]]", "", game).strip()
    if base and base != game:
        cands.append(base)
    if "(" not in game:   # tag-less filename → try the standard No-Intro region tags
        for tag in [" (USA)", " (World)", " (Europe)", " (Japan, USA)", " (USA, Europe)", " (Japan)"]:
            cands.append(game + tag)
    net_failed = False
    for nm in cands:
        url = "https://thumbnails.libretro.com/%s/Named_Boxarts/%s.png" % (
            urllib.parse.quote(sysname), urllib.parse.quote(nm))
        try:
            with urllib.request.urlopen(url, timeout=10) as r:   # bounded — never hangs on flaky wifi
                data = r.read()
            if data and len(data) > 1000:
                return data, False
        except urllib.error.HTTPError as e:
            if e.code in (404, 403, 410):
                continue          # this candidate name simply isn't in the database (a clean miss)
            net_failed = True; break   # 5xx / 429 rate-limit / auth — the server, not the name → stop
        except (urllib.error.URLError, socket.timeout, ConnectionError, OSError):
            net_failed = True; break   # DNS / timeout / no route → the host is down for ALL names too,
                                       # so don't burn one timeout per candidate (offline returns fast)
        except Exception:
            net_failed = True; break
    return None, net_failed

SCRAPE_STATE_F = "/userdata/gose-ui/scrape_state.json"

def _scrape_state():
    try:
        return json.load(open(SCRAPE_STATE_F))
    except Exception:
        return {}

def _rom_file_for(system, game):
    """The real ROM filename (with extension) in the system dir whose stem matches `game`
    (case-insensitive), or None. Used to anchor the gamelist <path> to an actual ROM."""
    d = os.path.join(ROMS, system)
    try:
        files = os.listdir(d)
    except Exception:
        return None
    gl = game.lower()
    for f in files:
        if f.startswith(".") or f in _SKIPDIRS or "gamelist" in f or os.path.isdir(os.path.join(d, f)):
            continue
        if os.path.splitext(f)[1].lower() in _SKIPEXT:
            continue
        if os.path.splitext(f)[0].lower() == gl:
            return f
    return None

def _gamelist_set_image(system, game, img_rel):
    """Record scraped box art in the system's gamelist.xml the Batocera way — an <image> field with a
    relative path — MERGING into any existing <game> entry (matched by <path>) without clobbering other
    games or other fields. Best-effort: a parse error or a missing ROM just skips the gamelist write
    (the on-disk image still drives the GOSE Library, which reads the images dir directly). Path-safe:
    img_rel is confined to the system dir; a gamelist whose root isn't <gameList> is left untouched."""
    import xml.etree.ElementTree as ET
    sysdir = os.path.join(ROMS, system)
    sysreal = os.path.realpath(sysdir)
    gl_path = os.path.join(sysdir, "gamelist.xml")
    rom_file = _rom_file_for(system, game)
    if not rom_file:
        return False
    img_abs = os.path.realpath(os.path.join(sysdir, img_rel.lstrip("./")))
    if not (img_abs == sysreal or img_abs.startswith(sysreal + os.sep)):
        return False                       # refuse a media path that escapes the system dir
    rom_rel = "./" + rom_file
    try:
        if os.path.isfile(gl_path):
            tree = ET.parse(gl_path)
            root = tree.getroot()
            if root.tag != "gameList":
                return False               # unknown schema — never clobber it
        else:
            root = ET.Element("gameList")
            tree = ET.ElementTree(root)
    except Exception as e:
        LOG.warning("gamelist parse failed for %s (left untouched): %s", system, e)
        return False
    target = None
    for gnode in root.findall("game"):
        p = (gnode.findtext("path") or "").strip()
        if not p:
            continue
        if os.path.basename(p) == rom_file or \
           os.path.splitext(os.path.basename(p))[0].lower() == game.lower():
            target = gnode; break
    if target is None:
        target = ET.SubElement(root, "game")
        ET.SubElement(target, "path").text = rom_rel
        ET.SubElement(target, "name").text = game
    node = target.find("image")
    if node is None:
        node = ET.SubElement(target, "image")
    node.text = img_rel
    try:
        tmp = gl_path + ".tmp"
        tree.write(tmp, encoding="utf-8", xml_declaration=True)
        os.replace(tmp, gl_path)
        return True
    except Exception as e:
        LOG.warning("gamelist write failed for %s: %s", system, e)
        try: os.remove(gl_path + ".tmp")
        except OSError: pass
        return False

def _write_art(system, game, data):
    """Persist scraped box art in BOTH places: the PNG under <system>/images/<game>-image.png (the
    GOSE Library's media dir) AND a merged <image> entry in gamelist.xml (what the Batocera frontend
    reads). Path-safe — the filename is confined to the images dir."""
    imgd = os.path.join(ROMS, system, "images")
    os.makedirs(imgd, exist_ok=True)
    fn = game + "-image.png"
    full = os.path.realpath(os.path.join(imgd, fn))
    if not full.startswith(os.path.realpath(imgd) + os.sep):
        raise ValueError("unsafe art filename")
    tmp = full + ".part"
    with open(tmp, "wb") as out:
        out.write(data)
    os.replace(tmp, full)
    _gamelist_set_image(system, game, "./images/" + fn)

def scrape_system(system, force=False, state=None):
    # Pull cover art from libretro-thumbnails for any game missing it. Art is written to disk
    # (/userdata/roms/<sys>/images/<game>-image.png) + recorded in gamelist.xml so it persists across
    # reboots and the Batocera frontend sees it too. A scrape_state manifest records ok/miss per game so
    # the auto pass (force=False) doesn't re-hit the network for known-missing titles on every boot,
    # while still picking up newly-added games. Manual scrape (force=True) retries misses. A network
    # failure is NEVER cached as 'miss' — so flaky wifi can't poison a title for good.
    sysname = _LIBRETRO_SYS.get(system)
    if not sysname:
        return {"ok": False, "error": "no thumbnail source for '%s'" % system}
    d = os.path.join(ROMS, system)
    imgd = os.path.join(d, "images")
    os.makedirs(imgd, exist_ok=True)
    own_state = state is None
    if state is None:
        state = _scrape_state()
    scraped, missed, skipped, net_errors = 0, 0, 0, 0
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
        data, net_failed = _scrape_one(sysname, game)
        if data:
            try:
                _write_art(system, game, data)
                scraped += 1; state[key] = "ok"
            except Exception as e:
                LOG.warning("scrape %s/%s: write failed: %s", system, game, e); net_errors += 1
        elif net_failed:
            net_errors += 1                # transient — do NOT cache 'miss', so a later pass retries
        else:
            missed += 1; state[key] = "miss"
    if own_state:
        write_json_atomic(SCRAPE_STATE_F, state)
    LOG.info("scrape %s: +%d art, %d missed, %d net-errors, %d skipped",
             system, scraped, missed, net_errors, skipped)
    return {"ok": True, "system": system, "scraped": scraped, "missed": missed,
            "net_errors": net_errors, "had_art": skipped}

def scrape_game(system, game):
    """Per-game manual 'Fetch art' (Library tile). Honest outcomes the UI can speak verbatim:
      ok+found=True            → art written (file + gamelist);
      ok+found=False           → no match in the database (EXPECTED for homebrew — NOT a failure);
      ok+found=False+no_source → this system has no art database at all;
      ok=False+net=True        → couldn't reach the scraper (flaky wifi) — try again."""
    system = (system or "").strip(); game = (game or "").strip()
    if not system or not game:
        return {"ok": False, "error": "system and game are required"}
    if "/" in game or "\\" in game or ".." in game:
        return {"ok": False, "error": "invalid game name"}
    sysname = _LIBRETRO_SYS.get(system)
    if not sysname:
        return {"ok": True, "found": False, "no_source": True,
                "error": "no art database for '%s'" % system}
    data, net_failed = _scrape_one(sysname, game)
    state = _scrape_state(); key = system + "/" + game
    if data:
        try:
            _write_art(system, game, data)
        except Exception as e:
            return {"ok": False, "error": "couldn't save art: %s" % e}
        state[key] = "ok"; write_json_atomic(SCRAPE_STATE_F, state)
        LOG.info("scrape-game %s/%s: art found (%d bytes)", system, game, len(data))
        return {"ok": True, "found": True, "scraped": 1}
    if net_failed:
        LOG.info("scrape-game %s/%s: could not reach scraper", system, game)
        return {"ok": False, "net": True,
                "error": "couldn't reach the art server — check your connection and try again"}
    state[key] = "miss"; write_json_atomic(SCRAPE_STATE_F, state)
    LOG.info("scrape-game %s/%s: no match in database", system, game)
    return {"ok": True, "found": False, "error": "no art found for this title"}

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

# ===== STORAGE MANAGER (task #38): "what's eating my space" breakdown + confirmed deletes =====
# Disk totals come from statvfs (authoritative). Category sizes come from du with a hard per-call
# timeout so a huge tree (saves/ is GBs) can never hang the (threaded) server. Deletes are confirmed
# AND path-confined: raw deletes are realpath-locked to the data roots below; apps/cores reuse the
# existing flatpak / libretro uninstall paths (never raw rm) — see store_uninstall / emulator_uninstall.
THEMES_DIR = "/userdata/themes"
STORAGE_LOW_FRAC = 0.10                 # banner when free < 10% of the disk ...
STORAGE_LOW_ABS = 2 * 1024 ** 3         # ... or under 2 GiB absolute (whichever triggers first)

def _store_del_roots():
    # the ONLY areas storage-manager raw deletes may touch (realpath'd). gallery dirs included so a
    # screenshot/clip is deletable; the account/PIN file (/userdata/system/gose) is outside ALL of
    # these AND _is_protected -> doubly unreachable.
    seen, roots = set(), []
    for p in (ROMS, SAVES_ROOT, BIOS_ROOT, THEMES_DIR, SHOTS_DIR, *GALLERY_DIRS):
        rp = os.path.realpath(p)
        if rp not in seen:
            seen.add(rp); roots.append(rp)
    return roots

def _within(path, root):
    return path == root or path.startswith(root.rstrip(os.sep) + os.sep)

def _du_multi(paths, timeout=25):
    """du -sb over several paths in ONE call -> {given_path: bytes}. Timeout/missing -> partial/empty.
    Never raises and never blocks past `timeout` (the watchdog against a huge-tree hang)."""
    paths = [p for p in paths if p and os.path.exists(p)]
    if not paths:
        return {}
    try:
        r = subprocess.run(["du", "-sb", "--", *paths], capture_output=True, text=True, timeout=timeout)
    except Exception:
        return {}
    out = {}
    for line in (r.stdout or "").strip().splitlines():
        parts = line.split("\t", 1)
        if len(parts) == 2:
            try:
                out[parts[1]] = int(parts[0])
            except Exception:
                pass
    return out

_SZ_UNITS = {"B": 1, "K": 1024, "M": 1024 ** 2, "G": 1024 ** 3, "T": 1024 ** 4,
             "KB": 1000, "MB": 1000 ** 2, "GB": 1000 ** 3, "TB": 1000 ** 4,
             "KIB": 1024, "MIB": 1024 ** 2, "GIB": 1024 ** 3, "TIB": 1024 ** 4}

def _parse_size(s):
    # flatpak's 'size' column is a human string (g_format_size: "1.2 GB", "512.0 MB", "?")
    try:
        m = re.match(r"\s*([\d.]+)\s*([A-Za-z]+)?", s or "")
        if not m:
            return None
        return int(float(m.group(1)) * _SZ_UNITS.get((m.group(2) or "B").upper(), 1))
    except Exception:
        return None

def _rom_item_count(sysdir):
    # cheap top-level count of actual game files/dirs (skip art/media + sidecars). Used only for a label.
    n = 0
    try:
        for name in os.listdir(sysdir):
            full = os.path.join(sysdir, name)
            if os.path.isdir(full):
                if name.lower() not in _SKIPDIRS:
                    n += 1
            elif os.path.splitext(name)[1].lower() not in _SKIPEXT:
                n += 1
    except Exception:
        pass
    return n

def _installed_app_sizes():
    # flatpak's own size column accounts for ostree dedup (a per-app du would over-count shared runtimes)
    try:
        r = subprocess.run(["flatpak", "list", "--app", "--columns=application,name,size"],
                           capture_output=True, text=True, timeout=20)
    except Exception:
        return []
    apps = []
    for line in r.stdout.splitlines():
        parts = line.split("\t")
        if parts and parts[0].strip():
            apps.append({"id": parts[0].strip(),
                         "name": (parts[1].strip() if len(parts) > 1 and parts[1].strip() else parts[0].strip()),
                         "bytes": _parse_size(parts[2]) if len(parts) > 2 else None})
    apps.sort(key=lambda a: -(a["bytes"] or 0))
    return apps

def _installed_core_sizes():
    out = []
    try:
        for f in sorted(os.listdir(LIBRETRO_DIR)):
            if f.endswith("_libretro.so"):
                try:
                    b = os.path.getsize(os.path.join(LIBRETRO_DIR, f))
                except Exception:
                    b = None
                out.append({"core": f[:-len("_libretro.so")], "bytes": b})
    except Exception:
        pass
    out.sort(key=lambda c: -(c["bytes"] or 0))
    return out

def storage_breakdown():
    """One call: disk totals + a sorted, per-category 'what's eating my space' breakdown.
    Each ROM SYSTEM is its own row (the big eaters); saves/bios/themes/gallery + installed apps/cores
    are category rows. `open:true` rows drill into per-item children via /storage/group."""
    try:
        st = os.statvfs("/userdata")
        total = st.f_blocks * st.f_frsize
        free = st.f_bavail * st.f_frsize
    except Exception as e:
        return {"ok": False, "error": str(e)}
    frac = (free / total) if total else 1.0
    out = {"ok": True, "total": total, "free": free, "used": total - free,
           "low": bool(free < STORAGE_LOW_ABS or frac < STORAGE_LOW_FRAC),
           "low_pct": int(STORAGE_LOW_FRAC * 100), "free_pct": round(frac * 100, 1),
           "groups": []}
    groups = []
    # --- ROM systems, one row each (skip the ~200 empty placeholder dirs) ---
    try:
        sysdirs = sorted(d for d in os.listdir(ROMS)
                         if os.path.isdir(os.path.join(ROMS, d)) and not d.startswith("."))
    except Exception:
        sysdirs = []
    rsizes = _du_multi([os.path.join(ROMS, d) for d in sysdirs], timeout=30)
    for d in sysdirs:
        p = os.path.join(ROMS, d)
        b = rsizes.get(p)
        cnt = _rom_item_count(p)
        if cnt == 0 and (b or 0) <= 1024 * 1024:       # empty / placeholder-only -> not worth a row
            continue
        groups.append({"key": "rom:" + d, "cat": "roms", "name": _SYS_EMU.get(d, d),
                       "sub": "%s · %d item%s" % (d, cnt, "" if cnt == 1 else "s"),
                       "bytes": b, "open": True})
    # --- category rows ---
    sv = _du_multi([SAVES_ROOT], timeout=25).get(SAVES_ROOT)
    groups.append({"key": "saves", "cat": "saves", "name": "Game saves",
                   "sub": "per-game progress & states", "bytes": sv, "open": True, "warn": True})
    apps = _installed_app_sizes()
    groups.append({"key": "apps", "cat": "apps", "name": "Installed apps",
                   "sub": "%d app%s · live inside saves" % (len(apps), "" if len(apps) == 1 else "s"),
                   "bytes": sum(a["bytes"] or 0 for a in apps) if apps else 0, "open": True})
    cores = _installed_core_sizes()
    groups.append({"key": "cores", "cat": "cores", "name": "Emulator cores",
                   "sub": "%d core%s · system partition" % (len(cores), "" if len(cores) == 1 else "s"),
                   "bytes": sum(c["bytes"] or 0 for c in cores) if cores else 0, "open": True})
    try:
        gal = game_gallery().get("items", [])
    except Exception:
        gal = []
    groups.append({"key": "gallery", "cat": "gallery", "name": "Screenshots & clips",
                   "sub": "%d item%s" % (len(gal), "" if len(gal) == 1 else "s"),
                   "bytes": sum(i.get("size") or 0 for i in gal), "open": True})
    groups.append({"key": "bios", "cat": "bios", "name": "BIOS files",
                   "sub": "console firmware", "bytes": _du_multi([BIOS_ROOT], timeout=15).get(BIOS_ROOT),
                   "open": True})
    groups.append({"key": "themes", "cat": "themes", "name": "Themes",
                   "sub": "EmulationStation themes",
                   "bytes": _du_multi([THEMES_DIR], timeout=15).get(THEMES_DIR), "open": True})
    groups.sort(key=lambda g: -(g["bytes"] or 0))
    out["groups"] = groups
    return out

def _list_dir_sized(base, warn=False):
    """Per-entry rows for a directory: files by stat, subdirs by du (one call). Each carries a
    realpath-locked raw delete descriptor (/storage/delete kind=path)."""
    items = []
    try:
        names = sorted(os.listdir(base))
    except Exception:
        return items
    dir_sizes = _du_multi([os.path.join(base, n) for n in names
                           if os.path.isdir(os.path.join(base, n))], timeout=25)
    for name in names:
        full = os.path.join(base, name)
        if os.path.isdir(full):
            b, kind = dir_sizes.get(full), "dir"
        else:
            try:
                b = os.path.getsize(full)
            except Exception:
                b = None
            kind = "file"
        items.append({"name": name, "bytes": b, "kind": kind, "warn": warn,
                      "del": {"ep": "/storage/delete",
                              "body": {"kind": "path", "path": os.path.realpath(full), "confirm": True}}})
    items.sort(key=lambda x: -(x["bytes"] or 0))
    return items

def _group_rom_files(system):
    if not re.match(r"^[A-Za-z0-9][A-Za-z0-9_+\-]{0,63}$", system or ""):
        return {"ok": False, "error": "invalid system name"}
    base = os.path.join(ROMS, system)
    if not _within(os.path.realpath(base), os.path.realpath(ROMS)) or not os.path.isdir(base):
        return {"ok": False, "error": "no such system"}
    whole = _du_multi([base], timeout=25).get(base)
    head = {"name": "Delete ALL of %s" % _SYS_EMU.get(system, system),
            "sub": "%d item(s) — frees the whole folder" % _rom_item_count(base),
            "bytes": whole, "kind": "all", "warn": True,
            "del": {"ep": "/storage/delete",
                    "body": {"kind": "rom_system", "system": system, "confirm": True}}}
    return {"ok": True, "name": _SYS_EMU.get(system, system), "key": "rom:" + system,
            "items": [head] + _list_dir_sized(base)}

def _group_apps():
    return {"ok": True, "name": "Installed apps", "key": "apps",
            "items": [{"name": a["name"], "sub": a["id"], "bytes": a["bytes"], "kind": "app",
                       "del": {"ep": "/store/uninstall", "body": {"id": a["id"]}}}
                      for a in _installed_app_sizes()]}

def _group_cores():
    return {"ok": True, "name": "Emulator cores", "key": "cores",
            "items": [{"name": c["core"], "sub": "libretro core", "bytes": c["bytes"], "kind": "core",
                       "del": {"ep": "/emulators/uninstall", "body": {"core": c["core"]}}}
                      for c in _installed_core_sizes()]}

def _group_gallery():
    try:
        gal = game_gallery().get("items", [])
    except Exception:
        gal = []
    return {"ok": True, "name": "Screenshots & clips", "key": "gallery",
            "items": [{"name": i["name"], "bytes": i.get("size"), "kind": i.get("kind", "file"),
                       "del": {"ep": "/storage/delete",
                               "body": {"kind": "path", "path": os.path.realpath(i["path"]),
                                        "confirm": True}}}
                      for i in gal]}

def storage_group(key):
    key = (key or "").strip()
    if key.startswith("rom:"):
        return _group_rom_files(key[4:])
    if key == "saves":
        return {"ok": True, "name": "Game saves", "key": "saves",
                "items": _list_dir_sized(SAVES_ROOT, warn=True)}
    if key == "apps":
        return _group_apps()
    if key == "cores":
        return _group_cores()
    if key == "gallery":
        return _group_gallery()
    if key == "bios":
        return {"ok": True, "name": "BIOS files", "key": "bios", "items": _list_dir_sized(BIOS_ROOT)}
    if key == "themes":
        return {"ok": True, "name": "Themes", "key": "themes", "items": _list_dir_sized(THEMES_DIR)}
    return {"ok": False, "error": "unknown group"}

def storage_delete(payload):
    """Raw, CONFIRMED, path-confined delete. apps/cores go through their own uninstall routes (not here).
    Confinement: realpath must land inside /userdata AND inside a data root (_store_del_roots) AND not be
    _is_protected — so ../ traversal, the OS, and the account/PIN file are all refused."""
    payload = payload or {}
    if not payload.get("confirm"):
        return {"ok": False, "error": "refused: delete requires an explicit confirm"}
    kind = payload.get("kind")
    if kind == "rom_system":
        sysn = payload.get("system") or ""
        if not re.match(r"^[A-Za-z0-9][A-Za-z0-9_+\-]{0,63}$", sysn):
            return {"ok": False, "error": "invalid system name"}
        target = os.path.join(ROMS, sysn)
    elif kind == "path":
        target = payload.get("path") or ""
    else:
        return {"ok": False, "error": "unknown delete kind"}
    try:
        rp = os.path.realpath(target)
    except Exception:
        return {"ok": False, "error": "bad path"}
    if not (rp == FS_ROOT or rp.startswith(FS_ROOT + os.sep)):
        return {"ok": False, "error": "refused: path escapes /userdata"}
    roots = _store_del_roots()
    if not any(_within(rp, r) for r in roots):
        return {"ok": False, "error": "refused: outside the deletable storage areas"}
    if rp in roots:
        return {"ok": False, "error": "refused: can't delete an entire storage root at once"}
    if _is_protected(rp):
        return {"ok": False, "error": "refused: that's a protected system path"}
    if not os.path.lexists(rp):
        return {"ok": False, "error": "already gone"}
    try:
        if os.path.isdir(rp) and not os.path.islink(rp):
            shutil.rmtree(rp)
        else:
            os.remove(rp)
    except Exception as e:
        return {"ok": False, "error": str(e)}
    LOG.info("STORAGE DELETE [%s] %s", kind, rp)
    return {"ok": True, "deleted": rp, "kind": kind}

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
    # Owner-approved free games (provenance review, docs/19 spirit: nothing commercial).
    # Both Flathub IDs verified live against flathub.org/api/v2/summary 2026-06-07.
    # Kapman: GPLv2+ (KDE Games) — fully open-source code AND assets.
    # SuperTuxKart: GPLv3 code, CC-BY-SA assets — fully open-source.
    {"id": "org.kde.kapman", "name": "Kapman", "desc": "Maze-chase classic, fully open-source (KDE Games)", "cat": "Games", "icon": "gamepad-2"},
    {"id": "net.supertuxkart.SuperTuxKart", "name": "SuperTuxKart", "desc": "Open-source kart racer", "cat": "Games", "icon": "gamepad-2"},
    # Dev / security tools — install-only Flatpaks, not baked into the image.
    # Wireshark Flathub ID verified live against flathub.org/api/v2/summary 2026-06-08.
    {"id": "org.wireshark.Wireshark", "name": "Wireshark", "desc": "Network protocol analyzer — inspect exactly what your device sends/receives. A desktop tool (keyboard/mouse), for digging deeper than the built-in Network Monitor.", "cat": "Tools", "icon": "network"},
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

def _player_devices():
    """Enumerate player-capable pads from /proc/bus/input/devices in the launcher's DEFAULT
    player order: HUMAN pads (passthrough/native) first, then our uinput virtual AI seats,
    each sorted by js index, so a human always lands on the lowest player slot when present.
    Returns (all_js, devices) where devices is a list of dicts
    {js, path, guid, name, source}. SINGLE source of truth for both _virtual_pad_args (what
    actually launches) and the pre-launch lobby (/lobby/state) — so the lobby can never show
    a seat->player mapping that disagrees with the cmdline GOSE will build.
    NOTE: a physical pad's GUID must exist in the launcher's controller DB to generate binds —
    true for common pads (same constraint ES had); the AI pads guarantee it by masquerading as
    Xbox 360 while reporting their own name ("AI virtual controller N", bind keys off GUID)."""
    try:
        txt = open("/proc/bus/input/devices").read()
    except Exception:
        return [], []
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
        js = int(jss[0]); path = "/dev/input/event" + evs[0]
        name_m = re.search(r'Name="([^"]*)"', blk)
        name = name_m.group(1) if name_m else "pad"
        if "gose-passthrough" in blk:
            # Host-pad PASSTHROUGH (uinput mirror of the human's physical pad). It IS a human
            # player (lowest player slot) with its REAL GUID (pt_open recreated the real
            # vendor/product/version, so the kernel-id GUID matches the launcher DB entry).
            ids = re.search(r"Bus=(\w+) Vendor=(\w+) Product=(\w+) Version=(\w+)", blk)
            guid = (_sdl_guid(*(int(x, 16) for x in ids.groups())) if ids else _XBOX_GUID)
            phys.append((js, path, guid, name, "passthrough"))
        elif "py-evdev-uinput" in blk:
            virt.append((js, path, _XBOX_GUID, name, "virtual"))
        elif not any(s in name.lower() for s in _NON_PADS):
            ids = re.search(r"Bus=(\w+) Vendor=(\w+) Product=(\w+) Version=(\w+)", blk)
            guid = (_sdl_guid(*(int(x, 16) for x in ids.groups())) if ids else _XBOX_GUID)
            phys.append((js, path, guid, name, "native"))
    all_js = sorted(set(all_js)); virt.sort(); phys.sort()
    devices = [{"js": js, "path": path, "guid": guid, "name": name, "source": src}
               for (js, path, guid, name, src) in (phys + virt)]
    return all_js, devices

def _virtual_pad_args(max_players=5, order=None):
    """Build emulatorlauncher -pN controller args (the job EmulationStation used to do).
    Default order is _player_devices' (humans then AI seats). `order`, when given, is a list
    of device event paths (the pre-launch lobby's seat->player mapping, P1 first): devices are
    emitted in THAT order and any path not present is skipped — so a stale/garbage mapping
    degrades to whatever real pads remain, and an order that matches NOTHING falls back to the
    historical positional default rather than launching with zero pads. order=None keeps the
    exact pre-lobby behaviour. Each device keeps its OWN SDL joystick index (-pNindex) no
    matter which player slot it lands in."""
    all_js, devices = _player_devices()
    if order:
        by_path = {d["path"]: d for d in devices}
        chosen = [by_path[p] for p in order if p in by_path]
        if chosen:                       # all-stale order → keep the safe default mapping
            devices = chosen
    args = []
    for n, d in enumerate(devices[:max_players], start=1):
        js = d["js"]
        idx = all_js.index(js) if js in all_js else (n - 1)
        args += ["-p%dindex" % n, str(idx), "-p%dguid" % n, d["guid"],
                 "-p%dname" % n, d["name"], "-p%ddevicepath" % n, d["path"],
                 "-p%dnbbuttons" % n, "11", "-p%dnbhats" % n, "1", "-p%dnbaxes" % n, "6"]
    return args

def launch_game(system, game, players=None):
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
    # #97 BIOS gate: check before spawning — a missing BIOS = black-screen crash, not a mystery.
    _missing_bios = _bios_missing_for_system(system)
    if _missing_bios:
        _sysname = _SYS.get(system, system)
        _files = ", ".join(_missing_bios)
        return {
            "ok": False,
            "bios_missing": True,
            "missing": _missing_bios,
            "error": (
                "Can't launch %s: missing BIOS file%s %s — "
                "drop %s into /userdata/bios (open BIOS Check in Settings to see what's needed)"
                % (_sysname, "s" if len(_missing_bios) != 1 else "",
                   _files, "them" if len(_missing_bios) != 1 else "it")
            ),
        }
    try:
        _spawn(["emulatorlauncher"] + _virtual_pad_args(order=players) + ["-system", system, "-rom", rom])
        record_recent(system, game)
        _session_start(system, game)   # playtime tracking: finalize prior, start new
        _TIMECTL["slot"] = 0; _TIMECTL["ff"] = False   # #37 RetroArch launch defaults
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
        # players: optional list of device event paths (the pre-launch lobby's seat->player
        # mapping, P1 first). Absent → the historical default order (humans then AI seats).
        pl = payload.get("players")
        return launch_game(payload["system"], payload["game"],
                           pl if isinstance(pl, list) and pl else None)
    app = payload.get("app"); cmd = payload.get("cmd")
    if app == "moonlight":
        return launch_moonlight()
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

# ---- Moonlight: stream your PC (task 66). moonlight-qt ships on the Batocera image; we just
# surface + launch it. Its own UI discovers PCs + handles pairing (Sunshine / GeForce Experience). ----
MOONLIGHT_BIN = "/usr/bin/moonlight-qt"

def moonlight_status():
    import shutil
    p = MOONLIGHT_BIN if os.path.isfile(MOONLIGHT_BIN) else (shutil.which("moonlight-qt") or shutil.which("moonlight"))
    return {"ok": True, "installed": bool(p), "bin": p}

def launch_moonlight():
    st = moonlight_status()
    if not st["installed"]:
        return {"ok": False, "error": "Moonlight isn't installed on this image."}
    try:
        _spawn([st["bin"]])
        return {"ok": True, "launched": "moonlight",
                "note": "Moonlight is opening. On your PC, run Sunshine (or GeForce Experience), "
                        "then pick your PC in Moonlight and enter the PIN to pair."}
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
        return {"ok": True, "connection": conn, "type": typ, "online": online, "has_wifi": has_wifi,
                "hostname": socket.gethostname()}
    except Exception as e:
        return {"ok": False, "error": str(e), "hostname": socket.gethostname()}

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

def sys_audio_devices():
    """GET /sys/audio-devices — list available output sinks via `batocera-audio list`
    (tab-delimited: <device_id>\\t<label>), plus the current selection from batocera.conf.
    Timeout-safe: 5 s max; falls back to a single-device list from aplay -L on failure.
    Returns {"ok":True, "devices":[{"id":..,"label":..}], "current":..}"""
    current = _bconf_get("audio.device") or "auto"
    try:
        r = subprocess.run(["batocera-audio", "list"], capture_output=True, text=True, timeout=5)
        devices = []
        for line in r.stdout.splitlines():
            line = line.strip()
            if not line:
                continue
            parts = line.split("\t", 1)
            dev_id = parts[0].strip()
            label = parts[1].strip() if len(parts) > 1 else dev_id
            if dev_id:
                devices.append({"id": dev_id, "label": label})
        if not devices:
            # batocera-audio listed nothing — fallback honest single entry
            devices = [{"id": current, "label": "Default (system)"}]
        return {"ok": True, "devices": devices, "current": current}
    except Exception as e:
        # batocera-audio absent or timed out — surface what we know
        return {"ok": True, "devices": [{"id": current, "label": "Default (system)"}],
                "current": current, "note": "batocera-audio unavailable: %s" % str(e)}

def sys_audio_device_set(device):
    """POST /sys/audio-device {device: <id>} — write audio.device to batocera.conf
    and apply immediately via `batocera-audio set`.  Always atomic (uses _bconf_set).
    Returns {"ok":True, "device":..} on success."""
    if not device or not isinstance(device, str):
        return {"ok": False, "error": "device is required"}
    device = device.strip()
    if not device:
        return {"ok": False, "error": "device is empty"}
    # Write to batocera.conf first (survives reboot)
    _bconf_set("audio.device", device)
    # Apply live without reboot
    try:
        subprocess.run(["batocera-audio", "set", device],
                       capture_output=True, text=True, timeout=5)
    except Exception:
        pass  # conf is already written; live-apply is best-effort
    return {"ok": True, "device": device}

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

# ---- Settings backends (task 14): SSH / display mode / vsync / timezone -----------------
def sys_ssh(enabled=None):
    # Real state = a running sshd/dropbear. Toggle = init script now + batocera.conf for
    # the next boot. The Settings row arms a press-twice confirm before calling this with
    # enabled=False (disabling cuts remote console access on purpose).
    if enabled is not None:
        on = bool(enabled)
        try:
            _bconf_set("system.ssh.enabled", "1" if on else "0")
        except Exception as e:
            LOG.warning("ssh conf write failed: %s", e)
        try:
            subprocess.run(["/bin/sh", "-c",
                "for s in /etc/init.d/S50sshd /etc/init.d/S50dropbear; do [ -x \"$s\" ] && \"$s\" %s; done; true"
                % ("start" if on else "stop")], capture_output=True, text=True, timeout=15)
        except Exception as e:
            return {"ok": False, "error": str(e)}
        LOG.info("ssh %s (settings)", "enabled" if on else "DISABLED")
    try:
        r = subprocess.run(["/bin/sh", "-c",
                            "pgrep -x sshd >/dev/null 2>&1 || pgrep -x dropbear >/dev/null 2>&1"],
                           timeout=8)
        running = (r.returncode == 0)
    except Exception:
        running = False
    return {"ok": True, "enabled": running}

# ---- Security: owner-gated SSH (docs/31 SB-1) ------------------------------------------------
# THE GATE (docs/16/31): turning network SSH on exposes remote ROOT, so it is reserved to the
# human OWNER and an AI can NEVER flip it — not even an admin-tier ai_token. The agent's
# observe<play<admin tiers govern the *agent* (port 8731); this gate lives on the *UI server*
# (loopback 8780) and checks an OWNER SECRET, not a tier, so "admin AI" buys nothing here. The
# only route an AI has to 8780 is its sandboxed `system.run` shell, which (a) cannot read the
# dev/owner token (`/userdata/system/gose/token` is shadowed 0o000 in the agent mount-ns) and
# (b) does not know the device sign-in PIN. Either proof = owner; neither = refused.
OWNER_TOKEN_F = os.environ.get("GOSE_OWNER_TOKEN_FILE") or "/userdata/system/gose/token"
SSH_CRED_F    = os.environ.get("GOSE_SSH_CRED_FILE") or "/userdata/system/gose/ssh_cred.json"
_SSH_DRYRUN   = os.environ.get("GOSE_SSH_DRYRUN") == "1"   # test seam: exercise the flow w/o touching dropbear
# THE UNIFY (docs/31, Task #87): SSH no longer mints a separate random password. On enable the
# root credential is set to the SAME value the owner just signed-in/proved with at the gate (their
# device PIN or the dev token) — one credential. The login scrypt hash isn't reversible, so the
# value is captured at gate-time, used once to set the root password, and never stored.
# Rate-limit for remote SSH auth (docs/31): a 4-digit PIN can become the root credential, so we cap
# dropbear auth tries/connection (-T) + drop idle sessions (-I). The init (/etc/init.d/S50dropbear)
# sources /etc/default/dropbear and honors $DROPBEAR_ARGS, so we ship the flags there; they apply on
# the next dropbear (re)start the enable flow triggers. A per-IP throttle (iptables hashlimit) is
# documented in docs/31 for the shipped image.
SSH_DROPBEAR_DEFAULT_F = os.environ.get("GOSE_DROPBEAR_DEFAULT_FILE") or "/etc/default/dropbear"
SSH_AUTH_MAX_TRIES = 3      # dropbear -T : max password attempts per connection (default is 10)
SSH_IDLE_TIMEOUT_S = 300    # dropbear -I : drop an idle session after 5 min
SSH_RATE_LIMIT_ARGS = "-T %d -I %d" % (SSH_AUTH_MAX_TRIES, SSH_IDLE_TIMEOUT_S)

def _owner_token():
    try:
        t = open(OWNER_TOKEN_F).read().strip()
        return t or None
    except Exception:
        return None

def _owner_credential(payload):
    """Owner-gate that ALSO returns the exact credential the owner proved with, so SSH can be set to
    the SAME value the owner signs in with (one credential — docs/31). Returns (ok, cred, kind):
      PIN path   -> (True, '<pin>',   'pin')    the device sign-in PIN (the human-at-the-kiosk proof)
      token path -> (True, '<token>', 'token')  the dev/owner token (sandbox-shadowed from the AI shell)
      refused    -> (False, None, None)
    Neither proof is obtainable by an AI: the PIN is verified through the rate-limited scrypt path; the
    dev token (`/userdata/system/gose/token`) is 0o000-shadowed in the agent mount-ns. Constant-time
    compares; deny by default. The returned cred is used ONLY to set the live root password and is
    NEVER logged or persisted (ssh_cred.json keeps non-secret flags only)."""
    p = payload or {}
    ot, given = _owner_token(), str(p.get("owner_token") or "")
    if ot and given:
        try:
            import hmac
            if hmac.compare_digest(given, ot):
                return True, ot, "token"
        except Exception:
            pass
    if p.get("pin"):
        pin = str(p.get("pin"))
        try:
            if pin_verify({"pin": pin}).get("valid"):
                return True, pin, "pin"
        except Exception:
            pass
    return False, None, None

def _owner_ok(payload):
    """Owner-gate as a plain bool (the proof, not the credential) — for the paths where only the gate
    matters (check / disable / the legacy /sys/ssh toggle). Same two proofs as _owner_credential."""
    return _owner_credential(payload)[0]

def _credential_is_weak(cred):
    """Honest remote-SSH strength check on the credential that will become the root password.
    Policy (2026-06-08): 8 digits is the minimum compliant PIN, so an 8-digit numeric PIN is NOT
    flagged as weak for SSH. Only an all-same-digit trivial PIN (e.g. 00000000) or an unusually
    short credential (< 8 chars) earns a warning. A longer/complex password or the dev token is
    not flagged."""
    if not cred:
        return False
    if cred.isdigit():
        # Compliant 8-digit PINs: warn only for trivially weak patterns (all-same digit)
        if len(cred) >= 8:
            return len(set(cred)) == 1   # e.g. "00000000" — all same digit is still trivial
        return True  # numeric and < 8 chars (shouldn't be settable, but gate it anyway)
    return False  # password (non-numeric) or dev token — not flagged

_WEAK_PIN_MSG = ("Your device sign-in credential is a trivially weak numeric PIN (all the same digit). "
                 "Consider a more varied PIN or a longer sign-in password — SSH auth is rate-limited, "
                 "but a less predictable secret is far stronger.")

def _ssh_harden_dropbear(write=True):
    """Rate-limit remote SSH auth (docs/31). dropbear's init sources /etc/default/dropbear and honors
    $DROPBEAR_ARGS, so we ship `-T`(max auth tries/conn) + `-I`(idle timeout) there; it applies on the
    next dropbear (re)start the enable flow triggers. Returns the args string either way, so the
    rate-limit is verifiable without mutating anything when write=False (dryrun / the 'shown' path)."""
    if write:
        try:
            with open(SSH_DROPBEAR_DEFAULT_F, "w") as f:
                f.write("# GOSE: rate-limit remote SSH auth (docs/31) — applied on dropbear start\n")
                f.write('DROPBEAR_ARGS="%s"\n' % SSH_RATE_LIMIT_ARGS)
        except Exception as e:
            LOG.warning("dropbear rate-limit config write failed: %s", e)
    return SSH_RATE_LIMIT_ARGS

def _ssh_cred_load():
    try:
        return json.load(open(SSH_CRED_F))
    except Exception:
        return {}

def security_ssh_state():
    cred = _ssh_cred_load()
    enabled = bool(cred.get("_dry_enabled")) if _SSH_DRYRUN else sys_ssh().get("enabled", False)
    src = cred.get("source", "login_credential")
    # can_reveal=True only when a server-generated password was stored and hasn't been revealed yet.
    # For login_credential the owner already knows their PIN — no reveal window.
    can_reveal = bool(cred.get("can_reveal")) if src == "generated" else False
    cred_source = "login" if src == "login_credential" else src
    return {"ok": True, "enabled": enabled, "has_credential": bool(cred.get("set")),
            "username": "root", "owner_required": True,
            "credential_source": cred_source,
            "can_reveal": can_reveal,
            "rate_limited": True, "auth_max_tries": SSH_AUTH_MAX_TRIES,
            "ssh_rate_limit_args": SSH_RATE_LIMIT_ARGS}

def security_ssh(payload):
    """POST /security/ssh {action: check|enable|disable|state|reveal, owner_token?|pin?}.
    THE UNIFY (docs/31, #87): on enable the SSH/root credential is set to the SAME sign-in credential
    the owner just typed at the gate (their device PIN, or the dev token) — sign in with X, SSH with X,
    one credential. No separate random password. The value is captured at gate-time (the login scrypt
    hash isn't reversible), used once to set the root password, and NEVER stored — ssh_cred.json holds
    only non-secret flags. enable also rate-limits SSH auth (dropbear -T/-I) and warns when the
    credential is a short numeric PIN (weak for remote SSH). check -> owner-gate probe only (no
    mutation), so the gate can be verified live without touching the running service or its password.
    disable -> stops SSH.
    reveal (#99): one-time credential reveal for the Settings Security pane modal. Two cases:
      login_credential (the #87 unified path): owner already knows their PIN — return an informational
        message with no plaintext (no owner gate needed; nothing sensitive is stored).
      generated (future path): a server-generated random password was stored temporarily for the OOBE
        reveal window; requires owner proof (PIN or dev token — an AI token is refused because AI tokens
        are not valid owner proofs); returns the plaintext ONCE then clears can_reveal and wipes the
        stored plaintext so it can never be revealed again. Plaintext never logged or in diag bundles."""
    p = payload or {}
    action = (p.get("action") or "").lower()
    if action in ("", "state"):
        return security_ssh_state()
    if action == "reveal":
        rec = _ssh_cred_load()
        src = rec.get("source", "login_credential")
        if src == "login_credential" or src == "login":
            # login-credential case: the SSH password IS the device PIN/password.
            # We never stored it in plaintext (the #87 unify), and the owner already knows it.
            # Return a clear informational message — no owner gate needed, no plaintext in response.
            LOG.info("security/ssh reveal: login_credential — returning informational message (no plaintext)")
            return {"ok": True, "credential_source": "login",
                    "message": "Your remote access password is your device PIN/password — "
                               "the same credential you use to sign in to GOSE. "
                               "GOSE never stores a plaintext copy."}
        if src == "generated":
            # generated-credential case: a server-generated random password was stored for the
            # one-time reveal window. Requires owner proof — AI tokens are refused because only
            # owner_token (the dev token, 0o000-shadowed from the agent mount-ns) and the device
            # PIN are accepted by _owner_credential; an AI bearer token is neither.
            ok, _cred, _kind = _owner_credential(p)
            if not ok:
                LOG.warning("security/ssh reveal REFUSED — requester is not the owner (generated-cred path)")
                return {"ok": False, "code": "ERR_NOT_OWNER",
                        "error": "owner authorization required — reveal is owner-only; "
                                 "an AI token is never accepted here"}
            if not rec.get("can_reveal"):
                LOG.warning("security/ssh reveal REFUSED — already revealed (can_reveal=false)")
                return {"ok": False, "code": "ERR_ALREADY_REVEALED",
                        "error": "credential has already been revealed — the one-time window has closed"}
            plaintext = rec.get("_generated_pw")
            if not plaintext:
                LOG.warning("security/ssh reveal: generated source but no stored plaintext (inconsistent state)")
                return {"ok": False, "code": "ERR_NO_CREDENTIAL",
                        "error": "no stored credential to reveal (inconsistent state)"}
            # Clear the reveal flag and wipe the stored plaintext — one reveal only, ever.
            rec["can_reveal"] = False
            rec.pop("_generated_pw", None)
            try:
                write_json_atomic(SSH_CRED_F, rec)
            except Exception as e:
                LOG.warning("ssh cred flag persist after reveal failed: %s", e)
            # Log that a reveal happened but NEVER log the plaintext.
            LOG.info("security/ssh reveal: generated credential revealed ONCE by owner — can_reveal cleared")
            return {"ok": True, "credential_source": "generated", "credential": plaintext}
        # Unknown source — be safe, return nothing.
        LOG.warning("security/ssh reveal: unknown credential source %r — refusing", src)
        return {"ok": False, "error": "cannot reveal credential — source unknown"}
    if action not in ("check", "enable", "disable"):
        return {"ok": False, "error": "action must be check|enable|disable|state|reveal"}
    ok, cred, kind = _owner_credential(p)   # the gate AND (for enable) the credential to set, in one
    if not ok:
        LOG.warning("security/ssh %s REFUSED — requester is not the owner", action)
        return {"ok": False, "code": "ERR_NOT_OWNER",
                "error": "owner authorization required — SSH is owner-only (docs/16/31); "
                         "an AI can never enable it"}
    if action == "check":          # gate passed, no side effects (the safe verification path)
        return {"ok": True, "owner": True, "enabled": security_ssh_state()["enabled"]}
    if action == "enable":
        weak = _credential_is_weak(cred)
        if not _SSH_DRYRUN:
            try:
                r = subprocess.run(["chpasswd"], input="root:%s\n" % cred,   # set root cred == sign-in cred
                                   capture_output=True, text=True, timeout=15)
                if r.returncode != 0:
                    return {"ok": False, "error": "could not set password: %s"
                            % ((r.stderr or "chpasswd failed").strip()[:160])}
            except Exception as e:
                return {"ok": False, "error": "could not set password: %s" % e}
            _ssh_harden_dropbear(write=True)        # rate-limit BEFORE (re)start so it takes effect
            st = sys_ssh(enabled=True)
            if not st.get("ok"):
                return {"ok": False, "error": st.get("error", "ssh start failed")}
            enabled = bool(st.get("enabled"))
        else:
            enabled = True
        rec = _ssh_cred_load()
        rec.update({"set": True, "set_at": int(time.time()), "username": "root",
                    "source": "login_credential"})   # the WHAT (non-secret flag), never the secret
        if _SSH_DRYRUN:
            rec["_dry_enabled"] = True
        try:
            write_json_atomic(SSH_CRED_F, rec)
        except Exception as e:
            LOG.warning("ssh cred flag persist failed: %s", e)
        LOG.info("ssh ENABLED by owner — root credential set to the owner's sign-in credential%s%s",
                 " (WEAK short numeric PIN)" if weak else "", " (dryrun)" if _SSH_DRYRUN else "")
        out = {"ok": True, "enabled": enabled, "username": "root", "credential_source": "login",
               "note": "SSH uses your device sign-in PIN/password — nothing new to remember; "
                       "GOSE stores no password.",
               "rate_limited": True, "auth_max_tries": SSH_AUTH_MAX_TRIES,
               "ssh_rate_limit_args": _ssh_harden_dropbear(write=False)}
        if weak:
            out["weak_credential"] = True
            out["weak_warning"] = _WEAK_PIN_MSG
        if _SSH_DRYRUN:    # test seam only: a hash (never plaintext) lets the harness assert the
            import hashlib  # chpasswd target == the typed credential (not a random one). Not persisted.
            out["dry_target_sha256"] = hashlib.sha256(("root:%s\n" % cred).encode()).hexdigest()
        return out
    # disable
    if not _SSH_DRYRUN:
        st = sys_ssh(enabled=False)
        enabled = bool(st.get("enabled"))
    else:
        rec = _ssh_cred_load(); rec["_dry_enabled"] = False
        try:
            write_json_atomic(SSH_CRED_F, rec)
        except Exception:
            pass
        enabled = False
    LOG.info("ssh DISABLED by owner%s", " (dryrun)" if _SSH_DRYRUN else "")
    return {"ok": True, "enabled": enabled}

# ---- Security: Samba / network share (docs/31 SB-2, Task #39) -------------------------
# SMB OFF by default (ship blocker SB-2): on real Wi-Fi, smbd exposes /userdata
# (ROMs, saves, configs) to anyone on the network, guest-accessible, with no password.
# The shipped batocera.conf.gose sets system.samba.enabled=0. This feature surfaces
# the current state and gives the owner an explicit opt-in toggle.
#
# SECURITY CONTRACT (docs/31):
#   - The shipped default is OFF; this endpoint never auto-enables.
#   - Enabling is a plain informed action — no owner-gate needed because SMB on LAN is
#     less severe than remote root SSH, but we surface an honest exposure warning so the
#     owner understands what they're turning on.
#   - The dev VM runs under SLIRP (guest 10.0.2.15, no real LAN route in or out) so
#     the share is not actually reachable from outside even when enabled here. That fact
#     is reported in the state so the UI can be honest about it.
#   - Disabling kills smbd/nmbd now and sets system.samba.enabled=0 in batocera.conf
#     so it stays off after reboot.
#   - Enabling sets system.samba.enabled=1 and starts smbd/nmbd.
#
# Batocera's smb.conf exposes one share: [share] -> /userdata (writeable, guest ok).
# The share name and paths come from the live smb.conf so we're not inventing them.
#
# Schema (GET /security/smb):
#   {ok, enabled, host, ip, share_path, share_name, unc,
#    shares:[{name,path,writeable}], slirp_vm:bool, exposure_warning:str}
# Schema (POST /security/smb {action:"enable"|"disable"}):
#   {ok, enabled, [error]}

def _smb_shares():
    """Read shares from the live smb.conf (whichever the init script uses).
    Returns a list of {name,path,writeable} skipping [global]/[homes]/[nobody]/[printers]."""
    _SKIP = {"global", "homes", "nobody", "printers", "print$"}
    shares = []
    conf = "/etc/samba/smb-secure.conf" if os.path.isfile("/etc/samba/smb-secure.conf") else "/etc/samba/smb.conf"
    try:
        cur = None
        for raw in open(conf):
            line = raw.strip()
            if line.startswith("[") and line.endswith("]"):
                cur = line[1:-1].lower()
            elif cur and cur not in _SKIP:
                if line.lower().startswith("path"):
                    path = line.split("=", 1)[-1].strip()
                    # ensure we have an entry for this share name
                    entry = next((s for s in shares if s["name"] == cur), None)
                    if entry is None:
                        entry = {"name": cur, "path": path, "writeable": False}
                        shares.append(entry)
                    else:
                        entry["path"] = path
                elif line.lower().replace(" ", "").startswith("writeable=") or \
                     line.lower().replace(" ", "").startswith("writable="):
                    val = line.split("=", 1)[-1].strip().lower()
                    entry = next((s for s in shares if s["name"] == cur), None)
                    if entry is None:
                        entry = {"name": cur, "path": "", "writeable": False}
                        shares.append(entry)
                    entry["writeable"] = val in ("yes", "true", "1")
    except Exception:
        pass
    return [s for s in shares if s.get("path")]

def _smb_enabled():
    """True if smbd is actually running right now."""
    try:
        r = subprocess.run(["/bin/sh", "-c",
                            "pgrep -x smbd >/dev/null 2>&1"],
                           timeout=6)
        return r.returncode == 0
    except Exception:
        return False

def _is_slirp_vm():
    """True when we're running under QEMU SLIRP (guest has 10.0.2.x — not a real LAN)."""
    try:
        out = subprocess.run(["/bin/sh", "-c", "ip route show default"],
                             capture_output=True, text=True, timeout=5).stdout
        return "10.0.2.2" in out
    except Exception:
        return False

_SMB_EXPOSURE_WARNING = (
    "Enabling file sharing exposes /userdata (ROMs, saves, configs) to every device "
    "on your local network with no password. Only enable this on a network you trust."
)

def security_smb_state():
    enabled = _smb_enabled()
    shares = _smb_shares()
    hostname = socket.gethostname()
    # Best routable IP for the UNC path hint (pick first non-loopback IPv4)
    ip = ""
    try:
        out = subprocess.run(["/bin/sh", "-c", "ip -4 addr show | grep 'inet ' | grep -v '127\\.0\\.0\\.1'"],
                             capture_output=True, text=True, timeout=5).stdout
        for l in out.splitlines():
            m = re.search(r"inet (\d+\.\d+\.\d+\.\d+)/", l)
            if m:
                ip = m.group(1); break
    except Exception:
        pass
    # Primary share for the UNC hint (prefer "share" or "roms", else first)
    primary = next((s for s in shares if s["name"] in ("share", "roms")), shares[0] if shares else None)
    share_name = primary["name"] if primary else "share"
    share_path = primary["path"] if primary else "/userdata"
    unc = "\\\\%s\\%s" % (hostname, share_name)
    slirp = _is_slirp_vm()
    out = {"ok": True, "enabled": enabled, "host": hostname, "ip": ip,
           "share_name": share_name, "share_path": share_path, "unc": unc,
           "shares": shares, "slirp_vm": slirp,
           "exposure_warning": _SMB_EXPOSURE_WARNING}
    return out

def security_smb(payload):
    """POST /security/smb {action: "enable"|"disable"|"state"}.
    SMB is OFF by default (docs/31 SB-2). Enable/disable writes batocera.conf atomically
    and starts/stops smbd+nmbd immediately. The UI surfaces an honest exposure warning.
    No owner-gate: SMB is less severe than remote-root SSH (no credential required, the
    share is guest-accessible), but the page warns explicitly before enabling."""
    p = payload or {}
    action = (p.get("action") or "state").lower()
    if action == "state":
        return security_smb_state()
    if action not in ("enable", "disable"):
        return {"ok": False, "error": "action must be enable|disable|state"}
    on = (action == "enable")
    try:
        _bconf_set("system.samba.enabled", "1" if on else "0")
    except Exception as e:
        LOG.warning("smb conf write failed: %s", e)
    try:
        if on:
            subprocess.run(["/bin/sh", "-c",
                "for s in /etc/init.d/S91smb; do [ -x \"$s\" ] && \"$s\" start; done; true"],
                capture_output=True, text=True, timeout=20)
        else:
            # kill smbd/nmbd directly (same as the init stop verb)
            subprocess.run(["/bin/sh", "-c",
                "kill -9 $(pidof smbd) 2>/dev/null; kill -9 $(pidof nmbd) 2>/dev/null; true"],
                capture_output=True, text=True, timeout=10)
    except Exception as e:
        return {"ok": False, "error": str(e)}
    LOG.info("smb %s (security settings)", "ENABLED" if on else "disabled")
    st = security_smb_state()
    return st

# ---- Network Activity Monitor (Task #84, docs/31) ----------------------------------
# Lightweight "what is my device talking to" view — complementary to the #83 audit
# (which explains what's exposed) and to #82 Wireshark (which is the deep tool).
# This is visibility/monitoring only, NOT a full IDS.
#
# GET /net/connections:
#   {ok, generated_at, slirp_vm, connections:[...], listeners:[...], note}
#
# connections[]:
#   {local, remote, state, process, hostname, verdict, verdict_reason}
# listeners[]:
#   {local_addr, local_port, process, verdict, verdict_reason}
#
# Verdicts:
#   "loopback"   — bound to 127.x / ::1 (not LAN-reachable)
#   "gose-stack" — known GOSE service port (8780/8731)
#   "slirp-gw"   — remote is 10.0.2.x (SLIRP gateway/DNS, not the real internet)
#   "external"   — routable non-loopback remote (on real hardware = actual internet)
#   "exposed"    — listener on 0.0.0.0 / :: (reachable on real LAN)
#   "ok"         — listener on loopback or gose-internal
#
# Reverse DNS: best-effort, per-IP, max 1.5 s timeout, results cached for the
# life of one /net/connections call (avoids hanging the server on DNS misses).
# Process name: from ss -tunp output; cmdline is NOT exposed (could carry tokens).
#
# Security: local network metadata only — connection tuples + port + process name.
# No cmdlines, no environment, no file paths. Scrubbed at parse time.

_NETMON_KNOWN_PORTS = {
    8780: ("GOSE UI Server", "gose-stack"),
    8731: ("GOSE Agent", "gose-stack"),
    22:   ("SSH (dropbear)", "ssh"),
    5357: ("WSD/WSDD discovery", "discovery"),
    111:  ("rpcbind", "nfs-rpc"),
    2049: ("NFS server", "nfs-rpc"),
}
_NETMON_NFS_RPC_RANGE = range(32768, 65536)   # dynamic rpc ports fall here
_NETMON_KNOWN_PROCS = {"connmand", "dropbear", "rpcbind", "rpc.mountd",
                        "rpc.statd", "smbd", "nmbd", "python3", "WebKitNetworkPr"}

def _netmon_rdns(ip, cache, timeout=1.5):
    """Reverse-DNS lookup for a single IP.  Returns hostname or None.
    Cached per call. Never raises."""
    if ip in cache:
        return cache[ip]
    # skip loopback / APIPA — no useful PTR exists
    if ip.startswith("127.") or ip == "::1" or ip.startswith("169.254."):
        cache[ip] = None; return None
    result = [None]
    def _work():
        try:
            name = socket.gethostbyaddr(ip)[0]
            if name and name != ip:
                result[0] = name
        except Exception:
            pass
    t = threading.Thread(target=_work, daemon=True)
    t.start(); t.join(timeout)
    cache[ip] = result[0]
    return result[0]

def _netmon_conn_verdict(local_ip, local_port, remote_ip, remote_port):
    """Classify one active connection."""
    # loopback both ends → internal
    def _is_lo(ip): return ip.startswith("127.") or ip == "::1"
    if _is_lo(local_ip) and _is_lo(remote_ip):
        return "loopback", "both ends loopback"
    # SLIRP gateway / DNS (10.0.2.x)
    if remote_ip.startswith("10.0.2."):
        svc = "SLIRP gateway" if remote_ip == "10.0.2.2" else \
              "SLIRP DNS"     if remote_ip == "10.0.2.3" else "SLIRP network"
        return "slirp-gw", svc
    # known GOSE stack port
    if local_port in _NETMON_KNOWN_PORTS:
        return "gose-stack", _NETMON_KNOWN_PORTS[local_port][0]
    if remote_port in _NETMON_KNOWN_PORTS:
        return "gose-stack", _NETMON_KNOWN_PORTS[remote_port][0]
    # remote is routable non-loopback
    return "external", "non-loopback remote"

def _netmon_listener_verdict(addr, port):
    """Classify one listening socket."""
    loopback = (addr in ("127.0.0.1", "::1", "127.0.0.1"))
    if addr == "127.0.0.1" or addr == "::1":
        if port in _NETMON_KNOWN_PORTS:
            return "gose-stack", _NETMON_KNOWN_PORTS[port][0] + " (loopback)"
        return "ok", "loopback only — not LAN-reachable"
    # interface-bound (not 0.0.0.0 or ::) — limited exposure
    if addr not in ("0.0.0.0", "::"):
        if port in _NETMON_KNOWN_PORTS:
            lbl = _NETMON_KNOWN_PORTS[port][0]
            return "gose-stack", lbl + " (interface-bound)"
        return "ok", "interface-bound (%s)" % addr
    # 0.0.0.0 / :: — exposed on all interfaces (critical on real hardware)
    if port in _NETMON_KNOWN_PORTS:
        lbl, cat = _NETMON_KNOWN_PORTS[port]
        return "exposed", "%s — 0.0.0.0 (exposed on real LAN; SLIRP-contained in dev VM)" % lbl
    # dynamic NFS RPC ports
    if port in _NETMON_NFS_RPC_RANGE:
        return "exposed", "NFS RPC (dynamic port) — 0.0.0.0 (exposed on real LAN)"
    return "exposed", "0.0.0.0 — exposed on real LAN (SLIRP-contained in dev VM)"

def _netmon_parse_ss(raw):
    """Parse 'ss -tunp' output into a list of connection dicts.
    Only established/close-wait TCP + established UDP."""
    conns = []
    for line in raw.splitlines():
        parts = line.split()
        if len(parts) < 5: continue
        netid, state = parts[0], parts[1]
        if netid not in ("tcp", "udp"): continue
        # only meaningful states
        if state not in ("ESTAB", "CLOSE-WAIT", "SYN-SENT", "SYN-RECV"): continue
        # columns: netid state recv-q send-q local peer [process]
        local_raw = parts[4] if len(parts) > 4 else ""
        peer_raw  = parts[5] if len(parts) > 5 else ""
        proc_raw  = parts[6] if len(parts) > 6 else ""

        def _split_addr(s):
            # handle [ipv6]:port and ipv4:port and addr%iface:port
            s = re.sub(r'%[a-zA-Z0-9]+:', ':', s)  # strip interface suffix from addr
            if s.startswith('['):
                m = re.match(r'\[([^\]]+)\]:(\d+)', s)
                return (m.group(1), int(m.group(2))) if m else (s, 0)
            parts2 = s.rsplit(':', 1)
            try: return (parts2[0], int(parts2[1]))
            except Exception: return (s, 0)

        local_ip, local_port = _split_addr(local_raw)
        remote_ip, remote_port = _split_addr(peer_raw)

        # process name — extract from users:(("name",...)) ; never expose args/cmdline
        proc = ""
        m = re.search(r'users:\(\("([^"]{1,32})"', proc_raw)
        if m: proc = m.group(1)

        verdict, reason = _netmon_conn_verdict(local_ip, local_port, remote_ip, remote_port)
        conns.append({
            "local": "%s:%s" % (local_ip, local_port),
            "remote": "%s:%s" % (remote_ip, remote_port),
            "proto": netid, "state": state,
            "process": proc,
            "verdict": verdict, "verdict_reason": reason,
            "hostname": None,   # filled in by caller after rdns
        })
    return conns

def _netmon_parse_listeners(raw):
    """Parse 'ss -tlnp' (TCP listeners) into list of listener dicts."""
    listeners = []
    for line in raw.splitlines():
        parts = line.split()
        if len(parts) < 5: continue
        state = parts[0]
        if state != "LISTEN": continue
        local_raw = parts[3] if len(parts) > 3 else ""
        proc_raw  = parts[5] if len(parts) > 5 else ""

        def _split_addr(s):
            s = re.sub(r'%[a-zA-Z0-9]+:', ':', s)
            if s.startswith('['):
                m = re.match(r'\[([^\]]+)\]:(\d+)', s)
                return (m.group(1), int(m.group(2))) if m else (s, 0)
            p2 = s.rsplit(':', 1)
            try: return (p2[0], int(p2[1]))
            except Exception: return (s, 0)

        addr, port = _split_addr(local_raw)
        proc = ""
        m = re.search(r'users:\(\("([^"]{1,32})"', proc_raw)
        if m: proc = m.group(1)

        verdict, reason = _netmon_listener_verdict(addr, port)
        listeners.append({
            "local_addr": addr, "local_port": port,
            "process": proc,
            "verdict": verdict, "verdict_reason": reason,
        })
    return listeners

def net_connections():
    """GET /net/connections — active connections + listeners with verdicts.
    Calls ss with a 6-second timeout; returns a fallback payload on failure.
    Reverse DNS is attempted per unique remote IP, max 1.5s each, cached."""
    try:
        r1 = subprocess.run(["/bin/sh", "-c", "ss -tunp 2>/dev/null"],
                            capture_output=True, text=True, timeout=8)
        r2 = subprocess.run(["/bin/sh", "-c", "ss -tlnp 2>/dev/null"],
                            capture_output=True, text=True, timeout=8)
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "ss timed out (>8 s) — try again", "connections": [], "listeners": []}
    except Exception as e:
        return {"ok": False, "error": "ss unavailable: %s" % e, "connections": [], "listeners": []}

    conns = _netmon_parse_ss(r1.stdout or "")
    listeners = _netmon_parse_listeners(r2.stdout or "")

    # Reverse DNS: collect unique non-trivial remote IPs, resolve in parallel threads
    rdns_cache = {}
    unique_remotes = set()
    for c in conns:
        ip = c["remote"].rsplit(":", 1)[0]
        if ip and not ip.startswith("127.") and ip != "::1" and ip != "*":
            unique_remotes.add(ip)

    if unique_remotes:
        threads = []
        for ip in unique_remotes:
            t = threading.Thread(target=_netmon_rdns, args=(ip, rdns_cache, 1.5), daemon=True)
            t.start(); threads.append(t)
        for t in threads:
            t.join(2.0)   # outer cap: all threads together get 2 s
        for c in conns:
            ip = c["remote"].rsplit(":", 1)[0]
            c["hostname"] = rdns_cache.get(ip)

    slirp = _is_slirp_vm()
    return {
        "ok": True,
        "generated_at": int(time.time()),
        "slirp_vm": slirp,
        "connections": conns,
        "listeners": listeners,
        "note": (
            "Dev VM (SLIRP): all '0.0.0.0' listeners are isolated by the hypervisor — "
            "only ports 8780/8731/22 are host-forwarded (loopback only). "
            "On real hardware every 'exposed' listener is reachable on your Wi-Fi."
        ) if slirp else (
            "Running on real hardware. 'exposed' listeners are reachable on your network."
        ),
    }

def sys_display(mode=None):
    # Real guest video mode via xrandr on :0 (virtio-vga). GET lists what the panel
    # supports + which is live; POST switches. Bad/unsupported modes are refused.
    if mode is not None:
        if not re.match(r"^\d{3,4}x\d{3,4}$", str(mode)):
            return {"ok": False, "error": "mode must look like 1920x1080"}
        r = subprocess.run(["/bin/sh", "-c", "DISPLAY=:0 xrandr -s " + str(mode)],
                           capture_output=True, text=True, timeout=10)
        if r.returncode != 0:
            return {"ok": False, "error": (r.stderr or "xrandr failed").strip()[:200]}
        LOG.info("display mode set: %s", mode)
    out = subprocess.run(["/bin/sh", "-c", "DISPLAY=:0 xrandr"],
                         capture_output=True, text=True, timeout=10).stdout
    cur, modes = None, []
    for line in out.splitlines():
        m = re.match(r"^\s+(\d{3,4}x\d{3,4})\s", line)
        if m:
            if m.group(1) not in modes:
                modes.append(m.group(1))
            if "*" in line:
                cur = m.group(1)
    return {"ok": True, "mode": cur, "modes": modes[:16]}

def sys_vsync(on=None):
    # RetroArch vsync via batocera.conf's raw-retroarch override key. RA's default is ON,
    # so an absent key reads as on (explicit=False says we haven't pinned it).
    if on is not None:
        _bconf_set("global.retroarch.video_vsync", "true" if on else "false")
        LOG.info("vsync set: %s", bool(on))
    v = _bconf_get("global.retroarch.video_vsync")
    return {"ok": True, "on": (v is None) or (v == "true"), "explicit": v is not None}

def sys_timezone(tz=None):
    # System timezone via batocera.conf (applied by Batocera at boot). The page clocks
    # apply the same IANA id live via localStorage gose-tz; this makes the OS side match.
    if tz is not None:
        if not re.match(r"^[A-Za-z][\w+\-/]{1,48}$", str(tz)):
            return {"ok": False, "error": "bad timezone id"}
        _bconf_set("system.timezone", str(tz))
        LOG.info("timezone set: %s", tz)
    return {"ok": True, "timezone": _bconf_get("system.timezone")}

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
    # Settings > AI & Remote > "Remote agent control" — the global gate (default: enabled)
    if _ui_prefs_load().get("gose-ai-remote") == "off":
        LOG.info("AI join refused (remote agent control disabled in Settings): %s", name)
        return {"ok": False, "error": "remote agent control is disabled in Settings > AI & Remote"}
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
    # Same name rules as ai_request — the hub/OOBE pair flow takes free text, so a junk
    # name must be refused here, not rendered forever in the roster.
    name = (payload.get("name") or "").strip()[:32]
    tier = payload.get("tier")
    if not name or not re.match(r"^[\w][\w .\-]*$", name):
        return {"ok": False, "error": "name required (letters/digits/space/.-_, max 32)"}
    if tier not in AI_TIERS:
        return {"ok": False, "error": "tier must be one of %s" % AI_TIERS}
    with _AI_LOCK:
        g = _ai_grants_load()
        prev = g.get(name, {})
        if tier == "observe":
            if payload.get("pair") or prev:
                # Paired roster entry at the safe floor tier. Two ways here: OOBE/Hub first
                # pairing (pair flag), or the owner DOWNGRADING an existing agent to Observe —
                # both keep the entry + its stable token so the AI stays identifiable in the
                # Hub and can be re-elevated later. It never self-elevates (docs/16).
                g[name] = {"tier": "observe", "granted_at": int(time.time()), "expires": None,
                           "seat": None,
                           "paired_via": prev.get("paired_via") or payload.get("via", "oobe"),
                           "token": prev.get("token") or secrets.token_hex(16)}
            else:
                g.pop(name, None)     # never-paired observe — nothing to keep (== no grant)
        else:
            days = payload.get("expires_days")    # None/0 = permanent until revoked (the default)
            # optional controller seat (1-4) — pins the AI to it; ABSENT key = keep the
            # current seat (so a tier-only change can't silently unpin a seated AI)
            seat = payload.get("seat") if "seat" in payload else prev.get("seat")
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
            if prev.get("paired_via"):
                g[name]["paired_via"] = prev["paired_via"]
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

# ---- AI activity feed: the public-facing "your AI did X at Y" endpoint consumed by
#      gose-ai-about.html. Newest-first (reversed from the append-order audit log).
#      SCRUBBED: only {ts, name, op, ok, code} pass through — no tokens, no secrets.
#      Audit entries never contain tokens by design (server.py audit_append), but the
#      scrub is explicit and defensive so this contract holds even if the schema grows.
_ACTIVITY_SAFE_KEYS = frozenset(("ts", "name", "op", "ok", "code"))

def ai_activity(limit=50):
    try:
        limit = max(1, min(int(limit), 200))
    except (TypeError, ValueError):
        limit = 50
    raw = ai_audit(limit)
    entries = raw.get("entries", [])
    # scrub: keep only the declared safe keys, strip everything else
    scrubbed = [{k: e[k] for k in _ACTIVITY_SAFE_KEYS if k in e} for e in entries]
    scrubbed.reverse()                                # newest first
    return {"ok": True, "entries": scrubbed}

# ---- First-boot / OOBE (docs/25) -------------------------------------------------------
# A flag file decides whether the kiosk lands on the first-boot wizard or the desktop.
# Completing the wizard WRITES the flag, persists the owner account, applies the privacy
# defaults (opt-IN only — docs/24), and optionally issues the first AI pairing token.
# Reset = remove the flag (also done by factory reset) -> next boot re-runs the wizard.
OOBE_DONE_FLAG = "/userdata/system/gose/.oobe-done"
# env override = test seam: PIN/account flows get verified against a sandbox accounts file
# on an isolated instance (GOSE_UI_PORT) without ever touching the owner's real record.
ACCOUNTS_F = os.environ.get("GOSE_ACCOUNTS_FILE") or "/userdata/system/gose/accounts.json"

def _accounts_load():
    try:
        return json.load(open(ACCOUNTS_F))
    except Exception:
        return {"users": []}

def _owner_record(acc=None):
    acc = acc if acc is not None else _accounts_load()
    return next((u for u in acc.get("users", []) if u.get("role") == "owner"), None)

def oobe_status():
    done = os.path.exists(OOBE_DONE_FLAG)
    info = {}
    if done:
        try:
            info = json.load(open(OOBE_DONE_FLAG)) or {}
        except Exception:
            info = {}
    raw = _owner_record()
    owner = None
    if raw:
        # never serve the PIN salt/hash to pages — expose only the booleans the lock
        # screen needs (has_pin = a PIN was chosen; pin_set = a verifiable hash exists)
        owner = {k: v for k, v in raw.items() if not k.startswith("pin_")}
        owner["pin_set"] = bool(raw.get("pin_hash"))
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
    # The owner account = the canonical account store the lock screen reads later. PINs are
    # stored as salted scrypt hashes (never cleartext). Today's wizard sends only has_pin
    # (the raw PIN never leaves its page), so a has_pin-without-hash account finishes PIN
    # setup at the lock screen's first unlock (the migration path, /auth/pin/set). If a
    # future wizard sends account.pin, it is hashed right here and the lock asks from day 1.
    users = [{"username": username, "display": display, "role": "owner",
              "accent": acct.get("accent") or "#5cd0ff",
              "has_password": bool(acct.get("has_password")), "has_pin": bool(acct.get("has_pin")),
              "created_at": int(time.time())}]
    raw_pin = str(acct.get("pin") or "")
    if PIN_RE.match(raw_pin):
        salt = secrets.token_hex(16)
        users[0].update({"pin_salt": salt, "pin_hash": _pin_compute(raw_pin, salt),
                         "pin_algo": PIN_ALGO, "has_pin": True})
    write_json_atomic(ACCOUNTS_F, {"users": users,
                                   "device_name": (p.get("device_name") or "GOSE").strip()[:48],
                                   "locale": p.get("locale"), "keyboard": p.get("keyboard"),
                                   "timezone": p.get("timezone"), "theme": p.get("theme")})
    # seed the CANONICAL UI-prefs store: the wizard's personalize step (theme + "your
    # color" accent) must show OS-wide, on every page, surviving kiosk reloads
    try:
        seed = {}
        if p.get("theme"):
            seed["gose-theme"] = str(p["theme"])[:24]
        if acct.get("accent"):
            seed["gose-accent"] = str(acct["accent"])[:16]
        if p.get("timezone"):
            seed["gose-tz"] = str(p["timezone"])[:48]
        if seed:
            ui_prefs_set({"set": seed})
    except Exception as e:
        LOG.warning("oobe ui_prefs seed failed: %s", e)
    _apply_oobe_privacy(p.get("privacy") or {})
    # AI pairing — the step pairs each named agent live (so the token is shown once, at
    # pairing); completion re-issues every grant idempotently (pair keeps the stable token)
    # so a completed wizard always ends with its agents granted even if a live call dropped.
    # Back-compat: "ai" = the legacy single {name}; "ais" = the multi-pair list [{name},…].
    first = None
    paired_names = []
    seen = set()
    for a in [p.get("ai") or {}] + [x for x in (p.get("ais") or []) if isinstance(x, dict)]:
        nm = (a.get("name") or "").strip()[:32]
        if not nm or nm in seen:
            continue
        seen.add(nm)
        r = ai_grant({"name": nm, "tier": "observe", "pair": True, "via": "oobe"})
        if r.get("ok"):
            paired_names.append(r.get("name") or nm)
            if first is None:
                first = r
    info = {"completed_at": int(time.time()), "owner": username}
    try:
        os.makedirs(os.path.dirname(OOBE_DONE_FLAG), exist_ok=True)
        write_json_atomic(OOBE_DONE_FLAG, info)
    except Exception as e:
        return {"ok": False, "error": "could not write first-boot flag: %s" % e}
    LOG.info("OOBE complete: owner=%s device=%s ai=%s", username, p.get("device_name"),
             ", ".join(paired_names) or "(none)")
    return {"ok": True, "owner": username, "ai_paired": bool(paired_names),
            "ai_name": paired_names[0] if paired_names else None,
            "ai_token": (first or {}).get("token"),
            "ai_paired_names": paired_names}

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

# ---- PIN auth (the lock screen; docs/24 §1.5) --------------------------------------------
# HONEST SCOPE: this is a CONVENIENCE LOCK, not encryption. The PIN gates the lock-screen
# UI only — the disk is not encrypted and anyone with SSH/shell access can edit
# accounts.json (that is also the documented recovery path for a forgotten PIN: delete the
# pin_* keys from the owner record and the lock screen re-runs PIN setup).
# Storage: per-account random salt + scrypt hash in the owner's accounts.json record
# (pin_salt / pin_hash / pin_algo / pin_len). Brute force: 5 consecutive misses lock
# verification for 30 s (in-memory — a server restart clears it, which is fine for a
# convenience lock and means the lockout can never brick the lock screen permanently).
# Credential policy (Zeke, 2026-06-08): NEW PINs must be exactly 8 digits.
# PIN_RE enforces this on set. Existing (grandfathered) PINs of any digit length
# are still VERIFIED so existing owners are never locked out — PIN_VERIFY_RE accepts
# 4-8 digits for the verify path only (the migration path prompts upgrade on next change).
PIN_RE        = re.compile(r"^\d{8}$")      # set/change: must be exactly 8 digits
PIN_VERIFY_RE = re.compile(r"^\d{4,8}$")   # verify: accept any stored-length PIN (grandfather)
PIN_ALGO = "scrypt-16384-8-1"
PIN_MAX_TRIES = 5
PIN_LOCKOUT_S = 30
_PIN_GUARD = threading.Lock()
_PIN_FAILS = {"n": 0, "until": 0.0}

def _pin_compute(pin, salt_hex):
    import hashlib
    return hashlib.scrypt(pin.encode("utf-8"), salt=bytes.fromhex(salt_hex),
                          n=16384, r=8, p=1, dklen=32).hex()

def _pin_locked_for():
    return max(0.0, _PIN_FAILS["until"] - time.time())

def pin_status():
    o = _owner_record()
    with _PIN_GUARD:
        lf = _pin_locked_for()
        left = 0 if lf > 0 else max(0, PIN_MAX_TRIES - _PIN_FAILS["n"])
    out = {"ok": True, "enabled": bool(o and o.get("has_pin")),
           "set": bool(o and o.get("pin_hash") and o.get("pin_salt")),
           "locked_for": round(lf, 1), "tries_left": left}
    if out["set"] and o.get("pin_len"):
        out["pin_len"] = o["pin_len"]   # lets the lock pad auto-submit at the right length
    return out

def pin_verify(payload):
    """POST /auth/pin {pin} -> {ok, valid, tries_left?, locked_for?}. Constant-time compare;
    consecutive misses arm the lockout. A success resets the counter."""
    pin = str((payload or {}).get("pin") or "")
    o = _owner_record()
    if not (o and o.get("pin_hash") and o.get("pin_salt")):
        return {"ok": False, "error": "no PIN is set", "set": False}
    with _PIN_GUARD:
        lf = _pin_locked_for()
        if lf > 0:
            return {"ok": True, "valid": False, "locked_for": round(lf, 1), "tries_left": 0}
        valid = False
        if PIN_VERIFY_RE.match(pin):   # grandfather: accept any stored-length PIN for verify
            try:
                import hmac
                valid = hmac.compare_digest(_pin_compute(pin, o["pin_salt"]), o["pin_hash"])
            except Exception as e:
                LOG.error("pin verify failed: %s", e)
        if valid:
            _PIN_FAILS["n"] = 0
            _PIN_FAILS["until"] = 0.0
            LOG.info("PIN ok (owner %s)", o.get("username"))
            return {"ok": True, "valid": True}
        _PIN_FAILS["n"] += 1
        if _PIN_FAILS["n"] >= PIN_MAX_TRIES:
            _PIN_FAILS["n"] = 0
            _PIN_FAILS["until"] = time.time() + PIN_LOCKOUT_S
            LOG.warning("PIN lockout armed (%ss)", PIN_LOCKOUT_S)
            return {"ok": True, "valid": False, "locked_for": float(PIN_LOCKOUT_S), "tries_left": 0}
        LOG.warning("PIN wrong (%d/%d)", _PIN_FAILS["n"], PIN_MAX_TRIES)
        return {"ok": True, "valid": False, "tries_left": PIN_MAX_TRIES - _PIN_FAILS["n"]}

def pin_set(payload):
    """POST /auth/pin/set {pin, current?}. First set (no hash yet — fresh account or the
    has_pin-only migration) needs no current PIN: there is no secret to check against, and
    the alternative is locking the owner out of his own device. CHANGING an existing PIN
    requires the current one, verified through the same rate-limited path (so the change
    endpoint can't be used to brute-force either)."""
    p = payload or {}
    pin = str(p.get("pin") or "")
    if not PIN_RE.match(pin):
        return {"ok": False, "error": "PIN must be exactly 8 digits"}
    acc = _accounts_load()
    o = _owner_record(acc)
    if not o:
        return {"ok": False, "error": "no owner account yet — finish first-boot setup"}
    if o.get("pin_hash") and o.get("pin_salt"):
        cur = pin_verify({"pin": str(p.get("current") or "")})
        if not cur.get("valid"):
            out = {"ok": False, "error": "current PIN required to change the PIN"}
            for k in ("locked_for", "tries_left"):
                if k in cur:
                    out[k] = cur[k]
            return out
    salt = secrets.token_hex(16)
    o["pin_salt"] = salt
    o["pin_hash"] = _pin_compute(pin, salt)
    o["pin_algo"] = PIN_ALGO
    o["pin_len"] = len(pin)
    o["has_pin"] = True
    write_json_atomic(ACCOUNTS_F, acc)
    with _PIN_GUARD:
        _PIN_FAILS["n"] = 0
        _PIN_FAILS["until"] = 0.0
    LOG.info("PIN set for owner %s (len %d)", o.get("username"), len(pin))
    return {"ok": True, "set": True}

# ---- UI prefs — the CANONICAL personalization store (Settings overhaul, task 14) --------
# One server-side dict so theme/accent/etc survive kiosk reloads and EVERY page (incl.
# lock) reads the same values: assets/a11y.js GETs /ui/prefs on each page load, mirrors
# into localStorage (the per-page cache) and applies theme + accent live. Writers go
# through GOSE.prefs.set() -> POST /ui/prefs. The OOBE personalize step seeds theme +
# accent here at /oobe/complete, so the wizard's accent shows OS-wide (the acceptance
# test for this store). Keys are exactly the localStorage names Settings owns.
UI_PREFS_F = "/userdata/system/gose/ui_prefs.json"
_PREFS_LOCK = threading.Lock()
_PREF_KEY_RE = re.compile(
    r"^gose-(theme|accent|wp|live|glow|tz|clockfmt|signin|input|platform|sounds|ai-remote|"
    r"ui-scale|uiscale|contrast|bold|cb|cb-palette|motion|opaque|focus|hold-alt|snd-quiet|"
    r"snd-(?:vol|mute)-(?:system|notify|battery|ui))$")
# NEVER server-synced (deliberately outside the whitelist above, and stripped on load in
# case a stale/hand-edited prefs file carries them): these localStorage keys are LIVE
# page-side state — gose-wenabled (widget toggles, docs/23 §4.5) applies via storage
# events the moment it changes; a server echo through a11y.js's mirror would overwrite
# live toggles with a stale copy. Same for widget placement/descriptors.
_PREF_NEVER_SYNC = {"gose-wenabled", "gose-wpos", "gose-wdesc"}

def _ui_prefs_load():
    try:
        d = json.load(open(UI_PREFS_F))
        if not isinstance(d, dict):
            return {}
        for k in _PREF_NEVER_SYNC:
            d.pop(k, None)
        return d
    except Exception:
        return {}

def ui_prefs_get():
    p = _ui_prefs_load()
    # pre-store installs: derive theme/accent from the OOBE owner record so a wizard
    # finished before this store existed still personalizes the whole OS
    if "gose-accent" not in p or "gose-theme" not in p:
        acc = _accounts_load()
        owner = next((u for u in acc.get("users", []) if u.get("role") == "owner"), None)
        if owner and owner.get("accent") and "gose-accent" not in p:
            p["gose-accent"] = owner["accent"]
        if acc.get("theme") and "gose-theme" not in p:
            p["gose-theme"] = acc["theme"]
    return {"ok": True, "prefs": p}

def ui_prefs_set(payload):
    if (payload or {}).get("reset"):
        with _PREFS_LOCK:
            try:
                if os.path.exists(UI_PREFS_F):
                    os.remove(UI_PREFS_F)
            except Exception as e:
                return {"ok": False, "error": str(e)}
        LOG.info("ui_prefs reset to defaults")
        return {"ok": True, "prefs": {}}
    m = (payload or {}).get("set")
    if not isinstance(m, dict) or not m:
        return {"ok": False, "error": "set must be a non-empty object"}
    clean = {}
    for k, v in m.items():
        if isinstance(k, str) and k in _PREF_NEVER_SYNC:
            return {"ok": False,
                    "error": "%s is page-local live state — never server-synced (docs/23 §4.5)" % k}
        if not (isinstance(k, str) and _PREF_KEY_RE.match(k)):
            return {"ok": False, "error": "unknown pref key: %r" % (k,)}
        if v is None:
            clean[k] = None
            continue
        v = str(v)
        if len(v) > 64:
            return {"ok": False, "error": "value too long for %s" % k}
        clean[k] = v
    with _PREFS_LOCK:
        p = _ui_prefs_load()
        for k, v in clean.items():
            if v is None:
                p.pop(k, None)
            else:
                p[k] = v
        os.makedirs(os.path.dirname(UI_PREFS_F), exist_ok=True)
        write_json_atomic(UI_PREFS_F, p)
    LOG.info("ui_prefs set: %s", ", ".join("%s=%s" % kv for kv in sorted(clean.items())))
    return {"ok": True, "prefs": p}

# ---- Privacy controls (Settings > Privacy; opt-IN model per docs/24) --------------------
# privacy.json records the choices; the ones with a real backend APPLY here too:
#   * boxart_scrape  -> the SCRAPE_AUTO_FLAG that auto_scrape_boot actually reads
#   * screen_capture -> "never" gates /capture/shot, /capture/clip and the clip buffer
#     ("ask" behaves as "always" until an approval-prompt UI exists — labeled in the UI)
#   * diagnostics    -> recorded only; GOSE sends nothing today (labeled in the UI)
PRIVACY_F = "/userdata/system/gose/privacy.json"

def _privacy_load():
    try:
        d = json.load(open(PRIVACY_F))
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}

def privacy_get():
    p = _privacy_load()
    p.setdefault("boxart_scrape", os.path.exists(SCRAPE_AUTO_FLAG))  # the flag is the truth
    p.setdefault("screen_capture", "always")
    p.setdefault("diagnostics", False)
    return {"ok": True, "privacy": p}

def privacy_set(payload):
    payload = payload or {}
    p = _privacy_load()
    chg = {}
    if "boxart_scrape" in payload:
        p["boxart_scrape"] = chg["boxart_scrape"] = bool(payload["boxart_scrape"])
        _apply_oobe_privacy({"boxart_scrape": p["boxart_scrape"]})   # writes/removes the real flag
    if "screen_capture" in payload:
        v = payload["screen_capture"]
        if v not in ("ask", "always", "never"):
            return {"ok": False, "error": "screen_capture must be ask|always|never"}
        p["screen_capture"] = chg["screen_capture"] = v
    if "diagnostics" in payload:
        p["diagnostics"] = chg["diagnostics"] = bool(payload["diagnostics"])
    if not chg:
        return {"ok": False, "error": "nothing to set"}
    os.makedirs(os.path.dirname(PRIVACY_F), exist_ok=True)
    write_json_atomic(PRIVACY_F, p)
    LOG.info("privacy set: %s", chg)
    return {"ok": True, "privacy": p}

def _capture_allowed():
    return _privacy_load().get("screen_capture", "always") != "never"

# ===== Notifications center (task #22) ============================================
# Server-backed notification history that other surfaces feed (achievements #33,
# copilot #42, low-battery, downloads). Canonical store under the OS-protected prefix;
# capped + rotated; atomic writes; thread-safe. The desktop GETs this for the bell /
# center and POSTs new ones. Auto-DND ("no toast while a game runs") is enforced
# CLIENT-side so the history here ALWAYS records — the store never drops a notification.
NOTIF_F = "/userdata/system/gose/notifications.json"
NOTIF_CAP = 200                       # keep the newest N; older entries roll off (no unbounded growth)
_NOTIF_LOCK = threading.Lock()
_NOTIF_KINDS = ("info", "success", "warning", "error", "system")

def _notif_load():
    try:
        d = json.load(open(NOTIF_F))
    except Exception:
        d = None
    items = d.get("items") if isinstance(d, dict) else None
    return items if isinstance(items, list) else []

def _notif_save(items):
    os.makedirs(os.path.dirname(NOTIF_F), exist_ok=True)
    write_json_atomic(NOTIF_F, {"items": items[:NOTIF_CAP]})     # atomic + capped

def _notif_unread(items):
    return sum(1 for n in items if isinstance(n, dict) and not n.get("read"))

def notifications_get():
    with _NOTIF_LOCK:
        items = _notif_load()
    return {"ok": True, "items": items, "unread": _notif_unread(items)}

def notifications_post(payload):
    payload = payload or {}
    title = ("" if payload.get("title") is None else str(payload.get("title"))).strip()
    body = ("" if payload.get("body") is None else str(payload.get("body"))).strip()
    if not title and not body:
        return {"ok": False, "error": "title or body required"}
    kind = str(payload.get("kind") or "info").lower()
    if kind not in _NOTIF_KINDS:
        kind = "info"
    icon = payload.get("icon")
    try:
        ts = float(payload.get("ts"))
    except (TypeError, ValueError):
        ts = time.time()
    rec = {"id": secrets.token_hex(6), "title": title[:200], "body": body[:1000],
           "kind": kind, "icon": (str(icon)[:40] if icon else None),
           "read": False, "ts": ts}
    with _NOTIF_LOCK:
        items = _notif_load()
        items.insert(0, rec)
        _notif_save(items)
        unread = _notif_unread(items[:NOTIF_CAP])
    return {"ok": True, "notification": rec, "unread": unread}

def notifications_read(payload):
    payload = payload or {}
    ids = payload.get("ids")
    if not isinstance(ids, list):
        ids = [payload["id"]] if payload.get("id") else []
    ids = set(str(x) for x in ids)
    do_all = bool(payload.get("all"))
    with _NOTIF_LOCK:
        items = _notif_load()
        for n in items:
            if isinstance(n, dict) and (do_all or n.get("id") in ids):
                n["read"] = True
        _notif_save(items)
        items = items[:NOTIF_CAP]
        unread = _notif_unread(items)
    # return the canonical list so the client can render straight from the mutation response
    return {"ok": True, "items": items, "unread": unread}

def notifications_clear(payload):
    payload = payload or {}
    with _NOTIF_LOCK:
        if payload.get("id"):
            keep = [n for n in _notif_load()
                    if isinstance(n, dict) and n.get("id") != str(payload["id"])]
        else:
            keep = []
        _notif_save(keep)
        keep = keep[:NOTIF_CAP]
    # return the canonical list so the client renders straight from the mutation response
    return {"ok": True, "items": keep, "unread": _notif_unread(keep), "count": len(keep)}

# ---- Screenshot (works anywhere, incl. GL games — frame comes from the host) ----
def capture_shot(payload):
    import time as _t
    if not _capture_allowed():
        return {"ok": False, "error": "screen capture is set to Never in Settings > Privacy"}
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
    if not _capture_allowed():
        return {"ok": False, "error": "screen capture is set to Never in Settings > Privacy"}
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

# #37 time-control mirror: RetroArch NCI is fire-and-forget and reports neither the
# active save slot nor the fast-forward state back, so the Game Bar (the pad-first
# driver of these) tracks them here. Reset to RetroArch's launch defaults — slot 0,
# FF off — in launch_game (a fresh emulatorlauncher starts at slot 0, normal speed).
_TIMECTL = {"slot": 0, "ff": False}

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
    if action == "save":
        # #37 NCI can't report the active slot, but a SAVE writes <game>.state[N] on disk — read
        # the newest one to SELF-CORRECT the tracked slot. RetroArch's savestate_auto_index moves
        # the launch slot unpredictably, so disk is the only honest source of truth for "current".
        try:
            time.sleep(0.4)
            sl = game_state_slots("", "").get("slots", [])
            if sl:
                _TIMECTL["slot"] = max(sl, key=lambda s: s["mtime"])["slot"]
        except Exception:
            pass
    return {"ok": True, "action": action, "slot": _TIMECTL["slot"]}

def game_slot(direction):
    # #37 step the active save slot (RetroArch shows the slot # on-screen). HONEST FALLBACK:
    # NCI has no absolute "set slot N" verb and no "get slot" verb — only STATE_SLOT_PLUS/MINUS —
    # so next/prev stepping is the reliable primitive (same as RetroArch's own F6/F7). The tracked
    # number is a best-effort estimate that self-corrects on the next save (savestate_auto_index
    # can shift the real launch slot). Step through 0-9 to pick any slot.
    cmd = {"next": "STATE_SLOT_PLUS", "prev": "STATE_SLOT_MINUS"}.get(direction)
    if not cmd:
        return {"ok": False, "error": "bad direction"}
    _nci(cmd); _resume_game()
    _TIMECTL["slot"] = max(0, min(9, _TIMECTL["slot"] + (1 if direction == "next" else -1)))
    return {"ok": True, "direction": direction, "slot": _TIMECTL["slot"]}

def game_ff(on=None):
    # #37 fast-forward. RetroArch NCI FAST_FORWARD is a TOGGLE (no absolute on/off verb), so we
    # mirror the resulting state and only send a packet when the target differs from tracked.
    target = (not _TIMECTL["ff"]) if on is None else (on in (True, 1, "1", "true", "on"))
    if target != _TIMECTL["ff"]:
        _nci("FAST_FORWARD"); _resume_game()
        _TIMECTL["ff"] = target
    return {"ok": True, "on": _TIMECTL["ff"]}

def game_rewind(on=None, system=None, game=None):
    # #37 per-game rewind ENABLE flag. configgen maps batocera `<system>["<rom>"].rewind`=1 →
    # RetroArch rewind_enable=true. Rewind allocates a state buffer at core load, so it CANNOT
    # hot-swap — HONEST FALLBACK: this applies on next launch. (In-game, hold the core's rewind
    # hotkey to actually scrub back; NCI REWIND is a held action, not a one-shot bar toggle.)
    system, game = _cur_game(system, game)
    if not (system and game):
        return {"ok": False, "error": "no current game"}
    k = _gkey(system, game)
    if on is not None:
        _bconf_set(k + ".rewind", "1" if (on in (True, 1, "1", "true", "on")) else "0")
    enabled = (_bconf_get(k + ".rewind") or "0") == "1"
    return {"ok": True, "system": system, "game": game, "enabled": enabled,
            "note": "applies on next launch (the rewind buffer is allocated at core load)"}

def game_timectl():
    # #37 one read for the Game Bar's time controls: current slot, FF state, rewind-enable.
    rw = game_rewind()
    return {"ok": True, "slot": _TIMECTL["slot"], "max_slot": 9,
            "ff": _TIMECTL["ff"], "ff_supported": True,
            "rewind_enabled": bool(rw.get("enabled")), "rewind_note": rw.get("note", ""),
            "slot_note": "NCI has no set-slot/get-slot verb — ←→ steps (F6/F7); count self-corrects on save"}

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
    return {"ok": True, "system": system, "game": game, "slots": out,
            "current": _TIMECTL["slot"]}   # #37 highlight the active slot

# ===== Game-Bar quick controls (tasks 54/62/70/72) ============================================
# Surfaced on the Game Bar overlay, pad-driven. All per-game writes go to batocera.conf via the
# atomic _bconf_set (the same key shapes configgen reads); honest "next launch" where RetroArch
# can't hot-swap. These reuse the EXISTING Batocera machinery (shaderset / bezel / hud / cheats)
# rather than reinventing it (docs research: configgen Emulator.py + emulatorlauncher.py).

def _cur_game(system=None, game=None):
    """Resolve the current game for per-game Game-Bar ops. The bar SIGSTOPs the running
    game, so RetroArch NCI (GET_STATUS) won't reply — recent.json[0] (written by
    launch_game) is the reliable, correctly-keyed (rom-stem) source. NCI is a fallback."""
    if system and game:
        return system, game
    try:
        rec = json.load(open(RECENT_F))
        if rec and rec[0].get("system") and rec[0].get("game"):
            return rec[0]["system"], rec[0]["game"]
    except Exception:
        pass
    gr = game_running()
    return (system or gr.get("system") or ""), (game or gr.get("game") or "")

def _bconf_resolve(k_game, k_sys, k_glob):
    """Most-specific-wins read across the three batocera.conf scopes configgen merges."""
    for key, src in ((k_game, "game"), (k_sys, "system"), (k_glob, "global")):
        v = _bconf_get(key)
        if v is not None:
            return v, src
    return None, "default"

# ---- #70 shaders + bezel (per-game). configgen reads <system>["<rom>"].shaderset and .bezel. ----
SYS_SHADERS_DIR = "/usr/share/batocera/shaders/configs"
USER_SHADERS_DIR = "/userdata/shaders/configs"
_SHADER_LABELS = {"none": "None", "scanlines": "Scanlines", "retro": "CRT",
                  "curvature": "CRT curved", "zfast": "CRT fast",
                  "sharp-bilinear-simple": "Sharp", "enhanced": "Enhanced",
                  "flatten-glow": "Glow", "mega-bezel": "Mega-Bezel",
                  "mega-bezel-lite": "Mega-Bezel lite", "mega-bezel-ultralite": "Mega-Bezel ulite"}

def _shadersets():
    # The ONLY valid shaderset values are the configs/<name>/ dirs that ship (plus user ones)
    # and "none" — never fabricated names. configgen falls back if the dir is missing.
    out = ["none"]
    for d in (SYS_SHADERS_DIR, USER_SHADERS_DIR):
        try:
            for n in sorted(os.listdir(d)):
                if not n.startswith(".") and os.path.isdir(os.path.join(d, n)) and n not in out:
                    out.append(n)
        except Exception:
            pass
    return out

def game_shader(system=None, game=None):
    system, game = _cur_game(system, game)
    if not (system and game):
        return {"ok": False, "error": "no current game"}
    k = _gkey(system, game)
    ss, src = _bconf_resolve(k + ".shaderset", system + ".shaderset", "global.shaderset")
    if ss is None:
        ss = "none"
    bz, _bs = _bconf_resolve(k + ".bezel", system + ".bezel", "global.bezel")
    bezel_on = bz not in (None, "none", "", "0", "false")
    avail = [{"id": s, "label": _SHADER_LABELS.get(s, s)} for s in _shadersets()]
    return {"ok": True, "system": system, "game": game, "shaderset": ss, "source": src,
            "bezel": bezel_on, "available": avail,
            "note": "applies on next launch (RetroArch can't hot-swap a named shaderset)"}

def set_game_shader(payload):
    system, game = _cur_game((payload or {}).get("system"), (payload or {}).get("game"))
    if not (system and game):
        return {"ok": False, "error": "no current game"}
    k = _gkey(system, game)
    out = {"ok": True, "system": system, "game": game}
    if payload.get("shaderset") is not None:
        ss = str(payload["shaderset"])
        if ss != "none" and ss not in _shadersets():
            return {"ok": False, "error": "unknown shaderset: " + ss}
        _bconf_set(k + ".shaderset", ss)
        out["shaderset"] = ss
    if payload.get("bezel") is not None:
        on = payload["bezel"] in (True, 1, "1", "true", "on")
        _bconf_set(k + ".bezel", "default" if on else "none")   # "default" = the bundled decoration
        out["bezel"] = on
    out["note"] = "applies on next launch"
    LOG.info("game shader set: %s shaderset=%s bezel=%s", k, out.get("shaderset"), out.get("bezel"))
    return out

# ---- #72 cheats (RetroArch cheat DB). cheat_database_path = /userdata/cheats/cht/<DB>/<game>.cht ----
CHEAT_DB = "/userdata/cheats/cht"

def _find_cht(game):
    # match <game>.cht (case-insensitive stem) across all cheat-DB subfolders. We never join the
    # game name into a path — we list dirs and compare stems — so there is no traversal surface.
    if not (game and os.path.isdir(CHEAT_DB)):
        return None
    target = os.path.splitext(str(game))[0].lower()
    try:
        dbs = sorted(os.listdir(CHEAT_DB))
    except Exception:
        return None
    for db in dbs:
        d = os.path.join(CHEAT_DB, db)
        if not os.path.isdir(d):
            continue
        try:
            for f in os.listdir(d):
                if f.lower().endswith(".cht") and os.path.splitext(f)[0].lower() == target:
                    return os.path.join(d, f)
        except Exception:
            pass
    return None

def _parse_cht(path):
    vals = {}
    try:
        for line in open(path, errors="replace"):
            m = re.match(r'\s*([A-Za-z0-9_]+)\s*=\s*(.*?)\s*$', line)
            if m:
                vals[m.group(1)] = m.group(2).strip().strip('"')
    except Exception:
        return []
    try:
        n = int(vals.get("cheats", "0") or "0")
    except ValueError:
        n = 0
    out = []
    for i in range(min(n, 512)):
        out.append({"i": i, "desc": vals.get("cheat%d_desc" % i, "Cheat %d" % i),
                    "enable": str(vals.get("cheat%d_enable" % i, "false")).lower() == "true"})
    return out

def game_cheats(system=None, game=None):
    system, game = _cur_game(system, game)
    path = _find_cht(game)
    if not path:
        return {"ok": True, "system": system, "game": game, "file": None, "cheats": [],
                "note": ("No cheats for this game" if os.path.isdir(CHEAT_DB)
                         else "No cheat database on this image")}
    return {"ok": True, "system": system, "game": game, "file": path,
            "cheats": _parse_cht(path), "note": "Toggling a cheat applies on next launch"}

def set_game_cheat(payload):
    system, game = _cur_game((payload or {}).get("system"), (payload or {}).get("game"))
    path = _find_cht(game)
    if not path:
        return {"ok": False, "error": "no cheat file for this game"}
    try:
        idx = int(payload.get("index"))
    except (TypeError, ValueError):
        return {"ok": False, "error": "index required"}
    on = payload.get("enable") in (True, 1, "1", "true", "on")
    key = "cheat%d_enable" % idx
    rx = re.compile(r'^(\s*%s\s*=\s*)(.*)$' % re.escape(key))
    try:
        lines = open(path, errors="replace").read().splitlines()
    except Exception as e:
        return {"ok": False, "error": str(e)}
    found = False
    for j, l in enumerate(lines):
        m = rx.match(l)
        if m:
            quoted = '"' in m.group(2)
            lines[j] = m.group(1) + (('"%s"' % ("true" if on else "false")) if quoted
                                     else ("true" if on else "false"))
            found = True
            break
    if not found:
        return {"ok": False, "error": "cheat %d not in file" % idx}
    try:
        tmp = path + ".tmp"
        with open(tmp, "w") as f:
            f.write("\n".join(lines) + "\n"); f.flush(); os.fsync(f.fileno())
        os.replace(tmp, path)
    except Exception as e:
        return {"ok": False, "error": str(e)}
    return {"ok": True, "index": idx, "enable": on, "note": "applies on next launch"}

# ---- #62 MangoHud perf/battery HUD. Batocera's emulatorlauncher inserts `mangohud` natively when
# global.hud != none (gated by per-emulator hud_support). We just set the key — surface, don't wrap. ----
# fps = a lean custom HUD (fps + battery); full = Batocera's built-in "perf" preset (fps/cpu/gpu/temps).
_HUD_FPS_CUSTOM = "position=top-left\\nbackground_alpha=0.4\\nlegacy_layout=false\\nfps\\nbattery"

def _mangohud_ok():
    return bool(shutil.which("mangohud"))

def hud_get():
    raw = _bconf_get("global.hud") or "none"
    if raw == "custom" and (_bconf_get("global.hud_custom") or "") == _HUD_FPS_CUSTOM:
        mode = "fps"
    else:
        mode = {"none": "off", "perf": "full"}.get(raw, "off" if raw == "none" else "custom")
    return {"ok": True, "mode": mode, "raw": raw, "available": _mangohud_ok()}

def hud_set(mode):
    mode = (mode or "off").lower()
    if mode not in ("off", "fps", "full"):
        return {"ok": False, "error": "mode must be off/fps/full"}
    if mode != "off" and not _mangohud_ok():
        return {"ok": False, "error": "MangoHud is not installed on this image", "available": False}
    if mode == "off":
        _bconf_set("global.hud", "none")
    elif mode == "full":
        _bconf_set("global.hud", "perf")
    else:   # fps
        _bconf_set("global.hud", "custom")
        _bconf_set("global.hud_custom", _HUD_FPS_CUSTOM)
    LOG.info("HUD set: %s", mode)
    return {"ok": True, "mode": mode, "available": True, "note": "applies on next game launch"}

# ---- #54 Wi-Fi quick toggle (the radio's POWER, via connman — real on handheld hardware) ----
def net_wifi_status():
    info = net_info()
    powered = None
    try:
        tech = subprocess.run(["connmanctl", "technologies"],
                              capture_output=True, text=True, timeout=8).stdout
        cur = None
        for line in tech.splitlines():
            s = line.strip()
            if s.startswith("/net/connman/technology/"):
                cur = s
            elif cur and cur.endswith("/wifi") and s.startswith("Powered ="):
                powered = "True" in s
    except Exception:
        pass
    return {"ok": True, "has_wifi": bool(info.get("has_wifi")), "powered": powered,
            "connection": info.get("connection"), "online": info.get("online")}

def net_wifi_toggle(on=None):
    st = net_wifi_status()
    if not st.get("has_wifi") or st.get("powered") is None:
        return {"ok": False, "error": "No Wi-Fi radio on this device", "has_wifi": False}
    target = (not st["powered"]) if on is None else (on in (True, 1, "1", "true", "on"))
    try:
        subprocess.run(["connmanctl", "enable" if target else "disable", "wifi"],
                       capture_output=True, text=True, timeout=10)
    except Exception as e:
        return {"ok": False, "error": str(e)}
    return {"ok": True, "powered": target, "has_wifi": True}

# ---- save-state thumbnails (task 53): RetroArch writes <game>.state[N].png beside each state.
# The "continue where you left off" picture for library/home resume cards. Path-confined to SAVES. ----
SAVES_ROOT = "/userdata/saves"

def _state_name_ok(s):
    # a system folder or ROM stem — never a path component. Blocks ../ traversal at the input.
    return bool(s) and "/" not in s and "\\" not in s and s not in (".", "..") and "\x00" not in s

def latest_state_thumb(system, game):
    """Path to the NEWEST save-state thumbnail PNG for system/game, or None when no
    state/png exists. Confined to SAVES_ROOT — rejects traversal in BOTH the input
    (no path separators) and the resolved path (realpath must stay under saves)."""
    import glob, re
    if not (_state_name_ok(system) and _state_name_ok(game)):
        return None
    d = os.path.realpath(os.path.join(SAVES_ROOT, system))
    if d != SAVES_ROOT and not d.startswith(SAVES_ROOT + os.sep):
        return None
    if not os.path.isdir(d):
        return None
    best, best_mt = None, -1.0
    for f in glob.glob(glob.escape(os.path.join(d, game)) + ".state*"):
        if f.endswith(".png") or not re.search(r"\.state(\d*)$", f):
            continue
        png = os.path.realpath(f + ".png")
        if not png.startswith(SAVES_ROOT + os.sep) or not os.path.isfile(png):
            continue   # missing thumb, or a symlink escaping the saves root
        try:
            mt = os.path.getmtime(f)   # newest STATE wins (the latest place you left off)
        except OSError:
            continue
        if mt > best_mt:
            best, best_mt = png, mt
    return best

def state_thumb_url(system, game):
    from urllib.parse import quote
    if latest_state_thumb(system, game):
        return "/game/state/thumb?system=" + quote(system) + "&game=" + quote(game)
    return None

# ---- BIOS checker (task 52): many systems need a user-supplied BIOS; without it a launch
# silently fails. Batocera ships the authoritative per-system BIOS manifest (with md5s) as a
# `systems = {...}` dict inside /usr/bin/batocera-systems. We read THAT (the real artifact) —
# never a hand-maintained copy that could drift — and check /userdata/bios for presence + md5. ----
BIOS_ROOT = "/userdata/bios"
BATOCERA_SYSTEMS = "/usr/bin/batocera-systems"
_BIOS_MANIFEST = None

def _bios_manifest():
    # Parse the `systems` literal out of batocera-systems with ast (never exec the script).
    # literal_eval is safe: the dict is pure str/dict/list literals. Cached after first read.
    global _BIOS_MANIFEST
    if _BIOS_MANIFEST is not None:
        return _BIOS_MANIFEST
    man = {}
    try:
        import ast
        with open(BATOCERA_SYSTEMS) as fh:
            tree = ast.parse(fh.read())
        for node in tree.body:
            if isinstance(node, ast.Assign) and any(
                    isinstance(t, ast.Name) and t.id == "systems" for t in node.targets):
                man = ast.literal_eval(node.value)
                break
    except Exception as e:
        LOG.warning("bios manifest parse failed: %s", e)
        man = {}
    _BIOS_MANIFEST = man
    return man

def _bios_missing_for_system(system):
    """Return list of missing BIOS filenames for *system*, or [] if all present / none needed.

    Reuses _bios_manifest() — same source as #52 /bios/status.  No false-positives:
    systems absent from the manifest (nes, genesis, homebrew) return [] unconditionally.
    Archive entries (.zip) are checked for the zip itself, not individual inner files.
    """
    man = _bios_manifest()
    entry = man.get(system)
    if not entry:
        return []   # not in manifest → no BIOS needed
    biosfiles = entry.get("biosFiles") or []
    if not biosfiles:
        return []   # manifest entry exists but lists nothing
    # dedupe by target file path (same logic as bios_status)
    seen, missing = set(), []
    for bf in biosfiles:
        rel = bf.get("file", "")
        if not rel or rel in seen:
            continue
        seen.add(rel)
        full = os.path.realpath(os.path.join("/userdata", rel))
        present = (full == BIOS_ROOT or full.startswith(BIOS_ROOT + os.sep)) and os.path.isfile(full)
        if not present:
            missing.append(os.path.basename(rel))
    return missing


def _md5_file(path, cap=96 * 1024 * 1024):
    # md5 only when it's cheap+meaningful: skip files larger than cap (PS3 PUP / CHDs) → unverified.
    try:
        if os.path.getsize(path) > cap:
            return None
        import hashlib
        h = hashlib.md5()
        with open(path, "rb") as fh:
            for blk in iter(lambda: fh.read(1 << 20), b""):
                h.update(blk)
        return h.hexdigest()
    except Exception:
        return None

def bios_status(system_filter=None):
    man = _bios_manifest()
    have = set()
    try:
        for s in (list_games().get("systems") or []):
            if s.get("system"):
                have.add(s["system"])
    except Exception:
        pass
    out = []
    for key in sorted(man.keys()):
        if system_filter and key != system_filter:
            continue
        entry = man[key] or {}
        # dedupe by target file (a .zip appears once per zipped member in the manifest)
        files, order = {}, []
        for bf in (entry.get("biosFiles") or []):
            rel = bf.get("file", "")
            if not rel:
                continue
            if rel not in files:
                files[rel] = {"md5s": set(), "archive": False}
                order.append(rel)
            if bf.get("zippedFile"):
                files[rel]["archive"] = True          # md5 is of a member inside the zip
            elif bf.get("md5"):
                files[rel]["md5s"].add(bf["md5"])
        flist = []
        for rel in order:
            info = files[rel]
            full = os.path.realpath(os.path.join("/userdata", rel))
            present = (full == BIOS_ROOT or full.startswith(BIOS_ROOT + os.sep)) and os.path.isfile(full)
            md5_ok, md5_expected = None, (sorted(info["md5s"])[0] if info["md5s"] else None)
            if present and info["md5s"] and not info["archive"]:
                got = _md5_file(full)
                if got is not None:
                    md5_ok = got in info["md5s"]      # any listed md5 is an accepted match
            flist.append({"file": os.path.basename(rel), "rel": rel,
                          "drop": os.path.dirname(os.path.join("/userdata", rel)),
                          "present": present, "archive": info["archive"],
                          "md5_ok": md5_ok, "md5_expected": md5_expected})
        present_n = sum(1 for f in flist if f["present"])
        out.append({"system": key, "name": entry.get("name", key), "has_games": key in have,
                    "files": flist, "required": len(flist), "present_count": present_n,
                    "missing": [f["file"] for f in flist if not f["present"]],
                    "complete": bool(flist) and present_n == len(flist)})
    # the user's own systems that need NOTHING (absent from the manifest) — say so honestly
    for s in sorted(have):
        if s not in man and (not system_filter or s == system_filter):
            out.append({"system": s, "name": _SYS.get(s, s), "has_games": True,
                        "files": [], "required": 0, "present_count": 0, "missing": [],
                        "complete": True, "none_needed": True})
    return {"ok": True, "bios_dir": BIOS_ROOT, "manifest_ok": bool(man),
            "systems": out, "count": len(out)}

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

# ---- #44 custom collections (user-created named shelves) ----
# Schema: collections.json = [{"id": str, "name": str, "created": epoch, "games": [{"system":str,"game":str}, ...]}, ...]
# Auto-collections are computed at read time (not stored): __recently_added, __recently_played, __most_played.
COLLECTIONS_F = "/userdata/system/gose/collections.json"
_COLL_LOCK = threading.Lock()

def _coll_load():
    try:
        v = json.load(open(COLLECTIONS_F))
        return v if isinstance(v, list) else []
    except Exception:
        return []

def _coll_save(lst):
    os.makedirs("/userdata/system/gose", exist_ok=True)
    write_json_atomic(COLLECTIONS_F, lst)

def collections_list():
    """GET /collections — user collections + computed auto-collections."""
    colls = _coll_load()
    out = [{"id": c["id"], "name": c["name"], "created": c.get("created", 0),
            "game_count": len(c.get("games", []))} for c in colls]
    # auto-collections (computed, not stored)
    pt = _playstats()
    rec_f = RECENT_F
    # Recently Added: top-20 ROMs by file mtime, across all systems
    try:
        added = []
        for sysname in os.listdir(ROMS):
            d = os.path.join(ROMS, sysname)
            if not os.path.isdir(d):
                continue
            for f in os.listdir(d):
                if f.startswith(".") or f in _SKIPDIRS or "gamelist" in f:
                    continue
                if os.path.isdir(os.path.join(d, f)):
                    continue
                ext = os.path.splitext(f)[1].lower()
                if ext in _SKIPEXT:
                    continue
                stem = os.path.splitext(f)[0]
                try:
                    mt = int(os.path.getmtime(os.path.join(d, f)))
                except Exception:
                    mt = 0
                added.append({"system": sysname, "game": stem, "added": mt})
        added.sort(key=lambda x: -x["added"])
        out.append({"id": "__recently_added", "name": "Recently Added", "auto": True,
                    "game_count": min(20, len(added))})
    except Exception:
        pass
    # Recently Played: from recent.json (newest first, up to 20)
    try:
        recent_rows = json.load(open(rec_f)) if os.path.exists(rec_f) else []
        out.append({"id": "__recently_played", "name": "Recently Played", "auto": True,
                    "game_count": min(20, len(recent_rows))})
    except Exception:
        out.append({"id": "__recently_played", "name": "Recently Played", "auto": True, "game_count": 0})
    # Most Played: from playstats, already computed by _game_stats_all
    played_count = sum(1 for v in pt.values() if isinstance(v, dict) and v.get("total_secs", 0) > 0)
    out.append({"id": "__most_played", "name": "Most Played", "auto": True,
                "game_count": min(10, played_count)})
    return {"ok": True, "collections": out}

def collection_get(coll_id):
    """GET /collections/<id> — full game list for one collection (user or auto)."""
    if coll_id == "__recently_added":
        try:
            added = []
            for sysname in os.listdir(ROMS):
                d = os.path.join(ROMS, sysname)
                if not os.path.isdir(d):
                    continue
                for f in os.listdir(d):
                    if f.startswith(".") or f in _SKIPDIRS or "gamelist" in f:
                        continue
                    if os.path.isdir(os.path.join(d, f)):
                        continue
                    if os.path.splitext(f)[1].lower() in _SKIPEXT:
                        continue
                    stem = os.path.splitext(f)[0]
                    try:
                        mt = int(os.path.getmtime(os.path.join(d, f)))
                    except Exception:
                        mt = 0
                    added.append({"name": stem, "system": sysname,
                                  "img": _game_img(sysname, stem), "added": mt,
                                  "fav": (sysname, stem) in _fav_set()})
            added.sort(key=lambda x: -x["added"])
            games = added[:20]
        except Exception as e:
            return {"ok": False, "error": str(e)}
        return {"ok": True, "id": coll_id, "name": "Recently Added", "auto": True, "games": games}
    if coll_id == "__recently_played":
        try:
            rec_rows = json.load(open(RECENT_F)) if os.path.exists(RECENT_F) else []
        except Exception:
            rec_rows = []
        favset = _fav_set()
        games = [{"name": r.get("game", ""), "system": r.get("system", ""),
                  "img": _game_img(r.get("system", ""), r.get("game", "")),
                  "last_played": r.get("t"), "fav": (r.get("system", ""), r.get("game", "")) in favset}
                 for r in rec_rows[:20]]
        return {"ok": True, "id": coll_id, "name": "Recently Played", "auto": True, "games": games}
    if coll_id == "__most_played":
        pt = _playstats(); favset = _fav_set()
        played = [(k, v) for k, v in pt.items() if isinstance(v, dict) and v.get("total_secs", 0) > 0]
        played.sort(key=lambda kv: -kv[1].get("total_secs", 0))
        games = []
        for key, entry in played[:10]:
            s, _, g = key.partition("/")
            games.append({"name": g, "system": s, "img": _game_img(s, g),
                          "playtime_s": entry.get("total_secs", 0),
                          "last_played": entry.get("last_played"),
                          "fav": (s, g) in favset})
        return {"ok": True, "id": coll_id, "name": "Most Played", "auto": True, "games": games}
    # user collection
    with _COLL_LOCK:
        colls = _coll_load()
    c = next((x for x in colls if x.get("id") == coll_id), None)
    if c is None:
        return {"ok": False, "error": "collection not found"}
    favset = _fav_set()
    games = []
    for entry in c.get("games", []):
        s, g = entry.get("system", ""), entry.get("game", "")
        if not s or not g:
            continue
        # degrade cleanly if ROM was deleted: include entry with img=null (page can grey it out)
        games.append({"name": g, "system": s, "img": _game_img(s, g),
                      "fav": (s, g) in favset})
    return {"ok": True, "id": coll_id, "name": c["name"], "created": c.get("created", 0),
            "games": games}

def collection_create(payload):
    """POST /collections — create a named collection. Returns new collection id."""
    name = (payload or {}).get("name", "").strip()
    if not name:
        return {"ok": False, "error": "name required"}
    if len(name) > 64:
        return {"ok": False, "error": "name too long (max 64 chars)"}
    with _COLL_LOCK:
        colls = _coll_load()
        coll_id = "col_" + secrets.token_hex(6)
        colls.append({"id": coll_id, "name": name, "created": int(time.time()), "games": []})
        _coll_save(colls)
    return {"ok": True, "id": coll_id, "name": name}

def collection_delete(coll_id):
    """POST /collections/<id>/delete — remove a collection (games untouched)."""
    if not coll_id or coll_id.startswith("__"):
        return {"ok": False, "error": "cannot delete auto-collections"}
    with _COLL_LOCK:
        colls = _coll_load()
        before = len(colls)
        colls = [c for c in colls if c.get("id") != coll_id]
        if len(colls) == before:
            return {"ok": False, "error": "collection not found"}
        _coll_save(colls)
    return {"ok": True}

def collection_add_game(coll_id, payload):
    """POST /collections/<id>/add — add a game to a collection."""
    if not coll_id or coll_id.startswith("__"):
        return {"ok": False, "error": "auto-collections are read-only"}
    system = (payload or {}).get("system", "")
    game = (payload or {}).get("game", "")
    if not system or not game:
        return {"ok": False, "error": "system+game required"}
    with _COLL_LOCK:
        colls = _coll_load()
        c = next((x for x in colls if x.get("id") == coll_id), None)
        if c is None:
            return {"ok": False, "error": "collection not found"}
        already = any(e.get("system") == system and e.get("game") == game
                      for e in c.get("games", []))
        if not already:
            c.setdefault("games", []).append({"system": system, "game": game})
            _coll_save(colls)
    return {"ok": True, "already": already}

def collection_remove_game(coll_id, payload):
    """POST /collections/<id>/remove — remove a game from a collection (ROM untouched)."""
    if not coll_id or coll_id.startswith("__"):
        return {"ok": False, "error": "auto-collections are read-only"}
    system = (payload or {}).get("system", "")
    game = (payload or {}).get("game", "")
    if not system or not game:
        return {"ok": False, "error": "system+game required"}
    with _COLL_LOCK:
        colls = _coll_load()
        c = next((x for x in colls if x.get("id") == coll_id), None)
        if c is None:
            return {"ok": False, "error": "collection not found"}
        before = len(c.get("games", []))
        c["games"] = [e for e in c.get("games", [])
                      if not (e.get("system") == system and e.get("game") == game)]
        _coll_save(colls)
    return {"ok": True, "removed": before - len(c["games"])}

# ---- stranger's-hands resilience: boot-success counter + backup / restore / factory reset (gap J1/J2) ----
# Boot counter: the watchdog INCREMENTS .boot_attempts every time it (re)starts the UI server; this
# server CLEARS it the moment it serves the home page (proof the UI booted far enough to render).
# A crash-loop that never reaches home lets the count climb -> watchdog trips safe mode at the threshold.
BOOT_ATTEMPTS_F = ROOT + "/.boot_attempts"
BACKUP_DIR = "/userdata/backups"
# What a backup captures (relative to /userdata): the whole GOSE UI/state dir minus caches/logs,
# plus the AI account tokens + audit. NEVER roms, NEVER saves, NEVER the OS.
_BACKUP_INCLUDE = ["gose-ui", "system/gose/ai_tokens.json", "system/gose/ai_audit.jsonl",
                   "system/gose/collections.json"]
_BACKUP_EXCLUDE = ["gose-ui/*.log", "gose-ui/*.log.*", "gose-ui/__pycache__",
                   "gose-ui/*.tmp", "gose-ui/.boot_attempts", "gose-ui/.safe_mode",
                   "gose-ui/_stream_test.bin", "gose-ui/_render_common.pyc"]
# Factory reset wipes these GOSE state files back to defaults (grants handled separately via the
# agent-sync path). ROMs (/userdata/roms) and saves (/userdata/saves) are deliberately untouched.
_RESET_DEFAULTS = [
    (ROOT + "/favorites.json", []),
    (ROOT + "/recent.json", []),
    (ROOT + "/playtime.json", {}),
    (PLAYSTATS_F, {}),
    (ROOT + "/ai_requests.json", {}),
    (COLLECTIONS_F, []),
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

# ---- Diagnostics support-bundle export (#19) ----------------------------------------
# Gathers logs (tailed), safe config (no secrets), versions, and service health into a
# single .tar.gz under /userdata. NEVER includes: accounts PIN hashes, ai_tokens,
# ssh credentials, dev token, any file that lives under /userdata/system/gose/token,
# or anything containing 'password'/'pin_hash'/'secret' in the path.
#
# Secret-exclusion contract (enforced by _DIAG_NEVER and _scrub_accounts):
#   - ai_tokens.json  (bearer tokens)
#   - token           (dev token)
#   - accounts.json   included ONLY after PIN hashes + pin_salt are scrubbed
#   - ssh_cred.json   excluded (path pattern)
#   - ai_audit.jsonl  excluded (may contain prompt text)
#   - *.key / *.pem / *.p12 / *.pfx  excluded by extension
#
# Log cap: each log is tailed to 2000 lines (avoids huge display.log filling the bundle).
# Bundle lives at /userdata/gose-diagnostics-<timestamp>.tar.gz; caller cleans it up.

DIAG_DIR = "/userdata"
_DIAG_NEVER = {
    "ai_tokens.json", "token", "ssh_cred.json", "ai_audit.jsonl",
}
_DIAG_NEVER_EXT = {".key", ".pem", ".p12", ".pfx", ".crt"}
_DIAG_SECRET_KEYS = {"pin_hash", "pin_salt", "password", "secret", "token", "ssh_key"}
_DIAG_LOG_CAP = 2000    # lines per log (tail)
_DIAG_LOG_MAXBYTES = 512 * 1024  # 512 KB absolute ceiling per log

def _diag_safe_path(p):
    """Return True iff this file path is safe to include (not a secret)."""
    base = os.path.basename(p)
    if base in _DIAG_NEVER:
        return False
    _, ext = os.path.splitext(base)
    if ext.lower() in _DIAG_NEVER_EXT:
        return False
    # block any file whose name contains clearly secret keywords
    bl = base.lower()
    for kw in ("ssh_cred", "ai_token", "pin_hash", "pin_salt", ".key", "secret"):
        if kw in bl:
            return False
    return True

def _scrub_accounts():
    """Load accounts.json and remove all secret fields before bundling."""
    _SECRET_FIELDS = {"pin_hash", "pin_salt", "password", "secret", "ssh_key", "token"}
    try:
        d = json.load(open("/userdata/system/gose/accounts.json"))
    except Exception:
        return None
    if isinstance(d, dict) and "users" in d:
        scrubbed = []
        for u in (d["users"] or []):
            su = {k: v for k, v in u.items() if k not in _SECRET_FIELDS}
            scrubbed.append(su)
        d = dict(d)
        d["users"] = scrubbed
    return json.dumps(d, indent=2).encode()

def _tail_log(path, maxlines=_DIAG_LOG_CAP, maxbytes=_DIAG_LOG_MAXBYTES):
    """Read a log file, tailing to maxlines and capping at maxbytes. Returns bytes."""
    try:
        size = os.path.getsize(path)
    except Exception:
        return b""
    try:
        with open(path, "rb") as fh:
            # if file fits within maxbytes, read everything and tail lines
            if size <= maxbytes:
                raw = fh.read()
            else:
                fh.seek(-maxbytes, 2)
                raw = fh.read(maxbytes)
        lines = raw.splitlines(keepends=True)
        if len(lines) > maxlines:
            header = ("... [tailed to last %d lines] ...\n" % maxlines).encode()
            lines = [header] + lines[-maxlines:]
        return b"".join(lines)
    except Exception:
        return b""

def _diag_service_health():
    """Check liveness of the five key services. Returns list of {id, name, ok, detail}."""
    svcs = []
    # 1. gose-agent (port 8731)
    try:
        s = socket.create_connection(("127.0.0.1", 8731), 2); s.close()
        svcs.append({"id": "agent", "name": "GOSE Agent", "ok": True, "detail": "port 8731 open"})
    except Exception as e:
        svcs.append({"id": "agent", "name": "GOSE Agent", "ok": False, "detail": str(e)})
    # 2. UI server (self — we're answering so this is always up; still check port)
    try:
        s = socket.create_connection(("127.0.0.1", 8780), 2); s.close()
        svcs.append({"id": "server", "name": "UI Server", "ok": True, "detail": "port 8780 open"})
    except Exception as e:
        svcs.append({"id": "server", "name": "UI Server", "ok": False, "detail": str(e)})
    # 3. pad-nav bridge (pgrep)
    ok = subprocess.run(["pgrep", "-f", "gose-pad-nav.py"], capture_output=True).returncode == 0
    svcs.append({"id": "bridge", "name": "Pad-Nav Bridge", "ok": ok,
                 "detail": "running" if ok else "not found in process list"})
    # 4. passthrough (pad_passthrough.py)
    ok = subprocess.run(["pgrep", "-f", "pad_passthrough"], capture_output=True).returncode == 0
    svcs.append({"id": "passthrough", "name": "Pad Passthrough", "ok": ok,
                 "detail": "running" if ok else "not found (OK if no physical pad)"})
    # 5. kiosk (kiosk.py)
    ok = subprocess.run(["pgrep", "-f", "kiosk.py"], capture_output=True).returncode == 0
    svcs.append({"id": "kiosk", "name": "Kiosk (WebKit)", "ok": ok,
                 "detail": "running" if ok else "not found in process list"})
    return svcs

def _diag_versions():
    """Gather version strings: GOSE version, batocera version, kernel."""
    out = {}
    out["gose"] = VERSION.get("version", "unknown") + " (build " + VERSION.get("build", "?") + ")"
    try:
        out["batocera"] = open("/usr/share/batocera/batocera.version").read().strip()
    except Exception:
        out["batocera"] = "unknown"
    try:
        out["kernel"] = subprocess.run(["uname", "-r"], capture_output=True,
                                        text=True, timeout=5).stdout.strip()
    except Exception:
        out["kernel"] = "unknown"
    out["base"] = VERSION.get("base", "unknown")
    return out

def _diag_safe_config():
    """Read batocera.conf — scrub lines that contain secret keywords (passwords/tokens/PINs)."""
    _SECRET_PATTERNS = ("password", "token", "secret", "pin", "key=", "passwd", "ssh_pass")
    try:
        lines = open("/userdata/system/batocera.conf").readlines()
    except Exception:
        return b"# batocera.conf not found\n"
    out = []
    redacted = 0
    for line in lines:
        ll = line.lower()
        if any(kw in ll for kw in _SECRET_PATTERNS):
            # keep the key name, redact the value
            if "=" in line:
                key = line.split("=", 1)[0]
                out.append((key + "=[REDACTED]\n").encode())
            else:
                out.append(b"[REDACTED LINE]\n")
            redacted += 1
        else:
            out.append(line.encode() if isinstance(line, str) else line)
    if redacted:
        out.insert(0, ("# %d lines redacted (secret values)\n" % redacted).encode())
    return b"".join(out)

def diag_health():
    """GET /diag/health — returns current service liveness as JSON."""
    svcs = _diag_service_health()
    all_ok = all(s["ok"] for s in svcs)
    return {"ok": True, "all_ok": all_ok, "services": svcs,
            "versions": _diag_versions(),
            "watchdog": {
                "safe_mode": os.path.exists(ROOT + "/.safe_mode"),
                "boot_attempts": _safe_read_int(BOOT_ATTEMPTS_F),
                "prev_ui_available": os.path.isdir("/userdata/gose-ui.prev") and
                                     bool(os.listdir("/userdata/gose-ui.prev")),
            }}

def _safe_read_int(path, default=0):
    try: return int(open(path).read().strip() or str(default))
    except Exception: return default

def diag_bundle():
    """POST /diag/bundle — create a support .tar.gz at /userdata/gose-diagnostics-<ts>.tar.gz.
    Returns {ok, path, size, members} on success. Bundle NEVER contains secrets (enforced here
    and verified by caller: grep members for accounts/token/ssh_cred/pin returns nothing)."""
    import tarfile, io
    ts = time.strftime("%Y%m%d-%H%M%S")
    name = "gose-diagnostics-%s.tar.gz" % ts
    final = os.path.join(DIAG_DIR, name)
    tmp = os.path.join(DIAG_DIR, ".tmp-diag-%s.tar.gz" % ts)

    members_added = []
    try:
        with tarfile.open(tmp, "w:gz") as tf:
            def add_bytes(arcname, data):
                if not isinstance(data, bytes):
                    data = data.encode()
                info = tarfile.TarInfo(name=arcname)
                info.size = len(data)
                tf.addfile(info, io.BytesIO(data))
                members_added.append(arcname)

            # --- versions ---
            add_bytes("diag/versions.json", json.dumps(_diag_versions(), indent=2))

            # --- service health ---
            svcs = _diag_service_health()
            add_bytes("diag/services.json", json.dumps({"services": svcs,
                "watchdog": {
                    "safe_mode": os.path.exists(ROOT + "/.safe_mode"),
                    "boot_attempts": _safe_read_int(BOOT_ATTEMPTS_F),
                    "prev_ui_available": os.path.isdir("/userdata/gose-ui.prev") and
                                         bool(os.listdir("/userdata/gose-ui.prev")),
                }}, indent=2))

            # --- port/process snapshot ---
            port_out = subprocess.run(["ss", "-tlnp"], capture_output=True,
                                       text=True, timeout=10).stdout
            add_bytes("diag/ports.txt", port_out)
            ps_out = subprocess.run(["ps", "aux"], capture_output=True,
                                     text=True, timeout=10).stdout
            add_bytes("diag/processes.txt", ps_out)

            # --- config (scrubbed) ---
            add_bytes("diag/batocera.conf.txt", _diag_safe_config())

            # --- accounts (scrubbed — NO pin_hash/pin_salt) ---
            acct = _scrub_accounts()
            if acct is not None:
                add_bytes("diag/accounts-scrubbed.json", acct)

            # --- gose settings (exclude secrets) ---
            for fname in ("privacy.json", "ui_prefs.json", "store_sources.json"):
                p = "/userdata/system/gose/" + fname
                if os.path.isfile(p) and _diag_safe_path(p):
                    try:
                        add_bytes("diag/gose-" + fname, open(p, "rb").read())
                    except Exception:
                        pass

            # --- logs (tailed, capped) ---
            LOG_DIR = "/userdata/system/logs"
            try:
                log_files = sorted(os.listdir(LOG_DIR))
            except Exception:
                log_files = []
            for lf in log_files:
                lp = os.path.join(LOG_DIR, lf)
                if not os.path.isfile(lp) or not _diag_safe_path(lp):
                    continue
                data = _tail_log(lp)
                add_bytes("diag/logs/" + lf, data)

            # gose-ui server log (not in system/logs)
            for extra_log in (ROOT + "/gose.log", ROOT + "/server.log"):
                if os.path.isfile(extra_log):
                    data = _tail_log(extra_log)
                    add_bytes("diag/logs/" + os.path.basename(extra_log), data)

            # --- manifest (what's in the bundle) ---
            manifest = {
                "created": ts, "members": list(members_added),
                "secrets_excluded": list(_DIAG_NEVER),
                "note": "accounts.json is included with pin_hash/pin_salt/password scrubbed"
            }
            add_bytes("diag/MANIFEST.json", json.dumps(manifest, indent=2))

        os.replace(tmp, final)
        size = os.path.getsize(final)
        LOG.info("DIAG BUNDLE %s (%d bytes, %d members)", name, size, len(members_added))

        # Self-verify: unpack member list and assert no secrets made it in
        verify = subprocess.run(["tar", "-tzf", final], capture_output=True,
                                  text=True, timeout=30)
        all_members = verify.stdout.splitlines()
        bad = [m for m in all_members if any(s in m for s in
               ("ai_tokens", "ssh_cred", "pin_hash", "pin_salt", "/token",
                "password", "secret"))]
        if bad:
            # Something slipped through — remove the bundle and refuse
            try: os.remove(final)
            except Exception: pass
            LOG.error("DIAG BUNDLE secret leak detected: %s", bad)
            return {"ok": False, "error": "bundle aborted: secret leak detected: " + str(bad)}

        return {"ok": True, "file": name, "path": final, "size": size,
                "members": all_members}
    except Exception as e:
        try: os.remove(tmp)
        except Exception: pass
        LOG.error("diag bundle failed: %s", e)
        return {"ok": False, "error": str(e)}

def diag_bundle_delete(filename):
    """DELETE (or POST /diag/bundle/delete) — remove a diagnostics bundle by filename."""
    if not filename:
        return {"ok": False, "error": "filename required"}
    base = os.path.basename(filename)
    if not base.startswith("gose-diagnostics-") or not base.endswith(".tar.gz"):
        return {"ok": False, "error": "not a diagnostics bundle"}
    path = os.path.join(DIAG_DIR, base)
    real = os.path.realpath(path)
    if not real.startswith(os.path.realpath(DIAG_DIR) + os.sep):
        return {"ok": False, "error": "path not in diagnostics dir"}
    if not os.path.isfile(path):
        return {"ok": False, "error": "file not found"}
    try:
        os.remove(path)
        return {"ok": True, "file": base}
    except Exception as e:
        return {"ok": False, "error": str(e)}

# ---- RetroAchievements (#33) -----------------------------------------------
# Batocera wires RA via batocera.conf global.retroachievements* keys; configgen
# writes them into cheevos_* in retroarchcustom.cfg at launch.  We never hold
# the password in our own JSON — only in batocera.conf (Batocera's own design),
# which _diag_safe_config() already scrubs ("password" pattern).  The RA token
# that RetroArch exchanges internally is also scrubbed ("token" pattern).
#
# Unlock detection: RetroArch logs achievement unlocks to its own log at
# /userdata/system/configs/retroarch/saves/retroarch.log (or the path in
# retroarch.cfg log_file).  We tail that log from a known offset and fire a
# GOSE notify toast via notifications_post() for each newly-seen unlock line.
# Limitation: requires an RA account + RA-supported game; our homebrew ROMs are
# NOT in the RA database — "no achievements for this game" is expected, not a bug.
#
# Per-game cheevos data: RetroArch downloads per-game achievement definitions
# into its runtime cache when an RA-supported game is loaded.  The stable
# in-guest path is /userdata/system/configs/retroarch/cache/cheevos/<gameid>.json
# (written by rcheevos at game load).  Offline / no account: honest empty.

RA_LOG = "/userdata/system/configs/retroarch/saves/retroarch.log"
_ra_log_pos = 0      # byte offset: tail from here so we don't re-fire old lines
_ra_log_lock = threading.Lock()

def ra_state_get():
    """GET /ra/state — current RA config from batocera.conf (no secrets returned)."""
    enabled = _bconf_get("global.retroachievements")
    username = _bconf_get("global.retroachievements.username") or ""
    hardcore = _bconf_get("global.retroachievements.hardcore")
    leaderboards = _bconf_get("global.retroachievements.leaderboards")
    # never return the password/token — just confirm whether credentials are set
    has_credentials = bool(username.strip())
    return {
        "ok": True,
        "enabled": (enabled or "0").strip() not in ("", "0", "false"),
        "username": username.strip(),
        "has_credentials": has_credentials,
        "hardcore": (hardcore or "0").strip() not in ("", "0", "false"),
        "leaderboards": (leaderboards or "0").strip() not in ("", "0", "false"),
        "note": "password is stored in batocera.conf only; never returned by this endpoint"
    }

def ra_credentials_set(payload):
    """POST /ra/credentials — set RA username/password and enable/disable/hardcore.
    The password is written ONLY to batocera.conf (Batocera's canonical store).
    It is NEVER stored in our own JSON files.  _diag_safe_config() scrubs it
    from any support bundle (matches 'password' pattern).  Pass enabled=null/
    missing to leave toggle as-is; pass password='' to clear credentials."""
    payload = payload or {}

    # --- enabled toggle ---
    if "enabled" in payload and payload["enabled"] is not None:
        _bconf_set("global.retroachievements", "1" if payload["enabled"] else "0")

    # --- credentials ---
    username = payload.get("username")
    password = payload.get("password")  # RA account password (RA handles token exchange)
    if username is not None:
        uname = str(username).strip()[:64]
        _bconf_set("global.retroachievements.username", uname)
        if not uname:
            # clearing username: also clear password and disable
            _bconf_set("global.retroachievements.password", "")
            _bconf_set("global.retroachievements", "0")
    if password is not None:
        # store in batocera.conf only; _diag_safe_config scrubs "password" lines
        _bconf_set("global.retroachievements.password", str(password).strip()[:256])
        # if credentials are provided, auto-enable
        if str(password).strip() and username is not None and str(username).strip():
            _bconf_set("global.retroachievements", "1")

    # --- hardcore toggle ---
    if "hardcore" in payload and payload["hardcore"] is not None:
        _bconf_set("global.retroachievements.hardcore", "1" if payload["hardcore"] else "0")

    # --- leaderboards toggle ---
    if "leaderboards" in payload and payload["leaderboards"] is not None:
        _bconf_set("global.retroachievements.leaderboards", "1" if payload["leaderboards"] else "0")

    LOG.info("RA credentials updated (username=%s, has_pw=%s)",
             payload.get("username", "(unchanged)"), bool(payload.get("password")))
    return ra_state_get()

def ra_achievements_get(system, game):
    """GET /ra/achievements?system=&game= — per-game achievement list from the
    RetroArch cheevos cache.  Returns honest empty state when no RA account is
    configured, or game is not in the RA database (homebrew ROMs are not),
    or cache not yet downloaded (game not yet launched with RA enabled).
    Never fabricates unlock data."""
    st = ra_state_get()
    if not st["enabled"] or not st["has_credentials"]:
        return {"ok": True, "system": system, "game": game,
                "achievements": [], "total": 0,
                "state": "no_account",
                "note": "RetroAchievements is disabled or no account configured. "
                        "Set credentials in Settings › RetroAchievements."}

    # RetroArch downloads per-game cheevos JSON into its runtime cache.
    cache_dir = "/userdata/system/configs/retroarch/cache/cheevos"
    achievements = []
    state = "no_data"
    matched_file = None

    if os.path.isdir(cache_dir):
        candidates = [f for f in os.listdir(cache_dir) if f.endswith(".json")]
        for fname in candidates:
            try:
                fp = os.path.join(cache_dir, fname)
                d = json.load(open(fp))
                # RA cache JSON has a "Title" field matching the game name
                title = (d.get("Title") or d.get("title") or "").lower()
                gname = (game or "").lower().replace("_", " ").replace("-", " ")
                if title and gname and (gname in title or title in gname):
                    matched_file = fname
                    raw = d.get("Achievements") or d.get("achievements") or []
                    if isinstance(raw, list):
                        achievements = [_ra_fmt_achievement(a) for a in raw
                                        if isinstance(a, dict)]
                    elif isinstance(raw, dict):
                        achievements = [_ra_fmt_achievement(a) for a in raw.values()
                                        if isinstance(a, dict)]
                    state = "cached"
                    break
            except Exception:
                continue

    if state == "no_data":
        return {"ok": True, "system": system, "game": game,
                "achievements": [], "total": 0,
                "state": "no_cache",
                "note": "No achievement data cached for this game. "
                        "Launch the game once with RA enabled to download achievement data. "
                        "Homebrew and unlicensed ROMs are not in the RetroAchievements database."}

    return {"ok": True, "system": system, "game": game,
            "achievements": achievements, "total": len(achievements),
            "state": state, "cache_file": matched_file}

def _ra_fmt_achievement(a):
    """Normalize a raw RA cache achievement dict into a clean shape."""
    if not isinstance(a, dict):
        return {}
    return {
        "id": a.get("ID") or a.get("id"),
        "title": a.get("Title") or a.get("title") or a.get("name") or "",
        "description": a.get("Description") or a.get("description") or "",
        "points": a.get("Points") or a.get("points") or 0,
        "badge": a.get("BadgeName") or a.get("badge_name") or "",
        "unlocked": bool(a.get("HardcoreAchieved") or a.get("DateEarned") or
                         a.get("Unlocked") or a.get("unlocked")),
        "hardcore": bool(a.get("HardcoreAchieved") or a.get("hardcore_unlocked")),
    }

def ra_poll_unlocks():
    """GET /ra/poll — tail the RetroArch log for new achievement unlock lines
    and fire GOSE notify toasts for each.  Called from the cheevos page while
    a game is running.  Tracks byte offset so each line fires exactly once.
    Limitation: requires RA enabled + RA-supported ROM + game launched."""
    global _ra_log_pos
    unlocks = []
    with _ra_log_lock:
        if not os.path.isfile(RA_LOG):
            return {"ok": True, "unlocks": [], "state": "no_log",
                    "note": "RetroArch log not found. Launch an RA-enabled game first."}
        try:
            size = os.path.getsize(RA_LOG)
            if size < _ra_log_pos:
                _ra_log_pos = 0     # log was rotated / truncated
            if size == _ra_log_pos:
                return {"ok": True, "unlocks": [], "state": "idle"}
            with open(RA_LOG, "rb") as fh:
                fh.seek(_ra_log_pos)
                new_data = fh.read(min(size - _ra_log_pos, 65536))   # cap: 64 KB per poll
                _ra_log_pos += len(new_data)
        except Exception as e:
            return {"ok": True, "unlocks": [], "state": "error", "error": str(e)}

    # RA unlock lines look like:
    #   [CHEEVOS]: Awarded achievement "Name" (1234)
    #   [CHEEVOS]: Awarded hardcore achievement "Name" (1234)
    for line in new_data.decode("utf-8", errors="replace").splitlines():
        line = line.strip()
        if "[CHEEVOS]" not in line:
            continue
        llow = line.lower()
        if "awarded" not in llow and "unlock" not in llow:
            continue
        m = re.search(r'["“”]([^“”"]{1,200})["“”]', line)
        name = m.group(1).strip() if m else "Achievement"
        hardcore = "hardcore" in llow
        pts_m = re.search(r"\((\d+)\)", line)
        pts = int(pts_m.group(1)) if pts_m else 0
        ev = {"title": name, "hardcore": hardcore, "points": pts}
        unlocks.append(ev)
        # fire a GOSE.notify toast (auto-DND if game running is client-side)
        title = ("\U0001f3c6 " if hardcore else "⭐ ") + name
        body = ("Hardcore unlock!" if hardcore else "Achievement unlocked!") + (
            (" (%d pts)" % pts) if pts else "")
        notifications_post({"title": title, "body": body, "kind": "success", "icon": "trophy"})

    return {"ok": True, "unlocks": unlocks, "state": "ok"}

# ---- Netplay (#65): surface RetroArch netplay host/join pad-first ----------------------
# Architecture: configgen already handles the emulatorlauncher -netplaymode / -netplayip /
# -netplayport flags — our job is (a) read/write the global nickname + relay preference in
# batocera.conf, and (b) spawn emulatorlauncher with the right flags for host or join.
# The MITM relay server list mirrors what Batocera ships; "none" means direct LAN/IP.
#
# Plumbing-verified: config read/write + launch cmdline construction + kill-by-PID.
# Needs-two-peers: the full RA netplay handshake (frame-sync, rollback) requires a second
# machine — host launch spawns correctly, but the session stays in "waiting for client"
# until a peer actually connects. That is RA netplay protocol, not a GOSE bug.
#
# Autosave caveat: RA netplay + autosave can crash (libretro/RetroArch#15248).
# We always add -autosave=0 to any netplay emulatorlauncher call.

_NETPLAY_BCONF_KEYS = {
    "nickname": "global.netplay.nickname",
    "port":     "global.netplay.port",
    "relay":    "global.netplay.relay",
}
_NETPLAY_DEFAULT_PORT = "55435"
_NETPLAY_MITM_SERVERS = ["none", "nyc", "madrid", "montreal", "saopaulo", "sydney"]
_netplay_host_pid = None   # PID of the current host launch (for stop/kill)
_netplay_lock = threading.Lock()

def netplay_config_get():
    """GET /netplay/config — read netplay preferences from batocera.conf."""
    nickname = _bconf_get("global.netplay.nickname") or ""
    port = _bconf_get("global.netplay.port") or _NETPLAY_DEFAULT_PORT
    relay = _bconf_get("global.netplay.relay") or "none"
    return {
        "ok": True,
        "nickname": nickname,
        "port": port,
        "relay": relay,
        "relay_options": _NETPLAY_MITM_SERVERS,
        "default_port": _NETPLAY_DEFAULT_PORT,
        "note": "relay='none' = direct LAN/IP; any other value = RetroArch MITM relay server",
    }

def netplay_config_set(payload):
    """POST /netplay/config — write nickname / port / relay to batocera.conf atomically."""
    payload = payload or {}
    if "nickname" in payload and payload["nickname"] is not None:
        nick = str(payload["nickname"]).strip()[:32]
        _bconf_set("global.netplay.nickname", nick)
    if "port" in payload and payload["port"] is not None:
        try:
            p = int(payload["port"])
            if not (1024 <= p <= 65535):
                return {"ok": False, "error": "port must be 1024–65535"}
        except (ValueError, TypeError):
            return {"ok": False, "error": "port must be a number"}
        _bconf_set("global.netplay.port", str(p))
    if "relay" in payload and payload["relay"] is not None:
        relay = str(payload["relay"]).strip()
        if relay not in _NETPLAY_MITM_SERVERS:
            return {"ok": False, "error": "unknown relay; choose one of: " + ", ".join(_NETPLAY_MITM_SERVERS)}
        _bconf_set("global.netplay.relay", relay)
    LOG.info("netplay config updated: %s", payload)
    return netplay_config_get()

def netplay_host(payload):
    """POST /netplay/host — launch a game as a RetroArch netplay host.
    Body: {system, game}  (players optional — same as /launch).
    Returns: {ok, pid, cmdline_proof, ip_hint, port, note}
    The session waits for a client; a second peer must connect to the reported IP:port.
    Autosave disabled per RA#15248 to avoid crash on netplay connect.
    Plumbing-verified: cmdline built + process spawned + PID returned.
    Needs-two-peers: the frame-sync handshake needs an actual peer; tested single-machine only."""
    global _netplay_host_pid
    payload = payload or {}
    system = payload.get("system"); game = payload.get("game")
    if not system or not game:
        return {"ok": False, "error": "system + game required"}
    # locate ROM (same logic as launch_game)
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
            r = _dir_game_rom(system, p)
            if r and os.path.splitext(os.path.basename(r))[0] == game:
                rom = r; break
            continue
        if os.path.splitext(f)[0] == game and os.path.splitext(f)[1].lower() not in _SKIPEXT:
            rom = p; break
    if not rom:
        return {"ok": False, "error": "rom not found for " + game}
    # read current config
    nickname = _bconf_get("global.netplay.nickname") or ""
    port = _bconf_get("global.netplay.port") or _NETPLAY_DEFAULT_PORT
    relay = _bconf_get("global.netplay.relay") or "none"
    # build the emulatorlauncher command with netplay host flags
    # emulatorlauncher -system <s> -rom <r> -netplaymode host -netplayport <p> [-nick <n>]
    # autosave=0 passed via -autosave to avoid RA#15248 crash
    argv = (["emulatorlauncher"] + _virtual_pad_args() +
            ["-system", system, "-rom", rom,
             "-netplaymode", "host",
             "-netplayport", port])
    if nickname:
        argv += ["-netplaynick", nickname]
    if relay and relay != "none":
        argv += ["-netplayrelay", relay]
    # autosave-off: passed as a game-specific arg (configgen reads it if the key is set)
    # We set the per-game key momentarily; configgen will honour it at launch.
    _bconf_set("global.netplay.autosave_override", "0")
    cmdline_proof = " ".join(argv)
    try:
        env = dict(os.environ); env.setdefault("DISPLAY", ":0")
        logf = open("/userdata/gose-ui/launch.log", "ab")
        proc = subprocess.Popen(argv, env=env, stdout=logf, stderr=subprocess.STDOUT,
                                stdin=subprocess.DEVNULL, start_new_session=True)
        with _netplay_lock:
            _netplay_host_pid = proc.pid
        record_recent(system, game)
        _session_start(system, game)
        _TIMECTL["slot"] = 0; _TIMECTL["ff"] = False
        # get local IP hint for the other player
        try:
            ip_hint = socket.gethostbyname(socket.gethostname())
        except Exception:
            ip_hint = "unknown — check Settings > Network"
        return {
            "ok": True, "pid": proc.pid, "cmdline_proof": cmdline_proof,
            "ip": ip_hint, "port": port,
            "relay": relay if relay != "none" else None,
            "share_with": ("Relay: %s (no IP needed)" % relay) if relay != "none" else ("%s:%s" % (ip_hint, port)),
            "note": ("Waiting for a client to connect. "
                     "Needs-two-peers: a second machine must connect to complete the session. "
                     "Autosave disabled per RA#15248."),
        }
    except Exception as e:
        return {"ok": False, "error": str(e)}

def netplay_join(payload):
    """POST /netplay/join — connect to a netplay host as client.
    Body: {system, game, host_ip, host_port (opt)}
    Returns {ok, pid, cmdline_proof, note}.
    Plumbing-verified: cmdline built + spawned.
    Needs-two-peers: requires a live host at host_ip:host_port."""
    payload = payload or {}
    system = payload.get("system"); game = payload.get("game")
    host_ip = (payload.get("host_ip") or "").strip()
    if not system or not game:
        return {"ok": False, "error": "system + game required"}
    if not host_ip:
        return {"ok": False, "error": "host_ip required"}
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
            r = _dir_game_rom(system, p)
            if r and os.path.splitext(os.path.basename(r))[0] == game:
                rom = r; break
            continue
        if os.path.splitext(f)[0] == game and os.path.splitext(f)[1].lower() not in _SKIPEXT:
            rom = p; break
    if not rom:
        return {"ok": False, "error": "rom not found for " + game}
    nickname = _bconf_get("global.netplay.nickname") or ""
    port = str(payload.get("host_port") or _bconf_get("global.netplay.port") or _NETPLAY_DEFAULT_PORT)
    argv = (["emulatorlauncher"] + _virtual_pad_args() +
            ["-system", system, "-rom", rom,
             "-netplaymode", "client",
             "-netplayip", host_ip,
             "-netplayport", port])
    if nickname:
        argv += ["-netplaynick", nickname]
    cmdline_proof = " ".join(argv)
    try:
        env = dict(os.environ); env.setdefault("DISPLAY", ":0")
        logf = open("/userdata/gose-ui/launch.log", "ab")
        proc = subprocess.Popen(argv, env=env, stdout=logf, stderr=subprocess.STDOUT,
                                stdin=subprocess.DEVNULL, start_new_session=True)
        record_recent(system, game)
        _session_start(system, game)
        _TIMECTL["slot"] = 0; _TIMECTL["ff"] = False
        return {
            "ok": True, "pid": proc.pid, "cmdline_proof": cmdline_proof,
            "connecting_to": "%s:%s" % (host_ip, port),
            "note": ("Connecting to host. Needs-two-peers: requires a live host at %s:%s "
                     "running the same game + core." % (host_ip, port)),
        }
    except Exception as e:
        return {"ok": False, "error": str(e)}

def netplay_stop():
    """POST /netplay/stop — kill the active netplay host by PID."""
    global _netplay_host_pid
    with _netplay_lock:
        pid = _netplay_host_pid
        _netplay_host_pid = None
    if not pid:
        return {"ok": False, "error": "no active netplay host tracked (kill by PID via /proc/kill)"}
    try:
        os.kill(pid, 15)   # SIGTERM
        return {"ok": True, "killed_pid": pid}
    except ProcessLookupError:
        return {"ok": True, "killed_pid": pid, "note": "process already gone"}
    except Exception as e:
        return {"ok": False, "error": str(e)}

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
    _finalize_session()   # record playtime before killing the process
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
            "excluded": sorted(_EXCLUDE_CORES), "review": sorted(_REVIEW_CORES), "swap": _CORE_SWAP,
            "community": _src_core_rows()}   # third-party cores from added sources (docs/29)

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
# "direct" writes the fetched bytes as <dest>; "zip" extracts the named member as <dest>;
# "zipdir" extracts the archive's <strip>-prefixed members into roms/<system>/<datadir>/
# and writes <dest> as the marker file ES/the Library lists (directory-data games).
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
    # Tyrian — the FULL commercial game, made freeware by its developer Jason Emery in 2004
    # (he reacquired the rights; community-hosted at camanis.net, the mirror OpenTyrian's own
    # README points to). NOT a commercial-ROM exception: redistribution is author-sanctioned.
    # This image ships OpenTyrian v2.1 (GPLv2 engine, /usr/bin/opentyrian — verified in-guest
    # 2026-06-07); its Batocera configgen chdirs to roms/tyrian/data, so the Tyrian 2.1 data
    # set goes there and the .game marker is what ES/the Library lists. (Tyrian 2000's data
    # needs the opentyrian2000 fork, which this image does NOT ship — hence 2.1, not 2000.)
    # URL verified 2026-06-07: HEAD 200, application/zip, 4,754,048 bytes; members are a flat
    # lowercase tyrian21/ tree, exactly the filenames OpenTyrian opens.
    {"id": "tyrian", "name": "Tyrian", "system": "tyrian",
     "desc": "Legendary 1995 vertical-scrolling shmup — the full game, freeware since 2004.",
     "license": "Freeware data (Jason Emery, 2004); GPLv2 engine", "cat": "Shmup",
     "dest": "Tyrian.game",
     "kind": "zipdir", "strip": "tyrian21/", "datadir": "data",
     "url": "https://www.camanis.net/tyrian/tyrian21.zip"},
    # Blade Buster (NES homebrew shmup) was REVIEWED and SKIPPED 2026-06-07: romhacking.net
    # (its canonical host) 403s non-browser clients and prohibits hotlinking, so there is no
    # stable direct URL this installer could honestly use. Revisit if the author publishes
    # a first-party direct link.
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
    out += _src_game_rows()   # community-source entries (docs/29) — labeled via "source"
    return {"ok": True, "games": out,
            "note": "Curated free & homebrew games with verified download links. "
                    "No commercial ROMs are distributed."}

def games_install(payload):
    gid = (payload or {}).get("id", "").strip()
    if ":" in gid:                       # "<source_id>:<entry_id>" = community source entry
        return source_entry_install(gid)
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
    if g["kind"] == "zipdir":
        # whole-directory game data (e.g. Tyrian): extract the archive's <strip> members
        # into roms/<system>/<datadir>/, then write <dest> as the marker the Library lists.
        import io, zipfile
        datadir = os.path.join(sysdir, g["datadir"])
        if os.path.realpath(datadir) != datadir or not datadir.startswith(sysdir + os.sep):
            return {"ok": False, "error": "refused: data dir escapes the roms directory"}
        try:
            zf = zipfile.ZipFile(io.BytesIO(data))
            members = [m for m in zf.infolist()
                       if m.filename.startswith(g["strip"]) and not m.is_dir()]
            if not members:
                return {"ok": False, "error": "archive had no files under '%s'" % g["strip"]}
            total = 0
            for m in members:
                rel = m.filename[len(g["strip"]):]
                out = os.path.normpath(os.path.join(datadir, rel))
                if not out.startswith(datadir + os.sep):    # zip-slip confinement
                    return {"ok": False, "error": "refused: archive member escapes the data dir"}
                os.makedirs(os.path.dirname(out), exist_ok=True)
                with open(out, "wb") as f:
                    f.write(zf.read(m))
                total += m.file_size
        except Exception as e:
            return {"ok": False, "error": "extract failed: %s" % e}
        try:
            tmp = out_path + ".tmp"
            with open(tmp, "w") as f:
                f.write(g["name"] + "\n")   # marker rom; the data lives in <datadir>
                f.flush(); os.fsync(f.fileno())
            os.replace(tmp, out_path)
        except Exception as e:
            return {"ok": False, "error": "write failed: %s" % e}
        LOG.info("GAME INSTALL %s -> %s (%d files, %d bytes) + marker %s",
                 gid, datadir, len(members), total, out_path)
        return {"ok": True, "id": gid, "installed": True, "bytes": total,
                "files": len(members), "path": out_path}
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
    if ":" in gid:                       # community source entry (incl. orphans)
        return source_entry_uninstall(gid)
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
    dd = g.get("datadir")
    if dd:
        # zipdir games keep their data beside the marker — remove it too (same confinement)
        datadir = os.path.join(sysdir, dd)
        if os.path.realpath(datadir) == datadir and datadir.startswith(sysdir + os.sep) \
                and os.path.isdir(datadir):
            shutil.rmtree(datadir, ignore_errors=True)
    LOG.info("GAME UNINSTALL %s", gid)
    return {"ok": True, "id": gid, "removed": True}

# ===== Community store sources (docs/29): user-added third-party manifest repos =====
# THE LEGAL LINE: GOSE ships ONLY its built-in legal sources (the curated GAMES_CATALOG,
# the libretro buildbot, Flathub) and never pre-loads, suggests, or recommends any
# third-party content repo. The USER brings a manifest URL; adding it requires passing
# an explicit terms-acceptance screen (timestamp stored). Content legality is the
# source's and the user's responsibility — same posture as the SD-card import.
import hashlib

SOURCES_F = "/userdata/system/gose/store_sources.json"   # under the OS-protected prefix
_SRC_LOCK = threading.Lock()
_SRC_SCHEMA = 1
_SRC_MAX_MANIFEST = 2 * 1024 * 1024     # manifest fetch cap (huge-manifest hardening)
_SRC_MAX_ENTRIES = 500
_SRC_MAX_DL = 512 * 1024 * 1024         # per-entry download cap
_SRC_FETCH_T = 20                       # manifest fetch timeout (down-URL hardening)
_SRC_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_.-]{0,47}$")
_SRC_SYS_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,31}$")
# safe basename: no path separators, no leading dot — first line of traversal defense
# (the '..' check is explicit below; install re-checks with realpath confinement)
_SRC_FNAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 ._()+\[\]-]{0,79}$")
_SRC_DATADIR_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,40}$")
_SRC_SHA_RE = re.compile(r"^[0-9a-fA-F]{64}$")

# Shown read-only in the Sources tab — the ONLY sources GOSE ships with. Never extend
# this list with third-party content repos (docs/29 §0).
BUILTIN_SOURCES = [
    {"id": "official", "name": "GOSE catalog",
     "desc": "Curated free & homebrew games with author-sanctioned redistribution", "kind": "games"},
    {"id": "libretro-buildbot", "name": "libretro buildbot",
     "desc": "Official libretro nightly emulator-core builds", "kind": "emulators"},
    {"id": "flathub", "name": "Flathub",
     "desc": "Flatpak app repository", "kind": "apps"},
]

def _src_sid(url):
    # deterministic source id from the URL: re-adding the same URL updates the same record
    return "src" + hashlib.sha256((url or "").strip().lower().encode()).hexdigest()[:10]

def _http_url_ok(u, maxlen=400):
    from urllib.parse import urlparse
    if not isinstance(u, str) or not (1 <= len(u) <= maxlen):
        return False
    try:
        p = urlparse(u)
    except Exception:
        return False
    return p.scheme in ("http", "https") and bool(p.netloc)

def _is_private_ip(addr_str):
    """Return True if addr_str resolves to a private/loopback/link-local/reserved address."""
    try:
        addr = ipaddress.ip_address(addr_str)
    except ValueError:
        return True   # unparseable → treat as unsafe
    return (addr.is_private or addr.is_loopback or addr.is_link_local
            or addr.is_reserved or addr.is_multicast
            or addr.is_unspecified)

def _ssrf_check(url, allow_local=False):
    """Resolve every IP for url's hostname and refuse if any is private/loopback/reserved.
    Returns (ok: bool, error_msg: str|None).
    Defeats basic DNS-rebind by checking at fetch time, not just at add time."""
    from urllib.parse import urlparse
    try:
        host = urlparse(url).hostname
    except Exception:
        return False, "couldn't parse manifest URL"
    if not host:
        return False, "couldn't parse manifest URL (no host)"
    try:
        results = socket.getaddrinfo(host, None)
    except socket.gaierror as e:
        return False, "couldn't resolve host '%s': %s" % (host, e)
    for (_fam, _type, _proto, _cname, sockaddr) in results:
        ip = sockaddr[0]
        if _is_private_ip(ip):
            if allow_local:
                LOG.warning("STORE SSRF-OPT-IN: %s resolved to private/local %s (allow_local=True)", url, ip)
                return True, None
            return False, (
                "refused: source URL '%s' resolves to a private/local address (%s); "
                "enable 'local_source' to allow fetching from internal hosts" % (host, ip)
            )
    return True, None

def _fetch_capped(url, cap, timeout, allow_local=False):
    # bounded fetch: refuses non-http(s) schemes, SSRF targets, and anything over the byte cap.
    # allow_local=True lets the SSRF guard pass for intentional local/dev sources.
    if not _http_url_ok(url):
        raise ValueError("only http(s) URLs are supported")
    ok, err = _ssrf_check(url, allow_local=allow_local)
    if not ok:
        raise ValueError(err)
    req = urllib.request.Request(url, headers={"User-Agent": "GOSE-Store/1"})
    # redirects disabled: urllib follows them by default; we re-check the final URL instead.
    class _NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            # resolve redirects manually so we can SSRF-check the destination
            ok2, err2 = _ssrf_check(newurl, allow_local=allow_local)
            if not ok2:
                raise ValueError(err2)
            return super().redirect_request(req, fp, code, msg, headers, newurl)
    opener = urllib.request.build_opener(_NoRedirect)
    with opener.open(req, timeout=timeout) as r:
        data = r.read(cap + 1)
    if len(data) > cap:
        raise ValueError("response exceeds the %d MB cap" % max(1, cap // (1024 * 1024)))
    return data

def _str_field(d, key, maxlen, default=""):
    v = d.get(key, default)
    return v.strip()[:maxlen] if isinstance(v, str) else default

def _validate_entry(e, i, seen):
    """One manifest entry -> (clean_entry|None, error|None). Honest, specific errors."""
    if not isinstance(e, dict):
        return None, "entries[%d]: not an object" % i
    eid = e.get("id")
    tag = "entries[%d] (%s)" % (i, eid if isinstance(eid, str) else "?")
    if not isinstance(eid, str) or not _SRC_ID_RE.match(eid):
        return None, tag + ": bad id (want ^[a-z0-9][a-z0-9_.-]{0,47}$)"
    if eid in seen:
        return None, tag + ": duplicate id"
    name = _str_field(e, "name", 80)
    if not name:
        return None, tag + ": name required"
    typ = e.get("type")
    if typ not in ("game", "emulator"):
        return None, tag + ": type must be 'game' or 'emulator' (got %r)" % (typ,)
    url = e.get("url")
    if not _http_url_ok(url):
        return None, tag + ": bad download url (http/https required)"
    lic = _str_field(e, "license", 120)
    if not lic:
        return None, tag + ": license is required (honest provenance — docs/29 §2)"
    sha = e.get("sha256")
    if sha is not None and (not isinstance(sha, str) or not _SRC_SHA_RE.match(sha)):
        return None, tag + ": sha256 must be 64 hex chars"
    size = e.get("size")
    if size is not None and (not isinstance(size, int) or isinstance(size, bool)
                             or not (0 < size <= _SRC_MAX_DL)):
        return None, tag + ": size must be a positive integer <= %d" % _SRC_MAX_DL
    out = {"id": eid, "type": typ, "name": name, "url": url.strip(), "license": lic,
           "desc": _str_field(e, "desc", 300), "cat": _str_field(e, "cat", 24) or "Community"}
    if sha:
        out["sha256"] = sha.lower()
    if size:
        out["size"] = size
    if typ == "game":
        system = e.get("system")
        if not isinstance(system, str) or not _SRC_SYS_RE.match(system):
            return None, tag + ": bad system id (want ^[a-z0-9][a-z0-9_-]{0,31}$)"
        dest = e.get("dest")
        if not isinstance(dest, str) or not _SRC_FNAME_RE.match(dest) or ".." in dest:
            return None, tag + ": dest must be a safe filename (no slashes, no '..')"
        kind = e.get("kind", "direct")
        if kind not in ("direct", "zip", "zipdir"):
            return None, tag + ": kind must be direct|zip|zipdir"
        out.update(system=system, dest=dest, kind=kind)
        if kind == "zip":
            member = e.get("member")
            if not isinstance(member, str) or not (1 <= len(member) <= 200) \
                    or member.startswith("/") or ".." in member:
                return None, tag + ": zip kind needs a safe 'member' path"
            out["member"] = member
        if kind == "zipdir":
            strip = e.get("strip"); datadir = e.get("datadir")
            if not isinstance(strip, str) or not (0 < len(strip) <= 100) or ".." in strip:
                return None, tag + ": zipdir kind needs a 'strip' prefix"
            if not isinstance(datadir, str) or not _SRC_DATADIR_RE.match(datadir):
                return None, tag + ": zipdir kind needs a safe 'datadir'"
            out.update(strip=strip, datadir=datadir)
    else:   # emulator = a libretro core
        core = e.get("core")
        if not isinstance(core, str) or not _CORE_NAME_RE.match(core):
            return None, tag + ": bad core name"
        out["core"] = core
    seen.add(eid)
    return out, None

def _validate_manifest(data, url, allow_local=False):
    """Manifest bytes -> (meta dict with the VALID entries, per-entry errors/warnings).
    meta=None means the whole source is refused (the errors say exactly why).
    Warns (never hard-refuses) when a manifest is served over plain http without sha256."""
    from urllib.parse import urlparse
    try:
        doc = json.loads(data.decode("utf-8"))
    except Exception as e:
        return None, ["manifest is not valid JSON: %s" % e]
    if not isinstance(doc, dict):
        return None, ["manifest must be a JSON object"]
    if doc.get("gose_source") != _SRC_SCHEMA:
        return None, ["unsupported schema: gose_source=%r (this GOSE understands gose_source: %d)"
                      % (doc.get("gose_source"), _SRC_SCHEMA)]
    name = _str_field(doc, "name", 80)
    if not name:
        return None, ["source 'name' is required"]
    entries = doc.get("entries")
    if not isinstance(entries, list) or not entries:
        return None, ["'entries' is required and must be a non-empty list"]
    if len(entries) > _SRC_MAX_ENTRIES:
        return None, ["too many entries (%d; cap is %d)" % (len(entries), _SRC_MAX_ENTRIES)]
    valid, errors, seen = [], [], set()
    for i, e in enumerate(entries):
        ent, err = _validate_entry(e, i, seen)
        if ent:
            valid.append(ent)
        else:
            errors.append(err)
    if not valid:
        return None, errors + ["no valid entries — source refused"]
    # HTTP-without-sha256 warning: a plain-http manifest with hash-less entries is
    # MITM-swappable. Warn clearly; don't hard-refuse (user may run a LAN dev server
    # intentionally with allow_local=True).
    if urlparse(url).scheme == "http":
        http_no_hash = [e["id"] for e in valid if not e.get("sha256")]
        if http_no_hash:
            errors.append(
                "WARNING: manifest served over plain http (not https) and %d "
                "entr%s lack a sha256 hash — these downloads can be replaced by "
                "a network attacker. Use https and add sha256 for each entry."
                % (len(http_no_hash), "y" if len(http_no_hash) == 1 else "ies")
            )
    homepage = doc.get("homepage")
    meta = {"id": _src_sid(url), "url": url, "name": name,
            "description": _str_field(doc, "description", 300),
            "maintainer": _str_field(doc, "maintainer", 120),
            "homepage": homepage if _http_url_ok(homepage, 200) else "",
            "entries": valid}
    return meta, errors

def _sources_load():
    try:
        d = json.load(open(SOURCES_F))
    except Exception:
        d = {}
    if not isinstance(d, dict):
        d = {}
    d.setdefault("schema", _SRC_SCHEMA)
    d.setdefault("sources", [])
    d.setdefault("orphans", [])
    return d

def _src_counts(rec):
    g = sum(1 for e in rec["entries"] if e["type"] == "game")
    return {"games": g, "emulators": len(rec["entries"]) - g}

def _src_entry_installed(rec, e):
    ins = (rec.get("installs") or {}).get(e["id"])
    return bool(ins) and os.path.isfile(ins.get("path", ""))

def store_sources():
    with _SRC_LOCK:
        d = _sources_load()
    out = []
    for rec in d["sources"]:
        entries = []
        for e in rec["entries"]:
            ee = dict(e)
            ee["installed"] = _src_entry_installed(rec, e)
            entries.append(ee)
        out.append({"id": rec["id"], "url": rec["url"], "name": rec["name"],
                    "description": rec.get("description", ""),
                    "maintainer": rec.get("maintainer", ""),
                    "homepage": rec.get("homepage", ""),
                    "added": rec.get("added"), "accepted_terms": rec.get("accepted_terms"),
                    "refreshed": rec.get("refreshed"), "counts": _src_counts(rec),
                    "errors": rec.get("errors", []),
                    "installed": sum(1 for x in entries if x["installed"]),
                    "entries": entries})
    orphans = [o for o in d["orphans"] if o.get("path") and os.path.exists(o["path"])]
    return {"ok": True, "builtin": BUILTIN_SOURCES, "sources": out, "orphans": orphans}

def source_preview(payload):
    # fetch + validate WITHOUT storing — the preview step before the terms screen
    url = ((payload or {}).get("url") or "").strip()
    allow_local = bool((payload or {}).get("local_source"))
    if not _http_url_ok(url):
        return {"ok": False, "error": "a full http(s) manifest URL is required"}
    try:
        data = _fetch_capped(url, _SRC_MAX_MANIFEST, _SRC_FETCH_T, allow_local=allow_local)
    except Exception as e:
        return {"ok": False, "error": "couldn't fetch the manifest: %s" % e}
    meta, errors = _validate_manifest(data, url, allow_local=allow_local)
    if not meta:
        return {"ok": False, "error": "manifest refused", "errors": errors}
    with _SRC_LOCK:
        already = any(s["id"] == meta["id"] for s in _sources_load()["sources"])
    c = _src_counts(meta)
    return {"ok": True, "url": url, "id": meta["id"], "name": meta["name"],
            "description": meta["description"], "maintainer": meta["maintainer"],
            "homepage": meta["homepage"], "count": len(meta["entries"]),
            "games": c["games"], "emulators": c["emulators"],
            "errors": errors, "already": already}

def source_add(payload):
    url = ((payload or {}).get("url") or "").strip()
    allow_local = bool((payload or {}).get("local_source"))
    if not _http_url_ok(url):
        return {"ok": False, "error": "a full http(s) manifest URL is required"}
    if (payload or {}).get("accept_terms") is not True:
        # the legal line: no source lands without the user's explicit acceptance
        return {"ok": False, "error": "terms not accepted — adding a third-party source requires "
                "explicitly accepting that its content is the maintainer's and your responsibility"}
    try:
        data = _fetch_capped(url, _SRC_MAX_MANIFEST, _SRC_FETCH_T, allow_local=allow_local)
    except Exception as e:
        return {"ok": False, "error": "couldn't fetch the manifest: %s" % e}
    meta, errors = _validate_manifest(data, url, allow_local=allow_local)
    if not meta:
        return {"ok": False, "error": "manifest refused", "errors": errors}
    now = int(time.time())
    with _SRC_LOCK:
        d = _sources_load()
        old = next((s for s in d["sources"] if s["id"] == meta["id"]), None)
        rec = dict(meta)
        rec.update(errors=errors, refreshed=now,
                   added=(old or {}).get("added", now),
                   accepted_terms=(old or {}).get("accepted_terms", now),
                   installs=(old or {}).get("installs", {}),
                   local_source=allow_local or bool((old or {}).get("local_source")))
        d["sources"] = [s for s in d["sources"] if s["id"] != meta["id"]] + [rec]
        write_json_atomic(SOURCES_F, d)
    LOG.info("STORE SOURCE %s %s '%s' (%d entries, %d entry errors; terms accepted @%d)",
             "re-added" if old else "added", rec["id"], rec["name"],
             len(rec["entries"]), len(errors), rec["accepted_terms"])
    c = _src_counts(rec)
    return {"ok": True, "id": rec["id"], "name": rec["name"], "count": len(rec["entries"]),
            "games": c["games"], "emulators": c["emulators"], "errors": errors,
            "accepted_terms": rec["accepted_terms"], "updated": bool(old)}

def source_refresh(payload):
    sid = ((payload or {}).get("id") or "").strip()
    with _SRC_LOCK:
        rec = next((s for s in _sources_load()["sources"] if s["id"] == sid), None)
    if not rec:
        return {"ok": False, "error": "unknown source id"}
    allow_local = bool(rec.get("local_source"))
    try:
        data = _fetch_capped(rec["url"], _SRC_MAX_MANIFEST, _SRC_FETCH_T, allow_local=allow_local)
    except Exception as e:
        # a down manifest URL must not nuke a working source
        return {"ok": False, "error": "refresh failed (%s) — keeping the previous entry list" % e,
                "kept": len(rec["entries"])}
    meta, errors = _validate_manifest(data, rec["url"], allow_local=allow_local)
    if not meta:
        return {"ok": False, "error": "refreshed manifest refused — keeping the previous entry list",
                "errors": errors, "kept": len(rec["entries"])}
    with _SRC_LOCK:
        d = _sources_load()
        rec = next((s for s in d["sources"] if s["id"] == sid), None)
        if not rec:
            return {"ok": False, "error": "unknown source id"}
        rec.update(name=meta["name"], description=meta["description"],
                   maintainer=meta["maintainer"], homepage=meta["homepage"],
                   entries=meta["entries"], errors=errors, refreshed=int(time.time()))
        write_json_atomic(SOURCES_F, d)
    LOG.info("STORE SOURCE refreshed %s (%d entries, %d errors)", sid, len(meta["entries"]), len(errors))
    return {"ok": True, "id": sid, "count": len(meta["entries"]), "errors": errors}

def source_remove(payload):
    sid = ((payload or {}).get("id") or "").strip()
    with _SRC_LOCK:
        d = _sources_load()
        rec = next((s for s in d["sources"] if s["id"] == sid), None)
        if not rec:
            return {"ok": False, "error": "unknown source id"}
        # entries vanish from the catalog; INSTALLED FILES STAY (they're the user's),
        # recorded as orphans so they keep an honest provenance label + stay uninstallable
        orphaned, now = 0, int(time.time())
        for eid, ins in (rec.get("installs") or {}).items():
            if not (ins.get("path") and os.path.exists(ins["path"])):
                continue
            e = next((x for x in rec["entries"] if x["id"] == eid), None)
            d["orphans"].append({"source_name": rec["name"], "source_id": sid,
                                 "entry": e or {"id": eid, "name": eid, "type": "game"},
                                 "path": ins["path"], "datadir": ins.get("datadir"),
                                 "removed": now})
            orphaned += 1
        d["sources"] = [s for s in d["sources"] if s["id"] != sid]
        write_json_atomic(SOURCES_F, d)
    LOG.info("STORE SOURCE removed %s ('%s') — %d installs kept as orphans", sid, rec["name"], orphaned)
    return {"ok": True, "removed": sid, "orphaned": orphaned,
            "note": "installed files were kept on disk; they stay in the catalog labeled as "
                    "from a removed source and can still be uninstalled individually"}

def _src_game_rows():
    # third-party game entries for the merged /games/catalog — ADDITIVE fields only
    # (id/name/system/desc/license/cat/sysname/installed match the official shape, plus
    # source/source_id/orphan), so existing consumers (store page, widgets_store) are safe.
    with _SRC_LOCK:
        d = _sources_load()
    rows = []
    for rec in d["sources"]:
        for e in rec["entries"]:
            if e["type"] != "game":
                continue
            rows.append({"id": rec["id"] + ":" + e["id"], "name": e["name"],
                         "system": e["system"], "desc": e.get("desc", ""),
                         "license": e["license"], "cat": e.get("cat", "Community"),
                         "sysname": _SYS_EMU.get(e["system"], _SYS.get(e["system"], e["system"])),
                         "installed": _src_entry_installed(rec, e),
                         "source": rec["name"], "source_id": rec["id"]})
    for o in d["orphans"]:
        e = o.get("entry") or {}
        if e.get("type") == "emulator" or not (o.get("path") and os.path.isfile(o["path"])):
            continue
        system = e.get("system", "")
        rows.append({"id": (o.get("source_id") or "src") + ":" + (e.get("id") or "?"),
                     "name": e.get("name") or os.path.basename(o["path"]),
                     "system": system, "desc": e.get("desc", ""),
                     "license": e.get("license", "unknown"), "cat": e.get("cat", "Community"),
                     "sysname": _SYS_EMU.get(system, _SYS.get(system, system)),
                     "installed": True, "orphan": True,
                     "source": (o.get("source_name") or "removed source") + " (removed)",
                     "source_id": o.get("source_id")})
    return rows

def _src_core_rows():
    # third-party emulator (libretro core) entries, grouped per source, for /emulators
    with _SRC_LOCK:
        d = _sources_load()
    out = []
    for rec in d["sources"]:
        cores = []
        for e in rec["entries"]:
            if e["type"] != "emulator":
                continue
            cores.append({"id": rec["id"] + ":" + e["id"], "core": e["core"], "name": e["name"],
                          "license": e["license"], "desc": e.get("desc", ""),
                          "installed": _src_entry_installed(rec, e)})
        if cores:
            out.append({"source": rec["name"], "source_id": rec["id"], "cores": cores})
    orph = []
    for o in d["orphans"]:
        e = o.get("entry") or {}
        if e.get("type") == "emulator" and o.get("path") and os.path.isfile(o["path"]):
            orph.append({"id": (o.get("source_id") or "src") + ":" + (e.get("id") or "?"),
                         "core": e.get("core") or "?", "name": e.get("name") or "?",
                         "license": e.get("license", "unknown"), "installed": True, "orphan": True})
    if orph:
        out.append({"source": "removed sources", "source_id": None, "cores": orph, "orphan": True})
    return out

def _src_install_target(e, sid):
    """Resolve + CONFINE the install target for a validated entry.
    Returns (out_path, dataroot_or_None, error_or_None) — same realpath discipline
    as games_install/emulator_install (a21e885)."""
    if e["type"] == "game":
        sysdir = os.path.join(ROMS, e["system"])
        out_path = os.path.join(sysdir, e["dest"])
        if os.path.realpath(out_path) != out_path or not out_path.startswith(sysdir + os.sep):
            return None, None, "refused: install path escapes the roms directory"
        dataroot = None
        if e.get("kind") == "zipdir":
            # per-source subfolder hygiene: a source's data trees live under src-<id>/
            # so sources can't clobber each other's (or the official catalog's) data
            dataroot = os.path.join(sysdir, "src-" + sid, e["datadir"])
            if os.path.realpath(dataroot) != dataroot or not dataroot.startswith(sysdir + os.sep):
                return None, None, "refused: data dir escapes the roms directory"
        return out_path, dataroot, None
    out_path = os.path.join(LIBRETRO_DIR, e["core"] + "_libretro.so")
    if os.path.realpath(out_path) != out_path or not out_path.startswith(LIBRETRO_DIR + "/"):
        return None, None, "refused: install path escapes the libretro directory"
    return out_path, None, None

def _write_atomic_bytes(path, blob, mode=None):
    tmp = path + ".tmp"
    with open(tmp, "wb") as f:
        f.write(blob); f.flush(); os.fsync(f.fileno())
    if mode is not None:
        os.chmod(tmp, mode)
    os.replace(tmp, path)

def source_entry_install(gid):
    # install one entry from an added source ("<source_id>:<entry_id>") — routed here
    # by games_install so it shares the /games/install rate limit + UI plumbing
    sid, _, eid = gid.partition(":")
    with _SRC_LOCK:
        rec = next((s for s in _sources_load()["sources"] if s["id"] == sid), None)
    e = rec and next((x for x in rec["entries"] if x["id"] == eid), None)
    if not e:
        return {"ok": False, "error": "unknown source entry (was the source removed?)"}
    out_path, dataroot, err = _src_install_target(e, sid)
    if err:
        LOG.warning("STORE SOURCE install REFUSED %s: %s", gid, err)
        return {"ok": False, "error": err}
    ins = (rec.get("installs") or {}).get(eid)
    if os.path.exists(out_path):
        if ins and ins.get("path") == out_path:
            return {"ok": True, "id": gid, "installed": True, "already": True, "path": out_path}
        # ownership guard: never silently overwrite a file this source didn't install
        return {"ok": False, "error": "refused: %s already exists and wasn't installed by this "
                "source — remove that file first if you really want this entry" % out_path}
    try:
        data = _fetch_capped(e["url"], _SRC_MAX_DL, 60)
    except Exception as ex:
        return {"ok": False, "error": "download failed: %s" % ex, "url": e["url"]}
    if e.get("sha256"):
        got = hashlib.sha256(data).hexdigest()
        if got != e["sha256"]:
            LOG.warning("STORE SOURCE sha256 MISMATCH %s (manifest %s, got %s)", gid, e["sha256"], got)
            return {"ok": False, "error": "sha256 mismatch — install refused "
                    "(manifest says %s, downloaded %s)" % (e["sha256"], got)}
    note = None
    if e.get("size") and e["size"] != len(data):
        note = "size differs from the manifest (%d vs %d bytes)" % (len(data), e["size"])
    files = None
    try:
        if e["type"] == "emulator":
            blob = data
            if data[:4] == b"PK\x03\x04":   # buildbot-style zip holding the .so
                import io, zipfile
                zf = zipfile.ZipFile(io.BytesIO(data))
                member = next((n for n in zf.namelist() if n.endswith(".so")), None)
                if not member:
                    return {"ok": False, "error": "no .so found in the downloaded archive"}
                blob = zf.read(member)
            if len(blob) < 4096:
                return {"ok": False, "error": "downloaded core looks corrupt (%d bytes)" % len(blob)}
            _write_atomic_bytes(out_path, blob, mode=0o755)
        elif e["kind"] == "zipdir":
            import io, zipfile
            zf = zipfile.ZipFile(io.BytesIO(data))
            members = [m for m in zf.infolist()
                       if m.filename.startswith(e["strip"]) and not m.is_dir()]
            if not members:
                return {"ok": False, "error": "archive had no files under '%s'" % e["strip"]}
            total = 0
            for m in members:
                rel = m.filename[len(e["strip"]):]
                out = os.path.normpath(os.path.join(dataroot, rel))
                if not out.startswith(dataroot + os.sep):    # zip-slip confinement
                    return {"ok": False, "error": "refused: archive member escapes the data dir"}
                os.makedirs(os.path.dirname(out), exist_ok=True)
                with open(out, "wb") as f:
                    f.write(zf.read(m))
                total += m.file_size
            os.makedirs(os.path.dirname(out_path), exist_ok=True)
            _write_atomic_bytes(out_path, (e["name"] + "\n").encode())   # the marker the Library lists
            files = len(members)
        else:
            blob = data
            if e["kind"] == "zip":
                import io, zipfile
                zf = zipfile.ZipFile(io.BytesIO(data))
                blob = zf.read(e["member"])
            if len(blob) < 64:
                return {"ok": False, "error": "downloaded file looks corrupt (%d bytes)" % len(blob)}
            os.makedirs(os.path.dirname(out_path), exist_ok=True)
            _write_atomic_bytes(out_path, blob)
    except Exception as ex:
        return {"ok": False, "error": "install failed: %s" % ex}
    with _SRC_LOCK:
        d = _sources_load()
        rec2 = next((s for s in d["sources"] if s["id"] == sid), None)
        if rec2 is not None:
            rec2.setdefault("installs", {})[eid] = {
                "path": out_path, "datadir": dataroot,   # THIS entry's tree only
                "t": int(time.time()), "sha256_verified": bool(e.get("sha256"))}
            write_json_atomic(SOURCES_F, d)
    LOG.info("STORE SOURCE INSTALL %s -> %s (%d bytes%s, sha256 %s)", gid, out_path, len(data),
             (", %d files" % files) if files else "",
             "verified" if e.get("sha256") else "not provided")
    res = {"ok": True, "id": gid, "installed": True, "path": out_path, "bytes": len(data),
           "sha256_verified": bool(e.get("sha256")), "source": rec["name"]}
    if note:
        res["note"] = note
    if files:
        res["files"] = files
    return res

def source_entry_uninstall(gid):
    sid, _, eid = gid.partition(":")
    with _SRC_LOCK:
        d = _sources_load()
        rec = next((s for s in d["sources"] if s["id"] == sid), None)
        if rec:
            ins = (rec.get("installs") or {}).get(eid)
            orph = None
        else:
            orph = next((o for o in d["orphans"] if o.get("source_id") == sid
                         and (o.get("entry") or {}).get("id") == eid), None)
            ins = orph
    if not ins or not ins.get("path"):
        return {"ok": False, "error": "not installed (no install record for this entry)"}
    path = ins["path"]
    # confinement on the RECORDED path too (defense against a tampered store file)
    if os.path.realpath(path) != path or not (path.startswith(ROMS + os.sep)
                                              or path.startswith(LIBRETRO_DIR + "/")):
        return {"ok": False, "error": "refused: recorded path is outside the install roots"}
    try:
        if os.path.isfile(path):
            os.remove(path)
    except Exception as ex:
        return {"ok": False, "error": str(ex)}
    dd = ins.get("datadir")
    if dd and os.path.realpath(dd) == dd and dd.startswith(ROMS + os.sep) and os.path.isdir(dd):
        shutil.rmtree(dd, ignore_errors=True)
        try:
            os.rmdir(os.path.dirname(dd))   # drop the src-<id> parent if now empty
        except Exception:
            pass
    with _SRC_LOCK:
        d = _sources_load()
        rec2 = next((s for s in d["sources"] if s["id"] == sid), None)
        if rec2 is not None:
            (rec2.get("installs") or {}).pop(eid, None)
        else:
            d["orphans"] = [o for o in d["orphans"] if not (o.get("source_id") == sid
                            and (o.get("entry") or {}).get("id") == eid)]
        write_json_atomic(SOURCES_F, d)
    LOG.info("STORE SOURCE UNINSTALL %s (%s)", gid, path)
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
                    chk = rom_check(p, system=sysid)
                    by_sys.setdefault(sysid, []).append({"file": f, "path": p, "size": sz,
                                                         "integrity": chk})
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
    suspect_files = []   # integrity: files that passed the copy but looked damaged at source
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
            # integrity check on source before copying (fast: size+header only, no hash)
            chk = g.get("integrity") or rom_check(src, system=s["system"])
            if not chk.get("ok"):
                suspect_files.append({"file": g["file"], "system": s["system"],
                                      "reason": chk.get("reason", "suspect")})
                LOG.warning("ROM INTEGRITY suspect at import: %s (%s) — %s",
                            g["file"], s["system"], chk.get("reason", "suspect"))
                # We still copy it: the check is advisory. A "bad header" for a multi-game
                # .zip or an unrecognized variant should not silently skip the user's game.
                # The suspect list is returned so the UI can surface it.
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
    LOG.info("STORAGE import %s: +%d skipped=%d errors=%d suspect=%d aborted=%s",
             vol_id, imported, skipped, len(errors), len(suspect_files), aborted)
    # Auto-scrape-on-import: fill cover art for the just-imported systems. OPT-IN ONLY — gated on the
    # SAME privacy flag as the boot pass (scraping leaks ROM filenames; default OFF, docs/24). Runs on
    # a daemon thread so the import response is never blocked by the (flaky) network; force=False so it
    # only hits the net for the genuinely-new titles and skips anything already known.
    if imported and by_system and os.path.exists(SCRAPE_AUTO_FLAG):
        syslist = [s for s in by_system if s in _LIBRETRO_SYS]
        if syslist:
            def _post_import_scrape():
                try:
                    st2 = _scrape_state()
                    for s in syslist:
                        scrape_system(s, force=False, state=st2)
                        write_json_atomic(SCRAPE_STATE_F, st2)   # checkpoint per system
                        time.sleep(0.3)
                    LOG.info("post-import auto-scrape done for %s", syslist)
                except Exception as e:
                    LOG.warning("post-import auto-scrape failed: %s", e)
            threading.Thread(target=_post_import_scrape, daemon=True).start()
            LOG.info("post-import auto-scrape queued for %s", syslist)
    return {"ok": True, "vol_id": vol_id, "imported": imported, "skipped": skipped,
            "errors": errors, "by_system": by_system, "aborted": aborted,
            "suspect": suspect_files}

# ===== ROM INTEGRITY CHECK (task #47) =====
# Fast sanity layer run at import-time (size + header magic only — no hash).
# Hash is opt-in on the on-demand /rom/check endpoint (with_hash=True) so the
# hot import path is never blocked by hashing a 4 GB CHD or ISO.
#
# Per-system magic table.  Two entries per rule:
#   magic_offset  (int)  — byte offset of the magic bytes in the file
#   magic_bytes   (bytes)  — the expected prefix at that offset
# Anything not in the table gets status "ok" / verified=False ("unknown format,
# can't verify") — never a false flag.
#
# Sources: public ROM-format specs + libretro core documentation.
_ROM_MAGIC = {
    # iNES / NES 2.0  "NES\x1a"
    "nes":        [(0, b"NES\x1a")],
    # SNES: either a plain ROM (header at 0x200 for headered or 0 for unheadered)
    # or SFC — no universal magic; leave as unverified (common, hard to check fast)
    "snes":       [],
    # Game Boy / GBC — Nintendo logo bytes start at 0x104
    "gb":         [(0x104, b"\xce\xed\x66\x66\xcc\x0d\x00\x0b")],
    "gbc":        [(0x104, b"\xce\xed\x66\x66\xcc\x0d\x00\x0b")],
    # GBA cartridge header: fixed word at 0x04 (entry point area) + Nintendo logo at 0xA0
    "gba":        [(0x04, b"\x2e\x00\x00\xea"), (0xA0, b"NINTENDO")],
    # N64: two known byte-order magic values
    "n64":        [(0, b"\x80\x37\x12\x40"),    # big-endian .z64
                   (0, b"\x37\x80\x40\x12"),    # byteswapped .v64
                   (0, b"\x40\x12\x37\x80")],   # little-endian .n64 — any one match is ok
    # PS1 CD image (MODE2/XA): sync bytes
    "psx":        [(0, b"\x00\xff\xff\xff\xff\xff\xff\xff\xff\xff\xff\x00")],
    # PS2 CD image: same sync
    "ps2":        [(0, b"\x00\xff\xff\xff\xff\xff\xff\xff\xff\xff\xff\x00")],
    # Sega Mega Drive / Genesis: "SEGA" at 0x100 (TMSS string in ROM header)
    "megadrive":  [(0x100, b"SEGA")],
    # Sega Master System / Game Gear: TMR SEGA string at 0x7FF0 (common 32 KB ROMs)
    "mastersystem": [(0x7FF0, b"TMR SEGA")],
    "gamegear":   [(0x7FF0, b"TMR SEGA")],
    # Sega Saturn: first 16 bytes of sector 0
    "saturn":     [(0, b"SEGA SEGASATURN ")],
    # Sega Dreamcast: GD-ROM header
    "dreamcast":  [(0, b"SEGA SEGAKATANA ")],
    # PC Engine / TurboGrafx-16: no universal header; skip
    "pcengine":   [],
    # NDS ROM: fixed magic at 0xC0
    "nds":        [(0xC0, b"\x24\xff\xae\x51\x69\x9a\xa2\x21\x3d\x84\x82\x0a\x84\xe4\x09\xad")],
    # 3DS: NCCH magic at 0x100
    "3ds":        [(0x100, b"NCCH")],
    # PSP ISO (UMD image as .iso): UMD magic
    "psp":        [(0, b"\x00\xcd\x00\x01\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00")],
    # Zip-based ROM sets (zip, .apk, etc.)
    "mame":       [(0, b"PK\x03\x04")],
    # Arcade: also zip
    "fba":        [(0, b"PK\x03\x04")],
    # Wii / GameCube disc image: Wii magic word at 0x18
    "wii":        [(0x18, b"\x5d\x1c\x9e\xa3")],
    "gamecube":   [(0x1c, b"\xc2\x33\x9f\x3d")],
    # 7z (common archive for ROM sets)
    # covered by extension check; magic is 7z¼¯' (6 bytes) — added here for completeness
}
# Extensions that are inherently zip/archive-shaped
_ZIP_EXTS = {".zip", ".apk", ".cbz"}
# 7z magic
_7Z_MAGIC = b"7z\xbc\xaf\x27\x1c"

# Hash-cap: skip per-file hashing above this size on the hot path;
# on-demand /rom/check?hash=1 will hash regardless (caller's choice).
_HASH_CAP_BYTES = 256 * 1024 * 1024   # 256 MB

def rom_check(path, system=None, with_hash=False):
    """Return a dict describing the integrity of a ROM file.

    Fields:
      ok          bool   — True = file is probably fine; False = suspect
      verified    bool   — True = a system-specific check confirmed the format;
                           False = format unknown / no rule (ok is still True)
      status      str    — "ok" | "suspect"
      reason      str    — human-readable; present when status=="suspect" or verified==False
      size        int    — file size in bytes
      md5         str    — hex md5 (only if with_hash=True and size <= _HASH_CAP_BYTES or forced)
      crc32       str    — 8-char hex crc32 (same gating)
    """
    try:
        st = os.stat(path)
    except OSError as e:
        return {"ok": False, "verified": False, "status": "suspect",
                "reason": "unreadable: %s" % e, "size": 0}

    size = st.st_size
    if size == 0:
        return {"ok": False, "verified": False, "status": "suspect",
                "reason": "zero bytes", "size": 0}

    result = {"ok": True, "verified": False, "status": "ok", "size": size}

    # --- header / magic check ---
    ext = os.path.splitext(path)[1].lower()

    # zip-shaped files: any system may arrive as .zip; check PK magic
    is_zip_ext = ext in _ZIP_EXTS
    # 7z check
    is_7z_ext = ext == ".7z"

    rules = _ROM_MAGIC.get(system or "", None)

    try:
        # Read only as many bytes as the deepest rule needs (capped at 16 KB for sanity)
        max_offset = 0
        max_bytes  = 0
        checks_to_run = []

        if is_zip_ext:
            checks_to_run = [(0, b"PK\x03\x04")]
            max_offset = 0; max_bytes = 4
        elif is_7z_ext:
            checks_to_run = [(0, _7Z_MAGIC)]
            max_offset = 0; max_bytes = 6
        elif rules is not None:
            checks_to_run = rules
            if rules:
                for off, magic in rules:
                    max_bytes = max(max_bytes, off + len(magic))
                max_offset = max(off for off, _ in rules)
            # else: rules = [] means "known system, no cheap header" → verified=False, ok=True

        if checks_to_run:
            read_len = min(max_bytes, 16384)
            if size < read_len:
                result.update(ok=False, status="suspect",
                              reason="truncated (file smaller than expected header region)")
                # skip further checks; fall through to hash if requested
            else:
                with open(path, "rb") as fh:
                    header = fh.read(read_len)
                matched_any = False
                for off, magic in checks_to_run:
                    if len(header) >= off + len(magic) and header[off:off+len(magic)] == magic:
                        matched_any = True
                        break
                if matched_any:
                    result["verified"] = True
                else:
                    ext_desc = ext or "unknown"
                    sys_desc = system or "unknown"
                    result.update(ok=False, status="suspect",
                                  reason="bad header for %s (ext %s)" % (sys_desc, ext_desc))
        elif rules is None and not is_zip_ext and not is_7z_ext:
            # completely unknown system + extension
            result["reason"] = "unknown format, can't verify"

    except OSError as e:
        result.update(ok=False, status="suspect", reason="read error: %s" % e)

    # --- optional hash (md5 + crc32) ---
    if with_hash:
        try:
            m = hashlib.md5()
            c = 0
            with open(path, "rb") as fh:
                for chunk in iter(lambda: fh.read(65536), b""):
                    m.update(chunk)
                    c = zlib.crc32(chunk, c)
            result["md5"]   = m.hexdigest()
            result["crc32"] = "%08x" % (c & 0xFFFFFFFF)
        except OSError as e:
            result["hash_error"] = str(e)

    return result

def rom_check_endpoint(qs):
    """Handler for GET /rom/check?path=...&system=...&hash=0|1"""
    path = qs.get("path", "")
    if not path:
        return {"ok": False, "error": "missing path"}
    # confinement: only files under the roms tree or /media mounts
    rp = os.path.realpath(path)
    if not (rp.startswith(ROMS + "/") or rp.startswith("/media/")):
        return {"ok": False, "error": "refused: path outside roms tree"}
    system = qs.get("system") or None
    with_hash = qs.get("hash", "0") not in ("0", "false", "")
    r = rom_check(rp, system=system, with_hash=with_hash)
    r["path"] = path
    return r

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

# ---- Pre-launch PARTY/SEAT LOBBY state (docs/27): who can play + the seat->player mapping
#      the launcher WILL use. Read-only; the lobby page POSTs the chosen order to /launch as
#      {players:[event-path,...]} (consumed by _virtual_pad_args(order=)). This endpoint NEVER
#      mutates anything — it derives a view over _player_devices (the launch source of truth),
#      the controller registry (source chips / OS-admin), and the AI grant store (seated AIs). ----
_AI_SEAT_RE = re.compile(r"AI virtual controller\s+(\d+)", re.I)

def lobby_state():
    # available players: every player-capable pad in the EXACT order/identity the launcher sees
    # (_player_devices), enriched with registry source/admin flags so the page shows source chips
    # and the OS-admin/dev-pad badges without a second, drift-prone enumeration.
    all_js, devices = _player_devices()
    reg = {p["path"]: p for p in _parse_controllers() if p.get("path")}
    admin, _stored = _effective_admin(list(reg.values()))
    # seated AIs: grant.seat (1-4, play/admin tier) pins an AI to the N-th virtual pad in js
    # order == "AI virtual controller N" (docs/27 §6, agent _pin_seat). This is the AGENT-side
    # pin — surfaced here for display; the launcher only maps evdev devices to -pN slots.
    grants = _ai_grants_load()
    seat_ai = {}
    for name, rec in grants.items():
        s = rec.get("seat")
        if s and ai_tier(name) in ("play", "admin"):
            try:
                seat_ai.setdefault(int(s), []).append(name)
            except (TypeError, ValueError):
                pass
    avail, vi = [], 0
    for d in devices:
        rp = reg.get(d["path"], {})
        item = {"path": d["path"], "name": d["name"], "source": d["source"],
                "guid": d["guid"], "js": d["js"],
                "id": rp.get("id"), "is_dev": bool(rp.get("is_dev")),
                "is_os_admin": (rp.get("id") == admin) if rp.get("id") else False}
        if d["source"] == "virtual":
            m = _AI_SEAT_RE.search(d["name"] or "")
            vi += 1
            seat = int(m.group(1)) if m else vi      # name's N, else js-order ordinal
            item["seat"] = seat
            item["ai"] = seat_ai.get(seat) or []     # AI agent(s) pinned to this seat (display)
        avail.append(item)
    # default_order = exactly what _virtual_pad_args() emits with no override (P1 first),
    # capped at the lobby's 4 seats — the honest "this is what launches if you change nothing".
    default_order = [{"slot": i + 1, "path": d["path"], "name": d["name"], "source": d["source"]}
                     for i, d in enumerate(devices[:4])]
    return {"ok": True, "max_players": 4, "available": avail, "default_order": default_order,
            "grants": {n: {"tier": ai_tier(n), "seat": grants[n].get("seat")} for n in grants}}

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
                     "snapmode", "act",
                     # full shell refresh (asset deploys / recovery) — the shell's run()
                     # already handles it (gose-wm.js); this makes POST /wm/reload reach it
                     "reload"}
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
        if route == "/diag/health":
            return self._json(diag_health())
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
        if route == "/game/stats":
            q = self._qs()
            if q.get("system") and q.get("game"):
                return self._json(_game_stats_one(q["system"], q["game"]))
            return self._json(_game_stats_all())
        if route == "/game/gallery":
            return self._json(game_gallery())
        if route == "/game/fps":
            return self._json(fps_get())
        if route == "/favorites.json":
            return self._json(favorites_json())
        if route == "/collections":
            return self._json(collections_list())
        if route.startswith("/collections/") and not route.endswith("/add") \
                and not route.endswith("/remove") and not route.endswith("/delete"):
            coll_id = route[len("/collections/"):]
            return self._json(collection_get(coll_id))
        if route == "/game/state/slots":
            from urllib.parse import parse_qs
            q = parse_qs(self.path.split("?", 1)[1] if "?" in self.path else "")
            return self._json(game_state_slots((q.get("system") or [""])[0], (q.get("game") or [""])[0]))
        if route == "/game/timectl":
            return self._json(game_timectl())
        if route == "/bios/status":
            return self._json(bios_status(self._qs().get("system") or None))
        if route == "/apps/moonlight":
            return self._json(moonlight_status())
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
        if route == "/ai/activity":
            return self._json(ai_activity(self._qs().get("limit", 50)))
        if route == "/game/options":
            q = self._qs()
            return self._json(game_options(q.get("system", ""), q.get("game", "")))
        if route == "/game/shader":
            q = self._qs()
            return self._json(game_shader(q.get("system", ""), q.get("game", "")))
        if route == "/game/cheats":
            q = self._qs()
            return self._json(game_cheats(q.get("system", ""), q.get("game", "")))
        if route == "/sys/hud":
            return self._json(hud_get())
        if route == "/net/wifi/status":
            return self._json(net_wifi_status())
        if route == "/oobe/status":
            return self._json(oobe_status())
        if route == "/auth/pin":
            return self._json(pin_status())
        if route == "/storage.json":
            return self._json(storage_info())
        if route == "/storage/breakdown":
            return self._json(storage_breakdown())
        if route == "/storage/group":
            return self._json(storage_group(self._qs().get("key")))
        if route == "/storage/pending":
            return self._json(storage_pending())
        if route == "/rom/check":
            return self._json(rom_check_endpoint(self._qs()))
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
        if route == "/store/sources":
            return self._json(store_sources())
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
        if route == "/sys/audio-devices":
            return self._json(sys_audio_devices())
        if route == "/sys/audio-device":
            return self._json(sys_audio_devices())   # GET: return current + list
        if route == "/sys/brightness":
            return self._json(sys_brightness_host())
        if route == "/sys/perf":
            return self._json(sys_perf_host())
        if route == "/sys/battery":
            return self._json(battery_info())
        if route == "/sys/ssh":
            return self._json(sys_ssh())
        if route == "/security/ssh":
            return self._json(security_ssh_state())
        if route == "/security/smb":
            return self._json(security_smb_state())
        if route == "/ra/state":
            return self._json(ra_state_get())
        if route == "/ra/achievements":
            q = self._qs()
            return self._json(ra_achievements_get(q.get("system", ""), q.get("game", "")))
        if route == "/ra/poll":
            return self._json(ra_poll_unlocks())
        if route == "/netplay/config":
            return self._json(netplay_config_get())
        if route == "/sys/display":
            return self._json(sys_display())
        if route == "/sys/vsync":
            return self._json(sys_vsync())
        if route == "/sys/timezone":
            return self._json(sys_timezone())
        if route == "/ui/prefs":
            return self._json(ui_prefs_get())
        if route == "/privacy":
            return self._json(privacy_get())
        if route == "/notifications":
            return self._json(notifications_get())
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
        if route == "/lobby/state":
            return self._json(lobby_state())
        if route == "/net/scan":
            return self._json(host_bridge("/wifi/scan"))
        if route == "/net/wifi":
            return self._json(host_bridge("/wifi/status"))
        if route == "/net/connections":
            return self._json(net_connections())
        if route == "/capture/buffer":
            return self._json(host_bridge("/clip/status"))
        if route == "/bt/status":
            return self._json(bt_status())
        if route == "/peripherals":
            return self._json(peripherals())
        if route == "/apps.json":
            return self._json(installed_apps())
        if route == "/game/state/thumb":
            q = self._qs()
            p = latest_state_thumb(q.get("system", ""), q.get("game", ""))   # path-confined to /userdata/saves
            if not p or not os.path.isfile(p):
                self.send_error(404); return
            try:
                with open(p, "rb") as fh:
                    data = fh.read()
                self.send_response(200); self.send_header("Content-Type", "image/png")
                self.send_header("Content-Length", str(len(data))); self.end_headers(); self.wfile.write(data)
            except Exception:
                self.send_error(500)
            return
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
        if route == "/diag/bundle":
            return self._json(diag_bundle())
        if route == "/diag/bundle/delete":
            try:
                n = int(self.headers.get("Content-Length", 0))
                payload = json.loads(self.rfile.read(n).decode() or "{}") if n else {}
            except Exception:
                payload = {}
            return self._json(diag_bundle_delete(payload.get("file", "")))
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
        if route in ("/store/sources/preview", "/store/sources/add",
                     "/store/sources/remove", "/store/sources/refresh"):
            try:
                n = int(self.headers.get("Content-Length", 0))
                payload = json.loads(self.rfile.read(n).decode() or "{}") if n else {}
            except Exception:
                payload = {}
            if route.endswith("/preview"):
                return self._json(source_preview(payload))
            if route.endswith("/add"):
                return self._json(source_add(payload))
            if route.endswith("/remove"):
                return self._json(source_remove(payload))
            return self._json(source_refresh(payload))
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
        if route in ("/storage/detected", "/storage/import", "/storage/dismiss", "/storage/removed",
                     "/storage/delete"):
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
            if route == "/storage/delete":
                return self._json(storage_delete(payload))
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
        if route in ("/auth/pin", "/auth/pin/set"):
            try:
                n = int(self.headers.get("Content-Length", 0))
                payload = json.loads(self.rfile.read(n).decode() or "{}") if n else {}
            except Exception:
                payload = {}
            if route == "/auth/pin/set":
                return self._json(pin_set(payload))
            return self._json(pin_verify(payload))
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
        if route == "/collections":
            try:
                n = int(self.headers.get("Content-Length", 0))
                payload = json.loads(self.rfile.read(n).decode() or "{}") if n else {}
            except Exception:
                payload = {}
            return self._json(collection_create(payload))
        if route.startswith("/collections/"):
            try:
                n = int(self.headers.get("Content-Length", 0))
                payload = json.loads(self.rfile.read(n).decode() or "{}") if n else {}
            except Exception:
                payload = {}
            parts = route[len("/collections/"):].rsplit("/", 1)
            if len(parts) == 2:
                coll_id, verb = parts
                if verb == "add":
                    return self._json(collection_add_game(coll_id, payload))
                if verb == "remove":
                    return self._json(collection_remove_game(coll_id, payload))
                if verb == "delete":
                    return self._json(collection_delete(coll_id))
            return self._json({"ok": False, "error": "unknown collection verb"})
        if route == "/game/savestate":
            return self._json(game_state("save"))
        if route == "/game/loadstate":
            return self._json(game_state("load"))
        if route in ("/game/shader", "/game/cheat", "/sys/hud", "/net/wifi/toggle"):
            try:
                n = int(self.headers.get("Content-Length", 0))
                payload = json.loads(self.rfile.read(n).decode() or "{}") if n else {}
            except Exception:
                payload = {}
            if route == "/game/shader":
                return self._json(set_game_shader(payload))
            if route == "/game/cheat":
                return self._json(set_game_cheat(payload))
            if route == "/sys/hud":
                return self._json(hud_set(payload.get("mode")))
            return self._json(net_wifi_toggle(payload.get("on")))
        if route in ("/game/slot", "/game/ff", "/game/rewind"):
            try:
                n = int(self.headers.get("Content-Length", 0))
                payload = json.loads(self.rfile.read(n).decode() or "{}") if n else {}
            except Exception:
                payload = {}
            if route == "/game/ff":
                return self._json(game_ff(payload.get("on")))
            if route == "/game/rewind":
                return self._json(game_rewind(payload.get("on")))
            return self._json(game_slot(payload.get("dir") or payload.get("direction")))
        if route in ("/scrape", "/game/options", "/game/scrape"):
            try:
                n = int(self.headers.get("Content-Length", 0))
                payload = json.loads(self.rfile.read(n).decode() or "{}") if n else {}
            except Exception:
                payload = {}
            if route == "/game/options":
                return self._json(set_game_options(payload))
            if route == "/game/scrape":
                return self._json(scrape_game(payload.get("system", ""), payload.get("game", "")))
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
                if payload.get("on") and not _capture_allowed():
                    return self._json({"ok": False,
                                       "error": "screen capture is set to Never in Settings > Privacy"})
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
        if route == "/ra/credentials":
            try:
                n = int(self.headers.get("Content-Length", 0))
                payload = json.loads(self.rfile.read(n).decode() or "{}") if n else {}
            except Exception:
                payload = {}
            return self._json(ra_credentials_set(payload))
        if route in ("/netplay/config", "/netplay/host", "/netplay/join", "/netplay/stop"):
            try:
                n = int(self.headers.get("Content-Length", 0))
                payload = json.loads(self.rfile.read(n).decode() or "{}") if n else {}
            except Exception:
                payload = {}
            if route == "/netplay/config":
                return self._json(netplay_config_set(payload))
            if route == "/netplay/host":
                return self._json(netplay_host(payload))
            if route == "/netplay/join":
                return self._json(netplay_join(payload))
            return self._json(netplay_stop())
        if route == "/sys/audio-device":
            try:
                n = int(self.headers.get("Content-Length", 0))
                payload = json.loads(self.rfile.read(n).decode() or "{}") if n else {}
            except Exception:
                payload = {}
            return self._json(sys_audio_device_set(payload.get("device")))
        if route in ("/ui/prefs", "/privacy", "/sys/ssh", "/sys/display", "/sys/vsync",
                     "/sys/timezone"):
            try:
                n = int(self.headers.get("Content-Length", 0))
                payload = json.loads(self.rfile.read(n).decode() or "{}") if n else {}
            except Exception:
                payload = {}
            if route == "/ui/prefs":
                return self._json(ui_prefs_set(payload))
            if route == "/privacy":
                return self._json(privacy_set(payload))
            if route == "/sys/ssh":
                # legacy toggle: a STATE change is owner-only now (docs/31 SB-1) — closes the
                # ungated-enable bypass; the canonical, credential-generating path is /security/ssh.
                if payload.get("enabled") is not None and not _owner_ok(payload):
                    return self._json({"ok": False, "code": "ERR_NOT_OWNER",
                                       "error": "owner authorization required — SSH is owner-only "
                                                "(docs/16/31); use Settings > Security"})
                return self._json(sys_ssh(payload.get("enabled")))
            if route == "/sys/display":
                return self._json(sys_display(payload.get("mode")))
            if route == "/sys/vsync":
                return self._json(sys_vsync(payload.get("on")))
            return self._json(sys_timezone(payload.get("tz")))
        if route == "/security/ssh":
            try:
                n = int(self.headers.get("Content-Length", 0))
                payload = json.loads(self.rfile.read(n).decode() or "{}") if n else {}
            except Exception:
                payload = {}
            return self._json(security_ssh(payload))
        if route == "/security/smb":
            try:
                n = int(self.headers.get("Content-Length", 0))
                payload = json.loads(self.rfile.read(n).decode() or "{}") if n else {}
            except Exception:
                payload = {}
            return self._json(security_smb(payload))
        if route in ("/notifications", "/notifications/read", "/notifications/clear"):
            try:
                n = int(self.headers.get("Content-Length", 0))
                payload = json.loads(self.rfile.read(n).decode() or "{}") if n else {}
            except Exception:
                payload = {}
            if route == "/notifications/read":
                return self._json(notifications_read(payload))
            if route == "/notifications/clear":
                return self._json(notifications_clear(payload))
            return self._json(notifications_post(payload))
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
threading.Thread(target=_session_watcher, daemon=True).start()  # playtime: finalize session on SIGKILL/unexpected exit
_PORT = int(os.environ.get("GOSE_UI_PORT") or 8780)   # override = isolated test instances
print("serving GOSE UI + live /status.json on 127.0.0.1:%d (threaded)" % _PORT)
# threaded: a slow /fs/sizes (du) or the 4s agent socket no longer blocks page loads
Server(("127.0.0.1", _PORT), h).serve_forever()
