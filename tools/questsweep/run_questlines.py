#!/usr/bin/env python3
"""Run the questline playthrough playbooks.

Game-free (the fake co-op world; this is what the tests do):
    python -I tools/questsweep/run_questlines.py --list
    python -I tools/questsweep/run_questlines.py --fake --line all
    python -I tools/questsweep/run_questlines.py --fake --line ncr --players 3 --bug reward_twice      # must FAIL
    python -I tools/questsweep/run_questlines.py --fake --line goodsprings --quests VCG02

In a game (the lead runs this; this tool never launches a game). Start N co-op instances joined as one session on a save made for the
line (the quests of questlines/<line>/playthrough.json are reset first with test.quest.reset when the bridge has it), then:
    python -I tools/questsweep/run_questlines.py --line goodsprings --instances 0,1
    python -I tools/questsweep/run_questlines.py --line ncr --instances 0,1,2 --natural --actions
`--natural` plays the dialogue lines (needs the speaker refs of the plan), `--actions` lets the bots kill / talk / walk / take for the
objectives (they can reach named NPCs: use a disposable save). Stages the lane marked as excluded (they end the game or need a
disposable save) are skipped unless `--include-excluded`. Exit code: 0 all pass, 1 a playbook failed, 2 usage or setup error.
"""
from __future__ import annotations

import argparse
import contextlib
import json
import os
import sys
import time
from typing import Any, Dict, List

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
for p in (os.path.join(REPO, "tools", "bots"), os.path.join(REPO, "tools"), os.path.join(REPO, "tools", "runner")):
    if p not in sys.path:
        sys.path.insert(0, p)

from playbooks import core, fakecoop, questlines  # noqa: E402

FAKE_OPTS = {"fast": True, "anim": False, "ui_s": 0.3, "barrier_s": 0.3, "stage_s": 0.3, "handoff_s": 0.3, "spawn_s": 0.3, "settle_s": 1.0,
             "settle_poll_s": 0.0, "repo_root": REPO}


def log(s: str) -> None:
    print(s, flush=True)


def pick(names: List[str], arg: str) -> List[str]:
    if arg == "all":
        return names
    out = []
    for a in arg.split(","):
        n = a if a.startswith("ql_") else "ql_" + a
        if n not in names:
            raise SystemExit(f"unknown line {a!r}; use --list")
        out.append(n)
    return out


def run_fake(names: List[str], players: int, bugs: set, outroot: str, opts: Dict[str, Any]) -> List[core.RunResult]:
    res = []
    for name in names:
        pb = questlines.make(name)
        world, refs = questlines.fake(name, players or 2, bugs, plan=pb.plan)
        outdir = os.path.join(outroot, name)
        world.flight_dir = os.path.join(outdir, "flight")
        o = dict(FAKE_OPTS)
        o.update(opts)
        res.append(core.run_playbook(pb, fakecoop.make_machines(world), fakecoop.FakeComparer(), outdir, refs, o, log))
    return res


def run_live(names: List[str], a, outroot: str, opts: Dict[str, Any]) -> List[core.RunResult]:
    from nvbridge import NvBridge          # noqa: WPS433
    from bots import Bot                   # noqa: WPS433
    from playbooks.adapter import PlaybookBot
    inst = [int(x) for x in a.instances.split(",")]
    results: List[core.RunResult] = []
    with contextlib.ExitStack() as stack:
        try:
            nvs = [stack.enter_context(NvBridge.connect(instance=i, wait_s=a.wait)) for i in inst]
        except Exception as e:   # noqa: BLE001
            print(f"cannot reach the game instances: {e}", file=sys.stderr)
            raise SystemExit(2)
        comparer = core.LiveComparer(REPO)
        for name in names:
            pb = questlines.make(name)
            n = a.players or len(nvs)
            if n > len(nvs):
                log(f"SKIP {name}: needs {n} instances, have {len(nvs)}")
                continue
            reset = [f"{int(x, 16):08X}" for x in pb.plan.data.get("reset", [])]
            for nv in nvs[:n]:
                try:
                    nv.call("test.quest.reset", {"quests": reset}, timeout_s=60)
                except Exception as e:   # noqa: BLE001
                    log(f"note: test.quest.reset not available ({e}); starting from the current save")
                    break
            machines = [core.Machine(i, "host" if i == 0 else f"client{i}",
                                     PlaybookBot(Bot(nv, "host" if i == 0 else f"client{i}")), inst[i]) for i, nv in enumerate(nvs[:n])]
            results.append(core.run_playbook(pb, machines, comparer, os.path.join(outroot, name), {}, opts, log))
            if not results[-1].ok and not a.keep_going:
                break
    return results


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--line", default="", help="line slug(s), comma list, or 'all'")
    ap.add_argument("--fake", action="store_true")
    ap.add_argument("--players", type=int, default=0)
    ap.add_argument("--bug", action="append", default=[], help="(fake) switch on a deliberate bug; the run must then fail")
    ap.add_argument("--quests", default="", help="only these quest editor ids")
    ap.add_argument("--instances", default="0,1")
    ap.add_argument("--wait", type=float, default=30.0)
    ap.add_argument("--natural", action="store_true")
    ap.add_argument("--actions", action="store_true", help="(live) let the bots act on the objectives; fake runs always do")
    ap.add_argument("--no-actions", action="store_true")
    ap.add_argument("--include-excluded", action="store_true")
    ap.add_argument("--raw-setup", action="store_true", help="(live) also run the lane's non-startquest/setstage setup console lines")
    ap.add_argument("--keep-going", action="store_true")
    ap.add_argument("--outdir", default="")
    a = ap.parse_args(argv)

    names = questlines.names()
    if a.list:
        for n in names:
            pb = questlines.make(n)
            s = pb.plan.stats()
            print(f"{n:18} {s.get('quests', 0):3} quests {s.get('stages', 0):4} stages  {pb.plan.data.get('category', ''):10} {pb.plan.data.get('title', '')}")
        print("fake bugs: " + ", ".join(sorted(questlines.QL_BUGS)))
        return 0
    if not a.line:
        ap.error("--line is required (or --list)")
    sel = pick(names, a.line)
    outroot = a.outdir or os.path.join(REPO, "out", "questsweep", time.strftime("%Y%m%dT%H%M%S"))
    os.makedirs(outroot, exist_ok=True)
    opts: Dict[str, Any] = {"repo_root": REPO, "ql_quests": [x for x in a.quests.split(",") if x]}
    if a.natural:
        opts["ql_natural"] = True
    if a.no_actions:
        opts["ql_actions"] = False
    elif a.actions:
        opts["ql_actions"] = True
    elif not a.fake:
        opts["ql_actions"] = False
    if a.include_excluded:
        opts["ql_include_excluded"] = True
    if a.raw_setup:
        opts["ql_raw_setup"] = True
    results = run_fake(sel, a.players, set(a.bug), outroot, opts) if a.fake else run_live(sel, a, outroot, opts)
    print()
    w = max([len(r.playbook) for r in results] + [8])
    for r in results:
        print(f"{'PASS' if r.ok else 'FAIL'}  {r.playbook:{w}}  {len(r.steps)} steps  {r.reason}")
    ok = bool(results) and all(r.ok for r in results)
    with open(os.path.join(outroot, "summary.json"), "w", encoding="utf-8") as f:
        json.dump({"mode": "fake" if a.fake else "live", "bugs": a.bug, "ok": ok, "results": [r.to_json() for r in results]}, f, indent=1, default=str)
    print(f"summary: {os.path.join(outroot, 'summary.json')}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
