# GOSE layer

Files copied onto the Batocera x86_64 **userdata** partition to turn it into
GOSE-PC. The base OS stays stock; everything GOSE lives here so it's reproducible
and distro-agnostic (the same layer concept maps onto ROCKNIX on the device).

| Path in repo | Copied to (in image) | Purpose |
|--------------|----------------------|---------|
| `system/custom.sh` | `/userdata/system/custom.sh` | Autostart the GOSE agent (TCP **8731**) at boot |
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
| *(repo)* `gui/mockup/` + `gose-vm-host/` shell files | `/userdata/gose-ui/` | **The GOSE shell** — UI server, kiosk pages + assets, helper daemons, vendored xlib. Build-time COPY (not duplicated here) so it can't drift. See docs/32 + Task #90. |
| `boot/boot-custom.sh` | `/boot-custom.sh` (FAT boot partition) | Pre-ES hook (`S00bootcustom`, before `S31emulationstation`): re-applies the `emulationstation-standalone` → `gose-session.sh` patch each boot so the shell autostarts on a clean image. |

**Shell autostart (docs/32):** `boot-custom.sh` redirects the front-end to
`/userdata/gose-ui/gose-session.sh`, which starts `gose_vm_server.py` and the kiosk. The
`/userdata/system/services/custom_service` Batocera auto-creates from `custom.sh` only
starts the **agent**, not the shell — don't confuse the two.

The EmulationStation **theme** (`themes/gose/`) is the next GUI milestone — it ports
the prototypes in `gui/mockup/` to batocera-emulationstation theme format v7. Until
then the layer runs the agent + applies config/splash on stock ES.
