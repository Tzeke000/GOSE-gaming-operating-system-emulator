#!/usr/bin/env python3
"""Render a concept image of the GOSE Windows-like desktop (controller-only).

Produces desktop-concept.png — a static "what it looks like" mockup to vibe on.
The living, navigable version is desktop.html. Run:  python3 render_desktop.py
"""
from __future__ import annotations

import os
from PIL import Image, ImageDraw, ImageFont

W, H = 1280, 720
OUT = os.path.join(os.path.dirname(__file__), "desktop-concept.png")

# Palette
BG_TOP = (12, 16, 34)
BG_BOT = (28, 36, 86)
PANEL = (22, 28, 52)
TILE = (38, 47, 86)
TILE_SEL = (76, 194, 255)
TEXT = (233, 238, 252)
MUTED = (150, 162, 196)
ACCENT = (120, 220, 170)


def font(size, bold=False):
    paths = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans%s.ttf" % ("-Bold" if bold else ""),
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ]
    for p in paths:
        if os.path.exists(p):
            return ImageFont.truetype(p, size)
    return ImageFont.load_default()


def vgrad(draw):
    for y in range(H):
        t = y / H
        c = tuple(int(BG_TOP[i] + (BG_BOT[i] - BG_TOP[i]) * t) for i in range(3))
        draw.line([(0, y), (W, y)], fill=c)


def tile(draw, x, y, w, h, label, glyph, selected=False):
    fill = tuple(min(255, c + 40) for c in TILE) if selected else TILE
    draw.rounded_rectangle([x, y, x + w, y + h], radius=14, fill=fill)
    if selected:
        draw.rounded_rectangle([x, y, x + w, y + h], radius=14, outline=TILE_SEL, width=4)
    # glyph chip
    draw.rounded_rectangle([x + 14, y + 14, x + 54, y + 54], radius=10, fill=(16, 22, 44))
    g = font(24, bold=True)
    gb = draw.textbbox((0, 0), glyph, font=g)
    draw.text((x + 34 - (gb[2] - gb[0]) / 2, y + 34 - (gb[3] - gb[1]) / 2),
              glyph, font=g, fill=TILE_SEL if selected else ACCENT)
    draw.text((x + 16, y + h - 30), label, font=font(17, bold=True), fill=TEXT)


def main():
    img = Image.new("RGB", (W, H))
    d = ImageDraw.Draw(img)
    vgrad(d)

    # Desktop shortcut icons (top-left) — the "hacker tools" live on the desktop.
    for i, (lbl, gl) in enumerate([("Terminal", ">_"), ("Files", "▤"), ("Network", "≈"),
                                   ("AI Bridge", "AI")]):
        ix, iy = 36, 40 + i * 92
        d.rounded_rectangle([ix, iy, ix + 60, iy + 60], radius=12, fill=(20, 26, 50))
        gg = font(22, bold=True)
        d.text((ix + 30 - d.textlength(gl, font=gg) / 2, iy + 18), gl, font=gg, fill=ACCENT)
        d.text((ix + 30 - d.textlength(lbl, font=font(13)) / 2, iy + 64), lbl,
               font=font(13), fill=MUTED)

    # Start menu panel (open) — the system "tiles"
    px, py, pw, ph = 300, 70, 900, 520
    d.rounded_rectangle([px, py, px + pw, py + ph], radius=18, fill=PANEL)
    d.text((px + 28, py + 22), "Start", font=font(26, bold=True), fill=TEXT)
    d.text((px + 28, py + 56), "Pick a system", font=font(16), fill=MUTED)

    systems = [("PSP", "▶"), ("PS2", "◆"), ("Switch", "⬡"), ("SNES", "✦"),
               ("N64", "★"), ("Genesis", "∞"), ("PS1", "●"), ("GameCube", "◼"),
               ("Dreamcast", "◐"), ("Arcade", "▣"), ("Mario 64", "M"), ("Tools", "⚙")]
    cols, tw, th, gap = 4, 190, 132, 22
    gx, gy = px + 28, py + 96
    for idx, (name, gl) in enumerate(systems):
        r, c = divmod(idx, cols)
        x = gx + c * (tw + gap)
        y = gy + r * (th + gap)
        tile(d, x, y, tw, th, name, gl, selected=(name == "PSP"))

    # Right info card: AI agents + game state
    cx, cy, cw, ch = 300, py + ph - 0, pw, 0  # placeholder (kept layout simple)

    # Taskbar
    tb = H - 56
    d.rectangle([0, tb, W, H], fill=(10, 14, 30))
    d.rounded_rectangle([12, tb + 10, 12 + 120, tb + 46], radius=10, fill=(38, 70, 120))
    d.text((30, tb + 18), "▣ GOSE", font=font(18, bold=True), fill=TEXT)
    # pinned recents
    for i, lbl in enumerate(["God of War", "Mario 64", "Sonic"]):
        bx = 150 + i * 150
        d.rounded_rectangle([bx, tb + 10, bx + 138, tb + 46], radius=8, fill=(26, 33, 60))
        d.text((bx + 12, tb + 18), lbl, font=font(14), fill=MUTED)
    # system tray
    tray = "Ava ●  Wren ●  Iris ○   ⌁ 82%   ≋ WiFi   14:32"
    d.text((W - 18 - d.textlength(tray, font=font(15)), tb + 19), tray,
           font=font(15), fill=TEXT)

    # Controller hint bar (top-right)
    hint = "[A] Select   [B] Back   [☰] Start/Settings   LB/RB Switch system"
    d.rounded_rectangle([W - 30 - d.textlength(hint, font=font(15)) - 24, 18,
                         W - 18, 52], radius=10, fill=(18, 24, 46))
    d.text((W - 30 - d.textlength(hint, font=font(15)), 27), hint,
           font=font(15), fill=MUTED)

    img.save(OUT)
    print("wrote", OUT)


if __name__ == "__main__":
    main()
