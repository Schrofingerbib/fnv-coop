"""Game-free tests for the enemy / NPC sync playbooks (pb_enemy.py), the load-robust playbook clock (clocks.py), the FIGHT animation
thresholds and tools/bots/run_fight.py, each with mutation twins.

Run: python -I tools/bots/playbooks/tests/run_enemy_tests.py   (prints ALL OK)

What is proven (against T6b's fake co-op world with the NPC layer, fakenpc.py; NOTHING here ran in a game):
  * the four playbooks (enemy_fight, enemy_handoff, enemy_spawn, corpse_loot_race) pass on a correct world with 2 to 4 players, on a
    laggy link and on a lossy link, and the runs are not vacuous (the NPCs really walked, attacked, staggered, were handed over);
  * every deliberately wrong behaviour of the fake (a twin) fails the playbook it is registered with, with a reason that names the problem;
  * the Track A inbox flake is fixed at its root: fake runs measure waits in the world's own virtual time, so CPU load cannot fail them,
    while a real desync (never converges, ownership never moves, a lost message) still fails. The old wall-clock behaviour is kept as
    clock="wall" and fails under a simulated starved process, which is the twin that proves the fix matters;
  * live runs scale only their waiting budgets with the measured machine load (LoadMeter), never a check;
  * the FIGHT animation thresholds see a 600 ms missing stagger that the default thresholds cannot, and the NPC pose comparison follows
    the owner across a handoff (without the owner windows the same clean run fails);
  * the scenario goodsprings_enemies is valid and its refs reach the playbooks; tools/bots/run_fight.py's exit codes and preflight.
  * fix rounds (each with a twin): --from-step / --only-step run one step of enemy_fight alone on the world the earlier ones left (arrange
    puts the players next to the enemies, finds the weapon, checks the targets are alive and the owners agreed, and rebuilds the start
    numbers from the world); a full run never calls an arrange part; a dead target is reported, not papered over; Ctx.settle() fails early
    with the stuck sequence numbers and acks when nothing moves, but not for slow or ack-only progress; --timescale sends "sgtm X" to
    every copy after the setup, shrinks fixed sleeps by 1/X (not timeouts), resets to 1 after a pass, a failure and an interrupt.
"""
from __future__ import annotations

import atexit
import contextlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))
sys.path.insert(0, os.path.join(REPO, "tools", "bots"))
sys.path.insert(0, os.path.join(REPO, "tools"))

from playbooks import (animwatch, chaos_adapter, clocks, core, fakecoop, fakenpc, pb_enemy, probe,  # noqa: E402
                       registry, scenario_adapter)

# the budgets the old tests used: 0.2 to 0.3 REAL seconds (now virtual seconds, see clocks.py)
OPTS = {"fast": True, "anim": True, "ui_s": 0.2, "barrier_s": 0.2, "stage_s": 0.2, "handoff_s": 0.2, "spawn_s": 0.2, "settle_s": 0.3,
        "settle_poll_s": 0.0, "repo_root": REPO}
ENEMY = registry.ENEMY_TESTS


def tmp(prefix="enemy-"):
    """A scratch directory that is removed when the test process exits (a few hundred run directories once filled the disk)."""
    d = tempfile.mkdtemp(prefix=prefix)
    atexit.register(shutil.rmtree, d, True)
    return d


def run(name, bugs=(), n=None, opts=None, outdir=None, **world_kw):
    e = registry.REGISTRY[name]
    w, refs = e.fake(n or e.players, set(bugs), **world_kw)
    d = outdir or tmp()
    w.flight_dir = os.path.join(d, "flight")
    o = dict(OPTS)
    o.update(opts or {})
    return core.run_playbook(registry.make(name, REPO), fakecoop.make_machines(w), fakecoop.FakeComparer(), d, refs, o), w


# (playbook, bug) -> regex the failure reason must match: the reason has to NAME the problem, not just fail
REASONS = {
    ("enemy_fight", "double_damage"): "applied twice",
    ("enemy_fight", "kill_credit_dup"): "credited",
    ("enemy_fight", "npc_hit_twice"): "exactly once on the victim",
    ("enemy_fight", "npc_pos_stale"): "units from where its owner",
    ("enemy_fight", "npc_no_attack_anim"): "animation check.*group_mismatch_episodes",
    ("enemy_fight", "npc_no_stagger_anim"): "animation check.*group_mismatch_episodes",
    ("enemy_fight", "npc_death_pose_missing"): "animation check.*deathpose",
    ("enemy_fight", "npc_no_draw"): "animation check.*wrong_weapon",
    ("enemy_fight", "npc_slides"): "animation check.*glitches",
    ("enemy_fight", "ai_both"): "AI runs on",
    ("enemy_fight", "dup_kill"): "died",
    ("enemy_fight", "stale_echo"): "still alive",
    ("enemy_fight", "no_fight_sync"): "still alive",
    ("enemy_handoff", "ghost_owner"): "AI runs on",
    ("enemy_handoff", "handoff_snap"): "animation check.*glitches",
    ("enemy_handoff", "handoff_dup"): "differs between machines|duplicate",
    ("enemy_handoff", "hp_revive_on_handoff"): "gained health",
    ("enemy_handoff", "ai_both"): "AI runs on",
    ("enemy_handoff", "flap_owner"): "flapped",
    ("enemy_handoff", "owner_split"): "timed out",
    ("enemy_handoff", "double_damage"): "double damage",
    ("enemy_handoff", "kill_credit_dup"): "credited",
    ("enemy_spawn", "double_spawn"): "counts after",
    ("enemy_spawn", "spawn_pick_differs"): "timed out|disagree",
    ("enemy_spawn", "spawn_ids_differ"): "snapdiff|disagree",
    ("enemy_spawn", "respawn_on_return"): "spawned again",
    ("corpse_loot_race", "corpse_loot_dup"): "duplicated",
    ("corpse_loot_race", "corpse_loot_lost"): r"\blost\b",
    ("corpse_loot_race", "corpse_unsynced"): "differs between machines",
    ("corpse_loot_race", "kill_credit_dup"): "credited",
    ("corpse_loot_race", "no_fight_sync"): "still alive",
}


class Registered(unittest.TestCase):
    def test_four_tests_registered_with_the_scenario(self):
        self.assertEqual(ENEMY, ("enemy_fight", "enemy_handoff", "enemy_spawn", "corpse_loot_race"))
        for n in ENEMY:
            self.assertIn(n, registry.REGISTRY)
            self.assertEqual(scenario_adapter.MAPPERS[n][0], "goodsprings_enemies")
            self.assertEqual(registry.make(n, REPO).scenario, "goodsprings_enemies")
            self.assertTrue(registry.make(n, REPO).anim, n)

    def test_every_enemy_twin_has_a_named_reason_and_every_reason_a_twin(self):
        twins = {t for t in registry.TWINS if t[0] in ENEMY}
        self.assertEqual(twins, set(REASONS))

    def test_every_npc_bug_of_the_fake_is_used_by_an_enemy_twin(self):
        new = {"double_damage", "kill_credit_dup", "npc_hit_twice", "npc_pos_stale", "npc_no_attack_anim", "npc_no_stagger_anim",
               "npc_death_pose_missing", "npc_no_draw", "npc_slides", "ai_both", "ghost_owner", "handoff_snap", "handoff_dup",
               "hp_revive_on_handoff", "spawn_pick_differs", "spawn_ids_differ", "respawn_on_return", "corpse_loot_dup",
               "corpse_loot_lost", "corpse_unsynced"}
        self.assertTrue(new <= set(fakecoop.BUGS))
        self.assertTrue(new <= {b for _, b in registry.TWINS})


class CleanRuns(unittest.TestCase):
    def test_every_enemy_test_passes(self):
        for name in ENEMY:
            with self.subTest(name):
                r, _ = run(name)
                self.assertTrue(r.ok, f"{name}: {r.reason}")
                self.assertGreaterEqual(len(r.steps), 5)
                self.assertEqual(r.steps[-1].name, "animation and pose check")
                rep = json.load(open(os.path.join(r.outdir, "anim_report.json"), encoding="utf-8"))
                self.assertGreater(rep["counters"]["compared"], 0, "no NPC was compared between machines")
                self.assertGreater(rep["counters"]["avatar.compared"], 0)

    def test_three_and_four_players(self):
        for name in ENEMY:
            pb = registry.make(name, REPO)
            for n in (3, 4):
                if not (pb.min_players <= n <= pb.max_players):
                    continue
                with self.subTest(f"{name} x{n}"):
                    r, _ = run(name, n=n, opts={"anim": False})
                    self.assertTrue(r.ok, f"{name} with {n} players: {r.reason}")

    def test_laggy_replication(self):
        for name in ENEMY:
            with self.subTest(name):
                r, _ = run(name, lag=4, opts={"anim": False})
                self.assertTrue(r.ok, f"{name}: {r.reason}")

    def test_lossy_link_with_a_quiet_window(self):
        chaos = dict(chaos_adapter.relaxed_options("bad_wifi"), settle_s=0.3, chaos="bad_wifi", fast=True, settle_poll_s=0.0, anim=False)
        for name in ENEMY:
            with self.subTest(name):
                r, w = run(name, opts=chaos, loss_every=1)
                self.assertTrue(r.ok, f"{name}: {r.reason}")
                self.assertGreater(w.n_deliv, 0, "the link never dropped anything: the test proves nothing")

    def test_the_runs_are_not_vacuous(self):
        """The fake NPCs really fought: they walked, drew, attacked, staggered, hit the players, and the handoff really happened."""
        r, w = run("enemy_fight")
        self.assertTrue(r.ok, r.reason)
        st = w.npc_stats
        self.assertGreater(st.get("attacks", 0), 3)
        self.assertGreater(st.get("draws", 0), 0)
        self.assertGreater(st.get("staggers", 0), 3)
        self.assertGreater(sum(w.npc_hit_n), 3, "no NPC hit landed on a player")
        moved = [fakecoop.math.dist(L["pos"], L["home"]) for L in w.live.values()]
        self.assertGreater(max(moved), 300, "no NPC walked anywhere")
        r, w = run("enemy_handoff")
        self.assertTrue(r.ok, r.reason)
        self.assertGreaterEqual(len(w.owner_changes), 2)
        self.assertTrue(all(1 <= v <= 2 for v in w.owner_changes.values()), w.owner_changes)
        self.assertEqual({d["owner"] for d in r_windows(r)}, {"host", "client1"})
        r, w = run("enemy_spawn")
        self.assertTrue(r.ok, r.reason)
        self.assertEqual(len([k for k, v in w.truth["refs"].items() if v.get("encounter")]), 3)
        r, w = run("corpse_loot_race")
        self.assertTrue(r.ok, r.reason)
        self.assertEqual(sum(b.me["inv"].get("STIMPAK", 0) for b in w.bots), 3)
        self.assertEqual(w.t(pb_enemy.CORPSE_VICTIM)["items"], {})


def r_windows(result):
    return json.load(open(os.path.join(result.outdir, "anim_report.json"), encoding="utf-8"))["owner_windows"]


class Twins(unittest.TestCase):
    def test_every_twin_is_caught_with_a_named_reason(self):
        for (name, bug), pattern in sorted(REASONS.items()):
            with self.subTest(f"{name} + {bug}"):
                bad, _ = run(name, bugs={bug}, opts={"anim": "animation check" in pattern})     # the animation check only where it is the catcher
                self.assertFalse(bad.ok, f"twin {bug} was NOT caught by {name}")
                self.assertRegex(bad.reason, pattern)

    def test_failure_files_are_saved_for_the_analyzer(self):
        d = tmp("enemy-fail-")
        r, _ = run("enemy_fight", bugs={"double_damage"}, outdir=d)
        fails = [x for x in os.listdir(d) if x.startswith("FAIL-")]
        self.assertEqual(len(fails), 1, os.listdir(d))
        info = json.load(open(os.path.join(d, fails[0], "failure.json"), encoding="utf-8"))
        self.assertEqual(info["playbook"], "enemy_fight")
        self.assertEqual([m["name"] for m in info["machines"]], ["host", "client1"])

    def test_rising_counters_fail_every_enemy_test(self):
        for name in ENEMY:
            with self.subTest(name):
                bad, _ = run(name, bugs={"counter_noise"}, opts={"anim": False})
                self.assertFalse(bad.ok)
                self.assertIn("desync counters rose", bad.reason)

    def test_a_check_that_is_switched_off_lets_its_twin_through(self):
        """Mutation twin of the playbook itself: with the damage check gone the double-damage world passes the kill check."""
        orig = pb_enemy.check_kill
        pb_enemy.check_kill = lambda ctx, target, hp_before, ledger, killers=None: None
        try:
            r, _ = run("enemy_fight", bugs={"double_damage"})
        finally:
            pb_enemy.check_kill = orig
        self.assertTrue(r.ok or "applied twice" not in r.reason, "the double-damage twin must be invisible without check_kill")
        r, _ = run("enemy_fight", bugs={"double_damage"})
        self.assertFalse(r.ok)


# ---------------------------------------------------------------------------------------------------------------------------------
class SlowWall(clocks.Clock):
    """A starved process: every look at the clock finds `step` more real seconds gone (what a loaded PC does to a wall-clock deadline)."""
    kind = "wall"

    def __init__(self, step: float = 0.05):
        self.t, self.step = 0.0, step

    def now(self) -> float:
        self.t += self.step
        return self.t

    def sleep(self, seconds: float) -> None:
        self.t += seconds


class Burners:
    """Pure-Python busy threads: they share the GIL with the test, so the test gets a fraction of the CPU, as on a PC with builds running."""

    def __init__(self, n: int = 3):
        self.stop = threading.Event()
        self.threads = [threading.Thread(target=self._burn, daemon=True) for _ in range(n)]

    def _burn(self):
        x = 0
        while not self.stop.is_set():
            for i in range(20000):
                x += i * i

    def __enter__(self):
        for t in self.threads:
            t.start()
        return self

    def __exit__(self, *a):
        self.stop.set()
        for t in self.threads:
            t.join(timeout=5)


FLAKY_BEFORE = ("fight", "loot_race", "companion_recruit", "fast_travel_tether", "ownership_handoff", "vats_hit")     # Track A inbox


class LoadRobustness(unittest.TestCase):
    """The inbox item: 'playbook convergence window (0.3 s) flakes under load'. Fixed: fake runs wait in the world's virtual time."""

    def test_the_six_flaky_playbooks_pass_under_cpu_load(self):
        with Burners(3):
            for name in FLAKY_BEFORE + ENEMY[:1]:
                with self.subTest(name):
                    r, _ = run(name, opts={"anim": False})
                    self.assertTrue(r.ok, f"{name} under load: {r.reason}")

    def test_default_clock_of_a_fake_run_is_virtual_time(self):
        w, refs = registry.REGISTRY["loot_race"].fake(2)
        ctx = core.Ctx(fakecoop.make_machines(w), None, tmp(), refs, {"fast": True})
        self.assertEqual(ctx.clock.kind, "sim")
        t0 = ctx.clock.now()
        ctx.advance(1.0)
        self.assertGreaterEqual(ctx.clock.now() - t0, 1.0 - 1e-9)
        self.assertLess(ctx.clock.now() - t0, 1.5)
        self.assertEqual(core.Ctx(fakecoop.make_machines(w), None, tmp(), refs, {}).clock.kind, "wall")     # not a fast run
        self.assertEqual(core.Ctx(fakecoop.make_machines(w), None, tmp(), refs, {"fast": True, "clock": "wall"}).clock.kind, "wall")

    def test_twin_the_old_wall_clock_fails_when_the_process_is_starved(self):
        """The twin of the fix: with the old behaviour (a wall deadline, no scaling) a clean playbook fails on a starved process."""
        for name in ("loot_race", "ownership_handoff", "vats_hit"):
            with self.subTest(name):
                ok, _ = run(name, opts={"anim": False})
                self.assertTrue(ok.ok, ok.reason)
                bad, _ = run(name, opts={"anim": False, "clock": SlowWall(0.2), "load_scale": False})
                self.assertFalse(bad.ok, f"{name}: the wall-clock twin should fail on a starved process")
                self.assertRegex(bad.reason, "did not converge|timed out")

    def test_a_real_desync_still_fails_on_the_virtual_clock(self):
        # never converges: the clock budget is spent in full (not skipped), then the run fails with the machines named
        r, w = run("loot_race", lag=10 ** 6, opts={"anim": False, "settle_s": 0.05})
        self.assertFalse(r.ok)
        self.assertIn("did not converge", r.reason)
        self.assertGreaterEqual(w.vt, clocks.SIM_MIN_WAIT_S - 0.2, "the failure came before the (virtual) budget was spent")
        # ownership never moves
        r, _ = run("enemy_handoff", bugs={"owner_split"}, opts={"anim": False})
        self.assertFalse(r.ok)
        self.assertIn("timed out", r.reason)
        self.assertIn("simulated", r.reason)
        # a reliable message lost for good
        chaos = dict(chaos_adapter.relaxed_options("bad_wifi"), settle_s=0.3, chaos="bad_wifi", fast=True, settle_poll_s=0.0, anim=False)
        r, _ = run("loot_race", bugs={"no_retransmit"}, opts=chaos, loss_every=1)
        self.assertFalse(r.ok)
        # and a repair in the quiet window
        r, _ = run("loot_race", bugs={"repair_after_quiet"}, opts=chaos, loss_every=1)
        self.assertFalse(r.ok)
        self.assertIn("quiescence", r.reason)

    def test_a_frozen_simulated_clock_fails_instead_of_hanging(self):
        class Dead:
            vt = 0.0
            tick_hooks: list = []

        class Bot:
            world = Dead()
            name = "host"

            def coop_status(self):
                return {"lastSeq": 0, "host": {"allSynced": True}}
        ms = [core.Machine(0, "host", Bot()), core.Machine(1, "client1", Bot())]
        ctx = core.Ctx(ms, None, tmp(), {}, {"fast": True, "stall_polls": 50})
        t0 = time.monotonic()
        with self.assertRaisesRegex(core.StepFailure, "stopped advancing"):
            ctx.wait_until("never", lambda: False, timeout_s=1.0)
        self.assertLess(time.monotonic() - t0, 5.0)

    def test_budget_rules(self):
        w, refs = registry.REGISTRY["loot_race"].fake(2)
        sim = core.Ctx(fakecoop.make_machines(w), None, tmp(), refs, {"fast": True})
        self.assertEqual(sim.budget(0.2), clocks.SIM_MIN_WAIT_S)               # a real-time number is lifted to a usable virtual budget
        self.assertEqual(sim.budget(30.0), 30.0)                               # a long budget is kept
        wall = core.Ctx([], None, tmp(), {}, {"clock": clocks.ManualClock(), "load_meter": clocks.LoadMeter(lambda: 3 * clocks.LOAD_REF_US)})
        self.assertAlmostEqual(wall.budget(10.0), 30.0)
        quiet = core.Ctx([], None, tmp(), {}, {"clock": clocks.ManualClock(), "load_meter": clocks.LoadMeter(lambda: clocks.LOAD_REF_US / 2)})
        self.assertAlmostEqual(quiet.budget(10.0), 10.0)                       # a fast machine never shortens a budget
        off = core.Ctx([], None, tmp(), {}, {"clock": clocks.ManualClock(), "load_scale": False,
                                                          "load_meter": clocks.LoadMeter(lambda: 3 * clocks.LOAD_REF_US)})
        self.assertAlmostEqual(off.budget(10.0), 10.0)

    def test_load_meter(self):
        calls = []
        clk = clocks.ManualClock()

        def cal():
            calls.append(1)
            return 5.0 * clocks.LOAD_REF_US
        m = clocks.LoadMeter(cal, ttl_s=2.0, clock=clk)
        self.assertEqual(m.factor(), 5.0)
        self.assertEqual(m.factor(), 5.0)
        self.assertEqual(len(calls), 1, "calibration must be cached inside the ttl")
        clk.advance(2.5)
        m.factor()
        self.assertEqual(len(calls), 2)
        self.assertEqual(clocks.LoadMeter(lambda: 1000 * clocks.LOAD_REF_US, clock=clocks.ManualClock()).factor(), clocks.LOAD_CAP)
        self.assertEqual(clocks.LoadMeter(lambda: 0.1 * clocks.LOAD_REF_US, clock=clocks.ManualClock()).factor(), 1.0)
        self.assertGreater(clocks.calibrate_us(), 0)

    def test_a_live_wait_gets_the_scaled_budget_and_a_real_desync_still_fails(self):
        def make(factor):
            clk = clocks.ManualClock()
            return clk, core.Ctx([], None, tmp(), {}, {"clock": clk, "load_meter": clocks.LoadMeter(lambda: factor * clocks.LOAD_REF_US)})
        clk, ctx = make(3.0)
        ctx.wait_until("slow but converges", lambda: clk.t >= 2.5, timeout_s=1.0, poll_s=0.1)       # needs 2.5 s, budget 3 s
        clk, ctx = make(1.0)
        with self.assertRaisesRegex(core.StepFailure, "timed out after 1s"):
            ctx.wait_until("slow", lambda: clk.t >= 2.5, timeout_s=1.0, poll_s=0.1)                  # same wait on a quiet PC: too slow
        clk, ctx = make(3.0)
        with self.assertRaisesRegex(core.StepFailure, "timed out after 3s"):
            ctx.wait_until("never", lambda: False, timeout_s=1.0, poll_s=0.1)                        # a desync that never heals still fails

    def test_clock_option_errors(self):
        w, refs = registry.REGISTRY["loot_race"].fake(2)
        with self.assertRaises(ValueError):
            core.Ctx(fakecoop.make_machines(w), None, tmp(), refs, {"clock": "moon"})
        with self.assertRaises(ValueError):
            core.Ctx([], None, tmp(), {}, {"clock": "sim"})


# ---------------------------------------------------------------------------------------------------------------------------------
class AnimationWiring(unittest.TestCase):
    def _streams(self, remote_staggers: bool):
        """Owner (host) runs an NPC that staggers for 600 ms; the client shows the same NPC 100 ms later, with or without the stagger."""
        from animcheck import model, synth
        G = synth.G

        def owner(t):
            stag = 2000 <= t < 2600
            return synth.State(pos=(100.0, 100.0, 0.0), groups=[(0, G["Stagger" if stag else "Idle"])], weapon_out=True, weapon_form=0x300)
        host = synth.make_probes("host", {0x1000: owner}, 5000)
        client = synth.make_probes("client", {0x1000: (synth.delayed(owner, 100) if remote_staggers else
                                                       (lambda t: synth.State(pos=(100.0, 100.0, 0.0), groups=[(0, G["Idle"])],
                                                                              weapon_out=True, weapon_form=0x300)))}, 5000)
        return {"host": model.stream_from_probes("host", host, use_rx=True), "client": model.stream_from_probes("client", client, use_rx=True)}

    def test_fight_thresholds_see_a_missing_stagger_that_the_default_ones_cannot(self):
        from animcheck import analyze as A, config as CFG
        bad = self._streams(remote_staggers=False)
        self.assertEqual(A.analyze(bad, th=CFG.DEFAULT, owner="host")["counters"]["group_mismatch_episodes"], 0,
                         "documented limit: a 600 ms mismatch is a transition for the default 400 ms grace")
        self.assertGreaterEqual(A.analyze(bad, th=CFG.FIGHT, owner="host")["counters"]["group_mismatch_episodes"], 1)
        good = self._streams(remote_staggers=True)
        self.assertEqual(A.analyze(good, th=CFG.FIGHT, owner="host")["counters"]["group_mismatch_episodes"], 0, "a correct copy must stay clean")
        self.assertEqual(CFG.PROFILES["fight"], CFG.FIGHT)
        self.assertLess(CFG.FIGHT.state_grace_ms, CFG.DEFAULT.state_grace_ms)

    def test_enemy_playbooks_use_the_fight_profile_and_spec(self):
        for n in ENEMY:
            self.assertEqual(registry.make(n, REPO).anim_profile, "fight")
        self.assertEqual(registry.make("fight", REPO).anim_profile, "default", "the old playbooks keep the default thresholds")
        for k in ("dead_mismatch", "stance_mismatch", "pos_err_p95", "glitch.deathpose", "group_mismatch_episodes"):
            self.assertIn(k, animwatch.FIGHT_NPC_SPEC)

    def test_pose_comparison_follows_the_owner_across_a_handoff(self):
        r, _ = run("enemy_handoff")
        self.assertTrue(r.ok, r.reason)
        win = r_windows(r)
        self.assertEqual([w["owner"] for w in win], ["host", "client1"])
        self.assertLess(win[0]["t1_ms"] - win[1]["t0_ms"], 1e-6)
        # On a laggy link the direction of the comparison matters: the observer is compared with the source about one interpolation delay
        # EARLIER. A clean run with a remote copy 250 ms behind its owner passes with the windows.
        r, _ = run("enemy_handoff", npc_lag=0.25)
        self.assertTrue(r.ok, f"clean run on a laggy link: {r.reason}")
        # Twin, on the SAME recorded data (so thread timing cannot make it flaky): a copy 350 ms behind, analysed with the windows and
        # then as if the owner never changed (the host stays the source of truth, the comparison runs backwards): the second must fail.
        seen = []
        orig = animwatch.AnimWatch.report

        def both(self):
            with_windows = orig(self)
            log, self.owner_log = self.owner_log, []
            try:
                without = orig(self)
            finally:
                self.owner_log = log
            seen.append((with_windows, without))
            return with_windows
        animwatch.AnimWatch.report = both
        try:
            run("enemy_handoff", npc_lag=0.35)
        finally:
            animwatch.AnimWatch.report = orig
        self.assertTrue(seen)
        with_windows, without = seen[-1]
        self.assertEqual(len(with_windows["owner_windows"]), 2)
        self.assertNotIn("owner_windows", without)
        self.assertGreater(without["counters"]["group_mismatch_episodes"], 0, "without the owner windows the laggy handoff must look desynced")
        self.assertGreater(without["counters"]["group_mismatch_episodes"], 5 * with_windows["counters"]["group_mismatch_episodes"])

    def test_the_windows_are_what_the_report_compares(self):
        """The merged report equals the per-window analyses: owner of each window, margin around the change left out."""
        r, _ = run("enemy_handoff")
        rep = json.load(open(os.path.join(r.outdir, "anim_report.json"), encoding="utf-8"))
        self.assertEqual(len(rep["owner_windows"]), 2)
        self.assertGreater(rep["counters"]["compared"], 0)
        single, _ = run("enemy_fight")
        rep1 = json.load(open(os.path.join(single.outdir, "anim_report.json"), encoding="utf-8"))
        self.assertNotIn("owner_windows", rep1, "one owner: the plain whole-run analysis")


# ---------------------------------------------------------------------------------------------------------------------------------
class ScenarioAndAdapter(unittest.TestCase):
    def test_scenario_is_valid_and_its_refs_reach_the_playbooks(self):
        import gzip
        sys.path.insert(0, os.path.join(REPO, "tools", "scenarios"))
        import scenlib
        self.assertEqual(scenlib.validate_file(os.path.join(REPO, "scenarios", "goodsprings_enemies.json")), [])
        forms = json.load(gzip.open(os.path.join(REPO, "out", "questdata", "forms.json.gz"), "rt", encoding="utf-8"))
        sc = scenlib.load_scenario(os.path.join(REPO, "scenarios", "goodsprings_enemies.json"))
        self.assertEqual(sc["players"], 2)
        sigs = {d["sig"] for d in sc["forms"].values()}
        self.assertTrue({"ACHR", "ACRE", "NPC_", "ALCH"} <= sigs, "powder gangers, geckos, a spawn base and an item")
        for n in ENEMY:
            refs = scenario_adapter.refs_for(n, REPO)
            pb = registry.make(n, REPO)
            self.assertFalse([k for k in pb.needs_scenario_refs if k not in refs], n)
        fight = scenario_adapter.refs_for("enemy_fight", REPO)
        self.assertEqual(len(fight["hostiles"]), 4)
        for h in fight["hostiles"]:
            self.assertIn(h, forms)
            base = fight["hostile_base_of"][h]
            self.assertIn(forms[base]["sig"], ("NPC_", "CREA"), f"{h}: base {base}")
        self.assertEqual(scenario_adapter.refs_for("corpse_loot_race", REPO)["item_count"], 3)
        steps = [s["op"] for s in sc["setup"]]
        self.assertIn("additem", steps, "the first ganger must carry loot")
        self.assertTrue(any(s["op"] == "console" and "Health" in s["cmd"] for s in sc["setup"]), "the bots must survive the fight")

    def test_handoff_refs_name_a_far_and_a_near_place(self):
        refs = scenario_adapter.refs_for("enemy_handoff", REPO)
        self.assertEqual(refs["far_pos"]["ref"], "FalloutNV.esm:09289B")
        self.assertIn("ref", refs["near_pos"])


# ---------------------------------------------------------------------------------------------------------------------------------
class RunFight(unittest.TestCase):
    def _run(self, *args):
        p = subprocess.run([sys.executable, "-I", os.path.join(REPO, "tools", "bots", "run_fight.py"), *args], capture_output=True, text=True,
                           errors="replace")
        return p.returncode, p.stdout + p.stderr

    def test_fake_run_passes_and_a_twin_fails(self):
        d = tmp()
        code, out = self._run("--fake", "--which", "fight,loot", "--outdir", d)
        self.assertEqual(code, 0, out)
        self.assertIn("PASS  enemy_fight", out)
        self.assertIn("PASS  corpse_loot_race", out)
        code, out = self._run("--fake", "--which", "fight", "--bug", "double_damage", "--outdir", d)
        self.assertEqual(code, 1, out)
        self.assertIn("FAIL  enemy_fight", out)

    def test_usage_errors_and_listing(self):
        self.assertEqual(self._run("--fake", "--which", "nope")[0], 2)
        self.assertEqual(self._run("--fake", "--bug", "nope")[0], 2)
        code, out = self._run("--list-bugs")
        self.assertEqual(code, 0)
        for name, bug in registry.TWINS:
            if name in ENEMY:
                self.assertIn(bug, out)

    def test_instances_argument(self):
        import run_fight
        self.assertEqual(run_fight.parse_instances("0,1"), [0, 1])
        self.assertEqual(run_fight.parse_instances("2"), [0, 1])
        self.assertEqual(run_fight.parse_instances("1,3"), [1, 3])
        for bad in ("1", "0", "0,0", ""):
            with self.assertRaises(ValueError):
                run_fight.parse_instances(bad)
        self.assertEqual(self._run("--fake", "--instances", "1", "--which", "loot")[0], 0, "--instances is ignored in a fake run")
        self.assertEqual(self._run("--instances", "1", "--preflight-only")[0], 2)

    def test_pick_accepts_short_and_full_names(self):
        sys.path.insert(0, os.path.join(REPO, "tools", "bots"))
        import run_fight
        self.assertEqual(run_fight.pick("all"), list(ENEMY))
        self.assertEqual(run_fight.pick("loot,fight"), ["corpse_loot_race", "enemy_fight"])
        self.assertEqual(run_fight.pick("enemy_spawn,spawn"), ["enemy_spawn"])
        with self.assertRaises(ValueError):
            run_fight.pick("vats_hit")

    def test_preflight_reports_missing_bridge_methods(self):
        import run_fight

        class Rpc(Exception):
            def __init__(self, code):
                super().__init__(f"[{code}]")
                self.code = code

        class Bridge:
            def __init__(self, missing=()):
                self.missing = set(missing)

            def call(self, method, params=None, timeout_s=0):
                if method in self.missing:
                    raise Rpc(-32601)
                if method == "coop.owner":
                    raise Rpc(-32602)               # bad parameters: the method exists
                return {}
        rows, missing = run_fight.preflight([Bridge(), Bridge()])
        self.assertEqual(missing, [])
        self.assertTrue(all(r["status"] == "present" for r in rows))
        rows, missing = run_fight.preflight([Bridge(), Bridge({"coop.owner", "coop.combat"})])
        self.assertEqual(missing, ["coop.owner"], "only a missing REQUIRED method blocks the run")
        self.assertEqual({r["method"]: r["status"] for r in rows}["coop.combat"], "MISSING")
        self.assertEqual([r for r in rows if r["method"] == "coop.owner"][0]["per_instance"], ["present", "MISSING"])
        # twin: a probe that treats 'method not found' as present would never block
        old = run_fight.METHOD_NOT_FOUND
        run_fight.METHOD_NOT_FOUND = 12345
        try:
            _, missing2 = run_fight.preflight([Bridge({"coop.owner"})])
        finally:
            run_fight.METHOD_NOT_FOUND = old
        self.assertEqual(missing2, [], "twin: a probe that no longer recognises 'method not found' would let a blocked run start")


class CreatedNpcs(unittest.TestCase):
    """In a game a created NPC has a different runtime id on every machine; the playbooks address it by net id through PerMachine."""

    def test_per_machine_members(self):
        lists = {"host": [("N1", "B", "FF000010"), ("N2", "B", "FF000011"), (None, "B", "FF000012")],
                 "client1": [("N1", "B", "FF000A10"), ("N2", "B", "FF000A11"), (None, "B", "FF000A12")]}
        self.assertEqual(pb_enemy.member_keys(lists)["host"], pb_enemy.member_keys(lists)["client1"])
        ms = pb_enemy.per_machine_members(lists)
        self.assertEqual([m.key for m in ms], ["N1", "N2"], "a member without a net id cannot be matched across machines")
        host, client = core.Machine(0, "host", None), core.Machine(1, "client1", None)
        self.assertEqual(pb_enemy.ref_on(ms[0], host), "FF000010")
        self.assertEqual(pb_enemy.ref_on(ms[0], client), "FF000A10")
        self.assertEqual(pb_enemy.ref_on("FalloutNV.esm:104C70", client), "FalloutNV.esm:104C70", "a placed NPC has one id everywhere")
        self.assertEqual([m.key for m in pb_enemy.per_machine_members(lists, skip={"N1"})], ["N2"])
        with self.assertRaisesRegex(core.StepFailure, "no copy"):
            pb_enemy.ref_on(ms[0], core.Machine(2, "client2", None))
        led = pb_enemy.Ledger()
        led.add(ms[0], probe.Result(True, "", {"hits": 1}))
        self.assertEqual(led.hits, {"N1": 1})

    def test_the_fight_works_with_different_ids_per_machine(self):
        """Every machine addresses the NPCs of enemy_fight through its own id (here the same string, wrapped): the run is unchanged."""
        class Renamed(core.Playbook):
            name = "renamed"
            scenario = "goodsprings_enemies"
            needs_scenario_refs = ("hostiles",)

            def run(self, ctx):
                targets = [pb_enemy.PerMachine({m.name: r for m in ctx.machines}, r) for r in ctx.refs["hostiles"]]
                with ctx.step("walk"):
                    pb_enemy.gather(ctx, ctx.refs["hostiles"][0])
                with ctx.step("fight"):
                    for t in targets:
                        hp = pb_enemy.current_hp(ctx, t)
                        seg = pb_enemy.Ledger()
                        pb_enemy.volley(ctx, t, seg)
                        ctx.settle()
                        pb_enemy.check_kill(ctx, t, hp, seg)
        w, refs = pb_enemy.fake_enemy_fight(2)
        r = core.run_playbook(Renamed(), fakecoop.make_machines(w), fakecoop.FakeComparer(), tmp("enemy-renamed-"), refs, dict(OPTS, anim=False))
        self.assertTrue(r.ok, r.reason)


class Diagnostics(unittest.TestCase):
    def test_engine_counters_are_saved_next_to_the_result_but_never_asserted_on(self):
        w, refs = pb_enemy.fake_corpse(2)
        for b in w.bots:
            def call(method, params=None, timeout_s=0, _o=b.nv.call, _b=b):
                if method == "combat.status":
                    return {"stats": {"hitsApplied": 7}, "who": _b.name}
                return _o(method, params, timeout_s)
            b.nv.call = call
        d = tmp("enemy-stats-")
        r = core.run_playbook(registry.make("corpse_loot_race", REPO), fakecoop.make_machines(w), fakecoop.FakeComparer(), d, refs, dict(OPTS))
        self.assertTrue(r.ok, r.reason)
        st = json.load(open(os.path.join(d, "engine_stats.json"), encoding="utf-8"))
        self.assertEqual(set(st), {"corpse_loot_race start", "corpse_loot_race end"})
        self.assertEqual(st["corpse_loot_race end"]["client1"]["combat.status"]["stats"]["hitsApplied"], 7)
        self.assertNotIn("anim.npc", st["corpse_loot_race end"]["host"], "a method the build does not have is skipped")
        r, _ = run("corpse_loot_race")                 # the plain fake has no combat.status: the run is unaffected
        self.assertTrue(r.ok, r.reason)
        st = json.load(open(os.path.join(r.outdir, "engine_stats.json"), encoding="utf-8"))
        self.assertNotIn("combat.status", st["corpse_loot_race end"]["host"])


class FakeWorldRules(unittest.TestCase):
    """The fake itself must behave: a wrong fake would make every playbook result meaningless."""

    def test_npcs_obey_the_leash_and_the_owner_only_runs_the_ai(self):
        w, refs = pb_enemy.fake_enemy_fight(2)
        for _ in range(400):
            w.tick()
        # players start 2500 away from the NPCs' post: the leash (1500 + 450) keeps them at their post, weapons drawn
        self.assertTrue(all(fakecoop.math.dist(L["pos"], L["home"]) < 1.0 for L in w.live.values()))
        self.assertEqual(sum(w.npc_hit_n), 0)

    def test_a_remote_copy_lags_the_owner_by_the_interpolation_delay(self):
        w, refs = pb_enemy.fake_enemy_handoff(2)
        for _ in range(200):
            w.tick()
        k = fakecoop.norm(pb_enemy.HOSTILES[0])
        own, rem = w.npc_view(k, 0), w.npc_view(k, 1)
        d = fakecoop.math.dist(own["pos"], rem["pos"])
        self.assertLess(d, fakenpc.NPC_SPEED * 0.25, "a copy must be at most about one interpolation buffer behind")
        self.assertTrue(w.t(pb_enemy.HOSTILES[0])["owner"] == 0)


class StepRun(core.Playbook):
    """Three steps that only record what ran: the engine's selection and arrange rules, without a game or a fake world rule in the way."""
    name = "steps3"
    resumable = True

    def __init__(self, fail_at: int = 0):
        self.calls = []
        self.fail_at = fail_at

    def run(self, ctx):
        for i in (1, 2, 3):
            def body(i=i):
                self.calls.append(("body", i))
                if i == self.fail_at:
                    raise core.StepFailure("boom", "check")
            ctx.run_step(f"s{i}", body, arrange=lambda i=i: self.calls.append(("arrange", i)), compare=False)


def run_steps(pb, **o):
    w, refs = pb_enemy.fake_enemy_fight(2)
    return core.run_playbook(pb, fakecoop.make_machines(w), fakecoop.FakeComparer(), tmp("enemy-steps-"), refs, dict(OPTS, anim=False, **o))


def run_fight_step(w, refs, **o):
    """enemy_fight on an existing world (the game's world is still there between two runs)."""
    return core.run_playbook(registry.make("enemy_fight", REPO), fakecoop.make_machines(w), fakecoop.FakeComparer(), tmp(), refs,
                             dict(OPTS, anim=False, **o))


class ResumableSteps(unittest.TestCase):
    """--from-step N / --only-step N: a failing step is rerun without the steps before it, on the world they left."""

    def test_engine_skips_and_arranges_once_for_the_first_step_that_runs(self):
        pb = StepRun()
        r = run_steps(pb)
        self.assertTrue(r.ok, r.reason)
        self.assertEqual(pb.calls, [("body", 1), ("body", 2), ("body", 3)], "a full run never calls arrange")
        self.assertEqual((r.skipped, [s.index for s in r.steps]), ([], [1, 2, 3]))
        pb = StepRun()
        r = run_steps(pb, from_step=2)
        self.assertTrue(r.ok, r.reason)
        self.assertEqual(pb.calls, [("arrange", 2), ("body", 2), ("body", 3)], "arrange for the first step only: step 3 follows step 2")
        self.assertEqual((r.skipped, [s.index for s in r.steps]), (["01-s1"], [2, 3]))
        pb = StepRun()
        r = run_steps(pb, only_step=3)
        self.assertEqual(pb.calls, [("arrange", 3), ("body", 3)])
        self.assertEqual(r.skipped, ["01-s1", "02-s2"])
        self.assertEqual((r.only_step, r.to_json()["only_step"], r.to_json()["skipped"]), (3, 3, ["01-s1", "02-s2"]))
        pb = StepRun()
        run_steps(pb, from_step=1)
        self.assertEqual(pb.calls, [("body", 1), ("body", 2), ("body", 3)], "--from-step 1 is a full run")

    def test_a_failure_keeps_the_real_step_number(self):
        r = run_steps(StepRun(fail_at=3), from_step=2)
        self.assertFalse(r.ok)
        self.assertEqual([(s.index, s.ok) for s in r.steps], [(2, True), (3, False)])
        self.assertTrue(os.path.basename(r.steps[-1].fail_dir).startswith("FAIL-1-03-"), r.steps[-1].fail_dir)

    def test_no_step_selected_and_non_resumable_playbooks_are_refused(self):
        r = run_steps(StepRun(), only_step=9)
        self.assertFalse(r.ok)
        self.assertIn("no step was selected", r.reason)
        w, refs = pb_enemy.fake_enemy_handoff(2)
        r = core.run_playbook(registry.make("enemy_handoff", REPO), fakecoop.make_machines(w), fakecoop.FakeComparer(), tmp(), refs,
                              dict(OPTS, only_step=2))
        self.assertFalse(r.ok)
        self.assertIn("cannot run from a later step", r.reason)
        self.assertEqual(r.steps, [], "nothing ran")

    def test_full_run_never_arranges(self):
        calls = []
        orig = {n: getattr(pb_enemy, n) for n in ("place_near", "require_alive", "owners_settled", "rebaseline_fight")}
        try:
            for n, f in orig.items():
                setattr(pb_enemy, n, lambda *a, _f=f, _n=n, **k: (calls.append(_n), _f(*a, **k))[1])
            r, _ = run("enemy_fight", opts={"anim": False})
            self.assertTrue(r.ok, r.reason)
            self.assertEqual(calls, [], "a full run behaves exactly as before: no arrange part runs")
            w, refs = pb_enemy.fake_enemy_fight(2)
            self.assertTrue(run_fight_step(w, refs, only_step=2).ok)
            self.assertEqual(set(calls), set(orig), "a partial run uses all of them")
        finally:
            for n, f in orig.items():
                setattr(pb_enemy, n, f)

    def test_every_enemy_fight_step_runs_alone_on_the_world_the_one_before_left(self):
        w, refs = pb_enemy.fake_enemy_fight(2)
        for n in range(1, 8):
            r = run_fight_step(w, refs, only_step=n)
            self.assertTrue(r.ok, f"step {n} alone: {r.reason}")
            self.assertEqual([s.index for s in r.steps], [n])
        self.assertTrue(all(not w.bots[0].target_alive(h) for h in pb_enemy.HOSTILES), "the seven single-step runs killed all three")

    def test_from_step_after_an_earlier_partial_run(self):
        w, refs = pb_enemy.fake_enemy_fight(2)
        self.assertTrue(run_fight_step(w, refs, only_step=4).ok)               # enemy 1 is dead, the others fight on
        r = run_fight_step(w, refs, from_step=5)
        self.assertTrue(r.ok, r.reason)
        self.assertEqual([s.index for s in r.steps], [5, 6, 7])

    def test_every_step_starts_from_a_cold_world(self):
        """The first run in a fresh world (players 2500 units away, nothing walked): steps 1 to 6 arrange it themselves; step 7 needs
        the dead and says so instead of passing."""
        for n in range(1, 7):
            with self.subTest(n):
                w, refs = pb_enemy.fake_enemy_fight(2)
                r = run_fight_step(w, refs, only_step=n)
                self.assertTrue(r.ok, f"step {n} on a cold world: {r.reason}")
        w, refs = pb_enemy.fake_enemy_fight(2)
        r = run_fight_step(w, refs, only_step=7)
        self.assertFalse(r.ok)
        self.assertIn("alive somewhere", r.reason)

    def test_arrange_puts_the_players_next_to_the_enemies(self):
        w, refs = pb_enemy.fake_enemy_fight(2)
        far = [fakecoop.math.dist(w.players[s]["pos"], w.t(pb_enemy.HOSTILES[0])["pos"]) for s in range(2)]
        self.assertGreater(min(far), 2000)
        self.assertTrue(run_fight_step(w, refs, only_step=4).ok)
        near = [fakecoop.math.dist(w.players[s]["pos"], w.t(pb_enemy.HOSTILES[0])["pos"]) for s in range(2)]
        self.assertLess(max(near), 900, "arrange hopped both players to the fight")
        # twin: without the placement the players stay where they were (the fake has no range rule, so the position is what shows it)
        orig = pb_enemy.place_near
        pb_enemy.place_near = lambda *a, **k: None
        try:
            w, refs = pb_enemy.fake_enemy_fight(2)
            run_fight_step(w, refs, only_step=4)
            stayed = [fakecoop.math.dist(w.players[s]["pos"], w.t(pb_enemy.HOSTILES[0])["pos"]) for s in range(2)]
        finally:
            pb_enemy.place_near = orig
        self.assertGreater(min(stayed), 2000, "twin: no placement, no hop")

    def test_place_near_leaves_players_who_are_close_where_they_are(self):
        w, refs = pb_enemy.fake_enemy_fight(2)
        ctx = core.Ctx(fakecoop.make_machines(w), None, tmp(), refs, dict(OPTS))
        pb_enemy.place_near(ctx, pb_enemy.HOSTILES[0])
        where = [tuple(p["pos"]) for p in w.players]
        pb_enemy.place_near(ctx, pb_enemy.HOSTILES[0])
        self.assertEqual([tuple(p["pos"]) for p in w.players], where)

    def test_a_dead_target_cannot_be_arranged(self):
        w, refs = pb_enemy.fake_enemy_fight(2)
        self.assertTrue(run_fight_step(w, refs, only_step=4).ok)
        r = run_fight_step(w, refs, only_step=4)               # enemy 1 is dead already
        self.assertFalse(r.ok)
        self.assertEqual(r.steps[-1].kind, "arrange")
        self.assertIn("already dead", r.reason)
        self.assertIn("reload the scenario", r.reason)
        # twin: without the alive check the same run goes on and shoots a corpse without a word
        w, refs = pb_enemy.fake_enemy_fight(2)
        run_fight_step(w, refs, only_step=4)
        orig = pb_enemy.require_alive
        pb_enemy.require_alive = lambda *a, **k: None
        try:
            r2 = run_fight_step(w, refs, only_step=4)
        finally:
            pb_enemy.require_alive = orig
        self.assertNotEqual(r2.steps[-1].kind, "arrange")

    def test_machines_that_disagree_about_a_target_cannot_be_arranged(self):
        w, refs = pb_enemy.fake_enemy_fight(2)
        k = fakecoop.norm(pb_enemy.HOSTILES[0])
        w.views[1]["refs"][k]["dead"] = True               # the client thinks it is dead, the host does not
        r = run_fight_step(w, refs, only_step=4)
        self.assertFalse(r.ok)
        self.assertEqual(r.steps[-1].kind, "arrange")
        self.assertIn("alive on some machines and dead on others", r.reason)

    def test_rebaseline_counts_the_dead_so_the_duplicate_check_stays_exact(self):
        w, refs = pb_enemy.fake_enemy_fight(2)
        self.assertTrue(run_fight_step(w, refs, only_step=4).ok)
        r = run_fight_step(w, refs, only_step=5)
        self.assertTrue(r.ok, r.reason)
        w, refs = pb_enemy.fake_enemy_fight(2)
        run_fight_step(w, refs, only_step=4)
        orig = pb_enemy.rebaseline_fight

        def forgets_the_dead(ctx, st, hostiles):
            orig(ctx, st, hostiles)
            base_of = ctx.refs.get("hostile_base_of") or {}
            st.c0 = {b: n - sum(1 for x in st.killed if base_of.get(x) == b) for b, n in (st.c0 or {}).items()}
        pb_enemy.rebaseline_fight = forgets_the_dead
        try:
            bad = run_fight_step(w, refs, only_step=5)
        finally:
            pb_enemy.rebaseline_fight = orig
        self.assertFalse(bad.ok, "twin: a baseline that forgets the dead makes the next step see a phantom duplicate or vanish")
        self.assertRegex(bad.reason, "live refs")

    def test_a_bug_still_fails_the_step_it_belongs_to_when_that_step_runs_alone(self):
        r, _ = run("enemy_fight", bugs={"double_damage"}, opts={"anim": False})
        self.assertFalse(r.ok)
        failed = r.steps[-1].index
        self.assertEqual(failed, 4)
        w, refs = pb_enemy.fake_enemy_fight(2, {"double_damage"})
        r = run_fight_step(w, refs, only_step=failed)
        self.assertFalse(r.ok)
        self.assertIn("applied twice", r.reason)
        self.assertEqual(r.steps[-1].index, failed)


class FightCli(unittest.TestCase):
    def _run(self, *args):
        p = subprocess.run([sys.executable, "-I", os.path.join(REPO, "tools", "bots", "run_fight.py"), *args], capture_output=True, text=True,
                           errors="replace")
        return p.returncode, p.stdout + p.stderr

    def test_only_step_and_from_step_on_the_fake_world(self):
        d = tmp()
        code, out = self._run("--fake", "--which", "fight", "--only-step", "4", "--outdir", d)
        self.assertEqual(code, 0, out)
        res = json.load(open(os.path.join(d, "summary.json"), encoding="utf-8"))["results"][0]
        self.assertEqual((res["only_step"], len(res["skipped"])), (4, 6))
        self.assertEqual([s["index"] for s in res["steps"]], [4, 8])               # 8: the animation check after the playbook
        code, out = self._run("--fake", "--which", "fight", "--from-step", "6", "--outdir", d)
        res = json.load(open(os.path.join(d, "summary.json"), encoding="utf-8"))["results"][0]
        self.assertEqual(res["from_step"], 6)
        self.assertEqual([s["index"] for s in res["steps"]][:2], [6, 7])

    def test_a_failure_prints_the_command_that_reruns_that_step(self):
        d = tmp()
        code, out = self._run("--fake", "--which", "fight", "--bug", "double_damage", "--outdir", d)
        self.assertEqual(code, 1, out)
        self.assertIn("rerun only the failed step (04-", out)
        line = [x for x in out.splitlines() if x.strip().startswith("python -I tools/bots/run_fight.py")][0]
        self.assertIn("--from-step 4", line)
        self.assertIn("--which fight", line)
        self.assertNotIn("--outdir", line)

    def test_usage_errors(self):
        for args in (("--which", "fight", "--from-step", "2", "--only-step", "3"), ("--only-step", "2"), ("--from-step", "3"),
                     ("--which", "handoff", "--only-step", "2"), ("--which", "fight", "--only-step", "-1"),
                     ("--which", "fight", "--timescale", "0"), ("--which", "fight", "--timescale", "500")):
            with self.subTest(args):
                code, out = self._run("--fake", *args)
                self.assertEqual(code, 2, out)
        code, out = self._run("--fake", "--which", "handoff", "--only-step", "2")
        self.assertIn("enemy_fight", out, "the error names the tests that can")

    def test_rerun_command_replaces_the_old_selection(self):
        import run_fight
        cmd = run_fight.rerun_command(["--instances", "0,1", "--which", "fight", "--only-step", "2", "--outdir", "x y", "--reload",
                                       "--timescale", "4"], 5)
        self.assertEqual(cmd, "python -I tools/bots/run_fight.py --instances 0,1 --which fight --timescale 4 --from-step 5")
        self.assertEqual(run_fight.rerun_command(["--from-step=3", "--which", "fight"], 6),
                         "python -I tools/bots/run_fight.py --which fight --from-step 6")

    def test_a_partial_run_in_a_game_does_not_reload_the_scenario(self):
        """In a game the scenario jump is what costs a minute: --from-step / --only-step skip it, --reload brings it back."""
        import types
        import run_coop
        import run_fight

        class Nv:
            def call(self, method, params=None, timeout_s=0):
                return {}

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False
        mod = types.ModuleType("nvbridge")
        mod.NvBridge = types.SimpleNamespace(connect=lambda instance, wait_s: Nv())
        old_mod, old_main = sys.modules.get("nvbridge"), run_coop.main
        seen = []
        sys.modules["nvbridge"] = mod
        run_coop.main = lambda args, extra=None: (seen.append((list(args), dict(extra or {}))), 0)[1]
        base = ["--instances", "0,1", "--which", "fight", "--outdir", tmp()]
        try:
            for extra_args, no_scenario, want in ((["--only-step", "4"], True, {"only_step": 4}),
                                                  (["--from-step", "3", "--timescale", "4"], True, {"from_step": 3, "timescale": 4.0}),
                                                  (["--from-step", "3", "--reload"], False, {"from_step": 3}),
                                                  ([], False, {}), (["--from-step", "1"], False, {"from_step": 1})):
                seen.clear()
                with contextlib.redirect_stdout(io.StringIO()):         # the preflight table mentions 'skipped': keep the runner's output clean
                    self.assertEqual(run_fight.main(base + extra_args), 0)
                args, extra = seen[0]
                self.assertEqual(("--no-scenario" in args), no_scenario, extra_args)
                self.assertEqual(extra, want, extra_args)
        finally:
            run_coop.main = old_main
            if old_mod is None:
                sys.modules.pop("nvbridge", None)
            else:
                sys.modules["nvbridge"] = old_mod


class ScriptBot:
    """A bot that only answers coop.status, from a function of the manual clock: stuck, creeping or catching up, as the test wants."""

    def __init__(self, name, status_fn):
        self.name = name
        self._status = status_fn
        self.nv = type("Nv", (), {"call": staticmethod(self._call)})()

    def _call(self, method, params=None, timeout_s=0):
        raise RuntimeError("unknown method " + method)

    def coop_status(self):
        return self._status()


def settle_ctx(host_status, client_status, **opts):
    clock = clocks.ManualClock()
    machines = [core.Machine(0, "host", ScriptBot("host", lambda: host_status(clock.now()))),
                core.Machine(1, "client1", ScriptBot("client1", lambda: client_status(clock.now())))]
    o = {"clock": clock, "load_scale": False, "settle_s": 24.0, "settle_poll_s": 0.5}
    o.update(opts)
    return core.Ctx(machines, None, tmp("settle-"), {}, o), clock


def host_st(head, acked, synced, mismatch=0, desyncs=()):
    return {"role": "host", "lastSeq": head, "counters": {}, "host": {"lastSeq": head, "allSynced": synced, "badMessages": 0,
            "peers": [{"slot": 1, "joined": True, "acked": acked}]},
            "desyncs": {"digests": {"mismatch": mismatch, "repairs": mismatch, "effectsRepaired": 0}, "desyncs": list(desyncs)}}


class FailFastSettle(unittest.TestCase):
    """Ctx.settle() stops when nothing moves, with the reason, instead of waiting the whole budget for the same answer."""

    def stuck(self, **opts):
        return settle_ctx(lambda t: host_st(10, 7, False), lambda t: {"role": "client", "lastSeq": 7}, **opts)

    def test_stuck_machines_fail_early_and_the_reason_says_what_is_stuck(self):
        ctx, clock = self.stuck()
        with self.assertRaises(core.StepFailure) as cm:
            ctx.settle()
        self.assertEqual(cm.exception.kind, "settle")
        self.assertLess(clock.now(), 8.0, "gave up after the 6 s stall window, not the 24 s budget")
        msg = cm.exception.reason
        for want in ("did not converge", "no progress for", "stopped early", "lastSeq per machine {'host': 10, 'client1': 7}",
                     "allSynced=False", "client acks {'1': 7}"):
            self.assertIn(want, msg)

    def test_twin_without_the_stall_window_the_whole_budget_is_spent(self):
        ctx, clock = self.stuck(settle_stall_s=0)
        with self.assertRaises(core.StepFailure) as cm:
            ctx.settle()
        self.assertGreaterEqual(clock.now(), 24.0)
        self.assertIn("within 24s", cm.exception.reason)
        self.assertNotIn("no progress", cm.exception.reason)

    def test_the_window_follows_the_budget_unless_set(self):
        ctx, _ = self.stuck()
        self.assertEqual(ctx._stall_window(20.0), 6.0)
        self.assertEqual(ctx._stall_window(180.0), 45.0, "a chaos profile with a long budget gets a long window")
        ctx, _ = self.stuck(settle_stall_s=2.5)
        self.assertEqual(ctx._stall_window(180.0), 2.5)
        ctx, clock = self.stuck(settle_stall_s=2.5)
        with self.assertRaises(core.StepFailure):
            ctx.settle()
        self.assertLess(clock.now(), 4.0)

    def test_a_repeating_digest_mismatch_is_named(self):
        n = [0]

        def host(t):
            n[0] += 1                                   # the counter rises on every poll, the stuck mismatch is always the same one
            return host_st(10, 7, False, mismatch=n[0], desyncs=[{"key": "quest:VCG02", "digest": "ab12"}])
        ctx, clock = settle_ctx(host, lambda t: {"role": "client", "lastSeq": 7})
        with self.assertRaises(core.StepFailure) as cm:
            ctx.settle()
        self.assertLess(clock.now(), 8.0, "a repair loop is not progress")
        self.assertIn("digest mismatch counter rose by", cm.exception.reason)
        self.assertIn("quest:VCG02", cm.exception.reason)

    def test_slow_but_moving_machines_are_not_stopped(self):
        def client(t):
            return {"role": "client", "lastSeq": 7 + min(3, int(t // 4))}       # one more message every 4 s (window 6 s)
        ctx, clock = settle_ctx(lambda t: host_st(10, 7 + min(3, int(t // 4)), 7 + min(3, int(t // 4)) == 10), client)
        ctx.settle()
        self.assertGreaterEqual(clock.now(), 12.0)

    def test_only_the_acks_moving_counts_as_progress(self):
        """lastSeq of every machine is frozen for 20 s while the host's view of the client's acks creeps up: still moving."""
        def host(t):
            late = t >= 20.0
            return host_st(10, 10 if late else 4 + int(t // 4), late)
        ctx, clock = settle_ctx(host, lambda t: {"role": "client", "lastSeq": 10 if t >= 20.0 else 7})
        ctx.settle()
        self.assertGreaterEqual(clock.now(), 20.0)

    def test_twin_progress_that_ignores_acks_or_everything_stops_moving_runs_early(self):
        def acks_only(t):
            late = t >= 20.0
            return host_st(10, 10 if late else 4 + int(t // 4), late)
        orig = core.Ctx.__dict__["_progress"]
        try:
            core.Ctx._progress = staticmethod(lambda st, host: tuple(sorted((n, core.last_seq(s)) for n, s in st.items())))
            ctx, _ = settle_ctx(acks_only, lambda t: {"role": "client", "lastSeq": 10 if t >= 20.0 else 7})
            with self.assertRaisesRegex(core.StepFailure, "no progress"):
                ctx.settle()
            core.Ctx._progress = staticmethod(lambda st, host: ())
            ctx, _ = settle_ctx(lambda t: host_st(10, 7 + min(3, int(t // 4)), 7 + min(3, int(t // 4)) == 10),
                                lambda t: {"role": "client", "lastSeq": 7 + min(3, int(t // 4))})
            with self.assertRaisesRegex(core.StepFailure, "no progress"):
                ctx.settle()
        finally:
            core.Ctx._progress = orig

    def test_converged_machines_pass_at_once(self):
        ctx, clock = settle_ctx(lambda t: host_st(10, 10, True), lambda t: {"role": "client", "lastSeq": 10})
        ctx.settle()
        self.assertLess(clock.now(), 2.0)

    def test_fake_world_that_never_delivers_fails_early_in_virtual_time(self):
        r, w = run("loot_race", lag=10 ** 6, opts={"anim": False, "settle_s": 60.0, "settle_stall_s": 1.0})
        self.assertFalse(r.ok)
        self.assertRegex(r.reason, r"no progress for 1(\.\d+)? simulated s")
        self.assertIn("budget was 60", r.reason)
        r, w = run("loot_race", lag=10 ** 6, opts={"anim": False, "settle_s": 5.0, "settle_stall_s": 0})
        self.assertIn("did not converge", r.reason)
        self.assertNotIn("no progress", r.reason)


class Timescale(unittest.TestCase):
    """--timescale X: "sgtm X" on every copy after the setup, fixed sleeps shrink by X, sgtm 1 at the end and on every failure."""

    @staticmethod
    def sgtm(world):
        return {b.name: [c["cmd"] for m, c in b.nv.calls if m == "exec" and c["cmd"].startswith("sgtm")] for b in world.bots}

    def test_fixed_sleeps_shrink_by_the_timescale_but_budgets_do_not(self):
        def took(scale, fn, *a):
            clock = clocks.ManualClock()
            w, refs = pb_enemy.fake_enemy_fight(2)
            ctx = core.Ctx(fakecoop.make_machines(w), None, tmp(), refs, {"clock": clock, "load_scale": False, "timescale": scale})
            fn(ctx, *a)
            return clock.now()
        self.assertAlmostEqual(took(1.0, core.Ctx.advance, 4.0), 4.0, delta=0.2)
        self.assertAlmostEqual(took(4.0, core.Ctx.advance, 4.0), 1.0, delta=0.2)
        self.assertAlmostEqual(took(4.0, core.Ctx.pause, 2.0), 0.5, delta=0.2)
        self.assertAlmostEqual(took(0.5, core.Ctx.advance, 1.0), 2.0, delta=0.2, msg="slow motion waits longer")
        clock = clocks.ManualClock()
        w, refs = pb_enemy.fake_enemy_fight(2)
        ctx = core.Ctx(fakecoop.make_machines(w), None, tmp(), refs, {"clock": clock, "load_scale": False, "timescale": 4.0})
        self.assertEqual(ctx.budget(10.0), 10.0, "timeouts stay real time")
        with self.assertRaisesRegex(core.StepFailure, "timed out after 10s"):
            ctx.wait_until("never", lambda: False, timeout_s=10.0)
        self.assertGreaterEqual(clock.now(), 10.0)
        # twin: sleeps that ignore the timescale take the full time
        orig = core.Ctx.game_wall_s
        core.Ctx.game_wall_s = lambda self, s: s
        try:
            self.assertAlmostEqual(took(4.0, core.Ctx.advance, 4.0), 4.0, delta=0.2)
        finally:
            core.Ctx.game_wall_s = orig

    def test_a_fake_world_clock_is_game_time_already(self):
        w, refs = pb_enemy.fake_enemy_fight(2)
        ctx = core.Ctx(fakecoop.make_machines(w), None, tmp(), refs, dict(OPTS, timescale=4.0))
        t0 = w.vt
        ctx.advance(1.0)
        self.assertAlmostEqual(w.vt - t0, 1.0, delta=0.1)

    def test_run_sets_the_speed_after_setup_and_resets_it_afterwards(self):
        r, w = run("enemy_fight", opts={"anim": False, "timescale": 4.0})
        self.assertTrue(r.ok, r.reason)
        self.assertEqual(self.sgtm(w), {"host": ["sgtm 4", "sgtm 1"], "client1": ["sgtm 4", "sgtm 1"]})
        first = [i for i, (m, c) in enumerate(w.bots[0].nv.calls) if m == "exec"][0]
        self.assertTrue(all(m != "coop.owner" for m, _ in w.bots[0].nv.calls[:first]), "set before any step looked at the world")
        self.assertEqual(r.timescale, {"requested": 4.0, "set": {"host": "", "client1": ""}, "reset": {"host": "", "client1": ""}})
        res = json.load(open(os.path.join(r.outdir, "result.json"), encoding="utf-8"))
        self.assertEqual(res["timescale"]["requested"], 4.0)

    def test_default_speed_sends_nothing(self):
        r, w = run("enemy_fight", opts={"anim": False})
        self.assertTrue(r.ok, r.reason)
        self.assertEqual(self.sgtm(w), {"host": [], "client1": []})
        self.assertIsNone(r.timescale)
        self.assertNotIn("timescale", r.to_json())

    def test_the_speed_is_reset_after_a_failed_step(self):
        r, w = run("enemy_fight", bugs={"double_damage"}, opts={"anim": False, "timescale": 2.5})
        self.assertFalse(r.ok)
        self.assertEqual(self.sgtm(w), {"host": ["sgtm 2.5", "sgtm 1"], "client1": ["sgtm 2.5", "sgtm 1"]})
        self.assertEqual(r.timescale["reset"], {"host": "", "client1": ""})

    def test_the_speed_is_reset_when_the_run_is_interrupted(self):
        class Interrupted(core.Playbook):
            name = "interrupted"

            def run(self, ctx):
                raise KeyboardInterrupt
        w, refs = pb_enemy.fake_enemy_fight(2)
        with self.assertRaises(KeyboardInterrupt):
            core.run_playbook(Interrupted(), fakecoop.make_machines(w), fakecoop.FakeComparer(), tmp(), refs, dict(OPTS, timescale=3.0))
        self.assertEqual(self.sgtm(w), {"host": ["sgtm 3", "sgtm 1"], "client1": ["sgtm 3", "sgtm 1"]})

    def test_a_copy_that_refuses_stops_the_run_and_the_others_go_back_to_1(self):
        w, refs = pb_enemy.fake_enemy_fight(2)
        real = w.bots[0].nv.exec
        w.bots[0].nv.exec = lambda cmd, *a, **k: ({"ok": False, "output": "no such command"} if cmd.startswith("sgtm 4") else real(cmd, *a, **k))
        r = core.run_playbook(registry.make("enemy_fight", REPO), fakecoop.make_machines(w), fakecoop.FakeComparer(), tmp(), refs,
                              dict(OPTS, anim=False, timescale=4.0))
        self.assertFalse(r.ok)
        self.assertIn("could not set the game speed", r.reason)
        self.assertIn("host", r.reason)
        self.assertEqual([s for s in r.steps if s.name != "(outside step)"], [], "no step ran at mixed speeds")
        self.assertEqual(self.sgtm(w)["client1"], ["sgtm 4", "sgtm 1"])

    def test_cli_records_the_speed_in_the_summary(self):
        d = tmp()
        p = subprocess.run([sys.executable, "-I", os.path.join(REPO, "tools", "bots", "run_fight.py"), "--fake", "--which", "loot",
                            "--timescale", "4", "--outdir", d], capture_output=True, text=True, errors="replace")
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertIn("timescale 4 (sgtm), reset to 1 on every copy", p.stdout)
        res = json.load(open(os.path.join(d, "summary.json"), encoding="utf-8"))["results"][0]
        self.assertEqual(res["timescale"]["requested"], 4.0)


class SpawnPlacement(unittest.TestCase):
    """In game 2026-10-21: enemy_spawn (spawn_mode host) failed at step 01 because the host's walk to the marker was stuck on a
    slope. Placing the players is not what the test checks, so a stuck walk falls back to a teleport and leaves a note."""

    def run_host_mode(self, blocked, no_fallback=False, times_out=False):
        self.lines = []
        w, refs = pb_enemy.fake_enemy_spawn(2)
        refs = dict(refs, spawn_mode="host")
        w.walk_blocked = blocked
        w.walk_times_out = times_out
        if no_fallback:   # twin: the old placement, a plain walk
            orig = pb_enemy.goto_or_teleport
            pb_enemy.goto_or_teleport = lambda ctx, m, ref, dist: m.bot.goto_ref(ref, dist)
        try:
            d = tmp()
            w.flight_dir = os.path.join(d, "flight")
            r = core.run_playbook(registry.make("enemy_spawn", REPO), fakecoop.make_machines(w), fakecoop.FakeComparer(), d, refs, dict(OPTS), log=self.lines.append)
        finally:
            if no_fallback:
                pb_enemy.goto_or_teleport = orig
        return r

    def test_a_stuck_walk_to_the_marker_teleports_and_notes_it(self):
        r = self.run_host_mode(blocked=True)
        first = r.steps[0]
        self.assertTrue(first.ok, first.reason)
        self.assertTrue(any("did not arrive (stuck" in n and "teleported" in n for n in self.lines), self.lines)

    def test_a_walk_that_times_out_far_away_teleports_too(self):
        # in game 2026-10-22: "client1 walks to the marker: timeout: 4774 units left (wanted within 300)"
        r = self.run_host_mode(blocked=False, times_out=True)
        self.assertTrue(r.steps[0].ok, r.steps[0].reason)
        self.assertTrue(any("did not arrive (timeout" in n for n in self.lines), self.lines)
        # twin: the first fallback (stuck only) still fails on a timeout
        r2 = self.run_host_mode(blocked=False, times_out=True, no_fallback=True)
        self.assertIn("walks to the marker: timeout", r2.steps[0].reason)

    def test_twin_a_plain_walk_fails_step_one(self):
        r = self.run_host_mode(blocked=True, no_fallback=True)
        self.assertFalse(r.steps[0].ok)
        self.assertIn("walks to the marker: stuck", r.steps[0].reason)

    def test_the_fake_teleport_jumps_at_once(self):
        # PR #23 review: with walk_speed set, mode="teleport" must not take walking time in the fake
        w, refs = pb_enemy.fake_enemy_spawn(2)
        bot = fakecoop.make_machines(w)[0].bot
        t0 = w.vt
        self.assertTrue(bot.goto_ref(refs["marker"], 300, mode="teleport").ok)
        self.assertLess(w.vt - t0, 0.05)
        t1 = w.vt
        bot.goto_pos(15000, 0, 0)                 # a walk back over 5000 units takes virtual time
        self.assertGreater(w.vt - t1, 1.0)

    def test_an_open_walk_needs_no_teleport(self):
        r = self.run_host_mode(blocked=False)
        self.assertTrue(r.steps[0].ok, r.steps[0].reason)
        self.assertFalse(any("teleported" in n for n in self.lines), self.lines)



class ScenarioGivesWhatItEquips(unittest.TestCase):
    """In game 2026-10-22: the start save had no 9mm pistol, so 'equip_pistol' did nothing and enemy_handoff failed at step 01."""

    def test_every_equipped_item_is_given_first(self):
        import re
        with open(os.path.join(REPO, "scenarios", "goodsprings_enemies.json"), encoding="utf-8") as f:
            sc = json.load(f)
        given = set()
        for s in sc["setup"]:
            cmd = s.get("cmd", "")
            m = re.match(r"player\.additem (\{\w+\})", cmd)
            if m:
                given.add(m.group(1))
            m = re.match(r"player\.equipitem (\{\w+\})", cmd)
            if m:
                self.assertIn(m.group(1), given, f"{s['id']} equips {m.group(1)} before any step gives it")
        # twin: the old setup (no give_pistol) fails the same rule
        old = [s for s in sc["setup"] if s.get("id") != "give_pistol"]
        self.assertFalse(any(re.match(r"player\.additem \{pistol\}", s.get("cmd", "")) for s in old))


class EquipAllGivesAMissingWeapon(unittest.TestCase):
    """In game 2026-10-22: --from-step 2 arranged the world and failed 'host equips FalloutNV.esm:0E3778: not_in_inventory'."""

    def test_a_missing_weapon_is_given_then_equipped(self):
        w, refs = pb_enemy.fake_enemy_fight(2) if hasattr(pb_enemy, "fake_enemy_fight") else (None, None)
        if w is None:
            self.skipTest("no fake_enemy_fight fixture")
        ms = fakecoop.make_machines(w)
        weapon = refs.get("weapon")
        if not weapon:
            self.skipTest("the fixture has no weapon ref")
        for m in ms:
            m.bot.me["inv"].pop(weapon, None)
            m.bot.me["inv"].pop(fakecoop.hexid(weapon) if weapon[:1].isdigit() else weapon, None)
        lines = []
        ctx = core.Ctx(ms, fakecoop.FakeComparer(), tmp(), refs, dict(OPTS), "test", lines.append)
        ctx.equip_all()
        self.assertTrue(all(m.bot.me.get("equipped") == weapon for m in ms))
        self.assertTrue(any("gave one" in l for l in lines), lines)
        # twin: equipping alone (the old equip_all) fails on the same empty inventory
        for m in ms:
            m.bot.me["inv"].pop(weapon, None)
        self.assertFalse(ms[0].bot.equip(weapon).ok)


if __name__ == "__main__":
    unittest.main()
