# 32 — The clean build bakes the GOSE shell (Task #90)

**Status:** shipped to `build-gose-pc.sh` 2026-06-08. A conclusive end-to-end test
still needs a real `sudo ./build-gose-pc.sh` on a Linux host (see "Untested seam").

## The problem (the keystone ship-blocker)

GOSE had two disconnected ship paths. `build-gose-pc.sh` (the "clean build") rsynced
only `gose-layer/system/` + the repo `agent/`. It baked **none of the GOSE shell** —
not the UI server (`gose_vm_server.py`), not the ~39 `gui/mockup/*.html` kiosk pages +
assets, not `kiosk.py` / `gose-session.sh` / the helper daemons. Those lived ONLY in
the running guest's `/userdata/gose-ui`, pushed live from the host by ad-hoc scripts.

So a fresh clean build booted hardened Batocera with **no GOSE UI**, which is why
`dist/package-bundle.ps1` shipped the hand-built **dev disk** instead (and with it: SSH
on + root pw "linux", `.oobe-done` + the dev account, the owner token — i.e. #91/#92/#4).

## The fix — build-time COPY of the canonical sources (no duplication)

`build-gose-pc.sh` now bakes the shell at build time from the files that are already the
source of truth, so nothing is duplicated into `gose-layer/` and nothing can go stale:

| Source in repo | Baked to (in image) |
|---|---|
| `gui/mockup/*.html` + `gui/mockup/assets/**` + `_render_common.py`/`render_*.py` | `/userdata/gose-ui/` |
| `pc-image/gose-vm-host/` → `gose_vm_server.py`, `kiosk.py`, `gose-session.sh`, `gose-pad-nav.py`, `overlay_window.py`, `watchdog.py`, `gose-storage-handler.sh`, `guide_toggle.sh`, `shot.sh`, `start-shell.sh`, `99-gose-storage.rules`, `gamecontrollerdb.txt` | `/userdata/gose-ui/` |
| `pc-image/gose-vm-host/vendor/` (vendored python-xlib + six) | `/userdata/gose-ui/vendor/` |
| `pc-image/gose-layer/boot/boot-custom.sh` | `/boot-custom.sh` (FAT boot partition) |

Excluded on purpose: host-only dev tooling under `gose-vm-host` (`reload_ui.py`,
`push_*.py`, `boot-gose-vm.ps1`, `host_bridge.py`, `inject_gose_layer.py`,
`swap_shell.py`, `serve_and_kiosk.py`, `elev*`), design `*-concept.png`, `__pycache__`,
`*.bak`, and per-install runtime state (`ai_*.json`, `favorites.json`, `recent.json`,
`playtime.json`, `*.log`) — a fresh image must start clean. `vkbd.py` is a live-only
orphan (referenced nowhere in the repo or the running server/pages) and is not baked.

**Completeness was proven** by diffing the live guest `/userdata/gose-ui` inventory (243
product files, state/logs/backups filtered out) against the repo source set: the only
file the sources don't carry is a non-load-bearing `README.md`, and the repo is actually
*ahead* of the live VM (it adds `gose-diagnostics.html`). So the bake is the complete
current shell.

## How the shell AUTOSTARTS on a fresh image

This is the part the pre-mortem mis-described as a `custom_service`. It is not — the
`/userdata/system/services/custom_service` on the dev disk only starts the **agent**
(it is the auto-migrated copy of the old `custom.sh`; Batocera v43 moves
`custom.sh` → `services/custom_service` on first boot). The **shell** starts a different
way:

- Batocera launches the front-end via `/usr/bin/emulationstation-standalone`. GOSE
  swaps that script's ES launch line:
  `dbus-run-session -- emulationstation <opts>` → `dbus-run-session -- sh /userdata/gose-ui/gose-session.sh`.
- On the dev disk that edit was persisted in the Batocera **overlay**
  (`/boot/boot/overlay`). A clean build has no overlay, so the edit is gone.
- Fix: `boot-custom.sh` re-applies the patch on **every** boot. Batocera's
  `S00bootcustom` runs `/boot/boot-custom.sh` **before** `S31emulationstation`, so the
  patch is in place for that boot's ES. It is idempotent and self-heals after an OS
  update (which restores the stock squashfs). `gose-session.sh` then starts
  `gose_vm_server.py` and `exec`s `kiosk.py`.

The agent autostart is unchanged: the existing `gose-layer/system/custom.sh` rsync +
Batocera's first-boot auto-migration brings up `gose_agent` on 8731.

## Robustness fixes folded in

- **CRLF normalization.** The shell scripts are committed with CRLF (Windows authoring);
  `/bin/sh` chokes on the trailing `\r`. The build now strips CR from every baked `*.sh`
  (`gose-session.sh`, `custom.sh`, `provision-baked-apps.sh`, `harden-firstboot.sh`,
  `boot-custom.sh`, …). Without this the agent autostart + first-boot hardener would
  silently fail on a real Linux build.
- **`/userdata/system/logs`** is pre-created — `gose-session.sh` logs pad-nav/overlay
  there at S31, before `custom.sh` creates it at S99.

## Why this unblocks #1 / #91 / #4

Once the clean build carries the shell, `package-bundle.ps1` can point `-ImageGz` at the
clean build output instead of the dev disk — so the shipped image inherits the hardened
`batocera.conf.gose` (SSH off, Samba off, security mode), no `.oobe-done`/dev account,
and no baked owner token, automatically.

## Untested seam (the follow-up)

Verified here: (a) the copy list covers the complete live inventory (diff above);
(b) `bash -n` + a full `--dry-run` of `build-gose-pc.sh`; (c) `boot-custom.sh` reproduces
the exact working dev-disk ES launch line. **Not** verified: a real
`sudo ./build-gose-pc.sh` on a Linux host (no Linux build host available here) — that is
the conclusive test, plus a boot of the resulting image to confirm the GOSE shell comes
up cold. Also a parallel follow-up: `inject_gose_layer.py` (the host-side live-push) has
the same historical gap and should be reconciled to push this same file set.
