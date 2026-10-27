#!/usr/bin/env python3
"""Game-free tests for the five VATS playbooks (tools/bots/playbooks/pb_vats.py), with mutation twins.

Run: python -I tools/bots/playbooks/tests/run_vats_tests.py   (prints ALL OK)

What is proven:
  * every playbook passes on a correct fake co-op world with VATS (VatsWorld) with 2 and 3 players, and runs at least 8 steps;
  * every twin (playbook + one deliberately wrong fake behaviour) FAILS, and the failing step names the problem;
  * a playbook without its scenario refs is a clear failure, not a crash;
  * the helper layer (limb choice, target choice, fire, cancel) behaves on the fake bridge the way plugin/vats documents it;
  * vats_world_moves (the measured hand steps: the other copy's clock and a walking NPC while VATS is open, the VATS player cannot walk, the selected
    limb is the hit limb) passes on the healthy fake, and each of its twins (frozen clock, frozen NPC, walking in VATS, locked controls, wrong limb)
    fails with the reason that names it; it also fails, not passes, when its own measurement cannot be made (no NPC motion, clock at TimeScale 0);
  * run_vats.py --fake runs it (exit 0) and fails it with a bug (exit 1).
"""
from __future__ import annotations

import atexit
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))
sys.path.insert(0, os.path.join(REPO, "tools", "bots"))
sys.path.insert(0, os.path.join(REPO, "tools"))

from playbooks import core, fakecoop, pb_vats as V  # noqa: E402

OPTS = {"fast": True, "anim": False, "ui_s": 0.2, "barrier_s": 0.2, "stage_s": 0.2, "handoff_s": 0.2, "spawn_s": 0.2, "settle_s": 0.3, "settle_poll_s": 0.0,
        "config_s": 5.0, "repo_root": REPO}
CLASS_OF = {"vats_gun": "gun", "vats_melee": "melee", "vats_explosive": "explosive", "vats_energy": "energy", "vats_world_moves": "any"}
CLASSES = tuple(n for n in CLASS_OF if n != "vats_world_moves")        # the four weapon-class playbooks
RUN_VATS = os.path.join(REPO, "tools", "bots", "run_vats.py")


def run(name, bugs=(), n=2, opts=None, refs_drop=(), edit=None):
    w, refs = V.fake_vats_class(CLASS_OF[name])(n, set(bugs), lag=1)
    for k in refs_drop:
        refs.pop(k, None)
    if edit:
        edit(w)
    d = tempfile.mkdtemp(prefix="vats-pb-")
    atexit.register(shutil.rmtree, d, True)
    w.flight_dir = os.path.join(d, "flight")
    o = dict(OPTS)
    o.update(opts or {})
    return core.run_playbook(V.make_vats(name), fakecoop.make_machines(w), fakecoop.FakeComparer(), d, refs, o), w


class Clean(unittest.TestCase):
    def test_every_vats_playbook_passes_with_two_and_three_players(self):
        for name in CLASSES:
            for n in (2, 3):
                with self.subTest(f"{name} x{n}"):
                    r, _ = run(name, n=n)
                    self.assertTrue(r.ok, f"{name} x{n}: {r.reason} / " + "; ".join(s.reason for s in r.steps if not s.ok))
                    self.assertGreaterEqual(len(r.steps), 8, "a VATS playbook has at least 8 steps")

    def test_party_slow_mo_step_is_optional_and_runs_when_asked(self):
        r, _ = run("vats_gun", opts={"slowmo": True})
        self.assertTrue(r.ok, r.reason or "; ".join(s.reason for s in r.steps if not s.ok))
        self.assertTrue(any("slow-mo" in s.name for s in r.steps))

    def test_every_class_has_its_own_weapon_and_playbook(self):
        self.assertEqual({CLASS_OF[n] for n in CLASSES}, set(V.CLASSES))
        self.assertEqual(set(V.WEAPON_IDS), set(V.CLASSES))
        self.assertEqual(set(CLASS_OF), set(V.VATS_REGISTRY))
        self.assertEqual(V.VATS_CLASS_PLAYBOOKS, CLASSES)
        for name, cls in CLASS_OF.items():
            self.assertEqual(V.make_vats(name).weapon_class, cls)

    def test_a_missing_scenario_ref_is_a_clear_failure(self):
        for drop in ("target", "weapon"):
            r, _ = run("vats_gun", refs_drop=(drop,))
            self.assertFalse(r.ok)
            self.assertIn(drop, r.reason)


class Twins(unittest.TestCase):
    def test_every_twin_is_caught(self):
        self.assertGreaterEqual(len(V.VATS_TWINS), 25)
        for name, bug in V.VATS_TWINS:
            with self.subTest(f"{name} + {bug}"):
                self.assertIn(bug, V.VATS_BUGS)
                r, _ = run(name, bugs=(bug,))
                self.assertFalse(r.ok, f"{name} passed with the deliberate bug {bug}")
                self.assertTrue(r.reason or any((not s.ok) and s.reason for s in r.steps), "a failure without a reason")

    def test_the_slow_mo_twin_is_caught_when_the_step_runs(self):
        for name, bug in V.VATS_SLOWMO_TWINS:
            r, _ = run(name, bugs=(bug,), opts={"slowmo": True})
            self.assertFalse(r.ok, f"{name} passed with {bug}")
            r, _ = run(name, bugs=(bug,))
            self.assertTrue(r.ok, f"{bug} must not matter without the slow-mo step")

    def test_every_bug_has_a_twin_and_every_class_is_in_the_twins(self):
        self.assertEqual({b for _p, b in V.VATS_TWINS} | {b for _p, b in V.VATS_SLOWMO_TWINS}, set(V.VATS_BUGS))
        self.assertEqual({p for p, _b in V.VATS_TWINS}, set(V.VATS_REGISTRY))


class WorldMoves(unittest.TestCase):
    """vats_world_moves: the hand steps of INGAME-TESTS step 4, measured on the fake VATS world."""

    def reasons(self, r):
        return " | ".join(s.reason for s in r.steps if not s.ok) or r.reason

    def test_passes_on_a_healthy_world_with_two_and_three_players(self):
        for n in (2, 3):
            r, _ = run("vats_world_moves", n=n)
            self.assertTrue(r.ok, f"x{n}: {self.reasons(r)}")
            self.assertGreaterEqual(len(r.steps), 5)
            # one 'sits in VATS' measurement step per player, each watched on every other copy
            self.assertEqual(len([s for s in r.steps if "sits in VATS" in s.name]), n)

    def test_both_clock_readers_work(self):
        """worldtime.clock when the module is loaded, the GameHour / TimeScale globals when it is not."""
        for no_wt in (False, True):
            r, w = run("vats_world_moves", edit=lambda w, v=no_wt: setattr(w, "no_worldtime", v))
            self.assertTrue(r.ok, f"no_worldtime={no_wt}: {self.reasons(r)}")
            self.assertEqual(any(c[0] == "worldtime.clock" for b in w.bots for c in b.nv.calls), True)     # it is always tried first

    def test_the_clock_wrapping_midnight_is_not_a_failure(self):
        r, _ = run("vats_world_moves", edit=lambda w: setattr(w, "clock_h", [23.99, 23.99]))
        self.assertTrue(r.ok, self.reasons(r))
        self.assertAlmostEqual(V.clock_delta_h(23.99, 0.02), 0.03, places=6)
        self.assertAlmostEqual(V.clock_delta_h(0.0, 23.0), -1.0, places=6)
        self.assertEqual(V.clock_delta_h(5.0, 5.0), 0.0)

    def test_path_length(self):
        self.assertEqual(V.path_length([(0, 0, 0), (3, 4, 0), (3, 4, 12)]), 17.0)
        self.assertEqual(V.path_length([(1, 1, 1)]), 0.0)

    def test_no_weapon_argument_is_needed_when_the_player_holds_one(self):
        def hold(w):
            for p in w.players:
                p["equipped"] = V.WEAPON_IDS["gun"]
        r, _ = run("vats_world_moves", refs_drop=("weapon",), edit=hold)
        self.assertTrue(r.ok, self.reasons(r))

    def test_no_weapon_at_all_is_a_clear_failure(self):
        def strip(w):
            for p in w.players:
                p["inv"].clear()
        r, _ = run("vats_world_moves", refs_drop=("weapon",), edit=strip)
        self.assertFalse(r.ok)
        self.assertIn("--weapon any=", self.reasons(r))

    def test_each_twin_fails_with_the_reason_that_names_it(self):
        want = {"vats_pauses_others": "game clock", "vats_world_paused": "game clock", "vats_clock_frozen": "game clock", "vats_npcs_frozen": "NPC walked",
                "vats_player_walks": "while the VATS menu was open", "vats_controls_stay_locked": "after leaving VATS",
                "vats_limb_ignored": "never dropped", "vats_limb_hits_neighbour": "never dropped", "vats_reports_wrong_limb": "vats.state highlights"}
        self.assertEqual(set(want), {b for p, b in V.VATS_TWINS if p == "vats_world_moves"})
        for bug, text in want.items():
            r, _ = run("vats_world_moves", bugs=(bug,))
            self.assertFalse(r.ok, f"passed with {bug}")
            self.assertIn(text, self.reasons(r), f"{bug}: {self.reasons(r)}")

    def test_the_freeze_twins_fail_in_the_vats_measurement_not_in_the_baseline(self):
        """A frozen clock or NPC is a bug only while a player is in VATS: with nobody in VATS (the baseline) both must move."""
        for bug in ("vats_clock_frozen", "vats_npcs_frozen", "vats_world_paused"):
            r, _ = run("vats_world_moves", bugs=(bug,))
            failed = [s for s in r.steps if not s.ok]
            self.assertEqual(len(failed), 1)
            self.assertIn("sits in VATS", failed[0].name, bug)
            self.assertNotIn("nothing to compare", failed[0].reason, f"{bug}: the baseline itself failed")

    def test_a_measurement_that_cannot_be_made_fails_instead_of_passing(self):
        r, _ = run("vats_world_moves", edit=lambda w: w.chasers.clear())
        self.assertFalse(r.ok)
        self.assertIn("nothing to compare", self.reasons(r))
        r, _ = run("vats_world_moves", edit=lambda w: setattr(w, "time_scale", 0.0))
        self.assertFalse(r.ok)
        self.assertIn("did not move by itself", self.reasons(r))

    def test_the_players_are_released_even_when_a_check_fails(self):
        for bug in ("vats_world_paused", "vats_player_walks", "vats_limb_ignored"):
            _, w = run("vats_world_moves", bugs=(bug,))
            self.assertTrue(all(b.mode == 0 for b in w.bots), f"{bug}: a player was left in the VATS menu")
            self.assertTrue(all(m is None for m in w.mover), f"{bug}: a movement key was left held")


class RunVatsCli(unittest.TestCase):
    def cli(self, *args):
        d = tempfile.mkdtemp(prefix="vats-cli-")
        atexit.register(shutil.rmtree, d, True)
        return subprocess.run([sys.executable, "-I", RUN_VATS, "--fake", "--outdir", d, *args], capture_output=True, text=True, errors="replace", timeout=300)

    def test_fake_run_passes(self):
        p = self.cli("--playbook", "vats_world_moves")
        self.assertEqual(p.returncode, 0, p.stdout[-1500:] + p.stderr[-800:])
        self.assertIn("PASS  vats_world_moves", p.stdout)

    def test_fake_bug_fails_with_exit_1(self):
        for bug in ("vats_world_paused", "vats_player_walks", "vats_limb_ignored"):
            p = self.cli("--playbook", "vats_world_moves", "--bug", bug)
            self.assertEqual(p.returncode, 1, f"{bug}: " + p.stdout[-1500:] + p.stderr[-800:])
            self.assertIn("FAIL  vats_world_moves", p.stdout)

    def test_list_names_it(self):
        p = subprocess.run([sys.executable, "-I", RUN_VATS, "--list"], capture_output=True, text=True, errors="replace")
        self.assertIn("vats_world_moves", p.stdout)


class Helpers(unittest.TestCase):
    def setUp(self):
        self.w, self.refs = V.fake_vats_class("gun")(2, set(), lag=1)
        self.bot = self.w.bots[0]
        self.bot.goto_ref(self.refs["target"], 500)
        self.bot.give_item(self.refs["weapon"])
        self.bot.equip(self.refs["weapon"])

    def test_state_outside_vats_is_empty(self):
        st = V.state(self.bot)
        self.assertEqual(st["mode"], 0)
        self.assertEqual(st["limbs"], [])

    def test_enter_select_every_limb_and_leave(self):
        t = self.refs["target"]
        self.assertTrue(V.enter(self.bot, t))
        self.assertTrue(V.selecting(V.state(self.bot)))
        for limb in V.LIMBS:
            self.assertTrue(V.select(self.bot, t, limb), limb)
            self.assertEqual(V.state(self.bot)["limb"], limb)
        self.assertTrue(V.leave(self.bot))
        self.assertEqual(V.state(self.bot)["mode"], 0)

    def test_select_is_refused_outside_vats_and_for_a_non_target(self):
        r = V.select(self.bot, self.refs["target"], "head")
        self.assertFalse(r)
        self.assertIn("not in target", r.reason)
        V.enter(self.bot, self.refs["target"])
        r = V.select(self.bot, "FalloutNV.esm:ABCDEF", "head")
        self.assertFalse(r)
        self.assertIn("not in the engine", r.reason)

    def test_attack_hurts_the_target_at_the_chosen_limb_and_closes_vats(self):
        t = self.refs["target"]
        hp0 = self.bot.health(t)
        self.assertTrue(V.attack(self.bot, t, "head"))
        self.assertLess(self.w.t(t)["hp"], hp0)
        self.assertEqual(self.w.t(t)["limb_damage"].get("head", 0) > 0, True)
        self.assertEqual(V.state(self.bot)["mode"], 0)

    def test_without_the_bridge_method_the_error_names_it(self):
        class NoVats:
            class nv:   # noqa: N801
                @staticmethod
                def call(method, params=None, timeout_s=0):
                    err = RuntimeError("unknown method")
                    err.code = -32601
                    raise err
        with self.assertRaises(V.MissingBridge) as cm:
            V.state(NoVats)
        self.assertIn("vats.state", str(cm.exception))

    def test_set_config_is_host_only(self):
        self.assertTrue(V.set_config(self.w.bots[0], killcam=False))
        r = V.set_config(self.w.bots[1], killcam=True)
        self.assertFalse(r)
        self.assertIn("host", r.reason)


if __name__ == "__main__":
    res = unittest.main(exit=False, verbosity=1)
    ok = res.result.wasSuccessful()
    print("RESULT: ALL OK" if ok else "RESULT: FAILED")
    sys.exit(0 if ok else 1)
