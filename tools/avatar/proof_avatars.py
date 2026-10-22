#!/usr/bin/env python3
"""In-game proof of the player avatars between TWO machines (roadmap step 3). WRITTEN, NEVER RUN against a real game: it needs two co-op game
instances. `--selftest` runs the analysis against a scripted fake game plus injected defects (every defect must be caught); that is what the gate runs.

    python -I tools/avatar/proof_avatars.py --host 0 --client 1 --enemy-ref 000XXXXX         # all phases
    python -I tools/avatar/proof_avatars.py --host 0 --client 1 --phases wiring,look,rates   # some phases
    python -I tools/avatar/proof_avatars.py --host 0 --client 1 --phases look --loadout off  # the client already holds the gear
    python -I tools/avatar/proof_avatars.py --selftest

Provision two instances first (the lead starts them; this tool never launches a game and never takes the game slot):
    python -I tools/runner/run.py provision --n 2 --ini coop.enable=1 --ini live.actors=1 --ini live.avatars=1 --ini live.combat=1 --ini avatar.enabled=1 \
        --ini dev.bots=1
  Host and client in one co-op session, both in an EXTERIOR with about 120 m of free ground along +X in front of the client (the rates phase walks the
  client away by teleport); ideally a non-default height / hair.
  The client's loadout (a weapon WITH a modification, and armor) is NOT a hand step any more: `--loadout auto` (the default, used when the look phase runs)
  gives it through bridge console lines: player.additem + player.EquipItem for the armor and the weapon, then the xNVSE function
  player.SetEquippedWeaponModFlags (NVSE Commands_Inventory.cpp, flags 0..7, one bit per mod slot) on the worn weapon, and reads every step back
  (player.GetEquipped, player.GetEquippedWeaponModFlags, then avatar.look). `--loadout off` skips it (the client must then hold the gear already).
  Defaults (DEFAULT_LOADOUT below) are pinned vanilla ids; override with --armor / --weapon / --weapon-mod / --mod-flags.

Phases (default: all), each one judged by pure functions below:
  wiring     both machines run the avatar module, list the other player in the roster, have a puppet ref for it, accepted snapshots, nothing forged.
  look       (first: the loadout above is given and read back, and the client is checked to hold a modified weapon and armor.)
             the Look the client sent (selfLook, avatar.look) equals what the host stored (roster[].lookInfo): race, sex, hair, eyes, hair colour, height,
             weight, face coefficients, worn items and weapon modification flags. Then the PUPPET on the host: wears the same items, and the weapon
             modification flags READ BACK from the engine equal the wanted ones (this is the check of the EnableWeaponMod hypothesis, reported on its own
             line); SetScale was sent when the height differs; no console exception, no foreign reference refused.
  rates      the host's receive rate of the client's stream: 15..24 Hz while the client stands near (tier near), 3.5..7.5 Hz after the client is moved
             about 60 m away (tier far), near again after it comes back.
  lifecycle  the client enters an interior (--interior): the host parks the puppet (no ref) but keeps the roster entry; back outside: one puppet again;
             the client loads a save (--save): the host's puppet stays, the client's puppets come back; the avatar API (avatar.api) round-trips
             ref -> slot -> ref and acting refs every time; no orphan (tagged == puppets) on either machine at the end.
  hits       the host reports a hit on the puppet of the client through the router (avatar.hit, needs --enemy-ref, an enemy the host owns): route
             forwarded, action clamp, the host's puppet survives, and the CLIENT's combat counters (hitsReceived / hitsApplied / damageApplied) go up by one hit.

Exit code: 0 all passed, 1 a check failed, 2 usage / cannot reach the game, 3 a phase could not be judged (missing input) and nothing else failed.
Every threshold is a first guess; tune from one clean run.
"""
from __future__ import annotations

import argparse
import copy
import json
import os
import re
import sys
import time
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))

Check = Tuple[str, bool, str]
Call = Callable[[str, Optional[dict]], Any]

PHASES = ("wiring", "look", "rates", "lifecycle", "hits")
NEAR_HZ = (15.0, 24.0)
FAR_HZ = (3.5, 7.5)
RATE_WINDOW_S = 10.0
SETTLE_S = 4.0
HIT_DAMAGE = 5
FAR_OFFSET = 4200.0      # game units (60 m) along +X
PARK_WAIT_S = 4.0
LOADOUT_WAIT_S = 12.0

# The scripted loadout of the CLIENT (the look phase needs a weapon with a modification and worn armor). Every id was taken from the game data of this
# repo, out/questdata/forms.json.gz (keys "Plugin.esm:HEX", fields sig / edid), not guessed:
#   WEAP FalloutNV.esm:0CD539 edid WeapNVTrailCarbine        the Trail Carbine (a vanilla weapon that has a scope mod)
#   IMOD FalloutNV.esm:0EEA70 edid ModNVTrailCarbineScope    its scope; the dump holds only 9 IMOD forms, this is the one whose name pairs with a WEAP there
#   ARMO FalloutNV.esm:1264FE edid ArmorLeatherReinforced    reinforced leather armor
# Why flags and not "attach the IMOD": the game has no console function that attaches an IMOD to an inventory item (the vanilla command table,
# plugin/hooks/tools/re/command_table.json, ends at 0x1CD and has only EnableWeaponMod; the xNVSE source in third_party/NVSE has no AttachModToInventoryItem).
# What the avatar layer syncs is the ExtraWeaponModFlags byte of the WORN weapon, and xNVSE writes exactly that byte with SetEquippedWeaponModFlags
# (bit n-1 = mod slot n), so slot 1 (= the scope on the Trail Carbine) is flag 1. "mod" is kept for the report and for a later real IMOD attach.
DEFAULT_LOADOUT: Dict[str, Any] = {"armor": "FalloutNV.esm:1264FE", "weapon": "FalloutNV.esm:0CD539", "mod": "FalloutNV.esm:0EEA70", "mod_flags": 1}


# ------------------------------------------------------------------------------------------------------------------------------------------
# pure checks
# ------------------------------------------------------------------------------------------------------------------------------------------
def roster_entry(live: Dict[str, Any], slot: int) -> Optional[Dict[str, Any]]:
    for r in live.get("avatars", {}).get("roster", []):
        if r.get("slot") == slot:
            return r
    return None


def check_wiring(host: Dict[str, Any], client: Dict[str, Any]) -> List[Check]:
    out: List[Check] = []

    def add(name: str, ok: bool, detail: str = "") -> None:
        out.append((f"wiring: {name}", bool(ok), detail))

    add("avatar module runs on both machines", bool(host.get("avatarModule", {}).get("running")) and bool(client.get("avatarModule", {}).get("running")),
        f"{host.get('avatarModule')} / {client.get('avatarModule')}")
    ha, ca = host.get("avatars", {}), client.get("avatars", {})
    cslot = ca.get("self")
    add("client has a slot", isinstance(cslot, int) and cslot not in (0, 255), f"self {cslot}")
    he, ce = roster_entry(host, cslot) if isinstance(cslot, int) else None, roster_entry(client, 0)
    add("host has a puppet ref for the client", bool(he and he.get("ref")), str(he))
    add("client has a puppet ref for the host", bool(ce and ce.get("ref")), str(ce))
    add("snapshots accepted on both machines", ha.get("accepted", 0) > 0 and ca.get("accepted", 0) > 0, f"{ha.get('accepted')} / {ca.get('accepted')}")
    add("nothing forged, nothing rejected as junk", ha.get("forged", 0) == 0 and ca.get("forged", 0) == 0, f"forged {ha.get('forged')}/{ca.get('forged')}")
    add("both published local snapshots", ha.get("localSnapshots", 0) > 0 and ca.get("localSnapshots", 0) > 0)
    return out


LOOK_KEYS = ("race", "female", "hair", "eyes", "hairColor", "height", "weight", "faceCoeffs")


def norm_equip(equip: Sequence[Dict[str, Any]]) -> List[Tuple[int, bool, int]]:
    return sorted((int(e.get("form", 0)), bool(e.get("weapon")), int(e.get("mods", 0))) for e in equip)


def check_look(sent: Optional[Dict[str, Any]], stored: Optional[Dict[str, Any]], who: str) -> List[Check]:
    """`sent` = the client's selfLook, `stored` = the host's roster[].lookInfo for the client."""
    out: List[Check] = []
    if not sent or not stored:
        return [(f"look: {who} has both sides of the look", False, f"sent={bool(sent)} stored={bool(stored)}")]
    for k in LOOK_KEYS:
        out.append((f"look: {who} {k} arrived as sent", sent.get(k) == stored.get(k), f"sent {sent.get(k)!r} stored {stored.get(k)!r}"))
    out.append((f"look: {who} worn items and weapon mods arrived as sent", norm_equip(sent.get("equip", [])) == norm_equip(stored.get("equip", [])),
                f"sent {norm_equip(sent.get('equip', []))} stored {norm_equip(stored.get('equip', []))}"))
    return out


def check_gear(source: Dict[str, Any], puppet: Dict[str, Any], engine: Dict[str, Any], expect_scale: bool) -> List[Check]:
    """`source` = avatar.look on the client, `puppet` = avatar.puppetlook on the host, `engine` = avatar.status["engine"] on the host."""
    out: List[Check] = []
    want = {int(str(w["form"]), 16): w for w in source.get("worn", [])}
    have = {int(str(w["form"]), 16): w for w in puppet.get("worn", [])}
    out.append(("look: the puppet exists", bool(puppet.get("ref")) and int(str(puppet.get("ref", "0")), 16) != 0, str(puppet.get("ref"))))
    missing = sorted(set(want) - set(have))
    out.append(("look: the puppet wears every item the player wears", not missing, f"missing {[hex(m) for m in missing]}"))
    out.append(("look: the held weapon is the same", all(bool(have[f].get("weapon")) == bool(want[f].get("weapon")) for f in want if f in have), ""))
    wmods = {f: int(w.get("mods", 0)) for f, w in want.items() if w.get("weapon") and int(w.get("mods", 0))}
    if wmods:
        got = {f: int(have[f].get("mods", 0)) for f in wmods if f in have}
        known = all(have[f].get("modsKnown") for f in wmods if f in have)
        out.append(("look: WEAPON MODS read back from the engine equal the wanted ones (the EnableWeaponMod hypothesis)", known and got == wmods,
                    f"wanted {wmods} read back {got} known={known} modLines={engine.get('modLines')}"))
        out.append(("look: EnableWeaponMod lines were sent", engine.get("modLines", 0) >= 1, f"modLines {engine.get('modLines')}"))
    if expect_scale:
        out.append(("look: SetScale was sent for the player's height", engine.get("scaleApplied", 0) >= 1, f"scaleApplied {engine.get('scaleApplied')}"))
    out.append(("look: no console exception, no foreign reference refused", engine.get("linesException", 0) == 0 and engine.get("foreignRefused", 0) == 0,
                f"exceptions {engine.get('linesException')} foreign {engine.get('foreignRefused')}"))
    return out


def form_ints(worn: Sequence[Dict[str, Any]]) -> Dict[int, Dict[str, Any]]:
    out: Dict[int, Dict[str, Any]] = {}
    for w in worn:
        try:
            out[int(str(w.get("form", "0")), 16)] = w
        except ValueError:
            pass
    return out


def check_loadout_worn(src: Dict[str, Any], armor: int, weapon: int, flags: int) -> List[Check]:
    """`src` = avatar.look on the client: it must hold the modified weapon and the armor the look phase is about (so the weapon-mod check of
    check_gear cannot pass by having nothing to compare). armor / weapon are runtime form ids, flags the wanted ExtraWeaponModFlags."""
    worn = form_ints(src.get("worn", []))
    w = worn.get(weapon)
    out: List[Check] = [("look: the client wears the loadout armor", armor in worn and not worn[armor].get("weapon"), f"worn {[hex(f) for f in worn]}")]
    out.append(("look: the client holds the loadout weapon", bool(w and w.get("weapon")), f"worn {[hex(f) for f in worn]}"))
    got = int(w.get("mods", 0)) if w else 0
    out.append(("look: the client's weapon has the modification (avatar.look mods)", bool(w) and got == flags and got != 0, f"mods {got} wanted {flags}"))
    return out


def check_loadout_any(src: Dict[str, Any]) -> List[Check]:
    """For --loadout off: the client must already hold SOME modified weapon and SOME armor."""
    worn = list(src.get("worn", []))
    gun = [x for x in worn if x.get("weapon") and int(x.get("mods", 0))]
    arm = [x for x in worn if not x.get("weapon")]
    return [("look: the client holds a weapon with a modification", bool(gun), f"worn {worn}"),
            ("look: the client wears armor", bool(arm), f"worn {worn}")]


def rate_hz(a: Dict[str, Any], b: Dict[str, Any], slot: int, dt: float) -> Optional[float]:
    ea, eb = roster_entry(a, slot), roster_entry(b, slot)
    if not ea or not eb or dt <= 0:
        return None
    return (eb.get("rx", 0) - ea.get("rx", 0)) / dt


def check_rate(label: str, hz: Optional[float], band: Tuple[float, float], tier: Optional[str], want_tier: str) -> List[Check]:
    ok_rate = hz is not None and band[0] <= hz <= band[1]
    return [
        (f"rates: {label} stream rate {band[0]}..{band[1]} Hz", ok_rate, f"measured {hz if hz is None else round(hz, 1)} Hz"),
        (f"rates: {label} tier is {want_tier}", tier == want_tier, f"tier {tier!r}"),
    ]


def api_ok(api: Dict[str, Any], slot: int, expect_present: bool) -> List[Check]:
    ps = {p.get("slot"): p for p in api.get("puppets", [])}
    p = ps.get(slot)
    out: List[Check] = [("api: avatar.api is registered", bool(api.get("registered")), "")]
    if expect_present:
        out.append((f"api: slot {slot} has a puppet", p is not None, str(sorted(ps))))
        out.append((f"api: ref -> slot -> ref round-trips for slot {slot}", bool(p and p.get("roundTrip")), str(p)))
        out.append((f"api: the acting ref of slot {slot} is its puppet", bool(p and p.get("acting") == p.get("ref")), str(p)))
    else:
        out.append((f"api: slot {slot} has NO puppet (parked)", p is None, str(sorted(ps))))
    return out


def check_orphans(status: Dict[str, Any], who: str) -> List[Check]:
    puppets = [p for p in status.get("puppets", []) if p.get("ref") not in (None, "0x00000000")]
    tagged = status.get("tagged")
    return [(f"lifecycle: {who} has no orphan puppet (tagged == puppets)", tagged == len(puppets), f"tagged {tagged} puppets {len(puppets)}")]


def check_hit(result: Dict[str, Any], before: Dict[str, Any], after: Dict[str, Any], host_after: Dict[str, Any], expect_dmg: int) -> List[Check]:
    cb, ca = before.get("stats", {}), after.get("stats", {})
    out: List[Check] = []
    out.append(("hits: the router forwarded the hit", result.get("route") == "forwarded", str(result)))
    out.append(("hits: the local puppet is clamped, not killed", result.get("action") == "clamp", str(result)))
    out.append(("hits: the real player's machine received exactly one hit", ca.get("hitsReceived", 0) - cb.get("hitsReceived", 0) == 1,
                f"{cb.get('hitsReceived')} -> {ca.get('hitsReceived')}"))
    out.append(("hits: ... and applied it", ca.get("hitsApplied", 0) - cb.get("hitsApplied", 0) == 1, f"{cb.get('hitsApplied')} -> {ca.get('hitsApplied')}"))
    out.append(("hits: ... with the damage the enemy dealt", ca.get("damageApplied", 0) - cb.get("damageApplied", 0) >= expect_dmg,
                f"{cb.get('damageApplied')} -> {ca.get('damageApplied')} (wanted +{expect_dmg})"))
    out.append(("hits: the host's puppet of the client is still there", any(p.get("ref") not in (None, "0x00000000") for p in host_after.get("puppets", [])), ""))
    return out


# ------------------------------------------------------------------------------------------------------------------------------------------
# driver
# ------------------------------------------------------------------------------------------------------------------------------------------
def eval_expr(call: Call, expr: str) -> float:
    """An NVSE expression evaluated on a machine through the bridge `exec` (the same printc line nvbridge.eval sends)."""
    out = str(call("exec", {"cmd": 'printc "NVCOOP_EVAL=%g" (' + expr + ')'}).get("output", ""))
    i = out.rfind("NVCOOP_EVAL=")
    if i < 0:
        raise RuntimeError(f"cannot evaluate {expr!r}: {out!r}")
    return float(out[i + 12:].split()[0])


class Proof:
    def __init__(self, host: Call, client: Call, sleep: Callable[[float], None] = time.sleep, now: Callable[[], float] = time.monotonic,
                 wait_s: float = 40.0, interior: str = "", exterior: str = "", enemy_ref: str = "", save: str = "",
                 load: Optional[Callable[[], None]] = None, host_load: Optional[Callable[[], None]] = None,
                 loadout: Optional[Dict[str, Any]] = None):
        self.h, self.c, self.sleep, self.now, self.wait_s = host, client, sleep, now, wait_s
        self.loadout = loadout            # None = --loadout off
        self.interior, self.exterior, self.enemy_ref, self.save = interior, exterior, enemy_ref, save
        self.load, self.host_load = load, host_load
        self.results: List[Check] = []
        self.skipped: List[str] = []

    def add(self, checks: List[Check]) -> None:
        self.results += checks

    def live(self) -> Tuple[Dict[str, Any], Dict[str, Any]]:
        return self.h("coop.live", None), self.c("coop.live", None)

    def wait_for(self, pred: Callable[[], bool], timeout: Optional[float] = None, poll: float = 0.5) -> bool:
        end = self.now() + (timeout or self.wait_s)
        while True:
            if pred():
                return True
            if self.now() >= end:
                return False
            self.sleep(poll)

    def client_slot(self) -> int:
        return int(self.c("coop.live", None).get("avatars", {}).get("self", -1))

    def runtime_id(self, spec: str) -> int:
        """'Plugin.esm:HEX' -> the 32-bit runtime id on the CLIENT (load-order index in the top byte). A bare hex string is taken as it is."""
        if ":" not in spec:
            return int(spec, 16)
        name, local = spec.rsplit(":", 1)
        plugins = sorted(self.c("plugins", None).get("plugins", []), key=lambda p: p["index"])
        for p in plugins:
            if p["name"].lower() == name.lower():
                return (int(p["index"]) << 24) | int(local, 16)
        raise RuntimeError(f"plugin {name} is not loaded on the client")

    def give_loadout(self) -> None:
        """Scripted, no human: armor + a modified weapon on the CLIENT, each step read back from the engine, then from the avatar layer.
        A failed step is a FAIL line that says which step; the look phase then fails on its own checks as well."""
        lo = self.loadout or {}
        flags = int(lo["mod_flags"])
        try:
            armor, weapon = self.runtime_id(lo["armor"]), self.runtime_id(lo["weapon"])
        except Exception as e:  # noqa: BLE001
            self.add([("loadout: ids resolve on the client", False, str(e))])
            return

        def step(what: str, cmd: str) -> bool:
            r = self.c("exec", {"cmd": cmd})
            ok = bool(r.get("ok", True))
            if not ok:
                self.add([(f"loadout: {what}", False, f"{cmd!r} refused: {r.get('output', '')}")])
            return ok

        def polled(expr: str, want: Callable[[float], bool]) -> Tuple[bool, float]:
            val = [float("nan")]

            def pred() -> bool:
                try:
                    val[0] = eval_expr(self.c, expr)
                except Exception:  # noqa: BLE001
                    return False
                return want(val[0])
            return self.wait_for(pred, LOADOUT_WAIT_S, 0.3), val[0]

        for what, form in (("armor", armor), ("weapon", weapon)):
            if eval_expr(self.c, f"player.GetItemCount {form:08X}") < 1:
                if not step(f"{what} added to the inventory", f"player.additem {form:08X} 1"):
                    return
        for what, form in (("armor", armor), ("weapon", weapon)):
            if not step(f"{what} equip line accepted", f"player.EquipItem {form:08X}"):
                return
            ok, v = polled(f"player.GetEquipped {form:08X}", lambda x: x != 0)
            self.add([(f"loadout: the {what} is equipped (GetEquipped)", ok, f"GetEquipped {form:08X} = {v}")])
        if not step("weapon mod flags line accepted", f"player.SetEquippedWeaponModFlags {flags}"):
            return
        ok, v = polled("player.GetEquippedWeaponModFlags", lambda x: int(x) == flags)
        self.add([("loadout: the equipped weapon carries the modification (GetEquippedWeaponModFlags)", ok, f"read back {v} wanted {flags}")])
        seen: Dict[str, Any] = {}

        def layer() -> bool:
            seen.clear()
            seen.update(self.c("avatar.look", None))
            return all(c[1] for c in check_loadout_worn(seen, armor, weapon, flags))
        ok = self.wait_for(layer, LOADOUT_WAIT_S, 0.5)
        self.add([("loadout: the avatar layer sees the modified weapon and the armor (avatar.look)", ok,
                   f"worn {seen.get('worn')} wanted armor {armor:08X} weapon {weapon:08X} mods {flags}")])

    # ---- phases ----------------------------------------------------------------------------------------------------------------------
    def wiring(self) -> None:
        def ready() -> bool:
            hl, cl = self.live()
            cs = cl.get("avatars", {}).get("self", -1)
            a, b = roster_entry(hl, cs), roster_entry(cl, 0)
            return bool(a and a.get("ref") and b and b.get("ref"))
        self.wait_for(ready)
        hl, cl = self.live()
        self.add(check_wiring(hl, cl))

    def look(self) -> None:
        slot = self.client_slot()
        self.sleep(SETTLE_S)
        hl, cl = self.live()
        self.add(check_look(cl.get("avatars", {}).get("selfLook"), (roster_entry(hl, slot) or {}).get("lookInfo"), "client -> host"))
        src = self.c("avatar.look", None)
        if self.loadout:
            self.add(check_loadout_worn(src, self.runtime_id(self.loadout["armor"]), self.runtime_id(self.loadout["weapon"]), int(self.loadout["mod_flags"])))
        else:
            self.add(check_loadout_any(src))
        pup = self.h("avatar.puppetlook", {"slot": slot})
        st = self.h("avatar.status", None)
        want_scale = bool(src.get("ok")) and src.get("height", 0) not in (0, 1000)
        self.add(check_gear(src, pup, st.get("engine", {}), want_scale))
        # the other direction: the client's puppet of the host
        hsrc = self.h("avatar.look", None)
        hpup = self.c("avatar.puppetlook", {"slot": 0})
        cst = self.c("avatar.status", None)
        self.add(check_gear(hsrc, hpup, cst.get("engine", {}), bool(hsrc.get("ok")) and hsrc.get("height", 0) not in (0, 1000)))

    def sample_rate(self, slot: int) -> Tuple[Optional[float], Optional[str]]:
        a, _ = self.live()
        t0 = self.now()
        self.sleep(RATE_WINDOW_S)
        b, _ = self.live()
        t1 = self.now()
        e = roster_entry(b, slot)
        return rate_hz(a, b, slot, t1 - t0), (e or {}).get("tier")

    def rates(self) -> None:
        slot = self.client_slot()
        pos = self.c("avatar.local", None).get("pos")
        if not pos:
            self.skipped.append("rates: avatar.local gave no position")
            return
        x0, y0, z0 = pos
        self.sleep(SETTLE_S)
        hz, tier = self.sample_rate(slot)
        self.add(check_rate("near", hz, NEAR_HZ, tier, "near"))
        self.c("exec", {"cmd": f"player.SetPos X {x0 + FAR_OFFSET:.1f}"})
        self.sleep(SETTLE_S)
        hz, tier = self.sample_rate(slot)
        self.add(check_rate("far", hz, FAR_HZ, tier, "far"))
        self.c("exec", {"cmd": f"player.SetPos X {x0:.1f}"})
        self.sleep(SETTLE_S)
        hz, tier = self.sample_rate(slot)
        self.add(check_rate("near again", hz, NEAR_HZ, tier, "near"))

    def lifecycle(self) -> None:
        slot = self.client_slot()
        self.add(api_ok(self.h("avatar.api", None), slot, True))
        if self.interior:
            self.c("exec", {"cmd": f"coc {self.interior}"})
            parked = self.wait_for(lambda: roster_entry(self.h("coop.live", None), slot) is not None and
                                   not any(p.get("slot") == slot for p in self.h("avatar.api", None).get("puppets", [])), PARK_WAIT_S + 20)
            self.add([("lifecycle: the puppet is parked while the client is inside", parked, "")])
            self.add(api_ok(self.h("avatar.api", None), slot, False))
            self.add([("lifecycle: the roster still lists the client", roster_entry(self.h("coop.live", None), slot) is not None, "")])
            if self.exterior:
                self.c("exec", {"cmd": f"coc {self.exterior}"})
                back = self.wait_for(lambda: any(p.get("slot") == slot for p in self.h("avatar.api", None).get("puppets", [])), self.wait_s)
                self.add([("lifecycle: one puppet again after the client came out", back, "")])
                self.add(api_ok(self.h("avatar.api", None), slot, True))
        else:
            self.skipped.append("lifecycle: --interior not given, cell change not judged")
        if self.save and self.load:
            before = self.h("avatar.api", None)
            self.load()
            ok = self.wait_for(lambda: bool(roster_entry(self.c("coop.live", None), 0) and (roster_entry(self.c("coop.live", None), 0) or {}).get("ref")), self.wait_s)
            self.add([("lifecycle: the client's puppets are back after it loaded a save", ok, "")])
            after = self.h("avatar.api", None)
            self.add([("lifecycle: the host kept its puppet of the client through the client's load",
                       [p.get("ref") for p in before.get("puppets", []) if p.get("slot") == slot] == [p.get("ref") for p in after.get("puppets", []) if p.get("slot") == slot],
                       f"{before.get('puppets')} -> {after.get('puppets')}")])
        else:
            self.skipped.append("lifecycle: --save not given, load not judged")
        self.sleep(2.0)
        self.add(check_orphans(self.h("avatar.status", None), "host"))
        self.add(check_orphans(self.c("avatar.status", None), "client"))

    def hits(self) -> None:
        if not self.enemy_ref:
            self.skipped.append("hits: --enemy-ref not given (an enemy the host owns)")
            return
        slot = self.client_slot()
        cb = self.c("combat.status", None)
        res = self.h("avatar.hit", {"slot": slot, "damage": HIT_DAMAGE, "attackerRef": self.enemy_ref})
        self.sleep(2.0)
        ca = self.c("combat.status", None)
        self.add(check_hit(res, cb, ca, self.h("avatar.api", None), HIT_DAMAGE))

    def run(self, phases: Sequence[str]) -> List[Check]:
        if self.loadout and "look" in phases:
            self.give_loadout()
        for ph in phases:
            getattr(self, ph)()
        return self.results


def report(results: List[Check], skipped: Sequence[str]) -> int:
    bad = 0
    for name, ok, detail in results:
        print(f"{'PASS' if ok else 'FAIL'}  {name}" + ("" if ok or not detail else f"   [{detail}]"))
        bad += 0 if ok else 1
    for s in skipped:
        print(f"SKIP  {s}")
    print(f"{len(results) - bad} passed, {bad} failed, {len(skipped)} not judged")
    if bad:
        return 1
    return 3 if skipped else 0


# ------------------------------------------------------------------------------------------------------------------------------------------
# selftest: a scripted fake game with injected defects
# ------------------------------------------------------------------------------------------------------------------------------------------
class FakeGame:
    """Two machines that behave as the avatar layer should. Every defect in DEFECTS breaks exactly one promise."""

    DEFECTS = ("look_mismatch", "mods_not_applied", "gear_missing", "rate_ignored", "tier_wrong", "no_park", "orphan_after_load", "api_roundtrip",
               "foreign_refused", "hit_not_forwarded", "hit_proceeds", "hit_not_received", "forged", "scale_not_applied", "no_modlines", "puppet_lost_on_client_load",
               # the scripted loadout (each one breaks one step of it; the look phase must report it)
               "loadout_skipped", "additem_refused", "weapon_not_equipped", "armor_not_equipped", "modflags_refused", "modflags_not_in_layer",
               "wrong_flags", "armor_not_in_layer")

    def __init__(self, defect: str = ""):
        self.d = defect
        self.t = 0.0
        self.cell = "ext"          # the client's cell: ext | int
        self.far = False
        self.rx = 0.0              # host's accepted snapshots of the client
        self.host_ref, self.client_puppet_of_host = 0x2000, 0x3000
        self.hits_recv = self.hits_applied = self.dmg = 0
        self.loads = 0
        self.height = 1100
        # the client's gear starts EMPTY: only the scripted loadout (or the defect-free default of Proof) gives it. Runtime ids = plugin index 0 + local id.
        self.gun = 0x000CD539
        self.armor = 0x001264FE
        self.inv: Dict[int, int] = {}
        self.equipped: set = set()
        self.mods = 0

    # time
    def sleep(self, s: float) -> None:
        self.rx += s * self.rate()
        self.t += s

    def now(self) -> float:
        return self.t

    def tier(self) -> str:
        if self.cell != "ext":
            return "out"
        if self.d == "tier_wrong":
            return "near"
        return "far" if self.far else "near"

    def rate(self) -> float:
        if self.d == "rate_ignored":
            return 20.0
        return {"near": 20.0, "far": 5.0, "out": 1.0}[self.tier()]

    def parked(self) -> bool:
        return self.cell != "ext" and self.d != "no_park"

    # lookInfo as it is sent and as it is stored
    def look_info(self, stored: bool) -> Dict[str, Any]:
        h = self.height + (13 if stored and self.d == "look_mismatch" else 0)
        return {"race": 1, "female": False, "hair": 4660, "eyes": 85, "hairColor": 10531008, "height": h, "weight": 950, "faceCoeffs": 130,
                "equip": self.worn_equip()}

    def worn_equip(self) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        if self.armor in self.equipped and self.d != "armor_not_in_layer":
            out.append({"form": self.armor, "weapon": False, "mods": 0})
        if self.gun in self.equipped:
            out.append({"form": self.gun, "weapon": True, "mods": 0 if self.d == "modflags_not_in_layer" else self.mods})
        return out

    def avatars(self, host: bool) -> Dict[str, Any]:
        if host:
            roster = [{"slot": 1, "ref": 0 if self.parked() else self.host_ref, "look": True, "tier": self.tier(), "rx": int(self.rx), "lookInfo": self.look_info(True)}]
            return {"self": 0, "roster": roster, "accepted": int(self.rx) + 1, "forged": 3 if self.d == "forged" else 0, "localSnapshots": 100}
        roster = [{"slot": 0, "ref": self.client_puppet_of_host, "look": True, "tier": "near", "rx": int(self.rx), "lookInfo": {}}]
        return {"self": 1, "roster": roster, "accepted": int(self.rx) + 1, "forged": 0, "localSnapshots": 100, "selfLook": self.look_info(False)}

    def live(self, host: bool) -> Dict[str, Any]:
        return {"avatarModule": {"enabled": True, "running": True}, "avatars": self.avatars(host)}

    def engine(self) -> Dict[str, Any]:
        return {"linesException": 0, "foreignRefused": 2 if self.d == "foreign_refused" else 0, "scaleApplied": 0 if self.d == "scale_not_applied" else 1,
                "modLines": 0 if self.d == "no_modlines" else 2}

    def status(self, host: bool) -> Dict[str, Any]:
        puppets = [] if self.parked() and host else [{"slot": 1 if host else 0, "ref": "0x%08X" % (self.host_ref if host else self.client_puppet_of_host)}]
        tagged = len(puppets) + (2 if self.d == "orphan_after_load" and self.loads else 0)
        return {"puppets": puppets, "tagged": tagged, "engine": self.engine()}

    def api(self) -> Dict[str, Any]:
        puppets = [] if self.parked() else [{"slot": 1, "ref": "0x%08X" % self.host_ref, "acting": "0x%08X" % self.host_ref, "roundTrip": self.d != "api_roundtrip"}]
        if self.d == "no_park" and self.cell != "ext":
            puppets = [{"slot": 1, "ref": "0x%08X" % self.host_ref, "acting": "0x%08X" % self.host_ref, "roundTrip": True}]
        return {"registered": True, "localSlot": 0, "puppets": puppets}

    def worn(self, mods_ok: bool, missing: bool) -> Dict[str, Any]:
        w = [{"form": "0x%08X" % self.gun, "weapon": True, "mods": self.mods if mods_ok else 0, "modsKnown": True}]
        if not missing:
            w.append({"form": "0x%08X" % self.armor, "weapon": False, "mods": 0, "modsKnown": True})
        return w

    def eval_line(self, cmd: str) -> Optional[Dict[str, Any]]:
        m = re.match(r'printc "NVCOOP_EVAL=%g" \((.*)\)$', cmd)
        if not m:
            return None
        e = m.group(1).split()
        if e[0] == "player.GetItemCount":
            v = self.inv.get(int(e[1], 16), 0)
        elif e[0] == "player.GetEquipped":
            v = 1 if int(e[1], 16) in self.equipped else 0
        elif e[0] == "player.GetEquippedWeaponModFlags":
            v = self.mods if self.gun in self.equipped else 0
        else:
            raise KeyError(cmd)
        return {"ok": True, "output": "NVCOOP_EVAL=%g" % v}

    def loadout_line(self, cmd: str) -> Optional[Dict[str, Any]]:
        t = cmd.split()
        if t[0] == "player.additem":
            if self.d == "additem_refused":
                return {"ok": False, "output": "Script command \"additem\" not found"}
            self.inv[int(t[1], 16)] = self.inv.get(int(t[1], 16), 0) + int(t[2])
            return {"ok": True, "output": ""}
        if t[0] == "player.EquipItem":
            f = int(t[1], 16)
            refused = (f == self.gun and self.d == "weapon_not_equipped") or (f == self.armor and self.d == "armor_not_equipped")
            if self.inv.get(f, 0) >= 1 and not refused:
                self.equipped.add(f)
            return {"ok": True, "output": ""}
        if t[0] == "player.SetEquippedWeaponModFlags":
            if self.gun in self.equipped and self.d != "modflags_refused":
                self.mods = int(t[1]) + (2 if self.d == "wrong_flags" else 0)
            return {"ok": True, "output": ""}
        return None

    def host_call(self, method: str, params: Optional[dict] = None) -> Any:
        p = params or {}
        if method == "coop.live":
            return self.live(True)
        if method == "avatar.puppetlook":
            return {"slot": 1, "ref": "0x%08X" % self.host_ref, "worn": self.worn(self.d != "mods_not_applied", self.d == "gear_missing")}
        if method == "avatar.status":
            return self.status(True)
        if method == "avatar.api":
            return self.api()
        if method == "avatar.look":
            return {"ok": True, "height": 1000, "worn": []}
        if method == "avatar.hit":
            route = "noPort" if self.d == "hit_not_forwarded" else "forwarded"
            if route == "forwarded" and self.d != "hit_not_received":
                self.hits_recv += 1
                self.hits_applied += 1
                self.dmg += p.get("damage", 0)
            return {"route": route, "action": "proceed" if self.d == "hit_proceeds" else "clamp", "slot": p.get("slot")}
        raise KeyError(method)

    def client_call(self, method: str, params: Optional[dict] = None) -> Any:
        p = params or {}
        if method == "coop.live":
            return self.live(False)
        if method == "plugins":
            return {"plugins": [{"name": "FalloutNV.esm", "index": 0}, {"name": "nvcoop.esp", "index": 1}]}
        if method == "avatar.look":
            return {"ok": True, "height": self.height, "worn": [{"form": "0x%08X" % w["form"], "weapon": w["weapon"], "mods": w["mods"]} for w in self.worn_equip()]}
        if method == "avatar.puppetlook":
            return {"slot": 0, "ref": "0x%08X" % self.client_puppet_of_host, "worn": []}
        if method == "avatar.status":
            return self.status(False)
        if method == "avatar.local":
            return {"pos": [1000.0, 2000.0, 0.0]}
        if method == "combat.status":
            return {"stats": {"hitsReceived": self.hits_recv, "hitsApplied": self.hits_applied, "damageApplied": self.dmg}}
        if method == "exec":
            cmd = p.get("cmd", "")
            ev = self.eval_line(cmd) or self.loadout_line(cmd)
            if ev is not None:
                return ev
            if cmd.startswith("coc ") and "Interior" in cmd:
                self.cell = "int"
            elif cmd.startswith("coc "):
                self.cell = "ext"
            elif cmd.startswith("player.SetPos X"):
                self.far = float(cmd.split()[-1]) > 3000
            return {"ok": True}
        raise KeyError(method)

    def do_load(self) -> None:
        self.loads += 1
        if self.d == "puppet_lost_on_client_load":
            self.host_ref += 1   # the host's puppet was rebuilt: not the same object


def run_fake(defect: str) -> List[Check]:
    g = FakeGame(defect)
    pr = Proof(g.host_call, g.client_call, sleep=g.sleep, now=g.now, wait_s=20.0, interior="GSInterior", exterior="GSExterior", enemy_ref="00112233",
               save="s", load=g.do_load, loadout=None if defect == "loadout_skipped" else dict(DEFAULT_LOADOUT))
    # the proof's own 'load' bumps the fake's counter; with a defect the host puppet changes
    results = pr.run(PHASES)
    results += [("skipped", False, s) for s in pr.skipped]
    return results


def selftest() -> int:
    bad = 0
    clean = run_fake("")
    failed = [c for c in clean if not c[1]]
    print(f"clean run: {len(clean)} checks, {len(failed)} failed")
    for name, _ok, detail in failed:
        print(f"  UNEXPECTED FAIL {name} [{detail}]")
    if failed or len(clean) < 50:
        bad += 1
    for d in FakeGame.DEFECTS:
        res = run_fake(d)
        caught = [c for c in res if not c[1]]
        print(f"defect {d:28s} {'caught by ' + str(len(caught)) + ' check(s)' if caught else 'SURVIVED'}")
        if not caught:
            bad += 1
    # --loadout off: a client that already holds the gear passes, an empty one fails with the two precondition lines
    for pre, want_fail in ((True, False), (False, True)):
        g = FakeGame("")
        if pre:
            g.inv = {g.gun: 1, g.armor: 1}
            g.equipped = {g.gun, g.armor}
            g.mods = 1
        pr = Proof(g.host_call, g.client_call, sleep=g.sleep, now=g.now, wait_s=20.0, loadout=None)
        pr.run(("wiring", "look"))
        failed = [c[0] for c in pr.results if not c[1]]
        print(f"loadout off, gear {'present' if pre else 'absent '}: {'failed ' + str(len(failed)) if failed else 'passed'}")
        if bool(failed) != want_fail:
            bad += 1
    # pure helpers
    assert norm_equip([{"form": 2, "weapon": True, "mods": 1}, {"form": 1}]) == [(1, False, 0), (2, True, 1)]
    assert rate_hz({"avatars": {"roster": [{"slot": 1, "rx": 10}]}}, {"avatars": {"roster": [{"slot": 1, "rx": 110}]}}, 1, 5.0) == 20.0
    assert rate_hz({}, {}, 1, 5.0) is None
    print("proof_avatars selftest: " + ("FAILED" if bad else "ALL OK"))
    return 1 if bad else 0


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--host", type=int, default=0, help="bridge instance id of the host")
    ap.add_argument("--client", type=int, default=1, help="bridge instance id of the client")
    ap.add_argument("--phases", default=",".join(PHASES), help="comma list of: " + ",".join(PHASES))
    ap.add_argument("--interior", default="", help="editor id of an interior cell the client can 'coc' into (lifecycle)")
    ap.add_argument("--exterior", default="", help="editor id of the exterior cell to come back to (lifecycle)")
    ap.add_argument("--save", default="", help="save name (no extension) the CLIENT loads in the lifecycle phase")
    ap.add_argument("--enemy-ref", default="", help="a hostile actor the HOST owns, in hex (hits phase)")
    ap.add_argument("--wait", type=float, default=40.0)
    ap.add_argument("--loadout", choices=("auto", "off"), default="auto",
                    help="auto (default): give the CLIENT armor + a weapon with a modification through the bridge before the look phase; off: it must hold them already")
    ap.add_argument("--armor", default=DEFAULT_LOADOUT["armor"], help="loadout armor, Plugin.esm:HEX (default %(default)s, ArmorLeatherReinforced)")
    ap.add_argument("--weapon", default=DEFAULT_LOADOUT["weapon"], help="loadout weapon, Plugin.esm:HEX (default %(default)s, WeapNVTrailCarbine)")
    ap.add_argument("--weapon-mod", default=DEFAULT_LOADOUT["mod"], help="the IMOD the flag stands for, Plugin.esm:HEX (report only; default %(default)s, ModNVTrailCarbineScope)")
    ap.add_argument("--mod-flags", type=int, default=DEFAULT_LOADOUT["mod_flags"], help="ExtraWeaponModFlags to set on the worn weapon, 1..7 (default %(default)s = mod slot 1)")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args(argv)
    if a.selftest:
        return selftest()
    if not 1 <= a.mod_flags <= 7:
        print("--mod-flags must be 1..7 (xNVSE SetEquippedWeaponModFlags takes 0..7; 0 would remove the modification)", file=sys.stderr)
        return 2
    phases = [p.strip() for p in a.phases.split(",") if p.strip()]
    bad = [p for p in phases if p not in PHASES]
    if bad:
        print(f"unknown phase(s) {bad}; choose from {', '.join(PHASES)}", file=sys.stderr)
        return 2
    sys.path.insert(0, os.path.join(REPO, "tools", "runner"))
    from nvbridge import NvBridge  # noqa: E402
    import nvready  # noqa: E402
    try:
        hb = NvBridge.connect(instance=a.host, wait_s=a.wait)
        cb = NvBridge.connect(instance=a.client, wait_s=a.wait)
    except Exception as e:  # noqa: BLE001
        print(f"cannot reach the game instances: {e}", file=sys.stderr)
        return 2
    try:
        why = nvready.wait_ready([hb, cb])   # coop.live fails with "main thread did not service the request" while a copy still boots or loads
        if why:
            print(f"cannot reach the game instances: {why}", file=sys.stderr)
            return 2
        host = lambda m, p=None: hb.call(m, p or {})  # noqa: E731
        client = lambda m, p=None: cb.call(m, p or {})  # noqa: E731
        loadout = {"armor": a.armor, "weapon": a.weapon, "mod": a.weapon_mod, "mod_flags": a.mod_flags} if a.loadout == "auto" else None
        pr = Proof(host, client, wait_s=a.wait, interior=a.interior, exterior=a.exterior, enemy_ref=a.enemy_ref, save=a.save,
                   load=(lambda: cb.load_save(a.save)) if a.save else None, loadout=loadout)
        results = pr.run(phases)
        return report(results, pr.skipped)
    finally:
        hb.close()
        cb.close()


if __name__ == "__main__":
    sys.exit(main())
