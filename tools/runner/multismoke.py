#!/usr/bin/env python3
"""Two-instance smoke (L2): provision N hardlinked instances with a dll, `up` them (sequential, minimized,
no-activate, multiInstance=1 so the plugin renames the game mutex in-process), then ping every pipe every
--every seconds for --minutes, recording latency/frames and whether each game window stays minimized.

    python -I -X utf8 tools/runner/multismoke.py [--n 2] [--minutes 10] [--every 30] [--dll out/L1/nvcoop.dll]

Holds the game slot for the whole run (owner L2), always kills the games and releases the slot. Never touches
the owner's INIs or saves (instances are private copies; the save used is copied FROM My Games read-only).
Writes runs/<id>/multismoke.json; exit 0 only if every ping of every instance succeeded.
"""
import argparse
import json
import shutil
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
sys.path.insert(0, str(HERE))
import instances  # noqa: E402
import launchprobe  # noqa: E402
import nvbridge  # noqa: E402
import run  # noqa: E402
import winproc  # noqa: E402


def grep_log(i, pats=("multiInstance", "ALREADY", "NOT FOUND", "loading")):
    f = instances.instance_dir(i) / "Data" / "NVSE" / "Plugins" / "nvcoop.log"
    if not f.exists():
        return ["<no nvcoop.log>"]
    return [l.rstrip() for l in f.read_text(errors="replace").splitlines() if any(p in l for p in pats)][:12]


def window_states(pid):
    return [("iconic" if ic else "NORMAL") for _, p, _, ic in launchprobe.windows_of({pid})]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=2)
    ap.add_argument("--minutes", type=float, default=10)
    ap.add_argument("--every", type=float, default=30)
    ap.add_argument("--dll", default=str(REPO / "out" / "L1" / "nvcoop.dll"))
    ap.add_argument("--slot-wait", type=int, default=3600)
    a = ap.parse_args()
    out = {"cpus": __import__("os").cpu_count(), "sys_before": winproc.system_memory(), "dll": a.dll, "dll_sha256": instances.sha256_file(Path(a.dll)), "n": a.n, "pings": [], "ok": False}
    try:
        run.slot_acquire("L2", ttl=int(a.minutes * 60) + 900, wait=a.slot_wait)
    except run.SlotBusy as e:
        print(f"slot busy: {e}", file=sys.stderr)
        return 2
    for _ in range(60):  # previous owner may still be shutting its game down
        if not winproc.list_pids("FalloutNV.exe"):
            break
        time.sleep(1)
    else:
        print("FalloutNV.exe still running after acquiring the slot; abort", file=sys.stderr)
        run.slot_release("L2")
        return 2
    t_start = time.time()
    try:
        for i in range(a.n):
            instances.provision(i, role="host" if i == 0 else "client", plugin_dll=Path(a.dll),
                                autoload="autosave", autoload_delay_frames=600, extra_ini={"coop": {"enable": "0"}})
            sav = instances.instance_dir(i) / "Saves"
            src = Path.home() / "Documents" / "My Games" / "FalloutNV" / "Saves" / "autosave.fos"
            shutil.copy2(src, sav / "autosave.fos")
        class A:  # args for run.cmd_up
            n, wait_pipe, stagger, wait_pipe_each, ttl, slot_wait, allow_foreign = a.n, 0, 2.0, 300, 1800, 60, False
        rc = run.cmd_up(A)
        out["up_rc"] = rc
        st = run.load_state()
        out["pids"] = st["pids"]
        if rc != 0:
            return 2
        bridges = [nvbridge.NvBridge.connect(i, wait_s=120) for i in range(a.n)]
        for i, b in enumerate(bridges):
            b.wait_event("autoload.done", timeout_s=300)
        out["logs"] = {i: grep_log(i) for i in range(a.n)}
        deadline = time.time() + a.minutes * 60
        allok = True
        while True:
            row = {"t": round(time.time() - t_start, 1), "sys": winproc.system_memory(), "cpus": __import__("os").cpu_count()}
            for i, b in enumerate(bridges):
                t0 = time.time()
                try:
                    p = b.ping()
                    row[i] = {"ok": True, "ms": round((time.time() - t0) * 1000, 1), "frames": p.get("frames"),
                              "windows": window_states(int(st["pids"][str(i)])),
                              **winproc.proc_stats(int(st["pids"][str(i)]))}
                except Exception as e:  # noqa: BLE001
                    row[i] = {"ok": False, "err": f"{type(e).__name__}: {e}"}
                    allok = False
                allok = allok and winproc.pid_alive(int(st["pids"][str(i)]))
            out["pings"].append(row)
            print(json.dumps(row), flush=True)
            if time.time() >= deadline:
                break
            time.sleep(a.every)
        for b in bridges:
            try:
                b.close()
            except Exception:  # noqa: BLE001
                pass
        out["ok"] = allok
    finally:
        run.cmd_down(type("A", (), {})())  # kills our pids and releases the slot
        d = REPO / "runs" / (time.strftime("%Y-%m-%dT%H%M") + "-multismoke")
        d.mkdir(parents=True, exist_ok=True)
        (d / "multismoke.json").write_text(json.dumps(out, indent=1))
        print(json.dumps({k: v for k, v in out.items() if k != "pings"}, indent=1))
    return 0 if out["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
