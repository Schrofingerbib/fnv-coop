"""L10 two-process integration test: host and client are separate OS processes (coop_peer*.exe) joined by the
real UDP transport. Proves commit -> wire -> apply -> convergence, and that the mutation twins (client sink not
wired / host capture not wired) are caught by the same scenario.

Run: python -I plugin/coop/tests/test_integration.py   (needs out/L10/*.exe from plugin/coop/build_coop.cmd)
"""
import json
import os
import queue
import subprocess
import sys
import threading
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
OUT_BIN = ROOT / "out" / "L10"
BIN = OUT_BIN        # replaced by a private temp copy in setUpClass (a rebuild of out/L10 mid-test must not kill running peers)

VCG02 = {
    "cause": {"kind": 1, "source": "FalloutNV.esm:10A214", "aux": 10}, "origin": 0,
    "effects": [
        {"type": "SetStage", "quest": "FalloutNV.esm:10A214", "stage": 10},
        {"type": "SetGlobal", "global": "FalloutNV.esm:123456", "value": 3},
        {"type": "AddItem", "item": "FalloutNV.esm:00000F", "count": 100, "to": "default"},
        {"scope": {
            "cause": {"kind": 1, "source": "FalloutNV.esm:104C00", "aux": 5},
            "effects": [
                {"type": "SetStage", "quest": "FalloutNV.esm:104C00", "stage": 5},
                {"type": "StartQuest", "quest": "FalloutNV.esm:104C01"},
            ]}},
        {"type": "EnableRef", "ref": "FalloutNV.esm:000777"},
        {"type": "ModKarma", "delta": 2, "to": "default"},
    ],
}
TOPIC = {
    "cause": {"kind": 2, "source": "FalloutNV.esm:15D4E9", "aux": 0}, "origin": 0,
    "effects": [
        {"type": "SetObjective", "quest": "FalloutNV.esm:10A214", "objective": 3, "flags": 3},
        {"type": "DisableRef", "ref": "FalloutNV.esm:000777"},
    ],
}


class Peer:
    def __init__(self, exe, *args):
        self.p = subprocess.Popen([str(BIN / exe), *args], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                  stderr=subprocess.STDOUT, text=True, encoding="utf-8", bufsize=1)
        self.q = queue.Queue()
        self.txns = []
        self.log = []          # every output line (stdout+stderr merged), kept for diagnostics
        self.exe = exe
        threading.Thread(target=self._read, daemon=True).start()
        self.ready = self.expect("READY", 10)
        self.info = json.loads(self.ready[len("READY "):])

    def _read(self):
        for line in self.p.stdout:
            line = line.rstrip("\r\n")
            if line.startswith("TXN "):
                self.txns.append(json.loads(line[4:]))
            self.q.put(line)
        self.q.put(None)

    def expect(self, prefix, timeout=10):
        import time
        end = time.time() + timeout
        while time.time() < end:
            try:
                line = self.q.get(timeout=max(0.01, end - time.time()))
            except queue.Empty:
                break
            if line is None:
                raise AssertionError(self.describe_exit("waiting for %r" % prefix))
            if line.startswith(prefix):
                return line
        raise AssertionError("timeout waiting for %r" % prefix)

    def describe_exit(self, what):
        try:
            rc = self.p.wait(timeout=2)
        except subprocess.TimeoutExpired:
            rc = "still running"
        return "peer %s exited (rc=%s) while %s; last output:\n  %s" % (self.exe, rc, what, "\n  ".join(self.log[-12:]))

    def cmd(self, text, reply, timeout=10):
        self.p.stdin.write(text + "\n")
        self.p.stdin.flush()
        return self.expect(reply, timeout)

    def wait(self, kind, n, ms, tolerate_exit=False):
        """returns True on OK, False on TIMEOUT (or on a peer exit when tolerate_exit: the expected-failure mutant scenarios)"""
        self.p.stdin.write("%s %d %d\n" % (kind, n, ms))
        self.p.stdin.flush()
        import time
        end = time.time() + ms / 1000.0 + 5
        while time.time() < end:
            try:
                l = self.q.get(timeout=0.1)
            except queue.Empty:
                continue
            if l is None:
                if tolerate_exit:
                    self.exit_note = self.describe_exit("waiting for %s" % kind)
                    return False
                raise AssertionError(self.describe_exit("waiting for %s" % kind))
            if l.startswith("OK " + kind):
                return True
            if l.startswith("TIMEOUT " + kind):
                return False
        raise AssertionError("no reply to " + kind)

    def dump(self):
        return json.loads(self.cmd("dump", "DUMP")[5:])

    def status(self):
        return json.loads(self.cmd("status", "STATUS")[7:])

    def close(self):
        try:
            self.p.stdin.write("quit\n")
            self.p.stdin.flush()
        except OSError:
            pass
        try:
            self.p.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.p.kill()
            self.p.wait()
        for f in (self.p.stdin, self.p.stdout):
            try:
                f.close()
            except OSError:
                pass


class Scenario:
    """host + 1 early client + 1 late client. Returns a dict of observations."""

    def __init__(self, host_exe="coop_peer.exe", client_exe="coop_peer.exe"):
        self.host_exe, self.client_exe = host_exe, client_exe

    def run(self):
        peers = []
        try:
            host = Peer(self.host_exe, "--role", "host", "--bind", "127.0.0.1", "--port", "0")
            peers.append(host)
            port = str(host.info["port"])
            a = Peer(self.client_exe, "--role", "client", "--host", "127.0.0.1", "--port", port, "--token", "501")
            peers.append(a)
            obs = {"host": host, "a": a}
            obs["joined"] = host.wait("wait_joined", 1, 5000) and a.wait("wait_joined", 1, 5000)
            host.cmd("scope " + json.dumps(VCG02), "OK scope")
            host.cmd("scope " + json.dumps(TOPIC), "OK scope")
            tol = self.host_exe != "coop_peer.exe" or self.client_exe != "coop_peer.exe"   # mutant twins: a non-converging run must still fail by ASSERTION
            obs["host_synced"] = host.wait("wait_seq", 2, 4000, tolerate_exit=tol)
            obs["a_synced"] = a.wait("wait_seq", 2, 4000, tolerate_exit=tol)
            # late joiner: replay from the story log
            b = Peer(self.client_exe, "--role", "client", "--host", "127.0.0.1", "--port", port, "--token", "502")
            peers.append(b)
            obs["b"] = b
            obs["b_synced"] = b.wait("wait_seq", 2, 4000, tolerate_exit=tol)
            def safe(peer, fn):
                try:
                    return fn()
                except (AssertionError, ValueError, OSError):
                    if not tol:
                        raise
                    return None
            obs["host_world"], obs["a_world"], obs["b_world"] = (safe(x, x.dump) for x in (host, a, b))
            obs["host_status"], obs["a_status"] = (safe(x, x.status) for x in (host, a))
            obs["converged"] = (obs["a_synced"] and obs["b_synced"] and obs["host_world"] == obs["a_world"] == obs["b_world"])
            return obs
        finally:
            for p in peers:
                p.close()


class ActionScenario:
    """host + guests A and B; A asks the host to activate a ref (nothing is applied locally on A)."""

    def __init__(self, host_exe="coop_peer.exe"):
        self.host_exe = host_exe

    def run(self):
        peers = []
        try:
            host = Peer(self.host_exe, "--role", "host", "--bind", "127.0.0.1", "--port", "0")
            peers.append(host)
            port = str(host.info["port"])
            a = Peer("coop_peer.exe", "--role", "client", "--host", "127.0.0.1", "--port", port, "--token", "601")
            b = Peer("coop_peer.exe", "--role", "client", "--host", "127.0.0.1", "--port", port, "--token", "602")
            peers += [a, b]
            o = {"a": a, "b": b, "host": host}
            o["joined"] = a.wait("wait_joined", 1, 5000) and b.wait("wait_joined", 1, 5000)
            a.cmd("action activate falloutnv.esm:000ABC", "OK action")
            o["a_synced"] = a.wait("wait_seq", 1, 3000)
            o["b_synced"] = b.wait("wait_seq", 1, 3000)
            o["host_world"], o["a_world"], o["b_world"] = host.dump(), a.dump(), b.dump()
            return o
        finally:
            for p in peers:
                p.close()


class BaselineScenario:
    """host in a game in progress; the client has a DIFFERENT world from its own save and joins late."""
    SEED = ["--seed", "6", "--baseline", "1"]

    def __init__(self, client_exe="coop_peer.exe"):
        self.client_exe = client_exe

    def run(self):
        peers = []
        try:
            host = Peer("coop_peer.exe", "--role", "host", "--bind", "127.0.0.1", "--port", "0", *self.SEED,
                        "--set", "stage:falloutnv.esm:000200=10", "--set", "global:falloutnv.esm:000300=7",
                        "--set", "obj:falloutnv.esm:000200#0=3", "--set", "karma:=12")
            peers.append(host)
            host.cmd("scope " + json.dumps({"cause": {"kind": 1, "source": "FalloutNV.esm:000201", "aux": 5}, "origin": 0,
                     "effects": [{"type": "SetStage", "quest": "FalloutNV.esm:000201", "stage": 5}]}), "OK scope")
            port = str(host.info["port"])
            c = Peer(self.client_exe, "--role", "client", "--host", "127.0.0.1", "--port", port, "--token", "701", *self.SEED,
                     "--set", "stage:falloutnv.esm:000200=30", "--set", "stage:falloutnv.esm:000202=9",
                     "--set", "global:falloutnv.esm:000300=99", "--set", "obj:falloutnv.esm:000200#0=1", "--set", "karma:=-5")
            peers.append(c)
            o = {"host": host, "c": c}
            o["baseline_done"] = c.wait("wait_baseline", 1, 6000)
            o["c_synced"] = c.wait("wait_seq", 1, 4000)
            o["host_world"], o["c_world"] = host.dump(), c.dump()
            o["c_status"] = c.status()
            return o
        finally:
            for p in peers:
                p.close()


class TestIntegration(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        global BIN
        import shutil, tempfile
        cls._tmp = tempfile.mkdtemp(prefix="coop_peers_")
        for f in OUT_BIN.glob("coop_peer*.exe"):
            shutil.copy2(f, cls._tmp)
        BIN = Path(cls._tmp)
        for exe in ("coop_peer.exe", "coop_peer_mut_sink.exe", "coop_peer_mut_commit.exe", "coop_peer_mut_action.exe",
                    "coop_peer_mut_baseline.exe"):
            if not (BIN / exe).exists():
                raise unittest.SkipTest("missing %s (run plugin\\coop\\build_coop.cmd)" % exe)

    def test_commit_wire_apply_converge(self):
        o = Scenario().run()
        self.assertTrue(o["joined"])
        self.assertTrue(o["converged"], json.dumps({k: o[k] for k in ("host_world", "a_world", "b_world")}, indent=1))
        w = o["a_world"]
        self.assertEqual(w["stages"]["falloutnv.esm:10A214"], 10)
        self.assertEqual(w["stages"]["falloutnv.esm:104C00"], 5)     # nested scope rode in the same txn
        self.assertEqual(w["items"]["falloutnv.esm:00000F"], 100)
        self.assertEqual(w["karma"], 2)
        self.assertEqual(w["enabled"]["falloutnv.esm:000777"], 0)    # enable then (txn 2) disable: order preserved
        self.assertEqual(w["objectives"]["falloutnv.esm:10A214#3"], 3)

    def test_one_script_run_is_one_txn_with_all_effects(self):
        o = Scenario().run()
        a_txns = [t for t in o["a"].txns if t["dir"] == "apply"]
        self.assertEqual([t["seq"] for t in a_txns], [1, 2])
        first = a_txns[0]
        types = [e["type"] for e in first["effects"]]
        self.assertEqual(types, ["SetStage", "SetGlobal", "AddItem", "SetStage", "StartQuest", "EnableRef", "ModKarma"])
        self.assertEqual(first["effects"][0], {"type": "SetStage", "quest": "falloutnv.esm:10A214", "stage": 10})
        self.assertEqual(first["cause"]["source"], "falloutnv.esm:10A214")
        self.assertTrue(all(r == "Ok" for r in first["results"]))
        # recipients resolved at commit: the AddItem reaches this client as a mask, not "default"
        self.assertTrue(first["effects"][2]["to"].startswith("mask:"))
        # host committed exactly the same two txns
        h_txns = [t for t in o["host"].txns if t["dir"] == "commit"]
        self.assertEqual([t["seq"] for t in h_txns], [1, 2])

    def test_status_reports_peers_and_counts(self):
        o = Scenario().run()
        hs = o["host_status"]
        self.assertEqual(hs["role"], "host")
        self.assertEqual(hs["lastSeq"], 2)
        self.assertEqual(hs["connected"], 2)
        self.assertEqual(hs["host"]["commits"], 2)
        self.assertEqual(o["a_status"]["transport"]["kind"], "enet")
        self.assertEqual(hs["transport"]["kind"], "enet")
        self.assertEqual(o["a_status"]["client"]["applied"], 2)
        self.assertTrue(o["a_status"]["client"]["joined"])

    def test_mutation_client_sink_not_wired_is_caught(self):
        o = Scenario(client_exe="coop_peer_mut_sink.exe").run()
        self.assertTrue(o["a_synced"])   # the protocol layer is fine (txns arrive and are acked)...
        self.assertFalse(o["converged"])  # ...but the world never changed, so convergence MUST fail
        self.assertNotEqual(o["a_world"], o["host_world"])
        self.assertTrue(all(r == "MutantNotWired" for t in o["a"].txns for r in t.get("results", [])))

    def test_mutation_host_capture_not_wired_is_caught(self):
        o = Scenario(host_exe="coop_peer_mut_commit.exe").run()
        self.assertFalse(o["a_synced"])   # nothing was ever committed to the wire
        self.assertFalse(o["converged"])
        self.assertEqual((o["host_status"] or {}).get("lastSeq", 0), 0)

    def test_guest_action_executes_on_host_and_reaches_everyone(self):
        o = ActionScenario().run()
        self.assertTrue(o["joined"] and o["a_synced"] and o["b_synced"])
        for w in (o["host_world"], o["a_world"], o["b_world"]):
            self.assertEqual(w["enabled"]["falloutnv.esm:000ABC"], 1)
        self.assertEqual(o["a_world"]["items"].get("falloutnv.esm:00000F"), 5)    # reward to the requester only
        self.assertNotIn("falloutnv.esm:00000F", o["b_world"]["items"])
        self.assertNotIn("falloutnv.esm:00000F", o["host_world"]["items"])
        txn = [t for t in o["b"].txns if t["dir"] == "apply"][0]
        self.assertEqual(txn["origin"], 1)                                         # origin = the guest's slot

    def test_mutation_host_ignores_actions_is_caught(self):
        o = ActionScenario(host_exe="coop_peer_mut_action.exe").run()
        self.assertFalse(o["a_synced"])
        self.assertNotEqual(o["a_world"]["enabled"].get("falloutnv.esm:000ABC"), 1)

    def test_baseline_join_converges_a_divergent_world(self):
        o = BaselineScenario().run()
        self.assertTrue(o["baseline_done"] and o["c_synced"])
        self.assertEqual(o["host_world"], o["c_world"])
        self.assertEqual(o["c_world"]["stages"]["falloutnv.esm:000200"], 10)     # lowered from 30
        self.assertEqual(o["c_world"]["stages"]["falloutnv.esm:000202"], 0)      # reset from 9
        self.assertEqual(o["c_world"]["stages"]["falloutnv.esm:000201"], 5)      # host txn before the join
        self.assertEqual(o["c_world"]["karma"], 12)
        self.assertEqual(o["c_world"]["objectives"]["falloutnv.esm:000200#0"], 3)
        self.assertTrue(o["c_status"]["client"]["baselineDone"])

    def test_mutation_client_without_baseline_stays_divergent(self):
        o = BaselineScenario(client_exe="coop_peer_mut_baseline.exe").run()
        self.assertFalse(o["baseline_done"])
        self.assertNotEqual(o["host_world"], o["c_world"])


    @classmethod
    def tearDownClass(cls):
        import shutil
        shutil.rmtree(getattr(cls, "_tmp", ""), ignore_errors=True)


if __name__ == "__main__":
    unittest.main(verbosity=2)
