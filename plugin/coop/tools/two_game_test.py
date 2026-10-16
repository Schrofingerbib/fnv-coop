#!/usr/bin/env python3
"""L10 REAL two-game co-op test (host game + client game, both minimized). NOT RUN YET: needs the owner's
multi-instance approval. It refuses to launch unless --approved is passed.

    python -I -X utf8 plugin/coop/tools/two_game_test.py --variant live|late [--baseline] [--quest 10A214 --stage 10] [--approved]
    python -I -X utf8 plugin/coop/tools/two_game_test.py --dry-run          (provisions ini files, prints the plan, launches nothing)

Variants
  live : host and client games are both up and joined, THEN `setstage` on the host. The client game must show the same
         stage (GetStageDone) within the timeout, and snapdiff(host shared, client shared) must be clean.
  late : host runs `setstage` first, THEN the client game starts and joins; the story-log replay (or, with --baseline,
         the world-baseline transfer) must bring the client to the same stage.
Both: game launches go through tools/gameslot.py (lease owner L10) and every instance is started minimized/no-activate by
tools/runner/run.py launch_instance (no desktop input is ever sent). Private instance root ../fnv-instances-l10/{0,1};
instance 0 = host, instance 1 = client. Saves: autosave.fos copied from ../fnv-instances-l5/0/Saves.
Evidence written to out/L10/twogame/<variant>/ (S_host.json, S_client.json, snapdiff.txt, coop_status_*.json, result.json).
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
RUNNER = REPO / "tools" / "runner"
sys.path.insert(0, str(RUNNER))
ROOT = REPO.parent / "fnv-instances-l10"
os.environ["FNV_INSTANCES_ROOT"] = str(ROOT)
PORT = 27971
HOST_I, CLIENT_I = 10, 11   # private instance ids: own dirs AND own pipes (nvcoop-10 / nvcoop-11) so other lanes' instances 0/1 never collide
DLL = REPO / "out" / "L10" / "plugin" / "nvcoop.dll"
SNAPTOOLS = REPO / "plugin" / "snapshot" / "tools"
SETTLE_FRAMES = 1500
IGNORE_QUEST_PREFIXES = ("NVDLC",)   # same documented DLC start-up noise filter as L5's proof (see proof_single.py)


def plugin_dir(i):
    return ROOT / str(i) / "Data" / "NVSE" / "Plugins"


def write_ini(i, host_baseline):
    role = "host" if i == HOST_I else "client"
    lines = ["[bridge]", "instance=%d" % i, "autoload=autosave", "autoloadDelayFrames=600", "minimized=1", "multiInstance=1", "",
             "[coop]", "instanceId=%d" % i, "role=%s" % role, "pipe=\\\\.\\pipe\\nvcoop-%d" % i, "enable=1",
             "port=%d" % PORT, "host=127.0.0.1", "listen=127.0.0.1", "token=%d" % (1000 + i), "dialogue=1",
             "baseline=%d" % (1 if host_baseline else 0)]
    (plugin_dir(i) / "nvcoop.ini").write_text("\n".join(lines) + "\n")


def provision(baseline):
    import instances
    for i in (HOST_I, CLIENT_I):
        instances.provision(i, role="host" if i == HOST_I else "client", plugin_dll=DLL if DLL.exists() else None, autoload="autosave")
        sav = ROOT / str(i) / "Saves"
        sav.mkdir(parents=True, exist_ok=True)
        src = REPO.parent / "fnv-instances-l5" / "0" / "Saves" / "autosave.fos"
        if src.exists():
            shutil.copy2(src, sav / "autosave.fos")
        if DLL.exists():
            shutil.copy2(DLL, plugin_dir(i) / "nvcoop.dll")
            for f in sorted(DLL.parent.glob("nvcoop_*.json")):      # generated data (rules / EDIDs / INFO->quest) from build_plugin.cmd
                shutil.copy2(f, plugin_dir(i) / f.name)
        write_ini(i, baseline)


def filtered(src, dst):
    d = json.loads(Path(src).read_text(encoding="utf-8"))
    qs = d.get("shared", {}).get("quests", {})
    for k in [k for k, v in qs.items() if k.split(" (")[0].startswith(IGNORE_QUEST_PREFIXES) or str(v.get("edid", "")).startswith(IGNORE_QUEST_PREFIXES)]:
        del qs[k]
    Path(dst).write_text(json.dumps(d), encoding="utf-8")


def parse_diff(text):
    """snapdiff output -> {path: (old, new)} for CHANGED / ADDED / REMOVED lines"""
    out = {}
    for line in text.splitlines():
        line = line.strip()
        for kind in ("CHANGED ", "ADDED ", "REMOVED "):
            if line.startswith(kind):
                body = line[len(kind):]
                if kind == "CHANGED " and ": " in body and " -> " in body:
                    path, rest = body.rsplit(": ", 1)
                    old, new = rest.split(" -> ", 1)
                    out[path] = (old, new)
                else:
                    out[body.split(" (was")[0]] = (kind.strip(), body)
    return out


def snapdiff(a, b):
    fa, fb = a.with_name("f_" + a.name), b.with_name("f_" + b.name)
    filtered(a, fa)
    filtered(b, fb)
    cp = subprocess.run([sys.executable, "-I", "-X", "utf8", str(SNAPTOOLS / "snapdiff.py"), str(fa), str(fb)], capture_output=True, text=True)
    return cp.returncode, (cp.stdout + cp.stderr).strip()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--variant", choices=["live", "late"], default="live")
    ap.add_argument("--baseline", action="store_true", help="use the world-baseline join (needs L7's dumper in the DLL)")
    ap.add_argument("--quest", default="10A214")
    ap.add_argument("--stage", type=int, default=10)
    ap.add_argument("--approved", action="store_true", help="owner approved multi-instance launches")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    out = REPO / "out" / "L10" / "twogame" / a.variant
    out.mkdir(parents=True, exist_ok=True)
    for stale in out.glob("*.json"):
        stale.unlink()
    provision(a.baseline)
    plan = {"variant": a.variant, "baseline": a.baseline, "quest": a.quest, "stage": a.stage, "port": PORT, "root": str(ROOT)}
    print("plan:", json.dumps(plan))
    if a.dry_run:
        print("dry-run: ini files written to", plugin_dir(HOST_I), plugin_dir(CLIENT_I))
        return 0
    if not a.approved:
        print("REFUSING to launch two game instances without --approved (owner has not approved multi-instance yet)")
        return 2

    import nvbridge
    import run as runner
    import winproc
    gs = [sys.executable, "-I", str(REPO / "tools" / "gameslot.py")]
    os.environ["FNV_LANE"] = "L10"    # runner.launch_instance registers the pids on our lease (kill only our own games)
    if subprocess.run(gs + ["acquire", "--owner", "L10", "--slots", "2", "--ttl", "2400", "--wait", "3600"]).returncode != 0:
        print("could not get the game slot")
        return 3
    pids = {}
    nvs = {}
    res = {"plan": plan, "checks": {}}

    def check(name, cond, detail=""):
        res["checks"][name] = {"pass": bool(cond), "detail": detail}
        print(("PASS " if cond else "FAIL ") + name + (" :: %s" % detail if detail else ""))

    def launch(i):
        pid = runner.launch_instance(i)
        pids[i] = pid
        st = runner.load_state()
        st["pids"][str(i)] = pid
        runner.save_state(st)
        nv = nvbridge.NvBridge.connect(i, wait_s=240)
        nv.wait_event("autoload.done", timeout_s=240)
        base = nv.ping()["frames"]
        while nv.ping()["frames"] - base < SETTLE_FRAMES:
            time.sleep(0.25)
        nvs[i] = nv
        return nv

    def joined(nv, timeout=60):
        end = time.time() + timeout
        while time.time() < end:
            s = nv.call("coop.status")
            if s.get("client", {}).get("joined") and not s.get("client", {}).get("syncing"):
                return s
            time.sleep(0.5)
        return nv.call("coop.status")

    ok = False
    try:
        host = launch(HOST_I)
        hs = host.call("coop.status")
        check("host coop running", hs.get("running") and hs.get("role") == "host", json.dumps(hs)[:300])
        if a.variant == "live":
            time.sleep(12)    # two launches ~12 s apart are safe (L5)
            client = launch(CLIENT_I)
            cs = joined(client)
            check("client joined host", cs.get("client", {}).get("joined"), json.dumps(cs.get("client", {})))
            host.state_to_file(str(out / "S0_host.json"))
            client.state_to_file(str(out / "S0_client.json"))
            host.exec("setstage %s %d" % (a.quest, a.stage))
        else:
            host.state_to_file(str(out / "S0_host.json"))
            host.exec("setstage %s %d" % (a.quest, a.stage))
            time.sleep(12)
            client = launch(CLIENT_I)
            cs = joined(client)
            check("late client joined host", cs.get("client", {}).get("joined"), json.dumps(cs.get("client", {})))
        try:
            client.wait_stage(a.quest, a.stage, 30000)
        except Exception as e:     # keep going: collect diagnostics from both games
            print("client did not reach the stage:", e)
        check("client game shows host stage", client.eval("GetStage %s" % a.quest) >= a.stage, "client GetStage=%s host GetStage=%s" % (client.eval("GetStage %s" % a.quest), host.eval("GetStage %s" % a.quest)))
        for nm, nv_ in (("host", host), ("client", client)):
            try:
                stt = nv_.call("coop.status")
                print("%s coop.status: lastSeq=%s connected=%s counters=%s tally=%s hub=%s" % (nm, stt.get("lastSeq"), stt.get("connected"), json.dumps(stt.get("counters")), json.dumps(stt.get("effectTally")), json.dumps(stt.get("hub"))))
                print("%s coop.log: %s" % (nm, json.dumps(nv_.call("coop.log", {"n": 6}))[:900]))
            except Exception as e:
                print(nm, "status failed:", e)
        time.sleep(3)
        host.state_to_file(str(out / "S_host.json"))
        client.state_to_file(str(out / "S_client.json"))
        rc, text = snapdiff(out / "S_host.json", out / "S_client.json")
        (out / "snapdiff.txt").write_text(text)
        print("absolute snapdiff host vs client: rc=%d (%d lines; launch drift of time-driven vars is expected)" % (rc, len(text.splitlines())))
        if (out / "S0_host.json").exists() and not (out / "S0_client.json").exists():
            # late join: no pre-action client snapshot exists; every path the HOST changed must not appear in the absolute host/client diff
            _, th = snapdiff(out / "S0_host.json", out / "S_host.json")
            dh, dabs = parse_diff(th), parse_diff(text)
            bad = [p for p in dh if p in dabs]
            print("late join: host changed %d paths; %d of them still differ on the client" % (len(dh), len(bad)))
            for p_ in bad[:15]:
                print("   DIFFERENT:", p_, dabs[p_])
            check("every path the host changed has the same value on the late-joined client", not bad and len(dh) > 0, "%d differ" % len(bad))
        elif (out / "S0_host.json").exists():
            # L5's delta method: what CHANGED in each game since the pre-action snapshot must agree (absolute dumps of two separate
            # launches always differ by time-driven vars). Every path the host changed must end at the same value on the client.
            _, th = snapdiff(out / "S0_host.json", out / "S_host.json")
            _, tc = snapdiff(out / "S0_client.json", out / "S_client.json")
            dh, dc = parse_diff(th), parse_diff(tc)
            (out / "delta_host.txt").write_text(th)
            (out / "delta_client.txt").write_text(tc)
            def num(x):
                try:
                    return float(x)
                except ValueError:
                    return x
            def same(a, b):
                a, b = num(a), num(b)
                return abs(a - b) < 1e-3 if isinstance(a, float) and isinstance(b, float) else a == b
            missing = [p for p, v in dh.items() if p not in dc]
            wrong = [(p, v[1], dc[p][1]) for p, v in dh.items() if p in dc and not same(v[1], dc[p][1])]
            print("delta host=%d paths, delta client=%d paths; host changes missing on client: %d; different final value: %d" % (len(dh), len(dc), len(missing), len(wrong)))
            for p in missing[:15]:
                print("   MISSING on client:", p, dh[p])
            for w in wrong[:15]:
                print("   DIFFERENT:", w)
            check("delta(host) == delta(client): every host change reached the client with the same value", not missing and not wrong, "%d missing, %d different" % (len(missing), len(wrong)))
        else:
            check("snapdiff(host, client) clean", rc == 0, "rc=%d %s" % (rc, text[-400:]))
        try:
            sc = client.call("capture.end").get("scripts", {})     # L5 counters on the client (guest role)
        except Exception as e:
            sc = {"error": str(e)}
        res["client_scripts"] = sc
        check("no result script ran on the client while applying", sc.get("ranWhileApplying", 0) == 0, json.dumps(sc))
        log1 = (plugin_dir(CLIENT_I) / "nvcoop.log").read_text(errors="replace") if (plugin_dir(CLIENT_I) / "nvcoop.log").exists() else ""
        check("instance 1 has the multi-instance neutralization (NEUTRALIZED in nvcoop.log)", "NEUTRALIZED" in log1, "")
        cst = client.call("coop.status")
        res["client_counters"] = cst.get("counters")
        res["client_tally"] = cst.get("effectTally")
        print("client effect tally:", json.dumps(cst.get("effectTally")))
        print("client counters:", json.dumps(cst.get("counters")))
        (out / "coop_status_host.json").write_text(json.dumps(host.call("coop.status"), indent=1))
        (out / "coop_status_client.json").write_text(json.dumps(client.call("coop.status"), indent=1))
        ok = all(c["pass"] for c in res["checks"].values())
    finally:
        for i, nv in nvs.items():
            try:
                nv.quit()
            except Exception:
                pass
        for i, pid in pids.items():
            for _ in range(60):
                if not winproc.pid_alive(pid):
                    break
                time.sleep(0.5)
            else:
                winproc.kill_pid(pid)
        subprocess.run(gs + ["release", "--owner", "L10"])
        res["ok"] = ok
        (out / "result.json").write_text(json.dumps(res, indent=1))
    print("PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
