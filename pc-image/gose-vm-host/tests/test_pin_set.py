"""Security test: pin_set must NOT let an unauthenticated caller set the owner PIN.

The hole: after OOBE the owner had has_pin:True but no hash yet, and the first PIN set
required no proof — so any loopback caller could POST /auth/pin/set and claim the owner
PIN (privesc). Fix: the first set now requires owner presence (_owner_ok: dev/owner token
or a hold-✕ confirm); the legit owner's PIN is hashed at OOBE before this is reachable.
Changing an existing PIN still requires the current one. These tests assert all three.

Linux/VM-gated (importing gose_vm_server has /userdata side-effects); skips elsewhere.
"""
import os
import sys
import unittest

g = None
if sys.platform.startswith("linux"):
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
    try:
        import gose_vm_server as g
    except Exception:
        g = None


@unittest.skipUnless(g is not None, "needs gose_vm_server importable on Linux (run in the VM)")
class TestPinSetGate(unittest.TestCase):
    def setUp(self):
        self._saved = {k: getattr(g, k) for k in
                       ("_accounts_load", "_owner_record", "write_json_atomic", "_owner_ok", "ACCOUNTS_F")}
        g.write_json_atomic = lambda path, obj: None        # don't touch disk
        g.ACCOUNTS_F = "/tmp/_test_accounts.json"
        with g._PIN_GUARD:                                  # clear any rate-limit carry-over
            g._PIN_FAILS["n"] = 0
            g._PIN_FAILS["until"] = 0.0

    def tearDown(self):
        for k, v in self._saved.items():
            setattr(g, k, v)

    def _owner(self, has_hash):
        rec = {"username": "owner", "role": "owner", "has_pin": True}
        if has_hash:
            salt = g.secrets.token_hex(16)
            rec.update({"pin_salt": salt, "pin_hash": g._pin_compute("11111111", salt),
                        "pin_algo": g.PIN_ALGO, "pin_len": 8})
        acc = {"users": [rec]}
        g._accounts_load = lambda: acc
        g._owner_record = lambda a=None: rec
        return rec

    def test_first_set_BLOCKED_without_owner_presence(self):
        self._owner(has_hash=False)
        g._owner_ok = lambda p: False                       # unauthenticated caller
        r = g.pin_set({"pin": "12345678"})
        self.assertFalse(r["ok"], r)
        self.assertEqual(r.get("code"), "ERR_NOT_OWNER")    # the hole is closed

    def test_first_set_allowed_with_owner_presence(self):
        rec = self._owner(has_hash=False)
        g._owner_ok = lambda p: True                        # dev/owner token or hold-✕
        r = g.pin_set({"pin": "12345678"})
        self.assertTrue(r["ok"], r)
        self.assertTrue(rec.get("pin_hash"))                # the PIN got set

    def test_unauth_pin_field_is_not_a_valid_proof(self):
        # An attacker passing {"pin": "..."} can't use it AS proof: there's no hash to verify
        # against, so _owner_ok (real) returns False and the set is refused.
        self._owner(has_hash=False)                         # restore the REAL _owner_ok
        g._owner_ok = self._saved["_owner_ok"]
        r = g.pin_set({"pin": "12345678"})
        self.assertFalse(r["ok"], r)
        self.assertEqual(r.get("code"), "ERR_NOT_OWNER")

    def test_change_still_requires_current_pin(self):
        self._owner(has_hash=True)                          # existing PIN = "11111111"
        g._owner_ok = lambda p: False                       # irrelevant for the change path
        bad = g.pin_set({"pin": "22222222", "current": "99999999"})
        self.assertFalse(bad["ok"], bad)
        self.assertIn("current PIN", bad["error"])
        with g._PIN_GUARD:
            g._PIN_FAILS["n"] = 0; g._PIN_FAILS["until"] = 0.0
        good = g.pin_set({"pin": "22222222", "current": "11111111"})
        self.assertTrue(good["ok"], good)


if __name__ == "__main__":
    unittest.main()
