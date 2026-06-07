# GOSE Desktop — visual mockup & prototype

Live prototypes + rendered concepts for the Windows-PC-style, controller-only UI.
Flow: **boot.html → login.html → desktop.html** (the login signs into the desktop).

| Screen | Live prototype | Concept PNG | Render with |
|--------|----------------|-------------|-------------|
| Boot splash | `boot.html` | `boot-concept.png` | `python3 render_boot.py` |
| Login / user select | `login.html` | `login-concept.png` | `python3 render_login.py` |
| Desktop | `desktop.html` | `desktop-concept.png` | `python3 render_desktop.py` |

Open the HTML in a browser; drive with keyboard, mouse, or a **gamepad** (Odin pad
/ Xbox / **PS5**). Renderers need `Pillow`, `cairosvg`, `fonttools` (see
`../../requirements-dev.txt`) and share `_render_common.py`.

## Branding
The boot splash (`boot.html` / `boot-concept.png`) recreates the GOSE identity:
hexagon "G" + gamepad mark, violet→blue gradient, italic wordmark, capability
icons, and the credit line. The mark is exported to `assets/brand/gose-logo.png` for reuse.
To drop in the exact logo art, **replace `assets/brand/gose-logo.png`** (square,
transparent PNG) — `boot.html` picks it up automatically. The splash is
brand-fixed (always black + violet/blue), independent of the UI theme picker.

## Themes (default: sleek clean black)
All screens share `assets/themes.css`. The default theme is **Onyx** (sleek black);
users switch in the desktop's **Settings → Theme** (Onyx / Midnight / Neon / Light),
and the choice persists in `localStorage` and applies via `<html data-theme="…">`.
Add a theme by adding a `[data-theme="…"]` block of CSS vars in `themes.css`.

## Look & feel (current)
A real Windows-desktop vibe: frosted **Explorer-style window** with box-art grid +
sidebar, a **Windows-11 centered taskbar** (Start in cyan + pinned apps), desktop
shortcut icons, **Start menu** (search + pinned systems + recents), a **Settings**
panel, and a system tray with **AI agent** status, Wi-Fi/volume/battery,
clock, and the current **input mode**.

## Multi-input (matches the device requirement)
- **Focus mode** (default): D-pad/stick moves a highlight, **A** select, **B** back.
- **Pointer mode**: press **Y** → right stick becomes a mouse cursor, **A** clicks.
  On device this is **AntiMicroX** with per-app auto-profiles (`docs/07-controllers.md`).
- **Mouse + keyboard** work natively; **PS5 DualSense** via the standard Gamepad API.

## Vibe-coding it
Edit `desktop.html`: palette in `:root` CSS vars, systems in `SYS`, games in
`GAMES`, settings rows in `SETTINGS`. Icons are tintable Lucide SVGs (just add a
`<span class="ic" data-i="icon-name">`); fonts are Inter. Instant, dependency-free.

## Vendored assets (pulled into the repo, licenses included)
- **Lucide icons** — `assets/icons/*.svg` (ISC license, `assets/icons/LICENSE`).
- **Inter font** — `assets/fonts/inter-latin-*.woff2` for the web + `Inter-*.ttf`
  for the PNG renderer (OFL, `assets/fonts/LICENSE`).
Both are MIT/ISC/OFL and safe to ship. See `docs/09-toolchain.md`.
