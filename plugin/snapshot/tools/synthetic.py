"""Helper for other lanes' tests: build VALID synthetic snapshots (they pass snaplib.guard with the default
thresholds and the JSON Schema), then apply mutations without breaking the self-consistency rules
(meta.counts must equal the dumped content).

    import sys; sys.path.insert(0, "plugin/snapshot/tools")
    from synthetic import base_snapshot, make_synthetic

    before = base_snapshot()                                     # 60 quests, 22 globals, player "host"
    after = make_synthetic(before, {
        "shared/quests/VMS01/currentStage": 20,                  # EDID or FormRef allowed for quest/ref/global keys
        "shared/quests/VMS01/completedStages": [0, 10, 20],
        "shared/quests/VMS01/objectives/20": {"displayed": True, "completed": False},
        "shared/globals/TestGlobal01/value": 5.0,
        "perPlayer/host/caps": 150,
    })
    after = make_synthetic(after, {"shared/quests/NewQuest": Add(quest_entry("NewQuest", 0x200000))})
    gone  = make_synthetic(before, {"shared/globals/TestGlobal02": DELETE})

Path = "/"-separated keys into the snapshot. A segment that is not a key at its level is looked up by `edid`
among that level's children (so "VMS01" finds "FalloutNV.esm:104C2A"). Value DELETE removes the key; Add(x)
inserts a new key (last segment is the key itself, e.g. a new FormRef). meta.counts is recomputed.
"""
import copy
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

ESM = "FalloutNV.esm"
VMS01 = "FalloutNV.esm:104C2A"
DELETE = object()


class Add:
    def __init__(self, value):
        self.value = value


def ref(n, plugin=ESM):
    return "%s:%06X" % (plugin, n)


def quest_entry(edid, stage=0, running=None, objectives=None, vars=None, completed_stages=None):
    running = bool(stage) if running is None else running
    return {"edid": edid, "name": edid, "rawFlags": 1 if running else 0, "running": running, "completed": False,
            "failed": False, "currentStage": stage,
            "completedStages": list(completed_stages if completed_stages is not None else ([stage] if stage else [])),
            "objectives": dict(objectives or {}), "vars": dict(vars or {})}


def player_entry(karma=25.0, caps=100):
    return {"actorValues": {"Karma": {"base": karma, "current": karma}, "Speech": {"base": 30.0, "current": 30.0},
                            "XP": {"base": 0.0, "current": 0.0}},
            "inventory": {}, "caps": caps, "perks": {},
            "position": {"x": 0.0, "y": 0.0, "z": 0.0, "rotZ": 0.0, "cell": ref(0x0DA3F6)}}


def base_snapshot(n_quests=60, n_globals=20, players=("host",)):
    """A valid snapshot. Quest VMS01 (stage 10, objectives 10/20), globals TestGlobalNN + GameHour, 2 reputations,
    karma 25, refs DocMitchell/Sunny, one entry per player id in perPlayer. n_quests/n_globals are totals."""
    quests = {ref(0x300000 + i): quest_entry("TestQuest%02d" % i, 10 * (i % 4)) for i in range(max(n_quests - 1, 0))}
    quests[VMS01] = quest_entry("VMS01", 10, objectives={"10": {"displayed": True, "completed": False},
                                                          "20": {"displayed": False, "completed": False}},
                                vars={"iDocState": 1}, completed_stages=[0])
    globs = {ref(0x0A0000 + i): {"edid": "TestGlobal%02d" % i, "type": "f", "value": float(i)}
             for i in range(max(n_globals - 1, 0))}
    globs[ref(0x000038)] = {"edid": "GameHour", "type": "f", "value": 9.5}
    snap = {
        "schemaVersion": 1,
        "meta": {"gameVersion": "1.4.0.525", "plugins": [ESM], "pluginsHash": "0123456789abcdef",
                 "instance": "synthetic", "counts": {}},
        "shared": {
            "quests": quests, "globals": globs,
            "reputations": {ref(0x0F7F85): {"edid": "NCR", "fame": 0.0, "infamy": 0.0},
                            ref(0x0F7F86): {"edid": "Legion", "fame": 0.0, "infamy": 0.0}},
            "karma": 25.0, "factionRanks": {},
            "refs": {ref(0x104C8F): {"exists": True, "edid": "DocMitchell", "alive": True, "enabled": True,
                                     "cell": ref(0x0DA3F6)},
                     ref(0x104C90): {"exists": True, "edid": "Sunny", "alive": True, "enabled": True,
                                     "cell": ref(0x0DA3F6)}},
        },
        "perPlayer": {p: player_entry() for p in players},
    }
    return _recount(snap)


def _recount(snap):
    sh = snap.get("shared") or {}
    snap.setdefault("meta", {})["counts"] = {"quests": len(sh.get("quests", {})), "globals": len(sh.get("globals", {})),
                                             "reputations": len(sh.get("reputations", {}))}
    return snap


def _resolve(node, seg):
    if isinstance(node, dict):
        if seg in node:
            return seg
        for k, v in node.items():
            if isinstance(v, dict) and v.get("edid") == seg:
                return k
    return seg


def make_synthetic(base=None, mutations=None):
    """Deep-copy `base` (default base_snapshot()), apply {path: value} mutations, recompute meta.counts."""
    snap = copy.deepcopy(base if base is not None else base_snapshot())
    for path, value in (mutations or {}).items():
        parts = path.strip("/").split("/")
        node = snap
        for seg in parts[:-1]:
            node = node[_resolve(node, seg)]
        last = parts[-1]
        if isinstance(value, Add):
            node[last] = copy.deepcopy(value.value)
            continue
        key = _resolve(node, last)
        if value is DELETE:
            del node[key]
        else:
            node[key] = copy.deepcopy(value)
    return _recount(snap)
