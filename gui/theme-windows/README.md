# Windows-like front-end theme (stub) `[CUSTOM]`

Target: a controller-only home that feels like a Windows desktop —
tile/start-menu of systems, a taskbar-style bar (clock/battery/quick settings),
box-art libraries — driving the flow **Home → system → library → launch**.

See `../../docs/06-gui-plan.md` for the full plan and the Path A (theme) vs
Path B (custom Godot app) decision.

## Reality check (from research)
- **Batocera** uses `batocera-emulationstation` with **XML theme format v7** (NOT
  ES-DE — themes are not cross-compatible). **ROCKNIX** uses its own ES fork.
- So this theme targets the EmulationStation XML theme engine of the chosen
  distro. Confirm which distro is the daily driver before investing in the theme.

## Next steps (when on hardware)
1. Identify the exact ES fork + theme format version on the running distro.
2. Build `theme.xml` + assets: desktop background, system tiles ("start menu"),
   taskbar overlay, box-art library views (recent/favorites), on-screen keyboard.
3. Wire a "Tools" collection: terminal, file manager, network tools, AI bridge
   launcher (`python3 /storage/gose/ai-bridge/bridge.py`).
4. Install via `scripts/setup-device.sh`.

(No theme XML committed yet — it needs the on-device theme engine/version to
target. This stub holds the plan so the work is unambiguous next session.)
