"""Client library vs. a pure-Python fake pipe server (no game, no C++), plus mutation twins."""
import json
import subprocess
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))

import conformance  # noqa: E402
import nvbridge  # noqa: E402
from fake_server import FakeServer  # noqa: E402

from uniq import unique_instance  # noqa: E402


def fresh_instance():
    return unique_instance()


class PythonFake(unittest.TestCase):
    def serve(self, mutant=None):
        inst = fresh_instance()
        srv = FakeServer(inst, mutant).start()
        self.addCleanup(srv.stop)
        nv = nvbridge.NvBridge.connect(inst)
        self.addCleanup(nv.close)
        return srv, nv

    def second(self, srv):
        def mk():
            c = nvbridge.NvBridge.connect(int(srv.name.rsplit("-", 1)[1]))
            self.addCleanup(c.close)
            return c
        return mk

    def test_conformance(self):
        srv, nv = self.serve()
        self.assertTrue(conformance.run(nv, connect2=self.second(srv)))

    def test_quest_helpers(self):
        _, nv = self.serve()
        nv.exec("set GetStage 104C1C 200")
        nv.exec("set GetStageDone 104C1C 10 1")
        nv.exec("set GetQuestRunning 104C1C 0")
        self.assertEqual(nv.stage("104C1C"), 200)
        self.assertTrue(nv.stage_done("104C1C", 10))
        self.assertFalse(nv.quest_running("104C1C"))
        self.assertEqual(nv.wait_stage("104C1C", 200, 500)["value"], 200)
        with self.assertRaises(nvbridge.WaitTimeout):
            nv.wait_stage("104C1C", 7, 200)

    def test_exec_ref_on_the_wire(self):
        srv, nv = self.serve()
        sent = []
        orig = nv.send_raw
        nv.send_raw = lambda line: (sent.append(json.loads(line)), orig(line))[1]
        self.assertEqual(nv.exec("getref", ref="00103E61")["output"], "REF=00103E61")
        self.assertEqual(sent[-1]["params"], {"cmd": "getref", "ref": "00103E61"})
        nv.exec("echo plain")
        self.assertNotIn("ref", sent[-1]["params"], "exec without ref must not send a ref param")
        self.assertEqual(nv.eval("refid", ref="0x14"), 20.0)
        self.assertEqual(sent[-1]["params"], {"cmd": 'printc "NVCOOP_EVAL=%g" (refid)', "ref": "0x14"})

    def test_cli_exec_with_ref(self):
        inst = fresh_instance()
        srv = FakeServer(inst).start()
        self.addCleanup(srv.stop)
        out = subprocess.run([sys.executable, "-I", str(HERE.parent / "nvbridge.py"), "--instance", str(inst),
                              "exec", "getref", "--ref", "FalloutNV.esm:103E61"], capture_output=True, text=True, timeout=20)
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertEqual(json.loads(out.stdout), {"ok": True, "output": "REF=00103E61", "ref": "00103E61"})
        bad = subprocess.run([sys.executable, "-I", str(HERE.parent / "nvbridge.py"), "--instance", str(inst),
                              "exec", "getref", "--ref", "Nope.esm:1"], capture_output=True, text=True, timeout=20)
        self.assertEqual(bad.returncode, 2)
        self.assertIn("-32013", bad.stderr)

    def test_quit_and_cli(self):
        srv, nv = self.serve()
        self.assertEqual(nv.quit(), {"quitting": True})
        self.assertTrue(srv.quit_called.wait(2))

    def test_connect_retry_times_out_cleanly(self):
        with self.assertRaises(nvbridge.ConnectionLost):
            nvbridge.NvBridge.connect(9999, wait_s=0.3)

    def test_cli_exec(self):
        inst = fresh_instance()
        srv = FakeServer(inst).start()
        self.addCleanup(srv.stop)
        out = subprocess.run([sys.executable, "-I", str(HERE.parent / "nvbridge.py"), "--instance", str(inst),
                              "exec", "echo from-cli"], capture_output=True, text=True, timeout=20)
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertEqual(json.loads(out.stdout), {"ok": True, "output": "from-cli"})
        bad = subprocess.run([sys.executable, "-I", str(HERE.parent / "nvbridge.py"), "--instance", str(inst),
                              "call", "nope"], capture_output=True, text=True, timeout=20)
        self.assertEqual(bad.returncode, 2)
        self.assertIn("-32601", bad.stderr)


class MutationTwins(unittest.TestCase):
    """Each mutant is a broken server; the conformance suite MUST reject it."""

    def assertCaught(self, mutant):
        inst = fresh_instance()
        srv = FakeServer(inst, mutant).start()
        self.addCleanup(srv.stop)
        nv = nvbridge.NvBridge.connect(inst)
        self.addCleanup(nv.close)
        def mk():
            c = nvbridge.NvBridge.connect(inst)
            self.addCleanup(c.close)
            return c
        with self.assertRaises((AssertionError, nvbridge.BridgeError), msg=f"mutant {mutant} was NOT caught"):
            conformance.run(nv, connect2=mk)

    def test_wrong_id(self):
        self.assertCaught("wrong_id")

    def test_events_ignore_since(self):
        self.assertCaught("events_no_since")

    def test_errors_swallowed(self):
        self.assertCaught("drop_error")

    def test_lease_not_enforced(self):
        self.assertCaught("lock_ignore")

    def test_save_guard_missing(self):
        self.assertCaught("save_guard")

    def test_waitfor_timeout_lies(self):
        self.assertCaught("waitfor_no_timeout")

    def test_exec_ref_ignored(self):
        self.assertCaught("exec_ref_ignored")

    def test_client_that_drops_the_ref_is_caught(self):
        """Twin of the client side: an NvBridge whose exec forgets `ref` must fail the same conformance run."""
        class DropsRef(nvbridge.NvBridge):
            def exec(self, cmd, timeout_ms=None, *, ref=None):
                return super().exec(cmd, timeout_ms)
        inst = fresh_instance()
        srv = FakeServer(inst).start()
        self.addCleanup(srv.stop)
        nv = DropsRef.connect(inst)
        self.addCleanup(nv.close)
        with self.assertRaises((AssertionError, nvbridge.BridgeError), msg="a client that drops ref was NOT caught"):
            conformance.check_exec_ref(nv)


if __name__ == "__main__":
    unittest.main()
