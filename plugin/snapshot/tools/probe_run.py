#!/usr/bin/env python3
"""Run the empirical volatility probe against the real game (one minimized instance, under the gameslot lease).

    python -I -X utf8 plugin/snapshot/tools/probe_run.py [--launches 2] [--dumps 16] [--interval 4] [--settle 8] [--keep]

For each launch: start instance 0 (L10's instance root), wait for the autoload and --first-frames (60) frames, take --dumps `state`
snapshots --interval seconds apart WITH NO INPUT, quit the game. Then run volatile_probe.py over all dumps and write
out/snapshot/volatile_keys.{json,txt}. Snapshots go to out/snapshot/probe/L<launch>_<n>.json.
Refuses to start while another FalloutNV.exe is running; always releases the lease and kills its own instance.
"""
import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / "tools" / "runner"))
sys.path.insert(0, str(REPO / "plugin" / "snapshot" / "tools"))
os.environ.setdefault("FNV_INSTANCES_ROOT", str(REPO.parent / "fnv-instances-l10"))
import nvbridge  # noqa: E402
import run as runner  # noqa: E402
import winproc  # noqa: E402
import volatile_probe as vp  # noqa: E402
import snaplib  # noqa: E402

OWNER = "L7-probe"


def lease(cmd, *extra):
    return subprocess.run([sys.executable, "-I", str(REPO / "tools" / "gameslot.py"), cmd, "--owner", OWNER, *extra],
                          capture_output=True, text=True)


def one_launch(n, dumps, interval, outdir, first_frames):
    foreign = winproc.list_pids("FalloutNV.exe")
    if foreign:
        raise SystemExit("FalloutNV.exe already running (pids %s); refusing to launch" % foreign)
    pid = runner.launch_instance(0)
    st = runner.load_state()
    st["pids"]["0"] = pid
    runner.save_state(st)
    files = []
    nv = nvbridge.NvBridge.connect(0, wait_s=240)
    try:
        nv.wait_event("autoload.done", timeout_s=240)
        base = nv.ping()["frames"]
        while nv.ping()["frames"] - base < first_frames:
            time.sleep(0.25)
        for k in range(dumps):
            if k:
                time.sleep(interval)
            path = outdir / ("L%d_%d.json" % (n, k))
            t0 = time.time()
            r = nv.state_to_file(str(path))
            print("launch %d dump %d: %s (%.1fs) %s" % (n, k, path.name, time.time() - t0, json.dumps(r)[:120]))
            files.append(path)
    finally:
        try:
            nv.quit()
        except Exception:
            pass
        for _ in range(60):
            if not winproc.pid_alive(pid):
                break
            time.sleep(0.5)
        else:
            winproc.kill_pid(pid)
    return files


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--launches", type=int, default=2)
    ap.add_argument("--dumps", type=int, default=16)
    ap.add_argument("--interval", type=float, default=4.0)
    ap.add_argument("--settle", type=int, default=8, help="first N dump intervals are the settling window")
    ap.add_argument("--first-frames", type=int, default=60, help="frames to wait after autoload.done before dump 0")
    ap.add_argument("--wait", type=int, default=1500, help="seconds to wait for the game slot")
    a = ap.parse_args()
    outdir = REPO / "out" / "snapshot" / "probe"
    outdir.mkdir(parents=True, exist_ok=True)
    wr = REPO / "out" / "snapshot" / "watchrefs.txt"
    if wr.exists():
        os.environ["FNVCOOP_WATCHREFS"] = str(wr)
    r = lease("acquire", "--ttl", "1500", "--wait", str(a.wait))
    print(r.stdout.strip() or r.stderr.strip())
    if r.returncode != 0:
        raise SystemExit("could not get the game slot")
    groups = []
    try:
        for n in range(a.launches):
            groups.append(one_launch(n, a.dumps, a.interval, outdir, a.first_frames))
    finally:
        lease("release")
    story = vp.story_refs(str(REPO / "out" / "questdata" / "scenarios"))
    snaps = [[snaplib.load(str(p)) for p in g] for g in groups if g]
    res = vp.probe(snaps, story, vp.load_manual(vp.MANUAL_DEFAULT), settle=a.settle, interval_s=a.interval)
    txt = vp.render_txt(res)
    problems = vp.check_txt(txt, story)
    if problems:
        raise SystemExit("generated file violates the safety rules: %s" % problems[0])
    (REPO / "out" / "snapshot" / "volatile_keys.json").write_text(json.dumps(res, indent=1, sort_keys=True) + chr(10))
    (REPO / "out" / "snapshot" / "volatile_keys.txt").write_text(txt)
    c = res["counts"]
    print("volatile: %d vars, %d quest fields, %d globals; %d warnings, %d protected" % (
        c["vars"], c["fields"], c["globals"], c["warnings"], c["protected"]))
    print("settling: %d keys, last change after ~%s s (not excluded; take baselines after this)" % (
        c["settling"], res["recommendedSettleSeconds"]))
    for w in res["warnings"]:
        print("WARNING story quest %s %s changes by itself (%s): NOT excluded" % (w["edid"], w["field"], ",".join(w["reasons"])))


if __name__ == "__main__":
    main()
