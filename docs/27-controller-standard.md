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
| **Up/Down/Left/Right** | d-pad (`ABS_HAT0*` / `BTN_DPAD_*`) **and** left stick (`ABS_X/Y`) | `Up` `Down` `Left` `Right` | move focus (spatial order, §3.1) |
| **Accept**         | A (`BTN_SOUTH`) — also Start    | `Return`             | activate the focused element               |
| **Back**           | B (`BTN_EAST`) — also Select    | `Escape`             | back out **one level**; never traps (§3.2) |
| **Tab prev/next**  | L1 (`BTN_TL`) / R1 (`BTN_TR`)   | `bracketleft` / `bracketright` (`[` `]`) | previous / next tab — tab-like structures only (§3.3) |
| **Window carousel**| Guide (`BTN_MODE`) hold or tap  | *(none — semantic `wm.*` events, §4)* | the controller Alt-Tab (docs/23 §7) |
| **Snap**           | L2 held + d-pad                 | *(none — semantic `wm.*` events, §4)* | the controller Win+Z snap chooser |

Mechanics (single source: the constants at the top of `gose-pad-nav.py`):

- **Held-direction auto-repeat:** a held d-pad direction or deflected stick fires
  once immediately, again after **0.40 s** (`REPEAT_INITIAL`), then every
  **0.18 s** (`REPEAT_INTERVAL`) until release. Pages get plain repeated keydowns —
  they implement **no repeat logic of their own**.
- **Stick deadzone:** ±**12000** from center (`STICK_DEADZONE`); one key per
  crossing, re-armed at re-center (no re-fire while held past the threshold).
- **Debounce:** only EV_KEY value 1 (press) maps; release (0) and kernel
  autorepeat (2) are ignored.
- **Guide hold:** release after ≥ **0.35 s** (`GUIDE_HOLD_S`) selects in the
  carousel ("release-selects"); a shorter tap leaves a sticky modal that A/B
  finish. **L2 threshold:** analog `ABS_Z` ≥ **100** (`L2_THRESHOLD`) counts held.
- X (`BTN_WEST`) and Y (`BTN_NORTH`) synthesize **no keys** in normal nav — they
  are reserved by the WM layer (`wm.act` / `wm.overview` while a modal is open).
  Pages must not design pad-required actions onto X/Y (see §3.5).

## 2. The delivery chain — one input authority

```
physical pad ──(usb-redir / Bluetooth)──┐
virtual AI pad ──(uinput) ──────────────┤
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
                        ⑥ key synthesis      (xdotool key <keysym>, DISPLAY=:0)
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
7. **Modals take input priority via capture-phase listeners** with
   `stopPropagation`, and re-arm page nav on close (the `storage-offer.js` /
   OSK pattern).
8. **Accept both arrow-key spellings.** WebKitGTK can deliver `ArrowDown` or
   bare `Down` — match either (`gose-import.html` pattern) when the page might
   run in an embedded frame.
9. **Background refreshes never steal focus.** A poll that re-renders must keep
   (and clamp) the current focus index; resetting focus to 0 mid-navigation was
   the second half of the `13a2f52` bug.

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
(docs/25 §5.2b).

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
