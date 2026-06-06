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

1. **Battery/power management — APPROVED, must be baked into step 3** (Zeke
   2026-06-06): charge %, low-battery warning, suspend/resume on power button.
   The *software* layer (read `/sys/class/power_supply`, battery widget,
   warnings) is buildable + mockable in the VM now; live verification is
   `[needs hardware]` — the VM has no battery.
2. **Controller breadth — ALL controller types must maneuver the OS on first
   startup** (Zeke 2026-06-06). Two parts: (a) bake `xpadneo` (Xbox pads) into
   the image — PS4/PS5 are kernel-native — `[needs image build]`; (b) the
   pad→menu bridge must accept ANY detected pad during OOBE: the admin-pad
   arbitration (docs/07) only locks in AFTER a user exists — pre-OOBE there is
   no admin yet, so the first pad that navigates becomes the admin candidate.
3. **Storage auto-import** — udev already *sees* an inserted SD/USB; the missing
   piece is a rule + UI offer: "ROMs found on this card — add to your Library?"
   **Approved to build** (works in the VM via USB passthrough; task on the list).

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

1. Storage auto-import (§5.3) — after windowing wave-1 lands (same files).
2. OOBE wizard (§3) — new `gose-oobe.html` + first-boot flag in the server;
   reuses login.html, the pairing screen, and the privacy settings page.
3. Image-bake of the §4 app set — folds into the `pc-image/` build +
   Steam-packaging work (docs/17 §C); Flatpak for Steam/Firefox/VLC/Obsidian,
   CloakBrowser per its own install notes.

Related: docs/16 (AI pairing), docs/19 (license fence), docs/21 (widgets),
docs/23 (windowing — step 4), docs/24 (privacy defaults — the OOBE privacy page
implements it).
