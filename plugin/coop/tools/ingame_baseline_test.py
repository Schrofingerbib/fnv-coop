#!/usr/bin/env python3
"""L10 in-game baseline join test (single game instance, minimized). The game is the HOST with baseline=1 (real L7 engine
dump of the shared world). The dump is exported to a file; a fake client (coop_peer) loads it, deliberately CHANGES a
counted set of entries (stages, globals changed/removed, objectives, quest running flags, karma) and joins with that
world. The host must send exactly the differences, and the client's world must end equal to the host's.

    python -I -X utf8 plugin/coop/tools/ingame_baseline_test.py
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
EXPLAIN = [("102037", 7), ("104c1c", 4), ("10c721", 9), ("10c721", 24), ("157b4e", 10), ("168a86", 2)]   # the 6 vars of interest


def story_refs():
    """lower-cased refs of L3's story quests (scenario kind == 'story')"""
    refs = set()
    for p in (REPO / "out" / "questdata" / "scenarios").glob("*.json"):
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            continue
        if d.get("kind") == "story":
            refs.add(d["ref"].lower())
    return refs


def main():
    fresh = "--fresh" in sys.argv   # drift check: client joins with a FRESH dump taken later (no deliberate changes)
    out = REPO / "out" / "L10" / "ingame_baseline"
    out.mkdir(parents=True, exist_ok=True)
    foreign = winproc.list_pids("FalloutNV.exe")
    if foreign:
        sys.exit("FalloutNV.exe already running (pids %s); refusing to launch" % foreign)
    ini = REPO.parent / "fnv-instances-l10" / "0" / "Data" / "NVSE" / "Plugins" / "nvcoop.ini"
    lines = [("baseline=1" if l.startswith("baseline=") else l) for l in ini.read_text().splitlines()]
    ini.write_text(chr(10).join(lines) + chr(10))
    pid = runner.launch_instance(0)
    st = runner.load_state(); st["pids"]["0"] = pid; runner.save_state(st)
    nv = nvbridge.NvBridge.connect(0, wait_s=240)
    peer = None
    ok = False
    try:
        nv.wait_event("autoload.done", timeout_s=240)
        base = nv.ping()["frames"]
        while nv.ping()["frames"] - base < 600:
            time.sleep(0.25)
        t0 = time.time()
        while not nv.call("coop.status").get("running") and time.time() - t0 < 180:
            time.sleep(0.5)
        hs = nv.call("coop.status")
        print("host started:", hs.get("running"), "error:", hs.get("error"), "start took ~%.1fs after load" % (time.time() - t0))
        assert hs.get("running"), hs
        bfile = out / "host_baseline.bin"
        t1 = time.time()
        exp = nv.call("coop.baseline.export", {"path": str(bfile)}, timeout_s=120)
        print("host baseline export (%.1fs):" % (time.time() - t1), json.dumps(exp))
        cfile = bfile
        if fresh:
            cfile = out / "fresh_dump.bin"
            nv.call("coop.baseline.export", {"path": str(cfile), "fresh": True}, timeout_s=120)
        peer = Peer("coop_peer.exe", "--role", "client", "--host", "127.0.0.1", "--port", str(PORT), "--token", "888",
                    "--baseline", "1", "--baseline-file", str(cfile), "--mutate", "0" if fresh else "1", "--explain", ",".join("falloutnv.esm:%s:%d" % (q, v) for q, v in EXPLAIN), *(["--verify-file", str(bfile)] if fresh else []))
        for q, v in EXPLAIN:
            try:
                print("host explain %s:%d ->" % (q, v), json.dumps(nv.call("coop.explain", {"quest": "falloutnv.esm:" + q, "var": v, "slot": 1})).replace("\n", " | ")[:400])
            except Exception as e:
                print("host explain failed:", e)
        mut = json.loads(peer.cmd("mutation", "MUTATION")[len("MUTATION "):])
        print("client deliberately differs in %d entries (+ karma)" % mut.get("mutatedEntries", 0))
        done = peer.wait("wait_baseline", 1, 120000)
        for l in peer.log:
            if l.startswith("EXPLAIN "):
                print("client", l[:400])
        ver = json.loads(peer.cmd("verify_baseline", "VERIFY")[len("VERIFY "):])
        cs = peer.status()["client"]
        hs2 = nv.call("coop.status")
        base_txns = [t for t in peer.txns if t["dir"] == "baseline"]
        got = [e for t in base_txns for e in t["effects"]]
        types = {}
        for e in got:
            types[e["type"]] = types.get(e["type"], 0) + 1
        print("client baseline: done=%s effectsApplied=%s kept=%s reverted=%s chunks=%s; received effect types=%s" % (
            done, cs["baselineEffects"], cs["baselineKept"], cs["baselineReverted"], cs["baselineChunks"], json.dumps(types)))
        print("verify vs host original (stages/globals/objectives/running/karma): mismatches=%d %s" % (ver["mismatches"], ver["first"]))
        print("host stats:", json.dumps({k: hs2["host"][k] for k in ("welcomes", "snapshots", "replays", "badMessages")}))
        res = {"mutation": mut, "types": types, "client": cs, "verify": ver, "export": exp, "done": done}
        (out / "result.json").write_text(json.dumps(res, indent=1))
        if fresh:
            (out / "drift_effects.json").write_text(json.dumps(got, indent=1))
            story = story_refs()
            loud, explained, other = [], [], []
            for e in got:
                t = e["type"]
                ref = (e.get("owner") or e.get("quest") or e.get("global") or "").lower()
                if t == "SetScriptVar":
                    (explained if ref in story else other).append(e)
                elif t in ("SetStage", "SetObjective", "StartQuest", "StopQuest", "CompleteQuest", "CompleteAllObjectives"):
                    (loud if ref in story else other).append(e)
                else:
                    other.append(e)
            print("DRIFT CHECK: fresh dump vs host baseline, joined ~%.0fs later: %d effects (budget 100); client mismatches vs host baseline: %d %s" % (
                time.time() - t0, len(got), ver["mismatches"], ver["first"]))
            print("  story-quest script vars (explained, host-authoritative):", len(explained))
            for e in explained:
                print("    ", json.dumps(e))
            print("  non-story quest/var/faction effects (DLC startup etc.):", len(other))
            for e in other:
                print("    ", json.dumps(e))
            if loud:
                print("  !!! STORY QUEST STATE DISAGREES AFTER LOAD (host and client legitimately differ on story state):")
                for e in loud:
                    print("    ", json.dumps(e))
            checks = {"client converged to the host baseline (0 mismatches)": done and ver["mismatches"] == 0,
                      "drift within budget (<=100 effects)": len(got) <= 100,
                      "no story-quest stage/objective/running drift": not loud}
            for k, v in checks.items():
                print(("PASS " if v else "FAIL ") + k)
            ok = all(checks.values())
            print("PASS" if ok else "FAIL")
            sys.exit(0 if ok else 1)
        expected = mut["mutatedEntries"] + 1  # + the absolute-karma delta
        # Invariant (coordinator): every script var / global the joiner receives BEYOND the deliberate mutations must be a key the host
        # itself committed after its baseline (state polling -> StateReplicator). An extra with no host commit = the host world changed
        # without anything being captured/replicated (an L5 polling gap) and the test FAILS, listing the vars.
        log = nv.call("coop.log", {"n": 4096}).get("txns", [])
        committed = set()
        for t in log:
            if t.get("dir") == "commit":
                for e in t["effects"]:
                    if e["type"] == "SetScriptVar":
                        committed.add(("var", e["owner"].lower(), e["var"]))
                    elif e["type"] == "SetGlobal":
                        committed.add(("global", e["global"].lower()))
        mutated_globals = {k[len("global:"):].lower() for k in mut["changed"] if k.startswith("global:")}
        extras = []
        for e in got:
            if e["type"] == "SetScriptVar":
                extras.append(("var", e["owner"].lower(), e["var"]))
            elif e["type"] == "SetGlobal" and e["global"].lower() not in mutated_globals:
                extras.append(("global", e["global"].lower()))
        uncaptured = [x for x in extras if x not in committed]
        (out / "baseline_effects.json").write_text(json.dumps(got, indent=1))
        for e in got:
            if e["type"] == "SetScriptVar" and ("var", e["owner"].lower(), e["var"]) in uncaptured:
                print("   uncaptured: %s v%d host value sent = %r" % (e["owner"], e["var"], e["value"]))
        print("host live commits since its baseline: %d keys; joiner got %d extra var/global entries, %d with NO host commit: %s" % (
            len(committed), len(extras), len(uncaptured), uncaptured[:20]))
        st_poll = nv.call("coop.status").get("replicator")
        print("host replicator:", json.dumps(st_poll))
        expected_total = expected + len(extras)
        total = (mut["totalStages"] + mut["totalGlobals"] + mut["totalObjectives"] + mut["totalQuestRunning"])
        checks = {
            "baseline completed": done and not cs["syncing"] and cs["baselineDone"],
            "host sent exactly the %d deliberate differences plus only host-committed var/global changes (%d)" % (expected, len(extras)): cs["baselineEffects"] == expected_total,
            "no uncaptured host var/global change (every extra has a host commit)": not uncaptured,
            "client world == host original (0 mismatches)": ver["mismatches"] == 0,
            "client rejected? no": cs["rejectReason"] == 0,
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
        lines = [("baseline=0" if l.startswith("baseline=") else l) for l in ini.read_text().splitlines()]
        ini.write_text(chr(10).join(lines) + chr(10))
    print("PASS" if ok else "FAIL")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
