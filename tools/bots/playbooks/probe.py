"""Thin adapter between playbooks and a bot.

Playbooks call P.<name>(bot, ...). If the bot (T6a's tools/bots/bot.py, or the fake) has a method of that name it is used.
Otherwise a fallback built from raw bridge calls runs, or, where the bridge method does not exist yet, the call returns a
failed Result that says exactly which bridge method is missing (see docs/tools/T6b-playbooks.md, "Bridge methods needed").
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, Optional, Tuple


class MissingBridge(RuntimeError):
    """A bridge method this playbook needs is not in the build. The text says which."""


@dataclass
class Result:
    ok: bool
    reason: str = ""
    data: Dict[str, Any] = field(default_factory=dict)

    def __bool__(self) -> bool:
        return self.ok


def hexid(ref: str) -> str:
    """'FalloutNV.esm:104C1C' -> '104C1C'; a bare hex id stays as it is."""
    s = ref.split(":")[-1]
    return re.sub(r"^0x", "", s).upper().lstrip("0") or "0"


def fullref(ref: str, plugin: str = "FalloutNV.esm") -> str:
    return ref if ":" in ref else f"{plugin}:{hexid(ref)}"


def _rpc(bot, method: str, params: Optional[dict] = None, timeout_s: float = 15.0) -> Result:
    try:
        return Result(True, "", {"result": bot.nv.call(method, params or {}, timeout_s=timeout_s)})
    except Exception as e:  # noqa: BLE001
        name = type(e).__name__
        if name in ("ConnectionLost", "GameCrashed"):
            raise
        code = getattr(e, "code", None)
        if code in (-32601, -32600) or "unknown method" in str(e).lower():
            return Result(False, f"bridge method {method!r} is not implemented in this build (needed by T6b playbooks)")
        return Result(False, f"{method}: {e}")


def _has(bot, name: str):
    return callable(getattr(bot, name, None))


# ---- actions with fallbacks -------------------------------------------------------------------
def set_stage(bot, quest: str, stage: int) -> Result:
    if _has(bot, "set_stage"):
        return bot.set_stage(quest, stage)
    try:
        r = bot.nv.exec(f"setstage {hexid(quest)} {int(stage)}")
        return Result(bool(r.get("ok", True)), "" if r.get("ok", True) else str(r.get("output", "")))
    except Exception as e:  # noqa: BLE001
        if type(e).__name__ in ("ConnectionLost", "GameCrashed"):
            raise
        return Result(False, f"setstage failed: {e}")


def start_quest(bot, quest: str) -> Result:
    if _has(bot, "start_quest"):
        return bot.start_quest(quest)
    r = bot.nv.exec(f"startquest {hexid(quest)}")
    return Result(bool(r.get("ok", True)), str(r.get("output", "")) if not r.get("ok", True) else "")


def scene_pass(bot, quest: str) -> Result:
    """This player finished their own per-player step of a barrier quest (chargen). Exists in the bridge today."""
    if _has(bot, "scene_pass"):
        return bot.scene_pass(quest)
    return _rpc(bot, "coop.scenePass", {"quest": fullref(quest)})


def consent(bot, kind: str, quest: str = "", info: str = "", yes: bool = True) -> Result:
    """Answer the party Ready / Not yet consent prompt (L8, docs/dialogue-design.md s12) on this machine.
    Bridge method (T6a, dev-only): bot.consent {answer: "ready"|"notyet"}. `kind`, `quest`, `info` are only used by fakes."""
    if _has(bot, "consent"):
        return bot.consent(kind, quest=quest, info=info, yes=yes)
    return _rpc(bot, "bot.consent", {"answer": "ready" if yes else "notyet"})


def menu_press(bot, button: int) -> Result:
    """Press a button of the open message box (0 = first). Bridge method (T6a, dev-only): bot.menu.press {button}."""
    if _has(bot, "menu_press"):
        return bot.menu_press(button)
    return _rpc(bot, "bot.menu.press", {"button": int(button)})


def fast_travel(bot, marker: str) -> Result:
    """Start fast travel to a map marker ref. bridge method test.fasttravel (T6a: a MoveTo teleport, genuine:false) (console: player.moveto is NOT the same)."""
    if _has(bot, "fast_travel"):
        return bot.fast_travel(marker)
    return _rpc(bot, "test.fasttravel", {"marker": fullref(marker)}, timeout_s=60)


def end_dialogue(bot) -> Result:
    if _has(bot, "end_dialogue"):
        return bot.end_dialogue()
    return _rpc(bot, "dev.endDialogue")


def close_container(bot, ref: str) -> Result:
    if _has(bot, "close_container"):
        return bot.close_container(ref)
    return Result(True, "", {"note": "no close_container on this bot; container menu is closed by the next action"})


def consent_pending(bot) -> bool:
    """Is a Ready / Not yet prompt waiting for this player? Bridge method (runtime, L10): coop.consent {} -> {pending:[...]}."""
    if _has(bot, "consent_pending"):
        return bool(bot.consent_pending())
    r = _rpc(bot, "coop.consent", {})
    if not r.ok:
        raise MissingBridge(r.reason)
    return bool(r.data["result"].get("pending"))


# ---- queries with fallbacks -------------------------------------------------------------------
def stage_done(bot, quest: str, stage: int) -> bool:
    if _has(bot, "stage_done"):
        return bool(bot.stage_done(quest, stage))
    return bot.nv.stage_done(hexid(quest), stage)


def quest_stage(bot, quest: str) -> int:
    if _has(bot, "quest_stage"):
        return int(bot.quest_stage(quest))
    return bot.nv.stage(hexid(quest))


def quest_running(bot, quest: str) -> bool:
    if _has(bot, "quest_running"):
        return bool(bot.quest_running(quest))
    return bot.nv.quest_running(hexid(quest))


def objective_displayed(bot, quest: str, obj: int) -> bool:
    if _has(bot, "objective_displayed"):
        return bool(bot.objective_displayed(quest, obj))
    return bot.nv.objective_displayed(hexid(quest), obj)


def objective_completed(bot, quest: str, obj: int) -> bool:
    if _has(bot, "objective_completed"):
        return bool(bot.objective_completed(quest, obj))
    return bot.nv.objective_completed(hexid(quest), obj)


def _eval_ref(bot, ref: str, fn: str) -> float:
    """`fn` evaluated with `ref` as the calling reference (exec {cmd, ref}). The game's console does not accept
    '<hexFormID>.Func' (in game, 2026-10-21). `ref` goes as it is: 'Plugin.esm:HEX' (the bridge adds the plugin's
    load-order index) or a bare hex runtime id."""
    return bot.nv.eval(fn, ref=ref)


def _exec_ref(bot, ref: str, cmd: str) -> Result:
    """Console line `cmd` with `ref` as the calling reference (see _eval_ref)."""
    r = bot.nv.exec(cmd, ref=ref)
    ok = r.get("ok", True)
    return Result(bool(ok), "" if ok else str(r.get("output", "")))


def seed_items(bot, holder: str, item: str, count: int) -> Result:
    """Put `count` x `item` into the container/actor `holder` (console `additem` on the holder, UNVERIFIED)."""
    return _exec_ref(bot, holder, f"additem {hexid(item)} {int(count)}")


def set_teammate(bot, ref: str) -> Result:
    """Make the NPC `ref` a teammate on this machine (console `SetPlayerTeammate 1` on the NPC)."""
    return _exec_ref(bot, ref, "SetPlayerTeammate 1")


def is_teammate(bot, ref: str) -> bool:
    if _has(bot, "is_teammate"):
        return bool(bot.is_teammate(ref))
    return _eval_ref(bot, ref, "GetPlayerTeammate") != 0


def door_open(bot, ref: str) -> bool:
    if _has(bot, "door_open"):
        return bool(bot.door_open(ref))
    return _eval_ref(bot, ref, "GetOpenState") == 1


def is_locked(bot, ref: str) -> bool:
    if _has(bot, "is_locked"):
        return bool(bot.is_locked(ref))
    # GetLockLevel is -1/0 for unlocked in vanilla scripts; unverified in this project: treat >0 as locked.
    return _eval_ref(bot, ref, "GetLockLevel") > 0


def terminal_entry_used(bot, ref: str, entry: str) -> bool:
    if _has(bot, "terminal_entry_used"):
        return bool(bot.terminal_entry_used(ref, entry))
    r = _rpc(bot, "dev.terminalState", {"ref": fullref(ref)})
    if not r.ok:
        raise MissingBridge(r.reason)
    return entry in (r.data["result"].get("used") or [])


def owner_of(bot, ref: str) -> Tuple[int, int]:
    """(owner slot, epoch) of an actor from the actor ownership service. NEEDS bridge method coop.owner."""
    if _has(bot, "owner_of"):
        return tuple(bot.owner_of(ref))  # type: ignore[return-value]
    r = _rpc(bot, "coop.owner", {"ref": fullref(ref)})
    if not r.ok:
        raise MissingBridge(r.reason)
    res = r.data["result"]
    return int(res["owner"]), int(res["epoch"])


def ref_count(bot, base: str, cell: str = "") -> int:
    """How many live refs of base form `base` exist (duplicate-actor check). NEEDS bridge method dev.countRefs."""
    if _has(bot, "ref_count"):
        return int(bot.ref_count(base, cell))
    r = _rpc(bot, "dev.countRefs", {"base": fullref(base), "cell": cell})
    if not r.ok:
        raise MissingBridge(r.reason)
    return int(r.data["result"]["count"])


def distance(bot, ref: str) -> float:
    if _has(bot, "distance_to"):
        return float(bot.distance_to(ref))
    return float(bot.nv.eval(f"GetDistance {hexid(ref)}"))


def in_combat(bot) -> bool:
    if _has(bot, "in_combat"):
        return bool(bot.in_combat())
    return bot.nv.eval("player.GetCombatTarget") != 0


def spectating(bot) -> bool:
    """Is this machine currently showing another player's dialogue (the spectator view)?"""
    if _has(bot, "spectating"):
        return bool(bot.spectating())
    st = bot.coop_status()
    return bool((st.get("ui") or {}).get("bridged"))


def item_count(bot, item: str) -> int:
    """How many of `item` this machine's own player holds."""
    if _has(bot, "inventory_count"):
        return int(bot.inventory_count(item))
    inv = bot.inventory()
    return int(inv.get(hexid(item), inv.get(item, 0)))


def holder_count(bot, item: str, holder: str) -> int:
    """How many of `item` a container/corpse ref holds, as seen on this machine."""
    if _has(bot, "inventory_count"):
        return int(bot.inventory_count(item, holder))
    return int(bot.ref_container_items(holder).get(item, 0))


# ---- spawn, VATS timing, save/load, session ----------------------------------------------------
def spawn(bot, base: str, at: str) -> Result:
    """Place one `base` at the ref `at` (console `placeatme <base> 1` with `at` as the calling reference, UNVERIFIED)."""
    if _has(bot, "spawn"):
        return bot.spawn(base, at)
    return _exec_ref(bot, at, f"placeatme {hexid(base)} 1")


def frames(bot) -> int:
    """Frames this machine has run (coop.status counters.frames): proves the world on that machine did not pause."""
    return int((bot.coop_status().get("counters") or {}).get("frames", 0))


def rebases(bot) -> int:
    return int(((bot.coop_status().get("client") or {}).get("rebases", 0)))


def save_game(bot, name: str) -> Result:
    if _has(bot, "save_game"):
        return bot.save_game(name)
    try:
        bot.nv.save_game(name, overwrite=True)
        return Result(True)
    except Exception as e:  # noqa: BLE001
        if type(e).__name__ in ("ConnectionLost", "GameCrashed"):
            raise
        return Result(False, f"save failed: {e}")


def load_game(bot, name: str) -> Result:
    if _has(bot, "load_game"):
        return bot.load_game(name)
    try:
        bot.nv.load_save(name)
        return Result(True)
    except Exception as e:  # noqa: BLE001
        if type(e).__name__ in ("ConnectionLost", "GameCrashed"):
            raise
        return Result(False, f"load failed: {e}")


def session_leave(bot) -> Result:
    """This machine drops out of the co-op session. NEEDS runtime method coop.leave (L10)."""
    if _has(bot, "session_leave"):
        return bot.session_leave()
    return _rpc(bot, "coop.leave")


def session_join(bot) -> Result:
    """Rejoin the session. NEEDS runtime method coop.join (L10)."""
    if _has(bot, "session_join"):
        return bot.session_join()
    return _rpc(bot, "coop.join", timeout_s=60)
