"""Connects playbooks to T4b's scenarios (scenarios/*.json + tools/scenarios/runner.py).

`refs_for(playbook, repo)` turns a scenario's `forms` glossary into the ref names a playbook needs (MAPPERS).
`start(playbook, bridges, repo)` jumps every instance to the scenario's starting state with T4b's Runner. If the scenario
file does not exist yet (pnr_vote, dialogue_spectator, goodsprings_opening are mine to request), it falls back to
"load the save, reset the quests" and says so in the returned notes.
"""
from __future__ import annotations

import json
import os
import sys
from typing import Any, Callable, Dict, List, Optional, Tuple

from . import goodsprings_plan as GP


def _forms(repo: str, scenario: str) -> Optional[Dict[str, str]]:
    p = os.path.join(repo, "scenarios", scenario + ".json")
    if not os.path.isfile(p):
        return None
    with open(p, encoding="utf-8") as f:
        return {k: v["ref"] for k, v in json.load(f).get("forms", {}).items()}


def _questdata_infos(repo: str, edid: str, n: int) -> List[str]:
    q = GP._load(repo, "out/questdata/quests.json")["quests"]
    for e in q:
        if e["edid"] == edid:
            return list(e["infos"])[:n]
    return []


def _m_fight(f, repo):
    return {"raiders": [f["gang1"], f["gang2"], f["gang3"]]}


def _m_handoff(f, repo):
    return {"raiders": [f["gang1"], f["gang2"], f["gang3"]],
            "far_pos": {"ref": f["novac"], "dist": 400}, "near_pos": {"ref": f["gangMarker"], "dist": 300}}


def _m_loot(f, repo):
    return {"item": f["teddyRef"], "item_base": f["teddy"]}


def _m_container(f, repo):
    # item_a: stimpaks (the scenario/seed puts them in the dresser); item_b: bottle caps
    return {"container": f["dresser"], "item_a": f["stimpak"], "item_b": "FalloutNV.esm:00000F",
            "seed": {"item": f["stimpak"], "count": 6}}


def _m_doors(f, repo):
    return {"door": f["docDoor"], "locked_door": f["gate"], "terminal": f["terminal"], "terminal_entry": 0,
            "locked_box": f["dresser"], "key": f["stimpak"]}


def _m_companion(f, repo):
    return {"companion": f["boone"]}      # recruit_info is not in the scenario: the playbook falls back to the console


def _m_sync(f, repo):
    plan = GP.load_plan(repo)
    q = next(x for x in plan["quests"] if x["edid"] == "VCG02")
    stages = [(n, q["objective_effects"].get(n, [])) for kind, n in q["steps"] if kind == "stage"]
    return {"quest": q["formid"], "stages": stages, "start": True}


def _m_travel(f, repo):
    return {"marker": f["novac"], "anchor": f["novac"], "tether_radius": 3000.0,        # radius: ASSUMED, see docs
            "far_pos": {"ref": f["gangMarker"], "dist": 300}}


def _m_pnr(f, repo):
    # VMQ05 stage 70 -> 100 through the endgame confirm (host box, then Ready / Not yet for the guests)
    return {"moore": f["moore"], "ready_quest": f["vmq05"], "ready_from": 70, "ready_to": 100}


def _m_dialogue(f, repo):
    return {"npc": f["sunny"], "infos": [f["info"]], "speakers": "guest", "done_quest": f["vcg02"], "done_stage": 25}


def _m_spawn(f, repo):
    return {"marker": f["gangMarker"], "base": f["gangBase"]}


def _base_of(repo: str, ref: str) -> Optional[str]:
    """The base form of a placed reference, from out/questdata/forms.json.gz."""
    import gzip
    p = os.path.join(repo, "out", "questdata", "forms.json.gz")
    if p not in _BASES:
        with gzip.open(p, "rt", encoding="utf-8") as fh:
            _BASES[p] = {k: v.get("base") for k, v in json.load(fh).items() if v.get("base")}
    return _BASES[p].get(ref)


_BASES: Dict[str, Dict[str, str]] = {}


def _hostiles(f, repo):
    hs = [f["gang1"], f["gang2"], f["gecko1"], f["gecko2"]]
    return {"hostiles": hs, "hostile_base_of": {h: _base_of(repo, h) for h in hs}}


def _m_enemy_fight(f, repo):
    return _hostiles(f, repo)


def _m_enemy_handoff(f, repo):
    return dict(_hostiles(f, repo), far_pos={"ref": f["novac"], "dist": 400}, near_pos={"ref": f["gangMarker"], "dist": 300})


def _m_enemy_spawn(f, repo):
    return {"marker": f["gangMarker"], "base": f["gangBase"], "count": 3, "spawn_mode": "host"}


def _m_corpse(f, repo):
    return {"victim": f["gang1"], "item": f["stimpak"], "item_count": 3, "hostile_base_of": {f["gang1"]: _base_of(repo, f["gang1"])}}


def _m_vats(f, repo):
    return {"target": f["gang1"], "limb": "Head"}


def _m_saveload(f, repo):
    # stages 10 and 20 as in the scenario; it names no reward item, so the reward check is skipped with a note
    return {"quest": f["vcg02"], "stage_a": 10, "stage_b": 20}


# playbook -> (scenario file name, mapper). Scenario name None: no scenario yet (fallback start).
MAPPERS: Dict[str, Tuple[Optional[str], Optional[Callable]]] = {
    "fight": ("goodsprings_fight", _m_fight),
    "ownership_handoff": ("ownership_handoff", _m_handoff),
    "loot_race": ("loot_race", _m_loot),
    "container_share": ("door_terminal_container_matrix", _m_container),
    "door_terminal_lock": ("door_terminal_container_matrix", _m_doors),
    "companion_recruit": ("companion_recruit", _m_companion),
    "quest_step_sync": ("quest_stage_sync", _m_sync),
    "fast_travel_tether": ("fast_travel_tether", _m_travel),
    "spawn_once": ("spawn_once", _m_spawn),
    "vats_hit": ("vats_hit", _m_vats),
    "save_load_rejoin": ("save_load_rejoin", _m_saveload),
    "pnr_vote": ("pnr_vote", _m_pnr),
    "pnr_vote_guest_declines": ("pnr_vote_guest_declines", _m_pnr),
    "dialogue_spectator": ("dialogue_spectator", _m_dialogue),
    "goodsprings_opening": ("goodsprings_opening", None),
    "enemy_fight": ("goodsprings_enemies", _m_enemy_fight),
    "enemy_handoff": ("goodsprings_enemies", _m_enemy_handoff),
    "enemy_spawn": ("goodsprings_enemies", _m_enemy_spawn),
    "corpse_loot_race": ("goodsprings_enemies", _m_corpse),
}


def refs_for(playbook: str, repo: str) -> Dict[str, Any]:
    scen, mapper = MAPPERS[playbook]
    if mapper is None:
        return {}
    forms = _forms(repo, scen) if scen else {}
    if forms is None:
        raise FileNotFoundError(f"scenario {scen}.json is missing")
    return mapper(forms, repo)


def _import_scenarios(repo: str):
    d = os.path.join(repo, "tools", "scenarios")
    if d not in sys.path:
        sys.path.insert(0, d)
    import scenlib      # noqa: WPS433
    import runner       # noqa: WPS433
    return scenlib, runner


def start(playbook: str, bridges: list, repo: str, log: Callable[[str], None] = print) -> Dict[str, Any]:
    """Put every instance in the playbook's starting state. Returns {"mode": "scenario"|"fallback", "notes": [...]}."""
    scen, _ = MAPPERS[playbook]
    notes: List[str] = []
    if scen and os.path.isfile(os.path.join(repo, "scenarios", scen + ".json")):
        scenlib, runner = _import_scenarios(repo)
        sc = scenlib.load_scenario(os.path.join(repo, "scenarios", scen + ".json"))
        r = runner.Runner(bridges, log=log).jump(sc)
        bad = [s for s in r.get("setup", []) if s.get("status") in ("error", "fail")]
        if bad:
            raise RuntimeError(f"scenario {scen} setup failed: {bad[:2]}")
        return {"mode": "scenario", "scenario": scen, "jump": r, "notes": notes}
    notes.append(f"no scenario file for {playbook}: loaded the 'autosave' save and reset the quests it touches")
    for nv in bridges:
        nv.load_save("autosave")
    quests = {}.get(playbook, [])
    if quests:
        for nv in bridges:
            nv.call("test.quest.reset", {"quests": quests})
    return {"mode": "fallback", "scenario": scen, "notes": notes}
