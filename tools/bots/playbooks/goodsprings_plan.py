"""The Goodsprings opening (VCG01 -> VCG02 -> VCG03) as data, built from out/questdata.

Nothing here is typed by hand except the *actions* a bot does at some stages (ACTIONS) and the barrier stages. Every quest id,
stage number, objective flag and ref id comes from:
    out/questdata/scenarios/VCG0{1,2,3}.json   (steps in order: startquest / setstage N)
    out/questdata/quests.json                  (quest FormIDs, stage and objective lists)
    out/questdata/m1_goodsprings.json          (objective flags each stage sets: kind displayed/completed)
    out/questdata/refnames.json.gz             (editor id -> placed ref id)
`check_plan()` verifies that everything resolves; the tests run it, so a questdata change that breaks the path is caught
game-free.
"""
from __future__ import annotations

import gzip
import json
import os
from typing import Any, Dict, List, Tuple

QUESTS = ("VCG01", "VCG02", "VCG03")
BARRIER_STAGES = {"VCG01": (30,)}       # chargen: advance only when every player finished their own (rules.json barrier)

# placed refs the bots use, by editor id (resolved through refnames.json.gz) or by raw FormID where the ref has no editor id
REF_EDIDS = {
    "doc": "DocMitchellREF",
    "tester": "VCG01VigorTesterREF",
    "couch": "DocMitchellCouchREF",
    "sunny": "SunnyRef",
    "trudy": "TrudyRef",
    "gecko1": "VCG02Gecko1REF",
    "gecko2": "VCG02Gecko2REF",
    "broc": "VCG03BrocFlowerREF",
    "xander": "VCG03XanderRootREF",
}
REF_RAW = {"campfire": "FalloutNV.esm:0CDB95",
           "exit_marker": "FalloutNV.esm:105BCF"}      # these two have no editor id in refnames.json.gz; ids from m1_goodsprings.json

# What the bots do at a stage (after the host has set it): (quest, stage) -> list of action names. Interpreted by
# pb_story.run_action. Where the bots cannot yet pick the exact dialogue INFO, the stage itself is set by the host's console.
ACTIONS: Dict[Tuple[str, int], List[str]] = {
    ("VCG01", 10): ["all:walk:tester", "all:activate:tester"],
    ("VCG01", 40): ["all:activate:couch"],
    ("VCG01", 50): ["all:walk:exit_marker"],
    ("VCG02", 25): ["all:kill:gecko1", "all:kill:gecko2"],
    ("VCG02", 40): ["talk_end:sunny"],
    ("VCG02", 100): ["talk_end:sunny"],
    ("VCG03", 10): ["split_pick:broc,xander", "all:walk:sunny", "all:activate:campfire", "talk_end:trudy"],
}


def _load(root: str, rel: str):
    p = os.path.join(root, *rel.split("/"))
    if p.endswith(".gz"):
        with gzip.open(p, "rt", encoding="utf-8") as f:
            return json.load(f)
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def hex_of(ref: str) -> str:
    return ref.split(":")[-1].lstrip("0").upper() or "0"


def load_plan(repo_root: str) -> Dict[str, Any]:
    qd = "out/questdata"
    quests = _load(repo_root, f"{qd}/quests.json")["quests"]
    by_edid = {q["edid"]: q for q in quests}
    m1 = _load(repo_root, f"{qd}/m1_goodsprings.json")
    m1q = {q["edid"]: q for q in (m1["quests"] if isinstance(m1["quests"], list) else m1["quests"].values())}
    names = _load(repo_root, f"{qd}/refnames.json.gz")
    by_ref_edid: Dict[str, List[str]] = {}
    for ref, v in names.items():
        if v.get("edid"):
            by_ref_edid.setdefault(v["edid"], []).append(ref)
    refs: Dict[str, str] = dict(REF_RAW)
    for k, edid in REF_EDIDS.items():
        c = by_ref_edid.get(edid, [])
        if len(c) != 1:
            raise KeyError(f"editor id {edid} resolves to {len(c)} refs: {c}")
        refs[k] = c[0]
    plan: Dict[str, Any] = {"quests": [], "refs": refs, "barriers": {}, "actions": ACTIONS}
    for edid in QUESTS:
        sc = _load(repo_root, f"{qd}/scenarios/{edid}.json")
        q = by_edid[edid]
        steps = []
        for s in sc["steps"]:
            if s["id"] == "start":
                steps.append(("start", None))
            elif s["id"].startswith("stage_"):
                steps.append(("stage", int(s["id"].split("_")[1])))
            else:
                raise ValueError(f"unexpected scenario step id {s['id']}")
        eff: Dict[int, List[Tuple[int, str, int]]] = {}
        for st in m1q[edid]["stages"]:
            for oe in st.get("objective_effects", []):
                eff.setdefault(st["index"], []).append((oe["objective"], oe["kind"], oe["value"]))
        plan["quests"].append({"edid": edid, "formid": hex_of(q["formid"]), "name": q["name"], "steps": steps,
                               "objective_effects": eff, "stages_in_esm": [s["index"] for s in q["stages"]],
                               "objectives_in_esm": [o["index"] for o in q["objectives"]]})
        if edid in BARRIER_STAGES:
            plan["barriers"][hex_of(q["formid"])] = list(BARRIER_STAGES[edid])
    return plan


def check_plan(plan: Dict[str, Any], repo_root: str) -> List[str]:
    """Returns a list of problems; empty means every id resolves."""
    problems: List[str] = []
    names = _load(repo_root, "out/questdata/refnames.json.gz")
    for q in plan["quests"]:
        for kind, n in q["steps"]:
            if kind == "stage" and n not in q["stages_in_esm"]:
                problems.append(f"{q['edid']}: stage {n} is not a stage of the quest")
        for st, effs in q["objective_effects"].items():
            if st not in q["stages_in_esm"]:
                problems.append(f"{q['edid']}: objective effect on missing stage {st}")
            for obj, kind, val in effs:
                if obj not in q["objectives_in_esm"]:
                    problems.append(f"{q['edid']}: stage {st} sets objective {obj} which is not an objective")
        for bs in plan["barriers"].get(q["formid"], []):
            if bs not in q["stages_in_esm"]:
                problems.append(f"{q['edid']}: barrier stage {bs} is not a stage")
    for k, ref in plan["refs"].items():
        if ref not in names and k not in REF_RAW:
            problems.append(f"ref {k}={ref} not in refnames")
    qedids = {q["edid"] for q in plan["quests"]}
    for (qe, st), acts in plan["actions"].items():
        if qe not in qedids:
            problems.append(f"action for unknown quest {qe}")
            continue
        q = next(x for x in plan["quests"] if x["edid"] == qe)
        if st not in q["stages_in_esm"]:
            problems.append(f"action at {qe} stage {st}: not a stage")
        for a in acts:
            for tok in a.split(":")[-1].split(","):
                if tok not in plan["refs"]:
                    problems.append(f"action {a!r}: unknown ref name {tok!r}")
    return problems
