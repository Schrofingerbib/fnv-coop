"""The benchmark scenario runner and the arena layout, against a fake game (no game, no bridge).
Run: python -I -m unittest discover -s tools/bench/tests -p test_scenario.py
Twin: a runner that lets one failing phase end the whole run (the failure the real runner guards against) must fail test_a_failing_phase_does_not_end_the_run.
"""
import math
import sys
import threading
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
import arena as arena_mod  # noqa: E402
import scenario  # noqa: E402


class R:
    def __init__(self, ok=True, reason=""):
        self.ok, self.reason = ok, reason


class FakeBot:
    def __init__(self, ok=True):
        self.at, self.ok, self.calls, self.modes = [0.0, 0.0, 0.0], ok, 0, []

    def pos(self):
        return list(self.at)

    def goto_pos(self, x, y, z, timeout_s=5.0, mode="walk", **kw):
        self.calls += 1
        self.modes.append(mode)
        self.at = [x, y, z]
        threading.Event().wait(0.002)
        return R(self.ok, "" if self.ok else "stuck: fake")


class FakeSamp:
    def __init__(self):
        self.phase = ["warmup"]
        self.seen = []

    def __setattr__(self, k, v):
        object.__setattr__(self, k, v)


class FakeRelay:
    def __init__(self):
        self.profiles = []

    def profile(self, name):
        self.profiles.append(name)


class FakePhases:
    NET_PROFILES = ["lan", "bad_wifi"]

    def __init__(self):
        self.called = []

    def input_probe(self, bots, log):
        self.called.append("probe")
        return {"copy0": {"ok": True}}

    def area_change(self, samp, bots, k, log):
        self.called.append("area")
        return {"ok": True, "recovery_s": 2.0}

    def run_playbook(self, name, out, log, timeout_s=0):
        self.called.append(f"playbook:{name}")
        return {"name": name, "ok": True, "steps": [], "pass_pct": 100.0}


def make_ctx(**kw):
    samp = FakeSamp()
    # record every phase name the sampler is told about
    phase = []

    class L(list):
        def __setitem__(self, i, v):
            phase.append(v)
            list.__setitem__(self, i, v)
    samp.phase = L(["warmup"])
    bots = [FakeBot(), FakeBot()]
    ctx = scenario.Ctx(samp, bots, arena_mod.Arena(size=100), k=0.01, log=lambda s: None, phases_mod=FakePhases(), **kw)
    return ctx, phase, bots


class Arena(unittest.TestCase):
    def test_square_is_four_corners_around_the_start(self):
        pts = arena_mod.Arena(size=450).course("square", around=(10, 20, 5))
        self.assertEqual(pts, [(460, 20, 5), (460, 470, 5), (10, 470, 5), (10, 20, 5)])

    def test_arena_origin_wins_over_the_current_position(self):
        pts = arena_mod.Arena(origin=(1000, 2000, 3), size=100).course("line", around=(0, 0, 0))
        self.assertEqual(pts[0], (1200, 2000, 3))

    def test_ring_points_are_equidistant(self):
        a = arena_mod.Arena(origin=(0, 0, 0), ring_radius=700)
        pts = a.ring(6)
        self.assertEqual(len(pts), 6)
        for p in pts:
            self.assertAlmostEqual(math.hypot(p[0], p[1]), 700.0, places=6)

    def test_unknown_course_and_missing_origin_are_errors(self):
        with self.assertRaises(KeyError):
            arena_mod.Arena().course("spiral", around=(0, 0, 0))
        with self.assertRaises(ValueError):
            arena_mod.Arena().course("square")


class Runner(unittest.TestCase):
    def test_the_shipped_scenarios_validate(self):
        for n in ("standard", "smoke"):
            d = scenario.load(n)
            self.assertTrue(d["steps"])

    def test_unknown_phase_and_empty_scenario_are_rejected(self):
        with self.assertRaises(ValueError):
            scenario.validate({"steps": [{"phase": "teleport_to_mars"}]})
        with self.assertRaises(ValueError):
            scenario.validate({"steps": []})

    def test_steps_run_in_order_and_the_sampler_gets_each_label(self):
        ctx, phase, _ = make_ctx()
        scenario.run(ctx, {"steps": [{"phase": "idle", "secs": 1}, {"phase": "walk", "who": "host", "secs": 2, "label": "host_walks"},
                                     {"phase": "wait", "secs": 1, "label": "settle"}]})
        self.assertEqual(phase, ["idle", "host_walks", "settle"])
        self.assertEqual([t["phase"] for t in ctx.extras["timeline"]], ["idle", "host_walks", "settle"])

    def test_walk_who_selects_the_bots(self):
        ctx, _, bots = make_ctx()
        scenario.run(ctx, {"steps": [{"phase": "walk", "who": "client", "secs": 2}]})
        self.assertEqual(bots[0].calls, 0)
        self.assertGreater(bots[1].calls, 0)
        ctx2, _, bots2 = make_ctx()
        scenario.run(ctx2, {"steps": [{"phase": "walk", "who": "both", "secs": 2}]})
        self.assertGreater(bots2[0].calls, 0)
        self.assertGreater(bots2[1].calls, 0)

    def test_walk_failures_are_counted_with_their_reason(self):
        ctx, _, bots = make_ctx()
        bots[0].ok = False
        scenario.run(ctx, {"steps": [{"phase": "walk", "who": "host", "secs": 2}]})
        self.assertGreater(ctx.walk["legs"], 0)
        self.assertEqual(ctx.walk["legs_ok"], 0)
        self.assertIn("stuck: fake", ctx.walk["why"])

    def test_a_failing_phase_does_not_end_the_run(self):
        ctx, phase, _ = make_ctx()

        @scenario.register("boom_test_only")
        def boom(c, s):
            raise RuntimeError("boom")
        try:
            scenario.run(ctx, {"steps": [{"phase": "idle", "secs": 1}, {"phase": "boom_test_only"}, {"phase": "wait", "secs": 1, "label": "after"}]})
        finally:
            scenario.PHASES.pop("boom_test_only", None)
        self.assertEqual(phase[-1], "after", "the step after the failure still ran")
        self.assertEqual(ctx.extras["phase_errors"][0]["phase"], "boom_test_only")

    def test_twin_stop_on_error_makes_one_failure_end_the_run(self):
        """Twin: with the old behaviour switched on, the failing phase must escape run() (so the test above would fail)."""
        @scenario.register("boom_twin_only")
        def boom(c, s):
            raise RuntimeError("boom")
        old = scenario.MUTANT
        scenario.MUTANT = "stop_on_error"
        try:
            ctx, _, _ = make_ctx()
            with self.assertRaises(RuntimeError):
                scenario.run(ctx, {"steps": [{"phase": "boom_twin_only"}, {"phase": "idle", "secs": 1}]})
        finally:
            scenario.MUTANT = old
            scenario.PHASES.pop("boom_twin_only", None)

    def test_net_is_skipped_without_a_relay_and_stepped_with_one(self):
        ctx, phase, _ = make_ctx()
        scenario.run(ctx, {"steps": [{"phase": "net", "secs": 1}]})
        self.assertIn("net", ctx.extras["skipped"])
        relay = FakeRelay()
        ctx2, phase2, _ = make_ctx(relay=relay)
        scenario.run(ctx2, {"steps": [{"phase": "net", "secs": 1, "recover_secs": 1}]})
        self.assertEqual(relay.profiles, ["lan", "bad_wifi", "lan"])
        self.assertIn("net_bad_wifi", phase2)
        self.assertEqual(phase2[-1], "net_recover")

    def test_game_phases_call_the_helpers(self):
        ctx, _, _ = make_ctx()
        ctx.outdir = "x"
        scenario.run(ctx, {"steps": [{"phase": "probe"}, {"phase": "area"}, {"phase": "playbook", "name": "loot_race"}]})
        self.assertEqual(ctx.phases.called, ["probe", "area", "playbook:loot_race"])
        self.assertIn("playbook_loot", ctx.extras)
        self.assertEqual(ctx.extras["area"]["recovery_s"], 2.0)


if __name__ == "__main__":
    unittest.main()
