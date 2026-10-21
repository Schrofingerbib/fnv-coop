#!/usr/bin/env python3
"""Run the four two-player VATS playbooks (tools/bots/playbooks/pb_vats.py, docs/tools/vats.md): gun, melee, explosive, energy.

Game-free (the fake VATS world, what the unit tests use):
    python -I tools/bots/run_vats.py --fake
    python -I tools/bots/run_vats.py --fake --playbook vats_melee --bug vats_no_lunge          # must FAIL (exit 1)
    python -I tools/bots/run_vats.py --list

In a game (the helper runs this; this tool never launches a game or takes the game slot). Start two co-op instances joined as one session
(host = pipe 0, client = pipe 1) with `[dev] bots=1` and `[vats] enabled=1` in nvcoop.ini (and [live] actors=1 combat=1 so hits are synced), then:
    python -I tools/bots/run_vats.py --instances 0,1 --weapon gun=<item> --weapon melee=<item> --weapon explosive=<item> --weapon energy=<item>
    python -I tools/bots/run_vats.py --instances 0,1 --playbook vats_gun --weapon gun=<item> --slowmo
`<item>` is an editor id or `Plugin.esm:HEX` of a weapon of that class that the player may use. This tool deliberately has NO default: the editor ids
were not verified, and a class test with the wrong weapon proves nothing. Every playbook starts from the scenario vats_hit (a Powder Ganger).
Afterwards: python -I plugin/vats/proof/vats_proof.py snapshot / check (docs/tools/vats.md "In-game proof").
Exit code: 0 all pass, 1 a playbook failed, 2 usage or setup error. Nothing here has run in a game.
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
for p in (HERE, os.path.join(REPO, "tools"), os.path.join(REPO, "tools", "runner")):
    if p not in sys.path:
        sys.path.insert(0, p)

from playbooks import core, fakecoop, pb_vats  # noqa: E402


def log(s: str) -> None:
    print(s, flush=True)


def run_fake(names: List[str], players: int, bugs: set, outroot: str, opts: dict) -> List[core.RunResult]:
    results = []
    for name in names:
        cls = pb_vats.make_vats(name).weapon_class
        world, refs = pb_vats.fake_vats_class(cls)(players or 2, bugs)
        outdir = os.path.join(outroot, name)
        world.flight_dir = os.path.join(outdir, "flight")
        o = {"fast": True, "ui_s": 0.3, "barrier_s": 0.3, "stage_s": 0.3, "handoff_s": 0.3, "spawn_s": 0.3, "settle_s": 1.0, "settle_poll_s": 0.0, "repo_root": REPO,
             "config_s": 5.0}
        o.update(opts)
        results.append(core.run_playbook(pb_vats.make_vats(name), fakecoop.make_machines(world), fakecoop.FakeComparer(), outdir, refs, o, log))
    return results


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--playbook", default="all", help="vats_gun | vats_melee | vats_explosive | vats_energy | comma list | all")
    ap.add_argument("--fake", action="store_true")
    ap.add_argument("--bug", action="append", default=[], help="(fake) a deliberate bug; the run must then fail")
    ap.add_argument("--players", type=int, default=0)
    ap.add_argument("--instances", default="0,1")
    ap.add_argument("--weapon", action="append", default=[], metavar="CLASS=ITEM")
    ap.add_argument("--neighbor", default="", help="Plugin.esm:HEX of a second hostile next to the target (the explosive blast check)")
    ap.add_argument("--slowmo", action="store_true", help="also run the party slow-mo step (the host's setting is switched on for it)")
    ap.add_argument("--wait", type=float, default=30.0)
    ap.add_argument("--outdir", default="")
    ap.add_argument("--keep-going", action="store_true")
    ap.add_argument("--no-scenario", action="store_true")
    a = ap.parse_args(argv)
    if a.list:
        for n, cls in pb_vats.VATS_REGISTRY.items():
            pb = cls()
            print(f"{n:16} {pb.weapon_class:10} scenario={pb.scenario:10} {pb.summary}")
        return 0
    names = list(pb_vats.VATS_REGISTRY) if a.playbook == "all" else [x for x in a.playbook.split(",") if x]
    unknown = [n for n in names if n not in pb_vats.VATS_REGISTRY]
    if unknown:
        print(f"unknown playbook(s) {unknown}; use --list", file=sys.stderr)
        return 2
    stamp = time.strftime("%Y%m%dT%H%M%S")
    outroot = a.outdir or os.path.join(REPO, "out", "vats", stamp)
    os.makedirs(outroot, exist_ok=True)
    opts: Dict[str, Any] = {"repo_root": REPO, "slowmo": a.slowmo, "anim": False}
    meta: Dict[str, Any] = {"stamp": stamp, "mode": "fake" if a.fake else "live", "bugs": a.bug}
    if a.fake:
        results = run_fake(names, a.players, set(a.bug), outroot, opts)
    else:
        weapons = {}
        for w in a.weapon:
            if "=" not in w:
                print(f"--weapon wants CLASS=ITEM, got {w!r}", file=sys.stderr)
                return 2
            k, v = w.split("=", 1)
            weapons[k.strip().lower()] = v.strip()
        missing = [pb_vats.make_vats(n).weapon_class for n in names if pb_vats.make_vats(n).weapon_class not in weapons]
        if missing:
            print(f"no weapon given for class(es) {missing}: pass --weapon <class>=<editor id or Plugin.esm:HEX> (there is no default on purpose)", file=sys.stderr)
            return 2
        from bots import Bot
        from nvbridge import NvBridge
        from playbooks import scenario_adapter
        from playbooks.adapter import PlaybookBot
        results = []
        with contextlib.ExitStack() as stack:
            inst = [int(x) for x in a.instances.split(",")]
            try:
                nvs = [stack.enter_context(NvBridge.connect(instance=i, wait_s=a.wait)) for i in inst]
            except Exception as e:  # noqa: BLE001
                print(f"cannot reach the game instances: {e}", file=sys.stderr)
                return 2
            comparer = core.LiveComparer(REPO)
            for name in names:
                pb = pb_vats.make_vats(name)
                try:
                    meta.setdefault("meta", {})[name] = scenario_adapter.start("vats_hit", nvs, REPO, log) if not a.no_scenario else {"mode": "skipped"}
                    refs = dict(scenario_adapter.refs_for("vats_hit", REPO))
                except Exception as ex:  # noqa: BLE001
                    log(f"FAIL {name}: scenario start failed: {ex}")
                    results.append(core.RunResult(name, False, [], os.path.join(outroot, name), f"scenario start failed: {ex}"))
                    if not a.keep_going:
                        break
                    continue
                refs["weapon"] = weapons[pb.weapon_class]
                if a.neighbor:
                    refs["neighbor"] = a.neighbor
                machines = [core.Machine(i, "host" if i == 0 else f"client{i}", PlaybookBot(Bot(nv, "host" if i == 0 else f"client{i}")), inst[i]) for i, nv in enumerate(nvs)]
                r = core.run_playbook(pb, machines, comparer, os.path.join(outroot, name), refs, opts, log)
                results.append(r)
                if not r.ok and not a.keep_going:
                    break
    print()
    width = max([len(r.playbook) for r in results] + [8])
    for r in results:
        print(f"{'PASS' if r.ok else 'FAIL'}  {r.playbook:{width}}  {len(r.steps)} steps  {r.reason}")
    ok = bool(results) and all(r.ok for r in results)
    with open(os.path.join(outroot, "summary.json"), "w", encoding="utf-8") as f:
        json.dump({"meta": meta, "ok": ok, "results": [r.to_json() for r in results]}, f, indent=1, default=str)
    print(f"summary: {os.path.join(outroot, 'summary.json')}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
