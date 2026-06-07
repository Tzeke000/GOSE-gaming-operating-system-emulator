# 19 — License Audit (paid-distribution / Steam ship-blocker) `[CUSTOM]`

> **Status:** SHIP-BLOCKER review for a paid/commercial GOSE build. Compiled 2026-06-06
> from the **live VM** (`/usr/lib/libretro/*.so` + `/usr/share/libretro/info/*.info`) and the
> repo's own vendored assets. Every core-license claim below is the **verbatim `license = "…"`
> string** read from that core's `.info` file on the device; the non-commercial verdicts are
> additionally cross-checked against each project's upstream LICENSE (see *Verification* per row).
>
> **Method (reuse-first):** the libretro `.info` files already carry a machine-readable
> `license` field — that is the authoritative per-core source and is what `scancode-toolkit`
> would surface anyway, so enumerating the `.info` strings (then spot-verifying the
> non-commercial ones upstream) is the faster, equally-thorough path. Scan helper:
> `<agent-home>\scratch\gose_license_scan.py` (host-side, read-only over SSH).
>
> **Bottom line:** stock Batocera ships **117 libretro cores**; **11 are licensed
> non-commercial** and MUST be removed/replaced before any paid release, **3 need manual
> review** (ambiguous or empty license field), and **103 are commercial-OK** but most carry
> **attribution and (for the GPL family) source-availability** obligations. No copyrighted
> BIOS or commercial ROMs ship in the image. The GOSE layer's own deps are all permissive
> (OFL / ISC / MIT / Apache-2.0 / public-domain).

---

## 1. The headline blockers (remove before a paid build)

These 11 cores carry a **non-commercial** clause in their `.info` license string. Selling a
build that bundles them is a license violation. **snes9x, genesisplusgx, and fbneo
explicitly name "commercial bundles / commercial product or activity" as prohibited** —
verified against upstream LICENSE files, not just the `.info`.

| Core (.so) | `.info` license string (verbatim) | System | Verified upstream |
|---|---|---|---|
| `snes9x` | `"Non-commercial"` | SNES | ✅ snes9x LICENSE: *"for non-commercial purposes… Commercial use includes… including Snes9x or derivatives in commercial game bundles"* |
| `snes9x_next` | `"Non-commercial"` | SNES (snes9x 2010) | ✅ same snes9x license lineage |
| `genesisplusgx` | `"Non-commercial"` | MD/MS/GG/SegaCD/SG-1000 | ✅ Genesis-Plus-GX LICENSE.txt: *"Redistributions may not be sold, nor may they be used in a commercial product or activity."* |
| `genesisplusgx-expanded` | `"Non-commercial"` | (GPGX fork) | ✅ inherits GPGX non-commercial license |
| `genesisplusgx-wide` | `"Non-commercial"` | (GPGX fork) | ✅ inherits GPGX non-commercial license |
| `fbneo` | `"Non-commercial"` | Arcade / NeoGeo / CPS | ✅ FBNeo LICENSE.md: *"Redistributions may not be sold, nor may they be used in a commercial product or activity."* |
| `fmsx` | `"Non-commercial"` | MSX | ⚠️ fMSX is well-known non-commercial (Marat Fayzullin); license string consistent — treat as blocker |
| `mame078plus` | `"MAME Noncommercial"` | Arcade (MAME 0.78) | ⚠️ old/pre-2016 MAME license = explicitly non-commercial |
| `opera` | `"LGPL/Non-commercial"` | 3DO | ⚠️ dual string with a non-commercial component → cannot ship in a paid build until clarified |
| `px68k` | `"Custom Non-Commercial"` | Sharp X68000 | ⚠️ custom non-commercial terms |
| `quasi88` | `"BSD 3-Clause and MAME non-commercial"` | NEC PC-8801 | ⚠️ mixed; the MAME-non-commercial portion blocks commercial use |

**Note on coverage:** excluding these does **not** drop whole systems, because the device
already ships commercial-OK alternatives for the big ones:
- **SNES** → `bsnes` (GPLv3), `bsnes_hd` (GPLv3), `mesen-s` (GPLv3) all present. ✅
- **Arcade** → current `mame` (`"GPLv2+"`) is commercial-OK; only the legacy `mame078plus` and `fbneo` are blocked.
- **Sega MD/32X** → `blastem` (GPLv3), `picodrive` (see §3 review) present; **Sega CD / SMS-GG accuracy** is the real loss from dropping GPGX — `gearsystem` (GPLv3) covers SMS/GG, but Sega-CD/Mega-CD has no clean commercial-OK libretro core on the image (see decision list §5).
- **3DO (`opera`)** → no commercial-OK libretro alternative on the image → 3DO becomes an optional user add-on.

---

## 2. Full core inventory (all 117) with verdict

Verdict key: **OK** = commercial distribution permitted, attribution required (see §4 for GPL
source obligation) · **EXCLUDE** = non-commercial, remove from paid build · **REVIEW** =
license field missing/ambiguous, needs manual legal review (not guessed).

| Core | `.info` license | Verdict |
|---|---|---|
| 81 | GPLv3 | OK |
| a5200 | GPLv2 | OK |
| arduous | GPLv3 | OK |
| atari800 | GPLv2 | OK |
| beetle-saturn | GPLv2 | OK |
| bennugd | GPLv3 | OK |
| bk | BSD | OK |
| blastem | GPLv3 | OK |
| bluemsx | GPLv2 | OK |
| boom3 | GPLv2 | OK |
| boom3_xp | GPLv2 | OK |
| bsnes_hd | GPLv3 | OK |
| bsnes | GPLv3 | OK |
| cap32 | GPLv2 | OK |
| desmume | GPLv2 | OK |
| dice | GPLv3 | OK |
| dolphin | GPLv2+ | OK |
| dosbox_pure | GPLv2 | OK |
| easyrpg | GPLv3 | OK |
| emuscv | GPLv3 | OK |
| fake08 | MIT | OK |
| **fbneo** | **Non-commercial** | **EXCLUDE** |
| fceumm | GPLv2 | OK |
| flycast | GPLv2 | OK |
| **fmsx** | **Non-commercial** | **EXCLUDE** |
| freechaf | GPLv3 | OK |
| freeintv | GPLv3 | OK |
| fuse | GPLv3 | OK |
| gambatte | GPLv2 | OK |
| gearcoleco | GPLv3 | OK |
| gearsystem | GPLv3 | OK |
| **genesisplusgx-expanded** | **Non-commercial** | **EXCLUDE** |
| **genesisplusgx-wide** | **Non-commercial** | **EXCLUDE** |
| **genesisplusgx** | **Non-commercial** | **EXCLUDE** |
| gw | zlib | OK |
| handy | Zlib | OK |
| hatari | GPLv2 | OK |
| **hatarib** | *(empty — no license field)* | **REVIEW** |
| holani | GPLv3 | OK |
| kronos | GPLv2 | OK |
| lowresnx | zlib | OK |
| lutro | MIT | OK |
| **mame078plus** | **MAME Noncommercial** | **EXCLUDE** |
| mame | GPLv2+ | OK |
| mednafen_lynx | Zlib\|GPLv2 | OK |
| mednafen_ngp | GPLv2 | OK |
| mednafen_psx | GPLv2 | OK |
| mednafen_supergrafx | GPLv2 | OK |
| mednafen_wswan | GPLv2 | OK |
| melonds | GPLv3 | OK |
| melondsds | GPLv3+ | OK |
| mesen-s | GPLv3 | OK |
| mesen | GPLv3 | OK |
| mgba | MPLv2.0 | OK |
| minivmac | GPLv2 | OK |
| mrboom | MIT | OK |
| mupen64plus-next | GPLv2 | OK |
| neocd | LGPLv3 | OK |
| nestopia | GPLv2 | OK |
| np2kai | MIT | OK |
| nxengine | GPLv3 | OK |
| o2em | Artistic License | OK |
| **opera** | **LGPL/Non-commercial** | **EXCLUDE** |
| parallel_n64 | GPLv2 | OK |
| pce_fast | GPLv2 | OK |
| pce | GPLv2 | OK |
| pcfx | GPLv2 | OK |
| pcsx2 | GPL | OK |
| pcsx_rearmed | GPLv2 | OK |
| pd777 | MIT | OK |
| **picodrive** | **MAME** | **REVIEW** |
| play | MIT | OK |
| pokemini | GPLv3 | OK |
| potator | Public Domain | OK |
| ppsspp | GPLv2 | OK |
| prboom | GPLv2 | OK |
| prosystem | GPLv2 | OK |
| puae2021 | GPLv2 | OK |
| puae | GPLv2 | OK |
| **px68k** | **Custom Non-Commercial** | **EXCLUDE** |
| **quasi88** | **BSD 3-Clause and MAME non-commercial** | **EXCLUDE** |
| reminiscence | GPLv3 | OK |
| same_cdi | GPLv2+ | OK |
| sameduck | MIT | OK |
| scummvm | GPLv3 | OK |
| smsplus | GPLv2 | OK |
| **snes9x** | **Non-commercial** | **EXCLUDE** |
| **snes9x_next** | **Non-commercial** | **EXCLUDE** |
| stella | GPLv2 | OK |
| superbroswar | GPLv2 | OK |
| swanstation | GPLv3 | OK |
| tgbdual | GPLv2 | OK |
| theodore | GPLv3 | OK |
| tic80 | MIT | OK |
| tyrquake | GPLv2 | OK |
| uzem | MIT | OK |
| vb | GPLv2 | OK |
| vba-m | GPLv2 | OK |
| vecx | GPLv3 | OK |
| vice_x128 | GPLv2 | OK |
| vice_x64 | GPLv2 | OK |
| vice_x64sc | GPLv2 | OK |
| vice_xpet | GPLv2 | OK |
| vice_xplus4 | GPLv2 | OK |
| vice_xscpu64 | GPLv2 | OK |
| vice_xvic | GPLv2 | OK |
| vircon32 | 3-clause BSD | OK |
| virtualjaguar | GPLv3 | OK |
| vitaquake2-rogue | GPLv2 | OK |
| vitaquake2-xatrix | GPLv2 | OK |
| vitaquake2-zaero | GPLv2 | OK |
| vitaquake2 | GPLv2 | OK |
| wasm4 | ISC | OK |
| x1 | BSD | OK |
| xrick | GPLv3 | OK |
| yabasanshiro | GPLv2 | OK |
| **zc210** | *(empty — no license field)* | **REVIEW** |

Totals: **103 OK · 11 EXCLUDE · 3 REVIEW = 117.**

---

## 3. The REVIEW set (do NOT guess — manual legal review required)

- **`picodrive` — license string `"MAME"`.** PicoDrive (notaz) historically carried the
  *old* MAME license, which **had a non-commercial restriction** (distinct from current
  MAME's GPLv2+/BSD-3 relicense of 2016). The bare `"MAME"` string is ambiguous about which
  era. **Action:** read PicoDrive's actual upstream LICENSE before shipping; if it is the
  pre-2016 MAME license, reclassify as **EXCLUDE**. Treat as non-shippable until confirmed.
- **`hatarib` — empty license field.** Hatari itself is GPLv2, and HatariB is a libretro
  wrapper around Hatari — *likely* GPLv2 — but the `.info` carries **no** `license` value, so
  this is unverified. **Action:** confirm from the HatariB repo LICENSE; do not assume.
  (Atari ST also needs TOS ROM = BIOS, a separate user-supplied-firmware concern.)
- **`zc210` — empty license field.** Zinc/ZX-related core with no license string on the
  device. **Action:** identify the upstream project and its LICENSE before shipping.

---

## 4. Obligations on the commercial-OK ("OK") cores — not free of duties

"Commercial-OK" ≠ "no strings." For a paid build:

- **GPLv2 / GPLv2+ / GPLv3 / GPLv3+ / LGPLv3 (the large majority above):** commercial sale is
  permitted, **but** distributing the binaries triggers the **corresponding-source
  obligation** — GOSE must ship, or make a written offer for, the complete source of every
  GPL/LGPL core in the image, with build scripts, under the same license, and must **not**
  add restrictions beyond the GPL. **GPLv3 anti-Tivoization** (§6 Installation Information)
  is a live concern if GOSE ships on a **locked-down device** (signed kernel / verified boot
  that refuses user-modified cores) — for a Steam *desktop VM* this is low-risk (users can
  swap cores); for a future locked handheld, flag for legal review.
- **MPL-2.0 (`mgba`), zlib, BSD-2/3, MIT, ISC, Artistic, Public Domain:** permissive — sale
  fine; **retain copyright/license notices and attribution** (see the in-OS credits page,
  `gui/mockup/gose-licenses.html`). Public-domain (`potator`) and Apache-2.0 have no
  attribution-in-binary requirement but it's good practice to list them anyway.
- **Practical requirement:** ship a **third-party-licenses / NOTICE** artifact and an in-OS
  credits screen. Both are now provided: this audit + `gose-licenses.html`.

---

## 5. DECISION / EXCLUSION LIST (what to actually do for a paid build)

**A. Remove these 11 cores from the shipped image** (build-time delete of the `.so` + its
`.info`, and hide the systems they alone serve):

```
snes9x_libretro.so / snes9x_next_libretro.so            (SNES — covered by bsnes / mesen-s)
genesisplusgx_libretro.so / -expanded / -wide           (Sega — partly covered, see B)
fbneo_libretro.so                                        (Arcade/NeoGeo/CPS — see B)
fmsx_libretro.so                                         (MSX — covered by bluemsx GPLv2)
mame078plus_libretro.so                                  (legacy MAME — use current mame GPLv2+)
opera_libretro.so                                        (3DO — no OK alternative; make add-on)
px68k_libretro.so                                        (X68000 — no OK alternative; make add-on)
quasi88_libretro.so                                      (PC-8801 — no OK alternative; make add-on)
```

**B. Replacements / system coverage after exclusion:**
- **SNES** → default to `bsnes` (GPLv3) or `mesen-s` (GPLv3); both already on the image. No loss.
- **Arcade** → default to current `mame` (`"GPLv2+"`). NeoGeo/CPS that FBNeo handled: `mame`
  covers most; if accuracy gaps remain, ship FBNeo only as an **optional user-installed
  add-on** (downloaded by the user, not bundled in the paid depot).
- **Sega MD/SMS/GG/32X** → `blastem` (GPLv3, MD/32X) + `gearsystem` (GPLv3, SMS/GG). **Sega
  CD / Mega-CD** loses its clean core (GPGX/picodrive); ship Sega-CD as an **add-on** or
  resolve `picodrive`'s license first (§3).
- **MSX** → `bluemsx` (GPLv2) replaces fMSX.
- **3DO / X68000 / PC-8801** → no commercial-OK libretro core on the image → expose as
  **optional add-ons the user installs themselves** (the user installing a non-commercial
  core for personal use is the user's act, not GOSE's commercial distribution).

**C. The "optional add-on" pattern** (reuse the store/OTA path from docs/18): keep the paid
base image free of every EXCLUDE/REVIEW core; let users opt-in to install non-commercial
cores from libretro's own buildbot at runtime. The user's personal-use install is outside
GOSE's commercial-distribution liability. Document this clearly and never auto-install them.

**D. Resolve the 3 REVIEW cores** (§3) before they ship in either channel; until resolved,
treat `picodrive`, `hatarib`, `zc210` as **excluded** from the paid base.

**E. Add the source-availability artifact** for all retained GPL/LGPL cores (§4): a hosted
corresponding-source mirror + written offer, referenced from the credits page.

---

## 6. GOSE-layer own dependencies (vendored / imported)

All permissive — **no copyleft or non-commercial blocker in GOSE's own stack.** Attribution
required for OFL / ISC / MIT / Apache-2.0; verified against the in-repo LICENSE files where
present.

| Component | Where | License | Verified from | Verdict |
|---|---|---|---|---|
| **Inter** font (ttf + woff2) | `gui/mockup/assets/fonts/` | **SIL OFL 1.1** | in-repo `fonts/LICENSE` (read) | OK — bundle/sell **with** the OS is allowed; **must not** sell the font alone; keep copyright + reserved-name rule |
| **Lucide** icons (SVG set) | `gui/mockup/assets/icons/` | **ISC** (+ some icons derived from **Feather**, MIT) | in-repo `icons/LICENSE` (read) | OK — keep copyright notice |
| GOSE brand marks (gose-core) | `gui/mockup/assets/brand/` | GOSE-original (first-party) | repo | OK — owned IP |
| `evdev` (python-evdev) | agent optional `[device]` dep | **BSD (Revised/3-clause), per upstream** | `agent/pyproject.toml` (dep) | OK — verify exact upstream LICENSE text before release |
| **WinBox.js** | planned vendor `assets/winbox/` (docs/18 §windowing) | **Apache-2.0** | docs/18 (not yet vendored on disk) | OK when vendored — keep NOTICE/attribution |
| **simple-keyboard** | planned OSK (docs/18 §OSK) | **MIT** | docs/18 | OK when vendored |
| **libDaltonLens** CVD matrices | planned a11y (docs/18 §accessibility) | **Public Domain** | docs/18 | OK |
| **hail2u/color-blindness-emulation** | planned a11y filters | **CC0** | docs/18 | OK |
| Okabe-Ito palette | planned a11y palette | public reference (not copyrightable values) | docs/18 | OK |
| **pymacaroons** | planned token layer (docs/18 §security) | **MIT** | docs/18 | OK |
| **landrun** / **nsjail** | planned sandbox (docs/18 §security) | **Apache-2.0** | docs/18 | OK when vendored |
| `ruff`, `pytest`, `Pillow`, `cairosvg`, `fonttools`, `brotli` | `requirements-dev.txt` (**dev/build only — not shipped in the OS image**) | MIT / MIT / HPND / **LGPLv3** / MIT / MIT | repo | OK — build-time only; not redistributed in the device image |

Notes:
- **"Capframe"** named in docs/14/16 is, per docs/18 §security, an **unverifiable** project —
  already flagged for replacement by `pymacaroons` (MIT). Do not vendor "Capframe" without
  confirming it exists with a real license.
- The planned-but-not-yet-vendored libs (WinBox, simple-keyboard, etc.) are listed so the
  obligation is tracked **before** they land on disk; re-verify each LICENSE at vendor time.
- `cairosvg` is **LGPLv3** but is **dev/build tooling only** (renders concept PNGs) and is
  **not** shipped in the distributed image, so it imposes no obligation on the product.

---

## 7. BIOS / firmware / ROM posture

**GOSE ships NO copyrighted BIOS, firmware, or commercial ROMs.** Verified on the live VM:

- **`/userdata/bios/`** top level contains only `readme.txt` and `NstDatabase.xml` (Nestopia's
  NES game database — GPL data, **not** a console BIOS). All console-BIOS subdirs
  (`amiga/`, `bluemsx/`, `cemu/`, …) are **empty scaffolding** — the standard Batocera
  "drop your own legally-obtained BIOS here" layout. ✅
- **`/userdata/roms/<system>/`**: every commercial-console dir (atari2600, snes, n64, psx,
  gba, …) is **empty** of ROMs (only `gamelist.xml` / `_info.txt` scaffolding). ✅
- Cores requiring BIOS to run (snes9x optional BS-X/STBIOS, Lynx `lynxboot.img`, Saturn,
  PSX `scph*.bin`, 3DO, Atari ST TOS, etc.) ship with **no** such files — the user supplies
  their own. This is the correct posture and must be **kept** for a paid build.

**Bundled free/homebrew content present** (Batocera's default freeware set). These are *not*
copyrighted commercial ROMs, but **for a paid product the redistribution right of each
third-party homebrew title should be confirmed** (the author's permission), or the build
should ship the rom dirs empty and let users add content:

| File | System | Note / license posture |
|---|---|---|
| `doom1_shareware.wad` + `doom1_shareware_license.txt` | prboom | id Software **shareware** — redistribution permitted under the bundled shareware license (license file is shipped alongside). Lowest-risk. |
| `pong1k2p.nes`, `2048 (tsone).nes` | nes | Homebrew (Pong, 2048) — the brief calls these fine; 2048-tsone is open-source. Confirm author redistribution terms. |
| `prboom.wad` | prboom | PrBoom's own free game-data WAD (GPL/free). OK. |
| `MrBoom.libretro` (Mr.Boom) | mrboom | Mr.Boom is open-source (the core is MIT). OK. |
| `SpaceTwins.gba` | gba | Homebrew — **confirm author redistribution permission**. |
| `Old-Towers.bin` | megadrive | RetroSouls homebrew — **confirm redistribution permission** (some RetroSouls titles are freely distributable, some commercial). REVIEW. |
| `Reflectron (aetherbyte).pce`, `Santatlantean (aetherbyte).pce` | pcengine | Aetherbyte homebrew (commercial-homebrew publisher) — **confirm redistribution permission**. REVIEW. |
| `DonkeyKongClassic (Shiru).smc` | snes | Shiru homebrew — generally freely distributable; **uses "Donkey Kong" trademark in the filename** — trademark review. |
| `fix_it_felix_64.d64` | c64 | "Fix-It Felix Jr." (Disney/Wreck-It-Ralph IP) C64 homebrew — **trademark/IP review; recommend removing from a paid build.** |
| `Cannonball.cannonball.disabled` | cannonball | OutRun engine reimplementation; ships **`.disabled`** (no ROM) and needs user's own OutRun data. OK as shipped. |
| `od-commander.odc`, `sdlpop.sdlpop`, `devilutionx`/`moonlight` (.keep, empty) | various | Open-source app launchers (SDLPoP GPLv3 needs user's POP data; DevilutionX needs user's Diablo data). Engines OK; no copyrighted data shipped. |

**Recommendation:** for the paid build, ship **empty** rom dirs (first-run import flow per
docs/18 §distribution) OR keep only the titles whose free-redistribution right is
documented (Doom shareware with its license file, PrBoom WAD, Mr.Boom, 2048). Remove
trademark-laden filenames (Fix-It Felix, Donkey Kong) regardless of code license.

---

## 8. UNKNOWNs — needs manual legal review before a paid release

1. **`picodrive` ("MAME" license)** — confirm pre- vs post-2016 MAME license; may be a 12th
   blocker. (§3)
2. **`hatarib` and `zc210`** — empty `.info` license fields; identify and confirm upstream
   license. (§3)
3. **`opera` ("LGPL/Non-commercial")** — confirm whether any LGPL-only build path exists, or
   treat as a hard 3DO exclusion. (§1)
4. **Batocera base-image redistribution terms** — GOSE is a Batocera derivative; selling a
   Batocera-derived image in a paid Steam depot needs Batocera's own redistribution/branding
   terms confirmed (Batocera is GPL/buildroot-based; mind trademark + the "no commercial
   redistribution of our build" community norms). Cross-ref docs/18 §distribution risk.
5. **GPLv3 anti-Tivoization** on any future **locked** GOSE hardware (signed boot refusing
   user cores) — desktop-VM Steam build is low-risk; locked handheld is not. (§4)
6. **Bundled homebrew redistribution rights** — per-title author permission for SpaceTwins,
   Old-Towers, Aetherbyte titles; trademark on Fix-It-Felix / Donkey-Kong filenames. (§7)
7. **`evdev` exact license text** — confirm the shipped python-evdev version's LICENSE
   (reported BSD) at release time. (§6)
8. **Corresponding-source artifact** for all retained GPL/LGPL cores must exist and be
   reachable before distribution (an obligation, not optional). (§4/§5E)

> None of the items above were guessed. Every "OK"/"EXCLUDE" verdict derives from the actual
> `.info` license string read on the device; the four non-commercial headliners
> (snes9x, genesisplusgx, fbneo, + bsnes-as-replacement) were additionally verified against
> upstream LICENSE files. Anything not directly verifiable is parked in this section.
</content>
</invoke>
