# 25 — First Boot, Preloaded Services & the Default App Set `[CUSTOM]`

> Status: **DESIGN — APPROVED by Zeke 2026-06-06** (Discord session, afternoon).
> The boot ladder below is modeled on how Windows starts — specifically the rule
> that **the OS never asks the user a question until the hardware needed to
> answer it already works.** Step 1–3 facts were verified against the live GOSE
> VM (service list pulled over SSH 2026-06-06), not assumed.

---

## 1. The five-step boot ladder (Windows-style, GOSE edition)

Windows loads firmware → kernel/drivers → services + Plug-and-Play → a hidden
session → and only THEN the setup wizard, with display/input/network already
live. GOSE mirrors that:

| # | Layer | Who provides it | Status |
|---|---|---|---|
| 1 | **Bootloader** | QEMU/SeaBIOS now; the Odin 2's modified abl later | exists / device-gated |
| 2 | **Linux kernel + drivers** (display, storage, input) | Batocera base | exists |
| 3 | **Preloaded services + plug-and-play** (see §2) | Batocera base + GOSE services | exists, gaps in §5 |
| 4 | **The GOSE shell** — window manager (Openbox) → kiosk → GOSE server → controller→menu bridge | ours, autostarts today | exists |
| 5 | **First-boot wizard (OOBE)** → desktop with the default apps (§4) | ours | **to build** |

The pad drives the UI before the first question exists — that's the step-4
guarantee the wizard depends on (the thing Windows handhelds get wrong; see
docs/23 §1.6).

## 2. Step 3 in detail — what's preloaded (verified running in the VM)

**Inherited from the Batocera base** (boot order `S05`→`S32`):

| Service | Windows equivalent | Job |
|---|---|---|
| `udevd` | Plug-and-Play | hardware appears (pad, BT, SD/USB) → load driver, fire rules |
| `pipewire` | Windows Audio | mixing, device switching |
| `connman` + `wpa_supplicant` | network services | WiFi scan/join, wired |
| `bluetoothd` | Bluetooth stack | pairing, HID pads |
| `dbus` | RPC/service bus | inter-service messaging |
| smaller: firmware loader, audioconfig, **brightness**, share/mergerfs (`/userdata` storage), `sixad` (PS3 pads), `acpid` (power events) | various | |

**Added by GOSE** (via the `custom_service` hook, all autostart):

- **GOSE server** — UI backend: store, library, widgets, settings, `/wm` (docs/23)
- **AI agent** — token-gated, tiered (docs/16), loopback-only by default (docs/24)
- **crash-recovery watchdog** — boot counter → safe mode if the shell keeps dying
- *(step 4 rides on these: Openbox → kiosk → `gose-pad-nav.py`)*

## 3. The first-boot wizard (OOBE) — flow

All controller-drivable; every page appears only after its hardware already
works (WiFi page opens pre-scanned; the controller page already sees the pad
that navigated you to it).

1. **Language**
2. **WiFi** — *skippable; GOSE must be fully usable offline* (docs/24)
3. **Create user + login** (login.html flow exists)
4. **Controller pairing** — shows already-detected pads; pair more
5. **Privacy page** — everything **OFF by default**, opt-IN toggles only
   (box-art scraping, any online feature) — docs/24 is authoritative
6. **AI pairing (optional)** — "pair an AI companion?" → the Observe-tier
   token/pairing flow (docs/16); skippable, never nagged
7. Done → desktop, default apps already installed (§4)

### 3a. Step taxonomy — required vs defaulted vs skippable (Zeke 2026-06-06)

Every wizard page carries a visible, controller-readable badge so it's
unmistakable which steps are blockers and which aren't. Three kinds:

- **REQUIRED** (gold badge, **no Skip control**, Continue stays disabled until
  satisfied): **License** (legal accept) and **Account** (the device needs an
  owner — a non-empty username). R1/Continue do not advance; an inline hint
  states what's missing, and clears the moment the step is satisfied.
- **DEFAULTED** (green "Default ready" badge, no Skip — one-button Continue with
  a sensible pre-selection): **Language** (en-US), **Keyboard** (from language),
  **Controller** (the pad that navigated you here is auto-detected + already
  "Ready ✓"; Continue confirms it as owner — connect extras later in Settings).
- **SKIPPABLE** (muted "Optional" badge + an always-reachable Skip button in the
  roving-focus order; skipping advances immediately with **no side effects**):
  **Network/Wi-Fi** ("Skip for now" bypasses scanning entirely → stays offline;
  also carries a subtle "Recommended" badge), **Privacy** (Skip = everything
  stays OFF, the default), **Personalize** (Skip = onyx theme + "GOSE" name),
  **AI pairing** (Skip = no grant; pair later from the Hub). Extra controllers
  are covered by the DEFAULTED controller step — Continue means "the current pad
  is enough."

Skip is the footer ghost button (A once focused, same one-press cost as
Continue); L2 was considered as a Skip accelerator but is reserved by the
windowing snap/modal layer (docs/23), so it isn't reused here. Pad-driven
end-to-end skip-heavy verification done 2026-06-06.

## 4. The default app set (locked by Zeke, 2026-06-06)

**Baked into the image — zero downloads on first boot:**

- **GOSE's own apps:** Library, Store, Settings, Terminal, Files, Gallery,
  Task Manager, AI Hub, Peripherals
- **Emulation:** RetroArch + **every core we may ship commercially**
  (the docs/19 license audit's commercial-safe set — already the default after
  the 11-core swap)
- **Steam** (baked in, not store-download — Zeke's call)
- **Firefox** — **the default browser**
- **CloakBrowser** — the AI-stealth Chromium, so a paired AI can browse from
  inside GOSE (the agent-side browser; distinct from Firefox which is the
  human's)
- **VLC** — media playback (plays ~everything, controller-friendly)
- **Obsidian** — notes/knowledge vault, pre-installed (Zeke's standard)

**Store-download instead of baked (license-fenced):** the 11 personal-use-only
cores from docs/19 — a Steam-bought GOSE can't *sell* them pre-installed, so
the Store offers them as a one-click install (the Emulators tab already does
license-aware installs).

## 5. Step-3 gaps (honest list)

1. **Battery/power management — BUILT (software layer), live on the VM**
   (Zeke 2026-06-06). Done:
   - **Source.** `gose_vm_server.battery_info()` reads real hardware from
     `/sys/class/power_supply/BAT*` (capacity/status + charge_now/current_now
     time-to-empty); in the dev VM (no battery) it sources the laptop's REAL
     battery via the host bridge. Every reading carries an honest
     `battery_source` (`local:BAT0` / `host:Win32_Battery` / `host:psutil`).
     Served at `GET /sys/battery` and mirrored into `/status.json`
     (`battery_pct, charging, secs_left, battery_source`). A
     `/tmp/gose-bat-override` test hook (source `override:test`) lets QA force a
     low value without draining a laptop.
   - **Widget.** "Battery & Power" (`GW.define`, follows docs/21): charge %,
     charging/discharging, est. time left when discharging, honest source line;
     blue focus glow, row-count height, hover-naming; appears in the spatial nav
     order (right dock, between Notifications and System). Pad-verified.
   - **Low-battery warning.** Non-blocking notifications at 20% then 10% while
     discharging, via the existing `GOSE.notify` toast/center path; re-arms on
     charge/recovery. Pad-verified (forced 15% then 8%, both fired once).
   - **Power actions.** Suspend / Restart / Shut Down are focusable,
     pad-navigable items wired to `POST /sys/power`; every invocation is logged
     to `power_actions.log`. Suspend pad-invoke verified end-to-end.
   - `[needs hardware]`: **real ACPI suspend/resume** and the **physical power
     button** — the VM cannot ACPI-sleep, so suspend there logs + no-ops
     (won't hang the guest); the real sleep/resume + power-button event
     (`acpid`) lands on the Odin 2 hardware. Real `/sys/class/power_supply/BAT*`
     readings are also hardware-confirmed there.
2. **Controller breadth — ALL controller types must maneuver the OS on first
   startup** (Zeke 2026-06-06). Two parts: (a) bake `xpadneo` (Xbox pads) into
   the image — PS4/PS5 are kernel-native — `[needs image build]`; (b) the
   pad→menu bridge must accept ANY detected pad during OOBE: the admin-pad
   arbitration (docs/07) only locks in AFTER a user exists — pre-OOBE there is
   no admin yet, so the first pad that navigates becomes the admin candidate.
   **(b) DONE 2026-06-06** in `gose-pad-nav.py` `AdminGate`: when no admin is set AND
   `.oobe-done` is absent, ANY detected pad is allowed to drive the menus; normal
   arbitration resumes once an admin exists or setup completes (selftest cases added).
   **(a)** `xpadneo` image-bake remains `[needs image build]`.
3. **Storage auto-import — BUILT (software layer), verified end-to-end on the VM**
   (2026-06-06). "ROMs found on this card — add to your Library?" When an SD/USB
   with ROMs is inserted, GOSE detects it, offers it, and copies the games into
   the Library.
   - **Reuse, not reinvent.** Batocera's stock stack already does the hard part:
     `99-external-storage.rules` → `batocera-storage-udev` → `batocera-storage-manager`
     **mounts** any inserted partition under `/media/<label>` and skips the
     system/boot/userdata LUNs. GOSE adds **only** detection-of-ROMs + the offer +
     the copy — it does **not** duplicate any mount logic.
   - **Detection.** A parallel GOSE udev rule (`99-gose-storage.rules`, installed +
     `udevadm control --reload`'d each boot by `gose-session.sh`) fires
     `gose-storage-handler.sh` on block add/remove. The handler waits for
     Batocera's `/media` mount, then `POST /storage/detected`. The server
     (`gose_vm_server.py`) scans the volume and classifies each ROM-shaped file by
     extension (parsed from `es_systems.cfg`, the same source ES uses); a
     system-named parent folder is the tie-breaker for ambiguous extensions
     (`.bin`, `.zip`, …). Generic/unknown files are ignored; the system SD,
     `/userdata`, `/boot` are never touched.
   - **Offer (pad-navigable).** A home-page poller (`assets/storage-offer.js`,
     reusing the `GOSE.notify` toast path) fires a one-time toast and shows a modal:
     **N ROMs found on `<volume>` — [Add all] [Choose] [Not now]**. "Choose" opens
     `gose-import.html`, a pad-navigable per-system review surface (toggle systems,
     live count, Import).
   - **Import = COPY (not symlink / not Batocera's mergerfs union).** Removable
     media that gets pulled must never break the Library or leave dangling links —
     a copy makes the games permanently the user's. Files land in
     `/userdata/roms/<system>/`; identical files are skipped (collision-safe);
     name collisions get a `(2)` suffix; the import aborts cleanly if the card is
     pulled mid-copy. Already-imported volumes are debounced (no re-nag).
   - **Eject.** Device-removal drops the offer and aborts any in-flight import
     (the offer poll also filters volumes whose mount has vanished) — no crash, no
     dangling state.
   - `[needs hardware]`: a **real** SD/USB insertion on the Odin 2 (the VM path was
     verified with a `scsi_debug` removable disk + real udev events; on hardware
     the same rule fires from a physical card). USB passthrough of a host stick to
     the dev VM is the other way to exercise it live.

## 5b. Widget nav order (Zeke, 2026-06-06 — required fix)

Cycling focus across desktop widgets must follow **spatial order: left→right,
top→down — computed from the widgets' CURRENT positions**. Widgets are
drag-movable with persisted positions (docs/21), so the order is recomputed
from live geometry whenever a widget moves — never a hardcoded list. Folded
into windowing chunk B (same nav code).

## 5c. Standing test discipline

When testing the OS, the AI tester (Wren) drives it **with her own virtual
pad end-to-end** — every new surface gets actually navigated, not just
rendered — so get-stuck-in-navigation traps are caught and fixed before Zeke
hits them. (A window/page op that can't be done on the pad does not exist —
docs/23 §1.6.)

## 6. Build order

1. ~~Storage auto-import (§5.3)~~ — **DONE 2026-06-06** (software layer, VM-verified
   end-to-end; real-hardware insertion is the only `[needs hardware]` piece).
2. ~~OOBE wizard (§3)~~ — **DONE 2026-06-06** (built + pad-driven end-to-end on the VM).
   `gose-oobe.html` is the full 11-step wizard (welcome · language · keyboard ·
   controller · network[skippable] · license · account · privacy · personalize ·
   AI pairing[skippable] · getting-ready → desktop). First-boot flag
   `/userdata/system/gose/.oobe-done`; server endpoints `GET /oobe/status`,
   `POST /oobe/complete` (writes the flag + `accounts.json`, applies the privacy
   opt-INs via the `scrape_auto` flag, issues the first AI pairing), `POST /oobe/reset`.
   `gose-session.sh` routes the kiosk to the wizard when the flag is absent (covers a
   watchdog relaunch mid-setup), the desktop once done; `gose-boot.html` re-checks via
   `/oobe/status`. Pre-user pad arbitration (§5.2b) implemented in `gose-pad-nav.py`
   (no admin + OOBE-not-done ⇒ any pad drives the wizard). Reuse: `login.html` focus
   idiom, the shared OSK in `cursor.js` (on-pad text entry), `themes.css`/a11y,
   `/controllers` + `/net/scan` live data, `/ai/grant` (Observe pairing token + Hub
   entry). Controller-paced CSS completion animations respect the a11y reduce-motion
   attribute. **Reset to first-boot:** `POST /oobe/reset {"wipe_account":true}` or
   `rm /userdata/system/gose/.oobe-done` (factory reset also resets it).
3. Image-bake of the §4 app set — folds into the `pc-image/` build +
   Steam-packaging work (docs/17 §C); Flatpak for Steam/Firefox/VLC/Obsidian,
   CloakBrowser per its own install notes.

Related: docs/16 (AI pairing), docs/19 (license fence), docs/21 (widgets),
docs/23 (windowing — step 4), docs/24 (privacy defaults — the OOBE privacy page
implements it).
