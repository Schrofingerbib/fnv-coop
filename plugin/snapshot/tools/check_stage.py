#!/usr/bin/env python3
"""Assert that setting QUEST STAGE produced (at least) the statically expected unconditional effects.

  check_stage.py --oracle effects.json[.gz] | --scenarios DIR  QUEST STAGE BEFORE.json AFTER.json
                 [--strict-extras] [--ignore-type T ...] [--json] [--min-quests N] [--min-globals N]

observed = effect_oracle.observe(before, after);  expected = oracle[QUEST][STAGE] (+ the implicit SetStage).
  FAIL (exit 1) if any unconditional expected effect has no matching observed effect (observed is a superset of expected);
  extras (observed effects nothing expects, minus --ignore-type types) are reported, and fail only with --strict-extras;
  conditional expected effects that did / did not fire are reported but never fail.
  exit 2: invalid input (guard failure on either snapshot, quest/stage unknown to the oracle or snapshot).
A no-op stage change always fails: the implicit SetStage expectation cannot be observed in identical snapshots.

Oracle formats accepted
  native (L3 "effects.json.gz", format assumed until L3 documents it; see docs/snapshot-schema.md):
    {"schema":1,"quests":{"VMS01":{"ref":"FalloutNV.esm:080664","stages":{"10":
        {"unconditional":[<effect dict>...],"conditional":[<effect dict>...]}}}}}
  L3 scenario file(s) (out/questdata/scenarios/<EDID>.json): derived on the fly - objectives displayed/completed
    parsed from the stage script are treated as unconditional (L3 does not tell us if they sit under an If).
Expected effects may omit fields (wildcard) and `to`: absent = any, "any" = at least one player, "all" = every player.
"""
import gzip
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import effect_oracle  # noqa: E402
import snaplib  # noqa: E402


class OracleError(Exception):
    pass


# ---------------------------------------------------------------- oracle loading
def _open(path):
    return gzip.open(path, "rt", encoding="utf-8") if path.endswith(".gz") else open(path, encoding="utf-8")


def load_native(path):
    with _open(path) as f:
        d = json.load(f)
    if not isinstance(d, dict) or "quests" not in d:
        raise OracleError("not a native effects oracle (no 'quests')")
    return d


def scenario_to_native(sc):
    q = {"ref": sc.get("ref"), "stages": {}}
    for step in sc.get("steps", []):
        sid = step.get("id", "")
        if not sid.startswith("stage_"):
            continue
        both = (step.get("expect") or {}).get("both") or {}
        unc = []
        objs = both.get("objectives") or {}
        for o in objs.get("displayed", []):
            unc.append({"type": "SetObjective", "quest": sc["ref"], "objective": o, "flags": 1})
        for o in objs.get("completed", []):
            unc.append({"type": "SetObjective", "quest": sc["ref"], "objective": o, "flags": 2})
        if both.get("quest_completed"):
            unc.append({"type": "CompleteQuest", "quest": sc["ref"]})
        if both.get("quest_failed"):
            unc.append({"type": "FailQuest", "quest": sc["ref"]})
        q["stages"][sid[len("stage_"):]] = {"unconditional": unc, "conditional": []}
    return sc["quest"], q


def load_scenarios(directory):
    quests = {}
    for fn in sorted(os.listdir(directory)):
        if fn.endswith(".json") and not fn.startswith("_"):
            with open(os.path.join(directory, fn), encoding="utf-8") as f:
                sc = json.load(f)
            if isinstance(sc, dict) and "quest" in sc and "steps" in sc:
                name, q = scenario_to_native(sc)
                quests[name] = q
    if not quests:
        raise OracleError("no scenario files in %s" % directory)
    return {"schema": 1, "quests": quests}


# ---------------------------------------------------------------- matching
def _val_eq(k, exp, obs):
    if k == "flags":
        return isinstance(obs, int) and (obs & exp) == exp  # observed may have more bits (completed implies displayed)
    if isinstance(exp, float) or isinstance(obs, float):
        return isinstance(obs, (int, float)) and abs(exp - obs) <= effect_oracle.FLOAT_TOL
    return exp == obs


def _to_ok(exp_to, obs_to):
    if exp_to is None:
        return True
    if exp_to == "any":
        return obs_to not in (None, "shared")
    return exp_to == obs_to


def matches(exp, obs):
    if exp.get("type") != obs.get("type"):
        return False
    for k, v in exp.items():
        if k == "type":
            continue
        if k == "to":
            if not _to_ok(v, obs.get("to")):
                return False
        elif k not in obs or not _val_eq(k, v, obs[k]):
            return False
    return True


# ---------------------------------------------------------------- the check
def find_quest(snap, name):
    for ref, q in ((snap.get("shared") or {}).get("quests") or {}).items():
        if q.get("edid") == name or ref == name:
            return ref
    return None


def check(oracle, quest, stage, before, after, strict_extras=False, ignore_types=(),
          min_quests=snaplib.DEFAULT_MIN_QUESTS, min_globals=snaplib.DEFAULT_MIN_GLOBALS):
    """Returns (exit_code, report dict)."""
    rep = {"quest": quest, "stage": stage, "problems": [], "missing": [], "matched": [], "extras": [],
           "conditional_hit": [], "conditional_miss": []}
    for name, s in (("before", before), ("after", after)):
        rep["problems"] += ["%s snapshot: %s" % (name, p) for p in snaplib.guard(s, ("shared",), min_quests, min_globals)]
    q = (oracle.get("quests") or {}).get(quest)
    if q is None:
        rep["problems"].append("oracle has no entry for quest %s (refusing to pass vacuously)" % quest)
    elif str(stage) not in q.get("stages", {}):
        rep["problems"].append("oracle has no entry for %s stage %s" % (quest, stage))
    ref = find_quest(after, quest)
    if ref is None:
        rep["problems"].append("quest %s not present in snapshot" % quest)
    if rep["problems"]:
        return 2, rep

    entry = q["stages"][str(stage)]
    unconditional = [{"type": "SetStage", "quest": ref, "stage": int(stage)}] + list(entry.get("unconditional", []))
    conditional = list(entry.get("conditional", []))
    observed = effect_oracle.observe(before, after)

    used = set()
    for exp in unconditional:
        hit = next((i for i, o in enumerate(observed) if matches(exp, o)), None)
        if hit is None:
            rep["missing"].append(exp)
        else:
            rep["matched"].append(exp)
    for exp in conditional:
        hits = [i for i, o in enumerate(observed) if matches(exp, o)]
        (rep["conditional_hit"] if hits else rep["conditional_miss"]).append(exp)
    for i, o in enumerate(observed):
        if o["type"] not in set(ignore_types) and not any(matches(e, o) for e in unconditional + conditional):
            rep["extras"].append(o)
    bad = bool(rep["missing"]) or (strict_extras and bool(rep["extras"]))
    return (1 if bad else 0), rep


def render(code, rep):
    L = []
    if code == 2:
        L.append("INVALID (cannot judge):")
        L += ["  " + p for p in rep["problems"]]
        return "\n".join(L)
    L.append("%s %s stage %s: %d expected matched, %d missing, %d extra, conditional %d fired / %d not"
             % ("PASS" if code == 0 else "FAIL", rep["quest"], rep["stage"], len(rep["matched"]),
                len(rep["missing"]), len(rep["extras"]), len(rep["conditional_hit"]), len(rep["conditional_miss"])))
    for k, tag in (("missing", "MISSING"), ("extras", "EXTRA"), ("conditional_miss", "cond-not-fired"),
                   ("conditional_hit", "cond-fired")):
        for e in rep[k]:
            L.append("  %s %s" % (tag, effect_oracle.ekey(e)))
    return "\n".join(L)


def main(argv=None):
    snaplib.setup_console()
    argv = list(sys.argv[1:] if argv is None else argv)
    opts = {"oracle": None, "scenarios": None, "strict": False, "json": False, "ignore": [],
            "min_quests": snaplib.DEFAULT_MIN_QUESTS, "min_globals": snaplib.DEFAULT_MIN_GLOBALS}
    pos = []
    i = 0
    while i < len(argv):
        a = argv[i]
        if a == "--oracle": opts["oracle"] = argv[i + 1]; i += 1
        elif a == "--scenarios": opts["scenarios"] = argv[i + 1]; i += 1
        elif a == "--ignore-type": opts["ignore"].append(argv[i + 1]); i += 1
        elif a == "--min-quests": opts["min_quests"] = int(argv[i + 1]); i += 1
        elif a == "--min-globals": opts["min_globals"] = int(argv[i + 1]); i += 1
        elif a == "--strict-extras": opts["strict"] = True
        elif a == "--json": opts["json"] = True
        else: pos.append(a)
        i += 1
    if len(pos) != 4 or not (opts["oracle"] or opts["scenarios"]):
        print(__doc__)
        return 2
    quest, stage, bpath, apath = pos
    try:
        oracle = load_native(opts["oracle"]) if opts["oracle"] else load_scenarios(opts["scenarios"])
        before, after = snaplib.load(bpath), snaplib.load(apath)
    except (OSError, ValueError, OracleError, snaplib.SnapshotError) as e:
        print("INVALID: %s" % e)
        return 2
    code, rep = check(oracle, quest, int(stage), before, after, opts["strict"], opts["ignore"],
                      opts["min_quests"], opts["min_globals"])
    print(json.dumps(rep, indent=1) if opts["json"] else render(code, rep))
    return code


if __name__ == "__main__":
    sys.exit(main())
