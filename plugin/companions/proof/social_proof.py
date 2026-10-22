"""In-game proof helper for plugin/companions (docs/tools/companions-crime.md). Runs against two or three live game instances (host = instance 0,
guests = 1 and 2) that are in a co-op session with nvcoop.ini [companions] enabled=1 dev=1 (and the live layer's SocialService wired, see
plugin/companions/companions_glue.h).

    python -I plugin/companions/proof/social_proof.py snapshot --out out/social/before.json
    ... drive it: python -I plugin/companions/proof/social_proof.py drive --faction FalloutNV.esm:0A46E7 --instances 0,1,2
        (with no --npc and no --victim-net the drive sets everything up itself, see below; with them it is the old manual drive)
    python -I plugin/companions/proof/social_proof.py snapshot --out out/social/after.json
    python -I plugin/companions/proof/social_proof.py check out/social/before.json out/social/after.json --crimes 3 --kills 2
    python -I plugin/companions/proof/social_proof.py --selftest      # game-free: the checker itself, with twins

`drive` without --npc recruits Boone for guest 1 through the bridge, no human: on the host it enables him and moves him to the host's player, then runs
`exec.ref {ref: boone, cmd: "SetPlayerTeammate 1", scope: true, acting: {ghost: <guest 1's puppet, coop.status playerRefs.slots>, slot: 1}}`, which the capture
hook records as a CompanionOwner effect for slot 1 (what the story path would do). It then asserts social.status companions=1 on every instance, that
social.dev cmd on guest 1 is applied (stateApplied +1) and that the same command on guest 2 is refused (cmdRefused +1, stateApplied unchanged).
`drive` without --victim-net places an NCR trooper (--trooper-base, default eldoradoNCRTrooper1 FalloutNV.esm:0E7E21) next to guest 1's puppet with `placeatme`
run by the host, finds the new reference with dev.refsOf (before / after) and reads its net id with coop.owner; that is the crime's victim.
`snapshot` calls the bridge method social.status on every instance. `drive` uses social.dev (crime / kill / cmd / forgive / settings) so the proof does not need
the combat glue to be wired, then reads the engine's own faction flags with the condition functions GetPCFactionAttack / GetPCFactionMurder / GetPCEnemyofFaction
on every instance (they must agree). `check` prints one PASS/FAIL line per rule and exits 1 on any FAIL.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

BOONE = "FalloutNV.esm:096BCE"             # CraigBooneREF (scenarios/companion_recruit.json)
NCR_TROOPER_BASE = "FalloutNV.esm:0E7E21"  # eldoradoNCRTrooper1, a plain NPC_
NCR_FACTION = "FalloutNV.esm:0A46E7"       # NCRFactionNV
POLL_STEP_S = 0.5
Check = Tuple[str, bool, str]


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


def _is_fatal(e: BaseException) -> bool:
    return type(e).__name__ in ("ConnectionLost", "GameCrashed")


def _wait(pred: Callable[[], Any], timeout_s: float, sleep: Callable[[float], None]) -> Any:
    """Polls `pred` (a truthy value ends it) every POLL_STEP_S for up to timeout_s; returns the last value."""
    v = pred()
    for _ in range(int(timeout_s / POLL_STEP_S)):
        if v:
            break
        sleep(POLL_STEP_S)
        v = pred()
    return v


def _hexid(ref: str) -> str:
    h = ref.split(":")[-1]
    return (h[2:] if h.lower().startswith("0x") else h).upper().lstrip("0") or "0"


def puppet_ref(host: Any, slot: int) -> str:
    """The host's puppet (ghost) reference of player `slot` as "Plugin.esm:HEX": coop.status playerRefs.slots gives "plugin:<decimal local id>"."""
    s = ((host.call("coop.status").get("playerRefs") or {}).get("slots") or {}).get(str(slot), "")
    if not s:
        raise RuntimeError(f"the host has no puppet for player {slot} (coop.status playerRefs.slots; needs [live] avatars=1 and the guest in the session)")
    plugin, local = s.rsplit(":", 1)
    return f"{plugin}:{int(local):X}"


def _status(nv: Any) -> Dict[str, Any]:
    return flatten(nv.call("social.status"))


def _try_call(nv: Any, method: str, params: Dict[str, Any]) -> str:
    """'' when the bridge call worked, else the error text (a refused social.dev command is an RPC error)."""
    try:
        nv.call(method, params)
        return ""
    except Exception as e:  # noqa: BLE001
        if _is_fatal(e):
            raise
        return str(e) or type(e).__name__


def recruit_boone(conns: List[Any], boone: str, sleep: Callable[[float], None]) -> List[Check]:
    """Recruit `boone` for guest 1 through the bridge (see the module doc), then the companion rules of docs/INGAME-TESTS.md step 4 item 5."""
    out: List[Check] = []
    host = conns[0]
    try:
        ghost = puppet_ref(host, 1)
        host.exec("enable", ref=boone)
        host.exec("moveto player", ref=boone)
        r = host.call("exec.ref", {"ref": boone, "cmd": "SetPlayerTeammate 1", "scope": True, "acting": {"ghost": ghost, "slot": 1}})
        out.append(("the host ran SetPlayerTeammate for Boone as guest 1's puppet", bool(r.get("ok")), str(r)))
    except Exception as e:  # noqa: BLE001
        if _is_fatal(e):
            raise
        return [("Boone can be recruited for guest 1 through the bridge", False, str(e))]

    def everywhere() -> bool:
        return all(_status(c).get("companions") == 1 for c in conns)

    ok = bool(_wait(everywhere, 10.0, sleep))
    out.append(("social.status companions=1 on every instance", ok, str([_status(c).get("companions") for c in conns])))
    if not ok:
        return out
    pre = [_status(c) for c in conns]        # after the roster settled: the recruit itself may bump stateApplied
    out.append(("(info) teammateUnset on the host", True, str(_status(host).get("teammateUnset"))))
    err = _try_call(conns[1], "social.dev", {"op": "cmd", "kind": 1, "npc": boone})
    g1 = _status(conns[1])
    out.append(("guest 1 can command Boone: stateApplied becomes 1 there", not err and delta(pre[1], g1, "stateApplied") == 1,
                f"error={err!r} stateApplied+={delta(pre[1], g1, 'stateApplied')}"))
    if len(conns) > 2:
        err2 = _try_call(conns[2], "social.dev", {"op": "cmd", "kind": 1, "npc": boone})
        g2 = _status(conns[2])
        out.append(("the same command on guest 2 is refused (cmdRefused +1, nothing applied)",
                    bool(err2) and delta(pre[2], g2, "cmdRefused") == 1 and delta(pre[2], g2, "stateApplied") == 0,
                    f"error={err2!r} cmdRefused+={delta(pre[2], g2, 'cmdRefused')} stateApplied+={delta(pre[2], g2, 'stateApplied')}"))
    return out


def place_trooper(conns: List[Any], base: str, sleep: Callable[[float], None]) -> Tuple[Optional[int], List[Check]]:
    """Place one `base` NPC next to guest 1's puppet (placeatme run by the host) and return (its net id, checks)."""
    host = conns[0]
    out: List[Check] = []
    try:
        ghost = puppet_ref(host, 1)
        before = set(host.call("dev.refsOf", {"base": base}).get("refs") or [])
        host.exec(f"placeatme {_hexid(base)} 1", ref=ghost)
        new = _wait(lambda: sorted(set(host.call("dev.refsOf", {"base": base}).get("refs") or []) - before), 5.0, sleep)
    except Exception as e:  # noqa: BLE001
        if _is_fatal(e):
            raise
        return None, [("an NCR trooper can be placed next to guest 1", False, str(e))]
    out.append(("placeatme made exactly one new trooper reference (dev.refsOf before / after)", len(new) == 1, str(new)))
    if not new:
        return None, out
    ref = new[-1]
    ref = ref if ":" in ref else f"FalloutNV.esm:{_hexid(ref)}"

    def netid() -> Optional[int]:
        r = host.call("coop.owner", {"ref": ref})
        n = r.get("netId")
        return int(n) if r.get("known") and n else None

    try:
        nid = _wait(netid, 10.0, sleep)
    except Exception as e:  # noqa: BLE001
        if _is_fatal(e):
            raise
        return None, out + [("the trooper has a net id (coop.owner)", False, str(e))]
    out.append(("the trooper has a net id (coop.owner netId)", bool(nid), f"{ref} -> {nid}"))
    return (int(nid) if nid else None), out


def drive(instances: List[int], faction: str, npc: Optional[str], victim_net: Optional[int], *, boone: str = BOONE, trooper_base: str = NCR_TROOPER_BASE,
          recruit: bool = True) -> Tuple[List[Dict[str, int]], List[Check]]:
    sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "tools" / "runner"))
    from nvbridge import NvBridge  # noqa: E402
    import nvready  # noqa: E402

    conns = [NvBridge.connect(instance=i, wait_s=10) for i in instances]
    try:
        why = nvready.wait_ready(conns)      # a copy that is still booting or loading answers "main thread did not service the request"
        if why:
            print("cannot reach the game instances: " + why, flush=True)
            raise SystemExit(2)
        return drive_on(conns, faction, npc, victim_net, boone=boone, trooper_base=trooper_base, recruit=recruit, sleep=time.sleep)
    finally:
        for c in conns:
            c.close()


def drive_on(conns: List[Any], faction: str, npc: Optional[str], victim_net: Optional[int], *, sleep: Callable[[float], None], boone: str = BOONE,
             trooper_base: str = NCR_TROOPER_BASE, recruit: bool = True) -> Tuple[List[Dict[str, int]], List[Check]]:
    """Scripted scenario through social.dev, then the engine's faction flags on every instance. Instance 1 is the criminal.
    npc None (and recruit): Boone is recruited for guest 1 first (and commanded by the recruit step). victim_net None: an NCR trooper is placed
    next to guest 1 and its net id used. Explicit npc / victim_net keep the old manual drive."""
    checks: List[Check] = []
    recruited = False
    if npc is None and recruit and len(conns) > 1:
        checks += recruit_boone(conns, boone, sleep)
        npc, recruited = boone, True
        if not all(ok for _r, ok, _d in checks):
            return [], checks
    if victim_net is None:
        if len(conns) < 2:
            return [], checks + [("a trooper needs guest 1 (two or more instances)", False, "")]
        victim_net, more = place_trooper(conns, trooper_base, sleep)
        checks += more
        if victim_net is None:
            return [], checks
    crim = conns[1] if len(conns) > 1 else conns[0]
    crim.call("social.dev", {"op": "crime", "kind": 0, "faction": faction, "victimNet": victim_net, "cell": 0})       # a witnessed assault
    crim.call("social.dev", {"op": "kill", "id": 1, "victimNet": victim_net, "xp": 100, "karma": 4, "killer": 1})        # ... and the kill
    if npc and not recruited:
        crim.call("social.dev", {"op": "cmd", "kind": 1, "npc": npc})                                                     # wait
    flags = []
    for c in conns:
        flags.append({fn: int(c.eval(f"{fn} {faction.split(':')[1]}")) for fn in ("GetPCFactionAttack", "GetPCFactionMurder", "GetPCEnemyofFaction")})
    return flags, checks


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


class _FakeRpcError(Exception):
    pass


class _FakeSocialWorld:
    """Three machines for the `drive` setup. `defect` breaks one thing: norecruit (the SetPlayerTeammate line runs but no CompanionOwner is
    recorded) | wrongslot (recorded for slot 0, not the acting guest) | anyguest (guest 2 may command Boone too) | nonew (placeatme makes no reference)
    | nonet (the trooper never gets a net id) | noghost (the host has no puppet for guest 1) | split (the faction flags differ on instance 2)
    | slow (healthy but the guests hear of the recruit 1.5 s late: the polling must wait for it)."""

    GHOST_LOCAL = 0xFF0012
    NET_ID = 4242

    def __init__(self, defect: str = "", owner: Optional[int] = None):
        self.defect = defect
        self.t = 0.0
        self.owner = owner
        self.t_recruit = -99.0
        self.refs: List[str] = []
        self.stats = [{"stateApplied": 0, "cmdRefused": 0, "teammateUnset": 0} for _ in range(3)]
        self.exec_ref_calls = 0
        self.placed = 0
        self.dev: List[Tuple[str, Dict[str, Any]]] = []      # social.dev calls that were accepted, on the criminal

    def sleep(self, s: float) -> None:
        self.t += s


class _FakeSocialConn:
    def __init__(self, w: _FakeSocialWorld, idx: int):
        self.w = w
        self.i = idx

    def close(self) -> None:
        pass

    def exec(self, cmd: str, ref: Optional[str] = None) -> Dict[str, Any]:
        w = self.w
        if cmd.lower().startswith("placeatme ") and self.i == 0 and ref == f"FalloutNV.esm:{w.GHOST_LOCAL:X}" and w.defect != "nonew":
            w.placed += 1
            w.refs.append(f"FF{0x000100 + w.placed:06X}")
        return {"ok": True, "output": ""}

    def eval(self, expr: str, ref: Optional[str] = None) -> float:
        return 0.0 if (self.w.defect == "split" and self.i == 2) else 1.0

    def call(self, method: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        w, p = self.w, params or {}
        if method == "coop.status":
            return {"playerRefs": {"binds": 1, "slots": {"0": "", "1": "" if w.defect == "noghost" else f"FalloutNV.esm:{w.GHOST_LOCAL}", "2": "FalloutNV.esm:16711953"}}}
        if method == "social.status":
            heard = self.i == 0 or w.t >= w.t_recruit + (1.5 if w.defect == "slow" else 0.0)
            n = 1 if w.owner is not None and heard else 0
            return {"enabled": True, "running": True, "sync": {"host": self.i == 0, "self": self.i, "synced": True, "companions": n, "stats": dict(w.stats[self.i])}}
        if method == "exec.ref":
            w.exec_ref_calls += 1
            if not (p.get("scope") is True and p.get("cmd") == "SetPlayerTeammate 1" and p.get("acting", {}).get("ghost") == f"FalloutNV.esm:{w.GHOST_LOCAL:X}"):
                return {"ok": False}     # an unscoped or ghost-less line is "ambient": the capture hook records nothing
            if w.defect != "norecruit":
                w.owner = 0 if w.defect == "wrongslot" else int(p["acting"]["slot"])
                w.t_recruit = w.t
            return {"ok": True}
        if method == "dev.refsOf":
            return {"refs": list(w.refs), "count": len(w.refs)}
        if method == "coop.owner":
            known = p["ref"].split(":")[-1] in w.refs and w.defect != "nonet"
            return {"owner": 0, "known": known, "netId": w.NET_ID if known else 0}
        if method == "social.dev":
            if p["op"] == "cmd":
                if w.owner != self.i and not (w.defect == "anyguest" and self.i == 2):
                    w.stats[self.i]["cmdRefused"] += 1
                    raise _FakeRpcError("refused")
                w.stats[self.i]["stateApplied"] += 1
            else:
                w.dev.append((p["op"], dict(p)))
            return {"ok": True}
        raise KeyError(method)


def _selftest_drive(expect: Callable[[str, bool], None]) -> None:
    faction = NCR_FACTION

    def run(defect: str = "", n: int = 3, npc: Optional[str] = None, victim: Optional[int] = None, owner: Optional[int] = None):
        w = _FakeSocialWorld(defect, owner)
        flags, checks = drive_on([_FakeSocialConn(w, i) for i in range(n)], faction, npc, victim, sleep=w.sleep)
        ok = bool(flags) and all(c[1] for c in checks) and flags_agree(flags)[0]
        return w, flags, checks, ok

    w, flags, checks, ok = run()
    expect("drive: a healthy 3 instance setup passes every check", ok and len(checks) >= 6)
    crimes = [d for op, d in w.dev if op == "crime"]
    expect("drive: the crime's victim is the trooper's net id from coop.owner", len(crimes) == 1 and crimes[0]["victimNet"] == w.NET_ID and crimes[0]["faction"] == faction)
    expect("drive: Boone was recruited once through exec.ref and the trooper placed once", w.exec_ref_calls == 1 and w.placed == 1)
    expect("drive: the wait command was issued once by the recruit step, not again", w.stats[1]["stateApplied"] == 1)
    expect("drive: a slow roster (guests hear of the recruit 1.5 s late) still passes", run("slow")[3])
    expect("drive: two instances (no guest 2) still pass", run(n=2)[3])
    for defect in ("norecruit", "wrongslot", "anyguest", "nonew", "nonet", "noghost", "split"):
        expect(f"drive twin {defect}: the broken setup is caught", not run(defect)[3])
    w, flags, checks, ok = run(npc="FalloutNV.esm:096BCE", victim=7, owner=1)
    expect("drive: with --npc and --victim-net nothing is recruited or placed (the old manual drive)", ok and w.exec_ref_calls == 0 and w.placed == 0 and not checks)
    expect("drive: the old manual drive uses the given victim net and commands the given NPC", [d for op, d in w.dev if op == "crime"][0]["victimNet"] == 7 and w.stats[1]["stateApplied"] == 1)


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
    _selftest_drive(expect)
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
    d.add_argument("--faction", required=True, help="the faction the crime is against, e.g. FalloutNV.esm:0A46E7 (NCRFactionNV)")
    d.add_argument("--npc", help="an NPC for guest 1 to command (the old manual drive); without it Boone is recruited for guest 1 by the script")
    d.add_argument("--victim-net", type=int, default=None, help="the crime's victim net id; without it an NCR trooper is placed and its net id read")
    d.add_argument("--boone", default=BOONE, help="the companion to recruit when --npc is not given")
    d.add_argument("--trooper-base", default=NCR_TROOPER_BASE, help="the NPC base placed as the victim when --victim-net is not given")
    d.add_argument("--no-recruit", action="store_true", help="do not recruit anybody and do not command anybody unless --npc is given")
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
        flags, checks = drive([int(x) for x in args.instances.split(",")], args.faction, args.npc, args.victim_net,
                              boone=args.boone, trooper_base=args.trooper_base, recruit=not args.no_recruit)
        bad = 0
        for rule, ok, detail in checks:
            print(("PASS " if ok else "FAIL ") + rule + ("" if ok else f"  [{detail}]"))
            bad += 0 if ok else 1
        if not flags:
            print("FAIL the setup did not finish, so the crime was not driven")
            return 1
        ok, detail = flags_agree(flags)
        print(("PASS" if ok else "FAIL") + " the engine's faction flags agree on every instance: " + detail)
        return 0 if ok and not bad else 1
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
