"""Arena content with known right answers: an enemy ring and a pile of loot built at runtime in the arena cell.

Each check is PASS or FAIL with the numbers behind it, and the result has the same shape as a playbook summary, so the benchmark's scoring
and report treat it like one (extras["playbook_fight"], extras["playbook_loot"]).

Created refs have different runtime ids on each machine, so every check compares COUNTS (how many live refs of a base form exist, how many of
them are alive, how many of an item each player holds), never ref ids across machines.
"""
import time

ENEMY_BASE = "FalloutNV.esm:104DF1"     # a Powder Ganger (NPC_)
LOOT_BASE = "FalloutNV.esm:015169"      # a Stimpak: a plain world item that is picked up whole
N_ENEMIES = 4
N_LOOT = 4
SPAWN_WAIT_S = 20.0
KILL_WAIT_S = 20.0
STANDOFF = 350.0          # the client shoots from this far outside the ring (aim_at refuses a target closer than 100 units)
SHOT_VOLLEYS = 12         # per victim; a shot can miss and these enemies are not weakened
SHOT_WINDOW_S = 2.5       # after each volley, wait this long for the owner's death to show on the host


def hexid(ref):
    return ref.split(":")[-1].lstrip("0") or "0"


class Result:
    """A playbook-shaped summary built from named checks."""

    def __init__(self, name):
        self.name, self.steps, self.t0 = name, [], time.time()

    def check(self, name, ok, detail="", seconds=None):
        self.steps.append({"name": name, "ok": bool(ok), "seconds": seconds, "snapdiff": None, "digest_mismatch": 0, "reason": "" if ok else str(detail)[:140]})
        return bool(ok)

    def summary(self, error=None):
        n = len(self.steps)
        ok = sum(1 for s in self.steps if s["ok"])
        d = {"name": self.name, "ok": n > 0 and ok == n and not error, "steps": self.steps, "steps_total": n, "steps_ok": ok,
             "pass_pct": round(100.0 * ok / n, 1) if n else 0.0, "snapdiff_equal": 0, "seconds": round(time.time() - self.t0, 1)}
        if error:
            d["error"] = str(error)[:160]
        return d


def wait_until(fn, timeout_s, poll_s=0.5):
    end = time.time() + timeout_s
    last = None
    while time.time() < end:
        last = fn()
        if last[0]:
            return last
        time.sleep(poll_s)
    return last if last is not None else (False, None)


def live_refs(bot, base):
    return list(bot.refs_of(base) or [])


def count(bot, base):
    return int(bot.count_refs(base))


def is_alive(bot, ref):
    """GetDead through the console with the ref as the calling reference (created refs are not known to bot.target_alive)."""
    return float(bot.nv.eval("GetDead", ref=ref)) == 0


def alive_count(bot, base):
    return sum(1 for r in live_refs(bot, base) if is_alive(bot, r))


def both(bots, fn):
    return [fn(b) for b in bots]


# ------------------------------------------------------------------ fight
def make_player_safe(bots):
    for b in bots:
        try:
            b.nv.exec("player.SetAV Health 9999")
        except Exception:   # noqa: BLE001
            pass


def spawn_ring(host, arena, base, n):
    """The host places n of `base` at its own position, then moves each new one to a point on the ring around the arena origin."""
    before = set(live_refs(host, base))
    for _ in range(n):
        host.nv.exec(f"player.PlaceAtMe {hexid(base)} 1")
    time.sleep(1.0)
    new = [r for r in live_refs(host, base) if r not in before]
    pts = arena.ring(max(1, len(new)))
    for r, (x, y, z) in zip(new, pts):
        for axis, v in (("X", x), ("Y", y), ("Z", z)):
            host.nv.exec(f"SetPos {axis} {v:.0f}", ref=r)
    return new


def shoot_down(shooter, owner, base, ref, origin, log=None):
    """The way a player kills: stand off outside the ring and shoot `ref` until the owner (the host) shows one more of `base` dead.
    A guest's console KillActor on an actor it does not own is refused on purpose (guest gate, plugin/hooks/suppress_policy.cpp), so a
    console kill by the client proves nothing. Returns "" when it died, else why not."""
    alive0 = alive_count(owner, base)
    tp = shooter.pos(ref)
    v = [tp[0] - origin[0], tp[1] - origin[1]] if origin else [1.0, 0.0]
    n = (v[0] ** 2 + v[1] ** 2) ** 0.5 or 1.0
    if not (v[0] or v[1]):
        v, n = [1.0, 0.0], 1.0
    r = shooter.goto_pos(tp[0] + STANDOFF * v[0] / n, tp[1] + STANDOFF * v[1] / n, tp[2], mode="teleport")
    if not r:
        return f"could not stand off from the target: {getattr(r, 'reason', r)}"
    why = "no volley fired"
    for _ in range(SHOT_VOLLEYS):
        r = shooter.fire(ref)
        if not r and "dead" not in str(getattr(r, "reason", "")):
            why = f"the shot failed: {getattr(r, 'reason', r)}"
            time.sleep(0.3)
            continue
        t0 = time.time()
        while time.time() - t0 < SHOT_WINDOW_S:
            if alive_count(owner, base) < alive0:
                return ""
            time.sleep(0.1)
        why = f"{SHOT_VOLLEYS} volleys did not kill it"
    return why


def arena_fight(bots, arena, log, enemy_base=ENEMY_BASE, n=N_ENEMIES):
    host, client = bots[0], bots[1]
    res = Result("arena_fight")
    try:
        make_player_safe(bots)
        n0 = both(bots, lambda b: count(b, enemy_base))
        a0 = both(bots, lambda b: alive_count(b, enemy_base))
        res.check("the arena starts without extra enemies of that kind", n0[0] == n0[1], f"counts host/client {n0}")
        spawn_ring(host, arena, enemy_base, n)
        t = time.time()
        ok, got = wait_until(lambda: (lambda c: (all(v == n0[0] + n for v in c), c))(both(bots, lambda b: count(b, enemy_base))), SPAWN_WAIT_S)
        res.check(f"{n} enemies on the ring appear on BOTH copies, no more no fewer", ok, f"counts host/client {got} (want {n0[0] + n} each)",
                  round(time.time() - t, 1))
        time.sleep(5)
        c = both(bots, lambda b: count(b, enemy_base))
        res.check("still the same number after 5 quiet seconds (no clone, no second roll)", all(v == n0[0] + n for v in c), f"counts {c}")
        al = both(bots, lambda b: alive_count(b, enemy_base))
        res.check("every new enemy is alive on both copies", all(v == a0[0] + n for v in al), f"alive host/client {al} (want {a0[0] + n})")

        # the host kills all but two (console); the client shoots the rest (a console kill by a guest is refused)
        host_refs = [r for r in live_refs(host, enemy_base) if is_alive(host, r)]
        for r in host_refs[: max(0, n - 2)]:
            host.nv.exec("KillActor", ref=r)
        t = time.time()
        want = a0[0] + 2
        ok, got = wait_until(lambda: (lambda c: (all(v == want for v in c), c))(both(bots, lambda b: alive_count(b, enemy_base))), KILL_WAIT_S)
        res.check(f"{max(0, n - 2)} kills by the host show as dead on BOTH copies", ok, f"alive host/client {got} (want {want})", round(time.time() - t, 1))
        t = time.time()
        whys = [shoot_down(client, host, enemy_base, r, arena.origin, log)
                for r in [r for r in live_refs(client, enemy_base) if is_alive(client, r)][:2]]
        ok, got = wait_until(lambda: (lambda c: (all(v == a0[0] for v in c), c))(both(bots, lambda b: alive_count(b, enemy_base))), KILL_WAIT_S)
        res.check("2 kills by the client (shot) show as dead on BOTH copies", ok,
                  f"alive host/client {got} (want {a0[0]}); {'; '.join(w for w in whys if w)}", round(time.time() - t, 1))
        time.sleep(3)
        c = both(bots, lambda b: count(b, enemy_base))
        res.check("the corpses stay one each (nothing duplicated by the deaths)", all(v == n0[0] + n for v in c), f"counts {c}")
    except Exception as e:   # noqa: BLE001
        log(f"arena fight failed: {e}")
        return res.summary(error=e)
    log(f"arena fight: {res.summary()['steps_ok']}/{len(res.steps)} checks passed")
    return res.summary()


# ------------------------------------------------------------------ loot
def item_total(bot, base):
    """How many of the item this copy's own player holds (console GetItemCount)."""
    return int(float(bot.nv.eval(f"player.GetItemCount {hexid(base)}")))


def arena_loot(bots, arena, log, loot_base=LOOT_BASE, n=N_LOOT):
    host, client = bots[0], bots[1]
    res = Result("arena_loot")
    try:
        n0 = both(bots, lambda b: count(b, loot_base))
        res.check("the arena starts without extra loot of that kind", n0[0] == n0[1], f"counts host/client {n0}")
        have0 = both(bots, lambda b: item_total(b, loot_base))
        for _ in range(n):
            host.nv.exec(f"player.PlaceAtMe {hexid(loot_base)} 1")
        t = time.time()
        ok, got = wait_until(lambda: (lambda c: (all(v == n0[0] + n for v in c), c))(both(bots, lambda b: count(b, loot_base))), SPAWN_WAIT_S)
        res.check(f"{n} items on the ground appear on BOTH copies exactly once", ok, f"counts host/client {got} (want {n0[0] + n})", round(time.time() - t, 1))

        # both players grab everything at the same moment: each item must go to exactly one player
        import threading

        def grab(b):
            for r in live_refs(b, loot_base):
                try:
                    b.nv.exec("Activate player", ref=r)
                except Exception:   # noqa: BLE001
                    pass
        ts = [threading.Thread(target=grab, args=(b,), daemon=True) for b in bots]
        [x.start() for x in ts]
        [x.join(timeout=60) for x in ts]
        time.sleep(3)
        gain = [item_total(b, loot_base) - h for b, h in zip(bots, have0)]
        res.check(f"{n} items grabbed by both at once: they add up to exactly {n} (no duplicate, nothing lost)", sum(gain) == n, f"host gained {gain[0]}, client gained {gain[1]}")
        ok, got = wait_until(lambda: (lambda c: (all(v == n0[0] for v in c), c))(both(bots, lambda b: count(b, loot_base))), KILL_WAIT_S)
        res.check("the ground is empty again on BOTH copies", ok, f"counts host/client {got} (want {n0[0]})")
    except Exception as e:   # noqa: BLE001
        log(f"arena loot failed: {e}")
        return res.summary(error=e)
    log(f"arena loot: {res.summary()['steps_ok']}/{len(res.steps)} checks passed")
    return res.summary()
