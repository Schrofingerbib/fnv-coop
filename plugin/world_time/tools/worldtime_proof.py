#!/usr/bin/env python3
"""In-game proof of the world-time lane (FULL-SYNC-PLAN 3.6 / 3.9): one clock, wait / sleep by party consent, native door changes, autosaves.

NOT RUN by the lane that wrote it (code only). The helper runs it on a PC where two (or three) game instances are already in one co-op
session (host = instance 0), standing near each other, with nvcoop built with plugin/world_time and these lines in every nvcoop.ini:

    [worldtime]
    enabled=1
    [dev]
    bots=1                       ; bot.menu.press / bot.consent answer the prompts
    ; the clock step writes the game clock: also set the environment variable NVCOOP_TEST_HELPERS=1 for the game processes

    python -I plugin/world_time/tools/worldtime_proof.py --instances 0 1 [2] [--door falloutnv.esm:XXXXXX] [--party-travel MARKER]
    python -I plugin/world_time/tools/worldtime_proof.py --selftest      # game-free: the script's own pass / fail logic against a fake world

Steps (each prints PASS / FAIL with the numbers it saw):
  1 module     worldtime.status on every instance: enabled, session up, pins ok (host also: pending-travel hook installed); clients got beacons
  2 clock      the host writes its clock +5 h: every client follows within 3 game minutes; a client writes +3 h: it is pulled back
  3 rest       a client asks to wait 2 h (vetoed, nothing moves yet); the asker confirms, the others answer Ready: every clock is +2 h, once
  4 refused    the same question, one player answers Not yet: no clock moves, the host sent a refusal
  5 door       (--door) the host activates the door natively (console Activate, no SetOpenState): every instance reports the same open state
  6 saves      clients report autosaves off, the host on
  7 travel     (--party-travel MARKER) a client asks to fast travel: vetoed; after the answers every instance started a trip
Exit code 0 only if every step passed.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

TOL_MIN = 3.0        # game minutes two clocks may differ after a sync
SETTLE_S = 6.0


def minutes_between(a: Dict[str, Any], b: Dict[str, Any]) -> float:
    """Game minutes from clock b to clock a, hour-of-day fine part plus whole days (mirrors nvwt::deltaMinutes)."""
    dh = (a["hour"] - b["hour"]) * 60.0
    wrapped = dh - 1440.0 * ((dh + 720.0) // 1440.0)
    dd = (a["daysPassed"] - b["daysPassed"]) * 1440.0
    via_wrap = wrapped + 1440.0 * round((dd - wrapped) / 1440.0)
    via_days = dh + dd
    return via_days if abs(via_days) < abs(via_wrap) else via_wrap


class Proof:
    def __init__(self, nvs: List[Any], sleep: Callable[[float], None], door: Optional[str], party_marker: Optional[str], log=print):
        self.nvs = nvs
        self.sleep = sleep
        self.door = door
        self.marker = party_marker
        self.log = log
        self.ok = True
        self.results: List[Dict[str, Any]] = []

    def check(self, name: str, cond: bool, detail: Any = "") -> bool:
        self.results.append({"step": name, "pass": bool(cond), "detail": str(detail)[:400]})
        self.log(("PASS " if cond else "FAIL ") + name + ((" :: " + str(detail)[:300]) if detail != "" else ""))
        self.ok &= bool(cond)
        return bool(cond)

    def call(self, i: int, method: str, params: Optional[Dict[str, Any]] = None) -> Any:
        return self.nvs[i].call(method, params or {})

    def status(self, i: int) -> Dict[str, Any]:
        return self.call(i, "worldtime.status")

    def clock(self, i: int, setto: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        return self.call(i, "worldtime.clock", {"set": setto} if setto else {})

    def drifts(self) -> List[float]:
        h = self.clock(0)
        return [minutes_between(self.clock(i), h) for i in range(1, len(self.nvs))]

    def wait_until(self, pred: Callable[[], bool], timeout_s: float) -> bool:
        t = 0.0
        while t < timeout_s:
            if pred():
                return True
            self.sleep(0.5)
            t += 0.5
        return pred()

    # ---------------------------------------------------------------------------------------------------------------- steps
    def step_module(self) -> None:
        sts = [self.status(i) for i in range(len(self.nvs))]
        self.check("1 module: enabled with a session on every instance", all(s.get("enabled") and s.get("session") for s in sts), [(s.get("enabled"), s.get("session"), s.get("pins")) for s in sts])
        self.check("1 module: engine pins match this exe", all(s.get("pins") for s in sts))
        self.check("1 module: host installed the pending-travel hook", bool(sts[0].get("hooked")), sts[0].get("hooked"))
        got = self.wait_until(lambda: all(self.status(i).get("stats", {}).get("beaconsRecv", 0) > 0 for i in range(1, len(self.nvs))), SETTLE_S)
        self.check("1 module: every client received a clock beacon", got)

    def step_clock(self) -> None:
        h = self.clock(0)
        target = dict(h)
        target["hour"] = (h["hour"] + 5.0) % 24.0
        if h["hour"] + 5.0 >= 24.0:
            target["day"] = min(28, h["day"] + 1)
            target["daysPassed"] = h["daysPassed"] + 1.0
        target["daysPassed"] = target["daysPassed"] + (5.0 / 24.0 if target["daysPassed"] == h["daysPassed"] else 0.0)
        self.clock(0, {k: target[k] for k in ("year", "month", "day", "hour", "daysPassed")})
        ok = self.wait_until(lambda: all(abs(d) <= TOL_MIN for d in self.drifts()), SETTLE_S)
        self.check("2 clock: clients follow a host clock jump of +5 h", ok, [round(d, 2) for d in self.drifts()])
        if len(self.nvs) > 1:
            c = self.clock(1)
            bump = dict(c)
            bump["hour"] = (c["hour"] + 3.0) % 24.0
            bump["daysPassed"] = c["daysPassed"] + 3.0 / 24.0
            self.clock(1, {k: bump[k] for k in ("year", "month", "day", "hour", "daysPassed")})
            ok = self.wait_until(lambda: abs(self.drifts()[0]) <= TOL_MIN, SETTLE_S + 4)
            self.check("2 clock: a client that runs away is pulled back", ok, [round(d, 2) for d in self.drifts()])

    def ask_rest(self, minutes: int, answers: Dict[int, str]) -> Dict[str, Any]:
        asker = 1
        r = self.call(asker, "worldtime.rest", {"kind": "wait", "minutes": minutes})
        out = {"vetoed": r.get("proceed") is False}
        self.sleep(1.0)
        self.call(asker, "bot.menu.press", {"button": 1})   # the asker's own "Yes"
        self.sleep(1.0)
        for i in range(len(self.nvs)):
            if i == asker:
                continue
            self.call(i, "bot.consent", {"answer": answers.get(i, "ready")})
        return out

    def step_rest(self) -> None:
        if len(self.nvs) < 2:
            return
        before = [self.clock(i) for i in range(len(self.nvs))]
        epoch0 = self.status(0).get("stats", {}).get("skipsExecuted", 0)
        vetoed = self.ask_rest(120, {})
        self.check("3 rest: the asker's wait is vetoed until the party agrees", vetoed["vetoed"], vetoed)
        ok = self.wait_until(lambda: self.status(0).get("stats", {}).get("skipsExecuted", 0) == epoch0 + 1, SETTLE_S + 4)
        self.check("3 rest: the host executed exactly one skip", ok, self.status(0).get("stats"))
        self.sleep(2.0)
        moved = [minutes_between(self.clock(i), before[i]) for i in range(len(self.nvs))]
        self.check("3 rest: every clock moved by about 120 minutes", all(115.0 <= m <= 130.0 for m in moved), [round(m, 1) for m in moved])
        self.check("3 rest: the clocks agree afterwards", all(abs(d) <= TOL_MIN for d in self.drifts()), [round(d, 2) for d in self.drifts()])

    def step_refused(self) -> None:
        if len(self.nvs) < 3:
            self.log("SKIP 4 refused: needs three instances (the second answerer says Not yet)")
            return
        self.sleep(9.0)   # past the cooldown of an earlier answer
        before = [self.clock(i) for i in range(len(self.nvs))]
        sent0 = self.status(0).get("stats", {}).get("verdictsSent", 0)
        self.ask_rest(60, {2: "notyet"})
        self.sleep(SETTLE_S)
        moved = [minutes_between(self.clock(i), before[i]) for i in range(len(self.nvs))]
        self.check("4 refused: no clock moved by the refused wait", all(m < 5.0 for m in moved), [round(m, 1) for m in moved])
        self.check("4 refused: the host told the asker no", self.status(0).get("stats", {}).get("verdictsSent", 0) > sent0)

    def step_door(self) -> None:
        if not self.door:
            self.log("SKIP 5 door: no --door given")
            return
        self.call(0, "exec", {"cmd": "Activate player", "ref": self.door})
        def read(i: int) -> Optional[int]:
            for o in self.call(i, "worldtime.objects", {"max": 4000}).get("objects", []):
                if o["ref"] == self.door:
                    return o["open"]
            return None
        ok = self.wait_until(lambda: len({read(i) for i in range(len(self.nvs))}) == 1 and read(0) in (1, 3), SETTLE_S)
        self.check("5 door: a native door change shows up on every instance", ok, [read(i) for i in range(len(self.nvs))])
        self.check("5 door: the host sent it as an ObjectState (poll)", self.status(0).get("objects", {}).get("emitted", 0) >= 1, self.status(0).get("objects"))

    def step_saves(self) -> None:
        host = self.status(0).get("autosavesOff")
        cl = [self.status(i).get("autosavesOff") for i in range(1, len(self.nvs))]
        self.check("6 saves: engine autosaves are off on clients and on for the host", host is False and all(cl), {"host": host, "clients": cl})

    def step_travel(self) -> None:
        if not self.marker or len(self.nvs) < 2:
            self.log("SKIP 7 travel: no --party-travel MARKER given")
            return
        before = [self.status(i).get("stats", {}).get("travelsStarted", 0) for i in range(len(self.nvs))]
        r = self.call(1, "worldtime.travel", {"marker": self.marker})
        self.check("7 travel: a party trip is vetoed until answered", r.get("proceed") is False, r)
        self.sleep(1.0)
        self.call(1, "bot.menu.press", {"button": 1})
        self.sleep(1.0)
        for i in range(len(self.nvs)):
            if i != 1:
                self.call(i, "bot.consent", {"answer": "ready"})
        ok = self.wait_until(lambda: all(self.status(i).get("stats", {}).get("travelsStarted", 0) == before[i] + 1 for i in range(len(self.nvs))), SETTLE_S + 6)
        self.check("7 travel: every instance started exactly one trip", ok, [self.status(i).get("stats", {}).get("travelsStarted", 0) for i in range(len(self.nvs))])

    def run(self) -> bool:
        for step in (self.step_module, self.step_clock, self.step_rest, self.step_refused, self.step_door, self.step_saves, self.step_travel):
            step()
        self.log("ALL OK" if self.ok else "FAILED")
        return self.ok


# ------------------------------------------------------------------------------------------------------------------------ fake world
class FakeWorld:
    """Two or three fake instances that behave the way the module is supposed to (and a broken mode for the self test)."""

    def __init__(self, n: int, broken: str = ""):
        self.n = n
        self.broken = broken
        self.t = 0.0
        self.clocks = [{"year": 2281, "month": 10, "day": 23, "hour": 9.0, "daysPassed": 0.0} for _ in range(n)]
        self.stats = [{"beaconsRecv": 1, "skipsExecuted": 0, "verdictsSent": 0, "travelsStarted": 0} for _ in range(n)]
        self.door_open = 3
        self.emitted = 0
        self.asked: Optional[Dict[str, Any]] = None
        self.answers: Dict[int, str] = {}
        self.pressed = False
        self.cooldown_until = 0.0

    def advance_clock(self, c: Dict[str, Any], minutes: float) -> None:
        h = c["hour"] + minutes / 60.0
        extra = int(h // 24)
        c["hour"] = h - 24 * extra
        c["daysPassed"] += minutes / 1440.0
        c["day"] += extra

    def sleep(self, s: float) -> None:
        self.t += s
        for c in self.clocks:
            self.advance_clock(c, s * 0.5)
        # followers pull toward the host
        if self.broken != "nofollow":
            for i in range(1, self.n):
                if abs(minutes_between(self.clocks[i], self.clocks[0])) > 2.5:
                    self.clocks[i] = dict(self.clocks[0])
        if self.asked and self.pressed and len(self.answers) >= self.n - 1:
            ask = self.asked
            self.asked = None
            if all(a == "ready" for a in self.answers.values()):
                if ask["kind"] == "rest" and self.broken != "noskip":
                    for c in self.clocks:
                        self.advance_clock(c, ask["minutes"])
                    self.stats[0]["skipsExecuted"] += 1
                if ask["kind"] == "travel":
                    for s_ in self.stats:
                        s_["travelsStarted"] += 1
            else:
                self.stats[0]["verdictsSent"] += 1
                self.cooldown_until = self.t + 8.0
            self.answers = {}
            self.pressed = False

    def nv(self, i: int) -> Any:
        w = self

        class Bridge:
            def call(self, method: str, params: Optional[Dict[str, Any]] = None, **_: Any) -> Any:
                p = params or {}
                if method == "worldtime.status":
                    s = {"enabled": True, "session": True, "pins": True, "hooked": i == 0, "stats": dict(w.stats[i]), "role": "host" if i == 0 else "client",
                         "autosavesOff": i != 0 if w.broken != "autosave" else False}
                    if i == 0:
                        s["objects"] = {"emitted": w.emitted}
                    return s
                if method == "worldtime.clock":
                    if p.get("set"):
                        w.clocks[i] = dict(p["set"])
                    return dict(w.clocks[i])
                if method == "worldtime.rest":
                    if w.t < w.cooldown_until:
                        return {"proceed": False}
                    w.asked = {"kind": "rest", "minutes": p["minutes"]}
                    w.answers = {}
                    w.pressed = False
                    return {"proceed": False, "awaiting": True}
                if method == "worldtime.travel":
                    w.asked = {"kind": "travel"}
                    w.answers = {}
                    w.pressed = False
                    return {"proceed": False, "awaiting": True}
                if method == "bot.menu.press":
                    w.pressed = True
                    return {}
                if method == "bot.consent":
                    w.answers[i] = p["answer"]
                    return {}
                if method == "exec":
                    if "Activate" in p["cmd"]:
                        w.door_open = 1 if w.door_open == 3 else 3
                        w.emitted += 1
                    return {"ok": True}
                if method == "worldtime.objects":
                    return {"objects": [{"ref": "falloutnv.esm:100", "kind": 0, "open": w.door_open}]}
                raise RuntimeError("fake bridge: unknown method " + method)

        return Bridge()


def selftest() -> int:
    bad = 0

    def run(n: int, broken: str = "") -> Proof:
        w = FakeWorld(n, broken)
        p = Proof([w.nv(i) for i in range(n)], w.sleep, "falloutnv.esm:100", "falloutnv.esm:500", log=lambda *_: None)
        p.run()
        return p

    good = run(3)
    if not good.ok:
        print("selftest: the proof fails against a world that behaves:", [r for r in good.results if not r["pass"]])
        bad += 1
    for mode, step in (("nofollow", "2 clock"), ("noskip", "3 rest"), ("autosave", "6 saves")):
        p = run(3, mode)
        failed = [r["step"] for r in p.results if not r["pass"]]
        if p.ok or not any(f.startswith(step) for f in failed):
            print("selftest: broken world %r was not caught by %r (failed: %s)" % (mode, step, failed))
            bad += 1
    two = run(2)
    if not two.ok:
        print("selftest: two-instance run failed:", [r for r in two.results if not r["pass"]])
        bad += 1
    if bad:
        return 1
    print("worldtime_proof selftest: ALL OK (%d checks on the good run, 3 broken worlds caught)" % len(good.results))
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--instances", type=int, nargs="*", default=[0, 1])
    ap.add_argument("--door")
    ap.add_argument("--party-travel", dest="marker")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    if a.selftest:
        return selftest()
    sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "tools" / "runner"))
    import nvbridge  # noqa: E402

    nvs = [nvbridge.NvBridge.connect(i, wait_s=5.0) for i in a.instances]
    try:
        p = Proof(nvs, time.sleep, a.door, a.marker)
        ok = p.run()
        out = Path(__file__).resolve().parents[3] / "out" / "world-time"
        out.mkdir(parents=True, exist_ok=True)
        (out / "proof.json").write_text(json.dumps(p.results, indent=1))
        return 0 if ok else 1
    finally:
        for nv in nvs:
            nv.close()


if __name__ == "__main__":
    sys.exit(main())
