"""Actor ownership playbook (milestone 3): the two-copy ownership HANDOFF test of FULL-SYNC-PLAN section 6.

    ownership_transfer   two game copies. An NPC both players stand next to is owned by the host; the host walks away, ownership moves
                         to the client exactly once, then the host comes back and it moves back. Along the way:
                           * both copies agree on (owner, epoch) the whole time (coop.owner), epochs only ever rise, by one per handoff;
                           * the NPC's AI runs on exactly one copy, the owner's, and on none while the handoff is pending;
                           * the NPC never jumps (no respawn, no Reset3D, no rewind to an old position), keeps its ref and its base form;
                           * ownership does not flap at the border;
                           * the desync counters do not rise (the engine of core.py checks that after every step).

STATUS: written, NEVER RUN. Nothing here has touched a game. It needs the in-game pieces that do not exist yet:
  * bridge method coop.owner (plugin/actors implements it; the answer carries owner, epoch, known, mine, pending, ai, standIn),
  * an engine adapter that feeds ActorSync (IActorPort + ActorSync::track for the loaded actors) - until then coop.owner answers
    {"known":false} for every actor and this playbook stops at its first step with a clear message,
  * the scenario `ownership_handoff` (tools/scenarios) for the starting positions.
Run it with tools/bots/run_actor_handoff.py (never started by the tests, never part of the registry: it needs real instances).
"""
from __future__ import annotations

import json
from typing import Any, Dict, List, Optional, Tuple

from . import probe as P
from .core import Ctx, Machine, Playbook, StepFailure
from .pb_combat import walk_to


# ---- probes ---------------------------------------------------------------------------------------------------------------------
def owner_info(bot, ref: str) -> Dict[str, Any]:
    """The full coop.owner answer of this machine for `ref`. Raises MissingBridge when the build does not have the service."""
    r = P._rpc(bot, "coop.owner", {"ref": P.fullref(ref)})          # noqa: SLF001
    if not r.ok:
        raise P.MissingBridge(r.reason)
    res = r.data["result"]
    if res.get("unsupported"):
        raise P.MissingBridge("coop.owner is unsupported in this build: " + str(res.get("reason", "")))
    return res


def npc_pos(bot, ref: str) -> Tuple[float, float, float]:
    return tuple(float(P._eval_ref(bot, ref, f"GetPos {a}")) for a in "XYZ")      # noqa: SLF001


def dist(a: Tuple[float, float, float], b: Tuple[float, float, float]) -> float:
    return sum((x - y) ** 2 for x, y in zip(a, b)) ** 0.5


class Trace:
    """What every machine said about the NPC at every poll: (owner, epoch, ai, pending, pos). Filled by wait loops."""

    def __init__(self) -> None:
        self.rows: List[Dict[str, Any]] = []

    def sample(self, ctx: Ctx, ref: str) -> Dict[str, Dict[str, Any]]:
        snap = {}
        for m in ctx.present:
            info = owner_info(m.bot, ref)
            row = {"machine": m.name, "owner": int(info["owner"]), "epoch": int(info["epoch"]), "known": bool(info.get("known")),
                   "ai": bool(info.get("ai")), "pending": bool(info.get("pending")), "mine": bool(info.get("mine")),
                   "standIn": bool(info.get("standIn")), "pos": npc_pos(m.bot, ref)}
            snap[m.name] = row
            self.rows.append(row)
        return snap

    def epochs_never_fall(self) -> Optional[str]:
        last: Dict[str, int] = {}
        for r in self.rows:
            e = r["epoch"]
            if r["machine"] in last and e < last[r["machine"]]:
                return f"{r['machine']} saw the epoch fall from {last[r['machine']]} to {e}"
            last[r["machine"]] = max(last.get(r["machine"], 0), e)
        return None

    def ai_conflict(self) -> Optional[str]:
        """More than one copy with AI on in one sample round, or AI on while a handoff is pending on that copy."""
        by_round: Dict[int, int] = {}
        per = max(1, len({r["machine"] for r in self.rows}))
        for i, r in enumerate(self.rows):
            if r["ai"]:
                by_round[i // per] = by_round.get(i // per, 0) + 1
            if r["ai"] and r["pending"] and (r["mine"]):
                return f"{r['machine']} runs the NPC's AI while the handoff is pending"
        bad = [k for k, v in by_round.items() if v > 1]
        return f"AI runs on more than one copy at once (sample rounds {bad[:3]})" if bad else None

    def max_jump(self, machine: str) -> float:
        pts = [r["pos"] for r in self.rows if r["machine"] == machine]
        return max((dist(a, b) for a, b in zip(pts, pts[1:])), default=0.0)


def agreed(snap: Dict[str, Dict[str, Any]]) -> Optional[Tuple[int, int]]:
    """(owner, epoch) if every machine reports the same known, settled pair; else None."""
    pairs = {(r["owner"], r["epoch"]) for r in snap.values() if r["known"] and not r["pending"]}
    if len(pairs) == 1 and all(r["known"] and not r["pending"] for r in snap.values()):
        return next(iter(pairs))
    return None


# ---- the playbook ---------------------------------------------------------------------------------------------------------------
class OwnershipTransferPlaybook(Playbook):
    name = "ownership_transfer"
    summary = "Two copies: an NPC changes owner when the host walks away and back; one handoff each way, AI on one copy, no jump."
    scenario = "ownership_handoff"
    needs_scenario_refs = ("raiders", "far_pos", "near_pos")
    min_players = 2
    max_players = 2
    anim = False
    JUMP_UNITS = 350.0              # the largest NPC step between two polls that is still "walking"

    def run(self, ctx: Ctx) -> None:
        npc: str = list(ctx.refs["raiders"])[0]
        far = ctx.refs["far_pos"]
        near = ctx.refs["near_pos"]
        base = ctx.refs.get("raider_base")
        rounds = int(ctx.opts.get("rounds", 2))                 # host away / host back = one round
        poll_s = float(ctx.opts.get("poll_s", 0.2))
        host, client = ctx.host, ctx.clients[0]
        trace = Trace()
        counted = {"n": None}

        def sample() -> Dict[str, Dict[str, Any]]:
            return trace.sample(ctx, npc)

        def settled_pair() -> Optional[Tuple[int, int]]:
            return agreed(sample())

        with ctx.step("both players next to the NPC (the scenario's start): ownership is known and identical everywhere"):
            # no gathering here on purpose: the scenario puts the host and the client at different distances from the NPC, and the
            # border step below needs the host to be able to come back CLOSER to it than the client is (flap bait).
            ctx.wait_until("every copy knows the NPC's owner and they agree", lambda: settled_pair() is not None,
                           timeout_s=ctx.opts.get("own_s", 20.0), poll_s=poll_s)
            pair = settled_pair()
            ctx.expect(pair is not None, "no agreed owner")
            owner0, epoch0 = pair                                       # type: ignore[misc]
            ctx.expect(epoch0 >= 1, f"epoch {epoch0} (an owned actor has a positive epoch)")
            snap = sample()
            ctx.expect(owner0 == host.idx, f"the host should own an NPC both players are next to, owner is slot {owner0}")
            ais = [n for n, r in snap.items() if r["ai"]]
            ctx.expect(ais == [host.name], f"AI must run on the owner only, it runs on {ais}")
            ctx.expect(not any(r["standIn"] for r in snap.values()), "a stand-in clone exists although the NPC is loaded everywhere")
            if base and ctx.optional("duplicate-NPC checks", lambda: P.ref_count(host.bot, base)) is not None:
                counted["n"] = P.ref_count(host.bot, base)

        with ctx.step("the owner publishes, the other copy follows"):
            ctx.pause(3.0)
            snap = sample()
            d = dist(snap[host.name]["pos"], snap[client.name]["pos"])
            ctx.expect(d < 150.0, f"the client's copy of the NPC is {d:.0f} units from the owner's (more than 2 m)")

        owner, epoch = owner0, epoch0
        for rnd in range(rounds):
            walker, other = (host, client) if rnd % 2 == 0 else (client, host)
            # round 0: the host walks away (ownership -> client) and comes back; round 1: the client walks away (-> host) and comes back
            with ctx.step(f"round {rnd + 1}: {walker.name} walks away, the NPC changes owner once"):
                start_rows = len(trace.rows)
                with ctx.walking(walker):
                    ctx.must(walk_to(walker, far), f"{walker.name} walks away")
                ctx.wait_until(f"ownership moves to {other.name} on every copy",
                               lambda: (lambda p: p is not None and p[0] == other.idx)(settled_pair()),
                               timeout_s=ctx.opts.get("handoff_s", 20.0), poll_s=poll_s)
                ctx.settle()
                snap = sample()
                pair = agreed(snap)
                ctx.expect(pair is not None, f"after the handoff the copies disagree: {json.dumps({k: (v['owner'], v['epoch']) for k, v in snap.items()})}")
                new_owner, new_epoch = pair                             # type: ignore[misc]
                ctx.expect(new_owner == other.idx, f"owner is slot {new_owner}, expected {other.idx}")
                ctx.expect(new_epoch == epoch + 1, f"epoch went {epoch} -> {new_epoch}; one handoff must raise it by exactly one")
                ais = [n for n, r in snap.items() if r["ai"]]
                ctx.expect(ais == [other.name], f"AI must run on the new owner only, it runs on {ais}")
                rows = trace.rows[start_rows:]
                for mname in (host.name, client.name):
                    pts = [r["pos"] for r in rows if r["machine"] == mname]
                    jumps = [dist(a, b) for a, b in zip(pts, pts[1:])]
                    ctx.expect(max(jumps, default=0.0) < self.JUMP_UNITS,
                               f"the NPC jumped {max(jumps, default=0):.0f} units between two polls on {mname} (respawn or rewound state?)")
                owner, epoch = new_owner, new_epoch

            with ctx.step(f"round {rnd + 1}: the walker returns to the border, ownership must not flap"):
                ctx.must(walk_to(walker, near), f"{walker.name} walks back")
                ctx.pause(3.0)
                for _ in range(5):
                    ctx.settle()
                snap = sample()
                pair = agreed(snap)
                ctx.expect(pair == (owner, epoch), f"ownership flapped or changed at the border: expected {(owner, epoch)}, got {pair}")

        with ctx.step("no recreation, no duplicates, nothing lost"):
            problem = trace.epochs_never_fall()
            ctx.expect(problem is None, problem or "")
            problem = trace.ai_conflict()
            ctx.expect(problem is None, problem or "")
            for mname in (host.name, client.name):
                ctx.expect(trace.max_jump(mname) < self.JUMP_UNITS, f"largest NPC step on {mname} was {trace.max_jump(mname):.0f} units")
            if counted["n"] is not None:
                n1 = P.ref_count(host.bot, base)
                ctx.expect(n1 == counted["n"], f"the number of live {base} refs changed from {counted['n']} to {n1} (a clone or a respawn)")
            ctx.expect(all(owner_info(m.bot, npc).get("known") for m in ctx.present), "a copy forgot the NPC")
