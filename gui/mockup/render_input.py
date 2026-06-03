#!/usr/bin/env python3
"""Render the GOSE navigation chooser concept -> input-select-concept.png (PC variant)."""
from __future__ import annotations
import os
from PIL import Image, ImageDraw
from _render_common import (base, font, icon, panel, text,
                            ACC, TEXT, MUTED, LINE, SURFACE, SURFACE2)

W, H = 1280, 720
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "input-select-concept.png")

CARDS = [("keyboard", "Keyboard & Mouse", "Type and click to navigate", True),
         ("gamepad-2", "Controller", "Xbox · PS5 · 8BitDo — BT or wired", False)]


def card(img, d, x, y, w, h, ic, title, sub, sel):
    fill = (92, 208, 255, 26) if sel else SURFACE
    panel(img, [x, y, x + w, y + h], 18, fill,
          outline=ACC if sel else LINE, width=2 if sel else 1)
    g = icon(ic, 56, ACC)
    img.alpha_composite(g, (x + (w - 56) // 2, y + 30))
    text(d, (x, y + 104), title, font(20, 700), TEXT, center_w=w)
    text(d, (x, y + 134), sub, font(13), MUTED, center_w=w)
    if sel:
        tag = "DEFAULT"
        tw = d.textlength(tag, font=font(11, 700))
        panel(img, [x + (w - tw) / 2 - 11, y + 164, x + (w + tw) / 2 + 11, y + 188], 11, (92, 208, 255, 40))
        text(d, (x, y + 168), tag, font(11, 700), ACC, center_w=w)


def main():
    img = base(W, H)
    d = ImageDraw.Draw(img)

    # platform badge
    badge = "GOSE — PC App"
    bw = d.textlength(badge, font=font(13)) + 50
    bx = (W - bw) / 2
    panel(img, [bx, 120, bx + bw, 152], 16, SURFACE, outline=LINE, width=1)
    img.alpha_composite(icon("monitor", 16, MUTED), (int(bx + 16), 128))
    text(d, (bx + 40, 128), badge, font(13), MUTED)

    text(d, (0, 176), "How do you want to navigate?", font(32, 700), TEXT, center_w=W)
    text(d, (0, 224), "Choose your input. You can change it anytime in Settings.",
         font(15), MUTED, center_w=W)

    cw, ch, gap = 250, 210, 24
    total = cw * 2 + gap
    sx = (W - total) // 2
    for i, (ic, t, s, sel) in enumerate(CARDS):
        card(img, d, sx + i * (cw + gap), 290, cw, ch, ic, t, s, sel)

    img.alpha_composite(icon("plug-zap", 16, MUTED), (W // 2 - 250, 540))
    text(d, (W // 2 - 226, 540),
         "Connect a controller (Bluetooth or USB), then pick Controller — else it falls back to keyboard.",
         font(13), MUTED)

    # remember checkbox (checked)
    rx = W // 2 - 92
    panel(img, [rx, 584, rx + 20, 604], 6, ACC)
    img.alpha_composite(icon("check", 14, (6, 8, 14)), (rx + 3, 587))
    text(d, (rx + 30, 586), "Remember my choice", font(13), MUTED)

    text(d, (0, H - 44), "Arrow keys / mouse · Enter to select  —  auto-continues with default",
         font(13), MUTED, center_w=W)
    img.convert("RGB").save(OUT)
    print("wrote", OUT)


if __name__ == "__main__":
    main()
