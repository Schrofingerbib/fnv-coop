#!/usr/bin/env python3
"""Questline capture sweep (single game instance). Generalized from the Goodsprings sweep.

    python -I -X utf8 plugin/snapshot/tools/goodsprings_sweep.py --config questlines/<slug>/sweep.json [--resume]
                      [--run-id ID] [--quests A,B] [--stages 10,20] [--time-budget 2400] [--dry-run] [--finalize]
    python -I -X utf8 plugin/snapshot/tools/goodsprings_sweep.py --probe-console      # verify the setup console forms first

Without --config the Goodsprings template (out/questdata/m1_goodsprings.json) is used and, for continuity, results stay
under out/goodsprings_sweep/<run-id>/. With --config the output is out/sweeps/<slug>/<run-id>/ (stages/*.json, report.md,
done.json) plus out/sweeps/<slug>/report.md (latest) and runs/<run-id>/summary.json (L9 coverage format, kind "game").

Per run item (quest, stage[, variant]):
    loadSave -> frames -> test.quest.reset(plan.reset_ids) -> quest setup -> stage setup -> variant setup -> S0 -> control
    -> S1 -> capture.begin -> <stage command> -> capture.end -> S2 -> sweep_lib.analyze_stage (rules R-*).
Setup commands go through the console, EXCEPT `set <id>.<var> to N` (quest variable by hex id or EditorID), which is
translated to the bridge helper test.quest.setvar (deterministic; see internal lane notes "console forms"). A setup command
the console rejects ("Could not parse"...) turns the item into status "error" (setup) instead of a bogus pass/fail.
Resumable (existing stage JSONs are skipped), time-boxed, crash-tolerant (kill + relaunch + continue). Runs under the
gameslot lease (owner L7-sweep) on the L5 instance root with the canonical out/L1/nvcoop.dll copied in; the instance's
own dll/ini are restored afterwards. Snapshots are deleted after analysis except for failing items.
"""
import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
TOOLS = REPO / "plugin" / "snapshot" / "tools"
sys.path.insert(0, str(TOOLS))
sys.path.insert(0, str(REPO / "tools" / "runner"))
INSTANCE_ROOT = REPO.parent / "fnv-instances-l7"      # private, hardlinked: tools/runner/run.py provision --n 2
SAVE_ROOTS = [REPO.parent / "fnv-instances-l5", REPO.parent / "fnv-instances-l10"]   # where a missing save can be copied from
os.environ["FNV_INSTANCES_ROOT"] = str(INSTANCE_ROOT)

import sweep_config  # noqa: E402
import sweep_lib  # noqa: E402

OWNER = "L7-sweep"
DEFAULT_CONFIG = REPO / "out" / "questdata" / "m1_goodsprings.json"
ORDER_LAST = ("VCG01", "VCG00")      # Goodsprings only: cinematic / chargen quests last
PARSE_ERRORS = ("could not parse", "error line", "unknown", "invalid")
SET_RE = re.compile(r"^\s*set\s+([A-Za-z0-9_]+)\.([A-Za-z0-9_]+)\s+to\s+(-?[0-9.]+)\s*$", re.I)


# --------------------------------------------------------------------------------------------------- plan / helpers
def item_name(edid, stage, variant=None):
    return "%s_%d%s" % (edid, stage, ("__" + re.sub(r"[^A-Za-z0-9_-]", "_", variant)) if variant else "")


def plan_items(plan, only_quests=None, only_stages=None):
    items = []
    for q in plan["quests"]:
        if only_quests and q["edid"] not in only_quests:
            continue
        for s in q["stages"]:
            if only_stages and s["index"] not in only_stages:
                continue
            variants = s["variants"] or [None]
            for v in variants:
                items.append({"edid": q["edid"], "hex": q["hex"], "stage": s["index"], "quest": q, "st": s, "variant": v})
    return items


def load_oracle():
    import gzip
    with gzip.open(REPO / "out" / "questdata" / "effects_native.json.gz", "rt", encoding="utf-8") as f:
        return json.load(f)


def lease(cmd, *extra):
    return subprocess.run([sys.executable, "-I", str(REPO / "tools" / "gameslot.py"), cmd, "--owner", OWNER, *extra],
                          capture_output=True, text=True)


def acquire_slot(ttl, wait):
    """Take ONE game slot (others may run a game concurrently). Returns the slot index = our instance id."""
    r = lease("acquire", "--ttl", str(ttl), "--wait", str(wait), "--slots", "1")
    print(r.stdout.strip() or r.stderr.strip())
    if r.returncode != 0:
        sys.exit("could not get a game slot")
    m = re.search(r"SLOTS\s+(\d+)", r.stdout)
    return int(m.group(1)) if m else 0


def restore_files(pairs, game=None, timeout=90.0):
    """Write each (path, bytes) back, retrying until the file is writable (a dying FalloutNV.exe keeps nvcoop.dll locked for a while).
    Kills our own game pid first; never raises before every file was tried. Returns the paths that could not be restored."""
    if game is not None:
        try:
            game.kill()
        except Exception:   # noqa: BLE001
            pass
    failed = []
    for path, data in pairs:
        if data is None or path is None:
            continue
        deadline = time.time() + timeout
        while True:
            try:
                path.write_bytes(data)
                break
            except OSError as e:
                if time.time() > deadline:
                    print("RESTORE FAILED %s: %s" % (path, e))
                    failed.append(path)
                    break
                time.sleep(1.0)
    return failed


class Game:
    def __init__(self, frames, instance=0):
        import nvbridge, run as runner, winproc  # noqa: E401
        self.nvbridge, self.runner, self.winproc = nvbridge, runner, winproc
        self.frames = frames
        self.instance = instance
        self.nv = None
        self.pid = None
        self.launches = 0

    def wait_frames(self, n, timeout=240):
        base = self.nv.ping()["frames"]
        t0 = time.time()
        while self.nv.ping()["frames"] - base < n:
            if time.time() - t0 > timeout:
                raise TimeoutError("frames did not advance")
            time.sleep(0.25)

    def launch(self):
        self.kill()
        os.environ["FNV_LANE"] = OWNER     # run.launch_instance registers the pid on our lease (tools/gameslot.py register-pid)
        t0 = time.time()
        self.pid = self.runner.launch_instance(self.instance)
        print("launch: process started %.0fs" % (time.time() - t0))
        st = self.runner.load_state()
        st["pids"][str(self.instance)] = self.pid
        self.runner.save_state(st)
        self.nv = self.nvbridge.NvBridge.connect(self.instance, wait_s=240)
        print("launch: bridge connected %.0fs" % (time.time() - t0))
        self.nv.wait_event("autoload.done", timeout_s=240)
        print("launch: autoload.done %.0fs" % (time.time() - t0))
        self.wait_frames(self.frames)
        print("launch: ready %.0fs" % (time.time() - t0))
        self.launches += 1

    def kill(self):
        if self.nv is not None:
            try:
                self.nv.quit()
            except Exception:
                pass
        if not self.pid:   # launch may have died after spawning: fall back to the pid recorded in the runner state
            try:
                self.pid = self.runner.load_state()["pids"].get(str(self.instance))
            except Exception:   # noqa: BLE001
                self.pid = None
        if self.pid:
            for _ in range(40):
                if not self.winproc.pid_alive(self.pid):
                    break
                time.sleep(0.5)
            else:
                self.winproc.kill_pid(self.pid)
        self.nv, self.pid = None, None     # never kill other lanes' games: only the pid we launched


def ensure_save(instance, save):
    """Slots map to instance ids; make sure this instance has the save (copied from any provisioned sibling)."""
    dst = INSTANCE_ROOT / str(instance) / "Saves" / (save + ".fos")
    if dst.exists():
        return
    sibs = [d for root in [INSTANCE_ROOT] + SAVE_ROOTS if root.exists() for d in sorted(root.iterdir())]
    for sib in sibs:
        src = sib / "Saves" / (save + ".fos")
        if sib.is_dir() and src.exists():
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(src, dst)
            print("copied save %s from instance %s to instance %d" % (save, sib.name, instance))
            return
    sys.exit("save %s.fos not found in any instance under %s" % (save, INSTANCE_ROOT))


class SetupError(Exception):
    pass


def edid_to_hex(plan, edid):
    for q in plan["quests"]:
        if q["edid"].lower() == edid.lower():
            return q["hex"]
    # a quest outside this config (e.g. a prerequisite): resolve through the global quest data
    global _GLOBAL_QUESTS
    try:
        _GLOBAL_QUESTS
    except NameError:
        d = json.loads((REPO / "out" / "questdata" / "quests.json").read_text(encoding="utf-8"))
        qs = d["quests"] if isinstance(d, dict) and "quests" in d else d
        qs = list(qs.values()) if isinstance(qs, dict) else qs
        _GLOBAL_QUESTS = {x["edid"].lower(): x["ref"] for x in qs if x.get("edid") and x.get("ref")}
    ref = _GLOBAL_QUESTS.get(edid.lower())
    if not ref:
        return None
    plug, local = sweep_config.split_ref(ref)
    return sweep_config.hex8(plug, local, plan["loadorder"])


def run_setup_cmd(nv, plan, cmd):
    """Execute one setup command; raises SetupError if the game rejected it."""
    m = SET_RE.match(cmd)
    if m:   # quest variable: the bridge helper (by name) instead of the unverified console syntax
        who, var, val = m.groups()
        qid = who if re.fullmatch(r"[0-9A-Fa-f]{6,8}", who) else edid_to_hex(plan, who)
        if not qid:
            raise SetupError("cannot resolve quest %r in: %s" % (who, cmd))
        try:
            nv.call("test.quest.setvar", {"quest": qid, "var": var, "value": float(val)})
        except Exception as e:   # noqa: BLE001
            raise SetupError("%s -> %s" % (cmd, e))
        return
    r = nv.exec(cmd)
    out = (r.get("output") or "") if isinstance(r, dict) else ""
    low = out.lower()
    if (isinstance(r, dict) and r.get("ok") is False) or any(p in low for p in PARSE_ERRORS):
        raise SetupError("%s -> %s" % (cmd, out.strip()[:120] or r))


WORLD_EFFECTS = {"moveref", "enableref", "disableref", "kill", "additem", "removeitem", "placeatme", "resurrect", "setowner", "equipitem"}


def run_item(game, plan, item, oracle, outdir, save, frames, control_s, settle_s, first):
    nv = game.nv
    tmp = outdir / "tmp"
    tmp.mkdir(parents=True, exist_ok=True)
    edid, stage, q, st, var = item["edid"], item["stage"], item["quest"], item["st"], item["variant"]
    name = item_name(edid, stage, var["name"] if var else None)
    s = {k: tmp / ("%s_%s.json" % (name, k)) for k in ("S0", "S1", "S2")}
    t0 = time.time()
    ph = {}
    tp = [t0]

    def mark(k):
        now = time.time()
        ph[k] = round(now - tp[0], 1)
        tp[0] = now
    if not first:
        nv.load_save(save)
        mark("load")
        game.wait_frames(frames)
        mark("frames")
    rst = nv.call("test.quest.reset", {"quests": plan["reset_ids"]})
    cmds = list(q["setup"]) + list(st["setup"]) + (list(var["setup"]) if var else [])
    for c in cmds:
        run_setup_cmd(nv, plan, c)
        time.sleep(0.3)
    if cmds:
        time.sleep(1.5)
    mark("setup")
    nv.state_to_file(str(s["S0"]))
    mark("dumpS0")
    time.sleep(control_s)
    nv.state_to_file(str(s["S1"]))
    mark("control+S1")
    nv.call("capture.begin")
    nv.exec(st["command"])
    m = re.match(r"\s*setstage\s+([0-9A-Fa-f]+)\s+(\d+)", st["command"], re.I)
    if m:
        try:
            nv.wait_stage(m.group(1), int(m.group(2)), 8000)
        except Exception:   # readback can legitimately differ (e.g. 255, or a script resets it)
            pass
    time.sleep(settle_s)
    mark("stage+settle")
    cap = nv.call("capture.end")
    nv.state_to_file(str(s["S2"]))
    mark("end+S2")
    snaps = {k: json.loads(v.read_text(encoding="utf-8")) for k, v in s.items()}
    res = sweep_lib.analyze_stage(oracle, edid, stage, snaps["S0"], snaps["S1"], snaps["S2"], cap)
    res["variant"] = var["name"] if var else None
    res["command"] = st["command"]
    res["setup"] = cmds
    res["captured"] = sweep_lib.captured_effects(cap)
    res["reset"] = rst
    res["capture_stats"] = {k: cap.get(k) for k in ("stats", "unmapped", "scripts")}
    mark("analyze")
    res["phases"] = ph
    res["seconds"] = round(time.time() - t0, 1)
    if res["status"] == "fail":
        res["capture_raw"] = cap
        keep = outdir / "snapshots"
        keep.mkdir(exist_ok=True)
        for k in ("S1", "S2"):
            shutil.copyfile(s[k], keep / s[k].name)
    for v in s.values():
        v.unlink(missing_ok=True)
    return res


# --------------------------------------------------------------------------------------------------- console probe
PROBE_QUESTS = {"DeadMoney": ("01005229", "NVDLC01MQ00"), "HonestHearts": ("02008891", "NVDLC02MQ00"),
                "OldWorldBlues": ("03002FCA", "NVDLC03MQ00"), "LonesomeRoad": ("04003603", "NVDLC04MQ00")}


def probe_console(game, plan, outpath):
    """Verify, in game, every console form the questline setup lists use. Writes outpath and prints a table."""
    nv = game.nv
    res = []

    def rec(name, ok, detail=""):
        res.append({"form": name, "ok": bool(ok), "detail": str(detail)[:200]})
        print(("OK   " if ok else "FAIL ") + name + ("  " + str(detail)[:140] if detail else ""))

    def console(cmd):
        r = nv.exec(cmd)
        out = (r.get("output") or "") if isinstance(r, dict) else ""
        bad = (isinstance(r, dict) and r.get("ok") is False) or any(p in out.lower() for p in PARSE_ERRORS)
        return (not bad), out.strip()

    nv.call("test.quest.reset", {"quests": ["000842DD", "01005229", "02008891", "03002FCA", "04003603"]})
    ok, out = console("startquest 000842DD")
    rec("startquest <hex> (base game)", ok and nv.quest_running("000842DD"), out)
    ok, out = console("setstage 000842DD 10")
    rec("setstage <hex> N (base game)", ok and nv.stage("000842DD") == 10, out)
    ok, out = console("completequest 000842DD")
    rec("completequest <hex>", ok and nv.quest_completed("000842DD"), out)
    for dlc, (hx, edid) in PROBE_QUESTS.items():
        ok1, o1 = console("startquest " + hx)
        ok2, o2 = console("setstage %s 10" % hx)
        try:
            st = nv.stage(hx)
        except Exception as e:   # noqa: BLE001
            st = "readback failed: %s" % e
        rec("startquest/setstage on %s prefix %s (%s)" % (dlc, hx[:2], edid), ok1 and ok2 and st == 10, "stage readback %s %s%s" % (st, o1, o2))
    # quest variable: console form vs the helper (readback through the state dump)
    sn = REPO / "out" / "sweeps" / "_probe_state.json"
    sn.parent.mkdir(parents=True, exist_ok=True)

    def var_value(quest_hex, var):
        nv.state_to_file(str(sn))
        d = json.loads(sn.read_text(encoding="utf-8"))
        for ref, qq in d["shared"]["quests"].items():
            if ref.upper().endswith(quest_hex[2:].upper()) and ref.lower().startswith("falloutnv"):
                return (qq.get("vars") or {}).get(var)
        return None

    before = var_value("000842DD", "bPrimmClosed")
    ok, out = console("set 000842DD.bPrimmClosed to 1")
    after_console = var_value("000842DD", "bPrimmClosed")
    rec("set <hexFormID>.<var> to N (console)", ok and after_console == 1, "before %s after %s %s" % (before, after_console, out))
    try:
        nv.call("test.quest.setvar", {"quest": "000842DD", "var": "bPrimmClosed", "value": 0})
        after_helper = var_value("000842DD", "bPrimmClosed")
        rec("set via bridge helper test.quest.setvar (sweep default)", after_helper == 0, "value now %s" % after_helper)
    except Exception as e:   # noqa: BLE001
        rec("set via bridge helper test.quest.setvar (sweep default)", False, e)
    ok, out = console("set VMQ01.bPrimmClosed to 1")
    rec("set <EditorID>.<var> to N (console)", ok and var_value("000842DD", "bPrimmClosed") == 1, out)
    for form in ("player.moveto 03002F88", "player.additem 030104E2 1", "player.addperk 030098BE 1",
                 "setobjectivedisplayed 16A161 45 1", "stopquest 000842DD"):
        ok, out = console(form)
        rec(form + " (parses)", ok, out)
    outpath.parent.mkdir(parents=True, exist_ok=True)
    outpath.write_text(json.dumps({"generated": datetime.now().isoformat(timespec="seconds"), "results": res}, indent=1), encoding="utf-8")
    return res


# --------------------------------------------------------------------------------------------------- output
def write_summary(run_id, plan, results, started, finished):
    runs = REPO / "runs" / run_id
    runs.mkdir(parents=True, exist_ok=True)
    out = []
    for q in plan["quests"]:
        rs = [r for (e, st, v), r in results.items() if e == q["edid"]]
        if not q["stages"]:
            out.append({"quest": q["edid"], "status": "skip", "steps": 0, "failed_steps": [], "duration_s": 0.0})
            continue
        out.append({"quest": q["edid"], "status": sweep_lib.quest_status(rs), "steps": len(rs),
                    "failed_steps": sorted({"stage_%d" % r["stage"] for r in rs if r["status"] in ("fail", "error")}),
                    "duration_s": round(sum(r.get("seconds", 0) for r in rs), 1)})
    summ = {"schema": 1, "run_id": run_id, "kind": "game", "started": started, "finished": finished, "results": out,
            "note": "%s capture sweep: single instance, reload autosave per item, quests reset to pristine; pass = static "
                    "oracle (state delta + hooks capture) satisfied and no unexplained extras" % plan["slug"]}
    (runs / "summary.json").write_text(json.dumps(summ, indent=1), encoding="utf-8")
    return summ


def write_report(outdir, plan, items, results, run_id):
    tot = {}
    for it in items:
        r = results.get((it["edid"], it["stage"], it["variant"]["name"] if it["variant"] else None))
        k = r["status"] if r else "not run"
        tot[k] = tot.get(k, 0) + 1
    excl = [(q["edid"], e) for q in plan["quests"] for e in q["excluded"]]
    L = ["# %s sweep (%s)" % (plan["slug"], run_id), "",
         "config: `%s`; quests %d (%d with stages); runs %d; excluded stages %d" % (
             plan["source"], len(plan["quests"]), sum(1 for q in plan["quests"] if q["stages"]), len(items), len(excl)),
         "", "results: " + ", ".join("%s %d" % kv for kv in sorted(tot.items())), ""]
    if plan["warnings"]:
        L += ["config warnings:"] + ["- " + w for w in plan["warnings"][:20]] + [""]
    L += ["| quest | stage | variant | status | reasons |", "|---|---|---|---|---|"]
    for it in items:
        key = (it["edid"], it["stage"], it["variant"]["name"] if it["variant"] else None)
        r = results.get(key)
        if r is None:
            L.append("| %s | %d | %s | not run | |" % (it["edid"], it["stage"], key[2] or ""))
        elif r["status"] not in ("pass", "noent"):
            L.append("| %s | %d | %s | %s | %s |" % (it["edid"], it["stage"], key[2] or "", r["status"],
                                                    "; ".join(map(str, r.get("reasons", []))) or r.get("detail", "")))
    if excl:
        L += ["", "excluded (not swept):"] + ["- %s %s: %s" % (e, x.get("index"), str(x.get("reason"))[:100]) for e, x in excl[:60]]
    text = "\n".join(L) + "\n"
    (outdir / "report.md").write_text(text, encoding="utf-8")
    return tot


def latest_unfinished(root):
    if not root.exists():
        return None
    runs = sorted([p for p in root.iterdir() if p.is_dir() and (p / "stages").exists()], key=lambda p: p.name)
    for p in reversed(runs):
        if not (p / "done.json").exists():
            return p.name
    return None


def main():
    try:
        sys.stdout.reconfigure(line_buffering=True)   # redirected logs must show progress live
    except Exception:   # noqa: BLE001
        pass
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="")
    ap.add_argument("--run-id", default="")
    ap.add_argument("--resume", action="store_true", help="continue the latest unfinished run of this slug")
    ap.add_argument("--out-dir", default="")
    ap.add_argument("--quests", default="")
    ap.add_argument("--stages", default="")
    ap.add_argument("--save", default="autosave")
    ap.add_argument("--frames", type=int, default=400)
    ap.add_argument("--control", type=float, default=2.0)
    ap.add_argument("--settle", type=float, default=3.0)
    ap.add_argument("--time-budget", type=int, default=2400)
    ap.add_argument("--max-relaunch", type=int, default=8)
    ap.add_argument("--wait", type=int, default=3600)
    ap.add_argument("--reload-every", type=int, default=1,
                    help="load the save once per N stages; in between only test.quest.reset (falls back to a reload after a failing stage or "
                         "one whose capture touched the world: moves, enable/disable, kills, items)")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--finalize", action="store_true")
    ap.add_argument("--probe-console", action="store_true")
    a = ap.parse_args()

    cfg = Path(a.config) if a.config else DEFAULT_CONFIG
    if not cfg.is_absolute():
        cfg = REPO / cfg
    plan = sweep_config.load_config(str(cfg))
    if not a.config:   # Goodsprings template: keep the old ordering (cinematic quests last) and the old output root
        plan["quests"].sort(key=lambda q: (q["edid"] in ORDER_LAST, ORDER_LAST.index(q["edid"]) if q["edid"] in ORDER_LAST else 0))
    only_q = set(a.quests.split(",")) if a.quests else None
    only_s = {int(x) for x in a.stages.split(",")} if a.stages else None
    items = plan_items(plan, only_q, only_s)
    legacy_root = REPO / "out" / "goodsprings_sweep"
    root = Path(a.out_dir) if a.out_dir else (legacy_root if not a.config else REPO / "out" / "sweeps" / plan["slug"])
    run_id = a.run_id
    if not run_id and a.resume:
        run_id = latest_unfinished(root)
    run_id = run_id or (datetime.now().strftime("%Y-%m-%dT%H%M") + "-%s-sweep" % plan["slug"])
    outdir = root / run_id
    sdir = outdir / "stages"
    sdir.mkdir(parents=True, exist_ok=True)
    results = {}
    for it in items:
        f = sdir / (item_name(it["edid"], it["stage"], it["variant"]["name"] if it["variant"] else None) + ".json")
        if f.exists():
            results[(it["edid"], it["stage"], it["variant"]["name"] if it["variant"] else None)] = json.loads(f.read_text(encoding="utf-8"))
    todo = [it for it in items if (it["edid"], it["stage"], it["variant"]["name"] if it["variant"] else None) not in results]
    st = sweep_config.plan_stats(plan)
    print("%s: %d quests (%d with stages), %d stages, %d runs, %d excluded; run %s: %d done, %d to run" % (
        plan["slug"], st["quests"], st["quests_with_stages"], st["stages"], st["runs"], st["excluded"], run_id, len(results), len(todo)))
    for w in plan["warnings"][:5]:
        print("config warning:", w)
    if a.dry_run:
        return 0
    started = datetime.now().isoformat(timespec="seconds")
    if todo or a.probe_console:
        oracle = load_oracle()
        instance = acquire_slot(a.time_budget + 900, a.wait)
        saved_dll = saved_ini = None
        inst_dll = inst_ini = None
        try:
            if not (INSTANCE_ROOT / str(instance) / "Data" / "NVSE" / "Plugins" / "nvcoop.dll").exists():
                lease("release")
                sys.exit("instance %d is not provisioned under %s (tools/runner/run.py provision)" % (instance, INSTANCE_ROOT))
            print("using game slot / instance %d" % instance)
            ensure_save(instance, a.save)
            inst_dll = INSTANCE_ROOT / str(instance) / "Data" / "NVSE" / "Plugins" / "nvcoop.dll"
            inst_ini = inst_dll.with_name("nvcoop.ini")
            saved_dll, saved_ini = inst_dll.read_bytes(), inst_ini.read_bytes()   # restore exactly this afterwards
            shutil.copyfile(REPO / "out" / "L1" / "nvcoop.dll", inst_dll)
            lines = []
            for ln in saved_ini.decode("utf-8", "replace").splitlines():
                t = ln.strip().lower()
                if t.startswith("role="):
                    ln = "role=host"            # capture needs the host role (guest = capture off + suppression gate)
                if t.startswith("enable="):
                    ln = "enable=0"             # coop session stays disabled
                if t.startswith("autoload="):
                    ln = "autoload=" + a.save   # the sweep owns this instance's ini while it holds the slot
                lines.append(ln)
            inst_ini.write_text(chr(10).join(lines) + chr(10))
            # watch list = default watch refs + every ref the oracle mentions for these quests (Enable/Disable/Kill observable)
            wr = REPO / "out" / "snapshot" / "watchrefs.txt"
            refs_all = {x for x in (wr.read_text(encoding="utf-8").split() if wr.exists() else []) if ":" in x and not x.startswith("#")}
            for q in plan["quests"]:
                for e_ in ((oracle["quests"].get(q["edid"]) or {}).get("stages") or {}).values():
                    for e in e_.get("unconditional", []) + e_.get("conditional", []):
                        for k in ("ref", "actor"):
                            if e.get(k):
                                refs_all.add(e[k])
            merged = outdir / "watchrefs.txt"
            merged.write_text("\n".join(sorted(refs_all)) + "\n", encoding="utf-8")
            os.environ["FNVCOOP_WATCHREFS"] = str(merged)
            os.environ["NVCOOP_TEST_HELPERS"] = "1"
            game = Game(a.frames, instance)
        except BaseException:   # any failure while preparing the instance: restore what we touched, free the slot
            if saved_dll is not None:
                restore_files([(inst_dll, saved_dll), (inst_ini, saved_ini)])
            lease("release")
            raise
        t_start = time.time()
        relaunches = 0
        first = True
        since_load, prev_clean = 1, False
        try:
            game.launch()
            hs = game.nv.call("hooks.status")
            print("hooks: role %s installed %s failed %s criticalOk %s" % (hs["role"], hs["installed"], hs["failed"], hs["criticalOk"]))
            names = game.nv.plugins()
            if [n.lower() for n in names[:len(plan["loadorder"])]] != [n.lower() for n in plan["loadorder"][:len(names)]]:
                raise SystemExit("load order mismatch: game %s vs expected %s" % (names, plan["loadorder"]))
            if a.probe_console:
                probe_console(game, plan, REPO / "out" / "sweeps" / "_console_probe.json")
                todo = []
            for it in todo:
                key = (it["edid"], it["stage"], it["variant"]["name"] if it["variant"] else None)
                if (REPO / "out" / "L7tmp" / "PAUSE").exists():
                    print("PAUSE file present; stopping after the last finished stage; continue with --resume")
                    break
                if time.time() - t_start > a.time_budget:
                    print("time budget reached; continue with --resume")
                    break
                try:
                    skip_load = first or (since_load < a.reload_every and prev_clean)
                    since_load = 1 if not skip_load or first else since_load + 1
                    res = run_item(game, plan, it, oracle, outdir, a.save, a.frames, a.control, a.settle, skip_load)
                    first = False
                    prev_clean = res.get("status") == "pass" and not (WORLD_EFFECTS & {e.get("type") for e in res.get("captured", [])})
                except SetupError as e:
                    res = {"quest": it["edid"], "stage": it["stage"], "variant": key[2], "status": "error",
                           "detail": "SetupError: %s" % e, "reasons": ["setup: %s" % str(e)[:160]], "seconds": 0.0}
                    first = False
                    print("SETUP-ERROR %s %d: %s" % (it["edid"], it["stage"], str(e)[:160]))
                except Exception as e:   # noqa: BLE001
                    res = {"quest": it["edid"], "stage": it["stage"], "variant": key[2], "status": "error",
                           "detail": "%s: %s" % (type(e).__name__, e), "reasons": ["%s: %s" % (type(e).__name__, str(e)[:160])], "seconds": 0.0}
                    print("ERROR %s %d: %s" % (it["edid"], it["stage"], res["detail"][:200]))
                    relaunches += 1
                    try:
                        if relaunches > a.max_relaunch:
                            raise RuntimeError("too many relaunches")
                        game.launch()
                        first = True
                    except Exception as e2:   # noqa: BLE001
                        print("stopping: %s" % e2)
                        (sdir / (item_name(it["edid"], it["stage"], key[2]) + ".json")).write_text(json.dumps(res, indent=1), encoding="utf-8")
                        results[key] = res
                        break
                (sdir / (item_name(it["edid"], it["stage"], key[2]) + ".json")).write_text(json.dumps(res, indent=1), encoding="utf-8")
                results[key] = res
                print("%-5s %s %3d %-10s %s  %ss  %s" % (res["status"].upper(), it["edid"], it["stage"], key[2] or "", res.get("counts", ""),
                                                      res.get("seconds"), "; ".join(map(str, res.get("reasons", [])))[:110]))
        finally:
            restore_files([(inst_dll, saved_dll), (inst_ini, saved_ini)], game)
            lease("release")
    done = sum(1 for it in items if (it["edid"], it["stage"], it["variant"]["name"] if it["variant"] else None) in results)
    tot = write_report(outdir, plan, items, results, run_id)
    if a.config:
        shutil.copyfile(outdir / "report.md", root / "report.md")
    print("results: %s  (%d of %d runs have a result)" % (tot, done, len(items)))
    if done == len(items) or a.finalize:
        (outdir / "done.json").write_text(json.dumps({"finished": datetime.now().isoformat(timespec="seconds"), "totals": tot}), encoding="utf-8")
        summ = write_summary(run_id, plan, results, started, datetime.now().isoformat(timespec="seconds"))
        print("runs/%s/summary.json written: %s" % (run_id, [(r["quest"], r["status"]) for r in summ["results"] if r["status"] != "skip"][:12]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
