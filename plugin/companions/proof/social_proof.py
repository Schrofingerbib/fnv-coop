"""In-game proof helper for plugin/companions (docs/tools/companions-crime.md). NEVER run by the agents: the helper runs it on his PC against two or three live
game instances (host = instance 0, guests = 1 and 2) that are in a co-op session with nvcoop.ini [companions] enabled=1 dev=1 (and the live layer's SocialService
wired, see plugin/companions/companions_glue.h).

    python -I plugin/companions/proof/social_proof.py snapshot --out out/social/before.json
    ... drive it: python -I plugin/companions/proof/social_proof.py drive --faction FalloutNV.esm:HEX --npc FalloutNV.esm:HEX ...
    python -I plugin/companions/proof/social_proof.py snapshot --out out/social/after.json
    python -I plugin/companions/proof/social_proof.py check out/social/before.json out/social/after.json --crimes 3 --kills 2
    python -I plugin/companions/proof/social_proof.py --selftest      # game-free: the checker itself, with twins

`snapshot` calls the bridge method social.status on every instance. `drive` uses social.dev (crime / kill / cmd / forgive / settings) so the proof does not need
the combat glue to be wired, then reads the engine's own faction flags with the condition functions GetPCFactionAttack / GetPCFactionMurder / GetPCEnemyofFaction
on every instance (they must agree). `check` prints one PASS/FAIL line per rule and exits 1 on any FAIL.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


def delta(before: Dict[str, Any], after: Dict[str, Any], key: str) -> int:
    return int(after.get(key, 0)) - int(before.get(key, 0))


def flatten(st: Dict[str, Any]) -> Dict[str, Any]:
    """social.status is {enabled, running, engine{lines,linesFailed,evalCalls,evalFaults}, sync{host,self,synced,companions,...,stats{...}}} or sync = null."""
    sync = st.get("sync") or {}
    flat: Dict[str, Any] = {"enabled": st.get("enabled"), "running": st.get("running")}
    flat.update({"engine_" + k: v for k, v in (st.get("engine") or {}).items()})
    for k, v in sync.items():
        if k != "stats":
            flat[k] = v
    flat.update(sync.get("stats") or {})
    return flat


def check(before: List[Dict[str, Any]], after: List[Dict[str, Any]], expect: Optional[Dict[str, int]] = None, *, mutate: int = 0) -> List[Tuple[str, bool, str]]:
    """before/after: one flat social.status dict per machine (host first). expect: optional {"crimes": n, "kills": n, "alarms": n, "xpGrants": n}."""
    expect = expect or {}
    out: List[Tuple[str, bool, str]] = []
    n = len(after)

    def d(i: int, k: str) -> int:
        return delta(before[i], after[i], k)

    def total(k: str) -> int:
        return sum(d(i, k) for i in range(n))

    out.append(("every machine has the social module running", all(a.get("running") for a in after), str([a.get("running") for a in after])))
    out.append(("every machine has heard the host's picture", all(a.get("synced") for a in after) or mutate == 1, str([a.get("synced") for a in after])))
    out.append(("the companion tables are equal on every machine", len({a.get("companionDigest") for a in after}) == 1 or mutate == 2, str([a.get("companionDigest") for a in after])))
    out.append(("the wanted tables are equal on every machine", len({a.get("bountyDigest") for a in after}) == 1 or mutate == 3, str([a.get("bountyDigest") for a in after])))
    out.append(("the settings are the host's on every machine", len({a.get("settingsRev") for a in after}) == 1 or mutate == 4, str([a.get("settingsRev") for a in after])))
    out.append(("no undecodable datagram and no forged author", all(d(i, "dropped") == 0 and d(i, "spoofed") == 0 for i in range(n)) or mutate == 5,
                str([(d(i, "dropped"), d(i, "spoofed")) for i in range(n)])))
    out.append(("no console line and no condition call failed", all(d(i, "engine_linesFailed") == 0 and d(i, "engine_evalFaults") == 0 for i in range(n)) or mutate == 6,
                str([(d(i, "engine_linesFailed"), d(i, "engine_evalFaults")) for i in range(n)])))
    out.append(("no teammate flag change was refused by the engine", all(d(i, "teammateFailed") == 0 for i in range(n)) or mutate == 7, str([d(i, "teammateFailed") for i in range(n)])))
    out.append(("no client asked for more than a few resyncs", all(d(i, "resyncsAsked") <= 3 for i in range(n)) or mutate == 8, str([d(i, "resyncsAsked") for i in range(n)])))
    crimes = d(0, "crimesCounted")
    if "crimes" in expect:
        out.append(("the host counted the expected number of crimes", crimes == expect["crimes"] or mutate == 9, f"expected={expect['crimes']} counted={crimes}"))
    if crimes > 0:
        out.append(("every machine applied the host's faction flags", all(d(i, "bountyApplied") >= 1 for i in range(n)) or mutate == 10, str([d(i, "bountyApplied") for i in range(n)])))
    alarms = [d(i, "alarmsRaised") for i in range(n)]
    out.append(("every machine raised the same alarms (each alarm once per machine)", len(set(alarms)) == 1 or mutate == 11, str(alarms)))
    if "alarms" in expect:
        out.append(("the expected number of alarms was raised", alarms[0] == expect["alarms"] or mutate == 12, f"expected={expect['alarms']} got={alarms[0]}"))
    kills = d(0, "killsSettled")
    if "kills" in expect:
        out.append(("the host settled the expected number of kills", kills == expect["kills"] or mutate == 13, f"expected={expect['kills']} settled={kills}"))
    grants = total("xpLocal") + total("xpCommitted")
    if "xpGrants" in expect:
        out.append(("kill XP was paid exactly the expected number of times", grants == expect["xpGrants"] or mutate == 14, f"expected={expect['xpGrants']} paid={grants}"))
    out.append(("no more kills were settled than reported", kills <= max(total("killsReported"), 0) or mutate == 15, f"settled={kills} reported={total('killsReported')}"))
    out.append(("no commit to the story session failed", all(d(i, "commitFailed") == 0 for i in range(n)) or mutate == 16, str([d(i, "commitFailed") for i in range(n)])))
    out.append(("an alarm alerted at least one NPC somewhere when there was one", alarms[0] == 0 or total("actorsAlerted") >= 1 or mutate == 17, f"alarms={alarms[0]} alerted={total('actorsAlerted')}"))
    return out


def flags_agree(flags: List[Dict[str, int]], *, mutate: int = 0) -> Tuple[bool, str]:
    """The engine's own faction flags (GetPCFactionAttack / GetPCFactionMurder / GetPCEnemyofFaction) read on every instance must be identical."""
    same = all(f == flags[0] for f in flags) or mutate == 1
    return same, str(flags)


def snapshot(instances: List[int]) -> List[Dict[str, Any]]:
    sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "tools" / "runner"))
    from nvbridge import NvBridge  # noqa: E402

    res = []
    for inst in instances:
        with NvBridge.connect(instance=inst, wait_s=10) as nv:
            res.append(flatten(nv.call("social.status")))
    return res


def drive(instances: List[int], faction: str, npc: Optional[str], victim_net: int) -> List[Dict[str, int]]:
    """Scripted scenario through social.dev, then the engine's faction flags on every instance. Instance 1 is the criminal."""
    sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "tools" / "runner"))
    from nvbridge import NvBridge  # noqa: E402

    conns = [NvBridge.connect(instance=i, wait_s=10) for i in instances]
    try:
        crim = conns[1] if len(conns) > 1 else conns[0]
        crim.call("social.dev", {"op": "crime", "kind": 0, "faction": faction, "victimNet": victim_net, "cell": 0})       # a witnessed assault
        crim.call("social.dev", {"op": "kill", "id": 1, "victimNet": victim_net, "xp": 100, "karma": 4, "killer": 1})        # ... and the kill
        if npc:
            crim.call("social.dev", {"op": "cmd", "kind": 1, "npc": npc})                                                     # wait
        flags = []
        for c in conns:
            flags.append({fn: int(c.eval(f"{fn} {faction.split(':')[1]}")) for fn in ("GetPCFactionAttack", "GetPCFactionMurder", "GetPCEnemyofFaction")})
        return flags
    finally:
        for c in conns:
            c.close()


# ---- self test (game-free) ---------------------------------------------------------------------------------------------------------------


def _machines() -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    zero = {"running": 1, "synced": True, "companionDigest": "a", "bountyDigest": "b", "settingsRev": 2, "dropped": 0, "spoofed": 0, "engine_linesFailed": 0, "engine_evalFaults": 0,
            "teammateFailed": 0, "resyncsAsked": 0, "crimesCounted": 0, "bountyApplied": 0, "alarmsRaised": 0, "actorsAlerted": 0, "killsSettled": 0, "killsReported": 0,
            "xpLocal": 0, "xpCommitted": 0, "commitFailed": 0}
    before = [dict(zero) for _ in range(3)]
    after = [dict(zero, crimesCounted=3, bountyApplied=2, alarmsRaised=1, killsSettled=2, killsReported=3, xpLocal=1, xpCommitted=0, actorsAlerted=1),
             dict(zero, bountyApplied=2, alarmsRaised=1, killsReported=1, xpCommitted=1, actorsAlerted=1),
             dict(zero, bountyApplied=2, alarmsRaised=1, killsReported=0)]
    return before, after


def selftest() -> int:
    failures = 0

    def expect(name: str, cond: bool) -> None:
        nonlocal failures
        if not cond:
            failures += 1
            print(f"  FAIL {name}")

    want = {"crimes": 3, "kills": 2, "alarms": 1, "xpGrants": 2}
    b, a = _machines()
    expect("a healthy session passes every rule", all(ok for _r, ok, _d in check(b, a, want)))
    cases = {
        1: lambda b, a: a[2].update(synced=False),
        2: lambda b, a: a[1].update(companionDigest="x"),
        3: lambda b, a: a[2].update(bountyDigest="y"),
        4: lambda b, a: a[1].update(settingsRev=1),
        5: lambda b, a: a[0].update(spoofed=1),
        6: lambda b, a: a[1].update(engine_evalFaults=1),
        7: lambda b, a: a[2].update(teammateFailed=2),
        8: lambda b, a: a[2].update(resyncsAsked=9),
        9: lambda b, a: a[0].update(crimesCounted=1),
        10: lambda b, a: a[1].update(bountyApplied=0),
        11: lambda b, a: a[2].update(alarmsRaised=2),
        12: lambda b, a: [m.update(alarmsRaised=2) for m in a],
        13: lambda b, a: a[0].update(killsSettled=1),
        14: lambda b, a: a[1].update(xpCommitted=3),
        15: lambda b, a: a[0].update(killsSettled=9),
        16: lambda b, a: a[0].update(commitFailed=1),
        17: lambda b, a: [m.update(actorsAlerted=0) for m in a],
    }
    for n, brk in cases.items():
        b, a = _machines()
        brk(b, a)
        res = check(b, a, want)
        expect(f"twin {n}: the broken counters are caught", not all(ok for _r, ok, _d in res))
        b, a = _machines()
        brk(b, a)
        expect(f"twin {n}: the mutated checker lets them through", all(ok for _r, ok, _d in check(b, a, want, mutate=n)) or n in (12, 14, 15))
    st = {"enabled": True, "running": True, "engine": {"lines": 4, "linesFailed": 1, "evalCalls": 9, "evalFaults": 0},
          "sync": {"host": True, "self": 0, "synced": True, "companionDigest": "ab", "stats": {"crimesCounted": 2, "bountyApplied": 3}}}
    f = flatten(st)
    expect("flatten keeps engine, sync and stats counters", f["engine_linesFailed"] == 1 and f["crimesCounted"] == 2 and f["synced"] is True and f["running"] is True)
    expect("a status without a session flattens to not running", flatten({"enabled": True, "running": False, "sync": None}).get("running") is False)
    ok, _ = flags_agree([{"a": 1}, {"a": 1}])
    expect("equal faction flags agree", ok)
    ok, _ = flags_agree([{"a": 1}, {"a": 0}])
    expect("different faction flags do not agree", not ok)
    expect("the mutated flag check lets a difference through", flags_agree([{"a": 1}, {"a": 0}], mutate=1)[0])
    b, a = _machines()
    for m in a:
        m["crimesCounted"] = 0
        m["bountyApplied"] = 0
    expect("a crime-free run does not demand faction flags", all(ok for r, ok, _d in check(b, a, {}) if "faction flags" in r))
    if failures:
        print(f"social_proof selftest: {failures} failed")
        return 1
    print("social_proof selftest: ALL OK")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--selftest", action="store_true")
    sub = ap.add_subparsers(dest="cmd")
    s = sub.add_parser("snapshot")
    s.add_argument("--out", required=True)
    s.add_argument("--instances", default="0,1")
    d = sub.add_parser("drive")
    d.add_argument("--faction", required=True)
    d.add_argument("--npc")
    d.add_argument("--victim-net", type=int, default=1)
    d.add_argument("--instances", default="0,1")
    c = sub.add_parser("check")
    c.add_argument("before")
    c.add_argument("after")
    c.add_argument("--crimes", type=int)
    c.add_argument("--kills", type=int)
    c.add_argument("--alarms", type=int)
    c.add_argument("--xp-grants", type=int)
    args = ap.parse_args()
    if args.selftest:
        return selftest()
    if args.cmd == "snapshot":
        res = snapshot([int(x) for x in args.instances.split(",")])
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(json.dumps(res, indent=1), encoding="utf8")
        print(f"wrote {args.out} ({len(res)} machines)")
        return 0
    if args.cmd == "drive":
        flags = drive([int(x) for x in args.instances.split(",")], args.faction, args.npc, args.victim_net)
        ok, detail = flags_agree(flags)
        print(("PASS" if ok else "FAIL") + " the engine's faction flags agree on every instance: " + detail)
        return 0 if ok else 1
    if args.cmd == "check":
        before = json.loads(Path(args.before).read_text(encoding="utf8"))
        after = json.loads(Path(args.after).read_text(encoding="utf8"))
        want = {k: v for k, v in (("crimes", args.crimes), ("kills", args.kills), ("alarms", args.alarms), ("xpGrants", args.xp_grants)) if v is not None}
        bad = 0
        for rule, ok, detail in check(before, after, want):
            print(("PASS " if ok else "FAIL ") + rule + ("" if ok else f"  [{detail}]"))
            bad += 0 if ok else 1
        return 1 if bad else 0
    ap.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
