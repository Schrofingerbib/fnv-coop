"""real_stage against a fake world (no game): the checks pass when both copies agree and fail on the failures the real game showed."""
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
import real_stage as rs  # noqa: E402


class World:
    def __init__(self, client_kill_lost=False, host_kill_lost=False, loot_dup=False, pos_drift=0.0, weak_lost=False):
        self.dead = {r: False for r in (rs.GANG1, rs.GANG2, rs.GECKO1, rs.GECKO2)}
        self.client_dead = dict(self.dead)       # the client's own view
        self.host_view = dict(self.dead)
        self.corpse = {0: 0, 1: 0}
        self.inv = {0: 0, 1: 0}
        self.client_kill_lost, self.host_kill_lost, self.loot_dup, self.pos_drift = client_kill_lost, host_kill_lost, loot_dup, pos_drift
        self.shared_corpse = 0
        self.refused_console_kills = 0
        self.weak = set()
        self.shots = 0
        self.fire_error = ""            # the shooter's fire() fails with this reason
        self.weak_lost = weak_lost      # the host's Health 1 never reaches the client's copy
        self.counters = False           # a build whose coop.loot answers with the menuless block
        self.blind = False              # ... and whose detector never sees a thing (what the benchmark of 2026-10-27 could not tell)
        self.ml = {0: {}, 1: {}}
        self.guest_gate = False         # the client's console `RemoveItem` on the corpse is dropped (a script mutation on a guest); `player.` lines still run
        self.helper = False             # the build has coop.loot {"grab"} (needs NVCOOP_TEST_HELPERS=1 in the real game)


class Nv:
    def __init__(self, w, i):
        self.w, self.i = w, i

    def view(self):
        return self.w.host_view if self.i == 0 else self.w.client_dead

    def eval(self, fn, ref=None):
        w = self.w
        if fn == "GetDead":
            return 1.0 if self.view()[ref] else 0.0
        if fn.startswith("GetPos"):
            return 100.0 + (w.pos_drift if self.i == 1 else 0.0)
        if fn.startswith("GetActorValue"):
            if ref in w.weak and (self.i == 0 or not w.weak_lost):
                return 1.0
            return 50.0
        if fn.startswith("GetItemCount"):
            return float(w.corpse[self.i])
        if fn.startswith("player.GetItemCount"):
            return float(w.inv[self.i])
        raise RuntimeError(fn)

    def take_one(self):
        """The corpse loses one on every copy (shared: the loot service arbitrates) and this player's inventory gains it."""
        w = self.w
        if min(w.corpse.values()) > 0:
            w.corpse = {k: max(0, v - 1) for k, v in w.corpse.items()}
            w.inv[self.i] += 1

    def call(self, method, params=None):
        w = self.w
        if method == "combat.status":
            return {"stats": {"engineHits": 3, "proceeded": 3 if self.i == 1 else 0, "lastGate": "proceed: victim has no owner yet"}}
        if method == "coop.loot" and params and "grab" in params:
            if not w.helper:
                raise RuntimeError("no such method")
            ph = params["grab"].get("phase", "both")
            if ph in ("both", "add"):
                self.take_one()
            return {"grab": True, "phase": ph}
        if method != "coop.loot" or not w.counters:
            raise RuntimeError("no such method / no menuless block")
        m = {k: w.ml[self.i].get(k, 0) for k in rs.LOOT_COUNTERS}
        m["known"] = 2
        return {"enabled": True, "containers": 3, "menuless": m}

    def exec(self, line, ref=None):
        w = self.w
        if line.startswith("RemoveItem") and w.counters and not w.blind:
            m = w.ml[self.i]
            m["seen"] = m.get("seen", 0) + 1
            m["arbitrated"] = m.get("arbitrated", 0) + 1
        if line == "Kill":
            if self.i == 1:      # the guest gate: a guest console line that is not "player." changes nothing (plugin/hooks/suppress_policy.cpp)
                w.refused_console_kills += 1
                return {"ok": True}
            self.view()[ref] = True
            if not w.host_kill_lost:
                w.client_dead[ref] = True
        elif line.startswith("SetAV Health"):
            w.weak.add(ref)
        elif line.startswith("AddItem"):
            w.corpse = {0: 3, 1: 3}
        elif line.startswith("RemoveItem") and self.i == 1 and w.guest_gate:
            pass                                    # dropped by the guest gate
        elif line.startswith("RemoveItem"):
            n = int(line.split()[2])
            if w.loot_dup:
                w.corpse[self.i] = max(0, w.corpse[self.i] - n)
            else:
                w.corpse = {k: max(0, v - n) for k, v in w.corpse.items()}
        elif line.startswith("player.AddItem"):
            w.inv[self.i] += 1
        return {"ok": True}


class Res:
    def __init__(self, ok, reason=""):
        self.ok, self.reason = ok, reason

    def __bool__(self):
        return self.ok


class Bot:
    def __init__(self, w, i):
        self.w, self.i = w, i
        self.nv = Nv(w, i)

    def goto_pos(self, x, y, z, cell=None, timeout_s=30.0, mode="walk"):
        return Res(True)

    def fire(self, ref=None, shots=1, expect_hit=False):
        """A shot from the client goes to the owner (the host): a weakened gecko dies there, the death comes back."""
        w = self.w
        if w.fire_error:
            return Res(False, w.fire_error)
        w.shots += 1
        if (ref in w.weak or w.shots >= 3) and not w.client_kill_lost:    # client_kill_lost: the hit never reaches the owner
            w.host_view[ref] = True
            w.client_dead[ref] = True
        return Res(True)


def run(fn, **kw):
    w = World(**kw)
    bots = [Bot(w, 0), Bot(w, 1)]
    return fn(bots, lambda s: None), bots


class RealStage(unittest.TestCase):
    def setUp(self):
        self._s, self._w, self._ww = rs.time.sleep, rs.KILL_WAIT_S, rs.WEAK_WAIT_S
        rs.WEAK_WAIT_S = 0.05
        rs.time.sleep = lambda s: None
        rs.KILL_WAIT_S = 0.3

    def tearDown(self):
        rs.time.sleep, rs.KILL_WAIT_S, rs.WEAK_WAIT_S = self._s, self._w, self._ww

    def fight(self, **kw):
        return run(rs.real_fight, **kw)[0]

    def test_fight_all_pass_when_both_copies_agree(self):
        r = self.fight()
        self.assertEqual(r["pass_pct"], 100.0, [s for s in r["steps"] if not s["ok"]])
        self.assertLess(r["metrics"]["kill_latency_s"], 1.0)

    def test_a_kill_that_never_reaches_the_other_copy_fails_and_scores_the_wait(self):
        r = self.fight(client_kill_lost=True)
        self.assertTrue(any("by the client" in s["name"] and not s["ok"] for s in r["steps"]))
        self.assertEqual(r["metrics"]["kill_latency_s"], rs.KILL_WAIT_S)

    def test_the_client_kills_by_shooting_not_by_console(self):
        """Regression (bench dino-20261026-174728): the client's console `Kill` on a host-owned gecko is refused by the guest gate, so the
        gecko stayed alive on both copies. The client must kill the way a player does."""
        w = World()
        bots = [Bot(w, 0), Bot(w, 1)]
        r = rs.real_fight(bots, lambda s: None)
        self.assertEqual(r["pass_pct"], 100.0, [s for s in r["steps"] if not s["ok"]])
        self.assertEqual(w.refused_console_kills, 0, "the client issued a console Kill")
        self.assertGreaterEqual(w.shots, 1)
        self.assertLess(r["metrics"]["kill_latency_s"], 1.0)

    def test_mutation_twin_a_console_kill_by_the_client_cannot_pass(self):
        """The old method against the same guest gate: nothing dies, the step fails."""
        w = World()
        bots = [Bot(w, 0), Bot(w, 1)]
        self.assertIsNone(rs.kill_and_time(bots[1], [bots[0]], rs.GECKO1))
        self.assertEqual(w.refused_console_kills, 1)
        old = rs.shoot_and_time
        rs.shoot_and_time = lambda shooter, others, ref, away_from=None: (rs.kill_and_time(shooter, others, ref), "console kill")
        try:
            r = rs.real_fight([Bot(World(), 0), Bot(World(), 1)], lambda s: None)
        finally:
            rs.shoot_and_time = old
        self.assertTrue(any("by the client" in s["name"] and not s["ok"] for s in r["steps"]))

    def test_a_shot_that_cannot_be_fired_says_why(self):
        w = World(); w.fire_error = "too_close"
        r = rs.real_fight([Bot(w, 0), Bot(w, 1)], lambda s: None)
        bad = [s for s in r["steps"] if "by the client" in s["name"]][0]
        self.assertFalse(bad["ok"])
        self.assertIn("too_close", bad["reason"])

    def test_the_dead_stay_dead_names_the_actor_that_got_up(self):
        w = World()
        bots = [Bot(w, 0), Bot(w, 1)]
        orig = rs.time.sleep
        def revive(s):      # the gang member gets up on the client after the kills
            w.client_dead[rs.GANG1] = False
        rs.time.sleep = revive
        try:
            r = rs.real_fight(bots, lambda s: None)
        finally:
            rs.time.sleep = orig
        bad = [s for s in r["steps"] if "stay dead" in s["name"]]
        self.assertTrue(bad and not bad[0]["ok"])
        self.assertIn(f"{rs.GANG1} on the client", bad[0]["reason"])

    def test_the_hosts_weakening_must_reach_the_client_before_it_shoots(self):
        """Bench dino-20261027-144058: the host sets the gecko to Health 1, nothing checked that the client's copy got it."""
        r = self.fight(weak_lost=True)
        bad = [s for s in r["steps"] if "weak on the client" in s["name"]]
        self.assertTrue(bad and not bad[0]["ok"])
        self.assertIn("still reads 50", bad[0]["reason"])
        ok = self.fight()
        self.assertTrue([s for s in ok["steps"] if "weak on the client" in s["name"] and s["ok"]])

    def test_a_failed_client_kill_carries_both_machines_combat_counters(self):
        r = self.fight(client_kill_lost=True)
        bad = [s for s in r["steps"] if "by the client" in s["name"]][0]
        self.assertFalse(bad["ok"])
        self.assertIn("client combat: engineHits=3 proceeded=3", bad["reason"])
        self.assertIn("victim has no owner yet", bad["reason"])

    def test_the_corpse_check_says_never_died_for_a_copy_that_did_not_die(self):
        r = self.fight(client_kill_lost=True)
        bad = [s for s in r["steps"] if "stay dead" in s["name"]]
        self.assertTrue(bad and not bad[0]["ok"])
        self.assertIn(f"never died: {rs.GECKO1} on the host", bad[0]["reason"])
        self.assertNotIn("alive again", bad[0]["reason"])

    def test_position_drift_beyond_150_units_fails(self):
        r = self.fight(pos_drift=400.0)
        self.assertTrue(any("within 150" in s["name"] and not s["ok"] for s in r["steps"]))
        self.assertAlmostEqual(r["metrics"]["npc_pos_err"], 400.0 * 3 ** 0.5, places=1)

    def test_loot_exactly_once_passes(self):
        w = World()
        bots = [Bot(w, 0), Bot(w, 1)]
        w.corpse = {0: 3, 1: 3}
        r = rs.real_loot(bots, lambda s: None)
        self.assertEqual(r["pass_pct"], 100.0, [s for s in r["steps"] if not s["ok"]])

    def test_loot_taken_twice_is_caught(self):
        w = World(loot_dup=True)
        bots = [Bot(w, 0), Bot(w, 1)]
        w.corpse = {0: 3, 1: 3}
        r = rs.real_loot(bots, lambda s: None)
        self.assertTrue(any("add up" in s["name"] and not s["ok"] for s in r["steps"]))
        self.assertGreater(r["metrics"]["loot_total_gain"], rs.LOOT_N)

    def test_the_loot_services_counters_are_read_into_the_report_and_the_failure_reason(self):
        w = World(loot_dup=True)
        w.counters = True
        bots = [Bot(w, 0), Bot(w, 1)]
        w.corpse = {0: 3, 1: 3}
        r = rs.real_loot(bots, lambda s: None)
        self.assertIn("host", r["metrics"]["loot_service"])
        self.assertGreater(r["metrics"]["loot_service"]["client"]["arbitrated"], 0)
        self.assertEqual(r["metrics"]["loot_service"]["client"]["known"], 2)
        bad = [s for s in r["steps"] if "add up" in s["name"]][0]
        self.assertFalse(bad["ok"])
        self.assertIn("arbitrated", bad["reason"])      # the failure says what the service did
        self.assertIn("host:", bad["reason"])
        self.assertEqual(len(r["steps"]), 4)

    def test_a_service_that_saw_nothing_fails_its_own_step_even_when_the_gains_add_up(self):
        w = World()
        w.counters = w.blind = True
        bots = [Bot(w, 0), Bot(w, 1)]
        w.corpse = {0: 3, 1: 3}
        r = rs.real_loot(bots, lambda s: None)
        saw = [s for s in r["steps"] if "saw the grabs" in s["name"]][0]
        self.assertFalse(saw["ok"])
        self.assertIn("saw 0 differences", saw["reason"])
        self.assertEqual(r["metrics"]["loot_service"]["host"]["seen"], 0)

    def test_a_build_without_the_counters_runs_the_old_three_steps(self):
        w = World()
        bots = [Bot(w, 0), Bot(w, 1)]
        w.corpse = {0: 3, 1: 3}
        r = rs.real_loot(bots, lambda s: None)
        self.assertEqual(len(r["steps"]), 3)
        self.assertNotIn("loot_service", r["metrics"])
        self.assertEqual(r["pass_pct"], 100.0)

    def test_mutation_twin_counters_that_are_never_read_hide_a_blind_service(self):
        w = World()
        w.counters = w.blind = True
        w.corpse = {0: 3, 1: 3}
        old = rs.loot_status
        rs.loot_status = lambda bot: None
        try:
            r = rs.real_loot([Bot(w, 0), Bot(w, 1)], lambda s: None)
        finally:
            rs.loot_status = old
        self.assertEqual(r["pass_pct"], 100.0)           # the twin: nothing notices that the detector saw nothing ...
        self.assertNotIn("loot_service", r["metrics"])
        w2 = World()
        w2.counters = w2.blind = True
        w2.corpse = {0: 3, 1: 3}
        self.assertLess(rs.real_loot([Bot(w2, 0), Bot(w2, 1)], lambda s: None)["pass_pct"], 100.0)   # ... the real reader does

    def test_the_report_prints_the_loot_service_table(self):
        sys.path.insert(0, str(HERE.parent))
        import coop_bench  # noqa: E402
        w = World()
        w.counters = True
        w.corpse = {0: 3, 1: 3}
        pb = rs.real_loot([Bot(w, 0), Bot(w, 1)], lambda s: None)
        L = coop_bench.report_extras([], {"phases": {}}, {"playbook_loot": pb})
        text = chr(10).join(L)
        self.assertIn("Loot service counters", text)
        self.assertIn("| host |", text)
        self.assertIn("| client |", text)

    def interleave(self):
        """The two grabbing threads must take turns (the other tests make sleep a no-op, and then one bot empties the corpse before the other starts)."""
        real = self._s
        rs.time.sleep = lambda s: real(0.004)

    def test_a_guests_console_grab_takes_nothing_from_the_corpse_which_is_how_the_bench_got_6(self):
        """Bench dino-20261027-144058: host gained 3, client gained 3. The client's `RemoveItem` on the corpse is dropped by the guest gate, only its `player.AddItem` ran."""
        w = World(); w.guest_gate = True
        w.corpse = {0: 3, 1: 3}
        self.interleave()
        r = rs.real_loot([Bot(w, 0), Bot(w, 1)], lambda s: None)
        bad = [s for s in r["steps"] if "add up" in s["name"]][0]
        self.assertFalse(bad["ok"])
        self.assertGreater(r["metrics"]["loot_total_gain"], rs.LOOT_N)
        self.assertEqual(r["metrics"]["grab_path"], {"host": "console", "client": "console"})
        self.assertIn("a guest drops the RemoveItem", bad["reason"])

    def test_the_helper_grab_takes_from_the_corpse_on_a_guest_too(self):
        w = World(); w.guest_gate = w.helper = True
        w.corpse = {0: 3, 1: 3}
        r = rs.real_loot([Bot(w, 0), Bot(w, 1)], lambda s: None)
        self.assertEqual(r["pass_pct"], 100.0, [s for s in r["steps"] if not s["ok"]])
        self.assertEqual(r["metrics"]["loot_total_gain"], rs.LOOT_N)
        self.assertEqual(r["metrics"]["grab_path"]["host"], "helper")
        self.assertIn(r["metrics"]["grab_path"]["client"], ("helper", None))     # None: the host emptied the corpse before the client's turn

    def test_mutation_twin_the_console_grab_on_a_guest_gives_the_client_the_item_for_free(self):
        w = World(); w.guest_gate = w.helper = True
        w.corpse = {0: 3, 1: 3}
        old = rs.grab_once

        def console_only(bot, k, state):
            state["path"] = "console"
            bot.nv.exec(f"RemoveItem {rs.LOOT_ITEM} 1 1", ref=rs.GANG1)
            bot.nv.exec(f"player.AddItem {rs.LOOT_ITEM} 1 1")
        rs.grab_once = console_only
        self.interleave()
        try:
            r = rs.real_loot([Bot(w, 0), Bot(w, 1)], lambda s: None)
        finally:
            rs.grab_once = old
        self.assertLess(r["pass_pct"], 100.0)
        self.assertGreater(r["metrics"]["loot_total_gain"], rs.LOOT_N)

    def test_an_error_is_reported_not_raised(self):
        class Broken(Bot):
            def __init__(self):
                self.nv = type("N", (), {"eval": lambda s, *a, **k: (_ for _ in ()).throw(RuntimeError("bridge down"))})()
        r = rs.real_fight([Broken(), Broken()], lambda s: None)
        self.assertFalse(r["ok"])
        self.assertIn("bridge down", r["error"])


if __name__ == "__main__":
    unittest.main()
