#!/usr/bin/env python3
"""Render the GOSE Windows-style desktop concept -> desktop-concept.png.

The living, navigable version is desktop.html. Run: python3 render_desktop.py
"""
from __future__ import annotations
import os
from PIL import Image, ImageDraw, ImageFont, ImageFilter

W, H = 1280, 720
OUT = os.path.join(os.path.dirname(__file__), "desktop-concept.png")
ACC = (92, 200, 255); ACC2 = (155, 107, 255)
TEXT = (238, 242, 255); MUTED = (150, 162, 196); LINE = (255, 255, 255, 30)


def font(s, bold=False):
    for p in ("/usr/share/fonts/truetype/dejavu/DejaVuSans%s.ttf" % ("-Bold" if bold else ""),
              "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"):
        if os.path.exists(p):
            return ImageFont.truetype(p, s)
    return ImageFont.load_default()


def panel(img, box, radius, fill):
    lay = Image.new("RGBA", img.size, (0, 0, 0, 0))
    ImageDraw.Draw(lay).rounded_rectangle(box, radius=radius, fill=fill, outline=LINE, width=1)
    img.alpha_composite(lay)


def main():
    img = Image.new("RGBA", (W, H))
    d = ImageDraw.Draw(img)
    for y in range(H):  # diagonal-ish gradient
        t = y / H
        c = (int(10 + 7 * t), int(14 + 24 * t), int(31 + 17 * t), 255)
        d.line([(0, y), (W, y)], fill=c)
    # soft glows
    glow = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    gd = ImageDraw.Draw(glow)
    gd.ellipse([820, -160, 1320, 280], fill=ACC2 + (110,))
    gd.ellipse([-200, 470, 360, 900], fill=(14, 110, 160, 120))
    img.alpha_composite(glow.filter(ImageFilter.GaussianBlur(130)))

    # desktop icons
    icons = [("■", "This PC"), ("▶", "Games"), ("▲", "Emulators"),
             ("≡", "Files"), (">_", "Terminal"), ("★", "AI Hub")]
    for i, (g, lbl) in enumerate(icons):
        x, y = 30, 24 + i * 96
        panel(img, [x, y, x + 86, y + 84], 14, (26, 33, 60, 90))
        gf = font(22, bold=True)
        d.text((x + 43 - d.textlength(g, font=gf) / 2, y + 12), g, font=gf, fill=ACC)
        d.text((x + 43 - d.textlength(lbl, font=font(12)) / 2, y + 60), lbl, font=font(12), fill=TEXT)

    # ---- window ----
    wx, wy, ww, wh = 330, 70, 760, 470
    panel(img, [wx, wy, wx + ww, wy + wh], 14, (20, 26, 48, 225))
    panel(img, [wx, wy, wx + ww, wy + 42], 14, (255, 255, 255, 14))
    d.text((wx + 16, wy + 12), "▶  Games", font=font(15, bold=True), fill=TEXT)
    d.text((wx + 120, wy + 13), "›  This PC  ›  Library", font=font(13), fill=MUTED)
    for i, col in enumerate([(255, 189, 68), (40, 200, 64), (255, 95, 87)]):
        d.ellipse([wx + ww - 30 - i * 22, wy + 15, wx + ww - 18 - i * 22, wy + 27], fill=col)
    # sidebar
    for i, s in enumerate(["★ Favorites", "◷ Recent", "▶ PSP", "◆ N64", "● PS2", "○ Switch"]):
        d.text((wx + 16, wy + 60 + i * 34), s, font=font(13), fill=TEXT if i == 2 else MUTED)
    # cards
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
        ld = ImageDraw.Draw(lay)
        ld.rounded_rectangle([x, y, x + cw, y + ch], 10, fill=col + (210,),
                             outline=ACC if sel else LINE[:3] + (30,), width=3 if sel else 1)
        img.alpha_composite(lay)
        d.text((x + 10, y + ch - 24), nm, font=font(13, bold=True), fill=TEXT)
        d.text((x + 2, y + ch + 6), nm, font=font(12), fill=MUTED)

    # ---- taskbar (Win11: full-width bar, centered icons) ----
    panel(img, [0, H - 58, W, H], 0, (8, 11, 24, 220))
    btns = [("■", True), ("▶", False), (">_", False), ("★", False), ("≡", False)]
    total = len(btns) * 50 - 8
    bx = W / 2 - total / 2
    for g, start in btns:
        lay = Image.new("RGBA", img.size, (0, 0, 0, 0))
        if start:
            ImageDraw.Draw(lay).rounded_rectangle([bx, H - 50, bx + 42, H - 8], 11,
                                                  fill=(92, 200, 255, 255))
        else:
            ImageDraw.Draw(lay).rounded_rectangle([bx, H - 50, bx + 42, H - 8], 11,
                                                  fill=(255, 255, 255, 18))
        img.alpha_composite(lay)
        gf = font(20, bold=True)
        d.text((bx + 21 - d.textlength(g, font=gf) / 2, H - 44), g, font=gf,
               fill=(7, 17, 42) if start else (205, 215, 255))
        bx += 50
    # tray
    tray = "◉ Focus     Ava ●  Wren ●  Iris ○     wifi   vol   bat 82%    14:32"
    d.text((W - 18 - d.textlength(tray, font=font(14)), H - 38), tray, font=font(14), fill=TEXT)

    # hint + cursor
    hint = "Focus: D-pad move · A select · B back · Menu=Start · Y -> pointer"
    panel(img, [W - 30 - d.textlength(hint, font=font(13)) - 20, 20,
                W - 14, 50], 10, (13, 19, 48, 200))
    d.text((W - 28 - d.textlength(hint, font=font(13)), 28), hint, font=font(13), fill=MUTED)
    # mouse cursor
    cur = [(past := (642, 372)), (662, 392), (652, 392), (657, 404), (651, 406), (647, 394), (640, 398)]
    d.polygon(cur, fill=(255, 255, 255), outline=(10, 15, 34))

    img.convert("RGB").save(OUT)
    print("wrote", OUT)


if __name__ == "__main__":
    main()
