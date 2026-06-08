# 28 — Settings & the canonical UI-prefs store

Status: adopted (2026-06-07, settings overhaul task 14). Enforcement:
`gui/mockup/gose-settings.html` (the page), `gui/mockup/assets/a11y.js` (the
applier + sync), `pc-image/gose-vm-host/gose_vm_server.py` (`/ui/prefs`,
`/privacy`, `/sys/ssh`, `/sys/display`, `/sys/vsync`, `/sys/timezone`).
Companions: docs/24 (privacy), docs/25 (OOBE), docs/27 (input law).

## 1. The canonical store — `/ui/prefs`

Personalization (theme, accent, wallpaper, clock, sign-in mode, sound levels,
accessibility, …) lives in ONE server-side dict:
`/userdata/system/gose/ui_prefs.json`, served as `GET/POST /ui/prefs`.
localStorage is only the per-page **cache**.

- **Every page reads it the same way:** `assets/a11y.js` (already included on
  every live page) GETs `/ui/prefs` on load, mirrors the values into
  localStorage, and applies theme + accent + a11y live. A page never needs its
  own prefs plumbing.
- **Every writer writes through `GOSE.prefs.set({...})`** (exposed by a11y.js):
  localStorage immediately (instant apply), POST `/ui/prefs` async (canonical).
  A localStorage-only write WILL be overwritten by the next server sync — that
  is the design, not a bug. (Recent local writes get a 10 s grace window so the
  in-flight POST can land.)
- **Keys are whitelisted server-side** (`_PREF_KEY_RE`): exactly the
  localStorage names Settings owns (`gose-theme`, `gose-accent`, `gose-wp`,
  `gose-snd-*`, the `gose-a11y` set, …). Adding a setting = add the key there.
- **OOBE seeds it:** `/oobe/complete` writes the wizard's theme + "your color"
  accent (+ timezone) into the store, so the accent picked at first boot shows
  OS-wide — including the lock screen — and survives kiosk reloads. The lock
  screen's owner-accent read is a fallback for pre-store installs only.
- **Accent mechanics:** a valid `#rrggbb` in `gose-accent` overrides the theme's
  `--accent` token (plus a matching `--focusglow`) on `<html>`; empty = the
  theme's own color. One token, every page.

## 2. Settings-page law (extends docs/27)

- L1/R1 (`[`/`]`) own section switching. **d-pad Left never jumps to the rail**
  — inside the pane, Left/Right change the focused value (cycle rows, sliders);
  Left on anything else is a no-op (left edge). B/Esc backs out one level:
  pane → rail → desktop.
- **Every row answers A/Enter**: cycles step, sliders nudge, links/agents open
  their page, actions act (destructive ones arm a press-twice confirm), info
  rows read their value back, and **disabled rows explain why**.
- **No dead toggles.** A control is (a) wired to its real backend, or (b)
  rendered as `dis` — dimmed, `[needs hardware]`/`[needs build]` tag, with the
  honest reason on A. A toggle that silently does nothing is a bug.

## 3. Real backends added for the page

| Endpoint | Effect |
|---|---|
| `GET/POST /ui/prefs` | the canonical store (above); `{reset:true}` clears it |
| `GET/POST /privacy` | `boxart_scrape` → the real scrape flag; `screen_capture: never` blocks `/capture/*` + replay buffer; `diagnostics` recorded (nothing is sent) |
| `GET/POST /sys/ssh` | live sshd/dropbear state; toggle = init script now + `system.ssh.enabled` for next boot |
| `GET/POST /sys/display` | real panel modes via xrandr on `:0`; POST switches |
| `GET/POST /sys/vsync` | `global.retroarch.video_vsync` in batocera.conf |
| `GET/POST /sys/timezone` | `system.timezone` in batocera.conf (page clocks apply `gose-tz` live) |
| `/ai/join` | refused while Settings > AI & Remote > Remote agent control = Disabled (`gose-ai-remote: off`) |
| `/net.json` | now reports `hostname` |

## 4. Emulator default-core picker + integrator rows (tasks 55 + 77, 2026-06-08)

The **Emulators** category lists, one row per system, the default emulator core
each system runs — built live from `GET /emulators` (the same license-aware
catalog the Store's Emulators tab uses, docs/19). A row appears **only for
systems with more than one distinct core**: es_systems lists the same core under
several emulator labels, so those duplicates are deduped by core name (two
identical `mame` entries is never mistaken for a choice). 79 systems qualify on
the current image.

- **A opens a pad-drivable picker** — a modal under the docs/27 §3.10
  layered-Escape contract (it `GOSE.modalPush()`es / sets `data-gose-modal`, so
  the shell forwards B into the page, one layer per press). Each core shows its
  license + verdict badge exactly like the Store: **Commercial-OK** / **Review** /
  **Personal-use** (non-commercial cores are fenced — runnable, but not in a paid
  build, docs/19), plus `current` and `not installed` markers. ↑↓ choose, A sets,
  B/Esc backs out.
- **Pick → `POST /emulators/default {system, core}`** — the existing core-swap
  route (no server change). The row then reflects the new default; a personal-use
  core tags the row `· personal-use`. Uninstalled cores aren't selectable here —
  they're added in the Store first (the route requires the `.so` present).
- **Verified end-to-end** by a virtual-pad drive (docs/27 §7): snes bsnes→snes9x
  wrote `snes.core=snes9x` to batocera.conf, confirmed via `GET /emulators`, then
  restored to bsnes — no other system changed.

**Integrator rows** (deferred bits other surfaces parked in their own docs):

- docs/27 §7.1 — the **Controller test & latency** link in the `controllers`
  category → `gose-padtest.html` (the page returns to
  `gose-settings.html#controllers` on B). Other surfaces (help/tips, sound/music)
  had **no docs/27 §7.x settings-row spec landed** at integration time, so no rows
  were added for them — they wire their own when a spec lands.
