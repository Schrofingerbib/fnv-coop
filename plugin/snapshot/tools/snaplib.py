"""Shared logic for snapdiff.py / converge.py: load, validate, guard, semantic diff.

Exit-code convention used by both CLIs:
  0 = snapshots equivalent, 1 = semantic difference, 2 = invalid / vacuous input (guard failure).
"""
import fnmatch
import json
import os
import re
import sys

SCHEMA_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "snapshot.schema.json")

# Guard thresholds. Real FNV: ~1000+ quests, ~600+ globals. A dump far below that is a broken dump.
DEFAULT_MIN_QUESTS = 50
DEFAULT_MIN_GLOBALS = 10

# (fnmatch on "/"-joined path, absolute tolerance). First match wins; see docs/snapshot-schema.md section 6.
DEFAULT_TOLERANCES = [
    ("shared/globals/*/value", 1e-3),
    ("shared/reputations/*/*", 1e-3),
    ("shared/karma", 1e-3),
    ("shared/quests/*/vars/*", 1e-4),
    ("perPlayer/*/actorValues/*/*", 1e-2),
    ("perPlayer/*/position/rotZ", 5e-2),
    ("perPlayer/*/position/*", 1.0),
    ("*", 1e-4),
]

# Globals that legitimately differ per machine (wall-clock / time accumulators). Matched on the global's edid.
VOLATILE_GLOBAL_EDIDS = {"GameYear", "GameMonth", "GameDay", "GameHour", "GameDaysPassed", "TimeScale"}

# Arrays at these path patterns are sets of scalars: compare order-insensitively.
SET_ARRAY_PATTERNS = ["shared/quests/*/completedStages"]


def setup_console():
    """Make CLI output safe on cp1252 consoles: UTF-8 with replacement instead of UnicodeEncodeError."""
    for st in (sys.stdout, sys.stderr):
        try:
            st.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass


class SnapshotError(Exception):
    pass


def load(path):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError) as e:
        raise SnapshotError("cannot load %s: %s" % (path, e))


# ---------------------------------------------------------------- mini JSON-Schema validator
def _type_ok(v, t):
    if t == "object": return isinstance(v, dict)
    if t == "array": return isinstance(v, list)
    if t == "string": return isinstance(v, str)
    if t == "boolean": return isinstance(v, bool)
    if t == "integer": return isinstance(v, int) and not isinstance(v, bool)
    if t == "number": return isinstance(v, (int, float)) and not isinstance(v, bool)
    if t == "null": return v is None
    return True


def validate(value, schema, root=None, path="$"):
    """Subset of JSON Schema draft-07: type, enum, const, required, properties, patternProperties,
    additionalProperties, items, minimum, maximum, minProperties, pattern, $ref (#/definitions/x)."""
    root = root or schema
    errs = []
    if "$ref" in schema:
        node = root
        for part in schema["$ref"].lstrip("#/").split("/"):
            node = node[part]
        return validate(value, node, root, path)
    t = schema.get("type")
    if t is not None:
        ts = t if isinstance(t, list) else [t]
        if not any(_type_ok(value, x) for x in ts):
            return ["%s: expected %s, got %s" % (path, "|".join(ts), type(value).__name__)]
    if "enum" in schema and value not in schema["enum"]:
        errs.append("%s: %r not in enum" % (path, value))
    if "const" in schema and value != schema["const"]:
        errs.append("%s: expected %r" % (path, schema["const"]))
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if "minimum" in schema and value < schema["minimum"]:
            errs.append("%s: %r < minimum %r" % (path, value, schema["minimum"]))
        if "maximum" in schema and value > schema["maximum"]:
            errs.append("%s: %r > maximum %r" % (path, value, schema["maximum"]))
    if isinstance(value, str) and "pattern" in schema and not re.search(schema["pattern"], value):
        errs.append("%s: %r does not match %s" % (path, value, schema["pattern"]))
    if isinstance(value, dict):
        for r in schema.get("required", []):
            if r not in value:
                errs.append("%s: missing required '%s'" % (path, r))
        props = schema.get("properties", {})
        pprops = schema.get("patternProperties", {})
        addl = schema.get("additionalProperties", True)
        for k, v in value.items():
            sub = "%s.%s" % (path, k)
            if k in props:
                errs += validate(v, props[k], root, sub)
                continue
            matched = False
            for pat, ps in pprops.items():
                if re.search(pat, k):
                    matched = True
                    errs += validate(v, ps, root, sub)
            if matched:
                continue
            if addl is False:
                errs.append("%s: unexpected property" % sub)
            elif isinstance(addl, dict):
                errs += validate(v, addl, root, sub)
        if "propertyNames" in schema:
            for k in value:
                errs += validate(k, schema["propertyNames"], root, "%s{key %s}" % (path, k))
        if "minProperties" in schema and len(value) < schema["minProperties"]:
            errs.append("%s: fewer than %d properties" % (path, schema["minProperties"]))
    if isinstance(value, list) and "items" in schema:
        for i, v in enumerate(value):
            errs += validate(v, schema["items"], root, "%s[%d]" % (path, i))
    return errs


def load_schema():
    with open(SCHEMA_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


# ---------------------------------------------------------------- guard
def guard(snap, sections=("shared",), min_quests=DEFAULT_MIN_QUESTS, min_globals=DEFAULT_MIN_GLOBALS,
          check_schema=True):
    """Return a list of problems that make the snapshot untrustworthy as an oracle. Empty list = usable.
    The point: a dump that is empty / truncated / errored must FAIL, never compare equal vacuously."""
    p = []
    if not isinstance(snap, dict):
        return ["snapshot is not a JSON object"]
    if snap.get("schemaVersion") != 1:
        p.append("schemaVersion is %r, expected 1" % (snap.get("schemaVersion"),))
    meta = snap.get("meta")
    if not isinstance(meta, dict):
        p.append("missing meta")
        meta = {}
    if meta.get("error"):
        p.append("dumper reported error: %s" % meta["error"])
    if "shared" in sections:
        sh = snap.get("shared")
        if not isinstance(sh, dict):
            p.append("missing shared section")
        else:
            quests = sh.get("quests") if isinstance(sh.get("quests"), dict) else {}
            globs = sh.get("globals") if isinstance(sh.get("globals"), dict) else {}
            if len(quests) < min_quests:
                p.append("only %d quests (< %d): empty or truncated dump" % (len(quests), min_quests))
            if len(globs) < min_globals:
                p.append("only %d globals (< %d): empty or truncated dump" % (len(globs), min_globals))
            counts = meta.get("counts")
            if isinstance(counts, dict):
                if counts.get("quests") != len(quests):
                    p.append("meta.counts.quests=%r but %d quests present (truncated?)" % (counts.get("quests"), len(quests)))
                if counts.get("globals") != len(globs):
                    p.append("meta.counts.globals=%r but %d globals present (truncated?)" % (counts.get("globals"), len(globs)))
            elif min_quests > 0:
                p.append("meta.counts missing (cannot detect truncation)")
    if "perPlayer" in sections:
        pp = snap.get("perPlayer")
        if not isinstance(pp, dict) or not pp:
            p.append("missing or empty perPlayer section")
    if check_schema and not p:
        try:
            p += validate(snap, load_schema())[:10]
        except OSError as e:
            p.append("cannot read schema: %s" % e)
    return p


# ---------------------------------------------------------------- diff
def _flatten(node, path, out, set_patterns):
    key = "/".join(path)
    if isinstance(node, dict):
        if not node:
            out[key] = {}  # empty object is a leaf marker
        for k in node:
            _flatten(node[k], path + [str(k)], out, set_patterns)
    elif isinstance(node, list):
        if any(fnmatch.fnmatchcase(key, pat) for pat in set_patterns) and all(
                not isinstance(x, (dict, list)) for x in node):
            out[key] = ("set", tuple(sorted(node, key=lambda x: (str(type(x)), x))))
        else:
            out[key] = ("list", tuple(json.dumps(x, sort_keys=True) for x in node))
    else:
        out[key] = node


def _tol_for(path, tolerances):
    for pat, tol in tolerances:
        if fnmatch.fnmatchcase(path, pat):
            return tol
    return 1e-4


def _equal(a, b, path, tolerances):
    if isinstance(a, bool) or isinstance(b, bool):
        return type(a) is type(b) and a == b
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        if isinstance(a, int) and isinstance(b, int):
            return a == b
        return abs(a - b) <= _tol_for(path, tolerances)
    return a == b


def _label(snap, path):
    """Human label: insert the EDID next to FormRef keys, e.g. quests VMS01 (FalloutNV.esm:104C2A)."""
    parts = path.split("/")
    node = snap
    for i, part in enumerate(parts):
        if not isinstance(node, dict) or part not in node:
            break
        node = node[part]
        if isinstance(node, dict) and isinstance(node.get("edid"), str) and node.get("edid"):
            parts[i] = "%s (%s)" % (node["edid"], part)
    return "/".join(parts)


def volatile_paths(snap):
    """Path prefixes of volatile globals (clock values) present in snap."""
    res = []
    g = (snap.get("shared") or {}).get("globals") or {}
    for ref, v in g.items():
        if isinstance(v, dict) and v.get("edid") in VOLATILE_GLOBAL_EDIDS:
            res.append("shared/globals/%s" % ref)
    return res


def diff(a, b, sections=("shared",), include_meta=False, tolerances=None, ignore=(),
         default_ignores=True):
    """Semantic diff. Returns list of dicts {kind: changed|added|removed, path, a, b, label}."""
    tolerances = list(tolerances or []) + DEFAULT_TOLERANCES
    ignore = list(ignore)
    if default_ignores:
        ignore += [p + "*" for p in set(volatile_paths(a)) | set(volatile_paths(b))]
    ignore += ["meta/timestamp", "meta/instance"]  # never diffed even with include_meta

    def pick(s):
        r = {}
        for sec in sections:
            if sec in s:
                _flatten(s[sec], [sec], r, SET_ARRAY_PATTERNS)
        if include_meta and "meta" in s:
            _flatten(s["meta"], ["meta"], r, SET_ARRAY_PATTERNS)
        return r

    fa, fb = pick(a), pick(b)
    out = []
    for path in sorted(set(fa) | set(fb)):
        if any(fnmatch.fnmatchcase(path, pat) or path == pat for pat in ignore):
            continue
        if path not in fb:
            out.append({"kind": "removed", "path": path, "a": fa[path], "b": None, "label": _label(a, path)})
        elif path not in fa:
            out.append({"kind": "added", "path": path, "a": None, "b": fb[path], "label": _label(b, path)})
        elif not _equal(fa[path], fb[path], path, tolerances):
            out.append({"kind": "changed", "path": path, "a": fa[path], "b": fb[path], "label": _label(a, path)})
    return out


def _fmt(v):
    if isinstance(v, tuple) and v and v[0] in ("set", "list"):
        return "%s%s" % (v[0], list(v[1]))
    return json.dumps(v)


def render(d, limit=50):
    lines = []
    for e in d[:limit]:
        if e["kind"] == "changed":
            lines.append("  CHANGED %s: %s -> %s" % (e["label"], _fmt(e["a"]), _fmt(e["b"])))
        elif e["kind"] == "added":
            lines.append("  ADDED   %s = %s" % (e["label"], _fmt(e["b"])))
        else:
            lines.append("  REMOVED %s (was %s)" % (e["label"], _fmt(e["a"])))
    if len(d) > limit:
        lines.append("  ... and %d more" % (len(d) - limit))
    return "\n".join(lines)


def parse_tols(items):
    res = []
    for it in items or []:
        pat, _, val = it.rpartition("=")
        res.append((pat, float(val)))
    return res
