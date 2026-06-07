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
from .capabilities.gamestate import GameStateCapability


class Agent:
    def __init__(self, config: Optional[AgentConfig] = None,
                 emit: Optional[Callable[[str, dict], None]] = None):
        self.config = config or AgentConfig()
        self.emit = emit or (lambda *_: None)
        self.input = make_input(force_mock=self.config.force_mock)
        self.system = SystemCapability(allow_shell=self.config.allow_shell,
                                       sandbox_shell=self.config.sandbox_shell)
        self.games = GamesCapability(self.config.roms_dir,
                                     self.config.launch_templates,
                                     emit=self.emit)
        self.screen = ScreenCapability()
        if self.config.force_mock:
            self.screen.method = "mock"
            self.screen.backend = "mock"
        self.state = GameStateCapability(self.config.profiles_dir,
                                         self.config.retroarch_host,
                                         self.config.retroarch_port)
        self._ops: Dict[str, Callable[[dict], dict]] = self._build_ops()

    # ---- introspection ----
    def backends(self) -> Dict[str, str]:
        return {
            "input": self.input.backend,
            "system": self.system.backend,
            "games": self.games.backend,
            "screen": self.screen.backend,
            "state": self.state.backend,
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
            # input (seat = player slot; 1 is the original pad — see SeatManager)
            "input.button": lambda ar: self.input.button(
                a(ar, "button"), a(ar, "action", "tap"), int(ar.get("duration_ms", 80)),
                seat=ar.get("seat", 1)),
            "input.combo": lambda ar: self.input.combo(
                a(ar, "buttons"), int(ar.get("duration_ms", 80)), seat=ar.get("seat", 1)),
            "input.axis": lambda ar: self.input.axis(
                a(ar, "axis"), a(ar, "value"), seat=ar.get("seat", 1)),
            "input.type": lambda ar: self.input.type_text(a(ar, "text"), seat=ar.get("seat", 1)),
            "input.seats": lambda ar: self.input.seats(),
            "input.seat_open": lambda ar: self.input.seat_open(a(ar, "seat")),
            "input.seat_close": lambda ar: self.input.seat_close(a(ar, "seat")),
            # host-pad passthrough (input-level forwarding of a PHYSICAL pad;
            # replaces usb-redir for controllers — see capabilities/input.py)
            "input.pt_open": lambda ar: self.input.pt.open(ar),
            "input.pt_event": lambda ar: self.input.pt.event(a(ar, "pt_id"), a(ar, "events")),
            "input.pt_close": lambda ar: self.input.pt.close(a(ar, "pt_id")),
            "input.pt_list": lambda ar: self.input.pt.list(),
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
            # game state (read structured state from emulator memory — no screenshots)
            "state.profiles": lambda ar: self.state.list_profiles(),
            "state.attach": lambda ar: self.state.attach(ar.get("profile")),
            "state.read": lambda ar: self.state.read(ar.get("profile")),
            "state.status": lambda ar: self.state.status(),
            "state.read_raw": lambda ar: self.state.read_raw(
                a(ar, "address"), int(ar.get("count", 1)), ar.get("method", "core_memory")),
            "state.write_raw": lambda ar: self.state.write_raw(
                a(ar, "address"), a(ar, "data"), ar.get("method", "core_memory")),
        }


_MISSING = object()


def args_of(ar: dict, key: str, default=_MISSING):
    if key in ar:
        return ar[key]
    if default is _MISSING:
        raise AgentError(ERR_ARGS, f"missing required arg '{key}'")
    return default
