#!/usr/bin/env python3
"""Game-free tests for the four VATS playbooks (tools/bots/playbooks/pb_vats.py), with mutation twins.

Run: python -I tools/bots/playbooks/tests/run_vats_tests.py   (prints ALL OK)

What is proven:
  * every playbook passes on a correct fake co-op world with VATS (VatsWorld) with 2 and 3 players, and runs at least 8 steps;
  * every twin (playbook + one deliberately wrong fake behaviour) FAILS, and the failing step names the problem;
  * a playbook without its scenario refs is a clear failure, not a crash;
  * the helper layer (limb choice, target choice, fire, cancel) behaves on the fake bridge the way plugin/vats documents it.
"""
from __future__ import annotations

import atexit
import os
import shutil
import sys
import tempfile
import unittest

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))
sys.path.insert(0, os.path.join(REPO, "tools", "bots"))
sys.path.insert(0, os.path.join(REPO, "tools"))

from playbooks import core, fakecoop, pb_vats as V  # noqa: E402

OPTS = {"fast": True, "anim": False, "ui_s": 0.2, "barrier_s": 0.2, "stage_s": 0.2, "handoff_s": 0.2, "spawn_s": 0.2, "settle_s": 0.3, "settle_poll_s": 0.0,
        "config_s": 5.0, "repo_root": REPO}
CLASS_OF = {"vats_gun": "gun", "vats_melee": "melee", "vats_explosive": "explosive", "vats_energy": "energy"}


def run(name, bugs=(), n=2, opts=None, refs_drop=()):
    w, refs = V.fake_vats_class(CLASS_OF[name])(n, set(bugs), lag=1)
    for k in refs_drop:
        refs.pop(k, None)
    d = tempfile.mkdtemp(prefix="vats-pb-")
    atexit.register(shutil.rmtree, d, True)
    w.flight_dir = os.path.join(d, "flight")
    o = dict(OPTS)
    o.update(opts or {})
    return core.run_playbook(V.make_vats(name), fakecoop.make_machines(w), fakecoop.FakeComparer(), d, refs, o), w


class Clean(unittest.TestCase):
    def test_every_vats_playbook_passes_with_two_and_three_players(self):
        for name in V.VATS_REGISTRY:
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
        self.assertEqual(set(CLASS_OF.values()), set(V.CLASSES))
        self.assertEqual(set(V.WEAPON_IDS), set(V.CLASSES))
        for name, cls in CLASS_OF.items():
            self.assertEqual(V.make_vats(name).weapon_class, cls)

    def test_a_missing_scenario_ref_is_a_clear_failure(self):
        for drop in ("target", "weapon"):
            r, _ = run("vats_gun", refs_drop=(drop,))
            self.assertFalse(r.ok)
            self.assertIn(drop, r.reason)


class Twins(unittest.TestCase):
    def test_every_twin_is_caught(self):
        self.assertGreaterEqual(len(V.VATS_TWINS), 16)
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
