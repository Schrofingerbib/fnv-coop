#!/usr/bin/env python3
"""Generate the stage sweep configs and playthrough plans of every questline.

    python -I tools/questsweep/gen_plans.py            # write questlines/<line>/playthrough.json for all lines + questlines/goodsprings/sweep.json
    python -I tools/questsweep/gen_plans.py --check    # regenerate in memory and fail when a committed file is stale
    python -I tools/questsweep/gen_plans.py --line ncr

Inputs: the lane's sweep config (read with plugin/snapshot/tools/sweep_config.py, which understands all 15 shapes), out/questdata
(quests.json, effects.json.gz, infos.jsonl.gz, refnames.json.gz) and docs/quest-policies/rules.json. Output per line,
questlines/<line>/playthrough.json: a superset of the m1_goodsprings.json sweep format (quests[].edid/ref/stages[].index/objective_effects), so
plugin/snapshot/tools/sweep_config.load_config reads it too. What it adds, per stage:

  fx       the UNCONDITIONAL effects of the stage script (quest objectives, items to the player, XP, karma, enable/disable, kills,
           other quests started or advanced). These are what the playbook asserts on every machine.
  cond     counts of conditional effects (the host decides the branch; the playbook only requires every machine to agree)
  trigger  the dialogue INFO whose result script sets this stage (the natural way to reach it), when there is one
  act      what a player does for the objectives the stage displays: kill / talk / walk / take, with the objective's placed target refs

and per quest the co-op rules the playbook has to honour (barrier, ready check, exclusive outcomes, guest-local, per-player vars).
"""
from __future__ import annotations

import argparse
import os
import re
import sys
from collections import defaultdict
from typing import Any, Dict, List, Optional, Tuple

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "..", "plugin", "snapshot", "tools"))

from questsweep import common, rulesview  # noqa: E402
from questsweep.lines import BY_SLUG, LINES, Line  # noqa: E402

SCHEMA = 1
KIND = "questline-playthrough"
MAX_ACT_REFS = 3

VERBS = [("kill", re.compile(r"^(kill|destroy|eliminate|defeat|assassinate|slay|eradicate|clear out|wipe out|take out|dispose of)\b", re.I)),
         ("take", re.compile(r"^(collect|retrieve|pick up|take|grab|obtain|get|steal|loot|recover|fetch|gather|find the|acquire|salvage|buy)\b", re.I)),
         ("talk", re.compile(r"^(talk|speak|meet|ask|tell|report|return|bring|deliver|give|show|consult|visit|see|convince|persuade|confront|inform|"
                             r"hand|discuss|question|interrogate|negotiate|trade|use the terminal)\b", re.I))]


def verb_of(text: str) -> str:
    t = (text or "").strip()
    for name, rx in VERBS:
        if rx.search(t):
            return name
    return "walk"


# ---------------------------------------------------------------------------------------------------------------- data
class QData:
    def __init__(self, qd_dir: str = common.QD):
        self.dir = qd_dir
        j = lambda f: os.path.join(qd_dir, f)
        qs = common.load_json(j("quests.json"))["quests"]
        self.by_ref = {q["ref"]: q for q in qs}
        self.by_edid = {q["edid"]: q for q in qs}
        eff = common.load_json(j("effects.json.gz"))
        self.stage_scripts: Dict[Tuple[str, int], List[Tuple[int, dict]]] = defaultdict(list)
        self.info_scripts: Dict[str, List[Tuple[str, dict]]] = defaultdict(list)
        for s in eff["scripts"]:
            p = s["id"].split(":")
            if s["kind"] == "stage":
                self.stage_scripts[(f"{p[1]}:{p[2]}", int(p[3]))].append((int(p[4]), s))
            else:
                self.info_scripts[f"{p[1]}:{p[2]}"].append((p[3], s))
        # (quest ref, stage) -> [(info ref, 'begin'|'end')] for INFO scripts that set the stage unconditionally
        self.stage_infos: Dict[Tuple[str, int], List[Tuple[str, str]]] = defaultdict(list)
        for iref, lst in self.info_scripts.items():
            for on, s in lst:
                for e in s["effects"]:
                    if e["type"] == "SetStage" and not e.get("conditional") and not e.get("dynamic") and e.get("quest"):
                        self.stage_infos[(e["quest"], int(e["stage"]))].append((iref, on))
        # an INFO that sets several stages unconditionally (branches the effect extractor could not see) is no trigger for any of them
        targets: Dict[str, set] = defaultdict(set)
        for (qref, st), lst in self.stage_infos.items():
            for iref, _ in lst:
                targets[iref].add((qref, st))
        self.ambiguous = {i for i, t in targets.items() if len(t) > 1}
        self._info_rows: Optional[Dict[str, dict]] = None

    def info_rows(self) -> Dict[str, dict]:
        """Quest, flags and speaker base of the INFOs that are triggers (one pass over infos.jsonl.gz)."""
        if self._info_rows is None:
            want = {i for v in self.stage_infos.values() for i, _ in v}
            rows = {}
            for r in common.load_jsonl_gz(os.path.join(self.dir, "infos.jsonl.gz")):
                if r["ref"] in want:
                    sp = [c.get("p1") for c in r.get("conditions", []) if c.get("func") == "GetIsID" and not c.get("or")]
                    rows[r["ref"]] = {"quest": r.get("quest"), "flags": r.get("flags", []), "speaker_base": sp[0] if sp else None}
            self._info_rows = rows
        return self._info_rows


def _is_player(ref: Optional[str]) -> bool:
    return bool(ref) and common.full_ref(ref) == common.PLAYER_REF


def summarize(effects: List[dict], entry_conditional: bool) -> Tuple[Dict[str, Any], Dict[str, int]]:
    """Unconditional effects as the `fx` dict and the count of conditional effects per type."""
    fx: Dict[str, Any] = {}
    cond: Dict[str, int] = defaultdict(int)

    def add(key, val):
        lst = fx.setdefault(key, [])
        if val not in lst:
            lst.append(val)

    for e in effects:
        t = e["type"]
        if entry_conditional or e.get("conditional") or e.get("dynamic"):
            cond[t] += 1
            continue
        if t == "SetObjective" and e.get("quest") and e.get("kind") in ("displayed", "completed"):
            add("obj", [common.runtime_id(e["quest"]), int(e["objective"]), e["kind"], int(e["value"])])
        elif t == "SetStage" and e.get("quest"):
            add("stage", [common.runtime_id(e["quest"]), int(e["stage"])])
        elif t in ("StartQuest", "StopQuest", "CompleteQuest") and e.get("quest"):
            add("quest", [common.runtime_id(e["quest"]), t[:-5].lower()])
        elif t == "AddItem" and _is_player(e.get("to")):
            add("items", [common.full_ref(e["item"]), int(e.get("count", 1))])
        elif t == "ModActorValue" and e.get("av_name") == "XP" and _is_player(e.get("to")) and e.get("mode") == "mod":
            fx["xp"] = fx.get("xp", 0) + int(e.get("delta", 0))
        elif t == "ModKarma":
            fx["karma"] = fx.get("karma", 0) + int(e.get("delta", 0))
        elif t == "EnableRef" and e.get("ref"):
            add("enable", common.full_ref(e["ref"]))
        elif t == "DisableRef" and e.get("ref"):
            add("disable", common.full_ref(e["ref"]))
        elif t == "KillActor" and e.get("actor"):
            add("kill", common.full_ref(e["actor"]))
        else:
            cond["unmodeled:" + t] += 1
    return fx, dict(cond)


def stage_block(qd: QData, quest: dict, idx: int) -> Dict[str, Any]:
    """fx / cond for one stage, merged over the stage's result-script entries."""
    ent = {}
    for st in quest.get("stages", []):
        if st["index"] == idx:
            ent = {i: e for i, e in enumerate(st.get("entries", []))}
    fx: Dict[str, Any] = {}
    cond: Dict[str, int] = defaultdict(int)
    for ei, s in sorted(qd.stage_scripts.get((quest["ref"], idx), []), key=lambda x: x[0]):
        entry_cond = bool(ent.get(ei, {}).get("conditions"))
        f, c = summarize(s["effects"], entry_cond)
        for k, v in f.items():
            if k in ("xp", "karma"):
                fx[k] = fx.get(k, 0) + v
            else:
                lst = fx.setdefault(k, [])
                lst += [x for x in v if x not in lst]
        for k, v in c.items():
            cond[k] += v
        cond["unmodeled_calls"] += len(s.get("unmodeled", []))
    # last write wins per (quest, objective, kind)
    if "obj" in fx:
        last = {}
        for o in fx["obj"]:
            last[(o[0], o[1], o[2])] = o
        fx["obj"] = list(last.values())
    return {"fx": fx, "cond": {k: v for k, v in cond.items() if v}}


def objective_actions(quest: dict) -> Dict[int, dict]:
    out = {}
    for o in quest.get("objectives", []):
        refs = []
        for t in o.get("targets", []):
            r = t.get("ref") if isinstance(t, dict) else t
            if r and common.full_ref(r) not in refs:
                refs.append(common.full_ref(r))
        out[int(o["index"])] = {"verb": verb_of(o.get("text", "")), "refs": refs[:MAX_ACT_REFS]}
    return out


# ------------------------------------------------------------------------------------------------------------ plan build
def lane_plan(line: Line, qd: QData) -> Dict[str, Any]:
    """The lane's own sweep config normalised (sweep_config.load_config), or a plan built from the fixed quest list."""
    import sweep_config as sc
    if line.sweep:
        return sc.load_config(os.path.join(common.REPO, "questlines", line.sweep, "sweep.json"))
    order = sc.load_loadorder()
    quests = []
    for edid in line.quests:
        q = qd.by_edid.get(edid)
        if not q:
            continue
        plug, local = sc.split_ref(q["ref"])
        hx = sc.hex8(plug, local, order)
        quests.append({"edid": edid, "ref": q["ref"], "hex": hx, "plugin": plug, "setup": [], "run_last": False,
                       "stages": [{"index": s["index"], "command": f"setstage {hx} {s['index']}", "setup": [], "variants": [], "last": False}
                                  for s in sorted(q["stages"], key=lambda s: s["index"])], "excluded": []})
    return {"slug": line.slug, "source": "out/questdata/quests.json", "reset_ids": [q["hex"] for q in quests], "warnings": [], "quests": quests}


_SETUP = [(re.compile(r"^\s*startquest\s+([0-9a-fA-F]+)\s*$", re.I), "start"),
          (re.compile(r"^\s*setstage\s+([0-9a-fA-F]+)\s+(\d+)\s*$", re.I), "stage")]


def prereqs(lines: List[str], own_hex: str) -> List[dict]:
    out = []
    for ln in lines:
        for rx, op in _SETUP:
            m = rx.match(ln)
            if m:
                rid = f"{int(m.group(1), 16):08X}"
                if rid == own_hex:
                    break
                out.append({"op": op, "q": rid} if op == "start" else {"op": "stage", "q": rid, "n": int(m.group(2))})
                break
        else:
            out.append({"op": "raw", "line": ln})
    seen, uniq = set(), []
    for p in out:
        k = tuple(sorted(p.items()))
        if k not in seen:
            seen.add(k)
            uniq.append(p)
    return uniq


def build_quest(lq: dict, qd: QData, rules: dict) -> Dict[str, Any]:
    q = qd.by_ref.get(lq["ref"]) or qd.by_edid.get(lq["edid"])
    rid = lq["hex"]
    acts = objective_actions(q) if q else {}
    infos = qd.info_rows() if q else {}
    rv = rulesview.view(rules, lq["edid"])
    stage_rows: List[Dict[str, Any]] = []
    listed = {s["index"] for s in lq["stages"]} | {e["index"] for e in lq["excluded"]}
    plan_stages = [(s["index"], None, s) for s in lq["stages"]] + [(e["index"], e["reason"], None) for e in lq["excluded"]]
    if q and not listed:        # the lane lists the quest without stages: take them from questdata
        plan_stages = [(s["index"], None, None) for s in sorted(q["stages"], key=lambda s: s["index"])]
    shown_prev: List[int] = []
    for idx, excl, ls in sorted(plan_stages, key=lambda t: (t[2] is not None and t[2].get("last", False), t[0])):
        row: Dict[str, Any] = {"index": idx}
        if q:
            row.update(stage_block(qd, q, idx))
        else:
            row.update({"fx": {}, "cond": {}})
        if excl:
            row["excluded"] = excl
        if ls and ls.get("variants"):
            row["variants"] = [v.get("name") for v in ls["variants"]]
        fx = row["fx"]
        row["objective_effects"] = [{"objective": o[1], "kind": o[2], "value": o[3]} for o in fx.get("obj", []) if o[0] == rid]
        if q:
            trig = None
            cands = [c for c in qd.stage_infos.get((q["ref"], idx), []) if c[0] not in qd.ambiguous]
            for iref, on in sorted(cands, key=lambda c: (c[1] != "end", c[0])):
                meta = infos.get(iref, {})
                trig = {"info": iref, "on": on, "quest": common.runtime_id(meta["quest"]) if meta.get("quest") else None,
                        "speaker_base": meta.get("speaker_base"), "alternatives": len(cands) - 1}
                break
            if trig:
                guess = None
                for o in reversed(shown_prev):
                    a = acts.get(o)
                    if a and a["verb"] == "talk" and len(a["refs"]) == 1:
                        guess = a["refs"][0]
                        break
                if guess:
                    trig["speaker_ref"] = guess
                row["trigger"] = trig
            todo = []
            for o in fx.get("obj", []):
                if o[0] == rid and o[2] == "displayed" and o[3] == 1 and o[1] in acts and acts[o[1]]["refs"]:
                    todo.append({"o": o[1], **acts[o[1]]})
            if todo:
                row["act"] = todo
            shown_prev = [o[1] for o in fx.get("obj", []) if o[0] == rid and o[2] == "displayed" and o[3] == 1] or shown_prev
        stage_rows.append(row)
    pre = prereqs(list(lq.get("setup", [])), rid)
    return {"edid": lq["edid"], "ref": lq["ref"], "id": rid, "name": (q or {}).get("name", lq["edid"]), "plugin": lq["plugin"],
            "start": True, "prereq": pre, "rules": rv, "run_last": lq["run_last"],
            "objectives": {str(k): v for k, v in acts.items() if v["refs"]},
            "stages": stage_rows}


def ready_stages(qd: QData, rules: dict, quests: List[dict]) -> List[dict]:
    """ReadyCheck rules (on ANY quest: the rule sits on the quest that owns the INFO) -> the (quest, stage) pairs the listed INFOs set,
    for the quests of this line."""
    mine = {q["id"] for q in quests}
    out = []
    for rule_quest, entry in rules.items():
        for r in entry.get("rules", []):
            if r["kind"] != "ReadyCheck":
                continue
            for info in r.get("params", {}).get("infos", []):
                ref = common.full_ref(info)
                for (qref, st), lst in qd.stage_infos.items():
                    if any(i == ref for i, _ in lst) and common.runtime_id(qref) in mine:
                        out.append({"quest": common.runtime_id(qref), "stage": st, "info": ref, "rule_quest": rule_quest})
    return sorted(out, key=lambda x: (x["quest"], x["stage"]))


def build_companions(plan: Dict[str, Any], qd: QData) -> Optional[Dict[str, Any]]:
    p = os.path.join(common.REPO, "questlines", "systems", "companions.json")
    if not os.path.exists(p):
        return None
    c = common.load_json(p)
    comps = []
    for name, d in c["companions"].items():
        refs = d.get("refs") or {}
        ref = next(iter(refs.values()), None)
        ev = d.get("evidence", {})
        rec = [e["info"] for e in ev.get("recruit", [])][:1]
        dis = [e["info"] for e in ev.get("dismiss", [])][:1]
        comps.append({"name": name, "kind": d.get("kind", "humanoid"), "ref": common.full_ref(ref) if ref else None,
                      "recruit_info": common.full_ref("FalloutNV.esm:" + rec[0]) if rec else None,
                      "dismiss_info": common.full_ref("FalloutNV.esm:" + dis[0]) if dis else None,
                      "personal_quests": list(d.get("personal_quests") or [])})
    lim = c.get("follower_limit", {})
    return {"companions": comps, "caps": {"humanoid": 1, "critter": 1, "party_total": 4}, "cap_source": lim.get("mechanism", "")}


def build_line(line: Line, qd: QData, rules: dict) -> Dict[str, Any]:
    lp = lane_plan(line, qd)
    lqs = lp["quests"]
    if line.slug == "companions":     # the companion line: the quests are the companions' personal quests
        c = build_companions({}, qd) or {"companions": []}
        wanted = {e for comp in c["companions"] for e in comp["personal_quests"]} | {"VCassCompanion"}
        lqs = [q for q in lqs if q["edid"] in wanted]
        have = {q["edid"] for q in lqs}
        import sweep_config as sc
        order = sc.load_loadorder()
        for e in sorted(wanted - have):
            q = qd.by_edid.get(e)
            if q and q["stages"]:
                plug, local = sc.split_ref(q["ref"])
                hx = sc.hex8(plug, local, order)
                lqs.append({"edid": e, "ref": q["ref"], "hex": hx, "plugin": plug, "setup": [], "run_last": False, "excluded": [],
                            "stages": [{"index": s["index"], "command": "", "setup": [], "variants": [], "last": False}
                                       for s in sorted(q["stages"], key=lambda s: s["index"])]})
    quests = []
    for lq in lqs:
        if not (lq["stages"] or lq["excluded"] or (qd.by_edid.get(lq["edid"]) or {}).get("stages")):
            continue
        quests.append(build_quest(lq, qd, rules))
    quests = [q for q in quests if q["stages"]]
    stats = {"quests": len(quests), "stages": sum(len(q["stages"]) for q in quests),
             "excluded": sum(1 for q in quests for s in q["stages"] if s.get("excluded")),
             "with_fx": sum(1 for q in quests for s in q["stages"] if s["fx"]),
             "with_trigger": sum(1 for q in quests for s in q["stages"] if s.get("trigger")),
             "with_actions": sum(1 for q in quests for s in q["stages"] if s.get("act")),
             "conditional_effects": sum(sum(v for k, v in s["cond"].items() if not k.startswith("unmodeled")) for q in quests for s in q["stages"]),
             "guest_local": sum(1 for q in quests if q["rules"]["guest_local"]),
             "barrier": sum(1 for q in quests if q["rules"]["barrier"]),
             "exclusive": sum(len(q["rules"]["exclusive"]) for q in quests),
             "ready": 0}
    plan: Dict[str, Any] = {"schema": SCHEMA, "kind": KIND, "line": line.slug, "title": line.title, "category": line.category,
                            "milestone": line.title, "format": "m1_goodsprings.json compatible (quests[].edid/ref/stages[].index/objective_effects) plus fx/cond/trigger/act",
                            "source": {"sweep": f"questlines/{line.sweep}/sweep.json" if line.sweep else "out/questdata/quests.json",
                                       "generator": "tools/questsweep/gen_plans.py", "rules": "docs/quest-policies/rules.json"},
                            "players": {"min": 2, "max": 4}, "reset": lp["reset_ids"], "warnings": lp["warnings"], "stats": stats,
                            "ready_stages": ready_stages(qd, rules, quests), "quests": quests}
    plan["stats"]["ready"] = len(plan["ready_stages"])
    if line.slug == "companions":
        plan["companions"] = (build_companions({}, qd) or {})
    return plan


def goodsprings_sweep(plan: Dict[str, Any]) -> Dict[str, Any]:
    """questlines/goodsprings/sweep.json in the lane format sweep_config.load_config reads."""
    return {"milestone": "Goodsprings: VCG00 to VCG03, the ghost town fight", "format": "m1_goodsprings.json compatible",
            "core": {q["edid"]: q["name"] for q in plan["quests"]}, "reset_quests": [q["id"] for q in plan["quests"]],
            "quests": [{"edid": q["edid"], "ref": q["ref"], "formid": q["id"], "name": q["name"], "role": "core", "setup": [f"startquest {q['id']}"],
                        "stages": [{"index": s["index"], "console": f"setstage {q['id']} {s['index']}",
                                    "objective_effects": s["objective_effects"]} for s in q["stages"]]} for q in plan["quests"]]}


# ----------------------------------------------------------------------------------------------------------------- IO
def plan_path(slug: str) -> str:
    return os.path.join(common.REPO, "questlines", slug, "playthrough.json")


def render(plan: Dict[str, Any]) -> str:
    import json
    head = {k: v for k, v in plan.items() if k != "quests"}
    body = ",\n".join(" " + json.dumps(q, separators=(",", ":"), ensure_ascii=True) for q in plan["quests"])
    h = json.dumps(head, indent=1, ensure_ascii=True)
    return h[:-2] + ',\n "quests": [\n' + body + "\n ]\n}\n"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--line", action="append", default=[])
    ap.add_argument("--check", action="store_true", help="fail when a committed plan differs from a fresh generation")
    a = ap.parse_args(argv)
    if not common.have_questdata():
        print("SKIP: " + common.questdata_missing_reason())
        return 0
    qd = QData()
    rules = rulesview.load_rules()
    lines = [BY_SLUG[s] for s in a.line] if a.line else LINES
    bad = 0
    for line in lines:
        plan = build_line(line, qd, rules)
        text = render(plan)
        path = plan_path(line.slug)
        outs = [(path, text)]
        if line.slug == "goodsprings":
            import json
            outs.append((os.path.join(common.REPO, "questlines", "goodsprings", "sweep.json"), json.dumps(goodsprings_sweep(plan), indent=1) + "\n"))
        for p, t in outs:
            cur = open(p, encoding="utf-8").read() if os.path.exists(p) else None
            if a.check:
                if cur != t:
                    print(f"STALE {os.path.relpath(p, common.REPO)}")
                    bad += 1
            else:
                os.makedirs(os.path.dirname(p), exist_ok=True)
                with open(p, "w", encoding="utf-8", newline="\n") as f:
                    f.write(t)
        s = plan["stats"]
        print(f"{line.slug:13} {s['quests']:3} quests {s['stages']:4} stages  fx {s['with_fx']:4}  triggers {s['with_trigger']:4}  actions {s['with_actions']:4}"
              f"  excluded {s['excluded']:3}  guest-local {s['guest_local']} barrier {s['barrier']} exclusive {s['exclusive']} ready {s['ready']}")
    if a.check:
        print("ALL OK" if not bad else f"{bad} stale file(s): run python -I tools/questsweep/gen_plans.py")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
