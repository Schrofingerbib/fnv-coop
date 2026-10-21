"""Desync dashboard for the area digests (synccore/areadigest, plugin/areadigest, docs/tools/area-desync.md).

Every machine answers the bridge method `areadigest.status` (and its flight dump carries the same numbers in the header Meta record):

    {"enabled": true, "role": "host", "heals": {"total": 0, "perMin": 0,
        "byCat": {"story": {"total":0,"perMin":0,"mismatches":0,...}, "actors": {...}, "containers": {...}, "objects": {...}}},
     "counters": {...}, "bugs": [{"cat","key","cell","peer","count","authority","other"}]}

A "heal" is a key that was found different from its authority (the actor's owner, the host for everything else) and corrected on that machine.
The target in every scripted test is ZERO. This module sums the machines, prints one dashboard line and says what failed:

    from areadigest.dashboard import summarize, dashboard_line, failures
    s = summarize({"host": host_status, "client1": client_status})
    print(dashboard_line(s))              # area desync | heals/min story 0 actors 0 containers 0 objects 0 | ...
    assert not failures(s), failures(s)   # no heals, no bug lines

The playbook engine (tools/bots/playbooks/core.py) merges `areadigest.status` into each machine's status and treats the total as the desync
counter `areaHeals` (it must not rise during a step). CLI:

    python -I tools/areadigest/dashboard.py status host.json client.json     # status JSON files (named after the file)
    python -I tools/areadigest/dashboard.py nvfr host.nvfr client.nvfr       # flight dumps (header Meta record)
    python -I tools/areadigest/run_tests.py                                   # the tests and their twins (prints ALL OK)
Exit code 1 when a heal or a bug line is present (use --allow N to tolerate heals).
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional

CATS = ("story", "actors", "containers", "objects")
MUTANT = os.environ.get("AREADIGEST_MUTANT", "")   # test-only: one deliberate bug per name (tools/areadigest/run_tests.py)


def parse(raw: Any) -> Optional[dict]:
    """A status as a dict, or None when the machine has no running module (missing, disabled, not JSON)."""
    if isinstance(raw, (bytes, str)):
        try:
            raw = json.loads(raw)
        except ValueError:
            return None
    if not isinstance(raw, dict):
        return None
    if not raw.get("enabled") and MUTANT != "disabled_counts":
        return None
    return raw


def _int(v: Any) -> int:
    try:
        return int(v)
    except (TypeError, ValueError):
        return 0


def category_table(status: dict) -> Dict[str, Dict[str, int]]:
    """{category: {total, perMin, mismatches, inFlight, falsePositive, decided, raced, refused}} from one status (zeros for what is absent)."""
    by = ((status.get("heals") or {}).get("byCat")) or {}
    out: Dict[str, Dict[str, int]] = {}
    for c in CATS:
        row = by.get(c) or {}
        out[c] = {k: _int(row.get(k)) for k in ("total", "perMin", "mismatches", "inFlight", "falsePositive", "decided", "raced", "refused")}
    return out


def summarize(statuses: Mapping[str, Any]) -> dict:
    """Sum the machines. `statuses` maps a machine name to its areadigest.status (dict or JSON text; None/disabled machines are listed, not summed)."""
    per: Dict[str, dict] = {}
    disabled: List[str] = []
    bugs: List[str] = []
    names = list(statuses)
    if MUTANT == "host_only":
        names = names[:1]                       # twin: only the first machine is read
    for name in names:
        st = parse(statuses[name])
        if st is None:
            disabled.append(name)
            continue
        per[name] = {"role": st.get("role", "?"), "table": category_table(st), "total": _int((st.get("heals") or {}).get("total")),
                     "perMin": _int((st.get("heals") or {}).get("perMin"))}
        if MUTANT != "bug_ignored":
            for b in st.get("bugs") or []:
                bugs.append("%s: repeated %s key=%s x%s authority{%s} other{%s}" % (name, b.get("cat"), b.get("key"), b.get("count"),
                                                                                    b.get("authority"), b.get("other")))
    by_cat = {c: {"total": 0, "perMin": 0, "mismatches": 0} for c in CATS}
    for p in per.values():
        for c in CATS:
            for k in ("total", "perMin", "mismatches"):
                by_cat[c][k] += p["table"][c][k]
    return {"machines": len(statuses), "running": len(per), "disabled": disabled,
            "total": sum(by_cat[c]["total"] for c in CATS), "perMin": sum(by_cat[c]["perMin"] for c in CATS),
            "mismatches": sum(by_cat[c]["mismatches"] for c in CATS), "byCat": by_cat, "bugs": bugs, "perMachine": per}


def dashboard_line(s: dict) -> str:
    """One line for logs and test output."""
    key = "total" if MUTANT == "permin_is_total" else "perMin"
    cats = " ".join("%s %d" % (c, s["byCat"][c][key]) for c in CATS)
    return "area desync | heals/min %s | heals total %d | group mismatches %d | bug lines %d | machines %d/%d with the module" % (
        cats, s["total"], s["mismatches"], len(s["bugs"]), s["running"], s["machines"])


def failures(s: dict, max_heals: int = 0, max_per_min: Optional[int] = None, allow_bugs: int = 0, need_running: int = 0) -> List[str]:
    """What breaks the target (zero heals, no bug lines). `need_running`: at least that many machines must answer (a silent module proves nothing)."""
    out: List[str] = []
    cats = [c for c in CATS if not (MUTANT == "assert_skips_story" and c == "story")]
    total = sum(s["byCat"][c]["total"] for c in cats)
    if total > max_heals:
        out.append("%d heal(s), allowed %d: %s" % (total, max_heals, ", ".join("%s %d" % (c, s["byCat"][c]["total"]) for c in cats if s["byCat"][c]["total"])))
    if max_per_min is not None:
        pm = sum(s["byCat"][c]["perMin"] for c in cats)
        if pm > max_per_min:
            out.append("%d heal(s) in the last minute, allowed %d" % (pm, max_per_min))
    if len(s["bugs"]) > allow_bugs:
        out.append("%d repeated-mismatch bug line(s): %s" % (len(s["bugs"]), "; ".join(s["bugs"][:3])))
    if s["running"] < need_running:
        out.append("only %d of %d machines run the area digest module (need %d): %s" % (s["running"], s["machines"], need_running, ", ".join(s["disabled"]) or "?"))
    return out


# ------------------------------------------------------------------------------------------------------------- bridge and files
def fetch_status(bot: Any) -> Optional[dict]:
    """areadigest.status from a bot (tools/bots/bot.py) or anything with `.nv.call`. None when the module is not there; asked only once if not."""
    if getattr(bot, "_area_unsupported", False):
        return None
    call = getattr(getattr(bot, "nv", None), "call", None)
    if call is None:
        return None
    try:
        st = call("areadigest.status")
    except Exception:                                    # noqa: BLE001
        try:
            bot._area_unsupported = True
        except Exception:                                # noqa: BLE001
            pass
        return None
    return st if isinstance(st, dict) else None


def collect(bots: Mapping[str, Any]) -> Dict[str, Optional[dict]]:
    return {name: fetch_status(b) for name, b in bots.items()}


def from_nvfr(path: str) -> dict:
    """A status-shaped dict from the heal counters in a flight dump's Meta record (heals.<category>.total|perMin|mismatches)."""
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "flight"))
    try:
        import nvfr                                      # type: ignore
    finally:
        sys.path.pop(0)
    trace = nvfr.load(path)
    meta: Dict[str, str] = {}
    markers = 0
    for r in trace["records"]:
        if r["type"] == nvfr.T_META:
            meta.update(r.get("meta", {}))
        elif r["type"] == nvfr.T_MARKER and r.get("kind") == 8:
            markers += 1
    if MUTANT == "nvfr_meta_ignored":
        meta = {}
    by = {c: {"total": _int(meta.get("heals.%s.total" % c)), "perMin": _int(meta.get("heals.%s.perMin" % c)),
              "mismatches": _int(meta.get("heals.%s.mismatches" % c))} for c in CATS}
    return {"enabled": "heals.total" in meta, "role": nvfr.ROLES.get(trace["header"].get("role"), "?"),
            "heals": {"total": _int(meta.get("heals.total")), "perMin": _int(meta.get("heals.perMin")), "byCat": by}, "bugs": [], "healMarkers": markers}


def main(argv: List[str]) -> int:
    allow = 0
    args = list(argv)
    if "--allow" in args:
        i = args.index("--allow")
        allow = int(args[i + 1])
        del args[i:i + 2]
    if len(args) < 2 or args[0] not in ("status", "nvfr"):
        print(__doc__)
        return 2
    statuses: Dict[str, Any] = {}
    for p in args[1:]:
        name = Path(p).stem
        statuses[name] = json.loads(Path(p).read_text(encoding="utf-8")) if args[0] == "status" else from_nvfr(p)
    s = summarize(statuses)
    print(dashboard_line(s))
    bad = failures(s, max_heals=allow)
    for b in bad:
        print("FAIL", b)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
