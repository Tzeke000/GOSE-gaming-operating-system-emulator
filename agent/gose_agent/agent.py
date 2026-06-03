"""Agent core: wires capabilities together and dispatches protocol ops.

Dispatch is synchronous and side-effect-driven so it's trivially unit-testable
without any networking. The server layer wraps this with auth + transport.
"""
from __future__ import annotations

import platform
from typing import Any, Callable, Dict, Optional

from . import __version__
from .config import AgentConfig
from .protocol import AgentError, ERR_ARGS, ERR_UNKNOWN_OP
from .capabilities.input import make_input
from .capabilities.system import SystemCapability
from .capabilities.games import GamesCapability
from .capabilities.screen import ScreenCapability


class Agent:
    def __init__(self, config: Optional[AgentConfig] = None,
                 emit: Optional[Callable[[str, dict], None]] = None):
        self.config = config or AgentConfig()
        self.emit = emit or (lambda *_: None)
        self.input = make_input(force_mock=self.config.force_mock)
        self.system = SystemCapability(allow_shell=self.config.allow_shell)
        self.games = GamesCapability(self.config.roms_dir,
                                     self.config.launch_templates,
                                     emit=self.emit)
        self.screen = ScreenCapability()
        if self.config.force_mock:
            self.screen.method = "mock"
            self.screen.backend = "mock"
        self._ops: Dict[str, Callable[[dict], dict]] = self._build_ops()

    # ---- introspection ----
    def backends(self) -> Dict[str, str]:
        return {
            "input": self.input.backend,
            "system": self.system.backend,
            "games": self.games.backend,
            "screen": self.screen.backend,
        }

    def info(self) -> Dict[str, Any]:
        return {
            "version": __version__,
            "host": platform.node(),
            "backends": self.backends(),
            "ops": sorted(self._ops.keys()),
            "config": self.config.to_dict(),
        }

    # ---- dispatch ----
    def dispatch(self, op: str, args: Optional[dict] = None) -> dict:
        args = args or {}
        if not isinstance(args, dict):
            raise AgentError(ERR_ARGS, "args must be an object")
        handler = self._ops.get(op)
        if handler is None:
            raise AgentError(ERR_UNKNOWN_OP, f"unknown op '{op}'")
        return handler(args)

    def _build_ops(self) -> Dict[str, Callable[[dict], dict]]:
        a = args_of
        return {
            "ping": lambda ar: {"pong": True, "ts": __import__("time").time()},
            "agent.info": lambda ar: self.info(),
            # input
            "input.button": lambda ar: self.input.button(
                a(ar, "button"), a(ar, "action", "tap"), int(ar.get("duration_ms", 80))),
            "input.combo": lambda ar: self.input.combo(
                a(ar, "buttons"), int(ar.get("duration_ms", 80))),
            "input.axis": lambda ar: self.input.axis(a(ar, "axis"), a(ar, "value")),
            "input.type": lambda ar: self.input.type_text(a(ar, "text")),
            # system
            "system.run": lambda ar: self.system.run(
                a(ar, "cmd"), int(ar.get("timeout_ms", 10000))),
            "system.status": lambda ar: self.system.status(),
            "system.service": lambda ar: self.system.service(a(ar, "name"), a(ar, "action")),
            # games
            "games.systems": lambda ar: self.games.systems(),
            "games.list": lambda ar: self.games.list(a(ar, "system")),
            "games.launch": lambda ar: self.games.launch(a(ar, "system"), a(ar, "game")),
            "games.stop": lambda ar: self.games.stop(),
            # screen
            "screen.capture": lambda ar: self.screen.capture(
                ar.get("format", "png"), float(ar.get("scale", 1.0))),
        }


_MISSING = object()


def args_of(ar: dict, key: str, default=_MISSING):
    if key in ar:
        return ar[key]
    if default is _MISSING:
        raise AgentError(ERR_ARGS, f"missing required arg '{key}'")
    return default
