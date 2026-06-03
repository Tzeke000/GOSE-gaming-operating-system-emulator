#!/usr/bin/env python3
"""Mock preview of the GOSE EmulationStation theme (detailed gamelist view).

ES can't render in this container, so this approximates how theme.xml will look on
the booted VM/device, to set expectations. -> theme-preview.png
"""
from __future__ import annotations
import os
import sys
from PIL import Image, ImageDraw

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "gui", "mockup"))
from _render_common import (base, font, icon, panel, text, brand_logo,  # noqa: E402
                            ACC, TEXT, MUTED, LINE, SURFACE, SURFACE2)

W, H = 1280, 720
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                   "gose-layer", "themes", "gose", "theme-preview.png")

GAMES = ["God of War: Chains of Olympus", "Daxter", "Patapon", "Wipeout Pure",
         "Tekken 6", "GTA: Vice City Stories", "Crisis Core: FF VII", "Lumines",
         "Gran Turismo", "Metal Gear Solid: Peace Walker"]
META = [("Developer", "Ready at Dawn"), ("Publisher", "Sony CEA"),
        ("Genre", "Action / Hack-n-slash"), ("Players", "1"), ("Released", "2008")]
HELP = [("A", "Launch"), ("B", "Back"), ("X", "Options"), ("Y", "Favorite"), ("≡", "Menu")]


def main():
    img = base(W, H)
    d = ImageDraw.Draw(img)

    brand_logo(img, 44, 44, 56)
    text(d, (76, 30), "PlayStation Portable", font(26, 700), TEXT)

    # left: game list with accent selector on the focused row
    lx, ly, rh = 40, 100, 50
    for i, g in enumerate(GAMES):
        y = ly + i * rh
        if i == 0:
            panel(img, [lx, y, lx + 520, y + rh - 8], 8, (92, 208, 255, 40))
            d.rounded_rectangle([lx, y + 6, lx + 4, y + rh - 14], 2, fill=ACC)
        nm = g if len(g) < 30 else g[:29] + "…"
        text(d, (lx + 20, y + 11), nm, font(19, 700 if i == 0 else 500),
             (255, 255, 255) if i == 0 else (210, 214, 224) if i % 1 == 0 else MUTED)

    # right: box art + metadata + description
    ax, ay, aw, ah = 700, 100, 480, 300
    grad = Image.new("RGBA", (aw, ah))
    for yy in range(ah):
        t = yy / ah
        ImageDraw.Draw(grad).line([(0, yy), (aw, yy)],
                                  fill=(int(40 + 30 * t), int(50 + 20 * t), int(70 - 10 * t), 255))
    m = Image.new("L", (aw, ah), 0)
    ImageDraw.Draw(m).rounded_rectangle([0, 0, aw - 1, ah - 1], 12, fill=255)
    img.paste(grad, (ax, ay), m)
    d.rounded_rectangle([ax, ay, ax + aw, ay + ah], 12, outline=LINE, width=1)
    text(d, (ax + 18, ay + ah - 38), "God of War: Chains of Olympus", font(16, 700), TEXT)

    my = ay + ah + 22
    for i, (lbl, val) in enumerate(META):
        yy = my + i * 26
        text(d, (ax, yy), lbl + ":", font(13, 700), MUTED)
        text(d, (ax + 96, yy), val, font(13, 600), TEXT)
    # rating stars
    text(d, (ax, my + len(META) * 26), "Rating:", font(13, 700), MUTED)
    for s in range(5):
        col = ACC if s < 4 else (60, 64, 78)
        img.alpha_composite(icon("star", 16, col), (ax + 96 + s * 20, my + len(META) * 26 - 1))

    desc = ("A young Spartan warrior, Kratos, serves the gods of Olympus. Sent to "
            "stop the fall of the sun, he battles the minions of the underworld in "
            "this PSP entry — controller-only, AI-playable through the GOSE agent.")
    # wrap description
    words, line, yy = desc.split(), "", my + len(META) * 26 + 40
    for w in words:
        if d.textlength(line + " " + w, font=font(13)) > aw - 8:
            text(d, (ax, yy), line, font(13), (199, 204, 218)); line = w; yy += 22
        else:
            line = (line + " " + w).strip()
    text(d, (ax, yy), line, font(13), (199, 204, 218))

    # bottom helpsystem bar (styled button hints)
    total = sum(120 for _ in HELP)
    hx = (W - total) // 2
    for letter, label in HELP:
        d.ellipse([hx, H - 46, hx + 26, H - 20], outline=ACC, width=2)
        text(d, (hx + 13 - d.textlength(letter, font=font(13, 700)) / 2, H - 43), letter, font(13, 700), ACC)
        text(d, (hx + 34, H - 42), label, font(13, 600), MUTED)
        hx += 120

    img.convert("RGB").save(OUT)
    print("wrote", OUT)


if __name__ == "__main__":
    main()
