# 15 — Brand: the GOSE Core `[CUSTOM]`

> Direction set by Zeke, 2026-06-04 (with a reference render). The whole OS is
> branded around one symbol — the **GOSE Core** — the way Apple uses the apple.
> No letters, no controllers: a platform artifact, not "another emulator frontend."

## The symbol
A **floating faceted crystal** (elongated vertical bipyramid): purple on the left,
blue on the right, **glowing cyan edges**, a bright **energy core + plus** at the
heart, floating over a **holographic ring** with drifting particles. Reads as an
AI core / emulator kernel / gateway — what GOSE actually is.

## Assets (vector — crisp at any size, recolorable, animatable)
- `gui/mockup/assets/brand/gose-core.svg` — full mark (crystal + halo + base ring +
  particles). Use for **boot splash** and large hero spots.
- `gui/mockup/assets/brand/gose-core-mark.svg` — crystal + core only, square viewBox.
  Use for **headers, taskbar Start, login, and the tiny Core next to each AI's name**.
- Built as SVG on purpose: scales 16px→splash, themeable, and **animates** (the boot
  splash grows the Core in then floats it — Zeke's "Seed" idea, `boot.html`).

## Where it appears (rebranded 2026-06-04, replacing the old hexagon/gamepad logo)
boot splash · boot menu · BIOS (gose-setup) · first-time setup (gose-oobe) · login ·
desktop Start button · **a tiny Core beside every connected AI** in the taskbar
(lit = present, dimmed = offline). The Core is the universal marker for "an AI on
this platform."

## Palette
Black-chrome body, purple `#6f3cff`/`#c08bff`, blue `#2f6cff`/`#56b6ff`, cyan edge
`#9fe4ff`, core white→cyan. Matches the onyx theme accent family (`assets/themes.css`).

## TODO
- **Device boot splash as PNG:** Batocera wants a raster splash. Rasterize the SVG
  with a standalone tool (**resvg** — single binary, no deps; pull into the repo when
  we bake the device image). The Cairo-based Python renderer in `gui/mockup/` can't
  run on the current Windows box (no libcairo).
- Optionally regenerate the old concept PNGs (`*-concept.png`) with the new Core.
