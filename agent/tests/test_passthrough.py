"""Host-pad passthrough (input.pt_*) unit tests — the input-level forwarding that
replaces usb-redir for physical controllers (usb-redir on a 1 kHz pad = 4-7 s lag)."""
import glob
import json
import os
import tempfile
import threading
import unittest
import xml.etree.ElementTree as ET
from unittest import mock

from gose_agent.agent import Agent
from gose_agent.config import AgentConfig
from gose_agent.capabilities.input import (
    PassthroughManager, PT_KEYS, PT_PHYS, ensure_es_input_entry, es_binds,
    sdl_guid, udev_button_indices,
)
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


class TestEsInputAutoRegister(unittest.TestCase):
    """pt_open auto-registers the pad's SDL GUID in es_input.cfg so the launcher's
    configgen can generate binds for ANY pad brand (the exit-250 'Could not find
    controller data for GUID' fix, 2026-06-07)."""

    # The dev DualSense as seen in the guest (I: Bus=0003 Vendor=054c
    # Product=0ce6 Version=0100) — its known-good hand-written entry's GUID.
    DS = dict(name="DualSense Wireless Controller",
              vendor=0x054C, product=0x0CE6, version=0x0100, bustype=3)
    DS_GUID = "030000004c050000e60c000000010000"
    X360_GUID = "030000005e0400008e02000010010000"

    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.cfg = os.path.join(self.dir.name, "es_input.cfg")

    def ensure(self, **kw):
        args = dict(self.DS)
        args.update(kw)
        return ensure_es_input_entry(path=self.cfg, **args)

    def entries(self):
        return ET.parse(self.cfg).getroot().findall("inputConfig")

    def test_guid_matches_launcher_formula(self):
        # Must equal what configgen computes from the kernel ids (LE u16 fields).
        self.assertEqual(sdl_guid(3, 0x054C, 0x0CE6, 0x0100), self.DS_GUID)
        self.assertEqual(sdl_guid(3, 0x045E, 0x028E, 0x0110), self.X360_GUID)

    def test_absent_file_created_with_entry(self):
        r = self.ensure()
        self.assertEqual(r, {"es_input": "added", "guid": self.DS_GUID})
        root = ET.parse(self.cfg).getroot()
        self.assertEqual(root.tag, "inputList")
        (e,) = root.findall("inputConfig")
        self.assertEqual(e.get("deviceName"), "DualSense Wireless Controller")
        self.assertEqual(e.get("deviceGUID"), self.DS_GUID)
        self.assertEqual(e.get("type"), "joystick")
        # binds are computed from the pt device's actual key set (udev model)
        binds = {(i.get("name"), i.get("type"), i.get("id"), i.get("value"),
                  i.get("code")) for i in e.findall("input")}
        self.assertEqual(binds, set(es_binds(PT_KEYS)))
        # no stray tmp files from the atomic write
        self.assertEqual(sorted(os.listdir(self.dir.name)), ["es_input.cfg"])

    def test_idempotent_second_call_is_noop(self):
        self.ensure()
        before = open(self.cfg, "rb").read()
        r = self.ensure(name="renamed pad")   # same GUID keys the no-op, not the name
        self.assertEqual(r["es_input"], "present")
        self.assertEqual(open(self.cfg, "rb").read(), before)
        self.assertEqual(len(self.entries()), 1)

    def test_append_preserves_existing_entries_and_comment(self):
        with open(self.cfg, "w", encoding="utf-8") as fh:
            fh.write('<?xml version="1.0"?>\n<inputList>\n'
                     "\t<!-- hand-written: keep me -->\n"
                     '\t<inputConfig type="joystick" deviceName="Microsoft X-Box 360 pad"'
                     ' deviceGUID="%s">\n'
                     '\t\t<input name="a" type="button" id="1" value="1" code="305" />\n'
                     "\t</inputConfig>\n</inputList>\n" % self.X360_GUID)
        r = self.ensure()
        self.assertEqual(r["es_input"], "added")
        guids = [e.get("deviceGUID") for e in self.entries()]
        self.assertEqual(guids, [self.X360_GUID, self.DS_GUID])  # appended, not clobbered
        text = open(self.cfg, encoding="utf-8").read()
        self.assertIn("hand-written: keep me", text)             # comment survived

    def test_malformed_file_backed_up_and_recreated(self):
        with open(self.cfg, "w") as fh:
            fh.write("<inputList><inputConfig deviceGUID=")     # truncated/corrupt
        with self.assertLogs("gose.agent.input", level="ERROR"):
            r = self.ensure()
        self.assertEqual(r["es_input"], "added")
        (e,) = self.entries()                                    # parses clean again
        self.assertEqual(e.get("deviceGUID"), self.DS_GUID)
        self.assertTrue(glob.glob(self.cfg + ".bad-*"))          # original kept aside

    def test_wrong_root_tag_treated_as_malformed(self):
        with open(self.cfg, "w") as fh:
            fh.write("<notInputList/>")
        with self.assertLogs("gose.agent.input", level="ERROR"):
            r = self.ensure()
        self.assertEqual(r["es_input"], "added")
        self.assertEqual(ET.parse(self.cfg).getroot().tag, "inputList")
        self.assertTrue(glob.glob(self.cfg + ".bad-*"))

    def test_concurrent_registrations_all_land(self):
        # dispatch runs in a thread pool → pt_opens can race; the lock must keep
        # the file whole (XML corruption here would kill EVERY pad's launches).
        def reg(i):
            ensure_es_input_entry("pad %d" % i, i + 1, i + 1, 0, 3, path=self.cfg)
        threads = [threading.Thread(target=reg, args=(i,)) for i in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(len(self.entries()), 8)                 # parseable + complete

    def test_pt_open_wires_registration(self):
        pt = PassthroughManager(force_mock=True)
        with mock.patch.dict(os.environ, {"GOSE_ES_INPUT_CFG": self.cfg}):
            r = pt.open(dict(self.DS))
            self.assertEqual(r["es_input"], "added")
            pt.close(r["pt_id"])
            r2 = pt.open(dict(self.DS))                          # re-attach (host restart)
            self.assertEqual(r2["es_input"], "present")
        self.assertEqual(len(self.entries()), 1)
        # mock backend without the env override: never touches /userdata
        r3 = PassthroughManager(force_mock=True).open(dict(self.DS))
        self.assertEqual(r3["es_input"], "skipped")

    def test_pt_open_survives_registration_failure(self):
        pt = PassthroughManager(force_mock=True)
        with mock.patch.dict(os.environ, {"GOSE_ES_INPUT_CFG": self.cfg}), \
             mock.patch("gose_agent.capabilities.input.ensure_es_input_entry",
                        side_effect=OSError("disk full")), \
             self.assertLogs("gose.agent.input", level="ERROR"):
            r = pt.open(dict(self.DS))
        self.assertEqual(r["pt_id"], 1)                          # pad still opened
        self.assertTrue(r["es_input"].startswith("error:"))

    # The pre-fix entry shape: Xbox-360 ids copied onto a 17-key pt pad — the
    # shifted-labels bug (in-game start/select landed on the wrong buttons).
    SHIFTED = ('<?xml version="1.0"?>\n<inputList>\n'
               "\t<!-- keep this comment -->\n"
               '\t<inputConfig type="joystick" deviceName="DualSense Wireless'
               ' Controller" deviceGUID="%s">\n'
               '\t\t<input name="a" type="button" id="1" value="1" code="305" />\n'
               '\t\t<input name="start" type="button" id="7" value="1" code="315" />\n'
               '\t\t<input name="select" type="button" id="6" value="1" code="314" />\n'
               "\t</inputConfig>\n</inputList>\n")

    def test_stale_entry_corrected_in_place(self):
        with open(self.cfg, "w", encoding="utf-8") as fh:
            fh.write(self.SHIFTED % self.DS_GUID)
        r = self.ensure(name="renamed pad")
        self.assertEqual(r["es_input"], "corrected")
        (e,) = self.entries()                                    # not duplicated
        self.assertEqual(e.get("deviceName"),
                         "DualSense Wireless Controller")        # name kept
        ids = {i.get("name"): i.get("id") for i in e.findall("input")
               if i.get("type") == "button"}
        self.assertEqual(
            ids, {"a": "1", "b": "0", "x": "3", "y": "2", "pageup": "4",
                  "pagedown": "5", "select": "8", "start": "9",
                  "hotkey": "10", "l3": "11", "r3": "12"})
        text = open(self.cfg, encoding="utf-8").read()
        self.assertIn("keep this comment", text)                 # comment survived
        self.assertEqual(self.ensure()["es_input"], "present")   # now canonical

    def test_custom_keys_compute_custom_ids(self):
        r = ensure_es_input_entry("mini pad", 1, 2, 0, 3, path=self.cfg,
                                  keys=[0x130, 0x131, 0x13B])
        self.assertEqual(r["es_input"], "added")
        (e,) = self.entries()
        ids = {i.get("name"): i.get("id") for i in e.findall("input")
               if i.get("type") == "button"}
        self.assertEqual(ids, {"b": "0", "a": "1", "start": "2"})


class TestUdevIndexModel(unittest.TestCase):
    """es_input button ids must follow the consumers' ACTUAL indexing model:
    configgen copies the id verbatim into retroarch input_playerN_*_btn, and
    RetroArch's udev joypad driver (input_joypad_driver=udev) indexes buttons
    by ascending keycode over the device's real EV_KEY set. Copying the
    Xbox-360 ids onto the 17-key pt mirrors shifted select/start/hotkey/l3/r3
    by two — the 2026-06-07 in-game shifted-labels bug (proven live on
    pong1k2p: BTN_TR2 acted as start, BTN_START did nothing)."""

    # A real Xbox 360 pad's key set (xpad): no BTN_TL2/TR2 (triggers are ABS)
    # and no BTN_DPAD_* (dpad is HAT0) — also exactly EvdevInput's seat-pad set.
    XBOX_KEYS = [0x130, 0x131, 0x133, 0x134, 0x136, 0x137,
                 0x13A, 0x13B, 0x13C, 0x13D, 0x13E]

    def test_pt_indices_ascending_keycode(self):
        self.assertEqual(udev_button_indices(PT_KEYS), {
            0x130: 0, 0x131: 1, 0x133: 2, 0x134: 3,     # south east north west
            0x136: 4, 0x137: 5, 0x138: 6, 0x139: 7,     # tl tr tl2 TR2(=7!)
            0x13A: 8, 0x13B: 9, 0x13C: 10,              # select START(=9) mode
            0x13D: 11, 0x13E: 12,                       # thumbl thumbr
            0x220: 13, 0x221: 14, 0x222: 15, 0x223: 16,  # dpad keys
        })

    def test_pt_binds_not_xbox_shifted(self):
        ids = {n: i for n, t, i, v, c in es_binds(PT_KEYS) if t == "button"}
        # unshifted (same as the Xbox entry): face buttons + shoulders
        self.assertEqual(
            {k: ids[k] for k in ("a", "b", "x", "y", "pageup", "pagedown")},
            {"a": "1", "b": "0", "x": "3", "y": "2",
             "pageup": "4", "pagedown": "5"})
        # THE FIX: +2 vs the Xbox entry (TL2/TR2 occupy 6/7 on a 17-key pad)
        self.assertEqual(
            {k: ids[k] for k in ("select", "start", "hotkey", "l3", "r3")},
            {"select": "8", "start": "9", "hotkey": "10",
             "l3": "11", "r3": "12"})

    def test_seat_pads_match_stock_xbox_entry(self):
        # The AI seat pads (EvdevInput) expose XBOX_KEYS, so the STOCK
        # "Microsoft Xbox 360 pad" entry's ids are already correct for them —
        # which is why the seats need no corrected entry of their own.
        ids = {n: i for n, t, i, v, c in es_binds(self.XBOX_KEYS)
               if t == "button"}
        self.assertEqual(ids, {"a": "1", "b": "0", "x": "3", "y": "2",
                               "pageup": "4", "pagedown": "5", "select": "6",
                               "start": "7", "hotkey": "8", "l3": "9",
                               "r3": "10"})

    def test_key_updown_block_scanned_first(self):
        # udev scans KEY_UP..KEY_DOWN before BTN_MISC..KEY_MAX.
        self.assertEqual(udev_button_indices([0x130, 103, 108]),
                         {103: 0, 108: 1, 0x130: 2})

    def test_missing_keycode_drops_bind(self):
        rows = es_binds([0x130, 0x131])                  # only south + east
        names = [n for n, t, i, v, c in rows if t == "button"]
        self.assertEqual(sorted(names), ["a", "b"])
        # axis/hat rows always present (same ABS set on every pt device)
        self.assertEqual(len([r for r in rows if r[1] in ("axis", "hat")]), 10)


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
