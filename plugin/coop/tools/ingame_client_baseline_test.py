#!/usr/bin/env python3
"""L10 in-game test with the GAME AS THE CLIENT (single instance, minimized): the real engine joins a fake host whose
world deliberately differs from the game's, so L5's applyForce runs in the real engine and the gameplay lock is exercised.

 1. needs out/L10/ingame_baseline/host_baseline.bin (written by ingame_baseline_test.py: a real dump of this save)
 2. fake host (coop_peer --role host --baseline-file ... --mutate 1): same world with 17 stages/globals/running flags changed
 3. game starts as role=client, baseline=1: dumps ITS world, joins, receives the diff, force-applies it
 4. checks (all via the bridge): coop.status client.baselineDone, events coop.controlsLocked true then false,
    GetStage / GetQuestRunning of every changed quest equal the fake host's values, no reject.

    python -I -X utf8 plugin/coop/tools/ingame_client_baseline_test.py
"""
import json, os, sys, time
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / "tools" / "runner"))
sys.path.insert(0, str(REPO / "plugin" / "coop" / "tests"))
os.environ["FNV_INSTANCES_ROOT"] = str(REPO.parent / "fnv-instances-l10")
import nvbridge, run as runner, winproc  # noqa: E402
from test_integration import Peer  # noqa: E402

PORT = 27971
INI = REPO.parent / "fnv-instances-l10" / "0" / "Data" / "NVSE" / "Plugins" / "nvcoop.ini"


def set_ini(**kv):
    lines = INI.read_text().splitlines()
    seen = set()
    out = []
    for l in lines:
        k = l.split("=", 1)[0]
        if k in kv:
            out.append("%s=%s" % (k, kv[k])); seen.add(k)
        else:
            out.append(l)
    for k, v in kv.items():
        if k not in seen:
            out.append("%s=%s" % (k, v))
    INI.write_text(chr(10).join(out) + chr(10))


PLUGIN_INDEX = {}


def hexid(ref):   # "deadmoney.esm:000fbf" -> runtime FormID using the plugin's load-order index in the running game
    plugin, local = ref.rsplit(":", 1)
    return "%08X" % ((PLUGIN_INDEX[plugin.lower()] << 24) | int(local, 16))


def main():
    out = REPO / "out" / "L10" / "ingame_client_baseline"
    out.mkdir(parents=True, exist_ok=True)
    bfile = REPO / "out" / "L10" / "ingame_baseline" / "host_baseline.bin"
    if not bfile.exists():
        sys.exit("run ingame_baseline_test.py first (needs %s)" % bfile)
    if winproc.list_pids("FalloutNV.exe"):
        sys.exit("FalloutNV.exe already running")
    host = Peer("coop_peer.exe", "--role", "host", "--bind", "127.0.0.1", "--port", str(PORT), "--baseline", "1",
                "--baseline-file", str(bfile), "--mutate", "1")
    mut = json.loads(host.cmd("mutation", "MUTATION")[len("MUTATION "):])
    print("fake host world differs from the game's saved world in %d entries (+ karma)" % mut["mutatedEntries"])
    set_ini(role="client", baseline=1, enable=1, token=4242, forward_activate=0)
    pid = runner.launch_instance(0)
    st = runner.load_state(); st["pids"]["0"] = pid; runner.save_state(st)
    nv = nvbridge.NvBridge.connect(0, wait_s=240)
    ok = False
    try:
        nv.wait_event("autoload.done", timeout_s=240)
        for i, name in enumerate(nv.plugins()):
            PLUGIN_INDEX[name.lower()] = i
        t0 = time.time()
        cs = {}
        while time.time() - t0 < 180:
            cs = nv.call("coop.status")
            if cs.get("client", {}).get("baselineDone") or cs.get("client", {}).get("rejectReason"):
                break
            time.sleep(0.5)
        c = cs.get("client", {})
        print("game client status:", json.dumps(c))
        time.sleep(2)
        evs = [e for e in nv.events(0)["events"] if e["type"] == "coop.controlsLocked"]
        print("lock events:", [e.get("data") for e in evs])
        base = nv.ping()["frames"]
        while nv.ping()["frames"] - base < 120:
            time.sleep(0.2)
        print("effect tally (kind effect:status -> n):", json.dumps(nv.call("coop.status").get("effectTally")))
        print("first apply errors:", json.dumps(nv.call("coop.status").get("firstErrors")))
        bad = []
        for ref, want in mut["stagesTo"].items():
            got = nv.eval("GetStage %s" % hexid(ref))
            if int(got) != int(want):
                bad.append(("stage", ref, want, got))
        for ref, want in mut["runningTo"].items():
            got = nv.eval("GetQuestRunning %s" % hexid(ref))
            if bool(got) != bool(want):
                bad.append(("running", ref, want, got))
        print("verified %d quest stages and %d running flags in the real engine; wrong: %s" % (
            len(mut["stagesTo"]), len(mut["runningTo"]), bad))
        applied_results = {}
        lg = nv.call("coop.log", {"n": 4096})
        dirs = {}
        for t in lg.get("txns", []):
            dirs[t.get("dir")] = dirs.get(t.get("dir"), 0) + 1
        print("coop.log entries by dir:", dirs, "total", lg.get("count"))
        for t in [t for t in lg.get("txns", []) if t.get("dir") == "baseline"][:30]:
            print("  baseline:", json.dumps(t["effects"])[:160], t.get("results"))
        by = {}
        for t in lg.get("txns", []):
            if t.get("dir") == "apply" and t.get("seq") == 0:
                for e, r in zip(t["effects"], t.get("results", [])):
                    k = (e["type"], r.split(":")[0]); by[k] = by.get(k, 0) + 1
        print("  seq0 'apply' entries (snapshot/other):", by)
        for t in lg.get("txns", []):
            if t.get("dir") == "baseline":
                for e, r in zip(t["effects"], t.get("results", [])):
                    applied_results.setdefault(e["type"], {}).setdefault(r.split(":")[0], 0)
                    applied_results[e["type"]][r.split(":")[0]] += 1
        print("baseline effect results in the engine:", json.dumps(applied_results))
        (out / "result.json").write_text(json.dumps({"status": c, "lockEvents": evs, "wrong": bad, "results": applied_results, "mutation": mut}, indent=1))
        locked_then_unlocked = [str(e.get("data")) for e in evs]
        checks = {
            "client baseline completed, not rejected": bool(c.get("baselineDone")) and not c.get("rejectReason") and not c.get("syncing"),
            "gameplay controls locked then unlocked": any(e.get("data", {}).get("locked") is True for e in evs) and any(e.get("data", {}).get("locked") is False for e in evs),
            "real engine now has the host's stages and running flags": not bad and len(mut["stagesTo"]) > 0,
        }
        for k, v in checks.items():
            print(("PASS " if v else "FAIL ") + k)
        ok = all(checks.values())
    finally:
        host.close()
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
        set_ini(role="host", baseline=0)
    print("PASS" if ok else "FAIL")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
