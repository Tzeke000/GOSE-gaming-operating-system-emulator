#!/usr/bin/env python3
"""Preview the GOSE UI in a browser — the quick way to try the front-end now.

This serves the HTML prototypes (gui/mockup/) and opens the boot flow as the PC
app (boot -> choose navigation -> login -> desktop). It is UI-only: for the real
OS + emulators, run the GOSE-PC virtual machine (scripts/gose_vm.py). Zero deps.

    python3 scripts/gose-preview.py            # serve + open boot flow
    python3 scripts/gose-preview.py --no-open  # just serve (e.g. headless)
"""
from __future__ import annotations
import argparse
import functools
import http.server
import os
import socketserver
import threading
import webbrowser

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "gui", "mockup")


def main():
    ap = argparse.ArgumentParser(description="Serve the GOSE UI preview.")
    ap.add_argument("--port", type=int, default=8770)
    ap.add_argument("--no-open", action="store_true")
    a = ap.parse_args()

    handler = functools.partial(http.server.SimpleHTTPRequestHandler,
                                directory=os.path.abspath(ROOT))
    socketserver.TCPServer.allow_reuse_address = True
    with socketserver.TCPServer(("127.0.0.1", a.port), handler) as httpd:
        url = f"http://127.0.0.1:{a.port}/boot.html?platform=pc"
        print(f"GOSE UI preview at {url}\n(Ctrl-C to stop)")
        if not a.no_open:
            threading.Timer(0.6, lambda: webbrowser.open(url)).start()
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\nstopped.")


if __name__ == "__main__":
    main()
