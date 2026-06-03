# GOSE theme (batocera-emulationstation, format v7)

Sleek-black "onyx" EmulationStation theme that makes the booted GOSE-PC VM (and
later the Odin 2) match the GOSE desktop look — not stock Batocera. Installed to
`/userdata/themes/gose` and selected via `emulationstation.theme=gose`
(`pc-image/gose-layer/system/batocera.conf.gose`).

| File | Purpose |
|------|---------|
| `theme.xml` | Theme definition: system carousel, basic + detailed gamelists, styled help bar; onyx bg + cyan (#5CD0FF) accent + Inter fonts |
| `art/background.png` | Onyx gradient background (generated) |
| `art/logo.png` | GOSE brand mark |
| `fonts/Inter-700.ttf`, `Inter-600.ttf` | Typography (matches the web UI) |
| `_make_assets.py` | Regenerates `art/` + copies fonts/logo from `gui/mockup` |
| `theme-preview.png` | Mock of the detailed gamelist view (PIL — ES can't render here) |

Regenerate assets: `python3 _make_assets.py`. Refresh the preview:
`python3 pc-image/render_theme_preview.py`. XML well-formedness + structure are
checked in `agent/tests/test_theme.py`.

**Visual tuning on the real EmulationStation is [needs device]** — element/attribute
names follow classic ES v7; Batocera's fork may want minor tweaks, and per-system
logos fall back to Batocera's built-ins (we don't ship box art). The Windows-style
tiled desktop (`gui/mockup/desktop.html`) is the richer target; this theme brings
the same palette/brand/typography to ES today.
