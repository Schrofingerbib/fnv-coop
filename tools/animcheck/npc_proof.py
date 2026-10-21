"""In-game proof for the NPC animation layer (plugin/actors/anim). THE HELPER RUNS THIS on his PC, one game (caps) or one pair (sync) at a
time. It has been WRITTEN but never run against a game; `--selftest` runs only the offline part (a fake game, no bridge).

    python -I tools/animcheck/npc_proof.py caps --instance 0 --ref FalloutNV.esm:104C2F [--weapon] [--kill]
    python -I tools/animcheck/npc_proof.py sync --host 0 --client 1 --seconds 60 [--map HOSTREF=CLIENTREF] [--out out/npc/run1]
    python -I tools/animcheck/npc_proof.py --selftest

caps (ONE game): proves, one primitive at a time, what the animation layer assumes about the engine. Setup: nvcoop.ini [dev] animprobe=1 and
[anim] enabled=1, a loaded save, an ordinary NPC standing near the player (--ref), --weapon when that NPC carries a weapon (additem it first),
--kill to also prove the corpse placement (destroys the NPC: use a throw-away save). Every step prints PASS / FAIL / INFO and what it saw:
   1  anim.npc answers and the pinned direct calls (SetWeaponOut, SetIsAiming, MenuMode) are present in this exe
   2  the NPC can be restrained and read (valid, 3D)
   3  group numbering: PlayGroup Forward / FastForward / Left / TurnRight show up as animating groups 3 / 7 / 5 / 16 (INFO: which AnimData slot)
   4  a held group stays; a non-held group ends on its own
   5  the T-pose guard: a fresh Idle leaves a running sequence
   6  weapon draw / holster with the direct call and with the Equip / Unequip groups (needs --weapon); aim flag
   7  one-shots are readable as the weapon slot's action: AttackRight (32) and Stagger (223) read back as actionGroup (needs --weapon for the attack)
   8  playback rate (INFO: unsupported unless [anim] directrate=1) and knockdown (INFO: unsupported unless directknock=1)
   9  death and corpse placement (needs --kill): KillActor makes the NPC read dead, SetPos moves the body and it stays there
sync (TWO games in one co-op session, NPCs owned by the host fighting or walking): polls anim.probe on both machines for --seconds, then
runs the NPC fight assertions (tools/animcheck/npc.py) and prints the anim.npc counters of both machines. Run the fight playbook next to it.
Exit code: 0 = no FAIL, 1 = a FAIL, 2 = bad arguments, 3 = not ready (anim.npc missing: nothing was judged).
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parents[2]
if __package__ in (None, ""):
    sys.path.insert(0, str(ROOT / "tools"))
    sys.path.insert(0, str(ROOT / "tools" / "runner"))
    __package__ = "animcheck"

from .config import mutant  # noqa: E402
from .npc import analyze_npc, check_fight  # noqa: E402

# xNVSE kAnimGroup_* ids the layer relies on (tools/animcheck/animgroups_table.py): proven here against the running game
GROUPS = {"Idle": 0, "Left": 5, "Forward": 3, "FastForward": 7, "TurnRight": 16, "Equip": 24, "Unequip": 25, "AttackRight": 32, "Stagger": 223}

Result = Tuple[str, str, str]   # (status PASS|FAIL|INFO|SKIP, step, detail)


class NotReady(Exception):
    pass


class Proof:
    """Runs the caps steps against any object with call(method, params) and exec(cmd, ref=...)."""

    def __init__(self, bridge: Any, ref: str, weapon: bool = False, kill: bool = False, sleep: Callable[[float], None] = time.sleep):
        self.b = bridge
        self.ref = ref
        self.weapon = weapon
        self.kill = kill
        self.sleep = sleep
        self.results: List[Result] = []

    # ---- helpers -------------------------------------------------------------------------------------------------------------------------
    def add(self, status: str, step: str, detail: str = "") -> None:
        if mutant() == "npc_proof_pass" and status == "FAIL":
            status = "PASS"
        self.results.append((status, step, detail))

    def do(self, op: str, **kw) -> dict:
        r = self.b.call("anim.npc.do", {"ref": self.ref, "op": op, **kw})
        return json.loads(r) if isinstance(r, str) else r

    def read(self) -> dict:
        return self.do("read")

    def probe(self) -> dict:
        r = self.b.call("anim.probe", {"refs": [self.ref], "bones": False})
        for a in r.get("actors", []):
            return a
        return {}

    def playing(self) -> Dict[int, int]:
        """{group: slot} of every animating sequence of the NPC right now (anim.probe)."""
        out: Dict[int, int] = {}
        for s in self.probe().get("anim", {}).get("seqs", []):
            if s.get("state") == 1:
                out[int(s.get("group", -1))] = int(s.get("slot", -1))
        return out

    def wait(self, pred: Callable[[], bool], timeout_s: float = 1.5, step_s: float = 0.1) -> bool:
        t = 0.0
        while t <= timeout_s:
            if pred():
                return True
            self.sleep(step_s)
            t += step_s
        return pred()

    # ---- steps ---------------------------------------------------------------------------------------------------------------------------
    def step_pins(self) -> None:
        try:
            r = self.b.call("anim.npc", {})
        except Exception as e:
            raise NotReady(f"anim.npc is not answering: {e}")
        r = json.loads(r) if isinstance(r, str) else r
        if not r.get("enabled"):
            raise NotReady("anim.npc says enabled=false ([anim] enabled=0?)")
        pins = r.get("pins", {})
        self.add("PASS" if pins.get("set_weapon_out") else "FAIL", "1 pin SetWeaponOut (slot 0x116)", str(pins.get("set_weapon_out")))
        self.add("PASS" if pins.get("menu_mode") else "FAIL", "1 pin MenuMode (0x702360)", str(pins.get("menu_mode")))
        self.add("INFO", "1 other pins", f"set_aiming={pins.get('set_aiming')} push_actor_away={pins.get('push_actor_away')} direct flags: "
                 f"weapon_out={pins.get('direct_weapon_out')} aiming={pins.get('direct_aiming')} rate={pins.get('direct_rate')} knock={pins.get('direct_knock')}")

    def step_read(self) -> None:
        self.b.exec("SetRestrained 1", ref=self.ref)
        r = self.read()
        ok = bool(r.get("valid")) and bool(r.get("has3D"))
        self.add("PASS" if ok else "FAIL", "2 restrain + read", f"valid={r.get('valid')} has3D={r.get('has3D')} dead={r.get('dead')} moveGroup={r.get('moveGroup')} moveFlags={r.get('moveFlags')}")
        if not ok:
            raise NotReady("the NPC cannot be read: wrong ref, not loaded, or no 3D")

    def step_numbering(self) -> None:
        slots = set()
        for name in ("Forward", "FastForward", "Left", "TurnRight"):
            g = GROUPS[name]
            res = self.do("play", group=g, hold=True)
            seen = self.wait(lambda: g in self.playing())
            slot = self.playing().get(g)
            if slot is not None:
                slots.add(slot)
            self.add("PASS" if res.get("result") == "ok" and seen else "FAIL", f"3 PlayGroup {name} = group {g}", f"result={res.get('result')} playing={sorted(self.playing())}")
        self.add("INFO", "3 AnimData slots that held a movement group", str(sorted(slots)) + " (the layer treats every non-action group as the movement slot)")

    def step_hold(self) -> None:
        g = GROUPS["Forward"]
        self.do("play", group=g, hold=True)
        self.wait(lambda: g in self.playing())
        self.sleep(3.0)
        self.add("PASS" if g in self.playing() else "FAIL", "4 a held group stays for 3 s", f"playing={sorted(self.playing())}")
        self.do("play", group=g, hold=False)
        self.sleep(3.0)
        self.add("INFO", "4 a non-held group after 3 s", f"playing={sorted(self.playing())} (it should have ended by itself)")

    def step_tpose(self) -> None:
        self.do("play", group=GROUPS["Idle"], hold=True)
        ok = self.wait(lambda: len(self.playing()) > 0)
        r = self.read()
        self.add("PASS" if ok and r.get("moveGroup", -1) != -2 else "FAIL", "5 Idle leaves a running sequence (no T-pose)", f"playing={sorted(self.playing())} moveGroup={r.get('moveGroup')}")

    def step_weapon(self) -> None:
        if not self.weapon:
            self.add("SKIP", "6 weapon draw / holster / aim", "pass --weapon with an NPC that carries a weapon")
            return
        for want in (True, False):
            res = self.do("weapon", on=want)
            ok = self.wait(lambda: bool(self.read().get("weaponOut")) == want, 2.0)
            self.add("PASS" if ok else "FAIL", f"6 SetWeaponDrawn({want}) reads back as IsWeaponOut", f"result={res.get('result')} weaponOut={self.read().get('weaponOut')}")
        self.do("weapon", on=True)
        self.wait(lambda: bool(self.read().get("weaponOut")), 2.0)
        self.do("play", group=GROUPS["Unequip"], hold=False)
        seen = self.wait(lambda: GROUPS["Unequip"] in self.playing(), 0.6)
        self.add("INFO" if not seen else "PASS", "6 the Unequip group plays", f"playing={sorted(self.playing())}")
        res = self.do("aim", on=True)
        ok = self.wait(lambda: bool(self.read().get("aiming")), 1.0)
        self.add("PASS" if ok else "FAIL", "6 SetIsAiming(true) reads back", f"result={res.get('result')} aiming={self.read().get('aiming')}")
        self.do("aim", on=False)
        self.do("weapon", on=False)

    def step_oneshots(self) -> None:
        for name in ("AttackRight", "Stagger"):
            if name == "AttackRight" and not self.weapon:
                self.add("SKIP", "7 AttackRight reads back as the action", "needs --weapon")
                continue
            g = GROUPS[name]
            if name == "AttackRight":
                self.do("weapon", on=True)
                self.sleep(0.8)
            self.do("play", group=g, hold=False)
            ok = self.wait(lambda: self.read().get("actionGroup") == g, 0.8, 0.05)
            r = self.read()
            self.add("PASS" if ok else "FAIL", f"7 {name} ({g}) reads back as actionGroup", f"actionGroup={r.get('actionGroup')} actionPlayMs={r.get('actionPlayMs')} playing={sorted(self.playing())}")
            self.sleep(1.2)
        self.do("weapon", on=False)

    def step_optional(self) -> None:
        self.do("play", group=GROUPS["Forward"], hold=True)
        res = self.do("rate", value=1.5)
        if res.get("result") == "ok":
            self.sleep(0.3)
            freqs = [s.get("freq") for s in self.probe().get("anim", {}).get("seqs", []) if s.get("state") == 1]
            self.add("PASS" if any(abs(float(f) - 1.5) < 0.05 for f in freqs if f is not None) else "FAIL", "8 playback rate 1.5 shows as freq", f"freqs={freqs}")
        else:
            self.add("INFO", "8 playback rate", f"{res.get('result')} (enable [anim] directrate=1 to test it)")
        self.do("rate", value=1.0)
        res = self.do("knock", yaw=0.0, force=1.0)
        if res.get("result") == "ok":
            ok = self.wait(lambda: bool(self.read().get("knocked")) or bool(self.read().get("ragdoll")), 1.5)
            self.add("PASS" if ok else "FAIL", "8 PushActorAway knocks the NPC down", f"read={self.read()}")
            self.do("getup")
        else:
            self.add("INFO", "8 knockdown", f"{res.get('result')} (enable [anim] directknock=1 to test it)")

    def step_corpse(self) -> None:
        if not self.kill:
            self.add("SKIP", "9 death and corpse placement", "pass --kill (destroys the NPC: use a throw-away save)")
            return
        res = self.do("kill")
        ok = self.wait(lambda: bool(self.read().get("dead")), 2.0)
        self.add("PASS" if ok else "FAIL", "9 KillActor makes the NPC read dead", f"result={res.get('result')} dead={self.read().get('dead')}")
        self.sleep(4.0)
        r0 = self.read()
        if "x" not in r0:
            self.add("FAIL", "9 the body has a position", str(r0))
            return
        tx, ty = float(r0["x"]) + 200.0, float(r0["y"])
        self.do("place", x=tx, y=ty, z=float(r0["z"]), yaw=1.0)
        self.sleep(0.5)
        r1 = self.read()
        near = abs(float(r1.get("x", 0)) - tx) < 5.0 and abs(float(r1.get("y", 0)) - ty) < 5.0
        self.add("PASS" if near else "FAIL", "9 SetPos moves the dead body", f"wanted ({tx:.0f},{ty:.0f}) got ({r1.get('x')},{r1.get('y')})")
        self.sleep(2.0)
        r2 = self.read()
        stays = abs(float(r2.get("x", 0)) - tx) < 8.0 and abs(float(r2.get("y", 0)) - ty) < 8.0
        self.add("PASS" if stays else "FAIL", "9 the body stays where it was placed for 2 s", f"now ({r2.get('x')},{r2.get('y')})")

    def run(self) -> List[Result]:
        for step in (self.step_pins, self.step_read, self.step_numbering, self.step_hold, self.step_tpose, self.step_weapon, self.step_oneshots,
                     self.step_optional, self.step_corpse):
            step()
        return self.results


def report(results: List[Result]) -> int:
    bad = 0
    for status, step, detail in results:
        print(f"[{status}] {step}" + (f"   {detail}" if detail else ""))
        bad += status == "FAIL"
    print(f"npc_proof caps: {len(results)} steps, {bad} FAIL")
    return 1 if bad else 0


# ---- sync -----------------------------------------------------------------------------------------------------------------------------------
def run_sync(host: int, client: int, seconds: float, ref_map: Dict[int, int], out: Optional[str]) -> int:
    from .collect import Collector
    with Collector.from_instances({"host": host, "client": client}, out_dir=out) as c:
        c.run(seconds=seconds, hz=20.0, params={"near": {"radius": 3000}, "max": 16})
        streams = c.streams()
        counters = {}
        for m, b in c.bridges.items():
            try:
                r = b.call("anim.npc", {})
                counters[m] = json.loads(r) if isinstance(r, str) else r
            except Exception as e:
                counters[m] = {"error": repr(e)}
    rep = analyze_npc(streams, owner="host", ref_maps={"client": ref_map} if ref_map else None, label="npc_proof sync")
    for m, c in counters.items():
        st = c.get("stats", {})
        d = st.get("driver", {})
        print(f"{m}: anim.npc enabled={c.get('enabled')} drives={d.get('drives')} refused={d.get('refused')} tpose_fixes={d.get('tpose_fixes')} weapon_ladder_giveups={d.get('weapon_ladder_giveups')} "
              f"one_shots_played={d.get('one_shots_played')} corpse_corrections={d.get('corpse_corrections')} corpse_missing={d.get('corpse_missing')}")
    for k in sorted(rep["counters"]):
        if k.startswith(("attack", "reaction", "reload", "weapon_never", "corpse", "glitch", "wrong", "dead", "pose_err", "pos_err")):
            print(f"  {k} = {rep['counters'][k]}")
    fails = check_fight(rep)
    for f in fails:
        print("FAIL:", f)
    print("npc_proof sync:", "OK" if not fails else f"{len(fails)} FAIL")
    return 1 if fails else 0


# ---- selftest: a fake game -------------------------------------------------------------------------------------------------------------------
class FakeGame:
    """Models just enough of the game for the caps steps: PlayGroup, the weapon / aim flags, one-shots, rate, knock, kill, place."""

    def __init__(self, numbering_offset: int = 0, weapon_works: bool = True, rate: bool = True, knock: bool = True, placed_moves: bool = True):
        self.t = 0.0
        self.numbering_offset = numbering_offset   # the game's group number differs from the table by this
        self.weapon_works, self.rate_ok, self.knock_ok, self.placed_moves = weapon_works, rate, knock, placed_moves
        self.moves: Dict[int, Tuple[int, bool, float]] = {}   # group -> (slot, hold, started)
        self.action = -1
        self.action_at = 0.0
        self.weapon = self.aim = self.knocked = self.dead = False
        self.pos = [1000.0, 2000.0, 100.0]
        self.freq = 1.0
        self.restrained = False

    def sleep(self, s: float) -> None:
        self.t += s

    def _live(self) -> Dict[int, int]:
        out = {}
        for g, (slot, hold, t0) in self.moves.items():
            if hold or self.t - t0 < 1.5:
                out[g] = slot
        return out

    def exec(self, cmd: str, ref: Optional[str] = None):
        if cmd.startswith("SetRestrained"):
            self.restrained = True
        return {"ok": True, "output": "", "ref": ref}

    def call(self, method: str, params: Optional[dict] = None):
        p = params or {}
        if method == "anim.npc":
            return {"enabled": True, "pins": {"set_weapon_out": True, "set_aiming": True, "push_actor_away": True, "menu_mode": True, "direct_weapon_out": True,
                                              "direct_aiming": True, "direct_rate": self.rate_ok, "direct_knock": self.knock_ok}, "stats": {"driver": {}}}
        if method == "anim.probe":
            seqs = [{"slot": slot, "group": g, "state": 1, "freq": self.freq} for g, slot in self._live().items()]
            if self.action >= 0 and self.t - self.action_at < 0.9:
                seqs.append({"slot": 4, "group": self.action, "state": 1, "freq": 1.0})
            return {"actors": [{"ref": p["refs"][0], "anim": {"seqs": seqs}, "dead": self.dead}]}
        if method != "anim.npc.do":
            raise RuntimeError("unknown method " + method)
        op = p["op"]
        if op == "read":
            live = self._live()
            act = self.action if self.action >= 0 and self.t - self.action_at < 0.9 else -1
            return {"ok": True, "valid": True, "has3D": True, "dead": self.dead, "knocked": self.knocked, "ragdoll": self.knocked, "weaponOut": self.weapon, "aiming": self.aim,
                    "moveFlags": 0, "moveGroup": (next(iter(live)) if live else -2), "actionGroup": act, "actionPlayMs": int((self.t - self.action_at) * 1000) if act >= 0 else -1,
                    "x": self.pos[0], "y": self.pos[1], "z": self.pos[2], "yaw": 0.0}
        if op == "play":
            g = int(p["group"]) + self.numbering_offset
            if g in (32 + self.numbering_offset, 223 + self.numbering_offset):
                self.action, self.action_at = int(p["group"]), self.t
            else:
                self.moves = {g: (1, bool(p.get("hold", True)), self.t)}
            return {"result": "ok"}
        if op == "weapon":
            if self.weapon_works:
                self.weapon = bool(p["on"])
            return {"result": "ok"}
        if op == "aim":
            self.aim = bool(p["on"])
            return {"result": "ok"}
        if op == "rate":
            if not self.rate_ok:
                return {"result": "unsupported"}
            self.freq = float(p["value"])
            return {"result": "ok"}
        if op == "knock":
            if not self.knock_ok:
                return {"result": "unsupported"}
            self.knocked = True
            return {"result": "ok"}
        if op == "getup":
            self.knocked = False
            return {"result": "ok"}
        if op == "kill":
            self.dead = True
            return {"result": "ok"}
        if op == "place":
            if self.placed_moves:
                self.pos = [float(p["x"]), float(p["y"]), float(p["z"])]
            return {"result": "ok"}
        return {"result": "refused"}


def selftest() -> int:
    ok = True

    def run(game: FakeGame, **kw) -> List[Result]:
        pr = Proof(game, "0x00104C2F", sleep=game.sleep, **kw)
        return pr.run()

    good = run(FakeGame(), weapon=True, kill=True)
    fails = [r for r in good if r[0] == "FAIL"]
    print(f"selftest good game: {len(good)} steps, {len(fails)} FAIL")
    ok &= not fails and len(good) >= 20
    for label, game in (("another numbering", FakeGame(numbering_offset=1)), ("weapon call ignored", FakeGame(weapon_works=False)), ("SetPos does not move a body", FakeGame(placed_moves=False))):
        res = run(game, weapon=True, kill=True)
        bad = [r for r in res if r[0] == "FAIL"]
        print(f"selftest {label}: {len(bad)} FAIL (must be > 0)")
        ok &= bool(bad)
    # sync judge on synthetic streams: a clean pair passes, a pair where the copy never attacks fails
    from . import synth
    from .model import stream_from_probes
    from .synth import G, NPC_REF, State, make_probes

    def sc(attack: bool):
        def f(t: float) -> State:
            s = synth.idler()(t)
            if attack and 2000 <= t < 2500:
                s.groups = list(s.groups) + [(4, G["AttackRight"])]
            return s
        return f
    clean = {"host": stream_from_probes("host", make_probes("host", {NPC_REF: sc(True)}, 6000.0), use_rx=True),
             "client": stream_from_probes("client", make_probes("client", {NPC_REF: synth.delayed(sc(True), 120.0)}, 6000.0), use_rx=True)}
    bad = {"host": clean["host"], "client": stream_from_probes("client", make_probes("client", {NPC_REF: synth.delayed(sc(False), 120.0)}, 6000.0), use_rx=True)}
    f_clean, f_bad = check_fight(analyze_npc(clean, owner="host")), check_fight(analyze_npc(bad, owner="host"))
    print(f"selftest sync judge: clean {len(f_clean)} failures (must be 0), copy without the attack {len(f_bad)} (must be > 0)")
    ok &= not f_clean and bool(f_bad)
    print("npc_proof selftest:", "OK" if ok else "FAILED")
    return 0 if ok else 1


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("mode", nargs="?", choices=("caps", "sync"))
    ap.add_argument("--instance", type=int, default=0)
    ap.add_argument("--ref", default="")
    ap.add_argument("--weapon", action="store_true")
    ap.add_argument("--kill", action="store_true")
    ap.add_argument("--host", type=int, default=0)
    ap.add_argument("--client", type=int, default=1)
    ap.add_argument("--seconds", type=float, default=60.0)
    ap.add_argument("--map", action="append", default=[])
    ap.add_argument("--out", default=None)
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args(argv)
    if a.selftest:
        return selftest()
    if a.mode == "caps":
        if not a.ref:
            print("caps needs --ref (an NPC reference, e.g. FalloutNV.esm:104C2F or a runtime id)")
            return 2
        from nvbridge import NvBridge  # type: ignore
        try:
            with NvBridge.connect(instance=a.instance, wait_s=10.0) as nv:
                return report(Proof(nv, a.ref, weapon=a.weapon, kill=a.kill).run())
        except NotReady as e:
            print("NOT READY:", e)
            return 3
    if a.mode == "sync":
        rm = {}
        for m in a.map:
            x, y = m.split("=", 1)
            rm[int(x, 16)] = int(y, 16)
        return run_sync(a.host, a.client, a.seconds, rm, a.out)
    ap.print_usage()
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
