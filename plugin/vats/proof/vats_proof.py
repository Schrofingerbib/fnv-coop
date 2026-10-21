"""In-game proof helper for plugin/vats (docs/tools/vats.md). NEVER run by the agents: the helper runs it on his PC against two or more live game
instances (host = instance 0) that are in a co-op session with `nvcoop.ini [vats] enabled=1` while somebody uses VATS.

    python -I plugin/vats/proof/vats_proof.py snapshot --out out/vats/before.json                    # before the VATS playbook
    ... run it: python -I tools/bots/run_coop.py vats_gun   (and vats_melee / vats_explosive / vats_energy) ...
    python -I plugin/vats/proof/vats_proof.py snapshot --out out/vats/after.json
    python -I plugin/vats/proof/vats_proof.py check out/vats/before.json out/vats/after.json [--expect-slowmo] [--expect-autofire] [--expect-hooks-fired]
    python -I plugin/vats/proof/vats_proof.py --selftest                                              # game-free: the checker itself, with twins

`snapshot` calls the bridge method vats.status on every instance. `check` prints one PASS/FAIL line per rule and exits 1 on any FAIL.
The first rules say whether the hooks went in at all: with `gate`-style honesty, a 0 there means the pins did not match this exe and nothing else is worth reading.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Tuple

EXPECTED_SITES = {"time_scale": 11, "player_mult": 5, "target_mult": 9, "pause_main": 2}
PINS = ("setScale", "getMult", "menuMode", "menuModeOf", "accept", "unclick", "limb", "handleClick")


def flatten(st: Dict[str, Any]) -> Dict[str, Any]:
    """vats.status is {enabled, inited, running, pins{}, thunks{}, engineCalls{}, hooks[{name,sites,total,pinsOk}], sync{role, settings, partySlow, ledger, stats{}}}."""
    flat: Dict[str, Any] = {"enabled": bool(st.get("enabled")), "inited": bool(st.get("inited")), "running": bool(st.get("running"))}
    flat["pins"] = st.get("pins", {})
    for k, v in st.get("thunks", {}).items():
        flat["thunk_" + k] = v
    for k, v in st.get("engineCalls", {}).items():
        flat["call_" + k] = v
    flat["hooks"] = {h.get("name"): h.get("sites", 0) for h in st.get("hooks", [])}
    flat["multSettingsFallback"] = bool(st.get("multSettingsFallback"))
    sync = st.get("sync") or {}
    flat["role"] = sync.get("role")
    flat["settings"] = sync.get("settings", {})
    flat["partySlowStarted"] = int(sync.get("partySlow", {}).get("started", 0))
    flat["partySlowActive"] = bool(sync.get("partySlow", {}).get("active"))
    flat["appliedScale"] = float(sync.get("partySlow", {}).get("appliedScale", 1.0))
    flat["ledger"] = sync.get("ledger", {})
    flat.update(sync.get("stats", {}))
    return flat


def delta(before: Dict[str, Any], after: Dict[str, Any], key: str) -> int:
    return int(after.get(key, 0) or 0) - int(before.get(key, 0) or 0)


def check(before: List[Dict[str, Any]], after: List[Dict[str, Any]], *, slowmo: bool = False, autofire: bool = False, hooks_fired: bool = False,
          mutate: int = 0) -> List[Tuple[str, bool, str]]:
    """before/after: one flattened vats.status per machine (same order). Returns [(rule, ok, detail)]."""
    out: List[Tuple[str, bool, str]] = []
    n = len(after)

    def d(i: int, k: str) -> int:
        return delta(before[i], after[i], k)

    def total(k: str) -> int:
        return sum(d(i, k) for i in range(n))

    out.append(("every machine has the VATS module on and in a session", all(a.get("enabled") and a.get("running") for a in after), str([(a.get("enabled"), a.get("running")) for a in after])))
    out.append(("every engine pin matched this exe", all(all(a.get("pins", {}).get(p) for p in PINS) for a in after) or mutate == 1, str([a.get("pins") for a in after])))
    sites_ok = all(all(a.get("hooks", {}).get(h, 0) == c for h, c in EXPECTED_SITES.items()) for a in after)
    out.append(("every call-site group is fully redirected (11 / 5 / 9 / 2)", sites_ok or mutate == 2, str([a.get("hooks") for a in after])))
    out.append(("the multiplier hooks went in (no settings fallback)", not any(a.get("multSettingsFallback") for a in after) or mutate == 3, str([a.get("multSettingsFallback") for a in after])))
    out.append(("no thunk raised an error and none ran off the main thread", all(int(a.get("thunk_errors", 0)) == 0 and int(a.get("thunk_offThread", 0)) == 0 for a in after) or mutate == 4,
                str([(a.get("thunk_errors"), a.get("thunk_offThread")) for a in after])))
    out.append(("no engine call was refused or faulted", all(int(a.get("call_failed", 0)) == 0 and int(a.get("call_pinRefusals", 0)) == 0 for a in after) or mutate == 5,
                str([(a.get("call_failed"), a.get("call_pinRefusals")) for a in after])))
    out.append(("every machine is in the same real-time mode", len({bool(a.get("settings", {}).get("realtime")) for a in after}) == 1 or mutate == 6, str([a.get("settings", {}).get("realtime") for a in after])))
    out.append(("clients run the host's settings", len({json.dumps(a.get("settings", {}), sort_keys=True) for a in after}) == 1 or mutate == 7, "settings differ" if mutate != 7 else ""))
    if hooks_fired:
        out.append(("the VATS time-scale hook fired where VATS was used", total("thunk_scale") > 0 or mutate == 8, f"scale thunk calls={total('thunk_scale')}"))
        out.append(("the engine's freeze / slow-down requests were replaced", total("thunk_scaleChanged") > 0 or mutate == 9, f"changed={total('thunk_scaleChanged')}"))
        out.append(("the menu pause was lifted at least once", total("thunk_pauseChanged") > 0 or mutate == 10, f"changed={total('thunk_pauseChanged')}"))
    shooters = [i for i in range(n) if d(i, "actionsQueued") > 0]
    out.append(("somebody queued a VATS action", bool(shooters), f"actionsQueued={[d(i, 'actionsQueued') for i in range(n)]}"))
    queued = total("actionsQueued")
    out.append(("every queued action was seen by every other machine (relay)",
                all(d(i, "actionsReceived") == queued - d(i, "actionsQueued") for i in range(n)) or mutate == 11,
                str([(d(i, "actionsReceived"), queued - d(i, "actionsQueued")) for i in range(n)])))
    out.append(("every machine's VATS mode changes were announced", total("phasesSent") >= len(shooters) * 2 or mutate == 12, f"phasesSent={total('phasesSent')}"))
    if autofire:
        out.append(("every queued action was fired once by the auto fire", sum(d(i, "autoFires") for i in shooters) >= len(shooters) or mutate == 13,
                    f"autoFires={[d(i, 'autoFires') for i in range(n)]}"))
        out.append(("an action is never fired more often than it was queued", all(d(i, "autoFires") <= d(i, "actionsQueued") for i in range(n)) or mutate == 14,
                    str([(d(i, "autoFires"), d(i, "actionsQueued")) for i in range(n)])))
    if slowmo:
        host = next((i for i in range(n) if after[i].get("role") == "host"), 0)
        out.append(("the host started at least one party slow-mo window", d(host, "slowMoSent") > 0 and int(after[host].get("partySlowStarted", 0)) > int(before[host].get("partySlowStarted", 0)) or mutate == 15,
                    f"host slowMoSent={d(host, 'slowMoSent')}"))
        out.append(("every client applied the grant", all(d(i, "slowMoApplied") > 0 for i in range(n) if i != host) or mutate == 16, str([d(i, "slowMoApplied") for i in range(n)])))
    out.append(("no machine is still slowed: every world scale is back to 1", all(abs(float(a.get("appliedScale", 1.0)) - 1.0) < 1e-6 and not a.get("partySlowActive") for a in after) or mutate == 17,
                str([(a.get("appliedScale"), a.get("partySlowActive")) for a in after])))
    out.append(("no scale change failed", all(d(i, "scaleFailed") == 0 for i in range(n)) or mutate == 18, str([d(i, "scaleFailed") for i in range(n)])))
    out.append(("nothing was spoofed, rejected or undecodable in a clean run", all(d(i, "spoofed") == 0 and d(i, "undecodable") == 0 and d(i, "rejected") == 0 for i in range(n)) or mutate == 19,
                str([(d(i, "spoofed"), d(i, "undecodable"), d(i, "rejected")) for i in range(n)])))
    out.append(("AP was refunded at most once per remote VATS kill", total("grsRefunds") <= total("vatsKillsRemote") or mutate == 20, f"refunds={total('grsRefunds')} remoteKills={total('vatsKillsRemote')}"))
    return out


def snapshot(instances: List[int]) -> List[Dict[str, Any]]:
    sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "tools" / "runner"))
    from nvbridge import NvBridge  # noqa: E402

    res = []
    for inst in instances:
        with NvBridge.connect(instance=inst, wait_s=10) as nv:
            res.append(flatten(nv.call("vats.status")))
    return res


# ---- self test (game-free) -----------------------------------------------------------------------------------------------------------


def _machine(role: str, **kw: Any) -> Dict[str, Any]:
    base = {"enabled": True, "running": True, "pins": {p: True for p in PINS}, "hooks": dict(EXPECTED_SITES), "multSettingsFallback": False,
            "thunk_errors": 0, "thunk_offThread": 0, "call_failed": 0, "call_pinRefusals": 0, "role": role, "settings": {"realtime": True, "partySlowMo": True},
            "partySlowStarted": 0, "partySlowActive": False, "appliedScale": 1.0, "thunk_scale": 0, "thunk_scaleChanged": 0, "thunk_pauseChanged": 0,
            "actionsQueued": 0, "actionsReceived": 0, "phasesSent": 0, "autoFires": 0, "slowMoSent": 0, "slowMoApplied": 0, "scaleFailed": 0,
            "spoofed": 0, "undecodable": 0, "rejected": 0, "grsRefunds": 0, "vatsKillsRemote": 0}
    base.update(kw)
    return base


def _good() -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    before = [_machine("host"), _machine("client")]
    after = [_machine("host", actionsQueued=2, actionsReceived=0, phasesSent=4, autoFires=2, slowMoSent=2, partySlowStarted=1, thunk_scale=5, thunk_scaleChanged=4, thunk_pauseChanged=3,
                      grsRefunds=1, vatsKillsRemote=1),
             _machine("client", actionsQueued=0, actionsReceived=2, phasesSent=0, slowMoApplied=2, thunk_scale=3, thunk_scaleChanged=2, thunk_pauseChanged=1)]
    return before, after


def selftest() -> int:
    failures = 0

    def expect(name: str, cond: bool) -> None:
        nonlocal failures
        if not cond:
            failures += 1
            print(f"  FAIL {name}")

    flags = dict(slowmo=True, autofire=True, hooks_fired=True)
    b, a = _good()
    expect("a healthy two-machine VATS run passes every rule", all(ok for _r, ok, _d in check(b, a, **flags)))
    bad_cases = {
        1: lambda b, a: a[0]["pins"].update(accept=False),
        2: lambda b, a: a[1]["hooks"].update(time_scale=9),
        3: lambda b, a: a[0].update(multSettingsFallback=True),
        4: lambda b, a: a[1].update(thunk_errors=1),
        5: lambda b, a: a[0].update(call_failed=2),
        6: lambda b, a: a[1]["settings"].update(realtime=False),
        7: lambda b, a: a[1]["settings"].update(partySlowMo=False),
        8: lambda b, a: [a[i].update(thunk_scale=0) for i in range(2)],
        9: lambda b, a: [a[i].update(thunk_scaleChanged=0) for i in range(2)],
        10: lambda b, a: [a[i].update(thunk_pauseChanged=0) for i in range(2)],
        11: lambda b, a: a[1].update(actionsReceived=1),
        12: lambda b, a: a[0].update(phasesSent=1),
        13: lambda b, a: a[0].update(autoFires=0),
        14: lambda b, a: a[0].update(autoFires=3),
        15: lambda b, a: a[0].update(slowMoSent=0),
        16: lambda b, a: a[1].update(slowMoApplied=0),
        17: lambda b, a: a[1].update(appliedScale=0.35),
        18: lambda b, a: a[0].update(scaleFailed=2),
        19: lambda b, a: a[1].update(spoofed=1),
        20: lambda b, a: a[0].update(grsRefunds=3),
    }
    for n, brk in bad_cases.items():
        b, a = _good()
        brk(b, a)
        res = check(b, a, **flags)
        expect(f"twin {n}: the broken status is caught", not all(ok for _r, ok, _d in res))
        expect(f"twin {n}: the mutated checker lets it through", all(ok for _r, ok, _d in check(b, a, mutate=n, **flags)) or n in (6, 13, 14))   # 6 also trips rule 7; 13 and 14 trip each other
    st = {"enabled": True, "inited": True, "running": True, "pins": {"setScale": True}, "thunks": {"scale": 3, "errors": 0}, "engineCalls": {"failed": 1},
          "hooks": [{"name": "time_scale", "sites": 11, "total": 11, "pinsOk": True}], "multSettingsFallback": False,
          "sync": {"role": "client", "settings": {"realtime": True}, "partySlow": {"started": 1, "active": False, "appliedScale": 1.0}, "stats": {"actionsQueued": 4}}}
    f = flatten(st)
    expect("flatten keeps the pins, thunk and call counters, the hooks and the sync stats",
           f["thunk_scale"] == 3 and f["call_failed"] == 1 and f["hooks"]["time_scale"] == 11 and f["actionsQueued"] == 4 and f["role"] == "client" and f["partySlowStarted"] == 1)
    b, a = _good()
    for m in a:
        m["actionsQueued"] = 0
        m["actionsReceived"] = 0
    expect("a run in which nobody used VATS is not a pass", not all(ok for _r, ok, _d in check(b, a)))
    b, a = _good()
    expect("without the optional expectations only the base rules apply", all(ok for _r, ok, _d in check(b, a)))
    if failures:
        print(f"vats_proof selftest: {failures} failed")
        return 1
    print("vats_proof selftest: ALL OK")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--selftest", action="store_true")
    sub = ap.add_subparsers(dest="cmd")
    s = sub.add_parser("snapshot")
    s.add_argument("--instances", type=int, nargs="+", default=[0, 1])
    s.add_argument("--out", required=True)
    c = sub.add_parser("check")
    c.add_argument("before")
    c.add_argument("after")
    c.add_argument("--expect-slowmo", action="store_true")
    c.add_argument("--expect-autofire", action="store_true")
    c.add_argument("--expect-hooks-fired", action="store_true")
    args = ap.parse_args()
    if args.selftest:
        return selftest()
    if args.cmd == "snapshot":
        data = snapshot(args.instances)
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(json.dumps(data, indent=1), encoding="utf-8")
        print(f"wrote {args.out}")
        return 0
    if args.cmd == "check":
        before = json.loads(Path(args.before).read_text(encoding="utf-8"))
        after = json.loads(Path(args.after).read_text(encoding="utf-8"))
        res = check(before, after, slowmo=args.expect_slowmo, autofire=args.expect_autofire, hooks_fired=args.expect_hooks_fired)
        for rule, ok, detail in res:
            print(f"{'PASS' if ok else 'FAIL'}  {rule}   [{detail}]")
        return 0 if all(ok for _r, ok, _d in res) else 1
    ap.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
