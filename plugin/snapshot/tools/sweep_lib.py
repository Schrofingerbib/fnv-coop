"""Game-free analysis for the Goodsprings capture sweep (goodsprings_sweep.py).

For one stage we have: S0 (after load + quest reset), S1 (control dump a few seconds later, same idle world), S2 (after
`setstage Q N` + settle) and the hooks capture (capture.end txns). analyze_stage() answers, per stage:
  * missing_state   : unconditional expected effects (static oracle + implicit SetStage) the S1 -> S2 state delta lacks,
  * missing_capture : unconditional expected effects the hooks did not capture,
  * extras          : observed effects explained by neither the oracle (unconditional + conditional), the capture,
                      the implicit engine behaviour (StartQuest of a not-running quest) nor the ambient filter,
  * captured_not_observed : informational.
Status: pass | fail | noent (static oracle has no entry for the stage).

EVERY rule that removes an expectation or explains an observation is listed here with its written reason and is
recorded per stage in result["rules"] so nothing is silently loosened:

  R-DROP-OBJ   expected SetObjective/ClearObjective on an objective id that does not exist in the quest's runtime
               objective list (S2): the engine cannot perform it; oracle/data bug, dropped from state AND capture.
  R-DROP-ITEM  expected RemoveItem for an item/count the player does not hold in S1: the script command is a no-op,
               dropped from state AND capture (the oracle should have marked it conditional).
  R-ALREADY    expected end state already true in S1 (stage done, objective bits set, quest running/stopped/completed/
               failed, ref already enabled/disabled/dead, global already equal): a before/after diff cannot show it.
               State check skipped; the capture must still contain it.
  R-UNOBS      effect with no footprint in the snapshot: SetGlobal of a per-machine clock global (GameHour...),
               MoveRef of the player, Enable/Disable/Kill/MoveRef of a ref that is not in the dumped watch list.
               State check skipped; the capture must still contain it.
  R-NETZERO    StopQuest/StartQuest/CompleteQuest of the stage's own quest when S1 shows it not running: SetStage first
               starts the quest, the script then stops it, so S1 -> S2 shows no running change. State check skipped.
  R-NORM       vocabulary differences between hooks and oracle: XP is pseudo-AV 0x10000 in the capture vs engine AV 24
               in the oracle; recipient "default" means "the triggering player / shared by policy" (wildcard); a captured
               SetObjective with flags 0 is how the hooks encode SetObjectiveDisplayed(.., 0) (explains ClearObjective).
  R-IMPLICIT   SetStage on a not-running quest starts it: an observed StartQuest of the stage's quest is expected, and the
               capture must contain StartQuest (this is what exposes a hub that emitted no txn).
  R-AMBIENT    observed SetScriptVar for a (owner, variable NAME) listed in the shipped volatile keys file
               (plugin/snapshot/data/volatile_keys.txt, probe + manual additions): background quest-script timers.
  R-DRIFT      effects already moving between S0 and S1 (same identity, any value) and NVDLC startup machinery.
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import check_stage  # noqa: E402
import effect_oracle  # noqa: E402
import snaplib  # noqa: E402

IGNORE_QUEST_PREFIXES = ("NVDLC",)
PLAYER_REF = "falloutnv.esm:000014"
XP_PSEUDO_AV = 65536
XP_AV = 24
VOLATILE_KEYS = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data", "volatile_keys.txt"))


def low(e):
    return json.loads(json.dumps(e).lower())


def ekey(e):
    return json.dumps(e, sort_keys=True)


def ident(e):
    """Identity of a state change ignoring its value (a moving timer is one drifting key)."""
    return ekey({k: v for k, v in e.items() if k != "value"})


def ignored_refs(*snaps):
    out = set()
    for s in snaps:
        for ref, q in ((s.get("shared") or {}).get("quests") or {}).items():
            if str(q.get("edid", "")).startswith(IGNORE_QUEST_PREFIXES):
                out.add(ref.lower())
    return out


def load_volatile_names(path=VOLATILE_KEYS):
    """{(owner_ref_lower, varname_lower)} and {owner_ref_lower} (owner entries) from the shipped volatile keys file."""
    import volatile_probe
    names, owners = set(), set()
    try:
        with open(path, encoding="utf-8") as f:
            entries, _ = volatile_probe.parse_entries(f.read())
    except OSError:
        return names, owners
    for kind, ref, arg in entries:
        if kind == "var" and isinstance(arg, str):
            names.add((ref.lower(), arg.lower()))
        elif kind == "owner":
            owners.add(ref.lower())
    return names, owners


def _owner_ref(e):
    return (e.get("quest") or e.get("owner") or "").lower()


def _ambient(e, vol):
    names, owners = vol
    return e.get("type") == "setscriptvar" and (e.get("owner") in owners or (e.get("owner"), str(e.get("var")).lower()) in names)


def observed_delta(s0, s1, s2, vol=None):
    """State delta of the action (S1 -> S2) minus drift (R-DRIFT) and ambient (R-AMBIENT). Lower-cased."""
    vol = vol if vol is not None else load_volatile_names()
    drift = {ident(low(e)) for e in effect_oracle.observe(s0, s1)}
    skip = ignored_refs(s1, s2)
    out = []
    for e in effect_oracle.observe(s1, s2):
        le = low(e)
        if ident(le) in drift or _owner_ref(le) in skip or _ambient(le, vol):
            continue
        out.append(le)
    return out


def captured_effects(cap):
    return [low(e) for t in (cap or {}).get("txns", []) for e in t.get("effects", [])]


def normalize_captured(got):
    """R-NORM."""
    out = []
    for g in got:
        g = dict(g)
        if g.get("type") == "modactorvalue" and g.get("av") == XP_PSEUDO_AV:
            g["av"] = XP_AV
        if g.get("to") == "default":
            del g["to"]
        out.append(g)
        if g.get("type") == "setobjective" and g.get("flags") == 0:
            out.append({"type": "clearobjective", "quest": g.get("quest"), "objective": g.get("objective"), "flags": 3})   # any bits
    return out


def _covers(want, got_list):
    return any(check_stage.matches(want, g) for g in got_list)


def _q(snap, ref):
    return ((snap.get("shared") or {}).get("quests") or {}).get(ref) or \
        next((v for k, v in ((snap.get("shared") or {}).get("quests") or {}).items() if k.lower() == ref), None)


def _lower_map(d):
    return {k.lower(): v for k, v in (d or {}).items()}


def classify_expectation(e, ctx):
    """-> (action, rule): action in keep | drop | skip_state (capture still required)."""
    t = e.get("type")
    s1, s2 = ctx["s1"], ctx["s2"]
    quests1, quests2 = ctx["quests1"], ctx["quests2"]
    refs1, refs2 = ctx["refs1"], ctx["refs2"]
    own = ctx["own_ref"]
    if t in ("setobjective", "clearobjective"):
        q2 = quests2.get(e.get("quest"))
        if q2 is not None and str(e.get("objective")) not in q2.get("objectives", {}):
            return "drop", "R-DROP-OBJ"
        q1 = quests1.get(e.get("quest"))
        if q1 is not None:
            o = q1.get("objectives", {}).get(str(e.get("objective")), {})
            f1 = (1 if o.get("displayed") else 0) | (2 if o.get("completed") else 0)
            if t == "setobjective" and (f1 & e.get("flags", 0)) == e.get("flags", 0):
                return "skip_state", "R-ALREADY"
            if t == "clearobjective" and (f1 & e.get("flags", 0)) == 0:
                return "skip_state", "R-ALREADY"
    elif t == "removeitem":
        held = 0
        for p in (s1.get("perPlayer") or {}).values():
            for ref, it in (p.get("inventory") or {}).items():
                if ref.lower() == e.get("item"):
                    held = max(held, it.get("count", 0))
        if held < e.get("count", 1):
            return "drop", "R-DROP-ITEM"
    elif t == "setstage":
        q1 = quests1.get(e.get("quest"))
        if q1 is not None and e.get("stage") in q1.get("completedStages", []):
            return "skip_state", "R-ALREADY"
    elif t in ("startquest", "stopquest", "completequest", "failquest"):
        q1 = quests1.get(e.get("quest"))
        if q1 is not None:
            if t == "startquest" and q1.get("running"):
                return "skip_state", "R-ALREADY"
            if t == "stopquest" and not q1.get("running"):
                return "skip_state", "R-NETZERO" if e.get("quest") == own else "R-ALREADY"
            if t == "completequest" and q1.get("completed"):
                return "skip_state", "R-ALREADY"
            if t == "failquest" and q1.get("failed"):
                return "skip_state", "R-ALREADY"
    elif t == "setglobal":
        g1 = _lower_map((s1.get("shared") or {}).get("globals"))
        g = g1.get(e.get("global"))
        if g is not None:
            if g.get("edid") in snaplib.VOLATILE_GLOBAL_EDIDS:
                return "skip_state", "R-UNOBS"
            if abs(g.get("value", 0) - e.get("value", 0)) <= effect_oracle.FLOAT_TOL:
                return "skip_state", "R-ALREADY"
    elif t in ("enableref", "disableref", "killactor", "resurrectactor", "moveref"):
        ref = e.get("ref") or e.get("actor")
        if t == "moveref" and ref == PLAYER_REF:
            return "skip_state", "R-UNOBS"
        if ref not in refs2:
            return "skip_state", "R-UNOBS"
        r1 = refs1.get(ref, {})
        if t == "enableref" and r1.get("enabled") is True:
            return "skip_state", "R-ALREADY"
        if t == "disableref" and r1.get("enabled") is False:
            return "skip_state", "R-ALREADY"
        if t == "killactor" and r1.get("alive") is False:
            return "skip_state", "R-ALREADY"
    return "keep", None


def _explained(obs, allowed, captured):
    if any(check_stage.matches(a, obs) for a in allowed):
        return True
    if any(check_stage.matches(c, obs) for c in captured):
        return True
    if obs.get("type") == "setscriptvar":
        # observed vars carry a NAME, captured ones an INDEX: match on the owner only
        return any(c.get("type") == "setscriptvar" and c.get("owner") == obs.get("owner") for c in captured)
    return False


def analyze_stage(oracle, edid, stage, s0, s1, s2, cap, vol=None):
    res = {"quest": edid, "stage": stage, "status": "pass", "reasons": [], "missing_state": [], "missing_capture": [],
           "extras": [], "captured_not_observed": [], "rules": [], "counts": {}}
    problems = []
    for name, s in (("S1", s1), ("S2", s2)):
        problems += ["%s: %s" % (name, p) for p in snaplib.guard(s, ("shared",))]
    q = (oracle.get("quests") or {}).get(edid)
    entry = ((q or {}).get("stages") or {}).get(str(stage))
    if problems:
        res["status"], res["reasons"] = "fail", problems[:3]
        return res
    own_ref = None
    for ref, v in ((s2.get("shared") or {}).get("quests") or {}).items():
        if v.get("edid") == edid:
            own_ref = ref.lower()
    if own_ref is None:
        res["status"], res["reasons"] = "fail", ["quest %s not in snapshot" % edid]
        return res
    noent = entry is None
    unconditional = [low({"type": "SetStage", "quest": own_ref, "stage": stage})]
    conditional = []
    if not noent:
        unconditional += [low(e) for e in entry.get("unconditional", [])]
        conditional = [low(e) for e in entry.get("conditional", [])]
        unconditional = list({ekey(e): e for e in unconditional}.values())
    else:
        res["reasons"].append("oracle has no entry for %s stage %s" % (edid, stage))

    ctx = {"s1": s1, "s2": s2, "own_ref": own_ref,
           "quests1": _lower_map((s1.get("shared") or {}).get("quests")),
           "quests2": _lower_map((s2.get("shared") or {}).get("quests")),
           "refs1": _lower_map((s1.get("shared") or {}).get("refs")),
           "refs2": _lower_map((s2.get("shared") or {}).get("refs"))}
    observed_raw = [low(e) for e in effect_oracle.observe(s1, s2)]
    observed = observed_delta(s0, s1, s2, vol)
    got = normalize_captured(captured_effects(cap))

    need_state, need_capture = [], []
    for e in unconditional:
        action, rule = classify_expectation(e, ctx)
        if rule:
            res["rules"].append({"rule": rule, "effect": e})
        if action == "drop":
            continue
        need_capture.append(e)
        if action == "keep":
            need_state.append(e)
    # R-IMPLICIT: a not-running quest is started by SetStage; if the state shows it the capture must too
    q1, q2 = ctx["quests1"].get(own_ref), ctx["quests2"].get(own_ref)
    implicit_start = bool(q1 is not None and q2 is not None and not q1.get("running") and q2.get("running"))
    if implicit_start:
        e = {"type": "startquest", "quest": own_ref}
        need_capture.append(e)
        res["rules"].append({"rule": "R-IMPLICIT", "effect": e})

    res["missing_state"] = [e for e in need_state if not _covers(e, observed_raw)]
    res["missing_capture"] = [e for e in need_capture if not _covers(e, got)]
    allowed = unconditional + conditional + ([{"type": "startquest", "quest": own_ref}] if implicit_start else [])
    res["extras"] = [o for o in observed if not _explained(o, allowed, got)]
    res["captured_not_observed"] = [g for g in got if g.get("type") in ("setstage", "setobjective", "setglobal", "startquest",
                                                                       "stopquest", "completequest")
                                    and not _covers(g, observed_raw)]
    ntx = len((cap or {}).get("txns", []))
    res["counts"] = {"txns": ntx, "captured": len(got), "observed": len(observed), "expected": len(unconditional),
                     "extras": len(res["extras"])}
    if res["missing_state"]:
        res["reasons"].append("missing_state:%d" % len(res["missing_state"]))
    if res["missing_capture"]:
        res["reasons"].append("missing_capture:%d" % len(res["missing_capture"]))
    if res["extras"]:
        res["reasons"].append("extras:%d" % len(res["extras"]))
    if ntx == 0:
        res["reasons"].append("no_txn_captured")
    failing = res["missing_state"] or res["missing_capture"] or res["extras"] or ntx == 0
    res["status"] = "fail" if failing else ("noent" if noent else "pass")
    return res


def quest_status(stage_results):
    """Aggregate per-stage results for the L9 summary: error > fail > pass; noent counts as pass (nothing to check)."""
    st = [r["status"] for r in stage_results]
    if any(s == "error" for s in st):
        return "error"
    if any(s == "fail" for s in st):
        return "fail"
    return "pass" if st else "skip"
