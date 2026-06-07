# GOSE layer

Files copied onto the Batocera x86_64 **userdata** partition to turn it into
GOSE-PC. The base OS stays stock; everything GOSE lives here so it's reproducible
and distro-agnostic (the same layer concept maps onto ROCKNIX on the device).

| Path in repo | Copied to (in image) | Purpose |
|--------------|----------------------|---------|
| `system/custom.sh` | `/userdata/system/custom.sh` | Autostart the GOSE agent (TCP 5555) at boot |
| `system/batocera.conf.gose` | merged into `/userdata/system/batocera.conf` | Hostname, theme, splash, SSH, input defaults |
| `system/configs/emulationstation/es_input.cfg` | `/userdata/system/configs/emulationstation/es_input.cfg` | Seed launcher pad config (known-good DualSense entry); further pads auto-register at `input.pt_open` |
| `splash/gose-splash.png` | `/userdata/splash/` | Boot splash (the GOSE crystal brand screen, matches `gose-boot.html`) |
| `system-splash/boot-logo*.png` | `/usr/share/batocera/splash/` (rootfs — needs overlay/squashfs bake, see `system-splash/README.md`) | Replaces Batocera's early S03 splash with the crystal |

**Splash rule:** keep exactly ONE image in `splash/`. Batocera's S28 splash service
picks a *random* file from `/userdata/splash/` — a second image (the old
`gose-logo.png` that used to live here) is what caused the stale-logo flash during
boot (fixed 2026-06-07). Regenerate all splash PNGs from the current brand mark
with `py -3.11 _make_splash.py` (source: `gui/mockup/assets/brand/gose-crystal.png`).
| `themes/gose/` *(next)* | `/userdata/themes/gose/` | Windows-like EmulationStation theme |
| *(repo)* `agent/` | `/userdata/system/gose/agent/` | The GOSE agent, copied by the build |

The EmulationStation **theme** (`themes/gose/`) is the next GUI milestone — it ports
the prototypes in `gui/mockup/` to batocera-emulationstation theme format v7. Until
then the layer runs the agent + applies config/splash on stock ES.
