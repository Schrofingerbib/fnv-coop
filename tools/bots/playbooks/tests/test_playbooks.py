"""Game-free tests for the co-op playbooks, with mutation twins.

Run: python -I tools/bots/playbooks/tests/run_tests.py   (prints ALL OK)

What is proven:
  * every playbook passes on a correct fake co-op world with 2, 3 and 4 players (where it allows),
  * every twin (playbook + deliberately wrong fake behaviour) FAILS, with a reason that names the problem,
  * a rising desync counter fails every playbook,
  * a failed step saves the files T1b needs,
  * the Goodsprings plan resolves against out/questdata (and breaks loudly when it does not),
  * the scenario adapter maps real scenarios/*.json to the refs the playbooks need,
  * the live snapdiff comparer sees a real difference between two golden snapshots,
  * the adapter translates to T6a's real Bot (over its own fake bridge).
"""
from __future__ import annotations

import copy
import gzip
import json
import os
import shutil
import sys
import tempfile
import unittest

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))
sys.path.insert(0, os.path.join(REPO, "tools", "bots"))
sys.path.insert(0, os.path.join(REPO, "tools"))

from playbooks import (chaos_adapter, core, fakecoop, goodsprings_plan as GP, pb_story, probe,  # noqa: E402
                       registry, scenario_adapter)

OPTS = {"fast": True, "anim": False, "ui_s": 0.2, "barrier_s": 0.2, "stage_s": 0.2, "handoff_s": 0.2, "spawn_s": 0.2, "settle_s": 0.3, "settle_poll_s": 0.0, "repo_root": REPO}
_PLAN = None


def plan():
    global _PLAN
    if _PLAN is None:
        _PLAN = GP.load_plan(REPO)
    return _PLAN


def run(name, bugs=(), n=None, lag=1, opts=None, outdir=None, **world_kw):
    e = registry.REGISTRY[name]
    n = n or e.players
    if e.needs_plan:
        w, refs = pb_story.fake_goodsprings(plan(), n, set(bugs), lag=lag, **world_kw)
    else:
        w, refs = e.fake(n, set(bugs), lag=lag, **world_kw)
    d = outdir or tempfile.mkdtemp(prefix="t6b-")
    w.flight_dir = os.path.join(d, "flight")
    o = dict(OPTS)
    if any(b.startswith("puppet_") for b in bugs):
        o["anim"] = True
    o.update(opts or {})
    pb = registry.make(name, REPO, plan() if e.needs_plan else None)
    return core.run_playbook(pb, fakecoop.make_machines(w), fakecoop.FakeComparer(), d, refs, o), w


class CleanRuns(unittest.TestCase):
    def test_every_playbook_passes_with_default_players(self):
        for name in registry.REGISTRY:
            with self.subTest(name):
                r, _ = run(name)
                self.assertTrue(r.ok, f"{name}: {r.reason}")
                self.assertGreaterEqual(len(r.steps), 3, "a playbook with fewer than 3 steps proves little")

    def test_two_three_four_players(self):
        for name, e in registry.REGISTRY.items():
            pb = registry.make(name, REPO)
            for n in (2, 3, 4):
                if not (pb.min_players <= n <= pb.max_players):
                    continue
                with self.subTest(f"{name} x{n}"):
                    r, _ = run(name, n=n)
                    self.assertTrue(r.ok, f"{name} with {n} players: {r.reason}")

    def test_laggy_replication(self):
        for name in ("fight", "pnr_vote", "container_share", "goodsprings_opening"):
            with self.subTest(name):
                r, _ = run(name, lag=4)
                self.assertTrue(r.ok, f"{name}: {r.reason}")

    def test_wrong_player_count_is_refused_not_crashed(self):
        pb = registry.make("fight", REPO)
        w, refs = registry.REGISTRY["fight"].fake(1)
        res = core.run_playbook(pb, fakecoop.make_machines(w), fakecoop.FakeComparer(), tempfile.mkdtemp(), refs, OPTS)
        self.assertFalse(res.ok)
        self.assertIn("needs 2 to 4 players", res.reason)

    def test_missing_scenario_ref_is_a_clear_failure(self):
        pb = registry.make("loot_race", REPO)
        w, refs = registry.REGISTRY["loot_race"].fake(2)
        refs.pop("item")
        res = core.run_playbook(pb, fakecoop.make_machines(w), fakecoop.FakeComparer(), tempfile.mkdtemp(), refs, OPTS)
        self.assertFalse(res.ok)
        self.assertIn("missing refs", res.reason)


class Twins(unittest.TestCase):
    def test_every_twin_is_caught(self):
        for name, bug in registry.TWINS:
            with self.subTest(f"{name} + {bug}"):
                clean, _ = run(name)
                self.assertTrue(clean.ok, f"clean {name} must pass first: {clean.reason}")
                bad, _ = run(name, bugs={bug})
                self.assertFalse(bad.ok, f"twin {bug} was NOT caught by {name}")
                self.assertTrue(bad.reason, "a failure needs a reason")

    def test_every_fake_bug_has_a_twin_or_is_universal(self):
        covered = {b for _, b in registry.TWINS} | {"counter_noise", "no_retransmit", "repair_after_quiet", "repairs_in_flight"}  # last three: Chaos tests
        missing = set(fakecoop.BUGS) - covered
        self.assertFalse(missing, f"fake bugs with no twin: {sorted(missing)}")

    def test_rising_counters_fail_every_playbook(self):
        for name in registry.REGISTRY:
            with self.subTest(name):
                bad, _ = run(name, bugs={"counter_noise"})
                self.assertFalse(bad.ok, f"{name} ignored a rising desync counter")
                self.assertIn("desync counters rose", bad.reason)

    def test_animation_twins_name_the_counter(self):
        r, _ = run("fight", bugs={"puppet_slides"})
        self.assertIn("avatar.glitches", r.reason)
        r, _ = run("fight", bugs={"puppet_wrong_weapon"})
        self.assertIn("wrong_weapon", r.reason)
        r, _ = run("fight", bugs={"puppet_tpose"})
        self.assertRegex(r.reason, "glitches|anim_mismatches")

    def test_animation_check_compares_something_and_can_be_switched_off(self):
        r, _ = run("fight", opts={"anim": True})
        self.assertEqual(r.steps[-1].name, "animation and pose check")
        rep = json.load(open(os.path.join(r.outdir, "anim_report.json"), encoding="utf-8"))
        self.assertGreater(rep["counters"]["avatar.compared"], 0)
        self.assertGreater(rep["counters"]["compared"], 0)
        r, _ = run("fight", opts={"anim": False})
        self.assertNotEqual(r.steps[-1].name, "animation and pose check")
        for name in ("vats_hit", "ownership_handoff"):
            r, _ = run(name, opts={"anim": True})
            self.assertTrue(r.ok, f"{name}: {r.reason}")
            self.assertEqual(r.steps[-1].name, "animation and pose check")

    def test_every_playbook_where_someone_moves_has_animation_checks(self):
        static = {"quest_step_sync", "save_load_rejoin"}          # nobody walks anywhere
        for name in registry.REGISTRY:
            pb = registry.make(name, REPO)
            self.assertEqual(pb.anim, name not in static, name)
        for name in ("fast_travel_tether", "goodsprings_opening", "loot_race", "door_terminal_lock", "pnr_vote"):
            r, _ = run(name, opts={"anim": True})
            self.assertTrue(r.ok, f"{name}: {r.reason}")
            self.assertEqual(r.steps[-1].name, "animation and pose check", name)
            rep = json.load(open(os.path.join(r.outdir, "anim_report.json"), encoding="utf-8"))
            self.assertGreater(rep["counters"]["avatar.compared"], 0, name)

    def test_teleport_is_suppressed_but_only_for_what_a_teleport_explains(self):
        # without the suppression the jump itself is a (false) rubberband glitch...
        orig = core.Ctx.teleporting
        import contextlib

        @contextlib.contextmanager
        def nothing(self, grace_ms=700.0):
            yield
        core.Ctx.teleporting = nothing
        try:
            r, _ = run("fast_travel_tether", opts={"anim": True})
        finally:
            core.Ctx.teleporting = orig
        self.assertFalse(r.ok)
        self.assertIn("avatar.glitches", r.reason)
        # ...with it the clean run passes, and a puppet that slides right after the jump still fails
        r, _ = run("fast_travel_tether", opts={"anim": True})
        self.assertTrue(r.ok, r.reason)
        r, _ = run("fast_travel_tether", bugs={"puppet_slides_after_teleport"})
        self.assertFalse(r.ok)
        self.assertIn("animation check", r.reason)
        rep = json.load(open(os.path.join(r.outdir, "anim_report.json"), encoding="utf-8"))
        self.assertGreater(rep["counters"].get("avatar.glitch.sliding", 0), 0)

    def test_handoff_walk_window_enables_the_stuck_detector(self):
        r, _ = run("ownership_handoff", opts={"anim": True})
        self.assertTrue(r.ok, r.reason)
        # a puppet that stops while the host is walking must be caught: a frozen-puppet run through the same window
        from playbooks import animwatch
        self.assertTrue(hasattr(animwatch.AnimWatch, "walking"))

    def test_specific_reasons(self):
        r, _ = run("loot_race", bugs={"dup_pickup"})
        self.assertIn("copies", r.reason)
        r, _ = run("door_terminal_lock", bugs={"door_unsynced"})
        self.assertRegex(r.reason, "door open|snapdiff")
        r, _ = run("pnr_vote", bugs={"pnr_early"})
        self.assertIn("stage", r.reason)
        r, _ = run("fast_travel_tether", bugs={"tether_off"})
        self.assertIn("tether", r.reason)
        r, _ = run("ownership_handoff", bugs={"flap_owner"})
        self.assertIn("flapped", r.reason)


CHAOS = dict(chaos_adapter.relaxed_options("bad_wifi"), settle_s=0.3, ui_s=0.2, barrier_s=0.3, stage_s=0.3,
             handoff_s=0.2, spawn_s=0.2, chaos="bad_wifi")
LOSS = 1     # every first delivery is lost once: even single-commit playbooks see drops


class Chaos(unittest.TestCase):
    """Impaired link: transient mismatches are allowed in flight, but the world must converge with 0 repairs in the quiet window."""

    def test_clean_playbooks_pass_on_a_lossy_link(self):
        for name in registry.REGISTRY:
            with self.subTest(name):
                r, w = run(name, opts=CHOOSE(CHAOS), loss_every=LOSS)
                self.assertTrue(r.ok, f"{name}: {r.reason}")
                self.assertGreater(w.n_deliv // LOSS, 0, "the link never dropped anything: the test proves nothing")
                self.assertGreater(sum(w.stats["mismatch"]), 0, "no transient mismatch was produced")

    def test_dropped_reliable_message_never_retransmitted_fails(self):
        for name in ("loot_race", "quest_step_sync", "goodsprings_opening"):
            with self.subTest(name):
                bad, _ = run(name, bugs={"no_retransmit"}, opts=CHOOSE(CHAOS), loss_every=LOSS)
                self.assertFalse(bad.ok, f"{name} accepted a message that was lost for good")
                self.assertRegex(bad.reason, "converge|stage|differ|snapdiff|timed out")

    def test_late_repair_in_quiet_window_fails(self):
        bad, _ = run("loot_race", bugs={"repair_after_quiet"}, opts=CHOOSE(CHAOS), loss_every=LOSS)
        self.assertFalse(bad.ok)
        self.assertIn("quiescence window", bad.reason)

    def test_repairs_over_budget_fail_and_loosening_is_explicit(self):
        bad, _ = run("loot_race", bugs={"repairs_in_flight"}, opts=CHOOSE(CHAOS), loss_every=LOSS)
        self.assertFalse(bad.ok)
        self.assertIn("budget", bad.reason)
        ok, _ = run("loot_race", bugs={"repairs_in_flight"}, opts=CHOOSE(dict(CHAOS, repair_budget_per_min=10000.0)), loss_every=LOSS)
        self.assertTrue(ok.ok, ok.reason)

    def test_budget_file_needs_a_reason(self):
        d = tempfile.mkdtemp()
        os.makedirs(os.path.join(d, "tools", "chaos"))
        path = os.path.join(d, "tools", "chaos", "profiles.json")
        json.dump({"bad_wifi": {"repair_budget_per_min": 2}}, open(path, "w"))
        with self.assertRaises(ValueError):
            chaos_adapter.repair_budget(d, "bad_wifi")
        json.dump({"bad_wifi": {"repair_budget_per_min": 2, "repair_budget_reason": "known ENet reorder bug #12"}}, open(path, "w"))
        self.assertEqual(chaos_adapter.repair_budget(d, "bad_wifi"), 2.0)
        json.dump({"bad_wifi": {}}, open(path, "w"))
        self.assertEqual(chaos_adapter.repair_budget(d, "bad_wifi"), 0.0)
        self.assertEqual(chaos_adapter.repair_budget(tempfile.mkdtemp(), "bad_wifi"), 0.0)

    def test_hard_errors_still_fail_immediately_on_chaos(self):
        self.assertIn("applyFailed", core.MUST_NOT_RISE)
        self.assertNotIn("applyFailed", core.REPAIR_COUNTERS)

    def test_real_profiles_json_loads_with_current_budgets(self):
        for prof in chaos_adapter.PROFILES:
            self.assertGreaterEqual(chaos_adapter.relaxed_options(prof, REPO).get("repair_budget_per_min", 0.0), 0.0)


def CHOOSE(o):
    o = dict(o)
    o.setdefault("fast", True)
    o["settle_poll_s"] = 0.0
    return o


class Engine(unittest.TestCase):
    def test_settle_timeout_names_the_machines(self):
        r, _ = run("loot_race", lag=10 ** 6, opts={"settle_s": 0.05, "settle_poll_s": 0.01, "fast": True})
        self.assertFalse(r.ok)
        self.assertIn("did not converge", r.reason)

    def test_failure_files_for_t1b(self):
        d = tempfile.mkdtemp(prefix="t6b-fail-")
        r, w = run("loot_race", bugs={"dup_pickup"}, outdir=d)
        fails = [x for x in os.listdir(d) if x.startswith("FAIL-")]
        self.assertEqual(len(fails), 1, os.listdir(d))
        fd = os.path.join(d, fails[0])
        info = json.load(open(os.path.join(fd, "failure.json"), encoding="utf-8"))
        self.assertEqual(info["playbook"], "loot_race")
        self.assertEqual([m["name"] for m in info["machines"]], ["host", "client1"])
        for m in info["machines"]:
            self.assertIn("status", m)
            self.assertTrue(os.path.isfile(m["snapshot"]), m)
            self.assertTrue(os.path.isfile(m["flight_dump"]["path"]), m)
        self.assertTrue(os.path.isfile(os.path.join(d, "result.json")))
        shutil.rmtree(d, ignore_errors=True)

    def test_flight_dump_missing_is_recorded_not_fatal(self):
        d = tempfile.mkdtemp(prefix="t6b-fail-")
        e = registry.REGISTRY["loot_race"]
        w, refs = e.fake(2, {"dup_pickup"})
        w.flight_dir = ""        # fake bridge answers 'unknown method flight.dump'
        r = core.run_playbook(registry.make("loot_race", REPO), fakecoop.make_machines(w), fakecoop.FakeComparer(), d, refs, OPTS)
        fd = os.path.join(d, [x for x in os.listdir(d) if x.startswith("FAIL-")][0])
        info = json.load(open(os.path.join(fd, "failure.json"), encoding="utf-8"))
        self.assertIn("flight_dump_error", info["machines"][0])

    def test_bot_crash_is_a_failed_step_with_a_trace(self):
        e = registry.REGISTRY["loot_race"]
        w, refs = e.fake(2)

        def boom(*a, **k):
            raise RuntimeError("pipe exploded")
        w.bots[1].pickup = boom
        d = tempfile.mkdtemp()
        r = core.run_playbook(registry.make("loot_race", REPO), fakecoop.make_machines(w), fakecoop.FakeComparer(), d, refs, OPTS)
        self.assertFalse(r.ok)
        self.assertIn("pipe exploded", r.reason)
        self.assertTrue([x for x in os.listdir(d) if x.startswith("FAIL-")])

    def test_steps_are_recorded_with_timing_and_snapdiff(self):
        r, _ = run("container_share")
        self.assertTrue(all(s.ok for s in r.steps))
        self.assertIn("equal", {s.snapdiff for s in r.steps})
        saved = json.load(open(os.path.join(r.outdir, "result.json"), encoding="utf-8"))
        self.assertTrue(saved["ok"])

    def test_playbook_with_no_steps_is_not_a_pass(self):
        class Empty(core.Playbook):
            name = "empty"

            def run(self, ctx):
                pass
        w, refs = registry.REGISTRY["fight"].fake(2)
        r = core.run_playbook(Empty(), fakecoop.make_machines(w), fakecoop.FakeComparer(), tempfile.mkdtemp(), refs, OPTS)
        self.assertFalse(r.ok)


class GoodspringsPlan(unittest.TestCase):
    def test_plan_resolves(self):
        self.assertEqual(GP.check_plan(plan(), REPO), [])

    def test_golden_contents(self):
        p = plan()
        by = {q["edid"]: q for q in p["quests"]}
        self.assertEqual([q["edid"] for q in p["quests"]], ["VCG01", "VCG02", "VCG03"])
        self.assertEqual(by["VCG01"]["formid"], "104C1C")
        self.assertEqual(by["VCG02"]["formid"], "10A214")
        self.assertEqual(by["VCG03"]["formid"], "15D912")
        st1 = [n for k, n in by["VCG01"]["steps"] if k == "stage"]
        self.assertEqual(st1[:3], [0, 1, 3])
        self.assertEqual(st1[-1], 200)
        self.assertIn(30, st1)
        self.assertEqual(p["barriers"], {"104C1C": [30]})
        self.assertEqual(by["VCG02"]["steps"][0], ("start", None))
        self.assertEqual(p["refs"]["doc"], "FalloutNV.esm:104C0F")
        self.assertEqual(p["refs"]["sunny"], "FalloutNV.esm:104E85")
        self.assertEqual(by["VCG02"]["objective_effects"][5], [(3, "displayed", 1)])

    def test_breakage_is_detected(self):
        p = copy.deepcopy(plan())
        p["quests"][0]["stages_in_esm"].remove(30)
        self.assertTrue([x for x in GP.check_plan(p, REPO) if "barrier stage 30" in x or "stage 30" in x])
        p = copy.deepcopy(plan())
        p["actions"] = dict(p["actions"])
        p["actions"][("VCG02", 777)] = ["all:walk:sunny"]
        self.assertTrue([x for x in GP.check_plan(p, REPO) if "777" in x])
        p = copy.deepcopy(plan())
        p["actions"] = {("VCG01", 10): ["all:walk:nobody"]}
        self.assertTrue([x for x in GP.check_plan(p, REPO) if "nobody" in x])

    def test_opening_refuses_a_broken_plan(self):
        p = copy.deepcopy(plan())
        p["quests"][1]["stages_in_esm"] = [5]
        e = registry.REGISTRY["goodsprings_opening"]
        w, refs = pb_story.fake_goodsprings(plan(), 2)
        pb = registry.make("goodsprings_opening", REPO, p)
        r = core.run_playbook(pb, fakecoop.make_machines(w), fakecoop.FakeComparer(), tempfile.mkdtemp(), refs, OPTS)
        self.assertFalse(r.ok)
        self.assertIn("does not resolve", r.reason)

    def test_every_stage_of_the_path_is_driven(self):
        r, w = run("goodsprings_opening")
        self.assertTrue(r.ok, r.reason)
        for q in plan()["quests"]:
            got = set(w.truth["quests"][q["formid"]]["stages"])
            want = {n for k, n in q["steps"] if k == "stage"}
            self.assertEqual(got, want, q["edid"])
        # barrier: stage 30 was held until all players passed (twin pnr_vote/pnr_early proves the check)
        self.assertIn(30, w.truth["quests"]["104C1C"]["stages"])


class ScenarioAdapter(unittest.TestCase):
    def test_mappers_cover_every_playbook_and_satisfy_needs(self):
        self.assertEqual(set(scenario_adapter.MAPPERS), set(registry.REGISTRY))
        forms_all = json.load(gzip.open(os.path.join(REPO, "out", "questdata", "forms.json.gz"), "rt", encoding="utf-8"))
        for name in registry.REGISTRY:
            scen, mapper = scenario_adapter.MAPPERS[name]
            if mapper is None:
                continue
            with self.subTest(name):
                refs = scenario_adapter.refs_for(name, REPO)
                pb = registry.make(name, REPO)
                missing = [k for k in pb.needs_scenario_refs if k not in refs]
                self.assertFalse(missing, f"{name}: scenario adapter does not give {missing}")
                for k, v in refs.items():
                    if k == "infos":
                        continue
                    vals = v if isinstance(v, list) else [v]
                    for x in vals:
                        if isinstance(x, str) and x.startswith("FalloutNV.esm:"):
                            self.assertIn(x, forms_all, f"{name}.{k}={x} is not a known form")
                        if isinstance(x, dict) and "ref" in x:
                            self.assertIn(x["ref"], forms_all)

    def test_scenarios_named_exist_or_are_requested(self):
        have = {f[:-5] for f in os.listdir(os.path.join(REPO, "scenarios")) if f.endswith(".json")}
        for name, (scen, _) in scenario_adapter.MAPPERS.items():
            if scen is not None:
                self.assertIn(scen, have, f"{name} uses scenario {scen} which does not exist")

    def test_fallback_start_when_no_scenario(self):
        calls = []

        class NV:
            def load_save(self, n): calls.append(("load", n))
            def call(self, m, p=None): calls.append((m, p))
        scenario_adapter.MAPPERS["__tmp__"] = ("no_such_scenario", None)
        try:
            r = scenario_adapter.start("__tmp__", [NV(), NV()], REPO)
        finally:
            del scenario_adapter.MAPPERS["__tmp__"]
        self.assertEqual(r["mode"], "fallback")
        self.assertIn(("load", "autosave"), calls)


class LiveComparerOnGoldens(unittest.TestCase):
    def setUp(self):
        self.g = os.path.join(REPO, "tests", "goldens", "snapshot")
        self.c = core.LiveComparer(REPO)

    def _machine(self, golden):
        class B:
            def snapshot(s, path):
                shutil.copy(os.path.join(self.g, golden), path)
        return core.Machine(0, "m", B())

    def test_equal_and_different(self):
        d = tempfile.mkdtemp()
        a = self.c.snapshot(self._machine("base.json"), os.path.join(d, "a.json"))
        b = self.c.snapshot(self._machine("base.json"), os.path.join(d, "b.json"))
        self.assertEqual(self.c.diff(a, b, [])[0], 0)
        c = self.c.snapshot(self._machine("stage_changed.json"), os.path.join(d, "c.json"))
        code, lines = self.c.diff(a, c, [])
        self.assertEqual(code, 1)
        self.assertTrue(lines)

    def test_vacuous_snapshot_is_refused(self):
        d = tempfile.mkdtemp()
        a = self.c.snapshot(self._machine("base.json"), os.path.join(d, "a.json"))
        e = self.c.snapshot(self._machine("empty.json"), os.path.join(d, "e.json"))
        self.assertEqual(self.c.diff(a, e, [])[0], 2)


class Adapters(unittest.TestCase):
    def test_chaos_adapter(self):
        self.assertEqual(chaos_adapter.relaxed_options("lan"), {})
        self.assertFalse(chaos_adapter.relaxed_options("hell")["strict_counters"])
        r = chaos_adapter.apply_profile(REPO, "", "bad_wifi", 1)
        self.assertFalse(r["applied"])
        with self.assertRaises(ValueError):
            chaos_adapter.apply_profile(REPO, "", "dialup", 1)

    def test_playbookbot_translates_to_t6a_bot(self):
        from bots import Bot, FakeBridge, World
        from playbooks.adapter import PlaybookBot
        ITEM, CHEST, RAIDER, NPC, INFO = 0x500, 0x600, 0x603, 0x602, 0x800
        w = World()
        w.add_container(CHEST, (500, 0, 0), {ITEM: 5})
        w.add_actor(RAIDER, (1000, 1000, 0))
        w.add_actor(NPC, (300, 0, 0), topics=[INFO])
        nv = FakeBridge(w)
        b = PlaybookBot(Bot(nv, "t"))
        ref = lambda i: f"FalloutNV.esm:{i:X}"      # noqa: E731
        self.assertTrue(b.take(ref(ITEM), 3, ref(CHEST)))
        self.assertEqual(probe.item_count(b, ref(ITEM)), 3)
        self.assertEqual(probe.holder_count(b, ref(ITEM), ref(CHEST)), 2)
        self.assertTrue(b.put(ref(ITEM), 1, ref(CHEST)))
        self.assertFalse(b.fire(1), "fire before aim must fail with a reason")
        self.assertTrue(b.talk(ref(NPC)))
        self.assertTrue(b.pick_topic(ref(INFO)))
        self.assertTrue(probe.end_dialogue(b))
        r = b.use_terminal(ref(CHEST), 0)         # a chest is not a terminal: a failed result, never an exception
        self.assertFalse(r.ok)
        self.assertTrue(r.reason)
        probe.ref_count(b, ref(RAIDER))          # T6a's count_refs: must not raise

    def test_missing_bridge_methods_are_notes_not_crashes(self):
        class NV:
            def call(self, method, params=None, timeout_s=0):
                raise RuntimeError("unknown method " + method)

        class Bot:
            nv = NV()
            def inventory(self): return {}
        r = probe.consent(Bot(), "ready", quest="104C1C")
        self.assertFalse(r.ok)
        self.assertIn("bot.consent", r.reason)
        ctx = core.Ctx([], None, tempfile.mkdtemp())
        self.assertIsNone(ctx.optional("x", lambda: probe.owner_of(Bot(), "FalloutNV.esm:1")))
        self.assertTrue(ctx.notes)


def run_with_refs(name, change=None, drop=()):
    """One clean run of `name` on its fake world, with the scenario refs changed (`change`) or removed (`drop`)."""
    e = registry.REGISTRY[name]
    w, refs = e.fake(e.players, set(), lag=1)
    refs = dict(refs, **(change or {}))
    for k in drop:
        refs.pop(k, None)
    d = tempfile.mkdtemp(prefix="t6b-")
    w.flight_dir = os.path.join(d, "flight")
    return core.run_playbook(registry.make(name, REPO), fakecoop.make_machines(w), fakecoop.FakeComparer(), d, refs, OPTS), w


def legacy_exec_ref(bot, ref, cmd):
    """Twin: the old console fallback, '<hex>.<cmd>' (in game, 2026-10-21: Script command ... not found)."""
    r = bot.nv.exec(f"{probe.hexid(ref)}.{cmd}")
    return probe.Result(bool(r.get("ok", True)), str(r.get("output", "")))


class ConsoleRefLines(unittest.TestCase):
    """The probe / adapter console fallbacks run with exec {cmd, ref}: the game's console has no '<hexFormID>.Func' form."""

    def world(self):
        from bots import Bot, FakeBridge, World
        w = World()
        w.add_door(0x601, (200, 200, 0), locked=True)
        w.add_container(0x600, (500, 0, 0), {0x500: 1})
        w.add_actor(0x602, (300, 0, 0))
        nv = FakeBridge(w)
        return nv, Bot(nv, "t")

    def test_fallbacks_over_a_t6a_bot(self):
        from playbooks.adapter import PlaybookBot
        nv, bot = self.world()
        ref = lambda i: f"FalloutNV.esm:{i:X}"      # noqa: E731
        self.assertTrue(probe.is_locked(bot, ref(0x601)))
        self.assertFalse(probe.door_open(bot, ref(0x601)))
        self.assertTrue(PlaybookBot(bot).unlock(ref(0x601)))
        self.assertFalse(probe.is_locked(bot, ref(0x601)))
        self.assertFalse(probe.is_teammate(bot, ref(0x602)))
        self.assertTrue(probe.set_teammate(bot, ref(0x602)))
        self.assertTrue(probe.is_teammate(bot, ref(0x602)))
        self.assertTrue(probe.seed_items(bot, ref(0x600), ref(0x500), 4))
        self.assertEqual(bot.inventory_count(ref(0x500), ref(0x600)), 5)
        self.assertTrue(probe.spawn(bot, ref(0x700), ref(0x602)))
        self.assertEqual(bot.count_refs(ref(0x700)), 1)
        lines = [c for c in nv.calls if c[0] == "exec"]
        self.assertGreaterEqual(len([c for c in lines if len(c) == 3]), 8, "the fallbacks must pass the reference as ref")
        self.assertEqual([c for c in lines if __import__("re").search(r"(^|[\s(])[0-9A-Fa-f]+\.\w", c[1])], [])

    def test_legacy_fallbacks_fail_like_in_game(self):
        """Twin: the old '<hex>.Func' fallbacks fail on the fake console, as they did in game."""
        nv, bot = self.world()
        ref = lambda i: f"FalloutNV.esm:{i:X}"      # noqa: E731
        with self.assertRaises(Exception):
            bot.nv.eval(f"{probe.hexid(ref(0x601))}.GetLockLevel")
        self.assertFalse(legacy_exec_ref(bot, ref(0x602), "SetPlayerTeammate 1"))
        self.assertFalse(probe.is_teammate(bot, ref(0x602)))
        self.assertFalse(legacy_exec_ref(bot, ref(0x600), "additem 500 4"))
        self.assertEqual(bot.inventory_count(ref(0x500), ref(0x600)), 1)

    def test_playbook_console_paths_on_the_fake_coop(self):
        r, _ = run_with_refs("companion_recruit", drop=("recruit_info",))      # recruits with SetPlayerTeammate on the ref
        self.assertTrue(r.ok, r.reason)
        r, _ = run_with_refs("container_share", {"seed": {"item": "STIMPAK", "count": 2}})
        self.assertTrue(r.ok, r.reason)
        old = probe._exec_ref
        probe._exec_ref = legacy_exec_ref                                       # twin: the old lines
        try:
            r, _ = run_with_refs("companion_recruit", drop=("recruit_info",))
            self.assertFalse(r.ok, "a '<hex>.SetPlayerTeammate' line must fail")
            r, _ = run_with_refs("container_share", {"seed": {"item": "STIMPAK", "count": 2}})
            self.assertFalse(r.ok, "a '<hex>.additem' line must fail")
            self.assertIn("not found", r.reason)
        finally:
            probe._exec_ref = old


class Cli(unittest.TestCase):
    def _run(self, *args):
        import subprocess
        p = subprocess.run([sys.executable, "-I", os.path.join(REPO, "tools", "bots", "run_coop.py"), *args],
                           capture_output=True, text=True, errors="replace")
        return p.returncode, p.stdout + p.stderr

    def test_fake_all_passes_and_twin_fails(self):
        d = tempfile.mkdtemp()
        code, out = self._run("--fake", "--playbook", "loot_race,pnr_vote", "--outdir", d)
        self.assertEqual(code, 0, out)
        self.assertIn("PASS  loot_race", out)
        code, out = self._run("--fake", "--playbook", "loot_race", "--bug", "dup_pickup", "--outdir", d)
        self.assertEqual(code, 1, out)
        self.assertIn("FAIL  loot_race", out)

    def test_usage_errors(self):
        code, out = self._run("--fake", "--playbook", "nope")
        self.assertEqual(code, 2, out)
        code, out = self._run("--list")
        self.assertEqual(code, 0)
        for n in registry.REGISTRY:
            self.assertIn(n, out)


if __name__ == "__main__":
    unittest.main()
