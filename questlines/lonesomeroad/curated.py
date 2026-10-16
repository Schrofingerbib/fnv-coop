"""Hand-curated judgement for the Lonesome Road questline. Everything here was derived by reading the quest, stage,
object and topic scripts (see policy_review.md for the evidence trail). IDs are EDIDs; build.py turns them into
runtime FormIDs (prefix 04) and the tests check that every name resolves in out/questdata.

Nothing here invents a FormID. Unknowns are written as "unknown" or listed under `unverified`.
"""

QUESTLINE = 'DLC: Lonesome Road'

# ---------------------------------------------------------------------------------------------------------------------
# Story role of every quest (24 total). kind: story | script (stage-less script quest) | dialogue | data
ROLE = {
    'NVDLC04MQ00': ('story', 'The Reunion: DLC bootstrap. Quest script waits until the host player has left Doc Mitchell\'s house, fires the load '
                             'messages and adds NVDLC04PlayerFaction; stage 100 is set by the Canyon Wreckage door when the first player enters the Divide.'),
    'NVDLC04MQ01a': ('story', 'The Silo: Hopeville Missile Silo puzzle. ED-E (found as a companion at the silo) breaks the blast-door encryption, '
                              'hacks the mainframe; the launch-code note is the progress gate (GetHasNote on the host player).'),
    'NVDLC04MQ01': ('story', 'The Job: Lonesome Road to the collapsed overpass; detonate the warhead with the Laser Detonator; reach the High Road.'),
    'NVDLC04MQ01c': ('story', 'The Launch: Ashton Silo control station. ED-E arms the launch, a 60 s scripted sequence (NVDLC04MissileLaunchQuest) runs; '
                              'stage 100 is set by that timer script.'),
    'NVDLC04MQ02': ('story', 'The Divide: Silo 2 lockdown, the elevator ride, traversing the Divide to Ulysses\' Temple.'),
    'NVDLC04MQ02a': ('story', 'The Tunnelers: optional route (Cave of the Abaddon) through the Tunnelers; trigger-driven.'),
    'NVDLC04MQ03a': ('story', 'The Courier: Ulysses\' Temple. Forced greeting, then the fight (enemy) or the allied defence of the temple (friend) vs 8/16 '
                              'Marked Men; optional rescue of ED-E and disabling Ulysses\' eyebots.'),
    'NVDLC04MQ03b': ('story', 'The End: the nuke decision. TempleLaunchTerminal chooses nMissileTarget 0 (abort) / 1 NCR / 2 Legion / 3 both; '
                              'ED-E can sacrifice himself (NVDLC04EDEFinaleQuest) to abort it.'),
    'NVDLC04MQ04': ('story', 'The Apocalypse: started instead of 03b when a missile target is chosen (03b is failed with stage 255); same escape, plus '
                             'the nuked-area unlocks and the matching ending perk.'),
    'NVDLC04GenericPerkQUEST': ('story', 'Perk plumbing: Marked, endgame perks, Camarader-E, Voracious Reader, Roughin\' It, Irradiated Beauty; stages award '
                                         'SPECIAL/tag points and the endgame reputation swings. Runs a per-frame GameMode loop on the HOST player.'),
    'NVDLC04Ending': ('script', 'Ending slideshow driver: loot chest, heal, NoSave package, DisablePlayerControls 1 1 1 1 1 1 1, Player.MoveTo the theatre.'),
    'NVDLC04EDEFinaleQuest': ('script', 'ED-E\'s sacrifice cinematic (timer state machine, 9 phases).'),
    'NVDLC04MissileLaunchQuest': ('script', 'Ashton Silo launch timer (8 phases), ends by SetStage NVDLC04MQ01c 100.'),
    'NVDLC04MissileExplosionTimer': ('script', '33 s timer using SetInChargen then PlaceAtMe of the nuclear explosion.'),
    'NVDLC04TempleMarkedMenQuest': ('script', 'Temple battle wave spawner for the Marked Men; started by NVDLC04MQ03a stage 30, stopped by NVDLC04MQ03b stage 10.'),
    'NVDLC04UlyssesCombatBarks': ('script', 'Ulysses combat bark variables; started and stopped by the battle codebox.'),
    'NVDLC04DivideSupport': ('script', 'Support/medical helper quest for the Divide (start_enabled).'),
    'NVDLC04AutoInjectQuest': ('script', 'Auto-inject stimpak helper (hardcore heal-over-time check).'),
    'NVDLC04AutoDocTimerQuest': ('script', 'Auto-doc timer.'),
    'NVDLC04Achievements': ('data', 'Achievement/challenge counters; reads notes of the host player in a GameMode loop.'),
    'NVDLC04XPCalculator': ('data', 'XP value holder (iNVDLC04Act1FinalXP ... Act3FinalXP) read by RewardXP in stage scripts.'),
    'NVDLC04AudioHolotapes': ('dialogue', 'Holotape infos (8 topics).'),
    'NVDLC04DialogueEDE': ('dialogue', 'ED-E: hire / fire / trade / upgrade checks / audio logs / Ulysses radio cut-scene lines (384 topics).'),
    'NVDLC04DialogueUlysses': ('dialogue', 'Ulysses: forced greetings, speech-check conversation tree, friend-or-enemy fork, camp recipes (177 topics).'),
}

# Quests with no stages; why they are not in sweep.stages, and the probe (if any) that exercises their script instead.
NO_STAGE_REASON = {
    'NVDLC04Ending': 'container/script quest, 0 stages; started by NVDLC04MQ03b stage 30 and NVDLC04MQ04 stage 20 (both isolate-mode stages)',
    'NVDLC04EDEFinaleQuest': '0 stages; script-driven cinematic, covered by sweep.probes',
    'NVDLC04MissileLaunchQuest': '0 stages; script-driven, covered by sweep.probes',
    'NVDLC04MissileExplosionTimer': '0 stages; started by NVDLC04MissileLaunchQuest, covered by sweep.probes',
    'NVDLC04TempleMarkedMenQuest': '0 stages; started by NVDLC04MQ03a stage 30 (isolate-mode stage)',
    'NVDLC04UlyssesCombatBarks': '0 stages; bark variables only',
    'NVDLC04DivideSupport': '0 stages; start_enabled helper',
    'NVDLC04AutoInjectQuest': '0 stages; start_enabled helper',
    'NVDLC04AutoDocTimerQuest': '0 stages; timer helper',
    'NVDLC04Achievements': '0 stages; start_enabled counters',
    'NVDLC04XPCalculator': '0 stages; value holder',
    'NVDLC04AudioHolotapes': '0 stages; dialogue container (8 INFOs)',
    'NVDLC04DialogueEDE': '0 stages; dialogue container, INFO probes in sweep.info_probes',
    'NVDLC04DialogueUlysses': '0 stages; dialogue container, INFO probes in sweep.info_probes',
}

# console probes for stage-less script quests: (quest, prime commands (host), command, observe seconds, run_mode, note)
PROBES = [
    ('NVDLC04MissileLaunchQuest', [], 'startquest {Q}', 75, 'isolate',
     'whole 8-phase launch timer (about 60 s) incl. PlaceAtMe FX, ShowMap, SetStage NVDLC04MQ01c 100, EnablePlayerControls'),
    ('NVDLC04MissileExplosionTimer', [], 'startquest {Q}', 40, 'isolate',
     'SetInChargen 1 for 33 s, then PlaceAtMe NVDLC04MissileExplosion + image-space flash'),
    ('NVDLC04EDEFinaleQuest', ['set {Q}.bRunTimer to 1'], 'startquest {Q}', 60, 'isolate',
     'ED-E sacrifice: Say, SetDestroyed, SetGhost, KillActor NVDLC04EDEREF, SetStage NVDLC04MQ03b 20. Needs NVDLC04EDEREF loaded (Divide). '
     'unverified: console `set <hexquest>.var to` form'),
    ('NVDLC04TempleMarkedMenQuest', [], 'startquest {Q}', 30, 'isolate',
     'wave spawner: enables the pre-placed Marked Men refs; combat will start. Needs the temple cell loaded'),
    ('NVDLC04Ending', [], 'startquest {Q}', 20, 'isolate',
     'GameMode script on first frame: loot chest, heal, NoSave package, DisablePlayerControls all, Player.MoveTo slideshow marker. LAST in the run'),
]

# ---------------------------------------------------------------------------------------------------------------------
# Sweep setup. cmds use {Q:<EDID>} / {R:<EDID>} placeholders, resolved to runtime hex FormIDs by build.py.
# Only startquest / setstage / set <quest>.var / <ref>.enable / disable. `verify` marks commands whose console form is
# not proven in this repo (quest-variable set with a hex quest id).
START = 'startquest {Q:%s}'


def _tgt(n):
    return {'name': 'target_%d' % n, 'why': 'nMissileTarget=%d' % n,
            'setup': [{'cmd': 'set {Q:NVDLC04MQ03b}.nMissileTarget to %d' % n, 'verify': True,
                       'alt': 'set NVDLC04MQ03b.nMissileTarget to %d' % n}]}


# per (quest, stage): dict(variants=[...], run_mode=..., note=..., setup=[extra cmds])
STAGE = {
    ('NVDLC04MQ03b', 100): {'variants': [_tgt(0), _tgt(1), _tgt(2), _tgt(3)],
                            'note': 'stage script prints NVDLC04NukedNoOneMSG only when nMissileTarget == 0; also EnterTrigger Player on the silo box'},
    ('NVDLC04MQ04', 100): {'variants': [_tgt(1), _tgt(2), _tgt(3)],
                           'note': 'target decides messages, gate/boat enables and map markers: the nuke consequence in the Mojave'},
    ('NVDLC04MQ04', 20): {'variants': [
        {'name': 'ede_not_hired', 'why': 'bEDEHired=0', 'setup': [{'cmd': 'set {Q:NVDLC04DialogueEDE}.bEDEHired to 0', 'verify': True,
                                                                  'alt': 'set NVDLC04DialogueEDE.bEDEHired to 0'}]},
        {'name': 'ede_hired', 'why': 'bEDEHired=1: companion teardown branch', 'setup': [
            {'cmd': 'set {Q:NVDLC04DialogueEDE}.bEDEHired to 1', 'verify': True, 'alt': 'set NVDLC04DialogueEDE.bEDEHired to 1'}]}],
        'run_mode': 'isolate', 'note': 'StartQuest NVDLC04Ending: the ending GameMode script locks controls and moves the player'},
    ('NVDLC04MQ03b', 30): {'run_mode': 'isolate', 'note': 'StartQuest NVDLC04Ending (see MQ04 stage 20)'},
    ('NVDLC04MQ03a', 30): {'run_mode': 'isolate', 'note': 'StartQuest NVDLC04TempleMarkedMenQuest: Marked Men waves begin, combat can hang the sweep'},
    ('NVDLC04MQ03b', 10): {'note': 'StopQuest NVDLC04TempleMarkedMenQuest, TempleExit.Unlock'},
    ('NVDLC04MQ02', 5): {'note': 'NVDLC04Road02KlaxonLightREF.PlayGroup'},
    ('NVDLC04GenericPerkQUEST', 30): {'note': 'reads the host player Karma AV then RewardKarma(-Karma): shared karma goes to zero'},
}

# Quests whose reset set (test.quest.reset) must include extra quests that the stage scripts also move.
RESET_EXTRA = {
    'NVDLC04MQ00': ['NVDLC04MQ01a', 'NVDLC04XPCalculator'],
    'NVDLC04MQ01a': ['NVDLC04MQ01'],
    'NVDLC04MQ01': ['NVDLC04MQ01c'],
    'NVDLC04MQ01c': ['NVDLC04MQ02'],
    'NVDLC04MQ02': ['NVDLC04MQ03a'],
    'NVDLC04MQ02a': ['NVDLC04MQ02'],
    'NVDLC04MQ03a': ['NVDLC04MQ03b', 'NVDLC04TempleMarkedMenQuest'],
    'NVDLC04MQ03b': ['NVDLC04TempleMarkedMenQuest', 'NVDLC04Ending', 'NVDLC04XPCalculator'],
    'NVDLC04MQ04': ['NVDLC04Ending', 'NVDLC04XPCalculator'],
}

# stages the sweep cannot meaningfully cover, with the reason (none today: every stage is swept, some isolated)
EXCLUDED_STAGES = {}

# ---------------------------------------------------------------------------------------------------------------------
# Rule patch. Existing 16 kinds only. params follow tools/coverage/rules.py.
CTRL_ALL = {'scope': 'all', 'release': 'host-effect', 'timeout_s': 120}


def R(kind, **params):
    return {'kind': kind, 'params': params, 'origin': 'curated'}


RULES = {
    'NVDLC04MQ00': {
        'rules': [R('SaveSuppress'), R('PartyAwareCond', funcs={'getincell': 'entry:all-in,exit:any-in', 'isactionref': 'initiator'}),
                  R('ShareKarmaRep'), R('InitiatorDialogue')],
        'why': 'Intro bootstrap. The host-only quest script starts the DLC when GetInCell GSDocMitchellHouse == 0; reduced over the party (exit rule) '
               'it fires when the last player has left. NVDLC04PlayerFaction membership is shared (owner decision).',
    },
    'NVDLC04MQ01a': {
        'rules': [R('SaveSuppress'),
                  R('PartyAwareCond', funcs={'getincell': 'entry:all-in,exit:any-in', 'isactionref': 'initiator', 'getinworldspace': 'any',
                                             'gethasnote': 'any-party-member'}),
                  R('CompanionOwner', owner='party'), R('QuestItemDup', recipient='all'), R('RewardAll'), R('InitiatorDialogue')],
        'why': 'ED-E is the silo\'s key (blast door, mainframe, commissary): a quest-critical companion must work for the whole party, so owner="party" '
               '(NEW PARAM VALUE, see proposals). The launch-code note gate reads GetHasNote on the host player: reduce with any-party-member and '
               'duplicate the note to all players (QuestItemDup).',
    },
    'NVDLC04MQ01': {
        'rules': [R('SaveSuppress'), R('FastTravelLock', scope='party'),
                  R('PartyAwareCond', funcs={'isactionref': 'initiator', 'getdistance': 'min-over-party'}),
                  R('RewardAll'), R('InitiatorDialogue')],
        'why': 'Existing derived rules kept and curated. The Laser Detonator pickup is a Player.AddItemHealthPercent in an OnActivate: the activator '
               'gets it; QuestItemDup is NOT added because the weapon is not a quest item (it is looted once, first come). Open decision.',
    },
    'NVDLC04MQ01c': {
        'rules': [R('PartyTether', cells=['NVDLC04Road02World'], gather='auto-warp'),
                  R('PartyWarp', markers=['NVDLC04MissileLaunchViewMarkerREF'], mode='slot-offset', absent='hold'),
                  R('SetPlayerControls', **dict(CTRL_ALL, masks=[])), R('SaveSuppress'), R('FastTravelLock', scope='party'),
                  R('PartyAwareCond', funcs={'isactionref': 'initiator'}), R('RewardAll'), R('InitiatorDialogue')],
        'why': 'The launch is a one-way cinematic: LaunchControlActivator fades, locks controls, the rocket sequence runs, the Courier\'s Mile barrier '
               'is disabled. Everyone must be at the Ashton control station (PartyTether) and watch from the launch view marker (PartyWarp). '
               'Cell choice inferred from the Road02 object names in the launch scripts: unverified.',
    },
    'NVDLC04MissileLaunchQuest': {
        'rules': [R('SetPlayerControls', **dict(CTRL_ALL, masks=[])), R('SaveSuppress'), R('FastTravelLock', scope='party'), R('RewardAll')],
        'why': 'Timer script that ends with EnablePlayerControls (release effect) and SetStage NVDLC04MQ01c 100; the lock is released by the host effect.',
    },
    'NVDLC04MissileExplosionTimer': {
        'rules': [R('SaveSuppress'), R('SetPlayerControls', **dict(CTRL_ALL, masks=[]))],
        'why': 'SetInChargen 1 for 33 s hides level-up/UI on every machine for the explosion; treat as a control lock with a timeout failsafe.',
    },
    'NVDLC04MQ02': {
        'rules': [R('PartyTether', cells=['NVDLC04Silo2Elevator'], gather='auto-warp'),
                  R('SetPlayerControls', **dict(CTRL_ALL, masks=[])), R('SaveSuppress'),
                  R('PartyAwareCond', funcs={'isactionref': 'initiator', 'getdistance': 'min-over-party', 'getinworldspace': 'any'}),
                  R('RewardAll'), R('InitiatorDialogue')],
        'why': 'Silo 2 elevator ride (objective 7) is a scripted explosion sequence: the whole party rides together. The ED-E "stay close" checks '
               '(GetDistance, GetInWorldSpace) use min-over-party / any.',
    },
    'NVDLC04MQ02a': {
        'rules': [R('SaveSuppress'), R('PartyAwareCond', funcs={'isactionref': 'initiator', 'getdistance': 'min-over-party', 'getinworldspace': 'any'}),
                  R('RewardAll'), R('InitiatorDialogue')],
        'why': 'Optional cave route; the OnTriggerEnter Player trigger fires for whichever party member enters first (host sees guest ghosts via NV:MP). '
               'No tether: the Tunnelers cave is skippable and re-enterable.',
    },
    'NVDLC04MQ03a': {
        'rules': [R('PartyTether', cells=['NVDLC04DivideUlyssesTemple'], gather='wait'),
                  R('PartyAwareCond', funcs={'isactionref': 'initiator', 'getdistance': 'min-over-party', 'iskiller': 'any-party-member',
                                             'getactorvalue': 'min-over-party'}),
                  R('SaveSuppress'), R('CompanionOwner', owner='party'), R('RewardAll'), R('InitiatorDialogue')],
        'why': 'The temple fight is the climax: the forced greeting (StartConversation Player) must be taken by one initiator; the weak-player check '
               '(all combat skills < 75) uses min-over-party (a "max" reduction would be fairer; proposal). Kills by any member count (IsKiller).',
    },
    'NVDLC04TempleMarkedMenQuest': {
        'rules': [R('SaveSuppress')],
        'why': 'Wave spawner; no rule kind covers spawn pacing. Relies on host authority + NV:MP actor replication (unverified).',
    },
    'NVDLC04MQ03b': {
        'rules': [R('PartyTether', cells=['NVDLC04DivideUlyssesTemple'], gather='auto-warp'),
                  R('SetPlayerControls', **dict(CTRL_ALL, masks=['0 0 0 1 0 0 1'])), R('SaveSuppress'),
                  R('CompanionOwner', owner='party'), R('PartyAwareCond', funcs={'isactionref': 'initiator'}),
                  R('ShareKarmaRep'), R('RewardAll'), R('InitiatorDialogue')],
        'why': 'The nuke terminal is a world-changing choice with four branches. The initiator who activates the terminal chooses (owner decision: '
               'initiator chooses), everyone must be in the temple (PartyTether, story-critical), controls are locked for the exit sequence. '
               'The terminal is a menu session, not a dialogue: needs the proposed InitiatorMenu kind (see proposals).',
    },
    'NVDLC04EDEFinaleQuest': {
        'rules': [R('PartyTether', cells=['NVDLC04DivideUlyssesTemple'], gather='wait'), R('SetPlayerControls', **dict(CTRL_ALL, masks=[])),
                  R('SaveSuppress'), R('CompanionOwner', owner='party')],
        'why': 'ED-E\'s sacrifice: host-run state machine, KillActor on the companion, Player.RemovePerk NVDLC04EnhancedSensors and a slow-motion spell on the '
               '"player". Companion removal must clear every player\'s companion perks (per-player effect, see gaps: addperk/removeperk).',
    },
    'NVDLC04MQ04': {
        'rules': [R('PartyTether', cells=['NVDLC04DivideWorld'], gather='wait'), R('PartyWarp', markers=[], mode='slot-offset', absent='hold'),
                  R('SetPlayerControls', **dict(CTRL_ALL, masks=['0 0 0 1 0 0 1'])), R('SaveSuppress'), R('CompanionOwner', owner='party'),
                  R('PartyAwareCond', funcs={'isactionref': 'initiator'}), R('ShareKarmaRep'), R('RewardAll'), R('InitiatorDialogue')],
        'why': 'Aftermath of a launch: run for the Canyon Wreckage (timer), exit door cut-scene, ending, then the nuked-area unlocks and the matching '
               'endgame perk. The derived PartyWarp marker "rLinkedRef" is a script VARIABLE, not a ref: the real boat destination is unknown to '
               'the rules engine; markers are left empty on purpose (see policy_review, section on boats and gates).',
    },
    'NVDLC04Ending': {
        'rules': [R('SlideshowEnd', run='host-once', lock='all', release='together'),
                  R('PartyWarp', markers=['NVDLC04SlideShowMarkerREF', 'NVDLC04CanyonWreckageTeleportMarkerREF'], mode='slot-offset', absent='warp'),
                  R('SetPlayerControls', **dict(CTRL_ALL, masks=['1 1 1 1 1 1 1'])), R('SaveSuppress'), R('RewardAll')],
        'why': 'Ending slideshow: host runs it once, every player is moved to the theatre and locked, then released together at the Canyon Wreckage. '
               'absent="warp": a player who is elsewhere must not miss the ending (it is the DLC finale).',
    },
    'NVDLC04GenericPerkQUEST': {
        'rules': [R('PerPlayerScene'), R('ShareKarmaRep'), R('RewardAll'), R('QuestItemDup', recipient='all'), R('SaveSuppress'),
                  R('PartyAwareCond', funcs={'hasperk': 'initiator', 'getitemcount': 'initiator', 'getactorvalue': 'initiator'}),
                  R('InitiatorDialogue')],
        'why': 'The quest script evaluates Player.HasPerk / GetItemCount / IsInCombat every frame for ONE player (the host). Perk plumbing is per-player, '
               'so it needs a per-player instance: PerPlayerScene. Endgame perks and SPECIAL/tag points go to every player (RewardAll extension).',
    },
    'NVDLC04DialogueEDE': {
        'rules': [R('CompanionOwner', owner='party'), R('InitiatorDialogue'), R('RewardAll'), R('SaveSuppress')],
        'why': 'Hire / fire / trade / upgrade conversations: the initiator talks to ED-E; the hire result (AddPerk, AddToFaction, SetPlayerTeammate) '
               'must set the PARTY\'s ED-E, not the host\'s.',
    },
    'NVDLC04DialogueUlysses': {
        'rules': [R('InitiatorDialogue'), R('RewardAll'), R('SaveSuppress')],
        'why': 'Ulysses\' tree: initiator picks every speech option; camp gifts (Player.AddItem) go to everyone.',
    },
    'NVDLC04AudioHolotapes': {
        'rules': [R('InitiatorDialogue')], 'why': 'Holotape playback infos; initiator-driven, results are script vars.',
    },
    'NVDLC04AutoDocTimerQuest': {
        'rules': [R('PerPlayerScene')],
        'why': 'Auto-Doc fade: DisablePlayerControls + image-space fade for 4 s on the machine of whoever used the Auto-Doc. rules.py already names AutoDoc as '
               'the PerPlayerScene example: the lock must NOT spread to the party.',
    },
    'NVDLC04AutoInjectQuest': {
        'rules': [R('PerPlayerScene')],
        'why': 'Per-frame loop over Player health, combat, hardcore flag and inventory (auto-injects stimpaks/tourniquets). It evaluates one player; needs one '
               'instance per player. Hardcore mode is a per-machine setting.',
    },
    'NVDLC04Achievements': {
        'rules': [R('ExcludeFromGate')],
        'why': 'Achievement counters (GetHasNote on the host player); not required for any ending. Counters stay per profile.',
    },
}

# ---------------------------------------------------------------------------------------------------------------------
PROPOSALS = {
    'new_kinds': [
        {'kind': 'InitiatorMenu', 'status': 'proposal',
         'why': 'Object-script message-box choice flows (NVDLC04TempleLaunchTerminalSCRIPT nMessage state machine, LaunchControlActivator, EDECommissary, '
                'MQ01a mainframe) read GetButtonPressed on the machine that showed the box. InitiatorDialogue covers INFO topics only. '
                'A menu session would: (1) take the activating player\'s activation intent, (2) run the choices on the initiator, (3) feed the '
                'chosen button index back into the host script, (4) show a read-only copy to others.',
         'params': {'owner': 'initiator', 'mirror': 'spectators', 'timeout_s': 120}},
    ],
    'new_param_values': [
        {'kind': 'CompanionOwner', 'param': 'owner', 'value': 'party',
         'why': 'ED-E is a plot device for the whole party (silo terminals, launch, finale); recruiter ownership would make the other players\' '
                'progression depend on who clicked "hire".'},
        {'kind': 'PartyAwareCond', 'param': 'funcs.*', 'value': 'max-over-party',
         'why': 'NVDLC04MQ03aSCRIPT bWeakPlayer wants "any party member is strong" (all five combat skills < 75 means weak). min-over-party is used '
                'in the patch as the closest existing reduction.'},
        {'kind': 'SetPlayerControls', 'param': 'scope', 'value': 'initiator',
         'why': 'Forced greetings (Ulysses temple, ED-E flyaway) lock only the player who is in the conversation; the others keep roaming.'},
    ],
    'open_decisions': [
        'Endgame perks (DivideSurvivor / BearSlayer / ScourgeOfTheEast / DeadMansBurden) and the Marked perk: every player (proposed) or the initiator only?',
        'ED-E ownership: party (proposed) vs recruiter (default).',
        'Guest-originated activation intent (doors, terminals, levers): not implemented in L5; every story gate in this DLC is an OnActivate script.',
        'Nuke choice: initiator chooses vs unanimous confirmation. Patch assumes initiator + everyone tethered in the temple.',
        'Time skip on every Canyon Wreckage door use (GameDay += 3) with several players: once per party or once per player.',
    ],
}
