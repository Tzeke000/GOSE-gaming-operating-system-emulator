# GOSE Desktop — visual mockup & prototype

Two artifacts for vibe-coding the Windows-PC-style, controller-only home screen:

- **`desktop-concept.png`** — rendered concept ("what it looks like").
  Regenerate: `python3 render_desktop.py` (needs `Pillow`, `cairosvg`, `fonttools`
  from `../../requirements-dev.txt`).
- **`desktop.html`** — the **live, navigable prototype**. Open in a browser; drive
  with keyboard, mouse, or a **gamepad** (Odin pad / Xbox / **PS5**). Home → Games
  window → launch; Start menu; Settings panel.

## Look & feel (current)
A real Windows-desktop vibe: frosted **Explorer-style window** with box-art grid +
sidebar, a **Windows-11 centered taskbar** (Start in cyan + pinned apps), desktop
shortcut icons, **Start menu** (search + pinned systems + recents), a **Settings**
panel, and a system tray with **Ava/Wren/Iris** status, Wi-Fi/volume/battery,
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
