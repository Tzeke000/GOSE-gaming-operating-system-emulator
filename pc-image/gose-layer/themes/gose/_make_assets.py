#!/usr/bin/env python3
"""Generate/refresh the GOSE EmulationStation theme assets (rerunnable).

Creates art/background.png (onyx gradient) and copies the GOSE logo + Inter fonts
from gui/mockup into this theme so it's self-contained. Run from anywhere:
    python3 pc-image/gose-layer/themes/gose/_make_assets.py
"""
from __future__ import annotations
import os
import shutil
from PIL import Image, ImageDraw, ImageFilter

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, "..", "..", "..", ".."))
SRC = os.path.join(REPO, "gui", "mockup", "assets")
W, H = 1920, 1080


def background():
    img = Image.new("RGBA", (W, H))
    d = ImageDraw.Draw(img)
    for y in range(H):  # near-black vertical gradient (matches onyx theme)
        t = y / H
        d.line([(0, y), (W, y)], fill=(int(8 + 2 * t), int(9 + 2 * t), int(12 + 4 * t), 255))
    glow = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    ImageDraw.Draw(glow).ellipse([W * 0.55, -H * 0.35, W * 1.15, H * 0.45], fill=(30, 46, 70, 90))
    img.alpha_composite(glow.filter(ImageFilter.GaussianBlur(220)))
    os.makedirs(os.path.join(HERE, "art"), exist_ok=True)
    img.convert("RGB").save(os.path.join(HERE, "art", "background.png"))


def copies():
    os.makedirs(os.path.join(HERE, "art"), exist_ok=True)
    os.makedirs(os.path.join(HERE, "fonts"), exist_ok=True)
    shutil.copy(os.path.join(SRC, "brand", "gose-logo.png"), os.path.join(HERE, "art", "logo.png"))
    for f in ("Inter-700.ttf", "Inter-600.ttf"):
        shutil.copy(os.path.join(SRC, "fonts", f), os.path.join(HERE, "fonts", f))


if __name__ == "__main__":
    background()
    copies()
    print("theme assets written to", HERE)
