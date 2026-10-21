#!/usr/bin/env python3
"""Two-copy actor ownership handoff playbook (milestone 3). WRITTEN, NEVER RUN: it needs two real co-op game instances.

    python -I tools/bots/run_actor_handoff.py --instances 0,1            # host on pipe 0, client on pipe 1, joined as one session
    python -I tools/bots/run_actor_handoff.py --instances 0,1 --rounds 4 --no-scenario --refs refs.json

The lead starts the instances (the game slot rules of docs/CONTRACTS.md apply; this tool never launches a game or takes the slot).
It reads the starting positions from the T4b scenario `ownership_handoff` (refs: raiders, far_pos, near_pos), or from --refs (a JSON
file with the same keys). Exit code: 0 pass, 1 the playbook failed, 2 usage or setup error (including: the build has no coop.owner,
or the engine adapter does not feed ActorSync yet). Output goes to out/bots/<stamp>/ownership_transfer/ like run_coop.py.
"""
from __future__ import annotations

import argparse
import contextlib
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
for p in (HERE, os.path.join(REPO, "tools"), os.path.join(REPO, "tools", "runner")):
    if p not in sys.path:
        sys.path.insert(0, p)

from playbooks import core  # noqa: E402
from playbooks.pb_actors import OwnershipTransferPlaybook  # noqa: E402


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--instances", default="0,1", help="bridge instance ids, host first (exactly two)")
    ap.add_argument("--wait", type=float, default=30.0)
    ap.add_argument("--rounds", type=int, default=2, help="host-away / host-back rounds (each round is two handoffs)")
    ap.add_argument("--no-scenario", action="store_true", help="do not jump to the scenario first (use --refs)")
    ap.add_argument("--refs", default="", help="JSON file with raiders, far_pos, near_pos [, raider_base]")
    ap.add_argument("--outdir", default="")
    a = ap.parse_args(argv)
    inst = [int(x) for x in a.instances.split(",") if x != ""]
    if len(inst) != 2:
        ap.error("--instances needs exactly two ids (host, client)")
    from nvbridge import NvBridge
    from bots import Bot
    from playbooks.adapter import PlaybookBot
    from playbooks import scenario_adapter
    stamp = time.strftime("%Y%m%dT%H%M%S")
    outroot = a.outdir or os.path.join(REPO, "out", "bots", stamp)
    os.makedirs(outroot, exist_ok=True)
    log = lambda s: print(s, flush=True)       # noqa: E731
    with contextlib.ExitStack() as stack:
        try:
            nvs = [stack.enter_context(NvBridge.connect(instance=i, wait_s=a.wait)) for i in inst]
        except Exception as e:   # noqa: BLE001
            print(f"cannot reach the game instances: {e}", file=sys.stderr)
            return 2
        if a.refs:
            with open(a.refs, encoding="utf-8") as f:
                refs = json.load(f)
        else:
            refs = scenario_adapter.refs_for("ownership_handoff", REPO)
        if not a.no_scenario:
            try:
                scenario_adapter.start("ownership_handoff", nvs, REPO, log)
            except Exception as e:   # noqa: BLE001
                print(f"scenario start failed: {e}", file=sys.stderr)
                return 2
        machines = [core.Machine(i, "host" if i == 0 else f"client{i}", PlaybookBot(Bot(nv, "host" if i == 0 else f"client{i}")), inst[i])
                    for i, nv in enumerate(nvs)]
        opts = {"repo_root": REPO, "rounds": a.rounds}
        r = core.run_playbook(OwnershipTransferPlaybook(), machines, core.LiveComparer(REPO), os.path.join(outroot, "ownership_transfer"), refs, opts, log)
    print(f"{'PASS' if r.ok else 'FAIL'}  {r.playbook}  {len(r.steps)} steps  {r.reason}")
    return 0 if r.ok else 1


if __name__ == "__main__":
    sys.exit(main())
