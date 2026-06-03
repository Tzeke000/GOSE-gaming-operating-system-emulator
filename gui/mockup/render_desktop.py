#!/usr/bin/env python3
"""Render the GOSE Windows-style desktop concept -> desktop-concept.png.

Uses the vendored Inter font + Lucide icons (rasterized via cairosvg) so the
concept matches the live desktop.html. Run: python3 render_desktop.py
"""
from __future__ import annotations
import io
import os
from PIL import Image, ImageDraw, ImageFont, ImageFilter
import cairosvg

HERE = os.path.dirname(os.path.abspath(__file__))
ICONS = os.path.join(HERE, "assets", "icons")
FONTS = os.path.join(HERE, "assets", "fonts")
W, H = 1280, 720
OUT = os.path.join(HERE, "desktop-concept.png")
ACC = (92, 200, 255); ACC2 = (155, 107, 255)
TEXT = (238, 242, 255); MUTED = (150, 162, 196); LINE = (255, 255, 255, 28)

_fc = {}
def font(size, w=400):
    key = (size, w)
    if key not in _fc:
        path = os.path.join(FONTS, f"Inter-{w}.ttf")
        _fc[key] = ImageFont.truetype(path, size) if os.path.exists(path) else ImageFont.load_default()
    return _fc[key]

_ic = {}
def icon(name, size, color):
    """Rasterize a Lucide SVG and tint it `color`."""
    key = (name, size, color)
    if key in _ic:
        return _ic[key]
    png = cairosvg.svg2png(url=os.path.join(ICONS, f"{name}.svg"),
                           output_width=size, output_height=size)
    glyph = Image.open(io.BytesIO(png)).convert("RGBA")
    solid = Image.new("RGBA", glyph.size, color + (255,))
    solid.putalpha(glyph.getchannel("A"))
    _ic[key] = solid
    return solid

def panel(img, box, radius, fill):
    lay = Image.new("RGBA", img.size, (0, 0, 0, 0))
    ImageDraw.Draw(lay).rounded_rectangle(box, radius=radius, fill=fill, outline=LINE, width=1)
    img.alpha_composite(lay)

def text(d, xy, s, f, fill, center_w=None):
    if center_w is not None:
        xy = (xy[0] + (center_w - d.textlength(s, font=f)) / 2, xy[1])
    d.text(xy, s, font=f, fill=fill)


def main():
    img = Image.new("RGBA", (W, H))
    d = ImageDraw.Draw(img)
    for y in range(H):
        t = y / H
        d.line([(0, y), (W, y)], fill=(int(10 + 7 * t), int(14 + 24 * t), int(31 + 17 * t), 255))
    glow = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    gd = ImageDraw.Draw(glow)
    gd.ellipse([820, -160, 1320, 280], fill=ACC2 + (110,))
    gd.ellipse([-200, 470, 360, 900], fill=(14, 110, 160, 120))
    img.alpha_composite(glow.filter(ImageFilter.GaussianBlur(130)))

    # ---- desktop icons ----
    desk = [("monitor", "This PC"), ("gamepad-2", "Games"), ("cpu", "Emulators"),
            ("folder", "Files"), ("terminal", "Terminal"), ("sparkles", "AI Hub")]
    for i, (ic, lbl) in enumerate(desk):
        x, y = 30, 24 + i * 96
        panel(img, [x, y, x + 86, y + 84], 14, (26, 33, 60, 90))
        img.alpha_composite(icon(ic, 30, ACC), (x + 28, y + 12))
        text(d, (x, y + 58), lbl, font(12, 500), TEXT, center_w=86)

    # ---- window ----
    wx, wy, ww, wh = 330, 70, 760, 470
    panel(img, [wx, wy, wx + ww, wy + wh], 14, (20, 26, 48, 228))
    panel(img, [wx, wy, wx + ww, wy + 42], 14, (255, 255, 255, 14))
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
            panel(img, [wx + 10, yy - 4, wx + 158, yy + 26], 8, (92, 200, 255, 40))
        img.alpha_composite(icon(ic, 16, ACC if sel else MUTED), (wx + 18, yy))
        text(d, (wx + 42, yy), lbl, font(13, 600 if sel else 400), TEXT if sel else MUTED)
    games = [("God of War", (90, 160, 80)), ("Daxter", (60, 120, 170)), ("Patapon", (160, 80, 80)),
             ("Wipeout", (60, 170, 170)), ("Tekken 6", (160, 70, 120)), ("GTA VCS", (120, 160, 70)),
             ("Crisis Core", (90, 90, 170)), ("Lumines", (170, 120, 60))]
    cw, ch, gap = 138, 128, 16
    gx, gy = wx + 176, wy + 60
    for i, (nm, col) in enumerate(games):
        r, c = divmod(i, 4)
        x, y = gx + c * (cw + gap), gy + r * (ch + 36 + gap)
        sel = (i == 0)
        lay = Image.new("RGBA", img.size, (0, 0, 0, 0))
        ImageDraw.Draw(lay).rounded_rectangle([x, y, x + cw, y + ch], 10, fill=col + (215,),
            outline=ACC if sel else (255, 255, 255, 28), width=3 if sel else 1)
        img.alpha_composite(lay)
        text(d, (x + 10, y + ch - 24), nm, font(13, 700), TEXT)
        text(d, (x + 2, y + ch + 6), nm, font(12), MUTED)

    # ---- taskbar (Win11) ----
    panel(img, [0, H - 58, W, H], 0, (8, 11, 24, 225))
    btns = [("layout-grid", True), ("gamepad-2", False), ("terminal", False),
            ("sparkles", False), ("settings", False)]
    total = len(btns) * 50 - 8
    bx = W / 2 - total / 2
    for ic, start in btns:
        lay = Image.new("RGBA", img.size, (0, 0, 0, 0))
        if start:
            ImageDraw.Draw(lay).rounded_rectangle([bx, H - 50, bx + 42, H - 8], 11, fill=ACC + (255,))
        else:
            ImageDraw.Draw(lay).rounded_rectangle([bx, H - 50, bx + 42, H - 8], 11, fill=(255, 255, 255, 18))
        img.alpha_composite(lay)
        img.alpha_composite(icon(ic, 22, (7, 17, 42) if start else (205, 215, 255)),
                            (int(bx + 10), H - 39))
        bx += 50
    # tray (icons + text)
    tx = W - 18
    def tray_text(s, f, fill):
        nonlocal tx
        tx -= d.textlength(s, font=f); text(d, (tx, H - 36), s, f, fill); tx -= 14
    def tray_icon(name):
        nonlocal tx
        tx -= 18; img.alpha_composite(icon(name, 18, TEXT), (int(tx), H - 38)); tx -= 8
    tray_text("14:32", font(14, 600), TEXT)
    tray_icon("battery"); tray_text("82%", font(13), MUTED)
    tray_icon("volume-2"); tray_icon("wifi")
    tray_text("Iris", font(13), MUTED); d.ellipse([tx-14,H-31,tx-6,H-23], fill=(58,70,110)); tx-=22
    tray_text("Wren", font(13), TEXT);  d.ellipse([tx-14,H-31,tx-6,H-23], fill=ACC); tx-=22
    tray_text("Ava", font(13), TEXT);   d.ellipse([tx-14,H-31,tx-6,H-23], fill=ACC); tx-=26
    tray_text("Focus", font(13, 600), ACC); tray_icon("target")

    # hint + cursor
    hint = "Focus: D-pad move  ·  A select  ·  B back  ·  Start menu  ·  Y > pointer"
    hw = d.textlength(hint, font=font(13))
    panel(img, [W - 30 - hw - 20, 20, W - 14, 50], 10, (13, 19, 48, 200))
    text(d, (W - 28 - hw, 28), hint, font(13), MUTED)
    img.alpha_composite(icon("mouse-pointer-2", 24, TEXT), (636, 366))

    img.convert("RGB").save(OUT)
    print("wrote", OUT)


if __name__ == "__main__":
    main()
