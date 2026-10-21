#!/usr/bin/env python3
"""Empirical volatility probe: which state keys change BY THEMSELVES, so baseline dumps can leave them out.

  probe:  volatile_probe.py probe --scenarios out/questdata/scenarios [--manual FILE]
              [--json out/snapshot/volatile_keys.json] [--txt out/snapshot/volatile_keys.txt]
              [--settle N --interval SECONDS] --group S1.json S2.json [S3.json ...] [--group T1.json T2.json ...]
      --settle N: changes among the first N dump intervals of a launch are "settling" (state still converging after
      load, e.g. DLC startup quests): reported in result["settling"] with the time of the last change, NOT excluded.
      Take dumps right after autoload (not 600 frames later) so settling is actually observed.
      Each --group is ONE game launch: snapshots taken N seconds apart with NO player action in between.
      Several groups = several launches of the same save. A key is volatile if its value differs between any two
      consecutive snapshots of a group (reason "session") or between the first snapshots of two groups ("launch").
      Compared keys: quest script vars and watched-ref script vars (by NAME), quest fields running / stage /
      objectives, globals (clock globals are handled by the dumper already).
  check:  volatile_probe.py check volatile_keys.txt --scenarios out/questdata/scenarios
      Validates a generated or hand-edited file against the safety rules and exits 1 on any violation.

Safety rules (enforced when probing AND by `check`):
  * a quest is never dropped wholesale: the file format has no such directive;
  * a STORY quest (L3 scenario kind == "story", 234 of them) can only lose individual script vars. If a story
    quest's running/stage/objectives changes by itself that is reported as a WARNING (investigate, probably a real
    event or a bug) and is NOT excluded;
  * the manual file (plugin/snapshot/tools/volatile_manual.json) has "protect" (never exclude) and "add"
    (always exclude, still subject to the story rule).
Exit codes: probe 0 ok (warnings printed), 2 invalid input; check 0 ok, 1 violation, 2 invalid input.
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import snaplib  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
MANUAL_DEFAULT = os.path.join(HERE, "volatile_manual.json")
FIELDS = ("running", "stage", "objectives")
VAR_TOL = 1e-4
GLOBAL_TOL = 1e-3


def norm_ref(r):
    """Canonical ref: lower-case plugin, 6 upper-case hex (what L4's FormRef::make yields)."""
    plug, _, hx = r.rpartition(":")
    return "%s:%06X" % (plug.lower(), int(hx, 16) & 0xFFFFFF)


def _norm_story(story):
    return {norm_ref(r): e for r, e in story.items()}


# ---------------------------------------------------------------- story quests (L3)
def story_refs(scenarios_dir):
    """{ref: edid} for every quest L3 classifies as kind == 'story'."""
    idx_path = os.path.join(scenarios_dir, "_index.json")
    with open(idx_path, encoding="utf-8") as f:
        idx = json.load(f)
    out = {}
    for e in idx.get("scenarios", []):
        if e.get("kind") != "story":
            continue
        with open(os.path.join(scenarios_dir, e["file"]), encoding="utf-8") as f:
            sc = json.load(f)
        if sc.get("ref"):
            out[sc["ref"].split(":")[0] + ":" + sc["ref"].split(":")[1].upper()] = e["quest"]
    if not out:
        raise ValueError("no story quests found under %s (refusing to run without the safety list)" % scenarios_dir)
    return out


# ---------------------------------------------------------------- extraction
def extract(snap):
    """key -> comparable value. Keys: ('var', owner, lowername), ('field', quest, name), ('global', ref)."""
    out, edid = {}, {}
    sh = snap.get("shared") or {}
    for ref, q in (sh.get("quests") or {}).items():
        edid[ref] = q.get("edid", "")
        out[("field", ref, "running")] = bool(q.get("running"))
        out[("field", ref, "stage")] = (q.get("currentStage"), tuple(q.get("completedStages", [])))
        out[("field", ref, "objectives")] = json.dumps(q.get("objectives", {}), sort_keys=True)
        for name, v in (q.get("vars") or {}).items():
            out[("var", ref, name.lower())] = float(v)
    for ref, r in (sh.get("refs") or {}).items():
        edid[ref] = r.get("edid", "")
        for name, v in (r.get("vars") or {}).items():
            out[("var", ref, name.lower())] = float(v)
    for ref, g in (sh.get("globals") or {}).items():
        edid[ref] = g.get("edid", "")
        if g.get("edid") in snaplib.VOLATILE_GLOBAL_EDIDS:
            continue
        out[("global", ref)] = float(g.get("value", 0))
    return out, edid


def _differs(key, a, b):
    if isinstance(a, float) and isinstance(b, float):
        return abs(a - b) > (GLOBAL_TOL if key[0] == "global" else VAR_TOL)
    return a != b


def changed_keys(a, b):
    ka, kb = a[0], b[0]
    return {k for k in ka.keys() & kb.keys() if _differs(k, ka[k], kb[k])}


# ---------------------------------------------------------------- probe
def load_manual(path):
    if not path or not os.path.exists(path):
        return {"protect": {}, "add": {}}
    with open(path, encoding="utf-8") as f:
        d = json.load(f)
    d.setdefault("protect", {})
    d.setdefault("add", {})
    return d


def _manual_keys(section):
    keys = set()
    for v in section.get("vars", []):
        keys.add(("var", v["owner"], v["name"].lower()))
    for f in section.get("fields", []):
        keys.add(("field", f["quest"], f["field"]))
    for g in section.get("globals", []):
        keys.add(("global", g))
    return keys


def probe(groups, story, manual=None, _mutant_ignore_story=False, settle=0, interval_s=None):
    """groups: list of launches, each a list of >= 1 snapshot dicts (time ordered).
    story: {ref: edid}. Returns the result dict."""
    problems = []
    ex = []
    story = _norm_story(story)
    for gi, g in enumerate(groups):
        ex.append([])
        for si, s in enumerate(g):
            bad = snaplib.guard(s)
            if bad:
                problems.append("group %d snapshot %d: %s" % (gi, si, bad[0]))
            ex[gi].append(extract(s))
    if sum(len(g) for g in groups) < 2 or not (any(len(g) >= 2 for g in groups) or len(groups) >= 2):
        problems.append("need at least two snapshots (two in one group, or one in each of two groups)")
    if problems:
        raise ValueError("; ".join(problems))
    reasons, edids = {}, {}
    settling = {}   # key -> index of the LAST dump interval in which it changed, for keys that stop changing
    for g in ex:
        for _, ed in g:
            edids.update(ed)
    for g in ex:
        for i, (a, b) in enumerate(zip(g, g[1:])):
            for k in changed_keys(a, b):
                if i < settle:
                    settling[k] = max(settling.get(k, 0), i + 1)   # still converging after load: NOT volatile
                else:
                    reasons.setdefault(k, set()).add("session")    # still changing in steady state: volatile
    # cross-launch: compare the LAST (settled) snapshot of each launch
    for i in range(len(ex)):
        for j in range(i + 1, len(ex)):
            for k in changed_keys(ex[i][-1], ex[j][-1]):
                reasons.setdefault(k, set()).add("launch")
    settling = {k: v for k, v in settling.items() if k not in reasons}

    manual = manual or {"protect": {}, "add": {}}
    protect, add = _manual_keys(manual["protect"]), _manual_keys(manual["add"])
    for k in add:
        reasons.setdefault(k, set()).add("manual")

    res = {"schema": 1, "vars": [], "fields": [], "globals": [], "warnings": [], "protected": [],
           "counts": {"snapshots": sum(len(g) for g in groups), "groups": len(groups)}}
    for k in sorted(reasons, key=lambda k: tuple(str(x) for x in k)):
        why = sorted(reasons[k])
        if k in protect:
            res["protected"].append({"key": list(k), "reasons": why})
            continue
        if k[0] == "var":
            res["vars"].append({"owner": k[1], "name": k[2], "edid": edids.get(k[1], ""), "reasons": why})
        elif k[0] == "field":
            if norm_ref(k[1]) in story and not _mutant_ignore_story:
                res["warnings"].append({"code": "story_field_changes_by_itself", "quest": k[1], "edid": story[norm_ref(k[1])],
                                        "field": k[2], "reasons": why})
                continue
            res["fields"].append({"quest": k[1], "field": k[2], "edid": edids.get(k[1], ""), "reasons": why})
        else:
            res["globals"].append({"global": k[1], "edid": edids.get(k[1], ""), "reasons": why})
    res["settling"] = [{"key": list(k), "edid": edids.get(k[1], ""), "lastChangeDump": v,
                        "lastChangeSeconds": (v * interval_s) if interval_s else None}
                       for k, v in sorted(settling.items(), key=lambda kv: tuple(str(x) for x in kv[0]))
                       if k not in protect]
    res["recommendedSettleSeconds"] = (max([x["lastChangeSeconds"] for x in res["settling"]] or [0])
                                       if interval_s else None)
    res["counts"].update(settling=len(res["settling"]), vars=len(res["vars"]), fields=len(res["fields"]), globals=len(res["globals"]),
                         warnings=len(res["warnings"]), protected=len(res["protected"]))
    return res


def render_txt(res):
    L = ["# generated by plugin/snapshot/tools/volatile_probe.py - do not edit (see volatile_manual.json)",
         "# vars=%d fields=%d globals=%d from %d snapshots in %d launch(es)" % (
             len(res["vars"]), len(res["fields"]), len(res["globals"]), res["counts"]["snapshots"],
             res["counts"]["groups"])]
    for v in res["vars"]:
        L.append("var %s %s   # %s" % (v["owner"], v["name"], v["edid"]))
    for f in res["fields"]:
        L.append("field %s %s   # %s" % (f["quest"], f["field"], f["edid"]))
    for g in res["globals"]:
        L.append("global %s   # %s" % (g["global"], g["edid"]))
    return "\n".join(L) + "\n"


# ---------------------------------------------------------------- shared grammar (== L4 VolatileKeys::loadFromText)
def _parse_form(tok, pos):
    """Form = 'Plugin.esm:HEX' (one token) or 'Plugin.esm HEX' (two tokens). Returns (ref, newpos) or None."""
    if pos >= len(tok):
        return None
    if ":" in tok[pos]:
        plug, _, hx = tok[pos].rpartition(":")
        pos += 1
    else:
        if pos + 1 >= len(tok):
            return None
        plug, hx = tok[pos], tok[pos + 1]
        pos += 2
    try:
        n = int(hx, 16)
    except ValueError:
        return None
    if not plug or not hx:
        return None
    return "%s:%06X" % (plug.lower(), n & 0xFFFFFF), pos


def parse_entries(text):
    """-> (entries, errors). entries: list of (kind, ref, arg) with kind in var/owner/global/field/faction.
    Same acceptance rules as L4's loader; refs are normalised to lower-case plugin + 6 upper-case hex."""
    entries, errors = [], []
    for n, line in enumerate(text.splitlines(), 1):
        tok = line.split("#", 1)[0].split()
        if not tok:
            continue
        kind = tok[0]
        r = _parse_form(tok, 1)
        if r is None:
            errors.append("line %d: bad form" % n)
            continue
        ref, pos = r
        if kind in ("owner", "global", "faction"):
            if pos != len(tok):
                errors.append("line %d: trailing tokens" % n)
            else:
                entries.append((kind, ref, None))
        elif kind == "var":
            if pos + 1 != len(tok):
                errors.append("line %d: expected one index or name" % n)
            else:
                v = tok[pos]
                if v.isdigit():
                    if int(v) > 0xFFFF:
                        errors.append("line %d: index out of range" % n)
                    else:
                        entries.append(("var", ref, int(v)))
                else:
                    entries.append(("var", ref, v))
        elif kind == "field":
            if pos + 1 != len(tok) or tok[pos] not in FIELDS:
                errors.append("line %d: unknown field" % n)
            else:
                entries.append(("field", ref, tok[pos]))
        else:
            errors.append("line %d: unknown entry kind '%s' (whole-quest exclusion is not allowed)" % (n, kind))
    return entries, errors


class ReferenceKeys:
    """Pure-Python model of fnvcoop::VolatileKeys (used to cross-check the C++ loader on the same file).
    `names` maps (ref, lowername) -> index, the resolver the engine provides."""

    def __init__(self, text, names=None):
        entries, errs = parse_entries(text)
        if errs:
            raise ValueError("; ".join(errs))
        self.vars, self.owners, self.globals, self.fields = set(), set(), set(), set()
        self.pending = set()
        for kind, ref, arg in entries:
            if kind == "owner": self.owners.add(ref)
            elif kind == "global": self.globals.add(ref)
            elif kind == "field": self.fields.add((ref, arg))
            elif isinstance(arg, int): self.vars.add((ref, arg))
            else: self.pending.add((ref, arg))
        for ref, name in sorted(self.pending):
            idx = (names or {}).get((ref, name.lower()))
            if idx is not None:
                self.vars.add((ref, idx))
        self.unresolved = len([1 for ref, name in self.pending if (names or {}).get((ref, name.lower())) is None])

    def is_var(self, ref, idx):
        return ref in self.owners or (ref, idx) in self.vars

    def is_global(self, ref):
        return ref in self.globals

    def is_field(self, ref, field):
        return (ref, field) in self.fields

    def size(self):
        return len(self.vars) + len(self.owners) + len(self.globals) + len(self.fields) + self.unresolved


# ---------------------------------------------------------------- check
def check_txt(text, story):
    """Violations of the safety rules in an (even hand-edited) volatile keys file."""
    story = _norm_story(story)
    entries, errors = parse_entries(text)
    problems = list(errors)
    for kind, ref, arg in entries:
        if kind == "field" and ref in story:
            problems.append("STORY quest %s (%s) must not have field '%s' excluded" % (story[ref], ref, arg))
    return problems


# ---------------------------------------------------------------- CLI
def main(argv=None):
    snaplib.setup_console()
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0] not in ("probe", "check"):
        print(__doc__)
        return 2
    cmd, rest = argv[0], argv[1:]
    scen = manual = jout = tout = None
    settle, interval = 0, None
    groups, files = [], []
    i = 0
    while i < len(rest):
        a = rest[i]
        if a == "--scenarios": scen = rest[i + 1]; i += 2
        elif a == "--manual": manual = rest[i + 1]; i += 2
        elif a == "--json": jout = rest[i + 1]; i += 2
        elif a == "--txt": tout = rest[i + 1]; i += 2
        elif a == "--group": groups.append([]); i += 1
        elif a == "--settle": settle = int(rest[i + 1]); i += 2
        elif a == "--interval": interval = float(rest[i + 1]); i += 2
        else:
            (groups[-1] if cmd == "probe" and groups else files).append(a); i += 1
    if not scen:
        print("--scenarios DIR is required (the story-quest safety list)")
        return 2
    try:
        story = story_refs(scen)
    except (OSError, ValueError) as e:
        print("INVALID: %s" % e)
        return 2
    if cmd == "check":
        if len(files) != 1:
            print(__doc__)
            return 2
        try:
            with open(files[0], encoding="utf-8") as f:
                problems = check_txt(f.read(), story)
        except OSError as e:
            print("INVALID: %s" % e)
            return 2
        for p in problems:
            print("VIOLATION " + p)
        print("OK (%d story quests protected)" % len(story) if not problems else "%d violation(s)" % len(problems))
        return 1 if problems else 0
    try:
        snaps = [[snaplib.load(p) for p in g] for g in groups]
        res = probe(snaps, story, load_manual(manual or MANUAL_DEFAULT), settle=settle, interval_s=interval)
    except (snaplib.SnapshotError, ValueError, OSError) as e:
        print("INVALID: %s" % e)
        return 2
    txt = render_txt(res)
    problems = check_txt(txt, story)   # belt and braces: our own output must pass our own check
    if problems:
        print("INTERNAL ERROR: generated file violates safety rules: %s" % problems[0])
        return 2
    if jout:
        os.makedirs(os.path.dirname(os.path.abspath(jout)), exist_ok=True)
        with open(jout, "w", encoding="utf-8", newline="\n") as f:
            json.dump(res, f, indent=1, sort_keys=True)
            f.write("\n")
    if tout:
        os.makedirs(os.path.dirname(os.path.abspath(tout)), exist_ok=True)
        with open(tout, "w", encoding="utf-8", newline="\n") as f:
            f.write(txt)
    c = res["counts"]
    print("volatile: %d vars, %d quest fields, %d globals (protected %d) from %d snapshots / %d launch(es)"
          % (c["vars"], c["fields"], c["globals"], c["protected"], c["snapshots"], c["groups"]))
    if res.get("settling"):
        print("settling (change only in the first %d dump intervals, NOT volatile): %d keys, recommended settle time %s s"
              % (settle, len(res["settling"]), res.get("recommendedSettleSeconds")))
    for w in res["warnings"]:
        print("WARNING story quest %s (%s) %s changes by itself (%s): NOT excluded" % (
            w["edid"], w["quest"], w["field"], ",".join(w["reasons"])))
    return 0


if __name__ == "__main__":
    sys.exit(main())
