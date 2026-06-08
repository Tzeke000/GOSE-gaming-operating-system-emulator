# 33 — Prep for ship: clean image + packaging gate (Task #91)

**Status:** shipped to `dist/package-bundle.ps1` + `pc-image/verify-image-clean.ps1`
2026-06-08. A conclusive end-to-end test still needs a real `sudo ./build-gose-pc.sh`
on a Linux host followed by a Windows `package-bundle.ps1` run (see "Untested seam").

---

## The problem (#91)

`dist/package-bundle.ps1` defaulted its image source to the **hand-built dev disk**
(`D:\gose-vm\batocera-x86_64-43.1-20260529.img.gz`). That disk contains:

| What | Where | Risk |
|---|---|---|
| Owner agent-admin token | `/userdata/system/gose/token` | Anyone SSHs in / reads the image → agent-ADMIN |
| Per-AI provider keys | `/userdata/system/gose/ai_tokens.json` | API keys exposed |
| AI permission grants | `/userdata/system/gose/ai_grants.json`, `/userdata/gose-ui/ai_grants.json` | AI permission state from dev session |
| AI player registry | `/userdata/gose-ui/ai_players.json` | Dev session state |
| SSH ON + root pw "linux" | `batocera.conf`: `system.ssh.enabled=1` | Trivial remote root on any Wi-Fi |
| No `system.security.enabled=1` | (absent/commented) | Default root pw; no iptables |
| Per-user state | `recent.json`, `favorites.json`, `playtime.json`, `storage_offers.json`, `scrape_state.json` | Dev session leaked into shipped image |

Note: `.oobe-done` and `accounts.json` were **not** found on the current dev disk
(the OOBE gate appears to gate differently), but both are checked by the verify gate
as forward-compatibility guards (they are the relevant files if the OOBE ever writes
them to disk).

---

## The fix — two parts

### 1. `package-bundle.ps1`: default to the clean build output

`-ImageGz` now defaults to `pc-image/build/gose-pc-x86_64.img.gz` — the output of
`build-gose-pc.sh` (docs/32). That image is built from repo sources only, so it
carries none of the dev-disk cred files and inherits the hardened `batocera.conf.gose`
(`system.ssh.enabled=0`, `system.security.enabled=1`, `system.samba.enabled=0`).

**To package:** run `build-gose-pc.sh` on a Linux host first, then run `package-bundle.ps1`.

The old dev-disk default is gone. If you need to ship a dev-captured disk (fallback),
pass `-ImageGz` explicitly and scrub first (see below) — the script will still gate it.

### 2. `pc-image/verify-image-clean.ps1`: fail-closed cleanliness gate

`package-bundle.ps1` calls this gate before copying the image. It mounts the image
via WSL loop-mount and asserts:

**Absence checks** — packaging fails if any of these exist:
```
/userdata/system/gose/.oobe-done
/userdata/system/gose/accounts.json
/userdata/system/gose/token
/userdata/system/gose/ai_tokens.json
/userdata/system/gose/ai_grants.json
/userdata/gose-ui/ai_grants.json
/userdata/gose-ui/ai_requests.json
/userdata/gose-ui/ai_players.json
/userdata/gose-ui/recent.json
/userdata/gose-ui/favorites.json
/userdata/gose-ui/playtime.json
/userdata/gose-ui/storage_offers.json
/userdata/gose-ui/scrape_state.json
```

**Hardening checks** — packaging fails if any of these are wrong/absent in
`batocera.conf`:
```
system.ssh.enabled=0
system.security.enabled=1
system.samba.enabled=0
```

The gate is **fail-closed**: it exits non-zero and packaging aborts if any check fails.
There is no silent pass.

---

## Scrub mode (safety net)

If a dev-captured disk must be shipped (fallback), `-Scrub` scrubs a **COPY** of the
image — never the original at `D:\gose-vm\`:

```powershell
# Scrub a copy; then verify the copy; then package from the copy.
powershell -File pc-image\verify-image-clean.ps1 -ImageGz <dev-disk.img.gz> -Scrub
powershell -File pc-image\verify-image-clean.ps1 -ImageGz <scrubbed-dev-disk.img.gz>
powershell -File pc-image\dist\package-bundle.ps1 -Out C:\GOSE-dist -ImageGz <scrubbed-dev-disk.img.gz>
```

The scrubber removes the cred files and writes the hardening keys to `batocera.conf`.
Prefer the clean build over the scrubber — the clean build is the right answer;
the scrubber is the safety net.

---

## Files changed

| File | Change |
|---|---|
| `pc-image/dist/package-bundle.ps1` | `-ImageGz` default → clean build output; cleanliness gate added before image copy; missing image is now fail-closed (not a warning) |
| `pc-image/verify-image-clean.ps1` | New. Absence + hardening gate; -Scrub mode for dev-disk fallback |
| `docs/33-prep-for-ship.md` | This file |

Files explicitly NOT touched: `build-gose-pc.sh` (owned by #90), any server/agent
code, any gui pages, `D:\gose-vm\` (live dev VM).

---

## Untested seam

Verified here: (a) PowerShell parse (`Test-Script`); (b) logic review — the gate
checks every file found on the live dev disk + the hardening keys; (c) the default
image path matches `build-gose-pc.sh`'s `$OUT_IMG` (compressed) output path.

**Not verified:** a real Linux `sudo ./build-gose-pc.sh` producing the image at
`pc-image/build/gose-pc-x86_64.img.gz`, followed by a Windows
`package-bundle.ps1 -Out <dest>` run that invokes the verify gate against that image.
That is the conclusive test. The verify gate's WSL loop-mount path requires WSL2 on
the packaging host.

---

## Related

- docs/32: build bakes the shell (Task #90, the prerequisite)
- docs/04 decision log entries for #83 (hardening), #91 (prep-for-ship)
- pre-mortem memory: `reference_gose_premortem_shipblockers_2026-06-08`
- #92: the hardcoded agent admin TOKEN in `gose_vm_server.py` — a separate blocker,
  not addressed here (requires env-var migration + token rotation + git history scrub)
