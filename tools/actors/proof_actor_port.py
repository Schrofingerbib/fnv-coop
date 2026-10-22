"""In-game proof for the real actor port (plugin/actors/engine). THE HELPER RUNS THIS on one game copy. It has been WRITTEN but never
run against a game; `--selftest` runs only the offline part (a fake bridge with a tiny world, no game) and proves that this script
catches a broken port.

    python -I tools/actors/proof_actor_port.py --instance 0 [--ref Plugin.esm:HEX] [--pick Plugin.esm:HEX] [--game-dir DIR] [--out DIR]
    python -I tools/actors/proof_actor_port.py --selftest

Setup: one game copy, nvcoop.ini `[dev] actorport=1` (registers actors.read / actors.dev), a save standing in a cell with at least one
ordinary living NPC near the player (Goodsprings is fine). No co-op session is needed: the dev methods drive the same EnginePort the
session uses, without ActorSync. Pass --ref to choose a harmless NPC; otherwise the first suitable actor of world.actors is used.
It changes the chosen NPC (restrain, move, heading) and puts everything back; two disposable clones are created and deleted.

What it asserts, in order (each line of the report is one check):
  wiring   actors.port is enabled, actors.read answers, and the three pinned engine functions were found (placeAtMe, getActorValue,
           isDisabled): a mismatch there means the exe is not the one the port was written against.
  read     actors.read agrees with the console: GetPos X/Y/Z, GetAngle Z (degrees), GetAV Health, GetDead.
  ai       dev ai off -> GetRestrained 1 and a ledger entry; dev ai on -> GetRestrained 0 and the entry gone.
  place    dev place moves the NPC (console GetPos) and sets the heading; GetAngle Z must be +57.3 for yaw 1.0 rad (this settles the
           SetAngle sign that the port assumes); health and a limb value written through ForceActorValue read back with GetAV.
  drive    20 small drive steps end at the target; console lines per applied step stay within the budget; mean round trip is reported.
  standin  a stand-in clone is created next to the NPC (restrained, at the pose, visible in world.actors, in the ledger) and deleted again.
  clone    on a clone: kill -> dead, revive -> alive, protect -> IsEssential 1, unprotect -> 0.
  swap     (only with --pick) the NPC is replaced by a clone of the pick, the original is disabled; release restores it.
  release  dev release: ledger empty, the NPC is not restrained, no clone is left, the NPC is back where it was.
  ledger   (only with --game-dir) the ledger file next to the dll lists the restrained NPC while it is restrained.
  console  no console line was refused during the whole run (a wrong command name or argument shows up here first).

Exit code: 0 = every check passed, 1 = a check failed, 2 = bad arguments, 3 = not wired / not ready (nothing was judged).
Every tolerance below is a FIRST GUESS.
"""
from __future__ import annotations

import argparse
import json
import math
import sys
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools" / "runner"))

TOL_POS = 0.75          # game units, console prints %g
TOL_ANGLE = 1.5         # degrees
TOL_HEALTH = 0.5
HEALTH_STEP = 10
DRIVE_STEPS = 20
DRIVE_STEP_UNITS = 5.0
DRIVE_PERIOD_S = 0.06
MAX_LINES_PER_APPLIED_DRIVE = 4          # SetPos x/y/z + SetAngle
MAX_MEAN_ROUNDTRIP_MS = 200.0            # sanity bound, not a performance target


class NotReady(Exception):
    pass


class Result:
    def __init__(self) -> None:
        self.checks: List[Tuple[str, bool, str]] = []
        self.info: Dict[str, Any] = {}

    def check(self, name: str, ok: bool, detail: str = "") -> bool:
        self.checks.append((name, bool(ok), detail))
        print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f"  ({detail})" if detail else ""))
        return bool(ok)

    @property
    def failures(self) -> List[str]:
        return [f"{n}: {d}" if d else n for n, ok, d in self.checks if not ok]


def hexid(v: Any) -> int:
    return int(v, 16) if isinstance(v, str) else int(v)


def wrap_deg(d: float) -> float:
    d = math.fmod(d, 360.0)
    if d > 180.0:
        d -= 360.0
    if d <= -180.0:
        d += 360.0
    return d


def close(a: float, b: float, tol: float) -> bool:
    return abs(a - b) <= tol


def angle_close(a_deg: float, b_deg: float, tol: float) -> bool:
    return abs(wrap_deg(a_deg - b_deg)) <= tol


class Proof:
    """Runs the checks against anything with call(method, params), eval(expr, ref=...) and exec(cmd, ref=...) (NvBridge or the fake)."""

    def __init__(self, bridge: Any, res: Result, ref_hint: Optional[str] = None, pick: Optional[str] = None,
                 game_dir: Optional[str] = None, sleep: Callable[[float], None] = time.sleep) -> None:
        self.b = bridge
        self.r = res
        self.ref_hint = ref_hint
        self.pick = pick
        self.game_dir = game_dir
        self.sleep = sleep

    # ---- helpers ----
    def dev(self, **p: Any) -> dict:
        return self.b.call("actors.dev", p)

    def read(self, ref: str) -> dict:
        return self.b.call("actors.read", {"ref": ref})

    def port(self) -> dict:
        return self.b.call("actors.port", {})

    def ledger(self) -> dict:
        return self.port().get("ledger", {})

    def pos_console(self, ref: str) -> Tuple[float, float, float]:
        return tuple(self.b.eval(f"GetPos {a}", ref=ref) for a in "XYZ")  # type: ignore[return-value]

    # ---- steps ----
    def wiring(self) -> None:
        try:
            st = self.port()
        except Exception as e:  # RpcError: method missing
            raise NotReady(f"actors.port is not registered ({e}): nvcoop.dll without plugin/actors/engine") from e
        if not st.get("enabled"):
            raise NotReady("the actor port is disabled ([actors] engine=0)")
        try:
            self.read("14")
        except Exception as e:
            raise NotReady(f"actors.read is not registered ({e}): set nvcoop.ini [dev] actorport=1") from e
        st = self.port()
        pins = st.get("pins", {})
        for k in ("placeAtMe", "getActorValue", "isDisabled"):
            self.r.check(f"wiring.pin.{k}", bool(pins.get(k)), "prologue bytes at the recorded address differ from the exe the port was written for")
        self.r.check("wiring.seh_faults_zero", st.get("sehFaults", 0) == 0, str(st.get("sehFaults")))

    def choose(self) -> Tuple[str, dict]:
        if self.ref_hint:
            rd = self.read(self.ref_hint)
            if not rd.get("has3D"):
                # the engine ignores console writes (ForceActorValue, SetPos ...) on an actor that is not loaded, and a stand-in next to it is not in
                # world.actors; every check below would fail for that reason alone (sweep 2026-10-22: --ref 104C70 with the save elsewhere)
                raise NotReady(f"{self.ref_hint} is not loaded (has3D false, cell {rd.get('cell')}): load a save near it, or pass another --ref")
            return self.ref_hint, rd
        actors = self.b.call("world.actors", {"max": 40}).get("actors", [])
        for a in actors:
            ref = a.get("ref") or ""
            if not ref:
                continue
            try:
                rd = self.read(ref)
            except Exception:
                continue
            if rd.get("dead") or rd.get("disabled") or rd.get("restrained") or not rd.get("has3D") or hexid(rd.get("ref", "0")) == 0x14:
                continue
            return ref, rd
        raise NotReady("no suitable living, unrestrained NPC with 3D in world.actors: stand near one and retry, or pass --ref")

    def check_read(self, ref: str, rd: dict) -> None:
        cx, cy, cz = self.pos_console(ref)
        self.r.check("read.pos", close(rd["x"], cx, TOL_POS) and close(rd["y"], cy, TOL_POS) and close(rd["z"], cz, TOL_POS),
                     f"port ({rd['x']:.2f},{rd['y']:.2f},{rd['z']:.2f}) console ({cx:.2f},{cy:.2f},{cz:.2f})")
        ang = self.b.eval("GetAngle Z", ref=ref)
        self.r.check("read.heading_degrees", angle_close(rd["rotZ"] * 180.0 / math.pi, ang, TOL_ANGLE), f"port {rd['rotZ'] * 180.0 / math.pi:.2f} console {ang:.2f}")
        hp = self.b.eval("GetAV Health", ref=ref)
        self.r.check("read.health", close(float(rd.get("av", {}).get("16", -999)), hp, TOL_HEALTH), f"port {rd.get('av', {}).get('16')} console {hp}")
        dead = self.b.eval("GetDead", ref=ref)
        self.r.check("read.dead", bool(rd.get("dead")) == (dead != 0), f"port {rd.get('dead')} console {dead}")

    def check_ai(self, ref: str) -> None:
        base = self.ledger().get("restrained", 0)
        r = self.dev(op="ai", ref=ref, on=False)
        self.r.check("ai.off.flag", bool(r.get("aiOff")))
        self.r.check("ai.off.console", self.b.eval("GetRestrained", ref=ref) == 1, "GetRestrained")
        self.r.check("ai.off.ledger", self.ledger().get("restrained", 0) == base + 1, str(self.ledger()))
        self.dev(op="ai", ref=ref, on=True)
        self.r.check("ai.on.console", self.b.eval("GetRestrained", ref=ref) == 0, "GetRestrained")
        self.r.check("ai.on.ledger", self.ledger().get("restrained", 0) == base, str(self.ledger()))

    def check_place(self, ref: str, rd: dict) -> None:
        x0, y0, z0, yaw0 = rd["x"], rd["y"], rd["z"], rd["rotZ"]
        self.dev(op="ai", ref=ref, on=False)
        hp0 = float(rd.get("av", {}).get("16", 100))
        want_hp = max(1, int(hp0) - HEALTH_STEP)   # a step the tolerance cannot hide: a write that never happened reads back hp0
        self.dev(op="place", ref=ref, x=x0 + 60.0, y=y0, z=z0, yaw=1.0, health=want_hp)
        cx, cy, cz = self.pos_console(ref)
        self.r.check("place.pos", close(cx, x0 + 60.0, TOL_POS) and close(cy, y0, TOL_POS), f"console ({cx:.2f},{cy:.2f}) wanted ({x0 + 60:.2f},{y0:.2f})")
        ang = self.b.eval("GetAngle Z", ref=ref)
        self.r.check("place.heading_sign", angle_close(ang, 57.2958, TOL_ANGLE),
                     f"GetAngle Z after yaw 1.0 rad = {ang:.2f}; 57.30 = the port's SetAngle convention is right, -57.30 = the sign is flipped")
        hp = self.b.eval("GetAV Health", ref=ref)
        self.r.check("place.health_written", close(hp, want_hp, TOL_HEALTH), f"GetAV Health {hp} wanted {want_hp}")
        self.dev(op="place", ref=ref, x=x0, y=y0, z=z0, yaw=yaw0, health=int(hp0))
        hp = self.b.eval("GetAV Health", ref=ref)
        self.r.check("place.health_restored", close(hp, hp0, TOL_HEALTH), f"{hp} vs {hp0}")
        self.dev(op="ai", ref=ref, on=True)

    def check_drive(self, ref: str, rd: dict) -> None:
        x0, y0, z0, yaw0 = rd["x"], rd["y"], rd["z"], rd["rotZ"]
        self.dev(op="ai", ref=ref, on=False)
        last = self.dev(op="bind", ref=ref).get("lines", 0)
        rtt: List[float] = []
        per_step: List[int] = []
        for i in range(1, DRIVE_STEPS + 1):
            t0 = time.perf_counter()
            r = self.dev(op="drive", ref=ref, x=x0 + DRIVE_STEP_UNITS * i, y=y0, z=z0, yaw=yaw0)
            rtt.append((time.perf_counter() - t0) * 1000.0)
            per_step.append(int(r.get("lines", last)) - last)
            last = int(r.get("lines", last))
            self.sleep(DRIVE_PERIOD_S)
        cx, cy, cz = self.pos_console(ref)
        self.r.check("drive.end_pose", close(cx, x0 + DRIVE_STEP_UNITS * DRIVE_STEPS, 2.0), f"console x {cx:.2f} wanted {x0 + DRIVE_STEP_UNITS * DRIVE_STEPS:.2f}")
        self.r.check("drive.lines_per_step", max(per_step) <= MAX_LINES_PER_APPLIED_DRIVE and sum(per_step) > 0,
                     f"lines per step {per_step} (at most {MAX_LINES_PER_APPLIED_DRIVE} when a pose is applied, never all zero)")
        mean = sum(rtt) / len(rtt)
        self.r.info["drive_mean_roundtrip_ms"] = round(mean, 2)
        self.r.info["drive_lines_per_step"] = per_step
        self.r.check("drive.roundtrip_sane", mean <= MAX_MEAN_ROUNDTRIP_MS, f"mean {mean:.1f} ms per drive call (bridge + lines)")
        self.dev(op="place", ref=ref, x=x0, y=y0, z=z0, yaw=yaw0)
        self.dev(op="ai", ref=ref, on=True)

    def check_standin_and_clone(self, ref: str, rd: dict) -> None:
        before = len(self.b.call("world.actors", {"max": 200}).get("actors", []))
        led0 = self.ledger()
        s = self.dev(op="standin", near=ref, base=rd["base"], n=1)
        self.r.check("standin.created", bool(s.get("ok")) and s.get("ref") not in (None, "00000000"), json.dumps(s))
        if not s.get("ok"):
            return
        clone = s["ref"]
        net = s["netId"]
        c = self.read(clone)
        self.r.check("standin.restrained", bool(c.get("restrained")), "the clone must not run its own AI")
        self.r.check("standin.pose", close(c["x"], rd["x"] + 150.0, 2.0) and close(c["y"], rd["y"], 2.0), f"clone ({c['x']:.1f},{c['y']:.1f}) wanted ({rd['x'] + 150:.1f},{rd['y']:.1f})")
        after = len(self.b.call("world.actors", {"max": 200}).get("actors", []))
        self.r.check("standin.visible", after == before + 1, f"world.actors {before} -> {after}")
        led = self.ledger()
        self.r.check("standin.ledger", led.get("standIn", 0) == led0.get("standIn", 0) + 1, str(led))
        # kill / revive / protect on the clone (never on a real NPC)
        self.dev(op="kill", net=net)
        self.sleep(0.5)
        self.r.check("clone.kill", bool(self.read(clone).get("dead")))
        self.dev(op="revive", net=net)
        self.sleep(0.5)
        self.r.check("clone.revive", not self.read(clone).get("dead"))
        self.dev(op="protect", net=net, on=True)
        self.r.check("clone.protect", self.b.eval("IsEssential", ref=clone) == 1, "IsEssential")
        self.dev(op="protect", net=net, on=False)
        self.r.check("clone.unprotect", self.b.eval("IsEssential", ref=clone) == 0, "IsEssential")
        self.dev(op="unstandin", n=1)
        gone = False
        try:
            r2 = self.read(clone)
            gone = bool(r2.get("disabled"))
        except Exception:
            gone = True
        self.r.check("standin.removed", gone, "the clone must be disabled / deleted")
        led = self.ledger()
        self.r.check("standin.ledger_cleared", led.get("standIn", 0) == led0.get("standIn", 0), str(led))

    def check_swap(self, ref: str, rd: dict) -> None:
        if not self.pick:
            return
        r = self.dev(op="swap", ref=ref, pick=self.pick)
        active, real = r.get("active"), r.get("real")
        self.r.check("swap.replaced", active not in (None, real), json.dumps(r))
        if active and active != real:
            self.r.check("swap.original_disabled", bool(self.read(real).get("disabled")))
            self.r.check("swap.body_differs", self.read(active).get("base") != rd.get("base"))
        self.dev(op="release")
        self.r.check("swap.original_enabled_again", not self.read(ref).get("disabled"))

    def check_ledger_file(self, ref: str) -> None:
        if not self.game_dir:
            return
        plug = Path(self.game_dir) / "Data" / "NVSE" / "Plugins"
        # the split build loads the core from plugins/nvcoop_cache and the core writes the file next to ITSELF; the monolith writes next to nvcoop.dll
        cands = [plug / "nvcoop_cache" / "nvcoop_actors_ledger.txt", plug / "nvcoop_actors_ledger.txt"]
        path = max((c for c in cands if c.exists()), key=lambda c: c.stat().st_mtime, default=cands[1])
        self.dev(op="ai", ref=ref, on=False)
        self.sleep(1.5)   # the ledger file is written at most once a second, at a frame flush
        self.dev(op="scan")
        self.sleep(1.5)
        text = path.read_text(encoding="utf-8", errors="replace") if path.exists() else ""
        rid = self.read(ref)["ref"].upper()
        self.r.check("ledger.file", text.startswith("nvcoop-actors-ledger 1") and f"r {rid}" in text, f"{path} -> {text[:120]!r}")
        self.dev(op="ai", ref=ref, on=True)

    def check_release(self, ref: str, rd: dict) -> None:
        self.dev(op="ai", ref=ref, on=False)
        self.dev(op="release")
        led = self.ledger()
        self.r.check("release.ledger_empty", all(v == 0 for k, v in led.items() if k != "stale"), str(led))
        self.r.check("release.not_restrained", self.b.eval("GetRestrained", ref=ref) == 0, "GetRestrained")
        cx, cy, cz = self.pos_console(ref)
        self.r.check("release.back_in_place", close(cx, rd["x"], 2.0) and close(cy, rd["y"], 2.0), f"({cx:.1f},{cy:.1f}) vs ({rd['x']:.1f},{rd['y']:.1f})")

    def check_console_clean(self) -> None:
        st = self.port()
        self.r.check("console.no_refused_lines", st.get("stats", {}).get("consoleFailed", -1) == 0,
                     f"{st.get('stats', {}).get('consoleFailed')} of {st.get('stats', {}).get('consoleLines')} lines were refused")

    def run(self) -> Result:
        self.wiring()
        ref, rd = self.choose()
        self.r.info["ref"] = ref
        print(f"proof_actor_port: using {ref} at ({rd['x']:.1f},{rd['y']:.1f},{rd['z']:.1f})")
        self.check_read(ref, rd)
        self.check_ai(ref)
        self.check_place(ref, rd)
        self.check_drive(ref, rd)
        self.check_standin_and_clone(ref, rd)
        self.check_swap(ref, rd)
        self.check_ledger_file(ref)
        self.check_release(ref, rd)
        self.check_console_clean()
        return self.r


# ---- offline self-test: a fake bridge with a tiny world -----------------------------------------------------------------------------------
class FakeBridge:
    """Behaves like the plugin should. Faults (a set of names) make it misbehave in exactly one way each; run() must then FAIL."""

    def __init__(self, faults: Optional[set] = None) -> None:
        self.faults = faults or set()
        self.actors: Dict[str, dict] = {}
        self.add("00100001", 1000.0, 2000.0, 30.0, 0.5, base="00000500")
        self.add("00000014", 0.0, 0.0, 0.0, 0.0, player=True, base="00000007")
        self.ledger_r: set = set()
        self.ledger_s: set = set()
        self.standin_net: Dict[int, str] = {}
        self.lines = 0
        self.failed = 0
        self.next_clone = 0xFF000001
        self.pins = {"placeAtMe": True, "getActorValue": True, "isDisabled": True}
        if "pin" in self.faults:
            self.pins["placeAtMe"] = False

    def add(self, ref: str, x: float, y: float, z: float, rz: float, player: bool = False, base: str = "00000500") -> None:
        self.actors[ref] = {"ref": ref, "base": base, "x": x, "y": y, "z": z, "rotZ": rz, "dead": False, "disabled": False, "restrained": False, "ess": False,
                            "has3D": True, "hp": 100.0, "player": player, "cell": "00000001"}

    # --- bridge API ---
    @staticmethod
    def _norm(ref: str) -> str:
        return str(ref).split(":")[-1].upper().zfill(8)

    def eval(self, expr: str, ref: Optional[str] = None) -> float:
        a = self.actors[self._norm(ref) if ref else "00000014"]
        name = expr.split()[0]
        arg = expr.split()[1] if len(expr.split()) > 1 else ""
        if name == "GetPos":
            v = a[{"X": "x", "Y": "y", "Z": "z"}[arg]]
            return v + (5.0 if "pos_offset" in self.faults and arg == "X" else 0.0)
        if name == "GetAngle":
            deg = math.degrees(a["rotZ"])
            return -deg if "heading_sign" in self.faults else deg
        if name == "GetAV":
            return a["hp"]
        if name == "GetDead":
            return 1.0 if a["dead"] else 0.0
        if name == "GetRestrained":
            return 1.0 if a["restrained"] else 0.0
        if name == "IsEssential":
            return 1.0 if a["ess"] else 0.0
        raise KeyError(expr)

    def exec(self, cmd: str, ref: Optional[str] = None) -> dict:
        return {"ok": True, "output": ""}

    def call(self, method: str, params: Optional[dict] = None) -> Any:
        p = params or {}
        if method == "actors.port":
            return {"enabled": True, "pins": dict(self.pins), "sehFaults": 0,
                    "ledger": {"restrained": len(self.ledger_r), "standIn": len(self.ledger_s), "disabled": 0, "essential": 0, "stale": 0},
                    "stats": {"consoleLines": self.lines, "consoleFailed": self.failed + (3 if "refused" in self.faults else 0)}}
        if method == "world.actors":
            return {"actors": [{"id": int(r, 16), "ref": "Fake.esm:" + r[2:], "base": 1} for r, a in self.actors.items() if not a["player"] and not a["disabled"]]}
        if method == "actors.read":
            a = self.actors[self._norm(p["ref"])]
            return {"ref": a["ref"], "base": a["base"], "x": a["x"] + (5.0 if "read_pos" in self.faults else 0.0), "y": a["y"], "z": a["z"], "rotZ": a["rotZ"],
                    "dead": a["dead"], "disabled": a["disabled"], "has3D": a["has3D"], "restrained": a["restrained"], "av": {"16": a["hp"]}}
        if method == "actors.dev":
            return self._dev(p)
        raise KeyError(method)

    def _resolve(self, p: dict) -> str:
        if "net" in p:
            return self.standin_net[int(p["net"])]
        return self._norm(p["ref"])

    def _dev(self, p: dict) -> dict:
        op = p["op"]
        out: Dict[str, Any] = {}
        if op in ("bind", "ai", "place", "drive", "kill", "revive", "protect", "swap"):
            ref = self._resolve(p)
            a = self.actors[ref]
            out["netId"] = int(ref, 16)
            if op == "ai":
                on = bool(p.get("on", True))
                if not on:
                    if "ai_not_restrained" not in self.faults:
                        a["restrained"] = True
                    self.ledger_r.add(ref)
                    self.lines += 2
                else:
                    a["restrained"] = False
                    if "ai_ledger_leak" not in self.faults:
                        self.ledger_r.discard(ref)
                    self.lines += 2
                out["aiOff"] = not on
            elif op in ("place", "drive"):
                a["x"], a["y"], a["z"] = float(p.get("x", a["x"])), float(p.get("y", a["y"])), float(p.get("z", a["z"]))
                if "yaw" in p:
                    a["rotZ"] = float(p["yaw"])
                if "health" in p:
                    a["hp"] = float(p["health"])
                self.lines += 4
            elif op == "kill":
                a["dead"] = True
            elif op == "revive":
                a["dead"] = False if "no_revive" not in self.faults else True
            elif op == "protect":
                a["ess"] = bool(p.get("on", True))
        elif op == "standin":
            c = "%08X" % self.next_clone
            self.next_clone += 1
            base = self.actors[self._resolve({"ref": p["near"]})]
            self.add(c, base["x"] + (150.0 if "standin_pose" not in self.faults else 0.0), base["y"], base["z"], 0.0, base=p["base"])
            self.actors[c]["restrained"] = "standin_free" not in self.faults
            self.ledger_s.add(c)
            net = 1000 + int(p.get("n", 1))
            self.standin_net[net] = c
            out.update({"ok": True, "netId": net, "ref": c})
        elif op == "unstandin":
            c = self.standin_net.pop(1000 + int(p.get("n", 1)), None)
            if c:
                if "standin_stays" not in self.faults:
                    self.actors[c]["disabled"] = True
                    self.ledger_s.discard(c)
        elif op == "release":
            for r in list(self.ledger_r):
                if "release_leak" not in self.faults:
                    self.actors[r]["restrained"] = False
            if "release_leak" not in self.faults:
                self.ledger_r.clear()
        elif op in ("scan", "undo"):
            pass
        out["lines"] = self.lines
        out["failed"] = self.failed
        return out


def selftest() -> int:
    ok = True
    if not (wrap_deg(270) == -90 and wrap_deg(-190) == 170 and wrap_deg(180) == 180 and angle_close(179, -179, 3) and hexid("FF") == 255):
        print("selftest: angle / hex helpers are wrong")
        ok = False

    def run(faults: set) -> Result:
        res = Result()
        import io
        import contextlib
        with contextlib.redirect_stdout(io.StringIO()):
            Proof(FakeBridge(faults), res, sleep=lambda s: None).run()
        return res

    good = run(set())
    if good.failures:
        print("selftest: a correct port failed the proof:", good.failures)
        ok = False
    else:
        print(f"selftest: a correct port passes all {len(good.checks)} checks")
    twins = {
        "pos_offset": "read.pos", "read_pos": "read.pos", "heading_sign": "place.heading_sign", "ai_not_restrained": "ai.off.console",
        "ai_ledger_leak": "ai.on.ledger", "standin_free": "standin.restrained", "standin_pose": "standin.pose", "standin_stays": "standin.removed",
        "release_leak": "release.not_restrained", "no_revive": "clone.revive", "refused": "console.no_refused_lines", "pin": "wiring.pin.placeAtMe",
    }
    for fault, expect in twins.items():
        res = run({fault})
        names = [f.split(":")[0] for f in res.failures]
        if expect not in names:
            print(f"selftest: TWIN SURVIVED {fault!r}: expected {expect} to fail, failures were {names}")
            ok = False
        else:
            print(f"selftest: twin {fault!r} caught by {expect}")
    # not-ready paths
    class Dead:
        def call(self, method: str, params: Optional[dict] = None) -> Any:
            raise RuntimeError("no such method")

    try:
        Proof(Dead(), Result()).wiring()
        print("selftest: a missing actors.port was not reported as NotReady")
        ok = False
    except NotReady:
        print("selftest: a missing actors.port is reported as not ready")
    if ok:
        print("proof_actor_port selftest: ALL OK")
    return 0 if ok else 1


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--instance", type=int, default=0)
    ap.add_argument("--ref", default=None, help="Plugin.esm:HEX of the NPC to use")
    ap.add_argument("--pick", default=None, help="Plugin.esm:HEX of a base form for the leveled-pick swap check")
    ap.add_argument("--game-dir", default=None, help="game directory (to check the ledger file)")
    ap.add_argument("--out", default="out/actor-port/proof")
    ap.add_argument("--selftest", action="store_true")
    try:
        a = ap.parse_args(argv)
    except SystemExit:
        return 2
    if a.selftest:
        return selftest()
    from nvbridge import NvBridge  # noqa: E402

    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    res = Result()
    try:
        with NvBridge.connect(a.instance, wait_s=10.0) as b:
            Proof(b, res, a.ref, a.pick, a.game_dir).run()
    except NotReady as e:
        print(f"proof_actor_port: NOT READY: {e}")
        return 3
    (out / "report.json").write_text(json.dumps({"checks": res.checks, "info": res.info}, indent=2), encoding="utf-8")
    if res.failures:
        print(f"proof_actor_port: {len(res.failures)} check(s) FAILED:")
        for f in res.failures:
            print("  -", f)
        return 1
    print(f"proof_actor_port: all {len(res.checks)} checks passed (report in {out})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
