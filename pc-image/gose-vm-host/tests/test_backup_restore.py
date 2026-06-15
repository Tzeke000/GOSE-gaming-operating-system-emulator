"""Security-confinement tests for gose_restore (owner-gated backup restore).

gose_restore validates every member of a backup archive against path-traversal and
confines extraction to GOSE state. These tests prove a malicious or malformed backup
is REJECTED before any extraction — the property that matters when a backup file came
from anywhere untrusted. They touch NOTHING live: BACKUP_DIR is pointed at a temp dir
and the one accept-path test stubs the extract subprocess.

Scope: importing gose_vm_server runs module-level startup side-effects that assume a
real /userdata, so this suite runs on Linux/the VM and skips elsewhere. The happy-path
round-trip + gose_backup/gose_factory_reset (which hardcode /userdata) need a
userdata-root parametrization (follow-up) and are not covered here.
"""
import io
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
import unittest

# Importing the server has /userdata side-effects (token resolve, config loads) that
# only make sense on the device/VM — import conditionally so a no-/userdata host skips
# cleanly instead of polluting it.
g = None
if sys.platform.startswith("linux"):
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
    try:
        import gose_vm_server as g
    except Exception:
        g = None


@unittest.skipUnless(g is not None, "needs gose_vm_server importable on Linux (run in the VM)")
class TestRestoreConfinement(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="gv-backups-")
        self._saved = {"BACKUP_DIR": g.BACKUP_DIR, "_owner_ok": g._owner_ok}
        g.BACKUP_DIR = self.tmp

    def tearDown(self):
        g.BACKUP_DIR = self._saved["BACKUP_DIR"]
        g._owner_ok = self._saved["_owner_ok"]
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _allow_owner(self):
        g._owner_ok = lambda payload: True  # reach the validation logic past the owner gate

    def _make_archive(self, name, members):
        """Write a real .tar.gz of (arcname, bytes) members into BACKUP_DIR."""
        path = os.path.join(self.tmp, name)
        with tarfile.open(path, "w:gz") as tf:
            for arc, data in members:
                ti = tarfile.TarInfo(arc)
                ti.size = len(data)
                tf.addfile(ti, io.BytesIO(data))
        return path

    # --- owner gate (real _owner_ok, empty payload) ---
    def test_no_owner_rejected(self):
        r = g.gose_restore({})
        self.assertFalse(r["ok"])
        self.assertEqual(r["code"], "ERR_NOT_OWNER")

    # --- filename validation (must be a bare *.tar.gz basename) ---
    def test_pathy_filenames_rejected(self):
        self._allow_owner()
        for bad in ("../escape.tar.gz", "/abs/x.tar.gz", "sub/dir.tar.gz"):
            r = g.gose_restore({"file": bad})
            self.assertFalse(r["ok"])
            self.assertIn("invalid backup file", r["error"])

    def test_non_targz_rejected(self):
        self._allow_owner()
        r = g.gose_restore({"file": "notanarchive.txt"})
        self.assertFalse(r["ok"])
        self.assertIn("invalid backup file", r["error"])

    def test_missing_file_rejected(self):
        self._allow_owner()
        r = g.gose_restore({"file": "ghost.tar.gz"})
        self.assertFalse(r["ok"])
        self.assertIn("backup not found", r["error"])

    # --- archive-member confinement (the core security property) ---
    def test_member_traversal_rejected(self):
        self._allow_owner()
        self._make_archive("evil.tar.gz", [("gose-ui/../../etc/passwd", b"x")])
        r = g.gose_restore({"file": "evil.tar.gz"})
        self.assertFalse(r["ok"])
        self.assertIn("unsafe path", r["error"])

    def test_member_escapes_gose_state_rejected(self):
        self._allow_owner()
        self._make_archive("escape.tar.gz", [("system/ssh/authorized_keys", b"x")])
        r = g.gose_restore({"file": "escape.tar.gz"})
        self.assertFalse(r["ok"])
        self.assertIn("escapes GOSE state", r["error"])

    # --- accept path: a clean archive passes validation (extract stubbed, no /userdata write) ---
    def test_legit_archive_passes_validation(self):
        self._allow_owner()
        self._make_archive("good.tar.gz", [("gose-ui/index.html", b"<html>")])
        orig_run = g.subprocess.run

        def fake_run(cmd, *a, **k):
            # let `tar -tzf` (list) run for real so validation sees members; stub the
            # `tar -xzf` (extract) so nothing is written to the live /userdata.
            if "-xzf" in cmd:
                return subprocess.CompletedProcess(cmd, 0, "", "")
            return orig_run(cmd, *a, **k)

        g.subprocess.run = fake_run
        try:
            r = g.gose_restore({"file": "good.tar.gz"})
        finally:
            g.subprocess.run = orig_run
        self.assertTrue(r["ok"])
        self.assertEqual(r["members"], 1)


if __name__ == "__main__":
    unittest.main()
