"""tools/gameslot.py N-slot semantics (game-free). Each test gets a private GAMESLOT_DIR; mutation twins at the end."""
import importlib.util
import io
import json
import os
import sys
import tempfile
import time
import unittest
from contextlib import redirect_stdout
from pathlib import Path

TOOLS = Path(__file__).resolve().parents[2]


def load(path, slots=None, tmp=None):
    os.environ["GAMESLOT_DIR"] = str(tmp)
    os.environ["GAMESLOT_POLL"] = "0.05"
    if slots is not None:
        Path(tmp, "gameslot_config.json").write_text(json.dumps({"slots": slots}))
    spec = importlib.util.spec_from_file_location("gs_%d" % time.monotonic_ns(), path)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    m._game_pids = lambda: []
    m._avail_gb = lambda: 99.0   # tests must not depend on this machine's RAM
    return m


def call(m, *argv):
    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = m.main(list(argv))
    return rc, buf.getvalue()


def slots_of(out):
    return [int(x) for x in out.strip().rsplit("SLOTS ", 1)[1].split(",")]


class SlotTests(unittest.TestCase):
    SRC = TOOLS / "gameslot.py"

    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.tmp = Path(self.td.name)

    def tearDown(self):
        os.environ.pop("GAMESLOT_DIR", None)
        os.environ.pop("GAMESLOT_POLL", None)
        self.td.cleanup()

    def gs(self, slots=3):
        return load(self.SRC, slots, self.tmp)

    def test_n_single_slot_owners_get_distinct_slots_then_busy(self):
        m = self.gs(3)
        got = []
        for o in ("A", "B", "C"):
            rc, out = call(m, "acquire", "--owner", o, "--slots", "1")
            self.assertEqual(rc, 0, out)
            got += slots_of(out)
        self.assertEqual(sorted(got), [0, 1, 2])
        rc, out = call(m, "acquire", "--owner", "D", "--slots", "1", "--wait", "0")
        self.assertEqual(rc, 3)
        self.assertIn("BUSY", out)

    def test_legacy_no_slots_flag_is_exclusive(self):
        m = self.gs(3)
        rc, out = call(m, "acquire", "--owner", "OLD")
        self.assertEqual(rc, 0)
        self.assertEqual(slots_of(out), [0, 1, 2])
        self.assertEqual(call(m, "acquire", "--owner", "X", "--slots", "1")[0], 3)
        self.assertEqual(call(m, "release", "--owner", "OLD")[0], 0)
        self.assertEqual(call(m, "acquire", "--owner", "X", "--slots", "1")[0], 0)
        # and a legacy request cannot start while someone holds even one slot
        self.assertEqual(call(m, "acquire", "--owner", "OLD2")[0], 3)

    def test_config_one_slot_is_the_old_single_slot_behaviour(self):
        m = self.gs(1)
        self.assertEqual(call(m, "acquire", "--owner", "A")[0], 0)
        rc, out = call(m, "acquire", "--owner", "B")
        self.assertEqual(rc, 3)
        self.assertEqual(call(m, "release", "--owner", "B")[0], 4)
        self.assertEqual(call(m, "release", "--owner", "A")[0], 0)
        self.assertEqual(call(m, "acquire", "--owner", "B")[0], 0)

    def test_status_is_backward_compatible(self):
        m = self.gs(3)
        call(m, "acquire", "--owner", "A", "--slots", "2")
        s = m.status()
        self.assertEqual(s["lease"]["owner"], "A")
        self.assertEqual(s["max_slots"], 3)
        self.assertEqual([x["free"] for x in s["slots"]].count(True), 1)
        self.assertEqual(m.status()["running"], [])

    def test_multi_slot_all_or_nothing_rollback(self):
        m = self.gs(3)
        call(m, "acquire", "--owner", "A", "--slots", "2")
        rc, _ = call(m, "acquire", "--owner", "B", "--slots", "2")
        self.assertEqual(rc, 3)
        self.assertEqual([x["free"] for x in m.status()["slots"]].count(True), 1, "B must not keep a partial grab")
        self.assertEqual(call(m, "acquire", "--owner", "B", "--slots", "1")[0], 0)

    def test_reacquire_same_owner_keeps_slots_and_renews(self):
        m = self.gs(3)
        _, o1 = call(m, "acquire", "--owner", "A", "--slots", "2", "--ttl", "5")
        _, o2 = call(m, "acquire", "--owner", "A", "--slots", "2", "--ttl", "500")
        self.assertEqual(slots_of(o1), slots_of(o2))
        self.assertGreater(m.status()["slots"][slots_of(o1)[0]]["lease"]["expires"], time.time() + 400)

    def test_expired_lease_is_taken_over(self):
        m = self.gs(1)
        call(m, "acquire", "--owner", "A", "--ttl", "1")
        p = self.tmp / "gameslots" / "slot-0.json"
        d = json.loads(p.read_text())
        d["expires"] = time.time() - 1
        p.write_text(json.dumps(d))
        rc, _ = call(m, "acquire", "--owner", "B")
        self.assertEqual(rc, 0)
        self.assertEqual(json.loads(p.read_text())["owner"], "B")

    def test_foreign_legacy_lease_file_blocks_everyone(self):
        m = self.gs(3)
        (self.tmp / "gameslot.json").write_text(json.dumps({"owner": "OLDTOOL", "since": 0, "expires": time.time() + 600}))
        self.assertEqual(call(m, "acquire", "--owner", "A", "--slots", "1")[0], 3)
        self.assertEqual(call(m, "status")[0], 0)

    def test_fifo_waiter_not_starved_by_single_slot_jobs(self):
        m = self.gs(3)
        call(m, "acquire", "--owner", "A", "--slots", "1")
        (self.tmp / "gameslots" / "waiters").mkdir(parents=True, exist_ok=True)
        (self.tmp / "gameslots" / "waiters" / "EXCL.json").write_text(json.dumps(
            {"owner": "EXCL", "ticket": time.time() - 100, "polled": time.time(), "slots": None}))
        rc, out = call(m, "acquire", "--owner", "B", "--slots", "1")
        self.assertEqual(rc, 3, "free slots exist but an older waiter is queued")
        self.assertIn("EXCL", out)
        (self.tmp / "gameslots" / "waiters" / "EXCL.json").write_text(json.dumps(
            {"owner": "EXCL", "ticket": time.time() - 100, "polled": time.time() - 500, "slots": None}))  # stale
        self.assertEqual(call(m, "acquire", "--owner", "B", "--slots", "1")[0], 0)

    def test_wait_blocks_until_released(self):
        import threading
        m = self.gs(1)
        call(m, "acquire", "--owner", "A")
        threading.Timer(0.5, lambda: call(m, "release", "--owner", "A")).start()
        t0 = time.time()
        rc, _ = call(m, "acquire", "--owner", "B", "--wait", "10")
        self.assertEqual(rc, 0)
        self.assertGreaterEqual(time.time() - t0, 0.4)

    # ---- mutation twins: broken variants the exclusivity tests must reject ----
    def test_twin_no_exclusion_is_caught(self):
        m = self.gs(2)
        m._try_slot = lambda i, owner, ttl, now: True  # broken: every slot always granted
        a = call(m, "acquire", "--owner", "A", "--slots", "2")
        b = call(m, "acquire", "--owner", "B", "--slots", "2", "--wait", "0")
        self.assertEqual((a[0], b[0]), (0, 0), "mutant double-grants the same slots")  # the real module gives (0, 3)
        real = self.gs(2)
        call(real, "acquire", "--owner", "A", "--slots", "2")
        self.assertEqual(call(real, "acquire", "--owner", "B", "--slots", "2", "--wait", "0")[0], 3)

    def test_twin_ignoring_legacy_exclusive_is_caught(self):
        m = self.gs(3)
        m._legacy_blocks = lambda owner, now: False   # broken
        (self.tmp / "gameslot.json").write_text(json.dumps({"owner": "OLDTOOL", "since": 0, "expires": time.time() + 600}))
        self.assertEqual(call(m, "acquire", "--owner", "A", "--slots", "1")[0], 0, "mutant ignores the legacy lease")
        real = self.gs(3)
        self.assertEqual(call(real, "acquire", "--owner", "A2", "--slots", "1")[0], 3)

    # ---- the L5 incident: an OLD-code waiter / blanket kill must not hurt a live N-slot lease ----
    OLD = Path(__file__).resolve().parents[3].parent / "gameslot_old_backup.py"

    def test_old_code_waiter_cannot_double_grant_over_live_slots(self):
        """An old single-slot process only reads runs/gameslot.json. While a new-style lease is live it must see BUSY."""
        m = self.gs(3)
        call(m, "acquire", "--owner", "L5", "--slots", "3")
        leg = json.loads((self.tmp / "gameslot.json").read_text())
        self.assertTrue(leg["mirror"])
        # old algorithm, verbatim: take LOCK if absent or expired or ours
        cur = json.loads((self.tmp / "gameslot.json").read_text())
        old_would_take = not (cur and cur["owner"] != "OLDLANE" and cur["expires"] > time.time())
        self.assertFalse(old_would_take, "mirror lease must block old-code acquirers")
        call(m, "release", "--owner", "L5")
        self.assertFalse((self.tmp / "gameslot.json").exists(), "mirror removed when idle")

    def test_twin_without_mirror_old_code_double_grants(self):
        m = self.gs(3)
        m._sync_legacy = lambda now=None: None   # broken: no mirror
        call(m, "acquire", "--owner", "L5", "--slots", "3")
        self.assertFalse((self.tmp / "gameslot.json").exists(), "mutant leaves old code blind -> it would grant itself")

    def test_new_code_does_not_misread_mirror_as_foreign_lease(self):
        m = self.gs(3)
        call(m, "acquire", "--owner", "A", "--slots", "1")
        self.assertEqual(call(m, "acquire", "--owner", "B", "--slots", "1")[0], 0, "mirror must not block new-style acquirers")

    def test_takeover_kills_only_registered_pids_of_the_expired_lease(self):
        m = self.gs(3)
        killed = []
        m._game_pids = lambda: [111, 222, 333]          # 222/333 belong to other lanes
        m._kill_registered.__globals__["subprocess"] = type("S", (), {"run": staticmethod(lambda a, **k: killed.append(int(a[2])))})
        call(m, "acquire", "--owner", "OLD", "--slots", "1", "--ttl", "1")
        self.assertEqual(call(m, "register-pid", "--owner", "OLD", "--pid", "111")[0], 0)
        call(m, "acquire", "--owner", "LIVE", "--slots", "1")           # unrelated, still live
        call(m, "register-pid", "--owner", "LIVE", "--pid", "222")
        p = self.tmp / "gameslots" / "slot-0.json"
        d = json.loads(p.read_text())
        d["expires"] = time.time() - 1
        p.write_text(json.dumps(d))
        self.assertEqual(call(m, "acquire", "--owner", "NEW", "--slots", "1")[0], 0)
        self.assertEqual(killed, [111], "only the expired lease's registered pid; never 222 (live lease) or 333 (unknown)")

    def test_live_lease_is_never_taken_over_and_never_kills(self):
        m = self.gs(1)
        killed = []
        m._game_pids = lambda: [111]
        m._kill_registered.__globals__["subprocess"] = type("S", (), {"run": staticmethod(lambda a, **k: killed.append(a))})
        call(m, "acquire", "--owner", "L5", "--ttl", "1500")
        call(m, "register-pid", "--owner", "L5", "--pid", "111")
        self.assertEqual(call(m, "acquire", "--owner", "X")[0], 3)
        self.assertEqual(call(m, "acquire", "--owner", "X", "--slots", "1")[0], 3)
        self.assertEqual(killed, [])
        self.assertEqual(m.status()["lease"]["owner"], "L5")

    def test_twin_blanket_kill_is_caught(self):
        m = self.gs(1)
        killed = []
        m._game_pids = lambda: [111, 222]
        m._kill_registered.__globals__["subprocess"] = type("S", (), {"run": staticmethod(lambda a, **k: killed.append(int(a[2])))})
        m._kill_registered = lambda pids: [killed.append(p) for p in m._game_pids()]   # broken: kill all game procs
        call(m, "acquire", "--owner", "OLD", "--ttl", "1")
        p = self.tmp / "gameslots" / "slot-0.json"
        d = json.loads(p.read_text())
        d["expires"] = time.time() - 1
        p.write_text(json.dumps(d))
        call(m, "acquire", "--owner", "NEW")
        self.assertEqual(sorted(killed), [111, 222], "mutant kills pids nobody registered (the L5 incident)")

    def test_default_slots_is_two(self):
        m = load(self.SRC, None, self.tmp)
        self.assertEqual(m.max_slots(), 2)

    def test_low_memory_refuses_new_slot_but_not_renewal(self):
        m = self.gs(3)
        self.assertEqual(call(m, "acquire", "--owner", "A", "--slots", "1")[0], 0)
        m._avail_gb = lambda: 1.5
        rc, out = call(m, "acquire", "--owner", "B", "--slots", "1")
        self.assertEqual(rc, 3)
        self.assertIn("low memory", out)
        self.assertEqual(call(m, "acquire", "--owner", "A", "--slots", "1")[0], 0, "renewing a held slot is not 'new'")
        self.assertEqual(call(m, "acquire", "--owner", "A", "--slots", "2")[0], 3, "growing needs headroom too")
        m._avail_gb = lambda: 3.5
        self.assertEqual(call(m, "acquire", "--owner", "B", "--slots", "1")[0], 0)

    def test_first_game_is_never_blocked_by_low_memory(self):
        m = self.gs(3)
        m._avail_gb = lambda: 0.5
        self.assertEqual(call(m, "acquire", "--owner", "A", "--slots", "1")[0], 0)
        self.assertEqual(call(m, "release", "--owner", "A")[0], 0)
        self.assertEqual(call(m, "acquire", "--owner", "OLD")[0], 0, "legacy exclusive on an idle box")

    def test_min_free_configurable(self):
        m = self.gs(3)
        call(m, "acquire", "--owner", "A", "--slots", "1")
        (self.tmp / "gameslot_config.json").write_text(json.dumps({"slots": 3, "min_free_gb": 6}))
        m._avail_gb = lambda: 5.0
        self.assertEqual(call(m, "acquire", "--owner", "B", "--slots", "1")[0], 3)

    def test_twin_no_ram_guard_is_caught(self):
        m = self.gs(3)
        m.min_free_gb = lambda: 0.0      # broken: guard disabled
        m._avail_gb = lambda: 0.2
        call(m, "acquire", "--owner", "A", "--slots", "1")
        self.assertEqual(call(m, "acquire", "--owner", "B", "--slots", "1")[0], 0, "mutant grants with 0.2 GB free")
        real = self.gs(3)
        call(real, "acquire", "--owner", "C", "--slots", "1")
        real._avail_gb = lambda: 0.2
        self.assertEqual(call(real, "acquire", "--owner", "D", "--slots", "1")[0], 3)

    def test_need_gb_is_the_callers_need_not_the_default(self):
        """prove.py --reserve-gb 0.2 with 1 copy to boot needs 0.9 GB: 1.0 GB free must not be refused by the 3.0 GB default."""
        m = self.gs(3)
        call(m, "acquire", "--owner", "A", "--slots", "1")
        m._avail_gb = lambda: 1.0
        self.assertEqual(call(m, "acquire", "--owner", "B", "--slots", "1")[0], 3, "no --need-gb: the configured default still applies")
        rc, out = call(m, "acquire", "--owner", "B", "--slots", "1", "--need-gb", "0.90")
        self.assertEqual(rc, 0, out)
        call(m, "release", "--owner", "B")
        rc, out = call(m, "acquire", "--owner", "B", "--slots", "1", "--need-gb", "1.60")
        self.assertEqual(rc, 3)
        self.assertIn("1.0 GB free < 1.6 GB required", out)

    def test_twin_need_gb_ignored_is_caught(self):
        m = self.gs(3)
        m.required_free_gb = lambda need_gb=None: m.min_free_gb()      # broken: the caller's number is dropped
        call(m, "acquire", "--owner", "A", "--slots", "1")
        m._avail_gb = lambda: 1.0
        self.assertEqual(call(m, "acquire", "--owner", "B", "--slots", "1", "--need-gb", "0.90")[0], 3, "mutant refuses what the caller said it can run on")
        real = self.gs(3)
        call(real, "acquire", "--owner", "C", "--slots", "1")
        real._avail_gb = lambda: 1.0
        self.assertEqual(call(real, "acquire", "--owner", "D", "--slots", "1", "--need-gb", "0.90")[0], 0)

    def test_available_memory_is_available_mbytes_not_free(self):
        """_avail_gb is GlobalMemoryStatusEx ullAvailPhys (= perfmon Available MBytes: standby counts), checked against an independent read."""
        import ctypes
        spec = importlib.util.spec_from_file_location("gs_real", self.SRC)
        real = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(real)

        class MS(ctypes.Structure):
            _fields_ = [("l", ctypes.c_ulong), ("load", ctypes.c_ulong), ("total", ctypes.c_ulonglong), ("avail", ctypes.c_ulonglong),
                        ("x", ctypes.c_ulonglong * 5)]
        ms = MS()
        ms.l = ctypes.sizeof(ms)
        ctypes.WinDLL("kernel32").GlobalMemoryStatusEx(ctypes.byref(ms))
        self.assertAlmostEqual(real._avail_gb(), ms.avail / 2**30, delta=0.5)


if __name__ == "__main__":
    unittest.main()
