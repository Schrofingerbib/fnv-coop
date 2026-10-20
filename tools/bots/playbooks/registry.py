"""Name -> playbook, its fake world builder and its default player count."""
from __future__ import annotations

from typing import Callable, Dict, NamedTuple, Optional, Tuple

from . import pb_extra, pb_combat, pb_loot, pb_social, pb_story, pb_world
from .core import Playbook


class Entry(NamedTuple):
    cls: type
    fake: Optional[Callable]
    players: int
    needs_plan: bool = False


REGISTRY: Dict[str, Entry] = {
    "fight": Entry(pb_combat.FightPlaybook, pb_combat.fake_fight, 2),
    "loot_race": Entry(pb_loot.LootRacePlaybook, pb_loot.fake_loot, 2),
    "container_share": Entry(pb_loot.ContainerSharePlaybook, pb_loot.fake_container, 2),
    "door_terminal_lock": Entry(pb_world.DoorTerminalLockPlaybook, pb_world.fake_doors, 2),
    "companion_recruit": Entry(pb_social.CompanionRecruitPlaybook, pb_social.fake_recruit, 2),
    "dialogue_spectator": Entry(pb_social.DialogueSpectatorPlaybook, pb_social.fake_dialogue, 3),
    "pnr_vote": Entry(pb_story.PnrVotePlaybook, pb_story.fake_pnr, 3),
    "pnr_vote_guest_declines": Entry(pb_story.PnrVoteGuestDeclinesPlaybook, pb_story.fake_pnr, 2),
    "quest_step_sync": Entry(pb_story.QuestStepSyncPlaybook, pb_story.fake_sync, 2),
    "fast_travel_tether": Entry(pb_world.FastTravelTetherPlaybook, pb_world.fake_travel, 2),
    "ownership_handoff": Entry(pb_combat.OwnershipHandoffPlaybook, pb_combat.fake_handoff, 2),
    "spawn_once": Entry(pb_extra.SpawnOncePlaybook, pb_extra.fake_spawn, 2),
    "vats_hit": Entry(pb_extra.VatsHitPlaybook, pb_extra.fake_vats, 2),
    "save_load_rejoin": Entry(pb_extra.SaveLoadRejoinPlaybook, pb_extra.fake_saveload, 3),
    "goodsprings_opening": Entry(pb_story.GoodspringsOpeningPlaybook, None, 2, True),
}

# Twins: (playbook, fake bug) pairs where the playbook MUST fail. Used by the tests; also the documentation of coverage.
TWINS = [
    ("fight", "dup_kill"), ("fight", "stale_echo"), ("fight", "double_spawn"), ("fight", "no_fight_sync"),
    ("fast_travel_tether", "puppet_slides_after_teleport"),
    ("fight", "puppet_slides"), ("fight", "puppet_wrong_weapon"), ("fight", "puppet_tpose"),
    ("ownership_handoff", "puppet_slides"), ("vats_hit", "puppet_wrong_weapon"),
    ("loot_race", "dup_pickup"),
    ("container_share", "no_container_sync"),
    ("door_terminal_lock", "door_unsynced"), ("door_terminal_lock", "lock_unsynced"), ("door_terminal_lock", "terminal_unsynced"),
    ("companion_recruit", "recruit_local"),
    ("dialogue_spectator", "spectator_stuck"), ("dialogue_spectator", "dialogue_not_bridged"),
    ("pnr_vote", "pnr_early"), ("pnr_vote", "ready_veto_ignored"), ("pnr_vote", "no_stage_sync"), ("pnr_vote", "endgame_ignores_notyet"), ("pnr_vote_guest_declines", "endgame_ignores_notyet"),
    ("quest_step_sync", "no_stage_sync"), ("quest_step_sync", "no_objective_sync"),
    ("fast_travel_tether", "tether_off"), ("fast_travel_tether", "consent_ignored"), ("fast_travel_tether", "travel_in_combat"),
    ("ownership_handoff", "owner_split"), ("ownership_handoff", "flap_owner"),
    ("spawn_once", "double_spawn"),
    ("vats_hit", "vats_pauses_others"), ("vats_hit", "no_fight_sync"),
    ("save_load_rejoin", "no_rebase"), ("save_load_rejoin", "reward_dup_on_rejoin"), ("save_load_rejoin", "no_stage_sync"),
    ("goodsprings_opening", "no_stage_sync"), ("goodsprings_opening", "no_objective_sync"), ("goodsprings_opening", "pnr_early"),
]
# counter_noise must be caught by every playbook (the desync counters rose).


def make(name: str, repo_root: str = "", plan=None) -> Playbook:
    e = REGISTRY[name]
    if e.needs_plan:
        return e.cls(plan=plan, repo_root=repo_root)
    return e.cls()
