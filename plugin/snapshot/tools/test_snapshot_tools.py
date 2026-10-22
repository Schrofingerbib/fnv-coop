#!/usr/bin/env python3
"""Tests for snapdiff/converge/guard against synthetic goldens in tests/goldens/snapshot/.
Run: python -I plugin/snapshot/tools/test_snapshot_tools.py   (or via run_tests.py)

Mutation twins: deliberately broken differs (blind to stage changes / skipping the empty guard) are run
through the SAME oracle checks and those checks must FAIL on them.
"""
import copy
import hashlib
import os
import subprocess
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import converge  # noqa: E402
import snapdiff  # noqa: E402
import snaplib  # noqa: E402

GOLD = os.path.abspath(os.path.join(HERE, "..", "..", "..", "tests", "goldens", "snapshot"))


def g(name):
    return snaplib.load(os.path.join(GOLD, name + ".json"))


# ---------------------------------------------------------------- the real differ + mutants
def real_compare(a, b, **kw):
    return snapdiff.compare(a, b, **kw)[0]


def mutant_blind_to_stage(a, b, **kw):
    """Broken differ: forgets to look at currentStage / completedStages."""
    problems = snaplib.guard(a) + snaplib.guard(b)
    if problems:
        return 2
    d = snaplib.diff(a, b, ignore=["*/currentStage", "*/completedStages"])
    return 1 if d else 0


def mutant_no_guard(a, b, **kw):
    """Broken differ: no empty-snapshot guard, so two empty dumps compare equal."""
    return 1 if snaplib.diff(a, b) else 0


# ---------------------------------------------------------------- oracle checks (return True when the differ behaves)
def check_catches_stage_change(cmp):
    return cmp(g("base"), g("stage_changed")) == 1


def check_catches_objective_change(cmp):
    return cmp(g("base"), g("objective_changed")) == 1


def check_rejects_empty(cmp):
    return all(cmp(g(n), g(n)) == 2 for n in ("empty", "empty_shell", "near_empty", "errored"))


def check_identical(cmp):
    return cmp(g("base"), g("base")) == 0


class RealDiffer(unittest.TestCase):
    def test_identical_passes(self):
        self.assertTrue(check_identical(real_compare))

    def test_reordered_keys_still_equal(self):
        a, b = g("base"), g("reordered")
        self.assertNotEqual(list(a["shared"]["quests"]), list(b["shared"]["quests"]))  # fixture really is reordered
        self.assertEqual(real_compare(a, b), 0)

    def test_stage_change_caught_and_reported(self):
        code, _, d = snapdiff.compare(g("base"), g("stage_changed"))
        self.assertEqual(code, 1)
        self.assertEqual(len(d), 1)
        self.assertIn("VMS01", d[0]["label"])
        self.assertTrue(d[0]["path"].endswith("/currentStage"))
        self.assertEqual((d[0]["a"], d[0]["b"]), (10, 20))

    def test_other_semantic_changes_caught(self):
        for n in ("completed_stage_added", "objective_changed", "doc_dead", "float_changed"):
            self.assertEqual(real_compare(g("base"), g(n)), 1, n)

    def test_noise_within_tolerance_and_volatile_clock_and_meta_ignored(self):
        self.assertEqual(real_compare(g("base"), g("noise_ok")), 0)

    def test_noise_not_ignored_when_tolerance_tightened(self):
        code = snapdiff.compare(g("base"), g("noise_ok"), tolerances=[("shared/globals/*/value", 1e-6)])[0]
        self.assertEqual(code, 1)

    def test_clock_global_diff_reported_when_default_ignores_off(self):
        d = snaplib.diff(g("base"), g("noise_ok"), default_ignores=False)
        self.assertTrue(any("GameHour" in e["label"] for e in d))

    def test_perplayer_ignored_by_default_but_caught_when_requested(self):
        self.assertEqual(real_compare(g("base"), g("perplayer_changed")), 0)
        self.assertEqual(real_compare(g("base"), g("perplayer_changed"), sections=("shared", "perPlayer")), 1)

    def test_position_tolerance_in_perplayer(self):
        a = g("base")
        b = copy.deepcopy(a)
        b["perPlayer"]["host"]["position"]["x"] += 0.5  # < 1 unit tolerance
        self.assertEqual(real_compare(a, b, sections=("shared", "perPlayer")), 0)
        b["perPlayer"]["host"]["position"]["x"] += 5
        self.assertEqual(real_compare(a, b, sections=("shared", "perPlayer")), 1)

    def test_meta_only_diffed_on_request(self):
        a = g("base")
        b = copy.deepcopy(a)
        b["meta"]["pluginsHash"] = "ffffffffffffffff"
        self.assertEqual(real_compare(a, b), 0)
        self.assertEqual(real_compare(a, b, include_meta=True), 1)

    def test_empty_and_degenerate_snapshots_fail_not_pass(self):
        self.assertTrue(check_rejects_empty(real_compare))
        # even two empties that are byte-identical must be rejected
        self.assertEqual(real_compare(g("empty"), g("empty")), 2)

    def test_truncated_dump_detected_via_counts(self):
        problems = snaplib.guard(g("truncated"))
        self.assertTrue(any("truncated" in p for p in problems), problems)
        self.assertEqual(real_compare(g("base"), g("truncated")), 2)

    def test_schema_violation_rejected(self):
        self.assertEqual(real_compare(g("base"), g("bad_schema")), 2)

    def test_added_and_removed_quest_reported(self):
        a = g("base")
        b = copy.deepcopy(a)
        q = b["shared"]["quests"].pop("FalloutNV.esm:104C2A")
        b["meta"]["counts"]["quests"] -= 1
        d = snaplib.diff(a, b)
        self.assertTrue(d and all(e["kind"] == "removed" for e in d))
        d2 = snaplib.diff(b, a)
        self.assertTrue(all(e["kind"] == "added" for e in d2))

    def test_guard_accepts_base_and_all_valid_variants(self):
        for n in ("base", "reordered", "stage_changed", "noise_ok", "float_changed", "perplayer_changed"):
            self.assertEqual(snaplib.guard(g(n), ("shared", "perPlayer")), [], n)

    def test_cli_exit_codes(self):
        py = [sys.executable, "-I", os.path.join(HERE, "snapdiff.py")]
        f = lambda n: os.path.join(GOLD, n + ".json")
        self.assertEqual(subprocess.call(py + [f("base"), f("reordered")], stdout=subprocess.DEVNULL), 0)
        self.assertEqual(subprocess.call(py + [f("base"), f("stage_changed")], stdout=subprocess.DEVNULL), 1)
        self.assertEqual(subprocess.call(py + [f("empty"), f("empty")], stdout=subprocess.DEVNULL), 2)
        self.assertEqual(subprocess.call(py + [f("base"), f("missing")], stdout=subprocess.DEVNULL), 2)


class Converge(unittest.TestCase):
    def test_converged(self):
        code, lines = converge.check([("host", g("base")), ("c1", g("reordered")), ("c2", g("noise_ok"))])
        self.assertEqual(code, 0, lines)

    def test_divergence_names_the_client(self):
        code, lines = converge.check([("host", g("base")), ("c1", g("reordered")), ("c2", g("stage_changed"))])
        self.assertEqual(code, 1)
        text = "\n".join(lines)
        self.assertIn("DIVERGED c2", text)
        self.assertIn("converged c1", text)
        self.assertIn("VMS01", text)

    def test_empty_client_fails_not_vacuous(self):
        code, _ = converge.check([("host", g("base")), ("c1", g("empty"))])
        self.assertEqual(code, 2)
        code, _ = converge.check([("host", g("empty")), ("c1", g("empty"))])
        self.assertEqual(code, 2)

    def test_needs_two(self):
        self.assertEqual(converge.check([("host", g("base"))])[0], 2)

    def test_karma_must_be_shared(self):
        code, lines = converge.check([("host", g("base")), ("c1", g("karma_unshared"))], sections=("shared", "perPlayer"))
        self.assertEqual(code, 1, lines)
        self.assertTrue(any("KARMA NOT SHARED" in l for l in lines))

    def test_quest_items_to_everyone(self):
        code, lines = converge.check([("host", g("base")), ("c1", g("base"))],
                                     require_items=["FalloutNV.esm:0DE1AA"], sections=("shared", "perPlayer"))
        self.assertEqual(code, 0, lines)
        code, lines = converge.check([("host", g("base")), ("c1", g("base"))],
                                     require_items=["FalloutNV.esm:0DEFFF"], sections=("shared", "perPlayer"))
        self.assertEqual(code, 1)


class MutationTwins(unittest.TestCase):
    """The oracle checks must pass on the real differ and FAIL on each mutant."""

    def test_real_passes_all_checks(self):
        for chk in (check_catches_stage_change, check_catches_objective_change, check_rejects_empty, check_identical):
            self.assertTrue(chk(real_compare), chk.__name__)

    def test_stage_blind_mutant_is_caught(self):
        self.assertFalse(check_catches_stage_change(mutant_blind_to_stage))
        self.assertTrue(check_catches_objective_change(mutant_blind_to_stage))  # control: it still sees other things

    def test_no_guard_mutant_is_caught(self):
        self.assertFalse(check_rejects_empty(mutant_no_guard))
        self.assertTrue(check_catches_stage_change(mutant_no_guard))  # control


class LocalRunClassification(unittest.TestCase):
    """GuestLocalRun quests are per-machine by definition: shared compare must ignore them."""
    LIVE = os.path.abspath(os.path.join(HERE, "..", "..", "..", "out", "L10", "twogame", "live"))

    def _local_edid(self):
        return sorted(snaplib.load_local_allowlist()[0])[0]

    def _pair(self):
        a = g("base")
        b = copy.deepcopy(a)
        qk = next(iter(b["shared"]["quests"]))
        a["shared"]["quests"][qk]["edid"] = b["shared"]["quests"][qk]["edid"] = self._local_edid()
        b["shared"]["quests"][qk]["currentStage"] += 10
        return a, b, qk

    def test_local_quest_diff_ignored_in_shared(self):
        a, b, _ = self._pair()
        self.assertEqual(real_compare(a, b), 0)

    def test_local_quest_moved_to_perplayer(self):
        a, b, qk = self._pair()
        la = snaplib.localize(b)
        self.assertNotIn(qk, la["shared"]["quests"])
        self.assertIn(qk, la["perPlayer"]["host"]["localQuests"])
        self.assertEqual(real_compare(a, b, sections=("shared", "perPlayer")), 1)

    def test_twin_keeping_local_shared_still_diffs(self):
        a, b, _ = self._pair()
        self.assertEqual(snapdiff.compare(a, b, local_split=False)[0], 1)

    def test_non_local_quest_still_caught(self):
        a, b, qk = self._pair()
        a["shared"]["quests"][qk]["edid"] = b["shared"]["quests"][qk]["edid"] = "NotOnTheList"
        self.assertEqual(real_compare(a, b), 1)

    def test_faction_rank_listed_is_local(self):
        a, b = g("base"), g("base")
        b["shared"].setdefault("factionRanks", {})["X.esm:000ABC"] = {"edid": "", "rank": 1}
        al = (set(), {"X.esm:000abc"})
        self.assertEqual(snaplib.diff(snaplib.localize(a, al), snaplib.localize(b, al)), [])
        self.assertEqual(len(snaplib.diff(a, b, local_split=False)), 2)

    @unittest.skipUnless(os.path.exists(os.path.join(LIVE, "S_client.json")), "L10 live fixture absent")
    def test_l10_live_fixture(self):
        a = snaplib.load(os.path.join(self.LIVE, "S_host.json"))
        b = snaplib.load(os.path.join(self.LIVE, "S_client.json"))
        twin = snaplib.diff(a, b, local_split=False)
        real = snaplib.diff(a, b)
        self.assertGreaterEqual(len(twin), 40)
        self.assertLess(len(real), len(twin) - 25)
        names = snaplib.load_local_allowlist()[0]
        for e in real:
            self.assertNotIn(e["label"].split(" ")[0].split("/")[-1].lower(), names, e)


class VolatileQuestVars(unittest.TestCase):
    """Quest timers in the plugin's volatile-keys list (per-machine clocks) never count as shared divergence."""

    def snap(self, curtime):
        q = {"edid": "REPCONHQFreeform", "vars": {"CurTime": curtime, "Done": 1}}
        return {"schemaVersion": 1, "meta": {}, "perPlayer": {"0": {}}, "shared": {"quests": {"FalloutNV.esm:0E7533": q}}}

    def test_listed_timer_is_ignored_but_a_real_var_is_not(self):
        self.assertIn(("falloutnv.esm:0e7533", "curtime"), snaplib.load_volatile_vars())
        a, b = self.snap(8.0072), self.snap(0.393561)
        self.assertEqual(snaplib.diff(a, b), [])
        b["shared"]["quests"]["FalloutNV.esm:0E7533"]["vars"]["Done"] = 0   # a story var still differs
        self.assertEqual([d["path"] for d in snaplib.diff(a, b)], ["shared/quests/FalloutNV.esm:0E7533/vars/Done"])

    def test_twin_pre_classification_diff_keeps_the_timer(self):
        self.assertEqual(len(snaplib.diff(self.snap(8.0), self.snap(0.4), local_split=False)), 1)

    @staticmethod
    def fight_snap(ede, gom_time, gom_days, stalkers_on, stalkers_count):
        """The quest vars of the first two-copy enemy_fight run in game (2026-10-21, step 01, host vs guest)."""
        q = {"FalloutNV.esm:1572E8": {"edid": "vDialogueEDE", "vars": {"iCounter": ede}},
             "FalloutNV.esm:10C721": {"edid": "vDialogueGomorrah", "vars": {"Time": gom_time, "fDays": gom_days}},
             "FalloutNV.esm:1617A2": {"edid": "VERStalkersSouth01", "vars": {"StalkersOn": stalkers_on, "iCount": stalkers_count}}}
        return {"schemaVersion": 1, "meta": {}, "perPlayer": {"0": {}}, "shared": {"quests": q}}

    def test_tutorial_message_timer_is_per_machine_but_its_stages_are_not(self):
        # in game 2026-10-21/22: CGTutorial VATSTimer host 25 / 22.95 / 17.0 / 5.0, guest 0; completedStages host {2}, guest {2, 70}
        self.assertIn(("falloutnv.esm:059c85", "vatstimer"), snaplib.load_volatile_vars())

        def snap(timer, stages):
            q = {"FalloutNV.esm:059C85": {"edid": "CGTutorial", "vars": {"VATSTimer": timer}, "completedStages": stages}}
            return {"schemaVersion": 1, "meta": {}, "perPlayer": {"0": {}}, "shared": {"quests": q}}
        self.assertEqual(snaplib.diff(snap(16.999, [2]), snap(0, [2])), [])
        # the stage difference still counts (story state is never volatile)
        self.assertEqual([d["path"] for d in snaplib.diff(snap(5.0, [2]), snap(0, [2, 70]))],
                         ["shared/quests/FalloutNV.esm:059C85/completedStages"])
        # twin: without the volatile classification the timer differs
        self.assertEqual(len(snaplib.diff(snap(16.999, [2]), snap(0, [2]), local_split=False)), 1)

    def test_fight_run_frame_counter_and_clock_copies_are_per_machine(self):
        for key in (("falloutnv.esm:1572e8", "icounter"), ("falloutnv.esm:10c721", "time"), ("falloutnv.esm:10c721", "fdays")):
            self.assertIn(key, snaplib.load_volatile_vars())
        host, guest = self.fight_snap(41, 6.333857, 6.333857, 0, 18), self.fight_snap(40, 5.959093, 6.028465, 1, 0)
        # iCount is the night-stalker spawner's step counter (a host-private cursor, listed since 2026-10-22); only its StalkersOn flag still
        # differs: the script ran on the host and not on the guest (a real difference)
        self.assertEqual(sorted(d["path"] for d in snaplib.diff(host, guest)), ["shared/quests/FalloutNV.esm:1617A2/vars/StalkersOn"])
        # twin: without the volatile classification all five differ, as in the failing run
        self.assertEqual(len(snaplib.diff(host, guest, local_split=False)), 5)

    def test_spawner_step_counters_are_per_machine(self):
        # enemy_fight / npc_sync / enemy_parallel_pairs in game 2026-10-22: VERNovac01 iSpawnMonsters host 0 / guest 3 (the host-only spawner
        # walks it 0..3 on every update; the guest never runs that script)
        for key in (("falloutnv.esm:15f958", "ispawnmonsters"), ("falloutnv.esm:15f958", "iframe"), ("falloutnv.esm:157b4e", "ispawnmonsters")):
            self.assertIn(key, snaplib.load_volatile_vars())

        def snap(n, frame):
            q = {"FalloutNV.esm:15F958": {"edid": "VERNovac01", "vars": {"iSpawnMonsters": n, "iFrame": frame, "bVipersDead": 0}}}
            return {"schemaVersion": 1, "meta": {}, "perPlayer": {"0": {}}, "shared": {"quests": q}}
        self.assertEqual(snaplib.diff(snap(0, 0), snap(3, 4)), [])
        self.assertEqual(len(snaplib.diff(snap(0, 0), snap(3, 4), local_split=False)), 2)   # twin: the pre-fix compare
        bad = snap(3, 0)
        bad["shared"]["quests"]["FalloutNV.esm:15F958"]["vars"]["bVipersDead"] = 1   # a story-ish flag of the same quest still counts
        self.assertEqual([d["path"] for d in snaplib.diff(snap(0, 0), bad)], ["shared/quests/FalloutNV.esm:15F958/vars/bVipersDead"])


class GoldensProtected(unittest.TestCase):
    def test_manifest_matches(self):
        with open(os.path.join(GOLD, "MANIFEST.sha256")) as f:
            entries = [l.split() for l in f if l.strip()]
        self.assertGreaterEqual(len(entries), 10)
        for digest, name in entries:
            with open(os.path.join(GOLD, name), "rb") as fh:
                self.assertEqual(hashlib.sha256(fh.read()).hexdigest(), digest, "golden modified: " + name)


if __name__ == "__main__":
    unittest.main(verbosity=2)
