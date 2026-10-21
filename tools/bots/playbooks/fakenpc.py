"""NPC live layer of the fake co-op world (opt-in: FakeCoop(npc_ai=True)); used by the enemy playbooks (pb_enemy.py).

What it models, in the shape FULL-SYNC-PLAN section 3.2 asks for (it is a FAKE: it proves the playbooks' logic and their checks,
not the engine):
  * The OWNER machine of an NPC runs its AI: it walks toward the nearest player in its cell at NPC_SPEED, draws its weapon when a
    player is in aggro range, attacks every ATTACK_PERIOD seconds from ATTACK_RANGE (the hit on the player is applied once, by the
    victim), staggers when hit, and dies when its health reaches 0.
  * Every OTHER machine shows a remote-driven copy: the owner's state from INTERP seconds ago (the 100 ms interpolation buffer).
  * Positions and animations are NOT part of the replicated "shared" state (snapdiff does not see them, like the real live layer);
    health, death, owner and epoch are.
Bugs (names in fakecoop.BUGS) switch on one wrong behaviour each; every one has a twin in registry.TWINS.
"""
from __future__ import annotations

import copy
import math
from typing import Dict, Optional

DT = 0.05                  # virtual seconds per world tick
NPC_SPEED = 110.0          # units per second
AGGRO_RANGE = 3500.0       # a player this close makes the NPC draw its weapon and chase
ATTACK_RANGE = 450.0
LEASH = 1500.0             # an NPC only goes after players within this of its post (+ attack range)
ATTACK_PERIOD = 1.8
ATTACK_ANIM = 0.7
STAGGER_ANIM = 0.6         # animcheck matches inside a +-150 ms window, so a missing 600 ms stagger shows as a ~300 ms episode: only the FIGHT grace (250 ms) sees it
DRAW_ANIM = 0.45
NPC_DAMAGE = 5             # per NPC attack, on a player
HIT_DAMAGE = 25            # per player shot, on an NPC (FakeBot.fire)
INTERP = 0.10              # remote copies show the owner's state this old
HIST = 16
REBALANCE_EVERY = 10       # ticks between ownership re-evaluations
NPC_KINDS = ("raider", "actor", "companion", "creature")


def new_live(r: dict) -> dict:
    return {"pos": list(r["pos"]), "home": list(r["pos"]), "rot": 0.0, "lock_anim": "", "lock_until": 0.0, "next_attack": 0.0,
            "weapon_out": False, "phase": 0.0, "moving": False, "hist": [], "anim": "idle"}


def bump(world, key: str) -> None:
    world.npc_stats[key] = world.npc_stats.get(key, 0) + 1


def is_ai(world, r: dict) -> bool:
    return bool(world.npc_ai and r.get("ai") and r.get("kind") in NPC_KINDS)


def _record(world, L: dict) -> None:
    L["hist"].append((world.vt, list(L["pos"]), L["rot"], L["anim"], L["weapon_out"], L["phase"]))
    if len(L["hist"]) > HIST:
        L["hist"].pop(0)


def step(world) -> None:
    """One world tick of every AI NPC (called from FakeCoop.tick)."""
    if not world.npc_ai:
        return
    vt = world.vt
    for k, r in list(world.truth["refs"].items()):
        if not is_ai(world, r):
            continue
        L = world.live.get(k)
        if L is None:
            L = world.live[k] = new_live(r)
        if r["dead"]:
            L["anim"], L["weapon_out"], L["moving"] = "death", False, False
            _record(world, L)
            continue
        if not r["enabled"]:
            L["anim"], L["moving"] = "idle", False
            _record(world, L)
            continue
        target, bd = None, 1e18
        for s in range(world.n):
            if world.players[s]["cell"] != r["cell"]:
                continue
            if math.hypot(world.shown[s][0] - L["home"][0], world.shown[s][1] - L["home"][1]) > LEASH + ATTACK_RANGE:
                continue            # a player who left the NPC's post is not chased (a leash: bandits hold their ground)
            d = math.hypot(world.shown[s][0] - L["pos"][0], world.shown[s][1] - L["pos"][1])
            if d < bd:
                target, bd = s, d
        L["moving"] = False
        if vt < L["lock_until"]:
            L["anim"] = L["lock_anim"]
        elif target is None or bd > AGGRO_RANGE:
            L["anim"] = "idle"
        else:
            if not L["weapon_out"]:
                L["weapon_out"] = True
                L["lock_anim"], L["lock_until"] = "draw", vt + DRAW_ANIM
                L["anim"] = "draw"
                bump(world, "draws")
            else:
                tx, ty = world.shown[target][0], world.shown[target][1]
                L["rot"] = math.atan2(ty - L["pos"][1], tx - L["pos"][0])
                if bd > ATTACK_RANGE:
                    step_len = min(NPC_SPEED * DT, bd - ATTACK_RANGE * 0.9)
                    L["pos"][0] += math.cos(L["rot"]) * step_len
                    L["pos"][1] += math.sin(L["rot"]) * step_len
                    L["phase"] += step_len / 25.0
                    L["moving"], L["anim"] = True, "run"
                elif vt >= L["next_attack"]:
                    L["lock_anim"], L["lock_until"], L["anim"] = "attack", vt + ATTACK_ANIM, "attack"
                    L["next_attack"] = vt + ATTACK_PERIOD
                    bump(world, "attacks")
                    world.npc_hits_player(k, target)
                else:
                    L["anim"] = "idle"
        if "ai_both" in world.bugs:
            L["jit"] = not L.get("jit", False)
        _record(world, L)
    if world.tick_n % REBALANCE_EVERY == 0:
        world.rebalance_owners()


def on_player_hit(world, k: str) -> None:
    """A player's shot landed on NPC `k`, and it survived: it staggers (cancels a wind-up attack)."""
    L = world.live.get(k)
    r = world.truth["refs"].get(k)
    if L is None or r is None or r["dead"]:
        return
    L["lock_anim"], L["lock_until"], L["anim"] = "stagger", world.vt + STAGGER_ANIM, "stagger"
    L["next_attack"] = max(L["next_attack"], world.vt + STAGGER_ANIM + 0.2)
    bump(world, "staggers")


def on_death(world, k: str) -> None:
    L = world.live.get(k)
    if L is not None:
        L["anim"], L["weapon_out"], L["moving"] = "death", False, False


def on_owner_change(world, k: str, old: int, new: int) -> None:
    """Ownership of NPC `k` moved from machine `old` to machine `new` (the bugs below are the STR #906 family)."""
    L = world.live.get(k)
    r = world.truth["refs"][k]
    if L is None:
        return
    b = world.bugs
    world.owner_changes[k] = world.owner_changes.get(k, 0) + 1
    if "ghost_owner" in b:
        world.ghost[k] = {"machine": old, "state": copy.deepcopy({kk: L[kk] for kk in ("pos", "rot", "anim", "weapon_out", "phase")})}
    if "handoff_snap" in b:                       # the new owner reset the actor to its spawn point (Reset3D)
        L["pos"] = list(L["home"])
        L["hist"] = []
    if "handoff_dup" in b:                        # the new owner created a second copy instead of taking the first over
        world.dup_n += 1
        dk = f"{k}#dup{world.dup_n}"
        world.views[new]["refs"][dk] = copy.deepcopy(world.views[new]["refs"][k])
    if "hp_revive_on_handoff" in b and not r["dead"]:
        world.set_ref(k, hp=world.hp_max.get(k, r["hp"]))     # the new owner applied its old, full-health state


def state_of(L: dict) -> dict:
    return {"pos": list(L["pos"]), "rot": L["rot"], "anim": L["anim"], "weapon_out": L["weapon_out"], "phase": L["phase"]}


def view(world, k: str, machine: int) -> Optional[dict]:
    """What machine `machine` shows for NPC `k`: {pos, rot, anim, weapon_out, phase}, or None when it has no live state."""
    L = world.live.get(k)
    r = world.truth["refs"].get(k)
    if L is None or r is None:
        return None
    b = world.bugs
    if machine == r["owner"]:
        return state_of(L)
    g = world.ghost.get(k)
    if g is not None and g["machine"] == machine:        # ghost_owner: the former owner keeps showing (and simulating) its own copy
        return copy.deepcopy(g["state"])
    if "npc_pos_stale" in b:                             # positions never reach the other machines
        s = state_of(L)
        s["pos"], s["anim"] = list(L["home"]), "idle"
        return s
    h = L["hist"]
    if not h:
        return state_of(L)
    lag = int(round(world.npc_lag / DT))
    e = h[-1 - lag] if len(h) > lag else h[0]
    s = {"pos": list(e[1]), "rot": e[2], "anim": e[3], "weapon_out": e[4], "phase": e[5]}
    if "ai_both" in b:                                   # two machines drive the same NPC: it shakes
        s["pos"][0] += 7.0 if L.get("jit") else -7.0
    if s["anim"] == "attack" and "npc_no_attack_anim" in b:
        s["anim"] = "idle"
    if s["anim"] == "stagger" and "npc_no_stagger_anim" in b:
        s["anim"] = "idle"
    if s["anim"] == "death" and "npc_death_pose_missing" in b:
        s["anim"] = "idle"
    if s["anim"] == "draw" and "npc_no_draw" in b:
        s["anim"] = "idle"
    if "npc_no_draw" in b:
        s["weapon_out"] = False
    if "npc_slides" in b and s["anim"] == "run":
        s["anim"] = "idle"                               # glides across the ground on an idle animation
    return s
