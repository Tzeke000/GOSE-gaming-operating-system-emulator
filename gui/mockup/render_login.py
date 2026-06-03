#!/usr/bin/env python3
"""Render the GOSE login / user-select concept -> login-concept.png."""
from __future__ import annotations
import os
from PIL import Image, ImageDraw
from _render_common import (base, font, icon, panel, text, logo,
                            ACC, TEXT, MUTED, SURFACE, SURFACE2, LINE)

W, H = 1280, 720
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "login-concept.png")

USERS = [("Zeke", "user", "Administrator", True),
         ("Guest", "user", "Limited session", False),
         ("AI Session", "sparkles", "Ava / Wren / Iris", False)]


def avatar(img, d, cx, cy, ic, sel):
    r = 56 if sel else 48
    ring = ACC if sel else (60, 64, 78)
    lay = Image.new("RGBA", img.size, (0, 0, 0, 0))
    ld = ImageDraw.Draw(lay)
    ld.ellipse([cx - r, cy - r, cx + r, cy + r], fill=SURFACE2)
    ld.ellipse([cx - r, cy - r, cx + r, cy + r], outline=ring, width=3 if sel else 2)
    img.alpha_composite(lay)
    gi = icon(ic, int(r * 1.05), ACC if sel else MUTED)
    img.alpha_composite(gi, (cx - gi.width // 2, cy - gi.height // 2))


def main():
    img = base(W, H)
    d = ImageDraw.Draw(img)

    # clock + date (top center)
    text(d, (0, 70), "14:32", font(72, 700), TEXT, center_w=W)
    text(d, (0, 156), "Tuesday, June 3", font(18, 500), MUTED, center_w=W)

    # user tiles row
    n = len(USERS); gapx = 230; startx = W // 2 - (n - 1) * gapx // 2; cy = 330
    for i, (name, ic, sub, sel) in enumerate(USERS):
        cx = startx + i * gapx
        avatar(img, d, cx, cy, ic, sel)
        text(d, (cx - 100, cy + 72), name, font(20, 700 if sel else 600),
             TEXT if sel else MUTED, center_w=200)
        text(d, (cx - 100, cy + 100), sub, font(13), MUTED, center_w=200)

    # sign-in affordance for the selected user
    sx = W // 2 - 150
    panel(img, [sx, cy + 150, sx + 300, cy + 196], 23, SURFACE, outline=LINE, width=1)
    img.alpha_composite(icon("lock", 18, MUTED), (sx + 18, cy + 164))
    text(d, (sx + 46, cy + 164), "Enter PIN", font(15), MUTED)
    for k in range(4):
        d.ellipse([sx + 150 + k * 22, cy + 169, sx + 162 + k * 22, cy + 181],
                  fill=ACC if k < 2 else (60, 64, 78))
    text(d, (0, cy + 214), "Press  A  to sign in   ·   on-screen keypad for PIN",
         font(13), MUTED, center_w=W)

    # bottom-left: logo + hostname
    logo(img, 52, H - 44, 40)
    text(d, (80, H - 58), "GOSE", font(15, 700), TEXT)
    text(d, (80, H - 38), "odin2-gose · 192.168.1.50", font(12), MUTED)

    # bottom-right: power / restart / settings / wifi
    icons = ["wifi", "settings", "rotate-ccw", "power"]
    bx = W - 30
    for ic in icons:
        bx -= 52
        panel(img, [bx, H - 60, bx + 40, H - 20], 11, SURFACE, outline=LINE, width=1)
        img.alpha_composite(icon(ic, 20, TEXT if ic != "power" else (255, 120, 110)),
                            (bx + 10, H - 50))

    # controller hint (top-right)
    hint = "D-pad pick user  ·  A sign in  ·  X power options"
    hw = d.textlength(hint, font=font(13))
    panel(img, [W - 30 - hw - 20, 24, W - 14, 54], 10, SURFACE, outline=LINE, width=1)
    text(d, (W - 28 - hw, 32), hint, font(13), MUTED)

    img.convert("RGB").save(OUT)
    print("wrote", OUT)


if __name__ == "__main__":
    main()
