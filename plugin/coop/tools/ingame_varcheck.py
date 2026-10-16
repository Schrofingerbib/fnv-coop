#!/usr/bin/env python3
"""Diagnostic (single instance): for quests whose script vars the joiner received without a host commit, compare
  (a) the baseline the host session uses, (b) the host session world now, (c) L5's live poller view (vars.dump).
    python -I -X utf8 plugin/coop/tools/ingame_varcheck.py"""
import json, os, sys, time
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / "tools" / "runner"))
os.environ["FNV_INSTANCES_ROOT"] = str(REPO.parent / "fnv-instances-l10")
import nvbridge, run as runner, winproc  # noqa: E402

QUESTS = {"falloutnv.esm:102037": [7], "falloutnv.esm:104c1c": [4], "falloutnv.esm:10c721": [9, 24], "falloutnv.esm:157b4e": [10], "falloutnv.esm:168a86": [2]}
INI = REPO.parent / "fnv-instances-l10" / "0" / "Data" / "NVSE" / "Plugins" / "nvcoop.ini"


def main():
    if winproc.list_pids("FalloutNV.exe"):
        sys.exit("game running")
    lines = [("baseline=1" if l.startswith("baseline=") else l) for l in INI.read_text().splitlines()]
    INI.write_text(chr(10).join(lines) + chr(10))
    pid = runner.launch_instance(0)
    st = runner.load_state(); st["pids"]["0"] = pid; runner.save_state(st)
    nv = nvbridge.NvBridge.connect(0, wait_s=240)
    try:
        nv.wait_event("autoload.done", timeout_s=240)
        t0 = time.time()
        while not nv.call("coop.status").get("running") and time.time() - t0 < 120:
            time.sleep(0.5)
        for rnd in range(3):
            if rnd:
                time.sleep(15)
            bv = nv.call("coop.baseline.vars", {"quests": list(QUESTS)})
            try:
                live = nv.call("vars.dump", {"quests": list(QUESTS)})
            except Exception as e:
                live = {"error": str(e)}
            print("--- round %d (%.0fs after start) ---" % (rnd, time.time() - t0))
            for q, idxs in QUESTS.items():
                b = bv.get(q, {})
                print(q, "vars", idxs)
                for i in idxs:
                    print("   var %-3d baseline=%s sessionWorld=%s" % (i, b.get("baseline", {}).get(str(i)), b.get("sessionWorld", {}).get(str(i))))
            print("vars.dump (L5 live poller view):", json.dumps(live)[:1500])
        print("replicator:", json.dumps(nv.call("coop.status").get("replicator")))
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
        lines = [("baseline=0" if l.startswith("baseline=") else l) for l in INI.read_text().splitlines()]
        INI.write_text(chr(10).join(lines) + chr(10))


if __name__ == "__main__":
    main()
