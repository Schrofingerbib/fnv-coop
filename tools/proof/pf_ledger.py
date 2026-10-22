"""Ledger of proof runs and the "proven" rule (docs/PROOF-PLAN.md section 3). Pure functions plus a JSONL file. No game, no bridge.

A ledger line is the verdict (see prove.py) plus: ts, fresh (the copies were booted by prove.py and never hot swapped), clean, fake.

Proven rule for a test with N: the newest N non-ENV entries of the CURRENT build are all clean (PASS, digest repairs 0, area heals 0, no crash),
and at least one of them has fresh=true. Fake entries never count. An ENV entry says nothing about the build, so it neither breaks nor
extends a streak. A test with `blocked_reason` is blocked no matter what the ledger says.

A PASS recorded with `short: true` (the run was asked for with --runs below the test's N, a bug-finding pass) is kept in the ledger but never
counts toward "proven": it neither extends nor breaks the streak, and `status` says how many there are. A short FAIL still breaks it.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

MUTANT = os.environ.get("PROOF_MUTANT", "")   # test-only: one deliberate bug per name (tools/proof/tests/run_tests.py)


def append(path: Path, rec: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    row = dict(rec)
    row.setdefault("ts", time.strftime("%Y-%m-%dT%H:%M:%S"))
    lead = ""
    try:
        with open(path, "rb") as f:                 # a crash can leave a torn last line: start the new row on its own line
            f.seek(-1, os.SEEK_END)
            lead = "" if f.read(1) == b"\n" else "\n"
    except OSError:
        pass
    with open(path, "a", encoding="utf-8") as f:
        f.write(lead + json.dumps(row, separators=(",", ":")) + "\n")


def load(path: Path) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    try:
        for ln in Path(path).read_text(encoding="utf-8").splitlines():
            try:
                out.append(json.loads(ln))
            except ValueError:
                continue          # a torn last line after a crash: skip it
    except OSError:
        pass
    return out


def is_clean(rec: Dict[str, Any]) -> bool:
    return rec.get("verdict") == "PASS" and bool(rec.get("clean", True))


def is_short_pass(rec: Dict[str, Any]) -> bool:
    return bool(rec.get("short")) and rec.get("verdict") == "PASS" and MUTANT != "ledger_short_counts"


def round_done(entries: Iterable[Dict[str, Any]], test: str, name: str, fake: bool = False) -> bool:
    """True when `test` already has its verdict in the retest round `name` (prove.py queue --round NAME), so a restarted queue goes on with the
    next test instead of starting from the top. Done = a FAIL / CRASH / FLAKY, a manifest-blocked test, or a PASS of the last run of the series
    (run == of). ENV and the other BLOCKED lines say nothing about the build (a copy that would not boot, a switch that was off): the test is tried again."""
    if MUTANT == "round_ignored" or not name:
        return False
    mine = [e for e in entries if e.get("test") == test and e.get("round") == name and bool(e.get("fake")) == fake]
    if not mine:
        return False
    last = mine[-1]
    v = last.get("verdict")
    if v in ("FAIL", "CRASH", "FLAKY"):
        return True
    if v == "BLOCKED":
        return str(last.get("reason", "")).startswith("blocked:")
    return v == "PASS" and int(last.get("run", 0)) >= int(last.get("of", 1))


def streak(entries: Iterable[Dict[str, Any]], test: str, build: Optional[str] = None, include_fake: bool = False) -> Dict[str, Any]:
    """{"clean": n consecutive clean runs at the end, "fresh": any of them fresh, "last": newest verdict or None, "short": n short passes
    at the end of the streak (they do not count in "clean"), "any_clean": clean + short}."""
    rows = [e for e in entries if e.get("test") == test and (include_fake or not e.get("fake"))
            and e.get("verdict") != "ENV" and e.get("class") != "ENV"]
    if build:
        rows = [e for e in rows if e.get("build") == build]
    n, short, fresh = 0, 0, False
    for e in reversed(rows):
        if not is_clean(e):
            break
        if is_short_pass(e):
            short += 1
            continue
        n += 1
        fresh = fresh or bool(e.get("fresh"))
    return {"clean": n, "fresh": fresh, "last": rows[-1].get("verdict") if rows else None,
            "last_class": rows[-1].get("class") if rows else None, "short": short, "any_clean": n + short}


def state_of(test: Dict[str, Any], entries: List[Dict[str, Any]], build: Optional[str] = None, include_fake: bool = False) -> Dict[str, Any]:
    """{"state": proven|failing|blocked|partial|not_run, "clean": n, "of": N, "detail": short text, "short": n}. `short` counts the passes
    made with --runs below N: they are not in "clean" and `detail` says so."""
    r = _state_of(test, entries, build, include_fake)
    k = 0 if test.get("blocked_reason") else streak(entries, test["id"], build, include_fake)["short"]
    r["short"] = k
    if k and r["state"] != "proven":
        note = f"{k} short pass(es) (--runs below n={r['of']}) do not count"
        r["detail"] = (r["detail"] + "; " if r["detail"] else "") + note
    return r


def _state_of(test: Dict[str, Any], entries: List[Dict[str, Any]], build: Optional[str] = None, include_fake: bool = False) -> Dict[str, Any]:
    need = int(test.get("n", 1))
    if test.get("blocked_reason"):
        return {"state": "blocked", "clean": 0, "of": need, "detail": test["blocked_reason"][:80]}
    s = streak(entries, test["id"], build, include_fake)
    fresh_ok = s["fresh"] or MUTANT == "ledger_no_fresh"
    if s["clean"] >= need and fresh_ok:
        return {"state": "proven", "clean": s["clean"], "of": need, "detail": ""}
    if s["last"] in ("FAIL", "CRASH", "FLAKY", "TIMEOUT"):
        return {"state": "failing", "clean": s["clean"], "of": need, "detail": f"last {s['last']} ({s['last_class']})"}
    if s["last"] == "BLOCKED":
        return {"state": "blocked", "clean": 0, "of": need, "detail": f"last BLOCKED ({s['last_class']})"}
    if s["clean"] >= need:
        return {"state": "partial", "clean": s["clean"], "of": need, "detail": "needs one run from a fresh boot"}
    if s["clean"] > 0:
        return {"state": "partial", "clean": s["clean"], "of": need, "detail": ""}
    return {"state": "not_run", "clean": 0, "of": need, "detail": ""}


def tests_of_step(manifest: Dict[str, Any], step: int) -> List[Dict[str, Any]]:
    return [t for t in manifest["tests"] if t["roadmap"] == step or step in t.get("also_roadmap", [])]


def step_table(manifest: Dict[str, Any], entries: List[Dict[str, Any]], build: Optional[str] = None, include_fake: bool = False) -> List[Dict[str, Any]]:
    """One row per roadmap step: counts per state, percentage proven, per-test states."""
    rows = []
    for step in range(1, 11):
        ts = tests_of_step(manifest, step)
        if not ts:
            continue
        sts = {t["id"]: state_of(t, entries, build, include_fake) for t in ts}
        counts: Dict[str, int] = {}
        for s in sts.values():
            counts[s["state"]] = counts.get(s["state"], 0) + 1
        proven = counts.get("proven", 0)
        rows.append({"step": step, "total": len(ts), "proven": proven, "pct": round(100.0 * proven / len(ts)),
                     "counts": counts, "tests": sts})
    return rows


def render_table(rows: List[Dict[str, Any]], verbose: bool = True) -> str:
    out = []
    total = sum(r["total"] for r in rows)
    proven = sum(r["proven"] for r in rows)
    for r in rows:
        c = r["counts"]
        head = (f"step {r['step']:2d}  {r['pct']:3d}%  proven {r['proven']}/{r['total']}  failing {c.get('failing', 0)}  "
                f"blocked {c.get('blocked', 0)}  partial {c.get('partial', 0)}  not_run {c.get('not_run', 0)}")
        out.append(head)
        if verbose:
            for tid, s in r["tests"].items():
                mark = {"proven": "ok ", "failing": "BAD", "blocked": "blk", "partial": "...", "not_run": "   "}[s["state"]]
                out.append(f"    {mark} {tid:28s} {s['clean']}/{s['of']} {s['state']:9s} {s['detail']}")
    out.append(f"total   {round(100.0 * proven / total) if total else 0:3d}%  proven {proven}/{total}")
    return "\n".join(out)
