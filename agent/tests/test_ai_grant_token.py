"""SB-4.2 offline test: grant → token-minted → enforcement-accepts,
revoke → enforcement-refuses, claim-delivery round-trip, and anti-forgery guards.

Pure-Python, no VM, no HTTP server started.  We import the server module's
grant/revoke/claim functions directly (patching the module-level side effects
that would otherwise try to bind a socket and start threads).
"""
import importlib
import json
import os
import sys
import tempfile
import unittest
from unittest import mock

# ---- bootstrap: patch out module-level server startup before importing ----
# gose_vm_server.py executes Server(...).serve_forever() at module level.
# We neutralise the three side-effectful calls so import succeeds without
# a real in-VM filesystem or an open socket.
_srv_patch  = mock.patch("socketserver.ThreadingTCPServer.__init__", return_value=None)
_sfr_patch  = mock.patch("socketserver.TCPServer.serve_forever",      return_value=None)
_eud_patch  = mock.patch.dict(os.environ, {"GOSE_UI_PORT": "0"})
# also suppress auto-threads that read /userdata
_thread_patch = mock.patch("threading.Thread")

_srv_patch.start()
_sfr_patch.start()
_eud_patch.start()
_thread_patch.start()

# Add the server's directory to sys.path
_SERVER_DIR = os.path.join(
    os.path.dirname(__file__), "..", "..", "pc-image", "gose-vm-host"
)
sys.path.insert(0, os.path.abspath(_SERVER_DIR))

# Suppress the "serving GOSE UI" print
with mock.patch("builtins.print"):
    import gose_vm_server as S   # noqa: E402

_srv_patch.stop()
_sfr_patch.stop()
_thread_patch.stop()
# leave _eud_patch running so GOSE_UI_PORT stays 0 for any lazy init


class TestGrantTokenRoundtrip(unittest.TestCase):
    """
    grant → token is minted in ai_tokens.json and enforcement resolves it;
    revoke → token is removed and enforcement refuses it.
    """

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.grants_f  = os.path.join(self.tmp, "ai_grants.json")
        self.tokens_f  = os.path.join(self.tmp, "ai_tokens.json")
        self.requests_f = os.path.join(self.tmp, "ai_requests.json")
        # Wire the server's path constants to temp files so no /userdata access
        self._patch_paths()

    def _patch_paths(self):
        self._patches = [
            mock.patch.object(S, "AI_GRANTS_F",   self.grants_f),
            mock.patch.object(S, "AI_TOKENS_F",   self.tokens_f),
            mock.patch.object(S, "AI_REQUESTS_F", self.requests_f),
            mock.patch.object(S, "OOBE_DONE_FLAG", os.path.join(self.tmp, "NOTEXIST")),
        ]
        for p in self._patches:
            p.start()

    def tearDown(self):
        for p in self._patches:
            p.stop()

    # ---- helpers ----

    def _grant(self, name, tier, **extra):
        payload = {"name": name, "tier": tier, **extra}
        return S.ai_grant(payload)

    def _revoke(self, name):
        return S.ai_revoke({"name": name})

    def _resolve(self, token):
        return S._ai_token_resolve(token)

    # ---- tests ----

    def test_grant_play_mints_token(self):
        r = self._grant("Wren", "play")
        self.assertTrue(r["ok"], r)
        self.assertIsNotNone(r.get("token"), "token must be returned in grant response")
        # ai_tokens.json must contain this token
        tok_map = json.load(open(self.tokens_f))
        self.assertIn(r["token"], tok_map)
        rec = tok_map[r["token"]]
        self.assertEqual(rec["name"], "Wren")
        self.assertEqual(rec["tier"], "play")

    def test_token_enforcement_accepts_after_grant(self):
        r = self._grant("Iris", "play")
        name, tier = self._resolve(r["token"])
        self.assertEqual(name, "Iris")
        self.assertEqual(tier, "play")

    def test_grant_admin_mints_token(self):
        r = self._grant("Ava", "admin")
        self.assertTrue(r["ok"])
        name, tier = self._resolve(r["token"])
        self.assertEqual(name, "Ava")
        self.assertEqual(tier, "admin")

    def test_token_stable_across_tier_change(self):
        r1 = self._grant("Agent", "observe", pair=True)
        tok_first = r1["token"]
        r2 = self._grant("Agent", "play")
        # token must be the SAME stable value — tier upgrade does not rotate it
        self.assertEqual(tok_first, r2["token"])
        _, tier = self._resolve(tok_first)
        self.assertEqual(tier, "play", "enforcement must reflect upgraded tier")

    def test_revoke_removes_token_immediately(self):
        r = self._grant("Temp", "play")
        tok = r["token"]
        self.assertEqual(self._resolve(tok)[0], "Temp")  # was valid
        self._revoke("Temp")
        self.assertIsNone(self._resolve(tok)[0], "revoked token must be refused")

    def test_downgrade_to_observe_via_grant(self):
        r1 = self._grant("Slider", "play")
        tok = r1["token"]
        # downgrade to observe (pair=True preserves the entry + token)
        r2 = self._grant("Slider", "observe", pair=True)
        self.assertEqual(tok, r2.get("token"))
        _, tier = self._resolve(tok)
        self.assertEqual(tier, "observe")

    def test_virtual_pad_ai_cannot_bypass(self):
        """Anti-forgery: verify the existing owner-gate refuses a virtual-pad source.
        We test this by calling _confirm_admin_path with a mocked controller list
        that contains only a virtual pad — it must return None."""
        with mock.patch.object(S, "_parse_controllers", return_value=[
            {"id": "vc1", "source": "virtual", "name": "AI VC 1"}
        ]), mock.patch.object(S, "_effective_admin", return_value=("vc1", None)):
            result = S._confirm_admin_path()
        self.assertIsNone(result, "virtual pad must never be the confirm device")


class TestClaimDelivery(unittest.TestCase):
    """
    The /ai/request → owner-approves → GET /ai/token claim round-trip:
    the AI receives a claim secret at request time and can collect its token
    once the owner approves.
    """

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.grants_f   = os.path.join(self.tmp, "ai_grants.json")
        self.tokens_f   = os.path.join(self.tmp, "ai_tokens.json")
        self.requests_f = os.path.join(self.tmp, "ai_requests.json")
        self._patches = [
            mock.patch.object(S, "AI_GRANTS_F",    self.grants_f),
            mock.patch.object(S, "AI_TOKENS_F",    self.tokens_f),
            mock.patch.object(S, "AI_REQUESTS_F",  self.requests_f),
            mock.patch.object(S, "OOBE_DONE_FLAG",
                              os.path.join(self.tmp, "NOTEXIST")),
        ]
        for p in self._patches:
            p.start()

    def tearDown(self):
        for p in self._patches:
            p.stop()

    def test_request_returns_claim(self):
        r = S.ai_request({"name": "ClaimBot", "tier": "play"})
        self.assertTrue(r["ok"], r)
        self.assertIn("claim", r)
        self.assertIsNotNone(r["claim"])
        self.assertGreater(len(r["claim"]), 16)

    def test_claim_pending_before_approval(self):
        r = S.ai_request({"name": "WaitBot", "tier": "play"})
        claim = r["claim"]
        result = S.ai_token_claim("WaitBot", claim)
        self.assertFalse(result["ok"])
        self.assertEqual(result.get("code"), "ERR_NOT_APPROVED")

    def test_full_request_approve_claim_cycle(self):
        # Step 1: AI requests pairing
        req = S.ai_request({"name": "PlayBot", "tier": "play"})
        self.assertTrue(req["ok"])
        claim = req["claim"]

        # Step 2: owner approves (simulates Hub UI calling ai_grant)
        grant = S.ai_grant({"name": "PlayBot", "tier": "play"})
        self.assertTrue(grant["ok"])
        self.assertIsNotNone(grant.get("token"))

        # Step 3: AI claims its token via the claim secret
        result = S.ai_token_claim("PlayBot", claim)
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["name"], "PlayBot")
        self.assertEqual(result["tier"], "play")
        self.assertEqual(result["token"], grant["token"])

        # Step 4: claim is consumed — cannot be replayed
        replay = S.ai_token_claim("PlayBot", claim)
        self.assertFalse(replay["ok"])
        self.assertEqual(replay.get("code"), "ERR_NO_CLAIM")

    def test_wrong_claim_refused(self):
        S.ai_request({"name": "SneakyBot", "tier": "play"})
        S.ai_grant({"name": "SneakyBot", "tier": "play"})
        result = S.ai_token_claim("SneakyBot", "wrongclaim00000000000000")
        self.assertFalse(result["ok"])
        self.assertEqual(result.get("code"), "ERR_CLAIM")

    def test_token_enforcement_after_claim(self):
        req = S.ai_request({"name": "EnforceMe", "tier": "admin"})
        S.ai_grant({"name": "EnforceMe", "tier": "admin"})
        result = S.ai_token_claim("EnforceMe", req["claim"])
        tok = result["token"]
        name, tier = S._ai_token_resolve(tok)
        self.assertEqual(name, "EnforceMe")
        self.assertEqual(tier, "admin")

    def test_claim_not_in_grants_roster(self):
        """claim_secret must never appear in the public grants roster listing."""
        S.ai_request({"name": "SecretBot", "tier": "play"})
        S.ai_grant({"name": "SecretBot", "tier": "play"})
        roster = S.ai_grants()
        entry = roster.get("grants", {}).get("SecretBot", {})
        self.assertNotIn("claim_secret", entry,
                         "claim_secret must not leak via /ai/grants")
        self.assertNotIn("token", entry,
                         "token must not leak via /ai/grants")

    def test_hub_pair_no_claim_no_endpoint(self):
        """An AI paired directly from the Hub (no /ai/request) has no claim
        and GET /ai/token must return ERR_NO_CLAIM, not a token."""
        S.ai_grant({"name": "HubPaired", "tier": "play", "pair": True, "via": "hub"})
        result = S.ai_token_claim("HubPaired", "anyclaim")
        self.assertFalse(result["ok"])
        self.assertEqual(result.get("code"), "ERR_NO_CLAIM")

    def test_revoke_after_claim_enforces(self):
        """Token retrieved via claim must stop working after revoke."""
        req = S.ai_request({"name": "Mortal", "tier": "play"})
        grant = S.ai_grant({"name": "Mortal", "tier": "play"})
        claimed = S.ai_token_claim("Mortal", req["claim"])
        tok = claimed["token"]
        self.assertIsNotNone(S._ai_token_resolve(tok)[0])  # valid
        S.ai_revoke({"name": "Mortal"})
        self.assertIsNone(S._ai_token_resolve(tok)[0], "token must be dead after revoke")

    def test_claim_stable_across_tier_upgrade(self):
        """A tier-upgrade grant preserves the existing claim_secret so the AI
        can still collect its token after the owner upgrades its tier."""
        req = S.ai_request({"name": "Riser", "tier": "observe"})
        claim = req["claim"]
        # Owner approves at observe
        S.ai_grant({"name": "Riser", "tier": "observe", "pair": True})
        # Owner upgrades to play
        S.ai_grant({"name": "Riser", "tier": "play"})
        # AI claims with the original claim secret — should still work
        result = S.ai_token_claim("Riser", claim)
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["tier"], "play")


if __name__ == "__main__":
    unittest.main()
