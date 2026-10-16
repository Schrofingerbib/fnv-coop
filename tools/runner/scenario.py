#!/usr/bin/env python3
"""Scenario runner (L2): executes L3 quest scenarios (out/questdata/scenarios/*.json) against two instances.

Per step: the host runs step["host"] (console command), the host waits for step["wait"] (an NVSE
expression, polled via nvbridge waitFor), then every `expect.both` item is checked on the HOST immediately
and on the CLIENT with a sync timeout. Steps of `manual: true` quests are skipped. A CLIENT check that
fails (or times out) fails the step: that is the whole point of the co-op test.

    python scenario.py --quest VMS01 [--host 0 --client 1] [--out runs/x]
    python scenario.py --all --tier T0,T1 --junit out.xml --json out.json

Clients are duck-typed (exec, wait_for, state) so tests use in-process fakes; real runs use nvbridge.NvBridge.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
REPO = HERE.parents[1]
SCENARIO_DIR = REPO / "out" / "questdata" / "scenarios"


sys.path.insert(0, str(REPO / "tools" / "questdata"))
import loadorder  # noqa: E402


class LoadOrderError(Exception):
    pass


def check_load_order(bridge, who: str = "") -> None:
    """Fail fast if the instance's real plugin order differs from tools/questdata/loadorder.json
    (scenario FormIDs would then address the wrong forms). Uses the bridge `plugins` method."""
    try:
        r = bridge.call("plugins")
    except Exception as e:  # noqa: BLE001
        raise LoadOrderError(f"{who}: bridge `plugins` call failed ({type(e).__name__}: {e}); "
                             "cannot verify load order (use --skip-loadorder-check to override)") from e
    items = r.get("plugins", r) if isinstance(r, dict) else r
    if items and isinstance(items[0], dict) and "index" in items[0]:
        items = sorted(items, key=lambda p: p["index"])
        idx = [p["index"] for p in items]
        if idx != list(range(len(idx))):
            raise LoadOrderError(f"{who}: plugin indices are not contiguous from 0: {idx}")
    names = [p["name"] if isinstance(p, dict) else p for p in items]
    try:
        loadorder.verify(names)
    except loadorder.LoadOrderMismatch as e:
        raise LoadOrderError(f"{who}: {e}") from e


class CheckFailed(Exception):
    pass


@dataclass
class Check:
    who: str
    what: str
    ok: bool
    detail: str = ""


@dataclass
class StepResult:
    scenario: str
    step: str
    status: str = "pass"  # pass | fail | error | skipped
    checks: List[Check] = field(default_factory=list)
    message: str = ""
    duration_s: float = 0.0


def _is_timeout(e: Exception) -> bool:
    return type(e).__name__ == "WaitTimeout"


def expectation_exprs(both: Dict[str, Any], formid: Optional[str] = None, edid: Optional[str] = None) -> List[tuple]:
    """Translate L3 `expect.both` into (label, expr, op, value) tuples evaluated with GetXxx functions.

    The in-game console does not resolve EDIDs, so every quest reference in an expression is the hex
    FormID (`formid`, e.g. "080664" / "01011DF4"). EDIDs only appear in labels (display)."""
    fid = lambda q: formid if (formid and (edid is None or q == edid)) else q  # noqa: E731
    out = []
    if "stage" in both:
        s = both["stage"]
        out.append((f"stage {s['quest']}>={s['value']}", f"GetStageDone {fid(s['quest'])} {s['value']}", "truthy", 0))
    if "quest_running" in both:
        q = both["quest_running"]
        out.append((f"running {q}", f"GetQuestRunning {fid(q)}", "truthy", 0))
    q = both.get("stage", {}).get("quest") or both.get("quest_running") or both.get("quest")
    if "objectives" in both and q:
        for idx in both["objectives"].get("displayed", []):
            out.append((f"objective {idx} displayed", f"GetObjectiveDisplayed {fid(q)} {idx}", "truthy", 0))
        for idx in both["objectives"].get("completed", []):
            out.append((f"objective {idx} completed", f"GetObjectiveCompleted {fid(q)} {idx}", "truthy", 0))
    if both.get("quest_completed") and q:
        out.append((f"completed {q}", f"GetQuestCompleted {fid(q)}", "truthy", 0))
    if both.get("quest_failed") and q:
        out.append((f"failed {q}", f"GetQuestFailed {fid(q)}", "truthy", 0))
    return out


class SnapshotChecks:
    """Drives L7's CLIs by exit code (0 ok, 1 diff/fail, 2 invalid input):
    snapdiff.py HOST_AFTER CLIENT_AFTER             host and client agree on the shared sections
    check_stage.py --scenarios DIR Q N BEFORE AFTER  the stage produced the statically expected effects
    run_cli(argv) -> (exit_code, text); injectable for tests."""

    def __init__(self, workdir, scen_dir=SCENARIO_DIR, run_cli=None, tools_dir=None):
        self.work = Path(workdir)
        self.work.mkdir(parents=True, exist_ok=True)
        self.scen_dir = Path(scen_dir)
        self.tools = Path(tools_dir or REPO / "plugin" / "snapshot" / "tools")
        self.run_cli = run_cli or self._subproc
        self.before = None

    @staticmethod
    def _subproc(argv):
        import subprocess
        cp = subprocess.run([sys.executable, "-I"] + [str(x) for x in argv], capture_output=True, text=True)
        return cp.returncode, (cp.stdout + cp.stderr)[-2000:]

    def _dump(self, name, snap):
        p = self.work / f"{name}.json"
        p.write_text(json.dumps(snap))
        return p

    def begin_step(self, sc, step, host):
        self.before = self._dump(f"{sc['quest']}.{step['id']}.host_before", host.state())

    def end_step(self, sc, step, host, client):
        tag = f"{sc['quest']}.{step['id']}"
        ha = self._dump(tag + ".host_after", host.state())
        ca = self._dump(tag + ".client_after", client.state())
        out = []
        code, txt = self.run_cli([self.tools / "snapdiff.py", ha, ca])
        out.append(Check("both", "snapdiff host==client", code == 0, "" if code == 0 else f"exit {code}: {txt[:300]}"))
        if step["id"].startswith("stage_") and self.before is not None:
            n = step["id"].split("_", 1)[1]
            code, txt = self.run_cli([self.tools / "check_stage.py", "--scenarios", self.scen_dir, sc["quest"], n, self.before, ha])
            out.append(Check("host", f"side_effects stage {n}", code == 0, "" if code == 0 else f"exit {code}: {txt[:300]}"))
        return out


class Runner:
    def __init__(self, host, client, *, sync_timeout_ms: int = 5000, host_timeout_ms: int = 5000,
                 snapshot_diff: Optional[Callable[[Any, Any], List[str]]] = None,
                 snapshots: Optional[SnapshotChecks] = None, _mutant: Optional[str] = None):
        self.host, self.client = host, client
        self.sync_timeout_ms, self.host_timeout_ms = sync_timeout_ms, host_timeout_ms
        self.snapshot_diff = snapshot_diff
        self.snapshots = snapshots
        self._mutant = _mutant  # test-only: "skip_client" (never checks the client), "client_checks_host"

    def _check(self, who: str, nv, label: str, expr: str, op: str, value, timeout_ms: int) -> Check:
        try:
            nv.wait_for(expr, op=op, value=value, timeout_ms=timeout_ms)
            return Check(who, label, True)
        except Exception as e:  # noqa: BLE001
            return Check(who, label, False, ("timeout: " if _is_timeout(e) else "error: ") + str(e))

    def run_step(self, sc: Dict, step: Dict) -> StepResult:
        r = StepResult(sc["quest"], step["id"])
        t0 = time.monotonic()
        try:
            if self.snapshots:
                self.snapshots.begin_step(sc, step, self.host)
            res = self.host.exec(step["host"])
            if res.get("ok") is False:
                raise CheckFailed(f"host exec failed: {res.get('output')}")
            if step.get("wait"):
                r.checks.append(self._check("host", self.host, "wait " + step["wait"], step["wait"], "truthy", 0, self.host_timeout_ms))
            for label, expr, op, val in expectation_exprs(step.get("expect", {}).get("both", {}), sc.get("formid"), sc.get("quest")):
                r.checks.append(self._check("host", self.host, label, expr, op, val, self.host_timeout_ms))
                if self._mutant != "skip_client":
                    target = self.host if self._mutant == "client_checks_host" else self.client
                    r.checks.append(self._check("client", target, label, expr, op, val, self.sync_timeout_ms))
            if self.snapshot_diff and self._mutant != "skip_client":
                diffs = self.snapshot_diff(self.host.state(), self.client.state())
                r.checks.append(Check("both", "snapshot equal", not diffs, "; ".join(diffs[:5])))
            if self.snapshots and self._mutant != "skip_client":
                r.checks += self.snapshots.end_step(sc, step, self.host, self.client)
            bad = [c for c in r.checks if not c.ok]
            if bad:
                r.status = "fail"
                r.message = "; ".join(f"{c.who}:{c.what} ({c.detail})" for c in bad[:4])
        except CheckFailed as e:
            r.status, r.message = "fail", str(e)
        except Exception as e:  # noqa: BLE001
            r.status, r.message = "error", f"{type(e).__name__}: {e}"
        r.duration_s = round(time.monotonic() - t0, 3)
        return r

    def run_scenario(self, sc: Dict, stop_on_fail: bool = True) -> List[StepResult]:
        if sc.get("manual"):
            return [StepResult(sc["quest"], "*", "skipped", message="manual (T4) scenario")]
        out: List[StepResult] = []
        for step in sc["steps"]:
            r = self.run_step(sc, step)
            out.append(r)
            if stop_on_fail and r.status != "pass":
                break
        return out


# ------------------------------------------------------------------ reporting
POLICY_TAGS = {}  # quest -> L3 policy_checks labels (descriptive, not executable; reported only)


def summarize(results: List[StepResult]) -> Dict:
    by: Dict[str, Dict[str, int]] = {}
    for r in results:
        d = by.setdefault(r.scenario, {"pass": 0, "fail": 0, "error": 0, "skipped": 0})
        d[r.status] += 1
    tot = {k: sum(d[k] for d in by.values()) for k in ("pass", "fail", "error", "skipped")}
    return {"totals": tot, "policy_checks_unevaluated": dict(POLICY_TAGS), "scenarios": by, "ok": tot["fail"] == 0 and tot["error"] == 0,
            "steps": [asdict(r) for r in results]}


def write_junit(results: List[StepResult], path: Path) -> None:
    s = summarize(results)["totals"]
    root = ET.Element("testsuites")
    suite = ET.SubElement(root, "testsuite", name="fnv-coop-scenarios", tests=str(len(results)),
                          failures=str(s["fail"]), errors=str(s["error"]), skipped=str(s["skipped"]),
                          time=str(round(sum(r.duration_s for r in results), 3)))
    for r in results:
        tc = ET.SubElement(suite, "testcase", classname=r.scenario, name=r.step, time=str(r.duration_s))
        if r.status == "fail":
            ET.SubElement(tc, "failure", message=r.message[:500]).text = r.message
        elif r.status == "error":
            ET.SubElement(tc, "error", message=r.message[:500]).text = r.message
        elif r.status == "skipped":
            ET.SubElement(tc, "skipped", message=r.message)
    ET.ElementTree(root).write(path, encoding="utf-8", xml_declaration=True)


def write_summary(results: List[StepResult], path: Path, run_id: str, kind: str, started: str, finished: str) -> None:
    """runs/<run-id>/summary.json for the L9 coverage dashboard (schema 1, see internal lane notes)."""
    by: Dict[str, List[StepResult]] = {}
    for r in results:
        by.setdefault(r.scenario, []).append(r)
    rows = []
    for q, rs in by.items():
        failed = [r.step for r in rs if r.status in ("fail", "error")]
        if any(r.status == "error" for r in rs):
            st = "error"
        elif failed:
            st = "fail"
        elif all(r.status == "skipped" for r in rs):
            st = "skip"
        else:
            st = "pass"
        rows.append({"quest": q, "status": st, "steps": len(rs), "failed_steps": failed,
                     "duration_s": round(sum(r.duration_s for r in rs), 2)})
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"schema": 1, "run_id": run_id, "kind": kind, "started": started,
                                "finished": finished, "results": rows}, indent=1))


def load_scenarios(names: Optional[List[str]] = None, tiers: Optional[List[str]] = None,
                   directory: Path = SCENARIO_DIR) -> List[Dict]:
    out = []
    for f in sorted(Path(directory).glob("*.json")):
        if f.name.startswith("_"):
            continue
        if names and f.stem not in names:
            continue
        sc = json.loads(f.read_text(encoding="utf-8"))
        if tiers and sc.get("tier") not in tiers:
            continue
        out.append(sc)
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--quest", action="append", help="scenario EDID (repeatable)")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--tier", help="comma list, e.g. T0,T1")
    ap.add_argument("--host", type=int, default=0)
    ap.add_argument("--client", type=int, default=1)
    ap.add_argument("--sync-timeout-ms", type=int, default=5000)
    ap.add_argument("--load-save", help="save to load on both instances first")
    ap.add_argument("--dir", default=str(SCENARIO_DIR))
    ap.add_argument("--junit")
    ap.add_argument("--json")
    ap.add_argument("--skip-loadorder-check", action="store_true")
    ap.add_argument("--snapshots", action="store_true", help="run L7 snapdiff/check_stage CLIs per step")
    ap.add_argument("--run-id", help="write runs/<run-id>/summary.json (L9 format)")
    ap.add_argument("--kind", default="e2e", choices=["sim", "e2e", "playtest"])
    ap.add_argument("--keep-going", action="store_true", help="do not stop a scenario at its first failed step")
    a = ap.parse_args(argv)
    if not a.quest and not a.all:
        ap.error("--quest or --all required")
    scs = load_scenarios(a.quest, a.tier.split(",") if a.tier else None, Path(a.dir))
    if not scs:
        print("no scenarios matched", file=sys.stderr)
        return 2
    import nvbridge
    snap = None
    try:  # L7 snapshot diff, optional
        sys.path.insert(0, str(REPO / "plugin" / "snapshot" / "tools"))
        import snapdiff  # type: ignore
        snap = getattr(snapdiff, "diff_lines", None)
    except Exception:  # noqa: BLE001
        pass
    results: List[StepResult] = []
    started = time.strftime("%Y-%m-%dT%H:%M:%S")
    with nvbridge.NvBridge.connect(instance=a.host, wait_s=60) as h, nvbridge.NvBridge.connect(instance=a.client, wait_s=60) as c:
        if not a.skip_loadorder_check:
            try:
                check_load_order(h, "host")
                check_load_order(c, "client")
            except LoadOrderError as e:
                print(f"LOAD ORDER CHECK FAILED: {e}", file=sys.stderr)
                return 2
        if a.load_save:
            h.load_save(a.load_save)
            c.load_save(a.load_save)
        snaps = SnapshotChecks(REPO / "runs" / (a.run_id or "adhoc") / "snapshots", Path(a.dir)) if a.snapshots else None
        run = Runner(h, c, sync_timeout_ms=a.sync_timeout_ms, snapshot_diff=None if snaps else snap, snapshots=snaps)
        for sc in scs:
            POLICY_TAGS[sc["quest"]] = sc.get("policy_checks", [])
            results += run.run_scenario(sc, stop_on_fail=not a.keep_going)
    summ = summarize(results)
    if a.run_id:
        write_summary(results, REPO / "runs" / a.run_id / "summary.json", a.run_id, a.kind, started,
                      time.strftime("%Y-%m-%dT%H:%M:%S"))
    if a.junit:
        write_junit(results, Path(a.junit))
    if a.json:
        Path(a.json).write_text(json.dumps(summ, indent=1))
    print(json.dumps(summ["totals"]))
    return 0 if summ["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
