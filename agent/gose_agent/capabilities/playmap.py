"""Play-map registry — #117.

Baked per-game knowledge so a memory-less AI is not lost: controls, RAM field
semantics, seat assignment, launch args, and game-flow facts, all in one place.

Distinct from game-state *profiles* (profiles/ is pure RAM-field maps used by the
RetroArch NCI reader). Play-maps answer the higher-level question: "How do I actually
*play* this game?" — which paddle am I on, what does 'up' do, when is a game over,
etc. An AI consumes a play-map at the START of a session before touching any input.

Data lives in agent/gose_agent/play_maps/<id>.json (baked into the image).
Schema: see play_maps/pong1k2p.json + docs/32-play-map-registry.md (to be written).
"""
from __future__ import annotations

import glob
import json
import logging
import os
from typing import Any, Dict, Optional, Tuple

from ..protocol import AgentError, ERR_ARGS

log = logging.getLogger("gose.agent.playmap")

# Default location: sibling to this file's parent (gose_agent/play_maps/).
_DEFAULT_DIR = os.path.join(os.path.dirname(__file__), "..", "play_maps")

# Required top-level keys in every play-map.
_REQUIRED = ("id", "name", "system", "controls", "ram_fields", "game_flow")


def _validate(data: dict, source: str) -> Optional[str]:
    """Return an error string if the play-map is malformed, else None."""
    for key in _REQUIRED:
        if key not in data:
            return f"missing required key '{key}'"
    if not isinstance(data.get("controls"), dict):
        return "'controls' must be an object"
    if not isinstance(data.get("ram_fields"), dict):
        return "'ram_fields' must be an object"
    if not isinstance(data.get("game_flow"), dict):
        return "'game_flow' must be an object"
    return None


def load_play_maps(play_maps_dir: str) -> Tuple[Dict[str, dict], Dict[str, str]]:
    """Load all play-map JSONs from *play_maps_dir*.

    Returns (maps, skipped) where:
    - maps: id -> validated play-map dict
    - skipped: filename -> reason (bad JSON / missing required key)

    Bad maps are logged and excluded rather than crashing the agent.
    """
    maps: Dict[str, dict] = {}
    skipped: Dict[str, str] = {}
    if not play_maps_dir or not os.path.isdir(play_maps_dir):
        return maps, skipped
    for path in sorted(glob.glob(os.path.join(play_maps_dir, "*.json"))):
        fname = os.path.basename(path)
        try:
            with open(path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            err = _validate(data, path)
            if err:
                raise ValueError(err)
            key = data.get("id") or os.path.splitext(fname)[0]
            maps[key] = data
        except (json.JSONDecodeError, ValueError, KeyError) as e:
            reason = str(e)
            skipped[fname] = reason
            log.warning("skipping malformed play-map %s: %s", fname, reason)
    return maps, skipped


class PlayMapRegistry:
    """Read-only registry of baked play-maps.

    Loaded once at agent start; maps are immutable at runtime (no write path
    — play-maps are authored by developers and baked into the OS image, not
    modified by AI sessions).

    An AI session calls:
        games.playmaps            → list all ids + names
        games.playmap {id: ...}   → full map for one game
    """

    def __init__(self, play_maps_dir: Optional[str] = None):
        d = play_maps_dir or _DEFAULT_DIR
        self.play_maps_dir = os.path.abspath(d)
        self.maps, self.skipped = load_play_maps(self.play_maps_dir)
        log.info("play-map registry: %d maps loaded, %d skipped",
                 len(self.maps), len(self.skipped))

    # ---- agent ops ----

    def list_maps(self) -> Dict[str, Any]:
        """Return a summary list: id, name, system, and whether a ram_profile
        is cross-linked to the NCI profiles/ directory."""
        summaries = {}
        for k, m in self.maps.items():
            summaries[k] = {
                "id": m.get("id", k),
                "name": m.get("name", k),
                "system": m.get("system", ""),
                "crc": m.get("crc", ""),
                "ram_profile": m.get("ram_profile", ""),
                "controls": list(m.get("controls", {}).keys()),
            }
        return {
            "play_maps": summaries,
            "count": len(self.maps),
            "skipped": self.skipped,
            "play_maps_dir": self.play_maps_dir,
        }

    def get_map(self, map_id: str) -> Dict[str, Any]:
        """Return the full play-map for *map_id*, or raise AgentError."""
        if map_id not in self.maps:
            available = sorted(self.maps.keys())
            raise AgentError(ERR_ARGS,
                f"no play-map for '{map_id}'; available: {available}")
        return dict(self.maps[map_id])
