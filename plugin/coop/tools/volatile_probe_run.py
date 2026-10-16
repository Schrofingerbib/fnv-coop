#!/usr/bin/env python3
"""Collect the snapshots L7's volatile_probe.py needs: 2 game launches of the same save (3 and 2 state dumps, 20 s apart,
no input), then run the probe. Output: out/snapshot/volatile_keys.{json,txt}. Single instance, minimized, gameslot lease."""
import os, subprocess, sys, time
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / "tools" / "runner"))
os.environ["FNV_INSTANCES_ROOT"] = str(REPO.parent / "fnv-instances-l10")
import nvbridge, run as runner, winproc  # noqa: E402

OUT = REPO / "out" / "L10" / "volatile"
OUT.mkdir(parents=True, exist_ok=True)


def launch_and_dump(names):
    pid = runner.launch_instance(0)
    st = runner.load_state(); st["pids"]["0"] = pid; runner.save_state(st)
    nv = nvbridge.NvBridge.connect(0, wait_s=240)
    try:
        nv.wait_event("autoload.done", timeout_s=240)
        base = nv.ping()["frames"]
        while nv.ping()["frames"] - base < 1500:
            time.sleep(0.25)
        for i, n in enumerate(names):
            if i:
                time.sleep(20)
            nv.state_to_file(str(OUT / n))
            print("dumped", n)
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


if winproc.list_pids("FalloutNV.exe"):
    sys.exit("game already running")
launch_and_dump(["a1.json", "a2.json", "a3.json"])
time.sleep(5)
launch_and_dump(["b1.json", "b2.json"])
(REPO / "out" / "snapshot").mkdir(parents=True, exist_ok=True)
cp = subprocess.run([sys.executable, "-I", str(REPO / "plugin/snapshot/tools/volatile_probe.py"), "probe", "--scenarios", str(REPO / "out/questdata/scenarios"),
                     "--json", str(REPO / "out/snapshot/volatile_keys.json"), "--txt", str(REPO / "out/snapshot/volatile_keys.txt"),
                     "--group", *[str(OUT / n) for n in ("a1.json", "a2.json", "a3.json")],
                     "--group", *[str(OUT / n) for n in ("b1.json", "b2.json")]], capture_output=True, text=True)
print(cp.stdout[-3000:], cp.stderr[-1500:])
sys.exit(cp.returncode)
