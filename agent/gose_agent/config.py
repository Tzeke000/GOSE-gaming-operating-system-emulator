"""Agent configuration: env vars + optional JSON file, with sane defaults."""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field, asdict
from typing import Dict, Optional


def _env_bool(name: str, default: bool) -> bool:
    v = os.environ.get(name)
    if v is None:
        return default
    return v.strip().lower() in ("1", "true", "yes", "on")


@dataclass
class AgentConfig:
    host: str = "0.0.0.0"
    port: int = 8731
    token: Optional[str] = None          # required for non-loopback clients
    allow_shell: bool = True             # system.run enabled (owner's device)
    sandbox_shell: bool = True           # confine system.run (mount-ns jail + cap-drop)

    # Where the front-end keeps ROMs; systems are subdirectories.
    roms_dir: str = "/storage/roms"
    # Per-system launch command template. {game} is substituted with the path.
    # Real device fills these from the distro; mock backend just records them.
    launch_templates: Dict[str, str] = field(default_factory=dict)

    # Game-state interface (RetroArch Network Command Interface).
    retroarch_host: str = "127.0.0.1"
    retroarch_port: int = 55355
    # Per-game RAM-map profiles. Defaults to the packaged profiles/ directory.
    profiles_dir: str = os.path.join(os.path.dirname(__file__), "profiles")

    force_mock: bool = False             # force mock backends (testing/dev)

    @classmethod
    def load(cls, path: Optional[str] = None) -> "AgentConfig":
        cfg = cls()
        # 1) optional JSON file
        path = path or os.environ.get("GOSE_AGENT_CONFIG")
        if path and os.path.exists(path):
            with open(path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            for k, v in data.items():
                if hasattr(cfg, k):
                    setattr(cfg, k, v)
        # 2) env overrides
        cfg.host = os.environ.get("GOSE_AGENT_HOST", cfg.host)
        cfg.port = int(os.environ.get("GOSE_AGENT_PORT", cfg.port))
        cfg.token = os.environ.get("GOSE_AGENT_TOKEN", cfg.token)
        cfg.allow_shell = _env_bool("GOSE_AGENT_ALLOW_SHELL", cfg.allow_shell)
        cfg.sandbox_shell = _env_bool("GOSE_AGENT_SANDBOX_SHELL", cfg.sandbox_shell)
        cfg.roms_dir = os.environ.get("GOSE_AGENT_ROMS_DIR", cfg.roms_dir)
        cfg.retroarch_host = os.environ.get("GOSE_AGENT_RA_HOST", cfg.retroarch_host)
        cfg.retroarch_port = int(os.environ.get("GOSE_AGENT_RA_PORT", cfg.retroarch_port))
        cfg.profiles_dir = os.environ.get("GOSE_AGENT_PROFILES_DIR", cfg.profiles_dir)
        cfg.force_mock = _env_bool("GOSE_AGENT_FORCE_MOCK", cfg.force_mock)
        return cfg

    def to_dict(self) -> dict:
        d = asdict(self)
        if d.get("token"):
            d["token"] = "***"  # never echo the secret
        return d
