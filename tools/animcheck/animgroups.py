"""Animation group ids -> names and coarse categories.

Ids follow the xNVSE enum TESAnimGroup::kAnimGroup_* (see animgroups_table.py). That the running game uses the same
numbering is taken from xNVSE and has not been measured yet: the first in-game probe should be compared with
`anim.probe` output while the player walks / draws / reloads (docs/tools/T7-anim.md, "First in-game check").
"""
from __future__ import annotations

from .animgroups_table import NAMES

LOCO_WALK = {"Forward", "Backward", "Left", "Right"}
LOCO_RUN = {"FastForward", "FastBackward", "FastLeft", "FastRight"}
DODGE = {"DodgeForward", "DodgeBack", "DodgeLeft", "DodgeRight"}
TURN = {"TurnLeft", "TurnRight"}


def name(group: int) -> str:
    if 0 <= group < len(NAMES):
        return NAMES[group]
    return f"group{group}"


def category(group: int) -> str:
    """One of: idle, walk, run, dodge, turn, aim, attack, reload, jam, equip, hit, death, jump, block, misc, unknown."""
    if group < 0 or group >= len(NAMES):
        return "unknown"
    n = NAMES[group]
    if n in ("Idle", "DynamicIdle", "SpecialIdle"):
        return "idle"
    if n in LOCO_WALK:
        return "walk"
    if n in LOCO_RUN:
        return "run"
    if n in DODGE:
        return "dodge"
    if n in TURN:
        return "turn"
    if n.startswith("Aim"):
        return "aim"
    if n.startswith("Attack") or n.startswith("PlaceMine") or n in ("Counter", "stomp"):
        return "attack"
    if n.startswith("Reload"):
        return "reload"
    if n.startswith("Jam"):
        return "jam"
    if n in ("Holster", "Equip", "Unequip") or n.startswith("HandGrip"):
        return "equip"
    if n in ("Stagger", "Recoil", "BlockHit"):
        return "hit"
    if n == "Death":
        return "death"
    if n.startswith("Jump"):
        return "jump"
    if n == "BlockIdle":
        return "block"
    if n in ("Talking", "PipBoy", "PipBoyChild"):
        return "misc"
    return "misc"


# Categories that mean "the body is travelling under animation control"
LOCOMOTION = {"walk", "run", "dodge"}
# Categories only a living, standing actor plays
ALIVE_ONLY = {"idle", "walk", "run", "dodge", "turn", "aim", "attack", "reload", "jam", "equip", "block", "misc"}
AIRBORNE = {"jump"}


def is_reload(group: int) -> bool:
    return category(group) == "reload"
