"""Story moments of Lonesome Road: where the shared story changes in one way or another, with machine-checked evidence.

Each evidence item names a script label (script EDID, or "<QUEST> stage N" for stage scripts) and a regex; resolve() finds
the exact line so the policy review cites real locations. run_tests.py fails if any evidence no longer resolves.
"""
import re

import common

# kind: one-way = the party cannot undo it / later content depends on all players having seen it
MOMENTS = [
    {'id': 'dlc_entry', 'title': 'Enter the Divide at the Canyon Wreckage door', 'quests': ['NVDLC04MQ00'], 'kind': 'reversible',
     'evidence': [('NVDLC04WreckageDoorSCRIPT', r'SetStage NVDLC04MQ00 100', 'first entry completes MQ00 and starts MQ01a'),
                  ('NVDLC04WreckageDoorSCRIPT', r'Set GameDay to \(GameDay \+ 3\)', 'every entry adds 3 days to the shared clock'),
                  ('NVDLC04WreckageDoorSCRIPT', r'ShowMessage NVDLC04CompanionMSG', 'any hired companion blocks entry'),
                  ('NVDLC04MQ00SCRIPT', r'Player\.GetInCell GSDocMitchellHouse == 0', 'DLC bootstrap watches the host player only')],
     'tether': None, 'controls': 'none', 'rule': 'PartyAwareCond on the bootstrap; companion refusal must evaluate ALL hired companions of ALL players'},
    {'id': 'silo_codes', 'title': 'Hopeville Silo: launch-code note unlocks the mainframe', 'quests': ['NVDLC04MQ01a'], 'kind': 'reversible',
     'evidence': [('NVDLC04MQ01aSCRIPT', r'GetHasNote NVDLC04Silo02LaunchCode', 'stage 50 gate reads the host player\'s Pip-Boy notes'),
                  ('NVDLC04EDECommissarySCRIPT', r'GetButtonPressed', 'commissary choice is a local message box'),
                  ('NVDLC04Silo01CodeHolderSCRIPT', r'OnTriggerEnter Player', 'silo robots disabled when a player walks in')],
     'tether': None, 'controls': 'none', 'rule': 'PartyAwareCond gethasnote=any-party-member, QuestItemDup(note), CompanionOwner=party'},
    {'id': 'launch', 'title': 'Ashton Silo: ED-E arms the launch, 60 s cinematic, rocket leaves', 'quests': ['NVDLC04MQ01c', 'NVDLC04MissileLaunchQuest'],
     'kind': 'one-way',
     'evidence': [('NVDLC04LaunchControlActivatorSCRIPT', r'IsActionREF Player == 1', 'activation by "the player"'),
                  ('NVDLC04LaunchControlActivatorSCRIPT', r'DisablePlayerControl', 'fade + control lock for the activator'),
                  ('NVDLC04EDEMissileLaunchTriggerSCRIPT', r'AddScriptPackage NVDLC04EDEActivateMissileLaunch', 'ED-E walks to the console'),
                  ('NVDLC04MissileLaunchQuestSCRIPT', r'SetStage NVDLC04MQ01c 100', 'timer ends the quest'),
                  ('NVDLC04MissileLaunchQuestSCRIPT', r'EnablePlayerControls', 'host effect that must release every lock')],
     'tether': ['NVDLC04Road02World'], 'controls': 'all players locked; release = host EnablePlayerControls (120 s failsafe)',
     'rule': 'PartyTether(auto-warp) + PartyWarp(view marker) + SetPlayerControls(all) + SaveSuppress + FastTravelLock'},
    {'id': 'elevator', 'title': 'Silo 2 elevator ride (explosions, Tunnelers)', 'quests': ['NVDLC04MQ02'], 'kind': 'one-way',
     'evidence': [('NVDLC04Silo02ElevatorCodeHolderSCRIPT', r'bRideStart == 0', 'ride state machine on the object, host-run'),
                  ('NVDLC04Silo02ElevatorCodeHolderSCRIPT', r'bPlayerInterference == 0', 'ride restarts if "the player" interferes')],
     'tether': ['NVDLC04Silo2Elevator'], 'controls': 'all players locked inside the elevator cell',
     'rule': 'PartyTether(auto-warp) + SetPlayerControls(all)'},
    {'id': 'divide_door', 'title': 'Divide door 2: MQ02 stage 50, ED-E forced greet', 'quests': ['NVDLC04MQ02'], 'kind': 'one-way',
     'evidence': [('NVDLC04DivideDoor2SCRIPT', r'SetStage NVDLC04MQ02 50', 'quest advance on activation'),
                  ('NVDLC04DivideDoor2SCRIPT', r'^DisablePlayerControls', 'bare lock "to prevent blitzing past the ED-E forcegreet"')],
     'tether': None, 'controls': 'initiator only (forced greeting); others free', 'rule': 'proposal: SetPlayerControls scope=initiator'},
    {'id': 'ede_flyaway', 'title': 'ED-E flies away at the Tunnelers\' cave (MQ02a)', 'quests': ['NVDLC04MQ02a'], 'kind': 'one-way',
     'evidence': [('NVDLC04EDEFlyAwayTriggerSCRIPT', r'SetStage NVDLC04MQ02a 100', 'trigger fired by any player ghost'),
                  ('NVDLC04EDEFlyAwayTriggerSCRIPT', r'Player\.RemovePerk NVDLC04EnhancedSensors', 'companion perk removed from "the player"'),
                  ('NVDLC04EDEFlyAwayTriggerSCRIPT', r'NVDLC04EDEREF\.RemoveAllItems Player', 'ED-E inventory handed to "the player"')],
     'tether': None, 'controls': 'none', 'rule': 'CompanionOwner=party; RemovePerk must hit every player'},
    {'id': 'ulysses_greet', 'title': 'Ulysses\' forced greeting in the temple', 'quests': ['NVDLC04MQ03a', 'NVDLC04DialogueUlysses'], 'kind': 'one-way',
     'evidence': [('NVDLC04UlyssesSCRIPT', r'StartConversation Player NVDLC04UlyssesForceGreetDUPLICATE001', 'NPC-initiated, targets "Player"'),
                  ('NVDLC04TempleDoorLockTriggerSCRIPT', r'BEGIN OnTriggerEnter Player', 'iris/alarm trigger fires for the first player in')],
     'tether': ['NVDLC04DivideUlyssesTemple'], 'controls': 'initiator = the player whose trigger fired', 'rule': 'InitiatorDialogue + PartyTether(wait)'},
    {'id': 'temple_battle', 'title': 'Temple battle ends: Ulysses dead or ally, all Marked Men dead', 'quests': ['NVDLC04MQ03a', 'NVDLC04TempleMarkedMenQuest'],
     'kind': 'one-way',
     'evidence': [('NVDLC04UlyssesBattleCodeboxSCRIPT', r'SetStage NVDLC04MQ03b 10', 'win condition (enemy path)'),
                  ('NVDLC04UlyssesBattleCodeboxSCRIPT', r'iMarkedMenDeadCount == 16', 'win condition (friend path)'),
                  ('NVDLC04MQ03aSCRIPT', r'Player\.GetActorValue EnergyWeapons < 75', 'weak-player scaling reads the host player')],
     'tether': ['NVDLC04DivideUlyssesTemple'], 'controls': 'none (combat)', 'rule': 'PartyTether(wait) + IsKiller any-party-member'},
    {'id': 'marked_men', 'title': 'Marked Men waves in the temple (enemy path 8, friend path 16)', 'quests': ['NVDLC04TempleMarkedMenQuest', 'NVDLC04MQ03a'],
     'kind': 'one-way',
     'evidence': [('NVDLC04TempleMarkedMenQuestSCRIPT', r'GetHealthPercentage <= 0\.75', 'wave 1 starts when Ulysses is at 75% health or dead'),
                  ('NVDLC04TempleMarkedMenQuestSCRIPT', r'TempleBattleMMR01WeakREF\.MoveTo TempleMMSpawn01', 'waves are pre-placed actors moved to a spawn marker then enabled (weak variants for weak players)'),
                  ('NVDLC04TempleMarkedMenQuestSCRIPT', r'Set UlyssesBattleCodebox\.iMarkedMenDeadCount to', 'kill counter is an OBJECT-script variable written from a quest script'),
                  ('NVDLC04UlyssesBattleCodeboxSCRIPT', r'iMarkedMenDeadCount == 8', 'enemy-path win condition'),
                  ('NVDLC04GenericPerkQuestSCRIPT', r'Player\.AddPerk NVDLC04MarkedPerk', 'Marked perk after 3 of the 4 named Marked Men die: host player only'),
                  ('NVDLC04MQ03aSCRIPT', r'Set UlyssesBattleCodebox\.bWeakPlayer to 1', 'weak-player scaling: one player decides the fight for all')],
     'tether': ['NVDLC04DivideUlyssesTemple'], 'controls': 'none (combat); the temple is sealed until Ulysses is dealt with',
     'rule': 'PartyTether(wait). Capturable: EnableRef + MoveRef of pre-placed actors; not capturable: the counters. No party scaling'},
    {'id': 'nuke_terminal', 'title': 'Launch terminal: abort, NCR, Legion or both', 'quests': ['NVDLC04MQ03b', 'NVDLC04MQ04'], 'kind': 'one-way',
     'evidence': [('NVDLC04TempleLaunchTerminalSCRIPT', r'IsActionRef Player == 1\) && \(GetStage NVDLC04MQ03b == 10\)', 'opens the menu'),
                  ('NVDLC04TempleLaunchTerminalSCRIPT', r'Set nButton to GetButtonPressed', 'choice read on the local machine'),
                  ('NVDLC04TempleLaunchTerminalSCRIPT', r'Set NVDLC04MQ03b\.nMissileTarget to 3', 'nuke everybody'),
                  ('NVDLC04TempleLaunchTerminalSCRIPT', r'SetStage NVDLC04MQ03b 255', 'fails 03b, 04 takes over'),
                  ('NVDLC04TempleLaunchTerminalSCRIPT', r'NVDLC04EDEREF\.MoveTo NVDLC04EDEFinaleMarkerREF', 'abort path: ED-E teleports to the finale marker')],
     'tether': ['NVDLC04DivideUlyssesTemple'], 'controls': 'all players locked while the sequence runs; the chooser sees the menu',
     'rule': 'PartyTether(auto-warp) + InitiatorMenu (proposal) + ShareKarmaRep'},
    {'id': 'ede_sacrifice', 'title': 'ED-E sacrifices himself to abort the launch', 'quests': ['NVDLC04EDEFinaleQuest', 'NVDLC04MQ03b'], 'kind': 'one-way',
     'evidence': [('NVDLC04EDEFinaleQuestSCRIPT', r'NVDLC04EDEREF\.KillActor NVDLC04EDEREF', 'companion dies'),
                  ('NVDLC04EDEFinaleQuestSCRIPT', r'Player\.RemovePerk NVDLC04EnhancedSensors', 'companion perk removed from "the player"'),
                  ('NVDLC04EDEFinaleQuestSCRIPT', r'Player\.CastImmediateOnSelf NVDLC04SloMoEffect', 'slow-motion on "the player"'),
                  ('NVDLC04EDEFinaleQuestSCRIPT', r'SetStage NVDLC04MQ03b 20', 'quest advance at the end of the timer')],
     'tether': ['NVDLC04DivideUlyssesTemple'], 'controls': 'host effect timeline (about 35 s); slow-motion per player',
     'rule': 'PartyTether(wait) + SetPlayerControls(all) + CompanionOwner=party'},
    {'id': 'temple_exit', 'title': 'Temple exit door: ends the DLC story (03b->30 or 04->20)', 'quests': ['NVDLC04MQ03b', 'NVDLC04MQ04'], 'kind': 'one-way',
     'evidence': [('NVDLC04UlyssesTempleExitDoorSCRIPT', r'SetStage NVDLC04MQ03b 30', 'abort/no-nuke path'),
                  ('NVDLC04UlyssesTempleExitDoorSCRIPT', r'SetStage NVDLC04MQ04 20', 'nuke path'),
                  ('NVDLC04UlyssesTempleExitDoorSCRIPT', r'DisablePlayerControls 0 0 0 1 0 0 1', 'sneak/POV locked for the ending'),
                  ('NVDLC04UlyssesTempleExitDoorSCRIPT', r'SetEssential NVDLC04Ulysses 0', 'Ulysses becomes killable')],
     'tether': ['NVDLC04DivideUlyssesTemple'], 'controls': 'all players (sneak+POV mask)', 'rule': 'PartyTether(auto-warp) + SetPlayerControls(all)'},
    {'id': 'ending', 'title': 'Ending slideshow in the "theatre", then teleport to the Canyon Wreckage', 'quests': ['NVDLC04Ending'], 'kind': 'one-way',
     'evidence': [('NVDLC04EndingSCRIPT', r'Player\.MoveTo NVDLC04SlideShowMarkerREF', 'teleport the player to the theatre'),
                  ('NVDLC04EndingSCRIPT', r'DisablePlayerControls 1 1 1 1 1 1 1', 'total lock'),
                  ('NVDLC04EndingSCRIPT', r'Player\.AddScriptPackage NVDLC04PlayerNoSavePackage', 'no-save package on the player'),
                  ('NVDLC04SlideShowTriggerSCRIPT', r'SayTo Player NVDLC04EndingSlideStart', 'narration starts when the trigger fires'),
                  ('NVDLC04NarratorSCRIPT', r'SayToDone NVDLC04EndingSlideStart', 'branching slide chain (UlyssesDead / EDE fate / missile)')],
     'tether': None, 'controls': 'all players locked; SlideshowEnd runs once on the host, release together',
     'rule': 'SlideshowEnd + PartyWarp(absent=warp) + SetPlayerControls(all) + SaveSuppress'},
    {'id': 'completion', 'title': 'Canyon Wreckage completion trigger: the nuke lands in the Mojave', 'quests': ['NVDLC04MQ03b', 'NVDLC04MQ04'], 'kind': 'one-way',
     'evidence': [('NVDLC04CompletionTriggerSCRIPT', r'EnableFastTravel 0', 'fast travel disabled'),
                  ('NVDLC04CompletionTriggerSCRIPT', r'ForceWeather NVDLC04WastelandClear', 'weather override'),
                  ('NVDLC04CompletionTriggerSCRIPT', r'NVDLC04MQ03b\.nMissileTarget == 3', 'both factions')],
     'tether': None, 'controls': 'EnablePlayerControls (release)', 'rule': 'SetPlayerControls release + FastTravelLock'},
    {'id': 'nuked_areas', 'title': 'Nuked NCR / Legion areas unlock (boat, gate)', 'quests': ['NVDLC04MQ04'], 'kind': 'reversible',
     'evidence': [('NVDLC04MQ04 stage 100', r'NVDLC04NukedNCRGateREF\.Enable', 'gate enabled for target 1 and 3'),
                  ('NVDLC04MQ04 stage 100', r'NVDLC04CCBoatREF\.Enable', 'boat enabled for target 2 and 3'),
                  ('NVDLC04NukedLegionBoatSCRIPT', r'Player\.MoveTo rLinkedRef', 'boat teleports one player'),
                  ('NVDLC04NukedNCRGateSCRIPT', r'IsActionRef Player == 1', 'gate: non-player actors are turned back')],
     'tether': None, 'controls': 'none', 'rule': 'PartyWarp for the boat (destination marker unknown, see policy_review)'},
    {'id': 'endgame_perks', 'title': 'Endgame perks and reputation swings', 'quests': ['NVDLC04MQ03b', 'NVDLC04MQ04', 'NVDLC04GenericPerkQUEST'],
     'kind': 'one-way',
     'evidence': [('NVDLC04MQ04SCRIPT', r'Player\.AddPerk NVDLC04BearSlayerPerk', 'NCR target'),
                  ('NVDLC04MQ03bSCRIPT', r'Player\.AddPerk NVDLC04DivideSurvivorPerk', 'abort path'),
                  ('NVDLC04GenericPerkQUEST stage 70', r'AddReputationExact RepNVNCR', 'Bear-Slayer: NCR infamy set to the maximum'),
                  ('NVDLC04GenericPerkQUEST stage 50', r'AddReputationExact RepNVCaesarsLegion', 'Dead Man\'s Burden: both factions maxed')],
     'tether': None, 'controls': 'none', 'rule': 'RewardAll (perks) + ShareKarmaRep'},
]


def find_line(items, label, regex):
    """First (line, text) of the script `label` matching regex (case-insensitive), searching only non-comment code."""
    rx = re.compile(regex, re.I)
    for it in items:
        if it['label'] == label:
            for n, line in common.script_lines(it['source']):
                if rx.search(line):
                    return n, line
    return None


def resolve(items, data):
    """MOMENTS with evidence resolved to (line, text). Unresolvable evidence keeps line=None (tests fail on it)."""
    out = []
    for m in MOMENTS:
        ev = []
        for label, rx, what in m['evidence']:
            hit = find_line(items, label, rx)
            ev.append({'script': label, 'regex': rx, 'what': what, 'line': hit[0] if hit else None, 'text': common.clean(data, hit[1], 110) if hit else None})
        out.append(dict(m, evidence=ev))
    return out
