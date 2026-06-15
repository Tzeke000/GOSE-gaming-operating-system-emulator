"""Crash-recovery / safe-mode tests for the GOSE host watchdog.

watchdog.py is the "stranger's-hands resilience" path (gap J1): a boot-success
counter trips SAFE MODE on a crash-looping UI push so the device is never a black
brick. The module is env-parametrized on purpose so this path can be exercised on
throwaway dirs/ports without touching the live UI — this is that exercise.

The boot-counter + safe-mode HTTP server are pure-Python and run anywhere. The
snapshot/restore rollback uses rsync, so those cases skip cleanly where rsync is
absent (Windows dev host) and run fully on Linux (the VM / CI).
"""
import json
import os
import shutil
import sys
import tempfile
import threading
import time
import unittest
import urllib.request

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import watchdog as wd  # noqa: E402

_HAS_RSYNC = shutil.which("rsync") is not None


def _free_port():
    import socket
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _post(port, path):
    req = urllib.request.Request("http://127.0.0.1:%d%s" % (port, path), method="POST")
    with urllib.request.urlopen(req, timeout=4) as r:
        return r.status, json.loads(r.read().decode())


def _get(port, path):
    with urllib.request.urlopen("http://127.0.0.1:%d%s" % (port, path), timeout=4) as r:
        return r.status, r.read().decode()


def _read(path):
    with open(path) as f:
        return f.read()


class _WDTemp(unittest.TestCase):
    """Point every watchdog path constant at a throwaway tree so nothing live is touched."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="wd-ui-")
        self.prev = tempfile.mkdtemp(prefix="wd-prev-")
        os.rmdir(self.prev)  # start absent; snapshot_prev must create it
        # Override module globals (functions read these by name) — same idiom as test_audit.
        self._saved = {k: getattr(wd, k) for k in
                       ("UI_DIR", "PREV_DIR", "BOOT_F", "SAFE_F", "UI_PORT", "THRESHOLD",
                        "_restored_once")}
        wd.UI_DIR = self.tmp
        wd.PREV_DIR = self.prev
        wd.BOOT_F = self.tmp + "/.boot_attempts"
        wd.SAFE_F = self.tmp + "/.safe_mode"
        wd.UI_PORT = _free_port()
        wd.THRESHOLD = 3
        wd._restored_once = False

    def tearDown(self):
        for k, v in self._saved.items():
            setattr(wd, k, v)
        for d in (self.tmp, self.prev):
            shutil.rmtree(d, ignore_errors=True)


class TestBootCounter(_WDTemp):
    def test_missing_reads_none(self):
        self.assertIsNone(wd.read_attempts())  # never-started == unknown, distinct from 0

    def test_bump_climbs(self):
        self.assertEqual(wd.bump_attempts(), 1)
        self.assertEqual(wd.bump_attempts(), 2)
        self.assertEqual(wd.read_attempts(), 2)

    def test_clear_writes_zero(self):
        wd.bump_attempts()
        wd.clear_attempts()
        self.assertEqual(wd.read_attempts(), 0)  # explicit 0 == "this boot is good"

    def test_bump_after_clear(self):
        wd.clear_attempts()
        self.assertEqual(wd.bump_attempts(), 1)  # 0 + 1, not None-handling


@unittest.skipUnless(_HAS_RSYNC, "snapshot/restore needs rsync")
class TestSnapshotRestore(_WDTemp):
    def _write(self, name, body):
        with open(os.path.join(self.tmp, name), "w") as f:
            f.write(body)

    def test_snapshot_then_restore_roundtrip(self):
        self._write("index.html", "GOOD")
        self.assertTrue(wd.snapshot_prev())
        self.assertEqual(_read(os.path.join(self.prev, "index.html")), "GOOD")
        # UI goes bad, restore brings the known-good copy back
        self._write("index.html", "BROKEN")
        self.assertTrue(wd.restore_prev())
        self.assertEqual(_read(os.path.join(self.tmp, "index.html")), "GOOD")

    def test_snapshot_excludes_volatile(self):
        self._write("index.html", "x")
        self._write(".boot_attempts", "5")
        self._write("server.log", "noise")
        self.assertTrue(wd.snapshot_prev())
        self.assertTrue(os.path.exists(os.path.join(self.prev, "index.html")))
        self.assertFalse(os.path.exists(os.path.join(self.prev, ".boot_attempts")))
        self.assertFalse(os.path.exists(os.path.join(self.prev, "server.log")))

    def test_restore_noop_without_snapshot(self):
        self.assertFalse(wd.restore_prev())  # empty/absent PREV_DIR => False, never a crash


class TestSafeModeServer(_WDTemp):
    """The static safe-mode page a user lands on after a crash loop."""

    def _serve(self):
        srv = wd._SafeServer(("127.0.0.1", wd.UI_PORT), wd._SafeHandler)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        self.addCleanup(srv.server_close)   # close the listening socket
        self.addCleanup(srv.shutdown)       # stop serve_forever first (cleanups run LIFO)
        for _ in range(150):                # wait until it actually accepts — avoids a serve-thread bind race
            try:
                _get(wd.UI_PORT, "/health"); break
            except OSError:
                time.sleep(0.02)
        return srv

    def test_get_serves_safe_page(self):
        self._serve()
        status, body = _get(wd.UI_PORT, "/")
        self.assertEqual(status, 200)
        self.assertIn("Safe Mode", body)

    def test_health_reports_safe_mode(self):
        self._serve()
        status, body = _get(wd.UI_PORT, "/health")
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body), {"ok": True, "safe_mode": True})

    def test_retry_clears_counter_and_exits(self):
        wd.bump_attempts(); wd.bump_attempts()
        srv = self._serve()
        status, body = _post(wd.UI_PORT, "/boot/retry")
        self.assertEqual(status, 200)
        self.assertTrue(body["ok"])
        self.assertEqual(wd.read_attempts(), 0)        # counter reset so the next boot is a fresh streak
        self.assertTrue(srv._exit)                     # server asked to stop -> session loop relaunches UI

    def test_restore_without_prev_still_recovers(self):
        # No snapshot exists: restore can't roll back, but must NOT brick — it clears the
        # counter and tells the user it's retrying. (rsync not required for this branch.)
        srv = self._serve()
        status, body = _post(wd.UI_PORT, "/boot/restore")
        self.assertEqual(status, 200)
        self.assertFalse(body["ok"])
        self.assertIn("No previous", body["msg"])
        self.assertEqual(wd.read_attempts(), 0)
        self.assertTrue(srv._exit)

    def test_unknown_post_404s(self):
        self._serve()
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            _post(wd.UI_PORT, "/nope")
        self.assertEqual(ctx.exception.code, 404)


class TestEnterSafeMode(_WDTemp):
    @unittest.skipUnless(_HAS_RSYNC, "auto-restore branch needs rsync")
    def test_auto_restores_once_when_snapshot_exists(self):
        # A known-good snapshot exists -> first trip silently rolls back and resumes (no static page).
        with open(os.path.join(self.tmp, "index.html"), "w") as f:
            f.write("GOOD")
        self.assertTrue(wd.snapshot_prev())
        with open(os.path.join(self.tmp, "index.html"), "w") as f:
            f.write("BROKEN")
        with open(wd.SAFE_F, "w") as f:
            f.write("tripped")
        wd.enter_safe_mode()  # returns immediately via the auto-restore path
        self.assertTrue(wd._restored_once)
        self.assertEqual(_read(os.path.join(self.tmp, "index.html")), "GOOD")
        self.assertEqual(wd.read_attempts(), 0)
        self.assertFalse(os.path.exists(wd.SAFE_F))    # safe-mode marker cleared on recovery

    def test_parks_on_static_page_when_no_snapshot(self):
        # No rollback target -> enter_safe_mode blocks serving the static page until a human
        # chooses. Run it in a thread, hit /boot/retry, and confirm it unblocks + cleans up.
        wd.bump_attempts(); wd.bump_attempts(); wd.bump_attempts()
        t = threading.Thread(target=wd.enter_safe_mode, daemon=True)
        t.start()
        # wait for the safe server to come up on UI_PORT
        for _ in range(100):
            try:
                _get(wd.UI_PORT, "/health"); break
            except OSError:
                time.sleep(0.05)
        else:
            self.fail("safe-mode server did not come up")
        self.assertTrue(os.path.exists(wd.SAFE_F))     # marker written while parked
        status, body = _post(wd.UI_PORT, "/boot/retry")
        self.assertTrue(body["ok"])
        t.join(timeout=5)
        self.assertFalse(t.is_alive())                 # human action let it leave safe mode
        self.assertFalse(os.path.exists(wd.SAFE_F))    # marker cleared on exit


if __name__ == "__main__":
    unittest.main()
