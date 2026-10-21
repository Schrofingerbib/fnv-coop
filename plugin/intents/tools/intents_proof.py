#!/usr/bin/env python3
"""In-game proof of guest intents (FULL-SYNC-PLAN 3.7) and SaveSuppress enforcement.

NOT RUN by the lane that wrote it (code only). The helper runs it on a PC where three game instances are already in one co-op session
(instance 0 = host, 1 and 2 = guests), standing within about 400 units of the test references, with nvcoop built with plugin/intents and
nvcoop.ini [intents] enable=1 on all three (and [live] avatars=1 on the host and guests so every guest has a puppet; without puppets the
host acts as a stand-in and step 2 reports it).

    python -I plugin/intents/tools/intents_proof.py --instances 0 1 2 --door 0xDOOR --item 0xITEM --item-base 0xBASE --container 0xCONT
                                                    [--terminal 0xREF --entry 0] [--trigger 0xREF] [--close-menu CloseAllMenus]
    python -I plugin/intents/tools/intents_proof.py --selftest      # game-free: the script's own pass / fail logic against a fake world

References are runtime form ids ("0x00103E61") or "Plugin.esm:HEX" as the bridge's `ref` argument takes them. Use a door and a container near
the players; `--item` is a world item (a bottle cap pile) whose base form is `--item-base`.

Steps (each prints PASS / FAIL with the numbers it saw):
  1 module     intents.status on every instance: enabled, engine pins ok, session up, the SaveSuppress hook installed
  2 activate   a guest activates the door: the host ran it once, as the guest's puppet (or says it stood in), the door state matches everywhere
  3 pickup     both guests take the same item at the same moment: exactly one of them has it, the host has none, the item is gone everywhere
  4 container  guest 1 opens the container (lock held), guest 2 is told it is in use, guest 1 closes the menu: the lock is released
  5 terminal   (--terminal) a terminal entry intent reaches the host and the host answers (Executed or a plain Unsupported)
  6 trigger    (--trigger) an OnTriggerEnter intent runs once on the host and its repeat inside the cooldown does not
  7 save gate  the host's story turns SaveSuppress on: CanSaveNow is refused on every instance (and the game's own answer is restored when it is off)
Exit code 0 only if every step passed.
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

SETTLE_S = 2.5


class Proof:
    def __init__(self, nvs: List[Any], sleep: Callable[[float], None], args: argparse.Namespace, log=print):
        self.nvs = nvs
        self.sleep = sleep
        self.a = args
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

    # ---- helpers ----
    def status(self, nv) -> Dict[str, Any]:
        return nv.call("intents.status", {})

    def detail(self, nv) -> Dict[str, Any]:
        return self.status(nv).get("service", {}).get("detail", {}) or {}

    def host_stat(self, path: str) -> int:
        d: Any = self.detail(self.nvs[0])
        for k in path.split("."):
            d = d.get(k, 0) if isinstance(d, dict) else 0
        return int(d or 0)

    def activate(self, nv, ref: str) -> None:
        nv.call("activate.ref", {"ref": ref})

    def item_count(self, nv) -> float:
        return nv.eval(f"player.GetItemCount {self.a.item_base}")

    # ---- steps ----
    def step_module(self) -> None:
        self.log("1 module")
        for i, nv in enumerate(self.nvs):
            s = self.status(nv)
            self.check(bool(s.get("enabled")), f"instance {i}: [intents] enable=1", s.get("note"))
            self.check(bool(s.get("pinsOk")), f"instance {i}: engine pins match this exe", s.get("pinsOk"))
            self.check(bool(s.get("service", {}).get("up")), f"instance {i}: the intents session is up", s.get("service"))
            self.check(bool(s.get("canSaveHook")), f"instance {i}: the CanSaveNow hook is installed", s.get("canSaveHook"))
        self.check(self.detail(self.nvs[0]).get("role") == "host", "instance 0 is the host", self.detail(self.nvs[0]).get("role"))
        self.check(all(self.detail(nv).get("role") == "guest" for nv in self.nvs[1:]), "the others are guests")

    def step_activate(self) -> None:
        self.log("2 activate")
        before = self.host_stat("executed")
        puppet0 = self.host_stat("actedAs.puppet")
        stand0 = self.host_stat("actedAs.standIn")
        states0 = [nv.eval("GetOpenState", ref=self.a.door) for nv in self.nvs]
        self.activate(self.nvs[1], self.a.door)
        self.sleep(SETTLE_S)
        self.check(self.host_stat("executed") == before + 1, "the host ran the activation exactly once", self.host_stat("executed") - before)
        puppet = self.host_stat("actedAs.puppet") - puppet0
        stand = self.host_stat("actedAs.standIn") - stand0
        self.check(puppet == 1, "it ran as the guest's puppet" + (" (stand-in was used: no puppet for that guest)" if stand else ""), {"puppet": puppet, "standIn": stand})
        states = [nv.eval("GetOpenState", ref=self.a.door) for nv in self.nvs]
        self.check(len(set(states)) == 1 and states != states0, "the door state changed and is the same on every instance", (states0, states))
        self.check(self.detail(self.nvs[1]).get("verdicts", 0) >= 1, "the guest got its verdict", self.detail(self.nvs[1]).get("verdicts"))

    def step_pickup(self) -> None:
        self.log("3 pickup")
        before = [self.item_count(nv) for nv in self.nvs]
        won0, lost0 = self.host_stat("claims.won"), self.host_stat("claims.lost")
        self.activate(self.nvs[1], self.a.item)
        self.activate(self.nvs[2], self.a.item)
        self.sleep(SETTLE_S + 1.5)
        after = [self.item_count(nv) for nv in self.nvs]
        gained = [a - b for a, b in zip(after, before)]
        self.check(sorted(gained[1:]) == [0.0, gained[1] + gained[2]] and (gained[1] > 0) != (gained[2] > 0), "exactly one guest gained the item", gained)
        self.check(gained[0] == 0.0, "the host gained nothing", gained[0])
        self.check(self.host_stat("claims.won") == won0 + 1 and self.host_stat("claims.lost") == lost0 + 1, "one claim won, one lost",
                   (self.host_stat("claims.won") - won0, self.host_stat("claims.lost") - lost0))
        gone = [nv.eval("GetDisabled", ref=self.a.item) for nv in self.nvs]
        self.check(all(g == 1 for g in gone), "the item is gone on every instance", gone)

    def step_container(self) -> None:
        self.log("4 container")
        busy0 = self.host_stat("locks.busy")
        self.activate(self.nvs[1], self.a.container)
        self.sleep(SETTLE_S)
        self.check(self.host_stat("locks.held") == 1, "guest 1 holds the container lock", self.host_stat("locks.held"))
        self.activate(self.nvs[2], self.a.container)
        self.sleep(SETTLE_S)
        self.check(self.host_stat("locks.busy") == busy0 + 1, "guest 2 was told it is in use", self.host_stat("locks.busy") - busy0)
        self.check(self.detail(self.nvs[2]).get("rejected", {}).get("Busy", 0) >= 1, "guest 2 saw the Busy verdict", self.detail(self.nvs[2]).get("rejected"))
        self.nvs[1].exec(self.a.close_menu)
        self.sleep(SETTLE_S + 1.0)
        self.check(self.host_stat("locks.held") == 0, "closing the menu released the lock", self.host_stat("locks.held"))
        self.check(self.detail(self.nvs[1]).get("closesSent", 0) >= 1, "guest 1 sent the close", self.detail(self.nvs[1]).get("closesSent"))

    def step_terminal(self) -> None:
        self.log("5 terminal")
        if not self.a.terminal:
            self.log("  (skipped: pass --terminal REF)")
            return
        before = self.host_stat("executed")
        self.nvs[1].call("intents.submit", {"kind": "terminal", "ref": self.a.terminal, "path": [self.a.entry]})
        self.sleep(SETTLE_S)
        rej = self.host_stat("rejected.Unsupported") + self.host_stat("rejected.Failed")
        ran = self.host_stat("executed") - before
        self.check(ran + rej >= 1, "the host answered the terminal intent", {"executed": ran, "refused": rej})
        self.log(f"  info: terminal entry {'ran on the host' if ran else 'was refused (see rejected in intents.status); the hook path is unproven'}")

    def step_trigger(self) -> None:
        self.log("6 trigger")
        if not self.a.trigger:
            self.log("  (skipped: pass --trigger REF)")
            return
        before = self.host_stat("byKind.TriggerEnter")
        self.nvs[1].call("intents.submit", {"kind": "trigger", "ref": self.a.trigger})
        self.nvs[1].call("intents.submit", {"kind": "trigger", "ref": self.a.trigger})
        self.sleep(SETTLE_S)
        self.check(self.host_stat("byKind.TriggerEnter") == before + 1, "OnTriggerEnter ran once; the repeat inside the cooldown did not", self.host_stat("byKind.TriggerEnter") - before)

    def step_save_gate(self) -> None:
        self.log("7 save gate")
        host = self.nvs[0]
        allowed = [nv.call("intents.cansave", {}).get("allowed") for nv in self.nvs]
        self.log(f"  info: CanSaveNow before = {allowed}")
        host.call("coop.inject", {"txn": {"cause": {"kind": "QuestResult", "source": self.a.door}, "origin": 0, "effects": [{"type": "SaveSuppress", "on": True}]}})
        self.sleep(SETTLE_S)
        now = [nv.call("intents.cansave", {}).get("allowed") for nv in self.nvs]
        self.check(all(v is False for v in now), "CanSaveNow is refused on every instance while SaveSuppress is on", now)
        blocked = [self.status(nv).get("saveGate", {}).get("blocked", 0) for nv in self.nvs]
        self.check(all(b >= 1 for b in blocked), "every gate counted the refusal", blocked)
        host.call("coop.inject", {"txn": {"cause": {"kind": "QuestResult", "source": self.a.door}, "origin": 0, "effects": [{"type": "SaveSuppress", "on": False}]}})
        self.sleep(SETTLE_S)
        back = [nv.call("intents.cansave", {}).get("allowed") for nv in self.nvs]
        self.check(back == allowed, "with SaveSuppress off the game's own answer is back", (allowed, back))

    def run(self) -> int:
        self.step_module()
        self.step_activate()
        self.step_pickup()
        self.step_container()
        self.step_terminal()
        self.step_trigger()
        self.step_save_gate()
        self.log(f"{self.passes} checks passed, {len(self.failures)} failed")
        for f in self.failures:
            self.log(f"  FAILED: {f}")
        return 0 if not self.failures else 1


# ---- a fake world for --selftest ---------------------------------------------------------------------------------------------------------
class FakeWorld:
    """Three machines in one process. `defect` switches one behaviour off: claim | lock | gate | puppet | release | once."""

    def __init__(self, defect: str = ""):
        self.defect = defect
        self.t = 0.0
        self.door_open = 0
        self.item_gone = False
        self.items = [0.0, 0.0, 0.0]
        self.lock_holder: Optional[int] = None
        self.suppress = False
        self.menu_open = {1: False, 2: False}
        self.host = {"executed": 0, "actedAs": {"puppet": 0, "standIn": 0}, "claims": {"won": 0, "lost": 0}, "locks": {"busy": 0, "held": 0},
                     "byKind": {}, "rejected": {}}
        self.guest = {1: {"verdicts": 0, "rejected": {}, "closesSent": 0}, 2: {"verdicts": 0, "rejected": {}, "closesSent": 0}}
        self.gate = [0, 0, 0]
        self.trigger_at = -99.0

    def sleep(self, s: float) -> None:
        self.t += s

    def activate(self, idx: int, ref: str) -> None:
        h = self.host
        if ref == "door":
            runs = 2 if self.defect == "once" else 1
            for _ in range(runs):
                h["executed"] += 1
                h["actedAs"]["standIn" if self.defect == "puppet" else "puppet"] += 1
            self.door_open = 1 - self.door_open
            self.guest[idx]["verdicts"] += 1
        elif ref == "item":
            if not self.item_gone or self.defect == "claim":
                self.items[idx] += 10
                self.item_gone = True
                h["claims"]["won"] += 1
            else:
                h["claims"]["lost"] += 1
                self.guest[idx]["rejected"]["Taken"] = self.guest[idx]["rejected"].get("Taken", 0) + 1
            h["executed"] += 1
        elif ref == "container":
            if self.lock_holder not in (None, idx) and self.defect != "lock":
                h["locks"]["busy"] += 1
                self.guest[idx]["rejected"]["Busy"] = self.guest[idx]["rejected"].get("Busy", 0) + 1
            else:
                self.lock_holder = idx
                self.menu_open[idx] = True
            h["locks"]["held"] = 0 if self.lock_holder is None else 1

    def close_menu(self, idx: int) -> None:
        self.menu_open[idx] = False
        if self.lock_holder == idx and self.defect != "release":
            self.lock_holder = None
        self.guest[idx]["closesSent"] += 1
        self.host["locks"]["held"] = 0 if self.lock_holder is None else 1

    def cansave(self) -> bool:
        return not (self.suppress and self.defect != "gate")


class FakeNv:
    def __init__(self, w: FakeWorld, idx: int):
        self.w = w
        self.i = idx

    def call(self, method: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        p = params or {}
        w = self.w
        if method == "intents.status":
            detail = dict(w.host, role="host") if self.i == 0 else dict(w.guest[self.i], role="guest")
            return {"enabled": True, "pinsOk": True, "canSaveHook": True, "service": {"up": True, "detail": detail},
                    "saveGate": {"blocked": 1 if w.suppress and w.defect != "gate" else 0}}
        if method == "activate.ref":
            w.activate(self.i, "door" if p["ref"] == "D" else "item" if p["ref"] == "I" else "container")
            return {}
        if method == "intents.submit":
            if p["kind"] == "trigger":
                if w.t - w.trigger_at > 5:
                    w.host["byKind"]["TriggerEnter"] = w.host["byKind"].get("TriggerEnter", 0) + 1
                    w.host["executed"] += 1
                w.trigger_at = w.t
            else:
                w.host["executed"] += 1
            return {"id": 1}
        if method == "intents.cansave":
            return {"allowed": w.cansave()}
        if method == "coop.inject":
            w.suppress = bool(p["txn"]["effects"][0]["on"])
            return {}
        raise KeyError(method)

    def exec(self, cmd: str, ref: Optional[str] = None) -> Dict[str, Any]:
        if cmd == "CloseAllMenus":
            self.w.close_menu(self.i)
        return {"ok": True, "output": ""}

    def eval(self, expr: str, ref: Optional[str] = None) -> float:
        w = self.w
        if expr.startswith("GetOpenState"):
            return float(w.door_open)
        if expr.startswith("GetDisabled"):
            return 1.0 if w.item_gone else 0.0
        if expr.startswith("player.GetItemCount"):
            return w.items[self.i]
        raise KeyError(expr)


def selftest() -> int:
    ok = True
    for defect in ("", "claim", "lock", "gate", "puppet", "release", "once"):
        w = FakeWorld(defect)
        args = argparse.Namespace(door="D", item="I", item_base="0xF", container="C", terminal="T", entry=0, trigger="G", close_menu="CloseAllMenus")
        lines: List[str] = []
        p = Proof([FakeNv(w, i) for i in range(3)], w.sleep, args, log=lines.append)
        rc = p.run()
        expect_ok = defect == ""
        passed = rc == 0
        print(f"selftest defect={defect or 'none'}: script said {'PASS' if passed else 'FAIL'} (expected {'PASS' if expect_ok else 'FAIL'}), {p.passes} checks, {len(p.failures)} failed")
        if passed != expect_ok:
            ok = False
            print("\n".join(lines[-30:]))
    print("ALL OK" if ok else "SELFTEST FAILED")
    return 0 if ok else 1


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--instances", type=int, nargs="+", default=[0, 1, 2], help="bridge instance numbers; the first is the host")
    ap.add_argument("--door", help="a door (or lever) near the players")
    ap.add_argument("--item", help="a world item near the players")
    ap.add_argument("--item-base", help="the base form of --item, as a hex form id: the script counts it in the guests' inventories")
    ap.add_argument("--container", help="a container near the players")
    ap.add_argument("--terminal", help="a terminal (optional)")
    ap.add_argument("--entry", type=int, default=0, help="terminal entry index at the top menu")
    ap.add_argument("--trigger", help="a trigger volume reference (optional)")
    ap.add_argument("--close-menu", default="CloseAllMenus", help="console line that closes the open menu on a guest")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args(argv)
    if a.selftest:
        return selftest()
    missing = [n for n in ("door", "item", "item_base", "container") if not getattr(a, n)]
    if missing or len(a.instances) < 3:
        ap.error("needs --door --item --item-base --container and three instances (host + two guests)")
    sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "tools" / "runner"))
    from nvbridge import NvBridge  # noqa: E402
    nvs = [NvBridge.connect(instance=i, wait_s=10) for i in a.instances]
    try:
        return Proof(nvs, time.sleep, a).run()
    finally:
        for nv in nvs:
            nv.close()


if __name__ == "__main__":
    sys.exit(main())
