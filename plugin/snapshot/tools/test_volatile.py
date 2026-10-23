#!/usr/bin/env python3
"""Tests for volatile_probe.py (empirical volatility, story-quest safety) incl. mutation twins.
Run: python -I plugin/snapshot/tools/test_volatile.py
"""
import copy
import json
import os
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import synthetic  # noqa: E402
import volatile_probe as vp  # noqa: E402
import volatile_scan as vs  # noqa: E402

ROOT = os.path.abspath(os.path.join(HERE, "..", "..", ".."))
SCEN = os.path.join(ROOT, "out", "questdata", "scenarios")
VMS01 = synthetic.VMS01
TQ0 = "FalloutNV.esm:300000"   # TestQuest00 (non-story in these fixtures)
TQ1 = "FalloutNV.esm:300001"
DOC = "FalloutNV.esm:104C8F"
STORY = {VMS01: "VMS01"}


def snap(**mut):
    b = synthetic.base_snapshot()
    b["shared"]["quests"][TQ0]["vars"] = {"fStartTimer": 1.0, "iStable": 5}
    b["shared"]["quests"][VMS01]["vars"]["fTimer"] = 2.0
    b["shared"]["refs"][DOC]["vars"] = {"fRefTimer": 0.5, "iFlag": 1}
    return synthetic.make_synthetic(b, mut)


def tick(base, **mut):
    return synthetic.make_synthetic(base, mut)


class Probe(unittest.TestCase):
    def setUp(self):
        self.a = snap()

    def test_changing_keys_found_stable_keys_not(self):
        b = tick(self.a, **{"shared/quests/TestQuest00/vars/fStartTimer": 9.0,
                            "shared/refs/DocMitchell/vars/fRefTimer": 7.5,
                            "shared/globals/TestGlobal03/value": 77.0,
                            "shared/quests/TestQuest01/currentStage": 30})
        res = vp.probe([[self.a, b]], STORY)
        self.assertEqual({(v["owner"], v["name"]) for v in res["vars"]}, {(TQ0, "fstarttimer"), (DOC, "freftimer")})
        self.assertEqual([g["edid"] for g in res["globals"]], ["TestGlobal03"])
        self.assertEqual([(f["quest"], f["field"]) for f in res["fields"]], [(TQ1, "stage")])
        names = {v["name"] for v in res["vars"]}
        self.assertNotIn("istable", names)
        self.assertNotIn("iflag", names)

    def test_identical_dumps_yield_nothing(self):
        res = vp.probe([[self.a, copy.deepcopy(self.a)]], STORY)
        self.assertEqual((res["vars"], res["fields"], res["globals"], res["warnings"]), ([], [], [], []))

    def test_float_noise_within_tolerance_ignored(self):
        b = tick(self.a, **{"shared/quests/TestQuest00/vars/fStartTimer": 1.00001})
        self.assertEqual(vp.probe([[self.a, b]], STORY)["vars"], [])

    def test_clock_globals_not_reported(self):
        b = tick(self.a, **{"shared/globals/GameHour/value": 20.0})
        self.assertEqual(vp.probe([[self.a, b]], STORY)["globals"], [])

    def test_cross_launch_reason(self):
        b = tick(self.a, **{"shared/quests/TestQuest00/vars/fStartTimer": 4.0})
        res = vp.probe([[self.a], [b]], STORY)
        self.assertEqual([v["reasons"] for v in res["vars"]], [["launch"]])
        res = vp.probe([[self.a, b], [self.a]], STORY)
        self.assertIn("session", res["vars"][0]["reasons"])

    def test_story_quest_vars_may_be_excluded_but_fields_never(self):
        b = tick(self.a, **{"shared/quests/VMS01/vars/fTimer": 8.0, "shared/quests/VMS01/running": False,
                            "shared/quests/VMS01/currentStage": 20})
        res = vp.probe([[self.a, b]], STORY)
        self.assertIn((VMS01, "ftimer"), {(v["owner"], v["name"]) for v in res["vars"]})
        self.assertEqual(res["fields"], [])
        self.assertEqual({(w["quest"], w["field"]) for w in res["warnings"]}, {(VMS01, "running"), (VMS01, "stage")})
        self.assertEqual(vp.check_txt(vp.render_txt(res), STORY), [])

    def test_manual_protect_and_add(self):
        b = tick(self.a, **{"shared/quests/TestQuest00/vars/fStartTimer": 9.0})
        manual = {"protect": {"vars": [{"owner": TQ0, "name": "fStartTimer"}]},
                  "add": {"vars": [{"owner": DOC, "name": "iFlag"}], "fields": [{"quest": VMS01, "field": "stage"}]}}
        res = vp.probe([[self.a, b]], STORY, manual)
        self.assertEqual([(v["owner"], v["name"]) for v in res["vars"]], [(DOC, "iflag")])
        self.assertEqual(len(res["protected"]), 1)
        # manual add of a story-quest field is refused by the story rule
        self.assertEqual(res["fields"], [])
        self.assertTrue(any(w["field"] == "stage" for w in res["warnings"]))

    def test_invalid_inputs(self):
        with self.assertRaises(ValueError):
            vp.probe([[self.a]], STORY)                       # one snapshot cannot show volatility
        with self.assertRaises(ValueError):
            vp.probe([[self.a, {}]], STORY)                   # vacuous dump
        with self.assertRaises(OSError):
            vp.story_refs(tempfile.gettempdir() + "/nope")

    def test_txt_roundtrip_through_cpp_grammar(self):
        b = tick(self.a, **{"shared/quests/TestQuest00/vars/fStartTimer": 9.0, "shared/quests/TestQuest01/running": False,
                            "shared/globals/TestGlobal03/value": 77.0})
        txt = vp.render_txt(vp.probe([[self.a, b]], STORY))
        kinds = sorted(l.split()[0] for l in txt.splitlines() if l and not l.startswith("#"))
        self.assertEqual(kinds, ["field", "global", "var"])
        self.assertEqual(vp.check_txt(txt, STORY), [])


class SharedGrammar(unittest.TestCase):
    """The probe must speak L4's VolatileKeys::loadFromText grammar exactly (C++ side checked in run_tests)."""

    def test_all_spellings_parse(self):
        import volatile_cross as vc
        entries, errs = vp.parse_entries(vc.KEYS)
        self.assertEqual(errs, [])
        kinds = sorted({e[0] for e in entries})
        self.assertEqual(kinds, ["field", "global", "owner", "var"])
        self.assertIn(("var", "falloutnv.esm:104C2B", 7), entries)          # two-token form, index
        self.assertIn(("var", "falloutnv.esm:104C2A", "fStartTimer"), entries)

    def test_rejections_match_l4(self):
        for bad in ("quest FalloutNV.esm:000001", "field FalloutNV.esm:000001 bogus", "var FalloutNV.esm:000001",
                    "global FalloutNV.esm:000001 extra", "var FalloutNV.esm:000001 70000", "var nothex:zz 1",
                    "owner FalloutNV.esm:000001 x"):
            self.assertTrue(vp.parse_entries(bad)[1], bad)

    def test_reference_model(self):
        text = "\n".join(["owner A.esm:000001", "var A.esm:000002 fT", "var A.esm:000002 9", "field A.esm 000003 stage"])
        ref = vp.ReferenceKeys(text, {("a.esm:000002", "ft"): 4})
        self.assertTrue(ref.is_var("a.esm:000001", 123))
        self.assertTrue(ref.is_var("a.esm:000002", 4) and ref.is_var("a.esm:000002", 9))
        self.assertFalse(ref.is_var("a.esm:000002", 5))
        self.assertTrue(ref.is_field("a.esm:000003", "stage") and not ref.is_field("a.esm:000003", "running"))
        self.assertEqual(ref.unresolved, 0)

    def test_story_rule_applies_to_two_token_form_and_allows_owner(self):
        story = {"FalloutNV.esm:104C2A": "VMS01"}
        self.assertTrue(vp.check_txt("field falloutnv.esm 104c2a running", story))
        self.assertEqual(vp.check_txt("\n".join(["owner FalloutNV.esm:104C2A", "var FalloutNV.esm:104C2A 3"]), story), [])


class ShippedData(unittest.TestCase):
    """plugin/snapshot/data/volatile_keys.txt is committed and used at play time: it must stay valid and safe."""
    DATA = os.path.join(HERE, "..", "data")

    def test_txt_parses_and_has_provenance(self):
        with open(os.path.join(self.DATA, "volatile_keys.txt"), encoding="utf-8") as f:
            text = f.read()
        entries, errs = vp.parse_entries(text)
        self.assertEqual(errs, [])
        self.assertGreater(len(entries), 0)
        # "faction" (2026-10-21): per-machine DLC player factions, see synccore VolatileKeys::isVolatile
        self.assertTrue(all(e[0] in ("var", "owner", "global", "field", "faction") for e in entries))
        with open(os.path.join(self.DATA, "volatile_keys.json"), encoding="utf-8") as f:
            prov = json.load(f)
        for k in ("generated", "launches", "dumpsPerLaunch", "intervalSeconds", "gameVersion", "pluginsHash", "todo"):
            self.assertIn(k, prov)
        self.assertEqual(len(prov["entries"]["vars"]), sum(1 for e in entries if e[0] == "var"))

    @unittest.skipUnless(os.path.exists(os.path.join(SCEN, "_index.json")), "L3 scenarios not generated")
    def test_shipped_file_obeys_story_rule(self):
        with open(os.path.join(self.DATA, "volatile_keys.txt"), encoding="utf-8") as f:
            self.assertEqual(vp.check_txt(f.read(), vp.story_refs(SCEN)), [])

    def test_story_field_in_a_shipped_copy_would_fail(self):   # twin
        with open(os.path.join(self.DATA, "volatile_keys.txt"), encoding="utf-8") as f:
            text = f.read() + "field FalloutNV.esm:104C2A running" + chr(10)
        self.assertTrue(vp.check_txt(text, {"FalloutNV.esm:104C2A": "VMS01"}))


POS_SCRIPT = """scn VCG04QuestScript
float PlayerPositionX
float PlayerPositionY
short Done
float fTimer
float fStart
float fLeft
int iPick
begin GameMode
	set PlayerPositionX to (player.GetPos X)
	set PlayerPositionY to (player.GetPos Y)
	set fTimer to fTimer - GetSecondsPassed
	set fLeft to (fTimer - 5)
	if Done == 0
		set fStart to GameDaysPassed
		set iPick to GetRandomPercent
	endif
end
"""


class StaticScan(unittest.TestCase):
    """volatile_scan.py: script vars that hold a position or a per-frame clock are per-machine (Track A, 2026-10-22: VCG04 held
    the player's Y position and showed up as a desync of SetScriptVar 11649E var 16)."""
    NAMES = {"playerpositionx", "playerpositiony", "done", "ftimer", "fstart", "fleft", "ipick"}

    def test_position_timer_and_derived_vars_found_one_shots_left_out(self):
        got = {v: k for v, (k, _) in vs.scan_script(POS_SCRIPT, self.NAMES).items()}
        self.assertEqual(got, {"playerpositionx": "pos", "playerpositiony": "pos", "ftimer": "timer", "fleft": "derived"})

    def test_twin_without_the_position_rule_misses_the_player_position(self):
        got = vs.scan_script(POS_SCRIPT, self.NAMES, rules=("timer", "frametime", "derived"))
        self.assertNotIn("playerpositiony", got)   # the scan that does not know GetPos would let VCG04 var 16 stay shared

    def test_shipped_list_has_the_known_cases(self):
        vars_, _ = vs.shipped_vars()
        for key in (("falloutnv.esm:11649e", "playerpositionx"), ("falloutnv.esm:11649e", "playerpositiony"),
                    ("falloutnv.esm:171cdc", "1"), ("falloutnv.esm:171cdd", "1")):
            self.assertIn(key, vars_)

    def test_missing_reports_what_the_list_lacks(self):   # twin: a list without the VCG04 lines is flagged
        vars_, owners = vs.shipped_vars()
        cands = [("FalloutNV.esm:11649E", "VCG04", "playerpositiony", "pos", "x")]
        self.assertEqual(vs.missing(cands, vars_, owners), [])
        self.assertEqual(len(vs.missing(cands, vars_ - {("falloutnv.esm:11649e", "playerpositiony")}, owners)), 1)

    @unittest.skipUnless(os.path.exists(os.path.join(ROOT, "out", "questdata", "scripts.json.gz")), "L3 quest data not generated")
    def test_every_scanned_quest_var_is_in_the_shipped_list(self):
        cands = vs.scan(*vs.load_questdata(os.path.join(ROOT, "out", "questdata")))
        self.assertGreater(len(cands), 30)
        vars_, owners = vs.shipped_vars()
        self.assertEqual(vs.missing(cands, vars_, owners), [])




CURSOR_SCRIPT = """scn VERNovac01QuestScript
short iSpawnMonsters
short iFrame
short bGeckosDead
short bPlayed
begin GameMode
	if iSpawnMonsters == 0
		if iFrame == 0
			set iFrame to 1
			return
		else
			set iFrame to 0
			set iSpawnMonsters to 1
		endif
	elseif iSpawnMonsters == 1
		set iSpawnMonsters to 2
	elseif iSpawnMonsters == 2
		set iSpawnMonsters to 3
	else
		set iSpawnMonsters to 0
	endif
	if bGeckosDead == 1
		set bGeckosDead to 0
	else
		set bGeckosDead to 1
	endif
	set bPlayed to 1
end
"""


class CursorScan(unittest.TestCase):
    """Fight tests 2026-10-22 (enemy_fight, npc_sync, enemy_parallel_pairs): VERNovac01 iSpawnMonsters differed host 0 / guest 3. The
    spawner script runs on the host only and walks the counter 0..3 on every update: a per-machine cursor, never shared state."""
    NAMES = {"ispawnmonsters", "iframe", "bgeckosdead", "bplayed"}
    RULES = ("pos", "timer", "frametime", "derived", "cursor")

    def test_step_counters_found_flags_left_out(self):
        got = {v: k for v, (k, _) in vs.scan_script(CURSOR_SCRIPT, self.NAMES, self.RULES).items()}
        self.assertEqual(got, {"ispawnmonsters": "cursor"})   # iFrame has only 2 values here, the flags toggle 0/1

    def test_twin_without_the_cursor_rule_misses_the_spawn_counter(self):
        self.assertNotIn("ispawnmonsters", vs.scan_script(CURSOR_SCRIPT, self.NAMES))   # default rules: no cursor rule

    def test_only_encounter_quests_get_the_rule(self):
        scripts = [{"ref": "FalloutNV.esm:15F959", "script": {"type": "quest", "source": CURSOR_SCRIPT,
                                                              "vars": [{"name": n} for n in self.NAMES]}}]
        enc = {"falloutnv.esm:15f959": ("FalloutNV.esm:15F958", "VERNovac01")}
        self.assertEqual([(c[1], c[2], c[3]) for c in vs.scan(scripts, {}, encounters=enc)], [("VERNovac01", "ispawnmonsters", "cursor")])
        self.assertEqual(vs.scan(scripts, {}, encounters={}), [])   # twin: an ordinary quest script is not touched by the rule

    def test_shipped_list_has_the_spawner_counters(self):
        vars_, _ = vs.shipped_vars()
        for key in (("falloutnv.esm:15f958", "ispawnmonsters"), ("falloutnv.esm:157b4e", "ispawnmonsters"),
                    ("falloutnv.esm:1617a2", "icount"), ("falloutnv.esm:173faa", "icount")):
            self.assertIn(key, vars_)

    def test_shipped_list_has_the_per_machine_quest_vars_of_the_2026_10_22_sweep(self):
        # door_terminal_container_matrix: VMS31 iStoneSneak host 0 / guest 1 (cleared in GameMode by a day-of-week compare);
        # (VMS16 nGangerDeathCount is NOT per-machine: it is story progress that must sync; it stays off the list.)
        vars_, _ = vs.shipped_vars()
        for key in (("falloutnv.esm:1214ab", "istonesneak"),):
            self.assertIn(key, vars_)
        for key in (("falloutnv.esm:1214ab", "istonesneak"),):
            self.assertNotIn(key, vars_ - {key})      # twin: the list without the line misses the var (what the sweep saw)
        manual = json.load(open(os.path.join(HERE, "volatile_manual.json"), encoding="utf-8"))["add"]["vars"]
        self.assertIn("istonesneak", {v["name"].lower() for v in manual}, "the manual list keeps it across a regenerate")

    def test_inventory_lists_the_spawner_quests(self):
        enc = vs.load_encounters()
        self.assertEqual(enc["falloutnv.esm:15f959"], ("FalloutNV.esm:15F958", "VERNovac01"))


RAIN_SCRIPT = """scn NVDLC02rainscript
float UPang
float fTimer
short bRainSwap
begin GameMode
	set UPang to player.getangle x
	if (fTimer >= 0)
		set fTimer to ( fTimer - GetSecondsPassed )
	endif
end
"""


class QuestsJsonScan(unittest.TestCase):
    """Two-copy tests 2026-10-22: NVDLC02RainQuest var 138 (UPang = player.getangle x) differed host 0.42 / guest 0 in every step of every
    test. The scan found nothing there: forms.json lists only some QUSTs, and the rain quest (like every DLC quest and ~270 base-game ones)
    is only in quests.json."""
    RAIN = "HonestHearts.esm:00EDE7"
    QUESTS = {"quests": [{"ref": RAIN, "edid": "NVDLC02RainQuest", "script": "HonestHearts.esm:00EDE5"},
                         {"ref": "FalloutNV.esm:104C1C", "edid": "VCG01", "script": "FalloutNV.esm:104C1D"}]}
    FORMS = {"FalloutNV.esm:104C1C": {"sig": "QUST", "edid": "VCG01", "script": "FalloutNV.esm:104C1D"}}
    SCRIPTS = [{"ref": "HonestHearts.esm:00EDE5", "script": {"type": "quest", "source": RAIN_SCRIPT,
                                                              "vars": [{"name": "UPang"}, {"name": "fTimer"}, {"name": "bRainSwap"}]}}]

    def merged(self):
        return dict(self.FORMS, **vs.quests_missing_from_forms(self.FORMS, self.QUESTS))

    def test_quest_only_in_quests_json_is_scanned(self):
        got = [(c[1], c[2], c[3]) for c in vs.scan(self.SCRIPTS, self.merged(), encounters={})]
        self.assertEqual(got, [("NVDLC02RainQuest", "ftimer", "timer"), ("NVDLC02RainQuest", "upang", "pos")])

    def test_twin_forms_json_alone_misses_the_rain_quest(self):
        self.assertEqual(vs.scan(self.SCRIPTS, self.FORMS, encounters={}), [])   # the pre-fix scan: nothing, UPang stayed shared

    def test_quests_forms_already_has_are_not_replaced(self):
        self.assertEqual(list(vs.quests_missing_from_forms(self.FORMS, self.QUESTS)), [self.RAIN])

    def test_shipped_list_has_the_retest_cases(self):
        vars_, _ = vs.shipped_vars()
        for key in (("honesthearts.esm:00ede7", "upang"), ("honesthearts.esm:00ede7", "ftimer"), ("falloutnv.esm:11649e", "done"),
                    ("falloutnv.esm:127e9d", "lightson"), ("falloutnv.esm:13aaca", "lightson")):
            self.assertIn(key, vars_)

    def test_twin_a_list_without_the_rain_lines_is_flagged(self):
        vars_, owners = vs.shipped_vars()
        cands = vs.scan(self.SCRIPTS, self.merged(), encounters={})
        self.assertEqual(vs.missing(cands, vars_, owners), [])
        self.assertEqual(len(vs.missing(cands, vars_ - {("honesthearts.esm:00ede7", "upang")}, owners)), 1)


class SettleWindow(unittest.TestCase):
    """State that converges in the first seconds after load (DLC startup quests) is 'settling', not volatile."""

    def series(self):
        a0 = snap()
        a1 = tick(a0, **{"shared/quests/TestQuest00/vars/fStartTimer": 5.0,        # changes only at interval 1
                         "shared/quests/TestQuest01/running": False,
                         "shared/refs/DocMitchell/vars/fRefTimer": 1.0})           # keeps changing below
        a2 = tick(a1, **{"shared/refs/DocMitchell/vars/fRefTimer": 2.0})
        a3 = tick(a2, **{"shared/refs/DocMitchell/vars/fRefTimer": 3.0})
        a4 = tick(a3, **{"shared/refs/DocMitchell/vars/fRefTimer": 4.0})
        return [a0, a1, a2, a3, a4]

    def test_early_only_changes_are_settling_not_volatile(self):
        res = vp.probe([self.series()], STORY, settle=2, interval_s=4.0)
        vols = {(v["owner"], v["name"]) for v in res["vars"]}
        self.assertEqual(vols, {(DOC, "freftimer")})                     # still changing after the window
        self.assertEqual(res["fields"], [])                              # TestQuest01 running only settled
        settling = {tuple(s["key"]) for s in res["settling"]}
        self.assertIn(("field", TQ1, "running"), settling)
        self.assertIn(("var", TQ0, "fstarttimer"), settling)
        self.assertEqual(res["recommendedSettleSeconds"], 4.0)           # last settling change after dump 1 (1 * 4 s)
        self.assertEqual(vp.check_txt(vp.render_txt(res), STORY), [])

    def test_without_a_window_everything_that_moves_is_volatile(self):   # twin: the window is what separates them
        res = vp.probe([self.series()], STORY, settle=0)
        self.assertEqual({(v["owner"], v["name"]) for v in res["vars"]}, {(TQ0, "fstarttimer"), (DOC, "freftimer")})
        self.assertEqual([(f["quest"], f["field"]) for f in res["fields"]], [(TQ1, "running")])
        self.assertEqual(res["settling"], [])

    def test_cross_launch_compares_settled_snapshots(self):
        s = self.series()
        # launch B settles to the same final state: no launch-level volatility although its early dumps differ
        b = [snap(), tick(snap(), **{"shared/quests/TestQuest00/vars/fStartTimer": 5.0}), s[-1], s[-1], s[-1]]
        res = vp.probe([s, b], STORY, settle=2, interval_s=4.0)
        self.assertNotIn((TQ0, "fstarttimer"), {(v["owner"], v["name"]) for v in res["vars"]})


class Check(unittest.TestCase):
    def test_story_field_line_is_a_violation(self):
        probs = vp.check_txt("field %s running   # VMS01\n" % VMS01, STORY)
        self.assertEqual(len(probs), 1)
        self.assertIn("STORY", probs[0])

    def test_story_var_line_is_fine(self):
        self.assertEqual(vp.check_txt("var %s fTimer\n" % VMS01, STORY), [])

    def test_whole_quest_and_garbage_rejected(self):
        self.assertTrue(vp.check_txt("quest %s\n" % VMS01, STORY))
        self.assertTrue(vp.check_txt("field %s bogus\n" % TQ0, STORY))
        self.assertTrue(vp.check_txt("var %s\n" % TQ0, STORY))
        self.assertEqual(vp.check_txt("# only comments\n\n", STORY), [])


class MutationTwins(unittest.TestCase):
    """A generator that ignores the story rule puts a story quest in the filter; the check must catch it."""

    def test_mutant_without_story_rule_is_caught(self):
        a = snap()
        b = tick(a, **{"shared/quests/VMS01/running": False})
        real = vp.render_txt(vp.probe([[a, b]], STORY))
        mutant = vp.render_txt(vp.probe([[a, b]], STORY, _mutant_ignore_story=True))
        self.assertEqual(vp.check_txt(real, STORY), [])
        problems = vp.check_txt(mutant, STORY)
        self.assertTrue(problems and "STORY" in problems[0])

    def test_story_quest_in_filter_fails_cli_check(self):
        with tempfile.TemporaryDirectory() as d:
            scen = os.path.join(d, "s")
            os.mkdir(scen)
            with open(os.path.join(scen, "_index.json"), "w") as f:
                json.dump({"scenarios": [{"file": "VMS01.json", "quest": "VMS01", "kind": "story"},
                                         {"file": "X.json", "quest": "X", "kind": "container"}]}, f)
            with open(os.path.join(scen, "VMS01.json"), "w") as f:
                json.dump({"quest": "VMS01", "ref": VMS01}, f)
            with open(os.path.join(scen, "X.json"), "w") as f:
                json.dump({"quest": "X", "ref": TQ0}, f)
            good, bad = os.path.join(d, "good.txt"), os.path.join(d, "bad.txt")
            with open(good, "w") as f:
                f.write("var %s fTimer\nfield %s running\n" % (VMS01, TQ0))
            with open(bad, "w") as f:
                f.write("field %s running\n" % VMS01)
            py = [sys.executable, "-I", os.path.join(HERE, "volatile_probe.py"), "check"]
            q = dict(stdout=subprocess.DEVNULL)
            self.assertEqual(subprocess.call(py + [good, "--scenarios", scen], **q), 0)
            self.assertEqual(subprocess.call(py + [bad, "--scenarios", scen], **q), 1)
            self.assertEqual(subprocess.call(py + [bad], **q), 2)   # no safety list => refuse

    def test_cli_probe_end_to_end(self):
        a = snap()
        b = tick(a, **{"shared/quests/TestQuest00/vars/fStartTimer": 9.0, "shared/quests/VMS01/running": False})
        with tempfile.TemporaryDirectory() as d:
            scen = os.path.join(d, "s")
            os.mkdir(scen)
            with open(os.path.join(scen, "_index.json"), "w") as f:
                json.dump({"scenarios": [{"file": "VMS01.json", "quest": "VMS01", "kind": "story"}]}, f)
            with open(os.path.join(scen, "VMS01.json"), "w") as f:
                json.dump({"quest": "VMS01", "ref": VMS01}, f)
            pa, pb = os.path.join(d, "a.json"), os.path.join(d, "b.json")
            for p, o in ((pa, a), (pb, b)):
                with open(p, "w") as f:
                    json.dump(o, f)
            tj, tt = os.path.join(d, "v.json"), os.path.join(d, "v.txt")
            manual = os.path.join(d, "manual.json")        # isolated from the shipped manual additions
            with open(manual, "w") as f:
                json.dump({"protect": {}, "add": {}}, f)
            r = subprocess.run([sys.executable, "-I", os.path.join(HERE, "volatile_probe.py"), "probe", "--scenarios", scen,
                                "--manual", manual, "--json", tj, "--txt", tt, "--group", pa, pb], capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertIn("WARNING story quest VMS01", r.stdout)
            with open(tt) as f:
                lines = [l for l in f.read().splitlines() if l and not l.startswith("#")]
            self.assertEqual(len(lines), 1)
            self.assertTrue(lines[0].startswith("var %s fstarttimer" % TQ0))


@unittest.skipUnless(os.path.exists(os.path.join(SCEN, "_index.json")), "L3 scenarios not generated")
class RealStoryList(unittest.TestCase):
    def test_story_list_matches_l3_and_protects_known_quests(self):
        story = vp.story_refs(SCEN)
        self.assertGreater(len(story), 200)
        self.assertIn("VMS01", story.values())
        # a real story quest in the filter must fail the check
        ref = next(r for r, e in story.items() if e == "VMS01")
        self.assertTrue(vp.check_txt("field %s stage\n" % ref, story))


if __name__ == "__main__":
    unittest.main(verbosity=2)
