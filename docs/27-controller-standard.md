# 27 — Controller Standard

Status: adopted (2026-06-07). Enforcement: `pc-image/gose-vm-host/gose-pad-nav.py`
(the bridge) + `pc-image/gose-vm-host/gose_vm_server.py` (the registry) + every
page under `gui/mockup/`. Companions: docs/21 (widget standard), docs/23
(windowing, esp. §1.6), docs/25 §5b/§5c (nav order + test discipline).

> One contract for every controller and every UI surface, so any pad — Xbox,
> DualSense, Switch, 8BitDo, a generic DInput stick, or an AI's virtual pad —
> speaks **one button language**, delivered to pages through **one input path**.
> A surface that follows this standard is controller-driven for free; a surface
> that reads the pad itself is a bug (see §2.3). Guiding rule, from docs/23 §1.6:
> **an op that can't be done on the pad does not exist.**

---

## 1. The canonical control vocabulary

Every surface consumes exactly this vocabulary, and nothing else. The bridge
turns pad input into these keys; pages handle them in a `keydown` listener.

| Control            | Pad input                       | Synthesized key      | Meaning on a page                          |
|--------------------|---------------------------------|----------------------|--------------------------------------------|
| **Up/Down/Left/Right** | d-pad (`ABS_HAT0*` / `BTN_DPAD_*`) | `Up` `Down` `Left` `Right` | move focus (spatial order, §3.1) |
| **Pointer cursor** | left stick (`ABS_X/Y`)          | *(none — XTEST pointer motion, §1.1)* | moves the real X pointer; never moves focus |
| **Accept**         | A (`BTN_SOUTH`) — also Start    | `Return` — or an XTEST **button-1 click** at the pointer while the cursor is active (§1.1) | activate the focused element / click at the pointer |
| **Back**           | B (`BTN_EAST`) — also Select    | `Escape`             | back out **one level**; never traps (§3.2) |
| **Tab prev/next**  | L1 (`BTN_TL`) / R1 (`BTN_TR`)   | `bracketleft` / `bracketright` (`[` `]`) | previous / next tab — tab-like structures only (§3.3) |
| **Window carousel**| Guide (`BTN_MODE`) hold or tap  | *(none — semantic `wm.*` events, §4)* | the controller Alt-Tab (docs/23 §7) |
| **Snap**           | L2 held + d-pad                 | *(none — semantic `wm.*` events, §4)* | the controller Win+Z snap chooser |
| **Screenshot**     | Guide **held** + R1 (`BTN_MODE`+`BTN_TR`) | *(none — POST `/capture/shot`, §1.2)* | grab the screen to the gallery, **anywhere** incl. in-game |

Mechanics (single source: the constants at the top of `gose-pad-nav.py`):

- **Held-direction auto-repeat:** a held d-pad direction fires once immediately,
  again after **0.40 s** (`REPEAT_INITIAL`), then every **0.18 s**
  (`REPEAT_INTERVAL`) until release. Pages get plain repeated keydowns —
  they implement **no repeat logic of their own**. (The stick no longer
  participates in key repeat — it is the cursor, §1.1.)
- **Stick deadzone:** ±**12000** from center (`STICK_DEADZONE`); inside it the
  stick is at rest (no cursor motion), outside it deflection is normalized
  linearly to ±1.0 at full throw.
- **Debounce:** only EV_KEY value 1 (press) maps; release (0) and kernel
  autorepeat (2) are ignored.
- **Guide hold:** release after ≥ **0.35 s** (`GUIDE_HOLD_S`) selects in the
  carousel ("release-selects"); a shorter tap leaves a sticky modal that A/B
  finish. **L2 threshold:** analog `ABS_Z` ≥ **100** (`L2_THRESHOLD`) counts held.
- X (`BTN_WEST`) and Y (`BTN_NORTH`) synthesize **no keys** in normal nav — they
  are reserved by the WM layer (`wm.act` / `wm.overview` while a modal is open).
  Pages must not design pad-required actions onto X/Y (see §3.5).

### 1.1 The stick cursor (adopted 2026-06-07)

**The model: d-pad = focus, left stick = pointer.** The left stick moves the
**real X pointer** (XTEST relative motion at ≥60 Hz while deflected); it never
emits arrow keysyms. Mechanics (single source: the `CURSOR_*` constants in
`gose-pad-nav.py`):

- **Speed is linear with deflection** — gentle: deflection past the deadzone
  normalizes to 0..1 and scales up to **900 px/s** (`CURSOR_MAX_SPEED`) at full
  throw. Motion updates every `CURSOR_TICK_S` (**12 ms** — nominally ≥60 Hz;
  12 rather than 16 because the guest's `select()` carries ~1.5 ms timer slack,
  and speed is dt-scaled so the tick rate never changes cursor speed) while
  deflected; at rest the bridge returns to its idle cadence (no busy-spin).
  The "is a game running" check lives in a **background watcher thread**
  (`GameWatch`) — the 60 Hz path never spawns pgrep or scans `/proc` (both
  measured slow enough on the guest to cap the cursor at ~25 Hz).
- **A clicks at the pointer** (XTEST button 1) **only while the cursor is
  active** — i.e. the pointer moved within the last **1.5 s**
  (`CURSOR_CLICK_WINDOW`). Otherwise A stays `Return` for focus-nav, and Start
  is *always* `Return`, so Accept is never lost.
- **Auto-hide:** after **5 s** (`CURSOR_HIDE_S`) of pointer idle the X cursor is
  hidden via the **XFixes** extension (`XFixesHideCursor`); **any pointer
  activity wakes it** (task 26, 2026-06-07) — stick motion, **external motion
  the bridge didn't make** (a real host mouse/tablet: each bridge tick compares
  one `QueryPointer` — 0.77 ms measured in-guest — against the last position,
  with the bridge's own XTEST moves excluded by baseline invalidation so stick
  motion can never read as external and pin the cursor visible), and any
  pressed **pointer button**; every wake resets the idle timer. The wake honours
  the same suppression as motion (game / WM modal stays cursor-quiet, and the
  baseline dies while suppressed so in-game mouse motion can't phantom-SHOW at
  game exit). **No code path may leave the cursor permanently hidden:** the
  show/hide transitions fail VISIBLE, the engine's hide is idempotent per X
  connection (XFixes hide is refcounted — a stacked hide would eat a later
  show), and a hide dies with its client connection (a reconnect auto-reveals).
  Hiding by parking the pointer in a corner is **forbidden** — parking is real
  motion and fires hover side-effects. If XFixes is missing the cursor simply
  stays visible (honest limitation, logged at startup).
- **Layer rules apply unchanged (§4):** while a game owns the pad, stick motion
  and A-clicks are suppressed exactly like keys; while a WM modal is open the
  modal owns the whole pad and the cursor is frozen.
- **Topmost-ness:** the X hardware cursor is composited by the X server above
  all windows by nature — it cannot go "under" a window. (A page-drawn fake
  cursor can; see the `cursor.js` reconciliation note in §2.3.)

### 1.2 The global screenshot chord (adopted 2026-06-08)

**Guide held + R1 takes a screenshot, anywhere.** While the **carousel** is open
(Guide held — see §4), pressing **R1** (`BTN_TR`) fires a global screenshot
instead of cycling the carousel. The bridge POSTs the **existing** privacy-gated
capture route — `POST /capture/shot` on the in-guest server (`:8780`) — on a tiny
worker thread so the host screencap (a few seconds worst-case) never blocks the
evdev loop. Mechanics (single source: `gose-pad-nav.py`, `WMLayer`):

- **Works EVERYWHERE, including in-game.** `/capture/shot` grabs the **host**
  frame, so it captures GL games the guest can't read. The chord is handled in
  the **WM modal layer (§4 layer 3)**, which sits *above* game pad-suppression —
  so it deliberately **bypasses the "game owns the pad" silence**, exactly like
  the Game-Bar path. (Holding Guide already drops `/tmp/gose-wm-open`, which
  un-suppresses the pad over a running game; the chord rides that same exception.)
- **Privacy is honoured as a no-op.** The capture route is gated by Settings ›
  Privacy › *Screen capture*; when set to **Never** it returns `ok:false` and the
  bridge **does nothing but log it** (`screenshot chord -> no-op (...)`). The gate
  lives **server-side** (`c43967e`); the bridge never second-guesses it.
- **One shot per press.** Only the R1 key-**down** fires (kernel autorepeat
  `value==2` and release `value==0` are ignored), so a held R1 takes exactly one
  screenshot; a second physical press takes a second.
- **Carousel interplay — R1 is consumed, no double action.** In the carousel R1
  is **consumed** (no `wm.next` tab-cycle), and a shot during the hold **cancels
  the release-select**: the Guide release posts `wm.cancel` (dismiss the switcher)
  instead of `wm.select` (switch windows) — the user's intent was a screenshot,
  not a window switch. **Nothing unique is lost:** R1's old `wm.next` was a
  *redundant* forward-cycle — the **d-pad** (`wm.right`/`wm.left`) and **L1**
  (`wm.prev`) still cycle the carousel both ways.
- **Plain Guide is byte-identical when R1 is not pressed.** Tap → sticky modal;
  hold → release-selects; B → cancel — all unchanged. The `_shot_taken` latch is
  set only by the chord and cleared on every modal exit, so it can never leak into
  a plain Guide gesture.
- **Admin-gated for free.** The carousel is already admin-gated at entry (§4
  layer 4 / §6), so a non-admin pad can't open it and therefore can't chord —
  no separate gate needed.

## 2. The delivery chain — one input authority

```
physical pad ──(input-level passthrough: host evdev → guest uinput,
                pad_passthrough.py — replaced usb-redir/Bluetooth,
                commits cec3bdf / 6994770)──┐
virtual AI pad ──(uinput) ──────────────────┤
                                            ▼
                                  /dev/input/event*  (evdev)
                                        ▼
                        ① device admission   (§5 — pad buttons or rejected)
                                        ▼
                        ② Normalizer         (SDL_GameControllerDB GUID →
                                              standard evdev codes; identity
                                              for every kernel-standard pad)
                                        ▼
                        ③ WM modal layer     (Guide / L2 — consumes events,
                                              POSTs semantic wm.* to /wm/event)
                                        ▼
                        ④ suppression        (game owns the pad; Game-Bar /
                                              WM flag files are the exceptions)
                                        ▼
                        ⑤ AdminGate          (only the OS-admin / dev /
                                              admin-AI-seat pad drives menus)
                                        ▼
                        ⑥ input synthesis    (XTEST via a persistent X conn —
                                              python-xlib vendored in
                                              `pc-image/gose-vm-host/vendor/`;
                                              keys, pointer motion §1.1, clicks.
                                              Fallback: per-event xdotool spawn.
                                              XSendEvent is IGNORED by WebKit —
                                              XTEST is the path that works)
                                        ▼
                        page keydown handlers (the §1 vocabulary)
```

### 2.1 Pages NEVER read the pad

A page **must not** call `navigator.getGamepads()`, listen for
`gamepadconnected`/`gamepaddisconnected`, or poll button state. The bridge is
the **single input authority**; the page's `keydown` handler is the single
consumer. Two reasons, both proven on this codebase:

1. **Dead code at best.** The kiosk's WebKit2GTK has no gamepad library
   (libmanette absent) — `getGamepads` never returns a pad, so the poll burns a
   `requestAnimationFrame` loop forever doing nothing.
2. **Double input at worst — the canonical violation (commit `13a2f52`).** The
   Apps page kept a raw `getGamepads()` poll *on top of* its keydown handler. On
   any build where the gamepad API is live (Chromium preview, a libmanette
   WebKit), one d-pad press fired **both** paths and the grid jumped two cells
   per press. The fix was deleting the poll; the keydown handler is the single
   source of truth. Every raw poll is this bug waiting for the engine to change
   underneath it.

A raw poll also silently **bypasses ③④⑤** — it would keep driving a page while a
game owns the pad, ignore the admin gate, and fight the WM layer. That is not a
feature; it is a privilege escalation.

### 2.2 The Normalizer — one button language for ANY pad

Pads are identified by SDL GUID (bus/vendor/product/version) and remapped via
the vendored community `SDL_GameControllerDB` to the standard evdev codes the
bridge speaks. Kernel-driver pads (Xbox, DS4/DualSense, Switch, 8BitDo, the
virtual Xbox-360 AI pads) already report position-standard codes → the remap is
the **identity** (zero regression). Only a non-standard pad (no `BTN_SOUTH`)
that the DB knows gets rewritten; unknown pads keep sane evdev defaults. Net
effect: a DualSense Cross == an Xbox A == `Return`, with **no per-controller
code anywhere above the Normalizer**.

### 2.3 Permitted secondary synthesizers (same vocabulary, not second authorities)

- **Numpad-as-controller** (`assets/cursor.js`): translates numpad keys into the
  same `Arrow*/Enter/Escape` keydown events. It *produces* the vocabulary; it
  does not read the pad.
  **Reconciliation DONE (2026-06-07, windowing wave):** the page-drawn fake
  cursor (`#gose-cursor` + `cursor:none !important`) is **retired** — the left
  stick drives the **real X pointer** (§1.1), which the server composites above
  every window, so it can never freeze at a web-window frame edge or go
  "under" anything the way the page-drawn one did. If the default X arrow is
  too faint for the dark UI, the fix is an X cursor **theme** in the guest,
  never a fake cursor. (The OSK / numpad / Guide-overlay halves of `cursor.js`
  are unchanged.)
- **The shell→window key bridge** (`assets/gose-wm.js`): forwards the synthesized
  keys into a focused web-window's iframe. Same events, re-routed once, marked
  so they are never re-forwarded.

Anything else that wants pad input (a future remap/capture UI, OOBE controller
detection) goes through a **bridge/server API**, not the page (§7, item for
`gose-remap`).

## 3. Page-side rules

1. **Every interactive element is reachable by arrow-focus**, walking the
   docs/25 §5b spatial order: **left→right, top→down**, computed from live
   geometry (never a hardcoded list). Lists/grids wrap modulo-length so arrow
   movement can never dead-end. If a thing can only be clicked, it does not
   exist on this OS (docs/23 §1.6).
2. **`Enter` activates** the focused element. **`Escape` backs out one level**
   (modal → close the modal; page → parent page) and **never traps**: every
   surface state must have an Escape exit, and a handler that swallows Escape
   without closing something is a bug. Accepting `Backspace` as a Back alias is
   fine; relying on any *other* key for Back is not.
3. **`[` / `]` are only for tab-like structures** (store tabs, settings
   categories, library systems, taskman views). A page with no tabs ignores
   them; a page must not repurpose them for unrelated actions.
4. **Visible focus = the standard blue glow** — the same blue everywhere,
   reserved for focus, exactly as the widget standard defines it (docs/21 §1.4).
   No state-colored glows.
5. **No pad-required X/Y/L2/R2 actions.** The bridge synthesizes no keys for
   them (§1). Single-letter accelerators (`s`, `f`, `o`, `x`…) are
   keyboard-only conveniences; any action a pad user needs must ALSO be a
   focusable element or behind `Enter` on the focused item.
6. **Text entry uses the shared OSK** (`assets/cursor.js`): focusing any text
   field auto-shows it; while open it owns arrows/Enter/Escape (capture-phase),
   so page nav doesn't also fire. Never build a per-page keyboard.
   The OSK's contract (2026-06-07): the commit key is labeled **Enter** and a
   **Tab** key exists. **Enter-chaining:** Enter commits the field (the
   synthesized keydown lets single-field modals submit — a page that
   `preventDefault`s it owns what happens next); on a multi-field form it
   auto-advances to the next visible text input (DOM order) with the OSK open;
   on the LAST field it closes the OSK and hands focus to the page's primary
   control — `[data-osk-primary]`, else a real submit button — and fires a
   bubbling `gose-osk-chain-end` event for roving-focus pages. Pages whose pad
   nav moves a roving `.focus` class (the OOBE) may set `data-osk-auto` on
   `<body>`: the OSK then DOM-focuses a text field the roving focus lands on,
   so it auto-opens with no manual summon (opt-in only).
7. **Modals take input priority via capture-phase listeners** with
   `stopPropagation`, and re-arm page nav on close (the `storage-offer.js` /
   OSK pattern).
8. **Accept both arrow-key spellings.** WebKitGTK can deliver `ArrowDown` or
   bare `Down` — match either (`gose-import.html` pattern) when the page might
   run in an embedded frame.
9. **Background refreshes never steal focus.** A poll that re-renders must keep
   (and clamp) the current focus index; resetting focus to 0 mid-navigation was
   the second half of the `13a2f52` bug.
10. **The layered-Escape contract (adopted 2026-06-07).** Inside a *windowed*
   page, Escape (pad B) is normally a **shell window op** (close the window —
   docs/23 §7, commit `016c6cb`). But "back out one level" (§3.2) means a
   page's open **modal / sub-layer** must absorb that Escape first. The signal:

   > **While any modal/sub-layer is open, the page sets
   > `document.body.dataset.goseModal = "1"` (i.e. `<body data-gose-modal="1">`),
   > and removes it when the last layer closes.**

   The shell WM (`gose-wm.js`, both key paths — the shell capture handler and
   `hookFrame`'s in-iframe handler) checks the focused frame's body before
   consuming Escape: signal present → the Escape is **forwarded into the page**
   (one layer closes); signal absent → the window op runs. The shell document's
   own body carries the same signal for desktop-level layers (Quick-Access,
   OSK, notification center). Implementation sugar: `cursor.js` exposes
   refcounted `GOSE.modalPush()` / `GOSE.modalPop()` (overlapping layers — e.g.
   the OSK open on top of a password modal — stay correct); the shared OSK and
   the Quick-Access overlay are wired through it already, so any page using the
   shared OSK gets the contract for free. A page with its own modal (Wi-Fi
   password picker, wizard sub-dialogs, settings pickers) must push/pop around
   the modal's lifetime (or set the attribute directly if it doesn't load
   `cursor.js`). Result: B walks the layers — OSK → modal → window → (fullscreen
   page) desktop — one level per press, nothing ever trapped, nothing skipped.

## 4. Layer precedence (highest first)

Exactly one layer consumes a given pad event. From the top:

| # | Layer | Test | Effect |
|---|-------|------|--------|
| 1 | **Game owns the pad** | a game/emulator process is foreground and running (not SIGSTOPped) | bridge silent — the game reads evdev directly |
| 2 | **Game Bar flag** | `/tmp/gose-gamebar-open` exists | un-suppresses keys so the pad drives the bar over the (frozen) game |
| 3 | **WM modal layer** | Guide / L2 engaged, or `/tmp/gose-wm-open` | consumes the whole pad; semantic `wm.*` events POSTed to `/wm/event`, **no keys synthesized** |
| 4 | **Admin gate** | registry: OS-admin / dev pad / admin-AI seat (§6) | a non-admin pad's events are dropped for menus (it still works in games via layer 1) |
| 5 | **Page keydown** | default | the §1 vocabulary drives the focused surface |

Special cases, by design: the WM layer's flag mirrors the Game-Bar exception so
window ops work over a running game; the gate **fails open** when the registry
is unreachable (nav must never break because the server hiccuped); pre-OOBE
(no admin chosen, `.oobe-done` absent) **any** pad drives the first-boot wizard
(docs/25 §5.2b); and the **screenshot chord** (Guide held + R1, §1.2) is handled
inside layer 3, so it fires over a running game just like a window op — capturing
the host frame regardless of the game's hold on the pad.

### 4.1 Native-app discipline (adopted 2026-06-07 — the browser-trap killer)

A launched **native X app** (Firefox/VLC/Chromium/any flatpak) is a sibling
window *over* the kiosk; XTEST keys land in it, and its idea of Escape is its
own — a pad user could enter a browser and never get out. The bridge therefore
watches the **foreground X window** (`NativeWatch`: `_NET_ACTIVE_WINDOW` +
`WM_CLASS`/`_NET_WM_NAME` polled every `NATIVE_POLL_S` = 0.5 s in a background
thread on its **own X connection** — never the 60 Hz hot path, mirroring
GameWatch). Kiosk-family windows (`kiosk.py`, `gose-overlay`, name `GOSE` —
verified live) are exempt. While a **native app is foreground and no game is
running**:

| Pad input | Effect |
|---|---|
| **B** | politely close that window — EWMH **`_NET_CLOSE_WINDOW`** ClientMessage (the `wmctrl`-equivalent; an XTEST Escape would go *into* the app and mean whatever it wants) |
| **Guide** | **escape hatch**: re-activate the kiosk (`_NET_ACTIVE_WINDOW` ClientMessage) *and* the WM layer posts `wm.carousel` as usual — the user lands in the switcher, on top |
| everything else | flows to the native app unchanged (keys, stick cursor, A-clicks) |

Layer rules still hold: a **running game** suppresses all of this (the game's
window is native too — B must never close it; layer 1 wins), WM modals consume
B as `wm.cancel` first (layer 3), and the **AdminGate** applies (a friend's pad
can't close your browser). On a window's **first foreground sighting** the
bridge also applies the app-class policy's *native windows open
fullscreen-maximized* default (`_NET_WM_STATE` add maximized, once per window,
skipped while a game runs — docs/23 §4.5). If the vendored Xlib can't connect,
the watcher is OFF and logged; the bridge behaves exactly as before (honest
fallback). All decision logic is pure and covered by `--selftest` fakes.

## 5. Device admission — buttons are identity, names are not

A `/dev/input/event*` node attaches to the bridge **only if it exposes real pad
buttons**, i.e. at least one EV_KEY code inside (`GAMEPAD_BTN_RANGES` in
`gose-pad-nav.py::is_gamepad`; mirrored as `_PAD_BTN_RANGES` /
`_blk_has_pad_buttons` in `gose_vm_server.py` for the controller registry):

| Range | Codes | Covers |
|-------|-------|--------|
| `BTN_MISC..BTN_MOUSE`     | `0x100–0x10f` | exotic DInput pads |
| `BTN_JOYSTICK..BTN_DIGI`  | `0x120–0x13f` | classic joystick + gamepad (`BTN_SOUTH`…) |
| `BTN_DPAD_*`              | `0x220–0x223` | discrete-button d-pads |

Deliberately **outside**: mouse buttons (`0x110–0x117`, touchpads), digitizer
codes (`0x140–0x14f`), plain keyboard keys (`< 0x100`), and switch-only nodes.

**The lesson (2026-06-07): name keywords are not identity.** A composite pad
ships sibling nodes — "… Motion Sensors", "… Touchpad", "… Headset Jack" —
whose names all say "Controller". Before this rule, the DualSense motion node
(a) streamed ~1.4k ev/s whose accelerometer crossed the stick deadzone whenever
the pad was physically handled, emitting phantom arrows that queued real
presses seconds behind serialized `xdotool` spawns (the "7.2 s d-pad lag"), and
(b) showed up in the Hub as a settable OS-admin "controller" and claimed a game
player slot, shifting every AI seat. Buttons-in-range is the universal test; no
per-pad special cases.

## 6. Who may drive the OS menus

The bridge's AdminGate maps each admitted device to the server's controller
registry (`GET /controllers`) **by `/dev/input/eventN` path** and allows it iff
it is the **OS-admin** controller (set in the Hub /
`os_admin_controller.json`), the **dev pad** (the original seat-1 virtual pad),
or an **admin-tier AI's seat pad** (`ai_tokens.json`, seat → N-th virtual pad in
js order). Everyone else's pad does nothing in menus and everything in games.
Fail-open on registry unreachable; everyone-allowed pre-OOBE (§4).

## 7. The verification rule — drive it before you ship it

From docs/25 §5c, binding here: **a surface ships only after an end-to-end
virtual-pad drive** — enter the surface from where a user would, traverse
**every focusable element** with directions only, activate, back out with B all
the way to where you started. Rendering is not navigability; getting stuck IS
the bug, and it must be found by the tester's own pad, not by the user's.

### 7.1 The tester surface — `gose-padtest.html` (tasks #40 + #56, 2026-06-07)

A user-facing diagnostic for this standard: a pad diagram that lights as the §1
vocabulary arrives, honestly labeled **"what GOSE hears"** — it visualizes the
bridge's synthesized keys (the only thing pages ever receive, §2.1), never the
pad itself. Accept lights A *and* Start, Back lights B *and* Select (both map to
one key — the diagram shows the meaning, not a guess at the physical button).
Never-synthesized controls render dashed with their real owner labeled (left
stick = the X pointer §1.1 — shown live from pointer events; Guide/L2 = WM
layers; X/Y = reserved; R2/right stick = unused). Connected pads come from
`GET /controllers` with source chips (passthrough / virtual / bluetooth /
native, + OS-admin / dev-pad badges). A second tab measures **in-page input
latency** (key event → next frame callback) and displays the known chain
numbers honestly: passthrough p50 2.17 ms / p95 4.33 ms (commit `cec3bdf`),
XTEST synthesis sub-ms (~0.4 ms; vs ~50–100 ms per xdotool spawn), display
scan-out ≤1 refresh — unmeasurable from JS, so no fake total. "Test inputs" /
"Start sampling" are modal capture layers under the §3.10 contract: B lights on
the diagram **and** closes the layer (one level per press — the B test *is* the
exit).

- **Deferred (integrator pass):** the Settings row —
  `{ic:"gamepad-2", nm:"Controller test & latency", sub:"See what GOSE hears + measure input lag", t:"link", go:"gose-padtest.html"}`
  in `gose-settings.html` → `CATS` → the `controllers` category. The page
  already Escapes back to `gose-settings.html#controllers`.
- **Deferred (needs a server build):** raw per-button/axis testing below the
  vocabulary. Pages must not read `/dev/input` (§2.1), so this needs a registry
  endpoint — proposed: `GET /controllers/raw?id=<registry id>` streaming
  line-delimited JSON `{"t": <epoch ms>, "type": "key"|"abs", "code": <evdev
  code>, "name": "BTN_SOUTH", "value": <int>}` (read-only `evdev` tap of that
  one node, admin-gated like menus §6, auto-closing when the client drops or a
  game takes the pad — layer 1 must win here too). The page already probes the
  route and degrades to an honest `[needs server]` card.

## 8. Adding a new surface — checklist

1. **Input:** one `keydown` listener consuming only the §1 vocabulary
   (+ optional keyboard-only accelerators). **No `getGamepads`, no gamepad
   events, no polling — ever.**
2. **Focus:** every interactive element arrow-reachable in spatial order
   (§3.1); movement wraps; focus survives background refreshes (§3.9).
3. **Activate/Back:** `Enter` activates; `Escape` backs out one level and can
   never be swallowed without an exit (§3.2).
4. **Tabs:** `[` / `]` iff the surface has tab-like structure; otherwise ignore
   them (§3.3).
5. **Focus visuals:** the standard blue glow, focus-only (§3.4 / docs/21).
6. **Secondary actions:** reachable without X/Y/L2/R2 (§3.5).
7. **Text fields:** plain `<input>`/`<textarea>` so the shared OSK engages
   (§3.6); modal dialogs capture keys and always close on Escape (§3.7).
8. **If it's a widget,** declare it through the widget base (docs/21) — nav,
   glow, and naming are inherited, not re-implemented.
9. **Verify:** full virtual-pad drive per §7 before it ships. If the drive gets
   stuck anywhere, fix the surface, not the test.
