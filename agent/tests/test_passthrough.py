"""Host-pad passthrough (input.pt_*) unit tests — the input-level forwarding that
replaces usb-redir for physical controllers (usb-redir on a 1 kHz pad = 4-7 s lag)."""
import json
import os
import tempfile
import unittest
from unittest import mock

from gose_agent.agent import Agent
from gose_agent.config import AgentConfig
from gose_agent.capabilities.input import PassthroughManager, PT_PHYS
from gose_agent.protocol import AgentError
from gose_agent import server as srv

EV_KEY, EV_ABS = 1, 3
BTN_SOUTH, ABS_HAT0Y = 304, 17


def mock_agent():
    cfg = AgentConfig()
    cfg.force_mock = True
    return Agent(cfg)


class TestPassthroughManager(unittest.TestCase):
    def setUp(self):
        self.pt = PassthroughManager(force_mock=True)

    def open(self, **kw):
        args = {"name": "Sony DualSense", "vendor": 0x054C, "product": 0x0CE6,
                "version": 0x8111, "bustype": 3}
        args.update(kw)
        return self.pt.open(args)

    def test_open_returns_id_and_identity(self):
        r = self.open()
        self.assertEqual(r["pt_id"], 1)
        self.assertEqual(r["phys"], PT_PHYS)
        self.assertEqual(r["backend"], "mock")
        dev = self.pt._devices[1]
        self.assertEqual((dev.vendor, dev.product, dev.version, dev.bustype),
                         (0x054C, 0x0CE6, 0x8111, 3))

    def test_event_injects_batch(self):
        r = self.open()
        out = self.pt.event(r["pt_id"], [
            {"type": EV_KEY, "code": BTN_SOUTH, "value": 1},
            {"type": EV_ABS, "code": ABS_HAT0Y, "value": -1},
        ])
        self.assertEqual(out, {"done": True, "n": 2})
        self.assertEqual(self.pt._devices[1].events,
                         [(EV_KEY, BTN_SOUTH, 1), (EV_ABS, ABS_HAT0Y, -1)])

    def test_event_rejects_bad_type_and_shape(self):
        r = self.open()
        with self.assertRaises(AgentError):
            self.pt.event(r["pt_id"], [{"type": 0, "code": 1, "value": 1}])  # EV_SYN
        with self.assertRaises(AgentError):
            self.pt.event(r["pt_id"], [])
        with self.assertRaises(AgentError):
            self.pt.event(r["pt_id"], [{"code": 1, "value": 1}])             # no type

    def test_unknown_pt_id(self):
        with self.assertRaises(AgentError):
            self.pt.event(7, [{"type": EV_KEY, "code": BTN_SOUTH, "value": 1}])
        with self.assertRaises(AgentError):
            self.pt.close(7)

    def test_close_frees_slot(self):
        r = self.open()
        out = self.pt.close(r["pt_id"])
        self.assertEqual(out["open"], [])
        self.assertEqual(self.pt.list()["open"], [])

    def test_max_devices(self):
        for _ in range(PassthroughManager.MAX):
            self.open()
        with self.assertRaises(AgentError):
            self.open()

    def test_vendor_required_and_bounded(self):
        with self.assertRaises(AgentError):
            self.pt.open({"name": "x", "product": 1})           # vendor missing
        with self.assertRaises(AgentError):
            self.open(vendor=0x10000)                            # > 16 bit

    def test_defaults_version_and_bustype(self):
        r = self.pt.open({"name": "pad", "vendor": 1, "product": 2})
        dev = self.pt._devices[r["pt_id"]]
        self.assertEqual((dev.version, dev.bustype), (0, 3))     # BUS_USB default


class TestPassthroughOps(unittest.TestCase):
    """The ops are wired through Agent.dispatch (what the TCP server calls)."""

    def test_dispatch_roundtrip(self):
        a = mock_agent()
        r = a.dispatch("input.pt_open",
                       {"name": "Pad", "vendor": 1, "product": 2, "version": 3, "bustype": 3})
        a.dispatch("input.pt_event", {"pt_id": r["pt_id"], "events": [
            {"type": EV_KEY, "code": BTN_SOUTH, "value": 1}]})
        self.assertEqual(a.input.pt._devices[r["pt_id"]].events, [(EV_KEY, BTN_SOUTH, 1)])
        self.assertEqual(a.dispatch("input.pt_list", {})["open"][0]["pt_id"], r["pt_id"])
        a.dispatch("input.pt_close", {"pt_id": r["pt_id"]})
        self.assertEqual(a.dispatch("input.pt_list", {})["open"], [])

    def test_op_tiers(self):
        self.assertEqual(srv.OP_TIER["input.pt_open"], "play")
        self.assertEqual(srv.OP_TIER["input.pt_event"], "play")
        self.assertEqual(srv.OP_TIER["input.pt_close"], "play")
        self.assertEqual(srv.OP_TIER["input.pt_list"], "observe")


class TestPinSeatDeniesPt(unittest.TestCase):
    """A seat-assigned guest AI may not create/drive passthrough pads."""

    def setUp(self):
        fd, self.path = tempfile.mkstemp(suffix=".json")
        os.close(fd)
        with open(self.path, "w") as fh:
            json.dump({"tok-pinned": {"name": "T", "tier": "play", "seat": 2},
                       "tok-free": {"name": "F", "tier": "play"}}, fh)
        self.patch = mock.patch.object(srv, "_AI_TOKENS_PATH", self.path)
        self.patch.start()

    def tearDown(self):
        self.patch.stop()
        os.unlink(self.path)

    def test_pinned_token_denied_pt(self):
        for op in ("input.pt_open", "input.pt_event", "input.pt_close"):
            with self.assertRaises(srv.P.AgentError):
                srv.AgentServer._pin_seat({"token": "tok-pinned", "op": op}, {})

    def test_unpinned_token_untouched(self):
        out = srv.AgentServer._pin_seat({"token": "tok-free", "op": "input.pt_open"},
                                        {"name": "x"})
        self.assertEqual(out, {"name": "x"})


if __name__ == "__main__":
    unittest.main()
