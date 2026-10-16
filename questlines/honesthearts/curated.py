"""Hand-written judgment for the Honest Hearts questline.

Everything that names a script, line, INFO, quest or marker is anchored by an *evidence* spec that build.py resolves
against the ESM data (and fails if it does not resolve), so this text cannot drift away from the data.

Evidence spec forms:
  {'script': 'EDID', 'find': 'substring'}                      first line of that script containing the substring
  {'stage': 'NVDLC02MQ00:100', 'find': 'substring'}             stage result script
  {'info': '009EB6', 'find': 'substring'}                       INFO begin/end script line
  {'info': '009EB6', 'cond': 'GetActorValue'}                   INFO condition (function name)
  {'quest_script': 'NVDLC02MQ02a', 'find': 'substring'}
"""

PURPOSE = {
    'NVDLC02MQ00': 'Happy Trails Expedition: leave the Mojave with the caravan; weight/companion gate, intro slideshow, teleport to Zion',
    'NVDLC02MQ01': 'Arrival at Zion: scripted White Legs ambush on the caravan, Jed dies, meet Follows-Chalk and Joshua',
    'NVDLC02MQ02': 'Supply Train: umbrella for the four supply errands, take the supplies to Daniel',
    'NVDLC02MQ02a': 'Roadside Attraction: find and repair a compass (workbench)',
    'NVDLC02MQ02b': "Gone Fishin': two walkie-talkies from the fishing lodge (locked cabinet)",
    'NVDLC02MQ02c': "Tourist Trap: medical supply kit parts and five Li'l Scout lunchboxes",
    'NVDLC02MQ02d': 'Deliverer of Sorrows: hand the supplies to Daniel (fires when 02a, 02b, 02c are done)',
    'NVDLC02MQ03': 'The Grand Staircase: find the map; fans out into 03a-03d',
    'NVDLC02MQ03a': 'The Advance Scouts: two White Legs totems or kill the camp',
    'NVDLC02MQ03b': 'The Treacherous Road: disarm five bear traps or kill the sappers',
    'NVDLC02MQ03c': 'River Monsters: deal with the Yao Guai at Pine Creek (optional: mine the cave and detonate it, or kill the Yao Guai)',
    'NVDLC02MQ03d': 'Gathering Storms: report to Daniel; CHOICE point between Joshua and Daniel',
    'NVDLC02MQ04': "Crush the White Legs: Joshua's path (fight at Three Marys, Salt-Upon-Wounds' fate)",
    'NVDLC02MQ05': "Flight from Zion: Daniel's path (evacuate the Sorrows, collapse the Pine Creek tunnel)",
    'NVDLC02MQ05a': 'Optional: Prisoners of War (free three Sorrows)',
    'NVDLC02MQ05b': 'Optional: Sanctity of the Dead',
    'NVDLC02MQ05c': 'Optional: Retake the Bridge',
    'NVDLC02MQ06': 'Chaos in Zion: failure fork when Joshua or Daniel dies or is attacked; fails every other quest',
    'NVDLC02MQ07': 'Departing Paradise: loot locker, return to the Mojave through the Southern Passage',
    'NVDLC02MS01': 'Rite of Passage: Sacred Datura ritual and the Ghost of She (White Bird)',
    'NVDLC02MS02': 'Bighorners of the Eastern Virgin: lost calf (Follows-Chalk)',
    'NVDLC02MS03': "Civilized Man's Burden: carry a message between Joshua and Follows-Chalk (karma -50 branch)",
    'NVDLC02MS04': 'A Family Affair: tell Waking Cloud the truth about her husband (karma -50 branch)',
    'NVDLC02Ending': 'Ending slideshow: SetInCharGen, DisablePlayerControls, MoveTo slideshow marker, narrator SayTo chain',
    'NVDLC02IntroSlides': 'Intro slideshow before the teleport to Zion (same mechanism as Ending)',
    'NVDLC02Enc': 'Random encounter master script (GetRandomPercent, GameMode proximity boxes)',
    'NVDLC02Eve': 'Random event master script (same design as Enc)',
    'NVDLC02FollowersQuest': 'Companion state flags (hired/fired/dead/available) for Follows-Chalk, Waking Cloud, Joshua',
    'NVDLC02FollowersBanter': 'Companion banter timer',
    'NVDLC02DeathTracker': 'Starts MQ06 when Joshua or Daniel is dead',
    'NVDLC02XPCalculator': 'Computes the XP globals by player level',
    'NVDLC02MQ03CodeHolderQuest': 'MQ03 trap / dialogue code holder',
    'NVDLC02MQ04ForceGreetQuest': 'MQ04 finale force-greet packages for Joshua',
    'NVDLC02MQ04Timer': 'MQ04 fade timer: Player.MoveTo, disable Daniel / Waking Cloud, GameHour 20',
    'NVDLC02MQ04Timer2': 'MQ04 finale timer: restrain prisoners, Player.MoveTo, Joshua health reset',
    'NVDLC02MQ04ZionWLEnc': 'Zion encounters for the MQ04 branch',
    'NVDLC02MQ05Timer': 'MQ05 fade timer: Player.MoveTo, Joshua force greet, GameHour 20',
    'NVDLC02MQ05Timer2': 'MQ05 climax timer: Player.MoveTo climax marker, fire Joshua, equip detonator',
    'NVDLC02MQ05ZionWLEnc': 'Zion encounters for the MQ05 branch',
    'NVDLC02GenericPerkQUEST': 'Adds the DLC war club to the melee form list every frame (GameMode)',
    'NVDLC02RadioMessage': 'Happy Trails radio broadcast',
    'NVDLC02RainQuest': 'Rain effect quest',
}

# Dialogue quests: speaker and role
DIALOGUE_NOTES = {
    'NVDLC02DialogueCaravanMaster': 'Jed: departure gate (weight limit, companions), Survival check, caravan menu',
    'NVDLC02DialogueRicky': "Ricky: Barter/Perception/Science/Speech checks; sells the weight-limit increase",
    'NVDLC02DialogueStella': 'Stella barks and arrival conversation',
    'NVDLC02DialogueDaniel': 'Daniel: Sorrows leader, healer, the Joshua vs Daniel choice',
    'NVDLC02DialogueJoshua': 'Joshua Graham: recruit, Salt-Upon-Wounds execution/spare choice, MS03',
    'NVDLC02DialogueFollowsChalk': 'Follows-Chalk: recruit / fire, civilization question (MS03)',
    'NVDLC02DialogueWakingCloud': "Waking Cloud: recruit / fire, husband's fate (MS04), White Bird",
    'NVDLC02DialogueSaltUponWounds': 'Salt-Upon-Wounds: peace talk, execution, MQ05 retreat',
    'NVDLC02DialogueShaman': 'White Bird: Sacred Datura ritual (MS01)',
}

ENTRY_NOTES = {
    'NVDLC02MQ00': 'start_enabled. Quest script waits until the player has left GSDocMitchellHouse (nEnableDLC 0->1), 5 s timer, '
                   'then SetStage 10 + radio message. Stage 30 is set by the Jed INFOs; stage 100 is set at the END of the intro slideshow.',
    'NVDLC02MQ01': 'Not start-enabled. Started by Jed INFO 00F195 ("Enough lollygagging") with SetStage 5; ambush fires from INFO 00A7BD (SetStage 10).',
    'NVDLC02MQ02': 'Not start-enabled; stage 10 is not set by any HH script (inventory triggers list shows who starts it). Treated as unknown.',
    'NVDLC02MQ03': 'Started by Daniel INFO 0104A2/0104A1/0104A4 (SetStage MQ03 10 together with 03a/03b/03c).',
    'NVDLC02MQ03d': 'Stage 10 by the MQ03 fan-out; stage 20 by Daniel INFO 0093AF; stage 100 by the choice INFOs 010718 / 010723 / 0093AA.',
    'NVDLC02MQ04': "Started by Daniel INFOs 010718 / 010723 (Joshua's path).",
    'NVDLC02MQ05': "Started by Daniel INFO 0093AA (Daniel's path).",
    'NVDLC02MQ06': 'Started by NPC scripts (Joshua / Daniel GameMode: GetDead, or OnStartCombat Player) and by NVDLC02DeathTracker.',
    'NVDLC02MQ07': 'Set by NVDLC02EndingFinale (SayToDone) when MQ06 did not run.',
    'NVDLC02Ending': 'Started by Joshua finale INFOs 0109C3 / 0109C4 / 010743 (END: StartQuest NVDLC02Ending) and by NVDLC02MojaveDoor when MQ06 runs.',
    'NVDLC02IntroSlides': 'Started by Jed INFO 009EB8 (END: StartQuest NVDLC02IntroSlides + DisablePlayerControls).',
}

# ---------------------------------------------------------------------------------------------------------------------
# Rule needs per quest beyond what the script scan derives (kind -> reason). Kinds must be among the 16 existing ones.
NEEDS = {
    'NVDLC02MQ00': {
        'PartyTether': 'all players must be at the caravan (Jed) before the departure conversation completes; the current cell GSDocMitchellHouse is the wrong place',
        'PartyWarp': 'stage 100 does Player.MoveTo NVDLC02StartMarker and the slideshow does Player.MoveTo NVDLC02SlideShowMarkerREF',
        'CompanionOwner': 'NVDLC02ZionDoorSCRIPT auto-fires Mojave companions; Jed refuses a party that is not alone',
        'PartyAwareCond': 'departure INFOs read GetActorValue InventoryWeight, companion count and perks of the player',
        'SetPlayerControls': 'INFO 009EB8 DisablePlayerControls 1 1 1 1 1 1 1; slideshow locks everything',
        'SaveSuppress': 'slideshow adds NVDLC02PlayerNoSavePackage (prevents saving)',
        'SlideshowEnd': 'MQ00 stage 100 is only set when the intro slideshow ends (NVDLC02IntroSlidesFinale)',
        'RewardAll': 'INFO 009EB8 gives 175 caps; Survival check gives 50 XP',
        'InitiatorDialogue': 'Jed conversation',
    },
    'NVDLC02MQ01': {
        'PartyTether': 'ambush scene (ZionArrivalBox states 1-5) needs the whole party at the Zion arrival point; the fight starts when the first player crosses',
        'CompanionOwner': 'Follows-Chalk is hired by INFO 00E8FA / 00F6D8 / 010D6B',
    },
    'NVDLC02MQ03d': {
        'PartyTether': 'the choice conversation with Daniel must have the whole party in the Narrows',
        'CompanionOwner': 'INFOs 010718 / 010723 / 0093AA fire Waking Cloud',
        'FastTravelLock': 'MQ03 polls EnableFastTravel 0 while the Narrows sequence runs',
    },
    'NVDLC02MQ04': {
        'PartyTether': 'finale at Three Marys: Salt-Upon-Wounds sequence (iJoshuaSequence) needs the whole party',
        'PartyWarp': 'NVDLC02MQ04Timer: Player.MoveTo NVDLC02MQ05PlayerMarkerREF; Timer2: Player.MoveTo NVDLC02MQ04PlayerMarker2REF',
        'SetPlayerControls': 'DisablePlayerControls in INFO 010718/010723 END and in Timer2',
        'SaveSuppress': 'Autosave calls; MQ04 disables fast travel for the whole branch',
        'CompanionOwner': 'stage 100 fires Joshua',
        'InitiatorDialogue': "Joshua / Salt-Upon-Wounds conversations decide the finale",
    },
    'NVDLC02MQ05': {
        'PartyTether': 'PartyTether cells are empty in rules.json; the fade timer teleports the player to NVDLC02MQ05PlayerMarkerREF',
        'CompanionOwner': 'stage 100 and Timer2 fire Joshua',
        'RewardAll': 'RewardXP NVDLC02Act3XP',
        'FastTravelLock': 'FastTravelDisabled flag re-asserted every frame in the quest script',
    },
    'NVDLC02MQ06': {
        'PartyTether': 'cells are empty in rules.json; the whole party is moved by NVDLC02Ending',
        'ShareKarmaRep': 'fails every quest and sets the factions hostile (global SetEnemy)',
        'RewardAll': 'RewardXP NVDLC02XPCalculator.iNVDLC02Act3FinalXP',
        'SaveSuppress': 'ending slideshow adds NVDLC02PlayerNoSavePackage',
        'SetPlayerControls': 'MQ06 quest script re-enables controls every frame unless NVDLC02Ending runs',
        'FastTravelLock': 'stage 10 sets EnableFastTravel 1 for everyone',
    },
    'NVDLC02MQ07': {
        'PartyTether': 'the Southern Passage door transition',
        'RewardAll': 'the reward locker is a single shared container: each player must get every unique item (policy: RewardAll, not first-come)',
    },
    'NVDLC02MQ05b': {'RewardAll': 'stage 100 RewardXP NVDLC02SecondaryQuestXP', 'InitiatorDialogue': 'Speech 75 check in INFO 010B37 decides how the Sorrows fight',
                     'SaveSuppress': 'stage 100 Autosave'},
    'NVDLC02MQ05c': {'RewardAll': 'stage 100 RewardXP NVDLC02SecondaryQuestXP', 'SaveSuppress': 'stage 100 Autosave'},
    'NVDLC02MS02': {'RewardAll': 'stage 100 rewards XP, bonus XP for sparing the Bighorners and a war club', 'SaveSuppress': 'stage 30 / 40 / 100 Autosave'},
    'NVDLC02MS01': {'SetPlayerControls': 'ritual: tea scene and poison image-space modifier are per player', 'PerPlayerScene': 'the vision / Ghost of She hunt is per-player presentation'},
    'NVDLC02MS03': {'ShareKarmaRep': 'INFOs 010BC4 / 00F931 RewardKarma -50 (shared karma moves once, not once per player)'},
    'NVDLC02MS04': {'ShareKarmaRep': 'INFO 00F93A RewardKarma -50 (shared)'},
    'NVDLC02Ending': {
        'SlideshowEnd': 'host runs the narrator chain once; everybody sees the same slides and is released together',
        'SetPlayerControls': 'DisablePlayerControls 0 0 1 0 0 0 1 then 1 1 1 1 1 1 1',
        'PartyWarp': 'Player.MoveTo NVDLC02SlideShowMarkerREF; finale Player.MoveTo NVDLC02EndingMarkerREF / NVDLC02MQ06EndingMarkerREF',
        'SaveSuppress': 'NVDLC02PlayerNoSavePackage',
        'PartyTether': 'whole party must be present when the slideshow starts',
        'CompanionOwner': 'NVDLC02SlideShowTrigger disables Follows-Chalk and Waking Cloud',
    },
    'NVDLC02IntroSlides': {
        'SlideshowEnd': 'ends with SayToDone NVDLC02IntroSlidesFinale -> SetStage NVDLC02MQ00 100',
        'SetPlayerControls': 'DisablePlayerControls 1 1 1 1 1 1 1',
        'PartyWarp': 'Player.MoveTo NVDLC02SlideShowMarkerREF',
        'SaveSuppress': 'NVDLC02PlayerNoSavePackage',
        'PartyTether': 'whole party at the caravan before the show starts',
    },
    'NVDLC02MQ04Timer': {'PartyWarp': 'Player.MoveTo NVDLC02MQ05PlayerMarkerREF', 'SetPlayerControls': 'fade + controls disabled by the choice INFO',
                         'CompanionOwner': 'the choice INFO fires Waking Cloud'},
    'NVDLC02MQ04Timer2': {'PartyWarp': 'Player.MoveTo NVDLC02MQ04PlayerMarker2REF', 'SetPlayerControls': 'DisablePlayerControls'},
    'NVDLC02MQ05Timer': {'PartyWarp': 'Player.MoveTo NVDLC02MQ05PlayerMarkerREF', 'SetPlayerControls': 'fade + controls disabled by the choice INFO'},
    'NVDLC02MQ05Timer2': {'PartyWarp': 'Player.MoveTo NVDLC02MQ05ClimaxMarkerREF', 'SetPlayerControls': 'EnablePlayerControls 0 0 1 0 0 1 0 partial release',
                          'CompanionOwner': 'FireTrigger Joshua'},
    'NVDLC02FollowersQuest': {'CompanionOwner': 'hired / fired flags are single shared variables; owner must be per companion (recruiter)'},
    'NVDLC02DialogueDaniel': {'InitiatorDialogue': 'Daniel conversations', 'RewardAll': 'XP rewards + healing powder karma',
                              'QuestItemDup': 'MQ03 quest items (fake explosives, detonator, map, totems)', 'PerPlayerScene': 'paid healing: caps removed from the patient only',
                              'SetPlayerControls': 'choice INFOs DisablePlayerControls', 'CompanionOwner': 'choice INFOs fire Waking Cloud',
                              'ShareKarmaRep': 'healing powder donations RewardKarma 5-25'},
    'NVDLC02DialogueJoshua': {'InitiatorDialogue': 'Joshua conversations', 'CompanionOwner': 'hire / fire via NVDLC02HireTriggerREF / NVDLC02FireTriggerREF',
                              'RewardAll': 'XP rewards', 'SetPlayerControls': 'finale INFOs DisablePlayerControls'},
    'NVDLC02DialogueCaravanMaster': {'InitiatorDialogue': 'Jed conversations', 'RewardAll': '175 caps, XP', 'SetPlayerControls': 'INFO 009EB8',
                                     'PartyAwareCond': 'weight and companion gate', 'QuestItemDup': 'none: caps only'},
    'NVDLC02DialogueRicky': {'InitiatorDialogue': 'Ricky conversations', 'RewardAll': 'XP; psycho / caps trades are per player',
                             'PerPlayerScene': 'trades remove items from and give caps to the initiator only'},
    'NVDLC02DialogueFollowsChalk': {'InitiatorDialogue': 'Follows-Chalk conversations', 'CompanionOwner': 'hire / fire', 'ShareKarmaRep': 'RewardKarma -50'},
    'NVDLC02DialogueWakingCloud': {'InitiatorDialogue': 'Waking Cloud conversations', 'CompanionOwner': 'hire / fire', 'ShareKarmaRep': 'RewardKarma -50'},
    'NVDLC02DialogueSaltUponWounds': {'InitiatorDialogue': 'peace talk decides MQ04 / MQ05 outcome', 'SetPlayerControls': 'EnablePlayerControls in begin script'},
    'NVDLC02DialogueShaman': {'InitiatorDialogue': 'ritual conversation', 'PerPlayerScene': 'tea / vision is per player', 'QuestItemDup': 'Sacred Datura Root hand-in'},
}

# Param fixes for rules that already exist (quest -> kind -> new params + why)
PARAM_FIXES = [
    {'quest': 'NVDLC02MQ00', 'kind': 'PartyTether', 'params': {'cells': [], 'gather': 'wait'},
     'why': 'existing cells=["GSDocMitchellHouse"] is the interior the player LEAVES to start the DLC (quest script line "Player.GetInCell GSDocMitchellHouse == 0"); '
            'a tether on that cell defers every stage request of the quest (10, 20, 30, 100) until all players are inside the house, so the DLC could never start (stage 10 is requested only once the player is OUTSIDE). '
            'The caravan is in the open Mojave, so there is no cell name to list: use an empty cell list (no spatial gate) and gather=wait until L4 supports a ref anchor (proposed param anchor_ref=NVDLC02JedREF).',
     'evidence': [{'quest_script': 'NVDLC02MQ00', 'find': 'Player.GetInCell GSDocMitchellHouse == 0'}]},
    {'quest': 'NVDLC02MQ00', 'kind': 'PartyWarp',
     'params': {'markers': ['NVDLC02StartMarker', 'NVDLC02JedStartMarker', 'NVDLC02HappyTrailsCaravanGuardAFMarkerREF',
                            'NVDLC02HappyTrailsCaravanGuardCMMarkerREF', 'NVDLC02StellaMarkerREF', 'NVDLC02RickyMarkerREF',
                            'NVDLC02SlideShowMarkerREF'],
                'mode': 'slot-offset', 'absent': 'hold'},
     'why': 'the departure also does Player.MoveTo NVDLC02SlideShowMarkerREF in the intro slideshow; only NPC markers besides StartMarker were listed',
     'evidence': [{'quest_script': 'NVDLC02IntroSlides', 'find': 'Player.MoveTo NVDLC02SlideShowMarkerREF'},
                  {'stage': 'NVDLC02MQ00:100', 'find': 'Player.MoveTo NVDLC02StartMarker'}]},
    {'quest': 'NVDLC02MQ05', 'kind': 'PartyWarp',
     'params': {'markers': ['NVDLC02SaltUponWoundsMQ05MarkerRef', 'NVDLC02MQ04TeleportMarkerREF', 'NVDLC02DanielPineCreekMarker',
                            'NVDLC02MQ05PlayerMarkerREF', 'NVDLC02MQ05ClimaxMarkerREF'],
                'mode': 'slot-offset', 'absent': 'hold'},
     'why': 'the player moves are done by NVDLC02MQ05Timer (NVDLC02MQ05PlayerMarkerREF) and NVDLC02MQ05Timer2 (NVDLC02MQ05ClimaxMarkerREF), not by the MQ05 stage scripts the markers came from',
     'evidence': [{'quest_script': 'NVDLC02MQ05Timer', 'find': 'Player.MoveTo NVDLC02MQ05PlayerMarkerREF'},
                  {'quest_script': 'NVDLC02MQ05Timer2', 'find': 'Player.MoveTo NVDLC02MQ05ClimaxMarkerREF'}]},
    {'quest': 'NVDLC02MQ05', 'kind': 'PartyTether', 'params': {'cells': [], 'gather': 'auto-warp'},
     'why': 'cells stay empty: Zion Valley is exterior worldspace NVDLC02ZionCanyon (no per-area cell name). Empty list = no spatial gate, so the "party in the same area" rule needs an L4 worldspace/anchor param (see proposed[] in rules.patch.json). Documented so nobody assumes the tether works',
     'evidence': [{'quest_script': 'NVDLC02MQ05Timer', 'find': 'Player.MoveTo NVDLC02MQ05PlayerMarkerREF'}]},
    {'quest': 'NVDLC02MQ01', 'kind': 'PartyAwareCond',
     'params': {'funcs': {'GetDistance': 'min-over-party', 'IsActionRef': 'initiator', 'IsKiller': 'any-party-member',
                          'GetInCell': 'entry:all-in,exit:any-in'}},
     'why': 'MQ01 quest script reads GetDistance from the victim ref and the ambush code reads IsKiller; GetInCell is used by the arrival box',
     'evidence': [{'quest_script': 'NVDLC02MQ01', 'find': 'GetDistance Player < 4000'}]},
    {'quest': 'NVDLC02MQ07', 'kind': 'PartyAwareCond',
     'params': {'funcs': {'GetDistance': 'min-over-party', 'GetItemCount': 'min-over-party'}},
     'why': 'stage 10 adds a unique reward to the shared locker only if Player.GetItemCount == 0; min-over-party is 0 as soon as ANY player lacks the item, so every player can get a copy (any-party-member returns 1.0/0.0 only, see rules.cpp reduceCondition)',
     'evidence': [{'stage': 'NVDLC02MQ07:10', 'find': 'Player.GetItemCount NVDLC02WeapPowerFistUnique == 0'}]},
]

# Quests absent from rules.json that need rules (quest -> [(kind, params, why)])
NEW_QUEST_RULES = {
    'NVDLC02IntroSlides': [
        ('SlideshowEnd', {'run': 'host-once', 'lock': 'all', 'release': 'together'}, 'Narrator chain SayTo Player NVDLC02IntroSlides01..12; finale sets MQ00 stage 100'),
        ('SetPlayerControls', {'scope': 'all', 'release': 'host-effect', 'timeout_s': 180, 'masks': ['1 1 1 1 1 1 1']}, 'DisablePlayerControls 1 1 1 1 1 1 1 in the quest script'),
        ('PartyWarp', {'markers': ['NVDLC02SlideShowMarkerREF'], 'mode': 'slot-offset', 'absent': 'hold'}, 'Player.MoveTo NVDLC02SlideShowMarkerREF'),
        ('SaveSuppress', {}, 'Player.AddScriptPackage NVDLC02PlayerNoSavePackage'),
        ('PartyTether', {'cells': [], 'gather': 'wait'}, 'whole party must have talked to Jed (departure) before the show; no cell name exists for the caravan'),
    ],
    'NVDLC02Ending': [
        ('SlideshowEnd', {'run': 'host-once', 'lock': 'all', 'release': 'together'}, 'Ending slideshow (NVDLC02EndingSCRIPT, narrator SayToDone chain)'),
        ('SetPlayerControls', {'scope': 'all', 'release': 'host-effect', 'timeout_s': 240, 'masks': ['0 0 1 0 0 0 1', '1 1 1 1 1 1 1']}, 'two DisablePlayerControls calls'),
        ('PartyWarp', {'markers': ['NVDLC02SlideShowMarkerREF', 'NVDLC02EndingMarkerREF', 'NVDLC02MQ06EndingMarkerREF'], 'mode': 'slot-offset', 'absent': 'hold'}, 'Player.MoveTo slideshow marker, then the ending markers'),
        ('SaveSuppress', {}, 'NVDLC02PlayerNoSavePackage'),
        ('CompanionOwner', {'owner': 'recruiter'}, 'NVDLC02SlideShowTrigger disables Follows-Chalk and Waking Cloud'),
    ],
    'NVDLC02MQ04Timer': [
        ('PartyWarp', {'markers': ['NVDLC02MQ05PlayerMarkerREF', 'NVDLC02MQ05WakingCloudMarkerREF'], 'mode': 'slot-offset', 'absent': 'hold'}, 'Player.MoveTo NVDLC02MQ05PlayerMarkerREF after the 30 s fade'),
        ('SetPlayerControls', {'scope': 'all', 'release': 'host-effect', 'timeout_s': 120, 'masks': []}, 'controls are disabled by the choice INFO END script'),
    ],
    'NVDLC02MQ04Timer2': [
        ('PartyWarp', {'markers': ['NVDLC02MQ04PlayerMarker2REF'], 'mode': 'slot-offset', 'absent': 'hold'}, 'Player.MoveTo NVDLC02MQ04PlayerMarker2REF'),
        ('SetPlayerControls', {'scope': 'all', 'release': 'host-effect', 'timeout_s': 120, 'masks': []}, 'DisablePlayerControls in the first timer branch'),
    ],
    'NVDLC02MQ05Timer': [
        ('PartyWarp', {'markers': ['NVDLC02MQ05PlayerMarkerREF', 'NVDLC02MQ05WakingCloudMarkerREF', 'NVDLC02DanielPineCreekMarker'], 'mode': 'slot-offset', 'absent': 'hold'}, 'Player.MoveTo NVDLC02MQ05PlayerMarkerREF after the 30 s fade'),
        ('SetPlayerControls', {'scope': 'all', 'release': 'host-effect', 'timeout_s': 120, 'masks': []}, 'controls are disabled by the choice INFO END script'),
    ],
    'NVDLC02MQ05Timer2': [
        ('PartyWarp', {'markers': ['NVDLC02MQ05ClimaxMarkerREF'], 'mode': 'slot-offset', 'absent': 'hold'}, 'Player.MoveTo NVDLC02MQ05ClimaxMarkerREF'),
        ('SetPlayerControls', {'scope': 'all', 'release': 'host-effect', 'timeout_s': 120, 'masks': []}, 'EnablePlayerControls 0 0 1 0 0 1 0'),
        ('CompanionOwner', {'owner': 'recruiter'}, 'FireTrigger Joshua'),
    ],
    'NVDLC02FollowersQuest': [
        ('CompanionOwner', {'owner': 'recruiter'}, 'hired / fired flags per companion; hire dialogue lives in this quest too'),
        ('InitiatorDialogue', {}, 'hire / fire conversations'),
    ],
    'NVDLC02DialogueDaniel': [
        ('InitiatorDialogue', {}, 'choice point and healer conversations'),
        ('RewardAll', {}, 'XP rewards in INFO begin scripts'),
        ('QuestItemDup', {'recipient': 'all'}, 'MQ03 quest items'),
        ('SetPlayerControls', {'scope': 'all', 'release': 'host-effect', 'timeout_s': 120, 'masks': []}, 'choice INFOs DisablePlayerControls'),
        ('CompanionOwner', {'owner': 'recruiter'}, 'choice INFOs fire Waking Cloud'),
        ('ShareKarmaRep', {}, 'healing powder donations RewardKarma'),
    ],
    'NVDLC02DialogueJoshua': [
        ('InitiatorDialogue', {}, 'Joshua conversations'),
        ('CompanionOwner', {'owner': 'recruiter'}, 'hire / fire'),
        ('RewardAll', {}, 'XP rewards'),
        ('SetPlayerControls', {'scope': 'all', 'release': 'host-effect', 'timeout_s': 120, 'masks': []}, 'finale INFOs lock controls'),
    ],
    'NVDLC02DialogueCaravanMaster': [
        ('InitiatorDialogue', {}, 'Jed conversations'),
        ('RewardAll', {}, '175 caps, Survival XP'),
        ('SetPlayerControls', {'scope': 'all', 'release': 'host-effect', 'timeout_s': 120, 'masks': ['1 1 1 1 1 1 1']}, 'INFO 009EB8'),
        ('PartyAwareCond', {'funcs': {'HasPerk': 'initiator', 'GetIsSex': 'initiator'}}, 'departure gate: the companion refusals read shared VNPCFollowers quest variables (already party-wide); the InventoryWeight comparison needs the proposed max-over-party reduction (any-party-member returns 1.0/0.0 and cannot feed >= 76)'),
    ],
    'NVDLC02DialogueRicky': [
        ('InitiatorDialogue', {}, 'Ricky conversations'),
        ('RewardAll', {}, 'XP for skill checks'),
        ('PerPlayerScene', {}, 'psycho / caps trades affect the initiator only'),
    ],
    'NVDLC02DialogueFollowsChalk': [
        ('InitiatorDialogue', {}, 'Follows-Chalk conversations'),
        ('CompanionOwner', {'owner': 'recruiter'}, 'hire / fire'),
        ('ShareKarmaRep', {}, 'RewardKarma -50 branches'),
    ],
    'NVDLC02DialogueWakingCloud': [
        ('InitiatorDialogue', {}, 'Waking Cloud conversations'),
        ('CompanionOwner', {'owner': 'recruiter'}, 'hire / fire'),
        ('ShareKarmaRep', {}, 'RewardKarma -50 branch'),
    ],
    'NVDLC02DialogueSaltUponWounds': [
        ('InitiatorDialogue', {}, 'peace talk decides the finale'),
        ('SetPlayerControls', {'scope': 'all', 'release': 'host-effect', 'timeout_s': 120, 'masks': []}, 'EnablePlayerControls in the begin script'),
    ],
    'NVDLC02DialogueShaman': [
        ('InitiatorDialogue', {}, 'ritual conversation'),
        ('PerPlayerScene', {}, 'tea / vision per player'),
        ('QuestItemDup', {'recipient': 'all'}, 'Sacred Datura Root hand-in (RemoveItem 3 per player, see RemoveItemPerOwner)'),
    ],
}

# New reductions / params that the existing 16 kinds cannot express (justified proposals, NOT in rules.patch.json 'patches')
PROPOSED = [
    {'id': 'reduction:sum-over-party', 'applies_to': 'PartyAwareCond',
     'why': 'MQ03a needs 2 totems (GetItemCount NVDLC02WhiteLegTotem >= 2), MS01 needs 3 roots, MQ02c needs 5 lunchboxes, MQ02b needs 2 walkie-talkies. '
            'Loot is picked up through containers (not AddItem effects), so in a party the pieces are spread across players. any-party-member returns only 1.0/0.0 (rules.cpp reduceCondition) and could never reach the count; '
            'a pooled count (sum over the party) makes the quest advance the way it does for one player.',
     'evidence': [{'quest_script': 'NVDLC02MQ03a', 'find': 'Player.GetItemCount NVDLC02WhiteLegTotem >= 2'},
                  {'quest_script': 'NVDLC02MS01', 'find': 'Player.GetItemCount NVDLC02SacredDaturaRoot >= 3'},
                  {'quest_script': 'NVDLC02MQ02c', 'find': 'NVDLC02LilScoutLunch >= 5'},
                  {'quest_script': 'NVDLC02MQ02b', 'find': 'NVDLC02WalkieTalkie >= 2'}]},
    {'id': 'reduction:max-over-party', 'applies_to': 'PartyAwareCond',
     'why': 'The Jed departure gate compares GetActorValue InventoryWeight with 76 / 101. Reductions today are initiator, any-party-member (returns 1.0 or 0.0, so a later >= 76 compare fails), '
            'min-over-party and entry/exit; none returns the largest numeric value. A numeric max-over-party makes the INFO refuse while ANY player is overweight.',
     'evidence': [{'info': '009EB6', 'cond': 'GetActorValue'}, {'info': '011525', 'cond': 'GetActorValue'}]},
    {'id': 'kind:ChoiceConsent', 'applies_to': 'InitiatorDialogue',
     'params': {'topics': 'list of INFO refs that fork the story', 'mode': 'party-vote|host-decides|initiator-only', 'timeout_s': 90},
     'why': 'Daniel INFOs 010718/010723 (Joshua path, StartQuest NVDLC02MQ04) vs 0093AA (Daniel path, StartQuest NVDLC02MQ05) fork the DLC irreversibly and fail the side quests; '
            'initiator-picks lets one player decide for everyone. A consent step (default party-vote, tie goes to the host) fits the "party = one Courier" principle without a new owner decision.',
     'evidence': [{'info': '010718', 'find': 'StartQuest NVDLC02MQ04'}, {'info': '0093AA', 'find': 'StartQuest NVDLC02MQ05'}]},
    {'id': 'param:anchor_ref', 'applies_to': 'PartyTether',
     'params': {'anchor_ref': 'EDID of a placed ref', 'radius': 'game units'},
     'why': 'Zion Valley, the caravan and the Narrows are open worldspace (NVDLC02ZionCanyon) or Mojave exterior: there is no cell name for PartyTether.cells. '
            'A ref anchor + radius (NVDLC02JedREF, NVDLC02DanielREF, NVDLC02JoshuaREF) lets the tether work outdoors.',
     'evidence': [{'script': 'NVDLC02EncMasterScript', 'find': 'Player.GetInWorldspace NVDLC02ZionCanyon'}]},
    {'id': 'kind:FriendlyFireGuard', 'applies_to': 'ExcludeFromGate',
     'params': {'actors': ['NVDLC02JoshuaREF', 'NVDLC02DanielREF'], 'action': 'ignore OnStartCombat from party members unless the host confirms'},
     'why': 'Joshua and Daniel scripts start MQ06 (fail the whole DLC) on OnStartCombat Player; any one player (or a stray grenade) can end the DLC for everybody. '
            'Needs a confirm / ignore policy; not expressible with the existing kinds.',
     'evidence': [{'script': 'NVDLC02JoshuaSCRIPT', 'find': 'BEGIN OnStartCombat Player'}, {'script': 'NVDLC02DanielSCRIPT', 'find': 'BEGIN OnStartCombat Player'}]},
]

# Story moments: barrier / tether / cinematic control / reward routing (policy_review.md section 3)
MOMENTS = [
    {'id': 'M-entry', 'moment': 'Departure with the caravan (Jed, weight and companion gate)', 'quests': ['NVDLC02MQ00', 'NVDLC02DialogueCaravanMaster', 'NVDLC02IntroSlides'],
     'barrier': 'every player must satisfy the gate (<= 75 lb, or <= 100 lb after the Survival/Barter bribe, and no companion) before the INFO 009EB8 can run',
     'tether': 'whole party at the caravan (anchor NVDLC02JedREF)', 'cinematic_control': 'host runs the slideshow once (SlideshowEnd), everyone locked, released together',
     'reward': '175 caps (INFO 009EB8) and 50 XP (Survival check, INFO 009EB9) to every player (RewardAll)'},
    {'id': 'M-ambush', 'moment': 'Caravan ambush on arrival at Zion', 'quests': ['NVDLC02MQ01'],
     'barrier': 'none: MQ01 stage 10 is reached by the scripted INFO 00A7BD, not by a player',
     'tether': 'party at the arrival point (the MQ00 stage 100 destination NVDLC02StartMarker); late players are auto-gathered there', 'cinematic_control': 'the scene is host-owned NPC choreography; players keep control after Stella first line (bSceneStarts)',
     'reward': 'quest XP (RewardXP NVDLC02Act1XP) to every player'},
    {'id': 'M-choice', 'moment': 'Joshua vs Daniel (end of Gathering Storms)', 'quests': ['NVDLC02MQ03d', 'NVDLC02DialogueDaniel'],
     'barrier': 'ChoiceConsent (proposed) across the party before INFO 010718 / 010723 / 0093AA may run',
     'tether': 'whole party in the Narrows (anchor NVDLC02DanielREF)', 'cinematic_control': 'fade + DisablePlayerControls + 30 s timer, then party-warp to NVDLC02MQ05PlayerMarkerREF; release when the timer script finishes',
     'reward': 'RewardXP NVDLC02XPCalculator.iNVDLC02Act2FinalXP to every player at MQ03d stage 20; the fork itself gives no reward'},
    {'id': 'M-SUW', 'moment': 'Salt-Upon-Wounds: execute, spare or fight (Joshua path)', 'quests': ['NVDLC02MQ04', 'NVDLC02DialogueJoshua', 'NVDLC02DialogueSaltUponWounds'],
     'barrier': 'Speech 65 / 90 checks use the initiator only (initiator-picks); the outcome (iJoshuaSequence 8, 9, 10) changes the ending slide for everyone',
     'tether': 'whole party at Three Marys', 'cinematic_control': 'DisablePlayerControls during the execution; controls return by script in the begin/end of INFOs 00F2AC / 00F2AD',
     'reward': 'XP 65 / 90 to every player; karma shared'},
    {'id': 'M-flight', 'moment': 'Flight from Zion (Daniel path) and the Pine Creek tunnel', 'quests': ['NVDLC02MQ05', 'NVDLC02MQ05Timer2'],
     'barrier': 'Ending INFOs 01011D / 01011E compare MQ05 quest variable #5 with 3 (many/few Sorrows saved); MQ05a and MQ05b stage 100 increment nNumOptionalQuestsDone. The mapping of variable index to name is unverified, but the optional rescues change the ending slide for everyone',
     'tether': 'whole party at Pine Creek for the detonator', 'cinematic_control': 'Timer2: Player.MoveTo climax marker, EquipItem detonator on the player: party-warp + per-player equip',
     'reward': 'RewardXP NVDLC02Act3XP to every player; NVDLC02WeapNVDetonatorPineCreek is a quest item (duplicate)'},
    {'id': 'M-ending', 'moment': 'Ending slideshow', 'quests': ['NVDLC02Ending', 'NVDLC02MQ06', 'NVDLC02MQ07'],
     'barrier': 'start only when every player is present (PartyTether) or has been auto-gathered',
     'tether': 'whole party', 'cinematic_control': 'host runs the narrator chain once; SayTo Player is addressed to the host player; guests need Lines/SwapTexture replicated (SlideshowEnd)',
     'reward': 'final XP and the MQ07 reward locker: every player gets every unique item'},
]

# Owner-decision checks (policy_review.md section 2)
OWNER_CHECKS = [
    {'decision': 'Karma shared', 'verdict': 'ok with one hazard',
     'finding': 'RewardKarma -50 in INFOs 010BC4, 00F931 (MS03) and 00F93A (MS04), +5 / +15 / +25 or 5 per powder for the healing powder donations in INFOs 010F50, 010F51, 00A66A, 00A66D: one delta for the party, not one per player. '
                'Rule ShareKarmaRep is present for no HH quest in rules.json; the dialogue quests carry no rules at all.',
     'evidence': [{'info': '010BC4', 'find': 'RewardKarma -50'}, {'info': '00F93A', 'find': 'RewardKarma -50'}, {'info': '010F50', 'find': 'rewardkarma 25'}]},
    {'decision': 'Faction reputation shared', 'verdict': 'ok',
     'finding': 'No ModReputation in HH scripts. Faction membership of the player (NVDLC02PlayerFaction, stage 10 of MQ00) is a shared faction rank per the coordinator decision; '
                'there is no SetFactionRank capture on the host (blocker B7 in the status file).',
     'evidence': [{'stage': 'NVDLC02MQ00:10', 'find': 'Player.AddToFaction NVDLC02PlayerFaction 1'}]},
    {'decision': 'Quest items duplicated to every party member', 'verdict': 'gap',
     'finding': 'Quest items enter the inventory by looting containers, not by AddItem effects, and several quest scripts count items on the host player only '
                '(Player.GetItemCount in the MQ02a, MQ02b, MQ02c, MQ03, MQ03a, MQ05a and MS01 quest scripts). The QuestItemDup rule exists for several ruled quests but nothing pools counts.',
     'evidence': [{'quest_script': 'NVDLC02MQ03a', 'find': 'Player.GetItemCount NVDLC02WhiteLegTotem >= 2'}, {'quest_script': 'NVDLC02MS01', 'find': 'Player.GetItemCount NVDLC02SacredDaturaRoot >= 3'}]},
    {'decision': 'Caps/XP quest rewards: every player gets the full reward', 'verdict': 'ok in stages, gap in object scripts',
     'finding': 'RewardXP in stage scripts is captured and RewardAll applies (MQ01, MQ02, MQ02a-d, MQ03-MQ05, MQ06). The caps (INFO 009EB8, Player.AddItem Caps001 175) live in a dialogue quest without rules. '
                'The MQ07 reward is a single shared container (NVDLC02RewardFootLockerREF) filled once by the host player facts.',
     'evidence': [{'info': '009EB8', 'find': 'Player.AddItem Caps001 175'}, {'stage': 'NVDLC02MQ07:10', 'find': 'NVDLC02RewardFootLockerREF.AddItemHealthPercent NVDLC02WeapPowerFistUnique'}]},
    {'decision': 'Dialogue: initiator chooses, everyone sees line + result', 'verdict': 'breaks at three points',
     'finding': '(1) the departure gate reads the initiator only, (2) the Joshua / Daniel fork and the Salt-Upon-Wounds outcome are irreversible and decided by one player, '
                '(3) skill checks (Speech 65/90, Barter, Survival 50) use the initiator, so the best talker must start the conversation: acceptable, but the party cannot see why a branch was closed.',
     'evidence': [{'info': '009EB6', 'cond': 'GetActorValue'}, {'info': '0093AA', 'find': 'StartQuest NVDLC02MQ05'}]},
    {'decision': 'Party spread: free roam, story-critical moments need the party in the same area', 'verdict': 'rules incomplete',
     'finding': 'PartyTether cells are empty for MQ02d, MQ05, MQ06 and wrong for MQ00. Zion is open worldspace, so cell names cannot express the tether; an anchor/radius param is proposed.',
     'evidence': [{'quest_script': 'NVDLC02MQ00', 'find': 'Player.GetInCell GSDocMitchellHouse == 0'}]},
    {'decision': 'Companions: default owned by the recruiter (open)', 'verdict': 'gap',
     'finding': 'NVDLC02HireTriggerREF / NVDLC02FireTriggerREF use one set of shared flags (NVDLC02FollowersQuest.bFollowsChalkHired ...) and Player.AddPerk / RemovePerk. '
                'Two players cannot each hold a different companion, perks go to the Player reference of the machine that runs the script, and NVDLC02ZionDoorSCRIPT fires the Mojave companions of the host only.',
     'evidence': [{'script': 'NVDLC02HireTriggerSCRIPT', 'find': 'Player.AddPerk NVDLC02WellStackedCairns'}, {'script': 'NVDLC02ZionDoorSCRIPT', 'find': 'CraigBooneREF.SetPlayerTeammate 0'}]},
]

# Top 10 blockers: metrics are filled from the data at build time with str.format(**m)
BLOCKERS = [
    {'id': 'B1', 'severity': 'blocker', 'lane': 'L5/L10', 'title': 'Most Honest Hearts logic runs outside any capture scope',
     'text': 'Capture scopes exist only inside TESQuest::SetStage and TESTopicInfo::RunResultScript (capture_hub.h; mutations outside are counted as droppedNoScope). '
             'In HonestHearts.esm {ambient_mutators} mutating calls with an Effect type sit in quest GameMode blocks and object scripts (timers, doors, triggers, OnDeath / OnStartCombat): '
             '{ambient_story} change story or player state (objectives, quests, items, AV, globals, player MoveTo, SetScriptVar) and {ambient_presence} only change NPC presence '
             '(Enable / Disable / Kill / Resurrect, which NV:MP actor sync may cover: unknown). '
             'Examples: the fade timers that Player.MoveTo and Disable Daniel / Waking Cloud, NVDLC02ZionDoorSCRIPT, the ambush choreography. The story and player part would never reach a guest.',
     'evidence': [{'quest_script': 'NVDLC02MQ04Timer', 'find': 'Player.MoveTo NVDLC02MQ05PlayerMarkerREF'}, {'script': 'NVDLC02ZionDoorSCRIPT', 'find': 'CraigBooneREF.SetPlayerTeammate 0'},
                  {'quest_script': 'NVDLC02Ending', 'find': 'Player.MoveTo NVDLC02SlideShowMarkerREF'}],
     'fix': 'open an Intent/Ambient capture scope around quest-script blocks and trigger/activate/death events that mutate shared state (L5), and map them to the quest in Cause.source (L4 rules)'},
    {'id': 'B2', 'severity': 'blocker', 'lane': 'L8/L9/L4', 'title': 'Caravan entry gate reads one player; existing MQ00 tether can deadlock',
     'text': 'Jed refuses departure with GetActorValue InventoryWeight >= 76 (>= 101 after the Survival 50 or Barter bribe) or with companions, and ZionDoor fires the Mojave companions flagged in VNPCFollowers (one shared set of quest variables). '
             'Conditions are evaluated for the initiator only, so non-initiators arrive overloaded or with companions. rules.json also tethers MQ00 to GSDocMitchellHouse, the house the player leaves to start the DLC, '
             'so every MQ00 stage request would be deferred.',
     'evidence': [{'info': '009EB6', 'cond': 'GetActorValue'}, {'info': '011525', 'cond': 'GetActorValue'}, {'quest_script': 'NVDLC02MQ00', 'find': 'Player.GetInCell GSDocMitchellHouse == 0'}],
     'fix': 'proposed max-over-party reduction for GetActorValue InventoryWeight on NVDLC02DialogueCaravanMaster (the existing any-party-member returns 1.0/0.0 and cannot feed a >= 76 comparison); replace the MQ00 tether (rules.patch.json)'},
    {'id': 'B3', 'severity': 'blocker', 'lane': 'L5/L4/L6', 'title': 'The arrival ambush is an 11-state host choreography with player-specific triggers',
     'text': 'After the intro slideshow MQ00 stage 100 teleports the player and the caravan NPCs, lifts their essential and ghost flags and locks controls; Jed INFO 00F195 releases them and starts MQ01. '
             'NVDLC02ZionArrivalCodeHolderScript then steps through iState 1-11 in GameMode (enable / ghost the White Legs refs, PlaceAtMe grenade explosions, KillActor on Jed party members), '
             'MQ01 objective 10 completes only when every ambusher ref is dead, and a kill by the player flips bPlayerInterference through the guard OnDeath (IsKiller Player) which short-circuits the scene. '
             'IsKiller Player and the activator are the host player; a guest kill reaches the host as a synced death with no host-player killer (L6 notes that ghost kills run death scripts on the guest, where the gate suppresses them), so the interference branch is unreliable (unverified in game). '
             'Killing a caravan guard before stage 100 (the CM2 OnDeath block) fails MQ00 with stage 255.',
     'evidence': [{'stage': 'NVDLC02MQ00:100', 'find': 'SetEssential NVDLC02Jed 0'}, {'script': 'NVDLC02ZionArrivalCodeHolderScript', 'find': 'PlaceAtMe 25mmGrenadeExplosion'},
                  {'script': 'NVDLC02HappyTrailsCaravanGuardCM2SCRIPT', 'find': 'If IsKiller Player == 1'}, {'script': 'NVDLC02HappyTrailsCaravanGuardCM2SCRIPT', 'find': 'SetStage NVDLC02MQ00 255'},
                  {'quest_script': 'NVDLC02MQ01', 'find': 'NVDLC02WhiteLegsTribalMQ01HRef.GetDead == 1'}],
     'fix': 'PartyAwareCond IsKiller any-party-member, PartyTether at the arrival marker, host-only scene ownership; test: ambush finishes once for 2 players whichever one fires the first shot'},
    {'id': 'B4', 'severity': 'blocker', 'lane': 'L5/L4', 'title': 'Companions: SetPlayerTeammate and perks are neither captured nor applied',
     'text': 'HireTrigger / FireTrigger call SetPlayerTeammate, AddToFaction FollowerFaction and Player.AddPerk / RemovePerk; the hooks for these are guest-suppression only and the applier has no case for CompanionOwner, SetPerk or SetFactionRank. '
             'Hired/fired flags are single shared quest variables, so only one hirer per companion works, and the perk lands on whichever Player reference runs the script. '
             'Follows-Chalk and Waking Cloud swap each other out, Joshua is auto-fired at MQ04 / MQ05 stage 100 and MQ06 stage 10.',
     'evidence': [{'script': 'NVDLC02HireTriggerSCRIPT', 'find': 'NewFollower.SetPlayerTeammate 1'}, {'script': 'NVDLC02FireTriggerSCRIPT', 'find': 'Player.RemovePerk NVDLC02WellStackedCairns'},
                  {'stage': 'NVDLC02MQ04:100', 'find': 'NVDLC02FireTriggerREF.Activate NVDLC02JoshuaREF 1'}],
     'fix': 'capture + apply CompanionOwner / SetPerk / SetFactionRank (L5); per-owner perk routing (L4 CompanionOwner rule exists)'},
    {'id': 'B5', 'severity': 'blocker', 'lane': 'L8/L4', 'title': 'Joshua vs Daniel is an irreversible fork decided by one initiator',
     'text': 'Daniel INFOs 010718 and 010723 start MQ04 (Joshua), INFO 0093AA starts MQ05 (Daniel). Both END scripts disable controls, fade, fire Waking Cloud and arm a 30 s timer that moves the player; '
             'MQ04 stage 10 and MQ05 stage 20 fail any running side quest MS01-MS04 for everyone. Initiator-picks lets one player choose for the party.',
     'evidence': [{'info': '010718', 'find': 'StartQuest NVDLC02MQ04'}, {'info': '0093AA', 'find': 'StartQuest NVDLC02MQ05'}, {'stage': 'NVDLC02MQ04:10', 'find': 'SetStage NVDLC02MS03 255'}],
     'fix': 'ChoiceConsent (proposed kind) with party-vote default; test: both outcomes produce identical shared state on every client'},
    {'id': 'B6', 'severity': 'blocker', 'lane': 'L5/L4/L10', 'title': 'Intro / ending slideshows and scripted teleports are uncaptured and have no rules',
     'text': 'NVDLC02IntroSlides and NVDLC02Ending (SetInCharGen 1, DisablePlayerControls 1 1 1 1 1 1 1, Player.AddScriptPackage NVDLC02PlayerNoSavePackage, Player.MoveTo the slide marker) '
             'and the four timers NVDLC02MQ04Timer, Timer2, MQ05Timer, Timer2 have no entry in rules.json. DisablePlayerControls / EnablePlayerControls are not hooked (located only). '
             'The narrator addresses SayTo Player, which is the host player.',
     'evidence': [{'quest_script': 'NVDLC02Ending', 'find': 'SetInCharGen 1'}, {'quest_script': 'NVDLC02IntroSlides', 'find': 'DisablePlayerControls 1 1 1 1 1 1 1'}, {'script': 'NVDLC02NarratorSCRIPT', 'find': 'SetStage NVDLC02MQ00 100'}],
     'fix': 'rules.patch.json adds SlideshowEnd / SetPlayerControls / PartyWarp / SaveSuppress to the six quests; hook Cmd_Disable/EnablePlayerControls (L5)'},
    {'id': 'B7', 'severity': 'high', 'lane': 'L5/L4', 'title': 'Quest scripts poll the host player inventory; pooled counts are impossible today',
     'text': 'Seven quest scripts advance on Player.GetItemCount (totems >= 2, roots >= 3, lunchboxes >= 5, walkie-talkies >= 2, compass, medical kit, map). Loot pickups are not AddItem effects, '
             'so the host cannot see items other players found; QuestItemDup only duplicates scripted AddItem.',
     'evidence': [{'quest_script': 'NVDLC02MQ03a', 'find': 'Player.GetItemCount NVDLC02WhiteLegTotem >= 2'}, {'quest_script': 'NVDLC02MS01', 'find': 'Player.GetItemCount NVDLC02SacredDaturaRoot >= 3'}],
     'fix': 'PartyAwareCond reduction sum-over-party (proposed) for GetItemCount on these quests, plus a player-fact hook for Player.GetItemCount'},
    {'id': 'B8', 'severity': 'high', 'lane': 'L5', 'title': 'Faction, perk, lock and spawn effects have no host capture',
     'text': 'AddToFaction / RemoveFromFaction ({faction_calls} calls), AddPerk / RemovePerk, Lock / Unlock and PlaceAtMe ({spawn_calls} calls: ambush grenades, rockslides, generators) are not captured on the host '
             '(Round 4 hooks are guest suppression only) and SetFactionRank / SetPerk / SetLock have no applier case.',
     'evidence': [{'stage': 'NVDLC02MQ00:10', 'find': 'Player.AddToFaction NVDLC02PlayerFaction 1'}, {'script': 'NVDLC02RockslideScript', 'find': 'MarysExplosionMarker.PlaceAtMe'}],
     'fix': 'capture SetFactionRank / SetPerk / SetLock on the host and add applier cases; decide a PlaceAtMe tracker (spawn effect)'},
    {'id': 'B9', 'severity': 'high', 'lane': 'L5/L10', 'title': 'Fast travel locks and autosaves are enforced by the scripts, not by us',
     'text': 'MQ03, MQ04, MQ05 keep fast travel off by re-asserting EnableFastTravel 0 every GameMode frame (flag FastTravelDisabled) and clear it in MQ05 stage 100 / MQ06 stage 10 / MQ07 stage 10; '
             '{autosave_calls} Autosave calls and the NVDLC02PlayerNoSavePackage package guard the slideshows. The commands are not hooked (located only), so SetFastTravelLock / SaveSuppress are never emitted.',
     'evidence': [{'quest_script': 'NVDLC02MQ04', 'find': 'EnableFastTravel 0'}, {'stage': 'NVDLC02MQ05:30', 'find': 'Autosave'}],
     'fix': 'hook Cmd_EnableFastTravel and Autosave (L5); rules FastTravelLock / SaveSuppress then work as designed'},
    {'id': 'B10', 'severity': 'high', 'lane': 'L4/L9', 'title': 'One player can fail the whole DLC (MQ06 and global SetEnemy)',
     'text': 'Joshua and Daniel scripts run OnStartCombat Player / GetDead -> StartQuest MQ06 + SetStage MQ06 10, which fails every running quest of the DLC (up to {mq06_fail_count}), strips quest items, fires companions and sets '
             'NVDLC02PlayerFaction hostile to the Dead Horses and Sorrows (SetEnemy is global engine state with no sync). Friendly fire from any party member or a stray grenade triggers it.',
     'evidence': [{'script': 'NVDLC02JoshuaSCRIPT', 'find': 'BEGIN OnStartCombat Player'}, {'stage': 'NVDLC02MQ06:10', 'find': 'SetEnemy NVDLC02PlayerFaction NVDLC02DeadHorseFaction'}],
     'fix': 'FriendlyFireGuard (proposed); SetEnemy/SetAlly state needs a sync path; MQ06 gets a ChoiceConsent-style host confirm'},
]

# Real but ranked below the top 10 (kept in blockers.json "also")
ALSO = [
    {'id': 'A1', 'severity': 'medium', 'title': 'Time skips: +14 days is a delta applied by several scripts',
     'text': 'ZionDoor, MojaveDoor, Narrator and MQ00 stage 100 each add 14 days with SetGlobal GameDay. A delta written by whichever machine runs the script is applied once per machine; '
             'SetGlobal is host-wins, so only the host may run them and the date must be baselined.',
     'evidence': [{'script': 'NVDLC02ZionDoorSCRIPT', 'find': 'Set GameDay to (GameDay + 14)'}, {'script': 'NVDLC02MojaveDoorSCRIPT', 'find': 'Set GameDay To ( GameDay + 14 )'}]},
    {'id': 'A2', 'severity': 'medium', 'title': 'MQ07 reward locker is one shared container',
     'text': 'Stage 10 fills NVDLC02RewardFootLockerREF with each unique item that the host player does not already own. The first player to loot it takes the item; RewardAll needs one copy per player.',
     'evidence': [{'stage': 'NVDLC02MQ07:10', 'find': 'NVDLC02RewardFootLockerREF.AddItemHealthPercent NVDLC02WeapPowerFistUnique'}]},
    {'id': 'A3', 'severity': 'medium', 'title': 'Perk and gear form lists are initialised by quest scripts that guests do not run',
     'text': '{formlist_calls} AddFormToFormList / AddItemToLeveledList calls in NVDLC02MQ00 and NVDLC02GenericPerkQUEST; guests skip quest scripts, so DLC weapons never join the Cowboy / Pyromaniac / Laser Commander lists on guests.',
     'evidence': [{'quest_script': 'NVDLC02GenericPerkQUEST', 'find': 'AddFormToFormList WeaponListMeleeALL NVDLC02WeapWarClub'}]},
    {'id': 'A4', 'severity': 'low', 'title': 'Radio: the DLC switches the Mojave radio stations off and on',
     'text': 'NVDLC02IntroSlides and ZionDoor disable RNVTARef / RadioBlackMountainTAREF and the country radio and call PipBoyRadioOff; MojaveDoor re-enables them. The radio state is world state but the Pip-Boy playback is per player.',
     'evidence': [{'quest_script': 'NVDLC02IntroSlides', 'find': 'PipBoyRadioOff'}, {'script': 'NVDLC02MojaveDoorSCRIPT', 'find': 'RNVTARef.Enable'}]},
]

# Conversations for dialogue.md / dialogue_specs.json.  info ids are local ids of HonestHearts.esm INFOs;
# 'starts' gives the first words of the NPC line (checked against the ESM).
CONVERSATIONS = [
    {'id': 'dlg_hh_jed_departure_gate', 'name': 'Jed: ready to leave? (weight limit, companions, Survival check)', 'quest': 'NVDLC02DialogueCaravanMaster',
     'npc_base': 'HonestHearts.esm:009E7B', 'greeting': '009657',
     'summary': 'The departure INFOs read the initiator (InventoryWeight, companion count, perks). In a party, every member must pass, otherwise a non-initiator can arrive overloaded or with companions.',
     'infos': [
         ('009EB6', 'I bet you are, but you ain', 'refusal: InventoryWeight >= 76 (caps at 100 lb after the raise)'),
         ('011525', 'I bet you are, but you ain', 'refusal: InventoryWeight >= 101 (after the raise)'),
         ('009EB4', 'I bet you are, but we only got room', 'refusal: two companions'),
         ('009EB5', 'I bet you are, but we only got room', 'refusal: one companion'),
         ('009EB9', 'Hmm... all right, I reckon', 'Survival >= 50: raises the limit to 100 lb, XP 50 (initiator skill)'),
         ('009EBA', "That's exactly what I", 'Survival < 50 (male)'),
         ('00A0FB', 'Heh. All right, I', 'perk bypass (HasPerk)'),
         ('009EB8', 'Well, all right then', 'END: Caps 175, StartQuest NVDLC02IntroSlides, DisablePlayerControls 1 1 1 1 1 1 1')],
     'breaks': 'initiator-picks: weight, companion count and perks are read for the initiator only. Required: a max-over-party reduction (proposed) for GetActorValue InventoryWeight; the companion refusals read shared VNPCFollowers quest variables, '
               'and the 175 caps + XP to every player (RewardAll).'},
    {'id': 'dlg_hh_ricky_weight_bribe', 'name': 'Ricky: sell Jed a lighter load (Barter 50/75/100)', 'quest': 'NVDLC02DialogueRicky',
     'npc_base': None, 'greeting': None,
     'summary': 'Ricky raises the weight limit for caps. The caps leave the initiator, the limit variable (bWeightLimitIncreased) is shared for the whole party.',
     'infos': [
         ('00F5E6', 'You got a deal', 'Barter >= 100, costs 50 caps, sets bWeightLimitIncreased'),
         ('00F5FF', 'You got a deal', 'Barter 75-99, 75 caps'),
         ('00F5D8', 'You got a deal', 'Barter 50-74, 100 caps'),
         ('00F5D5', 'I travel light on purpose', 'Barter >= 50 opening, XP 50'),
         ('00F5D4', "Ricky travels light", 'Barter < 50 failure')],
     'breaks': 'caps removed from the initiator only; the raised limit applies to all players (shared quest variable), so one player pays for the party. Decision needed: split or charge the initiator.'},
    {'id': 'dlg_hh_jed_ambush_trigger', 'name': 'Jed: the White Legs ambush (arrival)', 'quest': 'NVDLC02DialogueCaravanMaster',
     'npc_base': 'HonestHearts.esm:009E7B', 'greeting': None,
     'summary': "Scripted conversation between Jed, Stella and Ricky starts MQ01 and fires the ambush. It is NPC-to-NPC; the player's presence and kills (IsKiller) decide whether it plays out or is short-circuited (bPlayerInterference).",
     'infos': [
         ('00F195', 'Enough lollygagging', 'END: ZionArrivalBox.iState 2, StartQuest NVDLC02MQ01, SetStage NVDLC02MQ01 5'),
         ('00A7BD', 'Holy hell, it', 'BEGIN/END: AmbushEnablerSequence, iJedSequence 3, SetStage NVDLC02MQ01 10'),
         ('00FFFE', 'Sorry to bother you with reality', 'END: ZionArrivalBox.bSceneStarts 1 (Ricky)')],
     'breaks': 'conversation-type INFOs run on the host with the NPCs (dialogue-design 7.4). The ambush trigger and kill credit are host-only; guests only see NPC motion. IsKiller must be any-party-member.'},
    {'id': 'dlg_hh_daniel_choice', 'name': "Daniel: Joshua's attack or Daniel's flight (the fork)", 'quest': 'NVDLC02DialogueDaniel',
     'npc_base': 'HonestHearts.esm:008A38', 'greeting': None,
     'summary': 'After the briefing (INFO 0093AF, MQ03d stage 20) the player picks one of two irreversible paths. END scripts disable controls, fire Waking Cloud and start a 30 s fade timer that teleports the player.',
     'infos': [
         ('0093AF', 'I appreciate it. Well', 'END: MQ03d stage 20, RemoveItem quest items, ShowMessage NVDLC02SideQuestWarningMSG'),
         ('010718', 'No. This is how we made the world', "Joshua's path: StartQuest NVDLC02MQ04, SetStage MQ03d 100, NVDLC02MQ04Timer"),
         ('010723', "I've already explained to them", "Joshua's path (variant): same END"),
         ('0093AA', 'It may be. I hope it is', "Daniel's path: StartQuest NVDLC02MQ05, SetStage MQ03d 100, NVDLC02MQ05Timer")],
     'breaks': 'irreversible fork picked by the initiator. Proposed ChoiceConsent; both END scripts DisablePlayerControls and start a timer that Player.MoveTo (party-warp needed).'},
    {'id': 'dlg_hh_daniel_speech_checks', 'name': 'Daniel: Speech 40 / 50 / 65 and Barter 50 checks', 'quest': 'NVDLC02DialogueDaniel',
     'npc_base': 'HonestHearts.esm:008A38', 'greeting': None,
     'summary': 'Skill gates read the initiator. XP rewards go to every player (RewardAll).',
     'infos': [
         ('0104A0', 'I understand. But I hope', 'Speech >= 40: XP 40 (SUCCEEDED)'),
         ('01049E', 'Corrected action is the most sincere', 'Speech < 40 (FAILED)'),
         ('0106E1', 'Fair enough. Just be careful', 'Speech >= 65: Daniel sells medical supplies, XP 65'),
         ('0106E4', 'I do.', 'Barter >= 50: supplies, XP 50'),
         ('010714', 'I never thought I', 'Speech >= 50 (Bishop Mordecai story): XP 50')],
     'breaks': 'none beyond the generic initiator rule; the XP is awarded per script call, so RewardAll must multiply it per player exactly once (dedupe by txn seq).'},
    {'id': 'dlg_hh_daniel_healing_paid', 'name': 'Daniel: paid healing (150-600 caps)', 'quest': 'NVDLC02DialogueDaniel',
     'npc_base': 'HonestHearts.esm:008A38', 'greeting': None,
     'summary': 'Healing costs caps from the patient and plays a medical animation with fade and DisablePlayerControls in Daniel own GameMode block.',
     'infos': [
         ('0106E6', 'Thanks. Hold still', 'END: SetAv Variable01 1, Player.RemoveItem Caps001 150'),
         ('0106DD', 'That should cover it', 'END: setstage VDoctors 30, Player.RemoveItem Caps001 300'),
         ('0106F6', 'Thanks. Hold still', 'END: SetAv Variable01 1, Player.RemoveItem Caps001 600')],
     'breaks': 'PerPlayerScene: only the patient is locked and charged; Daniel NPC script locks the Player reference of the machine running it (host). Needs a per-player scene effect or the initiator-targeted SetPlayerControls.'},
    {'id': 'dlg_hh_joshua_suw_confrontation', 'name': 'Joshua and Salt-Upon-Wounds: execute, spare or fight', 'quest': 'NVDLC02DialogueJoshua',
     'npc_base': None, 'greeting': None,
     'summary': 'The Speech 65 and Speech 90 checks decide whether Salt-Upon-Wounds is executed (iJoshuaSequence 8), fought (9) or spared (10). The value changes the ending slide of every player.',
     'infos': [
         ('00F2A9', "God's will", 'END: iJoshuaSequence 8, DisablePlayerControls (execution)'),
         ('00F2AF', 'He never had any dignity', 'END: iJoshuaSequence 9, SetEssential Joshua 1, Salt-Upon-Wounds hostile'),
         ('00F2A6', 'He has a debt to pay', 'Speech >= 65: XP 65'),
         ('01073A', 'I want to take from them', 'Speech >= 90: spare Salt-Upon-Wounds (iJoshuaSequence 10), XP 90'),
         ('01073C', 'I am the right hand of the Lord', 'Speech < 90: FAILED, combat'),
         ('01073D', 'No! He and his tribe', 'Speech < 65: FAILED, combat')],
     'breaks': 'the finale branch is a shared quest variable chosen by the initiator skill; party must be tethered. Combat with Salt-Upon-Wounds starts on the host and has to be replicated by NV:MP.'},
    {'id': 'dlg_hh_suw_peace_flight', 'name': 'Salt-Upon-Wounds: peace in the Daniel path (Speech 75)', 'quest': 'NVDLC02DialogueSaltUponWounds',
     'npc_base': None, 'greeting': None,
     'summary': 'A Speech 75 check lets the White Legs leave; SetStage NVDLC02MQ05 35 sets bWhiteLegsPeace and disables the waves.',
     'infos': [
         ('009849', 'You speak. I listen', 'Speech >= 75: SUCCEEDED'),
         ('0098E6', 'Weak words you talk', 'Speech < 75: FAILED'),
         ('00A848', 'Sorrows', 'END: SetStage NVDLC02MQ05 35'),
         ('00984C', 'We leave, but always fight', 'END: SetStage NVDLC02MQ05 35'),
         ('00A845', 'Ha! Yes, end!', 'END: hostile, StartCombat Player')],
     'breaks': 'MenuMode 1012 (rest) in the MQ05 quest script re-evaluates the peace each time the host rests: resting is a per-player menu.'},
    {'id': 'dlg_hh_followschalk_hire_fire', 'name': 'Follows-Chalk: take him along / send him home', 'quest': 'NVDLC02DialogueFollowsChalk',
     'npc_base': 'HonestHearts.esm:0094D0', 'greeting': '00965A',
     'summary': 'Hire and fire go through NVDLC02HireTriggerREF / NVDLC02FireTriggerREF (SetPlayerTeammate, FollowerFaction, Player.AddPerk NVDLC02WellStackedCairns).',
     'infos': [
         ('00E8FA', 'Goot sists. We head east', 'END: SetStage NVDLC02MQ01 30, HireTrigger Activate'),
         ('00F6D8', 'Goot sists. Maybe while', 'END: SetStage NVDLC02MQ01 30, HireTrigger Activate'),
         ('00EF06', 'I will. ', 'END: FireTrigger Activate Follows-Chalk'),
         ('00F8E8', "Let's move then.", 'END: HireTrigger (MS02 sequence)')],
     'breaks': 'CompanionOwner = recruiter: the perk and teammate flag belong to the initiator; the shared flag bFollowsChalkHired blocks a second recruiter. The third-party view needs Lines + the companion follow package replicated.'},
    {'id': 'dlg_hh_waking_cloud_hire_fire', 'name': 'Waking Cloud: take her along (and swap with Follows-Chalk)', 'quest': 'NVDLC02DialogueWakingCloud',
     'npc_base': 'HonestHearts.esm:009665', 'greeting': None,
     'summary': 'Hiring Waking Cloud fires Follows-Chalk if he is hired; only one companion at a time.',
     'infos': [
         ('00F8BA', 'Certainly. We should make haste', 'END: HireTrigger Waking Cloud, fire Follows-Chalk'),
         ('00F8B5', 'Excellent. His cave overlooks', 'END: HireTrigger, SetStage NVDLC02MS01 5'),
         ('00F8BE', 'I do hope you will reconsider', 'END: HireTrigger, SetStage NVDLC02MS01 5')],
     'breaks': 'a second recruiter can swap the first recruiter companion away: needs a per-owner companion table and a refusal line when the other player already owns one.'},
    {'id': 'dlg_hh_joshua_hire_fire', 'name': 'Joshua Graham: join / leave the party', 'quest': 'NVDLC02DialogueJoshua',
     'npc_base': None, 'greeting': None,
     'summary': 'Joshua is recruited with HireTrigger (Player.AddPerk NVDLC02WayOfTheCanaanite, removes all his caps) and auto-fired at MQ04 stage 100 and MQ05 stage 100.',
     'infos': [
         ('00F8B9', 'Right.', 'END: HireTrigger Joshua'),
         ('00F8B3', 'Go with God, brother', 'END: FireTrigger Joshua'),
         ('00FB32', "I don't think so.", 'END: HireTrigger Joshua (variant)')],
     'breaks': 'auto-fire at the stage 100 of both finales must fire the right owner and remove the perk from the owner only.'},
    {'id': 'dlg_hh_followschalk_civilization', 'name': "Civilized Man's Burden: tell Follows-Chalk about civilization (MS03)", 'quest': 'NVDLC02DialogueFollowsChalk',
     'npc_base': 'HonestHearts.esm:0094D0', 'greeting': None,
     'summary': 'Two answers (civilization good / bad) complete MS03 and cost 50 karma in the Joshua-relayed version. The answer also selects the ending slide for Follows-Chalk.',
     'infos': [
         ('010BC4', 'Really? He said that?', 'END: iFollowsChalkCivilization 3, SetStage NVDLC02MS03 100, RewardKarma -50'),
         ('00F931', 'Oh. Well, I guess that', 'END: iFollowsChalkCivilization 4, SetStage NVDLC02MS03 100, RewardKarma -50'),
         ('010B36', 'Really? You think so?', 'END: iFollowsChalkCivilization 3, SetStage NVDLC02MS03 100'),
         ('010B32', 'I see. Well, thanks', 'END: iFollowsChalkCivilization 4, SetStage NVDLC02MS03 100')],
     'breaks': 'karma -50 must be applied once to the shared karma, not once per player; the answer is a story choice chosen by the initiator.'},
    {'id': 'dlg_hh_waking_cloud_husband', 'name': "A Family Affair: Waking Cloud's husband (MS04)", 'quest': 'NVDLC02DialogueWakingCloud',
     'npc_base': 'HonestHearts.esm:009665', 'greeting': None,
     'summary': "Telling the truth or lying decides the ending slide for Waking Cloud; Daniel confirms his side in DialogueDaniel INFOs 0106FD, 0106F4, 0106E9 (SetStage NVDLC02MS04 100).",
     'infos': [
         ('00F93A', 'This is true? You spoke to him', 'END: SetStage NVDLC02MS04 100, RewardKarma -50'),
         ('00DAAB', 'About Daniel... I don', 'END: SetStage NVDLC02MS04 100'),
         ('00DAAC', 'Perhaps... I will have to have a', 'END: SetStage NVDLC02MS04 100')],
     'breaks': 'two NPC conversations feed the same quest variable; the variable (iWakingCloudFamily) is shared state, so only one answer wins.'},
    {'id': 'dlg_hh_shaman_ritual', 'name': 'White Bird: Sacred Datura ritual (MS01)', 'quest': 'NVDLC02DialogueShaman',
     'npc_base': 'HonestHearts.esm:0094B9', 'greeting': None,
     'summary': 'Hand over three roots, drink the tea, hunt the Ghost of She. The vision effects are per player.',
     'infos': [
         ('0094AA', 'These roots make sacred vision tea', 'BEGIN: Player.RemoveItem NVDLC02SacredDaturaRoot 3'),
         ('0094A8', 'Tea will wait', 'BEGIN: SetStage NVDLC02MS01 30'),
         ('00F6DC', 'You go to cave', 'END: SetStage NVDLC02MS01 40'),
         ('0094AC', 'Then visions of truth are at an end', 'BEGIN: SetStage NVDLC02MS01 100, Player.additem NVDLC02WeapYaoGuaiGauntletUnique')],
     'breaks': 'roots are removed from the initiator while the quest script counts the host inventory; the poison image-space modifier and the hunt are per player.'},
    {'id': 'dlg_hh_ending_slides', 'name': 'Narrator: ending slides', 'quest': 'NVDLC02Ending',
     'npc_base': None, 'greeting': None,
     'summary': 'The narrator speaks 15+ slide INFOs selected by shared quest variables; the show must be identical for everybody and run once.',
     'infos': [
         ('010105', 'And so it was that the conflict', 'intro slide, SwapTexture'),
         ('01010F', 'Though the Courier had stopped', 'MQ04 var 4 == 9: Joshua slide'),
         ('01011D', 'Daniel succeeded in leading the Sorrows', 'MQ05 var 5 >= 3: saved lots'),
         ('01011E', 'Daniel evacuated the Sorrows from Zion', 'MQ05 var 5 < 3: many died'),
         ('010113', 'And with that, the Courier walked out', 'finale slide')],
     'breaks': 'SayTo Player and SayToDone are host-player events; SlideshowEnd rule must make the host run the chain once and replicate the SwapTexture lines.'},
]
