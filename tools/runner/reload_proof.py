#!/usr/bin/env python3
"""In-game proof of hot reload (T3b-state). The lead runs this, one game at a time, against a game that runs the SPLIT
build (nvcoop.dll = loader, nvcoop_mod.dll = reloadable core; cmake -DNVCOOP_SPLIT_LOADER=ON). It does not launch the game
and does not take the game slot: start an instance under the lease first, then

    python -I -X utf8 tools/runner/reload_proof.py --instance 0 [--cycles 10] [--out runs/reload_proof.json]

For every cycle it reads the game's own view before and after `dev.reload` and checks, from OUTSIDE the game process:
  reload        dev.reload answered ok, not rolled back, the generation went up by exactly one
  hooks         hooks.status: same installed count, failed == 0, criticalOk; reload.state liveHooks == installed hooks (none leaked, none lost)
  threads       reload.state liveThreads unchanged; the game process's real thread count back to where it was
  handles       the game process handle count did not creep (<= start + 8 over the whole run)
  memory        working set growth over the whole run stays under --max-growth-mb (default 150)
  session       coop.status: running, role and client token unchanged (the session survived)
  participants  reload.state lists the same participants at the same versions, restored > 0, no failures and no leaks reported
  capture       capture.begin / exec / capture.end still records a SetStage after the reload (the hooks really work, not just exist);
                skip with --no-capture-probe
Output: JSON on stdout (and --out). Exit 0 = all checks passed, 1 = a check failed, 2 = could not run (no bridge, core not split).
The checks are pure functions over plain dicts, unit-tested with a fake game and mutation twins in tools/runner/tests/test_reload_proof.py.
"""
from __future__ import annotations

import argparse
import ctypes
import json
import sys
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

CAPTURE_CMD, CAPTURE_QUEST, CAPTURE_STAGE = "setstage 10A214 10", "10A214", 10
HANDLE_SLACK = 8
DEFAULT_MAX_GROWTH_MB = 150


class Result:
    def __init__(self, name: str, ok: bool, detail: str = ""):
        self.name, self.ok, self.detail = name, bool(ok), detail

    def as_dict(self) -> Dict[str, Any]:
        return {"check": self.name, "ok": self.ok, "detail": self.detail}


# ----------------------------------------------------------------------------------- process probe (real, via ctypes)
class ProcProbe:
    """Thread / handle / working-set counts of another process. Replaced by a fake in tests."""

    def __init__(self, pid: int):
        self.pid = pid

    def threads(self) -> int:
        from ctypes import wintypes

        class TE(ctypes.Structure):
            _fields_ = [("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD), ("th32ThreadID", wintypes.DWORD),
                        ("th32OwnerProcessID", wintypes.DWORD), ("tpBasePri", wintypes.LONG), ("tpDeltaPri", wintypes.LONG),
                        ("dwFlags", wintypes.DWORD)]

        k = ctypes.WinDLL("kernel32", use_last_error=True)
        k.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
        snap = k.CreateToolhelp32Snapshot(0x4, 0)  # TH32CS_SNAPTHREAD
        n = 0
        te = TE()
        te.dwSize = ctypes.sizeof(TE)
        ok = k.Thread32First(snap, ctypes.byref(te))
        while ok:
            if te.th32OwnerProcessID == self.pid:
                n += 1
            ok = k.Thread32Next(snap, ctypes.byref(te))
        k.CloseHandle(snap)
        return n

    def handles(self) -> int:
        from ctypes import wintypes
        k = ctypes.WinDLL("kernel32", use_last_error=True)
        k.OpenProcess.restype = wintypes.HANDLE
        h = k.OpenProcess(0x1000, False, self.pid)
        if not h:
            return -1
        cnt = wintypes.DWORD(0)
        k.GetProcessHandleCount(h, ctypes.byref(cnt))
        k.CloseHandle(h)
        return int(cnt.value)

    def working_set_mb(self) -> float:
        from ctypes import wintypes

        class PMC(ctypes.Structure):
            _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD), ("PeakWorkingSetSize", ctypes.c_size_t),
                        ("WorkingSetSize", ctypes.c_size_t), ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                        ("QuotaPagedPoolUsage", ctypes.c_size_t), ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                        ("QuotaNonPagedPoolUsage", ctypes.c_size_t), ("PagefileUsage", ctypes.c_size_t),
                        ("PeakPagefileUsage", ctypes.c_size_t)]

        k = ctypes.WinDLL("kernel32", use_last_error=True)
        k.OpenProcess.restype = wintypes.HANDLE
        h = k.OpenProcess(0x1000, False, self.pid)
        if not h:
            return -1.0
        p = ctypes.WinDLL("psapi", use_last_error=True)
        pmc = PMC()
        pmc.cb = ctypes.sizeof(PMC)
        p.GetProcessMemoryInfo(h, ctypes.byref(pmc), pmc.cb)
        k.CloseHandle(h)
        return pmc.WorkingSetSize / (1024 * 1024)


# ----------------------------------------------------------------------------------- snapshots and pure checks
def snapshot(nv, probe: Optional[Any]) -> Dict[str, Any]:
    """Everything the checks need, read through the bridge (and the process probe) at one moment."""
    s: Dict[str, Any] = {"t": time.time()}
    s["hooks"] = nv.call("hooks.status")
    s["reload"] = nv.call("reload.state")
    s["dev"] = nv.call("dev.status")
    try:
        s["coop"] = nv.call("coop.status")
    except Exception as e:  # coop disabled in this ini: not an error, the session check is then skipped
        s["coop"] = {"unavailable": str(e)}
    if probe is not None:
        s["proc"] = {"threads": probe.threads(), "handles": probe.handles(), "ws_mb": probe.working_set_mb()}
    return s


def _gen(dev: Dict[str, Any]) -> int:
    return int(dev.get("generation", -1)) if isinstance(dev, dict) else -1


def check_reload_reply(before: Dict[str, Any], after: Dict[str, Any], reply: Any, error: str = "") -> Result:
    """dev.reload answers {generation, msUnload, msLoad} on success and an RPC error (text) on any failure."""
    ok = not error and isinstance(reply, dict) and "generation" in reply
    gb, ga = _gen(before.get("dev", {})), _gen(after.get("dev", {}))
    last = after.get("dev", {}).get("last", {}) if isinstance(after.get("dev"), dict) else {}
    ok = ok and ga == gb + 1 and not last.get("rolledBack") and not last.get("coreDead")
    return Result("reload", ok, f"error={error!r} reply={reply!r} generation {gb}->{ga} rolledBack={last.get('rolledBack')} coreDead={last.get('coreDead')}")


def check_hooks(before: Dict[str, Any], after: Dict[str, Any]) -> Result:
    hb, ha = before.get("hooks", {}), after.get("hooks", {})
    live = after.get("reload", {}).get("liveHooks")
    bad = [h.get("name") for h in ha.get("hooks", []) if "fail" in str(h.get("status", "")).lower()]
    probs = []
    if ha.get("installed") != hb.get("installed"):
        probs.append(f"installed {hb.get('installed')} -> {ha.get('installed')}")
    if ha.get("failed", 1) != 0 or bad:
        probs.append(f"failed={ha.get('failed')} {bad}")
    if ha.get("criticalOk") is not True:
        probs.append("criticalOk false")
    # liveHooks counts every module's hooks (L5 + L11 + L12), so it can exceed hooks.status.installed (L5 only), never be lower.
    if not isinstance(live, int) or live < int(ha.get("installed", 0)):
        probs.append(f"liveHooks={live} < installed={ha.get('installed')}")
    lb = before.get("reload", {}).get("liveHooks")
    if isinstance(lb, int) and live != lb:
        probs.append(f"liveHooks {lb} -> {live} (leaked or lost)")
    return Result("hooks", not probs, "; ".join(probs) or f"installed={ha.get('installed')} liveHooks={live}")


def check_threads(before: Dict[str, Any], after: Dict[str, Any]) -> Result:
    tb, ta = before.get("reload", {}).get("liveThreads"), after.get("reload", {}).get("liveThreads")
    probs = []
    if tb != ta:
        probs.append(f"core threads {tb} -> {ta}")
    pb, pa = before.get("proc"), after.get("proc")
    if pb and pa and pa["threads"] > pb["threads"]:
        probs.append(f"process threads {pb['threads']} -> {pa['threads']}")
    return Result("threads", not probs, "; ".join(probs) or f"core={ta}" + (f" process={pa['threads']}" if pa else ""))


def check_participants(before: Dict[str, Any], after: Dict[str, Any]) -> Result:
    rb, ra = before.get("reload", {}), after.get("reload", {})
    probs = []
    if sorted(map(json.dumps, rb.get("participants", []))) != sorted(map(json.dumps, ra.get("participants", []))):
        probs.append("participant list or versions changed")
    if not ra.get("participants"):
        probs.append("no participants registered (reload registry not built into the core?)")
    if ra.get("restored", 0) <= 0:
        probs.append(f"restored={ra.get('restored')}")
    if ra.get("failures"):
        probs.append(f"failures={ra.get('failures')}")
    if ra.get("leaks"):
        probs.append(f"leaks={ra.get('leaks')}")
    if ra.get("chainedHooks", 0):
        probs.append(f"chainedHooks={ra.get('chainedHooks')}")
    return Result("participants", not probs, "; ".join(probs) or f"{len(ra.get('participants', []))} participants, restored={ra.get('restored')} fresh={ra.get('fresh')}")


def check_session(before: Dict[str, Any], after: Dict[str, Any]) -> Result:
    cb, ca = before.get("coop", {}), after.get("coop", {})
    if "unavailable" in cb or "unavailable" in ca:
        return Result("session", True, "coop status unavailable on this instance; skipped")
    if not cb.get("running"):
        return Result("session", True, "coop was not running before the reload; nothing to carry")
    probs = []
    if not ca.get("running"):
        probs.append(f"coop not running after the reload (error={ca.get('error')!r})")
    if ca.get("role") != cb.get("role"):
        probs.append(f"role {cb.get('role')} -> {ca.get('role')}")
    if cb.get("token") is not None and ca.get("token") != cb.get("token"):
        probs.append(f"token {cb.get('token')} -> {ca.get('token')}")
    return Result("session", not probs, "; ".join(probs) or f"running role={ca.get('role')} token={ca.get('token')}")


def check_capture(end: Any) -> Result:
    txns = end.get("txns", []) if isinstance(end, dict) else []
    for t in txns:
        for e in t.get("effects", []):
            if e.get("type") == "SetStage" and e.get("stage") == CAPTURE_STAGE and                     str(e.get("quest", "")).lower().endswith(CAPTURE_QUEST.lower().lstrip("0")):
                return Result("capture", True, f"SetStage {CAPTURE_QUEST} {CAPTURE_STAGE} captured by the new core's hooks")
    return Result("capture", False, f"no SetStage {CAPTURE_QUEST} {CAPTURE_STAGE} in {len(txns)} txn(s): {json.dumps(txns)[:300]}")


def check_run(first: Dict[str, Any], last: Dict[str, Any], max_growth_mb: float) -> List[Result]:
    out: List[Result] = []
    pf, pl = first.get("proc"), last.get("proc")
    if pf and pl:
        out.append(Result("handles", pl["handles"] <= pf["handles"] + HANDLE_SLACK, f"{pf['handles']} -> {pl['handles']} (slack {HANDLE_SLACK})"))
        growth = pl["ws_mb"] - pf["ws_mb"]
        out.append(Result("memory", growth <= max_growth_mb, f"working set {pf['ws_mb']:.0f} -> {pl['ws_mb']:.0f} MB (limit +{max_growth_mb:.0f})"))
    return out


# ----------------------------------------------------------------------------------- the run
def run_proof(nv, cycles: int, probe: Optional[Any], capture_probe: bool, max_growth_mb: float, log: Callable[[str], None] = print,
              sleep: Callable[[float], None] = time.sleep) -> Dict[str, Any]:
    results: List[Dict[str, Any]] = []
    timings: List[float] = []
    first = snapshot(nv, probe)
    if not isinstance(first.get("reload"), dict) or "participants" not in first["reload"]:
        raise RuntimeError("reload.state missing: the game is not running the split build with the reload registry")
    prev = first
    for i in range(1, cycles + 1):
        t0 = time.time()
        reply, err = None, ""
        try:
            reply = nv.call("dev.reload", {"reason": f"reload_proof {i}"}, timeout_s=60.0)
        except Exception as ex:  # an RPC error carries the loader's reason (veto, rollback, core dead)
            err = f"{type(ex).__name__}: {ex}"
        timings.append((time.time() - t0) * 1000)
        cur = snapshot(nv, probe)
        checks = [check_reload_reply(prev, cur, reply, err), check_hooks(prev, cur), check_threads(prev, cur),
                  check_participants(prev, cur), check_session(prev, cur)]
        if capture_probe:
            try:
                nv.call("capture.begin")
                nv.exec(CAPTURE_CMD)
                sleep(1.0)
                checks.append(check_capture(nv.call("capture.end")))
            except Exception as ex:  # a bridge error here is a failed proof, not a crash of the tool
                checks.append(Result("capture", False, f"bridge error: {ex}"))
        for c in checks:
            results.append({"cycle": i, **c.as_dict()})
            if not c.ok:
                log(f"cycle {i}: FAIL {c.name}: {c.detail}")
        prev = cur
        if any(not c.ok for c in checks):
            break  # the first broken cycle is the interesting one; later cycles would only repeat it
    for c in check_run(first, prev, max_growth_mb):
        results.append({"cycle": 0, **c.as_dict()})
    ok = all(r["ok"] for r in results) and bool(results)
    timings_sorted = sorted(timings)
    return {"ok": ok, "cycles_run": len(timings), "reload_ms": {"p50": timings_sorted[len(timings_sorted) // 2] if timings else None,
                                                                "max": max(timings) if timings else None},
            "results": results}


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--instance", type=int, default=0)
    ap.add_argument("--cycles", type=int, default=10)
    ap.add_argument("--out", default="")
    ap.add_argument("--no-capture-probe", action="store_true")
    ap.add_argument("--max-growth-mb", type=float, default=DEFAULT_MAX_GROWTH_MB)
    a = ap.parse_args(argv)
    from nvbridge import NvBridge, BridgeError
    try:
        nv = NvBridge.connect(instance=a.instance, wait_s=5)
    except Exception as e:
        print(json.dumps({"ok": False, "error": f"cannot connect to instance {a.instance}: {e}"}))
        return 2
    with nv:
        try:
            nv.call("ping")
            pid = getattr(nv, "game_pid", 0)
            probe = ProcProbe(pid) if pid else None
            rep = run_proof(nv, a.cycles, probe, not a.no_capture_probe, a.max_growth_mb)
        except (RuntimeError, BridgeError) as e:
            print(json.dumps({"ok": False, "error": str(e)}))
            return 2
    text = json.dumps(rep, indent=2)
    print(text)
    if a.out:
        Path(a.out).parent.mkdir(parents=True, exist_ok=True)
        Path(a.out).write_text(text, encoding="utf-8")
    return 0 if rep["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
