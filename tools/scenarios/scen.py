#!/usr/bin/env python3
"""Scenario CLI.

    python -I tools/scenarios/scen.py list
    python -I tools/scenarios/scen.py validate [NAME ...]              game-free; exit 1 on any error
    python -I tools/scenarios/scen.py plan NAME [--prev NAME]          show the jump decision (reload or in place) without a game
    python -I tools/scenarios/scen.py run NAME [NAME ...] [--json out.json] [--force-reload]   needs the pool (tools/pool)
    python -I tools/scenarios/scen.py jump NAME                        leave the instance(s) in the scenario's starting state

`run` and `jump` lease instances from the warm pool (`pool up` first); this tool never launches a game itself.
"""
import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import scenlib  # noqa: E402
import runner  # noqa: E402

REPO = HERE.parents[1]


def _paths(names):
    if not names:
        return scenlib.list_scenarios()
    out = []
    for n in names:
        p = Path(n)
        out.append(p if p.suffix == ".json" else scenlib.SCEN_DIR / (n + ".json"))
    return out


def cmd_list(a):
    for p in scenlib.list_scenarios():
        sc = scenlib.load_scenario(p)
        needs = sorted(scenlib.scenario_needs(sc) - scenlib.PRESENT)
        print("%-24s players=%d  %s%s" % (sc["name"], scenlib.players_of(sc), sc["title"],
                                          ("  [needs: %s]" % ",".join(needs)) if needs else ""))
    return 0


def cmd_validate(a):
    bad = 0
    for p in _paths(a.names):
        errs = scenlib.validate_file(p)
        print("%s %s" % ("FAIL" if errs else "ok  ", p.name))
        for e in errs:
            print("    " + e)
        bad += bool(errs)
    print("%d scenario file(s) checked, %d invalid" % (len(_paths(a.names)), bad))
    return 1 if bad else 0


def cmd_plan(a):
    sc = scenlib.load_scenario(_paths([a.name])[0])
    state = {"loaded": False}
    if a.prev:
        pv = scenlib.load_scenario(_paths([a.prev])[0])
        fp = scenlib.footprint(pv)
        state = {"loaded": True, "save": pv["start"]["save"], "dirty": sorted(fp["dirty"]), "failed": False}
    plan = scenlib.plan_jump(state, sc)
    print(json.dumps(plan, indent=1))
    print("reset ids:", " ".join(scenlib.reset_ids(sc)))
    return 0


def _with_lease(a, fn):
    sys.path.insert(0, str(REPO / "tools" / "pool"))
    try:
        from client import lease, PoolError
    except Exception as e:   # noqa: BLE001
        print("cannot import tools/pool/client.py: %s" % e)
        return 2
    scs = [scenlib.load_scenario(p) for p in _paths(a.names)]
    n = max(scenlib.players_of(s) for s in scs)
    try:
        with lease("scenarios", n=n, ttl_s=1800) as L:
            gens = getattr(L, "_gens", {})
            keys = [runner.StateStore.key(i, gens.get(i, 0)) for i in L.instances]
            store = runner.StateStore(REPO / "out" / "scenarios" / "state.json")
            rn = runner.Runner([L.bridge(i) for i in L.instances], store, keys)
            return fn(rn, scs)
    except PoolError as e:
        print("pool: %s" % e)
        return 2


def cmd_run(a):
    def go(rn, scs):
        results = [rn.run(s, a.force_reload) for s in scs if scenlib.players_of(s) <= len(rn.nvs)]
        print(runner.summarize(results))
        if a.json:
            Path(a.json).parent.mkdir(parents=True, exist_ok=True)
            Path(a.json).write_text(json.dumps(results, indent=1), encoding="utf-8")
        return 0 if all(r["status"] in ("pass", "partial") for r in results) else 1
    return _with_lease(a, go)


def cmd_jump(a):
    def go(rn, scs):
        j = rn.jump(scs[0], a.force_reload)
        print(json.dumps({k: j[k] for k in ("reload", "reasons", "seconds")}, indent=1))
        return 0
    return _with_lease(a, go)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list").set_defaults(fn=cmd_list)
    p = sub.add_parser("validate")
    p.add_argument("names", nargs="*")
    p.set_defaults(fn=cmd_validate)
    p = sub.add_parser("plan")
    p.add_argument("name")
    p.add_argument("--prev")
    p.set_defaults(fn=cmd_plan)
    for nm, fn in (("run", cmd_run), ("jump", cmd_jump)):
        p = sub.add_parser(nm)
        p.add_argument("names", nargs="+")
        p.add_argument("--json")
        p.add_argument("--force-reload", action="store_true")
        p.set_defaults(fn=fn)
    a = ap.parse_args(argv)
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main())
