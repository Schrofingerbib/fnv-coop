#!/usr/bin/env python3
"""Tests for the generalized sweep: config normalization (all real questline configs), setup translation/rejection,
variant expansion, resume detection, L9 summary and per-questline report. Mutation twins included.
Run: python -I plugin/snapshot/tools/test_sweep_driver.py
"""
import glob
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import goodsprings_sweep as gs  # noqa: E402
import sweep_config as sc  # noqa: E402

REPO = Path(sc.REPO)
CONFIGS = sorted(glob.glob(str(REPO / "questlines" / "*" / "sweep.json"))) + [str(REPO / "out" / "questdata" / "m1_goodsprings.json")]
ORDER = sc.load_loadorder()


def write_cfg(d, body):
    p = os.path.join(d, "sweep.json")
    with open(p, "w", encoding="utf-8") as f:
        json.dump(body, f)
    return p


@unittest.skipUnless(any(os.path.exists(p) for p in CONFIGS),
                     'sweep configs (questlines/*/sweep.json) are generated from your own game data; not in this repo')
class RealConfigs(unittest.TestCase):
    def plans(self):
        return [(p, sc.load_config(p)) for p in CONFIGS if os.path.exists(p)]

    def test_every_config_loads_with_stages_and_no_warnings(self):
        plans = self.plans()
        self.assertGreaterEqual(len(plans), 2)
        for p, plan in plans:
            self.assertTrue(any(q["stages"] for q in plan["quests"]), p)
            self.assertEqual(plan["warnings"], [], p)

    def test_runtime_ids_carry_the_dlc_load_order_prefix(self):
        for p, plan in self.plans():
            for q in plan["quests"]:
                idx = [x.lower() for x in ORDER].index(q["plugin"].lower())
                self.assertEqual(q["hex"][:2], "%02X" % idx, (p, q["edid"]))
                self.assertRegex(q["hex"], r"^[0-9A-F]{8}$")
                for s in q["stages"]:
                    self.assertTrue(s["command"].strip(), (p, q["edid"], s["index"]))

    def test_default_command_is_setstage_with_hex(self):
        for p, plan in self.plans():
            for q in plan["quests"]:
                for s in q["stages"]:
                    if s["command"].lower().startswith("setstage"):
                        self.assertRegex(s["command"], r"^setstage [0-9A-Fa-f]{6,8} \d+", (p, q["edid"], s))

    def test_reset_ids_are_hex8_and_cover_quests_with_stages(self):
        for p, plan in self.plans():
            self.assertTrue(plan["reset_ids"], p)
            for r in plan["reset_ids"]:
                self.assertRegex(r, r"^[0-9A-F]{8}$")

    def test_all_setup_verbs_are_known(self):
        known = {"setstage", "startquest", "set", "moveto", "additem", "addperk", "setobjectivedisplayed",
                 "setobjectivecompleted", "stopquest", "completequest"}
        seen = set()
        for p, plan in self.plans():
            for q in plan["quests"]:
                allc = list(q["setup"])
                for s in q["stages"]:
                    allc += s["setup"] + [s["command"]]
                    for v in s["variants"]:
                        allc += v["setup"]
                seen |= {sc.verb(c) for c in allc}
        self.assertLessEqual(seen, known, seen - known)

    def test_twin_loader_without_prefix_is_caught(self):
        orig = sc.hex8
        sc.hex8 = lambda plugin, local, order: "%08X" % (local & 0xFFFFFF)   # forgets the load-order byte
        try:
            bad = 0
            for p in CONFIGS:
                if not os.path.exists(p):
                    continue
                plan = sc.load_config(p, ORDER)
                for q in plan["quests"]:
                    idx = [x.lower() for x in ORDER].index(q["plugin"].lower())
                    if q["hex"][:2] != "%02X" % idx:
                        bad += 1
            self.assertGreater(bad, 0)    # the prefix test above would fail on this loader
        finally:
            sc.hex8 = orig


class Normalize(unittest.TestCase):
    def test_shapes(self):
        body = {"quests": [
            {"edid": "A", "ref": "DeadMoney.esm:005229", "setup": ["startquest 01005229"],
             "stages": [{"index": 10, "setup": [{"cmd": "setstage 01005229 5"}], "chain": [5], "variants": [{"name": "v1", "setup": ["set A.b to 1"]}]},
                        {"index": 20, "command": "setstage 01005229 20", "sweep": False, "exclude_reason": "cutscene"},
                        {"index": 30, "cmds": ["setstage 01005229 30"]}],
             "excluded_stages": [{"index": 40, "reason": "x"}]},
            {"edid": "B", "ref": "FalloutNV.esm:0842DD", "setup": {"quest": [{"cmd": "startquest 000842DD"}], "stages": {"10": ["set B.x to 2"]}},
             "stages": [10, {"index": 15, "host": "setstage 000842DD 15"}], "run_last": True},
            {"edid": "C", "ref": "NoSuch.esm:000001", "stages": [1]},
        ], "reset": [{"op": "reset", "quests": ["842DD"]}], "reset_ids": ["01005229"]}
        with tempfile.TemporaryDirectory() as d:
            plan = sc.load_config(write_cfg(d, body), ORDER)
        names = [q["edid"] for q in plan["quests"]]
        self.assertEqual(names, ["A", "B"])                       # C skipped (unknown plugin), B (run_last) after A
        a, b = plan["quests"]
        self.assertEqual(a["hex"], "01005229")
        self.assertEqual([s["index"] for s in a["stages"]], [10, 30])
        self.assertEqual(a["stages"][0]["setup"][:2], ["setstage 01005229 5", "setstage 01005229 5"])   # chain + explicit setup
        self.assertEqual(a["stages"][0]["variants"][0]["setup"], ["set A.b to 1"])
        self.assertEqual(a["stages"][1]["command"], "setstage 01005229 30")
        self.assertEqual({e["index"]: e["reason"] for e in a["excluded"]}, {20: "cutscene", 40: "x"})
        self.assertEqual(b["setup"], ["startquest 000842DD"])
        self.assertEqual(b["stages"][0]["setup"], ["set B.x to 2"])              # legion-style setup.stages
        self.assertEqual(b["stages"][1]["command"], "setstage 000842DD 15")
        self.assertTrue(any("not in the load order" in w for w in plan["warnings"]))
        self.assertEqual(sorted(plan["reset_ids"]), ["000842DD", "01005229"])

    def test_mismatching_config_id_warns_and_computed_wins(self):
        body = {"quests": [{"edid": "A", "ref": "HonestHearts.esm:008891", "console_id": "00008891", "stages": [10]}]}
        with tempfile.TemporaryDirectory() as d:
            plan = sc.load_config(write_cfg(d, body), ORDER)
        self.assertEqual(plan["quests"][0]["hex"], "02008891")
        self.assertTrue(any("differs" in w for w in plan["warnings"]))


class FakeNV:
    def __init__(self, exec_out="", ok=True):
        self.calls, self.exec_out, self.ok = [], exec_out, ok

    def call(self, method, params=None, timeout_s=0):
        self.calls.append((method, params))
        if method == "test.quest.setvar" and params["quest"] == "DEAD":
            raise RuntimeError("setvar failed: no such quest")
        return {}

    def exec(self, cmd, timeout_ms=None):
        self.calls.append(("exec", cmd))
        return {"ok": self.ok, "output": self.exec_out}


PLAN = {"loadorder": ORDER, "quests": [{"edid": "VMQ01", "hex": "000842DD"}]}


class Setup(unittest.TestCase):
    def test_set_goes_through_the_helper_by_hex_and_by_edid(self):
        nv = FakeNV()
        gs.run_setup_cmd(nv, PLAN, "set 11345D.BennyGotoPresidential to 1")
        gs.run_setup_cmd(nv, PLAN, "set VMQ01.bPrimmClosed to 0")
        self.assertEqual(nv.calls, [("test.quest.setvar", {"quest": "11345D", "var": "BennyGotoPresidential", "value": 1.0}),
                                    ("test.quest.setvar", {"quest": "000842DD", "var": "bPrimmClosed", "value": 0.0})])

    def test_other_commands_use_the_console(self):
        nv = FakeNV()
        gs.run_setup_cmd(nv, PLAN, "startquest 000842DD")
        self.assertEqual(nv.calls, [("exec", "startquest 000842DD")])

    @unittest.skipUnless(os.path.exists(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..', '..', 'out', 'questdata', 'quests.json')),
                         'needs out/questdata/quests.json generated from your own game data')
    def test_rejected_commands_are_errors_not_silent(self):
        for out, ok in (("Error line 1 Could not parse this line", True), ("", False), ("Unknown command foo", True)):
            with self.assertRaises(gs.SetupError):
                gs.run_setup_cmd(FakeNV(out, ok), PLAN, "startquest 000842DD")
        with self.assertRaises(gs.SetupError):
            gs.run_setup_cmd(FakeNV(), PLAN, "set DEAD.x to 1")                 # helper failure surfaces
        with self.assertRaises(gs.SetupError):
            gs.run_setup_cmd(FakeNV(), PLAN, "set NoSuchQuestEdid.x to 1")      # unresolvable quest

    def test_twin_console_only_translation_would_accept_a_rejected_set(self):
        # the unsafe alternative: sending `set` to the console and ignoring its output; the helper path must be what runs
        nv = FakeNV("Could not parse this line")
        gs.run_setup_cmd(nv, PLAN, "set 11345D.x to 1")      # no exception: the console is never asked
        self.assertEqual(nv.calls[0][0], "test.quest.setvar")


class ResetIds(unittest.TestCase):
    """Reset ids must carry the load-order prefix (a prefix-less DLC id resets the WRONG form)."""

    def cfg(self, reset_ids):
        body = {"reset_ids": reset_ids, "quests": [
            {"edid": "DLC2", "ref": "HonestHearts.esm:008891", "stages": [10]},
            {"edid": "DLC3", "ref": "OldWorldBlues.esm:002FCA", "stages": [10]},
            {"edid": "BASE", "ref": "FalloutNV.esm:0842DD", "stages": [10]}]}
        with tempfile.TemporaryDirectory() as d:
            return sc.load_config(write_cfg(d, body), ORDER)

    def test_default_reset_ids_are_the_prefixed_console_ids(self):
        self.assertEqual(sorted(self.cfg([])["reset_ids"]), ["000842DD", "02008891", "03002FCA"])

    def test_config_reset_ids_are_honoured(self):
        plan = self.cfg(["02008891"])
        self.assertEqual(plan["reset_ids"], ["02008891"])

    def test_prefixless_dlc_reset_id_is_upgraded_with_a_warning(self):
        plan = self.cfg(["8891", "000842DD", "2FCA"])
        self.assertEqual(plan["reset_ids"], ["02008891", "000842DD", "03002FCA"])
        self.assertEqual(sum("no load-order prefix" in w for w in plan["warnings"]), 2)

    def test_base_game_ids_are_never_rewritten(self):
        self.assertEqual(self.cfg(["000842DD"])["reset_ids"], ["000842DD"])

    def test_every_real_config_resets_only_prefixed_dlc_ids(self):
        for p in CONFIGS:
            if not os.path.exists(p):
                continue
            plan = sc.load_config(p, ORDER)
            dlc_locals = {int(q["hex"], 16) & 0xFFFFFF: q["hex"] for q in plan["quests"] if not q["hex"].startswith("00")}
            for r in plan["reset_ids"]:
                if r.startswith("00"):
                    self.assertNotIn(int(r, 16) & 0xFFFFFF, dlc_locals, (p, r))   # a base id that is really a DLC quest

    def test_twin_percent_x_local_id_drops_the_prefix(self):
        # the old driver built reset ids as "%X" % local_id: for a DLC quest that is NOT the console id
        plan = self.cfg([])
        bad = ["%X" % (int(q["hex"], 16) & 0xFFFFFF) for q in plan["quests"]]
        self.assertNotEqual(sorted(x.rjust(8, "0") for x in bad), sorted(plan["reset_ids"]))


class Items(unittest.TestCase):
    def plan(self):
        body = {"quests": [{"edid": "A", "ref": "FalloutNV.esm:0842DD", "stages": [
            {"index": 10, "variants": [{"name": "open"}, {"name": "closed gate!"}]}, {"index": 20}, {"index": 30}]}]}
        with tempfile.TemporaryDirectory() as d:
            return sc.load_config(write_cfg(d, body), ORDER)

    def test_variants_expand_and_names_are_file_safe(self):
        items = gs.plan_items(self.plan())
        self.assertEqual([(i["stage"], i["variant"]["name"] if i["variant"] else None) for i in items],
                         [(10, "open"), (10, "closed gate!"), (20, None), (30, None)])
        self.assertEqual(gs.item_name("A", 10, "closed gate!"), "A_10__closed_gate_")
        self.assertEqual(gs.item_name("A", 20), "A_20")
        self.assertEqual(len(gs.plan_items(self.plan(), only_stages={20})), 1)
        self.assertEqual(gs.plan_items(self.plan(), only_quests={"Z"}), [])

    def test_resume_picks_the_latest_unfinished_run(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            for name, done in (("2026-01-01T0000-x", True), ("2026-01-02T0000-x", False), ("2026-01-03T0000-x", True)):
                (root / name / "stages").mkdir(parents=True)
                if done:
                    (root / name / "done.json").write_text("{}")
            self.assertEqual(gs.latest_unfinished(root), "2026-01-02T0000-x")
            (root / "2026-01-02T0000-x" / "done.json").write_text("{}")
            self.assertIsNone(gs.latest_unfinished(root))
            self.assertIsNone(gs.latest_unfinished(root / "nope"))


class Outputs(unittest.TestCase):
    def test_summary_and_report(self):
        body = {"slug": "demo", "quests": [
            {"edid": "A", "ref": "FalloutNV.esm:0842DD", "stages": [10, 20]},
            {"edid": "B", "ref": "FalloutNV.esm:000DA115", "stages": []},
            {"edid": "C", "ref": "FalloutNV.esm:0015FFEC", "stages": [5, {"index": 6, "sweep": False, "exclude_reason": "cutscene"}]}]}
        with tempfile.TemporaryDirectory() as d:
            plan = sc.load_config(write_cfg(d, body), ORDER)
            results = {("A", 10, None): {"quest": "A", "stage": 10, "status": "pass", "seconds": 1.5},
                       ("A", 20, None): {"quest": "A", "stage": 20, "status": "fail", "seconds": 2.0, "reasons": ["missing_capture:1"]},
                       ("C", 5, None): {"quest": "C", "stage": 5, "status": "error", "seconds": 0.0, "detail": "SetupError"}}
            old = gs.REPO
            gs.REPO = Path(d)
            try:
                summ = gs.write_summary("run1", plan, results, "t0", "t1")
                items = gs.plan_items(plan)
                tot = gs.write_report(Path(d), plan, items, results, "run1")
            finally:
                gs.REPO = old
            by = {r["quest"]: r for r in summ["results"]}
            self.assertEqual((by["A"]["status"], by["A"]["failed_steps"], by["A"]["steps"]), ("fail", ["stage_20"], 2))
            self.assertEqual(by["B"]["status"], "skip")
            self.assertEqual(by["C"]["status"], "error")
            on_disk = json.loads((Path(d) / "runs" / "run1" / "summary.json").read_text())
            self.assertEqual((on_disk["kind"], on_disk["schema"], on_disk["run_id"]), ("game", 1, "run1"))
            self.assertEqual(tot, {"pass": 1, "fail": 1, "error": 1})
            report = (Path(d) / "report.md").read_text()
            self.assertIn("| A | 20 |  | fail | missing_capture:1 |", report)
            self.assertIn("excluded (not swept):", report)
            self.assertIn("cutscene", report)

    def test_summary_loads_in_the_l9_status_model(self):
        sys.path.insert(0, str(REPO / "tools" / "coverage"))
        try:
            import status  # noqa: E402
        except Exception:
            self.skipTest("L9 status model not importable")
        with tempfile.TemporaryDirectory() as d:
            runs = Path(d) / "runs" / "2026-10-16T0000-demo"
            runs.mkdir(parents=True)
            (runs / "summary.json").write_text(json.dumps({"schema": 1, "run_id": runs.name, "kind": "game", "started": "2026-10-16T00:00:00",
                                                           "finished": "2026-10-16T00:10:00",
                                                           "results": [{"quest": "A", "status": "pass", "steps": 2, "failed_steps": [], "duration_s": 1.0}]}))
            loader = getattr(status, "load_runs", None)
            if loader is None:
                self.skipTest("no load_runs in L9 status")
            got = loader(str(Path(d) / "runs"))
            self.assertTrue(got)


if __name__ == "__main__":
    unittest.main(verbosity=2)
