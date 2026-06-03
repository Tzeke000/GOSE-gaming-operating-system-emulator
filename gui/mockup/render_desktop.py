#!/usr/bin/env python3
"""Render the GOSE Windows-style desktop concept -> desktop-concept.png.

Default "onyx" (sleek black) theme; matches desktop.html. Uses the shared
renderer (Inter font + Lucide icons via cairosvg). Run: python3 render_desktop.py
"""
from __future__ import annotations
import os
from PIL import Image, ImageDraw
from _render_common import (base, font, icon, panel, text,
                            ACC, TEXT, MUTED, LINE, SURFACE, SURFACE2)

W, H = 1280, 720
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "desktop-concept.png")
WIN = (18, 19, 26)          # window/panel fill (onyx)
TASKBAR = (10, 11, 15)


def main():
    img = base(W, H)
    d = ImageDraw.Draw(img)

    # desktop icons
    desk = [("monitor", "This PC"), ("gamepad-2", "Games"), ("cpu", "Emulators"),
            ("folder", "Files"), ("terminal", "Terminal"), ("sparkles", "AI Hub")]
    for i, (ic, lbl) in enumerate(desk):
        x, y = 30, 24 + i * 96
        panel(img, [x, y, x + 86, y + 84], 14, (22, 24, 32, 150))
        img.alpha_composite(icon(ic, 30, ACC), (x + 28, y + 12))
        text(d, (x, y + 58), lbl, font(12, 500), TEXT, center_w=86)

    # window
    wx, wy, ww, wh = 330, 70, 760, 470
    panel(img, [wx, wy, wx + ww, wy + wh], 14, WIN + (240,))
    panel(img, [wx, wy, wx + ww, wy + 42], 14, (255, 255, 255, 10))
    img.alpha_composite(icon("gamepad-2", 18, TEXT), (wx + 14, wy + 12))
    text(d, (wx + 40, wy + 12), "Games", font(15, 700), TEXT)
    img.alpha_composite(icon("chevron-right", 16, MUTED), (wx + 96, wy + 13))
    text(d, (wx + 116, wy + 13), "This PC", font(13), MUTED)
    img.alpha_composite(icon("chevron-right", 16, MUTED), (wx + 168, wy + 13))
    text(d, (wx + 188, wy + 13), "Library", font(13), MUTED)
    for i, col in enumerate([(255, 189, 68), (40, 200, 64), (255, 95, 87)]):
        d.ellipse([wx + ww - 30 - i * 22, wy + 15, wx + ww - 18 - i * 22, wy + 27], fill=col)
    side = [("star", "Favorites", False), ("clock", "Recent", False), ("play", "PSP", True),
            ("gamepad-2", "N64", False), ("gamepad-2", "PS2", False), ("gamepad-2", "Switch", False)]
    for i, (ic, lbl, sel) in enumerate(side):
        yy = wy + 60 + i * 34
        if sel:
            panel(img, [wx + 10, yy - 4, wx + 158, yy + 26], 8, (92, 208, 255, 36))
        img.alpha_composite(icon(ic, 16, ACC if sel else MUTED), (wx + 18, yy))
        text(d, (wx + 42, yy), lbl, font(13, 600 if sel else 400), TEXT if sel else MUTED)
    games = [("God of War", (70, 96, 64)), ("Daxter", (48, 70, 104)), ("Patapon", (104, 56, 56)),
             ("Wipeout", (44, 100, 100)), ("Tekken 6", (104, 50, 78)), ("GTA VCS", (78, 100, 50)),
             ("Crisis Core", (62, 62, 110)), ("Lumines", (110, 80, 44))]
    cw, ch, gap = 138, 128, 16
    gx, gy = wx + 176, wy + 60
    for i, (nm, col) in enumerate(games):
        r, c = divmod(i, 4)
        x, y = gx + c * (cw + gap), gy + r * (ch + 36 + gap)
        sel = (i == 0)
        lay = Image.new("RGBA", img.size, (0, 0, 0, 0))
        ImageDraw.Draw(lay).rounded_rectangle([x, y, x + cw, y + ch], 10, fill=col + (235,),
            outline=ACC if sel else (255, 255, 255, 22), width=3 if sel else 1)
        img.alpha_composite(lay)
        text(d, (x + 10, y + ch - 24), nm, font(13, 700), TEXT)
        text(d, (x + 2, y + ch + 6), nm, font(12), MUTED)

    # taskbar (Win11 centered)
    panel(img, [0, H - 58, W, H], 0, TASKBAR + (240,))
    btns = [("layout-grid", True), ("gamepad-2", False), ("terminal", False),
            ("sparkles", False), ("settings", False)]
    bx = W / 2 - (len(btns) * 50 - 8) / 2
    for ic, start in btns:
        lay = Image.new("RGBA", img.size, (0, 0, 0, 0))
        fill = ACC + (255,) if start else (255, 255, 255, 16)
        ImageDraw.Draw(lay).rounded_rectangle([bx, H - 50, bx + 42, H - 8], 11, fill=fill)
        img.alpha_composite(lay)
        img.alpha_composite(icon(ic, 22, (6, 8, 14) if start else (205, 210, 224)),
                            (int(bx + 10), H - 39))
        bx += 50
    # tray
    tx = W - 18
    def tt(s, f, fill):
        nonlocal tx; tx -= d.textlength(s, font=f); text(d, (tx, H - 36), s, f, fill); tx -= 14
    def ti(name):
        nonlocal tx; tx -= 18; img.alpha_composite(icon(name, 18, TEXT), (int(tx), H - 38)); tx -= 8
    tt("14:32", font(14, 600), TEXT)
    ti("battery"); tt("82%", font(13), MUTED); ti("volume-2"); ti("wifi")
    tt("Iris", font(13), MUTED); d.ellipse([tx-14,H-31,tx-6,H-23], fill=(58,62,76)); tx-=22
    tt("Wren", font(13), TEXT);  d.ellipse([tx-14,H-31,tx-6,H-23], fill=ACC); tx-=22
    tt("Ava", font(13), TEXT);   d.ellipse([tx-14,H-31,tx-6,H-23], fill=ACC); tx-=26
    tt("Focus", font(13, 600), ACC); ti("target")

    hint = "Focus: D-pad move  ·  A select  ·  B back  ·  Start menu  ·  Y > pointer"
    hw = d.textlength(hint, font=font(13))
    panel(img, [W - 30 - hw - 20, 20, W - 14, 50], 10, SURFACE, outline=LINE, width=1)
    text(d, (W - 28 - hw, 28), hint, font(13), MUTED)
    img.alpha_composite(icon("mouse-pointer-2", 24, TEXT), (636, 366))

    img.convert("RGB").save(OUT)
    print("wrote", OUT)


if __name__ == "__main__":
    main()
