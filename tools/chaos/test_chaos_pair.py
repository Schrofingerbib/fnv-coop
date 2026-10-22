"""Game-free tests for chaos_pair.py: a pair joined through the seeded relay (fake client bridge, real relay on loopback).

Run through run_tests.py (which also runs the mutation twins). Direct: python -I tools/chaos/test_chaos_pair.py
"""
import json
import socket
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import chaos_pair as cp  # noqa: E402
import client as cc  # noqa: E402
from relay import Relay, RpcServer  # noqa: E402


class FakeNv:
    """A client copy's bridge: records the calls; coop.status says joined once coop.join was called (unless told not to)."""

    def __init__(self, join_works=True, reachable=True):
        self.calls = []
        self.join_works, self.reachable = join_works, reachable
        self.joined = True              # the pair was joined directly before the test

    def __call__(self):
        if not self.reachable:
            raise OSError("pipe nvcoop-1 does not exist")
        return self

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def call(self, method, params=None, timeout_s=35.0):
        self.calls.append((method, dict(params or {})))
        if method == "coop.leave":
            self.joined = False
        elif method == "coop.join":
            self.joined = self.join_works
        elif method == "coop.status":
            return {"role": "client", "running": True, "client": {"joined": self.joined}}
        return {"ok": True}


class FakeRelay:
    port = 41234
    log_path = "x.jsonl"
    closed = False

    def close(self):
        self.closed = True


class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t

    def sleep(self, s):
        self.t += s


INI = "[bridge]\ninstance=0\n[coop]\nenable=1\nrole=host\nport=27961 ; pair 1\n"


class PairTests(unittest.TestCase):
    def pair(self, nv, tmp, ini=INI, **kw):
        clock = Clock()
        self.relay = FakeRelay()
        made = []
        p = cp.ChaosPair(host_ini=lambda: ini, bridge=nv, relay_factory=lambda port: (made.append(port), self.relay)[1],
                         info_file=Path(tmp) / "chaos.json", sleep=clock.sleep, clock=clock, **kw)
        self.made = made
        return p

    def test_host_port(self):
        self.assertEqual(cp.host_port(INI), 27961)
        self.assertEqual(cp.host_port("[coop]\nenable=1\n"), cp.DEFAULT_PORT)
        self.assertEqual(cp.host_port("[other]\nport=5\n[coop]\nRole=host\n"), cp.DEFAULT_PORT, "only [coop] port counts")
        with self.assertRaises(ValueError):
            cp.host_port("[coop]\nport=0\n")

    def test_joined(self):
        self.assertTrue(cp.joined({"running": True, "client": {"joined": True}}))
        self.assertFalse(cp.joined({"running": True, "client": {"joined": False}}))
        self.assertFalse(cp.joined({"running": True, "host": {}}))
        self.assertFalse(cp.joined(None))

    def test_setup_joins_through_the_relay_and_waits_for_joined(self):
        with tempfile.TemporaryDirectory() as d:
            nv = FakeNv()
            p = self.pair(nv, d)
            p.setup()
            self.assertEqual(self.made, [27961], "the relay forwards to the host copy's real port")
            methods = [c[0] for c in nv.calls]
            self.assertLess(methods.index("coop.leave"), methods.index("coop.join"))
            self.assertIn(("coop.join", {"host": "127.0.0.1", "port": self.relay.port}), nv.calls, "the client joins the RELAY's port")
            self.assertEqual(methods[-1], "coop.status")
            info = json.loads((Path(d) / "chaos.json").read_text())
            self.assertEqual(info["listen"], self.relay.port)

    def test_teardown_puts_the_client_back_on_the_real_port(self):
        with tempfile.TemporaryDirectory() as d:
            nv = FakeNv()
            p = self.pair(nv, d)
            p.setup()
            nv.calls.clear()
            p.teardown()
            self.assertEqual(nv.calls, [("coop.leave", {}), ("coop.join", {"host": "127.0.0.1", "port": 27961})])
            self.assertTrue(self.relay.closed)
            p.teardown()                                   # twice is harmless
            self.assertEqual(len(nv.calls), 2)

    def test_unreachable_copy_is_an_env_problem(self):
        with tempfile.TemporaryDirectory() as d:
            p = self.pair(FakeNv(reachable=False), d)
            with self.assertRaises(cp.SetupError) as cm:
                p.setup()
            self.assertEqual(cm.exception.code, 2)
            self.assertIn("cannot reach the game instances", str(cm.exception))      # the words prove.py classes as ENV

    def test_not_joined_through_the_relay_is_a_failure(self):
        with tempfile.TemporaryDirectory() as d:
            p = self.pair(FakeNv(join_works=False), d, join_wait_s=5)
            with self.assertRaises(cp.SetupError) as cm:
                p.setup()
            self.assertEqual(cm.exception.code, 1)
            self.assertTrue(str(cm.exception).startswith("FAIL"))

    def test_run_passes_the_exit_code_on_and_always_tears_down(self):
        with tempfile.TemporaryDirectory() as d:
            nv = FakeNv()
            p = self.pair(nv, d)
            seen = []
            self.assertEqual(cp.run(p, ["tools/bots/run_fight.py", "--x"], runner=lambda argv: (seen.append(argv), 7)[1]), 7)
            self.assertTrue(seen[0][-2].endswith("run_fight.py") and seen[0][-1] == "--x")
            self.assertEqual(nv.calls[-1], ("coop.join", {"host": "127.0.0.1", "port": 27961}))
            nv2 = FakeNv()
            p2 = self.pair(nv2, d)

            def boom(argv):
                raise RuntimeError("runner died")
            with self.assertRaises(RuntimeError):
                cp.run(p2, ["x.py"], runner=boom)
            self.assertEqual(nv2.calls[-1], ("coop.join", {"host": "127.0.0.1", "port": 27961}), "put back even when the runner blew up")

    def test_failed_setup_does_not_run_the_test(self):
        with tempfile.TemporaryDirectory() as d:
            ran = []
            p = self.pair(FakeNv(reachable=False), d)
            self.assertEqual(cp.run(p, ["x.py"], runner=lambda a: ran.append(a) or 0), 2)
            self.assertEqual(ran, [])

    def test_wrapped_argv(self):
        a = cp.wrapped_argv(["tools/bots/run_fight.py", "--which", "fight"], python=["py"], repo=Path("R"))
        self.assertEqual(a, ["py", str(Path("R") / "tools/bots/run_fight.py"), "--which", "fight"])
        self.assertEqual(cp.wrapped_argv(["cmd", "/c", "echo"]), ["cmd", "/c", "echo"])


class RelayIntegration(unittest.TestCase):
    """The real seeded relay on loopback, set to bad_wifi / seed 7 through the info file exactly as run_fight.py does."""

    def run_once(self, tmp, n=400, seed=7, profile="bad_wifi"):
        echo = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        echo.bind(("127.0.0.1", 0))
        echo.settimeout(0.1)
        stop = threading.Event()

        def serve():
            while not stop.is_set():
                try:
                    data, addr = echo.recvfrom(2048)
                except socket.timeout:
                    continue
                except OSError:
                    return
                echo.sendto(data, addr)
        th = threading.Thread(target=serve, daemon=True)
        th.start()
        relay = Relay(("127.0.0.1", echo.getsockname()[1]), 0, "lan", None, Path(tmp) / "p.jsonl").start()
        rpc = RpcServer(relay, 0).start()
        info = Path(tmp) / "chaos.json"
        info.write_text(json.dumps({"listen": relay.port, "rpc": rpc.port}))
        try:
            cc.set_profile(str(info), profile, seed)
            c = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            # Every echo waits in this socket until the sends are done. Linux's default receive buffer (212992 bytes, about
            # 850 bytes per small datagram) holds only about 250 of them and drops the rest, which would read as relay loss.
            c.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 1 << 20)
            c.settimeout(0.05)
            for i in range(n):
                c.sendto(i.to_bytes(4, "big") + b"x" * 40, ("127.0.0.1", relay.port))
                time.sleep(0.002)
            got = []
            end = time.monotonic() + 1.5
            while time.monotonic() < end:
                try:
                    got.append(int.from_bytes(c.recvfrom(2048)[0][:4], "big"))
                except socket.timeout:
                    continue
            c.close()
            return relay.get_stats(), got
        finally:
            stop.set()
            rpc.shutdown()
            relay.close()
            echo.close()
            th.join(1)

    def test_bad_wifi_seed_7_loses_reorders_and_is_repeatable(self):
        with tempfile.TemporaryDirectory() as d:
            s1, got1 = self.run_once(d)
        self.assertEqual(s1["c2s"]["seen"], 400)
        self.assertGreater(s1["c2s"]["dropped"], 0, "bad_wifi drops packets")
        self.assertLess(len(got1), 400, "some datagrams never came back")
        self.assertGreater(len(got1), 300, "bad_wifi is bad, not dead")
        with tempfile.TemporaryDirectory() as d:
            s2, _got2 = self.run_once(d)
        self.assertEqual(s1["c2s"]["dropped"], s2["c2s"]["dropped"], "same seed, same packets: same drop decisions")
        with tempfile.TemporaryDirectory() as d:
            s3, _got3 = self.run_once(d, profile="hell", seed=7)
        self.assertGreater(s3["c2s"]["dropped"], s1["c2s"]["dropped"], "hell is worse than bad_wifi")


if __name__ == "__main__":
    unittest.main(verbosity=1)
