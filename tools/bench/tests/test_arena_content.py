"""arena_content against a fake world (no game): checks pass when both copies agree and fail when a copy misses an event."""
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
import arena as arena_mod  # noqa: E402
import arena_content as ac  # noqa: E402


class World:
    """Shared truth for the fake copies. `blind` names a copy that never sees the host's kills or the host's spawns."""

    def __init__(self, blind_kills=False, blind_spawns=False, dup_loot=False):
        self.actors, self.items = [], []          # [id, alive], ids
        self.blind_kills, self.blind_spawns, self.dup_loot = blind_kills, blind_spawns, dup_loot
        self.next = 1
        self.inv = {0: 0, 1: 0}
        self.refused_console_kills = 0
        self.shots = 0
        self.fire_error = ""


class Nv:
    def __init__(self, w, idx):
        self.w, self.idx = w, idx

    def eval(self, fn, ref=None):
        if fn.startswith("player.GetItemCount"):
            return float(self.w.inv[self.idx])
        for a in self.w.actors:
            if a[0] == ref:
                return 0.0 if (a[1] or (self.idx == 1 and self.w.blind_kills)) else 1.0
        raise RuntimeError("bad ref")

    def exec(self, line, ref=None):
        w = self.w
        if "PlaceAtMe" in line:
            base = line.split()[1]
            if base == ac.hexid(ac.ENEMY_BASE):
                w.actors.append([w.next, True])
            else:
                w.items.append(w.next)
            w.next += 1
        elif line.startswith("Activate"):
            if ref in w.items:
                if not w.dup_loot:
                    w.items.remove(ref)
                w.inv[self.idx] += 1
        elif line.startswith("KillActor") and self.idx == 1:
            w.refused_console_kills += 1      # the guest gate: a guest console line that is not "player." changes nothing
        elif line.startswith("KillActor"):
            for a in w.actors:
                if a[0] == ref:
                    a[1] = False
        return {"ok": True}


class Res:
    def __init__(self, ok, reason=""):
        self.ok, self.reason = ok, reason

    def __bool__(self):
        return self.ok


class Bot:
    def __init__(self, w, idx):
        self.w, self.idx, self.nv = w, idx, Nv(w, idx)

    def pos(self, ref=None):
        return (700.0, 0.0, 0.0)

    def goto_pos(self, x, y, z, cell=None, timeout_s=30.0, mode="walk"):
        return Res(True)

    def fire(self, ref=None, shots=1, expect_hit=False):
        """A shot from the client goes to the owner, which kills the actor there (the death replicates)."""
        if self.w.fire_error:
            return Res(False, self.w.fire_error)
        self.w.shots += 1
        for a in self.w.actors:
            if a[0] == ref:
                a[1] = False
        return Res(True)

    def _actors(self):
        if self.idx == 1 and self.w.blind_spawns:
            return []
        return self.w.actors

    def refs_of(self, base):
        if base == ac.ENEMY_BASE:
            return [a[0] for a in self._actors()]
        return list(self.w.items)

    def count_refs(self, base):
        return len(self.refs_of(base))

    def target_alive(self, ref):
        for a in self.w.actors:
            if a[0] == ref:
                return a[1] or (self.idx == 1 and self.w.blind_kills)
        return False

    def pickup(self, ref):
        if ref in self.w.items:
            self.w.items.remove(ref) if not self.w.dup_loot else None
            self.w.inv[self.idx] += 1
        return True

    def inventory(self):
        return {ac.hexid(ac.LOOT_BASE): self.w.inv[self.idx]}


def run(fn, **kw):
    w = World(**kw)
    bots = [Bot(w, 0), Bot(w, 1)]
    return fn(bots, arena_mod.Arena(origin=(0, 0, 0)), lambda s: None)


class ArenaContent(unittest.TestCase):
    def setUp(self):
        self._w, self._k, self._s = ac.SPAWN_WAIT_S, ac.KILL_WAIT_S, ac.time.sleep
        ac.SPAWN_WAIT_S = ac.KILL_WAIT_S = 0.3
        ac.time.sleep = lambda s: None

    def tearDown(self):
        ac.SPAWN_WAIT_S, ac.KILL_WAIT_S = self._w, self._k
        ac.time.sleep = self._s

    def test_fight_passes_when_both_copies_agree(self):
        r = run(ac.arena_fight)
        self.assertEqual(r["pass_pct"], 100.0, [s for s in r["steps"] if not s["ok"]])

    def test_the_client_kills_by_shooting_not_by_console(self):
        """Regression: the client's console KillActor on host-owned enemies is refused by the guest gate, so its 2 kills never happened."""
        w = World()
        bots = [Bot(w, 0), Bot(w, 1)]
        r = ac.arena_fight(bots, arena_mod.Arena(origin=(0, 0, 0)), lambda s: None)
        self.assertEqual(r["pass_pct"], 100.0, [s for s in r["steps"] if not s["ok"]])
        self.assertEqual(w.refused_console_kills, 0)
        self.assertEqual(w.shots, 2)

    def test_mutation_twin_a_console_kill_by_the_client_fails_the_step(self):
        w = World()
        bots = [Bot(w, 0), Bot(w, 1)]
        old = ac.shoot_down
        ac.shoot_down = lambda shooter, owner, base, ref, origin, log=None: shooter.nv.exec("KillActor", ref=ref) and ""
        try:
            r = ac.arena_fight(bots, arena_mod.Arena(origin=(0, 0, 0)), lambda s: None)
        finally:
            ac.shoot_down = old
        self.assertGreater(w.refused_console_kills, 0)
        self.assertTrue(any("by the client" in s["name"] and not s["ok"] for s in r["steps"]))

    def test_a_shot_that_cannot_be_fired_says_why(self):
        w = World()
        w.fire_error = "too_close"
        r = ac.arena_fight([Bot(w, 0), Bot(w, 1)], arena_mod.Arena(origin=(0, 0, 0)), lambda s: None)
        bad = [s for s in r["steps"] if "by the client" in s["name"]][0]
        self.assertFalse(bad["ok"])

    def test_fight_fails_when_a_copy_never_sees_the_kills(self):
        r = run(ac.arena_fight, blind_kills=True)
        self.assertLess(r["pass_pct"], 100.0)
        self.assertTrue(any("dead on BOTH" in s["name"] and not s["ok"] for s in r["steps"]))

    def test_fight_fails_when_a_copy_never_sees_the_spawns(self):
        r = run(ac.arena_fight, blind_spawns=True)
        self.assertTrue(any("appear on BOTH" in s["name"] and not s["ok"] for s in r["steps"]))

    def test_loot_passes_when_each_item_goes_to_one_player(self):
        r = run(ac.arena_loot)
        self.assertEqual(r["pass_pct"], 100.0, [s for s in r["steps"] if not s["ok"]])

    def test_loot_fails_when_items_are_duplicated(self):
        r = run(ac.arena_loot, dup_loot=True)
        self.assertTrue(any("add up" in s["name"] and not s["ok"] for s in r["steps"]))

    def test_an_error_is_reported_not_raised(self):
        class Broken(Bot):
            def count_refs(self, base):
                raise RuntimeError("bridge down")
        w = World()
        r = ac.arena_fight([Broken(w, 0), Broken(w, 1)], arena_mod.Arena(origin=(0, 0, 0)), lambda s: None)
        self.assertFalse(r["ok"])
        self.assertIn("bridge down", r["error"])


if __name__ == "__main__":
    unittest.main()
