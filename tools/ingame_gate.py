#!/usr/bin/env python3
"""In-game regression gate: the mandatory check after ANY nvcoop.dll change (game-free suites cannot see
in-process crashes such as the bridge `state` access violation).

    python -I -X utf8 tools/ingame_gate.py [--dll out/L1/nvcoop.dll] [--skip-coop] [--keep]

Under the game-slot lease it (1) builds a FRESH hardlinked instance under <repo>/../fnv-instances-gate with the
given dll, autoload=autosave, (2) launches it minimized, (3) asserts:
  loadorder     bridge `plugins` == tools/questdata/loadorder.json
  version       bridge.version.gitHash is a prefix/extension of `git rev-parse HEAD` (dirty build is a warning)
  hooks         hooks.status: installed > 0, failed == 0, criticalOk, no hook reporting failure
  state         `state` returns >= 50 quests, >= 1000 objectives, >= 10 globals (and passes L7's snaplib.guard)
  capture       capture.begin; exec `setstage 10A214 10`; capture.end has a txn with SetStage 10A214 10
(4) quits cleanly (bridge quit, process exits by itself), then (5) runs L10's coop host test
(plugin/coop/tools/ingame_host_test.py, own instance + dll) unless --skip-coop; (6) releases the lease.
Output: JSON on stdout, runs/<id>/gate.json + runs/<id>/summary.json (L9 schema 1, no quest rows), exit 0/1/2.
Exit 2 = could not run (slot busy, game already running, launch failure).
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

REPO = Path(__file__).resolve().parents[1]
GATE_ROOT = REPO.parent / "fnv-instances-gate"
sys.path.insert(0, str(REPO / "tools" / "runner"))
sys.path.insert(0, str(REPO / "tools" / "questdata"))
sys.path.insert(0, str(REPO / "plugin" / "snapshot" / "tools"))

MIN_QUESTS, MIN_OBJECTIVES, MIN_GLOBALS = 50, 1000, 10
CAPTURE_CMD, CAPTURE_QUEST, CAPTURE_STAGE = "setstage 10A214 10", "10A214", 10


# ----------------------------------------------------------------- pure checks (unit-tested without a game)
class Result:
    def __init__(self, name: str, ok: bool, detail: str = "", warn: str = ""):
        self.name, self.ok, self.detail, self.warn = name, bool(ok), detail, warn

    def as_dict(self) -> Dict[str, Any]:
        d = {"check": self.name, "ok": self.ok, "detail": self.detail}
        if self.warn:
            d["warn"] = self.warn
        return d


def check_loadorder(reply: Any) -> Result:
    import scenario
    try:
        scenario.check_load_order(type("B", (), {"call": lambda s, m, p=None: reply})(), "gate")
        return Result("loadorder", True, "plugins match loadorder.json")
    except scenario.LoadOrderError as e:
        return Result("loadorder", False, str(e))


def check_version(reply: Dict[str, Any], head: str) -> Result:
    h = str(reply.get("gitHash", "")).strip().lower()
    head = head.strip().lower()
    if not h or h in ("unknown", "none"):
        return Result("version", False, f"bridge.version has no gitHash: {reply}")
    if reply.get("mutant"):
        return Result("version", False, "dll is a MUTANT build")
    n = min(len(h), len(head))
    ok = n >= 7 and h[:n] == head[:n]
    warn = "dll built from a dirty tree" if reply.get("dirty") else ""
    return Result("version", ok, f"dll {h} vs HEAD {head[:12]}", warn)


def check_hooks(st: Dict[str, Any]) -> Result:
    bad = [h for h in st.get("hooks", []) if "fail" in str(h.get("status", "")).lower()]
    ok = st.get("installed", 0) > 0 and st.get("failed", 1) == 0 and st.get("criticalOk") is True and not bad
    return Result("hooks", ok, f"installed={st.get('installed')} failed={st.get('failed')} "
                               f"criticalOk={st.get('criticalOk')} bad={[h.get('name') for h in bad]}")


def check_state(snap: Dict[str, Any], schema: bool = True) -> Result:
    shared = snap.get("shared") if isinstance(snap, dict) else None
    shared = shared if isinstance(shared, dict) else {}
    quests = shared.get("quests") if isinstance(shared.get("quests"), dict) else {}
    globs = shared.get("globals") if isinstance(shared.get("globals"), dict) else {}
    objs = sum(len(q.get("objectives", {})) for q in quests.values() if isinstance(q, dict))
    probs = []
    if len(quests) < MIN_QUESTS:
        probs.append(f"quests {len(quests)} < {MIN_QUESTS}")
    if objs < MIN_OBJECTIVES:
        probs.append(f"objectives {objs} < {MIN_OBJECTIVES}")
    if len(globs) < MIN_GLOBALS:
        probs.append(f"globals {len(globs)} < {MIN_GLOBALS}")
    try:
        import snaplib
        probs += snaplib.guard(snap, check_schema=schema)[:3]
    except ImportError:
        pass
    return Result("state", not probs, f"quests={len(quests)} objectives={objs} globals={len(globs)}"
                                      + ("; " + "; ".join(probs) if probs else ""))


def check_capture(end: Dict[str, Any], quest: str = CAPTURE_QUEST, stage: int = CAPTURE_STAGE) -> Result:
    txns = end.get("txns", []) if isinstance(end, dict) else []
    for t in txns:
        for e in t.get("effects", []):
            if e.get("type") == "SetStage" and e.get("stage") == stage and \
                    str(e.get("quest", "")).lower().endswith(quest.lower().lstrip("0")):
                return Result("capture", True, f"txn with SetStage {quest} {stage} ({len(txns)} txn(s))")
    return Result("capture", False, f"no SetStage {quest} {stage} in {len(txns)} txn(s): "
                                    + json.dumps(txns)[:300])


# ----------------------------------------------------------------- game side
def git_head() -> str:
    return subprocess.run(["git", "rev-parse", "HEAD"], cwd=REPO, capture_output=True, text=True).stdout.strip()


def provision_gate(dll: Path) -> Path:
    os.environ["FNV_INSTANCES_ROOT"] = str(GATE_ROOT)
    import instances
    instances.destroy(0)
    instances.provision(0, role="host", plugin_dll=dll, autoload="autosave", autoload_delay_frames=600,
                        extra_ini={"coop": {"enable": "0"}})
    saves = instances.instance_dir(0) / "Saves"
    import mygames
    src = mygames.saves_dir() / "autosave.fos"
    if not src.exists():
        raise FileNotFoundError(f"no autosave to load: {src}")
    shutil.copy2(src, saves / "autosave.fos")
    return instances.instance_dir(0)


def run_game_checks(dll: Path, out: Dict[str, Any], t_budget: float = 240.0) -> None:
    import nvbridge
    import run as runner
    import winproc
    results: List[Result] = []
    out["checks"] = []
    pid = None
    nv = None
    try:
        provision_gate(dll)
        pid = runner.launch_instance(0)
        st = runner.load_state()
        st["pids"]["0"] = pid
        runner.save_state(st)
        nv = nvbridge.NvBridge.connect(0, wait_s=t_budget)
        nv.wait_event("autoload.done", timeout_s=t_budget)
        base = nv.ping()["frames"]
        t0 = time.time()
        while nv.ping()["frames"] - base < 600 and time.time() - t0 < 60:
            time.sleep(0.25)

        def guarded(name, fn):
            try:
                results.append(fn())
            except Exception as e:  # noqa: BLE001  (an exception in the bridge call IS a gate failure)
                results.append(Result(name, False, f"{type(e).__name__}: {e}"))
        guarded("loadorder", lambda: check_loadorder(nv.call("plugins")))
        guarded("version", lambda: check_version(nv.call("bridge.version"), git_head()))
        guarded("hooks", lambda: check_hooks(nv.call("hooks.status")))
        guarded("state", lambda: check_state(nv.state()))

        def cap():
            nv.call("capture.begin")
            nv.exec(CAPTURE_CMD)
            time.sleep(1.0)
            return check_capture(nv.call("capture.end"))
        guarded("capture", cap)
        alive = nv.ping()
        results.append(Result("alive-after-checks", alive.get("pong") is True, f"frames={alive.get('frames')}"))
    except Exception as e:  # noqa: BLE001
        results.append(Result("launch/connect", False, f"{type(e).__name__}: {e}"))
        out["setup_failed"] = True
    finally:
        clean = True
        if nv is not None:
            try:
                nv.quit()
            except Exception:  # noqa: BLE001
                pass
        if pid:
            for _ in range(80):
                if not winproc.pid_alive(pid):
                    break
                time.sleep(0.5)
            else:
                clean = False
                winproc.kill_pid(pid)
        results.append(Result("clean-quit", clean, "process exited by itself after quit" if clean else "had to kill"))
        try:
            st = runner.load_state()
            st["pids"].pop("0", None)
            runner.save_state(st)
        except Exception:  # noqa: BLE001
            pass
    out["checks"] += [r.as_dict() for r in results]


def run_coop_test(out: Dict[str, Any], timeout_s: float = 300) -> None:
    script = REPO / "plugin" / "coop" / "tools" / "ingame_host_test.py"
    ini = REPO.parent / "fnv-instances-l10" / "0" / "Data" / "NVSE" / "Plugins" / "nvcoop.ini"
    if ini.exists():  # the test needs coop enabled in L10's private instance
        ini.write_text("\n".join(("enable=1" if l.startswith("enable=") else l) for l in ini.read_text().splitlines()) + "\n")
    try:
        cp = subprocess.run([sys.executable, "-I", "-X", "utf8", str(script)], capture_output=True, text=True,
                            timeout=timeout_s, cwd=REPO)
        ok, tail = cp.returncode == 0, (cp.stdout + cp.stderr).strip().splitlines()[-6:]
    except subprocess.TimeoutExpired:
        ok, tail = False, ["timeout"]
    finally:
        pass  # the child test kills only its own instance; never kill other lanes' games
    out["checks"].append(Result("coop-host-test", ok, " | ".join(tail)).as_dict())


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dll", default=str(REPO / "out" / "L1" / "nvcoop.dll"))
    ap.add_argument("--skip-coop", action="store_true")
    ap.add_argument("--slot-wait", type=int, default=3600)
    a = ap.parse_args(argv)
    import run as runner
    import winproc
    started = time.strftime("%Y-%m-%dT%H:%M:%S")
    run_id = time.strftime("%Y-%m-%dT%H%M") + "-gate"
    out: Dict[str, Any] = {"run_id": run_id, "dll": a.dll, "head": git_head(), "started": started}
    dll = Path(a.dll)
    if not dll.exists():
        print(json.dumps({"error": f"dll not found: {dll}"}))
        return 2
    if winproc.list_pids("FalloutNV.exe"):
        print(json.dumps({"error": "FalloutNV.exe already running", "pids": winproc.list_pids("FalloutNV.exe")}))
        return 2
    try:
        runner.slot_acquire("L2-gate", ttl=900, wait=a.slot_wait)
    except runner.SlotBusy as e:
        print(json.dumps({"error": f"game slot busy: {e}"}))
        return 2
    t0 = time.time()
    try:
        run_game_checks(dll, out)
        if not a.skip_coop and not out.get("setup_failed"):
            run_coop_test(out)
    finally:
        runner.slot_release("L2-gate")
    out["duration_s"] = round(time.time() - t0, 1)
    out["finished"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    out["ok"] = all(c["ok"] for c in out["checks"]) and bool(out["checks"])
    d = REPO / "runs" / run_id
    d.mkdir(parents=True, exist_ok=True)
    (d / "gate.json").write_text(json.dumps(out, indent=1))
    (d / "summary.json").write_text(json.dumps({"schema": 1, "run_id": run_id, "kind": "e2e", "started": started,
                                                "finished": out["finished"], "results": [], "gate": out}, indent=1))
    print(json.dumps(out, indent=1))
    return 0 if out["ok"] else (2 if out.get("setup_failed") else 1)


if __name__ == "__main__":
    sys.exit(main())
