# 24 — OS Needs & Privacy (privacy-first, reuse-first) `[CUSTOM]`

> Status: **RESEARCH + ROADMAP (2026-06-06).** The owner's framing: *"Privacy is a big thing for
> this project"* and *"if it's already made, just download and use it."* So this doc leads with
> privacy, and every gap names the existing tool/standard/library to **adopt**, not reinvent.
> **This is the owner's to drive from** — nothing here is built; it's the prioritized needs list for a
> distributable GOSE.
>
> Scope note: GOSE is a Batocera-derived, controller-first handheld OS aiming to be a downloadable
> product (PC image now, Odin 2 / ARM later, maybe Steam). This doc covers **both** GOSE-specific
> gaps **and** general modern-OS table-stakes. It does NOT re-list what's already done.
>
> Legend: ⬜ missing · 🟡 partial · ✅ have. Effort: **S** (hours–1 day) · **M** (a few days) · **L** (1–2+ weeks).
> Companion docs: roadmap `docs/17`, build-plans `docs/18`, license `docs/19`, AI permissions `docs/16`,
> windowing `docs/23`.

---

## 0. What GOSE already HAS (so this doc only covers what's genuinely missing)

Treat all of these as **done** — do not re-list them as gaps:

Shell + GPU (virgl) · Settings (Windows-style, 11 sections incl. a Privacy pane shell) · Wi-Fi
scan/join (host netsh) · Bluetooth pair/connect (USB-redir passthrough, verified live) · power
actions · Files (This-PC model) · Terminal · Store (tabbed apps/emulators/games) · retro emulation
+ Steam/Proton/Bottles/Lutris/Heroic · library + cover art + playtime + recents/resume · save
states · accessibility (text scale, contrast, colorblind palettes) · **AI permission tiers +
enforcement + pairing + audit** (docs/16) · OS-protection (system-file delete guard) · AI virtual
controller + multiplayer seats · controller OS-control arbitration + gamepad→key bridge · real
laptop perf/brightness dials · screenshot gallery + clip buffer + live MJPEG stream · favorites /
continue-playing · crash-recovery / safe-mode watchdog · backup/restore + factory reset · license
audit + in-OS attribution page · desktop widgets + a widget standard (docs/21) · USB peripheral
passthrough plumbing · **Tailscale remote (tailnet-only, loopback-bound forwards)** · kernel
shell-sandbox (mount-ns jail + cap-drop). Windowing is **designed** (docs/23), not built.

Security baseline already true (verified in code): `gose_vm_server.py` + `host_bridge.py` bind
**loopback only**; Files API is path-confined (`_safe()` → can't escape `/userdata`); agent
requires a token for non-loopback; per-endpoint rate limits; atomic state writes; rotating logs;
**no analytics/telemetry SDK and no third-party tracker anywhere in the tree** (audited — a strong
starting posture).

---

# 1. PRIVACY (lead section — the owner's emphasis) ⭐

GOSE starts from a genuinely good place: it's local-first by nature (a handheld, not a cloud app),
ships **zero** analytics/tracker SDKs, and most network features are already user-initiated. The
work here is (a) **closing the two automatic outbound calls that exist today**, (b) turning the
existing per-AI permission model into a **general privacy/permission center**, and (c) making the
local-first, no-phone-home posture **explicit, auditable, and enforced** so it survives as a
shippable promise.

### 1.0 The phone-home audit (what GOSE calls out to TODAY — verified in code)

This is the honest inventory. Two calls are **automatic** (fire without the user asking) and need
addressing before any privacy claim; the rest are user-initiated and only need disclosure.

| Call | Where | Trigger | Data that leaves the device | Verdict |
|---|---|---|---|---|
| **Box-art scraper → `thumbnails.libretro.com`** | `gose_vm_server.py` `auto_scrape_boot()` (thread started at import, line ~2759) → `_scrape_one()` | **AUTOMATIC, every boot** (20 s after start) | **The user's ROM filenames** (i.e. *which games they own*), one HTTPS GET per missing-art title, to a third party (libretro.com) | ⬜ **FIX — leaks the game library without consent.** Make opt-in. |
| **Connectivity probe → `8.8.8.8:53` (Google DNS)** | `host_bridge.py` `online()` | **AUTOMATIC, on status poll** | A TCP SYN to Google DNS (reveals the device is online + your IP to Google) | 🟡 **FIX — swap to a neutral/local check** or make it opt-in. |
| Flatpak app install → **Flathub** | `gose_vm_server.py` `store_install()` | User-initiated (Store) | The app ID the user chose to install | ✅ acceptable — disclose in Store. |
| Libretro core install → **`buildbot.libretro.com`** | `gose_vm_server.py` emulator-store (`BUILDBOT_BASE`) | User-initiated (Emulator Store) | The core name the user chose | ✅ acceptable — disclose. |
| **Tailscale** agent + SSH | host, `boot-gose-vm.ps1` | Opt-in setup (off until configured) | Encrypted tailnet traffic to the user's own tailnet (Tailscale coordination server) | ✅ acceptable — already tailnet-only + TLS; disclose. |
| Agent pairing `urlopen` → `127.0.0.1:8780` | `agent/.../server.py` `_PAIR_URL` | Internal | Loopback only — does **not** leave the device | ✅ not a phone-home. |

**Also flag (security/privacy posture):** the **agent default `host` is `0.0.0.0`**
(`agent/gose_agent/config.py` line 19) — it listens on *all* interfaces, gated only by the token
for non-loopback peers. The VM forwards are loopback-bound so this is contained today, but on real
hardware (Odin 2 on Wi-Fi) a `0.0.0.0` bind is LAN-exposed. **Default it to `127.0.0.1`** and make
LAN/tailnet exposure an explicit opt-in. (S)

### 1.1 Telemetry OFF by default + opt-in only
- **What/why:** A distributable OS must collect nothing by default; any diagnostics are strictly
  opt-in with a clear toggle. This is the single biggest trust signal.
- **Status:** ✅ effectively true already (no telemetry SDK exists) — but it's **implicit**. Make it
  an **explicit, enforced, documented** stance with a visible "Telemetry: Off" control that can only
  be turned on by the user.
- **Reuse:** the **GrapheneOS / Mullvad posture** (collect nothing, state it plainly). For the
  *opt-in* diagnostics path later, self-hosted **GlitchTip** (Sentry-compatible, AGPL) so data
  never touches a third party — see §9. No SDK to adopt for "off"; it's a default + a switch.
- **Effort:** S (surface the switch; the default is already "off").

### 1.2 Close the automatic box-art scrape (data-minimization, local-first)
- **What/why:** Sending the user's game list to libretro on every boot is the one real privacy leak
  today. Cover art is nice-to-have, not worth silently disclosing someone's library.
- **Status:** ⬜ currently automatic.
- **Reuse:** keep the **libretro-thumbnails** source (no API key, good coverage) but gate it:
  (a) make `auto_scrape_boot()` **opt-in** (a Personalization/Privacy toggle "Download missing cover
  art" — default **off**, with a one-line disclosure of where it fetches from); (b) keep the manual
  **"Scrape" button** in Library as the explicit, user-initiated path; (c) prefer **art the user
  already has on disk** (the standard Batocera/`images/` layout) before any network call. Optionally
  bundle a small set of public-domain placeholders so an offline library still looks populated.
- **Effort:** S.

### 1.3 No phone-home / no silent network calls (enforced, not just intended)
- **What/why:** "We don't phone home" only holds if nothing *can* silently. After §1.2 the only
  remaining automatic call is the Google-DNS probe.
- **Status:** 🟡.
- **Reuse:** replace the `8.8.8.8` probe with a **route/link check that contacts no one**
  (NetworkManager/connman already report carrier + default-route state; `connmanctl` is on the
  image and the server already parses it). If an active reachability test is ever needed, point it
  at a **neutral, no-logging endpoint the user can change** (or the user's own Tailscale node), not
  Google. Pair this with the **network kill-switch** (§1.7) so "offline means offline."
- **Effort:** S.

### 1.4 A general Privacy & Permission Center (extend the AI tiers to everything)
- **What/why:** GOSE already has the hard part — a per-AI **Observe / Play / Admin** scoped,
  revocable, holder-of-key token model with enforcement + audit (docs/16). Generalize that one
  surface into the OS's **one place to see and control who/what can access what**: AIs, installed
  apps, and OS capabilities (network, files, camera/mic, location, controller).
- **Status:** 🟡 — AI side designed/in-flight; app + OS-capability side missing; a Privacy settings
  pane shell exists but is not wired to real controls.
- **Reuse:** the **docs/16 model itself** is the pattern — reuse **Capframe**-style capability
  tokens / **pymacaroons** (MIT) for the token layer (already chosen in docs/19/16), and present it
  as a **roster with per-entity toggles** (the Android 12+ / GrapheneOS per-app permission model is
  the UX to copy: per-permission, revocable, with a "used recently" indicator). For sandboxed
  flatpak apps, surface **Flatpak portal permissions** (`flatpak permission-show` / Flatseal's
  model) rather than inventing one.
- **Effort:** M.

### 1.5 Encryption at rest — user data + AI tokens/credentials
- **What/why:** A handheld is easily lost/stolen. User saves, screenshots, and especially **AI
  agent tokens and any stored secrets** must be encrypted at rest.
- **Status:** ⬜ — `/userdata` is plain ext4; the dev agent token currently sits in cleartext
  (`/userdata/system/gose/token`, and in `.mcp.json`).
- **What DOES exist (2026-06-07, honest scope):** the lock-screen **PIN is real auth now** —
  per-account salted **scrypt** hash in `accounts.json` (`pin_salt`/`pin_hash`, never cleartext),
  verified server-side at `POST /auth/pin` with a 5-try → 30 s lockout; set/changed at
  `POST /auth/pin/set` (changing requires the current PIN; a has_pin-without-hash account from
  an older OOBE finishes PIN setup at its next unlock instead of being locked out). **It is a
  convenience lock, NOT encryption:** it gates the lock-screen UI only — the disk stays readable
  to anyone with shell/SSH access, and deleting the owner record's `pin_*` keys is the documented
  forgot-my-PIN recovery. Real lost-device protection is this section's LUKS work, unchanged.
- **Reuse:** two tiers, both already named in docs/16/19:
  - **Secrets/tokens:** **age** (or libsodium sealed files) for the AI grant + signing keys —
    decrypt-at-boot, **TPM-sealed** on the Odin 2 if it has a TPM, software-encrypted otherwise.
    `sops` if config-file-level secret management is wanted.
  - **Bulk user data partition:** **LUKS2** (`cryptsetup`, standard on Linux) for `/userdata`, with
    the key in TPM/secure-enclave or a user PIN/passphrase. This is exactly how GrapheneOS/Android
    do file-based encryption; LUKS is the desktop-Linux equivalent and is in the kernel already.
- **Effort:** M (tokens via age) + L (full-partition LUKS + key management on device).

### 1.6 Data export + data delete (right to be forgotten)
- **What/why:** A complete, transparent OS lets the user **take all their data out** and **wipe it**
  on demand — table stakes for GDPR-style trust even on a local device.
- **Status:** 🟡 — backup/restore + factory reset exist (tar of `/userdata` + accounts/saves;
  "Reset GOSE" wipes config not ROMs). Reframe these as the privacy primitives they are and add a
  per-data-type view.
- **Reuse:** the **existing backup/restore tar path** = data export; the **existing factory reset** =
  data delete. Add: a "Your data" list (saves, screenshots, clips, recents, prefs, AI grants) each
  with **Export** and **Delete**; for true wipe use `cryptsetup erase`/key-shred once §1.5 lands
  (crypto-erase is instant and complete). **rclone** (§6) covers export-to-external.
- **Effort:** S (relabel + per-type delete) → M (crypto-erase once encrypted).

### 1.7 Network kill-switch / Offline Mode
- **What/why:** One toggle that guarantees **nothing leaves the device** — the strongest possible
  privacy primitive and a clean demo of the no-phone-home claim.
- **Status:** ⬜.
- **Reuse:** an **nftables**/`iptables` default-deny ruleset toggled from the Guide overlay + Privacy
  center (Linux firewall already in-kernel; this is config, not new code). "Airplane mode" UX from
  any phone OS. When on, block all egress except loopback; optionally allow a user-allowlisted set
  (e.g. their own tailnet).
- **Effort:** S–M.

### 1.8 Privacy Dashboard (show what's collected/sent — ideally "nothing")
- **What/why:** Make the posture *visible*: a dashboard that lists every network destination GOSE
  can contact, when it last did, and which were automatic vs. user-initiated — ideally reading
  "nothing sent automatically."
- **Status:** ⬜.
- **Reuse:** GOSE already has **rotating request logs** + the §1.0 audit; surface them. The model is
  GrapheneOS's **Network/Sensors indicators** + Little Snitch-style "who did this app contact."
  Implement as a read of the firewall/connection log + the egress allowlist; no third-party lib.
- **Effort:** M.

### 1.9 Scrapers / achievements / online features all opt-in with disclosure
- **What/why:** Every feature that touches the network states what it sends and is off until enabled.
- **Status:** 🟡 — scraper auto-runs (§1.2); store installs are user-initiated but undisclosed;
  RetroAchievements isn't built yet (good — build it opt-in from the start).
- **Reuse:** a single **"Online features" disclosure pattern** (one toggle + one sentence per
  feature) reused across scraper, Store, and a future **RetroAchievements** integration
  (RetroArch's built-in RA client, off by default, requires the user's own RA login → inherently
  opt-in). Don't auto-enable any of them.
- **Effort:** S per feature.

### 1.10 No third-party trackers/analytics SDKs (keep it that way)
- **What/why:** The tree is clean today; lock it in so a future dependency can't sneak one in.
- **Status:** ✅ — keep.
- **Reuse:** add a CI gate (§9): **scancode-toolkit** (already used for the license audit) +
  a simple denylist grep for known analytics SDKs on every build. Vendor only permissive,
  no-network libs.
- **Effort:** S.

### 1.11 Principles to adopt (privacy-respecting OS prior art)
- **GrapheneOS:** collect nothing; per-app, per-permission revocable model; network/sensor toggles
  per app; verified boot. → the permission-center + dashboard UX (§1.4, §1.8).
- **/e/OS:** "deGoogled," local-first defaults, no-tracker app sources. → default to neutral/no-Google
  endpoints (§1.3) and Flathub (not Google) for apps.
- **Mullvad / Tails:** minimize, state plainly, no accounts required. → no mandatory account; offline
  works fully (§1.7); plain-language privacy statement shipped in-OS.

---

# 2. Security (beyond what's done)

### 2.1 Signed updates / verified boot
- **What/why:** Updates must be authenticated so a tampered image can't be pushed; verified boot
  ensures the running system is the one shipped.
- **Status:** ⬜ (no update mechanism at all yet — see §3).
- **Reuse:** **RAUC** (A/B updates with signed bundles, x509 verification) or **Mender**; pair with
  **dm-verity** for read-only-rootfs integrity (the Steam Deck / ChromeOS pattern). Bundle signing
  via standard x509/`openssl`. Note the docs/19 **GPLv3 anti-Tivoization** caveat: a *locked* signed
  boot that refuses user cores triggers GPLv3 §6 on a sold device — keep the desktop/VM build
  user-modifiable; reserve lockdown discussion for a future locked handheld.
- **Effort:** L.

### 2.2 Full AI shell-sandbox rollout
- **What/why:** Untrusted/3rd-party AIs (and the AI shell tool) must run jailed.
- **Status:** 🟡 — `sandbox_shell` (mount-ns jail + cap-drop) exists and is on by default; needs to
  be the **enforced, non-bypassable** path for any non-owner agent + extended beyond shell to all
  capability tools.
- **Reuse:** **nsjail** or **landrun** (Landlock-based; both Apache-2.0, already named in docs/19)
  to wrap agent tool handlers; **bubblewrap** for app sandboxing (it's what Flatpak uses — reuse,
  don't duplicate).
- **Effort:** M.

### 2.3 Per-user data isolation
- **What/why:** Multiple accounts (kids, guests) must not read each other's saves/screenshots.
- **Status:** ⬜ — single shared `/userdata`; accounts model exists but no data partitioning.
- **Reuse:** standard **Linux users + per-user home dirs + permissions**, or
  per-account subdirs with ACLs; combine with §1.5 per-user encryption keys. Ties to §4.
- **Effort:** M.

### 2.4 Secrets management
- **What/why:** Tokens/keys/Wi-Fi passwords need a real secret store, not cleartext files.
- **Status:** ⬜ (dev token in cleartext today).
- **Reuse:** **age/sops** for file-level secrets; on-device **libsecret**/kernel keyring for runtime;
  TPM-sealing on hardware that has it. (Same toolchain as §1.5.)
- **Effort:** M.

### 2.5 Firewall posture
- **What/why:** Default-deny egress + a clear allowlist underpins both the kill-switch (§1.7) and
  the dashboard (§1.8).
- **Status:** ⬜ (no firewall rules today; relies on loopback binds).
- **Reuse:** **nftables** (in-kernel) with a shipped default ruleset; UI in §5.4.
- **Effort:** S–M.

---

# 3. Updates / Lifecycle ⭐ (ship-blocker)

### 3.1 OTA updater
- **What/why:** A distributed OS must update safely; there is **none** today.
- **Status:** ⬜.
- **Reuse:** **Batocera's own updater** (GOSE is Batocera-derived — inherit it) for the base, or
  **RAUC A/B** (atomic, signed, rollback-capable) for the GOSE layer. RAUC is the cleanest fit and
  pairs with §2.1.
- **Effort:** L.

### 3.2 Rollback
- **Status:** ⬜ — partial conceptually via the crash-recovery watchdog, but no image-level rollback.
- **Reuse:** **A/B partition scheme** (RAUC/Mender) → failed boot auto-reverts to the last-good slot
  (Steam Deck / Android model).
- **Effort:** (folded into 3.1.)

### 3.3 Release channels
- **What/why:** stable vs. beta so testers don't destabilize shippers.
- **Status:** ⬜.
- **Reuse:** RAUC/Mender channel support + a simple version manifest on the update server (§8.3).
- **Effort:** S (once 3.1 exists).

---

# 4. Accounts / Multi-user / Parental

### 4.1 User switching
- **Status:** 🟡 — accounts model + lock screen exist; no switch-user.
- **Reuse:** the existing **lock screen + accounts substrate**; add a PIN-gated account switcher
  (Batocera doesn't do this natively, so it's GOSE-layer — but reuse the existing lock UI + playtime
  per-account data). Pair with §2.3 isolation.
- **Effort:** M.

### 4.2 Kid mode / parental controls
- **Status:** ⬜.
- **Reuse:** **Batocera `ui_mode` (kiosk/kid) / ES-DE kid mode** pattern + a gamelist `kidgame`
  allowlist; PIN-gate via the lock screen. Time-limits via playtime data (already tracked).
- **Effort:** M.

### 4.3 Profiles
- **Status:** 🟡 — per-account prefs partly exist (localStorage).
- **Reuse:** per-user home dirs (§2.3) hold each profile's prefs/saves/art.
- **Effort:** (folded into 4.1.)

---

# 5. Networking

### 5.1 VPN (general, beyond Tailscale)
- **Status:** ✅ Tailscale (remote access). 🟡 no general consumer VPN.
- **Reuse:** **WireGuard** (in-kernel, trivial config) as the general VPN; **Mullvad**/OpenVPN
  configs importable. WireGuard is the modern default; expose an import-config UI.
- **Effort:** S–M.

### 5.2 Mobile hotspot / tethering
- **Status:** ⬜.
- **Reuse:** **NetworkManager** hotspot mode / `create_ap`; device-gated (needs real Wi-Fi hardware —
  Odin 2, not the NAT VM).
- **Effort:** M (device-gated).

### 5.3 Offline mode
- **Status:** ⬜ — see **§1.7** (this is the privacy kill-switch; same feature).
- **Effort:** S–M.

### 5.4 Firewall UI
- **Status:** ⬜.
- **Reuse:** a GOSE settings page over the **nftables** ruleset (§2.5); show the egress allowlist +
  per-app rules. Feeds the privacy dashboard (§1.8).
- **Effort:** M.

---

# 6. Power/Thermal, Storage, Backup/Cloud-sync

### 6.1 Power/thermal (real sensors)
- **Status:** 🟡 — dials read host values in the VM; real battery/thermal needs device sensors.
- **Reuse:** standard Linux **`/sys/class/power_supply`, `/sys/class/thermal`, `cpufreq`**
  governors (already used where present). Device-gated to the Odin 2.
- **Effort:** M (device-gated).

### 6.2 Storage management
- **Status:** ✅ Storage app (statvfs + treemap). 🟡 no SD/USB auto-mount management UI for removable
  media on device.
- **Reuse:** **udisks2** for removable-media mount/eject; Batocera handles SD already.
- **Effort:** S–M.

### 6.3 Cloud sync (optional, opt-in)
- **What/why:** Sync saves/screenshots to the user's **own** cloud — must be opt-in + user-owned
  storage to stay consistent with §1.
- **Status:** ✅ local backup/restore. ⬜ no cloud sync.
- **Reuse:** **rclone** (MIT) — supports the user's own S3/WebDAV/Drive/Dropbox/etc.; ships as a
  single binary; the user provides their own remote (no GOSE-operated cloud = no data custody for us).
  This doubles as the §1.6 export-to-external path.
- **Effort:** M.

---

# 7. Accessibility, i18n, Help, Onboarding

### 7.1 Accessibility completeness
- **Status:** ✅ text scale, high-contrast, colorblind palettes (libDaltonLens / Okabe-Ito).
  🟡 missing: **screen reader / TTS**, full controller-only reachability audit, larger hit targets.
- **Reuse:** **espeak-ng / Piper** for TTS readout (Piper is already in the owner's agent stack); **Orca** is
  the Linux SR but heavy — a lightweight focus-readout via Piper fits the kiosk better. WCAG as the
  checklist.
- **Effort:** M.

### 7.2 i18n / localization
- **Status:** 🟡 — English only; ~18 HTML pages with inline strings (will multiply if not extracted
  now — flagged in docs/17 §J9).
- **Reuse:** extract strings to **locale JSON** + a tiny runtime lookup (or **i18next**, MIT, if a
  framework is wanted). Do the extraction **before** more pages land. Ship `en` first.
- **Effort:** M.

### 7.3 Help / docs
- **Status:** 🟡 — in-OS Guide-glyph legend planned; public docs are the `docs/` tree.
- **Reuse:** **MkDocs (Material)** to publish `docs/` as a site (CI, §8.2); in-OS contextual help =
  the planned hold-Guide legend per page.
- **Effort:** S.

### 7.4 Onboarding / OOBE completeness
- **Status:** 🟡 — OOBE designed (brand-agnostic, name-your-own-AI, timezone step). Finish + test the
  full first-run, and **add a privacy step** (telemetry off confirm, online-features choices, encryption
  setup) — the OOBE is where §1 defaults get surfaced.
- **Reuse:** the existing `gose-oobe.html` flow; add a privacy panel that sets §1.1/§1.2/§1.5 defaults.
- **Effort:** S–M.

---

# 8. Distribution ⭐ (ship-blockers)

### 8.1 Flashable image + installer
- **Status:** 🟡 — `pc-image/build-gose-pc.sh` needs a Linux host (loop-mount); `make_ova.py` emits an
  `.ova`; no clean downloadable installer yet.
- **Reuse:** Batocera's **`.img` build path** (GOSE is a derivative); **balenaEtcher** as the
  recommended flasher (don't build one). Resolve the **license EXCLUDE list** (docs/19 — 11
  non-commercial cores) at build time for any paid build.
- **Effort:** L.

### 8.2 CI build pipeline
- **Status:** ⬜ (manual builds today).
- **Reuse:** **GitHub Actions** Linux runner → agent tests + `build-gose-pc.sh` → publish `.img`/`.ova`
  to Releases. Solves the "real build needs a Linux host" crux. Add the §1.10 tracker-denylist + §10
  license gate here.
- **Effort:** S–M.

### 8.3 Update server
- **Status:** ⬜.
- **Reuse:** a static manifest + signed bundles on any object store (or GitHub Releases) consumed by
  RAUC/Mender (§3). No bespoke server.
- **Effort:** M.

### 8.4 The Steam path
- **Status:** ⬜ (BLOCKED on docs/19 license EXCLUDE list + Batocera redistribution terms).
- **Reuse:** ship as a downloadable/installable per Valve's process; the blocker is legal (remove the
  11 non-commercial cores, resolve the 3 REVIEW cores, provide GPL corresponding-source) not technical.
- **Effort:** L (mostly legal/packaging).

---

# 9. Dev / Observability

### 9.1 Crash reporting (opt-in, self-hosted)
- **Status:** ⬜ — "export diagnostic bundle" is the tonight-able first step (docs/17 §J4).
- **Reuse:** **sentry-sdk** → **self-hosted GlitchTip** (AGPL, Sentry-compatible) so no data goes to
  a third party. **Off by default; opt-in** (consistent with §1.1). Interim: a manual "Export
  diagnostics" button (logs + health bundle) the user chooses to share.
- **Effort:** S (export) → M (opt-in self-hosted pipeline).

### 9.2 Logging
- **Status:** ✅ rotating `gose.log` (guest); 🟡 `host_bridge` lacks the full rotating treatment.
- **Reuse:** apply the same Python `logging` rotating-handler to host_bridge; **journald** for
  system-level on device.
- **Effort:** S.

### 9.3 CI pipeline
- **Status:** ⬜ — same as §8.2 (tests + build + license/tracker gates).
- **Effort:** S–M.

---

# 10. General-OS table-stakes (anything above missed)

| Item | Status | Reuse | Effort |
|---|---|---|---|
| **Window manager / multitasking** | ⬜ designed (docs/23), not built — biggest lift | **WinBox.js** (web) + **Openbox + wmctrl/xdotool** (native), per docs/23 | L |
| **Clipboard manager** | ⬜ | **clipnotify/greenclip** or a small web-layer clipboard for in-shell; `xclip` for native | S |
| **Notifications center** | 🟡 basic (notify.js + tray) → richer/grouped/actionable | extend existing `notify.js` | S |
| **Global search** | 🟡 apps+settings+files → add games + history | extend launcher `filter()` | S |
| **Clock / alarms / timers** | ⬜ (clock shown; no alarms) | a small web widget; `at`/timer for backing | S |
| **File associations** | 🟡 Files opens viewers by kind → make user-configurable defaults | a `mimeapps.list`-style map | S |
| **OSK controller-driving** | 🟡 OSK exists; controller-driving partial | **simple-keyboard** (MIT) + centralized input routing | M |
| **Consistent gamepad nav every page** | 🟡 some pages keyboard-first | unify on the zone-nav model already in `cursor.js` | M |
| **Printing** | ⬜ (low priority on handheld) | CUPS if ever needed | — |
| **Real suspend/resume** | ⬜ (sleep action exists; true S3 device-gated) | kernel suspend on device | M (device-gated) |
| **Screenshots/clips** | ✅ done | — | — |

---

# 11. RECOMMENDED PRIORITY ORDER

**Tier 0 — Privacy ship-blockers (do first; small, high-trust, mostly S):**
1. **Close the auto box-art scrape** (§1.2) — the one real data leak today. **S.**
2. **Replace the Google-DNS probe** + default the **agent bind to `127.0.0.1`** (§1.3, §1.0). **S.**
3. **Telemetry-off made explicit** + a Privacy step in OOBE (§1.1, §7.4). **S.**
4. **Network kill-switch / Offline Mode** (§1.7) — strongest privacy primitive, also a demo. **S–M.**

**Tier 1 — Privacy depth + the keystone:**
5. **Privacy & Permission Center** generalizing the AI tiers to apps + OS capabilities (§1.4). **M.**
6. **Encryption at rest** — AI tokens/secrets via **age** now, **LUKS** for `/userdata` on device
   (§1.5, §2.4). **M→L.**
7. **Data export/delete** relabel + per-type (§1.6) and **Privacy Dashboard** (§1.8). **S→M.**

**Tier 2 — Distribution ship-blockers (turn it into a product):**
8. **CI build pipeline** (§8.2) — unblocks everything downstream. **S–M.**
9. **OTA updater + rollback** via **RAUC A/B + signed bundles + dm-verity** (§3.1, §2.1). **L.**
10. **Flashable image/installer** + resolve the **docs/19 license EXCLUDE list** for any paid/Steam
    build (§8.1, §8.4). **L.**

**Tier 3 — Completeness (parallelizable, mostly reuse/integration):**
11. **Windowing/multitasking** build (docs/23) — the biggest feature lift. **L.**
12. Multi-user + kid mode (§4); per-user isolation (§2.3). **M.**
13. Cloud sync via **rclone** (§6.3); WireGuard VPN (§5.1); firewall UI (§5.4). **S–M.**
14. i18n string extraction **now** (§7.2); TTS accessibility (§7.1); MkDocs site (§7.3). **S–M.**
15. Opt-in self-hosted crash reporting (**GlitchTip**, §9.1); table-stakes polish (§10). **S–M.**

---

> **Bottom line for the owner:** GOSE's privacy posture is already strong (no trackers, loopback binds,
> local-first, most network use is opt-in). The honest gaps are **two automatic outbound calls** —
> the **boot art-scraper that sends your game list to libretro.com**, and a **Google-DNS reachability
> probe** — plus the **agent listening on `0.0.0.0` by default**. Fix those three (all small), make
> "telemetry off / nothing leaves the device" an explicit + enforced promise (kill-switch + dashboard
> + encryption-at-rest), and generalize the AI permission model into one Privacy Center. After that
> the remaining ship-blockers are distribution mechanics (CI, signed OTA, the license EXCLUDE list)
> and the windowing build. Every gap above names an existing tool to adopt — nothing here needs
> inventing.
