#!/usr/bin/env python3
"""Render the GOSE Boot Menu ("BIOS") concept -> bootmenu-concept.png.

PC-style boot picker shown when L1+R1 are held at power-on. onyx theme.
"""
from __future__ import annotations
import os
from PIL import Image, ImageDraw
from _render_common import (base, font, icon, panel, text, logo,
                            ACC, TEXT, MUTED, LINE, SURFACE, SURFACE2)

W, H = 1280, 720
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "bootmenu-concept.png")

BOOT = [("play", "ROCKNIX", "microSD  ·  Linux", "Default", True),
        ("smartphone", "Android", "internal storage", "", False)]
TOOLS = [("rotate-ccw", "Recovery", "system repair & factory reset", "", False),
         ("shield", "Safe Mode", "software render · no overclock", "", False),
         ("download", "Fastboot / Flash", "firmware bootloader over USB-C", "", False),
         ("settings", "GOSE Setup", "BIOS — boot order, timeout, theme", "", False),
         ("power", "Power Off", "", "", False)]

X, COLW = 330, 620


def row(img, d, y, ic, label, sub, tag, sel):
    h = 50
    if sel:
        panel(img, [X, y, X + COLW, y + h], 12, (92, 208, 255, 30), outline=(92, 208, 255, 120), width=1)
        d.rounded_rectangle([X, y + 9, X + 4, y + h - 9], 2, fill=ACC)
    else:
        panel(img, [X, y, X + COLW, y + h], 12, (255, 255, 255, 8))
    col = ACC if sel else (184, 190, 204)
    img.alpha_composite(icon(ic, 22, col if ic != "power" else (255, 122, 110)), (X + 18, y + h // 2 - 11))
    text(d, (X + 54, y + (9 if sub else 16)), label, font(16, 700 if sel else 600),
         TEXT if sel else (214, 218, 228))
    if sub:
        text(d, (X + 54, y + 28), sub, font(12), MUTED)
    if tag:
        tw = d.textlength(tag, font=font(12, 600))
        panel(img, [X + COLW - tw - 70, y + 14, X + COLW - 48, y + 36], 11, SURFACE2, outline=LINE, width=1)
        text(d, (X + COLW - tw - 59, y + 16), tag, font(12, 600), ACC)
    if sel:
        img.alpha_composite(icon("chevron-right", 20, ACC), (X + COLW - 32, y + h // 2 - 10))
    return y + h + 8


def main():
    img = base(W, H)
    d = ImageDraw.Draw(img)

    # top bar: logo + title + POST prompt
    logo(img, 52, 56, 40)
    text(d, (80, 38), "GOSE Boot Menu", font(22, 700), TEXT)
    text(d, (80, 66), "Snapdragon 8 Gen 2  ·  abl-mod  ·  firmware v0.1", font(12), MUTED)
    prompt = "Auto-boot ROCKNIX in 5s  —  hold L1 + R1 to stay"
    pw = d.textlength(prompt, font=font(13))
    panel(img, [W - 40 - pw - 44, 38, W - 40, 70], 11, SURFACE, outline=(92, 208, 255, 90), width=1)
    img.alpha_composite(icon("triangle-alert", 16, ACC), (int(W - 40 - pw - 34), 46))
    text(d, (W - 40 - pw - 10, 46), prompt, font(13), TEXT)

    y = 124
    text(d, (X + 2, y), "BOOT DEVICE", font(11, 700), MUTED)
    y += 22
    for e in BOOT:
        y = row(img, d, y, *e)
    y += 8
    text(d, (X + 2, y), "TOOLS", font(11, 700), MUTED)
    y += 22
    for e in TOOLS:
        y = row(img, d, y, *e)

    # footer: device info + hints
    panel(img, [X, H - 56, X + COLW, H - 18], 11, SURFACE, outline=LINE, width=1)
    img.alpha_composite(icon("hard-drive", 16, MUTED), (X + 16, H - 45))
    text(d, (X + 40, H - 45), "AYN Odin 2  ·  256 GB  ·  Battery 82%", font(13), MUTED)
    hint = "D-pad move · A select · auto-boot 5s"
    text(d, (X + COLW - d.textlength(hint, font=font(12)) - 16, H - 44), hint, font(12), MUTED)

    img.convert("RGB").save(OUT)
    print("wrote", OUT)


if __name__ == "__main__":
    main()
