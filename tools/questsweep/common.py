"""Shared helpers for the questline sweeps: repo paths, data loaders, form id conversions.

Everything here is game-free. The generated game data (out/questdata) is optional: `have_questdata()` says whether it is
there, and the generators / data-driven tests skip with a reason when it is not. The committed plans
(questlines/<line>/playthrough.json) need no game data at all.
"""
from __future__ import annotations

import gzip
import json
import os
import re
from typing import Any, Dict, List, Optional

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
QD = os.path.join(REPO, "out", "questdata")
RULES_JSON = os.path.join(REPO, "docs", "quest-policies", "rules.json")
RULES_PENDING = os.path.join(REPO, "docs", "quest-policies", "rules.pending.json")
GUEST_LOCAL_JSON = os.path.join(REPO, "plugin", "hooks", "data", "guest_local_run.json")
PLAYER_REF = "FalloutNV.esm:000014"
NEEDED_FILES = ("quests.json", "effects.json.gz", "infos.jsonl.gz", "refnames.json.gz")


def have_questdata() -> bool:
    return all(os.path.exists(os.path.join(QD, f)) for f in NEEDED_FILES)


def questdata_missing_reason() -> str:
    miss = [f for f in NEEDED_FILES if not os.path.exists(os.path.join(QD, f))]
    return ("needs out/questdata (python -I tools/questdata/run_all.py --data \"<your game>\\Data\"); missing: " + ", ".join(miss)) if miss else ""


def load_json(path: str) -> Any:
    opener = gzip.open if path.endswith(".gz") else open
    with opener(path, "rt", encoding="utf-8") as f:
        return json.load(f)


def load_jsonl_gz(path: str):
    with gzip.open(path, "rt", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                yield json.loads(line)


def dump_json(path: str, data: Any, indent: Optional[int] = None) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        json.dump(data, f, indent=indent, sort_keys=False, ensure_ascii=True, separators=(",", ":") if indent is None else None)
        f.write("\n")


_LOADORDER: Optional[List[str]] = None


def load_order() -> List[str]:
    global _LOADORDER
    if _LOADORDER is None:
        p = os.path.join(REPO, "tools", "questdata", "loadorder.json")
        try:
            _LOADORDER = list(load_json(p)["plugins"])
        except OSError:
            _LOADORDER = ["FalloutNV.esm", "DeadMoney.esm", "HonestHearts.esm", "OldWorldBlues.esm", "LonesomeRoad.esm",
                          "TribalPack.esm", "MercenaryPack.esm", "ClassicPack.esm", "CaravanPack.esm", "GunRunnersArsenal.esm"]
    return _LOADORDER


def split_ref(ref: str):
    """'DeadMoney.esm:005229' -> ('DeadMoney.esm', 0x5229); a bare hex id belongs to FalloutNV.esm."""
    if ":" in ref:
        plug, _, h = ref.rpartition(":")
    else:
        plug, h = "FalloutNV.esm", ref
    return plug, int(re.sub(r"^0x", "", h), 16) & 0xFFFFFF


def runtime_id(ref: str) -> str:
    """Runtime FormID (load-order index in the top byte), 8 hex digits: 'DeadMoney.esm:005229' -> '01005229'.
    This is the id the in-game console takes, and it keeps DLC quests from colliding with base-game ones."""
    plug, local = split_ref(ref)
    order = load_order()
    idx = order.index(plug) if plug in order else 0
    return f"{(idx << 24) | local:08X}"


def full_ref(ref: str) -> str:
    plug, local = split_ref(ref)
    return f"{plug}:{local:06X}"


def hexkey(runtime: str) -> str:
    """The key the fake world and the probes use for a quest: hex without leading zeros (probe.hexid)."""
    return re.sub(r"^0x", "", runtime.split(":")[-1]).upper().lstrip("0") or "0"


def slug(s: str) -> str:
    return re.sub(r"[^A-Za-z0-9_]+", "_", s).strip("_")
