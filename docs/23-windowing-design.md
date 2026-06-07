# 23 — Windowing & Multitasking Design `[CUSTOM]`

> Status: **DESIGN / RESEARCH (2026-06-06).** The owner's ask: "make windowing/multitasking
> like what Windows has, or better — like whatever OS does this best." This doc is the research
> synthesis + the chosen architecture + a phased build plan. **It is the owner's to approve before
> any build starts.** This is roadmap item **B** (docs/17 §B) — "the biggest architectural lift."
> Nothing here is built yet.

---

## 0. TL;DR (the recommendation)

- **Architecture: HYBRID, and the key insight is we already have half of it.** GOSE already runs a
  real lightweight X window manager — **Openbox** — underneath the kiosk (`gose-session.sh` writes
  Openbox keybinds + `openbox --reconfigure`). Native apps (Steam, emulators) are already real X
  windows. So we do **not** rebuild a WM from scratch. We add a **web-layer window manager** for the
  HTML "apps" inside the single WebKit kiosk, and **drive the existing Openbox/native windows** via
  `wmctrl`/`xdotool` (the server already does `xdotool ... windowactivate`). One **unified taskbar +
  one controller window-switcher** sits over both worlds.
- **Why not pure-X-WM (promote every HTML page to its own X window):** would spawn one WebKit process
  per "window" — RAM-murder on a handheld — and throw away the shared `GW` widget base + shell state.
- **Why not pure web-layer:** the web layer literally cannot manage Steam/emulator native X windows.
- **Controller-first headline ("or better"):** **hold Guide → a window carousel** (the controller
  Alt-Tab), **L2 + d-pad → Snap Layouts grid** (controller Win+Z) with **Snap-Assist fill**, a
  **bottom dock** of running windows as a nav zone, and a **Stage-Manager staged strip**. Every
  window op is a gamepad op — the thing desktop WMs get wrong (see the Xbox-handheld cautionary
  tale, §1.6).
- **Widget↔window-memory model:** a widget *is* a small live iframe; **maximize re-parents the same
  iframe into a window** (no reload); **"act out" tears the iframe down** (frees memory) and keeps a
  lightweight **descriptor** so it can be re-summoned. Phase 1 frees JS-heap (GC); phase 2 promotes
  heavy apps to their own process for **true OS-level RAM release** (SIGSTOP = suspend, kill = free).
- **Phase 1** (~3–5 days): web windows for the HTML apps via a WinBox-style layer, dock + carousel +
  controller snap, act-out→GC. **Phase 2** (~4–7 days): native apps in the same dock/carousel + real
  RAM release. **Phase 3** (~3–5 days): Snap Groups, overview, memory-pressure auto-suspend.

---

## 1. Research — how the best OSes do it, and what's genuinely best about each

### 1.1 Windows 11 — *structured layouts + assisted fill + persistent groups*
- **Snap Layouts:** `Win+Z` (or hover the maximize button) pops a grid of preset zone layouts
  (halves, quarters, 3-column); you pick the slot the active window takes.
- **Snap Assist:** the moment you snap the first window, thumbnails of your other open windows appear
  to **fill the remaining slots, one at a time** — you don't hunt-and-drag.
- **Snap Groups:** an arrangement you snapped is *remembered as a group*; hover its taskbar icon to
  restore the whole group at once. Groups also surface in Task View and Alt-Tab.
- **Best to steal:** the **3-step flow** — pick a layout → snap one window → *assist fills the rest*.
  It's the most "no fiddling" snap model shipping, and it maps cleanly to a d-pad (pick zone, pick
  window). Plus **persistent groups** as a first-class, restorable thing.
- Sources: [MS Learn — Snap Layouts/Groups](https://learn.microsoft.com/en-us/answers/questions/2337358/how-to-use-snap-layouts-and-snap-groups-in-windows),
  [MS Support — Snap your windows](https://support.microsoft.com/en-us/windows/snap-your-windows-885a9b1e-a983-a3b1-16cd-c531795e6241),
  [Windows Central — Snap Assist](https://www.windowscentral.com/how-use-snap-assist-windows-11).

### 1.2 macOS — *one focus + the rest staged; spread-to-find*
- **Stage Manager:** the window you're using is front-and-center; your other windows/groups wait as
  thumbnails on the side, one click away. You can stage a *suite* of windows as a single group.
- **Mission Control:** spreads **all** open windows into one flat layer to find a buried one; Spaces
  and Split-View groups sit in a strip across the top.
- **Spaces + Full-screen:** full desktops you switch between; full-screen view focuses one app.
- **Best to steal:** **Stage Manager's model is the most handheld-shaped idea in the desktop world** —
  one big focused window + a thin strip of the rest you cycle through. That's exactly right for a
  small screen + a controller (no precision dragging needed). Mission Control's "show me everything"
  is the right **overview** gesture.
- Sources: [Apple — Stage Manager](https://support.apple.com/guide/mac-help/use-stage-manager-mchl534ba392/mac),
  [Apple — Manage windows on your Mac](https://support.apple.com/en-in/guide/mac-pro/apd2345fc25d/mac).

### 1.3 SteamOS / Steam Deck — *dual-mode: controller shell by default, real desktop one button away*
- **Gaming Mode** = the Steam client (Big Picture UI) rendered inside **gamescope**, a gaming
  microcompositor that owns scaling/upscaling/HDR/VRR/framecap and boots as the default session.
- **Desktop Mode** = full **KDE Plasma**; you switch via the **Steam/Guide button → Power → Switch to
  Desktop**.
- **gamescope** can run nested or embedded, manages **both X11 (via XWayland) and Wayland** clients,
  and can host **multiple XWayland servers** in one compositor — i.e. it is itself a window manager
  tuned for games.
- **Best to steal:** the **dual-mode split** (a clean controller-first shell as the default, a real
  windowed environment a single system-button away) and the **Guide-button-as-system-key** convention.
  gamescope is the gold-standard handheld compositor if/when GOSE goes Wayland (today GOSE is X11 +
  Openbox; gamescope is the natural phase-3/Odin-2 upgrade for scaling/HDR).
- Sources: [SteamOS — Wikipedia](https://en.wikipedia.org/wiki/SteamOS),
  [ValveSoftware/gamescope](https://github.com/ValveSoftware/gamescope),
  [Gamescope — ArchWiki](https://wiki.archlinux.org/title/Gamescope),
  [steam-using-gamescope guide](https://github.com/shahnawazshahin/steam-using-gamescope-guide).

### 1.4 ChromeOS — *overview + simple snap + virtual desks, gesture/key driven*
- **Overview** key spreads all windows; **snap** to halves (`Alt+[` / `Alt+]`) or **Partial** (a
  big + a small) split; **Virtual Desks** (up to 8, `Search+[` / `Search+]`); split pairs move
  together between desks/overview.
- **Best to steal:** **everything has a keystroke** (snap, overview, desk-switch all bind to keys —
  not just mouse) and **Partial split** (asymmetric, not just 50/50) — both directly controller-mappable.
- Sources: [Chromebook Help — multitask](https://support.google.com/chromebook/answer/177891?hl=en),
  [ChromeUnboxed — overview/split](https://chromeunboxed.com/chrome-os-83-clamshell-overview-split-screen-dragging/),
  [AndroidPolice — virtual desks](https://www.androidpolice.com/how-to-create-manage-multiple-desktops-chromeos/).

### 1.5 GNOME / KDE / Openbox — *the lightweight-WM + scripting reality*
- KDE Plasma is the heavy, fully-featured desktop SteamOS falls back to; GNOME has Activities-overview
  + edge-snap. For GOSE the relevant tier is the **lightweight, scriptable** WM:
- **Openbox (what GOSE already runs):** a stacking X WM driven by `rc.xml` keybinds; windows are fully
  scriptable from outside via **`wmctrl`** (EWMH: list/switch/maximize/move windows, switch desktops)
  and **`xdotool`** (XTEST: simulate input, move/resize/activate/minimize windows). Pseudo-tiling on
  Openbox is conventionally done with exactly these two tools in shell scripts.
- **labwc** (Wayland successor to Openbox; reuses Openbox's config grammar, supports edge-snap/tiling,
  `foreign-toplevel` so `wlrctl` can list+switch windows) is the natural Wayland counterpart if GOSE
  migrates off X11.
- **Best to steal:** we don't need to *write* a WM — Openbox + `wmctrl`/`xdotool` already give
  list/switch/move/resize/maximize/minimize as **shell commands GOSE can call from the server**. The
  WM "engine" for native windows is sitting there unused.
- Sources: [Openbox-session help](https://openbox.org/help/Openbox-session),
  [Openbox pseudo-tiling with wmctrl/xdotool](https://forums.bunsenlabs.org/viewtopic.php?id=4042),
  [labwc](https://github.com/labwc/labwc), [labwc — ArchWiki](https://wiki.archlinux.org/title/Labwc).

### 1.6 Controller-first / handheld — *the part everyone except Valve gets wrong*
- **The cautionary tale:** Microsoft's "Xbox Full-Screen Experience" (2026) gives Windows handhelds a
  controller-first tile dashboard, but **standard Windows dialogs (UAC, file-explorer errors) still
  aren't controller-navigable** — the windowing underneath was built mouse-first and can't be fully
  driven by a pad. SteamOS "remains the gold standard" *because Valve built the shell controller-first
  from the metal up*. **Lesson for GOSE: a window op that can't be done on the pad does not exist.**
- **Nintendo Switch — the deliberate counter-design:** the Switch (and Switch 2) **refuses
  Windows-style windowing on purpose** — single-app **suspend/resume** only, because keeping multiple
  apps resident costs RAM and battery the handheld can't spare (Switch 2 reserves ~2–2.4 GB for system
  tasks incl. quick-resume). This is the honest frame for GOSE's memory model: **on a handheld, the
  correct default is "one focused thing + suspend the rest," not "keep everything live."** GOSE's
  widget↔window-memory model is exactly this instinct, made *optional* rather than forced.
- **Best synthesis for GOSE (controller-first AND real multitasking):**
  > **Stage Manager's one-focus-plus-staged-strip** (handheld-shaped) +
  > **Windows' Snap Layouts/Assist/Groups** (structured, assisted, persistent) +
  > **SteamOS's Guide-button-as-system-key + dual-mode discipline** (controller default, real windows
  > on demand) +
  > **Switch's suspend-the-rest memory honesty** (free RAM, don't hoard it) —
  > all reachable with **zero precision pointing**, because every op is a discrete d-pad/button choice,
  > not a drag.
- Sources: [XDA — why Steam Deck won the handheld OS problem](https://www.xda-developers.com/gaming-handhelds-solved-hardware-problem-underestimated-windows-problem/),
  [NoobFeed — Xbox FSE vs SteamOS](https://www.noobfeed.com/articles/xbox-full-screen-experience-steamos-handheld),
  [NintendoReporters — Switch 2 memory allocation](https://www.nintendoreporters.com/en/news/nintendo-switch-2/nintendo-switch-2-memory-allocation-a-developer-friendly-breakdown/).

---

## 2. GOSE's reality (verified against the code, not memory)

What actually exists today (so the design builds on it, doesn't ignore it):

| Piece | File | What it gives us |
|---|---|---|
| **Single WebKit kiosk** | `pc-image/gose-vm-host/kiosk.py` | GTK3 + WebKit2 4.1, fullscreen, undecorated, GPU-accel. One WebView. **No `keep_above`** — so launched native apps appear over it; app exit reveals it. |
| **Openbox X WM (already running!)** | `gose-session.sh` (writes `/etc/openbox/rc.xml` keybinds, `openbox --reconfigure`) | A real stacking WM under the kiosk. Native apps are real X windows it manages. |
| **Native app launch** | `gose_vm_server.py` (`/launch`): `flatpak run com.valvesoftware.Steam`, `emulatorlauncher`, `_RUN_EXT` | Apps spawn as separate X windows; pids tracked. |
| **Native window control (proven)** | `gose_vm_server.py`: `xdotool search --name '^GOSE$' \| ... windowactivate` | We already activate a window by name. `wmctrl`/`xdotool` available. |
| **Always-on-top web panel over games (proven)** | `overlay_window.py` | A **second** GTK+WebKit window, `type_hint=UTILITY`, `keep_above`, screenshot-blur backdrop, toggled by an Openbox **global** keybind. Proves a web surface *can* float over a native app — by being its own X window. |
| **Gamepad→key bridge** | `gose-pad-nav.py` | evdev → `xdotool key`. Buttons→keysyms, sticks/hat→arrows, autorepeat. **Pauses when a game is foreground** (game owns the pad). **Admin-gated** (only P1/dev/admin-AI pad drives menus). Game-Bar exception lets the pad drive an overlay while a game runs. |
| **Widget base `GW`** | `gui/mockup/assets/widget.js`, docs/21 | Declarative widgets with **drag-to-move + persisted position** (`localStorage gose-wpos`), enable/disable, **controller nav** (arrows→move, A→Enter, B→Esc, L1/R1→`[`/`]`), nav zones `[Menu][widgets][Dock]`. **This is already 60% of a web window manager.** |
| **Pages** | `gui/mockup/gose-*.html` served from `127.0.0.1:8780` | Today navigation is **full-page `location.href`** between separate HTML files (one page visible at a time). |
| **Task manager** | `gose-taskman.html` + `/procs.json`, `/proc/kill` | Live `/proc` list + kill (TERM/KILL). Process inventory + termination infra already exists. |
| **Guide overlay keybind** | Openbox `KP_5`/`Home` → `guide_toggle.sh` | A global system-key already wired. The Guide button convention is in place. |

**The single most important constraint** (call it out loud): a web "window" lives **inside** the
kiosk WebView. It therefore **cannot paint above a native X window** (Steam/emulator) — those are
sibling X windows the kiosk sits *behind*. The only web surface that can overlay a native app is a
**separate X window** (exactly what `overlay_window.py` is). This single fact drives the whole hybrid
design and the phase split (see §6, hard part #1).

---

## 3. Chosen architecture — Hybrid (web-layer WM + Openbox/EWMH for native), one unified switcher

```
              ┌──────────────────────────────────────────────────────────────┐
              │  Openbox (X WM, already running)                              │
              │                                                              │
   ┌──────────┴───────────────┐   ┌───────────────┐   ┌───────────────┐    │
   │  GOSE kiosk WebView       │   │ Steam (X win) │   │ emulator(X win)│   ... native X windows
   │  (kiosk.py — ONE process) │   └───────────────┘   └───────────────┘    │  (Openbox-managed)
   │                           │                                            │
   │  WEB-LAYER WM (new):      │   GOSE drives these from the server via    │
   │   • home canvas + widgets │   wmctrl / xdotool:                        │
   │   • web windows (iframes  │     list · activate · move · resize ·      │
   │     in WinBox-style frames)│    maximize · iconify · SIGSTOP · SIGTERM │
   │   • dock + carousel (web)  │                                           │
   └───────────────────────────┘                                            │
              │                                                              │
   ┌──────────┴───────────────┐   The OVERLAY X window (overlay_window.py,  │
   │  GOSE overlay WebView      │   keep_above) is the ONLY web surface that │
   │  (2nd X window, keep_above)│   can paint OVER a native app — so the     │
   │  → hosts the dock+carousel │   unified taskbar/switcher renders HERE    │
   │    WHEN a native app is fg │   when a native app is foreground.         │
   └───────────────────────────┘                                            │
              ▲                                                              │
   ┌──────────┴───────────────┐                                            │
   │  WINDOW REGISTRY (server) │  one merged list: web windows (from the    │
   │  GET /windows             │  WebView) + native windows (EWMH client    │
   │  POST /wm/<verb>          │  list). The dock + carousel read THIS.     │
   └───────────────────────────┘────────────────────────────────────────────┘
```

**The three layers and who owns what:**

1. **Web-layer WM** (inside the one kiosk WebView): manages **web apps** = HTML pages mounted as
   iframes inside draggable/resizable/snappable frames. Cheap — one WebView process, shared `GW` base
   and shell state. This is where the widget↔window continuum lives.
2. **Openbox + `wmctrl`/`xdotool`** (already present): manages **native apps** = Steam, emulators,
   AppImages. GOSE doesn't reimplement this; the server issues the WM commands.
3. **A unifying spine**: a **window registry** (`GET /windows`) that merges both kinds into one list,
   and a **WM command endpoint** (`POST /wm/<verb>`) so the dock + carousel + controller act on either
   kind uniformly. When the foreground is a **native app**, the dock/carousel are rendered by the
   **overlay X window** (reusing `overlay_window.py`), because that's the only web surface that can
   sit above a native window.

**Why this is the right call for a controller-first handheld that runs web + native:**
- Keeps the cheap single-WebView model for the dozens of light HTML apps (Files, Store, Settings,
  Terminal, AI Hub…) — no per-window process explosion. (The Switch-memory lesson.)
- Reuses the real WM (Openbox) that's *already there* for the few heavy native apps that genuinely
  need their own process and real RAM accounting.
- One switcher/dock/snap UX over both, so to the user there's just "windows," not "web vs native."
- Everything is discrete-choice driven → fully controller-navigable (the "or better").

---

## 4. The window model

### 4.1 What a "window" is
A **window** is a registry entry, regardless of kind:

```js
{
  id:    "win-files-1",
  kind:  "web" | "native",
  title: "Files",
  icon:  "folder",
  // web: the page to mount; native: how it was launched + its X handle
  url:   "gose-files.html",          // web
  xid:   "0x0420001", pid: 4821,     // native (EWMH window id + pid)
  geom:  { x, y, w, h },             // last placement (snap zone or free)
  state: "normal" | "max" | "snapped:<zone>" | "min" | "suspended" | "freed",
  group: "snapgrp-3" | null,         // Snap Group membership
  mem:   { route, scroll, ... }      // window-memory descriptor for re-summon
}
```

- **Web window** = an `<iframe src="gose-files.html">` mounted in a WinBox-style frame inside the
  kiosk WebView. The *same* iframe node is what a widget was (see §5).
- **Native window** = a real X window (Steam/emulator) discovered via EWMH `_NET_CLIENT_LIST`
  (`wmctrl -l`), keyed by its X window id + pid.
- The dock and carousel iterate the merged registry and don't care which kind an entry is — they call
  `POST /wm/<verb>` and the server routes to the web WM (postMessage into the WebView) or to
  `wmctrl`/`xdotool`/signals for native.

### 4.2 Window operations (uniform verbs)
`focus · move · resize · maximize · restore · minimize · snap(zone) · close · suspend · free`

| Verb | Web window | Native window |
|---|---|---|
| focus | WinBox `.focus()` (raise inside WebView) | `xdotool windowactivate <xid>` (already proven) |
| move/resize | WinBox `.move()/.resize()` | `wmctrl -i -r <xid> -e 0,x,y,w,h` |
| maximize | WinBox `.maximize()` | `wmctrl -i -r <xid> -b add,maximized_vert,maximized_horz` |
| minimize | WinBox `.minimize()` → dock | `xdotool windowminimize <xid>` (Openbox iconify) |
| snap(zone) | place in computed zone rect | same rect via `wmctrl -e` |
| close | WinBox `.close()` | `SIGTERM` pid (taskman path) |
| **suspend** | drop iframe `src` (JS GC) | **`SIGSTOP` pid** (Switch-style quick-resume; RAM kept, CPU 0) |
| **free** | remove iframe node + descriptor only | **`SIGKILL`/TERM** pid (true RSS freed) |

Note the deliberate **three tiers of "make it stop using resources"** (the honesty the Switch
research forces): **minimize** (still live) → **suspend** (paused, RAM kept, instant resume) →
**free** (process gone, RAM returned, must relaunch). The model labels which it's doing; "act out"
defaults to suspend, a deeper act-out = free (§5).

### 4.3 Snap layouts, adapted to a controller
- Zones are computed rects for a 16:9 handheld/TV screen: **halves** (L/R, T/B), **thirds**
  (L/C/R columns), **quarters**, and **Partial** (⅔ + ⅓, ChromeOS-style) — useful when one window is
  primary and one is a reference.
- A **Snap Layout chooser** = the Windows `Win+Z` grid, but it's a focus-nav overlay: a small panel
  of layout thumbnails, **d-pad to highlight, A to choose**.
- After placing the first window, **Snap-Assist fill**: the carousel reappears showing the other
  windows as cards to drop into the remaining zone(s) — **d-pad pick, A place** — exactly Windows'
  assist flow, no dragging.
- A completed arrangement becomes a **Snap Group** (saved in the registry + persisted like
  `gose-wpos`), restorable from its dock tile (Windows-style).

### 4.4 Taskbar / dock + the controller window-switcher (the Alt-Tab equivalent)
- **Dock** = a bottom bar of running-window tiles (web + native), each with the reserved **blue focus
  glow** (docs/21) and a small kind/badge. It's a first-class **nav zone** — the `GW` model already
  defines `[Menu][widgets][Dock]` zones, so the dock slots straight in. **A** focuses, **long-press a
  tile** = restore its Snap Group, **X over a tile** = minimize/suspend, **Y** = its window menu.
- **Window Carousel** (the controller Alt-Tab): **hold Guide** → a horizontal carousel of large
  window cards (live thumbnail + title). While held, **L1/R1 (or stick) cycle**; **release Guide** on
  the highlighted card to switch, or **A** to select, **B** to cancel. This is the headline gesture —
  it's the Stage-Manager "staged strip" and the Alt-Tab switcher fused, and it's pure d-pad.
- **Overview** (Mission-Control-style): **hold Guide longer / press Y in the carousel** expands the
  carousel into a **grid of all windows** for find-the-buried-one. d-pad to any cell, A to focus.

---

## 5. The widget↔window-memory model (the owner's design), integrated

This is the spine that makes widgets and windows **one continuum** instead of two systems.

```
 WIDGET (small, live)  ──maximize──►  WINDOW (large, framed)  ──act out──►  DESCRIPTOR (memory only)
   live iframe @ pos        re-parent the SAME iframe            tear down iframe; keep {id,url,route,
   on home canvas           into a WinBox frame (NO reload)      scroll}. RAM released (tier per §4.2).
        ▲                                                                         │
        └──────────────────────────── re-summon (A on its dock/launcher tile) ───┘
                                       remount iframe from the descriptor
```

- **A widget is already a live mini-app** (the `GW` base mounts each widget's body; many widgets fetch
  live data). Treat a widget's body as a mountable surface.
- **Maximize widget → window:** re-parent the **same DOM node / iframe** into a WinBox-style frame
  (WinBox supports singleton `mount(node)` / `unmount(dest)` that *moves* a fragment between
  containers — **no reload, state preserved**). Record a registry entry; the widget slot is left as a
  ghost placeholder.
- **"Act out" → free memory:** when you close/minimize past the act-out threshold, **remove the
  iframe `src` (or the iframe node entirely)**. The page's JS heap, DOM, timers, and decoded images
  become collectible → memory returns to the WebView heap. Only the **descriptor** survives
  (`{id, title, url, route, scroll}`) = the **"window memory"**: the window is gone but re-summonable
  to where it was.
- **Re-summon:** activating the app's dock/launcher tile remounts the iframe from the descriptor
  (optionally restoring route/scroll) — feels like the window was "still there."
- **Memory honesty (the hard, true part):**
  - **Phase 1** act-out frees memory **only within the single WebView process** — JS GC returns it to
    the WebKit heap; the OS may not see RSS drop immediately, and *every web window shares that one
    process's RAM pool*. Cheap and good enough for light apps; **not** true OS-level release.
  - **Phase 2** real OS-level RAM release: a window flagged **heavy** is **promoted to its own
    process** — either a native X window (Steam/emulator, already separate) or a **separate WebKit
    process** for a heavy web app — so "act out" can **SIGSTOP** (suspend, keep RAM, instant resume,
    Switch-style) or **kill** (truly return RSS). The model surfaces the tier so the user/AI knows
    whether "act out" paused it or freed it.
  - **Memory-pressure auto-act-out** (phase 3): when free RAM drops below a threshold, auto-suspend
    the least-recently-used windows (the Switch instinct, but transparent + reversible).

---

## 6. How native apps (Steam / emulators) participate

They're not web — they join the same UX through the registry + Openbox:

1. **On `/launch`** of a native app, the server records a provisional registry entry, then resolves
   its **X window id** (poll `wmctrl -l` / `_NET_CLIENT_LIST` for the new window of that pid) and its
   `pid` — so it gets a **dock tile + carousel card** like any web window.
2. **Switching to it** = `xdotool windowactivate <xid>` (the exact call the server already makes for
   the GOSE window). Switching **away** (back to the shell) = activate the kiosk window by name.
3. **The overlay caveat (hard part #1, the crux):** when a native app is foreground, the dock/carousel
   **cannot** be drawn by the kiosk WebView (it's behind the native window). So invoking the
   carousel/dock over a native app **raises the overlay X window** (`overlay_window.py`,
   `keep_above`) and renders the *same* dock/carousel HTML there — exactly how the Game Bar already
   floats over a running game. Selecting a card then `windowactivate`s the target and hides the
   overlay. This is the one place the "unified" switcher is physically two surfaces (in-WebView when
   shell is foreground; overlay X window when a native app is foreground) presenting one UI.
4. **Suspend/free for native** (real RAM): **`SIGSTOP`** = suspend (Switch-style quick resume, RAM
   retained); **`SIGTERM/KILL`** = act-out/free (RSS returned) — the taskman kill path already exists.
5. **Framing native windows:** add Openbox **per-class window rules** in `rc.xml` (undecorated +
   maximized for fullscreen apps, or sized to a snap zone for windowed ones). Future option: wrap
   native apps in **gamescope** for scaling/HDR/framecap (the SteamOS pattern) — defer to the Odin-2 /
   Wayland milestone (docs/17 §D), not needed for the X11 dev build.

---

## 7. Controller-first interaction — the window-management keymap

Every op is a gamepad op. The bridge already maps the pad to keys; windowing adds a **modal "WM
layer"** so the same buttons mean window-ops while it's active, without stealing the in-app pad.

**Two layers, switched by the Guide button (the SteamOS system-key convention):**

| Input | Normal (in a window / app) | **WM layer** (hold Guide, or after Guide-tap) |
|---|---|---|
| **Guide (KP_5/Home — already global)** | toggle Guide overlay | **hold = Window Carousel**; tap = open WM layer |
| **L1 / R1** | (app's own) | **cycle carousel ← / →** (Alt-Tab step) |
| **Left stick** | (app's own) | cycle carousel / move highlight in overview |
| **A** | activate | **select / focus** the highlighted window |
| **B** | back | **cancel** WM layer |
| **Y** | (app's own) | **maximize ⇄ restore** focused window; in carousel → **overview grid** |
| **X** | (app's own) | **minimize / suspend** ("act out") focused window |
| **L2 (hold) + d-pad** | (app's own) | **Snap Layout chooser** → d-pad picks zone → A places → Assist-fill |
| **D-pad (in WM layer, no L2)** | (app's own) | **nudge-move** the focused window between zones |
| **Start** | (app's own) | window menu (snap / group / suspend / close) |

- **Snap with a pad** = `L2 + d-pad` shows the zone grid (Win+Z), d-pad highlights a zone, **A**
  places, then **Snap-Assist** auto-offers the carousel to fill the rest. No pointer, no drag.
- **Pointer fallback** for the rare drag-resize: GOSE already has AntiMicroX pointer mode + a
  cursor.js (docs/07) — keep it as the escape hatch, but **nothing requires it**.
- **Implementation note (honest):** today `gose-pad-nav.py` is **stateless** key-synthesis (button →
  fixed keysym) and goes **silent when a game is foreground**. The WM layer needs two changes:
  (a) a **modal state** in the bridge (Guide-held → emit WM-semantic events, not raw arrows); and
  (b) the **Game-Bar exception** generalized so the WM layer (like the Game Bar today) is allowed to
  drive while a native app runs — the bridge already has exactly this exception mechanism
  (`/tmp/gose-gamebar-open`), so reuse it as a `/tmp/gose-wm-open` flag. Cleanest form: in WM mode the
  bridge **POSTs semantic events to `/wm/<verb>`** rather than synthesizing keys, so the web WM and
  the native-window controller get one clean event stream. This is admin-gated by the existing
  `AdminGate` (only the OS-admin/dev/admin-AI pad drives windowing).

---

## 8. Reuse-first inventory (verified)

| Need | Reuse | License / size | Notes |
|---|---|---|---|
| Web window frames (drag/resize/min/max/snap/focus, iframe mount, full programmatic API + events) | **WinBox.js** | **Apache-2.0**, **~6 KB** gzip, zero-dep | `url:` mounts an iframe; `mount(node)`/`unmount()` move a node between windows (the no-reload widget→window trick); methods `minimize/maximize/restore/close/focus/move/resize` + events `onclose/onminimize/onmaximize/onfocus/onblur/onmove/onresize` — **all controllable without a mouse**. `onclose` can return `true` to veto close (good for "are you sure / suspend instead"). Minimized windows collapse to a header (no built-in dock) → **we supply the dock**. Source: [github.com/nextapps-de/winbox](https://github.com/nextapps-de/winbox). |
| Native window list/switch/move/resize/max/min | **xdotool ONLY** (already used) | GPL, present in image | **AMENDED (chunk A finding, 2026-06-06): `wmctrl` is NOT on the Batocera image — only `xdotool` is.** Discovery is `xdotool search --onlyvisible` + `getwindowname/getwindowpid/getwindowgeometry` (batched via `%@` command-chaining — one spawn per query, not per window); ops are `windowactivate/windowmove/windowsize/windowstate/windowminimize`. Same EWMH data the §4.2 table's `wmctrl` calls would give — wherever this doc says `wmctrl`, read `xdotool`. Consequence: iconified native windows drop out of the visible list (tracking them across minimize = phase 2). |
| WM engine for native windows | **Openbox** (already running) | GPL, present | `rc.xml` keybinds + window rules. |
| Float a web panel over a native app | **`overlay_window.py`** (already built) | ours | `keep_above` X window; the carousel/dock host when a native app is foreground. |
| Controller nav + drag + persisted layout | **`GW` widget base** (already built) | ours | nav zones, drag-to-move, `localStorage` position persistence → extend for windows + Snap Groups. |
| Process list + kill (act-out/free) | **taskman + `/procs.json` + `/proc/kill`** (already built) | ours | the suspend/free plumbing for native. |
| Controller→event bridge | **`gose-pad-nav.py`** (already built) | ours | add the WM modal layer + `/wm` POST path. |
| (future) Wayland compositor w/ scaling/HDR + X11+Wayland window mgmt | **gamescope** / **labwc** | MIT / GPL | Odin-2 / Wayland milestone only (docs/17 §D). Not phase 1–3. |

Decision on WinBox vs. extending `GW`: **vendor WinBox** for the window *frame mechanics* (it's 6 KB,
Apache-2.0, and solves drag/resize/min/max/snap/focus/iframe correctly), and **wire it to `GW`** for
the controller nav, dock, Snap Groups, and persistence. We don't reinvent the frame; we don't adopt
WinBox's (mouse-first, dock-less) UX wholesale.

---

## 9. Phased build plan

Effort estimates are focused-session days for one builder, excluding live-on-hardware tuning
(needs the Odin / the owner's eye).

### Phase 0 — Spine (~0.5–1 day)
- **Window registry** data model (§4.1) + **`GET /windows`** (merge: web-window list from the WebView
  via a shell-side cache + native list from `wmctrl -l`/`_NET_CLIENT_LIST`).
- **`POST /wm/<verb>`** dispatch (routes to web WM via postMessage, or to `wmctrl`/`xdotool`/signals
  for native) (§4.2).
- Define the **WM semantic-event vocabulary** (`wm.next/prev/focus/snap/min/suspend/free/overview`).
- Reuse: existing server route patterns, taskman `/proc` infra.

### Phase 1 — Web-layer windows for the HTML apps (~3–5 days)  ← **start here**
- Vendor **WinBox.js**; build a thin `gose-wm.js` that wraps it + binds it to `GW` (controller nav,
  blue-glow focus, OSK on text fields).
- **Widget → window**: maximize a widget re-parents its live iframe into a WinBox frame (no reload);
  the home canvas shows a ghost slot. (§5)
- **Dock** of web windows (bottom bar, nav zone) + **Window Carousel** (hold-Guide) over web windows
  only, in the kiosk WebView. (§4.4)
- **Controller Snap**: Snap Layout chooser (`L2 + d-pad`) + Snap-Assist fill, for web windows. (§4.3)
- **Act-out → JS-GC RAM release** + window-memory descriptor + re-summon. (§5, phase-1 tier)
- Extend `gose-pad-nav.py` with the **WM modal layer** (Guide-held → `/wm` events) — web-only scope
  for now (shell is foreground, so no overlay needed yet).
- **Deliverable / acceptance:** open Files + Store + Terminal as three web windows, snap them into a
  layout, Alt-Tab between them with the pad, act-out one and watch WebView memory drop, re-summon it.
  All by controller. (Verifiable on the existing VM — no hardware needed.)

### Phase 2 — Native apps in the same UX + real OS-level RAM release (~4–7 days)
- **Merge native windows** into the registry/dock/carousel (resolve xid+pid on `/launch`,
  EWMH list).
- **Switch to/from native** via `xdotool windowactivate` (proven) + activate-GOSE-by-name back.
- **The overlay switcher**: when a native app is foreground, render the dock/carousel in the
  **overlay X window** (reuse `overlay_window.py`); generalize the Game-Bar pad-exception
  (`/tmp/gose-wm-open`) so the carousel drives over a native app. (§6.3, hard part #1)
- **Real RAM tiers** for native: **SIGSTOP** suspend (quick-resume) / **SIGTERM** free; **promote
  heavy web apps to their own WebKit process** so their act-out truly frees RSS. (§5 phase-2 tier)
- **Openbox per-class window rules** for native app framing (§6.5).
- **Deliverable / acceptance:** launch Steam, Alt-Tab between Steam ↔ a web window ↔ the home shell
  with the pad; snap a web window beside Steam; suspend Steam (instant resume) and free it (RSS drops
  in the dials). All by controller.

### Phase 3 — Parity polish + handheld-correct memory (~3–5 days)
- **Snap Groups**: save/restore arrangements (persist like `gose-wpos`); restore-group from a dock
  tile (Windows-style). (§4.3)
- **Stage-Manager staged-strip** mode (one focus + thumbnail strip) as an alternate layout. (§1.2)
- **Overview grid** (Mission-Control-style "show all windows"). (§4.4)
- **Memory-pressure auto-suspend** (LRU act-out under low free-RAM, transparent + reversible). (§5)
- Optional: **virtual desks / Spaces** (ChromeOS/macOS) — likely overkill for a handheld; gate behind
  a setting. (§1.4)
- **Deliverable:** save a "Files+Terminal" group and restore it in one button; auto-suspend kicks in
  under memory pressure and is reversible.

### Later / device-gated (not in 1–3)
- **gamescope/labwc Wayland** path for scaling/HDR/framecap + native Wayland window management — folds
  into the **Odin 2 / ARM build** (docs/17 §D). Re-evaluate X11 (Openbox) vs Wayland (labwc+gamescope)
  *then*; the registry + `/wm` + dock/carousel UX are transport-agnostic and carry over.

---

## 10. Honest hard parts (the things that will bite)

1. **Unifying web + native into one switcher is physically two surfaces.** A web window can't paint
   over a native X window (it's inside the kiosk WebView, which sits behind native windows). So the
   "one switcher" is in-WebView when the shell is foreground and an **overlay X window** when a native
   app is foreground. It *looks* unified but is two render paths kept in sync via the registry. This
   is the central subtlety; the overlay pattern (`overlay_window.py`) already proves it's tractable.
2. **Controller-driving a WM is the thing desktops fail at** (Xbox FSE: Windows dialogs still aren't
   pad-navigable). GOSE's advantage is it's controller-first from the metal — but the bridge today is
   stateless key-synthesis that goes silent over games. The WM modal layer + the generalized Game-Bar
   exception are real work, not config.
3. **"Free memory" has three honest tiers** — JS-GC (phase 1, within one WebView process, may not drop
   OS RSS), **SIGSTOP** (suspend, RAM kept), **kill** (RSS returned). Phase-1 act-out is the weakest
   tier; the model must *say* which tier it did, or it over-promises. Real OS-level release needs the
   phase-2 process-promotion, which trades the cheap single-WebView model for per-window RAM cost on
   the heavy ones — the same tradeoff the Switch makes deliberately.
4. **WebKitGTK process model**: by default many iframes share one web process, so phase-1 "many
   windows" = one RAM pool (cheap, but no isolation and no true per-window free). Promoting to
   separate processes (phase 2) restores isolation/true-free at RAM cost. Pick per-app, not globally.
5. **Native window discovery is racy**: a freshly-launched app's X window appears asynchronously after
   `/launch`; resolving xid by polling `_NET_CLIENT_LIST` for the new pid's window needs a short
   retry/backoff (some apps reparent or open splash windows first).

---

## 11. Open questions for the owner (approve / steer before build)

1. **Default multitask depth:** Switch-style "one focus, suspend the rest" as the *default* (best for
   handheld RAM/battery), with full snap-multitask as an opt-in mode? Or full-multitask default?
2. **Act-out default tier:** should "act out" default to **suspend** (instant resume, RAM kept) or
   **free** (RAM returned, relaunch)? Proposal: suspend on tap, free on hold/again.
3. **Virtual desks/Spaces** at all on a handheld, or skip (phase 3 is fine either way)?
4. **WinBox vendor vs. build-our-own frame**: OK to vendor WinBox (Apache-2.0, 6 KB) for the frame
   mechanics and wrap it in `GW`, vs. hand-rolling on top of the existing drag code?
5. **X11 now, Wayland later**: confirm we build phases 1–3 on the current X11+Openbox stack and treat
   gamescope/labwc as the Odin-2/Wayland milestone (not now)?

---

Related: docs/03 (architecture), docs/06 (GUI plan), docs/07 (controllers/input), docs/16 (AI
permission/AdminGate), docs/17 §B (this is roadmap item B), docs/21 (widget standard — the `GW`
base this builds on). Code touchpoints: `pc-image/gose-vm-host/{kiosk.py, gose-session.sh,
gose-pad-nav.py, overlay_window.py, gose_vm_server.py}`, `gui/mockup/assets/widget.js`,
`gui/mockup/gose-taskman.html`.
