#!/usr/bin/env python3
"""In-game proof of the player avatars between TWO machines (roadmap step 3). WRITTEN, NEVER RUN against a real game: it needs two co-op game
instances. `--selftest` runs the analysis against a scripted fake game plus injected defects (every defect must be caught); that is what the gate runs.

    python -I tools/avatar/proof_avatars.py --host 0 --client 1 --enemy-ref 000XXXXX         # all phases
    python -I tools/avatar/proof_avatars.py --host 0 --client 1 --phases wiring,look,rates   # some phases
    python -I tools/avatar/proof_avatars.py --selftest

Provision two instances first (the lead starts them; this tool never launches a game and never takes the game slot):
    python -I tools/runner/run.py provision --n 2 --ini coop.enable=1 --ini live.actors=1 --ini live.avatars=1 --ini live.combat=1 --ini avatar.enabled=1 \
        --ini dev.bots=1
  Host and client in one co-op session, both in an EXTERIOR with about 120 m of free ground along +X in front of the client (the rates phase walks the
  client away by teleport), the client holding a weapon WITH at least one modification and wearing armor; ideally a non-default height / hair.

Phases (default: all), each one judged by pure functions below:
  wiring     both machines run the avatar module, list the other player in the roster, have a puppet ref for it, accepted snapshots, nothing forged.
  look       the Look the client sent (selfLook, avatar.look) equals what the host stored (roster[].lookInfo): race, sex, hair, eyes, hair colour, height,
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
class Proof:
    def __init__(self, host: Call, client: Call, sleep: Callable[[float], None] = time.sleep, now: Callable[[], float] = time.monotonic,
                 wait_s: float = 40.0, interior: str = "", exterior: str = "", enemy_ref: str = "", save: str = "",
                 load: Optional[Callable[[], None]] = None, host_load: Optional[Callable[[], None]] = None):
        self.h, self.c, self.sleep, self.now, self.wait_s = host, client, sleep, now, wait_s
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
               "foreign_refused", "hit_not_forwarded", "hit_proceeds", "hit_not_received", "forged", "scale_not_applied", "no_modlines", "puppet_lost_on_client_load")

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
        self.gun = 0x3A00
        self.armor = 0x2A00
        self.mods = 0x05

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
                "equip": [{"form": self.armor, "weapon": False, "mods": 0}, {"form": self.gun, "weapon": True, "mods": self.mods}]}

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
        if method == "avatar.look":
            return {"ok": True, "height": self.height, "worn": [{"form": "0x%08X" % self.gun, "weapon": True, "mods": self.mods},
                                                              {"form": "0x%08X" % self.armor, "weapon": False, "mods": 0}]}
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
               save="s", load=g.do_load)
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
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args(argv)
    if a.selftest:
        return selftest()
    phases = [p.strip() for p in a.phases.split(",") if p.strip()]
    bad = [p for p in phases if p not in PHASES]
    if bad:
        print(f"unknown phase(s) {bad}; choose from {', '.join(PHASES)}", file=sys.stderr)
        return 2
    sys.path.insert(0, os.path.join(REPO, "tools", "runner"))
    from nvbridge import NvBridge  # noqa: E402
    try:
        hb = NvBridge.connect(instance=a.host, wait_s=a.wait)
        cb = NvBridge.connect(instance=a.client, wait_s=a.wait)
    except Exception as e:  # noqa: BLE001
        print(f"cannot reach the game instances: {e}", file=sys.stderr)
        return 2
    try:
        host = lambda m, p=None: hb.call(m, p or {})  # noqa: E731
        client = lambda m, p=None: cb.call(m, p or {})  # noqa: E731
        pr = Proof(host, client, wait_s=a.wait, interior=a.interior, exterior=a.exterior, enemy_ref=a.enemy_ref, save=a.save,
                   load=(lambda: cb.load_save(a.save)) if a.save else None)
        results = pr.run(phases)
        return report(results, pr.skipped)
    finally:
        hb.close()
        cb.close()


if __name__ == "__main__":
    sys.exit(main())
