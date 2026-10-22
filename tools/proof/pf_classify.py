"""Turn the evidence of one test run into a verdict and a class (docs/PROOF-PLAN.md section 2). Pure functions: no game, no files, no clock.

    classify(ev) -> {"verdict", "class", "repro", "why"}

`ev` keys (all optional): timeout, env_reason, crash (dict), build_reason, exit (runner exit code), setup_text (runner output when exit is 2),
from_failure ("reproduced" | "not-reproduced" | "no-recordings" | None), sync_symptoms (list), inspect_sync (list), bad_counters (list),
failure_kind (check|action|settle|snapdiff|counters|bridge|exception|arrange), reason (text).
Order of the table: ENV, CRASH, BUILD, then SYNC (a divergence was found) or TEST (the tool or assertion is wrong).
"""
from __future__ import annotations

import os
import re
from typing import Any, Dict, List, Optional

MUTANT = os.environ.get("PROOF_MUTANT", "")   # test-only: one deliberate bug per name (tools/proof/tests/run_tests.py)

SUSPICIOUS = re.compile(r"(repair|mismatch|heal|fault|error|fail|refus|forged|dropp|rollback|closeMismatch|giveup|glitch|orphan|"
                        r"bad|lost|stale|exception|unsupported|desync)", re.I)
NOISE = re.compile(r"(^|\.)(frames?|seq|lastSeq|acked|ticks?|uptime|time|ms|us|ok|total_?ms)$", re.I)
# the clean rule: these counters must not move in a passing run
ALWAYS_ZERO = ("desyncs.digests.repairs", "areadigest.status.heals.total")
SYNC_PROBLEM = re.compile(r"digest|lastSeq|synced|joined|behind|mismatch", re.I)


def flatten(obj: Any, prefix: str = "", out: Optional[Dict[str, float]] = None, depth: int = 0) -> Dict[str, float]:
    """Numeric leaves of a JSON value as {"a.b.c": number}. Booleans and strings are skipped; lists are indexed (first 16)."""
    out = {} if out is None else out
    if depth > 8:
        return out
    if isinstance(obj, bool):
        return out
    if isinstance(obj, (int, float)):
        out[prefix] = obj
    elif isinstance(obj, dict):
        for k, v in obj.items():
            flatten(v, f"{prefix}.{k}" if prefix else str(k), out, depth + 1)
    elif isinstance(obj, list):
        for i, v in enumerate(obj[:16]):
            flatten(v, f"{prefix}.{i}", out, depth + 1)
    return out


def snapshot_flat(per_method: Dict[str, Dict[int, Any]]) -> Dict[str, float]:
    """{method: {instance: answer}} -> {"inst.method.path": number}."""
    flat: Dict[str, float] = {}
    for method, by_inst in per_method.items():
        for inst, ans in by_inst.items():
            flatten(ans, f"{inst}:{method}", flat)
    return flat


def moved(before: Dict[str, float], after: Dict[str, float]) -> Dict[str, float]:
    """{key: delta} for every numeric counter that changed (keys only in `after` count from 0)."""
    out = {}
    for k, v in after.items():
        d = v - before.get(k, 0)
        if d:
            out[k] = round(d, 3) if isinstance(d, float) else d
    return out


def strip_inst(key: str) -> str:
    return key.split(":", 1)[1] if ":" in key else key


def suspicious_moved(mv: Dict[str, float], limit: int = 8) -> Dict[str, float]:
    """Moved counters that look like a problem, summed over instances, biggest first. This is what the verdict line shows."""
    agg: Dict[str, float] = {}
    for k, d in mv.items():
        name = strip_inst(k)
        if SUSPICIOUS.search(name) and not NOISE.search(name):
            agg[name] = agg.get(name, 0) + d
    top = sorted(agg.items(), key=lambda kv: -abs(kv[1]))[:limit]
    return dict(top)


def unclean(mv: Dict[str, float], allow_repairs: bool = False, must_zero: Optional[List[str]] = None) -> List[str]:
    """Why a PASS is not clean: a must-stay-zero counter moved. [] when clean."""
    if MUTANT == "no_clean_counters":
        return []
    zero = list(must_zero or []) + ([] if allow_repairs else list(ALWAYS_ZERO))
    bad = []
    for k, d in mv.items():
        name = strip_inst(k)
        if d > 0 and any(name.endswith(z) for z in zero):
            bad.append(f"{k} +{d:g}")
    return bad


def classify(ev: Dict[str, Any]) -> Dict[str, Any]:
    def out(verdict, cls, why, repro=None):
        return {"verdict": verdict, "class": cls, "repro": repro, "why": why}

    if ev.get("timeout"):
        return out("TIMEOUT", "ENV", "runner exceeded its timeout")
    if ev.get("env_reason"):
        return out("ENV", "ENV", ev["env_reason"])
    if ev.get("crash"):
        return out("CRASH", "CRASH", "a game copy exited during the run")
    if ev.get("build_reason"):
        return out("BLOCKED", "BUILD", ev["build_reason"])
    code = ev.get("exit")
    bad = list(ev.get("bad_counters") or [])
    if code == 0 and not bad:
        return out("PASS", None, "")
    if code == 3:
        return out("BLOCKED", "BUILD", "runner exit 3: not wired or a required bridge method is missing")
    if code == 2 and not bad:
        txt = (ev.get("setup_text") or "").lower()
        if "cannot reach" in txt or "pipe" in txt or "not ready" in txt:
            return out("ENV", "ENV", "game instances not reachable")
        return out("FAIL", "TEST", "runner exit 2: usage or setup error in the test invocation")
    kind = ev.get("failure_kind")
    ff = ev.get("from_failure")
    sync_ev = list(ev.get("sync_symptoms") or []) + list(ev.get("inspect_sync") or []) + bad
    if kind == "bridge":
        low = (ev.get("reason") or "").lower()
        if "-32601" in low or "not found" in low or "unknown method" in low:
            return out("BLOCKED", "BUILD", "a bridge method the test needs is missing in this build")
    if ff == "reproduced":
        return out("FAIL", "TEST" if MUTANT == "repro_is_test" else "SYNC", "divergence found in the recordings", "REPRODUCED")
    if sync_ev:
        return out("FAIL", "SYNC", "; ".join(sync_ev)[:120], "NOT REPRODUCED")
    if ff == "not-reproduced":
        return out("FAIL", "TEST", "no divergence in the recordings, copies healthy, counters clean")
    if kind in ("check", "snapdiff", "settle", "counters"):
        return out("FAIL", "SYNC", "cross-machine check failed; no recordings to replay", "NOT REPRODUCED")
    return out("FAIL", "TEST", "assertion failed with no sign of a divergence")


REASON_LINE = re.compile(r"^\s*(?:\[FAIL\]|FAIL\b|FAILED\b|BLOCKED\b|NOT READY\b|proof_actor_port: \d+ check|\d+ check\(s\) FAILED|SELFTEST FAILED)(.*)$")


def reason_from_output(text: str, limit: int = 200) -> str:
    """The first line that names a failure in a runner's output, else its last non-empty line. At most `limit` characters."""
    lines = [l.rstrip() for l in (text or "").splitlines() if l.strip()]
    for l in lines:
        if REASON_LINE.match(l):
            return l.strip()[:limit]
    return (lines[-1].strip() if lines else "no output")[:limit]


def check_name(failure: Optional[Dict[str, Any]], reason: str, fallback: str) -> str:
    """Short name of the failed check: the failing step of a playbook, else the start of the first failure line."""
    if failure and failure.get("step"):
        return str(failure["step"])[:60]
    m = re.match(r"^\W*(?:FAIL|FAILED|\[FAIL\])\W*(.{3,60})", reason or "")
    return (m.group(1).strip() if m else fallback)[:60]
