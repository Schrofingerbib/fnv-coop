#!/usr/bin/env python3
"""Game-free tests for the scenario tool, plus the mutation twin.  Run: python -I tools/scenarios/run_tests.py   (prints ALL OK).

Part 1: unit tests (validator, translation, comparison, jump planning, runner against tools/scenarios/fakegame.py).
Part 2: twin. The same unit tests are run again under each SCEN_MUTANT value; every mutant must make at least one test fail.
"""
import copy
import json
import os
import subprocess
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import scenlib  # noqa: E402
import runner  # noqa: E402
import fakegame  # noqa: E402

SCN = scenlib.SCEN_DIR
ORDER = scenlib.load_order()


def load(name):
    return scenlib.load_scenario(SCN / (name + ".json"))


def hexof(sc, alias):
    return scenlib.Resolver(sc, ORDER).hex("$" + alias)


def run_pair(names, caps=None, **flags):
    """Run scenarios in order on one fake 2-player world. Returns (world, results, nvs)."""
    w, clock, nvs = fakegame.make(2, **flags)
    sc0 = load(names[0])
    w.bases = {hexof(load("loot_race"), "teddyRef"): hexof(load("loot_race"), "teddy")}
    nov = hexof(load("ownership_handoff"), "novac")
    w.flags.setdefault("novac", nov)
    w.flags.setdefault("vcg02", hexof(load("quest_stage_sync"), "vcg02"))
    w.flags.setdefault("vmq05", hexof(load("pnr_vote"), "vmq05"))
    r = runner.Runner(nvs, runner.StateStore(), capabilities=caps, sleep=clock.sleep, now=clock.now, log=lambda *a: None, order=ORDER)
    results = [r.run(load(n)) for n in names]
    return w, results, r


ALL_CAPS = set(scenlib.CAPABILITIES)


class ShippedScenarios(unittest.TestCase):
    def test_ten_starters_exist_and_are_valid(self):
        names = {p.stem for p in scenlib.list_scenarios()}
        want = {"goodsprings_fight", "loot_race", "spawn_once", "ownership_handoff", "door_terminal_container_matrix",
                "companion_recruit", "quest_stage_sync", "save_load_rejoin", "vats_hit", "fast_travel_tether"}
        self.assertTrue(want <= names, want - names)
        for p in scenlib.list_scenarios():
            self.assertEqual(scenlib.validate_file(p), [], p.name)

    def test_every_form_in_every_scenario_resolves_with_matching_edid_and_sig(self):
        forms = scenlib.load_forms()
        n = 0
        for p in scenlib.list_scenarios():
            for alias, d in load(p.stem)["forms"].items():
                if d["sig"] in ("DIAL", "INFO"):
                    continue    # resolved against dials/infos by the validator (tests below)
                rec = forms.get(d["ref"]) or {"sig": scenlib.BUILTIN.get(d["ref"], (None, None))[0],
                                                "edid": scenlib.BUILTIN.get(d["ref"], (None, None))[1]}
                self.assertEqual((rec["edid"], rec["sig"]), (d["edid"], d["sig"]), "%s.%s" % (p.stem, alias))
                n += 1
        self.assertGreaterEqual(n, 20)

    def test_runtime_hex_uses_the_load_order(self):
        self.assertEqual(scenlib.runtime_hex("FalloutNV.esm:10A214", ORDER), "0010A214")
        self.assertEqual(scenlib.runtime_hex("DeadMoney.esm:00ECE0", ORDER), "0100ECE0")
        self.assertEqual(scenlib.runtime_hex("OldWorldBlues.esm:00D4BF", ORDER), "0300D4BF")


class ValidatorRejects(unittest.TestCase):
    """Each mutation of a good scenario must be reported."""

    def base(self):
        return copy.deepcopy(load("quest_stage_sync"))

    def errs(self, sc):
        return scenlib.validate(sc, name=sc.get("name"))

    def test_base_is_clean(self):
        self.assertEqual(self.errs(self.base()), [])

    def expect(self, mut, text):
        sc = self.base()
        mut(sc)
        errs = self.errs(sc)
        self.assertTrue(any(text in e for e in errs), "wanted %r in %s" % (text, errs))

    def test_invented_formid(self):
        self.expect(lambda s: s["forms"]["vcg02"].update(ref="FalloutNV.esm:ABCDEF"), "does not resolve")

    def test_wrong_edid(self):
        self.expect(lambda s: s["forms"]["vcg02"].update(edid="VCG01"), "edid")

    def test_wrong_record_type(self):
        self.expect(lambda s: s["forms"]["vcg02"].update(sig="NPC_"), "sig")

    def test_unnormalized_ref(self):
        self.expect(lambda s: s["forms"]["vcg02"].update(ref="FalloutNV.esm:10a214"), "normalized")

    def test_plugin_not_in_load_order(self):
        self.expect(lambda s: s["forms"]["vcg02"].update(ref="Nope.esm:10A214"), "load order")

    def test_unknown_alias_in_step(self):
        self.expect(lambda s: s["steps"][1].update(quest="$nothere"), "unknown alias")

    def test_raw_ref_that_does_not_exist(self):
        self.expect(lambda s: s["steps"][1].update(quest="FalloutNV.esm:ABCDEF"), "does not resolve")

    def test_stage_not_in_quest(self):
        self.expect(lambda s: s["steps"][1].update(stage=77), "not a stage")

    def test_var_not_in_quest_script(self):
        sc = self.base()
        v = [x for x in sc["steps"] if x["op"] == "setvar"][0]
        v["var"] = "nNoSuchVar"
        self.assertTrue(any("script variable" in e for e in self.errs(sc)))

    def test_wrong_sig_for_field(self):
        def m(s):
            s["forms"]["gangBase"] = {"ref": "FalloutNV.esm:104DF1", "edid": "GSPGHM2", "sig": "NPC_"}
            s["steps"][1] = {"id": "bad", "op": "moveto", "who": "player", "to": "$gangBase"}
        self.expect(m, "expected one of")

    def test_no_checks(self):
        self.expect(lambda s: s.update(steps=[x for x in s["steps"] if x["op"] != "check"] or [{"id": "w", "op": "wait", "seconds": 1}]),
                    "no check")

    def test_duplicate_step_id(self):
        self.expect(lambda s: s["steps"][1].update(id=s["steps"][0]["id"]), "duplicate id")

    def test_unknown_op_and_key(self):
        self.expect(lambda s: s["steps"][1].update(op="teleport"), "unknown op")
        self.expect(lambda s: s["steps"][1].update(color="red"), "unexpected key")

    def test_bad_save_name(self):
        def m(s):
            s["steps"].insert(0, {"id": "sv", "op": "save", "name": "mysave"})
        self.expect(m, "autosave")

    def test_raw_formid_in_console(self):
        def m(s):
            s["steps"].insert(0, {"id": "c", "op": "console", "cmd": "00104C1C.kill"})
        self.expect(m, "raw FormID")

    def test_unknown_capability_and_player_count(self):
        self.expect(lambda s: s["steps"][1].update(needs=["warp_drive"]), "unknown capability")
        self.expect(lambda s: s["steps"][1].update(on=5), "bad 'on'")

    def test_missing_start_save_and_reset(self):
        self.expect(lambda s: s["start"].pop("save"), "start.save")
        self.expect(lambda s: s.pop("reset"), "reset must")

    def test_check_without_value(self):
        def m(s):
            c = [x for x in s["steps"] if x["op"] == "check"][0]
            c.pop("value", None)
        self.expect(m, "needs a 'value'")

    def test_all_shipped_scenarios_reject_a_corrupted_ref(self):
        for p in scenlib.list_scenarios():
            sc = load(p.stem)
            first = next(iter(sc["forms"]))
            sc["forms"][first]["ref"] = "FalloutNV.esm:FFFFF1"
            self.assertTrue(any("does not resolve" in e for e in scenlib.validate(sc, name=sc["name"])), p.stem)


class DialogueAndMessages(unittest.TestCase):
    def test_info_and_dial_refs_resolve_and_are_checked(self):
        sc = copy.deepcopy(load("dialogue_spectator"))
        self.assertEqual(scenlib.validate(sc, name="dialogue_spectator"), [])
        bad = copy.deepcopy(sc)
        bad["forms"]["info"]["line"] = "Something else"
        self.assertTrue(any("INFO line" in e for e in scenlib.validate(bad, name="dialogue_spectator")))
        bad = copy.deepcopy(sc)
        bad["forms"]["info"]["ref"] = "FalloutNV.esm:FFFFF1"
        self.assertTrue(any("does not resolve" in e for e in scenlib.validate(bad, name="dialogue_spectator")))
        bad = copy.deepcopy(sc)
        bad["forms"]["topic"]["edid"] = "Nope"
        self.assertTrue(any("edid" in e for e in scenlib.validate(bad, name="dialogue_spectator")))

    def test_translation(self):
        r = scenlib.Resolver(load("pnr_vote"), ORDER)
        self.assertEqual(scenlib.step_actions({"id": "x", "op": "message_click", "button": 1}, r),
                         [("call", "bot.menu.press", {"button": 1})])
        r = scenlib.Resolver(load("dialogue_spectator"), ORDER)
        self.assertEqual(scenlib.step_actions({"id": "x", "op": "pick", "topic": "$topic", "info": "$info"}, r),
                         [("call", "bot.say", {"info": "FalloutNV.esm:10A1E5"})])
        self.assertEqual(scenlib.step_actions({"id": "x", "op": "console", "cmd": "SetObjectiveCompleted {vcg02} 10 1"}, r),
                         [("exec", "SetObjectiveCompleted 0010A214 10 1")])

    def test_consent_translation_and_vats_names_follow_t6a(self):
        r = scenlib.Resolver(load("pnr_vote"), ORDER)
        self.assertEqual(scenlib.step_actions({"id": "x", "op": "consent", "answer": "notyet"}, r),
                         [("call", "bot.consent", {"answer": "notyet"})])
        g = scenlib.Resolver(load("vats_hit"), ORDER)
        self.assertEqual(scenlib.step_actions({"id": "x", "op": "vats_hit", "target": "$gang1", "limb": "Head"}, g),
                         [("call", "bot.vats", {"ref": "00104C70"})])

    def test_pnr_vote_makes_the_next_jump_reload(self):
        self.assertIn("*", scenlib.footprint(load("pnr_vote"))["dirty"])


class Translation(unittest.TestCase):
    def test_actions(self):
        sc = load("door_terminal_container_matrix")
        res = scenlib.Resolver(sc, ORDER)
        step = {"id": "x", "op": "lock", "ref": "$docDoor", "level": 50}
        self.assertEqual(scenlib.step_actions(step, res), [("exec", "00103E61.lock 50")])
        self.assertEqual(scenlib.step_actions({"id": "x", "op": "additem", "who": "$dresser", "item": "$stimpak", "count": 3}, res),
                         [("exec", "00103E37.additem 00015169 3")])
        self.assertEqual(scenlib.step_actions({"id": "x", "op": "moveto", "who": "player", "to": "$docDoor"}, res),
                         [("exec", "player.moveto 00103E61")])
        q = scenlib.Resolver(load("quest_stage_sync"), ORDER)
        self.assertEqual(scenlib.step_actions({"id": "x", "op": "setstage", "quest": "$vcg02", "stage": 10}, q)[0],
                         ("exec", "setstage 0010A214 10"))
        self.assertEqual(scenlib.step_actions({"id": "x", "op": "setvar", "quest": "$vcg02", "var": "nGeckosKilled", "value": 2}, q),
                         [("call", "test.quest.setvar", {"quest": "0010A214", "var": "nGeckosKilled", "value": 2.0})])
        g = scenlib.Resolver(load("vats_hit"), ORDER)
        self.assertEqual(scenlib.step_actions({"id": "x", "op": "console", "cmd": "{gang1}.setav Health 100"}, g),
                         [("exec", "00104C70.setav Health 100")])

    def test_check_expressions(self):
        sc = load("goodsprings_fight")
        res = scenlib.Resolver(sc, ORDER)
        e = scenlib.check_expr({"id": "c", "op": "check", "kind": "dead", "ref": "$gang1", "value": 1}, res)
        self.assertEqual(e[:3], ("00104C70.GetDead", "==", 1))
        res2 = scenlib.Resolver(load("quest_stage_sync"), ORDER)
        e = scenlib.check_expr({"id": "c", "op": "check", "kind": "stage_done", "quest": "$vcg02", "stage": 20, "value": 1}, res2)
        self.assertEqual(e[0], "GetStageDone 0010A214 20")

    def test_compare_table(self):
        c = scenlib.compare
        self.assertTrue(c([1, 1], "==", 1, None, 0))
        self.assertFalse(c([1, 0], "==", 1, None, 0))
        self.assertTrue(c([1, 0], "==", 1, "sum", 0))
        self.assertFalse(c([1, 1], "==", 1, "sum", 0))      # duplicated loot
        self.assertFalse(c([0, 0], "==", 1, "sum", 0))      # vanished loot
        self.assertTrue(c([3, 3.5], "same", None, None, 1))
        self.assertFalse(c([3, 9], "same", None, None, 1))
        self.assertTrue(c([5, 6], "<=", 6, "max", 0))
        self.assertTrue(c([50], "<", 100, None, 0))
        self.assertTrue(c([99.5], "==", 100, None, 1))


class JumpPlanning(unittest.TestCase):
    def test_rules(self):
        fight, quest = load("goodsprings_fight"), load("quest_stage_sync")
        st = {"loaded": True, "save": "autosave", "dirty": [], "failed": False}
        self.assertFalse(scenlib.plan_jump(st, quest)["reload"])
        self.assertTrue(scenlib.plan_jump({"loaded": False}, quest)["reload"])
        self.assertTrue(scenlib.plan_jump(dict(st, save="other"), quest)["reload"])
        self.assertTrue(scenlib.plan_jump(dict(st, failed=True), quest)["reload"])
        self.assertTrue(scenlib.plan_jump(dict(st, dirty=["*"]), quest)["reload"])
        d = sorted(scenlib.footprint(fight)["dirty"])
        self.assertTrue(scenlib.plan_jump(dict(st, dirty=d), fight)["reload"])           # same gang again
        self.assertFalse(scenlib.plan_jump(dict(st, dirty=d), quest)["reload"])          # unrelated refs
        self.assertTrue(scenlib.plan_jump(st, quest, force_reload=True)["reload"])

    def test_load_op_makes_the_world_unnameable(self):
        self.assertIn("*", scenlib.footprint(load("save_load_rejoin"))["dirty"])

    def test_reset_ids_reuse_the_l7_goodsprings_group(self):
        ids = scenlib.reset_ids(load("quest_stage_sync"), ORDER)
        self.assertEqual(ids[:6], ["00104C1C", "0010A214", "0015D912", "00104EAE", "0015EC5B", "00102037"])


class RunnerOnFake(unittest.TestCase):
    def status(self, results, name):
        return [r for r in results if r["scenario"] == name][0]

    def test_synced_world_passes_every_runnable_scenario(self):
        names = ["goodsprings_fight", "loot_race", "spawn_once", "door_terminal_container_matrix", "companion_recruit",
                 "quest_stage_sync", "vats_hit", "fast_travel_tether", "dialogue_spectator", "pnr_vote", "pnr_vote_guest_declines"]
        _, results, _ = run_pair(names)
        for r in results:
            self.assertEqual(r["status"], "pass", (r["scenario"], [s for s in r["steps"] if s["status"] != "pass"]))

    def test_proposed_capabilities_are_blocked_not_passed(self):
        _, results, _ = run_pair(["ownership_handoff", "save_load_rejoin"])
        for r in results:
            self.assertEqual(r["status"], "partial", (r["scenario"], r["status"]))
            self.assertTrue(any(s["status"] == "blocked" for s in r["steps"]))
            self.assertFalse(any(s["status"] in ("fail", "error") for s in r["steps"]), r["steps"])

    def test_with_the_capabilities_present_they_pass_too(self):
        _, results, _ = run_pair(["vats_hit", "fast_travel_tether", "ownership_handoff", "save_load_rejoin", "dialogue_spectator", "pnr_vote", "pnr_vote_guest_declines"], caps=ALL_CAPS)
        for r in results:
            self.assertEqual(r["status"], "pass", (r["scenario"], [s for s in r["steps"] if s["status"] != "pass"]))

    def test_guest_blind_world_fails_the_sync_checks(self):
        _, results, _ = run_pair(["quest_stage_sync", "goodsprings_fight"], desync_guest=True)
        for r in results:
            self.assertEqual(r["status"], "fail", r["scenario"])

    def test_loot_duplication_is_caught(self):
        _, results, _ = run_pair(["loot_race"], dup_loot=True)
        r = results[0]
        self.assertEqual(r["status"], "fail")
        self.assertTrue(any(s["id"] == "exactly_one_copy" and s["status"] == "fail" for s in r["steps"]))

    def test_double_spawn_is_caught(self):
        _, results, _ = run_pair(["spawn_once"], double_spawn=True)
        self.assertEqual(results[0]["status"], "fail")

    def test_missing_ownership_handoff_is_caught(self):
        _, results, _ = run_pair(["ownership_handoff"], caps=ALL_CAPS, no_handoff=True)
        self.assertEqual(results[0]["status"], "fail")

    def test_steps_run_on_the_named_player_only(self):
        w, _, _ = run_pair(["goodsprings_fight"])
        kills = [(i, t) for i, k, t in w.calls if k == "exec" and t.endswith(".kill")]
        self.assertIn((1, "00104C77.kill"), kills)      # guest_kills_2
        self.assertNotIn((0, "00104C77.kill"), kills)
        self.assertIn((0, "00104C70.kill"), kills)
        self.assertNotIn((1, "00104C70.kill"), kills)

    def test_unavailable_check_times_out_with_a_detail(self):
        w, clock, nvs = fakegame.make(2)
        sc = copy.deepcopy(load("quest_stage_sync"))
        sc["steps"] = [{"id": "never", "op": "check", "kind": "stage", "quest": "$vcg02", "value": 55, "timeout": 2}]
        r = runner.Runner(nvs, runner.StateStore(), sleep=clock.sleep, now=clock.now, order=ORDER).run(sc)
        self.assertEqual(r["status"], "fail")
        self.assertIn("not met after", r["steps"][-1]["detail"])

    def test_console_parse_error_is_an_error_not_a_pass(self):
        w, clock, nvs = fakegame.make(2)
        sc = copy.deepcopy(load("quest_stage_sync"))
        sc["forms"]["gang1"] = {"ref": "FalloutNV.esm:104C70", "edid": "GSPG01Ref", "sig": "ACHR"}
        sc["steps"] = [{"id": "bad", "op": "console", "cmd": "bogus {gang1}"},
                       {"id": "c", "op": "check", "kind": "stage", "quest": "$vcg02", "value": 0}]
        r = runner.Runner(nvs, runner.StateStore(), sleep=clock.sleep, now=clock.now, order=ORDER).run(sc)
        self.assertEqual(r["status"], "error")

    def test_invalid_scenario_is_not_run(self):
        w, clock, nvs = fakegame.make(2)
        sc = copy.deepcopy(load("quest_stage_sync"))
        sc["forms"]["vcg02"]["ref"] = "FalloutNV.esm:ABCDEF"
        r = runner.Runner(nvs, runner.StateStore(), sleep=clock.sleep, now=clock.now, order=ORDER).run(sc)
        self.assertEqual(r["status"], "invalid")
        self.assertEqual(w.calls, [])


class InstantJump(unittest.TestCase):
    def loads(self, w):
        return len(w.loads)

    def test_second_unrelated_scenario_jumps_in_place(self):
        w, results, _ = run_pair(["quest_stage_sync", "goodsprings_fight", "door_terminal_container_matrix"])
        self.assertEqual([r["jump"]["reload"] for r in results], [True, False, False])
        self.assertEqual(self.loads(w), 2)                 # only the first jump loaded the save (once per player)
        self.assertTrue(all(r["status"] == "pass" for r in results))

    def test_conflicting_scenario_reloads(self):
        w, results, _ = run_pair(["goodsprings_fight", "ownership_handoff"], caps=ALL_CAPS)
        self.assertTrue(results[1]["jump"]["reload"])
        self.assertTrue(any("changed refs" in x for x in results[1]["jump"]["reasons"]))

    def test_repeating_a_scenario_that_killed_actors_reloads(self):
        w, results, _ = run_pair(["goodsprings_fight", "goodsprings_fight"])
        self.assertTrue(results[1]["jump"]["reload"])
        self.assertEqual(results[1]["status"], "pass")     # and it passes because the world was restored

    def test_failed_scenario_forces_a_reload_next(self):
        w, results, _ = run_pair(["quest_stage_sync", "quest_stage_sync"], desync_guest=True)
        self.assertEqual(results[0]["status"], "fail")
        self.assertTrue(results[1]["jump"]["reload"])

    def test_inplace_jump_resets_quest_state(self):
        w, results, r = run_pair(["quest_stage_sync", "door_terminal_container_matrix"])
        self.assertEqual(w.stage.get("0010A214", 0), 0)    # VCG02 was reset by the in-place jump

    def test_in_place_jump_is_cheap_in_bridge_calls(self):
        w, clock, nvs = fakegame.make(2)
        w.bases = {}
        rn = runner.Runner(nvs, runner.StateStore(), sleep=clock.sleep, now=clock.now, order=ORDER)
        rn.run(load("quest_stage_sync"))
        n0 = len(w.calls)
        j = rn.jump(load("door_terminal_container_matrix"))
        self.assertFalse(j["reload"])
        self.assertLess(len(w.calls) - n0, 20)
        self.assertLess(j["seconds"], 10)

    def test_state_survives_in_a_store_file(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "s.json"
            s = runner.StateStore(p)
            s.put("0@1", {"loaded": True, "save": "autosave", "dirty": [], "failed": False})
            self.assertEqual(runner.StateStore(p).get("0@1")["save"], "autosave")
            self.assertIsNone(runner.StateStore(p).get("0@2"))      # new generation = nothing loaded


class Cli(unittest.TestCase):
    def test_validate_and_list_exit_codes(self):
        py = [sys.executable, "-I", "-X", "utf8", str(HERE / "scen.py")]
        p = subprocess.run(py + ["validate"], capture_output=True, text=True)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertIn("0 invalid", p.stdout)
        p = subprocess.run(py + ["list"], capture_output=True, text=True)
        self.assertEqual(p.returncode, 0)
        self.assertEqual(len([l for l in p.stdout.splitlines() if l.strip()]), 14)
        p = subprocess.run(py + ["plan", "vats_hit", "--prev", "goodsprings_fight"], capture_output=True, text=True)
        self.assertEqual(p.returncode, 0)
        self.assertIn('"reload": true', p.stdout)


MUTANTS = {
    "noresolve": "validator stops resolving FormIDs, EditorIDs and record types",
    "noreload": "jump never reloads the save",
    "nodirty": "scenarios never record what they changed",
    "passall": "every check passes",
    "firsthost": "every step runs only on the host",
}


def main():
    if os.environ.get("SCEN_MUTANT"):
        runner_ = unittest.TextTestRunner(stream=open(os.devnull, "w"), verbosity=0)
        res = runner_.run(unittest.defaultTestLoader.loadTestsFromModule(sys.modules[__name__]))
        return 0 if res.wasSuccessful() else 1
    res = unittest.TextTestRunner(verbosity=1).run(unittest.defaultTestLoader.loadTestsFromModule(sys.modules[__name__]))
    if not res.wasSuccessful():
        return 1
    bad = []
    for m, what in MUTANTS.items():
        env = dict(os.environ, SCEN_MUTANT=m)
        p = subprocess.run([sys.executable, "-I", str(Path(__file__).resolve())], env=env, capture_output=True, text=True)
        caught = p.returncode != 0
        print("twin %-10s %-52s %s" % (m, what, "caught" if caught else "NOT CAUGHT"))
        if not caught:
            bad.append(m)
    if bad:
        print("RESULT: twin mutants not caught: %s" % bad)
        return 1
    print("RESULT: ALL OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
