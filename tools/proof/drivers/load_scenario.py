#!/usr/bin/env python3
"""prove.py piece that puts a booted copy next to the Goodsprings powder ganger (FalloutNV.esm:104C70) before a single-copy test that reads him.

    python -I tools/proof/drivers/load_scenario.py --instances 0 [--playbook enemy_fight] [--wait 240]

A freshly booted copy sits in the main menu: no save is loaded, so the NPC does not exist ("NOT READY: the NPC cannot be read: wrong ref, not
loaded, or no 3D"). This driver
  1. waits until every copy's main thread serves requests (ping: menuReady and a main loop that ticks), so loadSave is not refused
     with "main thread did not service the request (is the game loading?)";
  2. jumps to the scenario of that playbook (tools/bots/playbooks/scenario_adapter.py: loads the scenario's start save, enables the gang, moves
     the player to the first ganger: scenarios/goodsprings_enemies.json, the same start state as enemy_fight);
Prints `PASS name` / `FAIL name: why`. Exit 0 ok, 1 the jump failed, 2 the PC is not ready ("cannot reach the game instances"). The tests (no game)
are in tools/proof/drivers/tests.
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path
from typing import Any, Callable, List, Optional

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
sys.path.insert(0, str(HERE))
import drv_common as D  # noqa: E402

sys.path.insert(0, str(REPO / "tools" / "runner"))
import nvready  # noqa: E402

READY_WAIT_S = nvready.READY_WAIT_S
POLL_S = nvready.POLL_S
is_ready = nvready.is_ready                  # the wait is shared with prove.py and the proof scripts (tools/runner/nvready.py)
wait_ready = nvready.wait_ready


def run(bridges: List[Any], start: Callable[[List[Any]], dict], playbook: str, wait_s: float = READY_WAIT_S, out: Callable[[str], None] = print,
        sleep: Callable[[float], None] = time.sleep, now: Callable[[], float] = time.monotonic) -> int:
    chk = D.Checks(out)
    if D.MUTANT != "no_ready_wait":
        why = wait_ready(bridges, wait_s, sleep, now)
        if why:
            return D.env_exit(why, out)
    try:
        r = start(bridges)
    except Exception as e:  # noqa: BLE001
        chk.check("load scenario", False, f"{playbook}: {str(e)[:200]}")
        return D.EXIT_FAIL
    chk.check("load scenario", True)
    out(f"scenario {r.get('scenario')} mode {r.get('mode')}")
    return chk.exit_code()


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--instances", default="0", help="comma list of bridge instance ids")
    ap.add_argument("--playbook", default="enemy_fight", help="whose scenario start state to jump to (tools/bots/playbooks/scenario_adapter.py)")
    ap.add_argument("--wait", type=float, default=READY_WAIT_S)
    a = ap.parse_args(argv)
    sys.path.insert(0, str(REPO / "tools" / "runner"))
    sys.path.insert(0, str(REPO / "tools" / "bots"))
    from nvbridge import NvBridge  # noqa: E402
    from playbooks import scenario_adapter  # noqa: E402
    ids = [int(x) for x in a.instances.split(",") if x != ""]
    try:
        bridges = [NvBridge.connect(instance=i, wait_s=a.wait) for i in ids]
    except Exception as e:  # noqa: BLE001
        return D.env_exit(f"{e}")
    try:
        return run(bridges, lambda b: scenario_adapter.start(a.playbook, b, str(REPO), lambda *_: None), a.playbook, a.wait)
    finally:
        for b in bridges:
            b.close()


if __name__ == "__main__":
    sys.exit(main())
