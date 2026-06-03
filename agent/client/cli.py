"""Manual test CLI for the GOSE Agent.

Examples (from agent/):
    python3 client/cli.py ping
    python3 client/cli.py info
    python3 client/cli.py run "uname -a"
    python3 client/cli.py status
    python3 client/cli.py tap a
    python3 client/cli.py combo l1 r1
    python3 client/cli.py systems
    python3 client/cli.py list psp
    python3 client/cli.py launch psp "God of War"
    python3 client/cli.py shot > frame.json

Connection via flags or env: GOSE_HOST, GOSE_PORT, GOSE_TOKEN.
"""
from __future__ import annotations

import argparse
import json
import os
import sys

from gose_client import GoseClient, GoseClientError


def main(argv=None):
    ap = argparse.ArgumentParser(prog="gose-cli", description="Drive a GOSE Agent.")
    ap.add_argument("--host", default=os.environ.get("GOSE_HOST", "127.0.0.1"))
    ap.add_argument("--port", type=int, default=int(os.environ.get("GOSE_PORT", "8731")))
    ap.add_argument("--token", default=os.environ.get("GOSE_TOKEN"))
    ap.add_argument("cmd", help="ping|info|run|status|service|tap|press|release|combo|axis|"
                                "type|systems|list|launch|stop|shot|profiles|attach|state|"
                                "gamestatus|readmem")
    ap.add_argument("rest", nargs="*")
    a = ap.parse_args(argv)

    try:
        with GoseClient(a.host, a.port, token=a.token) as c:
            out = _run(c, a.cmd, a.rest)
        print(json.dumps(out, indent=2))
    except GoseClientError as e:
        print(f"error {e.code}: {e.message}", file=sys.stderr)
        return 1
    except (ConnectionError, OSError) as e:
        print(f"connection failed to {a.host}:{a.port}: {e}", file=sys.stderr)
        return 2
    return 0


def _run(c: GoseClient, cmd: str, rest):
    if cmd == "ping": return c.ping()
    if cmd == "info": return c.info()
    if cmd == "run": return c.run(" ".join(rest))
    if cmd == "status": return c.status()
    if cmd == "service": return c.service(rest[0], rest[1])
    if cmd == "tap": return c.tap(rest[0])
    if cmd == "press": return c.press(rest[0])
    if cmd == "release": return c.release(rest[0])
    if cmd == "combo": return c.combo(rest)
    if cmd == "axis": return c.axis(rest[0], float(rest[1]))
    if cmd == "type": return c.type_text(" ".join(rest))
    if cmd == "systems": return c.systems()
    if cmd == "list": return c.list_games(rest[0])
    if cmd == "launch": return c.launch(rest[0], " ".join(rest[1:]))
    if cmd == "stop": return c.stop()
    if cmd == "shot": return c.screenshot()
    if cmd == "profiles": return c.profiles()
    if cmd == "attach": return c.attach(rest[0] if rest else None)
    if cmd == "state": return c.read_state(rest[0] if rest else None)
    if cmd == "gamestatus": return c.game_status()
    if cmd == "readmem": return c.read_mem(rest[0], int(rest[1]) if len(rest) > 1 else 1)
    raise SystemExit(f"unknown command: {cmd}")


if __name__ == "__main__":
    raise SystemExit(main())
