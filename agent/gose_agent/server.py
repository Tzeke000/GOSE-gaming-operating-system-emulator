"""Asyncio TCP server speaking the JSON-lines protocol.

Same protocol over Wi-Fi/Ethernet and USB-net. Per-message token auth for
non-loopback peers. Blocking capability work (subprocess, sleeps) is offloaded to
a thread executor so the event loop stays responsive. Events are broadcast to all
connected clients.
"""
from __future__ import annotations

import asyncio
import ipaddress
import logging
from typing import Optional, Set

from .agent import Agent
from .config import AgentConfig
from . import protocol as P

log = logging.getLogger("gose.agent")


class AgentServer:
    def __init__(self, config: Optional[AgentConfig] = None):
        self.config = config or AgentConfig()
        self._writers: Set[asyncio.StreamWriter] = set()
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        # Agent emits events; we fan them out to connected clients.
        self.agent = Agent(self.config, emit=self._emit)

    # ---- event fan-out (thread-safe-ish: scheduled on the loop) ----
    def _emit(self, name: str, data: dict):
        if not self._loop:
            return
        msg = P.encode(P.event(name, data))
        for w in list(self._writers):
            try:
                w.write(msg)
            except Exception:
                pass

    @staticmethod
    def _is_loopback(peer) -> bool:
        try:
            return ipaddress.ip_address(peer[0]).is_loopback
        except Exception:
            return False

    def _authed(self, peer, msg: dict) -> bool:
        # Loopback with no configured token = open (dev convenience).
        if self._is_loopback(peer) and not self.config.token:
            return True
        if not self.config.token:
            # No token configured but remote peer: refuse by default.
            return False
        return msg.get("token") == self.config.token

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        peer = writer.get_extra_info("peername")
        self._writers.add(writer)
        log.info("client connected: %s", peer)
        try:
            while not reader.at_eof():
                line = await reader.readline()
                if not line:
                    break
                req_id = None
                try:
                    msg = P.decode_line(line.decode("utf-8", "replace"))
                    req_id = msg.get("id")
                    if not self._authed(peer, msg):
                        writer.write(P.encode(P.err_response(req_id, P.ERR_AUTH, "bad or missing token")))
                        await writer.drain()
                        break
                    op = msg.get("op")
                    if not isinstance(op, str):
                        raise P.AgentError(P.ERR_BADREQ, "missing 'op'")
                    args = msg.get("args") or {}
                    # Run (possibly blocking) dispatch off the event loop.
                    result = await asyncio.get_event_loop().run_in_executor(
                        None, self.agent.dispatch, op, args)
                    writer.write(P.encode(P.ok_response(req_id, result)))
                except P.AgentError as e:
                    writer.write(P.encode(P.err_response(req_id, e.code, e.message)))
                except Exception as e:  # noqa: BLE001 — never let one bad msg kill the conn
                    log.exception("dispatch error")
                    writer.write(P.encode(P.err_response(req_id, P.ERR_BACKEND, str(e))))
                await writer.drain()
        finally:
            self._writers.discard(writer)
            try:
                writer.close()
            except Exception:
                pass
            log.info("client disconnected: %s", peer)

    async def serve(self):
        self._loop = asyncio.get_event_loop()
        server = await asyncio.start_server(self._handle, self.config.host, self.config.port)
        addrs = ", ".join(str(s.getsockname()) for s in server.sockets)
        log.info("GOSE Agent listening on %s", addrs)
        async with server:
            await server.serve_forever()

    def run(self):
        logging.basicConfig(level=logging.INFO,
                            format="%(asctime)s %(levelname)s %(name)s: %(message)s")
        b = self.agent.backends()
        log.info("backends: %s", b)
        if not self.config.token:
            log.warning("no token set — only loopback clients allowed. "
                        "Set GOSE_AGENT_TOKEN for Wi-Fi/USB access.")
        try:
            asyncio.run(self.serve())
        except KeyboardInterrupt:
            log.info("shutting down")
