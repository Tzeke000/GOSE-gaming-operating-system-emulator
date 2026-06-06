# 26 — Brand Assets & Sound

Zeke's hand-made art + sound set, integrated into the OS, plus the sound manager
that plays it. Source art lives in `OneDrive/Documents/stuff for GESO/`
(brand mark, system icons, sounds). Processing script:
`D:/Wren/scratch/key_icons.py`.

## Icon set

11 cohesive neon-hexagon system icons + the faceted **crystal** brand mark. Each
source PNG was a flat-background neon render (no alpha); the background was cut to
transparent with a **border flood-fill** (so interior darks/brights are kept),
**edge despill** (transparent pixels take the nearest opaque RGB → no halo when
scaled on the dark UI) and a 1px feather, then trimmed and resized onto a square
canvas (icons 256², crystal 512²).

Files (`gui/mockup/assets/icons/brand/`) — named by GOSE's real category:

| Zeke's folder | file | used for |
|---|---|---|
| AI players | `ai.png` | AI Players |
| apps | `apps.png` | Apps launcher / Apps & Games |
| emulator | `emulators.png` | Emulators |
| folder | `files.png` | Files |
| Games | `games.png` | Games |
| librery | `library.png` | Library |
| notifications | `notifications.png` | Notifications |
| pictures | `gallery.png` | Gallery |
| settings | `settings.png` | Settings |
| store | `store.png` | Store |
| termanal | `terminal.png` | Terminal |

Brand mark: `gui/mockup/assets/brand/gose-crystal.png` (from the **black-bg** source).

### How they're wired

Most GOSE icons are monochrome SVG **masks** tinted with `currentColor`
(`assets/icons/<name>.svg`). A coloured PNG can't be a mask (it would flatten to
the accent colour), so `assets/icons.js` (`GICON`) renders the brand PNGs as a
contained **background-image** instead. Brand tokens are namespaced so they never
collide with the lucide names (`settings`/`terminal` would, hence `settings-app`
/`terminal-app`). `GICON.paint(root)` handles both; it's routed through
`widget.js` `paintIcons` and each page's painter. **Focus glow (docs/21) is
untouched** — it lives on the item element, not the icon.

Applied at: home side-nav, dock, all widget headers + the widget **catalog**
(so the Widgets manager matches), hub/appsgames pins, apps launcher grid, store
tabs + game tiles, library, settings rail, OOBE. The crystal is the launcher
`.ico` (multi-size 16–256, regenerated from the PNG), the boot splash, the OOBE
header + finale logo, and the home centerpiece/top-bar/page logos.

## Sound

`assets/sound.js` (`GOSESOUND`) owns the system sound set. `GOSESOUND.play(event)`
with **per-category volume + mute**, a **global quiet-mode** (all persisted in
localStorage), and **auto-duck** while a game is foreground via `/game/running`
(the same game-gate `gose-pad-nav.py` uses to fall silent under a game). The
legacy `GOSE.sound()` UI ticks (cursor.js) route through the manager, so
Settings → Sound is the one control surface.

Clips (`gui/mockup/assets/sounds/`, copied from Zeke's set, renamed):

| event | file | category | fires on |
|---|---|---|---|
| boot | `boot.mp3` | system | boot splash / kiosk start |
| login | `login.mp3` | system | OOBE account step complete |
| shutdown | `shutdown.mp3` | system | power → shut down |
| restart | `restart.mp3` | system | power → restart |
| sleep | `sleep.mp3` | system | power → suspend |
| wake | `wake.mp3` | system | resume (clip ready; web-resume hook is best-effort) |
| notify | `notify.mp3` | notify | generic `GOSE.notify` |
| download-done | `download-done.mp3` | notify | install/download finished (icon `download`) |
| error | `error.mp3` | notify | failed action / OOBE validation |
| warning | `warning.mp3` | notify | warnings (icon `triangle-alert`) |
| charging | `charging.mp3` | battery | AC plugged in (false→true edge) |
| battery-low | `battery-low.mp3` | battery | ≤20% discharging |
| battery-critical | `battery-critical.mp3` | battery | ≤10% discharging |

Category defaults: system 75, notify 75, battery 100, ui 50. Important alerts
(`battery-low/critical`, `error`, `warning`) bypass the game-duck. UI ticks
(`nav/select/back/launch`) stay the existing `.wav` set under the `ui` category.

OOBE `step-done` (per-step advance) and `welcome` (setup-complete finale) reuse
the `login` and `boot` clips respectively — no dedicated OOBE clips exist yet.

### Settings surface

Settings → Sound adds: **Quiet mode**, and per-category volume/mute pickers for
**System / Notification / Battery / UI** sounds (Mute · 25/50/75/100%), persisted,
each with a preview on change. The old single "UI sounds" on/off is folded into
the UI picker (`gose-sounds` kept in sync for back-compat).

## Pending / skipped

- **12th icon (`system`)** — Zeke's `system` source folder is empty, so that one
  app keeps its current lucide icon. Slot the brand icon in when the art exists.
- **3D logo** — `3D/GOSE icon/GOSE+3D+model.zip` was left for later (no turntable
  boot/loading animation yet); the static crystal is the boot/OOBE logo for now.
- **wake** clip is present but a reliable web "resume" trigger isn't wired (the
  kiosk can't see ACPI resume); fires only where resume is detectable.
