# GOSE Desktop — visual mockup & prototype

Two artifacts for vibe-coding the Windows-like, controller-only home screen:

- **`desktop-concept.png`** — a rendered concept image ("what it looks like").
  Regenerate: `python3 render_desktop.py` (uses Pillow).
- **`desktop.html`** — a **live, navigable prototype**. Open it in any browser and
  drive it with **arrow keys + Enter/Esc** or a **real gamepad** (Gamepad API).
  Home (system tiles) → pick a system → game library → launch.

## The vibe (current concept)
A Windows-desktop feel adapted to a console: a wallpaper, **desktop shortcuts**
(Terminal / Files / Network / AI Bridge — the hacker tools), a **Start menu** of
big system tiles (PSP, PS2, Switch, …), and a **taskbar** with a GOSE start
button, pinned recents, and a tray showing **Ava/Wren/Iris** connection status,
battery, Wi-Fi, and clock. Everything is highlight-and-select: D-pad/stick moves,
**A** selects, **B** backs out, **☰** opens Start/Settings.

## How to iterate ("vibe code")
Edit `desktop.html` directly — palette is in the `:root` CSS vars, systems in the
`SYSTEMS` array, per-system games in `LIB`. It's intentionally dependency-free and
self-contained so changes are instant. Once we like the feel, we port it to the
chosen implementation path (see `../../docs/06-gui-plan.md`):
- **Path A:** a `batocera-emulationstation` XML theme (format v7) on the device.
- **Path B:** a custom gamepad-first app (the HTML/JS here could even ship as a
  lightweight kiosk front-end, or be rebuilt in Godot).

This prototype is the design source of truth until we commit to a path on hardware.
