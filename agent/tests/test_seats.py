"""Seat manager + seat-arbitration unit tests (multiplayer seats)."""
import json
import os
import tempfile
import unittest
from unittest import mock

from gose_agent.agent import Agent
from gose_agent.config import AgentConfig
from gose_agent.capabilities.input import SeatManager, MockInput
from gose_agent.protocol import AgentError
from gose_agent import server as srv


def mock_agent():
    cfg = AgentConfig()
    cfg.force_mock = True
    return Agent(cfg)


class TestSeatManager(unittest.TestCase):
    def setUp(self):
        self.sm = SeatManager(MockInput)

    def test_seat1_exists_and_is_default(self):
        self.assertEqual(self.sm.seats()["seats"], [1])
        self.sm.button("a", "tap")  # default seat
        self.assertEqual(self.sm._seats[1].events[-1]["name"], "a")

    def test_open_close(self):
        self.assertEqual(self.sm.seat_open(2)["seats"], [1, 2])
        self.sm.button("b", "tap", seat=2)
        self.assertEqual(self.sm._seats[2].events[-1]["name"], "b")
        self.assertEqual(self.sm._seats[1].events, [])  # seat 1 untouched
        self.assertEqual(self.sm.seat_close(2)["seats"], [1])

    def test_seat1_permanent(self):
        with self.assertRaises(AgentError):
            self.sm.seat_close(1)

    def test_unopened_seat_rejected(self):
        with self.assertRaises(AgentError):
            self.sm.button("a", "tap", seat=3)

    def test_max_seats(self):
        with self.assertRaises(AgentError):
            self.sm.seat_open(5)

    def test_events_back_compat(self):
        """agent.input.events must still reach seat 1's mock recorder."""
        self.sm.button("x", "tap")
        self.assertEqual(self.sm.events[-1]["name"], "x")


class TestSeatOps(unittest.TestCase):
    def test_dispatch_routes_seat(self):
        a = mock_agent()
        a.dispatch("input.seat_open", {"seat": 2})
        a.dispatch("input.button", {"button": "up", "action": "press", "seat": 2})
        self.assertEqual(a.input._seats[2].events[-1]["action"], "press")
        self.assertEqual(a.input._seats[1].events, [])


class TestPinSeat(unittest.TestCase):
    """server._pin_seat: an AI token with a seat assignment is pinned to it."""

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

    def test_input_pinned_to_assigned_seat(self):
        msg = {"token": "tok-pinned", "op": "input.button"}
        out = srv.AgentServer._pin_seat(msg, {"button": "up", "seat": 1})
        self.assertEqual(out["seat"], 2)

    def test_unassigned_token_not_pinned(self):
        msg = {"token": "tok-free", "op": "input.button"}
        out = srv.AgentServer._pin_seat(msg, {"button": "up", "seat": 1})
        self.assertEqual(out.get("seat"), 1)

    def test_cannot_manage_other_seat(self):
        msg = {"token": "tok-pinned", "op": "input.seat_open"}
        with self.assertRaises(srv.P.AgentError):
            srv.AgentServer._pin_seat(msg, {"seat": 3})

    def test_can_manage_own_seat(self):
        msg = {"token": "tok-pinned", "op": "input.seat_open"}
        out = srv.AgentServer._pin_seat(msg, {"seat": 2})
        self.assertEqual(out["seat"], 2)

    def test_non_input_ops_untouched(self):
        msg = {"token": "tok-pinned", "op": "state.read"}
        out = srv.AgentServer._pin_seat(msg, {"profile": "x"})
        self.assertNotIn("seat", out)


if __name__ == "__main__":
    unittest.main()
