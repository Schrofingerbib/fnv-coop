#!/usr/bin/env python3
"""Generate the synthetic snapshot goldens (deterministic). Output is committed and sha256-pinned in
MANIFEST.sha256; test_snapshot_tools.py fails if a golden changes without the manifest being re-blessed
(`make_goldens.py --bless`), so agents cannot silently loosen the fixtures.
"""
import copy
import hashlib
import json
import os
import random
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ESM = "FalloutNV.esm"


def ref(n, plugin=ESM):
    return "%s:%06X" % (plugin, n)


def base():
    quests = {}
    for i in range(60):
        edid = "TestQuest%02d" % i
        stages = [0, 10, 20, 30] if i % 3 else [0, 10, 20, 30, 40, 50]
        cur = 10 * (i % 4)
        quests[ref(0x104C00 + i)] = {
            "edid": edid, "name": "Test Quest %d" % i, "rawFlags": 1 if cur else 0,
            "running": bool(cur), "completed": False, "failed": False, "currentStage": cur,
            "completedStages": [s for s in stages if s < cur and s],
            "objectives": {"10": {"displayed": bool(cur), "completed": cur > 10}},
            "vars": {"iCounter": float(i % 5), "bDone": 0},
        }
    # the real quest the mutation twins poke at
    quests[ref(0x104C2A)] = {
        "edid": "VMS01", "name": "Ain't That a Kick in the Head", "rawFlags": 1, "running": True,
        "completed": False, "failed": False, "currentStage": 10, "completedStages": [0],
        "objectives": {"10": {"displayed": True, "completed": False}, "20": {"displayed": False, "completed": False}},
        "vars": {"iDocState": 1},
    }
    globs = {}
    for i in range(20):
        globs[ref(0x0A0000 + i)] = {"edid": "TestGlobal%02d" % i, "type": "f" if i % 2 else "s", "value": float(i)}
    globs[ref(0x000038)] = {"edid": "GameHour", "type": "f", "value": 9.5}
    globs[ref(0x000039)] = {"edid": "GameDaysPassed", "type": "f", "value": 1.25}
    return {
        "schemaVersion": 1,
        "meta": {
            "gameVersion": "1.4.0.525", "plugins": [ESM, "HonestHearts.esm"], "pluginsHash": "0123456789abcdef",
            "timestamp": "2026-10-15T12:00:00Z", "instance": "host",
            "counts": {"quests": len(quests), "globals": len(globs), "reputations": 2},
        },
        "shared": {
            "quests": quests, "globals": globs,
            "reputations": {
                ref(0x0F7F85): {"edid": "NCR", "fame": 12.0, "infamy": 0.0},
                ref(0x0F7F86): {"edid": "Legion", "fame": 0.0, "infamy": 3.5},
            },
            "karma": 25.0,
            "factionRanks": {ref(0x0E5B3C): {"edid": "GoodspringsFaction", "rank": 1}},
            "refs": {
                ref(0x104C8F): {"exists": True, "edid": "DocMitchell", "alive": True, "enabled": True, "cell": ref(0x0DA3F6)},
                ref(0x104C90): {"exists": True, "edid": "Sunny", "alive": True, "enabled": True, "cell": ref(0x0DA3F6)},
            },
        },
        "perPlayer": {
            "host": {
                "actorValues": {"Karma": {"base": 25.0, "current": 25.0}, "Speech": {"base": 30.0, "current": 30.0}},
                "inventory": {ref(0x0DE1AA): {"edid": "QuestItemA", "count": 1}},
                "caps": 120, "perks": {ref(0x0C0001): 1},
                "position": {"x": 1000.5, "y": -200.25, "z": 64.0, "rotZ": 1.5, "cell": ref(0x0DA3F6)},
            }
        },
    }


def shuffled(obj, rng):
    """Same data, randomised dict key order and set-array order."""
    if isinstance(obj, dict):
        items = list(obj.items())
        rng.shuffle(items)
        return {k: shuffled(v, rng) for k, v in items}
    if isinstance(obj, list):
        out = [shuffled(v, rng) for v in obj]
        if all(isinstance(v, int) for v in out):  # completedStages is a set
            rng.shuffle(out)
        return out
    return obj


def variants():
    b = base()
    v = {"base": b}

    v["reordered"] = shuffled(b, random.Random(42))

    m = copy.deepcopy(b)
    m["shared"]["quests"][ref(0x104C2A)]["currentStage"] = 20
    v["stage_changed"] = m

    m = copy.deepcopy(b)
    m["shared"]["quests"][ref(0x104C2A)]["completedStages"] = [0, 10]
    v["completed_stage_added"] = m

    m = copy.deepcopy(b)
    m["shared"]["quests"][ref(0x104C2A)]["objectives"]["20"]["displayed"] = True
    v["objective_changed"] = m

    m = copy.deepcopy(b)
    m["shared"]["refs"][ref(0x104C8F)]["alive"] = False
    v["doc_dead"] = m

    m = copy.deepcopy(b)  # float noise within tolerance, plus meta/timestamp/instance churn, plus clock drift
    m["shared"]["globals"][ref(0x0A0001)]["value"] += 0.0004
    m["shared"]["reputations"][ref(0x0F7F85)]["fame"] += 0.0005
    m["shared"]["globals"][ref(0x000038)]["value"] = 17.25
    m["shared"]["globals"][ref(0x000039)]["value"] = 99.0
    m["meta"]["timestamp"] = "2030-01-01T00:00:00Z"
    m["meta"]["instance"] = "client1"
    v["noise_ok"] = m

    m = copy.deepcopy(b)  # float change beyond tolerance
    m["shared"]["globals"][ref(0x0A0001)]["value"] += 0.5
    v["float_changed"] = m

    m = copy.deepcopy(b)
    m["perPlayer"]["host"]["caps"] = 5
    m["perPlayer"]["host"]["position"]["x"] = 5000.0
    v["perplayer_changed"] = m

    m = copy.deepcopy(b)  # karma not shared: client's player karma differs from shared karma
    m["perPlayer"]["host"]["actorValues"]["Karma"]["current"] = -50.0
    v["karma_unshared"] = m

    # --- vacuous / broken dumps: the guard must reject every one of these
    v["empty"] = {}
    v["empty_shell"] = {"schemaVersion": 1, "meta": {"gameVersion": "1.4.0.525", "counts": {"quests": 0, "globals": 0}},
                        "shared": {"quests": {}, "globals": {}, "reputations": {}, "karma": 0.0, "factionRanks": {}, "refs": {}}}
    m = copy.deepcopy(b)
    keep = sorted(m["shared"]["quests"])[:3]
    m["shared"]["quests"] = {k: m["shared"]["quests"][k] for k in keep}
    m["meta"]["counts"]["quests"] = 3
    v["near_empty"] = m
    m = copy.deepcopy(b)
    for k in sorted(m["shared"]["quests"])[:20]:  # silently truncated: counts disagree with content
        del m["shared"]["quests"][k]
    v["truncated"] = m
    m = {"schemaVersion": 1, "meta": {"gameVersion": "1.4.0.525", "error": "access violation while reading engine state",
                                       "counts": {"quests": 0, "globals": 0}}}
    v["errored"] = m
    m = copy.deepcopy(b)
    m["shared"]["quests"][ref(0x104C2A)]["currentStage"] = "ten"  # schema violation
    v["bad_schema"] = m
    return v


def write(name, obj, pretty):
    """Write a golden only if its bytes would change, via temp file + atomic replace: concurrent test runs (several
    lanes run run_tests.py at once) must never observe a truncated or half-written fixture."""
    path = os.path.join(HERE, name + ".json")
    if pretty:
        text = json.dumps(obj, indent=1, sort_keys=True)
    else:
        text = json.dumps(obj, separators=(",", ":"))   # NOT sorted: reordered stays reordered
    data = (text + "\n").encode("utf-8")
    try:
        with open(path, "rb") as f:
            if f.read() == data:
                return path
    except OSError:
        pass
    tmp = "%s.%d.tmp" % (path, os.getpid())
    with open(tmp, "wb") as f:
        f.write(data)
    os.replace(tmp, path)
    return path


def sha(path):
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def main():
    bless = "--bless" in sys.argv
    vs = variants()
    lines = []
    for name in sorted(vs):
        p = write(name, vs[name], pretty=(name == "base"))
        lines.append("%s  %s.json" % (sha(p), name))
    manifest = os.path.join(HERE, "MANIFEST.sha256")
    if bless or not os.path.exists(manifest):
        with open(manifest, "w", newline="\n") as f:
            f.write("\n".join(lines) + "\n")
        print("manifest written (%d files)" % len(lines))
    else:
        with open(manifest) as f:
            old = f.read().split("\n")
        if [l for l in old if l] != lines:
            print("GOLDENS CHANGED vs MANIFEST.sha256 - re-run with --bless only if intentional")
            return 1
        print("goldens match manifest")
    return 0


if __name__ == "__main__":
    sys.exit(main())
