#!/usr/bin/env python3
"""Render the GOSE boot splash concept -> boot-concept.png."""
from __future__ import annotations
import os
from PIL import ImageDraw
from _render_common import base, font, text, logo, ACC, TEXT, MUTED, SURFACE2, LINE

W, H = 1280, 720
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "boot-concept.png")


def main():
    img = base(W, H)
    d = ImageDraw.Draw(img)
    cx = W // 2
    logo(img, cx, H // 2 - 70, 128)
    text(d, (0, H // 2 + 18), "GOSE", font(54, 700), TEXT, center_w=W)
    text(d, (0, H // 2 + 84), "Gaming Operating System Emulator",
         font(17, 500), MUTED, center_w=W)

    # slim progress bar
    bw, bx, by = 320, cx - 160, H // 2 + 150
    d.rounded_rectangle([bx, by, bx + bw, by + 6], 3, fill=SURFACE2)
    d.rounded_rectangle([bx, by, bx + int(bw * 0.62), by + 6], 3, fill=ACC)
    text(d, (0, by + 18), "Starting services · controllers · emulators",
         font(13), MUTED, center_w=W)

    text(d, (0, H - 46), "v0.1  ·  ROCKNIX  ·  Snapdragon 8 Gen 2",
         font(13, 500), (90, 96, 112), center_w=W)
    img.convert("RGB").save(OUT)
    print("wrote", OUT)


if __name__ == "__main__":
    main()
