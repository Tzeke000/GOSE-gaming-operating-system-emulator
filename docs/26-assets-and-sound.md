# 26 — Brand Assets & Sound

The owner's hand-made art + sound set, integrated into the OS, plus the sound manager
that plays it. Source art lives in `OneDrive/Documents/stuff for GESO/`
(brand mark, system icons, sounds). Processing script:
`<agent-home>/scratch/key_icons.py`.

## Icon set

12 cohesive neon-hexagon system icons + the faceted **crystal** brand mark. Each
source PNG was a flat-background neon render (no alpha); the background was cut to
transparent with a **border flood-fill** (so interior darks/brights are kept),
**edge despill** (transparent pixels take the nearest opaque RGB → no halo when
scaled on the dark UI) and a 1px feather, then trimmed and resized onto a square
canvas (icons 256², crystal 512²).

Files (`gui/mockup/assets/icons/brand/`) — named by GOSE's real category:

| Source folder | file | used for |
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
| system | `system.png` | System (live monitor widget) |
| termanal | `terminal.png` | Terminal |

Brand mark: `gui/mockup/assets/brand/gose-crystal.png` (from the **black-bg** source).

### How they're wired

Most GOSE icons are monochrome SVG **masks** tinted with `currentColor`
(`assets/icons/<name>.svg`). A coloured PNG can't be a mask (it would flatten to
the accent colour), so `assets/icons.js` (`GICON`) renders the brand PNGs as a
contained **background-image** instead. Brand tokens are namespaced so they never
collide with the lucide names (`settings`/`terminal` would, hence `settings-app`
/`terminal-app`; `system` has no lucide collision so it stays plain `system`).
`GICON.paint(root)` handles both; it's routed through
`widget.js` `paintIcons` and each page's painter. **Focus glow (docs/21) is
untouched** — it lives on the item element, not the icon.

Applied at: home side-nav, dock, all widget headers + the widget **catalog**
(so the Widgets manager matches), hub/appsgames pins, apps launcher grid, store
tabs + game tiles, library, settings rail, OOBE. The **System** monitor widget
(`widget.js` CATALOG + `gose-home.html` `GW.define`) carries the `system` brand
icon (was the lucide `cpu` mask). The crystal is the launcher
`.ico` (multi-size 16–256, regenerated from the PNG), the boot splash, the OOBE
header + finale logo, and the home centerpiece/top-bar/page logos.

## Sound

`assets/sound.js` (`GOSESOUND`) owns the system sound set. `GOSESOUND.play(event)`
with **per-category volume + mute**, a **global quiet-mode** (all persisted in
localStorage), and **auto-duck** while a game is foreground via `/game/running`
(the same game-gate `gose-pad-nav.py` uses to fall silent under a game). The
legacy `GOSE.sound()` UI ticks (cursor.js) route through the manager, so
Settings → Sound is the one control surface.

Clips (`gui/mockup/assets/sounds/`, copied from the owner's set, renamed):

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

## Menu music (shell soundtrack)

A quiet ambient loop under the **menu shell** — added 2026-06-08 (task 46). It is a
fifth sound **category** (`music`) registered in `sound.js` (`DEFV.music = 20`,
default ON at ~20%), but the looping playback lives in its own player so `sound.js`
stays the one-shot SFX manager:

- **`assets/music.js`** (`GOSEMUSIC`) — the player. It rides the existing loader:
  `cursor.js` auto-injects `sound.js` on every shell page, and `sound.js` injects
  `music.js`. Reuses the `music` category for **volume + mute** and the global
  **quiet-mode**, and the **same `/game/running` gate** the SFX duck + `gose-pad-nav`
  use — so Settings → Sound is still the single control surface and a launched game
  pauses the music (polled at 700 ms = near-instant; resumes on return). Verified live
  on the VM: home plays, `pong1k2p` launch paused it, kill-by-PID resumed it.
- **Engine = an `<audio>` element** (verified). NOT Web Audio — this WebKit2GTK build's
  `AudioContext.decodeAudioData` **hangs** (no callback). The `<audio>` media pipeline
  also can't load a *large* file over the shell's range-less HTTP server: a 3 MB src
  errors `MEDIA_ERR_SRC_NOT_SUPPORTED` while small clips load fine — so the track must
  stay small (the placeholder is ~1.1 MB and loads cleanly). MP3 has **no decoder** here.
- **Shell-only scope.** Plays on home/library/store/apps/settings/files/gallery/
  task-manager/etc.; a deny-list keeps it silent on boot, OOBE, BIOS setup, the lock
  screen, the in-game overlay, and login.
- **No nav restart-stutter.** Each page nav is a full reload, so the position + a
  wall-clock timestamp are persisted to `localStorage` (`gose-music-pos`) on
  `pagehide` and every 1.5 s; the next page resumes at *position + elapsed* (mod loop
  length). home→store→home never restarts the track from zero.
- **Autoplay is GATED here** (verified): the kiosk blocks audio autostart —
  `<audio>.play()` rejects `NotAllowedError` until a user gesture. The shell is
  controller/key-driven, so the **first** d-pad/key/pointer input starts the music
  (the rejection arms a one-shot gesture listener). For true play-on-boot, `kiosk.py`
  would set `WebKitSettings media-playback-requires-user-gesture = FALSE` (one line in
  the shell launcher — out of scope for this asset track; flagged as the follow-up).
- **OFF path.** Quiet-mode, category mute, or category volume **0** all silence it
  (each makes `wantOn()` false → the same pause path the game-gate uses; verified by
  loading home under each preset). Track is `localStorage gose-music-src` if set.

**Placeholder track** — `assets/sounds/menu-music-placeholder.wav` (36 s, 16 kHz/16-bit
**mono**, ~1.1 MB — small on purpose, see the range-less-server note above).
**100% synthesized** (pure sine/pad stdlib synthesis — no licensing): a seamless
**C–G–C** ambient motif (low-C drone with integer-cycle period-fit + slow detune
tremolo, three 12 s pad bars Cmaj→Gmaj→Cmaj windowed to zero at the bar edges so the
loop point is click-free, plus sparse decaying bells). Onyx/warm GOSE identity. The
owner's real track replaces it — see `docs/asset-prompts/06-menu-music.txt`.
Generator: `<agent-home>/scratch/make_menu_music.py`.

### Settings row — NOT auto-covered (spec to add)

The Sound tab's rows are a **hand-enumerated** array and its init reads a **hardcoded**
category list (`["system","notify","battery","ui"]`), so a new category is NOT picked up
automatically — but the generic `snd_<cat>` apply handler already supports any category.
The OFF path works today via Quiet mode (and any agent/`localStorage` write); to expose a
**Menu music** row, `gose-settings.html` (owned by another track — left untouched here)
should add to the `sound` tab `rows`:

```js
{ic:"music", nm:"Menu music", sub:"Looping ambient soundtrack under the menus (pauses in-game)",
 t:"cycle", v:["Mute","25%","50%","75%","100%"], apply:"snd_music"},
```

and add `"music"` to the init list that calls `volIdxLS(...)` (the `["system","notify",
"battery","ui"]` array). No handler change is needed — `snd_music` flows through the
existing `apply.indexOf("snd_")===0` branch. (Lucide `music` icon already exists.)

## Corrections / pending

- **Gallery + System icons (2026-06-07)** — the first integration ran before the owner
  fixed a source swap: `pictures/` then held the system art and `system/` was
  empty, so Gallery wore the wrong icon and System had none. Re-cut from the
  corrected source: `gallery.png` now carries the real pictures/gallery art, and
  the 12th icon `system.png` is cut + wired (`system` brand token) onto the System
  monitor widget. Both verified on the VM via pad-drive.
- **3D logo** — `3D/GOSE icon/GOSE+3D+model.zip` was left for later (no turntable
  boot/loading animation yet); the static crystal is the boot/OOBE logo for now.
- **wake** clip is present but a reliable web "resume" trigger isn't wired (the
  kiosk can't see ACPI resume); fires only where resume is detectable.
