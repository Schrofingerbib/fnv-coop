#!/usr/bin/env python3
"""Tests for sweep_lib.analyze_stage (synthetic snapshots + captures), incl. mutation twins.
Run: python -I plugin/snapshot/tools/test_sweep.py
"""
import copy
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import sweep_lib as sl  # noqa: E402
import synthetic  # noqa: E402

VMS01 = synthetic.VMS01
DOC = "FalloutNV.esm:104C8F"
ORACLE = {"schema": 1, "quests": {"VMS01": {"ref": VMS01, "stages": {"20": {
    "unconditional": [{"type": "SetObjective", "quest": VMS01, "objective": 20, "flags": 1},
                      {"type": "SetGlobal", "global": "FalloutNV.esm:0A0001", "value": 5.0}],
    "conditional": [{"type": "KillActor", "actor": DOC}]}}}}}


def base():
    b = synthetic.base_snapshot()
    b["shared"]["quests"][synthetic.VMS01]["vars"]["iDocState"] = 1
    return b


def after(b, **extra):
    m = {"shared/quests/VMS01/currentStage": 20, "shared/quests/VMS01/completedStages": [0, 20],
         "shared/quests/VMS01/objectives/20/displayed": True, "shared/globals/TestGlobal01/value": 5.0}
    m.update(extra)
    return synthetic.make_synthetic(b, m)


def cap(*effects):
    return {"txns": [{"effects": list(effects)}]}


GOOD_CAP = cap({"type": "SetStage", "quest": "falloutnv.esm:104c2a", "stage": 20},
               {"type": "SetObjective", "quest": "falloutnv.esm:104c2a", "objective": 20, "flags": 1},
               {"type": "SetGlobal", "global": "falloutnv.esm:0a0001", "value": 5.0})


def run(s0, s1, s2, c=GOOD_CAP, oracle=ORACLE, quest="VMS01", stage=20):
    return sl.analyze_stage(oracle, quest, stage, s0, s1, s2, c)


class Analyze(unittest.TestCase):
    def setUp(self):
        self.b = base()
        self.a = after(self.b)

    def test_clean_stage_passes(self):
        r = run(self.b, self.b, self.a)
        self.assertEqual((r["status"], r["reasons"]), ("pass", []), r)
        self.assertEqual(r["counts"]["txns"], 1)

    def test_missing_state_effect_fails(self):
        a = copy.deepcopy(self.a)
        a["shared"]["quests"][VMS01]["objectives"]["20"]["displayed"] = False
        r = run(self.b, self.b, a)
        self.assertEqual(r["status"], "fail")
        self.assertEqual([e["type"] for e in r["missing_state"]], ["setobjective"])

    def test_missing_capture_fails_even_when_state_changed(self):
        c = cap({"type": "SetStage", "quest": "falloutnv.esm:104c2a", "stage": 20})
        r = run(self.b, self.b, self.a, c)
        self.assertEqual(r["status"], "fail")
        self.assertEqual(len(r["missing_capture"]), 2)

    def test_unexplained_extra_fails_but_captured_extra_is_explained(self):
        a = after(self.b, **{"shared/globals/TestGlobal07/value": 99.0})
        r = run(self.b, self.b, a)
        self.assertEqual(r["status"], "fail")
        self.assertEqual([e["global"] for e in r["extras"]], ["falloutnv.esm:0a0007"])
        c = cap(*GOOD_CAP["txns"][0]["effects"], {"type": "SetGlobal", "global": "falloutnv.esm:0a0007", "value": 99.0})
        self.assertEqual(run(self.b, self.b, a, c)["status"], "pass")

    def test_conditional_effect_is_not_an_extra(self):
        a = after(self.b, **{"shared/refs/DocMitchell/alive": False})
        r = run(self.b, self.b, a)
        self.assertEqual(r["status"], "pass", r)

    def test_drift_between_s0_and_s1_is_ignored_even_when_value_keeps_moving(self):
        s0 = self.b
        s1 = synthetic.make_synthetic(self.b, {"shared/globals/TestGlobal09/value": 3.0})
        s2 = after(s1, **{"shared/globals/TestGlobal09/value": 6.0})
        self.assertEqual(run(s0, s1, s2)["status"], "pass")
        # twin of the drift rule: without a control interval the same movement IS an extra
        self.assertEqual(run(s1, s1, s2)["status"], "fail")

    def test_nvdlc_quest_noise_is_ignored(self):
        b = base()
        b["shared"]["quests"]["FalloutNV.esm:300005"]["edid"] = "NVDLC02MQ00"
        b["shared"]["quests"]["FalloutNV.esm:300005"]["vars"] = {"fStartTimer": 1.0}
        s2 = after(b, **{"shared/quests/NVDLC02MQ00/vars/fStartTimer": 9.0})
        self.assertEqual(run(b, b, s2)["status"], "pass")

    def test_no_txn_or_noop_fails(self):
        self.assertEqual(run(self.b, self.b, self.a, {"txns": []})["status"], "fail")
        r = run(self.b, self.b, copy.deepcopy(self.b))
        self.assertEqual(r["status"], "fail")

    def test_oracle_without_entry_is_noent_unless_extras(self):
        o = {"schema": 1, "quests": {}}
        r = run(self.b, self.b, self.a, GOOD_CAP, oracle=o)
        self.assertEqual(r["status"], "noent")
        self.assertEqual(sl.quest_status([r]), "pass")
        a = after(self.b, **{"shared/globals/TestGlobal07/value": 99.0})
        c = cap({"type": "SetStage", "quest": "falloutnv.esm:104c2a", "stage": 20})
        self.assertEqual(run(self.b, self.b, a, c, oracle=o)["status"], "fail")

    def test_invalid_snapshot_is_not_a_pass(self):
        r = run({}, {}, {})
        self.assertEqual(r["status"], "fail")

    def test_script_var_extra_explained_by_owner(self):
        a = after(self.b, **{"shared/quests/VMS01/vars/iDocState": 3})
        r = run(self.b, self.b, a)
        self.assertEqual(r["status"], "fail")      # nobody captured it
        c = cap(*GOOD_CAP["txns"][0]["effects"], {"type": "SetScriptVar", "owner": "falloutnv.esm:104c2a", "var": 4, "value": 3})
        self.assertEqual(run(self.b, self.b, a, c)["status"], "pass")

    def test_quest_status_aggregation(self):
        self.assertEqual(sl.quest_status([{"status": "pass"}, {"status": "noent"}]), "pass")
        self.assertEqual(sl.quest_status([{"status": "pass"}, {"status": "fail"}]), "fail")
        self.assertEqual(sl.quest_status([{"status": "fail"}, {"status": "error"}]), "error")
        self.assertEqual(sl.quest_status([]), "skip")


def rule_names(r):
    return sorted({x["rule"] for x in r["rules"]})


def oracle_with(unconditional, conditional=()):
    return {"schema": 1, "quests": {"VMS01": {"ref": VMS01, "stages": {"20": {
        "unconditional": list(unconditional), "conditional": list(conditional)}}}}}


SS = {"type": "SetStage", "quest": "falloutnv.esm:104c2a", "stage": 20}


class Rules(unittest.TestCase):
    """One test per sweep rule: it applies, it is recorded, and its negative case still fails."""

    def setUp(self):
        self.b = base()
        # pristine quest like after test.quest.reset: not running, nothing done, no objectives shown
        self.s1 = synthetic.make_synthetic(self.b, {"shared/quests/VMS01/running": False, "shared/quests/VMS01/currentStage": 0,
                                                    "shared/quests/VMS01/completedStages": []})

    def s2(self, **extra):
        m = {"shared/quests/VMS01/running": True, "shared/quests/VMS01/currentStage": 20,
             "shared/quests/VMS01/completedStages": [20]}
        m.update(extra)
        return synthetic.make_synthetic(self.s1, m)

    def test_drop_nonexistent_objective(self):
        o = oracle_with([{"type": "SetObjective", "quest": VMS01, "objective": 77, "flags": 1}])   # VMS01 has objectives 10, 20
        cap = {"txns": [{"effects": [SS, {"type": "StartQuest", "quest": "falloutnv.esm:104c2a"}]}]}
        r = run(self.s1, self.s1, self.s2(), cap, oracle=o)
        self.assertEqual(r["status"], "pass", r)
        self.assertIn("R-DROP-OBJ", rule_names(r))
        o2 = oracle_with([{"type": "SetObjective", "quest": VMS01, "objective": 20, "flags": 1}])   # exists: still required
        self.assertEqual(run(self.s1, self.s1, self.s2(), cap, oracle=o2)["status"], "fail")

    def test_drop_removeitem_player_does_not_hold(self):
        o = oracle_with([{"type": "RemoveItem", "item": "FalloutNV.esm:015038", "count": 1}])
        cap = {"txns": [{"effects": [SS, {"type": "StartQuest", "quest": "falloutnv.esm:104c2a"}]}]}
        r = run(self.s1, self.s1, self.s2(), cap, oracle=o)
        self.assertEqual((r["status"], "R-DROP-ITEM" in rule_names(r)), ("pass", True), r)
        s1 = synthetic.make_synthetic(self.s1, {"perPlayer/host/inventory/FalloutNV.esm:015038": {"edid": "X", "count": 2}})
        s2 = synthetic.make_synthetic(s1, {"shared/quests/VMS01/running": True, "shared/quests/VMS01/currentStage": 20,
                                           "shared/quests/VMS01/completedStages": [20]})
        self.assertEqual(run(s1, s1, s2, cap, oracle=o)["status"], "fail")   # held: the removal must be seen

    def test_already_true_skips_state_but_capture_is_still_required(self):
        s1 = synthetic.make_synthetic(self.b, {"shared/quests/VMS01/completedStages": [0, 20]})
        o = oracle_with([])
        capture = {"txns": [{"effects": [SS]}]}
        r = run(s1, s1, s1, capture, oracle=o)                      # stage 20 already done: no state change possible
        self.assertEqual(r["status"], "pass", r)
        self.assertIn("R-ALREADY", rule_names(r))
        self.assertEqual(run(s1, s1, s1, {"txns": [{"effects": []}]}, oracle=o)["status"], "fail")   # no capture: fail

    def test_unobservable_effects_skip_state_only(self):
        clock = "FalloutNV.esm:000038"                                # GameHour in the synthetic base
        o = oracle_with([{"type": "SetGlobal", "global": clock, "value": 23.0},
                         {"type": "MoveRef", "ref": "FalloutNV.esm:000014"},
                         {"type": "EnableRef", "ref": "FalloutNV.esm:777777"}])      # not a watched ref
        eff = [SS, {"type": "StartQuest", "quest": "falloutnv.esm:104c2a"},
               {"type": "SetGlobal", "global": "falloutnv.esm:000038", "value": 23.0},
               {"type": "MoveRef", "ref": "falloutnv.esm:000014"}, {"type": "EnableRef", "ref": "falloutnv.esm:777777"}]
        r = run(self.s1, self.s1, self.s2(), {"txns": [{"effects": eff}]}, oracle=o)
        self.assertEqual((r["status"], "R-UNOBS" in rule_names(r)), ("pass", True), r)
        r2 = run(self.s1, self.s1, self.s2(), {"txns": [{"effects": eff[:3]}]}, oracle=o)   # capture lacks MoveRef/EnableRef
        self.assertEqual(r2["status"], "fail")
        self.assertEqual(len(r2["missing_capture"]), 2)

    def test_watched_ref_enable_is_checked_in_state(self):
        o = oracle_with([{"type": "EnableRef", "ref": DOC}])
        s1 = synthetic.make_synthetic(self.s1, {"shared/refs/DocMitchell/enabled": False})
        eff = [SS, {"type": "StartQuest", "quest": "falloutnv.esm:104c2a"}, {"type": "EnableRef", "ref": DOC.lower()}]
        s2 = synthetic.make_synthetic(s1, {"shared/quests/VMS01/running": True, "shared/quests/VMS01/currentStage": 20,
                                           "shared/quests/VMS01/completedStages": [20], "shared/refs/DocMitchell/enabled": True})
        self.assertEqual(run(s1, s1, s2, {"txns": [{"effects": eff}]}, oracle=o)["status"], "pass")
        s2b = synthetic.make_synthetic(s2, {"shared/refs/DocMitchell/enabled": False})
        r = run(s1, s1, s2b, {"txns": [{"effects": eff}]}, oracle=o)
        self.assertEqual((r["status"], len(r["missing_state"])), ("fail", 1))

    def test_netzero_stop_after_implicit_start(self):
        o = oracle_with([{"type": "StopQuest", "quest": VMS01}])
        eff = [SS, {"type": "StartQuest", "quest": "falloutnv.esm:104c2a"}, {"type": "StopQuest", "quest": "falloutnv.esm:104c2a"}]
        s2 = synthetic.make_synthetic(self.s1, {"shared/quests/VMS01/currentStage": 20, "shared/quests/VMS01/completedStages": [20]})
        r = run(self.s1, self.s1, s2, {"txns": [{"effects": eff}]}, oracle=o)       # S1 and S2 both not running
        self.assertEqual((r["status"], "R-NETZERO" in rule_names(r)), ("pass", True), r)
        self.assertEqual(run(self.s1, self.s1, s2, {"txns": [{"effects": eff[:2]}]}, oracle=o)["status"], "fail")   # StopQuest not captured

    def test_normalization_xp_default_recipient_and_clear_objective(self):
        o = oracle_with([{"type": "ModActorValue", "av": 24, "delta": 50}, {"type": "ClearObjective", "quest": VMS01, "objective": 10, "flags": 1}])
        s1 = synthetic.make_synthetic(self.s1, {"shared/quests/VMS01/objectives/10/displayed": True})
        s2 = synthetic.make_synthetic(s1, {"shared/quests/VMS01/running": True, "shared/quests/VMS01/currentStage": 20,
                                           "shared/quests/VMS01/completedStages": [20],
                                           "shared/quests/VMS01/objectives/10/displayed": False,
                                           "perPlayer/host/actorValues/XP/current": 50.0})
        eff = [SS, {"type": "StartQuest", "quest": "falloutnv.esm:104c2a"},
               {"type": "ModActorValue", "av": 65536, "delta": 50, "to": "default"},
               {"type": "SetObjective", "quest": "falloutnv.esm:104c2a", "objective": 10, "flags": 0}]
        r = run(s1, s1, s2, {"txns": [{"effects": eff}]}, oracle=o)
        self.assertEqual(r["status"], "pass", r)
        eff_bad = [dict(e, av=999) if e.get("av") == 65536 else e for e in eff]      # a different AV is not XP
        self.assertEqual(run(s1, s1, s2, {"txns": [{"effects": eff_bad}]}, oracle=o)["status"], "fail")

    def test_implicit_start_needs_a_capture(self):
        o = oracle_with([])
        r = run(self.s1, self.s1, self.s2(), {"txns": [{"effects": [SS]}]}, oracle=o)    # state started the quest, capture lacks StartQuest
        self.assertEqual(r["status"], "fail")
        self.assertEqual([e["type"] for e in r["missing_capture"]], ["startquest"])
        ok = {"txns": [{"effects": [SS, {"type": "StartQuest", "quest": "falloutnv.esm:104c2a"}]}]}
        self.assertEqual(run(self.s1, self.s1, self.s2(), ok, oracle=o)["status"], "pass")

    def test_ambient_script_var_from_volatile_keys(self):
        o = oracle_with([])
        ok = {"txns": [{"effects": [SS, {"type": "StartQuest", "quest": "falloutnv.esm:104c2a"}]}]}
        s2 = self.s2(**{"shared/quests/VMS01/vars/fTimer": 4.0})
        vol = ({("falloutnv.esm:104c2a", "ftimer")}, set())
        self.assertEqual(sl.analyze_stage(o, "VMS01", 20, self.s1, self.s1, s2, ok, vol)["status"], "pass")
        self.assertEqual(sl.analyze_stage(o, "VMS01", 20, self.s1, self.s1, s2, ok, (set(), set()))["status"], "fail")

    def test_every_applied_rule_is_recorded(self):
        o = oracle_with([{"type": "SetObjective", "quest": VMS01, "objective": 77, "flags": 1}])
        r = run(self.s1, self.s1, self.s2(), {"txns": [{"effects": [SS, {"type": "StartQuest", "quest": "falloutnv.esm:104c2a"}]}]}, oracle=o)
        self.assertEqual(rule_names(r), ["R-DROP-OBJ", "R-IMPLICIT"])


class MutationTwins(unittest.TestCase):
    """Broken analyzers must be caught by the same scenarios."""

    def scenarios(self):
        b = base()
        good = after(b)
        extra = after(b, **{"shared/globals/TestGlobal07/value": 99.0})
        return b, good, extra

    def check_all(self):
        b, good, extra = self.scenarios()
        return {
            "clean passes": run(b, b, good)["status"] == "pass",
            "extra caught": run(b, b, extra)["status"] == "fail",
            "capture gap caught": run(b, b, good, cap({"type": "SetStage", "quest": "falloutnv.esm:104c2a", "stage": 20}))["status"] == "fail",
        }

    def test_real_passes_all(self):
        self.assertTrue(all(self.check_all().values()), self.check_all())

    def test_mutant_that_explains_everything_is_caught(self):
        orig = sl._explained
        sl._explained = lambda *a, **k: True
        try:
            res = self.check_all()
        finally:
            sl._explained = orig
        self.assertFalse(res["extra caught"])
        self.assertTrue(res["clean passes"])

    def test_mutant_that_drops_every_expectation_is_caught(self):
        orig = sl.classify_expectation
        sl.classify_expectation = lambda e, ctx: ("drop", "R-MUTANT")
        try:
            res = self.check_all()
        finally:
            sl.classify_expectation = orig
        self.assertFalse(res["capture gap caught"])
        self.assertTrue(res["clean passes"])

    def test_mutant_without_normalization_fails_the_xp_scenario(self):
        orig = sl.normalize_captured
        sl.normalize_captured = lambda got: got
        try:
            r = Rules("test_normalization_xp_default_recipient_and_clear_objective")
            r.setUp()
            with self.assertRaises(AssertionError):
                r.test_normalization_xp_default_recipient_and_clear_objective()
        finally:
            sl.normalize_captured = orig

    def test_mutant_that_ignores_capture_coverage_is_caught(self):
        orig = sl._covers
        sl._covers = lambda *a, **k: True
        try:
            res = self.check_all()
        finally:
            sl._covers = orig
        self.assertFalse(res["capture gap caught"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
