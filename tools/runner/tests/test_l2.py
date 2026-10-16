"""L2 tests (no game needed). Run: python tools/runner/tests/test_l2.py   (stdlib unittest)

Mutation twins are tests that run a deliberately broken variant and require the suite's assertion to
fail on it ("assertCaught").
"""
from __future__ import annotations

import ctypes
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import xml.etree.ElementTree as ET
from ctypes import wintypes
from pathlib import Path

HERE = Path(__file__).resolve().parent
RUNNER = HERE.parent
sys.path.insert(0, str(RUNNER))
sys.path.insert(0, str(HERE))

import instances  # noqa: E402
import jobqueue  # noqa: E402
import scenario  # noqa: E402
import run  # noqa: E402
import winproc  # noqa: E402
from fake_pair import make_pair, snap_diff  # noqa: E402

SCEN = {
    "quest": "TQ01", "ref": "T.esm:0A1234", "formid": "000A1234", "tier": "T1", "manual": False,
    "steps": [
        {"id": "start", "host": "startquest 000A1234", "host_edid": "startquest TQ01", "wait": "GetQuestRunning 000A1234",
         "expect": {"both": {"quest_running": "TQ01"}}},
        {"id": "stage_10", "host": "setstage 000A1234 10", "host_edid": "setstage TQ01 10", "wait": "GetStageDone 000A1234 10",
         "expect": {"both": {"stage": {"quest": "TQ01", "value": 10},
                             "objectives": {"displayed": [5], "completed": []}}}},
        {"id": "stage_20", "host": "setstage 000A1234 20", "host_edid": "setstage TQ01 20", "wait": "GetStageDone 000A1234 20",
         "expect": {"both": {"stage": {"quest": "TQ01", "value": 20}, "quest_completed": True,
                             "objectives": {"displayed": [], "completed": [5]}}}},
    ],
}


def make_fake_game(root: Path) -> Path:
    g = root / "game"
    (g / "Data").mkdir(parents=True)
    (g / "FalloutNV.exe").write_bytes(b"MZ" + b"exe" * 1000)
    (g / "Fallout_default.ini").write_text("[General]\nSLocalSavePath=Saves\\\n[Controls]\nbBackground Keyboard=0\n")
    (g / "Fallout.ini").write_text("[General]\nbUseMyGamesDirectory=1\n")  # must NOT be linked
    (g / "Data" / "FalloutNV.esm").write_bytes(b"TES4" + b"x" * 5000)
    (g / "Data" / "A - Main.bsa").write_bytes(b"BSA" * 500)
    (g / "Data/NVSE/Plugins").mkdir(parents=True)
    (g / "Data/NVSE/Plugins/other.dll").write_bytes(b"o")
    (g / "Data/NVSE/Plugins/nvcoop.ini").write_text("[bridge]\ninstance=99\n")
    (g / "Saves").mkdir()
    (g / "Saves" / "orig.fos").write_bytes(b"x")  # must not be linked
    return g


def tree_hash(root: Path) -> dict:
    return {str(p.relative_to(root)): (p.stat().st_size, instances.sha256_file(p))
            for p in sorted(root.rglob("*")) if p.is_file()}


class IniTests(unittest.TestCase):
    def test_set_existing_and_new(self):
        t = "[General]\nbA=1\n[Display]\nx=2\n"
        t = instances.ini_set(t, "General", "bA", "0")
        t = instances.ini_set(t, "General", "bB", "1")
        t = instances.ini_set(t, "New", "k", "v")
        self.assertEqual(instances.ini_get(t, "General", "bA"), "0")
        self.assertEqual(instances.ini_get(t, "general", "BB"), "1")
        self.assertEqual(instances.ini_get(t, "Display", "x"), "2")
        self.assertEqual(instances.ini_get(t, "New", "k"), "v")

    def test_crlf_preserved(self):
        t = instances.ini_set("[General]\r\nbA=1\r\n", "General", "bB", "2")
        self.assertIn("\r\n", t)
        self.assertNotIn("\n\n", t.replace("\r\n", ""))


class ProvisionTests(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.root = Path(self.td.name)
        self.game = make_fake_game(self.root)
        self.inst = self.root / "inst"
        os.environ["FNV_GAME_DIR"] = str(self.game)
        os.environ["FNV_INSTANCES_ROOT"] = str(self.inst)

    def tearDown(self):
        os.environ.pop("FNV_GAME_DIR", None)
        os.environ.pop("FNV_INSTANCES_ROOT", None)
        self.td.cleanup()

    def test_hardlinks_inis_and_game_untouched(self):
        before = tree_hash(self.game)
        dll = self.root / "nvcoop.dll"
        dll.write_bytes(b"dll1")
        m = instances.provision(0, role="host", plugin_dll=dll, autoload="save1")
        m1 = instances.provision(1, role="client", plugin_dll=dll)
        d0, d1 = instances.instance_dir(0), instances.instance_dir(1)
        self.assertGreaterEqual(m["link_counts"]["hardlink"], 3)
        self.assertEqual(os.stat(d0 / "FalloutNV.exe").st_ino, os.stat(self.game / "FalloutNV.exe").st_ino)
        self.assertEqual(os.stat(d0 / "Data" / "FalloutNV.esm").st_ino, os.stat(d1 / "Data" / "FalloutNV.esm").st_ino)
        # writable per-instance files are distinct real files
        ini0 = (d0 / "Fallout.ini").read_text()
        self.assertEqual(instances.ini_get(ini0, "General", "bUseMyGamesDirectory"), "0")
        self.assertEqual(instances.ini_get(ini0, "General", "bAlwaysActive"), "1")
        self.assertEqual(instances.ini_get(ini0, "Controls", "bBackground Keyboard"), "1")
        pr = (d0 / "FalloutPrefs.ini").read_text()
        self.assertEqual(instances.ini_get(pr, "Display", "bFull Screen"), "0")
        self.assertEqual(instances.ini_get(pr, "Display", "iSize W"), "640")
        self.assertEqual(instances.ini_get(pr, "Display", "iSize H"), "480")
        self.assertEqual((d0 / "steam_appid.txt").read_text(), "22380")
        self.assertFalse((d0 / "Saves" / "orig.fos").exists())
        n0 = (d0 / "Data/NVSE/Plugins/nvcoop.ini").read_text()
        self.assertIn("instance=0", n0)
        self.assertIn("autoload=save1", n0)
        self.assertIn("role=host", n0)
        self.assertIn("minimized=1", n0)
        self.assertIn("multiInstance=1", n0)
        self.assertIn("role=client", (d1 / "Data/NVSE/Plugins/nvcoop.ini").read_text())
        self.assertIn(r"\\.\pipe\nvcoop-1", m1["pipe"])
        self.assertEqual((d0 / "Data/NVSE/Plugins/nvcoop.dll").read_bytes(), b"dll1")
        # a plugin/ini that exists in the main game dir must not leak into / be written through
        self.assertFalse((d0 / "Data/NVSE/Plugins/other.dll").exists())
        # the read-only game dir is byte-identical afterwards
        self.assertEqual(before, tree_hash(self.game))

    def test_idempotent_and_pin_detects_tamper(self):
        instances.provision(0)
        instances.provision(0)  # second run: everything 'existing'
        self.assertEqual(instances.verify_pins(0), [])
        exe = instances.instance_dir(0) / "FalloutNV.exe"
        exe.unlink()  # break the link, write a different file (don't write through the hardlink)
        exe.write_bytes(b"patched")
        self.assertTrue(any("FalloutNV.exe" in b for b in instances.verify_pins(0)))

    def test_job_pin_check_blocks_stale_dll(self):
        dll = self.root / "nvcoop.dll"
        dll.write_bytes(b"v1")
        instances.provision(0, plugin_dll=dll)
        self.assertEqual(run.check_instance(0, dll), [])
        dll.write_bytes(b"v2")  # build output moved on
        self.assertTrue(any("nvcoop.dll" in b for b in run.check_instance(0, dll)))
        r = run.run_job("pin-test", 1, [sys.executable, "-c", "pass"], pool=1, dll_src=dll,
                        runs_dir=self.root / "runs", lease_timeout_s=5, use_game=False)
        self.assertEqual(r["status"], "pin_mismatch")

    def test_job_collects_logs_and_env(self):
        instances.provision(0)
        log = instances.instance_dir(0) / "Data/NVSE/Plugins/nvcoop.log"
        code = ("import os,pathlib,sys;"
                f"pathlib.Path(r'{log}').write_text('hello ' + os.environ['NVCOOP_INSTANCES']);"
                "pathlib.Path(os.environ['FNV_INSTANCES_ROOT'], '0', 'crash.dmp').write_bytes(b'dmp')")
        r = run.run_job("logs-test", 1, [sys.executable, "-c", code], pool=1, runs_dir=self.root / "runs",
                        lease_timeout_s=5, use_game=False)
        self.assertEqual(r["status"], "pass", r)
        out = self.root / "runs" / "logs-test" / "inst0"
        self.assertEqual((out / "Data/NVSE/Plugins/nvcoop.log").read_text(), "hello 0")
        self.assertTrue((out / "crash.dmp").exists())

    def test_mutant_copy_mode_not_zero_cost(self):
        """Twin: a provisioner that copies instead of linking must fail the inode-sharing assertion."""
        instances.provision(0, modes=("copy",))
        same = os.stat(instances.instance_dir(0) / "FalloutNV.exe").st_ino == os.stat(self.game / "FalloutNV.exe").st_ino
        self.assertFalse(same, "twin should be distinguishable from real hardlinks")


WORKER = r"""
import sys, time, json, os
sys.path.insert(0, sys.argv[1])
import jobqueue
root, log, who = sys.argv[2], sys.argv[3], sys.argv[4]
with jobqueue.lease(1, pool=1, job=who, root=root, timeout_s=60):
    with open(log, 'a') as f: f.write(json.dumps({'who': who, 'ev': 'in', 't': time.time()}) + '\n')
    time.sleep(0.4)
    with open(log, 'a') as f: f.write(json.dumps({'who': who, 'ev': 'out', 't': time.time()}) + '\n')
"""


def overlaps(events) -> bool:
    active = 0
    for e in sorted(events, key=lambda e: e["t"]):
        active += 1 if e["ev"] == "in" else -1
        if active > 1:
            return True
    return False


LAUNCHER = r"""
import sys, time, subprocess
sys.path.insert(0, sys.argv[1])
import run
time.sleep(1.0)                      # let the parent put us in the job first
cmd = [sys.executable, "-c", "import time; time.sleep(60)"]
if sys.argv[3] == "detached":
    p = run.spawn_detached(cmd, None, None)
else:
    p = subprocess.Popen(cmd)       # the old behaviour
print(p.pid, flush=True)
"""


class DetachTests(unittest.TestCase):
    def _run(self, mode):
        import ctypes
        from ctypes import wintypes
        k = ctypes.WinDLL("kernel32", use_last_error=True)
        k.CreateJobObjectW.restype = wintypes.HANDLE
        k.OpenProcess.restype = wintypes.HANDLE

        class BASIC(ctypes.Structure):
            _fields_ = [("a", ctypes.c_longlong), ("b", ctypes.c_longlong), ("LimitFlags", wintypes.DWORD),
                        ("c", ctypes.c_size_t), ("d", ctypes.c_size_t), ("e", wintypes.DWORD), ("f", ctypes.c_size_t),
                        ("g", wintypes.DWORD), ("h", wintypes.DWORD)]

        class IO(ctypes.Structure):
            _fields_ = [("x", ctypes.c_ulonglong * 6)]

        class EXT(ctypes.Structure):
            _fields_ = [("Basic", BASIC), ("Io", IO), ("p1", ctypes.c_size_t), ("p2", ctypes.c_size_t),
                        ("p3", ctypes.c_size_t), ("p4", ctypes.c_size_t)]
        job = k.CreateJobObjectW(None, None)
        info = EXT()
        info.Basic.LimitFlags = 0x2000 | 0x800        # KILL_ON_JOB_CLOSE | BREAKAWAY_OK
        self.assertTrue(k.SetInformationJobObject(wintypes.HANDLE(job), 9, ctypes.byref(info), ctypes.sizeof(info)))
        p = subprocess.Popen([sys.executable, "-c", LAUNCHER, str(RUNNER), "x", mode], stdout=subprocess.PIPE, text=True)
        h = k.OpenProcess(0x1F0FFF, False, p.pid)
        self.assertTrue(k.AssignProcessToJobObject(wintypes.HANDLE(job), h), ctypes.get_last_error())
        child = int(p.stdout.readline())
        p.wait(10)
        self.assertTrue(winproc.pid_alive(child))
        k.CloseHandle(wintypes.HANDLE(job))           # the short-lived launcher's job goes away
        time.sleep(1.0)
        alive = winproc.pid_alive(child)
        if alive:
            winproc.kill_pid(child)
        return alive

    def test_detached_child_outlives_launcher_job(self):
        self.assertTrue(self._run("detached"))

    def test_twin_plain_popen_dies_with_job(self):
        """Twin: the old plain Popen is killed when the launcher's job closes (the L5 'both died' symptom)."""
        self.assertFalse(self._run("plain"))


class PipeReadyTests(unittest.TestCase):
    def test_pipe_ready_true_for_existing_false_for_missing(self):
        from fake_server import FakeServer
        from uniq import unique_instance
        inst = unique_instance()
        self.assertFalse(run._pipe_ready(inst))
        srv = FakeServer(inst).start()
        try:
            self.assertTrue(run._pipe_ready(inst))
        finally:
            srv.stop()


class LoadOrderTests(unittest.TestCase):
    class B:
        def __init__(self, r):
            self.r = r

        def call(self, m, p=None):
            if isinstance(self.r, Exception):
                raise self.r
            return self.r

    ORDER = ["FalloutNV.esm", "DeadMoney.esm", "HonestHearts.esm", "OldWorldBlues.esm", "LonesomeRoad.esm",
             "TribalPack.esm", "MercenaryPack.esm", "ClassicPack.esm", "CaravanPack.esm", "GunRunnersArsenal.esm"]

    def test_matching_prefix_and_dict_forms_pass(self):
        scenario.check_load_order(self.B({"plugins": [{"name": n} for n in self.ORDER]}))
        scenario.check_load_order(self.B(self.ORDER[:3]))

    def test_mismatch_empty_and_missing_method_fail_fast(self):
        swapped = list(self.ORDER)
        swapped[8], swapped[9] = swapped[9], swapped[8]
        for bad in (swapped, [], self.ORDER + ["Extra.esm"], RuntimeError("unknown method")):
            with self.assertRaises(scenario.LoadOrderError, msg=str(bad)):
                scenario.check_load_order(self.B(bad), "host")

    def test_mutant_unchecked_runner_would_accept_wrong_order(self):
        """Twin: without the check a swapped order is silently accepted (FormIDs wrong)."""
        swapped = list(self.ORDER)
        swapped[8], swapped[9] = swapped[9], swapped[8]
        with self.assertRaises(scenario.LoadOrderError):
            scenario.check_load_order(self.B(swapped))
        self.assertNotEqual(swapped, self.ORDER)


class LoadOrderWireTests(unittest.TestCase):
    """The real bridge reply shape ({"count","plugins":[{"index","name"}]}) over a real pipe (L1's fake server)."""

    def run_with(self, plugins):
        import nvbridge
        from fake_server import FakeServer
        from uniq import unique_instance

        class Srv(FakeServer):
            def _handle(self, line):
                req = json.loads(line)
                if req.get("method") == "plugins":
                    return {"id": req["id"], "result": {"count": len(plugins), "plugins": plugins}}
                return super()._handle(line)
        inst = unique_instance()
        srv = Srv(inst).start()
        try:
            with nvbridge.NvBridge.connect(inst, wait_s=5) as nv:
                scenario.check_load_order(nv, "host")
        finally:
            srv.stop()

    def test_real_shape_passes_and_gaps_or_swaps_fail(self):
        order = LoadOrderTests.ORDER
        self.run_with([{"index": i, "name": n} for i, n in enumerate(order)])
        self.run_with([{"index": i, "name": n} for i, n in reversed(list(enumerate(order[:4])))])  # unsorted ok
        with self.assertRaises(scenario.LoadOrderError):
            self.run_with([{"index": i, "name": n} for i, n in enumerate(order[:2] + order[3:4])
                           ] + [{"index": 5, "name": "x.esm"}])
        sw = list(order)
        sw[1], sw[2] = sw[2], sw[1]
        with self.assertRaises(scenario.LoadOrderError):
            self.run_with([{"index": i, "name": n} for i, n in enumerate(sw)])


class GameSlotTests(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.root = Path(self.td.name)
        os.environ["FNV_GAME_DIR"] = str(make_fake_game(self.root))
        os.environ["FNV_INSTANCES_ROOT"] = str(self.root / "inst")
        instances.provision(0)
        self.calls = []
        self.orig = run.gameslot_cli
        self.code = 0

        def fake(*a):
            self.calls.append(a[0])
            return (self.code if a[0] == "acquire" else 0), "OK" if self.code == 0 else "BUSY: held by L5"
        run.gameslot_cli = fake

    def tearDown(self):
        run.gameslot_cli = self.orig
        os.environ.pop("FNV_GAME_DIR", None)
        os.environ.pop("FNV_INSTANCES_ROOT", None)
        self.td.cleanup()

    def job(self, code="pass", **kw):
        marker = self.root / "ran.txt"
        r = run.run_job("slot", 1, [sys.executable, "-c", f"open(r'{marker}','w').write('x');{code}"], pool=1,
                        runs_dir=self.root / "runs", lease_timeout_s=5, **kw)
        return r, marker

    def test_job_holds_slot_around_command_and_releases(self):
        r, marker = self.job()
        self.assertEqual(r["status"], "pass")
        self.assertEqual(self.calls, ["acquire", "release"])

    def test_release_even_when_job_fails(self):
        r, _ = self.job("raise SystemExit(3)")
        self.assertEqual(r["status"], "fail")
        self.assertEqual(self.calls, ["acquire", "release"])

    def test_busy_slot_blocks_the_command(self):
        self.code = 3
        r, marker = self.job()
        self.assertEqual(r["status"], "slot_busy")
        self.assertFalse(marker.exists(), "command must not run without the slot")
        self.assertNotIn("release", self.calls)

    def test_mutant_no_slot_is_caught(self):
        """Twin: use_game=False (no slot) runs the command with zero gameslot calls; the tests above require them."""
        r, marker = self.job(use_game=False)
        self.assertEqual(r["status"], "pass")
        self.assertEqual(self.calls, [], "mutant bypasses the slot - assertions above would fail on it")


class QueueTests(unittest.TestCase):
    def test_processes_never_share_an_instance(self):
        with tempfile.TemporaryDirectory() as td:
            log = Path(td) / "log.jsonl"
            ps = [subprocess.Popen([sys.executable, "-c", WORKER, str(RUNNER), td, str(log), f"w{i}"]) for i in range(5)]
            for p in ps:
                self.assertEqual(p.wait(60), 0)
            ev = [json.loads(l) for l in log.read_text().splitlines()]
            self.assertEqual(len(ev), 10)
            self.assertFalse(overlaps(ev))

    def test_mutant_no_locking_is_caught(self):
        """Twin: leases that don't lock must produce overlap, which the assertion above would flag."""
        orig = jobqueue._Lock.try_acquire
        jobqueue._Lock.try_acquire = lambda self, info: True  # broken: always 'acquired'
        try:
            with tempfile.TemporaryDirectory() as td:
                ev, lk = [], threading.Lock()

                def w(i):
                    with jobqueue.lease(1, pool=1, root=td, timeout_s=5):
                        with lk:
                            ev.append({"ev": "in", "t": time.time()})
                        time.sleep(0.2)
                        with lk:
                            ev.append({"ev": "out", "t": time.time()})
                ts = [threading.Thread(target=w, args=(i,)) for i in range(3)]
                [t.start() for t in ts]
                [t.join() for t in ts]
                self.assertTrue(overlaps(ev), "mutant should overlap")
        finally:
            jobqueue._Lock.try_acquire = orig

    def test_all_or_nothing_and_holder(self):
        with tempfile.TemporaryDirectory() as td:
            held = jobqueue.acquire(1, pool=2, job="a", root=td)
            self.assertEqual(jobqueue.instance_ids(held), [0])
            self.assertEqual(jobqueue.holder(0, td)["job"], "a")
            self.assertIsNone(jobqueue.holder(1, td))
            with self.assertRaises(jobqueue.LeaseTimeout):
                jobqueue.acquire(2, pool=2, job="b", root=td, timeout_s=0.5)
            self.assertIsNone(jobqueue.holder(1, td), "failed 2-lease must not keep instance 1")
            for l in held:
                l.release()
            with jobqueue.lease(2, pool=2, root=td) as ids:
                self.assertEqual(ids, [0, 1])

    def test_lock_released_on_holder_death(self):
        with tempfile.TemporaryDirectory() as td:
            code = ("import sys,time;sys.path.insert(0,sys.argv[1]);import jobqueue;"
                    "l=jobqueue.acquire(1,pool=1,root=sys.argv[2]);print('held',flush=True);time.sleep(60)")
            p = subprocess.Popen([sys.executable, "-c", code, str(RUNNER), td], stdout=subprocess.PIPE, text=True)
            self.assertEqual(p.stdout.readline().strip(), "held")
            self.assertIsNotNone(jobqueue.holder(0, td))
            p.kill()
            p.wait()
            time.sleep(0.2)
            self.assertIsNone(jobqueue.holder(0, td))


class ScenarioTests(unittest.TestCase):
    def run_pair(self, sync, mutant=None, **kw):
        h, c = make_pair(SCEN, sync=sync)
        r = scenario.Runner(h, c, sync_timeout_ms=300, host_timeout_ms=300, _mutant=mutant, **kw)
        return r.run_scenario(SCEN), h, c

    def test_synced_pair_passes_and_host_issues_commands(self):
        res, h, c = self.run_pair(True)
        self.assertEqual([r.status for r in res], ["pass"] * 3, [r.message for r in res])
        self.assertEqual(h.executed, ["startquest 000A1234", "setstage 000A1234 10", "setstage 000A1234 20"])
        self.assertEqual(c.executed, [], "client must never be driven directly")
        self.assertTrue(any(ch.who == "client" for r in res for ch in r.checks))

    def test_unsynced_pair_fails_on_client_check(self):
        res, _, _ = self.run_pair(False)
        self.assertEqual(res[0].status, "fail")
        self.assertIn("client", res[0].message)
        self.assertEqual(len(res), 1, "stops at first failed step")

    def test_keep_going(self):
        h, c = make_pair(SCEN, sync=False)
        res = scenario.Runner(h, c, sync_timeout_ms=100, host_timeout_ms=100).run_scenario(SCEN, stop_on_fail=False)
        self.assertEqual([r.status for r in res], ["fail"] * 3)

    def test_mutant_skip_client_is_caught(self):
        """Twin: a runner that never checks the client wrongly passes a broken sync layer."""
        good, _, _ = self.run_pair(False)
        mutant, _, _ = self.run_pair(False, mutant="skip_client")
        self.assertEqual(good[0].status, "fail")
        self.assertEqual(mutant[0].status, "pass", "mutant is the wrong-but-green runner")
        self.assertNotEqual([r.status for r in good][:1], [r.status for r in mutant][:1])

    def test_mutant_client_checks_host_is_caught(self):
        good, _, _ = self.run_pair(False)
        mutant, _, _ = self.run_pair(False, mutant="client_checks_host")
        self.assertEqual(good[0].status, "fail")
        self.assertEqual(mutant[0].status, "pass")

    def test_host_exec_failure_is_failure(self):
        h, c = make_pair(SCEN, sync=True)
        sc = json.loads(json.dumps(SCEN))
        sc["steps"][0]["host"] = "bogus"
        res = scenario.Runner(h, c).run_scenario(sc)
        self.assertEqual(res[0].status, "fail")

    def test_manual_skipped(self):
        h, c = make_pair(SCEN)
        res = scenario.Runner(h, c).run_scenario({**SCEN, "manual": True})
        self.assertEqual(res[0].status, "skipped")

    def test_snapshot_diff_detects_divergence(self):
        h, c = make_pair(SCEN, sync=True)
        h.sync = False  # host changes never reach the client => states diverge
        r = scenario.Runner(h, c, sync_timeout_ms=100, snapshot_diff=snap_diff)
        # client check would already fail first; make client trivially satisfied by pre-seeding
        for s in SCEN["steps"]:
            c.apply(s)
        h2 = scenario.Runner(h, c, sync_timeout_ms=100, snapshot_diff=lambda a, b: ["diverged"]).run_scenario(SCEN)
        self.assertEqual(h2[0].status, "fail")
        self.assertIn("snapshot", h2[0].message)

    def test_reports(self):
        res, _, _ = self.run_pair(True)
        bad, _, _ = self.run_pair(False)
        with tempfile.TemporaryDirectory() as td:
            x = Path(td) / "o.xml"
            scenario.write_junit(res + bad, x)
            root = ET.parse(x).getroot()
            suite = root.find("testsuite")
            self.assertEqual(suite.get("tests"), "4")
            self.assertEqual(suite.get("failures"), "1")
            self.assertEqual(len(root.findall(".//failure")), 1)
        s = scenario.summarize(res + bad)
        self.assertFalse(s["ok"])
        self.assertTrue(scenario.summarize(res)["ok"])

    def test_summary_json_for_l9(self):
        res, _, _ = self.run_pair(False)
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "r" / "summary.json"
            scenario.write_summary(res, p, "rid", "sim", "2026-10-16T09:00:00", "2026-10-16T09:01:00")
            d = json.loads(p.read_text())
        self.assertEqual(d["schema"], 1)
        self.assertEqual(d["results"], [{"quest": "TQ01", "status": "fail", "steps": 1,
                                         "failed_steps": ["start"], "duration_s": d["results"][0]["duration_s"]}])

    def test_snapshot_cli_checks_by_exit_code(self):
        calls = []
        codes = {"snapdiff.py": 0, "check_stage.py": 0}

        def cli(argv):
            calls.append([Path(str(x)).name for x in argv])
            return codes[Path(str(argv[0])).name], "boom"
        with tempfile.TemporaryDirectory() as td:
            h, c = make_pair(SCEN, sync=True)
            sn = scenario.SnapshotChecks(Path(td), run_cli=cli)
            res = scenario.Runner(h, c, snapshots=sn).run_scenario(SCEN)
            self.assertEqual([r.status for r in res], ["pass"] * 3)
            self.assertEqual(sum(1 for x in calls if x[0] == "check_stage.py"), 2, "only stage_* steps")
            cs = [x for x in calls if x[0] == "check_stage.py"][0]
            self.assertIn("TQ01", cs)
            self.assertIn("10", cs)
            codes["check_stage.py"] = 1
            res = scenario.Runner(h, c, snapshots=sn).run_scenario(SCEN)
            self.assertEqual(res[1].status, "fail")
            self.assertIn("side_effects", res[1].message)
            codes["check_stage.py"] = 0
            codes["snapdiff.py"] = 2  # invalid input counts as failure
            res = scenario.Runner(h, c, snapshots=sn).run_scenario(SCEN)
            self.assertEqual(res[0].status, "fail")
            res = scenario.Runner(h, c, snapshots=sn, _mutant="skip_client").run_scenario(SCEN)
            self.assertEqual(res[0].status, "pass", "twin: skipping client verification skips the diff")

    def test_no_edid_ever_reaches_the_game(self):
        """Real-game path: console takes FormIDs only. Neither exec nor waitFor/expr may contain an EDID."""
        for sync in (True, False):
            h, c = make_pair(SCEN, sync=sync)
            scenario.Runner(h, c, sync_timeout_ms=100, host_timeout_ms=100).run_scenario(SCEN, stop_on_fail=False)
            sent = h.executed + h.exprs + c.executed + c.exprs
            self.assertTrue(sent)
            self.assertTrue(any("000A1234" in s for s in sent))
            for s in sent:
                self.assertNotIn("TQ01", s)

    def test_mutant_edid_path_is_caught(self):
        """Twin: translating without the formid leaks the EDID, which the assertion above would flag."""
        leaked = [e[1] for e in scenario.expectation_exprs(SCEN["steps"][1]["expect"]["both"])]
        self.assertTrue(any("TQ01" in s for s in leaked))

    def test_expectation_translation(self):
        both = {"stage": {"quest": "Q", "value": 3}, "objectives": {"displayed": [1], "completed": [2]},
                "quest_completed": True}
        ex = [e[1] for e in scenario.expectation_exprs(both)]
        self.assertEqual(ex, ["GetStageDone Q 3", "GetObjectiveDisplayed Q 1", "GetObjectiveCompleted Q 2", "GetQuestCompleted Q"])
        ex = [e[1] for e in scenario.expectation_exprs(both, "0100ABCD", "Q")]
        self.assertEqual(ex, ["GetStageDone 0100ABCD 3", "GetObjectiveDisplayed 0100ABCD 1",
                              "GetObjectiveCompleted 0100ABCD 2", "GetQuestCompleted 0100ABCD"])

    def test_real_l3_scenarios_load_and_run_against_spec_fake(self):
        d = scenario.SCENARIO_DIR
        if not d.exists():
            self.skipTest("L3 scenarios not generated")
        scs = scenario.load_scenarios(["VMS01"], directory=d)
        self.assertEqual(len(scs), 1)
        sc = scs[0]
        h, c = make_pair(sc, sync=True)
        res = scenario.Runner(h, c, sync_timeout_ms=500, host_timeout_ms=500).run_scenario(sc)
        self.assertEqual({r.status for r in res}, {"pass"}, [r.message for r in res if r.status != "pass"])
        self.assertEqual(len(res), len(sc["steps"]))
        h, c = make_pair(sc, sync=False)
        res = scenario.Runner(h, c, sync_timeout_ms=100, host_timeout_ms=100).run_scenario(sc)
        self.assertEqual(res[-1].status, "fail")


if __name__ == "__main__":
    unittest.main(verbosity=2)
