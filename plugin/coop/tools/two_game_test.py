#!/usr/bin/env python3
"""L10 REAL two-game co-op test (host game + client game, both minimized).

    python -I -X utf8 plugin/coop/tools/two_game_test.py --variant live|late [--baseline] [--quest 10A214 --stage 10] [--approved]
    python -I -X utf8 plugin/coop/tools/two_game_test.py --dry-run          (provisions ini files, prints the plan, launches nothing)
    python -I -X utf8 plugin/coop/tools/two_game_test.py --selftest         (game-free: ambient list validation + diff parsing)

Variants
  live : host and client games are both up and joined, THEN `setstage` on the host. The client game must show the same
         stage (GetStage) within the timeout, every host change must reach the client, and snapdiff(host, client) must be clean.
  late : host runs `setstage` first, THEN the client game starts and joins; the story-log replay (or, with --baseline,
         the world-baseline transfer) must bring the client to the same stage.
  --baseline : the host takes a world baseline at start and the joiner is brought in line with the baseline join.

Robustness
  * a game counts as ready only when its bridge reports menuReady, a save is loaded (loadedCount >= 1) and the main loop pumps;
    nothing is done to a game before that, and a game that never gets ready is a FAILED check, not an exception;
  * the game slots are taken through tools/gameslot.py; every pid is registered on our lease; only OUR pids (the pid the game
    itself reports over its bridge, never a lookalike FalloutNV.exe) are ever killed; the slots are released on every exit path
    (normal, exception, Ctrl-C / SIGTERM / SIGBREAK, interpreter exit);
  * out/L10/twogame/<variant>[_baseline]/result.json is rewritten after every check and at exit, also on failure, with the
    per-check detail, the raw and the filtered snapdiff counts, client script counters and duplicate-apply counts;
  * known-ambient differences (real-time timers) come ONLY from plugin/coop/tools/ambient_diffs.json: exact quest EDID + var name
    entries, each with a reason and a coordinator approval. Quest stages, objectives, running flags, globals and faction ranks can
    never be listed (the loader rejects anything but quest script variables).
Private instance root ../fnv-instances-l10/{10,11}; instance 10 = host, 11 = client. Saves: autosave.fos copied from ../fnv-instances-l5/0/Saves.
"""
import argparse
import atexit
import collections
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import time
import traceback
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
AMBIENT_FILE = Path(__file__).resolve().parent / "ambient_diffs.json"
SETTLE_FRAMES = 1500
READY_TIMEOUT_S = 420
MIN_FREE_GB = 1.5
IDENT = re.compile(r"^[A-Za-z0-9_]+$")
GS = [sys.executable, "-I", str(REPO / "tools" / "gameslot.py")]
OWNER = "L10"


class Abort(Exception):
    """A step that later steps depend on failed; the failing check is already recorded."""


# ----------------------------------------------------------------------------------------------------------------------
# ambient allow-list
# ----------------------------------------------------------------------------------------------------------------------
def load_ambient(path=AMBIENT_FILE):
    """-> list of dicts {quest, var, reason, approved_by, regex}. Raises ValueError on anything that is not a plain
    quest-script-variable entry with a reason and an approval (so the list can never hide a stage / flag / objective)."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    out = []
    for n, e in enumerate(data.get("entries", [])):
        extra = set(e) - {"quest", "var", "reason", "approved_by"}
        if extra:
            raise ValueError("ambient entry %d: unsupported field(s) %s (only quest script variables may be listed)" % (n, sorted(extra)))
        for k in ("quest", "var"):
            if not IDENT.match(str(e.get(k, ""))):
                raise ValueError("ambient entry %d: %s must be one exact identifier (no wildcards), got %r" % (n, k, e.get(k)))
        if len(str(e.get("reason", "")).strip()) < 20:
            raise ValueError("ambient entry %d (%s.%s): a real reason is required" % (n, e["quest"], e["var"]))
        if not str(e.get("approved_by", "")).strip():
            raise ValueError("ambient entry %d (%s.%s): approved_by is required" % (n, e["quest"], e["var"]))
        e = dict(e)
        e["regex"] = re.compile(r"^shared/quests/%s \([^)]*\)/vars/%s$" % (re.escape(e["quest"]), re.escape(e["var"])))
        out.append(e)
    return out


def ambient_reason(path, ambient):
    for e in ambient:
        if e["regex"].match(path):
            return "%s.%s: %s" % (e["quest"], e["var"], e["reason"])
    return None


# ----------------------------------------------------------------------------------------------------------------------
# snapshot diff helpers
# ----------------------------------------------------------------------------------------------------------------------
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


def split_ambient(diffs, ambient):
    """-> (real {path: v}, ambient {path: (v, reason)})"""
    real, amb = {}, {}
    for p, v in diffs.items():
        r = ambient_reason(p, ambient)
        if r:
            amb[p] = (v, r)
        else:
            real[p] = v
    return real, amb


def snapdiff(a, b):
    cp = subprocess.run([sys.executable, "-I", "-X", "utf8", str(SNAPTOOLS / "snapdiff.py"), str(a), str(b)], capture_output=True, text=True)
    return cp.returncode, (cp.stdout + cp.stderr).strip()


def _num(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return x


def same_value(a, b):
    a, b = _num(a), _num(b)
    return abs(a - b) < 1e-3 if isinstance(a, float) and isinstance(b, float) else a == b


def selftest():
    amb = load_ambient()
    assert len(amb) >= 4
    sample = ("DIFFERENT: 3 difference(s) in shared\n"
              "  CHANGED shared/quests/vDialogueGomorrah (FalloutNV.esm:10C721)/vars/Time: 6.33 -> 5.96\n"
              "  CHANGED shared/quests/NVDLC01MQ00 (DeadMoney.esm:005229)/vars/StartTimer: 29.6 -> 0\n"
              "  CHANGED shared/quests/NVDLC02RainQuest (HonestHearts.esm:00EDE7)/stage: 5 -> 6\n")
    real, a = split_ambient(parse_diff(sample), amb)
    assert list(a) == ["shared/quests/vDialogueGomorrah (FalloutNV.esm:10C721)/vars/Time"], a
    assert len(real) == 2, real                       # a stage change on an ambient-listed QUEST is still real
    for bad in ({"quest": "VCG01", "var": "*", "reason": "x" * 30, "approved_by": "me"},          # wildcard
                {"quest": "VCG01", "var": "stage", "reason": "short", "approved_by": "me"},       # no real reason
                {"quest": "VCG01", "var": "v1", "reason": "x" * 30},                              # no approval
                {"quest": "VCG01", "var": "v1", "reason": "x" * 30, "approved_by": "me", "path": "shared/quests/VCG01/stage"}):
        tmp = Path(os.environ.get("TEMP", ".")) / ("ambient_selftest_%d.json" % os.getpid())
        tmp.write_text(json.dumps({"entries": [bad]}))
        try:
            load_ambient(tmp)
        except ValueError:
            pass
        else:
            raise AssertionError("loader accepted %r" % bad)
        finally:
            tmp.unlink()
    print("selftest ok: %d ambient entries, loader rejects wildcards / flags / unapproved entries" % len(amb))
    return 0


# ----------------------------------------------------------------------------------------------------------------------
# provisioning
# ----------------------------------------------------------------------------------------------------------------------
def plugin_dir(i):
    return ROOT / str(i) / "Data" / "NVSE" / "Plugins"


def write_ini(i, host_baseline):
    role = "host" if i == HOST_I else "client"
    lines = ["[bridge]", "instance=%d" % i, "autoload=autosave", "autoloadDelayFrames=600", "minimized=1", "multiInstance=1", "",
             "[coop]", "instanceId=%d" % i, "role=%s" % role, "pipe=\\\\.\\pipe\\nvcoop-%d" % i, "enable=1",
             "port=%d" % PORT, "host=127.0.0.1", "listen=127.0.0.1", "token=%d" % (1000 + i), "dialogue=1", "handshake=1",
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


def free_gb():
    return shutil.disk_usage(str(REPO)).free / 2**30


# ----------------------------------------------------------------------------------------------------------------------
class Run:
    """One test run: owns the slots, the pids and the result file; cleanup() is idempotent and runs on every exit path."""

    def __init__(self, args, out):
        self.a, self.out = args, out
        self.res = {"plan": {"variant": args.variant, "baseline": args.baseline, "quest": args.quest, "stage": args.stage, "port": PORT,
                             "root": str(ROOT)},
                    "checks": {}, "metrics": {}, "ok": False, "finished": False, "started": time.strftime("%Y-%m-%d %H:%M:%S")}
        self.pids, self.nvs = {}, {}
        self.slot_held = False
        self.cleaned = False

    # ---- results ----
    def flush(self):
        try:
            self.out.mkdir(parents=True, exist_ok=True)
            (self.out / "result.json").write_text(json.dumps(self.res, indent=1, default=str))
        except OSError as e:
            print("could not write result.json:", e)

    def check(self, name, cond, detail=""):
        self.res["checks"][name] = {"pass": bool(cond), "detail": detail}
        print(("PASS " if cond else "FAIL ") + name + (" :: %s" % detail if detail else ""))
        self.flush()
        return bool(cond)

    def require(self, name, cond, detail=""):
        if not self.check(name, cond, detail):
            raise Abort(name)

    # ---- slots / pids ----
    def acquire(self):
        for attempt in range(5):     # exit 1 = gameslot.py itself crashed (transient Windows file-replace race), 3 = busy past --wait
            cp = subprocess.run(GS + ["acquire", "--owner", OWNER, "--slots", "2", "--ttl", "2400", "--wait", "3600"], capture_output=True, text=True)
            print((cp.stdout + cp.stderr).strip()[-400:])
            if cp.returncode != 1:
                break
            time.sleep(5)
        self.slot_held = cp.returncode == 0
        return cp.returncode

    def cleanup(self, *_):
        if self.cleaned:
            return
        self.cleaned = True
        try:
            import winproc
            for i, nv in list(self.nvs.items()):
                try:
                    nv.quit()
                except Exception:
                    pass
            for i, pid in list(self.pids.items()):
                for _ in range(40):
                    if not winproc.pid_alive(pid):
                        break
                    time.sleep(0.5)
                else:
                    if pid in winproc.list_pids("FalloutNV.exe"):     # never kill a reused pid
                        winproc.kill_pid(pid)
        except Exception as e:
            print("cleanup (kill) problem:", e)
        finally:
            if self.slot_held:
                try:
                    subprocess.run(GS + ["release", "--owner", OWNER], capture_output=True, timeout=60)
                except Exception as e:
                    print("cleanup (release) problem:", e)
                self.slot_held = False
            self.res["finished"] = True
            self.flush()

    # ---- games ----
    def launch(self, i):
        import nvbridge
        import run as runner
        import winproc
        before = set(winproc.list_pids("FalloutNV.exe"))
        pid = runner.launch_instance(i)          # registers the pid it guesses on our lease
        self.pids[i] = pid
        st = runner.load_state()
        st["pids"][str(i)] = pid
        runner.save_state(st)
        nv = nvbridge.NvBridge.connect(i, wait_s=240)
        reported = int(nv.ping().get("pid", 0) or 0)       # the game itself tells us who it is: the only pid we ever kill
        if reported and reported != pid:
            print("instance %d: launcher guessed pid %d but the game reports %d (before=%s); using the reported pid" % (i, pid, reported, sorted(before)))
            self.pids[i] = reported
            runner.register_pid(reported)
        self.nvs[i] = nv
        return nv

    def wait_ready(self, i, label):
        """menuReady + a save loaded + main loop pumping + SETTLE_FRAMES frames. Returns the detail string; raises Abort on timeout / death."""
        import winproc
        nv = self.nvs[i]
        end = time.time() + READY_TIMEOUT_S
        last = {}
        while time.time() < end:
            if not winproc.pid_alive(self.pids[i]):
                self.require("%s game ready" % label, False, "process %d exited while loading" % self.pids[i])
            try:
                last = nv.ping()
            except Exception as e:
                last = {"error": str(e)}
            if last.get("menuReady") and int(last.get("loadedCount", 0)) >= 1 and int(last.get("mainLoopAgeMs", -1)) >= 0:
                break
            time.sleep(1.0)
        else:
            self.require("%s game ready" % label, False, "timeout %ds, last ping %s" % (READY_TIMEOUT_S, json.dumps(last)))
        try:
            nv.wait_event("autoload.done", timeout_s=60)
        except Exception:
            pass      # menuReady + loadedCount already say the save is loaded; the event may have been consumed
        base = nv.ping()["frames"]
        end = time.time() + 240
        while nv.ping()["frames"] - base < SETTLE_FRAMES and time.time() < end:
            time.sleep(0.25)
        done = nv.ping()["frames"] - base >= SETTLE_FRAMES
        detail = "pid %d, loadedCount %s, frames settled=%s" % (self.pids[i], last.get("loadedCount"), done)
        self.require("%s game ready" % label, done, detail)
        return nv

    def joined(self, nv, timeout=90):
        end = time.time() + timeout
        s = {}
        while time.time() < end:
            s = nv.call("coop.status")
            c = s.get("client", {})
            if c.get("joined") and not c.get("syncing"):
                return s
            time.sleep(0.5)
        return s


def duplicates_of(nv):
    """Count txn seqs the client applied more than once (dir != 'out'/'repair', seq > 0)."""
    try:
        log = nv.call("coop.log", {"n": 4096})
    except Exception as e:
        return {"error": str(e)}
    c = collections.Counter()
    for t in log.get("txns", []):
        if t.get("dir") in ("out", "repair") or not t.get("seq"):
            continue
        c[(t.get("dir"), t.get("seq"))] += 1
    dups = {"%s:%s" % k: v for k, v in c.items() if v > 1}
    return {"entries": sum(c.values()), "duplicate_seqs": dups, "duplicates": sum(v - 1 for v in dups.values())}


def run_test(r, ambient):
    a, out = r.a, r.out
    r.res["ambient_entries"] = [{k: e[k] for k in ("quest", "var", "reason", "approved_by")} for e in ambient]
    host = r.launch(HOST_I)
    r.wait_ready(HOST_I, "host")
    hs = host.call("coop.status")
    r.require("host coop running", hs.get("running") and hs.get("role") == "host", json.dumps(hs)[:300])
    if a.variant == "live":
        time.sleep(12)    # two launches ~12 s apart are safe (L5)
        client = r.launch(CLIENT_I)
        r.wait_ready(CLIENT_I, "client")
        cs = r.joined(client)
        r.require("client joined host", cs.get("client", {}).get("joined") and not cs.get("client", {}).get("syncing"), json.dumps(cs.get("client", {})))
        host.state_to_file(str(out / "S0_host.json"))
        client.state_to_file(str(out / "S0_client.json"))
        host.exec("setstage %s %d" % (a.quest, a.stage))
    else:
        host.state_to_file(str(out / "S0_host.json"))
        host.exec("setstage %s %d" % (a.quest, a.stage))
        time.sleep(12)
        client = r.launch(CLIENT_I)
        r.wait_ready(CLIENT_I, "client")
        cs = r.joined(client)
        r.require("late client joined host", cs.get("client", {}).get("joined") and not cs.get("client", {}).get("syncing"), json.dumps(cs.get("client", {})))
    try:
        client.wait_stage(a.quest, a.stage, 30000)
    except Exception as e:     # keep going: collect diagnostics from both games
        print("client did not reach the stage:", e)
    cstage, hstage = client.eval("GetStage %s" % a.quest), host.eval("GetStage %s" % a.quest)
    r.check("client game shows host stage", cstage >= a.stage and cstage == hstage, "client GetStage=%s host GetStage=%s" % (cstage, hstage))
    for nm, nv_ in (("host", host), ("client", client)):
        try:
            stt = nv_.call("coop.status")
            print("%s coop.status: lastSeq=%s connected=%s counters=%s tally=%s hub=%s" % (nm, stt.get("lastSeq"), stt.get("connected"), json.dumps(stt.get("counters")), json.dumps(stt.get("effectTally")), json.dumps(stt.get("hub"))))
        except Exception as e:
            print(nm, "status failed:", e)
    time.sleep(3)
    host.state_to_file(str(out / "S_host.json"))
    client.state_to_file(str(out / "S_client.json"))

    # absolute host-vs-client diff, raw and with the documented ambient entries removed
    rc, text = snapdiff(out / "S_host.json", out / "S_client.json")
    (out / "snapdiff.txt").write_text(text)
    absd = parse_diff(text)
    real, amb = split_ambient(absd, ambient)
    r.res["metrics"]["snapdiff_rc"] = rc
    r.res["metrics"]["snapdiff_raw"] = len(absd)
    r.res["metrics"]["snapdiff_after_ambient"] = len(real)
    r.res["metrics"]["ambient_ignored"] = {p: {"values": list(v[0]), "reason": v[1]} for p, v in amb.items()}
    r.res["metrics"]["snapdiff_real_paths"] = {p: list(v) for p, v in list(real.items())[:60]}
    print("snapdiff host vs client: rc=%d raw=%d ambient-ignored=%d real=%d" % (rc, len(absd), len(amb), len(real)))
    for p, v in list(real.items())[:30]:
        print("   DIFF:", p, v)
    r.check("snapdiff(host, client) clean (documented ambient timers excluded)", rc in (0, 1) and not real,
            "rc=%d raw=%d ambient=%d real=%d%s" % (rc, len(absd), len(amb), len(real), "" if rc in (0, 1) else " :: " + text[-300:]))

    # delta check: every path the host changed since the pre-action snapshot must end at the same value on the client
    if (out / "S0_client.json").exists():
        _, th = snapdiff(out / "S0_host.json", out / "S_host.json")
        _, tc = snapdiff(out / "S0_client.json", out / "S_client.json")
        dh, _ = split_ambient(parse_diff(th), ambient)
        dc, _ = split_ambient(parse_diff(tc), ambient)
        (out / "delta_host.txt").write_text(th)
        (out / "delta_client.txt").write_text(tc)
        missing = [p for p in dh if p not in dc]
        wrong = [(p, dh[p][1], dc[p][1]) for p in dh if p in dc and not same_value(dh[p][1], dc[p][1])]
        r.res["metrics"]["delta_host_paths"], r.res["metrics"]["delta_client_paths"] = len(dh), len(dc)
        print("delta host=%d paths, client=%d; host changes missing on client: %d; different: %d" % (len(dh), len(dc), len(missing), len(wrong)))
        for p in missing[:15]:
            print("   MISSING on client:", p, dh[p])
        for w in wrong[:15]:
            print("   DIFFERENT:", w)
        r.check("delta(host) == delta(client): every host change reached the client with the same value", dh and not missing and not wrong,
                "%d host paths, %d missing, %d different" % (len(dh), len(missing), len(wrong)))
    else:
        _, th = snapdiff(out / "S0_host.json", out / "S_host.json")
        dh, _ = split_ambient(parse_diff(th), ambient)
        bad = [p for p in dh if p in real]
        r.check("every path the host changed has the same value on the late-joined client", dh and not bad, "%d host paths, %d still differ" % (len(dh), len(bad)))

    # scripts that ran on the client (L5 counters), duplicates, status
    try:
        sc = client.call("capture.end").get("scripts", {})
    except Exception as e:
        sc = {"error": str(e)}
    r.res["client_scripts"] = sc
    r.res["metrics"]["scripts_rerun_on_client"] = sc.get("ranWhileApplying") if isinstance(sc, dict) else None
    r.check("no result script ran on the client while applying", sc.get("ranWhileApplying", 0) == 0 and "error" not in sc, json.dumps(sc))
    dup = duplicates_of(client)
    r.res["client_duplicates"] = dup
    r.res["metrics"]["duplicates"] = dup.get("duplicates")
    r.check("client applied no txn twice", dup.get("duplicates", 1) == 0, json.dumps(dup)[:300])
    log1 = (plugin_dir(CLIENT_I) / "nvcoop.log").read_text(errors="replace") if (plugin_dir(CLIENT_I) / "nvcoop.log").exists() else ""
    m = re.findall(r"GuestLocalRun allow-list: (\d+) scripts \((\d+) malformed\)", log1)
    r.check("client loaded the GuestLocalRun allow-list (nvcoop.log)", bool(m) and int(m[-1][0]) > 0 and int(m[-1][1]) == 0, "log lines: %s" % m[-2:])
    r.check("instance 1 has the multi-instance neutralization (NEUTRALIZED in nvcoop.log)", "NEUTRALIZED" in log1, "")
    cst = client.call("coop.status")
    r.res["client_counters"] = cst.get("counters")
    r.res["client_tally"] = cst.get("effectTally")
    (out / "coop_status_host.json").write_text(json.dumps(host.call("coop.status"), indent=1))
    (out / "coop_status_client.json").write_text(json.dumps(cst, indent=1))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--variant", choices=["live", "late"], default="live")
    ap.add_argument("--baseline", action="store_true", help="use the world-baseline join (needs L7's dumper in the DLL)")
    ap.add_argument("--quest", default="10A214")
    ap.add_argument("--stage", type=int, default=10)
    ap.add_argument("--approved", action="store_true", help="owner approved multi-instance launches")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    if a.selftest:
        return selftest()
    out = REPO / "out" / "L10" / "twogame" / (a.variant + ("_baseline" if a.baseline else ""))
    out.mkdir(parents=True, exist_ok=True)
    for stale in list(out.glob("*.json")) + list(out.glob("*.txt")):
        stale.unlink()
    r = Run(a, out)
    ambient = load_ambient()
    provision(a.baseline)
    print("plan:", json.dumps(r.res["plan"]))
    if a.dry_run:
        print("dry-run: ini files written to", plugin_dir(HOST_I), plugin_dir(CLIENT_I))
        return 0
    if not a.approved:
        print("REFUSING to launch two game instances without --approved (owner has not approved multi-instance yet)")
        return 2
    t_end = time.time() + 900      # disk swings are transient (other projects' builds): wait up to 15 min, polling every 30 s
    while free_gb() < MIN_FREE_GB and time.time() < t_end:
        print("waiting for disk space: %.2f GB free, need %.1f" % (free_gb(), MIN_FREE_GB))
        time.sleep(30)
    if free_gb() < MIN_FREE_GB:
        r.check("disk space", False, "%.2f GB free on the repo drive, need %.1f" % (free_gb(), MIN_FREE_GB))
        r.res["finished"] = True
        r.flush()
        return 4
    os.environ["FNV_LANE"] = OWNER    # runner.launch_instance registers the pids on our lease
    atexit.register(r.cleanup)
    for sig in ("SIGINT", "SIGTERM", "SIGBREAK"):
        if hasattr(signal, sig):
            signal.signal(getattr(signal, sig), lambda s, f: (r.cleanup(), os._exit(130)))
    rc = r.acquire()
    if rc != 0:
        r.check("game slots acquired", False, "gameslot acquire exit %d" % rc)
        r.res["finished"] = True
        r.flush()
        return 3
    try:
        run_test(r, ambient)
    except Abort as e:
        print("aborted at:", e)
    except BaseException as e:      # incl. KeyboardInterrupt: the result file must still say what happened
        r.res["exception"] = traceback.format_exc()
        r.check("test ran to completion", False, "%s: %s" % (type(e).__name__, e))
        traceback.print_exc()
    finally:
        r.cleanup()
    r.res["ok"] = bool(r.res["checks"]) and all(c["pass"] for c in r.res["checks"].values()) and "exception" not in r.res
    r.flush()
    print("PASS" if r.res["ok"] else "FAIL", json.dumps(r.res["metrics"].get("snapdiff_raw")), "raw diffs")
    return 0 if r.res["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
