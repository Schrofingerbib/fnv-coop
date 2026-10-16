#!/usr/bin/env python3
"""Observed-effects oracle: derive the shared-state changes between two snapshots, expressed in the
Effect vocabulary of synccore/include/fnvcoop/effect.h.

  effect_oracle.py BEFORE.json AFTER.json [--json]

Effect dict form (JSON): {"type": "SetStage", "quest": "FalloutNV.esm:080664", "stage": 20}.
Types and fields (same names as effect.h):
  SetStage{quest,stage}  SetObjective{quest,objective,flags(bit0 displayed, bit1 completed)}
  SetScriptVar{owner(quest or ref),var,value}  SetGlobal{global,value}  EnableRef{ref}  DisableRef{ref}  KillActor{actor}  ResurrectActor{actor}
  StartQuest{quest}  StopQuest{quest}  MoveRef{ref,cell}  ModReputation{faction,fame,infamy,to}
  ModKarma{delta,to}  AddItem/RemoveItem{item,count,to}  ModActorValue{av,delta,to}
Oracle-only extensions (observable in a snapshot, absent from effect.h): SetScriptVar (quest vars + watched-ref vars), CompleteQuest{quest},
  FailQuest{quest}, SetFactionRank{faction,rank}, AddPerk{perk,rank,to}, ClearObjective{quest,objective,flags}.
`to` is "shared" for shared-state effects, otherwise a player id, or "all" after aggregation when every
player in the snapshot saw the identical effect (see aggregate()).

Library use: observe(before, after) -> list of effect dicts (deterministically sorted).
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import snaplib  # noqa: E402

AV_CODES = {"Strength": 5, "Perception": 6, "Endurance": 7, "Charisma": 8, "Intelligence": 9, "Agility": 10,
            "Luck": 11, "XP": 24, "Barter": 32, "BigGuns": 33, "EnergyWeapons": 34, "Explosives": 35,
            "Lockpick": 36, "Medicine": 37, "MeleeWeapons": 38, "Repair": 39, "Science": 40, "Guns": 41,
            "Sneak": 42, "Speech": 43, "Survival": 44, "Unarmed": 45}
CAPS = "FalloutNV.esm:00000F"
FLOAT_TOL = 1e-3  # SetGlobal change threshold (same as snapshot tolerance for globals)


def ekey(e):
    return json.dumps(e, sort_keys=True)


def _vol_refs(*snaps):
    out = set()
    for s in snaps:
        for ref, g in ((s.get("shared") or {}).get("globals") or {}).items():
            if isinstance(g, dict) and g.get("edid") in snaplib.VOLATILE_GLOBAL_EDIDS:
                out.add(ref)
    return out


def _obj_flags(o):
    return (1 if o.get("displayed") else 0) | (2 if o.get("completed") else 0)


VAR_TOL = 1e-4


def _var_effects(owner, v0, v1):
    fx = []
    for name in sorted(v1):
        old = v0.get(name, 0)  # a variable that appears holding 0 is its default, not a change
        if abs(v1[name] - old) > VAR_TOL:
            fx.append({"type": "SetScriptVar", "owner": owner, "var": name, "value": v1[name]})
    return fx


def observe_shared(before, after):
    b, a = before.get("shared") or {}, after.get("shared") or {}
    fx = []
    bq, aq = b.get("quests") or {}, a.get("quests") or {}
    for ref in sorted(set(bq) | set(aq)):
        q0 = bq.get(ref) or {"running": False, "completed": False, "failed": False, "currentStage": 0,
                              "completedStages": [], "objectives": {}}
        q1 = aq.get(ref)
        if q1 is None:
            continue
        done0, done1 = set(q0.get("completedStages", [])), set(q1.get("completedStages", []))
        stages = done1 - done0
        if q1.get("currentStage") != q0.get("currentStage") and q1.get("currentStage"):
            stages.add(q1["currentStage"])  # stage set but isDone not (yet) recorded
        for s in sorted(stages):
            fx.append({"type": "SetStage", "quest": ref, "stage": s})
        for oid in sorted(set(q0.get("objectives", {})) | set(q1.get("objectives", {})), key=int):
            f0 = _obj_flags(q0.get("objectives", {}).get(oid, {}))
            f1 = _obj_flags(q1.get("objectives", {}).get(oid, {}))
            if f1 & ~f0:
                fx.append({"type": "SetObjective", "quest": ref, "objective": int(oid), "flags": f1})
            if f0 & ~f1:
                fx.append({"type": "ClearObjective", "quest": ref, "objective": int(oid), "flags": f0 & ~f1})
        fx += _var_effects(ref, q0.get("vars", {}), q1.get("vars", {}))
        if q1.get("running") and not q0.get("running"):
            fx.append({"type": "StartQuest", "quest": ref})
        if q0.get("running") and not q1.get("running"):
            fx.append({"type": "StopQuest", "quest": ref})
        if q1.get("completed") and not q0.get("completed"):
            fx.append({"type": "CompleteQuest", "quest": ref})
        if q1.get("failed") and not q0.get("failed"):
            fx.append({"type": "FailQuest", "quest": ref})

    vol = _vol_refs(before, after)
    bg, ag = b.get("globals") or {}, a.get("globals") or {}
    for ref in sorted(ag):
        if ref in vol:
            continue
        v1 = ag[ref]["value"]
        v0 = bg.get(ref, {}).get("value")
        if v0 is None or abs(v1 - v0) > FLOAT_TOL:
            fx.append({"type": "SetGlobal", "global": ref, "value": v1})

    br, ar = b.get("reputations") or {}, a.get("reputations") or {}
    for ref in sorted(ar):
        d_fame = round(ar[ref]["fame"] - br.get(ref, {}).get("fame", 0.0))
        d_inf = round(ar[ref]["infamy"] - br.get(ref, {}).get("infamy", 0.0))
        if d_fame or d_inf:
            fx.append({"type": "ModReputation", "faction": ref, "fame": d_fame, "infamy": d_inf, "to": "shared"})
    if "karma" in b and "karma" in a:
        dk = round(a["karma"] - b["karma"])
        if dk:
            fx.append({"type": "ModKarma", "delta": dk, "to": "shared"})

    bf, af = b.get("factionRanks") or {}, a.get("factionRanks") or {}
    for ref in sorted(af):
        if af[ref]["rank"] != bf.get(ref, {}).get("rank"):
            fx.append({"type": "SetFactionRank", "faction": ref, "rank": af[ref]["rank"]})

    brf, arf = b.get("refs") or {}, a.get("refs") or {}
    for ref in sorted(arf):
        n, o = arf[ref], brf.get(ref, {})
        if "enabled" in n and "enabled" in o and n["enabled"] != o["enabled"]:
            fx.append({"type": "EnableRef" if n["enabled"] else "DisableRef", "ref": ref})
        if "alive" in n and "alive" in o and n["alive"] != o["alive"]:
            fx.append({"type": "ResurrectActor" if n["alive"] else "KillActor", "actor": ref})
        fx += _var_effects(ref, o.get("vars", {}), n.get("vars", {}))
        if "cell" in n and "cell" in o and n["cell"] != o["cell"] and n["cell"]:
            fx.append({"type": "MoveRef", "ref": ref, "cell": n["cell"]})
    return fx


def observe_players(before, after):
    fx = []
    bp, ap = before.get("perPlayer") or {}, after.get("perPlayer") or {}
    for pid in sorted(ap):
        p1, p0 = ap[pid], bp.get(pid)
        if p0 is None:
            continue
        inv0, inv1 = p0.get("inventory", {}), p1.get("inventory", {})
        for item in sorted(set(inv0) | set(inv1)):
            d = inv1.get(item, {}).get("count", 0) - inv0.get(item, {}).get("count", 0)
            if d:
                fx.append({"type": "AddItem" if d > 0 else "RemoveItem", "item": item, "count": abs(d), "to": pid})
        dc = p1.get("caps", 0) - p0.get("caps", 0)
        if dc:
            fx.append({"type": "AddItem" if dc > 0 else "RemoveItem", "item": CAPS, "count": abs(dc), "to": pid})
        for name, code in sorted(AV_CODES.items(), key=lambda kv: kv[1]):
            field = "current" if name == "XP" else "base"   # XP: base stays 0, current carries the experience
            d0 = p0.get("actorValues", {}).get(name, {}).get(field)
            d1 = p1.get("actorValues", {}).get(name, {}).get(field)
            if d0 is not None and d1 is not None and round(d1 - d0):
                fx.append({"type": "ModActorValue", "av": code, "delta": round(d1 - d0), "to": pid})
        for perk, rank in sorted(p1.get("perks", {}).items()):
            if rank != p0.get("perks", {}).get(perk):
                fx.append({"type": "AddPerk", "perk": perk, "rank": rank, "to": pid})
    return fx


def aggregate(fx, players):
    """Collapse per-player effects that are identical for every player into to="all"."""
    players = sorted(players)
    groups = {}
    out = []
    for e in fx:
        if e.get("to") in players:
            k = ekey({k: v for k, v in e.items() if k != "to"})
            groups.setdefault(k, {})[e["to"]] = e
        else:
            out.append(e)
    for k, byp in sorted(groups.items()):
        some = next(iter(byp.values()))
        if len(players) > 1 and sorted(byp) == players:
            out.append(dict(some, to="all"))
        else:
            out.extend(byp[p] for p in sorted(byp))
    return out


def observe(before, after, do_aggregate=True):
    fx = observe_shared(before, after) + observe_players(before, after)
    if do_aggregate:
        fx = aggregate(fx, list((after.get("perPlayer") or {}).keys()))
    return sorted(fx, key=ekey)


def main(argv=None):
    snaplib.setup_console()
    argv = list(sys.argv[1:] if argv is None else argv)
    if len(argv) < 2:
        print(__doc__)
        return 2
    try:
        b, a = snaplib.load(argv[0]), snaplib.load(argv[1])
    except snaplib.SnapshotError as e:
        print("INVALID: %s" % e)
        return 2
    problems = ["before: " + p for p in snaplib.guard(b)] + ["after: " + p for p in snaplib.guard(a)]
    if problems:
        print("INVALID SNAPSHOT:\n  " + "\n  ".join(problems))
        return 2
    fx = observe(b, a)
    if "--json" in argv:
        print(json.dumps(fx, indent=1))
    else:
        for e in fx:
            print(ekey(e))
        print("%d observed effect(s)" % len(fx))
    return 0


if __name__ == "__main__":
    sys.exit(main())
