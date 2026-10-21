"""In-game proof for the player puppet (plugin/avatar + plugin/avatar/engine). THE LEAD / HELPER RUNS THIS, one game (loopback) or one
game pair at a time. It has been WRITTEN against the real engine but never run against a game; `--selftest` runs only the offline part
(synthetic T7 streams, no game, no bridge).

    python -I tools/avatar/proof_puppet.py --loopback [--offset 160,0,0] [--out out/avatar/run1]      ONE game instance (start here)
    python -I tools/avatar/proof_puppet.py --host 0 --client 1 [--slot 0] [--out out/avatar/run1]       two instances in one co-op session
    python -I tools/avatar/proof_puppet.py --selftest                                                   offline, never touches a game

Setup, loopback (one instance, the cheapest proof; the owner's PC is short of RAM):
  nvcoop.ini  [avatar] enabled=1   (template=player is the default: the puppet is made from the player's own base form)
              [dev] animprobe=1 and bots=1
  The loopback puppet is a puppet of the LOCAL player: the real player's snapshots go through the same interpolation buffer and the
  same driver a remote player would, shifted by --offset, and T7 compares the real player with its puppet in the same probe.
  Stand with about 15 m of free floor in front of you, in an exterior or a big interior, with a weapon in the inventory.
Setup, two instances: both with [dev] animprobe=1 and bots=1; [avatar] enabled=1 on the client; the co-op layer feeds the avatar glue.

What it does, in order:
  0. avatar.status: the layer must be enabled with every engine pin matching the running exe (backend.pins all ok), else exit 3.
  1. (loopback) avatar.loopback {on:true, offset}; waits until the puppet is Driving. (two instances) a Driving puppet for --slot.
  2. Polls anim.probe at --hz (tools/animcheck Collector) for the whole run.
  3. Drives the REAL player through a fixed choreography (stand, run, walk sneaking, jump, reload if possible, turn on the spot, stand)
     with the bot input driver. Windows where it moves are given to the stuck detector.
  4. Runs animcheck.analyze with avatars=[real player -> puppet] and asserts the T7 counters:
       no sliding, no T-pose, no floating / sinking, no jitter, no rubber banding, no stuck, no wrong death pose;
       pose error / position error / facing error inside the bounds below; no wrong weapon / aim; few animation mismatches.
  5. Asserts the driver's counters (avatar.status "stats"), the engine's counters ("engine": no console exceptions, no foreign
     reference refused, no spawn that found no new ref, console line cost, 3D drift) and the backend pins.
  6. With --load-save NAME: loads that save and checks no puppet orphan survived (tagged == puppets).
Exit code: 0 = all asserted, 1 = an assertion failed, 2 = bad arguments, 3 = not wired / not ready (nothing was judged).

What the proof measures that nothing else has (all UNMEASURED until the first run): whether SetPos / SetAngle / PlayGroup are accepted by
a restrained actor, whether PlaceAtMe of the template base works, the cost of a console line, whether SetAlert draws the weapon, whether
the animation group numbers are the xNVSE ones, whether the 3D follows SetPos (engine.drift3D), and the real animation of the owner.

Every threshold below is a FIRST GUESS: tune from one clean run, the way docs/tools/T7-anim.md says for config.py.
"""
from __future__ import annotations

import argparse
import json
import sys
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "tools" / "runner"))

from animcheck.analyze import analyze  # noqa: E402
from animcheck.config import Thresholds  # noqa: E402
from animcheck.counters import check  # noqa: E402

# ---- what a good puppet looks like (T7 counters, flat keys of report["counters"]) -------------------------------------
GLITCH_KINDS = ("sliding", "tpose", "floating", "sinking", "jitter", "rubberband", "stuck", "deathpose")
TRAP_SPEC: Dict[str, str] = {f"avatar.glitch.{k}": "==0" for k in GLITCH_KINDS}
FIDELITY_SPEC: Dict[str, str] = {
    "avatar.compared": ">=200",            # enough samples were really compared (a wrong ref would compare nothing)
    "avatar.glitches": "==0",
    "avatar.pose_err_p95": "<=12",         # units, bone positions after root alignment
    "avatar.pos_err_p95": "<=25",          # the analyzer already compares against the owner 100 ms earlier
    "avatar.facing_err_p95_deg": "<=35",
    "avatar.wrong_weapon": "==0",
    "avatar.wrong_aim": "==0",
    "avatar.stance_mismatch": "<=3",
    "avatar.anim_mismatches": "<=2",
}
SPEC: Dict[str, str] = {**TRAP_SPEC, **FIDELITY_SPEC}

# driver counters (avatar.status "stats"): upper bounds over the whole run
STATS_SPEC: Dict[str, int] = {
    "spawnFailures": 0,
    "refLost": 0,
    "restrainReasserts": 1,
    "groupRefused": 3,
    "drift": 5,
    "equipGaveUp": 0,
    "load3DTimeouts": 0,
}

# engine counters (avatar.status "engine"): upper bounds over the whole run. cost is microseconds per console line (p95).
ENGINE_SPEC: Dict[str, int] = {
    "linesException": 0,       # a console line that faulted
    "linesRefGone": 2,         # a line sent to a reference that vanished in between
    "foreignRefused": 0,       # the guard stopped a write to something that is not ours: a bug upstream
    "spawnNoNewRef": 0,        # PlaceAtMe ran but the diff found no new actor: the spawn path does not work
    "spawnNoAnchor": 0,
    "spawnRefused": 0,
    "evalFailures": 0,         # a condition evaluator was unavailable or faulted (pin mismatch)
    "drift3D": 5,              # samples where the 3D was more than 20 units from the reference position: SetPos does not move the body
    "costP95Us": 3000,         # first guess: a script line costs well under 3 ms
}
REQUIRED_BACKEND = ("LookupFormByID", "MenuModeAny", "GetRestrained", "IsWeaponOut", "GetItemCount", "GetEquipped")

# choreography: (name, moving). Executed by run_choreography against the host.
CHOREOGRAPHY = [
    ("stand", False), ("run_forward", True), ("run_back", True), ("walk_sneak", True), ("jump", False),
    ("reload", False), ("turn_on_spot", False), ("stand_end", False),
]


# ---- analysis (shared by the live run and the selftest) ---------------------------------------------------------------
def analyse_run(streams, host_ref: int, puppet_ref: int, moving: Optional[List[Tuple[float, float]]] = None,
                th: Optional[Thresholds] = None) -> dict:
    """streams: {'host': Stream, 'client': Stream}. moving: [(t0_ms, t1_ms)] windows on the stream clock in which the host
    player walks (enables the stuck detector for the PUPPET: it must also be moving, 100 ms later)."""
    avatars = [{"name": "host", "owner": "host", "owner_ref": host_ref, "observer": "client", "observer_ref": puppet_ref}]
    expect = {puppet_ref: [(a + 400.0, b) for a, b in moving]} if moving else None
    return analyze(streams, th or Thresholds(), owner="host", avatars=avatars, expect_moving=expect, label="proof_puppet")


def judge(report: dict, status: Optional[dict], spec: Optional[Dict[str, str]] = None) -> List[str]:
    """All failure strings (empty = pass): T7 counters, then the driver's own counters, then the engine and its pins."""
    fails = check(report, spec or SPEC)
    if status is not None:
        stats = status.get("stats", {})
        for k, limit in STATS_SPEC.items():
            if k not in stats:
                fails.append(f"stats.{k}: missing from avatar.status")
            elif stats[k] > limit:
                fails.append(f"stats.{k}: {stats[k]} > {limit}")
        fails += judge_engine(status)
    return fails


def judge_engine(status: dict) -> List[str]:
    """The real engine's own counters and the pins of the backend (avatar.status "engine" / "backend")."""
    fails: List[str] = []
    eng = status.get("engine")
    if not isinstance(eng, dict):
        return ["engine: missing from avatar.status (is this the real engine?)"]
    for k, limit in ENGINE_SPEC.items():
        if k not in eng:
            fails.append(f"engine.{k}: missing from avatar.status")
        elif eng[k] > limit:
            fails.append(f"engine.{k}: {eng[k]} > {limit}")
    be = status.get("backend")
    if not isinstance(be, dict):
        fails.append("backend: missing from avatar.status")
        return fails
    if not be.get("consoleOk"):
        fails.append("backend.consoleOk: the NVSE console interface is not available")
    pins = {p.get("name"): p.get("ok") for p in be.get("pins", []) if isinstance(p, dict)}
    for name in REQUIRED_BACKEND:
        if name not in pins:
            fails.append(f"backend.pins: {name} missing")
        elif not pins[name]:
            fails.append(f"backend.pins: {name} does not match the running exe")
    loc = status.get("local")
    if isinstance(loc, dict) and not loc.get("have"):
        fails.append("local: the local player was never read")
    return fails


# ---- loopback helpers (one instance: the real player and its puppet are both in the same probe) ------------------------------
def shift_actor(a: dict, delta: Sequence[float]) -> dict:
    """A copy of a probe actor with its position and every bone moved by `delta` (the loopback offset is removed again)."""
    out = json.loads(json.dumps(a))
    if isinstance(out.get("pos"), list) and len(out["pos"]) == 3:
        out["pos"] = [out["pos"][i] + delta[i] for i in range(3)]
    for name, v in list((out.get("bones") or {}).items()):
        if isinstance(v, list) and len(v) == 3:
            out["bones"][name] = [v[i] + delta[i] for i in range(3)]
    return out


def split_loopback(reply: dict, player_ref: int, puppet_ref: int, offset: Sequence[float]) -> Tuple[dict, dict]:
    """One anim.probe reply that holds both actors -> (reply of the 'host' machine, reply of the 'client' machine).
    The puppet is shown `offset` away from the player; its positions are shifted back so the T7 position error compares like with like."""
    host, client = dict(reply), dict(reply)
    host["actors"], client["actors"] = [], []
    neg = [-x for x in offset]
    for a in reply.get("actors", []):
        ref = _hex(a.get("ref", 0))
        if ref == player_ref:
            host["actors"].append(a)
        elif ref == puppet_ref:
            client["actors"].append(shift_actor(a, neg))
    return host, client


# ---- live run -------------------------------------------------------------------------------------------------------------
class NotReady(Exception):
    pass


def _hex(v: Any) -> int:
    return int(v, 16) if isinstance(v, str) else int(v)


def find_puppet(status: dict, slot: int) -> Tuple[int, dict]:
    for p in status.get("puppets", []):
        if int(p.get("slot", -1)) == slot:
            if p.get("phase") != "Driving":
                raise NotReady(f"puppet for slot {slot} is {p.get('phase')}, not Driving yet")
            return _hex(p["ref"]), p
    raise NotReady(f"avatar.status has no puppet for slot {slot}: {status.get('puppets')}")


def run_choreography(bot, record) -> None:
    """Drive the host's real player. `record(name, t0_s, t1_s, moving)` is called after each step. Uses relative waypoints so it
    works anywhere with ~15 m of free floor. Needs the input driver (a stepped SetPos would give a puppet nothing real to copy)."""
    if not bot.has_input_driver():
        raise NotReady("the host has no bot input driver (ini [dev] bots=1): the animation would not be real")
    p0 = bot.pos()
    fwd = (p0[0], p0[1] + 900.0, p0[2])
    back = (p0[0], p0[1], p0[2])
    side = (p0[0] + 400.0, p0[1], p0[2])

    def step(name: str, moving: bool, fn) -> None:
        t0 = time.monotonic()
        fn()
        record(name, t0, time.monotonic(), moving)

    step("stand", False, lambda: bot.wait(3.0))
    step("run_forward", True, lambda: bot.goto(fwd, radius=120.0, timeout_s=30.0, mode="input"))
    step("run_back", True, lambda: bot.goto(back, radius=120.0, timeout_s=30.0, mode="input"))
    step("walk_sneak", True, lambda: (bot.sneak_toggle(), bot.goto(side, radius=120.0, timeout_s=30.0, mode="input"), bot.sneak_toggle()))
    step("jump", False, lambda: (bot.jump(), bot.wait(2.0)))
    step("reload", False, lambda: (bot.reload(), bot.wait(3.0)))
    step("turn_on_spot", False, lambda: [(bot.look(yaw=a), bot.wait(0.6)) for a in (90.0, 180.0, 270.0, 0.0)])
    step("stand_end", False, lambda: (bot.stop(), bot.wait(3.0)))


def precheck(status: dict) -> Optional[str]:
    """Why the layer is not ready to be judged (None = ready). Not ready is exit 3: nothing was judged, nothing failed."""
    if status.get("enabled") is False or status.get("running") is False:
        return f"the avatar layer is not running: {status.get('reason', 'nvcoop.ini [avatar] enabled=1 missing?')}"
    be = status.get("backend")
    if isinstance(be, dict):
        bad = [p.get("name") for p in be.get("pins", []) if isinstance(p, dict) and not p.get("ok")]
        if bad:
            return f"engine pins that do not match the running exe: {bad} (a different FalloutNV.exe? run tools/avatar/verify_engine_pins.py)"
        if not be.get("consoleOk"):
            return "the NVSE console interface is not available to the avatar backend"
    return None


def wait_puppet(bridge, slot: int, timeout_s: float = 30.0) -> Tuple[int, dict]:
    """Poll avatar.status until the puppet of `slot` is Driving (spawn + 3D load take a few frames). NotReady on timeout."""
    deadline = time.monotonic() + timeout_s
    last = ""
    while time.monotonic() < deadline:
        try:
            return find_puppet(bridge.call("avatar.status", {}), slot)
        except NotReady as e:
            last = str(e)
        time.sleep(0.5)
    raise NotReady(f"no Driving puppet for slot {slot} within {timeout_s:.0f}s ({last})")


def finish(out: Path, col, host_ref: int, puppet_ref: int, moving, steps, status_after: dict, extra_fails: List[str]) -> int:
    streams = col.streams()
    rep = analyse_run(streams, host_ref, puppet_ref, moving)
    fails = judge(rep, status_after) + extra_fails
    (out / "report.json").write_text(json.dumps({"counters": rep["counters"], "steps": steps, "status": status_after,
                                                 "failures": fails}, indent=1), encoding="utf-8")
    try:
        from animcheck.report_html import render
        (out / "report.html").write_text(render(rep), encoding="utf-8")
    except Exception as e:  # the report is a convenience
        print(f"(no html report: {e!r})")
    c = rep["counters"]
    print("avatar counters:", " ".join(f"{k}={c[k]}" for k in sorted(c) if k.startswith("avatar.")))
    print("driver stats:", json.dumps(status_after.get("stats", {})))
    print("engine stats:", json.dumps(status_after.get("engine", {})))
    for f in fails:
        print("  FAIL", f)
    print("PASS" if not fails else "FAIL")
    return 0 if not fails else 1


def _run_with_polling(col, poll_once, hz: float, host_bot, record) -> None:
    """Poll in a thread while the choreography drives the real player; always joins the thread."""
    stop = threading.Event()

    def poll() -> None:
        period = 1.0 / hz
        nxt = time.monotonic()
        while not stop.is_set():
            poll_once()
            nxt += period
            d = nxt - time.monotonic()
            if d > 0:
                time.sleep(d)
            else:
                nxt = time.monotonic()

    th = threading.Thread(target=poll, daemon=True)
    th.start()
    try:
        run_choreography(host_bot, record)
    finally:
        stop.set()
        th.join(5.0)


def run_live(a) -> int:
    from animcheck.collect import Collector  # noqa: E402
    from bots import Bot  # noqa: E402

    out = Path(a.out or (ROOT / "out" / "avatar" / time.strftime("proof_%Y%m%d_%H%M%S")))
    out.mkdir(parents=True, exist_ok=True)
    with Collector.from_instances({"host": a.host, "client": a.client}, out_dir=out) as col:
        client, host = col.bridges["client"], col.bridges["host"]
        try:
            status = client.call("avatar.status", {})
        except Exception as e:  # method missing = the avatar glue is not in this nvcoop.dll
            print(f"NOT READY: avatar.status failed on the client: {e!r}")
            return 3
        why = precheck(status)
        if why:
            print(f"NOT READY: {why}")
            return 3
        try:
            puppet_ref, _ = find_puppet(status, a.slot)
        except NotReady as e:
            print(f"NOT READY: {e}")
            return 3
        host_ref = 0x14  # the player
        params = {"refs": ["player"], "bones": True}
        probe_params = {"host": params, "client": {"refs": [f"0x{puppet_ref:08X}"], "bones": True}}

        moving: List[Tuple[float, float]] = []
        steps: List[dict] = []

        def record(name: str, t0: float, t1: float, mv: bool) -> None:
            steps.append({"step": name, "t0_ms": t0 * 1000.0, "t1_ms": t1 * 1000.0, "moving": mv})
            if mv:
                moving.append((t0 * 1000.0, t1 * 1000.0))
            print(f"  step {name}: {t1 - t0:.1f}s")

        def poll_once() -> None:
            for m, b in col.bridges.items():
                t0 = time.monotonic()
                try:
                    r = b.call("anim.probe", probe_params[m])
                except Exception as e:
                    col.errors[m] += 1
                    col.last_error[m] = repr(e)
                    continue
                t1 = time.monotonic()
                if isinstance(r, dict):
                    r = dict(r)
                    r["machine"] = m
                    r["rx_ms"] = round((t0 + t1) / 2.0 * 1000.0, 3)
                    col.probes[m].append(r)
                    col._write(m, r)

        try:
            _run_with_polling(col, poll_once, a.hz, Bot(host, "avatar-proof"), record)
        except NotReady as e:
            print(f"NOT READY: {e}")
            return 3
        status_after = client.call("avatar.status", {})
        extra = load_check(client, a.load_save, a.slot) if a.load_save else []
        return finish(out, col, host_ref, puppet_ref, moving, steps, status_after, extra)


def run_loopback(a) -> int:
    """One instance: the loopback puppet copies the real player; both are in the same anim.probe reply, split into two machines."""
    from animcheck.collect import Collector  # noqa: E402
    from bots import Bot  # noqa: E402
    from nvbridge import NvBridge  # type: ignore  # noqa: E402

    try:
        offset = [float(x) for x in a.offset.split(",")]
        assert len(offset) == 3
    except Exception:
        print(f"bad --offset {a.offset!r}: want x,y,z")
        return 2
    out = Path(a.out or (ROOT / "out" / "avatar" / time.strftime("proof_loop_%Y%m%d_%H%M%S")))
    out.mkdir(parents=True, exist_ok=True)
    bridge = NvBridge.connect(instance=a.host, wait_s=10.0)
    with Collector({"host": bridge, "client": bridge}, out_dir=out) as col:
        try:
            status = bridge.call("avatar.status", {})
        except Exception as e:
            print(f"NOT READY: avatar.status failed: {e!r}")
            return 3
        why = precheck(status)
        if why:
            print(f"NOT READY: {why}")
            return 3
        slot = int(status.get("loopbackSlot", 1))
        try:
            bridge.call("avatar.loopback", {"on": True, "offset": offset})
            puppet_ref, _ = wait_puppet(bridge, slot)
        except NotReady as e:
            print(f"NOT READY: {e}")
            return 3
        except Exception as e:
            print(f"NOT READY: avatar.loopback failed: {e!r}")
            return 3
        host_ref = 0x14
        params = {"refs": ["player", f"0x{puppet_ref:08X}"], "bones": True}
        moving: List[Tuple[float, float]] = []
        steps: List[dict] = []

        def record(name: str, t0: float, t1: float, mv: bool) -> None:
            steps.append({"step": name, "t0_ms": t0 * 1000.0, "t1_ms": t1 * 1000.0, "moving": mv})
            if mv:
                moving.append((t0 * 1000.0, t1 * 1000.0))
            print(f"  step {name}: {t1 - t0:.1f}s")

        def poll_once() -> None:
            t0 = time.monotonic()
            try:
                r = bridge.call("anim.probe", params)
            except Exception as e:
                col.errors["host"] += 1
                col.last_error["host"] = repr(e)
                return
            t1 = time.monotonic()
            if not isinstance(r, dict):
                return
            rx = round((t0 + t1) / 2.0 * 1000.0, 3)
            for m, part in zip(("host", "client"), split_loopback(r, host_ref, puppet_ref, offset)):
                part["machine"] = m
                part["rx_ms"] = rx
                col.probes[m].append(part)
                col._write(m, part)

        try:
            _run_with_polling(col, poll_once, a.hz, Bot(bridge, "avatar-proof"), record)
        except NotReady as e:
            print(f"NOT READY: {e}")
            return 3
        status_after = bridge.call("avatar.status", {})
        extra = load_check(bridge, a.load_save, slot) if a.load_save else []
        rc = finish(out, col, host_ref, puppet_ref, moving, steps, status_after, extra)
        try:
            bridge.call("avatar.loopback", {"on": False})   # leave the world as it was found
        except Exception:
            pass
        return rc


def load_check(client, save: str, slot: int, wait_s: float = 60.0) -> List[str]:
    """Load a save on the client; afterwards the puppet must be rebuilt and no orphan may be left in the world."""
    fails: List[str] = []
    client.call("loadSave", {"name": save})
    deadline = time.monotonic() + wait_s
    status: dict = {}
    while time.monotonic() < deadline:
        time.sleep(1.0)
        try:
            status = client.call("avatar.status", {})
            find_puppet(status, slot)
            break
        except Exception:
            continue
    else:
        return [f"after loading {save!r} no Driving puppet for slot {slot} within {wait_s:.0f}s"]
    live = len(status.get("puppets", []))
    tagged = status.get("tagged")
    if tagged is None:
        fails.append("avatar.status has no 'tagged' count (needed to prove puppets are not saved)")
    elif tagged != live:
        fails.append(f"after the load {tagged} tagged puppets exist but only {live} are owned: orphans from the save")
    return fails


# ---- offline selftest --------------------------------------------------------------------------------------------------------
def _selftest() -> int:
    from animcheck import synth
    from animcheck.model import stream_from_probes

    host_ref, puppet_ref = 0x14, 0xFF00AA
    dur = 12000.0
    fails_total = 0
    checks = 0

    def streams_for(puppet_script) -> dict:
        host = synth.walker(speed=120.0)
        hp = synth.make_probes("host", {host_ref: host}, dur)
        cp = synth.make_probes("client", {puppet_ref: puppet_script(synth.delayed(host, 100.0))}, dur)
        return {"host": stream_from_probes("host", hp, use_rx=True), "client": stream_from_probes("client", cp, use_rx=True)}

    good_backend = {"consoleOk": True, "pins": [{"name": n, "addr": "0x0", "ok": True} for n in REQUIRED_BACKEND + ("IsSneaking", "GetDead")]}
    good_status = {"v": 1, "enabled": True, "running": True, "puppets": [{"slot": 0, "ref": f"0x{puppet_ref:08X}", "phase": "Driving"}],
                   "stats": {k: 0 for k in STATS_SPEC}, "tagged": 1, "engine": {k: 0 for k in ENGINE_SPEC},
                   "backend": good_backend, "local": {"have": True}}

    def expect(name: str, ok: bool) -> None:
        nonlocal fails_total, checks
        checks += 1
        if not ok:
            fails_total += 1
            print(f"  FAIL selftest: {name}")

    # 1. a clean puppet (the owner's script, 100 ms later, with a little noise) passes everything
    rep = analyse_run(streams_for(lambda s: s), host_ref, puppet_ref)
    f = judge(rep, good_status)
    expect(f"clean puppet passes ({f})", not f)
    expect("clean puppet was compared", rep["counters"]["avatar.compared"] >= 200)

    # 2. mutation twins of the proof: each injected defect must be reported by the matching counter and fail judge()
    twins = {
        "sliding": lambda s: synth.inject_sliding(s, 3000.0, 6000.0),
        "tpose": lambda s: synth.inject_tpose(s, 3000.0, 6000.0),
        "floating": lambda s: synth.inject_float(s, 3000.0, 6000.0),
        "jitter": lambda s: synth.inject_jitter(s, 3000.0, 6000.0, 12.0),
        "rubberband": lambda s: synth.inject_rubber(s, 4000.0),
    }
    for kind, inj in twins.items():
        rep = analyse_run(streams_for(inj), host_ref, puppet_ref)
        c = rep["counters"]
        f = judge(rep, good_status)
        hit = c.get(f"avatar.glitch.{kind}", 0) > 0 or (kind == "floating" and c.get("avatar.glitch.sinking", 0) > 0)
        expect(f"twin {kind}: counter reports it", hit)
        expect(f"twin {kind}: judge fails", bool(f))

    # 2b. the stuck detector: the host walks for the whole run (moving window given), a puppet that never moves is reported
    rep = analyse_run(streams_for(lambda s: s), host_ref, puppet_ref, moving=[(1000.0, 11000.0)])
    expect("clean puppet is not stuck", rep["counters"]["avatar.glitch.stuck"] == 0 and not judge(rep, good_status))
    rep = analyse_run(streams_for(lambda s: synth.idler()), host_ref, puppet_ref, moving=[(1000.0, 11000.0)])
    expect("twin stuck: counter reports it", rep["counters"]["avatar.glitch.stuck"] > 0)
    expect("twin stuck: judge fails", bool(judge(rep, good_status)))

    # 3. a puppet ref that is not in the stream compares nothing: must fail instead of passing vacuously
    streams = streams_for(lambda s: s)
    rep = analyse_run(streams, host_ref, 0xFF00BB)
    expect("wrong puppet ref fails (nothing compared)", bool(judge(rep, good_status)))

    # 4. driver counters: each bound bites
    rep = analyse_run(streams_for(lambda s: s), host_ref, puppet_ref)
    for k, limit in STATS_SPEC.items():
        bad = dict(good_status, stats={**good_status["stats"], k: limit + 1})
        expect(f"stats twin {k}", any(f"stats.{k}" in x for x in judge(rep, bad)))
    missing = dict(good_status, stats={})
    expect("missing stats fail", bool(judge(rep, missing)))

    # 4b. engine counters: each bound bites, a missing block / pin / console fails, a broken pin makes the run "not ready"
    for k, limit in ENGINE_SPEC.items():
        bad = dict(good_status, engine={**good_status["engine"], k: limit + 1})
        expect(f"engine twin {k}", any(f"engine.{k}" in x for x in judge(rep, bad)))
    no_engine = {k: v for k, v in good_status.items() if k != "engine"}
    expect("missing engine block fails", any("engine" in x for x in judge(rep, no_engine)))
    no_backend = {k: v for k, v in good_status.items() if k != "backend"}
    expect("missing backend block fails", any("backend" in x for x in judge(rep, no_backend)))
    bad_pin = dict(good_status, backend={"consoleOk": True, "pins": [dict(p, ok=(p["name"] != "GetRestrained")) for p in good_backend["pins"]]})
    expect("a pin that does not match fails", any("GetRestrained" in x for x in judge(rep, bad_pin)))
    expect("a pin that does not match is 'not ready'", precheck(bad_pin) is not None and "GetRestrained" in precheck(bad_pin))
    missing_pin = dict(good_status, backend={"consoleOk": True, "pins": good_backend["pins"][1:]})
    expect("a missing required pin fails", any("LookupFormByID" in x for x in judge(rep, missing_pin)))
    no_console = dict(good_status, backend={"consoleOk": False, "pins": good_backend["pins"]})
    expect("no console interface fails", any("consoleOk" in x for x in judge(rep, no_console)))
    expect("no console interface is 'not ready'", precheck(no_console) is not None)
    never_read = dict(good_status, local={"have": False})
    expect("a local player that was never read fails", any("local" in x for x in judge(rep, never_read)))
    expect("a ready layer has no precheck reason", precheck(good_status) is None)
    expect("a disabled layer is 'not ready'", precheck({"v": 1, "enabled": False, "running": False, "reason": "[avatar] enabled=0"}) is not None)

    # 4c. loopback: one probe reply holds both actors; split it, remove the offset, and the comparison is exactly the two-machine one
    off = [160.0, 0.0, 0.0]
    host = synth.walker(speed=120.0)
    both = synth.make_probes("host", {host_ref: host, puppet_ref: synth.delayed(host, 100.0)}, dur)
    shown = []   # what the game would report: the puppet is `off` away from where the owner's script puts it
    for r in both:
        r = json.loads(json.dumps(r))
        r["actors"] = [shift_actor(a, off) if _hex(a["ref"]) == puppet_ref else a for a in r["actors"]]
        shown.append(r)
    hp, cp = [], []
    for r in shown:
        h, c = split_loopback(r, host_ref, puppet_ref, off)
        h["machine"], c["machine"] = "host", "client"
        hp.append(h)
        cp.append(c)
    expect("split: the host machine holds only the player", all(len(r["actors"]) == 1 and _hex(r["actors"][0]["ref"]) == host_ref for r in hp))
    expect("split: the client machine holds only the puppet", all(len(r["actors"]) == 1 and _hex(r["actors"][0]["ref"]) == puppet_ref for r in cp))
    expect("split does not modify its input", shown[0]["actors"][0] == json.loads(json.dumps(shown[0]))["actors"][0])
    lstreams = {"host": stream_from_probes("host", hp, use_rx=True), "client": stream_from_probes("client", cp, use_rx=True)}
    rep_l = analyse_run(lstreams, host_ref, puppet_ref)
    expect(f"loopback split of a clean puppet passes ({judge(rep_l, good_status)})", not judge(rep_l, good_status))
    expect("loopback split was compared", rep_l["counters"]["avatar.compared"] >= 200)
    # twin: the offset is not removed again -> the position error is the offset and the proof fails
    hp2, cp2 = [], []
    for r in shown:
        h, c = split_loopback(r, host_ref, puppet_ref, [0.0, 0.0, 0.0])
        h["machine"], c["machine"] = "host", "client"
        hp2.append(h)
        cp2.append(c)
    lstreams2 = {"host": stream_from_probes("host", hp2, use_rx=True), "client": stream_from_probes("client", cp2, use_rx=True)}
    rep_l2 = analyse_run(lstreams2, host_ref, puppet_ref)
    expect("twin: loopback offset not removed is caught", bool(judge(rep_l2, good_status)))
    a1 = {"ref": "0xFF00AA", "pos": [1.0, 2.0, 3.0], "bones": {"Head": [1.0, 2.0, 63.0]}}
    a2 = shift_actor(a1, [10.0, 0.0, -1.0])
    expect("shift_actor moves pos and bones, not the original", a2["pos"] == [11.0, 2.0, 2.0] and a2["bones"]["Head"] == [11.0, 2.0, 62.0] and a1["pos"] == [1.0, 2.0, 3.0])

    # 5. status parsing
    try:
        find_puppet({"puppets": []}, 0)
        expect("no puppet raises", False)
    except NotReady:
        pass
    try:
        find_puppet({"puppets": [{"slot": 0, "ref": "0x1", "phase": "Loading3D"}]}, 0)
        expect("loading puppet raises", False)
    except NotReady:
        pass
    ref, _ = find_puppet(good_status, 0)
    expect("find_puppet parses the hex ref", ref == puppet_ref)

    # 6. the choreography refuses to run without the input driver (no fake animation)
    class NoDriver:
        def has_input_driver(self):
            return False

    try:
        run_choreography(NoDriver(), lambda *a: None)
        expect("choreography without driver refuses", False)
    except NotReady:
        pass

    # 7. the choreography with a stub bot executes every step in order and tells the stuck detector which ones move
    class StubBot:
        def has_input_driver(self):
            return True

        def pos(self):
            return (0.0, 0.0, 0.0)

        def __getattr__(self, name):   # goto / jump / reload / look / stop / wait / sneak_toggle
            return lambda *a, **k: True

    seen: List[Tuple[str, bool]] = []
    run_choreography(StubBot(), lambda name, t0, t1, mv: seen.append((name, mv)))
    expect("choreography steps and moving flags", seen == CHOREOGRAPHY)

    print(f"proof_puppet selftest: {checks} checks, {fails_total} failed")
    if fails_total:
        return 1
    print("proof_puppet selftest: ALL OK")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--host", type=int, default=0, help="instance number of the machine whose real player is copied")
    ap.add_argument("--client", type=int, default=1, help="instance number of the machine that shows the puppet")
    ap.add_argument("--slot", type=int, default=0, help="coop slot of the host player as seen on the client")
    ap.add_argument("--hz", type=float, default=20.0)
    ap.add_argument("--out")
    ap.add_argument("--load-save", help="after the choreography, load this save on the client and check for orphan puppets")
    ap.add_argument("--loopback", action="store_true", help="ONE instance (--host): the puppet copies the local player")
    ap.add_argument("--offset", default="160,0,0", help="loopback: world-space shift of the puppet, x,y,z (default 160,0,0 = 2.3 m)")
    ap.add_argument("--selftest", action="store_true", help="offline: synthetic streams only, never touches a game")
    a = ap.parse_args(argv)
    if a.selftest:
        return _selftest()
    if a.loopback:
        return run_loopback(a)
    return run_live(a)


if __name__ == "__main__":
    raise SystemExit(main())
