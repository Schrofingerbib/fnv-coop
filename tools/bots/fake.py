"""Game-free fake bridge for the bot API (same idea as tools/runner/tests/fake_server.py, but in-process).

It implements the console grammar and bridge methods in primitives.py over a tiny world, with a virtual clock
(sleep() advances time instantly) so tests run in milliseconds. mutant= injects one defect the tests must catch:

  setpos_ignored    SetPos does nothing
  angle_ignored     SetAngle does nothing
  activate_noop     Activate does nothing (doors never open, NPCs never talk, items never picked up)
  transfer_noop     RemoveItemTarget does nothing
  transfer_dup      RemoveItemTarget gives the destination the items but the source keeps them
  equip_noop        EquipItem does nothing
  drop_noop         Drop does nothing
  fire_miss         bot.fire reports fired:0
  health_stuck      shots land but health never changes
  dialog_never      activating an NPC never opens dialogue
  dialog_lists_all  bot.dialog lists the topics of every actor, not only those of the NPC in dialogue
  ref_info_error    ref.info always fails with an RPC error
  crash             exec raises ConnectionLost
  drop_wrong_ref    the OnDrop event names a different ref than the one the engine created
  travel_ignored    bot.fasttravel is accepted but the player never moves
  msgbox_stuck      bot.menu.press presses the engine's message box natively, but the box never closes
  msgbox_ui_only    bot.menu.press only answers the UI layer's box (the plugin before the native path existed)
  run_is_key        bot.move holds Run for run:true whatever the always-run toggle is (the plugin before run = speed)

Always-run: `always_run` is the engine's toggle (PlayerCharacter+0x651). True is the New Vegas default. bot.move `run` is the speed.
The fake plugin holds Run only when run != always_run (runControlFor in bot_rpc.h). The fake engine moves at run speed when
Run held != always_run (the run decision at 0x941A2B, keyboard mode). So with always-run on, Run held means walk. The bot.move
reply and bot.status carry the same run fields as bot_rpc.h.

Message boxes: `box` is the UI layer's own box (ui.open / ui.answer); `native_boxes` lists the engine's own queued boxes (MessageMenu,
menu id 1001), one entry per box = its number of buttons. bot.menu reports kind "message" while either is shown.
`box_waves` = [(virtual time, [button counts]), ...] sorted by time: engine boxes that join `native_boxes` later, the way the DLC
notices come after a load (6, then 2, then 2 about 16 s later). While an engine box is shown the game is in menu mode: held
movement controls do not move the player.
`box_closing_s` > 0: after a native press the box stays the top menu for that many virtual seconds with no current message (it is
closing). A press in that time gets the plugin's refusal "... has no current message (it is closing) ...". Then the box goes and the
next queued box (if any) is shown.
"""
from __future__ import annotations


import re
import math
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from .bot import PLAYER_REF, angle_diff, heading_deg
from .bot import BridgeError, ConnectionLost, RpcError  # re-exported from nvbridge
from . import primitives as P

PLAYER_ID = 0x14
_DRIVER_METHODS = {P.M_BOT_STATUS, P.M_BOT_MOVE, P.M_BOT_STOP, P.M_BOT_LOOK, P.M_BOT_FIRE, P.M_BOT_RELOAD, P.M_BOT_JUMP,
                   P.M_BOT_SNEAK, P.M_BOT_ACTIVATE}
# Functions the fake console evaluates. The game has IsSneaking. It has no GetSneaking ("Unknown variable", first in-game run 2026-10-21).
_EXPR_FUNCS = {"issneaking", "getangle", "getequipped", "getitemcount", "getopenstate", "getlocked", "getstage"}


JUMP_SETTLE_S = 2.0  # see M_BOT_SNEAK in FakeBridge: crouch toggles right after a jump


class VirtualClock:
    def __init__(self) -> None:
        self.t = 0.0
        self.on_advance = None

    def monotonic(self) -> float:
        return self.t

    def sleep(self, s: float) -> None:
        dt = max(s, 0.001)
        self.t += dt
        if self.on_advance:
            self.on_advance(dt)


@dataclass
class Thing:
    id: int
    kind: str                       # actor | container | door | item | terminal
    pos: List[float] = field(default_factory=lambda: [0.0, 0.0, 0.0])
    cell: int = 1
    health: float = 100.0
    items: Dict[int, int] = field(default_factory=dict)
    open_state: int = 3             # 3 closed, 1 open
    locked: bool = False
    disabled: bool = False
    base_item: int = 0              # for kind == item: what picking it up gives
    topics_text: List[str] = field(default_factory=list)   # terminal entry names
    base_id: int = 0                # base form id (0 = same as id); used by dev.countRefs
    topics: List[int] = field(default_factory=list)
    load_to: int = 0                # door only: the cell a load door leads to (0 = an ordinary door that opens and closes)

    @property
    def dead(self) -> bool:
        return self.kind == "actor" and self.health <= 0

    @property
    def type_id(self) -> int:
        return {"actor": P.TYPE_ACTOR, "container": P.TYPE_CONTAINER, "door": P.TYPE_DOOR, "terminal": 0x17, "item": 0x28}[self.kind]


@dataclass
class Player:
    pos: List[float] = field(default_factory=lambda: [0.0, 0.0, 0.0])
    cell: int = 1
    angle: Dict[str, float] = field(default_factory=lambda: {"X": 0.0, "Z": 0.0})
    items: Dict[int, int] = field(default_factory=dict)
    equipped: set = field(default_factory=set)
    in_dialogue: Optional[int] = None
    ammo: int = 12
    health: float = 200.0
    said: List[int] = field(default_factory=list)


class World:
    def __init__(self) -> None:
        self.things: Dict[int, Thing] = {}
        self.stages: Dict[int, int] = {}
        self.players: List[Player] = []
        self.walls: List[Tuple[float, float, float, float]] = []   # axis-aligned boxes (x1, y1, x2, y2) that block walking

    def add_wall(self, x1: float, y1: float, x2: float, y2: float) -> None:
        self.walls.append((min(x1, x2), min(y1, y2), max(x1, x2), max(y1, y2)))

    def add(self, thing: Thing) -> Thing:
        self.things[thing.id] = thing
        return thing

    def add_actor(self, id: int, pos=(0, 0, 0), health: float = 100.0, cell: int = 1, topics=()) -> Thing:
        return self.add(Thing(id, "actor", list(map(float, pos)), cell, health, topics=list(topics)))

    def add_container(self, id: int, pos=(0, 0, 0), items=None, cell: int = 1) -> Thing:
        return self.add(Thing(id, "container", list(map(float, pos)), cell, items=dict(items or {})))

    def add_door(self, id: int, pos=(0, 0, 0), locked: bool = False, cell: int = 1) -> Thing:
        return self.add(Thing(id, "door", list(map(float, pos)), cell, locked=locked))

    def add_terminal(self, id: int, pos=(0, 0, 0), cell: int = 1) -> Thing:
        return self.add(Thing(id, "terminal", list(map(float, pos)), cell))

    def add_item(self, id: int, base_item: int, pos=(0, 0, 0), cell: int = 1) -> Thing:
        return self.add(Thing(id, "item", list(map(float, pos)), cell, base_item=base_item))

    def set_stage(self, quest: int, stage: int) -> None:
        self.stages[quest] = stage

    def new_player(self) -> Player:
        p = Player()
        self.players.append(p)
        return p


def _hex(tok: str) -> int:
    return int(tok, 16)


class FakeBridge:
    def __init__(self, world: Optional[World] = None, mutant: Optional[str] = None, bot_methods: bool = True,
                 menu_method: bool = True, input_driver: Optional[bool] = None, drop_events: bool = True,
                 genuine_travel: bool = True, travel_refuses: bool = False):
        self.world = world or World()
        self.me = self.world.new_player()
        self.mutant = mutant
        self.bot_methods = bot_methods
        self.menu_method = menu_method
        self.input_driver = bot_methods if input_driver is None else input_driver
        self.drop_events = drop_events          # the OnDrop native handler is registered
        self.genuine_travel = genuine_travel    # bot.fasttravel exists (engine glue on)
        self.travel_refuses = travel_refuses    # the engine's may-travel check says no
        self.activate_reach = 400.0             # bot.activate range; the game's activation reach is about 150 (tests set it)
        self.drop_log: List[Dict[str, Any]] = []
        self.travel_due: Optional[Tuple[float, "Thing"]] = None   # (virtual time, marker): the player moves a little after the call
        self._next_ref = 0xFF000000
        self.held: set = set()
        self.terminal_used: Dict[str, List[Dict[str, Any]]] = {}
        self.box = None                 # menu id of a message box being shown (tests set it)
        self.box_answers: List[int] = []
        self.native_boxes: List[int] = []     # engine message boxes (MessageMenu 1001) queued, each = its button count (tests set it)
        self.native_presses: List[int] = []   # buttons pressed natively, in order
        self.box_waves: List[Tuple[float, List[int]]] = []   # (virtual time, [button counts]): engine boxes that appear later (tests set it)
        self.box_closing_s = 0.0                # seconds a pressed engine box stays closing (tests set it); 0 = it goes at once
        self._closing_until: Optional[float] = None
        self.always_run = True                  # the engine's always-run toggle; True = on, the New Vegas default (tests set it)
        self.shots_left = 0
        self.vats_state = {"phase": "idle", "reason": ""}
        self.sneaking = False
        self.shot_timer = 0.0
        self.jump_t = 0.0
        self.jump_base = 0.0
        self.clock = VirtualClock()
        self.clock.on_advance = self._advance
        self.calls: List[Tuple[str, Any]] = []
        self.lease = None
        self.game_pid = 1

    @classmethod
    def pair(cls, world: Optional[World] = None, **kw) -> Tuple["FakeBridge", "FakeBridge"]:
        w = world or World()
        return cls(w, **kw), cls(w, **kw)

    # ---- NvBridge surface used by Bot ----
    def plugins(self) -> List[str]:
        return ["FalloutNV.esm"]

    def ping(self) -> Dict[str, Any]:
        return {"pong": True, "pid": 1}

    def state_to_file(self, path: str, sections=None) -> Dict[str, Any]:
        return {"path": path, "sections": sections or []}

    def eval(self, expr: str) -> float:
        out = self.exec('printc "NVCOOP_EVAL=%g" (' + expr + ')')["output"]
        i = out.rfind("NVCOOP_EVAL=")
        if i < 0:   # the same error as NvBridge.eval
            raise BridgeError(f"cannot evaluate {expr!r}: {out!r}")
        return float(out[i + 12:].split()[0])

    def exec(self, cmd: str, timeout_ms: Optional[int] = None) -> Dict[str, Any]:
        if self.mutant == "crash":
            raise ConnectionLost("fake: pipe closed")
        self.calls.append(("exec", cmd))
        m = re.fullmatch(r'printc "NVCOOP_EVAL=%g" \((.*)\)', cmd)
        if m:
            fm = re.match(r"(?:\w+\.)?(\w+)", m.group(1).strip())
            if fm and fm.group(1).lower() not in _EXPR_FUNCS:
                return {"ok": False, "output": f"Unknown variable '{fm.group(1)}'"}   # what the game's console prints
            v = self._expr(m.group(1).strip())
            if v is None:
                return {"ok": False, "output": "unknown expression"}
            return {"ok": True, "output": f"NVCOOP_EVAL={v:g}"}
        ok = self._stmt(cmd.strip())
        return {"ok": ok, "output": "" if ok else "unknown command"}

    def call(self, method: str, params: Optional[Dict[str, Any]] = None, timeout_s: float = 35.0) -> Any:
        params = params or {}
        self.calls.append((method, params))
        w = self.world
        if method == P.M_REF_INFO:
            if self.mutant == "ref_info_error":
                raise RpcError(-32000, "reference not loaded", method)
            t = self._by_ref(params["ref"])
            if t == "player":
                p = self.me
                return {"x": p.pos[0], "y": p.pos[1], "z": p.pos[2], "lifeState": 0, "flags": 0, "type": 0x3B, "cell": p.cell}
            if t is None:
                raise RpcError(-32000, "reference not loaded", method)
            return {"x": t.pos[0], "y": t.pos[1], "z": t.pos[2], "lifeState": 2 if t.dead else 0,
                    "flags": P.FLAG_DISABLED if t.disabled else 0, "type": t.type_id, "cell": t.cell}
        if method == P.M_ACTIVATE:
            t = self._by_ref(params["ref"])
            if t is None or t == "player":
                raise RpcError(-32000, "ref or player not loaded", method)
            if self.mutant != "activate_noop":
                self._activate(t)
            return {"ok": True, "returned": 1}
        if method == P.M_QUERY:
            t = self._by_ref(params["ref"])
            if params["fn"] != "GetActorValue" or params.get("p1") != P.HEALTH_AV:
                raise RpcError(-32000, "query failed", method)
            return {"value": self.me.health if t == "player" else (t.health if t else 0)}
        if method == P.M_DIALOG_OPEN:
            return {"open": self.me.in_dialogue is not None}
        if method == P.M_DIALOG_CLOSE:
            was = self.me.in_dialogue is not None
            self.me.in_dialogue = None
            return {"closed": was}
        if method.startswith("bot.") and not self.bot_methods and method not in _DRIVER_METHODS:
            raise RpcError(-32601, "unknown method: " + method, method)
        if method in _DRIVER_METHODS:
            return self._driver(method, params)
        if method == P.M_BOT_VATS:
            t = self._by_ref(params["ref"])
            if isinstance(t, Thing) and not t.dead:
                if self.mutant != "vats_noop":
                    t.health -= 40
                self.vats_state = {"phase": "done" if self.mutant != "vats_fail" else "failed", "reason": "target_not_selectable"}
                return {"started": True}
            raise RpcError(-32000, "target not loaded", method)
        if method == P.M_BOT_SAY:
            d = self.me.in_dialogue
            t = w.things.get(d) if d is not None else None
            if t and _hex(params["info"].split(":")[1]) in t.topics:
                self.me.said.append(_hex(params["info"].split(":")[1]))
                return {"said": True}
            return {"said": False}
        if method == P.M_BOT_MENU:
            if not self.menu_method:
                raise RpcError(-32601, "unknown method: " + method, method)
            d = self.me.in_dialogue
            if d is not None:
                return {"kind": "dialogue", "id": 0x3F1, "dialogReady": True}
            if self.box is not None or self.native_boxes:
                return {"kind": "message", "id": 0x3E9}
            return {"kind": getattr(self, "_menu", "none")}
        if method == P.M_BOT_DIALOG:
            d = self.me.in_dialogue
            if d is None:
                raise RpcError(-32000, "no dialogue open", method)
            t = self.world.things[d]
            tops = list(t.topics)
            if self.mutant == "dialog_lists_all":
                tops = [top for x in self.world.things.values() for top in x.topics]
            return {"choices": [{"idx": i, "formId": top, "info": f"FalloutNV.esm:{top:X}"} for i, top in enumerate(tops)]}
        if method == P.M_BOT_INVENTORY:
            holder = self._by_ref(params["ref"]) if params.get("ref") else "player"
            items = self._holder_items(holder)
            if items is None:
                raise RpcError(-32000, "reference not loaded", method)
            want = [_hex(i.split(":")[1]) for i in params.get("items", [])]
            ids = want or sorted(items)
            return {"items": [{"item": f"FalloutNV.esm:{i:X}", "count": items.get(i, 0)} for i in ids if want or items.get(i, 0)],
                    "partial": not want}
        if method in (P.M_BOT_PRESS, P.M_BOT_CONSENT):
            if self.box is None:
                # same order as bot_rpc.h: the UI layer's box first, then (bot.menu.press only) the engine's own message box
                if method == P.M_BOT_PRESS and self.native_boxes and self.mutant != "msgbox_ui_only":
                    button, n = params["button"], self.native_boxes[0]
                    if self._closing_until is not None:   # the gate order of pressNativeMessageButton: closing before the button range
                        raise RpcError(-32000, "native message box: the message box has no current message (it is closing)"
                                               " (ui layer: no message box is being shown)", method)
                    if not 0 <= button < n:
                        raise RpcError(-32000, f"native message box: button {button} is not shown (the box has {n} buttons)"
                                               " (ui layer: no message box is being shown)", method)
                    self.native_presses.append(button)
                    if self.mutant != "msgbox_stuck":
                        if self.box_closing_s > 0:
                            self._closing_until = self.clock.t + self.box_closing_s
                        else:
                            self.native_boxes.pop(0)
                    return {"menu": 0x3E9, "button": button, "value": button, "buttons": n, "path": "native"}
                raise RpcError(-32000, "no message box is being shown", method)
            button = params["button"] if method == P.M_BOT_PRESS else {"ready": 0, "notyet": 1}[params["answer"]]
            self.box_answers.append(button)
            menu, self.box = self.box, None
            return {"menu": menu, "button": button, "path": "ui"} if method == P.M_BOT_PRESS else {"menu": menu, "button": button}
        if method == P.M_TERMINAL:
            t = self._by_ref(params["ref"])
            if not isinstance(t, Thing) or t.kind != "terminal" or getattr(self, "_menu", "none") != "terminal":
                raise RpcError(-32000, "terminal menu is not open", method)
            names = t.topics_text or ["Read log", "Exit"]
            if not 0 <= params["entry"] < len(names):
                raise RpcError(-32000, "entry index out of range", method)
            if self.mutant != "terminal_noop":
                self.terminal_used.setdefault(params["ref"], []).append({"index": params["entry"], "text": names[params["entry"]]})
            return {"entries": names, "used": names[params["entry"]], "index": params["entry"]}
        if method == P.M_TERMINAL_STATE:
            return {"used": self.terminal_used.get(params["ref"], [])}
        if method == P.M_FASTTRAVEL:
            t = self._by_ref(params["marker"])
            if not isinstance(t, Thing):
                raise RpcError(-32000, "marker not loaded", method)
            if self.mutant != "setpos_ignored":
                self.me.pos, self.me.cell = [t.pos[0] + 40, t.pos[1], t.pos[2]], t.cell
            return {"genuine": False}
        if method == P.M_BOT_FASTTRAVEL:
            if not self.genuine_travel:
                raise RpcError(-32000, "unsupported: engine glue not wired", method)
            t = self._by_ref(params["marker"])
            if not isinstance(t, Thing):
                raise RpcError(-32000, "marker not loaded", method)
            if self.travel_refuses:
                raise RpcError(-32000, "the engine refuses fast travel right now (combat)", method)
            if self.mutant not in ("travel_ignored", "setpos_ignored"):
                self.travel_due = (self.clock.t + 3.0, t)
            return {"genuine": True, "queued": True}
        if method == P.M_BOT_DROPS:
            if not self.drop_events:
                raise RpcError(-32000, "unsupported: the OnDrop event handler is not registered", method)
            since, base, window = params.get("since", 0), params.get("base", ""), params.get("windowMs", 5000) / 1000.0
            return {"seq": len(self.drop_log), "drops": [{k: v for k, v in d.items() if k != "at"} for d in self.drop_log
                                                        if d["seq"] > since and (not base or d["base"] == base) and self.clock.t - d["at"] <= window]}
        if method == P.M_REFS_OF:
            base = _hex(params["base"].split(":")[1])
            return {"refs": [f"FalloutNV.esm:{t.id:X}" for t in self.world.things.values()
                             if t.cell == self.me.cell and not t.disabled and not t.dead and (t.base_id or t.id) == base]}
        if method == P.M_COUNT_REFS:
            base = _hex(params["base"].split(":")[1])
            cell = self.me.cell
            return {"count": sum(1 for t in self.world.things.values()
                                 if t.cell == cell and not t.disabled and not t.dead and (t.base_id or t.id) == base)}
        raise RpcError(-32601, "unknown method: " + method, method)

    # ---- internals ----
    def _by_ref(self, form: str):
        if form == PLAYER_REF:
            return "player"
        name, local = form.rsplit(":", 1)
        return self.world.things.get(_hex(local))

    def _target(self, tok: str):
        if tok.lower() == "player":
            return "player"
        return self.world.things.get(_hex(tok))

    def _holder_items(self, t) -> Optional[Dict[int, int]]:
        if t == "player":
            return self.me.items
        return t.items if t is not None else None

    def _activate(self, t: Thing) -> None:
        me = self.me
        if t.kind == "door":
            if t.locked:
                return
            if t.load_to:   # a load door moves the player to the other cell; its open state stays closed (in game, 2026-10-21)
                me.cell = t.load_to
                return
            t.open_state = 1 if t.open_state == 3 else 3
        elif t.kind == "actor" and not t.dead:
            if self.mutant != "dialog_never":
                me.in_dialogue = t.id
        elif t.kind == "item":
            t.disabled = True
            me.items[t.base_item] = me.items.get(t.base_item, 0) + 1
        elif t.kind in ("container", "terminal"):
            self._menu = t.kind

    # ---- simulated input driver (plugin/bots): same method names and reply shapes as bot_rpc.h ----
    def _driver(self, method: str, params: Dict[str, Any]) -> Dict[str, Any]:
        if not self.input_driver:
            raise RpcError(-32601, "unknown method: " + method, method)
        me = self.me
        if method == P.M_BOT_STATUS:
            return {"enabled": True, "hooked": True, "frame": int(self.clock.t * 60), "held": sorted(self.held),
                    "shotsLeft": self.shots_left, "alwaysRun": self.always_run, "vats": self.vats_state}
        if method == P.M_BOT_MOVE:
            want = bool(params.get("run", False))
            run_control = want if self.mutant == "run_is_key" else want != self.always_run   # runControlFor in bot_rpc.h
            if self.mutant != "move_ignored":
                self.held -= {"Forward", "Backward", "Left", "Right", "Run"}
                f, st = float(params.get("forward", 0)), float(params.get("strafe", 0))
                if f > 0.3: self.held.add("Forward")
                if f < -0.3: self.held.add("Backward")
                if st > 0.3: self.held.add("Right")
                if st < -0.3: self.held.add("Left")
                if run_control: self.held.add("Run")
            return {"held": sorted(self.held), "run": want, "runControl": run_control, "alwaysRun": self.always_run}
        if method == P.M_BOT_STOP:
            if self.mutant != "stop_ignored":
                self.held.clear()
            self.shots_left = 0
            return {}
        if method == P.M_BOT_LOOK:
            if "ref" in params:
                t = self._by_ref(params["ref"])
                if not isinstance(t, Thing):
                    raise RpcError(-32000, "reference not loaded", method)
                z, x = heading_deg((me.pos[0], me.pos[1], me.pos[2] + 120), (t.pos[0], t.pos[1], t.pos[2] + 60))
            else:
                z, x = float(params.get("yaw", 0)), float(params.get("pitch", 0))
            if self.mutant != "look_ignored":
                me.angle["Z"], me.angle["X"] = z % 360.0, x
            return {"yaw": z, "pitch": x}
        if method == P.M_BOT_FIRE:
            n = int(params.get("shots", 1))
            if self.mutant != "fire_noop":
                self.shots_left = n
                self.shot_timer = 0.0
            return {"queued": n}
        if method == P.M_BOT_RELOAD:
            me.ammo = 12
            return {}
        if method == P.M_BOT_JUMP:
            if self.mutant != "jump_noop" and self.jump_t <= 0:
                self.jump_t, self.jump_base = 0.6, me.pos[2]
                self.jump_settle_until, self.jump_toggles = self.clock.t + JUMP_SETTLE_S, 0
            return {}
        if method == P.M_BOT_SNEAK:
            # In game (2026-10-21): within about 2 s of a jump only one crouch toggle takes effect; a second tap 1.1 s
            # later left the player crouched. Two seconds after the jump both taps toggle again.
            if self.clock.t < getattr(self, "jump_settle_until", 0.0):
                self.jump_toggles += 1
                if self.jump_toggles > 1:
                    return {}
            self.sneaking = not self.sneaking
            return {}
        if method == P.M_BOT_ACTIVATE:
            if self.mutant != "activate_noop":
                for t in self.world.things.values():
                    z, x = heading_deg((me.pos[0], me.pos[1], me.pos[2] + 120), (t.pos[0], t.pos[1], t.pos[2] + 60))
                    if (t.kind in ("door", "actor", "item") and math.dist(me.pos, t.pos) < self.activate_reach
                            and angle_diff(z, me.angle["Z"]) <= 10):
                        self._activate(t)
                        break
            return {}
        raise RpcError(-32601, "unknown method: " + method, method)

    def _blocked(self, x: float, y: float) -> bool:
        return any(x1 <= x <= x2 and y1 <= y <= y2 for x1, y1, x2, y2 in self.world.walls)

    def _advance(self, dt: float) -> None:
        """Move virtual time forward: locomotion from held controls, queued shots, jump arc."""
        me = self.me
        if self.travel_due and self.clock.t >= self.travel_due[0]:
            marker = self.travel_due[1]
            self.travel_due = None
            me.pos, me.cell = [marker.pos[0] + 40, marker.pos[1], marker.pos[2]], marker.cell
        t_end = self.clock.t
        left = dt
        while left > 1e-9:
            h = min(left, 0.02)
            left -= h
            if self._closing_until is not None and t_end - left >= self._closing_until:   # the closing box goes, the next one shows
                self._closing_until = None
                if self.native_boxes:
                    self.native_boxes.pop(0)
            while self.box_waves and t_end - left >= self.box_waves[0][0]:
                self.native_boxes.extend(self.box_waves.pop(0)[1])
            fwd = ("Forward" in self.held) - ("Backward" in self.held)
            side = ("Right" in self.held) - ("Left" in self.held)
            if (fwd or side) and not self.native_boxes:   # an engine message box is up: menu mode, the player does not move
                running = ("Run" in self.held) != self.always_run   # the engine's run decision: with always-run on, Run held = walk
                speed = 220.0 if running else 120.0
                yaw = math.radians(me.angle["Z"])
                dx = (math.sin(yaw) * fwd + math.cos(yaw) * side) * speed * h
                dy = (math.cos(yaw) * fwd - math.sin(yaw) * side) * speed * h
                nx, ny = me.pos[0] + dx, me.pos[1] + dy
                if not self._blocked(nx, ny):
                    me.pos[0], me.pos[1] = nx, ny
                elif not self._blocked(nx, me.pos[1]):   # slide along the wall like the engine does
                    me.pos[0] = nx
                elif not self._blocked(me.pos[0], ny):
                    me.pos[1] = ny
            if self.jump_t > 0:
                self.jump_t -= h
                me.pos[2] = self.jump_base + (80.0 * math.sin(math.pi * max(0.0, 1 - self.jump_t / 0.6)) if self.jump_t > 0 else 0.0)
            if self.shots_left > 0:
                self.shot_timer -= h
                if self.shot_timer <= 0:
                    self.shots_left -= 1
                    self.shot_timer = 0.18
                    self._shoot()

    def _shoot(self) -> None:
        me = self.me
        if me.ammo < 1:
            return
        me.ammo -= 1
        if self.mutant in ("fire_miss", "health_stuck"):
            return
        best = None
        for t in self.world.things.values():
            if t.kind != "actor" or t.dead or t.cell != me.cell:
                continue
            z, x = heading_deg((me.pos[0], me.pos[1], me.pos[2] + 120), (t.pos[0], t.pos[1], t.pos[2] + 60))
            if angle_diff(z, me.angle["Z"]) <= 6 and angle_diff(x, me.angle["X"]) <= 6:
                d = math.dist(me.pos, t.pos)
                if best is None or d < best[0]:
                    best = (d, t)
        if best:
            best[1].health -= 25

    def _stmt(self, line: str) -> bool:
        m = re.fullmatch(r"(?:(\w+)\.)?(\w+)(?:\s+(.*))?", line)
        if not m:
            return False
        tgt, cmd, rest = m.group(1), m.group(2).lower(), (m.group(3) or "").split()
        me = self.me
        mut = self.mutant
        if cmd == "setpos" and tgt == "player":
            if mut != "setpos_ignored":
                me.pos["XYZ".index(rest[0].upper())] = float(rest[1])
            return True
        if cmd == "setangle" and tgt == "player":
            if mut != "angle_ignored":
                me.angle[rest[0].upper()] = float(rest[1]) % 360.0
            return True
        if cmd == "moveto" and tgt == "player":
            t = self._target(rest[0])
            if isinstance(t, Thing) and mut != "setpos_ignored":
                me.pos = [t.pos[0] + 40, t.pos[1], t.pos[2]]
                me.cell = t.cell
            return isinstance(t, Thing)
        if cmd == "equipitem" and tgt == "player":
            if mut != "equip_noop" and me.items.get(_hex(rest[0]), 0) > 0:
                me.equipped.add(_hex(rest[0]))
            return True
        if cmd == "unequipitem" and tgt == "player":
            me.equipped.discard(_hex(rest[0]))
            return True
        if cmd == "drop" and tgt == "player":
            i, n = _hex(rest[0]), int(rest[1])
            if mut != "drop_noop" and me.items.get(i, 0) >= n:
                me.items[i] -= n
                self._next_ref += 1
                ground = self.world.add(Thing(self._next_ref, "item", list(me.pos), me.cell, base_item=i, base_id=i))
                self.drop_log.append({"seq": len(self.drop_log) + 1, "ref": f"FalloutNV.esm:{ground.id:X}", "base": f"FalloutNV.esm:{i:X}",
                                      "dropper": PLAYER_REF, "at": self.clock.t})
                if mut == "drop_wrong_ref":
                    self.drop_log[-1]["ref"] = "FalloutNV.esm:DEAD"
            return True
        if cmd == "activate":
            t = self._target(tgt or "")
            if isinstance(t, Thing) and mut != "activate_noop":
                self._activate(t)
            return isinstance(t, Thing)
        if cmd == "removeitemtarget":
            src, dst = self._target(tgt or ""), self._target(rest[1])
            si, di = self._holder_items(src), self._holder_items(dst)
            if si is None or di is None:
                return False
            i, n = _hex(rest[0]), int(rest[2])
            if mut == "transfer_noop" or si.get(i, 0) < n:
                return True
            if mut != "transfer_dup":
                si[i] -= n
            di[i] = di.get(i, 0) + n
            return True
        if cmd == "fireweapon":
            return False  # the console fallback does not exist in the fake: tests the "unsupported" path
        return False

    def _expr(self, e: str) -> Optional[float]:
        m = re.fullmatch(r"(?:(\w+)\.)?(\w+)(?:\s+(.*))?", e)
        if not m:
            return None
        tgt, fn, rest = m.group(1), m.group(2).lower(), (m.group(3) or "").split()
        me = self.me
        if fn == "issneaking":
            return 1.0 if self.sneaking else 0.0
        if fn == "getangle":
            return me.angle[rest[0].upper()]
        if fn == "getequipped":
            return 1.0 if _hex(rest[0]) in me.equipped else 0.0
        if fn == "getitemcount":
            items = self._holder_items(self._target(tgt or ""))
            return float(items.get(_hex(rest[0]), 0)) if items is not None else None
        if fn == "getopenstate":
            t = self._target(tgt or "")
            return float(t.open_state) if isinstance(t, Thing) else None
        if fn == "getlocked":
            t = self._target(tgt or "")
            return float(t.locked) if isinstance(t, Thing) else None
        if fn == "getstage":
            return float(self.world.stages.get(_hex(rest[0]), 0))
        return None
