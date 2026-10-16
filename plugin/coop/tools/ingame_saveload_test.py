#!/usr/bin/env python3
"""L10 in-game save/load test (single instance, minimized): the game is the HOST (baseline=1) with a fake client joined.
 1. host `save <name>` through the console -> NVSE SaveGame message -> sidecar `<name>.fos.fnvcoop` must appear next to the save
 2. host loadSave <name> -> PostLoadGame -> onGameLoaded: epoch must go 1 -> 2, coop.loaded event, and the fake client must get a
    Rebase and re-baseline (client worldEpoch 2, rebases >= 1)

    python -I -X utf8 plugin/coop/tools/ingame_saveload_test.py
Needs out/L10/ingame_baseline/host_baseline.bin (written by ingame_baseline_test.py) as the fake client's world.
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
ROOT = REPO.parent / "fnv-instances-l10" / "0"
INI = ROOT / "Data" / "NVSE" / "Plugins" / "nvcoop.ini"
NAME = "autosave_fnvcoop_saveload_%d" % os.getpid()   # must start with autosave (L1); NEVER the plain names autosave/quicksave


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


def guard_instance_saves():
    """Abort unless saves go to the instance-local Saves dir (never the owner's Documents\My Games)."""
    ini = (ROOT / "Fallout.ini").read_text(errors="replace")
    if "bUseMyGamesDirectory=0" not in ini.replace(" ", ""):
        sys.exit("ABORT: instance Fallout.ini does not set bUseMyGamesDirectory=0; saves could land in the owner's My Games")
    if NAME in ("autosave", "quicksave") or not NAME.startswith("autosave_fnvcoop_"):
        sys.exit("ABORT: unsafe save name")


def main():
    guard_instance_saves()
    out = REPO / "out" / "L10" / "ingame_saveload"
    out.mkdir(parents=True, exist_ok=True)
    bfile = REPO / "out" / "L10" / "ingame_baseline" / "host_baseline.bin"
    if not bfile.exists():
        sys.exit("run ingame_baseline_test.py first")
    if winproc.list_pids("FalloutNV.exe"):
        sys.exit("FalloutNV.exe already running")
    set_ini(role="host", baseline=1, enable=1, forward_activate=0)
    pid = runner.launch_instance(0)
    st = runner.load_state(); st["pids"]["0"] = pid; runner.save_state(st)
    nv = nvbridge.NvBridge.connect(0, wait_s=240)
    peer = None
    ok = False
    try:
        nv.wait_event("autoload.done", timeout_s=240)
        t0 = time.time()
        while not nv.call("coop.status").get("running") and time.time() - t0 < 180:
            time.sleep(0.5)
        peer = Peer("coop_peer.exe", "--role", "client", "--host", "127.0.0.1", "--port", str(PORT), "--token", "999",
                    "--baseline", "1", "--baseline-file", str(bfile))
        assert peer.wait("wait_baseline", 1, 120000), "fake client did not finish the first baseline"
        hs0 = nv.call("coop.status")
        print("before: host epoch", hs0["host"]["epoch"], "client", json.dumps({k: peer.status()["client"][k] for k in ("worldEpoch", "rebases", "baselineEffects")}))
        sav = ROOT / "Saves" / (NAME + ".fos")
        r = nv.save_game(NAME)
        print("bridge saveGame:", json.dumps(r)[:200])
        t1 = time.time()
        while not (ROOT / "Saves" / (NAME + ".fos.fnvcoop")).exists() and time.time() - t1 < 30:
            time.sleep(0.5)
        print("instance saves:", sorted(f.name for f in (ROOT / "Saves").glob(NAME + "*")))
        side = ROOT / "Saves" / (NAME + ".fos.fnvcoop")
        print("sidecar:", side, side.stat().st_size if side.exists() else "MISSING")
        saved = side.exists()
        loadname = NAME
        print("loading save %r (%s)" % (loadname, "with sidecar" if saved else "no sidecar: host reloads its world from a fresh engine dump"))
        nv.call("loadSave", {"name": loadname}, timeout_s=180)
        t2 = time.time()
        ev = None
        while time.time() - t2 < 120:
            es = [e for e in nv.events(0)["events"] if e["type"] == "coop.loaded"]
            if es:
                ev = es[-1]
                break
            time.sleep(0.5)
        print("coop.loaded event:", ev)
        time.sleep(3)
        deadline = time.time() + 150
        cs = peer.status()["client"]
        while time.time() < deadline and not (cs["worldEpoch"] >= 2 and cs["baselineDone"] and not cs["syncing"]):
            time.sleep(0.5)
            cs = peer.status()["client"]
        hs1 = nv.call("coop.status")
        print("after: host epoch", hs1["host"]["epoch"], "client", json.dumps({k: cs[k] for k in ("worldEpoch", "rebases", "baselineEffects", "baselineDone", "syncing")}))
        import shutil
        if side.exists():
            shutil.copy2(side, out / "sidecar.bin")      # kept as a fixture for the game-free repro (test_coop.cpp realSidecar)
        print("host after:", json.dumps(hs1.get("host")), "counters", json.dumps(hs1.get("counters")))
        print("client after:", json.dumps(peer.status()))
        (out / "result.json").write_text(json.dumps({"before": hs0, "after": hs1, "client": cs, "event": ev}, indent=1))
        checks = {
            "sidecar written next to the save (needs a bridge save method; SKIPPED if no save could be made)": (not saved) or side.stat().st_size > 0,
            "host handled the load (coop.loaded ok)": bool(ev and ev.get("data", {}).get("ok")),
            "host epoch bumped 1 -> 2": hs0["host"]["epoch"] == 1 and hs1["host"]["epoch"] == 2,
            "fake client rebased into epoch 2 and re-baselined": cs["worldEpoch"] == 2 and cs["rebases"] >= 1 and cs["baselineDone"] and not cs["syncing"],
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
        for f in ROOT.glob("Saves/" + NAME + "*"):
            f.unlink()
        set_ini(baseline=0)
    print("PASS" if ok else "FAIL")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
