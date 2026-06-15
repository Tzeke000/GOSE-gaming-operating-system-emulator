"""Screen capture capability: give the AI eyes on the device.

Real backend tries common Linux capture paths (grim/wlroots, scrot/X11, ffmpeg
x11grab, fbgrab, raw framebuffer). Mock backend returns a tiny valid PNG so the
perception loop is exercisable without a display (CI / container).

NOTE: ffmpeg x11grab is preferred over fbgrab because when the desktop is GPU/GL-
composited (virgl, qemu gtk,gl=on) the legacy framebuffer /dev/fb0 is powered down,
so fbgrab/fb0 capture a BLANK frame for both the shell AND games. x11grab reads the
real X display and works over GL. (overlay_window.py uses the same path.)
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

# Default long-side cap for the ffmpeg x11grab vision capture — keeps the PNG small
# enough to return over the tool channel while staying legible. scale<1 shrinks further.
_VISION_MAX_W = 480


class ScreenCapability:
    def __init__(self):
        self.method = self._detect()
        self.backend = "mock" if self.method == "mock" else "real"

    def _detect(self) -> str:
        if shutil.which("grim"):       # Wayland (wlroots) — likely on these distros
            return "grim"
        if shutil.which("scrot"):      # X11
            return "scrot"
        # ffmpeg x11grab — the only path that works under GL compositing (see module
        # docstring). Needs ffmpeg + a live X display.
        if shutil.which("ffmpeg") and (os.environ.get("DISPLAY") or os.path.exists("/tmp/.X11-unix/X0")):
            return "ffmpeg-x11"
        if shutil.which("fbgrab"):     # framebuffer (blind under GL — last resort)
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
            elif self.method == "ffmpeg-x11":
                env = dict(os.environ)
                env.setdefault("DISPLAY", ":0")
                disp = env["DISPLAY"]
                x11_in = disp if "." in disp.rsplit(":", 1)[-1] else disp + ".0"
                # Downscale: a full-res desktop PNG is more than AI vision needs and
                # overflows the tool channel. Cap the long side (scale<1 shrinks more).
                max_w = max(64, int(round(_VISION_MAX_W * (scale if 0 < scale < 1 else 1.0))))
                vf = "scale='min(%d,iw)':-2" % max_w
                subprocess.run(
                    ["ffmpeg", "-loglevel", "error", "-f", "x11grab", "-draw_mouse", "0",
                     "-i", x11_in, "-frames:v", "1", "-vf", vf, "-update", "1", "-y", out],
                    check=True, timeout=15, env=env)
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
