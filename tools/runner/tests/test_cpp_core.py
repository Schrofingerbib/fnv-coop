"""The real C++ bridge core (out/L1/nvbridge_fakehost.exe) driven through the real Python client.

Needs `tools\runner\build_l1.cmd` first; a missing exe is a test ERROR (set NVCOOP_NO_CPP=1 to skip C++ tests explicitly).
"""
import subprocess
import sys
import threading
import time
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))

import conformance  # noqa: E402
import nvbridge  # noqa: E402

import binaries  # noqa: E402

GOOD = "nvbridge_fakehost"
MUTANT = "nvbridge_fakehost_mutant"
MUTANT_LOCK = "nvbridge_fakehost_mutant_lock"
MUTANT_SAVE = "nvbridge_fakehost_mutant_save"
MUTANT_EXECREF = "nvbridge_fakehost_mutant_execref"
from uniq import unique_instance  # noqa: E402


class Host:
    def __init__(self, exe, *args):
        self.instance = unique_instance()
        self.p = subprocess.Popen([str(binaries.get(exe)), "--instance", str(self.instance), *args], stdout=subprocess.PIPE,
                                  stderr=subprocess.STDOUT, text=True, cwd=str(binaries.tmpdir()))
        line = self.p.stdout.readline()
        if not line.startswith("READY"):
            self.kill()
            raise RuntimeError(f"fake host did not start: {line!r}")

    def connect(self):
        return nvbridge.NvBridge.connect(self.instance, wait_s=5)

    def kill(self):
        if self.p.poll() is None:
            self.p.kill()
        self.p.wait(5)
        self.p.stdout.close()


def start(testcase, exe=GOOD, *args):
    h = Host(exe, *args)
    testcase.addCleanup(h.kill)
    return h


class CppCore(unittest.TestCase):
    def test_conformance(self):
        h = start(self)
        with h.connect() as nv:
            self.assertTrue(conformance.run(nv, connect2=h.connect))

    def test_ping_reports_main_loop(self):
        h = start(self)
        with h.connect() as nv:
            time.sleep(0.2)
            p = nv.ping()
            self.assertGreater(p["frames"], 0)
            self.assertTrue(0 <= p["mainLoopAgeMs"] < 1000)
            self.assertEqual(p["instance"], h.instance)

    def test_ping_answers_while_main_thread_is_stalled(self):
        h = start(self)
        with h.connect() as a, h.connect() as b:
            t = threading.Thread(target=lambda: a.exec("stall 1200"))
            t.start()
            time.sleep(0.3)
            t0 = time.monotonic()
            p = b.ping()
            self.assertLess(time.monotonic() - t0, 0.5, "ping must not wait for the main thread")
            self.assertGreater(p["mainLoopAgeMs"], 200)
            t.join()

    def test_main_thread_never_services_request(self):
        h = start(self)
        with h.connect() as nv:
            with self.assertRaises(nvbridge.RpcError) as cm:
                nv.call("exec", {"cmd": "stall 4000", "timeoutMs": 200}, timeout_s=10)
            self.assertEqual(cm.exception.code, -32001)
            time.sleep(4.0)  # let the stall finish; the bridge must recover
            self.assertEqual(nv.exec("echo alive")["output"], "alive")

    def test_exec_ref_runs_the_line_on_the_reference(self):
        h = start(self)
        with h.connect() as nv:
            conformance.check_exec_ref(nv)
            # the real core reports the plugin, the runtime id and the form type in its refusals
            with self.assertRaises(nvbridge.RpcError) as cm:
                nv.exec("getref", ref="FalloutNV.esm:F")
            self.assertEqual(cm.exception.code, -32013)
            self.assertIn("form 0000000F, form type 0x1F", cm.exception.message)
            self.assertIn("only a REFR, ACHR or ACRE", cm.exception.message)
            with self.assertRaises(nvbridge.RpcError) as cm:
                nv.exec("getref", ref="FalloutNV.esm:1000000")
            self.assertIn("at most FFFFFF", cm.exception.message)

    def test_custom_methods(self):
        h = start(self)
        with h.connect() as nv:
            self.assertEqual(nv.call("test.echo", {"a": [1, 2, {"b": None}]}), {"echo": {"a": [1, 2, {"b": None}]}})
            self.assertEqual(nv.call("test.echoThread", {"x": 1}), {"echo": {"x": 1}})
            with self.assertRaises(nvbridge.RpcError) as cm:
                nv.call("test.fail")
            self.assertEqual(cm.exception.code, -32000)
            self.assertIn("deliberate", cm.exception.message)

    def test_state_provider(self):
        h = start(self, GOOD, "--state-provider")
        with h.connect() as nv:
            self.assertEqual(nv.state(["quests", "globals"]), {"sections": "quests,globals"})
            self.assertEqual(nv.state(), {"sections": ""})

    def test_autoload(self):
        h = start(self, GOOD, "--autoload", "quicksave1", "--autoload-frames", "10")
        with h.connect() as nv:
            nv.wait_event("autoload.start", timeout_s=5)
            nv.wait_event("autoload.done", timeout_s=5)
            self.assertEqual(nv.ping()["loadedCount"], 1)

    def test_autoload_failure_reported(self):
        h = start(self, GOOD, "--autoload", "missing", "--autoload-frames", "10")
        with h.connect() as nv:
            ev = nv.wait_event("autoload.failed", timeout_s=5)
            self.assertIn("no such save", ev["data"]["error"])

    def test_concurrent_clients(self):
        h = start(self)
        errors = []

        def work(k):
            try:
                with h.connect() as nv:
                    for i in range(40):
                        out = nv.exec(f"echo c{k}-{i}")["output"]
                        assert out == f"c{k}-{i}", out
            except Exception as e:  # noqa: BLE001
                errors.append(repr(e))

        ts = [threading.Thread(target=work, args=(k,)) for k in range(4)]
        [t.start() for t in ts]
        [t.join(30) for t in ts]
        self.assertEqual(errors, [])

    def test_instances_are_isolated(self):
        a, b = start(self), start(self)
        with a.connect() as na, b.connect() as nb:
            na.exec("set v 1")
            nb.exec("set v 2")
            self.assertEqual(na.wait_for("v", op="==", value=1, timeout_ms=500)["value"], 1)
            self.assertEqual(nb.wait_for("v", op="==", value=2, timeout_ms=500)["value"], 2)
            self.assertNotEqual(na.ping()["pid"], 0)

    def test_second_server_on_same_instance_does_not_hijack(self):
        h = start(self)
        p2 = subprocess.Popen([str(binaries.get(GOOD)), "--instance", str(h.instance)], stdout=subprocess.PIPE, text=True, cwd=str(binaries.tmpdir()))
        self.addCleanup(lambda: (p2.kill(), p2.wait(5), p2.stdout.close()))
        p2.stdout.readline()
        time.sleep(0.3)
        with h.connect() as nv:
            self.assertEqual(nv.ping()["pid"], h.p.pid)

    def test_killed_game_raises_game_crashed(self):
        h = start(self)
        nv = h.connect()
        self.assertEqual(nv.game_pid, h.p.pid)
        h.p.kill()
        h.p.wait(5)
        with self.assertRaises(nvbridge.GameCrashed) as cm:
            nv.exec("echo x")
        self.assertEqual(cm.exception.pid, h.p.pid)
        nv.close()

    def test_quit_exits_process(self):
        h = start(self)
        with h.connect() as nv:
            self.assertEqual(nv.quit(), {"quitting": True})
        self.assertIsNotNone(h.p.wait(5))
        self.assertEqual(h.p.returncode, 0)


class CppMutationTwin(unittest.TestCase):
    def test_mutant_core_is_rejected(self):
        h = start(self, MUTANT)
        with h.connect() as nv:
            with self.assertRaises((AssertionError, nvbridge.BridgeError), msg="mutant C++ core was NOT caught"):
                conformance.run(nv, connect2=h.connect)

    def test_lock_mutant_is_rejected(self):
        h = start(self, MUTANT_LOCK)
        with h.connect() as nv:
            with self.assertRaises((AssertionError, nvbridge.BridgeError), msg="lease-not-enforced mutant was NOT caught"):
                conformance.run(nv, connect2=h.connect)

    def test_save_guard_mutant_is_rejected(self):
        h = start(self, MUTANT_SAVE)
        with h.connect() as nv:
            with self.assertRaises((AssertionError, nvbridge.BridgeError), msg="save-guard mutant was NOT caught"):
                conformance.run(nv, connect2=h.connect)

    def test_exec_ref_ignored_mutant_is_rejected(self):
        h = start(self, MUTANT_EXECREF)
        with h.connect() as nv:
            with self.assertRaises((AssertionError, nvbridge.BridgeError), msg="exec-ignores-ref mutant was NOT caught"):
                conformance.run(nv, connect2=h.connect)
            with self.assertRaises(AssertionError, msg="exec-ignores-ref mutant was NOT caught by check_exec_ref"):
                conformance.check_exec_ref(nv)


if __name__ == "__main__":
    unittest.main()
