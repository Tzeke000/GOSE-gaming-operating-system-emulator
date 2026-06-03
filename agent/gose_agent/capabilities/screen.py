"""Screen capture capability: give the AI eyes on the device.

Real backend tries common Linux capture paths (grim/wlroots, scrot/X11, fbgrab,
raw framebuffer). Mock backend returns a tiny valid PNG so the perception loop is
exercisable without a display (CI / container).
"""
from __future__ import annotations

import base64
import os
import shutil
import subprocess
import tempfile
from typing import Dict

# A 1x1 transparent PNG (valid, tiny) for the mock backend.
_MOCK_PNG_B64 = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg=="
)


class ScreenCapability:
    def __init__(self):
        self.method = self._detect()
        self.backend = "mock" if self.method == "mock" else "real"

    def _detect(self) -> str:
        if shutil.which("grim"):       # Wayland (wlroots) — likely on these distros
            return "grim"
        if shutil.which("scrot"):      # X11
            return "scrot"
        if shutil.which("fbgrab"):     # framebuffer
            return "fbgrab"
        if os.path.exists("/dev/fb0"):
            return "fb0"
        return "mock"

    def capture(self, fmt: str = "png", scale: float = 1.0) -> Dict:
        if self.method == "mock":
            return {"format": "png", "w": 1, "h": 1, "b64": _MOCK_PNG_B64,
                    "backend": "mock"}
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tf:
            out = tf.name
        try:
            if self.method == "grim":
                subprocess.run(["grim", out], check=True, timeout=10)
            elif self.method == "scrot":
                subprocess.run(["scrot", "-o", out], check=True, timeout=10)
            elif self.method == "fbgrab":
                subprocess.run(["fbgrab", out], check=True, timeout=10)
            else:  # raw framebuffer — best-effort copy; real conversion TBD
                shutil.copyfile("/dev/fb0", out)
            with open(out, "rb") as fh:
                data = fh.read()
            return {"format": "png", "b64": base64.b64encode(data).decode("ascii"),
                    "bytes": len(data), "backend": self.method}
        finally:
            try:
                os.unlink(out)
            except OSError:
                pass
