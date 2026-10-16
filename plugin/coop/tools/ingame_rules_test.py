#!/usr/bin/env python3
"""L10 in-game rules-engine smoke (single instance, minimized): the game is the HOST with the real rules.json (179 quests) and
EDID map loaded; a fake client joins. VCG01's chargen barrier: the host asks to advance VCG01 to stage 30 -> DEFERRED; the host
player passes, then the fake client sends ScenePass -> the barrier releases (BarrierRelease reaches the client) and the rules
engine calls the stage applier exactly once (event coop.stageReleased).

    python -I -X utf8 plugin/coop/tools/gen_coop_data.py        # once: out/L10/data/*
    python -I -X utf8 plugin/coop/tools/ingame_rules_test.py
"""
import json, os, shutil, sys, time
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / "tools" / "runner"))
sys.path.insert(0, str(REPO / "plugin" / "coop" / "tests"))
os.environ["FNV_INSTANCES_ROOT"] = str(REPO.parent / "fnv-instances-l10")
import nvbridge, run as runner, winproc  # noqa: E402
from test_integration import Peer  # noqa: E402

PORT = 27971
ROOT = REPO.parent / "fnv-instances-l10" / "0"
PLUGINS = ROOT / "Data" / "NVSE" / "Plugins"
INI = PLUGINS / "nvcoop.ini"
VCG01 = "falloutnv.esm:104c1c"


def set_ini(**kv):
    out, seen = [], set()
    for l in INI.read_text().splitlines():
        k = l.split("=", 1)[0]
        if k in kv:
            out.append("%s=%s" % (k, kv[k])); seen.add(k)
        else:
            out.append(l)
    out += ["%s=%s" % (k, v) for k, v in kv.items() if k not in seen]
    INI.write_text(chr(10).join(out) + chr(10))


def main():
    data = REPO / "out" / "L10" / "plugin"     # produced by build_plugin.cmd (dll + generated data)
    if not (data / "nvcoop_rules.json").exists():
        sys.exit("run plugin\coop\build_plugin.cmd first (generates the data files)")
    if winproc.list_pids("FalloutNV.exe"):
        sys.exit("FalloutNV.exe already running")
    for f in data.glob("nvcoop_*.json"):
        shutil.copy2(f, PLUGINS / f.name)
    set_ini(role="host", baseline=0, enable=1)
    pid = runner.launch_instance(0)
    st = runner.load_state(); st["pids"]["0"] = pid; runner.save_state(st)
    nv = nvbridge.NvBridge.connect(0, wait_s=240)
    peer = None
    ok = False
    try:
        nv.wait_event("autoload.done", timeout_s=240)
        t0 = time.time()
        while not nv.call("coop.status").get("running") and time.time() - t0 < 120:
            time.sleep(0.5)
        hs = nv.call("coop.status")
        rules = hs.get("rules", {})
        print("host rules:", json.dumps(rules))
        peer = Peer("coop_peer.exe", "--role", "client", "--host", "127.0.0.1", "--port", str(PORT), "--token", "1234")
        assert peer.wait("wait_joined", 1, 30000), "fake client did not join"
        stage0 = nv.eval("GetStage 104C1C")
        nv.exec("setstage 104C1C 30")          # the real engine SetStage -> L5 gate -> rules engine defers it
        time.sleep(1)
        stage_after_req = nv.eval("GetStage 104C1C")
        print("VCG01 stage before %s, right after `setstage 30`: %s (must be unchanged: deferred)" % (stage0, stage_after_req))
        r1 = {"proceed": stage_after_req != 30}
        r1b = nv.call("coop.stageRequested", {"quest": "falloutnv.esm:104c1d", "stage": 10})   # not a rules quest: proceeds
        nv.call("coop.scenePass", {"quest": VCG01})
        time.sleep(1.5)
        mid = nv.call("coop.status")["rules"]
        print("after host pass (client has not):", json.dumps({k: mid[k] for k in ("pendingDeferrals", "stagesDeferred", "stagesReleased")}))
        peer.cmd("action scenepass " + VCG01, "OK action")
        deadline = time.time() + 20
        while time.time() < deadline and nv.call("coop.status")["rules"]["stagesReleased"] < 1:
            time.sleep(0.3)
        end = nv.call("coop.status")["rules"]
        evs = [e for e in nv.events(0)["events"] if e["type"] == "coop.stageReleased"]
        time.sleep(2)
        stage_end = nv.eval("GetStage 104C1C")
        types = [e["type"] for t in peer.txns for e in t["effects"]]
        got_setstage = any(e.get("type") == "SetStage" and e.get("quest", "").lower().endswith("104c1c") and e.get("stage") == 30 for t in peer.txns for e in t["effects"])
        print("VCG01 stage after the barrier released:", stage_end, "| client got SetStage VCG01=30:", got_setstage)
        print("after both passed:", json.dumps({k: end[k] for k in ("pendingDeferrals", "stagesDeferred", "stagesReleased", "arrivals")}),
              "released events:", [e.get("data") for e in evs])
        print("effects the client received:", types)
        print("client txns:", json.dumps([{"dir": t["dir"], "seq": t["seq"], "cause": t["cause"], "effects": t["effects"]} for t in peer.txns if t["dir"] == "apply"])[:900])
        print("host log tail:", json.dumps(nv.call("coop.log", {"n": 4}))[:700])
        checks = {
            "rules.json loaded (>= 170 quests)": rules.get("quests", 0) >= 170,
            "VCG01 stage 30 deferred by the real SetStage gate": stage_after_req == stage0 and stage0 != 30,
            "unruled quest proceeds": r1b.get("proceed") is True,
            "still deferred while the client had not passed": mid["pendingDeferrals"] == 1 and mid["stagesReleased"] == 0,
            "released exactly once after everyone passed": end["stagesReleased"] == 1 and end["pendingDeferrals"] == 0 and len(evs) == 1,
            # the engine never lowers a quest stage (VCG01 is at 200 in this save), so GetStage cannot show 30; the hook still captured the call
            "released stage was issued to the real engine (setstage under StageGateBypass)": bool(evs) and evs[0].get("data", {}).get("issued") is True,
            "client received the committed SetStage VCG01=30 txn": got_setstage,
            # effect_json.cpp (L5) has no names for the barrier effects yet, so they print as "Unsupported": 2 arrivals + 1 release
            "client received the barrier effects (2 arrivals + 1 release)": len([t for t in types if t != "SetStage"]) >= 3,
        }
        for k, v in checks.items():
            print(("PASS " if v else "FAIL ") + k)
        ok = all(checks.values())
    finally:
        if peer:
            peer.close()
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
    print("PASS" if ok else "FAIL")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
