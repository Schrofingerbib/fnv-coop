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


def probes():
    """(label, expression) pairs built from real refs of the shipped scenarios; each is evaluated once after loading the start save."""
    d = scenlib.load_scenario(scenlib.SCEN_DIR / "door_terminal_container_matrix.json")
    g = scenlib.load_scenario(scenlib.SCEN_DIR / "goodsprings_fight.json")
    s = scenlib.load_scenario(scenlib.SCEN_DIR / "spawn_once.json")
    rd, rg, rs = (scenlib.Resolver(x) for x in (d, g, s))
    q = scenlib.load_scenario(scenlib.SCEN_DIR / "quest_stage_sync.json")
    rq = scenlib.Resolver(q)
    return [
        ("GetStage on a quest", "GetStage %s" % rq.hex("$vcg02")),
        ("GetStageDone", "GetStageDone %s 10" % rq.hex("$vcg02")),
        ("ref.GetDead on an actor ref", "%s.GetDead" % rg.hex("$gang1")),
        ("ref.GetDisabled", "%s.GetDisabled" % rg.hex("$gang1")),
        ("ref.GetAV Health", "%s.GetAV Health" % rg.hex("$gang1")),
        ("ref.GetLocked on a door ref", "%s.GetLocked" % rd.hex("$docDoor")),
        ("ref.GetItemCount on a container ref", "%s.GetItemCount %s" % (rd.hex("$dresser"), rd.hex("$stimpak"))),
        ("player.GetItemCount", "player.GetItemCount %s" % rd.hex("$stimpak")),
        ("player.GetDistance ref", "player.GetDistance %s" % rg.hex("$gangMarker")),
        ("ref.GetPlayerTeammate", "%s.GetPlayerTeammate" % rg.hex("$gang1")),
    ]


def mode_probe():
    out = []
    with lease("scenarios-proof", n=1, ttl_s=900) as L:
        nv = L.bridge(0)
        nv.load_save("autosave")
        time.sleep(3)
        for label, expr in probes():
            try:
                v = nv.eval(expr)
                out.append({"label": label, "expr": expr, "ok": True, "value": v})
            except Exception as e:   # noqa: BLE001
                out.append({"label": label, "expr": expr, "ok": False, "error": str(e)[:160]})
        for label, cmd in (("ref.lock", "%s.lock 50" % scenlib.Resolver(scenlib.load_scenario(scenlib.SCEN_DIR / "door_terminal_container_matrix.json")).hex("$docDoor")),):
            r = nv.exec(cmd)
            out.append({"label": label, "expr": cmd, "ok": r.get("ok") is not False and "parse" not in (r.get("output") or "").lower(),
                        "value": r.get("output")})
    return out


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
