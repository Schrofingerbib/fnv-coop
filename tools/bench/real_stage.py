"""Fight and loot checks on REAL placed actors (same ref id on every machine), with numbers.

The stage is the Goodsprings gang spot from scenarios/goodsprings_enemies.json: two Powder Gangers and two geckos, all placed in the game's own files,
so every copy addresses them by the same ref id (refs made at run time get different ids per machine and the loot and spawn sync cannot match them).

Result shape = a playbook summary (steps, pass_pct) plus `metrics`, numbers the benchmark scores:
  kill_latency_s   seconds from a kill on one copy until the other copy shows the actor dead (worst of the kills)
  npc_pos_err      worst distance between the two copies' view of a living actor, over several samples
  hp_diff          worst health difference between the copies for a living actor
"""
import threading
import time

GANG1, GANG2 = "104C70", "104C77"
GECKO1, GECKO2 = "10A1FE", "10A1FD"
MARKER = "105D4C"
LOOT_ITEM = "13B2B2"      # duct tape: a MISC item no AI uses
LOOT_N = 3
AMMO, PISTOL = "08ED03", "0E3778"
KILL_WAIT_S = 10.0
SHOT_VOLLEYS = 8          # a client kill is shot: up to this many volleys (a shot can miss)
SHOT_WINDOW_S = 2.5       # after each volley, wait this long for the owner's death to show on the other copy
STANDOFF = 350.0          # the shooter stands this far from the target (aim_at refuses a target closer than 100 units)


class Result:
    def __init__(self, name):
        self.name, self.steps, self.metrics, self.t0 = name, [], {}, time.time()

    def check(self, name, ok, detail="", seconds=None):
        self.steps.append({"name": name, "ok": bool(ok), "seconds": seconds, "snapdiff": None, "digest_mismatch": 0, "reason": "" if ok else str(detail)[:420]})
        return bool(ok)

    def summary(self, error=None):
        n = len(self.steps)
        ok = sum(1 for s in self.steps if s["ok"])
        d = {"name": self.name, "ok": n > 0 and ok == n and not error, "steps": self.steps, "steps_total": n, "steps_ok": ok, "metrics": self.metrics,
             "pass_pct": round(100.0 * ok / n, 1) if n else 0.0, "snapdiff_equal": 0, "seconds": round(time.time() - self.t0, 1)}
        if error:
            d["error"] = str(error)[:160]
        return d


def num(bot, fn, ref=None):
    return float(bot.nv.eval(fn, ref=ref) if ref else bot.nv.eval(fn))


def dead(bot, ref):
    return num(bot, "GetDead", ref) != 0


def pos(bot, ref):
    return tuple(num(bot, f"GetPos {a}", ref) for a in "XYZ")


def dist(a, b):
    return sum((x - y) ** 2 for x, y in zip(a, b)) ** 0.5


def hp(bot, ref):
    return num(bot, "GetActorValue Health", ref)


def stage(bots, log):
    """Both copies: enable and revive the four actors, stand at the gang spot, safe health, a pistol and ammo. Returns what each step answered."""
    out = {}
    for i, b in enumerate(bots):
        e = b.nv.exec
        e("Enable", ref=MARKER)
        for r in (GANG1, GANG2, GECKO1, GECKO2):
            e("Enable", ref=r)
        for r in (GANG1, GANG2):
            e("Resurrect", ref=r)
        e(f"player.MoveTo {GANG1}")
        e("player.SetAV Health 5000")
        e(f"player.AddItem {AMMO} 300")
        e(f"player.AddItem {PISTOL} 1")
        e(f"player.EquipItem {PISTOL}")
        out[f"copy{i}"] = "ok"
    # the host (owner of the world) gives the first ganger something to loot
    left = count_on(bots[0], GANG1)
    if left > 0:
        bots[0].nv.exec(f"RemoveItem {LOOT_ITEM} {left} 1", ref=GANG1)
        time.sleep(1)
    bots[0].nv.exec(f"AddItem {LOOT_ITEM} {LOOT_N}", ref=GANG1)
    time.sleep(3)
    log("real stage ready")
    return out


def both_dead(bots, ref):
    return all(dead(b, ref) for b in bots)


def kill_and_time(killer, others, ref):
    """Run the console kill on `killer`; seconds until every other copy shows it dead (None = never within the wait).
    Only the host can do this: a guest's console `Kill` on an actor it does not own is refused on purpose (the guest gate,
    plugin/hooks/suppress_policy.cpp: only single "player." lines run on a guest). A client kills with shoot_and_time."""
    t0 = time.time()
    killer.nv.exec("Kill", ref=ref)
    while time.time() - t0 < KILL_WAIT_S:
        if all(dead(o, ref) for o in others):
            return round(time.time() - t0, 2)
        time.sleep(0.1)
    return None


def shoot_and_time(shooter, others, ref, away_from=None):
    """The way a player kills: stand off from `ref` and shoot it. The hit goes to the actor's owner (the host), the owner applies the
    damage and the death comes back. Returns (seconds from the volley that killed until every other copy shows it dead, None; or None and
    the reason it did not work). `away_from`: a ref to keep out of the line of fire (the shooter stands on the far side of `ref`)."""
    tp = pos(shooter, ref)
    if away_from:
        ap = pos(shooter, away_from)
        v = [t - a for t, a in zip(tp[:2], ap[:2])]
        n = (v[0] ** 2 + v[1] ** 2) ** 0.5 or 1.0
        ux, uy = v[0] / n, v[1] / n
    else:
        ux, uy = 1.0, 0.0
    r = shooter.goto_pos(tp[0] + STANDOFF * ux, tp[1] + STANDOFF * uy, tp[2], mode="teleport")
    if not r:
        return None, f"could not stand off from the target: {getattr(r, 'reason', r)}"
    why = "no volley fired"
    for _ in range(SHOT_VOLLEYS):
        t0 = time.time()
        r = shooter.fire(ref)
        if not r and "dead" not in str(getattr(r, "reason", "")):
            why = f"the shot failed: {getattr(r, 'reason', r)}"
            time.sleep(0.3)
            continue
        while time.time() - t0 < SHOT_WINDOW_S:
            if all(dead(o, ref) for o in others):
                return round(time.time() - t0, 2), ""
            time.sleep(0.1)
        why = f"{SHOT_VOLLEYS} volleys did not kill it" if r else why
    return None, why


def combat_words(bot):
    """The combat module's own counters and last decisions on one copy, short, for a failed kill (bridge method combat.status). Empty when unavailable."""
    try:
        st = bot.nv.call("combat.status")
    except Exception:   # noqa: BLE001
        return ""
    if not isinstance(st, dict):
        return ""
    c = st.get("stats", {}) or {}
    keys = ("engineHits", "proceeded", "clamped", "neutralized", "hitsSent", "hitsReceived", "hitsApplied", "hitsRejected", "hitsAbandoned",
            "hitsResolvedLate", "killsForced", "deathsDeclared")
    nums = " ".join(f"{k}={c[k]}" for k in keys if c.get(k))
    extra = "; ".join(f"{k}: {str(c[k])[:80]}" for k in ("lastGate", "rejections") if c.get(k))
    return (nums + ("; " + extra if extra else "")).strip()


WEAK_WAIT_S = 5.0         # the host's weakening has this long to show on the client's copy


def weakened_on(bot, ref, wait_s=None):
    """Did the weakening (health 1) reach this copy? Polls its health for up to `wait_s`; returns (yes, last health read)."""
    wait_s = WEAK_WAIT_S if wait_s is None else wait_s
    t0, h = time.time(), None
    while True:
        h = hp(bot, ref)
        if h <= 1.5:
            return True, h
        if time.time() - t0 >= wait_s:
            return False, h
        time.sleep(0.25)


def who_is_alive(bots, refs):
    """Names of the (actor, copy) pairs that read alive: 'gecko10A1FE on the client'."""
    return [f"{r} on the {n}" for r in refs for n, b in zip(("host", "client"), bots) if not dead(b, r)]


def real_fight(bots, log):
    host, client = bots
    res = Result("real_fight")
    try:
        live = (GANG1, GANG2, GECKO1, GECKO2)
        alive = [[not dead(b, r) for r in live] for b in bots]
        res.check("the four real actors start alive on both copies", all(all(a) for a in alive), f"alive host {alive[0]} client {alive[1]}")
        # positions and health agree while they live (the owner runs their AI, the other copy follows)
        errs, diffs = [], []
        for _ in range(6):
            for r in live:
                if not dead(host, r) and not dead(client, r):
                    errs.append(dist(pos(host, r), pos(client, r)))
                    diffs.append(abs(hp(host, r) - hp(client, r)))
            time.sleep(0.5)
        res.metrics["npc_pos_err"] = round(max(errs), 1) if errs else None
        res.metrics["hp_diff"] = round(max(diffs), 1) if diffs else None
        res.check("every living actor is within 150 units on the two copies", bool(errs) and max(errs) <= 150, f"worst {max(errs) if errs else None:.0f} units")
        res.check("their health agrees within 1", bool(diffs) and max(diffs) <= 1, f"worst difference {max(diffs) if diffs else None}")

        t_host = kill_and_time(host, [client], GANG1)
        res.check("a kill by the host shows dead on the client (ganger)", t_host is not None, f"still alive on the client after {KILL_WAIT_S:.0f} s", t_host)
        ok = not dead(host, GANG2) and not dead(client, GANG2)
        res.check("the other ganger is still alive on both copies", ok, "killed by mistake or desynced")
        # the host weakens the gecko so one hit is lethal (best effort: more volleys follow when it is not enough)
        try:
            host.nv.exec("SetAV Health 1", ref=GECKO1)
            time.sleep(1)
        except Exception as e:   # noqa: BLE001
            log(f"could not weaken the gecko: {e}")
        # the host's weakening has to be on the client's copy too before the client shoots (a world effect, not an owner snapshot)
        ok_weak, h_client = weakened_on(client, GECKO1)
        res.check("the weakened gecko is weak on the client too", ok_weak, f"the host set Health 1, the client's copy still reads {h_client:g} after 5 s")
        seen_dead = {"host": False, "client": False}
        t_client, why = shoot_and_time(client, [host], GECKO1, away_from=GECKO2)
        seen_dead["host"], seen_dead["client"] = dead(host, GECKO1), dead(client, GECKO1)
        words = "" if t_client is not None else f"; host combat: {combat_words(host)}; client combat: {combat_words(client)}"
        res.check("a kill by the client shows dead on the host (gecko)", t_client is not None,
                  f"{why}; still alive on the host (on the client itself: dead={seen_dead['client']}){words}", t_client)
        ok = not dead(host, GECKO2) and not dead(client, GECKO2)
        res.check("the other gecko is still alive on both copies", ok, "killed by mistake or desynced")
        lat = [t for t in (t_host, t_client) if t is not None]
        res.metrics["kill_latency_s"] = max(lat) if len(lat) == 2 else KILL_WAIT_S
        time.sleep(2)
        up = who_is_alive(bots, (GANG1, GECKO1))
        # "alive again" only for a copy that was seen dead; a copy that never died is said so (the earlier kill step failed, nothing got up)
        seen = {f"{GANG1} on the host": t_host is not None, f"{GANG1} on the client": t_host is not None,
                f"{GECKO1} on the host": t_client is not None, f"{GECKO1} on the client": seen_dead["client"] or t_client is not None}
        again = [u for u in up if seen.get(u)]
        never = [u for u in up if not seen.get(u)]
        why_up = "; ".join(x for x in (f"read alive again: {', '.join(again)}" if again else "", f"never died: {', '.join(never)}" if never else "") if x)
        res.check("the dead stay dead on both copies", not up, why_up)
    except Exception as e:   # noqa: BLE001
        log(f"real fight failed: {e}")
        return res.summary(error=e)
    log(f"real fight: {res.summary()['steps_ok']}/{len(res.steps)} checks, metrics {res.metrics}")
    return res.summary()


# What the loot service of one copy says it did (coop.loot -> menuless block, plugin/loot LootSync::statusJson). Counters grow; `known` is a gauge.
LOOT_COUNTERS = ("scans", "seen", "suspects", "arbitrated", "graceExpired", "setsDeferred", "countFails", "reverted", "revertedItems", "refused", "busy", "rollbackItems")


def loot_status(bot):
    """{counter: value, 'known': n} of the loot service on this copy, or None when it cannot be read (a build without the menuless block, no session)."""
    try:
        j = bot.nv.call("coop.loot", {})
        if isinstance(j, str):
            import json
            j = json.loads(j)
        m = j.get("menuless") if isinstance(j, dict) else None
        if not isinstance(m, dict):
            return None
        out = {k: int(m.get(k, 0)) for k in LOOT_COUNTERS}
        out["known"] = int(m.get("known", 0))
        out["containers"] = int(j.get("containers", 0))
        return out
    except Exception:   # noqa: BLE001
        return None


def loot_delta(before, after):
    """Per-copy counters grown during the step; None when either read failed."""
    if before is None or after is None:
        return None
    d = {k: after[k] - before[k] for k in LOOT_COUNTERS}
    d["known"], d["containers"] = after["known"], after["containers"]
    return d


def loot_line(delta):
    names = ("host", "client")
    parts = []
    for n, d in zip(names, delta):
        if d is None:
            parts.append(f"{n}: counters unavailable")
        else:
            parts.append(f"{n}: containers {d['containers']} scanned-list {d['known']} scans {d['scans']} seen {d['seen']} arbitrated {d['arbitrated']} reverted {d['reverted']} "
                         f"({d['revertedItems']} items) refused {d['refused']} busy {d['busy']} deferred {d['setsDeferred']} graceExpired {d['graceExpired']} countFails {d['countFails']}")
    return "; ".join(parts)


def count_on(bot, ref):
    return int(num(bot, f"GetItemCount {LOOT_ITEM}", ref))


def grab_once(bot, k, state):
    """One take of LOOT_ITEM from the corpse by this player, the way a quick-loot style mod does it (container -1, player +1, outside the container menu).

    Console lines cannot do that on a GUEST: `RemoveItem <item> 1 1` with the corpse as calling reference is a script mutation and the guest gate drops it
    (plugin/hooks/suppress_policy.cpp; only "player." lines run), so the client's corpse stayed full while `player.AddItem` still gave the client the item
    (bench dino-20261027-144058: host gained 3, client gained 3, and the loot service never saw a difference on the client). So the take goes through the
    loot module's test helper (coop.loot {"grab"}, needs NVCOOP_TEST_HELPERS=1): even rounds in one call, odd rounds as two calls (remove, then add).
    A build without the helper falls back to the console lines (state["path"] = "console")."""
    if state.get("path") != "console":
        g = {"container": GANG1, "item": LOOT_ITEM, "count": 1}
        try:
            if k % 2 == 0:
                bot.nv.call("coop.loot", {"grab": dict(g, phase="both")})
            else:
                bot.nv.call("coop.loot", {"grab": dict(g, phase="remove")})
                time.sleep(0.03)
                bot.nv.call("coop.loot", {"grab": dict(g, phase="add")})
            state["path"] = "helper"
            return
        except Exception as e:   # noqa: BLE001
            if state.get("path") == "helper":
                raise
            state["path"], state["why"] = "console", str(e)[:100]
    bot.nv.exec(f"RemoveItem {LOOT_ITEM} 1 1", ref=GANG1)      # this build has no RemoveItemTarget: take = remove from the corpse + add to the player
    bot.nv.exec(f"player.AddItem {LOOT_ITEM} 1 1")


def real_loot(bots, log):
    """Needs real_fight to have killed GANG1 (it carries LOOT_N duct tape). Both players take from the corpse at the same moment."""
    res = Result("real_loot")
    try:
        c = [count_on(b, GANG1) for b in bots]
        res.check(f"the corpse shows {LOOT_N} duct tape on both copies", c == [LOOT_N, LOOT_N], f"host {c[0]} client {c[1]}")
        have0 = [int(num(b, f"player.GetItemCount {LOOT_ITEM}")) for b in bots]
        st0 = [loot_status(b) for b in bots]
        got = [0, 0]
        paths = [{}, {}]

        def grab(i):
            b = bots[i]
            for k in range(LOOT_N + 2):
                if count_on(b, GANG1) <= 0:
                    return
                try:
                    grab_once(b, k, paths[i])
                    got[i] += 1
                except Exception:   # noqa: BLE001
                    pass
                time.sleep(0.15)
        ts = [threading.Thread(target=grab, args=(i,), daemon=True) for i in (0, 1)]
        [t.start() for t in ts]
        [t.join(timeout=60) for t in ts]
        time.sleep(3)
        gain = [int(num(b, f"player.GetItemCount {LOOT_ITEM}")) - h for b, h in zip(bots, have0)]
        res.metrics["loot_total_gain"] = sum(gain)
        delta = [loot_delta(a, loot_status(b)) for a, b in zip(st0, bots)]
        counters = all(d is not None for d in delta)
        if any(d is not None for d in delta):
            res.metrics["loot_service"] = {n: d for n, d in zip(("host", "client"), delta)}
        res.metrics["grab_path"] = {"host": paths[0].get("path"), "client": paths[1].get("path")}
        how = f"grab path host={paths[0].get('path')} client={paths[1].get('path')}"
        if "console" in res.metrics["grab_path"].values():
            how += " (console: a guest drops the RemoveItem on the corpse, only its player.AddItem runs)"
        why = f"host gained {gain[0]}, client gained {gain[1]} (total {sum(gain)}); {how}; loot service: {loot_line(delta)}"
        res.check(f"both grab at once: the gains add up to exactly {LOOT_N}", sum(gain) == LOOT_N, why)
        if counters:   # a build with the counters: the service must at least have SEEN the grabs (otherwise the detector never looked at the corpse)
            seen, arb = sum(d["seen"] for d in delta), sum(d["arbitrated"] for d in delta)
            res.check("the loot service saw the grabs and arbitrated them", seen > 0 and arb > 0,
                      f"detector saw {seen} differences and arbitrated {arb} transfers; {loot_line(delta)}")
        left = [count_on(b, GANG1) for b in bots]
        res.check("the corpse is empty on both copies", left == [0, 0], f"left host {left[0]} client {left[1]}")
    except Exception as e:   # noqa: BLE001
        log(f"real loot failed: {e}")
        return res.summary(error=e)
    log(f"real loot: {res.summary()['steps_ok']}/{len(res.steps)} checks, metrics {res.metrics}")
    return res.summary()
