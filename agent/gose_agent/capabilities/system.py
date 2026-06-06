"""System capability: shell execution, health status, service control.

This is the AI's "remote hands" for fixing/tinkering with the OS. Shell is
powerful by design and gated behind config.allow_shell + the connection token.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time
from typing import Dict, Optional

from ..protocol import AgentError, ERR_DENIED, ERR_TIMEOUT
from .. import sandbox


def _read(path: str) -> str:
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            return fh.read().strip()
    except OSError:
        return ""


class SystemCapability:
    def __init__(self, allow_shell: bool = True, sandbox_shell: bool = True):
        self.allow_shell = allow_shell
        # When True, system.run is confined (mount-namespace jail + cap-drop,
        # see sandbox.py). Default ON: the agent has a single system.run path and
        # the capability layer can't see the caller's tier (that's resolved in
        # server.py, which this task must not edit), so we protect the critical
        # invariant for ALL callers. The owner's unconfined dev path is opt-out
        # via config (GOSE_AGENT_SANDBOX_SHELL=0) or an explicit confine=False;
        # a per-tier dev exemption is a one-line server.py follow-up.
        self.sandbox_shell = sandbox_shell
        # "real" because subprocess always works; status values degrade
        # gracefully when sysfs nodes are absent (e.g. in a container).
        self.backend = "real"

    def run(self, cmd: str, timeout_ms: int = 10000,
            confine: Optional[bool] = None) -> Dict:
        if not self.allow_shell:
            raise AgentError(ERR_DENIED, "shell execution disabled (allow_shell=false)")
        if not cmd or not isinstance(cmd, str):
            raise AgentError("ERR_ARGS", "cmd must be a non-empty string")
        if confine is None:
            confine = self.sandbox_shell

        if confine:
            # Backstop deny-list first (holds on every platform, even if the
            # kernel jail degrades or is unavailable off-Linux).
            try:
                sandbox.guard_command(cmd)
            except sandbox.GuardDenied as e:
                raise AgentError(ERR_DENIED, str(e)) from e
            if sys.platform.startswith("linux"):
                # The real jail: mount-ns + cap-drop. Linux-only mechanism.
                argv = sandbox.wrap_command(cmd)
                shell = False
            else:
                # Dev host (Windows/macOS) has no namespace mechanism; the
                # deny-list above is the only confinement here. The deployment
                # target is the Linux VM, where the jail engages.
                argv = cmd
                shell = True
        else:
            argv = cmd                       # owner/dev path: unconfined, as before
            shell = True

        try:
            proc = subprocess.run(
                argv, shell=shell, capture_output=True, text=True,
                timeout=max(0.1, timeout_ms / 1000.0),
            )
        except subprocess.TimeoutExpired as e:
            raise AgentError(ERR_TIMEOUT, f"command timed out after {timeout_ms}ms") from e
        return {
            "code": proc.returncode,
            "stdout": proc.stdout,
            "stderr": proc.stderr,
        }

    def service(self, name: str, action: str) -> Dict:
        if action not in ("start", "stop", "restart", "status"):
            raise AgentError("ERR_ARGS", f"bad action '{action}'")
        # systemd on most distros; fall back to plain run if absent.
        if shutil.which("systemctl"):
            return self.run(f"systemctl {action} {name}")
        return self.run(f"service {name} {action}")

    def status(self) -> Dict:
        return {
            "battery": self._battery(),
            "temp_c": self._temp(),
            "mem": self._mem(),
            "cpu": {"loadavg": list(os.getloadavg()) if hasattr(os, "getloadavg") else None,
                    "count": os.cpu_count()},
            "wifi": self._wifi(),
            "uptime_s": self._uptime(),
            "ts": time.time(),
        }

    # ---- best-effort readers (return None/partial when unavailable) ----
    def _battery(self):
        base = "/sys/class/power_supply"
        if not os.path.isdir(base):
            return None
        for name in os.listdir(base):
            cap = _read(f"{base}/{name}/capacity")
            if cap:
                return {"name": name, "percent": int(cap) if cap.isdigit() else cap,
                        "status": _read(f"{base}/{name}/status") or None}
        return None

    def _temp(self):
        for zone in ("/sys/class/thermal/thermal_zone0/temp",):
            v = _read(zone)
            if v.lstrip("-").isdigit():
                return round(int(v) / 1000.0, 1)
        return None

    def _mem(self):
        info = _read("/proc/meminfo")
        if not info:
            return None
        out = {}
        for line in info.splitlines():
            if line.startswith(("MemTotal", "MemAvailable")):
                k, v = line.split(":", 1)
                out[k] = int(v.strip().split()[0])  # kB
        return out or None

    def _wifi(self):
        # Lightweight: report whether an interface looks associated.
        try:
            if shutil.which("iwgetid"):
                ssid = subprocess.run(["iwgetid", "-r"], capture_output=True,
                                      text=True, timeout=3).stdout.strip()
                return {"ssid": ssid or None}
        except Exception:
            pass
        return None

    def _uptime(self):
        v = _read("/proc/uptime")
        return float(v.split()[0]) if v else None
