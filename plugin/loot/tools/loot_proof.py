#!/usr/bin/env python3
"""In-game proof of loot sync (FULL-SYNC-PLAN 3.5): shared containers, one lock, drops, first-claim-wins pickups.

Fully scripted: no step needs a human. It runs on a PC where two or three game instances are already in one co-op session (host = instance 0),
standing together next to a lootable container, with nvcoop built with NVCOOP_WITH_LOOT, nvcoop.ini [loot] enabled=1 and [dev] bots=1 on every
instance and the wiring line of plugin/loot/loot_glue.h in place (docs/tools/loot.md, "In-game proof"). tools/proof/prove.py provisions all of it.

    python -I plugin/loot/tools/loot_proof.py --instances 0 1 [2] --container Plugin.esm:HEX --item Stimpak
    python -I plugin/loot/tools/loot_proof.py --selftest          # game-free: the script's own pass / fail logic against a fake world

How the player moves things: the console on a calling reference. `RemoveItemTarget <item> <player> 1 1` run on the container moves one item from
it to the player, the same container read the plugin's watcher uses to see a take. `Activate 14 1` run on a container opens its menu for the
player. The container menu is closed by the bridge method bot.menu.close {kind:"container"} (plugin/bots, CloseAllMenusCore 0x7037E0, the engine's
own CloseAllMenus path); the script then waits until bot.menu reports no menu. A build without that method fails the step, it never waits for a hand.

Steps (each prints PASS / FAIL with the numbers it saw):
  1 module     coop.loot answers on every instance, the session is up, the same table digest everywhere
  2 lock       instance 1 opens the container, instance 2 tries: its menu is refused (openContainer returns to 0); the table says holder = 1
  3 take       instance 1 takes one item: every instance's table has one less; instance 1's inventory has one more; the digest matches on close
  4 drop       instance 1 drops the item: every instance has the same one world item (table digest) and instance 2 sees a world ref
  5 race       instances 1 and 2 pick the same item up at the same moment: exactly one of them ends up with it; the table is empty everywhere
Exit code 0 only if every step passed.
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

# test-only: one deliberate bug, set by the selftest's twin: "no_session_wait" checks the session at once, the way the first real sweep did
MUTANT = os.environ.get("LOOT_PROOF_MUTANT", "")
SESSION_S = 90.0     # the copies were booted a moment ago: the guests may still be joining when the script starts
SETTLE_S = 3.0
LOCK_S = 3.0
MENU_GONE_S = 5.0


class Proof:
    def __init__(self, nvs: List[Any], sleep: Callable[[float], None], container: str, item: str, log=print):
        self.nvs = nvs
        self.sleep = sleep
        self.container = container
        self.item = item
        self.log = log
        self.failures: List[str] = []
        self.passes = 0

    def check(self, ok: bool, what: str, seen: Any = None) -> bool:
        if ok:
            self.passes += 1
            self.log(f"  PASS {what}")
        else:
            self.failures.append(what)
            self.log(f"  FAIL {what}" + (f"  (saw {seen})" if seen is not None else ""))
        return ok

    def status(self, nv) -> Dict[str, Any]:
        return nv.call("coop.loot", {})

    def table(self, nv) -> Dict[str, Any]:
        return nv.call("coop.loot", {"container": self.container_net})

    def count(self, nv, item: Optional[str] = None) -> int:
        r = nv.call("bot.inventory", {"items": [item or self.item]})
        for it in r.get("items", []):
            return int(it.get("count", 0))
        return 0

    def close_container_menu(self, nv, who: str) -> bool:
        """Close the container menu on `nv` from code (bot.menu.close) and wait until bot.menu reports that no menu is open."""
        try:
            r = nv.call("bot.menu.close", {"kind": "container"})
        except Exception as e:  # noqa: BLE001
            if type(e).__name__ in ("ConnectionLost", "GameCrashed"):
                raise
            return self.check(False, f"{who}: bot.menu.close closed the container menu", str(e)[:160])
        self.check(bool(r.get("closed")), f"{who}: bot.menu.close answered closed", r)
        gone = self.wait(lambda: nv.call("bot.menu", {}).get("kind") == "none", MENU_GONE_S)
        return self.check(gone, f"{who}: bot.menu reports no menu after the close")

    def wait(self, pred: Callable[[], bool], timeout: float) -> bool:
        t = 0.0
        while t <= timeout:
            if pred():
                return True
            self.sleep(0.25)
            t += 0.25
        return pred()

    # ---- steps ----
    def step_module(self) -> bool:
        self.log("1 module")
        digs = []
        ok = True
        for i, nv in enumerate(self.nvs):
            s = self.status(nv)
            if MUTANT != "no_session_wait":
                t = 0.0
                while not (s.get("enabled") and s.get("session")) and t < SESSION_S:   # a join still in progress is not a failure
                    self.sleep(1.0)
                    t += 1.0
                    s = self.status(nv)
            ok &= self.check(bool(s.get("enabled")) and bool(s.get("session")), f"instance {i}: loot sync is enabled and in the session",
                             (s.get("enabled"), s.get("session")))
            digs.append(s.get("digest"))
        self.check(len(set(digs)) == 1, "every instance has the same table digest", digs)
        return ok

    def step_lock(self) -> None:
        self.log("2 lock")
        a, b = self.nvs[1], self.nvs[2] if len(self.nvs) > 2 else None
        a.exec("Activate 14 1", ref=self.container)
        self.check(self.wait(lambda: int(self.status(a).get("openContainer", 0)) != 0, LOCK_S), "instance 1's container menu is open and tracked")
        self.sleep(SETTLE_S / 2)
        self.container_net = int(self.status(a).get("openContainer", 0))
        holder = self.nvs[0].call("coop.loot", {"container": self.container_net}).get("holder")
        self.check(holder == 1, "the host's table says instance 1 holds the container", holder)
        if b is None:
            self.log("  (second-player refusal skipped: needs a second client, pass --instances 0 1 2)")
            return
        b.exec("Activate 14 1", ref=self.container)
        self.check(self.wait(lambda: int(self.status(b).get("openContainer", 0)) == 0, LOCK_S * 2), "instance 2's menu was refused and closed")

    def step_take(self) -> None:
        self.log("3 take")
        a = self.nvs[1]
        before = [self.count(nv) for nv in self.nvs]
        a.exec(f"RemoveItemTarget {self.item} player 1 1", ref=self.container)
        self.sleep(SETTLE_S)
        after = [self.count(nv) for nv in self.nvs]
        self.check(after[1] == before[1] + 1, "instance 1 has one more", (before[1], after[1]))
        digs = [self.table(nv).get("digest") for nv in self.nvs]
        self.check(len(set(digs)) == 1, "every instance's table of the container is the same", digs)
        self.close_container_menu(a, "instance 1")
        self.check(self.wait(lambda: int(self.status(a).get("openContainer", 0)) == 0, LOCK_S * 2), "the loot module saw the menu close")
        self.sleep(SETTLE_S)
        hosted = self.nvs[0].call("coop.loot", {"container": self.container_net})
        self.check(hosted.get("holder") == -1, "the lock is released after the close", hosted.get("holder"))
        st = [self.status(nv) for nv in self.nvs]
        self.check(int(st[0].get("hostStats", {}).get("closeMismatch", 0)) == 0, "the close digest matched (no silent difference)", st[0].get("hostStats"))

    def step_drop(self) -> Optional[str]:
        self.log("4 drop")
        a = self.nvs[1]
        before = [int(self.status(nv).get("items", 0)) for nv in self.nvs]
        a.exec(f"player.Drop {self.item} 1")
        self.sleep(SETTLE_S)
        after = [int(self.status(nv).get("items", 0)) for nv in self.nvs]
        self.check(all(x == y + 1 for x, y in zip(after, before)), "every instance knows exactly one more world item", (before, after))
        digs = [self.status(nv).get("digest") for nv in self.nvs]
        self.check(len(set(digs)) == 1, "same table digest everywhere", digs)
        return None

    def step_race(self) -> None:
        self.log("5 race")
        if len(self.nvs) < 3:
            self.log("  (skipped: needs a second client, pass --instances 0 1 2)")
            return
        a, b = self.nvs[1], self.nvs[2]
        refs = a.call("bot.drops", {}).get("drops", [])
        if not refs:
            self.check(False, "bot.drops reported the dropped ref")
            return
        ref = refs[-1]["ref"]
        before = [self.count(nv) for nv in self.nvs]
        a.exec("Activate 14 1", ref=ref)
        b.exec("Activate 14 1", ref=ref)
        self.sleep(SETTLE_S * 2)
        after = [self.count(nv) for nv in self.nvs]
        gained = (after[1] - before[1]) + (after[2] - before[2])
        self.check(gained == 1, "exactly one of the two ended up with the item", (before, after))
        self.check(all(int(self.status(nv).get("items", 0)) == 0 for nv in self.nvs), "no world item is left in any table")

    def run(self) -> int:
        self.container_net = 0
        if not self.step_module():
            self.log("module not running: stop")
            return 1
        self.step_lock()
        self.step_take()
        self.step_drop()
        self.step_race()
        self.log(f"{self.passes} passed, {len(self.failures)} failed")
        for f in self.failures:
            self.log("  failed: " + f)
        return 0 if not self.failures else 1


# ---- a fake world for --selftest ------------------------------------------------------------------------------------------------------------
class FakeNv:
    """One fake instance. `world` is shared; dupes=True models the missing arbitration (a lost pickup keeps its item)."""

    def __init__(self, world: "FakeWorld", idx: int):
        self.w = world
        self.i = idx

    def exec(self, cmd: str, ref: Optional[str] = None) -> Dict[str, Any]:
        w = self.w
        if cmd.startswith("Activate 14") and ref == w.container:
            if w.holder in (None, self.i):
                w.holder = self.i
                w.open[self.i] = True
            return {"ok": True, "output": ""}
        if cmd.startswith("Activate 14") and ref == w.item_ref:
            w.pick_queue.append(self.i)
            if len(w.pick_queue) >= w.expect_pickers:
                w.resolve()
            return {"ok": True, "output": ""}
        if cmd.startswith("RemoveItemTarget"):
            w.inv[self.i] += 1
            w.stock -= 1
            return {"ok": True, "output": ""}
        if cmd.startswith("player.Drop"):
            w.inv[self.i] -= 1
            w.items += 1
            return {"ok": True, "output": ""}
        return {"ok": True, "output": ""}

    def call(self, method: str, params: Dict[str, Any]) -> Dict[str, Any]:
        w = self.w
        if method == "coop.loot":
            if "container" in params:
                return {"known": True, "holder": -1 if w.holder is None else w.holder, "stacks": 1, "digest": f"{w.stock}-{w.items}"}
            return {"enabled": True, "session": w.t >= w.session_after, "openContainer": 256 if w.open.get(self.i) else 0, "items": w.items,
                    "digest": f"{w.stock}-{w.items}", "hostStats": {"closeMismatch": 0}}
        if method == "bot.menu.close":
            if w.close_missing:
                raise RuntimeError("unknown method bot.menu.close")
            if not w.open.get(self.i):
                raise RuntimeError("no menu is open")
            if not w.close_ignored:
                w.open[self.i] = False
                w.holder = None
            return {"closed": True, "menu": 1008, "kind": "container"}
        if method == "bot.menu":
            return {"kind": "container" if w.open.get(self.i) else "none", "id": 1008 if w.open.get(self.i) else 0}
        if method == "bot.inventory":
            return {"items": [{"item": params["items"][0], "count": w.inv[self.i]}]}
        if method == "bot.drops":
            return {"drops": [{"ref": w.item_ref}]}
        return {}

    def close(self) -> None:
        pass


class FakeWorld:
    def __init__(self, dupes: bool, players: int = 3, close_missing: bool = False, close_ignored: bool = False, session_after: float = 0.0):
        self.dupes = dupes
        self.session_after = session_after        # seconds until the guests have joined (session false before that)
        self.close_missing = close_missing        # the build has no bot.menu.close
        self.close_ignored = close_ignored        # bot.menu.close says closed but the menu stays up (and the lock with it)
        self.container = "Fake.esm:100"
        self.item_ref = "00ABCD01"
        self.holder: Optional[int] = None
        self.open: Dict[int, bool] = {}
        self.inv = [0] * players
        self.stock = 5
        self.items = 0
        self.pick_queue: List[int] = []
        self.expect_pickers = 2
        self.t = 0.0

    def resolve(self) -> None:
        first = True
        for who in self.pick_queue:
            self.inv[who] += 1
            if first:
                self.items -= 1
                first = False
            elif not self.dupes:
                self.inv[who] -= 1
        self.pick_queue = []

    def sleep(self, s: float) -> None:
        self.t += s
        if self.t > 0 and self.holder is not None and self.t > 40:
            pass


def selftest() -> int:
    rc = 0
    # (name, world arguments, the script must pass). The close twins prove the menu close is judged, not assumed: a build without the
    # bridge method and a close that does nothing both fail the take step, and neither waits for a human.
    cases = (("healthy", {}, True), ("dupes", {"dupes": True}, False),
             ("close_missing", {"close_missing": True}, False), ("close_ignored", {"close_ignored": True}, False),
             ("late_session", {"session_after": 12.0}, True))
    for name, kw, expect_ok in cases:
        w = FakeWorld(kw.get("dupes", False), close_missing=kw.get("close_missing", False), close_ignored=kw.get("close_ignored", False),
                      session_after=kw.get("session_after", 0.0))
        nvs = [FakeNv(w, i) for i in range(3)]
        quiet = lambda *_a, **_k: None
        p = Proof(nvs, w.sleep, w.container, "Stimpak", log=quiet)
        passed = p.run() == 0
        print(f"selftest {name}: script said {'PASS' if passed else 'FAIL'} (expected {'PASS' if expect_ok else 'FAIL'}), {p.passes} checks, {len(p.failures)} failed")
        if passed != expect_ok:
            rc = 1
    if rc == 0 and not MUTANT:   # twin: a script that checks the session before the guests joined must be noticed by the late_session world
        cp = subprocess.run([sys.executable, "-I", str(Path(__file__).resolve()), "--selftest"], capture_output=True, text=True,
                            env=dict(os.environ, LOOT_PROOF_MUTANT="no_session_wait"))
        if cp.returncode == 0:
            print("selftest: the no_session_wait twin was not caught (a join still in progress must not fail the module step)")
            rc = 1
    if rc == 0:
        print("selftest: ok (the duplicating world and both broken menu closes are caught)")
    return rc


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--instances", type=int, nargs="+", default=[0, 1], help="bridge instance numbers; the first is the host")
    ap.add_argument("--container", default="", help="container reference as Plugin.esm:HEX")
    ap.add_argument("--item", default="Stimpak", help="console form (EditorID) of the item moved around")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args(argv)
    if a.selftest:
        return selftest()
    if not a.container:
        print("--container is required (Plugin.esm:HEX of a container next to the players)")
        return 2
    sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "tools" / "runner"))
    from nvbridge import NvBridge  # noqa: E402
    import nvready  # noqa: E402

    nvs = [NvBridge.connect(instance=i, wait_s=10) for i in a.instances]
    try:
        why = nvready.wait_ready(nvs)
        if why:
            print("cannot reach the game instances: " + why)
            return 2
        return Proof(nvs, time.sleep, a.container, a.item).run()
    finally:
        for nv in nvs:
            nv.close()


if __name__ == "__main__":
    sys.exit(main())
