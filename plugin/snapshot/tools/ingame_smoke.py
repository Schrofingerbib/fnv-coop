#!/usr/bin/env python3
"""In-game smoke test for the L7 snapshot dumper (one minimized instance, under the gameslot lease).

    python -I -X utf8 plugin/snapshot/tools/ingame_smoke.py [--min-quests 50] [--min-objectives 1000] [--keep-dll]

Launches instance 0, waits for the autoload (autosave) + 600 frames, calls the bridge `state` method and checks:
  * meta.error is absent (no faulted section; a fault names the section and form),
  * the snapshot passes snaplib.guard (schema + counts),
  * >= --min-quests quests and >= --min-objectives objectives in total (real game: 640 quests, ~1,478 objectives),
  * two consecutive dumps differ in at most a handful of keys (sanity, not the volatility probe),
  * the dump took < 10 s.
Exit 0 = PASS. Evidence: out/snapshot/smoke/state_0.json, state_1.json, result.json.
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
import snaplib  # noqa: E402

OWNER = "L7-smoke"


def lease(cmd, *extra):
    return subprocess.run([sys.executable, "-I", str(REPO / "tools" / "gameslot.py"), cmd, "--owner", OWNER, *extra],
                          capture_output=True, text=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--min-quests", type=int, default=50)
    ap.add_argument("--min-objectives", type=int, default=1000)
    ap.add_argument("--wait", type=int, default=1500)
    a = ap.parse_args()
    out = REPO / "out" / "snapshot" / "smoke"
    out.mkdir(parents=True, exist_ok=True)
    wr = REPO / "out" / "snapshot" / "watchrefs.txt"
    if wr.exists():
        os.environ["FNVCOOP_WATCHREFS"] = str(wr)
    r = lease("acquire", "--ttl", "900", "--wait", str(a.wait))
    print(r.stdout.strip() or r.stderr.strip())
    if r.returncode != 0:
        sys.exit("could not get the game slot")
    ok = False
    try:
        foreign = winproc.list_pids("FalloutNV.exe")
        if foreign:
            sys.exit("FalloutNV.exe already running (pids %s); refusing to launch" % foreign)
        pid = runner.launch_instance(0)
        st = runner.load_state()
        st["pids"]["0"] = pid
        runner.save_state(st)
        nv = nvbridge.NvBridge.connect(0, wait_s=240)
        try:
            nv.wait_event("autoload.done", timeout_s=240)
            base = nv.ping()["frames"]
            while nv.ping()["frames"] - base < 600:
                time.sleep(0.25)
            snaps, secs = [], []
            for k in range(2):
                p = out / ("state_%d.json" % k)
                t0 = time.time()
                nv.state_to_file(str(p))
                secs.append(time.time() - t0)
                snaps.append(snaplib.load(str(p)))
                time.sleep(2)
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
        s = snaps[0]
        quests = (s.get("shared") or {}).get("quests") or {}
        objectives = sum(len(q.get("objectives") or {}) for q in quests.values())
        nvars = sum(len(q.get("vars") or {}) for q in quests.values())
        refs = (s.get("shared") or {}).get("refs") or {}
        diff = snaplib.diff(snaps[0], snaps[1], ("shared",))
        problems = snaplib.guard(s, ("shared", "perPlayer"), a.min_quests)
        checks = {
            "no meta.error (%s)" % s["meta"].get("error", "-"): not s["meta"].get("error"),
            "guard clean (%s)" % (problems[:1] or "ok"): not problems,
            ">= %d quests (got %d)" % (a.min_quests, len(quests)): len(quests) >= a.min_quests,
            ">= %d objectives (got %d)" % (a.min_objectives, objectives): objectives >= a.min_objectives,
            "dump < 10 s (%.2f s)" % max(secs): max(secs) < 10,
            "back-to-back dumps differ in < 100 keys (%d)" % len(diff): len(diff) < 100,
        }
        info = {"quests": len(quests), "objectives": objectives, "questVars": nvars, "refs": len(refs),
                "globals": len(s["shared"]["globals"]), "perks": len(s["perPlayer"].get("local", {}).get("perks", {})),
                "seconds": secs, "diffKeys": len(diff), "skippedObjectiveTargets": s["meta"].get("skippedObjectiveTargets", 0)}
        (out / "result.json").write_text(json.dumps({"info": info, "checks": checks}, indent=1))
        print(json.dumps(info))
        for k, v in checks.items():
            print(("PASS " if v else "FAIL ") + k)
        ok = all(checks.values())
    finally:
        lease("release")
    print("PASS" if ok else "FAIL")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
