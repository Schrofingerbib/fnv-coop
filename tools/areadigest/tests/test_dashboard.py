"""Tests for the area desync dashboard (tools/areadigest/dashboard.py) and its hook in the playbook engine.

Run through tools/areadigest/run_tests.py (it also runs every mutation twin: AREADIGEST_MUTANT=<name>, one deliberate bug each).
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
TOOLS = HERE.parent.parent
sys.path.insert(0, str(TOOLS))
sys.path.insert(0, str(TOOLS / "bots"))
sys.path.insert(0, str(TOOLS / "flight"))

from areadigest import dashboard as dash   # noqa: E402


def cat_row(total=0, per_min=0, mismatches=0, **extra):
    row = {"total": total, "perMin": per_min, "mismatches": mismatches}
    row.update(extra)
    return row


def status(role="client", story=None, actors=None, containers=None, objects=None, bugs=None, enabled=True):
    rows = {"story": story or cat_row(), "actors": actors or cat_row(), "containers": containers or cat_row(), "objects": objects or cat_row()}
    return {"enabled": enabled, "role": role,
            "heals": {"total": sum(r["total"] for r in rows.values()), "perMin": sum(r["perMin"] for r in rows.values()), "byCat": rows},
            "bugs": bugs or []}


class Parse(unittest.TestCase):
    def test_disabled_or_missing_machines_are_not_summed(self):
        self.assertIsNone(dash.parse(None))
        self.assertIsNone(dash.parse("not json"))
        self.assertIsNone(dash.parse({"enabled": False}))
        self.assertIsNone(dash.parse(json.dumps({"enabled": False})))                  # twin disabled_counts
        self.assertEqual(dash.parse(json.dumps(status()))["role"], "client")
        s = dash.summarize({"host": status("host"), "client1": {"enabled": False}, "client2": None})
        self.assertEqual((s["machines"], s["running"], s["disabled"]), (3, 1, ["client1", "client2"]))


class Summary(unittest.TestCase):
    def test_every_machine_and_category_is_summed(self):
        s = dash.summarize({
            "host": status("host", story=cat_row(1, 1, 2)),
            "client1": status(actors=cat_row(2, 1, 3), objects=cat_row(1, 0, 0)),
            "client2": status(actors=cat_row(1, 1, 1)),
        })
        self.assertEqual(s["total"], 5)                                                # twin host_only
        self.assertEqual(s["perMin"], 3)
        self.assertEqual(s["mismatches"], 6)
        self.assertEqual(s["byCat"]["actors"], {"total": 3, "perMin": 2, "mismatches": 4})
        self.assertEqual(s["byCat"]["story"]["total"], 1)
        self.assertEqual(s["perMachine"]["client1"]["table"]["objects"]["total"], 1)

    def test_the_line_shows_heals_per_minute_per_category(self):
        s = dash.summarize({"host": status("host"), "client1": status(actors=cat_row(total=7, per_min=2))})
        line = dash.dashboard_line(s)
        self.assertIn("heals/min story 0 actors 2 containers 0 objects 0", line)      # twin permin_is_total: shows 7
        self.assertIn("heals total 7", line)
        self.assertIn("machines 2/2", line)
        self.assertNotIn("\n", line)

    def test_bug_lines_carry_both_values(self):
        bug = {"cat": "actors", "key": "0x5", "cell": "0x100", "peer": 1, "count": 3, "authority": "actor 0x5 hp 100", "other": "actor 0x5 hp 30"}
        s = dash.summarize({"host": status("host", bugs=[bug])})
        self.assertEqual(len(s["bugs"]), 1)                                            # twin bug_ignored
        self.assertIn("hp 100", s["bugs"][0])
        self.assertIn("hp 30", s["bugs"][0])


class Failures(unittest.TestCase):
    def test_zero_heals_is_the_target(self):
        clean = dash.summarize({"host": status("host"), "client1": status()})
        self.assertEqual(dash.failures(clean), [])
        dirty = dash.summarize({"host": status("host", story=cat_row(1, 1, 1)), "client1": status()})
        bad = dash.failures(dirty)
        self.assertEqual(len(bad), 1)                                                  # twin assert_skips_story
        self.assertIn("story 1", bad[0])
        self.assertEqual(dash.failures(dirty, max_heals=1), [])

    def test_per_minute_budget_and_bugs_and_silent_modules(self):
        s = dash.summarize({"host": status("host", actors=cat_row(3, 3, 0)), "client1": {"enabled": False}})
        self.assertTrue(any("last minute" in f for f in dash.failures(s, max_heals=10, max_per_min=2)))
        self.assertTrue(any("only 1 of 2" in f for f in dash.failures(s, max_heals=10, need_running=2)))
        bug = {"cat": "actors", "key": "1", "count": 2, "authority": "a", "other": "b"}
        sb = dash.summarize({"host": status("host", bugs=[bug])})
        self.assertTrue(any("bug line" in f for f in dash.failures(sb)))
        self.assertEqual(dash.failures(sb, allow_bugs=1), [])


class Fetch(unittest.TestCase):
    class Nv:
        def __init__(self, result=None, fail=False):
            self.result, self.fail, self.calls = result, fail, 0

        def call(self, method, *a, **k):
            self.calls += 1
            assert method == "areadigest.status"
            if self.fail:
                raise RuntimeError("unknown method")
            return self.result

    class Bot:
        def __init__(self, nv):
            self.nv = nv

    def test_a_bot_without_the_method_is_asked_once(self):
        nv = self.Nv(fail=True)
        bot = self.Bot(nv)
        self.assertIsNone(dash.fetch_status(bot))
        self.assertIsNone(dash.fetch_status(bot))
        self.assertEqual(nv.calls, 1)

    def test_status_comes_through(self):
        bot = self.Bot(self.Nv(status("host")))
        self.assertEqual(dash.fetch_status(bot)["role"], "host")
        self.assertEqual(dash.collect({"a": bot})["a"]["role"], "host")
        self.assertIsNone(dash.fetch_status(object()))


class Nvfr(unittest.TestCase):
    def test_flight_dump_meta_gives_the_same_numbers(self):
        import nvfr
        meta = {"label": "client", "heals.story.total": "0", "heals.story.perMin": "0", "heals.story.mismatches": "1",
                "heals.actors.total": "2", "heals.actors.perMin": "1", "heals.actors.mismatches": "3",
                "heals.containers.total": "0", "heals.objects.total": "1", "heals.total": "3", "heals.perMin": "1"}
        records = [{"type": nvfr.T_META, "mono_us": 0, "frame": 0, "meta": meta},
                   {"type": nvfr.T_MARKER, "mono_us": 5, "frame": 1, "kind": 8, "text": "heal actors key=5"}]
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "client.nvfr")
            nvfr.dump(p, {"role": 1}, records)
            st = dash.from_nvfr(p)
        self.assertTrue(st["enabled"])                                                # twin nvfr_meta_ignored
        self.assertEqual(st["heals"]["total"], 3)
        self.assertEqual(st["heals"]["byCat"]["actors"]["total"], 2)
        self.assertEqual(st["healMarkers"], 1)
        s = dash.summarize({"client": st})
        self.assertEqual(s["total"], 3)
        self.assertIn("actors", dash.failures(s)[0])


class PlaybookHook(unittest.TestCase):
    """tools/bots/playbooks/core.py merges areadigest.status into each machine's status and counts the total as the desync counter areaHeals."""

    def setUp(self):
        from playbooks import core
        self.core = core

    class Machine:
        def __init__(self, name, nv):
            self.name = name
            self.bot = type("B", (), {"nv": nv})()

    class Ctx:
        def __init__(self, machines):
            self.present = machines

    def test_counter_is_registered_and_must_not_rise(self):
        c = self.core
        self.assertEqual(c.COUNTER_PATHS["areaHeals"], ("areadigest", "heals", "total"))
        self.assertIn("areaHeals", c.MUST_NOT_RISE)
        self.assertIn("areaHeals", c.REPAIR_COUNTERS)                                  # tolerated like repairs under chaos profiles
        st = {"role": "client", "areadigest": status(actors=cat_row(2, 2, 2))}
        self.assertEqual(c.read_counters(st)["areaHeals"], 2)
        self.assertEqual(c.read_counters({"role": "client"})["areaHeals"], 0)

    def test_status_is_merged_when_the_module_answers(self):
        nv_ok = Fetch.Nv(status("host", actors=cat_row(1, 1, 1)))
        nv_off = Fetch.Nv({"enabled": False})
        nv_bad = Fetch.Nv(fail=True)
        ms = [self.Machine("host", nv_ok), self.Machine("client1", nv_off), self.Machine("client2", nv_bad)]
        out = {m.name: {"role": "x"} for m in ms}
        self.core.Ctx._attach_area_status(self.Ctx(ms), out)
        self.assertEqual(out["host"]["areadigest"]["heals"]["total"], 1)
        self.assertNotIn("areadigest", out["client1"])                                  # disabled: nothing merged
        self.assertNotIn("areadigest", out["client2"])
        self.core.Ctx._attach_area_status(self.Ctx(ms), out)
        self.assertEqual(nv_bad.calls, 1)                                               # the method that does not exist is asked once

    def test_a_bot_with_no_bridge_is_left_alone(self):
        m = type("M", (), {})()
        m.name = "host"
        m.bot = object()
        out = {"host": {"role": "host"}}
        self.core.Ctx._attach_area_status(self.Ctx([m]), out)
        self.assertEqual(out, {"host": {"role": "host"}})


if __name__ == "__main__":
    unittest.main()
