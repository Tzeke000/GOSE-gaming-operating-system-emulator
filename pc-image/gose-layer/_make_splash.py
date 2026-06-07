#!/usr/bin/env python3
"""Generate the GOSE framebuffer boot-splash PNGs from the CURRENT brand mark.

Source of truth for the brand is gui/mockup/assets/brand/gose-crystal.png (the
crystal). This renders a static version of gose-boot.html (same radial onyx
background, crystal + glow, gradient GOSE wordmark, sub-line) at framebuffer
sizes, so every splash in the boot chain matches the kiosk boot screen:

  splash/gose-splash.png         1920x1080  -> /userdata/splash/ (Batocera S28
                                 user splash; keep it the ONLY image there —
                                 S28 picks a RANDOM file from that directory,
                                 which is how the stale-logo flash happened)
  system-splash/boot-logo.png    1920x1080  -> /usr/share/batocera/splash/
  system-splash/boot-logo-4x3.png 1600x1200    (Batocera S03 early splash; needs
                                 the rootfs overlay — see system-splash/README.md)

Run from anywhere:  py -3.11 pc-image/gose-layer/_make_splash.py
Deps: Pillow. Fonts: themes/gose/fonts/Inter-{600,700}.ttf (vendored).
"""
from __future__ import annotations
import math
import os
from PIL import Image, ImageDraw, ImageFilter, ImageFont

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
CRYSTAL = os.path.join(REPO, "gui", "mockup", "assets", "brand", "gose-crystal.png")
FONTS = os.path.join(HERE, "themes", "gose", "fonts")

# gose-boot.html palette
BG_IN = (0x14, 0x19, 0x3A)    # #14193a radial center
BG_MID = (0x0B, 0x0C, 0x1E)   # #0b0c1e at 55%
BG_OUT = (0x07, 0x07, 0x0F)   # #07070f edge
GRAD_L = (0x5C, 0xD0, 0xFF)   # wordmark gradient left  #5cd0ff
GRAD_R = (0x9A, 0x5B, 0xFF)   # wordmark gradient right #9a5bff
SUB = (0x8A, 0x90, 0xC0)      # sub-line #8a90c0
GLOW = (0x6A, 0x4D, 0xFF)     # crystal glow #6a4dff


def font(size: int, weight: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(os.path.join(FONTS, f"Inter-{weight}.ttf"), size)


def radial_bg(w: int, h: int) -> Image.Image:
    """radial-gradient(120% 90% at 50% 38%, IN 0%, MID 55%, OUT 100%)."""
    img = Image.new("RGB", (w, h))
    px = img.load()
    cx, cy = w * 0.5, h * 0.38
    rx, ry = w * 1.2, h * 0.9
    for y in range(h):
        dy = (y - cy) / ry
        for x in range(w):
            dx = (x - cx) / rx
            t = min(1.0, math.sqrt(dx * dx + dy * dy))
            if t < 0.55:
                u = t / 0.55
                c = tuple(int(a + (b - a) * u) for a, b in zip(BG_IN, BG_MID))
            else:
                u = (t - 0.55) / 0.45
                c = tuple(int(a + (b - a) * u) for a, b in zip(BG_MID, BG_OUT))
            px[x, y] = c
    return img


def tracked_text(s: str, f: ImageFont.FreeTypeFont, tracking: float) -> Image.Image:
    """Render text with letter-spacing into a transparent layer (white glyphs)."""
    d0 = ImageDraw.Draw(Image.new("RGBA", (1, 1)))
    widths = [d0.textlength(ch, font=f) for ch in s]
    asc, desc = f.getmetrics()
    w = int(sum(widths) + tracking * (len(s) - 1)) + 8
    h = asc + desc + 8
    layer = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    x = 4.0
    for ch, wd in zip(s, widths):
        d.text((x, 4), ch, font=f, fill=(255, 255, 255, 255))
        x += wd + tracking
    return layer


def hgrad(w: int, h: int, left, right) -> Image.Image:
    g = Image.new("RGBA", (w, h))
    d = ImageDraw.Draw(g)
    for x in range(w):
        t = x / max(1, w - 1)
        d.line([(x, 0), (x, h)], fill=tuple(int(a + (b - a) * t) for a, b in zip(left, right)) + (255,))
    return g


def render(w: int, h: int, out: str) -> None:
    img = radial_bg(w, h).convert("RGBA")
    s = h / 1080.0  # scale everything off the 1080p design

    # crystal + violet under-glow (drop-shadow(0 0 26px #6a4dff))
    csize = int(420 * s)
    crystal = Image.open(CRYSTAL).convert("RGBA").resize((csize, csize), Image.LANCZOS)
    cx, cy = (w - csize) // 2, int(h * 0.40) - csize // 2
    glow = Image.new("RGBA", crystal.size, GLOW + (0,))
    glow.putalpha(crystal.getchannel("A"))
    glow = glow.filter(ImageFilter.GaussianBlur(int(26 * s)))
    for _ in range(2):  # double-tap the glow so it reads on the fb like the CSS one
        img.alpha_composite(glow, (cx, cy))
    img.alpha_composite(crystal, (cx, cy))

    # GOSE wordmark — Inter 700, .14em tracking, #5cd0ff -> #9a5bff gradient
    wf = font(int(74 * s), 700)
    word = tracked_text("GOSE", wf, tracking=0.14 * 74 * s)
    wgrad = hgrad(word.width, word.height, GRAD_L, GRAD_R)
    wgrad.putalpha(word.getchannel("A"))
    wx, wy = (w - word.width) // 2, int(h * 0.625)
    img.alpha_composite(wgrad, (wx, wy))

    # sub-line — Inter 600 uppercase, .22em tracking, #8a90c0
    sf = font(int(27 * s), 600)
    sub = tracked_text("GAME OPERATING SYSTEM EMULATOR", sf, tracking=0.22 * 27 * s)
    tint = Image.new("RGBA", sub.size, SUB + (255,))
    tint.putalpha(sub.getchannel("A"))
    img.alpha_composite(tint, ((w - sub.width) // 2, int(h * 0.715)))

    os.makedirs(os.path.dirname(out), exist_ok=True)
    img.convert("RGB").save(out)
    print("wrote", out, f"{w}x{h}")


if __name__ == "__main__":
    render(1920, 1080, os.path.join(HERE, "splash", "gose-splash.png"))
    render(1920, 1080, os.path.join(HERE, "system-splash", "boot-logo.png"))
    render(1600, 1200, os.path.join(HERE, "system-splash", "boot-logo-4x3.png"))
