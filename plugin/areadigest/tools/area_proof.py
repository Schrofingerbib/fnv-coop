#!/usr/bin/env python3
"""In-game proof of the area desync digests (FULL-SYNC-PLAN section 4): a quiet session heals NOTHING, a corrupted copy is healed ONCE.

NOT RUN by the lane that wrote it (code only). The helper runs it on a PC where two or three game instances are already in one co-op session
(host = instance 0) standing near the same NPC, with nvcoop built with NVCOOP_WITH_AREADIGEST, the wiring of plugin/areadigest/area_glue.h
done, `[live] actors=1` and `[areadigest] enabled=1` on every instance (docs/tools/area-desync.md, "In-game proof").

    python -I plugin/areadigest/tools/area_proof.py --instances 0 1 [2] --ref Goodsprings.esm:104C1C
    python -I plugin/areadigest/tools/area_proof.py --selftest      # game-free: the script's own pass / fail logic against a fake world, plus defects

Steps (each prints PASS / FAIL with the numbers it saw):
  1 module   areadigest.status answers on every instance, enabled, running, link up
  2 quiet    30 s of normal play (nothing is touched): the heal counters of every instance do not move, no bug line
  3 health   the console sets the NPC's health to a wrong value on ONE client only (the host owns the NPC): within 25 s that client has the
             owner's health again, it healed exactly one key, nobody else healed anything, the host's value is untouched
  4 stable   20 more seconds: no further heal (a healed key stays healed), no bug line
  5 report   prints the dashboard line (tools/areadigest/dashboard.py) and exits 0 only if the whole run passed
Exit code 0 only if every step passed.
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path
from typing import Any, Callable, Dict, List

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent.parent / "tools"))
from areadigest import dashboard as dash     # noqa: E402

QUIET_S = 30.0
HEAL_WAIT_S = 25.0
STABLE_S = 20.0
WRONG_HP = 7.0


class Proof:
    def __init__(self, nvs: List[Any], sleep: Callable[[float], None], ref: str, log=print):
        self.nvs, self.sleep, self.ref, self.log = nvs, sleep, ref, log
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

    def statuses(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {}
        for i, nv in enumerate(self.nvs):
            try:
                out["host" if i == 0 else f"client{i}"] = nv.call("areadigest.status", {})
            except Exception as e:                    # noqa: BLE001
                out["host" if i == 0 else f"client{i}"] = {"enabled": False, "error": str(e)}
        return out

    def hp(self, i: int) -> float:
        return float(self.nvs[i].eval("GetAV Health", ref=self.ref))

    def heals(self) -> List[int]:
        return [int(((s or {}).get("heals") or {}).get("total", 0)) for s in self.statuses().values()]

    def run(self) -> bool:
        self.log("1 module")
        st = self.statuses()
        for name, s in st.items():
            self.check(bool(s.get("enabled")) and bool(s.get("running")), f"{name}: areadigest.status enabled and running", s.get("error") or s.get("settings"))
        self.check(len(self.nvs) >= 2, "at least two instances")
        if self.failures:
            return False

        self.log("2 quiet")
        before = self.heals()
        self.sleep(QUIET_S)
        after = self.heals()
        s = dash.summarize(self.statuses())
        self.log("  " + dash.dashboard_line(s))
        self.check(after == before, "no heal counter moved in a quiet session", (before, after))
        self.check(not s["bugs"], "no repeated-mismatch bug line", s["bugs"][:2])

        self.log("3 health")
        host_hp = self.hp(0)
        self.nvs[1].exec(f"SetAV Health {WRONG_HP:g}", ref=self.ref)
        self.check(abs(self.hp(1) - WRONG_HP) < 0.6, "the client's copy now has the wrong health", self.hp(1))
        base = self.heals()
        end = time.monotonic() + HEAL_WAIT_S
        healed = False
        while time.monotonic() < end:
            self.sleep(1.0)
            if abs(self.hp(1) - host_hp) <= 5.0:
                healed = True
                break
        self.check(healed, f"the client got the owner's health back within {HEAL_WAIT_S:g} s", self.hp(1))
        self.sleep(3.0)
        now = self.heals()
        moved = [b - a for a, b in zip(base, now)]
        self.check(moved[1] == 1 and sum(moved) == 1, "exactly one key was healed, on the client that was wrong", moved)
        self.check(abs(self.hp(0) - host_hp) < 0.6, "the host's value is untouched", (self.hp(0), host_hp))

        self.log("4 stable")
        self.sleep(STABLE_S)
        later = self.heals()
        s = dash.summarize(self.statuses())
        self.check(later == now, "a healed key stays healed (no further heal)", (now, later))
        self.check(not s["bugs"], "no repeated-mismatch bug line", s["bugs"][:2])

        self.log("5 report")
        self.log("  " + dash.dashboard_line(s))
        return not self.failures


# ---------------------------------------------------------------------------------------------------------------- self-test
class FakeWorld:
    """Two machines, one NPC owned by the host. A virtual clock; `defect` switches on one deliberate bug."""

    def __init__(self, defect: str = ""):
        self.t = 0.0
        self.defect = defect
        self.hp = [100.0, 100.0]
        self.heals = [0, 0]
        self.corrupted_at = None
        self.mode = "running"

    def tick(self, dt: float):
        end = self.t + dt
        while self.t < end:
            self.t += 0.5
            if self.defect == "quiet_heals" and int(self.t) % 17 == 0 and self.t == int(self.t):
                self.heals[1] += 1
            if self.corrupted_at is not None and self.hp[1] != self.hp[0] and self.defect != "no_heal":
                if self.t >= self.corrupted_at + 6.0:
                    self.hp[1] = self.hp[0]
                    self.heals[1] += 2 if self.defect == "heal_twice" else 1
                    if self.defect == "heals_host":
                        self.heals[0] += 1
                    self.corrupted_at = None

    def nv(self, i: int):
        w = self

        class Nv:
            def call(self, method, params=None, **k):
                if method != "areadigest.status":
                    raise RuntimeError("unknown method " + method)
                if w.defect == "disabled":
                    return {"enabled": False}
                by = {c: {"total": 0, "perMin": 0, "mismatches": 0} for c in dash.CATS}
                by["actors"]["total"] = w.heals[i]
                return {"enabled": True, "running": True, "role": "host" if i == 0 else "client", "heals": {"total": w.heals[i], "perMin": w.heals[i], "byCat": by},
                        "bugs": [{"cat": "actors", "key": "0x5", "count": 2, "authority": "hp 100", "other": "hp 7"}] if w.defect == "bug_line" else []}

            def eval(self, expr, ref=None):
                return w.hp[i]

            def exec(self, cmd, ref=None, **k):
                if cmd.startswith("SetAV Health"):
                    w.hp[i] = float(cmd.split()[-1])
                    w.corrupted_at = w.t
                return {"ok": True, "output": ""}
        return Nv()


def selftest() -> int:
    quiet = lambda *_a, **_k: None          # noqa: E731

    def one(defect: str) -> bool:
        w = FakeWorld(defect)
        p = Proof([w.nv(0), w.nv(1)], lambda s: w.tick(s), "Goodsprings.esm:104C1C", log=quiet)
        return p.run()

    ok = one("")
    print("proof against the correct fake world:", "PASS" if ok else "FAIL")
    bad = [] if ok else ["the correct world failed the proof"]
    for d in ("no_heal", "heal_twice", "heals_host", "quiet_heals", "bug_line", "disabled"):
        if one(d):
            bad.append(f"defect '{d}' was NOT caught")
        else:
            print(f"defect {d}: caught")
    if bad:
        print("FAILED:", "; ".join(bad))
        return 1
    print("ALL OK")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--instances", nargs="+", type=int, default=[0, 1])
    ap.add_argument("--ref", default="", help="an NPC reference the host owns, Plugin.esm:HEX")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    if a.selftest:
        return selftest()
    if not a.ref:
        print("--ref is required (an NPC all instances have loaded and the host owns)")
        return 2
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent.parent / "tools" / "runner"))
    from nvbridge import NvBridge                          # type: ignore
    nvs = [NvBridge.connect(instance=i, wait_s=10) for i in a.instances]
    ok = Proof(nvs, time.sleep, a.ref).run()
    print("ALL OK" if ok else "FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
