"""Pick an NPC that is really loaded, for the in-game proofs that need "an ordinary NPC next to the player".

The proofs used to hard-code the Goodsprings powder ganger (FalloutNV.esm:104C70). A copy that was just booted and jumped to a scenario does not
always have him loaded with 3D (sweep 2026-10-22 .. 23: actor_port "is not loaded (has3D false, cell 000E1AA6)", npc_caps "the NPC cannot be read",
actor_port_two_copy "coop.owner does not know the NPC"), and nothing else was tried. Now the ref from the manifest is only a HINT:

    pick = npc_pick.find(read, list_cands, hint_cand, player_pos=...)       # waits, polls, falls back to the nearest usable character
    pick.ref, pick.rid, pick.read, pick.fallback, pick.note

  * the hint wins when it is usable (loaded with 3D, alive, enabled, not restrained, not the player);
  * otherwise the characters of the player's cell (bridge world.actors) are tried nearest first (unknown distance last, essential NPCs after the rest);
  * `accept(ref, read)` lets a proof add its own condition (the two-copy proof: coop.owner knows the NPC on both copies and the host owns it);
  * when nothing qualifies in `wait_s` the error says WHY, per reason, so a NOT READY stays honest: "FalloutNV.esm:104C70 is not usable (no 3D);
    world.actors lists 4 characters, none usable (dead 1, no 3D 3)".

runtime_id() turns "Plugin.esm:HEX" into the runtime form id (8 hex digits) that anim.npc.do and anim.probe need: they parse the WHOLE string as hex
(strtoul), so "FalloutNV.esm:104C70" was read as 0xFA, an actor that does not exist (npc_caps could never read its NPC, whatever was loaded).

Pure logic (why_unusable, choose, explain, order, runtime_id) is tested without a game in tools/runner/tests/test_npc_pick.py, with mutation twins
(NPC_PICK_MUTANT=<name>).
"""
from __future__ import annotations

import math
import os
import time
from typing import Any, Callable, Dict, List, Optional, Tuple

# test-only: one deliberate bug (tools/runner/tests/test_npc_pick.py)
MUTANT = os.environ.get("NPC_PICK_MUTANT", "")

WAIT_S = 45.0           # how long a fresh jump may take until the NPCs of the cell are loaded with 3D
POLL_S = 1.0
MAX_CANDS = 40          # world.actors characters looked at per round
PLAYER_LOCAL = 0x14


class NotFound(Exception):
    """No usable NPC; the message says why per reason."""


class Pick:
    def __init__(self, ref: str, rid: Optional[int], read: dict, fallback: bool, note: str) -> None:
        self.ref, self.rid, self.read, self.fallback, self.note = ref, rid, read, fallback, note

    @property
    def runtime(self) -> Optional[str]:
        return None if self.rid is None else "%08X" % self.rid

    def __repr__(self) -> str:
        return f"Pick({self.ref}, {self.runtime}, fallback={self.fallback})"


# ---- pure logic ------------------------------------------------------------------------------------------------------------------------------
def key(ref: str) -> Tuple[str, int]:
    """'FalloutNV.esm:104C70' / 'falloutnv.esm:00104c70' -> ('falloutnv.esm', 0x104C70); a bare hex id -> ('', id)."""
    s = str(ref).strip()
    name, _, tail = s.rpartition(":")
    try:
        return name.lower(), int(tail, 16)
    except ValueError:
        return s.lower(), -1


def is_player(cand: dict) -> bool:
    if MUTANT == "player_allowed":
        return False
    k = key(cand.get("ref", ""))
    return cand.get("id") == PLAYER_LOCAL or k[1] == PLAYER_LOCAL or cand.get("player") is True


def why_unusable(cand: dict, rd: Any, allow_restrained: bool = False, require_3d: bool = True) -> Optional[str]:
    """None when the NPC is fit for a proof, else one short reason (the key of the summary)."""
    if is_player(cand):
        return "the player"
    if not isinstance(rd, dict):
        return "unreadable"
    if rd.get("ok") is False or rd.get("valid") is False:
        return "not a loaded actor"
    if rd.get("dead") and MUTANT != "dead_allowed":
        return "dead"
    if rd.get("disabled"):
        return "disabled"
    if rd.get("creature"):
        return "creature"
    if rd.get("restrained") and not allow_restrained:
        return "restrained"
    if require_3d and not rd.get("has3D") and MUTANT != "no_3d_check":
        return "no 3D"
    return None


def distance(rd: Any, origin: Optional[Tuple[float, float, float]]) -> float:
    if origin is None or not isinstance(rd, dict):
        return math.inf
    try:
        return math.sqrt(sum((float(rd[a]) - o) ** 2 for a, o in zip("xyz", origin)))
    except (KeyError, TypeError, ValueError):
        return math.inf


def order(cands: List[dict], reads: Dict[str, Any], origin: Optional[Tuple[float, float, float]]) -> List[dict]:
    """Nearest first; an NPC whose base is essential or whose distance is unknown goes behind the others; ties keep the listing order."""
    if MUTANT == "listing_order":
        return list(cands)

    def k(c: dict) -> Tuple[int, float]:
        rd = reads.get(c["ref"])
        return (1 if isinstance(rd, dict) and rd.get("baseEssential") else 0, distance(rd, origin))
    return sorted(cands, key=k)


def choose(hint: Optional[dict], cands: List[dict], reads: Dict[str, Any], origin: Optional[Tuple[float, float, float]] = None,
           accept: Optional[Callable[[str, dict], Optional[str]]] = None, allow_fallback: bool = True, allow_restrained: bool = False,
           require_3d: bool = True) -> Tuple[Optional[Pick], Dict[str, str]]:
    """The NPC to use from what was read this round, and why each other one was passed over ({ref: reason}).

    `reads[ref]` is the NPC's read (a dict) or None when it could not be read. `hint` and the `cands` are dicts {ref, id}."""
    if MUTANT == "no_fallback":
        allow_fallback = False
    skipped: Dict[str, str] = {}
    seen = set()
    hint_why: Optional[str] = None
    if hint:
        seen.add(key(hint["ref"]))
        hint_why = why_unusable(hint, reads.get(hint["ref"]), allow_restrained, require_3d)
        if hint_why is None and accept is not None:
            hint_why = accept(hint["ref"], reads[hint["ref"]])
        if hint_why is None:
            return Pick(hint["ref"], hint.get("id"), reads[hint["ref"]], False, f"using {hint['ref']}"), skipped
        skipped[hint["ref"]] = hint_why
        if not allow_fallback:
            return None, skipped
    rest = []
    for c in cands:
        if key(c["ref"]) in seen:
            continue
        seen.add(key(c["ref"]))
        rest.append(c)
    usable: List[dict] = []
    for c in rest:
        why = why_unusable(c, reads.get(c["ref"]), allow_restrained, require_3d)
        if why:
            skipped[c["ref"]] = why
        else:
            usable.append(c)
    for c in order(usable, reads, origin):
        why = accept(c["ref"], reads[c["ref"]]) if accept is not None else None
        if why:
            skipped[c["ref"]] = why
            continue
        note = (f"{hint['ref']} is not usable ({hint_why}); using {c['ref']}" if hint else f"using {c['ref']}")
        return Pick(c["ref"], c.get("id"), reads[c["ref"]], bool(hint), note), skipped
    return None, skipped


def explain(hint: Optional[dict], skipped: Dict[str, str], listed: int, hint_detail: str = "", fallback: bool = True) -> str:
    """The NOT READY text: what was asked for, what the cell offered, and why none of it qualified."""
    parts = []
    if hint and hint["ref"] in skipped:
        parts.append(f"{hint['ref']} is not usable ({skipped[hint['ref']]}{', ' + hint_detail if hint_detail else ''})")
    others = {r: w for r, w in skipped.items() if not hint or r != hint["ref"]}
    if not fallback:
        parts.append(f"world.actors lists {listed} character(s), the others were not tried (no fallback)")
    elif listed == 0:
        parts.append("world.actors lists no character in the player's cell (is a save loaded and the player standing in a cell?)")
    elif not others:
        parts.append(f"world.actors lists {listed} character(s), none other than the requested one")
    else:
        count: Dict[str, int] = {}
        for w in others.values():
            count[w] = count.get(w, 0) + 1
        why = ", ".join(f"{w} {n}" for w, n in sorted(count.items(), key=lambda kv: (-kv[1], kv[0])))
        parts.append(f"world.actors lists {listed} character(s), none usable ({why})")
    return "; ".join(parts)


def runtime_id(spec: str, plugins: Optional[List[dict]] = None) -> int:
    """'Plugin.esm:HEX' -> the runtime form id (load-order index << 24 | HEX); a bare hex id / '0x...' is already one.

    `plugins` is the bridge's `plugins` list ([{name, index}]). Raises ValueError for a plugin that is not loaded."""
    s = str(spec).strip()
    if MUTANT == "load_order_dropped":       # a plugin other than the first one resolves to an id of the first
        return int(s.rpartition(":")[2], 16)
    if ":" not in s:
        return int(s, 16)
    name, _, local = s.rpartition(":")
    for p in plugins or []:
        if str(p.get("name", "")).lower() == name.lower():
            return (int(p["index"]) << 24) | (int(local, 16) & 0xFFFFFF)
    raise ValueError(f"plugin {name} is not loaded")


# ---- the wait loop (bridge calls come in as callables, so the clock and the game are fakeable) -----------------------------------------
def find(read: Callable[[dict], Any], list_cands: Callable[[], List[dict]], hint: Optional[dict] = None,
         player_pos: Optional[Callable[[], Optional[Tuple[float, float, float]]]] = None,
         accept: Optional[Callable[[str, dict], Optional[str]]] = None, wait_s: float = WAIT_S, poll_s: float = POLL_S,
         allow_fallback: bool = True, allow_restrained: bool = False, require_3d: bool = True,
         sleep: Callable[[float], None] = time.sleep, now: Callable[[], float] = time.monotonic) -> Pick:
    """Poll until an NPC qualifies (see choose). `read(cand)` -> that NPC's read dict (raise or return None when it is not loaded);
    `list_cands()` -> [{ref, id}] of the player's cell. Raises NotFound(explain(...)) after wait_s."""
    end = now() + (0.0 if MUTANT == "no_wait" else wait_s)
    skipped: Dict[str, str] = {}
    listed = 0
    while True:
        try:
            cands = list(list_cands())[:MAX_CANDS]
        except Exception as e:  # noqa: BLE001 - world.actors refuses while the game loads
            cands = []
            skipped = {"world.actors": f"unavailable ({str(e)[:80]})"}
        cands = [{"ref": str(c["ref"]), "id": c.get("id")} for c in cands if c.get("ref")]
        listed = len(cands)
        reads: Dict[str, Any] = {}
        for c in ([hint] if hint else []) + cands:
            if c["ref"] in reads:
                continue
            try:
                reads[c["ref"]] = read(c)
            except Exception:  # noqa: BLE001 - not loaded on this copy
                reads[c["ref"]] = None
        origin = None
        if player_pos is not None:
            try:
                origin = player_pos()
            except Exception:  # noqa: BLE001
                origin = None
        pick, skipped = choose(hint, cands, reads, origin, accept, allow_fallback, allow_restrained, require_3d)
        if pick is not None:
            return pick
        if now() >= end:
            detail = ""
            if hint and isinstance(reads.get(hint["ref"]), dict):
                rd = reads[hint["ref"]]
                detail = f"has3D {str(bool(rd.get('has3D'))).lower()}" + (f", cell {rd['cell']}" if rd.get("cell") not in (None, "") else "")
            raise NotFound(explain(hint, skipped, listed, detail, allow_fallback and MUTANT != "no_fallback"))
        sleep(poll_s)
