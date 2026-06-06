"""GoseClient: a tiny stdlib client for the GOSE Agent JSON-lines protocol.

This is what the AI bridge (Ava/Wren/Iris adapter) imports to drive the device,
and what cli.py uses for manual testing. No external dependencies.

    from gose_client import GoseClient
    with GoseClient("192.168.1.50", 8731, token="secret") as c:
        c.launch("psp", "God of War")
        c.tap("a")
        png = c.screenshot()["b64"]
"""
from __future__ import annotations

import json
import socket
from typing import Any, Dict, Optional


class GoseClientError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message


class GoseClient:
    def __init__(self, host: str = "127.0.0.1", port: int = 8731,
                 token: Optional[str] = None, timeout: float = 15.0):
        self.host, self.port, self.token, self.timeout = host, port, token, timeout
        self._sock: Optional[socket.socket] = None
        self._buf = b""
        self._id = 0

    # ---- connection ----
    def connect(self) -> "GoseClient":
        self._sock = socket.create_connection((self.host, self.port), self.timeout)
        self._sock.settimeout(self.timeout)
        return self

    def close(self):
        if self._sock:
            try:
                self._sock.close()
            finally:
                self._sock = None

    def __enter__(self):
        return self.connect()

    def __exit__(self, *exc):
        self.close()

    # ---- core request/response ----
    def call(self, op: str, **args) -> Dict[str, Any]:
        # Try once, and if the connection is dead (e.g. the agent restarted), drop the
        # stale socket and reconnect once. Without this, a single agent restart wedges
        # the client forever on a dead socket.
        last_err: Optional[Exception] = None
        for attempt in (1, 2):
            if self._sock is None:
                self.connect()
            self._id += 1
            req: Dict[str, Any] = {"id": self._id, "op": op, "args": args}
            if self.token:
                req["token"] = self.token
            try:
                self._sock.sendall((json.dumps(req) + "\n").encode("utf-8"))
                # Read until we get a response matching our id (skip async events).
                while True:
                    line = self._readline()
                    msg = json.loads(line)
                    if "event" in msg:
                        continue  # ignore unsolicited events in the simple client
                    if msg.get("id") != req["id"]:
                        continue
                    if not msg.get("ok"):
                        raise GoseClientError(msg.get("code", "ERR"), msg.get("error", ""))
                    return msg.get("result", {})
            except GoseClientError as e:
                if e.code == "ERR_CONN" and attempt == 1:
                    self._reset(); last_err = e; continue  # dead connection — reconnect once
                raise  # ERR_DENIED / ERR_BACKEND / etc. are real responses, don't retry
            except OSError as e:  # broken pipe / reset / aborted on a stale socket
                self._reset(); last_err = e
                if attempt == 1:
                    continue
                raise GoseClientError("ERR_CONN", str(e))
        raise GoseClientError("ERR_CONN", str(last_err) if last_err else "connection failed")

    def _reset(self):
        """Drop the current socket + buffered bytes so the next call reconnects clean."""
        self.close()
        self._buf = b""

    def _readline(self) -> str:
        while b"\n" not in self._buf:
            chunk = self._sock.recv(65536)
            if not chunk:
                raise GoseClientError("ERR_CONN", "connection closed")
            self._buf += chunk
        line, self._buf = self._buf.split(b"\n", 1)
        return line.decode("utf-8")

    # ---- convenience methods (mirror the protocol) ----
    def ping(self): return self.call("ping")
    def info(self): return self.call("agent.info")

    def press(self, button): return self.call("input.button", button=button, action="press")
    def release(self, button): return self.call("input.button", button=button, action="release")
    def tap(self, button, duration_ms=80):
        return self.call("input.button", button=button, action="tap", duration_ms=duration_ms)
    def combo(self, buttons, duration_ms=80):
        return self.call("input.combo", buttons=buttons, duration_ms=duration_ms)
    def axis(self, axis, value): return self.call("input.axis", axis=axis, value=value)
    def type_text(self, text): return self.call("input.type", text=text)

    def run(self, cmd, timeout_ms=10000): return self.call("system.run", cmd=cmd, timeout_ms=timeout_ms)
    def status(self): return self.call("system.status")
    def service(self, name, action): return self.call("system.service", name=name, action=action)

    def systems(self): return self.call("games.systems")
    def list_games(self, system): return self.call("games.list", system=system)
    def launch(self, system, game): return self.call("games.launch", system=system, game=game)
    def stop(self): return self.call("games.stop")

    def screenshot(self, fmt="png", scale=1.0): return self.call("screen.capture", format=fmt, scale=scale)

    # game state (read structured state straight from emulator memory)
    def profiles(self): return self.call("state.profiles")
    def attach(self, profile=None): return self.call("state.attach", profile=profile)
    def read_state(self, profile=None): return self.call("state.read", profile=profile)
    def game_status(self): return self.call("state.status")
    def read_mem(self, address, count=1, method="core_memory"):
        return self.call("state.read_raw", address=address, count=count, method=method)
    def write_mem(self, address, data, method="core_memory"):
        return self.call("state.write_raw", address=address, data=data, method=method)
