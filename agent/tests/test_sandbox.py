import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from gose_agent import sandbox  # noqa: E402
from gose_agent.capabilities.system import SystemCapability  # noqa: E402
from gose_agent.protocol import AgentError, ERR_DENIED  # noqa: E402


class TestGuard(unittest.TestCase):
    """The deny-list backstop (runs in-process before spawning)."""

    def test_blocks_token_read(self):
        for cmd in (
            "cat /userdata/system/gose/token",
            "head -c1 /userdata/system/gose/ai_tokens.json",
            "python3 -c \"open('/userdata/system/gose/ai_grants.json')\"",
        ):
            with self.assertRaises(sandbox.GuardDenied):
                sandbox.guard_command(cmd)

    def test_blocks_os_clobber(self):
        for cmd in (
            "rm -rf /usr",
            "rm -rf /userdata/gose-ui",
            "dd if=/dev/zero of=/boot/x",
            "echo x > /etc/passwd",
            "chmod 777 /userdata/system/gose/agent",
        ):
            with self.assertRaises(sandbox.GuardDenied):
                sandbox.guard_command(cmd)

    def test_allows_normal_commands(self):
        for cmd in (
            "echo hi",
            "uname -a",
            "ls /userdata/roms",
            "cat /proc/meminfo",
            "df -h",
        ):
            sandbox.guard_command(cmd)  # must not raise

    def test_token_path_policy_complete(self):
        # Policy must name all three protected secrets.
        self.assertIn("/userdata/system/gose/token", sandbox.TOKEN_PATHS)
        self.assertIn("/userdata/system/gose/ai_tokens.json", sandbox.TOKEN_PATHS)
        self.assertIn("/userdata/system/gose/ai_grants.json", sandbox.TOKEN_PATHS)


class TestWrapCommand(unittest.TestCase):
    def test_argv_shape(self):
        argv = sandbox.wrap_command("echo hi")
        self.assertEqual(argv[0], sys.executable)
        self.assertTrue(argv[1].endswith("sandbox.py"))
        self.assertEqual(argv[2], "--confine")
        self.assertEqual(argv[3], "echo hi")

    def test_main_usage_without_confine(self):
        self.assertEqual(sandbox.main(["sandbox.py"]), 2)


class TestSystemRunConfinement(unittest.TestCase):
    """system.run wiring: confined path wraps; unconfined path runs raw."""

    def _fake_proc(self):
        m = mock.Mock()
        m.returncode = 0
        m.stdout = "out"
        m.stderr = ""
        return m

    def test_confined_path_wraps_and_no_shell_on_linux(self):
        cap = SystemCapability(allow_shell=True, sandbox_shell=True)
        with mock.patch("gose_agent.capabilities.system.sys.platform", "linux"):
            with mock.patch("subprocess.run", return_value=self._fake_proc()) as sp:
                cap.run("echo hi")
        argv, kw = sp.call_args
        passed = argv[0]
        self.assertIsInstance(passed, list)                 # argv list, not raw str
        self.assertEqual(passed[2], "--confine")
        self.assertEqual(passed[3], "echo hi")
        self.assertFalse(kw["shell"])                       # never shell=True when confined

    def test_unconfined_path_is_raw_shell(self):
        cap = SystemCapability(allow_shell=True, sandbox_shell=False)
        with mock.patch("subprocess.run", return_value=self._fake_proc()) as sp:
            cap.run("echo hi")
        argv, kw = sp.call_args
        self.assertEqual(argv[0], "echo hi")                # raw command string
        self.assertTrue(kw["shell"])                        # owner/dev path keeps shell=True

    def test_explicit_confine_false_overrides_default(self):
        cap = SystemCapability(allow_shell=True, sandbox_shell=True)
        with mock.patch("subprocess.run", return_value=self._fake_proc()) as sp:
            cap.run("echo hi", confine=False)
        argv, kw = sp.call_args
        self.assertEqual(argv[0], "echo hi")
        self.assertTrue(kw["shell"])

    def test_confined_guard_blocks_token_read_before_spawn(self):
        cap = SystemCapability(allow_shell=True, sandbox_shell=True)
        with mock.patch("subprocess.run") as sp:
            with self.assertRaises(AgentError) as ctx:
                cap.run("cat /userdata/system/gose/token")
            self.assertEqual(ctx.exception.code, ERR_DENIED)
            sp.assert_not_called()                          # blocked before any subprocess

    def test_unconfined_guard_does_not_apply(self):
        # Owner path is intentionally unfiltered.
        cap = SystemCapability(allow_shell=True, sandbox_shell=False)
        with mock.patch("subprocess.run", return_value=self._fake_proc()) as sp:
            cap.run("cat /userdata/system/gose/token")
            sp.assert_called_once()


if __name__ == "__main__":
    unittest.main()
