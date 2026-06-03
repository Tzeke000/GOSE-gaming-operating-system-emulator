"""Games capability: enumerate systems/titles and launch/stop emulators.

Systems are subdirectories of roms_dir (the ROCKNIX/Batocera convention). Titles
come from gamelist.xml when present, else from listing ROM files. Launch uses a
per-system command template from config; with no template (or on a non-device),
it runs in a safe "dry" mode that still tracks a simulated process so the rest of
the protocol (events, games.stop) is exercisable.
"""
from __future__ import annotations

import os
import shlex
import subprocess
import time
import xml.etree.ElementTree as ET
from typing import Callable, Dict, List, Optional

from ..protocol import AgentError, ERR_ARGS

# Common ROM extensions per ext-less listing fallback.
_ROM_HINT_EXT = {".iso", ".cso", ".chd", ".bin", ".cue", ".zip", ".7z", ".nsp",
                 ".xci", ".gba", ".gb", ".gbc", ".nes", ".sfc", ".smc", ".n64",
                 ".z64", ".gcm", ".rvz", ".pbp", ".elf"}


class GamesCapability:
    def __init__(self, roms_dir: str, launch_templates: Dict[str, str],
                 emit: Optional[Callable[[str, dict], None]] = None):
        self.roms_dir = roms_dir
        self.launch_templates = launch_templates or {}
        self.emit = emit or (lambda *_: None)
        self._proc: Optional[subprocess.Popen] = None
        self._sim: Optional[Dict] = None  # simulated launch (dry mode)
        self.backend = "real" if os.path.isdir(roms_dir) else "dry"

    def systems(self) -> Dict:
        if not os.path.isdir(self.roms_dir):
            return {"systems": [], "roms_dir": self.roms_dir, "note": "roms_dir not present"}
        out = sorted(
            d for d in os.listdir(self.roms_dir)
            if os.path.isdir(os.path.join(self.roms_dir, d)) and not d.startswith(".")
        )
        return {"systems": out, "roms_dir": self.roms_dir}

    def list(self, system: str) -> Dict:
        sysdir = os.path.join(self.roms_dir, system)
        if not os.path.isdir(sysdir):
            raise AgentError(ERR_ARGS, f"no such system '{system}'")
        games = self._from_gamelist(sysdir) or self._from_files(sysdir)
        return {"system": system, "count": len(games), "games": games}

    def _from_gamelist(self, sysdir: str) -> List[Dict]:
        gl = os.path.join(sysdir, "gamelist.xml")
        if not os.path.isfile(gl):
            return []
        try:
            root = ET.parse(gl).getroot()
        except ET.ParseError:
            return []
        games = []
        for g in root.findall("game"):
            path = (g.findtext("path") or "").strip()
            name = (g.findtext("name") or os.path.basename(path)).strip()
            if path.startswith("./"):
                path = os.path.join(sysdir, path[2:])
            games.append({"name": name, "path": path})
        return games

    def _from_files(self, sysdir: str) -> List[Dict]:
        games = []
        for fn in sorted(os.listdir(sysdir)):
            full = os.path.join(sysdir, fn)
            if not os.path.isfile(full):
                continue
            if os.path.splitext(fn)[1].lower() in _ROM_HINT_EXT:
                games.append({"name": os.path.splitext(fn)[0], "path": full})
        return games

    def _resolve(self, system: str, game: str) -> str:
        if os.path.isabs(game) and os.path.exists(game):
            return game
        for g in self.list(system)["games"]:
            if g["name"] == game or g["path"] == game or os.path.basename(g["path"]) == game:
                return g["path"]
        # Allow launching by raw path even if not enumerated.
        cand = os.path.join(self.roms_dir, system, game)
        if os.path.exists(cand):
            return cand
        raise AgentError(ERR_ARGS, f"game '{game}' not found in '{system}'")

    def launch(self, system: str, game: str) -> Dict:
        self.stop()  # one game at a time
        path = self._resolve(system, game) if os.path.isdir(self.roms_dir) else game
        template = self.launch_templates.get(system)
        if template:
            cmd = template.format(game=shlex.quote(path))
            self._proc = subprocess.Popen(cmd, shell=True)
            info = {"system": system, "game": game, "pid": self._proc.pid, "mode": "real"}
        else:
            # Dry mode: no template/device — simulate so events + stop work.
            self._sim = {"system": system, "game": game, "pid": -1,
                         "mode": "dry", "started": time.time()}
            info = dict(self._sim)
        self.emit("game.launched", info)
        return info

    def stop(self) -> Dict:
        stopped = False
        if self._proc and self._proc.poll() is None:
            self._proc.terminate()
            try:
                self._proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self._proc.kill()
            stopped = True
        self._proc = None
        if self._sim:
            stopped = True
            self._sim = None
        if stopped:
            self.emit("game.exited", {})
        return {"stopped": stopped}
