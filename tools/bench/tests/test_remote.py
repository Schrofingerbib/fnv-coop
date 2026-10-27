"""remote.RemoteHost with a fake ssh (no second PC)."""
import os
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
import remote  # noqa: E402


class FakeSsh:
    def __init__(self, pipes_ok=True, reachable=True):
        self.calls, self.pipes_ok, self.reach = [], pipes_ok, reachable

    def __call__(self, cmd, timeout):
        self.calls.append(cmd)
        joined = " ".join(cmd)
        if cmd[0] == "ssh" and joined.endswith("echo ok"):
            return SimpleNamespace(returncode=0 if self.reach else 255, stdout="ok\n" if self.reach else "", stderr="")
        if "up_out.txt" in joined:
            return SimpleNamespace(returncode=0, stdout="pipes: {0: True}\n" if self.pipes_ok else "pipes: {0: False}\n", stderr="")
        return SimpleNamespace(returncode=0, stdout="", stderr="")


class Proc:
    def __init__(self, *a, **k):
        self.args, self.dead = a, False

    def poll(self):
        return 1 if self.dead else None

    def terminate(self):
        self.dead = True


class Remote(unittest.TestCase):
    def tearDown(self):
        os.environ.pop("NVCOOP_REMOTE_0", None)

    def make(self, **kw):
        ssh = FakeSsh(**kw)
        return remote.RemoteHost(ssh="dino", ip="10.0.0.5", bridge_port=27999, log=lambda s: None, run=ssh, popen=Proc), ssh

    def test_up_starts_the_host_then_the_forwarder_and_the_tunnel_and_sets_the_env(self):
        rh, ssh = self.make()
        rh.up(boot_wait_s=5)
        text = [" ".join(c) for c in ssh.calls]
        i_host = next(i for i, t in enumerate(text) if "host_up.ps1" in t)
        i_relay = next(i for i, t in enumerate(text) if "relay_up.ps1" in t)
        self.assertLess(i_host, i_relay, "the host copy starts before the forwarder")
        self.assertTrue(any("scp" in t and "pipe_relay.py" in t for t in text), "the forwarder file is copied first")
        self.assertEqual(os.environ["NVCOOP_REMOTE_0"], "127.0.0.1:27999")
        self.assertIsNotNone(rh.tunnel)

    def test_a_host_that_never_brings_up_its_pipe_is_an_error(self):
        rh, _ = self.make(pipes_ok=False)
        orig = remote.time.sleep
        remote.time.sleep = lambda s: None
        try:
            with self.assertRaises(RuntimeError):
                rh.up(boot_wait_s=0.2)
        finally:
            remote.time.sleep = orig
        self.assertNotIn("NVCOOP_REMOTE_0", os.environ)

    def test_an_unreachable_machine_is_reported_before_anything_starts(self):
        rh, ssh = self.make(reachable=False)
        with self.assertRaises(RuntimeError) as cm:
            rh.up(boot_wait_s=1)
        self.assertIn("ssh", str(cm.exception))
        self.assertFalse(any("host_up.ps1" in " ".join(c) for c in ssh.calls))

    def test_stop_closes_the_tunnel_clears_the_env_and_runs_down(self):
        rh, ssh = self.make()
        rh.up(boot_wait_s=5)
        tunnel = rh.tunnel
        rh.stop()
        self.assertTrue(tunnel.dead)
        self.assertNotIn("NVCOOP_REMOTE_0", os.environ)
        self.assertIn("down.ps1", " ".join(ssh.calls[-1]))


if __name__ == "__main__":
    unittest.main()
