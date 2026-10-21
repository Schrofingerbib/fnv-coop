"""enemy-anim: NPC fight assertions (tools/animcheck/npc.py) on synthetic probe streams. run_npc_tests.py requires every mutation twin to FAIL this
suite: the NPC ones (ANIMCHECK_MUTANT=npc_*) and the shared detector / counter ones, because FIGHT_ASSERTS must fail on each injected defect."""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from animcheck import synth  # noqa: E402
from animcheck.config import DEFAULT  # noqa: E402
from animcheck.model import stream_from_probes, write_probes  # noqa: E402
from animcheck.npc import FIGHT_ASSERTS, NPC_THRESHOLDS, analyze_npc, check_fight, main as npc_main  # noqa: E402
from animcheck.synth import G, NPC_REF, State, make_probes  # noqa: E402

DUR = 9000.0
DELAY = 120.0   # what the copy lags the owner by: the interpolation buffer plus a little link latency


def stream(machine, script, dur=DUR):
    return stream_from_probes(machine, make_probes(machine, {NPC_REF: script}, dur), use_rx=True)


def fight(attacks=(), reactions=(), reloads=(), weapon=(), death=None, base=None):
    """An NPC standing still (or `base`) that plays the given one-shots: lists of (t0, t1, group name). weapon: [(t0, t1)] weapon out.
    death: (t, (dx, dy)) it dies at t and its body slides by (dx, dy) over 1.5 s, then rests."""
    base = base or synth.idler()

    def f(t: float) -> State:
        s = base(t)
        if death is not None and t >= death[0]:
            k = min(1.0, (t - death[0]) / 1500.0)
            return State(pos=(s.pos[0] + death[1][0] * k, s.pos[1] + death[1][1] * k, s.pos[2]), rotz=s.rotz, groups=[(0, G["Death"])], dead=True,
                         seq_state=1 if t < death[0] + 2000 else 0)
        for t0, t1, name in list(attacks) + list(reactions) + list(reloads):
            if t0 <= t < t1:
                s.groups = list(s.groups) + [(4, G[name])]
        for t0, t1 in weapon:
            if t0 <= t < t1:
                s.weapon_out = True
        return s
    return f


def run(owner_sc, copy_sc, dur=DUR, delay=DELAY, **kw):
    streams = {"owner": stream("owner", owner_sc, dur), "client": stream("client", synth.delayed(copy_sc, delay), dur)}
    return analyze_npc(streams, owner="owner", **kw)


def failed(rep, name):
    return any(f.startswith(name + ":") for f in rep["failures"])


ATT = [(2000.0, 2500.0, "AttackRight")]
HIT = [(4000.0, 4500.0, "Stagger")]
REL = [(6000.0, 7500.0, "ReloadA")]


class FightTests(unittest.TestCase):
    def test_a_clean_fight_passes_every_assertion(self):
        sc = fight(ATT, HIT, REL, weapon=[(1000.0, 8000.0)])
        rep = run(sc, sc)
        self.assertEqual(rep["failures"], [], rep["failures"])
        self.assertTrue(rep["ok"])
        c = rep["counters"]
        self.assertEqual((c["attack_events"], c["reaction_events"], c["reload_events"]), (1, 1, 1))
        self.assertEqual((c["attack_missing"], c["attack_extra"], c["reaction_missing"], c["reload_missing"], c["weapon_never_drawn"]), (0, 0, 0, 0, 0))

    def test_every_assertion_key_is_a_real_counter(self):
        sc = fight(ATT)
        rep = run(sc, sc)
        missing = [f for f in check_fight(rep) if "no such counter" in f]
        self.assertEqual(missing, [])
        self.assertTrue(set(FIGHT_ASSERTS) <= set(rep["counters"]))

    # ---- one-shots -------------------------------------------------------------------------------------------------------------------
    def test_an_attack_the_copy_never_played_is_missing(self):
        rep = run(fight(ATT), fight())
        self.assertEqual(rep["counters"]["attack_missing"], 1)
        self.assertTrue(failed(rep, "attack_missing"))

    def test_an_attack_played_twice_on_the_copy_is_extra(self):
        rep = run(fight(ATT), fight(ATT + [(3000.0, 3500.0, "AttackRight")]))
        self.assertEqual(rep["counters"]["attack_extra"], 1)
        self.assertEqual(rep["counters"]["attack_missing"], 0)
        self.assertTrue(failed(rep, "attack_extra"))

    def test_the_wrong_attack_group_is_missing_and_extra(self):
        rep = run(fight(ATT), fight([(2000.0, 2500.0, "AttackLeft")]))
        self.assertEqual((rep["counters"]["attack_missing"], rep["counters"]["attack_extra"]), (1, 1))

    def test_an_attack_that_arrives_far_too_late_is_missing(self):
        rep = run(fight(ATT), fight(ATT), delay=1800.0)
        self.assertEqual(rep["counters"]["attack_missing"], 1)

    def test_a_hit_reaction_and_a_reload_are_judged_too(self):
        rep = run(fight(reactions=HIT, reloads=REL), fight())
        self.assertEqual((rep["counters"]["reaction_missing"], rep["counters"]["reload_missing"]), (1, 1))
        rep = run(fight(), fight(reactions=HIT, reloads=REL))
        self.assertEqual((rep["counters"]["reaction_extra"], rep["counters"]["reload_extra"]), (1, 1))

    def test_two_swings_in_a_row_are_two_events(self):
        two = [(2000.0, 2300.0, "AttackRight"), (2600.0, 2900.0, "AttackRight")]
        rep = run(fight(two), fight(two))
        self.assertEqual(rep["counters"]["attack_events"], 2)
        self.assertEqual(rep["failures"], [])
        rep = run(fight(two), fight([(2000.0, 2900.0, "AttackRight")]))   # the copy merged them into one long play
        self.assertEqual(rep["counters"]["attack_missing"], 0)           # (both owner starts fall inside it)

    # ---- weapon ------------------------------------------------------------------------------------------------------------------------
    def test_a_weapon_that_was_never_drawn_on_the_copy_is_caught(self):
        rep = run(fight(weapon=[(1000.0, 4500.0)]), fight())
        self.assertEqual(rep["counters"]["weapon_never_drawn"], 1)
        self.assertTrue(failed(rep, "weapon_never_drawn"))

    def test_a_short_draw_or_a_late_but_real_draw_is_fine(self):
        rep = run(fight(weapon=[(1000.0, 2000.0)]), fight())
        self.assertEqual(rep["counters"]["weapon_never_drawn"], 0)
        rep = run(fight(weapon=[(1000.0, 5000.0)]), fight(weapon=[(1900.0, 5000.0)]))
        self.assertEqual(rep["counters"]["weapon_never_drawn"], 0)

    # ---- corpses -----------------------------------------------------------------------------------------------------------------------
    def test_a_corpse_in_the_same_place_passes(self):
        rep = run(fight(death=(3000.0, (300.0, 40.0))), fight(death=(3000.0, (320.0, 60.0))))   # 28 units apart: inside the tolerance
        self.assertEqual(rep["counters"]["corpses"], 1)
        self.assertEqual((rep["counters"]["corpse_wrong_place"], rep["counters"]["corpse_missing"], rep["counters"]["corpse_unsettled"]), (0, 0, 0))
        self.assertLess(rep["counters"]["corpse_err_max"], 40)
        self.assertEqual(rep["failures"], [], rep["failures"])

    def test_a_corpse_lying_somewhere_else_is_caught(self):
        rep = run(fight(death=(3000.0, (300.0, 40.0))), fight(death=(3000.0, (100.0, 200.0))))
        self.assertEqual(rep["counters"]["corpse_wrong_place"], 1)
        self.assertGreater(rep["counters"]["corpse_err_max"], 200)
        self.assertTrue(failed(rep, "corpse_wrong_place") and failed(rep, "corpse_err_max"))

    def test_a_copy_that_never_died_is_a_missing_corpse(self):
        rep = run(fight(death=(3000.0, (300.0, 40.0))), fight())
        self.assertEqual(rep["counters"]["corpse_missing"], 1)
        self.assertTrue(failed(rep, "corpse_missing"))

    def test_a_body_that_keeps_sliding_after_it_should_rest_is_unsettled(self):
        def sliding_corpse(t):
            s = fight(death=(3000.0, (300.0, 40.0)))(t)
            if t > 3000.0 + 5000.0:
                s.pos = (s.pos[0] + (t - 8000.0) * 0.2, s.pos[1], s.pos[2])   # 200 u/s for the last second
            return s
        rep = run(fight(death=(3000.0, (300.0, 40.0))), sliding_corpse, dur=9500.0)
        self.assertEqual(rep["counters"]["corpse_unsettled"], 1)
        self.assertTrue(failed(rep, "corpse_unsettled"))

    # ---- the eight glitch detectors are part of the fight assertions ---------------------------------------------------------------------
    def glitchy(self, injected, expect=None):
        return run(synth.idler(), injected, expect_moving=expect)

    def test_sliding_feet_fail_the_fight(self):
        rep = self.glitchy(synth.inject_sliding(synth.idler(), 2000.0, 3500.0))
        self.assertTrue(failed(rep, "glitch.sliding"), rep["failures"])

    def test_a_t_pose_fails_the_fight(self):
        rep = self.glitchy(synth.inject_tpose(synth.idler(), 2000.0, 3000.0))
        self.assertTrue(failed(rep, "glitch.tpose"), rep["failures"])

    def test_floating_fails_the_fight(self):
        rep = self.glitchy(synth.inject_float(synth.idler(), 2000.0, 3500.0))
        self.assertTrue(failed(rep, "glitch.floating"), rep["failures"])

    def test_sinking_fails_the_fight(self):
        rep = self.glitchy(synth.inject_float(synth.idler(), 2000.0, 3500.0, dz=-90.0))
        self.assertTrue(failed(rep, "glitch.sinking"), rep["failures"])

    def test_jitter_fails_the_fight(self):
        rep = self.glitchy(synth.inject_jitter(synth.idler(), 2000.0, 3500.0))
        self.assertTrue(failed(rep, "glitch.jitter"), rep["failures"])

    def test_rubber_banding_fails_the_fight(self):
        rep = self.glitchy(synth.inject_rubber(synth.idler(), 2000.0))
        self.assertTrue(failed(rep, "glitch.rubberband"), rep["failures"])

    def test_a_stuck_walker_fails_the_fight(self):
        rep = self.glitchy(synth.idler(), expect={NPC_REF: [(1000.0, 6000.0)]})
        self.assertTrue(failed(rep, "glitch.stuck"), rep["failures"])

    def test_a_dead_actor_with_a_live_animation_fails_the_fight(self):
        rep = self.glitchy(synth.inject_death_alive(synth.idler(), 2000.0))
        self.assertTrue(failed(rep, "glitch.deathpose"), rep["failures"])

    def test_a_proper_death_does_not_fail_the_fight(self):
        sc = fight(death=(3000.0, (200.0, 0.0)))
        rep = run(sc, sc)
        self.assertEqual(rep["failures"], [], rep["failures"])

    # ---- the command line --------------------------------------------------------------------------------------------------------------
    def test_the_command_line_exits_non_zero_on_a_failed_fight(self):
        sc = fight(ATT)
        with tempfile.TemporaryDirectory() as d:
            a, b, c = Path(d) / "owner.jsonl", Path(d) / "good.jsonl", Path(d) / "bad.jsonl"
            write_probes(a, make_probes("owner", {NPC_REF: sc}, DUR))
            write_probes(b, make_probes("client", {NPC_REF: synth.delayed(sc, DELAY)}, DUR))
            write_probes(c, make_probes("client", {NPC_REF: synth.delayed(fight(), DELAY)}, DUR))
            out = Path(d) / "r.json"
            self.assertEqual(npc_main([str(a), str(b), "--json", str(out)]), 0)
            self.assertTrue(json.loads(out.read_text(encoding="utf-8"))["ok"])
            self.assertEqual(npc_main([str(a), str(c)]), 1)

    def test_thresholds_are_the_documented_ones(self):
        self.assertEqual(NPC_THRESHOLDS.pose_tol_units, 14.0)
        self.assertEqual(NPC_THRESHOLDS.slide_min_speed, DEFAULT.slide_min_speed)
        self.assertEqual(FIGHT_ASSERTS["glitch.sliding"], "==0")
        self.assertEqual(len([k for k in FIGHT_ASSERTS if k.startswith("glitch.")]), 8)


if __name__ == "__main__":
    unittest.main()
