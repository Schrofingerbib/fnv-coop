"""Self-test of live_wiring_proof.py: a scripted fake game (a host and a client that answer coop.live and the lane status methods) plus
injected defects. The proof must pass on the healthy fake for every combination of lanes and must flag every defect. Never touches a game.

    python -I tools/runner/live_wiring_proof.py --selftest
"""
from __future__ import annotations

import copy
import sys
from typing import Any, Dict, List, Set

import live_wiring_proof as P

ALL = {"actors", "avatars", "combat", "spawns"}


class Fake:
    """One machine. `world` is shared by the host and the client so their answers agree."""

    def __init__(self, role: str, slot: int, expected: Set[str], defects: Set[str]):
        self.role, self.slot, self.expected, self.defects = role, slot, expected, defects
        self.frames = 100
        self.loads = 0
        self.refsBack = True

    def hub(self) -> Dict[str, Any]:
        self.frames += 0 if "hub_frozen" in self.defects else 31
        services = []
        for lane in P.LANES:
            on = lane in self.expected
            if "disabled_lane_enabled" in self.defects and not on and lane == "combat":
                on = True
            sess = on and not ("service_not_open" in self.defects and lane == "actors")
            services.append({"name": lane, "enabled": on, "sessionOpen": sess})
        in_session = bool(self.expected) and "no_session" not in self.defects
        role = ("client" if self.role == "host" else "host") if "wrong_role" in self.defects and self.role == "client" else self.role
        return {"inSession": in_session, "role": role if in_session else "none", "services": services, "frames": self.frames,
                "sessions": 1 if in_session else 0, "joins": 1, "leaves": 0, "loads": self.loads}

    def avatars(self) -> Dict[str, Any]:
        other = 1 if self.role == "host" else 0
        ref = 0 if "avatars_ref_zero" in self.defects else 0x00FF0123 + other
        if self.role == "client" and "puppets_not_back_after_load" in self.defects and self.loads:
            ref = 0
        roster = [] if "avatars_no_roster" in self.defects and self.role == "host" else [{"slot": other, "ref": ref, "look": True}]
        return {"inSession": True, "self": self.slot, "epoch": 5, "roster": roster, "sent": 300, "received": 300,
                "accepted": 0 if "avatars_not_accepted" in self.defects else 250, "rejected": 0, "forged": 3 if "avatars_forged" in self.defects else 0,
                "noRoster": 0, "relayed": 0, "looksApplied": 1, "localSnapshots": 280}

    def call(self, method: str, params=None) -> Any:
        if method == "coop.live":
            switches = set(self.expected)
            if "switch_on_unrequested" in self.defects:
                switches.add("spawns")
            d: Dict[str, Any] = {"switches": P.describe(switches), "warnings": "combat=1 ignored: it needs actors=1" if "warning" in self.defects else "",
                                 "configured": True, "loaded": "not_loaded" not in self.defects, "actorsConfigNotes": "", "hub": self.hub()}
            if "avatars" in self.expected:
                d["avatarModule"] = {"enabled": True, "running": "avatar_module_off" not in self.defects}
                d["avatars"] = self.avatars()
            return d
        if method == "actors.port":
            return {"enabled": True, "session": "actors_no_session" not in self.defects}
        if method == "combat.status":
            return {"enabled": True, "running": "combat_not_running" not in self.defects}
        if method == "coop.spawn":
            return {"error": "x"} if "spawn_error" in self.defects else {"sites": 0}
        raise RuntimeError(f"unknown method {method}")


def run(expected: Set[str], defects: Set[str], load: bool = True, machines: int = 2) -> List[P.Check]:
    host = Fake("host", 0, expected, defects)
    client = Fake("client", 1, expected, defects)
    calls = [host.call, client.call][:machines]
    clock = [0.0]

    def now() -> float:
        clock[0] += 0.5
        return clock[0]

    def do_load() -> None:
        client.loads += 1

    return P.run_checks(calls, expected, sleep=lambda s: None, wait_s=6.0, poll_s=1.0, load=do_load if load and machines == 2 else None, now=now)


def failed(results: List[P.Check]) -> List[str]:
    return [n for n, ok, _ in results if not ok]


DEFECTS = {
    # defect -> (expected lanes it needs, extra args)
    "hub_frozen": ALL, "no_session": ALL, "wrong_role": ALL, "service_not_open": ALL, "warning": ALL, "not_loaded": ALL,
    "avatars_no_roster": ALL, "avatars_ref_zero": ALL, "avatars_forged": ALL, "avatars_not_accepted": ALL, "avatar_module_off": ALL,
    "puppets_not_back_after_load": ALL, "actors_no_session": ALL, "combat_not_running": ALL, "spawn_error": ALL,
    "switch_on_unrequested": {"actors"}, "disabled_lane_enabled": {"actors"},
}


def main() -> int:
    bad = 0

    def expect(cond: bool, what: str) -> None:
        nonlocal bad
        print(f"  {'ok  ' if cond else 'FAIL'} {what}")
        if not cond:
            bad += 1

    print("healthy fake game")
    for exp in [set(), {"actors"}, {"avatars"}, {"actors", "avatars"}, {"actors", "combat"}, ALL]:
        r = run(exp, set(), load=bool(exp))
        expect(not failed(r) and len(r) > 5, f"expect {P.describe(exp)}: all {len(r)} checks pass" + ("" if not failed(r) else f"  FAILED {failed(r)}"))
    r = run({"actors"}, set(), load=False, machines=1)
    expect(not failed(r), "a single machine with actors on passes the per-machine checks")
    print("every injected defect is caught")
    for defect, exp in DEFECTS.items():
        r = run(set(exp), {defect}, load=True)
        expect(bool(failed(r)), f"{defect}: flagged ({len(failed(r))} failed checks)")
    print("parse / report")
    expect(P.parse_expect("none") == set() and P.parse_expect("actors, avatars") == {"actors", "avatars"}, "parse_expect")
    try:
        P.parse_expect("actors,wizards")
        expect(False, "unknown lane rejected")
    except ValueError:
        expect(True, "unknown lane rejected")
    expect(P.report([("a", True, ""), ("b", True, "")]) == 0 and P.report([("a", True, ""), ("b", False, "why")]) == 1, "report exit codes")
    print("live_wiring_proof selftest: " + ("ALL OK" if not bad else f"{bad} FAILED"))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
