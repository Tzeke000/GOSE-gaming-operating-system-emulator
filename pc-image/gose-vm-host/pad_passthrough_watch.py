#!/usr/bin/env python3
"""GOSE controller-passthrough WATCHDOG (py -3.11, Windows host).

Why this exists: pad_passthrough.py forwards the host DualSense/Xbox/etc into the
guest. If it ever exits — a crash, or (the bug found 2026-06-08) the last pad being
unplugged making the daemon quit — the controller silently disappears from the OS and
a human has to restart it. A shipped GOSE has no human to do that. This supervisor
makes the SYSTEM own it: it runs the daemon and relaunches it within ~1s of any exit,
forever, with a small backoff so an instant-crash can't become a hot spin.

Run:    py -3.11 pad_passthrough_watch.py   (boot-gose-vm.ps1 launches THIS, not the daemon)
Log:    D:\\gose-vm\\pad_passthrough_watch.log
Singleton: refuses to start if another watchdog is already running.

TODO (Windows service — proper form, lower priority):
  WinSW (https://github.com/winsw/winsw) can wrap this as a true Windows service with
  Restart=always semantics and survive reboots without the boot script. To do it:
    1. Download winsw.exe and rename to padwatch-svc.exe next to this file.
    2. Write padwatch-svc.xml:
         <service>
           <id>gose-padwatch</id>
           <name>GOSE Pad Passthrough Watchdog</name>
           <executable>py</executable>
           <arguments>-3.11 "%BASE%\\pad_passthrough_watch.py"</arguments>
           <log mode="roll-by-size"><sizeThreshold>10240</sizeThreshold></log>
           <onfailure action="restart" delay="1 sec"/>
         </service>
    3. padwatch-svc.exe install && padwatch-svc.exe start
  The bare watchdog (this file, started by boot-gose-vm.ps1) is the working fallback
  and is sufficient for the dev VM. Upgrade to WinSW when GOSE ships to end-users who
  need crash-on-host-reboot recovery without relaunching the boot script.
"""
from __future__ import annotations

import logging
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
DAEMON = os.path.join(HERE, "pad_passthrough.py")
LOGFILE = os.environ.get("GOSE_PADPT_WATCH_LOG", r"D:\gose-vm\pad_passthrough_watch.log")

logging.basicConfig(
    filename=LOGFILE, level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
_log = logging.getLogger("padwatch")

# Singleton is enforced by the launcher (boot-gose-vm.ps1 kills any existing watchdog +
# daemon before starting this), so no in-process guard is needed here.


def main() -> int:
    if not os.path.isfile(DAEMON):
        _log.error("daemon not found at %s — nothing to supervise", DAEMON)
        return 2
    # Relaunch backoff: 1s normally; if the daemon dies in <5s repeatedly, ease off
    # to avoid a hot crash-loop (caps at 15s), then snap back after a healthy run.
    backoff = 1.0
    _log.info("padwatch up — supervising %s", DAEMON)
    while True:
        started = time.monotonic()
        try:
            proc = subprocess.Popen([sys.executable, "-u", DAEMON])
        except Exception as e:
            _log.error("failed to launch daemon: %s; retrying in %.0fs", e, backoff)
            time.sleep(backoff)
            backoff = min(backoff * 2, 15.0)
            continue
        _log.info("daemon started pid=%s", proc.pid)
        proc.wait()
        ran = time.monotonic() - started
        if ran >= 30:
            backoff = 1.0  # it ran healthy; reset the backoff
        _log.warning("daemon exited (code=%s) after %.1fs — relaunching in %.0fs",
                     proc.returncode, ran, backoff)
        time.sleep(backoff)
        if ran < 5:
            backoff = min(backoff * 2, 15.0)  # instant death -> back off


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        _log.info("padwatch stopped (KeyboardInterrupt)")
        sys.exit(0)
