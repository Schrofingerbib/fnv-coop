#!/usr/bin/env python3
"""Tests for effect_oracle.py and check_stage.py (synthetic snapshots derived from the goldens).
Run: python -I plugin/snapshot/tools/test_effects.py
Mutation twins: a checker that never fails on missing effects, and an observer blind to objective changes,
are run through the same oracle checks and must be caught.
"""
import copy
import gzip
import json
import os
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import check_stage  # noqa: E402
import synthetic  # noqa: E402
import watchrefs  # noqa: E402
import effect_oracle  # noqa: E402
import snaplib  # noqa: E402

GOLD = os.path.abspath(os.path.join(HERE, "..", "..", "..", "tests", "goldens", "snapshot"))
VMS01 = "FalloutNV.esm:104C2A"
DOC = "FalloutNV.esm:104C8F"
QITEM = "FalloutNV.esm:0DE1AA"


def base():
    s = snaplib.load(os.path.join(GOLD, "base.json"))
    p1 = copy.deepcopy(s["perPlayer"]["host"])
    s["perPlayer"]["p1"] = p1
    return s


def stage20_after(b):
    """What a plausible 'setstage VMS01 20' result looks like."""
    a = copy.deepcopy(b)
    q = a["shared"]["quests"][VMS01]
    q["currentStage"] = 20
    q["completedStages"] = [0, 10, 20]
    q["objectives"]["20"]["displayed"] = True
    a["shared"]["globals"]["FalloutNV.esm:0A0001"]["value"] = 5.0
    a["shared"]["refs"][DOC]["alive"] = False
    a["shared"]["karma"] += 10
    a["shared"]["reputations"]["FalloutNV.esm:0F7F85"]["fame"] += 3
    a["shared"]["globals"]["FalloutNV.esm:000038"]["value"] = 22.0  # clock: must not show up
    for pid in ("host", "p1"):
        pp = a["perPlayer"][pid]
        pp["caps"] += 50
        pp["actorValues"]["Karma"]["current"] += 10
        pp["inventory"]["FalloutNV.esm:0DE1AA"] = {"edid": "QuestItemA", "count": 2}
    return a


ORACLE = {"schema": 1, "quests": {"VMS01": {"ref": VMS01, "stages": {"20": {
    "unconditional": [
        {"type": "SetObjective", "quest": VMS01, "objective": 20, "flags": 1},
        {"type": "SetGlobal", "global": "FalloutNV.esm:0A0001", "value": 5.0},
        {"type": "KillActor", "actor": DOC},
        {"type": "ModKarma", "delta": 10, "to": "shared"},
        {"type": "AddItem", "item": "FalloutNV.esm:00000F", "count": 50, "to": "all"},
        {"type": "AddItem", "item": QITEM, "count": 1, "to": "any"},
    ],
    "conditional": [{"type": "ModReputation", "faction": "FalloutNV.esm:0F7F85", "fame": 3, "to": "shared"},
                    {"type": "ModReputation", "faction": "FalloutNV.esm:0F7F86", "infamy": 1, "to": "shared"}],
}}}}}


ORACLE_SMALL = {"schema": 1, "quests": {"VMS01": {"stages": {"20": {"unconditional": [
    {"type": "SetObjective", "quest": VMS01, "objective": 20, "flags": 1}], "conditional": []}}}}}


def run_check(checker, oracle, b, a, **kw):
    return checker(oracle, "VMS01", 20, b, a, **kw)[0]


# ---------------------------------------------------------------- mutants
def mutant_never_fails_on_missing(oracle, quest, stage, before, after, **kw):
    code, rep = check_stage.check(oracle, quest, stage, before, after, **kw)
    return (0 if code == 1 else code), rep


def mutant_observer_blind_to_objectives(oracle, quest, stage, before, after, **kw):
    orig = effect_oracle.observe
    effect_oracle.observe = lambda b, a, **k: [e for e in orig(b, a, **k) if e["type"] != "SetObjective"]
    try:
        return check_stage.check(oracle, quest, stage, before, after, **kw)
    finally:
        effect_oracle.observe = orig


def oracle_checks():
    """(name, predicate(checker)) - predicate True means the checker behaved correctly."""
    b = base()
    a = stage20_after(b)
    a_missing_obj = copy.deepcopy(a)
    a_missing_obj["shared"]["quests"][VMS01]["objectives"]["20"]["displayed"] = False
    return [
        ("accepts correct transition", lambda c: run_check(c, ORACLE, b, a) == 0),
        ("catches missing objective effect", lambda c: run_check(c, ORACLE, b, a_missing_obj) == 1),
        ("catches no-op", lambda c: run_check(c, ORACLE, b, copy.deepcopy(b)) == 1),
    ]


class Observe(unittest.TestCase):
    def setUp(self):
        self.b = base()
        self.a = stage20_after(self.b)
        self.fx = effect_oracle.observe(self.b, self.a)
        self.keys = {effect_oracle.ekey(e) for e in self.fx}

    def has(self, e):
        return effect_oracle.ekey(e) in self.keys

    def test_expected_effects_observed(self):
        for e in [
            {"type": "SetStage", "quest": VMS01, "stage": 20},
            {"type": "SetStage", "quest": VMS01, "stage": 10},
            {"type": "SetObjective", "quest": VMS01, "objective": 20, "flags": 1},
            {"type": "SetGlobal", "global": "FalloutNV.esm:0A0001", "value": 5.0},
            {"type": "KillActor", "actor": DOC},
            {"type": "ModKarma", "delta": 10, "to": "shared"},
            {"type": "ModReputation", "faction": "FalloutNV.esm:0F7F85", "fame": 3, "infamy": 0, "to": "shared"},
            {"type": "AddItem", "item": "FalloutNV.esm:00000F", "count": 50, "to": "all"},
            {"type": "AddItem", "item": QITEM, "count": 1, "to": "all"},
        ]:
            self.assertTrue(self.has(e), e)

    def test_clock_global_not_reported(self):
        self.assertFalse(any(e.get("global") == "FalloutNV.esm:000038" for e in self.fx))

    def test_per_player_karma_av_not_double_counted(self):
        self.assertFalse(any(e["type"] == "ModActorValue" and e["av"] == 23 for e in self.fx))

    def test_only_one_player_gets_item(self):
        a = copy.deepcopy(self.a)
        del a["perPlayer"]["p1"]["inventory"][QITEM]
        fx = effect_oracle.observe(self.b, a)
        items = [e for e in fx if e["type"] == "AddItem" and e["item"] == QITEM]
        self.assertEqual([e["to"] for e in items], ["host"])

    def test_identical_snapshots_no_effects(self):
        self.assertEqual(effect_oracle.observe(self.b, copy.deepcopy(self.b)), [])

    def test_deterministic_and_order_independent(self):
        a2 = json.loads(json.dumps(self.a))
        self.assertEqual(effect_oracle.observe(self.b, a2), self.fx)

    def test_start_stop_complete_and_xp(self):
        a = copy.deepcopy(self.b)
        q = a["shared"]["quests"]["FalloutNV.esm:104C00"]
        q["running"] = True
        q["completed"] = True
        b2 = copy.deepcopy(self.b)
        b2["shared"]["quests"]["FalloutNV.esm:104C00"]["running"] = False
        for pid in a["perPlayer"]:
            a["perPlayer"][pid]["actorValues"]["Speech"]["base"] += 5
        fx = {effect_oracle.ekey(e) for e in effect_oracle.observe(b2, a)}
        self.assertIn(effect_oracle.ekey({"type": "StartQuest", "quest": "FalloutNV.esm:104C00"}), fx)
        self.assertIn(effect_oracle.ekey({"type": "CompleteQuest", "quest": "FalloutNV.esm:104C00"}), fx)
        self.assertIn(effect_oracle.ekey({"type": "ModActorValue", "av": 43, "delta": 5, "to": "all"}), fx)

    def test_objective_clear_is_reported(self):
        a = copy.deepcopy(self.b)
        a["shared"]["quests"][VMS01]["objectives"]["10"]["displayed"] = False
        fx = effect_oracle.observe(self.b, a)
        self.assertTrue(any(e["type"] == "ClearObjective" and e["objective"] == 10 for e in fx))


class CheckStage(unittest.TestCase):
    def setUp(self):
        self.b = base()
        self.a = stage20_after(self.b)

    def test_pass_and_reports(self):
        code, rep = check_stage.check(ORACLE, "VMS01", 20, self.b, self.a)
        self.assertEqual(code, 0, check_stage.render(code, rep))
        self.assertEqual(rep["missing"], [])
        self.assertEqual(len(rep["conditional_hit"]), 1)   # fame +3 fired
        self.assertEqual(len(rep["conditional_miss"]), 1)  # legion infamy did not
        self.assertTrue(any(e["type"] == "SetStage" and e["stage"] == 10 for e in rep["extras"]))  # unexpected extra

    def test_missing_effect_fails_and_is_named(self):
        a = copy.deepcopy(self.a)
        a["shared"]["refs"][DOC]["alive"] = True
        code, rep = check_stage.check(ORACLE, "VMS01", 20, self.b, a)
        self.assertEqual(code, 1)
        self.assertEqual([e["type"] for e in rep["missing"]], ["KillActor"])
        self.assertIn("MISSING", check_stage.render(code, rep))

    def test_all_means_every_player(self):
        a = copy.deepcopy(self.a)
        a["perPlayer"]["p1"]["caps"] -= 50  # only host got caps
        code, rep = check_stage.check(ORACLE, "VMS01", 20, self.b, a)
        self.assertEqual(code, 1)
        self.assertEqual(rep["missing"][0]["item"], "FalloutNV.esm:00000F")

    def test_extras_only_fail_when_strict(self):
        a = copy.deepcopy(self.a)
        a["shared"]["globals"]["FalloutNV.esm:0A0002"]["value"] = 77.0  # nobody expects this
        code, rep = check_stage.check(ORACLE, "VMS01", 20, self.b, a)
        self.assertEqual(code, 0)
        self.assertTrue(any(e.get("global") == "FalloutNV.esm:0A0002" for e in rep["extras"]))
        code, _ = check_stage.check(ORACLE, "VMS01", 20, self.b, a, strict_extras=True)
        self.assertEqual(code, 1)
        a["shared"]["quests"][VMS01]["completedStages"] = [0, 20]  # drop the unrelated stage-10 extra
        code, _ = check_stage.check(ORACLE, "VMS01", 20, self.b, a, strict_extras=True, ignore_types=["SetGlobal"])
        self.assertEqual(code, 0)

    def test_noop_fails_not_vacuous(self):
        self.assertEqual(check_stage.check(ORACLE, "VMS01", 20, self.b, copy.deepcopy(self.b))[0], 1)
        o = {"schema": 1, "quests": {"VMS01": {"stages": {"20": {"unconditional": [], "conditional": []}}}}}
        self.assertEqual(check_stage.check(o, "VMS01", 20, self.b, copy.deepcopy(self.b))[0], 1)

    def test_invalid_inputs_exit_2(self):
        g = lambda n: snaplib.load(os.path.join(GOLD, n + ".json"))
        self.assertEqual(check_stage.check(ORACLE, "VMS01", 20, g("empty"), g("empty"))[0], 2)
        self.assertEqual(check_stage.check(ORACLE, "VMS01", 20, self.b, g("truncated"))[0], 2)
        self.assertEqual(check_stage.check(ORACLE, "NOPE", 20, self.b, self.a)[0], 2)
        self.assertEqual(check_stage.check(ORACLE, "VMS01", 99, self.b, self.a)[0], 2)
        o = {"schema": 1, "quests": {"VMS01": ORACLE["quests"]["VMS01"]}}
        self.assertEqual(check_stage.check(o, "VMS01", 20, self.b, self.a)[0], 0)
        a = copy.deepcopy(self.a)
        del a["shared"]["quests"][VMS01]
        a["meta"]["counts"]["quests"] -= 1
        self.assertEqual(check_stage.check(ORACLE, "VMS01", 20, self.b, a)[0], 2)

    def test_scenario_adapter(self):
        sc = {"quest": "VMS01", "ref": VMS01, "steps": [
            {"id": "start", "expect": {"both": {"quest_running": "VMS01"}}},
            {"id": "stage_20", "expect": {"both": {"objectives": {"displayed": [20], "completed": []}}}}]}
        name, q = check_stage.scenario_to_native(sc)
        oracle = {"quests": {name: q}}
        self.assertEqual(check_stage.check(oracle, "VMS01", 20, self.b, self.a)[0], 0)
        a = copy.deepcopy(self.a)
        a["shared"]["quests"][VMS01]["objectives"]["20"]["displayed"] = False
        self.assertEqual(check_stage.check(oracle, "VMS01", 20, self.b, a)[0], 1)

    def test_objective_flags_superset_ok(self):
        a = copy.deepcopy(self.a)
        a["shared"]["quests"][VMS01]["objectives"]["20"]["completed"] = True  # flags 3 satisfies expected bit0
        self.assertEqual(check_stage.check(ORACLE, "VMS01", 20, self.b, a)[0], 0)

    def test_cli_with_gz_oracle(self):
        with tempfile.TemporaryDirectory() as d:
            op = os.path.join(d, "effects.json.gz")
            with gzip.open(op, "wt", encoding="utf-8") as f:
                json.dump(ORACLE, f)
            bp, ap = os.path.join(d, "b.json"), os.path.join(d, "a.json")
            for path, obj in ((bp, self.b), (ap, self.a)):
                with open(path, "w") as f:
                    json.dump(obj, f)
            py = [sys.executable, "-I", os.path.join(HERE, "check_stage.py")]
            q = dict(stdout=subprocess.DEVNULL)
            self.assertEqual(subprocess.call(py + ["--oracle", op, "VMS01", "20", bp, ap], **q), 0)
            self.assertEqual(subprocess.call(py + ["--oracle", op, "VMS01", "20", bp, bp], **q), 1)
            self.assertEqual(subprocess.call(py + ["--oracle", op, "VMS01", "21", bp, ap], **q), 2)
            self.assertEqual(subprocess.call([sys.executable, "-I", os.path.join(HERE, "effect_oracle.py"), bp, ap], **q), 0)


class ScriptVars(unittest.TestCase):
    def setUp(self):
        self.b = base()
        self.b["shared"]["refs"][DOC]["vars"] = {"iTalked": 0, "fTimer": 1.5}
        self.a = copy.deepcopy(self.b)

    def vars_fx(self, a):
        return [e for e in effect_oracle.observe(self.b, a) if e["type"] == "SetScriptVar"]

    def test_quest_var_change(self):
        self.a["shared"]["quests"][VMS01]["vars"]["iDocState"] = 3
        self.assertEqual(self.vars_fx(self.a), [{"type": "SetScriptVar", "owner": VMS01, "var": "iDocState", "value": 3}])

    def test_ref_var_change_and_within_tolerance(self):
        self.a["shared"]["refs"][DOC]["vars"]["iTalked"] = 1
        self.a["shared"]["refs"][DOC]["vars"]["fTimer"] += 0.00001
        self.assertEqual([(e["owner"], e["var"], e["value"]) for e in self.vars_fx(self.a)], [(DOC, "iTalked", 1)])

    def test_new_var_nonzero_reported_zero_not(self):
        self.a["shared"]["quests"][VMS01]["vars"].update({"iNew": 2, "iZero": 0})
        self.assertEqual([e["var"] for e in self.vars_fx(self.a)], ["iNew"])

    def test_ref_without_vars_before(self):
        del self.b["shared"]["refs"][DOC]["vars"]
        self.a["shared"]["refs"][DOC]["vars"] = {"iTalked": 1}
        self.assertEqual(len(self.vars_fx(self.a)), 1)

    def test_check_stage_expects_script_var(self):
        a = stage20_after(self.b)
        a["shared"]["quests"][VMS01]["vars"]["iDocState"] = 2
        o = copy.deepcopy(ORACLE)
        o["quests"]["VMS01"]["stages"]["20"]["unconditional"].append(
            {"type": "SetScriptVar", "owner": VMS01, "var": "iDocState", "value": 2})
        self.assertEqual(check_stage.check(o, "VMS01", 20, self.b, a)[0], 0)
        a["shared"]["quests"][VMS01]["vars"]["iDocState"] = 1  # unchanged from base => effect missing
        code, rep = check_stage.check(o, "VMS01", 20, self.b, a)
        self.assertEqual(code, 1)
        self.assertEqual(rep["missing"][0]["type"], "SetScriptVar")

    def test_snapdiff_sees_ref_var(self):
        import snapdiff
        self.a["shared"]["refs"][DOC]["vars"]["iTalked"] = 1
        self.assertEqual(snapdiff.compare(self.b, self.a)[0], 1)
        self.assertEqual(snapdiff.compare(self.b, copy.deepcopy(self.b))[0], 0)

    def test_schema_accepts_ref_vars_and_rejects_nonnumeric(self):
        self.assertEqual(snaplib.guard(self.b), [])
        self.b["shared"]["refs"][DOC]["vars"]["iTalked"] = "yes"
        self.assertNotEqual(snaplib.guard(self.b), [])

    def test_mutant_observer_blind_to_vars_is_caught(self):
        orig = effect_oracle._var_effects
        effect_oracle._var_effects = lambda *a, **k: []
        try:
            self.a["shared"]["refs"][DOC]["vars"]["iTalked"] = 1
            blind = self.vars_fx(self.a)
        finally:
            effect_oracle._var_effects = orig
        self.assertEqual(blind, [])           # the mutant misses it ...
        self.assertEqual(len(self.vars_fx(self.a)), 1)  # ... the real observer does not


class WatchRefs(unittest.TestCase):
    def test_from_text_and_m1(self):
        t = "# x\nFalloutNV.esm:00abcd some words\nnothing\n'DeadMoney.esm:000010',FalloutNV.esm:00ABCD\n"
        self.assertEqual(sorted(watchrefs.from_text(t)), ["DeadMoney.esm:000010", "FalloutNV.esm:00ABCD"])
        doc = {"quests": [{"npcs": [{"ref": "FalloutNV.esm:103E55", "sig": "REFR"},
                                   {"ref": "FalloutNV.esm:12429A", "sig": "FURN"}],
                           "objectives": [{"targets": [{"ref": "FalloutNV.esm:104c13", "sig": "REFR"}]}]}]}
        self.assertEqual(sorted(watchrefs.from_m1(doc)), ["FalloutNV.esm:103E55", "FalloutNV.esm:104C13"])

    def test_hostowned_runtime_ids(self):
        NL = chr(10)
        t = NL.join(["# load order: 00=FalloutNV.esm, 01=DeadMoney.esm", "0007F8C0", "01000010", "05000001", ""])
        self.assertEqual(sorted(watchrefs.from_hostowned(t)), ["DeadMoney.esm:000010", "FalloutNV.esm:07F8C0"])

    def test_collect_tolerates_missing_and_merges(self):
        with tempfile.TemporaryDirectory() as d:
            h, m = os.path.join(d, "h.txt"), os.path.join(d, "m.json")
            with open(h, "w") as f:
                f.write("FalloutNV.esm:000001\nFalloutNV.esm:000002\n")
            with open(m, "w") as f:
                json.dump({"npcs": [{"ref": "FalloutNV.esm:000002", "sig": "REFR"}]}, f)
            refs, notes = watchrefs.collect(h, m)
            self.assertEqual(refs, ["FalloutNV.esm:000001", "FalloutNV.esm:000002"])
            refs, notes = watchrefs.collect(os.path.join(d, "nope.txt"), m)
            self.assertEqual(refs, ["FalloutNV.esm:000002"])
            self.assertTrue(any("MISSING" in n for n in notes))
            out = os.path.join(d, "o", "w.txt")
            self.assertEqual(watchrefs.main(["-o", out, "--hostowned", h, "--m1", m]), 0)
            with open(out) as f:
                self.assertEqual([l.strip() for l in f if not l.startswith("#")],
                                 ["FalloutNV.esm:000001", "FalloutNV.esm:000002"])
            self.assertEqual(watchrefs.main(["-o", out + "2", "--hostowned", d + "/x", "--m1", d + "/y"]), 2)
            self.assertFalse(os.path.exists(out + "2"))

    def test_real_m1_file_if_present(self):
        m1 = os.path.abspath(os.path.join(HERE, "..", "..", "..", "out", "questdata", "m1_goodsprings.json"))
        if not os.path.exists(m1):
            self.skipTest("m1 not generated")
        refs, _ = watchrefs.collect(None, m1)
        self.assertGreater(len(refs), 20)


class SyntheticHelper(unittest.TestCase):
    def test_base_is_valid(self):
        b = synthetic.base_snapshot(players=("host", "p1"))
        self.assertEqual(snaplib.guard(b, ("shared", "perPlayer")), [])
        self.assertEqual(b["meta"]["counts"]["quests"], 60)

    def test_mutations_by_edid_and_counts_stay_in_sync(self):
        b = synthetic.base_snapshot()
        a = synthetic.make_synthetic(b, {
            "shared/quests/VMS01/currentStage": 20,
            "shared/quests/VMS01/completedStages": [0, 10, 20],
            "shared/quests/VMS01/objectives/20/displayed": True,
            "shared/globals/TestGlobal01/value": 5.0,
            "perPlayer/host/caps": 150,
            "shared/quests/FalloutNV.esm:200000": synthetic.Add(synthetic.quest_entry("NewQuest", 0)),
            "shared/globals/TestGlobal02": synthetic.DELETE})
        self.assertEqual(snaplib.guard(a, ("shared", "perPlayer")), [])
        self.assertEqual(a["meta"]["counts"], {"quests": 61, "globals": 19, "reputations": 2})
        self.assertEqual(b["shared"]["quests"][synthetic.VMS01]["currentStage"], 10)  # base untouched
        code, rep = check_stage.check(ORACLE_SMALL, "VMS01", 20, b, a)
        self.assertEqual(code, 0, check_stage.render(code, rep))

    def test_small_snapshots_need_explicit_min(self):
        small = synthetic.base_snapshot(n_quests=3, n_globals=2)
        after = synthetic.make_synthetic(small, {"shared/quests/VMS01/currentStage": 20,
                                                 "shared/quests/VMS01/completedStages": [0, 10, 20],
                                                 "shared/quests/VMS01/objectives/20/displayed": True})
        self.assertEqual(check_stage.check(ORACLE_SMALL, "VMS01", 20, small, after)[0], 2)  # guard: too few quests
        self.assertEqual(check_stage.check(ORACLE_SMALL, "VMS01", 20, small, after, min_quests=1, min_globals=1)[0], 0)

    def test_tools_are_ascii_and_console_safe(self):
        for fn in sorted(os.listdir(HERE)):
            if fn.endswith(".py"):
                with open(os.path.join(HERE, fn), "rb") as f:
                    f.read().decode("ascii")  # raises on any non-ASCII byte
        env = dict(os.environ, PYTHONIOENCODING="cp1252")
        for tool in ("check_stage.py", "snapdiff.py", "converge.py", "effect_oracle.py", "watchrefs.py"):
            r = subprocess.run([sys.executable, "-I", os.path.join(HERE, tool), "--help"], env=env,
                               capture_output=True)
            self.assertNotIn(b"Traceback", r.stderr, tool)


class MutationTwins(unittest.TestCase):
    def test_real_checker_passes_all(self):
        for name, pred in oracle_checks():
            self.assertTrue(pred(check_stage.check), name)

    def test_never_fails_mutant_caught(self):
        results = {n: p(mutant_never_fails_on_missing) for n, p in oracle_checks()}
        self.assertFalse(results["catches missing objective effect"])
        self.assertFalse(results["catches no-op"])
        self.assertTrue(results["accepts correct transition"])  # control

    def test_blind_observer_mutant_caught(self):
        results = {n: p(mutant_observer_blind_to_objectives) for n, p in oracle_checks()}
        self.assertFalse(results["accepts correct transition"] and results["catches missing objective effect"])
        self.assertTrue(results["catches no-op"])  # control: still sees stage


if __name__ == "__main__":
    unittest.main(verbosity=2)
