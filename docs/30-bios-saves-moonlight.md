# 30 — BIOS checker · save-state thumbnails · Moonlight tile

Status: built + verified live (2026-06-08, Batocera 43.1 VM). Server:
`pc-image/gose-vm-host/gose_vm_server.py`. Pages: `gui/mockup/gose-bios.html` (new),
`gose-library.html`, `gose-home.html`, `gose-apps.html`. Input law: docs/27.

This wave closes three "silent failure" gaps: a system that needs a BIOS just
crashed with no explanation (#52); there was no "continue where you left off"
picture (#53); and Moonlight shipped on the image but was never surfaced (#66).

---

## #52 — BIOS checker

**The manifest is the real artifact, never a copy.** Batocera ships the
authoritative per-system BIOS list (with md5s) as a module-level `systems = {...}`
dict inside `/usr/bin/batocera-systems`. The server reads it with `ast`
(`ast.literal_eval` of the `systems` assignment node — the script is never
executed) and caches it. If the file is missing/unparseable, the manifest is
empty and `manifest_ok` is `false` (the page shows an honest "unavailable" card).

`GET /bios/status[?system=<key>]` →
```
{ ok, bios_dir:"/userdata/bios", manifest_ok,
  systems:[ { system, name, has_games,
              required, present_count, missing:[filenames],
              complete, none_needed?,            // none_needed: in your library, needs NO bios
              files:[ { file, rel, drop:"/userdata/bios[/sub]",
                        present, archive,         // archive = a .zip whose members aren't md5'd individually
                        md5_ok,                   // true / false / null (null = unknown or not hashed)
                        md5_expected } ] } ] }
```
- **Presence** = the file exists under `/userdata/bios` (paths are confined to
  `BIOS_ROOT`). **md5** is computed only for present, non-archive files with a
  known md5 and size ≤ 96 MB (big PUP/CHD files report `md5_ok:null`). Any one of
  a file's listed md5s counts as a match (region variants).
- **Honesty:** a system the user has games for that needs no BIOS is returned
  with `none_needed:true` (the page shows a green "No BIOS" / "needs nothing"
  card). Multi-file systems (psx/saturn) list every accepted file as a checklist —
  it does NOT claim a system is "broken," it shows found/missing per file.
- **Dedupe:** a `.zip` referenced once per zipped member collapses to one row.

**Page `gose-bios.html`** (pad-driven, docs/27): filter tabs `Needs setup /
Your systems / All` (L1/R1 = `[`/`]`), a left rail of systems (↑↓, wraps) with a
status pill, and a detail pane listing each file with ✓/✗, exact filename, and
"Drop … into /userdata/bios". `→`/A steps focus into the detail pane to scroll
long lists; `←`/B backs out one level; B at the rail leaves to
`gose-settings.html`. Verified live with the virtual pad: render → system nav →
tab switch → B-back.

---

## #53 — save-state thumbnails ("Resume" picture)

RetroArch writes `<game>.state[N].png` beside each save state under
`/userdata/saves/<system>/`. The server serves the **newest** one, path-confined.

`GET /game/state/thumb?system=<s>&game=<g>` → `200 image/png` or `404`.
- **Confinement (tested):** the system/game inputs are rejected if they contain a
  path separator or `..`; the resolved PNG path must `realpath` to inside
  `/userdata/saves/` (a symlink escape is rejected too). `../` attempts → 404.
- `recent.json` entries now carry `state_thumb` (the URL above, or `null`) —
  additive, so old consumers are unaffected.
- **Surfaces:** the Library "▶ Continue Playing" cards and the Home "Apps &
  Games" recent tiles use `state_thumb` first, then cover art, then a
  gradient+title — degrading cleanly when no state/png exists. Library resume
  cards also get a small "▶ Resume" badge.

---

## #66 — Moonlight tile ("Stream your PC")

`moonlight-qt` ships on the Batocera image (`/usr/bin/moonlight-qt`). We surface +
launch it; its own UI discovers PCs and handles pairing (Sunshine / GeForce).

- `GET /apps/moonlight` → `{ ok, installed, bin }` (honest: `installed:false` when
  the binary is absent — the Apps tile then shows "not installed").
- `POST /launch {"app":"moonlight"}` → spawns the binary and returns a
  pairing-guidance `note`; honest error when the binary is missing.
- The tile lives in `gose-apps.html` (the installed-apps launcher — Moonlight is a
  system binary, not a flatpak, so it isn't in `/apps.json`). Verified live: pad
  navigates to the tile, A launches `moonlight-qt`.

---

## For the integrator (#77)

- **Settings row → BIOS checker.** Add to `gose-settings.html` `CATS` under a
  sensible category (e.g. `system` or `about`):
  `{ic:"hard-drive", nm:"BIOS files", sub:"Check which systems need a BIOS", t:"link", go:"gose-bios.html"}`.
  The page already Escapes back to `gose-settings.html`.
- No new settings toggle is required for #53/#66 (both are always-on surfacing).
