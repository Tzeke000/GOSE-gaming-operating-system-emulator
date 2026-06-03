import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from gose_agent.agent import Agent  # noqa: E402
from gose_agent.config import AgentConfig  # noqa: E402
from gose_agent.protocol import AgentError, ERR_UNKNOWN_OP, ERR_DENIED, ERR_ARGS  # noqa: E402


class TestAgentDispatch(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        # Fake roms layout: roms/psp/Game.iso
        psp = os.path.join(self.tmp, "psp")
        os.makedirs(psp)
        open(os.path.join(psp, "Cool Game.iso"), "w").close()
        self.events = []
        self.cfg = AgentConfig(force_mock=True, roms_dir=self.tmp, allow_shell=True)
        self.agent = Agent(self.cfg, emit=lambda n, d: self.events.append((n, d)))

    def test_backends_are_mock(self):
        b = self.agent.backends()
        self.assertEqual(b["input"], "mock")
        self.assertEqual(b["screen"], "mock")

    def test_ping(self):
        r = self.agent.dispatch("ping")
        self.assertTrue(r["pong"])

    def test_info_lists_ops(self):
        info = self.agent.dispatch("agent.info")
        self.assertIn("input.button", info["ops"])
        self.assertIn("games.launch", info["ops"])
        # token must never leak
        self.assertNotIn("secret", str(info["config"]))

    def test_unknown_op(self):
        with self.assertRaises(AgentError) as ctx:
            self.agent.dispatch("does.not.exist")
        self.assertEqual(ctx.exception.code, ERR_UNKNOWN_OP)

    def test_missing_arg(self):
        with self.assertRaises(AgentError) as ctx:
            self.agent.dispatch("input.button", {})  # no 'button'
        self.assertEqual(ctx.exception.code, ERR_ARGS)

    def test_input_button_recorded(self):
        self.agent.dispatch("input.button", {"button": "a", "action": "tap"})
        self.assertEqual(self.agent.input.events[-1]["kind"], "button")
        self.assertEqual(self.agent.input.events[-1]["name"], "a")

    def test_input_bad_button(self):
        with self.assertRaises(AgentError):
            self.agent.dispatch("input.button", {"button": "nope"})

    def test_system_run_echo(self):
        r = self.agent.dispatch("system.run", {"cmd": "echo hello"})
        self.assertEqual(r["code"], 0)
        self.assertIn("hello", r["stdout"])

    def test_system_run_denied_when_disabled(self):
        cfg = AgentConfig(force_mock=True, roms_dir=self.tmp, allow_shell=False)
        agent = Agent(cfg)
        with self.assertRaises(AgentError) as ctx:
            agent.dispatch("system.run", {"cmd": "echo hi"})
        self.assertEqual(ctx.exception.code, ERR_DENIED)

    def test_system_status_shape(self):
        s = self.agent.dispatch("system.status")
        for k in ("battery", "temp_c", "mem", "cpu", "uptime_s"):
            self.assertIn(k, s)

    def test_games_systems_and_list(self):
        sysres = self.agent.dispatch("games.systems")
        self.assertIn("psp", sysres["systems"])
        games = self.agent.dispatch("games.list", {"system": "psp"})
        self.assertEqual(games["count"], 1)
        self.assertEqual(games["games"][0]["name"], "Cool Game")

    def test_games_launch_dry_and_event(self):
        # No launch template -> dry mode, but still emits + tracks.
        r = self.agent.dispatch("games.launch", {"system": "psp", "game": "Cool Game"})
        self.assertEqual(r["mode"], "dry")
        self.assertEqual(self.events[-1][0], "game.launched")
        stop = self.agent.dispatch("games.stop")
        self.assertTrue(stop["stopped"])
        self.assertEqual(self.events[-1][0], "game.exited")

    def test_screen_capture_mock(self):
        r = self.agent.dispatch("screen.capture")
        self.assertEqual(r["backend"], "mock")
        self.assertTrue(r["b64"])


if __name__ == "__main__":
    unittest.main()
