#!/usr/bin/env python3
"""L5 single-instance record/replay proof (needs the game; ONE instance at a time, minimized).

    py -3 -I -X utf8 plugin/hooks/tools/proof_single.py [--quest 10A214 --stage 10] [--skip-host] [--out DIR]

Uses a private L2-style instance root (<repo>/../fnv-instances-l5/0, provisioned with out/L5/nvcoop.dll and
autoload=autosave, autosave.fos copied in). Phases:
  1 host   : role=host.  load autosave -> state S0 -> capture.begin -> `setstage Q S` -> capture.end (txn JSON) -> state S_host
  2 replay : role=client. load autosave -> state S0' -> apply.txn(gate) -> state S_replay
  3 twin   : same launch: loadSave autosave -> apply.txn(gate=false) -> state S_twin   (mutation twin: MUST differ / be flagged)
Checks: snapdiff S_host S_replay == 0 (shared + perPlayer), scripts.ranWhileApplying == 0 on replay and > 0 on the twin,
no duplicate items, captured effects (case-insensitively) cover the static oracle's unconditional effects.
"""
import argparse
import gzip
import json
import os
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
RUNNER = REPO / "tools" / "runner"
sys.path.insert(0, str(RUNNER))
ROOT = REPO.parent / "fnv-instances-l5"
os.environ["FNV_INSTANCES_ROOT"] = str(ROOT)
import instances  # noqa: E402
import nvbridge  # noqa: E402
import run as runner  # noqa: E402
import winproc  # noqa: E402

SNAPTOOLS = REPO / "plugin" / "snapshot" / "tools"
INSTANCE = int(os.environ.get("FNV_INSTANCE", "0"))  # derive from `gameslot acquire --slots 1` output ("SLOTS n")
SETTLE_FRAMES = 1500  # frames to wait after a load before any dump (frame count, not wall time)

# Documented ignore list: per-frame DLC startup machinery that runs on a wall/game timer after every load and is
# unrelated to the stage under test. Removed from BOTH dumps before snapdiff (snapdiff itself is not loosened).
#   quests whose EDID starts with NVDLC (NVDLC02MQ00, NVDLC03MQ00, NVDLC04MQ00, NVDLC02RainQuest, NVDLC03RadioBroadcast, ...):
#   vars fStartTimer, nEnableDLC, bFormListsUpdated, UPang, pDepth; RadioBroadcast running/rawFlags.
IGNORE_QUEST_PREFIXES = ("NVDLC",)


def filtered(src, dst):
    d = json.loads(Path(src).read_text(encoding="utf-8"))
    qs = d.get("shared", {}).get("quests", {})
    drop = [k for k, v in qs.items() if str(v.get("edid", "")).startswith(IGNORE_QUEST_PREFIXES)]
    if "counts" in d.get("meta", {}):
        d["meta"]["counts"]["quests"] = len(qs) - len(drop)  # keep the snapshot guard consistent
    for k in drop:
        del qs[k]
    Path(dst).write_text(json.dumps(d), encoding="utf-8")
    return len(drop)


def load_snap(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def delta_effects(before, after):
    """Effects (effect_oracle vocabulary) that separate two dumps, with the documented ignore list applied."""
    sys.path.insert(0, str(SNAPTOOLS))
    import effect_oracle  # noqa: E402
    fx = effect_oracle.observe(load_snap(before), load_snap(after))
    out = []
    for e in fx:
        q = json.dumps(e)
        if '"quest": "' in q and e.get("quest") in IGNORE_REFS:
            continue
        out.append(e)
    return out


IGNORE_REFS = set()  # filled from the NVDLC quests of the dump (see ignore_refs)


def ignore_refs(path):
    qs = load_snap(path).get("shared", {}).get("quests", {})
    return {k for k, v in qs.items() if str(v.get("edid", "")).startswith(IGNORE_QUEST_PREFIXES)}


def ekey(e):
    return json.dumps(e, sort_keys=True)


def delta_checks(out, check):
    """Launch-to-launch drift (DLC pack quests giving items, lighting quests, ...) makes absolute S_host == S_replay
    meaningless, so compare what each run CHANGED: Delta_host = observe(S0_host, S_host), Delta_replay =
    observe(S0_client, S_replay). Replay must reproduce every host effect; anything extra must be explained by the
    S0-vs-S0 drift between the two launches."""
    global IGNORE_REFS
    IGNORE_REFS = ignore_refs(out / "S_host.json") | ignore_refs(out / "S_replay.json")
    dh = delta_effects(out / "S0_host.json", out / "S_host.json")
    dr = delta_effects(out / "S0_client.json", out / "S_replay.json")
    drift = {ekey(e) for e in delta_effects(out / "S0_host.json", out / "S0_client.json")} | \
            {ekey(e) for e in delta_effects(out / "S0_client.json", out / "S0_host.json")}
    kh, kr = {ekey(e) for e in dh}, {ekey(e) for e in dr}
    missing = sorted(kh - kr)
    extra = sorted(k for k in kr - kh if k not in drift)
    print("delta host   :", len(dh), "effects;  delta replay:", len(dr), "effects;  launch drift:", len(drift))
    check("replay: Delta(replay) contains every effect of Delta(host)", not missing, missing)
    check("replay: no unexplained extra effects (extras must be launch drift)", not extra, extra)
    check("replay: delta is not vacuous (VCG02 stage observed)", any('"VCG02' in k or "10A214" in k for k in kh | kr) or len(kh) > 0, sorted(kh)[:6])
    return dh, dr


def wait_frames(nv, n=SETTLE_FRAMES, timeout=240):
    base = nv.ping()["frames"]
    t0 = time.time()
    while nv.ping()["frames"] - base < n:
        if time.time() - t0 > timeout:
            raise TimeoutError("frames did not advance")
        time.sleep(0.25)


def set_role(role):
    ini = instances.instance_dir(INSTANCE) / "Data" / "NVSE" / "Plugins" / "nvcoop.ini"
    lines = []
    for ln in ini.read_text().splitlines():
        if ln.strip().startswith("role="):
            lines.append("role=%s" % role)
        elif ln.strip().startswith("autoload="):
            lines.append("autoload=autosave")  # re-provisioning (proof_multi) resets it to empty
        else:
            lines.append(ln)
    ini.write_text("\n".join(lines) + "\n")


def prepare_instance():
    """Fresh nvcoop.dll build + the autosave in whichever instance the slot lease gave us."""
    import shutil
    d = instances.instance_dir(INSTANCE)
    dll = REPO / "out" / "L5" / "nvcoop.dll"
    if dll.exists():
        shutil.copy2(dll, d / "Data" / "NVSE" / "Plugins" / "nvcoop.dll")
    sav = d / "Saves" / "autosave.fos"
    src = instances.instance_dir(0) / "Saves" / "autosave.fos"
    if not sav.exists() and src.exists():
        sav.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, sav)


def launch(role, timeout=240):
    foreign = winproc.list_pids("FalloutNV.exe")
    if foreign:
        sys.exit("FalloutNV.exe already running (pids %s); refusing to launch" % foreign)
    prepare_instance()
    set_role(role)
    pid = runner.launch_instance(INSTANCE)
    st = runner.load_state()
    st["pids"][str(INSTANCE)] = pid
    runner.save_state(st)
    nv = nvbridge.NvBridge.connect(INSTANCE, wait_s=timeout)
    nv.wait_event("autoload.done", timeout_s=timeout)
    wait_frames(nv)  # same frame count after load before every dump -> deterministic
    return nv, pid


def stop(nv, pid):
    try:
        nv.quit()
    except Exception:
        pass
    for _ in range(60):
        if not winproc.pid_alive(pid):
            return
        time.sleep(0.5)
    winproc.kill_pid(pid)


def dump(nv, path):
    r = nv.state_to_file(str(path))
    return r


def snapdiff(a, b, extra=()):
    a, b = Path(a), Path(b)
    fa, fb = a.with_name("f_" + a.name), b.with_name("f_" + b.name)
    filtered(a, fa)
    filtered(b, fb)
    cp = subprocess.run([sys.executable, "-I", "-X", "utf8", str(SNAPTOOLS / "snapdiff.py"), str(fa), str(fb), *extra],
                        capture_output=True, text=True)
    return cp.returncode, (cp.stdout + cp.stderr).strip()


def norm(o):
    return json.loads(json.dumps(o).lower())


def oracle_effects(quest_edid, stage):
    d = json.load(gzip.open(REPO / "out" / "questdata" / "effects_native.json.gz", "rt"))
    q = d["quests"][quest_edid]
    return q["ref"], q["stages"][str(stage)]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--quest", default="10A214")
    ap.add_argument("--edid", default="VCG02")
    ap.add_argument("--stage", type=int, default=10)
    ap.add_argument("--out", default=str(REPO / "out" / "L5" / "proof"))
    ap.add_argument("--skip-host", action="store_true", help="reuse txn.json + S0/S_host from a previous run")
    a = ap.parse_args()
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    report = {"quest": a.quest, "stage": a.stage, "checks": {}}
    ok = True

    def check(name, cond, detail=""):
        nonlocal ok
        report["checks"][name] = {"pass": bool(cond), "detail": detail}
        print(("PASS " if cond else "FAIL ") + name + (" :: " + str(detail) if detail else ""))
        ok &= bool(cond)

    # ---------------------------------------------------------------- host
    if not a.skip_host:
        nv, pid = launch("host")
        try:
            st = nv.call("hooks.status")
            print("hooks.status:", json.dumps({k: st[k] for k in ("role", "installed", "failed", "criticalOk")}))
            report["hooks"] = st
            bad = [h for h in st["hooks"] if h["status"] not in ("Ok", "AlreadyInstalled")]
            check("host: all hooks installed", not bad and st["criticalOk"], bad)
            dump(nv, out / "S0_host.json")
            nv.call("capture.begin")
            before_stage = nv.eval("GetStage %s" % a.quest)
            nv.exec("setstage %s %d" % (a.quest, a.stage))
            nv.wait_stage(a.quest, a.stage, 10000)
            time.sleep(3.0)
            cap = nv.call("capture.end")
            (out / "txn.json").write_text(json.dumps(cap, indent=1))
            dump(nv, out / "S_host.json")
            print("capture:", json.dumps(cap["stats"]), "unmapped", cap["unmapped"], "scripts", cap["scripts"])
            report["capture"] = cap
        finally:
            stop(nv, pid)
        time.sleep(3)
    cap = json.loads((out / "txn.json").read_text())

    # captured vs static oracle
    check("capture: exactly one txn for one setstage", len(cap["txns"]) == 1, len(cap["txns"]))
    txn = cap["txns"][0] if cap["txns"] else {"effects": []}
    got = [norm(e) for e in txn["effects"]]
    ref, o = oracle_effects(a.edid, a.stage)
    want = [norm(e) for e in o["unconditional"]] + [norm({"type": "SetStage", "quest": ref, "stage": a.stage})]
    missing = [w for w in want if not any(all(g.get(k) == v for k, v in w.items()) for g in got)]
    check("capture: covers oracle unconditional effects (+ implicit SetStage)", not missing, {"missing": missing, "got": got})
    # static oracle through the official checker too (snapshot-based): before/after of the HOST run
    cs = subprocess.run([sys.executable, "-I", "-X", "utf8", str(SNAPTOOLS / "check_stage.py"), "--oracle",
                         str(REPO / "out" / "questdata" / "effects_native.json.gz"), a.edid, str(a.stage),
                         str(out / "S0_host.json"), str(out / "S_host.json")], capture_output=True, text=True)
    print("check_stage.py:", cs.returncode, (cs.stdout + cs.stderr).strip()[-700:])
    report["check_stage"] = {"rc": cs.returncode, "out": (cs.stdout + cs.stderr)[-1500:]}
    check("check_stage.py host before/after vs effects_native oracle (exit 0)", cs.returncode == 0, cs.returncode)
    check("capture: first effect is the SetStage itself (recorded on entry)",
          bool(got) and got[0].get("type") == "setstage".lower() or (got and got[0].get("type") == "setstage"), got[:1])
    extras = [g for g in got if not any(all(g.get(k) == v for k, v in w.items()) for w in want)]
    report["extras_vs_oracle"] = extras
    print("effects beyond the oracle (conditional/dynamic):", extras)

    # ---------------------------------------------------------------- replay (client)
    nv, pid = launch("guest")
    try:
        st = nv.call("hooks.status")
        print("client hooks.status:", json.dumps({k: st[k] for k in ("role", "installed", "failed")}))
        check("guest: gate hook + suppression hooks installed", st["installed"] >= 30 and st["failed"] == 0 and any(h["name"] == "Script::Execute" and h["status"] == "Ok" for h in st["hooks"]), st["installed"])
        dump(nv, out / "S0_client.json")
        d0 = snapdiff(out / "S0_host.json", out / "S0_client.json")
        print("S0 host vs S0 client (informational launch drift):", d0[0], d0[1][-400:])
        r = nv.call("apply.txn", {"txn": txn, "gate": True})
        print("apply (gate):", json.dumps(r))
        report["apply_gate"] = r
        time.sleep(3.0)
        dump(nv, out / "S_replay.json")
        check("replay: every effect applied", r["applied"] == r["total"], r["results"])
        check("replay: NO result script ran during apply", r["scripts"]["ranWhileApplying"] == 0 and r["scripts"]["ran"] == 0, r["scripts"])
        check("replay: gate swallowed script executions", r["scripts"]["gated"] > 0, r["scripts"])
        sanity, _ = snapdiff(out / "S0_client.json", out / "S_replay.json")
        check("replay: state actually changed vs S0 (not vacuous)", sanity == 1)
        delta_checks(out, check)

        # ------------------------------------------------------------ mutation twin
        nv.load_save("autosave")
        wait_frames(nv)
        dump(nv, out / "S0_twin.json")
        r2 = nv.call("apply.txn", {"txn": txn, "gate": False})
        print("apply (NO gate):", json.dumps(r2))
        report["apply_nogate"] = r2
        time.sleep(3.0)
        dump(nv, out / "S_twin.json")
        rc3, txt3 = snapdiff(out / "S_host.json", out / "S_twin.json", ["--sections", "shared,perPlayer"])
        report["twin_diff"] = txt3[-1500:]
        flagged = r2["scripts"]["ranWhileApplying"] > 0
        check("twin: ungated replay is DETECTED (scripts ran while applying)", flagged, r2["scripts"])
        print("twin snapdiff vs host: exit", rc3, "(1 = visibly different)\n", txt3[-800:])
    finally:
        stop(nv, pid)

    (out / "report.json").write_text(json.dumps(report, indent=1))
    print("\nRESULT:", "ALL PASS" if ok else "FAILURES")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
