#!/usr/bin/env python3
"""Preview the GOSE UI in a browser — the quick way to try the front-end now.

Serves the HTML prototypes (gui/mockup/) AND a tiny live telemetry endpoint
(/status.json) that pulls REAL device stats from the running GOSE Agent, so the
home screen's system dials show actual CPU/RAM/uptime (Task-Manager style) instead
of mock numbers. UI-only otherwise; the real OS + emulators run in the VM
(scripts/gose_vm.py). Zero external deps.

    python3 scripts/gose-preview.py                 # serve + open boot flow
    python3 scripts/gose-preview.py --no-open       # just serve (headless)
    GOSE_TOKEN=... python3 scripts/gose-preview.py   # token for the agent (live dials)
"""
from __future__ import annotations
import argparse
import functools
import http.server
import json
import os
import socketserver
import threading
import webbrowser

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(HERE, "..", "gui", "mockup")
import sys
sys.path.insert(0, os.path.join(HERE, "..", "agent", "client"))


def _agent_status():
    """Query the GOSE Agent for real device stats; return friendly dial values."""
    try:
        from gose_client import GoseClient
        host = os.environ.get("GOSE_HOST", "127.0.0.1")
        port = int(os.environ.get("GOSE_PORT", "8731"))
        token = os.environ.get("GOSE_TOKEN")
        with GoseClient(host, port, token=token, timeout=4) as c:
            s = c.status()
        mem = s.get("mem") or {}
        total = mem.get("MemTotal") or 0
        avail = mem.get("MemAvailable") or 0
        used = max(0, total - avail)
        cpu = s.get("cpu") or {}
        la = (cpu.get("loadavg") or [0])[0]
        cnt = cpu.get("count") or 1
        return {
            "ok": True,
            "cpu_pct": min(100, round(la / cnt * 100)),
            "mem_pct": round(used / total * 100) if total else 0,
            "mem_used_gb": round(used / 1048576, 1),
            "mem_total_gb": round(total / 1048576, 1),
            "temp_c": s.get("temp_c"),
            "uptime_s": round(s.get("uptime_s") or 0),
        }
    except Exception as e:
        return {"ok": False, "error": str(e)}


class Handler(http.server.SimpleHTTPRequestHandler):
    def do_GET(self):
        if self.path.split("?")[0] == "/status.json":
            body = json.dumps(_agent_status()).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        return super().do_GET()

    def log_message(self, *a):  # quiet
        pass


def main():
    ap = argparse.ArgumentParser(description="Serve the GOSE UI preview.")
    ap.add_argument("--port", type=int, default=8770)
    ap.add_argument("--no-open", action="store_true")
    a = ap.parse_args()

    handler = functools.partial(Handler, directory=os.path.abspath(ROOT))
    socketserver.TCPServer.allow_reuse_address = True
    with socketserver.TCPServer(("127.0.0.1", a.port), handler) as httpd:
        url = f"http://127.0.0.1:{a.port}/boot.html?platform=pc"
        print(f"GOSE UI preview at {url}  (live dials via /status.json)\n(Ctrl-C to stop)")
        if not a.no_open:
            threading.Timer(0.6, lambda: webbrowser.open(url)).start()
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\nstopped.")


if __name__ == "__main__":
    main()
