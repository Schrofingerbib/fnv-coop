#!/usr/bin/env python3
"""In-game proof of spawn-site arbitration (FULL-SYNC-PLAN 3.3): an encounter appears ONCE, dies ONCE, comes back only when due.

NOT RUN by the lane that wrote it (code only). The helper runs it on a PC where two or three game instances are already in one co-op
session (host = instance 0), standing within about 500 units of each other in the same exterior cell, with nvcoop built with
NVCOOP_WITH_SPAWN and the wiring in plugin/spawn/spawn_glue.h done (docs/tools/spawn-arb.md, "In-game proof").

    python -I plugin/spawn/tools/spawn_proof.py --instances 0 1 [2] [--base 0001CF7C] [--type 60] [--respawn]
    python -I plugin/spawn/tools/spawn_proof.py --selftest          # game-free: the script's own pass / fail logic against a fake world

How the encounter is made: the console command  player.PlaceAtMe <base> 1  on a CLIENT makes a dynamic actor nobody announced, which is
exactly what an engine-made random encounter looks like to the plugin (the actor feed finds a 0xFF actor no SpawnRef explains). If the
guest hook refuses console PlaceAtMe (docs/tools/spawn-arb.md, open item 1), step 1 fails with "no actor appeared" and the fallback is to
walk into a real random encounter or a leveled marker; the rest of the script is unchanged.

Steps (each prints PASS / FAIL with the numbers it saw):
  1 module     coop.spawn answers on every instance, link up, same digest
  2 once       one client rolls an encounter: every instance ends with exactly +1 actor, and the site is Spawned with the same owner / epoch
  3 race       two clients roll the same spot at the same moment: still exactly +1 on every instance (the second roll is thrown away)
  4 death      the actor is killed once: the site is Cleared on every instance
  5 no respawn a client's engine rolls the spot again right away: nothing new appears (suppressed), the site stays Cleared
  6 respawn    (--respawn) the host moves game time on by 4 days: the site becomes Respawnable and the next roll counts (+1 again)
Exit code 0 only if every step passed.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

# The base is a hex FormID, never an editor id: the bridge's console does not resolve editor ids (internal lane notes), and FalloutNV.esm has no
# creature with the EditorID "RadRoach" anyway (the radroach is CrRadroach, 0001CF7C). The sweep of 2026-10-22 rolled "RadRoach": the line made
# nothing on any copy and step 2 read "exactly one more actor (saw [0, 0, 0])".
DEFAULT_BASE = "0001CF7C"
_HEX = re.compile(r"^[0-9A-Fa-f]{1,8}$")


def base_problem(base: str) -> Optional[str]:
    if _HEX.match(str(base)):
        return None
    return (f"--base {base!r} is not a hex FormID: the bridge console does not resolve editor ids, so the roll would make nothing "
            f"(radroach = {DEFAULT_BASE}, CrRadroach in FalloutNV.esm)")


SETTLE_S = 8.0          # a roll needs: scan (<=0.5 s) + settle 0.5 s + round trip + SpawnRef creation
DEATH_SETTLE_S = 4.0


class Proof:
    def __init__(self, nvs: List[Any], sleep: Callable[[float], None], actor_type: int, base: str, log=print):
        self.nvs = nvs
        self.sleep = sleep
        self.type = actor_type
        self.base = base
        self.log = log
        self.failures: List[str] = []
        self.passes = 0
        self.roll_errors: List[str] = []

    # ---- helpers ----
    def check(self, ok: bool, what: str, seen: Any = None) -> bool:
        if ok:
            self.passes += 1
            self.log(f"  PASS {what}")
        else:
            self.failures.append(what)
            self.log(f"  FAIL {what}" + (f"  (saw {seen})" if seen is not None else ""))
        return ok

    def spawn(self, nv) -> Dict[str, Any]:
        return nv.call("coop.spawn", {})

    def sites(self, nv) -> Dict[int, Dict[str, Any]]:
        r = nv.call("coop.spawn", {"list": True, "max": 500})
        return {int(s["site"]): s for s in r.get("sites", [])}

    def actors(self, nv) -> List[Dict[str, Any]]:
        return nv.call("world.actors", {"max": 400, "type": self.type}).get("actors", [])

    def counts(self) -> List[int]:
        return [len(self.actors(nv)) for nv in self.nvs]

    def roll(self, idx: int) -> None:
        r = self.nvs[idx].exec(f"player.PlaceAtMe {self.base} 1")
        if isinstance(r, dict) and not r.get("ok", True):       # a refused line used to be dropped here, and the step only said "saw [0, 0, 0]"
            self.roll_errors.append(f"instance {idx}: {r.get('output') or 'refused'}")

    def states(self) -> List[Dict[int, Dict[str, Any]]]:
        return [self.sites(nv) for nv in self.nvs]

    def new_sites(self, before: List[Dict[int, Dict[str, Any]]]) -> List[Dict[int, Dict[str, Any]]]:
        now = self.states()
        return [{k: v for k, v in n.items() if k not in b} for n, b in zip(now, before)]

    # ---- steps ----
    def step_module(self) -> None:
        self.log("1 module")
        digs = []
        for i, nv in enumerate(self.nvs):
            r = self.spawn(nv)
            self.check("unsupported" not in r, f"instance {i}: coop.spawn is built in and running", r)
            self.check(bool(r.get("link", False)), f"instance {i}: link to the host is up", r.get("link"))
            digs.append(r.get("digest"))
        self.check(len(set(digs)) == 1, "every instance has the same site-table digest", digs)

    def step_once(self) -> Optional[int]:
        self.log("2 once")
        before_counts = self.counts()
        before_sites = self.states()
        self.roll(1)
        self.sleep(SETTLE_S)
        self.check(not self.roll_errors, "the game accepted the roll command (player.PlaceAtMe)", self.roll_errors)
        after = self.counts()
        deltas = [a - b for a, b in zip(after, before_counts)]
        self.check(all(d == 1 for d in deltas), "every instance has exactly one more actor", deltas)
        new = self.new_sites(before_sites)
        self.check(all(len(n) == 1 for n in new), "every instance knows exactly one new site", [len(n) for n in new])
        if not all(len(n) == 1 for n in new):
            return None
        recs = [next(iter(n.values())) for n in new]
        ids = [next(iter(n.keys())) for n in new]
        self.check(len(set(ids)) == 1, "it is the same site everywhere", ids)
        self.check(all(r.get("state") == "Spawned" for r in recs), "the site is Spawned everywhere", [r.get("state") for r in recs])
        self.check(len({(r.get("owner"), r.get("epoch")) for r in recs}) == 1, "one owner and epoch everywhere", [(r.get("owner"), r.get("epoch")) for r in recs])
        self.check(all(r.get("members") == 1 for r in recs), "one member everywhere", [r.get("members") for r in recs])
        return ids[0]

    def step_race(self) -> None:
        self.log("3 race")
        if len(self.nvs) < 3:
            self.log("  (skipped: needs a second client, pass --instances 0 1 2)")
            return
        # a different spot is needed so this is a NEW site: the caller moved the players, or we accept the same site (then it must be suppressed)
        before = self.counts()
        self.roll(1)
        self.roll(2)
        self.sleep(SETTLE_S)
        deltas = [a - b for a, b in zip(self.counts(), before)]
        self.check(all(d <= 1 for d in deltas) and len(set(deltas)) == 1, "both rolled, every instance shows the same number of new actors (at most one)", deltas)

    def step_death(self, site: Optional[int]) -> None:
        self.log("4 death")
        if site is None:
            self.check(False, "no site from step 2: cannot kill its actor")
            return
        host = self.nvs[0]
        actors = self.actors(host)
        # the newest actor on the host is the encounter's (world.actors lists the cell; the encounter's ref has a 0xFF prefix)
        created = [a for a in actors if (int(a.get("id", 0)) >> 24) == 0xFF]
        self.check(len(created) >= 1, "the host sees a dynamic actor to kill", len(created))
        if not created:
            return
        victim = created[-1]
        ref = format(int(victim["id"]), "08X")
        owner = None
        for i, nv in enumerate(self.nvs):
            o = nv.call("coop.owner", {"ref": f"fnvcoop.created:{ref}"})
            if o.get("mine"):
                owner = i
        target = self.nvs[owner if owner is not None else 0]
        target.exec("Kill", ref=ref)
        self.sleep(DEATH_SETTLE_S)
        st = [s.get(site) for s in self.states()]
        self.check(all(s and s.get("state") == "Cleared" for s in st), "the site is Cleared on every instance", [s and s.get("state") for s in st])

    def step_no_respawn(self, site: Optional[int]) -> None:
        self.log("5 no respawn")
        before = self.counts()
        alive_before = [sum(1 for a in self.actors(nv)) for nv in self.nvs]
        self.roll(1)
        self.sleep(SETTLE_S)
        after = self.counts()
        self.check(after == before, "the second roll was thrown away: no instance gained an actor", (before, after))
        if site is not None:
            st = [s.get(site) for s in self.states()]
            self.check(all(s and s.get("state") == "Cleared" for s in st), "the site is still Cleared", [s and s.get("state") for s in st])
        supp = [self.spawn(nv).get("stats", {}).get("rollsSuppressed", 0) for nv in self.nvs]
        self.check(sum(supp) >= 1, "some machine counted a suppressed roll", supp)
        _ = alive_before

    def step_respawn(self, site: Optional[int]) -> None:
        self.log("6 respawn")
        host = self.nvs[0]
        days = host.call("exec", {"cmd": "GetGameDaysPassed"}) if hasattr(host, "call") else {}
        try:
            cur = float(str(days.get("output", "0")).split()[-1])
        except (ValueError, AttributeError, IndexError):
            cur = 0.0
        host.exec(f"set GameDaysPassed to {cur + 4.0}")
        self.sleep(3.0)
        if site is not None:
            st = [s.get(site) for s in self.states()]
            self.check(all(s and s.get("state") == "Respawnable" for s in st), "the site is Respawnable on every instance", [s and s.get("state") for s in st])
        before = self.counts()
        self.roll(1)
        self.sleep(SETTLE_S)
        deltas = [a - b for a, b in zip(self.counts(), before)]
        self.check(all(d == 1 for d in deltas), "the next roll counts: +1 on every instance", deltas)

    def run(self, respawn: bool) -> int:
        self.step_module()
        site = self.step_once()
        self.step_race()
        self.step_death(site)
        self.step_no_respawn(site)
        if respawn:
            self.step_respawn(site)
        self.log(f"{self.passes} checks passed, {len(self.failures)} failed")
        for f in self.failures:
            self.log(f"  FAILED: {f}")
        return 0 if not self.failures else 1


# ---- a fake world for --selftest -----------------------------------------------------------------------------------------------------
class FakeNv:
    """One fake instance. `world` is shared; arbitrate=False models the missing feature (every machine keeps only its own roll)."""

    def __init__(self, world: "FakeWorld", idx: int):
        self.w = world
        self.i = idx

    def exec(self, cmd: str, ref: Optional[str] = None) -> Dict[str, Any]:
        if cmd.lower().startswith("player.placeatme"):
            base = cmd.split()[1] if len(cmd.split()) > 1 else ""
            if base_problem(base):          # like the real console through the bridge: an editor id does not resolve and nothing is placed
                return {"ok": False, "output": f"Script command 'PlaceAtMe' cannot resolve form '{base}'"}
            self.w.rolls.append((self.i, self.w.t))
        elif cmd == "Kill" and ref:
            self.w.kill(ref)
        elif cmd.startswith("set GameDaysPassed"):
            self.w.game_days_bump()
        return {"ok": True, "output": "4.0"}

    def call(self, method: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        params = params or {}
        w = self.w
        w.settle()
        if method == "coop.spawn" and params.get("list"):
            return {"sites": [dict(s, site=k) for k, s in w.sites[self.i].items()], "total": len(w.sites[self.i])}
        if method == "coop.spawn":
            return {"self": self.i, "host": self.i == 0, "link": True, "digest": w.digest(self.i), "stats": {"rollsSuppressed": w.suppressed[self.i]}}
        if method == "world.actors":
            return {"actors": [{"id": a, "ref": format(a, "08X"), "base": 1} for a in w.actors[self.i]]}
        if method == "coop.owner":
            return {"mine": self.i == 1 and int(params["ref"].split(":")[1], 16) in w.actors[1]}
        if method == "exec":
            return self.exec(params.get("cmd", ""), params.get("ref"))
        raise KeyError(method)


class FakeWorld:
    def __init__(self, n: int, arbitrate: bool):
        self.n = n
        self.arbitrate = arbitrate
        self.t = 0.0
        self.rolls: List[Any] = []
        self.actors: List[List[int]] = [[0x00100000 + i for i in range(3)] for _ in range(n)]   # three placed bystanders each
        self.sites: List[Dict[int, Dict[str, Any]]] = [dict() for _ in range(n)]
        self.suppressed = [0] * n
        self.next_id = 0xFF000001
        self.site_state = None      # None | "Spawned" | "Cleared" | "Respawnable"
        self.site_id = 0x1234ABCD
        self.pending_death = None

    def now(self) -> float:
        return self.t

    def sleep(self, s: float) -> None:
        self.t += s
        self.settle()

    def digest(self, i: int) -> str:
        return str((self.site_state, sorted(self.actors[i])))

    def _publish(self, state: str) -> None:
        self.site_state = state
        for i in range(self.n):
            self.sites[i][self.site_id] = {"state": state, "owner": 1, "epoch": 1, "members": 1, "gen": 1}

    def settle(self) -> None:
        if not self.rolls:
            return
        rolls, self.rolls = self.rolls, []
        if self.arbitrate:
            if self.site_state in (None, "Respawnable"):
                aid = self.next_id; self.next_id += 1
                for i in range(self.n):
                    self.actors[i].append(aid)
                self._publish("Spawned")
            elif self.site_state == "Cleared":
                for (i, _) in rolls:
                    self.suppressed[i] += 1
            # Spawned: every extra roll is dropped by its machine
        else:
            for (i, _) in rolls:
                self.actors[i].append(self.next_id); self.next_id += 1       # nobody arbitrates: each machine keeps its own copy

    def kill(self, ref: str) -> None:
        if self.arbitrate:
            self._publish("Cleared")

    def game_days_bump(self) -> None:
        if self.arbitrate and self.site_state == "Cleared":
            self._publish("Respawnable")


def selftest() -> int:
    ok = True
    for arbitrate, expect_ok in ((True, True), (False, False)):
        w = FakeWorld(3, arbitrate)
        nvs = [FakeNv(w, i) for i in range(3)]
        lines: List[str] = []
        p = Proof(nvs, w.sleep, 0x3B, DEFAULT_BASE, log=lines.append)
        rc = p.run(respawn=True)
        passed = rc == 0
        # fake created ids carry the 0xFF prefix, as the real ones do
        print(f"selftest arbitrate={arbitrate}: script said {'PASS' if passed else 'FAIL'} (expected {'PASS' if expect_ok else 'FAIL'}), {p.passes} checks, {len(p.failures)} failed")
        if passed != expect_ok:
            ok = False
            print("\n".join(lines[-25:]))
    # the failing world must fail for the right reason: the step that counts actors per instance
    w = FakeWorld(3, False)
    nvs = [FakeNv(w, i) for i in range(3)]
    p = Proof(nvs, w.sleep, 0x3B, DEFAULT_BASE, log=lambda s: None)
    p.run(respawn=False)
    if "every instance has exactly one more actor" not in p.failures:
        print("selftest: the duplicating world was not caught by the 'exactly one more actor' check")
        ok = False
    else:
        print("selftest: the duplicating world is caught by the per-instance actor count")
    # an editor id as the base (the sweep of 2026-10-22: "RadRoach"): the game refuses the roll, and the script must say THAT, not only "saw [0, 0, 0]"
    w = FakeWorld(3, True)
    nvs = [FakeNv(w, i) for i in range(3)]
    p = Proof(nvs, w.sleep, 0x3B, "RadRoach", log=lambda s: None)
    p.run(respawn=False)
    if "the game accepted the roll command (player.PlaceAtMe)" not in p.failures:
        print("selftest: a roll with an editor id was not reported as a refused command")
        ok = False
    else:
        print("selftest: a roll with an editor id is reported as a refused command")
    if not base_problem("RadRoach") or base_problem(DEFAULT_BASE) or base_problem("0001cf7c"):
        print("selftest: base_problem does not tell an editor id from a hex FormID")
        ok = False
    print("ALL OK" if ok else "SELFTEST FAILED")
    return 0 if ok else 1


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--instances", type=int, nargs="+", default=[0, 1], help="bridge instance numbers; the first is the host")
    ap.add_argument("--base", default=DEFAULT_BASE, help="hex FormID of the actor the encounter makes (default: CrRadroach); an EditorID does not resolve in the bridge console")
    ap.add_argument("--type", type=int, default=0x3C, help="form type world.actors lists (0x3B characters, 0x3C creatures)")
    ap.add_argument("--respawn", action="store_true", help="also prove the respawn step (moves the host's game time on by 4 days)")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args(argv)
    if a.selftest:
        return selftest()
    if base_problem(a.base):
        print(base_problem(a.base), file=sys.stderr)
        return 2
    sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "tools" / "runner"))
    from nvbridge import NvBridge  # noqa: E402
    nvs = [NvBridge.connect(instance=i, wait_s=10) for i in a.instances]
    try:
        return Proof(nvs, time.sleep, a.type, a.base).run(a.respawn)
    finally:
        for nv in nvs:
            nv.close()


if __name__ == "__main__":
    sys.exit(main())
