"""Develop arena content against two running copies without paying a boot per try.

    python -I tools/bench/arena_dev.py up        boot two copies, load the save, move both into the arena, leave them running
    python -I tools/bench/arena_dev.py fight     run the arena fight once and print every check
    python -I tools/bench/arena_dev.py loot      run the arena loot once and print every check
    python -I tools/bench/arena_dev.py eval "<console line>" [--on 0|1] [--ref HEX]
"""
import argparse, json, sys, time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE), str(HERE.parents[1] / "tools"), str(HERE.parents[1] / "tools" / "runner"), str(HERE.parents[1] / "tools" / "bots")]
import arena as arena_mod  # noqa: E402
import arena_content as ac  # noqa: E402
import coop_bench  # noqa: E402
import phases  # noqa: E402


def bots():
    from nvbridge import NvBridge
    from bots.bot import Bot
    return [Bot(NvBridge.connect(instance=i, wait_s=20), name=f"b{i}", slot=i) for i in (0, 1)]


def show(res):
    for s in res["steps"]:
        print(("PASS " if s["ok"] else "FAIL ") + s["name"] + ("" if s["ok"] else "  <- " + s["reason"]))
    print(f"{res['steps_ok']}/{res['steps_total']}  {res.get('error', '')}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["up", "fight", "loot", "real", "eval"])
    ap.add_argument("arg", nargs="?")
    ap.add_argument("--on", type=int, default=0)
    ap.add_argument("--ref", default=None)
    ap.add_argument("--save", default="autosave")
    a = ap.parse_args()
    if a.cmd == "up":
        coop_bench.boot(a.save)
        phases.goto_arena(arena_mod.Arena.load(), bots(), print)
        print("copies are up in the arena")
        return 0
    bs = bots()
    ar = arena_mod.Arena.load()
    if a.cmd == "fight":
        show(ac.arena_fight(bs, ar, print))
    elif a.cmd == "real":
        import real_stage as rs
        rs.stage(bs, print)
        show(rs.real_fight(bs, print))
        show(rs.real_loot(bs, print))
    elif a.cmd == "loot":
        show(ac.arena_loot(bs, ar, print))
    else:
        r = bs[a.on].nv.exec(a.arg, ref=a.ref) if a.ref else bs[a.on].nv.exec(a.arg)
        print(json.dumps(r)[:600])
    return 0


if __name__ == "__main__":
    sys.exit(main())
