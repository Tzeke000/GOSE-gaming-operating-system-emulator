# 35 — Crash recovery & safe mode (stranger's-hands resilience, gap J1)

**Status:** built + **test-covered** 2026-06-15. The recovery loop lives in
`pc-image/gose-vm-host/watchdog.py`; the boot-counter contract is shared with
`gose_vm_server.py`. Suite: `pc-image/gose-vm-host/tests/test_watchdog.py` (14 tests;
rollback cases need `rsync`, so they run fully on Linux/the VM and skip on a no-rsync
host). This doc is the mechanism of record.

---

## The problem

A GOSE device may end up in someone's hands who never touched the dev setup. If a
bad UI push (or a corrupt update) makes the shell crash on every boot, the device
must **not** become a black brick. It has to detect the crash loop and fall back to
something usable — ideally the last interface that worked, otherwise a plain
recovery page — with **no terminal, no dev token, no Wi-Fi** required.

## The boot-success counter contract

A single integer at `/userdata/gose-ui/.boot_attempts` is the crash-loop signal.
Three parties touch it, and only these:

- **`watchdog.py` INCREMENTS** it each time it has to (re)start the UI server
  (`bump_attempts`, in `main()` when `gose_vm_server.py` is not alive).
- **`gose_vm_server.py` CLEARS it to 0** the moment it serves the home page — that
  write *is* the proof "this boot reached a working UI" (`clear_boot_attempts`).
- A crash loop never reaches home, so the count only climbs. At
  `THRESHOLD` (default **3**) the watchdog trips safe mode.

`read_attempts()` distinguishes **`None`** (file absent → never started / unknown)
from **`0`** (explicitly cleared → known-good), so a fresh device is never mistaken
for a recovered one.

## Known-good snapshot + rollback

On each confirmed-good boot (`attempts == 0` **and** `/health` passes), the watchdog
mirrors the live UI dir to `/userdata/gose-ui.prev` via `rsync --delete`
(`snapshot_prev`), excluding volatile files (logs, caches, `.boot_attempts`,
`.safe_mode`). `restore_prev()` rsyncs `.prev` back over the live dir. The snapshot
is taken **once per boot streak** so a flapping device can't overwrite a good
snapshot with a bad one mid-loop.

## Safe mode

When the counter reaches `THRESHOLD`, `enter_safe_mode()`:

1. **Auto-restore, once.** If a `.prev` snapshot exists and we haven't already
   auto-restored this run, silently `restore_prev()`, clear the counter, drop the
   `.safe_mode` marker, and return — the session loop relaunches the restored UI.
   This is the common case and the user may never see a page.
2. **Park on a static page.** If there's no snapshot (or the auto-restore already
   happened and we tripped again), the watchdog binds a tiny stdlib HTTP server on
   the UI port (`_SafeServer`/`_SafeHandler`) serving a self-contained, **controller-
   and-keyboard navigable** recovery page (`SAFE_HTML`). It offers:
   - `POST /boot/restore` → roll back to `.prev` (if any), clear the counter, exit.
   - `POST /boot/retry` → clear the counter and exit to try a fresh start.
   `GET /health` reports `{"ok": true, "safe_mode": true}` so liveness checks see it.
   The server releases its socket (`server_close`) before exiting so the UI server
   can rebind the port without an EADDRINUSE race.

Auto-restore is attempted **at most once** per watchdog run (`_restored_once`) so a
snapshot that is itself bad can't cause an infinite restore→crash→restore loop — the
second trip parks on the page for a human instead.

## Related: kiosk freeze watchdog

Separate from the crash-loop path: the kiosk JS posts `POST /kiosk/tick` every 30s;
the server writes `/userdata/gose-ui/.kiosk_tick`. If the kiosk process is alive but
the tick is stale > `TICK_STALE_S` (120s) and no game is running, the watchdog kills
the kiosk so the emulationstation-standalone display loop relaunches it fresh. This
catches a frozen JS scheduler that the boot counter (process still alive) would miss.

## Testing

`watchdog.py` is env-parametrized (`GOSE_WD_UI_DIR`, `GOSE_WD_UI_PORT`,
`GOSE_WD_THRESHOLD`, `GOSE_WD_PREV_DIR`, …) so the whole path runs against throwaway
dirs/ports without touching the live UI. The suite overrides those module constants
per-test and exercises: the counter semantics (None vs 0 vs climbing), snapshot↔restore
round-trip + volatile-exclude, the safe-mode page (`/`, `/health`, `/boot/retry`,
restore-without-snapshot, 404), auto-restore-once, and the park-until-human flow.

```
cd pc-image/gose-vm-host && python3 -m unittest discover -s tests -v
```

On a host without `rsync` (Windows dev box) the 4 rollback cases skip; run inside the
VM (`/usr/bin/rsync` present) for full 14/14 coverage.
