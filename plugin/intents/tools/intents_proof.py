#!/usr/bin/env python3
"""In-game proof of guest intents (FULL-SYNC-PLAN 3.7) and SaveSuppress enforcement.

Needs three game instances already in one co-op session (instance 0 = host, 1 and 2 = guests), with nvcoop built with plugin/intents and
nvcoop.ini [intents] enable=1 on all three (and [live] avatars=1 on the host and guests so every guest has a puppet; without puppets the
host acts as a stand-in and step 2 reports it).

    python -I plugin/intents/tools/intents_proof.py --instances 0 1 2 --door 0xDOOR --item 0xITEM --item-base 0xBASE --container 0xCONT
                                                    [--terminal 0xREF --entry 0] [--trigger 0xREF] [--note 0xREF --note-base 0xNOTE]
                                                    [--npc 0xREF] [--stash] [--close-menu CloseAllMenus]
    python -I plugin/intents/tools/intents_proof.py --selftest      # game-free: the script's own pass / fail logic against a fake world

Step 0 puts all three players within 400 units of the door, the item and the container by itself (bridge ref.info reads where the three
references are, then `player.MoveTo` + `player.SetPos` on every instance, then ref.info again to verify; no human positioning). `--no-position`
skips it (the players are then expected to stand there already); `--radius N` changes the 400.

References are runtime form ids ("0x00103E61") or "Plugin.esm:HEX" as the bridge's `ref` argument takes them. Use a door and a container near
the players; `--item` is a world item (a bottle cap pile) whose base form is `--item-base`.

Steps (each prints PASS / FAIL with the numbers it saw):
  0 position   (default on) every player within --radius (400) of the door, the item and the container; a failure here stops the run
  1 module     intents.status on every instance: enabled, engine pins ok, session up, the SaveSuppress hook installed
  2 activate   a guest activates the door: the host ran it once, as the guest's puppet (or says it stood in), the door state matches everywhere
  3 pickup     both guests take the same item at the same moment: exactly one of them has it, the host has none, the item is gone everywhere
  4 container  guest 1 opens the container (lock held), guest 2 is told it is in use, guest 1 closes the menu: the lock is released
  5 terminal   (--terminal) a terminal entry intent reaches the host and the host answers (Executed or a plain Unsupported)
  6 trigger    (--trigger) an OnTriggerEnter intent runs once on the host and its repeat inside the cooldown does not
  6b stay      (--trigger) the guest's presence in the volume makes the host raise OnTrigger every frame by itself; a TriggerLeave ends it (G04)
  8 note       (--note, --note-base) guest 1 picks up a note: the host's journal has it (GetHasNote true on the host and everywhere), no inventory item (G10)
  9 combat     (--npc) an actor starts combat with guest 1: the host ran its OnStartCombat block once (G04)
  10 stash     (--stash) the shared gear containers of nvcoop_intents.json are loaded on the host and no swap failed (G35)
  7 save gate  the host's story turns SaveSuppress on: CanSaveNow is refused on every instance (and the game's own answer is restored when it is off)
Exit code 0 only if every step passed.
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

SETTLE_S = 2.5
NEAR_UNITS = 400.0          # the host only accepts an intent from a guest this close to the reference
MARGIN_UNITS = 60.0         # the standing point leaves room for the per-player offset below
PLAYER_INFO_REF = "FalloutNV.esm:14"
SPREAD_UNITS = 30.0         # player i stands i * SPREAD_UNITS east of the standing point, so the avatars do not occupy one spot
POSITION_TRIES = 3


class PositionError(Exception):
    pass


def dist3(a: Any, b: Any) -> float:
    return ((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2 + (a[2] - b[2]) ** 2) ** 0.5


def info_ref(nv: Any, ref: str) -> str:
    """The "Plugin.esm:HEX" form ref.info takes; a runtime id ("0x00103E61" or "00103E61") is mapped through the load order."""
    if ":" in ref:
        return ref
    rid = int(ref, 16)
    plugins = nv.plugins()
    if (rid >> 24) >= len(plugins):
        raise PositionError(f"{ref}: load index {rid >> 24} is not in the plugin list")
    return f"{plugins[rid >> 24]}:{rid & 0xFFFFFF:X}"


def runtime_hex(nv: Any, ref: str) -> str:
    """The 8 digit runtime id the console's MoveTo takes ("Plugin.esm:HEX" -> load index + local id)."""
    if ":" not in ref:
        return f"{int(ref, 16):08X}"
    name, local = ref.rsplit(":", 1)
    low = [p.lower() for p in nv.plugins()]
    if name.lower() not in low:
        raise PositionError(f"{ref}: plugin {name} is not loaded")
    return f"{(low.index(name.lower()) << 24) | int(local, 16):08X}"


def standing_point(points: List[Any], radius: float = NEAR_UNITS, margin: float = MARGIN_UNITS):
    """-> (anchor index, (x, y, z), worst distance). A player is first put at the anchor reference (MoveTo, which also fixes the cell), then
    moved sideways to (x, y) with SetPos; z stays the anchor's. Candidates in order of preference (least SetPos movement first): each
    reference's own spot, then the centroid, then the middle of the bounding box. The first whose worst 3D distance to all the references is
    <= radius - margin wins; otherwise the best one is returned (the caller reports it when it is over `radius`)."""
    xs, ys = [p[0] for p in points], [p[1] for p in points]
    cx, cy = sum(xs) / len(xs), sum(ys) / len(ys)
    bx, by = (min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2
    anchor = min(range(len(points)), key=lambda i: (points[i][0] - cx) ** 2 + (points[i][1] - cy) ** 2)
    cands = [(i, (p[0], p[1])) for i, p in enumerate(points)] + [(anchor, (cx, cy)), (anchor, (bx, by))]
    best = None
    for a, (x, y) in cands:
        st = (x, y, points[a][2])
        worst = max(dist3(st, p) for p in points)
        if worst <= radius - margin:
            return a, st, worst
        if best is None or worst < best[2]:
            best = (a, st, worst)
    return best


class Proof:
    def __init__(self, nvs: List[Any], sleep: Callable[[float], None], args: argparse.Namespace, log=print):
        self.nvs = nvs
        self.sleep = sleep
        self.a = args
        self.log = log
        self.failures: List[str] = []
        self.passes = 0

    def check(self, ok: bool, what: str, seen: Any = None) -> bool:
        if ok:
            self.passes += 1
            self.log(f"  PASS {what}")
        else:
            self.failures.append(what)
            self.log(f"  FAIL {what}" + (f"  (saw {seen})" if seen is not None else ""))
        return ok

    # ---- helpers ----
    def status(self, nv) -> Dict[str, Any]:
        return nv.call("intents.status", {})

    def detail(self, nv) -> Dict[str, Any]:
        return self.status(nv).get("service", {}).get("detail", {}) or {}

    def host_stat(self, path: str) -> int:
        d: Any = self.detail(self.nvs[0])
        for k in path.split("."):
            d = d.get(k, 0) if isinstance(d, dict) else 0
        return int(d or 0)

    def activate(self, nv, ref: str) -> None:
        nv.call("activate.ref", {"ref": ref})

    def item_count(self, nv) -> float:
        return nv.eval(f"player.GetItemCount {self.a.item_base}")

    # ---- steps ----
    def _spots(self, nv) -> Dict[str, Any]:
        """Where the three references and this instance's player are, as THIS instance sees them."""
        out = {}
        for name, ref in (("door", self.a.door), ("item", self.a.item), ("container", self.a.container), ("player", PLAYER_INFO_REF)):
            i = nv.call("ref.info", {"ref": info_ref(nv, ref)})
            out[name] = (float(i["x"]), float(i["y"]), float(i["z"]))
        return out

    def _misplaced(self) -> List[str]:
        bad = []
        for idx, nv in enumerate(self.nvs):
            try:
                spots = self._spots(nv)
            except Exception as e:  # noqa: BLE001  (a reference not loaded on that instance, a bridge error)
                if type(e).__name__ in ("ConnectionLost", "GameCrashed"):
                    raise
                bad.append(f"instance {idx}: cannot read a position ({e})")
                continue
            for name in ("door", "item", "container"):
                d = dist3(spots["player"], spots[name])
                if d > self.a.radius:
                    bad.append(f"instance {idx} is {d:.0f} units from the {name} (limit {self.a.radius:g})")
        return bad

    def step_position(self) -> bool:
        """Step 0, no human: reads where the three references are on the host, picks one standing point near all of them and puts every player
        there (player.MoveTo the anchor reference, then player.SetPos X / Y), retrying, then verifies on every instance."""
        self.log("0 position")
        host = self.nvs[0]
        names = ("door", "item", "container")
        refs = (self.a.door, self.a.item, self.a.container)
        try:
            pts = []
            for r in refs:
                i = host.call("ref.info", {"ref": info_ref(host, r)})
                pts.append((float(i["x"]), float(i["y"]), float(i["z"])))
        except Exception as e:  # noqa: BLE001
            if type(e).__name__ in ("ConnectionLost", "GameCrashed"):
                raise
            return self.check(False, "the host can read the door, the item and the container (ref.info)", str(e))
        anchor, stand, worst = standing_point(pts, self.a.radius)
        if not self.check(worst <= self.a.radius, f"the door, the item and the container are close enough together: one standing point is within {self.a.radius:g} of all three",
                          f"the best standing point is {worst:.0f} units from the farthest one of {list(zip(names, [tuple(round(c) for c in p) for p in pts]))}; "
                          "pick references from one room, or pass --no-position"):
            return False
        try:
            anchor_hex = runtime_hex(host, refs[anchor])
        except PositionError as e:
            return self.check(False, "the anchor reference has a runtime id", str(e))
        bad: List[str] = []
        for attempt in range(1, POSITION_TRIES + 1):
            for idx, nv in enumerate(self.nvs):
                nv.exec(f"player.MoveTo {anchor_hex}")
                self.sleep(0.5)
                for axis, v in (("X", stand[0] + SPREAD_UNITS * idx), ("Y", stand[1])):
                    nv.exec(f"player.SetPos {axis} {v:.2f}")
            self.sleep(SETTLE_S)
            bad = self._misplaced()
            if not bad:
                break
            self.log(f"  info: try {attempt}: {'; '.join(bad)}")
        return self.check(not bad, f"all {len(self.nvs)} players stand within {self.a.radius:g} units of the door, the item and the container", "; ".join(bad) if bad else None)

    def step_module(self) -> None:
        self.log("1 module")
        for i, nv in enumerate(self.nvs):
            s = self.status(nv)
            self.check(bool(s.get("enabled")), f"instance {i}: [intents] enable=1", s.get("note"))
            self.check(bool(s.get("pinsOk")), f"instance {i}: engine pins match this exe", s.get("pinsOk"))
            self.check(bool(s.get("service", {}).get("up")), f"instance {i}: the intents session is up", s.get("service"))
            self.check(bool(s.get("canSaveHook")), f"instance {i}: the CanSaveNow hook is installed", s.get("canSaveHook"))
        self.check(self.detail(self.nvs[0]).get("role") == "host", "instance 0 is the host", self.detail(self.nvs[0]).get("role"))
        self.check(all(self.detail(nv).get("role") == "guest" for nv in self.nvs[1:]), "the others are guests")

    def step_activate(self) -> None:
        self.log("2 activate")
        before = self.host_stat("executed")
        puppet0 = self.host_stat("actedAs.puppet")
        stand0 = self.host_stat("actedAs.standIn")
        states0 = [nv.eval("GetOpenState", ref=self.a.door) for nv in self.nvs]
        self.activate(self.nvs[1], self.a.door)
        self.sleep(SETTLE_S)
        self.check(self.host_stat("executed") == before + 1, "the host ran the activation exactly once", self.host_stat("executed") - before)
        puppet = self.host_stat("actedAs.puppet") - puppet0
        stand = self.host_stat("actedAs.standIn") - stand0
        self.check(puppet == 1, "it ran as the guest's puppet" + (" (stand-in was used: no puppet for that guest)" if stand else ""), {"puppet": puppet, "standIn": stand})
        states = [nv.eval("GetOpenState", ref=self.a.door) for nv in self.nvs]
        self.check(len(set(states)) == 1 and states != states0, "the door state changed and is the same on every instance", (states0, states))
        self.check(self.detail(self.nvs[1]).get("verdicts", 0) >= 1, "the guest got its verdict", self.detail(self.nvs[1]).get("verdicts"))

    def step_pickup(self) -> None:
        self.log("3 pickup")
        before = [self.item_count(nv) for nv in self.nvs]
        won0, lost0 = self.host_stat("claims.won"), self.host_stat("claims.lost")
        self.activate(self.nvs[1], self.a.item)
        self.activate(self.nvs[2], self.a.item)
        self.sleep(SETTLE_S + 1.5)
        after = [self.item_count(nv) for nv in self.nvs]
        gained = [a - b for a, b in zip(after, before)]
        self.check(sorted(gained[1:]) == [0.0, gained[1] + gained[2]] and (gained[1] > 0) != (gained[2] > 0), "exactly one guest gained the item", gained)
        self.check(gained[0] == 0.0, "the host gained nothing", gained[0])
        self.check(self.host_stat("claims.won") == won0 + 1 and self.host_stat("claims.lost") == lost0 + 1, "one claim won, one lost",
                   (self.host_stat("claims.won") - won0, self.host_stat("claims.lost") - lost0))
        gone = [nv.eval("GetDisabled", ref=self.a.item) for nv in self.nvs]
        self.check(all(g == 1 for g in gone), "the item is gone on every instance", gone)

    def step_container(self) -> None:
        self.log("4 container")
        busy0 = self.host_stat("locks.busy")
        self.activate(self.nvs[1], self.a.container)
        self.sleep(SETTLE_S)
        self.check(self.host_stat("locks.held") == 1, "guest 1 holds the container lock", self.host_stat("locks.held"))
        self.activate(self.nvs[2], self.a.container)
        self.sleep(SETTLE_S)
        self.check(self.host_stat("locks.busy") == busy0 + 1, "guest 2 was told it is in use", self.host_stat("locks.busy") - busy0)
        self.check(self.detail(self.nvs[2]).get("rejected", {}).get("Busy", 0) >= 1, "guest 2 saw the Busy verdict", self.detail(self.nvs[2]).get("rejected"))
        self.nvs[1].exec(self.a.close_menu)
        self.sleep(SETTLE_S + 1.0)
        self.check(self.host_stat("locks.held") == 0, "closing the menu released the lock", self.host_stat("locks.held"))
        self.check(self.detail(self.nvs[1]).get("closesSent", 0) >= 1, "guest 1 sent the close", self.detail(self.nvs[1]).get("closesSent"))

    def step_terminal(self) -> None:
        self.log("5 terminal")
        if not self.a.terminal:
            self.log("  (skipped: pass --terminal REF)")
            return
        before = self.host_stat("executed")
        self.nvs[1].call("intents.submit", {"kind": "terminal", "ref": self.a.terminal, "path": [self.a.entry]})
        self.sleep(SETTLE_S)
        rej = self.host_stat("rejected.Unsupported") + self.host_stat("rejected.Failed")
        ran = self.host_stat("executed") - before
        self.check(ran + rej >= 1, "the host answered the terminal intent", {"executed": ran, "refused": rej})
        self.log(f"  info: terminal entry {'ran on the host' if ran else 'was refused (see rejected in intents.status); the hook path is unproven'}")

    def step_trigger(self) -> None:
        self.log("6 trigger")
        if not self.a.trigger:
            self.log("  (skipped: pass --trigger REF)")
            return
        before = self.host_stat("byKind.TriggerEnter")
        self.nvs[1].call("intents.submit", {"kind": "trigger", "ref": self.a.trigger})
        self.nvs[1].call("intents.submit", {"kind": "trigger", "ref": self.a.trigger})
        self.sleep(SETTLE_S)
        self.check(self.host_stat("byKind.TriggerEnter") == before + 1, "OnTriggerEnter ran once; the repeat inside the cooldown did not", self.host_stat("byKind.TriggerEnter") - before)

    def step_stay(self) -> None:
        self.log("6b stay")
        if not self.a.trigger:
            self.log("  (skipped: pass --trigger REF)")
            return
        self.nvs[1].call("intents.submit", {"kind": "stay", "ref": self.a.trigger})
        self.sleep(SETTLE_S)
        self.check(self.host_stat("stay.present") >= 1, "the host holds guest 1's presence in the volume", self.host_stat("stay.present"))
        r0 = self.host_stat("stay.raises")
        self.sleep(1.0)
        self.check(self.host_stat("stay.raises") - r0 >= 10, "the host raised OnTrigger by itself, many times a second", self.host_stat("stay.raises") - r0)
        self.nvs[1].call("intents.submit", {"kind": "triggerleave", "ref": self.a.trigger})
        self.sleep(SETTLE_S)
        self.check(self.host_stat("stay.present") == 0, "TriggerLeave ended the presence", self.host_stat("stay.present"))
        r1 = self.host_stat("stay.raises")
        self.sleep(1.0)
        self.check(self.host_stat("stay.raises") == r1, "no OnTrigger after the leave", self.host_stat("stay.raises") - r1)

    def step_note(self) -> None:
        self.log("8 note")
        if not self.a.note:
            self.log("  (skipped: pass --note REF --note-base NOTEFORM)")
            return
        n0 = self.host_stat("notes")
        self.activate(self.nvs[1], self.a.note)
        self.sleep(SETTLE_S + 1.0)
        self.check(self.host_stat("notes") == n0 + 1, "the host granted the note to the journal once", self.host_stat("notes") - n0)
        has = [nv.eval(f"player.GetHasNote {self.a.note_base}") for nv in self.nvs]
        self.check(has[0] == 1, "GetHasNote is true on the host (VMQ01 polls it there)", has)
        self.check(all(h == 1 for h in has), "the note is in every journal (shared)", has)

    def step_combat(self) -> None:
        self.log("9 combat")
        if not self.a.npc:
            self.log("  (skipped: pass --npc REF)")
            return
        c0 = self.host_stat("byKind.StartCombat")
        self.nvs[1].call("intents.submit", {"kind": "combat", "ref": self.a.npc})
        self.sleep(SETTLE_S)
        self.check(self.host_stat("byKind.StartCombat") == c0 + 1, "the host ran the actor's OnStartCombat block once", self.host_stat("byKind.StartCombat") - c0)

    def step_stash(self) -> None:
        self.log("10 stash")
        if not self.a.stash:
            self.log("  (skipped: pass --stash)")
            return
        self.check(self.host_stat("stash.containers") >= 5, "the shared gear containers of nvcoop_intents.json are loaded", self.host_stat("stash.containers"))
        self.check(self.host_stat("stash.failed") == 0, "no stash swap failed", self.host_stat("stash.failed"))

    def step_save_gate(self) -> None:
        self.log("7 save gate")
        host = self.nvs[0]
        allowed = [nv.call("intents.cansave", {}).get("allowed") for nv in self.nvs]
        self.log(f"  info: CanSaveNow before = {allowed}")
        host.call("coop.inject", {"txn": {"cause": {"kind": "QuestResult", "source": self.a.door}, "origin": 0, "effects": [{"type": "SaveSuppress", "on": True}]}})
        self.sleep(SETTLE_S)
        now = [nv.call("intents.cansave", {}).get("allowed") for nv in self.nvs]
        self.check(all(v is False for v in now), "CanSaveNow is refused on every instance while SaveSuppress is on", now)
        blocked = [self.status(nv).get("saveGate", {}).get("blocked", 0) for nv in self.nvs]
        self.check(all(b >= 1 for b in blocked), "every gate counted the refusal", blocked)
        host.call("coop.inject", {"txn": {"cause": {"kind": "QuestResult", "source": self.a.door}, "origin": 0, "effects": [{"type": "SaveSuppress", "on": False}]}})
        self.sleep(SETTLE_S)
        back = [nv.call("intents.cansave", {}).get("allowed") for nv in self.nvs]
        self.check(back == allowed, "with SaveSuppress off the game's own answer is back", (allowed, back))

    def run(self) -> int:
        if getattr(self.a, "position", False) and not self.step_position():
            self.log(f"{self.passes} checks passed, {len(self.failures)} failed")
            for f in self.failures:
                self.log(f"  FAILED: {f}")
            return 1
        self.step_module()
        self.step_activate()
        self.step_pickup()
        self.step_container()
        self.step_terminal()
        self.step_trigger()
        self.step_stay()
        self.step_note()
        self.step_combat()
        self.step_stash()
        self.step_save_gate()
        self.log(f"{self.passes} checks passed, {len(self.failures)} failed")
        for f in self.failures:
            self.log(f"  FAILED: {f}")
        return 0 if not self.failures else 1


# ---- a fake world for --selftest ---------------------------------------------------------------------------------------------------------
class FakeWorld:
    """Three machines in one process. `defect` switches one behaviour off: claim | lock | gate | puppet | release | once, or breaks the
    positioning: stuck (instance 2 ignores MoveTo / SetPos, so that player is left out of range) | far (the door is 3000 units away from the rest).
    An activation from a player farther than 400 units from the reference does nothing, like the host's range check."""

    DOOR, ITEM, CONT = "FalloutNV.esm:103E61", "FalloutNV.esm:0F8B6C", "FalloutNV.esm:103E37"
    NOTE = "FalloutNV.esm:1048AA"

    def __init__(self, defect: str = ""):
        self.defect = defect
        self.t = 0.0
        self.door_open = 0
        self.item_gone = False
        self.items = [0.0, 0.0, 0.0]
        self.lock_holder: Optional[int] = None
        self.suppress = False
        self.menu_open = {1: False, 2: False}
        self.host = {"executed": 0, "actedAs": {"puppet": 0, "standIn": 0}, "claims": {"won": 0, "lost": 0}, "locks": {"busy": 0, "held": 0},
                     "byKind": {}, "rejected": {}}
        self.guest = {1: {"verdicts": 0, "rejected": {}, "closesSent": 0}, 2: {"verdicts": 0, "rejected": {}, "closesSent": 0}}
        self.gate = [0, 0, 0]
        self.stay_present = 0
        self.note_held = [0, 0, 0]
        self.host.update({"stay": {"present": 0, "raises": 0}, "notes": 0, "stash": {"containers": 5, "failed": 0}})
        self.trigger_at = -99.0
        self.refpos = {self.DOOR: [3000.0 if defect == "far" else 1000.0, 1000.0, 50.0], self.ITEM: [1100.0, 1000.0, 20.0], self.CONT: [1000.0, 1150.0, 30.0]}
        self.ppos = [[-5000.0 + i * 10, -5000.0, 0.0] for i in range(3)]      # every player starts far away from the references

    def near(self, idx: int, ref: str) -> bool:
        a, b = self.ppos[idx], self.refpos[ref]
        return sum((x - y) ** 2 for x, y in zip(a, b)) ** 0.5 <= 400.0

    def sleep(self, s: float) -> None:
        self.t += s
        if self.stay_present and self.defect != "stay":
            self.host["stay"]["raises"] += int(s * 60)

    def activate(self, idx: int, ref: str) -> None:
        h = self.host
        if ref == self.NOTE:
            h["notes"] += 1
            if self.defect != "note":
                self.note_held = [1, 1, 1]
            return
        if not self.near(idx, {"door": self.DOOR, "item": self.ITEM, "container": self.CONT}[ref]):
            return
        if ref == "door":
            runs = 2 if self.defect == "once" else 1
            for _ in range(runs):
                h["executed"] += 1
                h["actedAs"]["standIn" if self.defect == "puppet" else "puppet"] += 1
            self.door_open = 1 - self.door_open
            self.guest[idx]["verdicts"] += 1
        elif ref == "item":
            if not self.item_gone or self.defect == "claim":
                self.items[idx] += 10
                self.item_gone = True
                h["claims"]["won"] += 1
            else:
                h["claims"]["lost"] += 1
                self.guest[idx]["rejected"]["Taken"] = self.guest[idx]["rejected"].get("Taken", 0) + 1
            h["executed"] += 1
        elif ref == "container":
            if self.lock_holder not in (None, idx) and self.defect != "lock":
                h["locks"]["busy"] += 1
                self.guest[idx]["rejected"]["Busy"] = self.guest[idx]["rejected"].get("Busy", 0) + 1
            else:
                self.lock_holder = idx
                self.menu_open[idx] = True
            h["locks"]["held"] = 0 if self.lock_holder is None else 1

    def close_menu(self, idx: int) -> None:
        self.menu_open[idx] = False
        if self.lock_holder == idx and self.defect != "release":
            self.lock_holder = None
        self.guest[idx]["closesSent"] += 1
        self.host["locks"]["held"] = 0 if self.lock_holder is None else 1

    def cansave(self) -> bool:
        return not (self.suppress and self.defect != "gate")


class FakeNv:
    def __init__(self, w: FakeWorld, idx: int):
        self.w = w
        self.i = idx

    def call(self, method: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        p = params or {}
        w = self.w
        if method == "intents.status":
            detail = dict(w.host, role="host") if self.i == 0 else dict(w.guest[self.i], role="guest")
            return {"enabled": True, "pinsOk": True, "canSaveHook": True, "service": {"up": True, "detail": detail},
                    "saveGate": {"blocked": 1 if w.suppress and w.defect != "gate" else 0}}
        if method == "activate.ref":
            w.activate(self.i, w.NOTE if p["ref"] == w.NOTE else "door" if p["ref"] == w.DOOR else "item" if p["ref"] == w.ITEM else "container")
            return {}
        if method == "ref.info":
            if p["ref"] == PLAYER_INFO_REF:
                pos = w.ppos[self.i]
            elif p["ref"] in w.refpos:
                pos = w.refpos[p["ref"]]
            else:
                raise KeyError("reference not loaded")
            return {"x": pos[0], "y": pos[1], "z": pos[2], "cell": 7}
        if method == "intents.submit":
            if p["kind"] == "stay":
                w.stay_present = 1
                w.host["stay"]["present"] = 1
            elif p["kind"] == "triggerleave":
                if w.defect != "leave":
                    w.stay_present = 0
                    w.host["stay"]["present"] = 0
            elif p["kind"] == "combat":
                if w.defect != "combat":
                    w.host["byKind"]["StartCombat"] = w.host["byKind"].get("StartCombat", 0) + 1
            elif p["kind"] == "trigger":
                if w.t - w.trigger_at > 5:
                    w.host["byKind"]["TriggerEnter"] = w.host["byKind"].get("TriggerEnter", 0) + 1
                    w.host["executed"] += 1
                w.trigger_at = w.t
            else:
                w.host["executed"] += 1
            return {"id": 1}
        if method == "intents.cansave":
            return {"allowed": w.cansave()}
        if method == "coop.inject":
            w.suppress = bool(p["txn"]["effects"][0]["on"])
            return {}
        raise KeyError(method)

    def plugins(self) -> List[str]:
        return ["FalloutNV.esm"]

    def exec(self, cmd: str, ref: Optional[str] = None) -> Dict[str, Any]:
        w = self.w
        if cmd == "CloseAllMenus":
            w.close_menu(self.i)
        elif cmd.startswith("player.MoveTo ") and not (w.defect == "stuck" and self.i == 2):
            for name, pos in w.refpos.items():
                if int(cmd.split()[1], 16) == int(name.split(":")[1], 16):
                    w.ppos[self.i] = list(pos)
        elif cmd.startswith("player.SetPos ") and not (w.defect == "stuck" and self.i == 2):
            _c, axis, v = cmd.split()
            w.ppos[self.i]["XYZ".index(axis)] = float(v)
        return {"ok": True, "output": ""}

    def eval(self, expr: str, ref: Optional[str] = None) -> float:
        w = self.w
        if expr.startswith("GetOpenState"):
            return float(w.door_open)
        if expr.startswith("GetDisabled"):
            return 1.0 if w.item_gone else 0.0
        if expr.startswith("player.GetHasNote"):
            return float(w.note_held[self.i])
        if expr.startswith("player.GetItemCount"):
            return w.items[self.i]
        raise KeyError(expr)


def selftest() -> int:
    ok = True
    # (defect, position step on, substring the log must show for a failing run)
    cases = [("", True, ""), ("claim", True, ""), ("lock", True, ""), ("gate", True, ""), ("puppet", True, ""), ("release", True, ""), ("once", True, ""),
             ("stuck", True, "instance 2 is"),          # twin: a player left out of range is detected, and named
             ("stay", True, "raised OnTrigger"),        # twin: the host never re-raises OnTrigger
             ("leave", True, "ended the presence"),     # twin: a Leave that does not end the presence
             ("note", True, "GetHasNote"),              # twin: the note never reaches the host's journal
             ("combat", True, "OnStartCombat"),         # twin: the actor's block does not run
             ("far", True, "close enough together"),    # twin: references too far apart are reported, not walked into
             ("", False, "")]                           # twin: nobody positions the players (--no-position) and they start far away: every intent is ignored
    for defect, position, needle in cases:
        w = FakeWorld(defect)
        args = argparse.Namespace(door=w.DOOR, item=w.ITEM, item_base="0xF", container=w.CONT, terminal="T", entry=0, trigger="G", note=w.NOTE, note_base="0xB", npc="NPC", stash=True, close_menu="CloseAllMenus",
                                  position=position, radius=NEAR_UNITS)
        lines: List[str] = []
        p = Proof([FakeNv(w, i) for i in range(3)], w.sleep, args, log=lines.append)
        rc = p.run()
        expect_ok = defect == "" and position
        passed = rc == 0
        label = (defect or "none") + ("" if position else "+no-position")
        print(f"selftest defect={label}: script said {'PASS' if passed else 'FAIL'} (expected {'PASS' if expect_ok else 'FAIL'}), {p.passes} checks, {len(p.failures)} failed")
        if passed != expect_ok or (needle and not any(needle in x for x in lines)):
            ok = False
            print(chr(10).join(lines[-30:]))
    # the standing point itself: a tight cluster is stood at one of the references, a spread-out one fits in the middle, an impossible one is over the limit
    a, st, worst = standing_point([(0, 0, 0), (100, 0, 0), (0, 100, 0)])
    if not (worst <= NEAR_UNITS - MARGIN_UNITS and (st[0], st[1]) == (0, 0)):
        ok = False
        print("standing_point: a tight cluster should stand at a reference", a, st, worst)
    if standing_point([(0, 0, 0), (600, 0, 0), (300, 500, 0)])[2] > NEAR_UNITS:
        ok = False
        print("standing_point: a 600 unit triangle fits in 400 around the middle")
    if standing_point([(0, 0, 0), (1000, 0, 0), (0, 1000, 0)])[2] <= NEAR_UNITS:
        ok = False
        print("standing_point: references 1000 apart must not fit")
    print("ALL OK" if ok else "SELFTEST FAILED")
    return 0 if ok else 1


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--instances", type=int, nargs="+", default=[0, 1, 2], help="bridge instance numbers; the first is the host")
    ap.add_argument("--door", help="a door (or lever) near the players")
    ap.add_argument("--item", help="a world item near the players")
    ap.add_argument("--item-base", help="the base form of --item, as a hex form id: the script counts it in the guests' inventories")
    ap.add_argument("--container", help="a container near the players")
    ap.add_argument("--terminal", help="a terminal (optional)")
    ap.add_argument("--entry", type=int, default=0, help="terminal entry index at the top menu")
    ap.add_argument("--trigger", help="a trigger volume reference (optional)")
    ap.add_argument("--note", help="a note lying in the world near the players (optional)")
    ap.add_argument("--note-base", help="the NOTE form of --note, as a hex form id: GetHasNote is asked for it")
    ap.add_argument("--npc", help="an NPC that can start combat (optional)")
    ap.add_argument("--stash", action="store_true", help="check that the shared gear containers are loaded (optional)")
    ap.add_argument("--position", dest="position", action="store_true", default=True, help="(default) step 0: put every player within --radius of the door, the item and the container")
    ap.add_argument("--no-position", dest="position", action="store_false", help="skip step 0: the players already stand near the references")
    ap.add_argument("--radius", type=float, default=NEAR_UNITS, help="how close every player must be to each reference (units)")
    ap.add_argument("--close-menu", default="CloseAllMenus", help="console line that closes the open menu on a guest")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args(argv)
    if a.selftest:
        return selftest()
    missing = [n for n in ("door", "item", "item_base", "container") if not getattr(a, n)]
    if missing or len(a.instances) < 3:
        ap.error("needs --door --item --item-base --container and three instances (host + two guests)")
    sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "tools" / "runner"))
    from nvbridge import NvBridge  # noqa: E402
    nvs = [NvBridge.connect(instance=i, wait_s=10) for i in a.instances]
    try:
        return Proof(nvs, time.sleep, a).run()
    finally:
        for nv in nvs:
            nv.close()


if __name__ == "__main__":
    sys.exit(main())
