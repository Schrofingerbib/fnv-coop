"""rules.patch.json for the Old World Blues questline (proposal for docs/quest-policies/rules.json, owned by L9).

Only the 16 existing rule kinds are used for quest rules; the single new kind (LocalQuestScript) carries a written
justification. EDID-valued params (cells, markers, anchor, refs, perks, items, scripts, quests) are resolved against
quests.json / forms.json.gz by run_tests.py. Operations:
    replace  the quest already has an entry in rules.json: the rule list is replaced
    add      the quest has no entry yet
"""
BASE_KINDS = ['PartyTether', 'PartyWarp', 'SetPlayerControls', 'Barrier', 'PerPlayerScene', 'SlideshowEnd', 'CompanionOwner',
              'SaveSuppress', 'FastTravelLock', 'PartyAwareCond', 'QuestItemDup', 'RewardAll', 'RemoveItemPerOwner',
              'ShareKarmaRep', 'InitiatorDialogue', 'ExcludeFromGate']
EDID_PARAM_KEYS = ('cells', 'markers', 'anchor', 'refs', 'perks', 'items', 'scripts', 'quests')

BODY_PERKS = ['NVDLC03BrainlessPerk', 'NVDLC03SpinelessPerk', 'NVDLC03HeartlessPerk']
SWAP_PERKS = ['NVDLC03BigBrainedPerk', 'NVDLC03CardiacArrestPerk']
LOCK_ALL7 = '1 1 1 1 1 1 1'


def r(kind, **params):
    return {'kind': kind, 'params': params, 'origin': 'curated'}


def lock_all(masks, timeout=120):
    return r('SetPlayerControls', scope='all', release='host-effect', timeout_s=timeout, masks=masks)


def lock_origin(masks=None, timeout=30):
    # scope "origin" is an extension of the existing kind: lock only the player who ran the scene (AutoDoc fade etc.)
    return r('SetPlayerControls', scope='origin', release='host-effect', timeout_s=timeout, masks=masks or [])


def cond(**funcs):
    return r('PartyAwareCond', funcs=funcs)


STD_TRIGGER = 'any-party-member (block header filters on the Player ref; a guest ghost never matches it)'

QUESTS = {
    # ---------------------------------------------------------------- main line
    'NVDLC03MQ00': {
        'op': 'replace',
        'why': 'Derived entry put a PartyTether on GSDocMitchellHouse: the script reads Player.GetInCell GSDocMitchellHouse == 0 as '
               '"chargen finished, start the DLC", i.e. an EXIT gate; tethering the party into the house is the opposite of '
               'what is needed. Entry is a one-way trip (no return until the Teleporter at MQ05 stage 100) so the whole party '
               'must gather at the crashed satellite first.',
        'rules': [
            r('PartyTether', cells=[], anchor='NVDLC03CrashedSatelliteREF', radius=1500, gather='auto-warp',
              gate='MQ00 stage 100 is set by the satellite MenuMode button; refuse until every slot is inside the radius'),
            lock_all(['1 1 1 1 0 1 1']),
            r('PartyWarp', markers=['NVDLC03SlideShowMarkerREF'], mode='slot-offset', absent='hold'),
            r('SaveSuppress'),
            r('CompanionOwner', owner='recruiter', action='dismiss-all-on-entry',
              note='TeleportEffectTimer fires every follower; each recruiter keeps their own companion state'),
            cond(GetInCell='exit:any-in (nEnableDLC==0 gate: start only when nobody is inside GSDocMitchellHouse)',
                 IsActionRef='initiator', IsPlayerActionActive='initiator', IsSneaking='initiator',
                 IsInCombat='any-party-member', OnTrigger=STD_TRIGGER),
            r('RewardAll'),
            r('ShareKarmaRep', note='Player.AddToFaction NVDLC03PlayerFaction: faction membership is shared (coordinator decision)'),
            r('QuestItemDup', recipient='all'),
            r('InitiatorDialogue'),
        ],
    },
    'NVDLC03MQ01': {
        'op': 'replace',
        'why': 'Stage 10 removes brain, spine and heart from THE PLAYER: every party member must lose them. AddPerk is BLOCK_CMD only '
               '(never captured), so RewardAll{include AddPerk} needs a SetPerk capture hook. Stage 10 also rewrites '
               'GameMonth/GameDay (shared globals, host once). The derived QuestItemDup is dropped: the only inventory change is the gown, '
               'routed by RewardAll.include.',
        'rules': [
            r('RewardAll', include=['AddPerk', 'AddItem', 'EquipItem'], perks=BODY_PERKS, items=['NVDLC03PatientGown'],
              note='every player gets the three body-part perks and the patient gown'),
            r('SaveSuppress', release='PlayerStartTrigger (Player.RemoveScriptPackage)'),
            lock_all(['1 1 1 1 0 1 1'], timeout=300),
            cond(GetLevel='initiator: XP tier computed once on the host from ITS level (NVDLC03Act1XP..Act3XP are shared globals)',
                 OnTrigger=STD_TRIGGER, IsActionRef='initiator'),
            r('InitiatorDialogue'),
        ],
    },
    'NVDLC03MQ02': {
        'op': 'replace',
        'why': 'Order-independent trio (MQ02/03/04): stage 100 of each picks ForceActiveQuest or SetStage MQ05 10 by '
               'GetQuestCompleted, so the shared completion flags (not per-player) must drive it. Antenna climb and '
               'station door use IsActionRef Player.',
        'rules': [cond(IsActionRef='initiator', OnTrigger=STD_TRIGGER, GetQuestCompleted='shared'),
                  r('RewardAll'), r('InitiatorDialogue')],
    },
    'NVDLC03MQ03': {
        'op': 'replace',
        'why': 'X-8 tests run scripted cutscenes that lock controls and MoveTo a viewing marker (X8Experiment x4); the school tests '
               'read Player.GetInCell/GetHasNote and give a perk to the tester.',
        'rules': [
            r('PartyTether', cells=['NVDLC03X8School', 'NVDLC03X8Main'], gather='wait',
              gate='a test starts only when every slot is in the X-8 facility; no auto-warp across the Big Empty'),
            r('PartyWarp', markers=[], cells=['NVDLC03X8School'], mode='slot-offset', absent='hold',
              note='X8ExperimentViewMarker is only in refnames.json.gz; party-warp target is the school cell'),
            lock_all([LOCK_ALL7]),
            cond(GetInCell='entry:all-in,exit:any-in', GetHasNote='initiator', GetItemCount='initiator',
                 IsActionRef='initiator', OnTrigger=STD_TRIGGER),
            r('QuestItemDup', recipient='all', items=['NVDLC03WeapSonicGunGabrielsBark', 'NVDLC03X8KennelKey']),
            r('RewardAll', include=['AddPerk'], note='DN-Agent perk from X8Terminal01 is a quest reward: every player'),
            r('InitiatorDialogue'),
        ],
    },
    'NVDLC03MQ04': {
        'op': 'replace',
        'why': 'Suit assembly checks Player.GetItemCount for boots+gloves+chest and removes them; with quest-item duplication all '
               'players hold the parts so assembly must complete for everyone once.',
        'rules': [cond(GetItemCount='initiator', IsActionRef='initiator', OnTrigger=STD_TRIGGER),
                  r('QuestItemDup', recipient='all', items=['NVDLC03X13SuitBoots', 'NVDLC03X13SuitGloves',
                                                              'NVDLC03X13SuitChest']),
                  r('RewardAll'), r('InitiatorDialogue')],
    },
    'NVDLC03MQ05': {
        'op': 'replace',
        'why': 'Existing curated entry has empty markers/cells/masks and models the Teleporter as a plain party-warp. The finale is '
               '(1) a hostile/peace branch with a shared faction relation, (2) a Think Tank door that starts the ending '
               'slideshow (IsActionRef Player), (3) a per-player brain/heart/spine restoration and (4) a Teleporter that every '
               'player receives but that moves only its user and the party members within range.',
        'rules': [
            r('PartyTether', cells=['NVDLC03ThinkTank'], gather='wait',
              gate='ThinkTankDoor ending: refused until every slot is inside NVDLC03ThinkTank (message instead of Activate)'),
            r('Barrier', step='all-players-pass', refs=['NVDLC03ThinkTankToBigEmptyDoorREF'],
              note='exit door of the Think Tank starts the ending only after the whole party is inside'),
            r('PartyWarp', markers=['NVDLC03SlideShowMarkerREF', 'NVDLC03EndMarker'], mode='slot-offset', absent='hold',
              note='Ending and TransitionToTheSink both MoveTo; Teleporter use is a separate range-limited warp (WeapTeleporter)'),
            lock_all([LOCK_ALL7], timeout=900),
            r('SlideshowEnd', run='host-once', lock='all', release='together'),
            r('SaveSuppress'),
            r('PerPlayerScene', scene='AutoDoc body-part restoration (NVDLC03SinkAutoDoc menu)',
              per_player=['bRestoreBodyParts', 'bImplants', 'nButton', '*SwapMsg', 'perks:Brainless/Spineless/Heartless <-> '
                          'BigBrained/CardiacArrest/ReinforcedSpine', 'caps cost'],
              shared=['bRestoredBrain', 'bRestoredHeart', 'bRestoredSpine -> become "any player restored" flags'],
              note='bRestored* are single quest vars read by the menu; per-player state must come from HasPerk, not from them'),
            r('QuestItemDup', recipient='all', items=['NVDLC03PlayerBrain'],
              note='each player carries their own brain; stage 100 swap runs for every holder'),
            r('RewardAll', include=['AddItem', 'EquipItem', 'AddPerk', 'RemovePerk'], items=['NVDLC03WeapTeleporter'],
              perks=SWAP_PERKS),
            r('ShareKarmaRep', note='ending slides branch on Karma (<= -250 bad); shared Courier karma'),
            cond(IsActionRef='initiator', GetDistance='min-over-party', GetItemCount='initiator', HasPerk='initiator',
                 IsInCombat='any-party-member', OnTrigger=STD_TRIGGER),
            r('InitiatorDialogue'),
        ],
    },
    # ---------------------------------------------------------------- Sink buddies
    'NVDLC03HQBuddy': {
        'op': 'replace',
        'why': 'AutoDoc fade DisablePlayerControls is the user\'s own screen fade; the curated entry locks the whole party '
               '(scope all) for it. Module unlock flags are shared, the menu/perks/caps are per player.',
        'rules': [lock_origin(), r('PerPlayerScene', scene='NVDLC03SinkAutoDoc menu, AutoDocTimer fade',
                                   per_player=['bAwaitingInput', 'nButton', 'caps cost', 'perks'],
                                   shared=['bAutoDocUnlocked', 'bBarberActive', 'bCosmeticSurgeryActive', 'bImplant*Active']),
                  r('QuestItemDup', recipient='all', items=['NVDLC03HQBuddyAutoDocNOTE'],
                    note='module notes are duplicated on pickup and removed from every player when installed'),
                  cond(IsActionRef='initiator', GetHasNote='initiator', GetItemCount='initiator', OnTrigger=STD_TRIGGER),
                  r('RewardAll'), r('InitiatorDialogue')],
    },
    'NVDLC03HQBuddyUpgrade': {
        'op': 'replace', 'why': 'As HQBuddy (same AutoDoc scripts).',
        'rules': [lock_origin(), r('PerPlayerScene', scene='NVDLC03SinkAutoDoc implants', per_player=['implant perks', 'caps cost'],
                                   shared=['bImplant*Active', 'NVDLC03HQBuddy.iAutoDoc']),
                  r('QuestItemDup', recipient='all'), cond(GetHasNote='initiator', IsActionRef='initiator'), r('RewardAll')],
    },
    'NVDLC03HQBuddySamples': {
        'op': 'replace', 'why': 'Hand-in of plants/samples at the BioResearchStation consumes items from the handing player only.',
        'rules': [cond(IsActionRef='initiator', GetItemCount='initiator', GetHasNote='initiator'),
                  r('RemoveItemPerOwner'), r('QuestItemDup', recipient='all'), r('RewardAll'), r('InitiatorDialogue')],
    },
    # ---------------------------------------------------------------- side quests
    'NVDLC03X8Setup': {
        'op': 'replace', 'why': 'Tests read Player.GetInCell NVDLC03X8School and notes; one terminal user starts a shared test.',
        'rules': [cond(GetInCell='entry:all-in,exit:any-in', GetHasNote='initiator', GetItemCount='initiator',
                       IsActionRef='initiator', OnTrigger=STD_TRIGGER),
                  r('QuestItemDup', recipient='all', items=['NVDLC03X8KennelKey', 'NVDLC03WeapSonicGunGabrielsBark']),
                  r('RewardAll', include=['AddPerk']), r('InitiatorDialogue')],
    },
    'NVDLC03X8Replay': {
        'op': 'replace', 'why': 'Replay variants of X-8 tests; same gates.',
        'rules': [cond(GetInCell='entry:all-in,exit:any-in', GetHasNote='initiator'), r('RewardAll')],
    },
    'NVDLC03X13Setup': {
        'op': 'replace', 'why': 'Robot compliance perk (NVDLC03X13RobotCompliance) is a per-tester state: HasPerk/RemovePerk on the '
                                'player who uses the switch; detection by robots must see any party member.',
        'rules': [cond(HasPerk='initiator', GetDetected='any-party-member', GetInCell='entry:all-in,exit:any-in',
                       IsActionRef='initiator'),
                  r('PerPlayerScene', scene='X13CompSwitch / X13FailureSwitch robot-compliance perk', per_player=['perks'],
                    shared=['nTest', 'bDetected']),
                  r('RewardAll'), r('InitiatorDialogue')],
    },
    'NVDLC03X13Replay': {
        'op': 'replace', 'why': 'As X13Setup.',
        'rules': [cond(HasPerk='initiator', GetDetected='any-party-member', GetInCell='entry:all-in,exit:any-in'),
                  r('PerPlayerScene', scene='robot-compliance perk', per_player=['perks'], shared=['nTest']),
                  r('RewardAll'), r('InitiatorDialogue')],
    },
    'NVDLC03SonicEmitterQuest': {
        'op': 'replace', 'why': 'Upgrade of the Sonic Emitter is an item/note-gated chain: notes are per-player facts.',
        'rules': [cond(GetHasNote='initiator', OnTrigger=STD_TRIGGER), r('QuestItemDup', recipient='all'), r('RewardAll', include=['AddPerk']),
                  r('InitiatorDialogue')],
    },
    'NVDLC03ThinkTankQuest': {
        'op': 'add', 'why': 'Five-doctor tour; every objective is completed by dialogue (initiator). Stage 255 (fail) when a doctor '
                            'dies or fights: shared. XP reward to all.',
        'rules': [r('InitiatorDialogue'), r('RewardAll')],
    },
    'NVDLC03ThinkTankDoctorBorous': {
        'op': 'replace', 'why': 'Gabe\'s Bowl is picked up by one player and handed in by one: pickup duplicates, hand-in removes '
                                'from the handing player only; skill check (Speech 45) is the initiator\'s.',
        'rules': [cond(GetItemCount='initiator', IsActionRef='initiator'),
                  r('QuestItemDup', recipient='all', items=['NVDLC03GabesBowl']), r('RemoveItemPerOwner'),
                  r('RewardAll'), r('InitiatorDialogue')],
    },
    'NVDLC03ThinkTankDoctorDala': {'op': 'add', 'why': 'Dialogue-only completion.', 'rules': [r('InitiatorDialogue')]},
    'NVDLC03ThinkTankDoctorKlein': {'op': 'add', 'why': 'Dialogue-only completion.', 'rules': [r('InitiatorDialogue')]},
    'NVDLC03ThinkTankDoctor8': {'op': 'add', 'why': 'Dialogue-only completion.', 'rules': [r('InitiatorDialogue')]},
    'NVDLC03ThinkTankDoctor0a': {'op': 'add', 'why': 'Dialogue-only completion.', 'rules': [r('InitiatorDialogue')]},
    'NVDLC03ThinkTankDoctor0b': {'op': 'add', 'why': 'Dialogue-only completion.', 'rules': [r('InitiatorDialogue')]},
    # ---------------------------------------------------------------- systems with story effect
    'NVDLC03Enc': {
        'op': 'replace', 'why': 'Crater encounter boxes probe EnterTrigger Player and GetDistance Player/IsInCombat Player: a split '
                                'party must keep encounters near ANY member and not spawn per machine.',
        'rules': [cond(GetDistance='min-over-party', IsInCombat='any-party-member', EnterTrigger='any-party-member')],
    },
    'NVDLC03GenericPerkQUEST': {
        'op': 'replace', 'why': 'The implant/heartless perk engine is a quest script that reads Player.HasPerk/GetItemCount/GetAV '
                                'every frame and Player.AddItem/RemoveItem; the guest quest-script gate would skip it entirely. The derived RewardAll/QuestItemDup/'
                                'InitiatorDialogue are dropped: stage 10/11 items are side effects of owning the perk, not rewards.',
        'rules': [r('LocalQuestScript', scripts=['NVDLC03GenericPerkQUEST'], mode='run-on-every-machine-for-its-own-player'),
                  r('PerPlayerScene', scene='perk stage entries 10/11 (Implant GRX rank 1/2)', per_player=['perks', 'items'],
                    shared=[]),
                  cond(HasPerk='initiator', GetItemCount='initiator', GetAV='initiator')],
    },
    'NVDLC03Intro': {
        'op': 'add', 'why': 'Intro slideshow: SayTo chain driven by SayToDone on the Narrator, SetInChargen, no-save package, '
                            'lock of all controls, ends with MoveTo StartMarker.',
        'rules': [r('SlideshowEnd', run='host-once', lock='all', release='together'), lock_all([LOCK_ALL7], timeout=600),
                  r('SaveSuppress'), r('PartyWarp', markers=['NVDLC03SlideShowMarkerREF'], mode='slot-offset', absent='hold')],
    },
    'NVDLC03Ending': {
        'op': 'add', 'why': 'Ending slideshow (59 slide INFOs, Karma branch, map-marker counts) then TransitionToTheSink.',
        'rules': [r('SlideshowEnd', run='host-once', lock='all', release='together'), lock_all([LOCK_ALL7], timeout=900),
                  r('SaveSuppress'), r('PartyWarp', markers=['NVDLC03SlideShowMarkerREF', 'NVDLC03EndMarker'],
                                      mode='slot-offset', absent='hold'),
                  r('ShareKarmaRep'), cond(GetMapMarkerVisible='union-over-party', GetActorValue='shared-karma')],
    },
    'NVDLC03TransitionToBigEmpty': {
        'op': 'add', 'why': 'Fade then Player.MoveTo NVDLC03StartMarker after the intro slides.',
        'rules': [r('PartyWarp', markers=['NVDLC03StartMarker'], mode='slot-offset', absent='hold'), r('SaveSuppress')],
    },
    'NVDLC03TransitionToTheSink': {
        'op': 'add', 'why': 'Fade then SetStage MQ05 100 then Player.MoveTo NVDLC03EndMarker.',
        'rules': [r('PartyWarp', markers=['NVDLC03EndMarker'], mode='slot-offset', absent='hold')],
    },
    'NVDLC03TeleportEffectTimer': {
        'op': 'add', 'why': 'Fires every follower (SetPlayerTeammate 0, RemoveFromFaction, Waiting vars, MoveTo home marker) then '
                            'MoveTo SlideShowMarker.',
        'rules': [r('CompanionOwner', owner='recruiter', action='dismiss-all-on-entry'),
                  r('PartyWarp', markers=['NVDLC03SlideShowMarkerREF'], mode='slot-offset', absent='hold'),
                  r('SetPlayerControls', scope='all', release='host-effect', timeout_s=60, masks=[])],
    },
    'NVDLC03RadarFence': {
        'op': 'add', 'why': 'Per-player geofence: a player more than 23552 units from the pylon centre is teleported back to '
                            'StartMarker after a timed warning. Must be evaluated per player, never as min-over-party.',
        'rules': [r('LocalQuestScript', scripts=['NVDLC03RadarFence'], mode='run-on-every-machine-for-its-own-player'),
                  r('PerPlayerScene', scene='radar fence warning + MoveTo', per_player=['iState', 'fTimer', 'bRadar'], shared=[])],
    },
    'NVDLC03AutoDocTimer': {
        'op': 'add', 'why': 'Fade to black and back for the AutoDoc user only; EnablePlayerControls at the end.',
        'rules': [r('LocalQuestScript', scripts=['NVDLC03AutoDocTimer'], mode='run-on-every-machine-for-its-own-player'),
                  lock_origin()],
    },
    'NVDLC03HQBuddyBarkTimer': {
        'op': 'add', 'why': 'Sink device barks by distance/state; bark target is the nearest player.',
        'rules': [cond(GetDistance='min-over-party', IsActionRef='initiator'), r('InitiatorDialogue')],
    },
    'NVDLC03BrainTankOpenTimer': {
        'op': 'add', 'why': 'Opens the player-brain tank 5 s after any party member is near (OnTrigger Player) at MQ05 stage 40.',
        'rules': [cond(OnTrigger=STD_TRIGGER, GetItemCount='initiator', HasPerk='initiator')],
    },
    'NVDLC03X8ExperimentQuest': {
        'op': 'add', 'why': 'Hosts the X8Experiment cutscene scripts (DisablePlayerControls x4, Player.MoveTo viewing marker x4).',
        'rules': [lock_all([LOCK_ALL7]), r('PartyWarp', markers=[], cells=['NVDLC03X8School'], mode='slot-offset', absent='hold'),
                  cond(GetInCell='entry:all-in,exit:any-in')],
    },
    'NVDLC03AuralStealthSuitQUEST': {
        'op': 'add', 'why': 'Suit barks react to what the player wears/does (46 Player reads).',
        'rules': [r('LocalQuestScript', scripts=['NVDLC03AuralStealthSuitQUEST'], mode='run-on-every-machine-for-its-own-player'),
                  cond(GetEquipped='initiator', HasPerk='initiator', GetDetected='any-party-member')],
    },
    'NVDLC03X13RobotScorpions': {
        'op': 'add', 'why': 'Detection checks read Player.',
        'rules': [cond(GetDetected='any-party-member', GetDistance='min-over-party')],
    },
    'NVDLC03GiantRoboScorpionCombat': {
        'op': 'add', 'why': 'Boss encounter tied to Player distance/perks.',
        'rules': [cond(GetDistance='min-over-party', IsInCombat='any-party-member')],
    },
    'NVDLC03MobiusForbiddenBarkTimer': {'op': 'add', 'why': 'Bark timer by player distance.',
                                        'rules': [cond(GetDistance='min-over-party')]},
    'NVDLC03BigMTRandomBarkTimer': {'op': 'add', 'why': 'Bark timer by player position.', 'rules': [cond(GetDistance='min-over-party')]},
    'NVDLC03PlotReactionBark': {'op': 'add', 'why': 'Bark selection from quest-completion counters (shared).',
                                'rules': [cond(GetQuestCompleted='shared')]},
    # ---------------------------------------------------------------- dialogue containers
    'NVDLC03DialogueKlein': {'op': 'add', 'why': 'Klein: first-meeting forced scene, task assignment (MQ01 100 + MQ02/03/04 10), barter/skill '
                                                 'checks, kill-or-ally finale.',
                             'rules': [r('InitiatorDialogue'), r('RewardAll'),
                                       lock_all([LOCK_ALL7], timeout=180)]},
    'NVDLC03DialogueMobius': {'op': 'add', 'why': 'Mobius confrontation sets a shared faction relation (SetEnemy).',
                              'rules': [r('InitiatorDialogue'), r('RewardAll')]},
    'NVDLC03DialoguePlayerBrain': {'op': 'add', 'why': 'Brain jar conversation: item grant and perk swap (initiator\'s).',
                                   'rules': [r('InitiatorDialogue'), r('RewardAll', include=['AddItem']),
                                             r('QuestItemDup', recipient='all', items=['NVDLC03PlayerBrain']),
                                             r('ShareKarmaRep', note='Lie option = RewardKarma; Karma-conditioned lines')]},
    'NVDLC03DialogueBorous': {'op': 'add', 'why': 'Borous: Speech 45 check, bowl hand-in.', 'rules': [r('InitiatorDialogue'), r('RewardAll')]},
    'NVDLC03DialogueDala': {'op': 'add', 'why': 'Dala: Speech 45 / Perception 8 checks, tips.', 'rules': [r('InitiatorDialogue'), r('RewardAll')]},
    'NVDLC03Dialogue0': {'op': 'add', 'why': 'Dr. O: Int/Speech/Barter checks.', 'rules': [r('InitiatorDialogue'), r('RewardAll')]},
    'NVDLC03Dialogue8': {'op': 'add', 'why': 'Dr. 8: Karma lines (RewardKarma), skill checks.',
                         'rules': [r('InitiatorDialogue'), r('RewardAll'), r('ShareKarmaRep')]},
    'NVDLC03DialogueHQBuddies': {'op': 'add', 'why': 'Sink buddies: AutoDoc trait respec (RemovePerk x16 + ShowTraitMenu) is per player, '
                                                     'Main Computer barter menu is the initiator\'s.',
                                 'rules': [r('InitiatorDialogue'),
                                           r('PerPlayerScene', scene='AutoDoc "Psych Evaluation" trait respec + barter menus',
                                             per_player=['perks', 'barter menu'], shared=['bPsychEval']),
                                           r('RewardAll')]},
}

NEW_KINDS = [{
    'kind': 'LocalQuestScript',
    'justification': 'The L5 guest gate skips EVERY quest-owned script on a guest (suppress_policy: questScriptsMayRun). %(n)d OWB '
                     'quest scripts act on or read the local Player (implant perk engine, AutoDoc fade timer, radar '
                     'fence, stealth-suit barks, teleport/transition timers). None of the 16 kinds says "run this script on every '
                     'machine against that machine\'s own Player, with shared-state mutators still dropped".',
    'params': {'scripts': 'quest EDIDs', 'mode': 'run-on-every-machine-for-its-own-player'},
    'consumers': ['plugin/hooks/suppress_policy.cpp (questScriptsMayRun allowlist)', 'synccore rules.h (kind registry)'],
}]

NEW_PARAMS = {
    'SetPlayerControls.scope': '"origin": lock only the player who ran the scene (AutoDoc fade). Existing value: "all".',
    'PartyTether.anchor/radius': 'tether to a ref + radius when the scene is in an exterior worldspace with no interior cell.',
    'PartyTether.gather': '"wait" is already documented; used here for scenes deep inside a dungeon (no auto-warp).',
    'PartyWarp.cells': 'cell EDIDs as warp targets when the script\'s marker ref is only in refnames.json.gz.',
    'RewardAll.include': 'list of effect names routed Recipient=All although they are not rewards (AddPerk/AddItem/EquipItem).',
    'QuestItemDup.items': 'explicit item EDIDs the rule governs (for tests and for the hook allowlist).',
    'PartyAwareCond.funcs.OnTrigger': 'trigger-block filter "Player": fire for any party member. EnterTrigger likewise.',
    'PartyAwareCond.funcs.GetMapMarkerVisible': 'union-over-party (ending slides count discovered locations).',
}

EFFECT_PROPOSALS = [
    {'type': 'SetFactionRelation', 'for': 'SetEnemy/SetAlly/SetPCEnemyOfFaction x29 (Think Tank, Mobius, giant robo-scorpion, X-42 turrets)',
     'fields': 'factionA, factionB, relation(enemy|ally|neutral), host-wins'},
    {'type': 'SetPerk (capture+apply)', 'for': 'AddPerk x25 / RemovePerk x100; Effect exists, hooks are BLOCK_CMD only',
     'fields': 'perk, rank, Recipient (All for MQ01 stage 10, initiator for AutoDoc)'},
    {'type': 'NoteChange', 'for': 'AddNote x16 / RemoveNote x59 / GetHasNote x94 (HQ-Buddy unlock notes)',
     'fields': 'note, add|remove, Recipient'},
    {'type': 'PlayerRefSubstitution (hook, not an Effect)', 'for': 'object/trigger/activate scripts run on the host on behalf of a guest',
     'fields': '`Player`/`PlayerRef` literals inside the executing script resolve to the guest ghost'},
    {'type': 'SetLock/SetOpenState capture', 'for': 'Lock x76, Unlock x63, SetOpenState x43 gate Big Empty, Forbidden Zone, X-8, X-13',
     'fields': 'ref, locked, level, openState'},
]


def new_kinds(n_player_bound):
    """NEW_KINDS with the computed count of player-bound quest scripts (gaps.json quest_scripts_player_bound) filled in."""
    out = []
    for k in NEW_KINDS:
        k = dict(k)
        k['justification'] = k['justification'] % {'n': n_player_bound}
        out.append(k)
    return out
