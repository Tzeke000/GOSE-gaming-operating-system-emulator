"""Shared helpers for the GOSE concept renderers (boot/login/desktop).

Uses the vendored Inter font + Lucide icons. Palette mirrors the default "onyx"
theme in assets/themes.css so the rendered PNGs match the live HTML.
"""
from __future__ import annotations
import io
import os
from PIL import Image, ImageDraw, ImageFont, ImageFilter
import cairosvg

HERE = os.path.dirname(os.path.abspath(__file__))
ICONS = os.path.join(HERE, "assets", "icons")
FONTS = os.path.join(HERE, "assets", "fonts")

# onyx palette (RGB; matches themes.css)
ACC = (92, 208, 255)
ACC2 = (139, 144, 166)
TEXT = (237, 240, 246)
MUTED = (139, 145, 166)
LINE = (255, 255, 255, 22)
SURFACE = (16, 17, 22)
SURFACE2 = (22, 24, 32)

_fc = {}
def font(size, w=400):
    key = (size, w)
    if key not in _fc:
        p = os.path.join(FONTS, f"Inter-{w}.ttf")
        _fc[key] = ImageFont.truetype(p, size) if os.path.exists(p) else ImageFont.load_default()
    return _fc[key]

_ic = {}
def icon(name, size, color):
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

def base(W, H, glow=True):
    """Clean near-black background with a subtle top-right glow."""
    img = Image.new("RGBA", (W, H))
    d = ImageDraw.Draw(img)
    for y in range(H):
        t = y / H
        d.line([(0, y), (W, y)], fill=(int(8 + 2 * t), int(9 + 2 * t), int(12 + 4 * t), 255))
    if glow:
        g = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        ImageDraw.Draw(g).ellipse([W * 0.55, -H * 0.35, W * 1.15, H * 0.45], fill=(30, 46, 70, 90))
        img.alpha_composite(g.filter(ImageFilter.GaussianBlur(150)))
    return img

def panel(img, box, radius, fill, outline=LINE, width=1):
    lay = Image.new("RGBA", img.size, (0, 0, 0, 0))
    ImageDraw.Draw(lay).rounded_rectangle(box, radius=radius, fill=fill, outline=outline, width=width)
    img.alpha_composite(lay)

def text(d, xy, s, f, fill, center_w=None):
    if center_w is not None:
        xy = (xy[0] + (center_w - d.textlength(s, font=f)) / 2, xy[1])
    d.text(xy, s, font=f, fill=fill)

def logo(img, cx, cy, size):
    """GOSE logo mark: rounded square with an accent gradient + bold G."""
    half = size // 2
    grad = Image.new("RGBA", (size, size))
    for i in range(size):
        t = i / size
        c = (int(ACC[0] + (155 - ACC[0]) * t), int(ACC[1] + (107 - ACC[1]) * t),
             int(ACC[2] + (255 - ACC[2]) * t), 255)
        ImageDraw.Draw(grad).line([(0, i), (size, i)], fill=c)
    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, size - 1, size - 1], radius=size // 4, fill=255)
    img.paste(grad, (cx - half, cy - half), mask)
    d = ImageDraw.Draw(img)
    gf = font(int(size * 0.62), 700)
    g = "G"
    d.text((cx - d.textlength(g, font=gf) / 2, cy - int(size * 0.40)), g, font=gf, fill=(6, 8, 14))


BRAND = os.path.join(HERE, "assets", "brand", "gose-logo.png")
_brand = {}
def brand_logo(img, cx, cy, size):
    """Paste the official GOSE mark (hexagon + gamepad) centered at (cx, cy).

    The PNG includes glow padding; bump `size` ~20% over the visible mark you want.
    """
    if size not in _brand:
        _brand[size] = Image.open(BRAND).convert("RGBA").resize((size, size), Image.LANCZOS)
    b = _brand[size]
    img.alpha_composite(b, (cx - size // 2, cy - size // 2))

