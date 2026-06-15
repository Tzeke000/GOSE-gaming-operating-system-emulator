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


def _read(path):
    with open(path) as f:
        return f.read()


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


@unittest.skipUnless(g is not None, "needs gose_vm_server importable on Linux (run in the VM)")
class TestBackupRestoreRoundtrip(unittest.TestCase):
    """Full backup -> corrupt -> restore against a temp userdata tree (USERDATA param).
    Proves GOSE state round-trips AND that roms/saves are never captured or touched."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="gv-ud-")
        self._saved = {"USERDATA": g.USERDATA, "BACKUP_DIR": g.BACKUP_DIR, "_owner_ok": g._owner_ok}
        g.USERDATA = self.tmp
        g.BACKUP_DIR = os.path.join(self.tmp, "backups")
        g._owner_ok = lambda payload: True
        self._write("gose-ui/index.html", "V1")
        self._write("gose-ui/sub/a.txt", "alpha")
        self._write("system/gose/ai_tokens.json", "{}")
        self._write("roms/nes/game.nes", "ROMDATA")   # must never be backed up
        self._write("saves/game.srm", "SAVEDATA")     # must never be backed up

    def tearDown(self):
        for k, v in self._saved.items():
            setattr(g, k, v)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _write(self, rel, body):
        p = os.path.join(self.tmp, rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w") as f:
            f.write(body)

    def _members(self, archive):
        out = subprocess.run(["tar", "-tzf", archive], capture_output=True, text=True)
        return [m for m in out.stdout.splitlines() if m.strip()]

    def test_roundtrip_preserves_state_and_excludes_roms_saves(self):
        b = g.gose_backup(reason="test")
        self.assertTrue(b["ok"], b)
        members = self._members(b["path"])
        self.assertTrue(any(m.startswith("gose-ui") for m in members), members)      # captures GOSE state
        self.assertFalse(any("roms" in m or "saves" in m for m in members), members)  # never roms/saves

        # corrupt the live UI, then restore from the backup
        self._write("gose-ui/index.html", "BROKEN")
        os.remove(os.path.join(self.tmp, "gose-ui/sub/a.txt"))
        r = g.gose_restore({"file": b["file"]})
        self.assertTrue(r["ok"], r)
        self.assertEqual(_read(os.path.join(self.tmp, "gose-ui/index.html")), "V1")     # rolled back
        self.assertEqual(_read(os.path.join(self.tmp, "gose-ui/sub/a.txt")), "alpha")   # restored
        # roms + saves untouched the whole time
        self.assertEqual(_read(os.path.join(self.tmp, "roms/nes/game.nes")), "ROMDATA")
        self.assertEqual(_read(os.path.join(self.tmp, "saves/game.srm")), "SAVEDATA")


@unittest.skipUnless(g is not None, "needs gose_vm_server importable on Linux (run in the VM)")
class TestFactoryResetGate(unittest.TestCase):
    """factory_reset must refuse without owner AND without the confirm token — both
    return BEFORE any wipe, so these touch no live state."""

    def setUp(self):
        self._owner = g._owner_ok

    def tearDown(self):
        g._owner_ok = self._owner

    def test_no_owner_rejected(self):
        g._owner_ok = lambda payload: False
        r = g.gose_factory_reset({"confirm": "RESET"})
        self.assertFalse(r["ok"])
        self.assertEqual(r["code"], "ERR_NOT_OWNER")

    def test_missing_confirm_rejected(self):
        g._owner_ok = lambda payload: True
        r = g.gose_factory_reset({})   # owner ok, but no confirm token -> refuse before wiping
        self.assertFalse(r["ok"])
        self.assertIn("confirm", r["error"])


if __name__ == "__main__":
    unittest.main()
