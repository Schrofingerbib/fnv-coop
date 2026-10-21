#!/usr/bin/env python3
"""Run the four enemy / NPC sync tests (docs/tools/enemy-tests.md): fight, handoff, spawn, loot-from-corpse.

In a game (Track B helper; this tool never launches a game or takes the game slot). Start two co-op instances joined as one session
(host = pipe 0, client = pipe 1) with `[dev] bots=1` and `[dev] animprobe=1` in nvcoop.ini, then:

    python -I tools/bots/run_fight.py --instances 0,1                     # all four, in this order, scenario goodsprings_enemies
    python -I tools/bots/run_fight.py --instances 2                       # the same: a single number N means instances 0..N-1
    python -I tools/bots/run_fight.py --instances 0,1 --which fight       # one: fight | handoff | spawn | loot (or the full names)
    python -I tools/bots/run_fight.py --instances 0,1 --preflight-only    # just report which bridge methods this build has
    python -I tools/bots/run_fight.py --instances 0,1 --chaos bad_wifi --chaos-seed 7 --chaos-rpc runs/chaos.json
    python -I tools/bots/run_fight.py --instances 0,1 --keep-going --outdir out/fight-run1

Game-free (the fake world, what the unit tests use):

    python -I tools/bots/run_fight.py --fake
    python -I tools/bots/run_fight.py --fake --which fight --bug double_damage      # must FAIL (exit 1)
    python -I tools/bots/run_fight.py --fake --list-bugs

Before each test the instances jump to the scenario (scenarios/goodsprings_enemies.json). After every step the engine settles, runs
snapdiff host vs client, and checks the desync counters; the animation / pose check (T7, FIGHT thresholds) runs at the end of each test.
A failing step saves FAIL-<n>-<step>/ under the output directory (failure.json, snapshots, events, flight dumps) for tools/flight.

Exit code: 0 all pass, 1 a test failed, 2 usage or setup error, 3 BLOCKED (a bridge method the tests cannot do without is missing in
this build: nothing was run; see the table). Nothing here has run in a game yet.
"""
from __future__ import annotations

import argparse
import os
import sys
from typing import Any, Dict, List, Optional, Sequence, Tuple

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
for p in (HERE, os.path.join(REPO, "tools"), os.path.join(REPO, "tools", "runner")):
    if p not in sys.path:
        sys.path.insert(0, p)

from playbooks import fakecoop, registry  # noqa: E402

SHORT = {"fight": "enemy_fight", "handoff": "enemy_handoff", "spawn": "enemy_spawn", "loot": "corpse_loot_race"}
EXIT_BLOCKED = 3
METHOD_NOT_FOUND = -32601

# (bridge method, harmless params, what it is for, required). A method counts as PRESENT when calling it does not answer
# "method not found": bad parameters are fine (the answer proves the method exists). Methods that act (bot.fire ...) are not probed.
PROBES: List[Tuple[str, dict, str, bool]] = [
    ("coop.status", {}, "co-op session state and desync counters", True),
    ("coop.owner", {"ref": "FalloutNV.esm:104C70"}, "actor ownership (plugin/actors): owner, epoch, AI flag, net id", True),
    ("ref.info", {"ref": "player"}, "positions of actors (bots)", True),
    ("dev.countRefs", {"base": "FalloutNV.esm:104C6A"}, "duplicate-NPC checks ([dev] bots=1)", True),
    ("anim.probe", {"refs": []}, "animation / pose check ([dev] animprobe=1)", True),
    ("bot.status", {}, "bot input driver ([dev] bots=1)", True),
    ("coop.combat", {"ref": "FalloutNV.esm:104C70"}, "death / kill-credit / hit counters (combat hooks): checks are skipped without it", False),
    ("actors.port", {}, "the real actor port (actor-port agent): enabled, session, fed actors; shown below", False),
    ("combat.status", {}, "the combat module's own counters: saved to engine_stats.json, never asserted", False),
    ("anim.npc", {}, "the NPC animation driver's counters: saved to engine_stats.json, never asserted", False),
    ("dev.refsOf", {"base": "FalloutNV.esm:104C6A"}, "encounter member lists with net ids (spawn test): counts only without it", False),
    ("flight.dump", {"reason": "preflight"}, "flight recorder dump on a failure: fewer failure files without it", False),
]


def preflight(bridges: Sequence[Any]) -> Tuple[List[Dict[str, Any]], List[str]]:
    """-> (rows, missing required method names). `bridges` are objects with call(method, params, timeout_s=...)."""
    rows: List[Dict[str, Any]] = []
    missing: List[str] = []
    for method, params, why, required in PROBES:
        per = []
        answers = []
        for i, b in enumerate(bridges):
            try:
                answers.append(b.call(method, dict(params), timeout_s=10))
                per.append("present")
            except Exception as e:  # noqa: BLE001
                code = getattr(e, "code", None)
                name = type(e).__name__
                if name in ("ConnectionLost", "GameCrashed"):
                    raise
                if code == METHOD_NOT_FOUND or "unknown method" in str(e).lower() or "not implemented" in str(e).lower():
                    per.append("MISSING")
                else:
                    per.append("present")           # an error about the parameters or the world proves the method exists
        status = "MISSING" if "MISSING" in per else "present"
        rows.append({"method": method, "why": why, "required": required, "status": status, "per_instance": per, "answers": answers})
        if status == "MISSING" and required:
            missing.append(method)
    return rows, missing


def print_preflight(rows: List[Dict[str, Any]], missing: List[str]) -> None:
    print("bridge methods the enemy tests use:")
    for r in rows:
        tag = "required" if r["required"] else "optional"
        print(f"  {r['status']:8} {tag}  {r['method']:16} {r['why']}")
    for r in rows:
        if r["method"] == "actors.port" and r["answers"]:
            for i, a in enumerate(r["answers"]):
                if isinstance(a, dict):
                    print(f"  actors.port on instance {i}: enabled={a.get('enabled')} session={a.get('session')} fed={a.get('fed')} "
                          f"portEntries={a.get('portEntries')} standIns={a.get('standIns')}")
    if missing:
        print()
        print(f"BLOCKED: {len(missing)} required bridge method(s) missing in this build: {', '.join(missing)}")
        print("  coop.owner needs the actor-ownership engine adapter (plugin/actors/engine); [dev] bots=1 and [dev] animprobe=1 go in nvcoop.ini.")


def parse_instances(text: str) -> List[int]:
    """'0,1' -> [0, 1]; a single number N (>= 2) means N instances, ids 0..N-1 (host first)."""
    ids = [int(x) for x in text.split(",") if x.strip() != ""]
    if len(ids) == 1:
        if ids[0] < 2:
            raise ValueError("--instances N needs N >= 2 (or a list of instance ids such as 0,1)")
        ids = list(range(ids[0]))
    if len(ids) < 2 or len(set(ids)) != len(ids):
        raise ValueError(f"--instances needs two or more different instance ids, got {text!r}")
    return ids


def pick(which: str) -> List[str]:
    if which in ("", "all"):
        return list(registry.ENEMY_TESTS)
    out = []
    for w in which.split(","):
        w = w.strip()
        n = SHORT.get(w, w)
        if n not in registry.ENEMY_TESTS:
            raise ValueError(f"unknown test {w!r}; use fight, handoff, spawn, loot, all (or {', '.join(registry.ENEMY_TESTS)})")
        if n not in out:
            out.append(n)
    return out


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--which", default="all", help="fight, handoff, spawn, loot, a comma list, or all (default)")
    ap.add_argument("--fake", action="store_true", help="game-free run on the fake co-op world")
    ap.add_argument("--bug", action="append", default=[], help="(fake) switch on a deliberate bug; the run must then fail")
    ap.add_argument("--list-bugs", action="store_true", help="(fake) list the bugs and which test catches each, then exit")
    ap.add_argument("--players", type=int, default=0, help="number of bots (fake: 2 to 4; in a game: the first N of --instances)")
    ap.add_argument("--instances", default="0,1", help="bridge instance ids, host first")
    ap.add_argument("--pool", action="store_true", help="lease the instances from tools/pool")
    ap.add_argument("--wait", type=float, default=30.0)
    ap.add_argument("--no-scenario", action="store_true", help="do not jump to the scenario first")
    ap.add_argument("--no-anim", action="store_true", help="skip the animation / pose assertions")
    ap.add_argument("--preflight-only", action="store_true", help="only report which bridge methods the build has")
    ap.add_argument("--force", action="store_true", help="run even when a required bridge method is missing")
    ap.add_argument("--chaos", default="")
    ap.add_argument("--chaos-seed", type=int, default=None)
    ap.add_argument("--chaos-rpc", default="")
    ap.add_argument("--outdir", default="")
    ap.add_argument("--keep-going", action="store_true")
    a = ap.parse_args(argv)

    if a.list_bugs:
        for name, bug in registry.TWINS:
            if name in registry.ENEMY_TESTS:
                print(f"{name:18} {bug:24} {fakecoop.BUGS[bug]}")
        return 0
    try:
        names = pick(a.which)
        ids = None if a.fake else parse_instances(a.instances)
    except ValueError as e:
        print(str(e), file=sys.stderr)
        return 2
    if ids is not None:
        a.instances = ",".join(str(i) for i in ids)

    import run_coop
    args: List[str] = ["--playbook", ",".join(names)]
    for opt, val in (("--players", a.players or None), ("--outdir", a.outdir or None), ("--chaos", a.chaos or None),
                     ("--chaos-seed", a.chaos_seed), ("--chaos-rpc", a.chaos_rpc or None)):
        if val is not None:
            args += [opt, str(val)]
    for flag, on in (("--no-scenario", a.no_scenario), ("--no-anim", a.no_anim), ("--keep-going", a.keep_going)):
        if on:
            args.append(flag)
    if a.fake:
        for b in a.bug:
            if b not in fakecoop.BUGS:
                print(f"unknown fake bug {b!r}; use --list-bugs", file=sys.stderr)
                return 2
            args += ["--bug", b]
        return run_coop.main(["--fake"] + args)

    import contextlib
    with contextlib.ExitStack() as stack:
        try:
            if a.pool:
                sys.path.insert(0, os.path.join(REPO, "tools", "pool"))
                from client import lease           # noqa: WPS433
                L = stack.enter_context(lease("fight-test", n=a.players or 2, ttl_s=1800, timeout_s=600))
                nvs = [L.bridge(i) for i in L.instances]
            else:
                from nvbridge import NvBridge      # noqa: WPS433
                nvs = [stack.enter_context(NvBridge.connect(instance=int(i), wait_s=a.wait)) for i in a.instances.split(",")]
        except Exception as e:  # noqa: BLE001
            print(f"cannot reach the game instances: {e}", file=sys.stderr)
            return 2
        rows, missing = preflight(nvs)
        print_preflight(rows, missing)
        if a.preflight_only:
            return EXIT_BLOCKED if missing else 0
        if missing and not a.force:
            return EXIT_BLOCKED
        if a.pool:
            args += ["--pool"]
        else:
            args += ["--instances", a.instances, "--wait", str(a.wait)]
    # the bridges above are closed again; run_coop opens its own (one pipe client per instance at a time)
    return run_coop.main(args)


if __name__ == "__main__":
    sys.exit(main())
