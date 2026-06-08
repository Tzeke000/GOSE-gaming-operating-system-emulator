# 29 — Store: community source repos (games + emulators)

Status: **BUILT** (2026-06-07). Server: `pc-image/gose-vm-host/gose_vm_server.py`
(`Community Store Sources` section). UI: `gui/mockup/gose-store.html` (Sources tab).

## 0. The legal line (firm, non-negotiable)

GOSE ships **only** its existing legal sources: the official curated catalog
(`GAMES_CATALOG` — free/homebrew with author-sanctioned redistribution), the
**libretro buildbot** (emulator cores), and **Flathub** (apps). GOSE **never
pre-loads, suggests, recommends, or links** any specific third-party content
repo. There is no default list, no "popular sources" screen, no example URL in
the UI. **The user brings the URL.** The terms screen makes the ownership
explicit: the legality of a source's content is the responsibility of the
source's maintainer and the user who adds it — the same posture as the SD-card
import (docs/25 §5.3): GOSE moves the user's own bits; it does not curate them.

A source is added only after the user passes, by pad, through:
URL entry → live manifest preview → an explicit terms-acceptance screen.
The acceptance timestamp is stored with the source record.

## 1. Research → the adopted pattern

Looked at (owner's method — borrow, don't invent):

- **F-Droid repo index** (`index-v2.json`): one JSON document at a URL; a repo
  metadata block (name, description, timestamp) + a package list; **per-file
  sha256 + size** verified on download; versioned format (v0→v1→v2).
- **Flatpak remotes** (`.flatpakrepo`): a tiny pointer file — Title, Url,
  Comment/Description, Homepage, Icon — that the user explicitly adds
  (`flatpak remote-add`); the OS ships with only its chosen defaults.
- **EmuDeck / RetroDECK**: deliberately ship **no** ROM-source list at all —
  the user supplies content via their own folders. (Same legal line we hold;
  they confirm the posture, not the format.)

Adopted: **one self-contained JSON manifest at a URL** (F-Droid's single-file
simplicity), carrying repo-level metadata like a `.flatpakrepo` (name /
description / maintainer / homepage) plus an `entries[]` list with the
F-Droid-style integrity fields (`sha256`, `size`) and an honest `license`
field per entry. Versioned via a schema integer. No signing in v1 (F-Droid
signs; we verify per-file sha256 when given and require explicit user
acceptance instead — signing can be layered in a later schema rev).

## 2. Manifest format (schema 1)

A source IS a URL returning this JSON (`Content-Type` irrelevant; ≤ 2 MB):

```json
{
  "gose_source": 1,
  "name": "Example Homebrew Shelf",
  "description": "Hand-built homebrew for consoles I love.",
  "maintainer": "Jane Doe <jane@example.org>",
  "homepage": "https://example.org/shelf",
  "entries": [
    {
      "id": "mygame",
      "type": "game",
      "name": "My Game",
      "system": "gb",
      "url": "https://example.org/files/mygame.gb",
      "dest": "My Game.gb",
      "kind": "direct",
      "license": "GPLv3",
      "sha256": "9f86d081884c7d659a2feaa0c55ad015a3bf4f1b2b0b822cd15d6c15b0f00a08",
      "size": 32768,
      "desc": "A tiny puzzle game.",
      "cat": "Puzzle"
    },
    {
      "id": "somecore",
      "type": "emulator",
      "name": "SomeCore (libretro)",
      "core": "somecore",
      "url": "https://example.org/cores/somecore_libretro.so.zip",
      "license": "GPLv2",
      "sha256": "…"
    }
  ]
}
```

### Top-level fields

| field | req | rules |
|---|---|---|
| `gose_source` | yes | int schema version; this build understands `1` and refuses others honestly |
| `name` | yes | string, 1–80 chars — shown as the provenance label |
| `description` | no | string ≤ 300 |
| `maintainer` | no | string ≤ 120 — shown on the preview + Sources tab |
| `homepage` | no | http(s) URL ≤ 200 |
| `entries` | yes | list, 1–500 entries |

### Entry fields

| field | req | rules |
|---|---|---|
| `id` | yes | `^[a-z0-9][a-z0-9_.-]{0,47}$`, unique within the manifest |
| `type` | yes | `"game"` or `"emulator"` (anything else = honest per-entry error; reserved for later schemas) |
| `name` | yes | string 1–80 |
| `url` | yes | http(s) download URL |
| `license` | yes | non-empty string ≤ 120 — **required**; honest provenance is the price of entry |
| `sha256` | no | 64 hex chars; when present the download is verified and a mismatch **refuses the install** |
| `size` | no | int bytes (display + sanity; sha256 is the integrity check) |
| `desc` | no | string ≤ 300 |
| `cat` | no | string ≤ 24 (display category; defaults to "Community") |

`type:"game"` additionally:

| field | req | rules |
|---|---|---|
| `system` | yes | `^[a-z0-9][a-z0-9_-]{0,31}$` — the `roms/<system>` folder (Batocera system id) |
| `dest` | yes | **safe basename**: `^[A-Za-z0-9][A-Za-z0-9 ._()+\[\]-]{0,79}$` — no `/`, no `\`, no `..`, no leading dot. Violations are refused at validation **and** re-checked with realpath confinement at install |
| `kind` | no | `direct` (default: write the fetched bytes as `dest`) · `zip` (extract `member` as `dest`) · `zipdir` (extract `strip`-prefixed members into a **per-source** data dir + write `dest` as the marker) |
| `member` | zip | zip member path; `..` and absolute paths refused |
| `strip` | zipdir | member prefix to strip |
| `datadir` | zipdir | `^[A-Za-z0-9][A-Za-z0-9_-]{0,40}$` |

Validation is **per-entry and honest**: a bad entry is skipped and reported as
`entries[i] (id): reason` in the add/preview/refresh response (and shown in the
UI); good entries still load. Top-level violations (wrong schema version, no
valid entries, > 2 MB, unreachable URL) refuse the whole source with the reason.

## 3. Server state + endpoints

State: `/userdata/system/gose/store_sources.json` (atomic writes; lives under
the OS-protected `system/gose` prefix so the Files app can't delete it):

```json
{ "schema": 1,
  "sources": [ { "id": "src3fde914c21", "url": "…", "name": "…", "maintainer": "…",
                 "added": 1781000000, "accepted_terms": 1781000000, "refreshed": 1781000300,
                 "entries": [ …validated… ], "errors": [ …last validation report… ],
                 "installs": { "<entry_id>": { "path": "…", "datadir": "…", "t": …, "sha256_verified": true } } } ],
  "orphans": [ { "source_name": "…", "entry": {…}, "path": "…", "removed": … } ] }
```

Source id = `"src" + sha256(lowercased url)[:10]` — deterministic, so re-adding
the same URL updates the same record (keeping `accepted_terms` + `installs`).

Endpoints (all loopback, same trust surface as the rest of the UI server):

| route | method | does |
|---|---|---|
| `/store/sources` | GET | built-ins (read-only display), user sources (+entries, install state), orphans |
| `/store/sources/preview` | POST `{url}` | fetch + validate, return name/maintainer/counts/errors — **stores nothing** (the preview step) |
| `/store/sources/add` | POST `{url, accept_terms:true}` | refuses unless `accept_terms` is literally `true`; fetch + validate + store with `accepted_terms` timestamp |
| `/store/sources/refresh` | POST `{id}` | re-fetch; on fetch failure keeps the old entries and says so |
| `/store/sources/remove` | POST `{id}` | removes the source; **installed files stay** and move to `orphans` (labeled "from removed source X" in the catalog, still uninstallable) |

Installs/uninstalls of source entries route through the **existing** endpoints
`/games/install` / `/games/uninstall` with the namespaced id (below) — one
install machinery, one rate limit.

## 4. Catalog merge + provenance

- Source entry ids are namespaced **`<source_id>:<entry_id>`** — official
  catalog ids never contain `:`, so collisions with official entries (and
  between sources) are structurally impossible.
- `GET /games/catalog` appends `type:"game"` entries with `source` (name) +
  `source_id`; orphans appear with `source: "<name> (removed)"` + `orphan:true`
  while their file exists. Official entries carry no `source` key — additive,
  so existing consumers (store Games tab, `widgets_store`) are unaffected.
- `GET /emulators` gains a `community` list (per-source core entries) rendered
  as separate, clearly-labeled cards at the end of the Emulators tab.
- The Store UI shows a **provenance chip** (source name, distinct color) on
  every third-party entry; official entries keep their plain category chip.

## 5. Install hygiene + hardening (the self-check list, each one built)

1. **Path traversal (malicious manifest):** `dest`/`datadir` must match safe-
   basename regexes (no separators, no `..`) at validation; install re-checks
   `realpath(target) == target` and `startswith(roms/<system> + sep)` (the
   a21e885 confinement); zip extraction has the zip-slip check; emulator cores
   are confined to `/usr/lib/libretro` with the validated core-name regex.
   Tested live with a `../` manifest entry (refused) and a direct install-time
   bypass attempt (refused).
2. **Per-source subfolder hygiene:** a `zipdir` entry's data extracts under
   `roms/<system>/src-<source_id>/<datadir>/` — sources can't clobber each
   other's (or the official catalog's) data trees. The `dest` marker/rom itself
   must live flat in `roms/<system>/` (that's what the Library lists), so flat
   files are collision-guarded instead: an install whose `dest` already exists
   but isn't recorded as THIS entry's install is **refused** ("file exists,
   not owned by this source") rather than overwritten.
3. **Down / slow manifest URL:** 20 s fetch timeout, honest error; `refresh`
   failure keeps the old entries and reports; manifest endpoints rate-limited
   (`add` 6/min, `preview` + `refresh` 10/min).
4. **Huge manifests / downloads:** manifest read hard-capped at 2 MB; ≤ 500
   entries; field length caps; entry downloads hard-capped at 512 MB (over-cap
   refuses, no partial file left — writes are tmp + atomic rename).
5. **Duplicate ids:** in-manifest dupes are per-entry errors; cross-catalog
   dupes impossible via the `:` namespace.
6. **sha256:** verified over the fetched bytes when present; mismatch refuses
   with both digests in the error. `size` mismatch is reported as a note (the
   hash is the integrity check; size alone never blocks).
7. **Schema drift:** `gose_source != 1` refuses with "this GOSE understands
   gose_source: 1" — a future schema bumps the int and old builds fail honest.
8. **Crash safety:** the sources store uses `write_json_atomic`.
9. **Remove ≠ delete the user's files:** removing a source orphans its
   installed files (kept on disk, labeled, still uninstallable one by one).

Note on scope: the manifest URL is fetched by the in-VM server on the user's
own machine at the user's explicit request (same trust level as the built-in
browser or terminal); loopback/LAN URLs are deliberately allowed — that's also
what makes hermetic testing possible.

## 6. UI flow (pad-first, docs/27)

Store → **Sources** tab (L1/R1 reachable, 4th tab):

- **Built-in sources** listed read-only (official catalog · libretro buildbot ·
  Flathub) — visibly "built-in · read-only", no actions.
- **Add source** (focusable button) → modal (capture-phase keys,
  `GOSE.modalPush`/`Pop`, layered-Escape §3.10 — Escape backs out one step):
  1. **URL entry** — text field, shared OSK auto-opens (§3.6); Enter commits →
  2. **Preview** — source name, maintainer, entry count (games/emulators),
     any per-entry validation errors; Continue / Cancel (pad-focusable) →
  3. **Terms screen** — the contract, explicit accept by pad:
     *"This source's content is the responsibility of its maintainer and you.
     GOSE does not review it."* (+ the full points: GOSE doesn't review or
     endorse; only add sources you trust and have the right to use; removal
     keeps installed files). Buttons: **I accept — add this source** / Cancel.
- Per user-source card: **Refresh** and **Remove** are focusable elements in
  the normal ↑↓ order (no pad-unreachable actions).
- Third-party games appear in the **Games** tab with the provenance chip;
  third-party cores appear at the end of the **Emulators** tab under their
  source's labeled card.
