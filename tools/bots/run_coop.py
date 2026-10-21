#!/usr/bin/env python3
"""Run multi-bot co-op playbooks.

Game-free (fake co-op world; this is what the tests use):
    python -I tools/bots/run_coop.py --fake --playbook all
    python -I tools/bots/run_coop.py --fake --playbook loot_race --players 3 --bug dup_pickup     # must FAIL

In a game (the lead runs this; this tool never launches a game or takes the game slot). Start N co-op instances first
(host on pipe 0, clients on pipes 1..), joined as one session, then:
    python -I tools/bots/run_coop.py --playbook goodsprings_opening --instances 0,1
    python -I tools/bots/run_coop.py --playbook all --instances 0,1,2 --chaos bad_wifi --chaos-seed 7 --chaos-rpc runs/chaos.json
    python -I tools/bots/run_coop.py --playbook fight --pool          # lease instances from the T4a pool instead

Each playbook starts from its T4b scenario (a quick in-place jump on a warm instance), runs its steps, and after every
step checks that all machines converged, that snapdiff (host vs each client) is equal and that the desync counters did not
rise. A failing step saves FAIL-<n>-<step>/ (failure.json, snapshots, events, flight dumps) under the output directory for
tools/flight (T1b). Exit code: 0 all pass, 1 a playbook failed, 2 usage or setup error.
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

from playbooks import chaos_adapter, core, fakecoop, goodsprings_plan, registry  # noqa: E402


def log(s: str) -> None:
    print(s, flush=True)


def run_fake(names: List[str], players: int, bugs: set, outroot: str, opts: dict) -> List[core.RunResult]:
    from playbooks import pb_story
    results = []
    for name in names:
        e = registry.REGISTRY[name]
        n = players or e.players
        loss = chaos_adapter.FAKE_LOSS_EVERY.get(opts.get('chaos') or 'lan', 0)
        plan = None
        if e.needs_plan:
            plan = goodsprings_plan.load_plan(REPO)
            world, refs = pb_story.fake_goodsprings(plan, n, bugs, loss_every=loss)
        else:
            world, refs = e.fake(n, bugs, loss_every=loss)
        outdir = os.path.join(outroot, name)
        world.flight_dir = os.path.join(outdir, "flight")
        o = {"fast": True, "ui_s": 0.3, "barrier_s": 0.3, "stage_s": 0.3, "handoff_s": 0.3, "spawn_s": 0.3, "settle_s": 1.0, "settle_poll_s": 0.0, "repo_root": REPO}
        o.update({k: v for k, v in opts.items() if not k.endswith('_s') or k == 'quiesce_s'})
        pb = registry.make(name, REPO, plan)
        results.append(core.run_playbook(pb, fakecoop.make_machines(world), fakecoop.FakeComparer(), outdir, refs, o, log))
    return results


def _bridges(a, stack: contextlib.ExitStack):
    """Open one NvBridge per instance (own pipe each), or lease them from the pool."""
    if a.pool:
        sys.path.insert(0, os.path.join(REPO, "tools", "pool"))
        from client import lease           # noqa: WPS433
        L = stack.enter_context(lease("T6b-playbooks", n=a.players or 2, ttl_s=a.ttl, timeout_s=600))
        return [L.bridge(i) for i in L.instances], list(L.instances)
    from nvbridge import NvBridge          # noqa: WPS433
    inst = [int(x) for x in a.instances.split(",")]
    out = []
    for i in inst:
        out.append(stack.enter_context(NvBridge.connect(instance=i, wait_s=a.wait)))
    return out, inst


def main(argv=None, extra_opts=None) -> int:
    """extra_opts: Ctx options a caller adds that have no flag here (run_fight.py: from_step, only_step, timescale)."""
    ap =argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--list", action="store_true", help="list playbooks and exit")
    ap.add_argument("--playbook", default="", help="name, comma list, or 'all'")
    ap.add_argument("--players", type=int, default=0, help="number of bots (default: the playbook's own default)")
    ap.add_argument("--fake", action="store_true", help="game-free run on the fake co-op world")
    ap.add_argument("--bug", action="append", default=[], help="(fake) switch on a deliberate bug; the run must then fail")
    ap.add_argument("--instances", default="0,1", help="bridge instance ids, host first")
    ap.add_argument("--pool", action="store_true", help="lease the instances from tools/pool instead of --instances")
    ap.add_argument("--ttl", type=int, default=1800)
    ap.add_argument("--wait", type=float, default=30.0)
    ap.add_argument("--no-scenario", action="store_true", help="do not jump to the playbook's scenario first")
    ap.add_argument("--chaos", default="", help="chaos profile: " + ", ".join(chaos_adapter.PROFILES))
    ap.add_argument("--chaos-seed", type=int, default=None)
    ap.add_argument("--chaos-rpc", default="", help="chaos relay rpc port / host:port / info file")
    ap.add_argument("--outdir", default="")
    ap.add_argument("--no-anim", action="store_true", help="skip the T7 animation/pose assertions (needs [dev] animprobe=1 in the game)")
    ap.add_argument("--keep-going", action="store_true", help="run the next playbook after a failure")
    a = ap.parse_args(argv)

    if a.list:
        for n, e in registry.REGISTRY.items():
            pb = registry.make(n, REPO)
            print(f"{n:22} {e.players}p  scenario={pb.scenario:32} {pb.summary}")
        return 0
    if not a.playbook:
        ap.error("--playbook is required (or --list)")
    names = list(registry.REGISTRY) if a.playbook == "all" else [x for x in a.playbook.split(",") if x]
    unknown = [n for n in names if n not in registry.REGISTRY]
    if unknown:
        print(f"unknown playbook(s) {unknown}; use --list", file=sys.stderr)
        return 2
    stamp = time.strftime("%Y%m%dT%H%M%S")
    outroot = a.outdir or os.path.join(REPO, "out", "bots", stamp)
    os.makedirs(outroot, exist_ok=True)
    opts: Dict[str, Any] = {"repo_root": REPO, "chaos": a.chaos or None, "chaos_seed": a.chaos_seed}
    opts.update(chaos_adapter.relaxed_options(a.chaos, REPO))
    opts["anim"] = not a.no_anim
    opts.update(extra_opts or {})

    results: List[core.RunResult] = []
    meta: Dict[str, Any] = {"stamp": stamp, "mode": "fake" if a.fake else "live", "chaos": a.chaos or None,
                            "chaos_seed": a.chaos_seed, "bugs": a.bug}
    if a.fake:
        results = run_fake(names, a.players, set(a.bug), outroot, opts)
    else:
        if a.chaos:
            meta["chaos_applied"] = chaos_adapter.apply_profile(REPO, a.chaos_rpc, a.chaos, a.chaos_seed)
            log(f"chaos: {meta['chaos_applied']}")
        from bots import Bot
        from playbooks.adapter import PlaybookBot
        from playbooks import scenario_adapter
        with contextlib.ExitStack() as stack:
            try:
                nvs, inst = _bridges(a, stack)
            except Exception as e:   # noqa: BLE001
                print(f"cannot reach the game instances: {e}", file=sys.stderr)
                return 2
            comparer = core.LiveComparer(REPO)
            for name in names:
                e = registry.REGISTRY[name]
                n = a.players or e.players
                if n > len(nvs):
                    log(f"SKIP {name}: needs {n} instances, have {len(nvs)}")
                    continue
                use = nvs[:n]
                try:
                    meta.setdefault("meta", {})[name] = (scenario_adapter.start(name, use, REPO, log)
                                                         if not a.no_scenario else {"mode": "skipped"})
                    refs = scenario_adapter.refs_for(name, REPO)
                except Exception as ex:   # noqa: BLE001
                    log(f"FAIL {name}: scenario start failed: {ex}")
                    results.append(core.RunResult(name, False, [], os.path.join(outroot, name), f"scenario start failed: {ex}"))
                    if not a.keep_going:
                        break
                    continue
                machines = [core.Machine(i, "host" if i == 0 else f"client{i}", PlaybookBot(Bot(nv, "host" if i == 0 else f"client{i}")),
                                         inst[i]) for i, nv in enumerate(use)]
                pb = registry.make(name, REPO)
                r = core.run_playbook(pb, machines, comparer, os.path.join(outroot, name), refs, opts, log)
                results.append(r)
                if not r.ok and not a.keep_going:
                    break
    # summary
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
