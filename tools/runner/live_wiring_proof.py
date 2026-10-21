#!/usr/bin/env python3
"""In-game proof of the live-layer wiring (dll-wiring). WRITTEN, NEVER RUN against a real game: it needs two co-op game instances.

    python -I tools/runner/live_wiring_proof.py --instances 0,1 --expect actors,avatars            # host on pipe 0, client on pipe 1
    python -I tools/runner/live_wiring_proof.py --instances 0 --expect none                        # one instance, everything off (the default)
    python -I tools/runner/live_wiring_proof.py --instances 0,1 --expect actors,avatars --load MySave   # + the client loads a save

Provision the instances first (the lead starts them; this tool never launches a game and never takes the game slot):
    python -I tools/runner/run.py provision --n 2 --ini coop.enable=1 --ini live.actors=1 --ini live.avatars=1 [--ini live.combat=1 --ini live.spawns=1] ...

What it checks, per instance, through the bridge method `coop.live` (and the lane's own status method):
  * the switches the DLL parsed equal --expect (nothing is on that was not asked for), and no warning line (a dependency that was forced off);
  * the hub ticks (its frame counter advances) and, with something expected on, both machines are in a session with the right role;
  * every expected lane is an enabled service with its session part open, every other lane is disabled;
  * avatars: each machine lists the other player in its roster, has a puppet ref for it (not 0), snapshots were accepted and none were forged;
  * actors / combat / spawns: the lane's own status method answers and says it is running;
  * --load: after the CLIENT loads a save, the hub counted the load and the avatar puppets are back.
Exit code: 0 pass, 1 a check failed, 2 usage / cannot reach the game. `--selftest` runs the checks against a scripted fake game plus injected
defects (every defect must be caught); that is what the gate runs.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))

LANES = ("actors", "avatars", "combat", "spawns")
LANE_STATUS_METHOD = {"actors": "actors.port", "combat": "combat.status", "spawns": "coop.spawn"}

Check = Tuple[str, bool, str]


def describe(expected: Set[str]) -> str:
    return " ".join(f"{lane}={1 if lane in expected else 0}" for lane in LANES)


def service(hub: Dict[str, Any], name: str) -> Optional[Dict[str, Any]]:
    for s in hub.get("services", []):
        if s.get("name") == name:
            return s
    return None


def check_instance(label: str, expected: Set[str], first: Dict[str, Any], second: Dict[str, Any], role: str, lane_status: Dict[str, Any]) -> List[Check]:
    """One instance: `first` and `second` are two coop.live answers about a second apart; lane_status maps lane -> that lane's own status answer."""
    out: List[Check] = []

    def add(name: str, ok: bool, detail: str = "") -> None:
        out.append((f"{label}: {name}", bool(ok), detail))

    add("switches parsed as asked", first.get("switches") == describe(expected), f"got {first.get('switches')!r} want {describe(expected)!r}")
    add("no switch was forced off", not first.get("warnings"), str(first.get("warnings")))
    add("the live layer loaded", bool(first.get("loaded")) and bool(first.get("configured")))
    hub1, hub2 = first.get("hub", {}), second.get("hub", {})
    add("the hub ticks", hub2.get("frames", 0) > hub1.get("frames", 0), f"frames {hub1.get('frames')} -> {hub2.get('frames')}")
    if expected:
        add("in a session", bool(hub2.get("inSession")), f"role {hub2.get('role')}")
        add("role", hub2.get("role") == role, f"got {hub2.get('role')!r} want {role!r}")
        add("a session began", hub2.get("sessions", 0) >= 1)
    for lane in LANES:
        svc = service(hub2, lane)
        on = lane in expected
        if on:
            add(f"{lane} service enabled", bool(svc and svc.get("enabled")), str(svc))
            add(f"{lane} session part open", bool(svc and svc.get("sessionOpen")), str(svc))
        else:
            add(f"{lane} service off", bool(svc is None or not svc.get("enabled")), str(svc))
    if "actors" in expected:
        st = lane_status.get("actors", {})
        add("actors.port says it has a session", bool(st.get("session")), str(st)[:160])
    if "combat" in expected:
        st = lane_status.get("combat", {})
        add("combat.status says it runs", bool(st.get("running")), str(st)[:160])
    if "spawns" in expected:
        st = lane_status.get("spawns", {})
        add("coop.spawn answers", bool(st) and not st.get("error"), str(st)[:160])
    return out


def check_avatars(host: Dict[str, Any], client: Dict[str, Any]) -> List[Check]:
    out: List[Check] = []

    def add(name: str, ok: bool, detail: str = "") -> None:
        out.append((f"avatars: {name}", bool(ok), detail))

    add("the avatar module runs on both machines", bool(host.get("avatarModule", {}).get("running")) and bool(client.get("avatarModule", {}).get("running")),
        f"{host.get('avatarModule')} / {client.get('avatarModule')}")
    ha, ca = host.get("avatars", {}), client.get("avatars", {})
    cslot = ca.get("self")
    add("client has a slot", isinstance(cslot, int) and cslot not in (0, 255), f"self {cslot}")
    hr = {r.get("slot"): r for r in ha.get("roster", [])}
    cr = {r.get("slot"): r for r in ca.get("roster", [])}
    add("host roster lists the client", cslot in hr, f"roster {sorted(hr)}")
    add("client roster lists the host", 0 in cr, f"roster {sorted(cr)}")
    add("host has a puppet ref for the client", bool(hr.get(cslot, {}).get("ref")), str(hr.get(cslot)))
    add("client has a puppet ref for the host", bool(cr.get(0, {}).get("ref")), str(cr.get(0)))
    add("host accepted snapshots", ha.get("accepted", 0) > 0, f"accepted {ha.get('accepted')}")
    add("client accepted snapshots", ca.get("accepted", 0) > 0, f"accepted {ca.get('accepted')}")
    add("nothing forged", ha.get("forged", 0) == 0 and ca.get("forged", 0) == 0, f"forged {ha.get('forged')}/{ca.get('forged')}")
    add("local snapshots are published", ha.get("localSnapshots", 0) > 0 and ca.get("localSnapshots", 0) > 0)
    return out


def check_load(before: Dict[str, Any], after: Dict[str, Any], avatars_expected: bool) -> List[Check]:
    out: List[Check] = []
    out.append(("load: the hub counted the load", after.get("hub", {}).get("loads", 0) == before.get("hub", {}).get("loads", 0) + 1,
                f"loads {before.get('hub', {}).get('loads')} -> {after.get('hub', {}).get('loads')}"))
    out.append(("load: still in the session", bool(after.get("hub", {}).get("inSession")), ""))
    if avatars_expected:
        roster = after.get("avatars", {}).get("roster", [])
        out.append(("load: the puppets are back", bool(roster) and all(r.get("ref") for r in roster), str(roster)))
    return out


# ---------------------------------------------------------------------------------------------------------------------------------------
# driver
# ---------------------------------------------------------------------------------------------------------------------------------------
Call = Callable[[str, Optional[dict]], Any]


def run_checks(calls: List[Call], expected: Set[str], sleep: Callable[[float], None] = time.sleep, wait_s: float = 30.0, poll_s: float = 1.0,
               load: Optional[Callable[[], None]] = None, now: Callable[[], float] = time.monotonic) -> List[Check]:
    """calls[0] = host, calls[1] = client (optional). Each call(method, params) returns the decoded bridge result."""
    roles = ["host", "client"]
    results: List[Check] = []
    firsts, seconds, lane_status = [], [], []
    for i, call in enumerate(calls):
        firsts.append(call("coop.live", None))
    sleep(1.2)
    for i, call in enumerate(calls):
        seconds.append(call("coop.live", None))
        ls: Dict[str, Any] = {}
        for lane, method in LANE_STATUS_METHOD.items():
            if lane in expected:
                try:
                    ls[lane] = call(method, None)
                except Exception as e:  # noqa: BLE001
                    ls[lane] = {"error": str(e)}
        lane_status.append(ls)
    # avatars need time: poll until both rosters have refs or the wait is over
    if "avatars" in expected and len(calls) == 2:
        deadline = now() + wait_s
        while True:
            hs, cs = calls[0]("coop.live", None), calls[1]("coop.live", None)
            ok = all(r.get("ref") for r in hs.get("avatars", {}).get("roster", [])) and bool(hs.get("avatars", {}).get("roster")) and \
                all(r.get("ref") for r in cs.get("avatars", {}).get("roster", [])) and bool(cs.get("avatars", {}).get("roster"))
            if ok or now() >= deadline:
                seconds = [hs, cs]
                break
            sleep(poll_s)
    for i, call in enumerate(calls):
        results += check_instance(roles[i] if len(calls) > 1 else "host", expected, firsts[i], seconds[i], roles[i], lane_status[i])
    if "avatars" in expected and len(calls) == 2:
        results += check_avatars(seconds[0], seconds[1])
    if load and len(calls) == 2:
        before = calls[1]("coop.live", None)
        load()
        deadline = now() + wait_s
        after = before
        while now() < deadline:
            sleep(poll_s)
            after = calls[1]("coop.live", None)
            if after.get("hub", {}).get("loads", 0) > before.get("hub", {}).get("loads", 0) and after.get("hub", {}).get("inSession"):
                if "avatars" not in expected or all(r.get("ref") for r in after.get("avatars", {}).get("roster", [])):
                    break
        results += check_load(before, after, "avatars" in expected)
    return results


def parse_expect(text: str) -> Set[str]:
    t = {x.strip() for x in text.split(",") if x.strip()}
    if t == {"none"}:
        return set()
    bad = t - set(LANES)
    if bad:
        raise ValueError(f"unknown lane(s) {sorted(bad)}; choose from {', '.join(LANES)} or 'none'")
    return t


def report(results: List[Check]) -> int:
    bad = 0
    for name, ok, detail in results:
        print(f"{'PASS' if ok else 'FAIL'}  {name}" + ("" if ok or not detail else f"   [{detail}]"))
        bad += 0 if ok else 1
    print(f"{len(results) - bad} passed, {bad} failed")
    return 1 if bad else 0


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--instances", default="0,1", help="bridge instance ids: host first, then the client (one id = a single machine)")
    ap.add_argument("--expect", default="none", help="comma list of lanes that must be ON: actors,avatars,combat,spawns (or none)")
    ap.add_argument("--wait", type=float, default=30.0, help="seconds to wait for puppets / the load")
    ap.add_argument("--load", default="", help="save name (no extension) the client loads at the end")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args(argv)
    if a.selftest:
        sys.path.insert(0, HERE)
        import live_wiring_proof_selftest  # noqa: E402
        return live_wiring_proof_selftest.main()
    try:
        expected = parse_expect(a.expect)
    except ValueError as e:
        print(e, file=sys.stderr)
        return 2
    ids = [int(x) for x in a.instances.split(",") if x != ""]
    if not 1 <= len(ids) <= 2:
        print("--instances needs one or two ids", file=sys.stderr)
        return 2
    sys.path.insert(0, HERE)
    from nvbridge import NvBridge  # noqa: E402
    try:
        bridges = [NvBridge.connect(instance=i, wait_s=a.wait) for i in ids]
    except Exception as e:  # noqa: BLE001
        print(f"cannot reach the game instances: {e}", file=sys.stderr)
        return 2
    try:
        calls = [(lambda b: (lambda m, p=None: b.call(m, p or {})))(b) for b in bridges]
        load = (lambda: bridges[1].load_save(a.load)) if a.load and len(bridges) == 2 else None
        return report(run_checks(calls, expected, wait_s=a.wait, load=load))
    finally:
        for b in bridges:
            b.close()


if __name__ == "__main__":
    sys.exit(main())
