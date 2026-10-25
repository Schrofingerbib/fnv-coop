#!/usr/bin/env python
"""Find the benchmark arena: an exterior cell where the bots can really walk with movement input, and save it to tools/bench/arena.json.

    python -I tools/bench/find_arena.py              # boots two copies, loads the save, tries candidate cells, writes arena.json
    python -I tools/bench/find_arena.py --no-boot    # use the two copies that are already up and in the world

For each candidate cell the host is teleported there (console `cow`) and takes four short walks with real movement input. The first cell where
all four arrive wins; otherwise the cell where the most legs arrived. If NO cell lets any leg arrive, movement input itself does not work on this
setup (the report says so), and the benchmark keeps using step mode.
"""
import argparse, json, subprocess, sys, time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path[:0] = [str(HERE), str(ROOT / "tools"), str(ROOT / "tools" / "runner")]
import arena as arena_mod  # noqa: E402
import coop_bench  # noqa: E402
import phases  # noqa: E402


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--no-boot", action="store_true")
    ap.add_argument("--save", default="nvcoop_autosave_ready")
    ap.add_argument("--keep-up", action="store_true")
    ap.add_argument("--out", default="")
    a = ap.parse_args()
    if not a.no_boot:
        print("booting two copies, loading the save ...", flush=True)
        coop_bench.boot(a.save)
    from nvbridge import NvBridge
    from bots.bot import Bot
    conns = [NvBridge.connect(instance=i, wait_s=20) for i in (0, 1)]
    bots = [Bot(nv, name=f"find{i}", slot=i) for i, nv in enumerate(conns)]
    for b in bots:
        b.auto_dismiss = True
    log = lambda s: print("   " + s, flush=True)   # noqa: E731
    best, probes = phases.find_origin(bots, log)
    res = {"best": best, "probes": probes, "input_works_anywhere": any(sum(p["moved"]) > 50 for p in probes)}
    stamp = time.strftime("%Y%m%d-%H%M%S")
    outp = Path(a.out) if a.out else ROOT / "out" / "bench" / f"arena-{stamp}.json"
    outp.parent.mkdir(parents=True, exist_ok=True)
    outp.write_text(json.dumps(res, indent=1), encoding="utf-8")
    if best["legs_ok"] >= 1 and best.get("pos"):
        arena = arena_mod.Arena(origin=best["pos"], cell=("WastelandNV", best["cell"][0], best["cell"][1]))
        arena.save()
        print(f"arena saved: cell {best['cell']}, origin {best['pos']}, {best['legs_ok']}/4 legs arrived  ->  {arena_mod.DEFAULT_FILE}")
    else:
        print("NO cell let a walk arrive: real movement input does not work on this setup (or every candidate is blocked). arena.json not written.")
    print("details:", outp)
    if not a.keep_up and not a.no_boot:
        subprocess.run([sys.executable, "-I", str(ROOT / "tools" / "runner" / "run.py"), "down"], cwd=str(ROOT), capture_output=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
