#!/usr/bin/env python3
"""Render the GOSE boot splash concept -> boot-concept.png.

Recreates Zeke's brand splash: hexagon "G" mark with a gamepad, violet->blue
gradient, italic GOSE wordmark, capability icons, and the credit line. The live
animated version is boot.html. To use the exact logo art instead of this
recreation, drop a PNG at assets/brand/gose-logo.png (see boot.html).
"""
from __future__ import annotations
import math
import os
from PIL import Image, ImageDraw, ImageFilter
from _render_common import font, icon

HERE = os.path.dirname(os.path.abspath(__file__))
W, H = 1280, 720
OUT = os.path.join(HERE, "boot-concept.png")
LOGO = os.path.join(HERE, "assets", "brand", "gose-logo.png")

VIOLET = (176, 108, 255)
BLUE = (79, 134, 255)
GLOW = (150, 80, 255)
DIM = (150, 120, 200)


def vgrad(w, h, top=VIOLET, bot=BLUE):
    g = Image.new("RGBA", (w, h))
    d = ImageDraw.Draw(g)
    for y in range(h):
        t = y / max(1, h - 1)
        d.line([(0, y), (w, y)], fill=(int(top[0] + (bot[0] - top[0]) * t),
                                       int(top[1] + (bot[1] - top[1]) * t),
                                       int(top[2] + (bot[2] - top[2]) * t), 255))
    return g


def hexagon(cx, cy, r):
    return [(cx + r * math.cos(math.radians(60 * k - 90)),
             cy + r * math.sin(math.radians(60 * k - 90))) for k in range(6)]


def brand_mark(img, cx, cy, R):
    """Hexagon ring + centered gamepad, filled with the violet->blue gradient."""
    box = (cx - R - 30, cy - R - 30, cx + R + 30, cy + R + 30)
    bw, bh = box[2] - box[0], box[3] - box[1]
    grad = vgrad(bw, bh)

    # ring mask (outer hex minus inner hex), relative to box
    mask = Image.new("L", (bw, bh), 0)
    md = ImageDraw.Draw(mask)
    ox, oy = cx - box[0], cy - box[1]
    md.polygon(hexagon(ox, oy, R), fill=255)
    md.polygon(hexagon(ox, oy, int(R * 0.80)), fill=0)

    # outer glow behind the ring
    glow = Image.new("RGBA", (bw, bh), (0, 0, 0, 0))
    ImageDraw.Draw(glow).polygon(hexagon(ox, oy, R), outline=GLOW + (255,), width=10)
    img.alpha_composite(glow.filter(ImageFilter.GaussianBlur(16)), (box[0], box[1]))

    img.paste(grad, (box[0], box[1]), mask)

    # gamepad in the upper-center of the mark, gradient-filled
    gp_size = int(R * 1.05)
    gp = icon("gamepad-2", gp_size, (255, 255, 255))  # alpha = glyph
    gmask = gp.getchannel("A")
    gpgrad = vgrad(gp_size, gp_size)
    img.paste(gpgrad, (cx - gp_size // 2, cy - int(R * 0.62)), gmask)


def render_word(s, f, fill, shear=0.20, tracking=10, pad=30):
    d0 = ImageDraw.Draw(Image.new("RGBA", (1, 1)))
    widths = [d0.textlength(ch, font=f) for ch in s]
    asc, desc = f.getmetrics()
    w = int(sum(widths) + tracking * (len(s) - 1)) + pad * 2
    h = asc + desc + pad
    tmp = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    dt = ImageDraw.Draw(tmp)
    x = pad
    for ch, wd in zip(s, widths):
        dt.text((x, pad // 2), ch, font=f, fill=fill)
        x += wd + tracking
    ext = int(h * shear)
    out = tmp.transform((w + ext, h), Image.AFFINE, (1, -shear, 0, 0, 1, 0), resample=Image.BICUBIC)
    return out


def center(img, layer, cy):
    img.alpha_composite(layer, ((W - layer.width) // 2, cy))


def text_c(d, s, f, fill, cy, tracking=0):
    if tracking:
        total = sum(d.textlength(ch, font=f) for ch in s) + tracking * (len(s) - 1)
        x = (W - total) / 2
        for ch in s:
            d.text((x, cy), ch, font=f, fill=fill)
            x += d.textlength(ch, font=f) + tracking
    else:
        d.text(((W - d.textlength(s, font=f)) / 2, cy), s, font=f, fill=fill)


def main():
    img = Image.new("RGBA", (W, H), (3, 3, 6, 255))
    d = ImageDraw.Draw(img)

    brand_mark(img, W // 2, 168, 92)

    # GOSE wordmark (italic), white with a violet underglow
    word = render_word("GOSE", font(78, 700), (245, 245, 255))
    glow = word.filter(ImageFilter.GaussianBlur(14))
    center(img, glow, 300)
    center(img, word, 300)
    text_c(d, "GAME OPERATING SYSTEM EMULATOR", font(18, 600), VIOLET, 404, tracking=6)

    text_c(d, "BOOTING...", font(16, 700), (190, 150, 255), 452, tracking=8)

    # progress bar with a bright leading comet
    bw, bx, by = 360, (W - 360) // 2, 488
    d.rounded_rectangle([bx, by, bx + bw, by + 7], 4, fill=(40, 36, 60))
    fill_w = int(bw * 0.62)
    bar = vgrad(fill_w, 7, top=VIOLET, bot=BLUE)
    bmask = Image.new("L", (fill_w, 7), 0)
    ImageDraw.Draw(bmask).rounded_rectangle([0, 0, fill_w - 1, 6], 3, fill=255)
    img.paste(bar, (bx, by), bmask)
    comet = Image.new("RGBA", (40, 40), (0, 0, 0, 0))
    ImageDraw.Draw(comet).ellipse([14, 14, 26, 26], fill=(220, 210, 255, 255))
    img.alpha_composite(comet.filter(ImageFilter.GaussianBlur(6)), (bx + fill_w - 20, by - 16))

    # capability icons
    caps = ["gamepad-2", "cpu", "globe", "cloud"]
    gap, sz = 110, 38
    sx = W // 2 - (len(caps) - 1) * gap // 2
    for i, ic in enumerate(caps):
        g = icon(ic, sz, (170, 130, 245))
        img.alpha_composite(g, (sx + i * gap - sz // 2, 548))

    text_c(d, "POWERED BY EMULATION.  DRIVEN BY AI.", font(13, 600), (140, 110, 190), 612, tracking=4)
    text_c(d, "by Ezekiel Angeles-Gonzalez  ·  powered by Tzeke000 Studios",
           font(13, 500), (110, 96, 140), 662, tracking=2)

    img.convert("RGB").save(OUT)
    print("wrote", OUT)


def save_logo():
    """Standalone transparent brand mark for reuse (boot.html, etc.)."""
    s = 512
    m = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    brand_mark(m, s // 2, int(s * 0.52), 150)
    m.save(LOGO)
    print("wrote", LOGO)


if __name__ == "__main__":
    main()
    save_logo()
