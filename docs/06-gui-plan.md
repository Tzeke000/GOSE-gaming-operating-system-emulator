# Windows-like GUI Plan `[CUSTOM]`

Goal: a home screen that **looks/behaves like a Windows desktop** (tiles/start-menu
+ taskbar feel) but is **100% controller-navigable**. Flow: Home → system → that
system's library → title → auto-launch. Plus a tucked-away **Tools** area
(terminal, file manager, network tools, AI bridge launcher).

## Two implementation paths (decide after hands-on with the theme engine)

### Path A — Theme the distro's EmulationStation (fastest)
- Batocera = `batocera-emulationstation`, **XML theme format v7** (NOT ES-DE; see
  research findings). ROCKNIX = its own ES fork.
- Build a **Windows-style theme**: desktop-like background, tile/icon grid for
  systems = "start menu," a bottom taskbar-style bar (clock, battery, quick
  settings), box-art library views, recents/favorites.
- Pros: ships with controller nav, on-screen keyboard, scraping, per-system
  emulator launching — all for free. Lowest effort, fastest to "feels custom."
- Cons: bounded by the theme engine's layout primitives; a true desktop metaphor
  (draggable windows, real taskbar) is faked, not real.
- **Work product:** `gui/theme-windows/` (theme.xml + assets). Stub started.

### Path B — Custom front-end app (more control, "really mine")
- A standalone gamepad-first app that renders the desktop UI and launches emulators
  by command line. Candidate stacks (gamepad input is the key constraint):
  - **Godot 4** — excellent gamepad input, ships a small runtime, easy "desktop
    with tiles + taskbar" UI, GLES/Vulkan on Adreno. Strong candidate.
  - Web/Electron-ish (heavy on a handheld) — avoid unless needed.
  - SDL2 + a UI lib (lightweight, more manual).
- Pros: real desktop metaphor, exactly Zeke's vision, reusable AI-overlay surface.
- Cons: must re-implement library/scraping/launching/on-screen keyboard the front-
  ends give for free.

## Recommendation
Start with **Path A** to get the controller-only home→system→library→launch flow
working and "Windows-ish" quickly, while prototyping **Path B** in Godot for the
parts the theme can't express (real taskbar, AI assistant overlay). Promote to B
only where A visibly falls short. Keep both behind the same `setup-device.sh` so a
re-flash restores whichever we choose.

## Controller-only requirements (apply to both paths)
- D-pad/stick = move highlight; A = select; B = back; a held button or Start =
  taskbar/quick settings; on-screen keyboard for text (Wi-Fi password, search).
- Big, TV-legible at distance. Respect per-system aspect ratios on launch.
- Tools area reachable but out of the gaming path so the game UX stays clean.

## Open questions
- How "real" must the Windows look be (cosmetic vs. literal desktop windows)?
- Should the AI agent get an on-screen overlay here (ties into the AI control
  agent's `screen`/`input` capabilities)?
