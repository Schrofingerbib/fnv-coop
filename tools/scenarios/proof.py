#!/usr/bin/env python3
"""In-game proof for the scenario tool. NOT run by the tests; the lead runs it later with one game at a time.

    python -I -X utf8 tools/scenarios/proof.py probe            # 1 instance: are the console/eval forms the scenarios use valid?
    python -I -X utf8 tools/scenarios/proof.py jump             # 1 instance: time a reload jump, then in-place jumps
    python -I -X utf8 tools/scenarios/proof.py suite            # 2 instances (host+guest connected): run every shipped scenario

Needs the pool up (`python tools/pool/pool.py up`). Results go to out/scenarios/proof_<mode>.json. The probe settles the
forms marked UNVERIFIED in docs/tools/T4b-scenarios.md; the scenarios that use a form the probe rejects must be fixed first.
"""
import json
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(REPO / "tools" / "pool"))
import scenlib  # noqa: E402
import runner  # noqa: E402
from client import lease  # noqa: E402

OUT = REPO / "out" / "scenarios"
STATE = OUT / "proof_state.json"   # runner state, keyed by game process (runner.StateStore.lease_keys)


def _resolvers():
    names = ("door_terminal_container_matrix", "goodsprings_fight", "quest_stage_sync")
    return [scenlib.Resolver(scenlib.load_scenario(scenlib.SCEN_DIR / (n + ".json"))) for n in names]


def probes():
    """(label, expression, calling ref) triples built from real refs of the shipped scenarios through scenlib's own check
    translation, so the probe tests the exact console text the scenarios send. A ref-targeted expression runs with
    eval(expr, ref=<hex>): the game's console has no "<hex>.Func" form (in game, 2026-10-21). ref is None for the rest."""
    rd, rg, rq = _resolvers()

    def chk(label, res, **st):
        e = scenlib.check_expr(dict(st, id="probe", op="check"), res)
        return (label, e[0], e[6])
    return [
        chk("GetStage on a quest", rq, kind="stage", quest="$vcg02"),
        chk("GetStageDone", rq, kind="stage_done", quest="$vcg02", stage=10),
        chk("ref.GetDead on an actor ref", rg, kind="dead", ref="$gang1"),
        chk("ref.GetDisabled", rg, kind="disabled", ref="$gang1"),
        chk("ref.GetAV Health", rg, kind="av", ref="$gang1", av="Health"),
        chk("ref.GetLocked on a door ref", rd, kind="locked", ref="$docDoor"),
        chk("ref.GetItemCount on a container ref", rd, kind="item_count", who="$dresser", item="$stimpak"),
        chk("player.GetItemCount", rd, kind="item_count", who="player", item="$stimpak"),
        chk("player.GetDistance ref", rg, kind="distance", a="player", b="$gangMarker"),
        chk("ref.GetPlayerTeammate", rg, kind="teammate", ref="$gang1"),
    ]


def console_probes():
    """(label, exec action) pairs from scenlib.step_actions: ("exec", line) or ("exec", line, calling ref)."""
    rd = _resolvers()[0]
    return [("ref.lock", scenlib.step_actions({"id": "probe", "op": "lock", "ref": "$docDoor", "level": 50}, rd)[0])]


def probe_rows(nv):
    """Run every probe once on the bridge `nv`. -> one row per probe; ok False says the console rejected the form."""
    out = []
    for label, expr, ref in probes():
        try:
            v = nv.eval(expr) if ref is None else nv.eval(expr, ref=ref)
            out.append({"label": label, "expr": expr, "ref": ref, "ok": True, "value": v})
        except Exception as e:   # noqa: BLE001
            out.append({"label": label, "expr": expr, "ref": ref, "ok": False, "error": str(e)[:160]})
    for label, act in console_probes():
        cmd, ref = act[1], (act[2] if len(act) > 2 else None)
        r = nv.exec(cmd) if ref is None else nv.exec(cmd, ref=ref)
        out.append({"label": label, "expr": cmd, "ref": ref,
                    "ok": r.get("ok") is not False and "parse" not in (r.get("output") or "").lower(), "value": r.get("output")})
    return out


def mode_probe():
    with lease("scenarios-proof", n=1, ttl_s=900) as L:
        nv = L.bridge(0)
        nv.load_save("autosave")
        time.sleep(3)
        return probe_rows(nv)


def mode_jump(lease_fn=lease, state_path=STATE, **kw):
    """The first jump reloads unless the state store already knows this game process (then a later run in the same pool
    jumps in place). kw goes to runner.Runner (the tests pass a fake clock)."""
    names = ["quest_stage_sync", "goodsprings_fight", "door_terminal_container_matrix", "goodsprings_fight"]
    rows = []
    with lease_fn("scenarios-proof", n=1, ttl_s=1800) as L:
        sc1 = [scenlib.load_scenario(scenlib.SCEN_DIR / (n + ".json")) for n in names]
        # one-player view of two-player scenarios: only the host side is exercised
        rn = runner.for_lease(L, state_path, **kw)
        for sc in sc1:
            sc = dict(sc, players=1)
            t0 = time.time()
            j = rn.jump(sc)
            rows.append({"scenario": sc["name"], "reload": j["reload"], "reasons": j["reasons"], "seconds": round(time.time() - t0, 1)})
    return rows


def mode_suite(lease_fn=lease, state_path=STATE, show=print, **kw):
    with lease_fn("scenarios-proof", n=2, ttl_s=3600) as L:
        rn = runner.for_lease(L, state_path, **kw)
        res = [rn.run(scenlib.load_scenario(p)) for p in scenlib.list_scenarios()]
    show(runner.summarize(res))
    return res


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else ""
    fn = {"probe": mode_probe, "jump": mode_jump, "suite": mode_suite}.get(mode)
    if not fn:
        print(__doc__)
        return 2
    res = fn()
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / ("proof_%s.json" % mode)).write_text(json.dumps(res, indent=1), encoding="utf-8")
    for r in res if mode != "suite" else []:
        print(r)
    return 0


if __name__ == "__main__":
    sys.exit(main())
