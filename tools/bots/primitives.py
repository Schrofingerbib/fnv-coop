"""Every console line and bridge method the bot layer uses, in one place.

The console grammar here has NOT been run against the real game yet (code-only sprint). Each entry is tagged:
  VERIFIED   - already used by existing tools/tests in this repo
  UNVERIFIED - written from NVSE/console knowledge; the lead's first in-game run may need to fix the text here
Fixing a string in this file is the only change needed; the Bot code and the fake bridge tests keep working as long
as the fake bridge (fake.py) is updated to the same grammar.
"""
from __future__ import annotations

# ---- bridge methods (existing, VERIFIED to be registered in plugin/) ----
M_REF_INFO = "ref.info"            # {ref:"plugin:HEX"} -> {x,y,z,lifeState,flags,type,cell}
M_ACTIVATE = "activate.ref"        # {ref} -> {ok, returned}; activator is the host player
M_QUERY = "world.query"            # {ref, fn, p1} -> {value}
M_DIALOG_OPEN = "ui.dialogopen"    # -> {open}
M_DIALOG_CLOSE = "ui.closedialog"  # -> {closed}

# ---- dev-only bridge methods the bots need (plugin/bots, ini [dev] bots=1); see docs/tools/T6a-actions.md ----
M_BOT_FIRE = "bot.fire"        # {ref?, shots} -> {fired, ammo}
M_BOT_RELOAD = "bot.reload"    # {} -> {reloaded, ammo}
M_BOT_VATS = "bot.vats"        # {ref, limb?} -> {queued, hit}
M_BOT_SAY = "bot.say"          # {info: "plugin:HEX"} -> {said}
M_BOT_MENU = "bot.menu"        # {} -> {kind: "none|dialogue|container|terminal|pipboy|...", ref?}
M_BOT_INVENTORY = "bot.inventory"  # {ref?} -> {items:[{item, count}]}
# in-process input driver (plugin/bots/bot_rpc.h)
M_BOT_MENU_PRESS = "bot.menu.press"   # {button}
M_BOT_PRESS = M_BOT_MENU_PRESS
M_BOT_CONSENT = "bot.consent"          # {answer: ready|notyet}
M_BOT_DIALOG = "bot.dialog"            # -> {choices:[{idx,formId,info}]}
M_FASTTRAVEL = "test.fasttravel"       # {marker} -> {genuine:false}: a MoveTo teleport, the fallback
M_BOT_FASTTRAVEL = "bot.fasttravel"    # {marker} -> {genuine:true, queued}: the map menu's own travel path
M_BOT_DROPS = "bot.drops"              # {since?, base?, windowMs?} -> {seq, drops:[{seq,ref,base,dropper}]} from the OnDrop event
M_REFS_OF = "dev.refsOf"               # {base, cell?} -> {refs:[..], count}
M_TERMINAL = "dev.terminal"           # {ref, entry} -> {entries, used, index}
M_TERMINAL_STATE = "dev.terminalState" # {ref} -> {used:[{index,text}]}
M_COUNT_REFS = "dev.countRefs"         # {base, cell?} -> {count}
M_BOT_STATUS = "bot.status"    # -> {enabled, hooked, frame, held:[..], shotsLeft}
M_BOT_MOVE = "bot.move"        # {forward, strafe, run?, ms?}
M_BOT_STOP = "bot.stop"
M_BOT_LOOK = "bot.look"        # {yaw, pitch} | {ref}
M_BOT_JUMP = "bot.jump"
M_BOT_SNEAK = "bot.sneak"
M_BOT_ACTIVATE = "bot.activate"

HEALTH_AV = 16                 # actor value code of Health (decimal); UNVERIFIED for world.query p1

# FNV TESForm typeIDs, from world.actors docs in hooks_world
TYPE_ACTOR = 0x3B
TYPE_CONTAINER = 0x1B
TYPE_DOOR = 0x1C
FLAG_DISABLED = 0x800          # UNVERIFIED TESForm flag bit for "initially disabled / disabled"

# GetOpenState return values, UNVERIFIED
OPEN_STATES = {1: "open", 2: "opening", 3: "closed", 4: "closing"}


def setpos(axis: str, v: float) -> str:        # UNVERIFIED
    return f"player.SetPos {axis} {v:.2f}"


def setangle(axis: str, deg: float) -> str:   # UNVERIFIED
    return f"player.SetAngle {axis} {deg:.2f}"


def get_angle(axis: str) -> str:              # UNVERIFIED (expression)
    return f"player.GetAngle {axis}"


def moveto(hexid: str) -> str:                # VERIFIED name, UNVERIFIED use on player from console
    return f"player.MoveTo {hexid}"


def equip(item: str) -> str:                  # UNVERIFIED
    return f"player.EquipItem {item}"


def unequip(item: str) -> str:                # UNVERIFIED
    return f"player.UnequipItem {item}"


def is_equipped(item: str) -> str:            # UNVERIFIED (expression)
    return f"player.GetEquipped {item}"


def item_count(holder: str, item: str) -> str:  # UNVERIFIED (expression); holder is "player" or a hex ref id
    return f"{holder}.GetItemCount {item}"


def transfer(src: str, item: str, dst: str, count: int) -> str:   # UNVERIFIED: NVSE RemoveItemTarget
    return f"{src}.RemoveItemTarget {item} {dst} {count} 1"


def drop(item: str, count: int) -> str:       # UNVERIFIED
    return f"player.Drop {item} {count}"


def activate_console(hexid: str) -> str:      # UNVERIFIED fallback when activate.ref is missing
    return f"{hexid}.Activate player 1"


def open_state(hexid: str) -> str:            # UNVERIFIED (expression)
    return f"{hexid}.GetOpenState"


def locked(hexid: str) -> str:                # UNVERIFIED (expression)
    return f"{hexid}.GetLocked"


def get_stage(quest: str) -> str:             # VERIFIED (used by nvbridge.stage)
    return f"GetStage {quest}"


def fire_console() -> str:                    # UNVERIFIED fallback when bot.fire is missing
    return "player.FireWeapon"


def reload_console() -> str:                  # UNVERIFIED fallback when bot.reload is missing
    return "player.ReloadWeapon"
