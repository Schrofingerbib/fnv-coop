"""Curated sweep prerequisites for Dead Money (hand-written, validated by run_tests.py against the quest data).

Commands are structured so build.py can render the hex-FormID console text:
    {'verb': 'setstage', 'quest': EDID, 'stage': N, 'why': ...}
    {'verb': 'startquest', 'quest': EDID, 'why': ...}
    {'verb': 'set', 'quest': EDID, 'var': NAME, 'value': V, 'why': ...}      (quest variable; console syntax unverified)
The test checks: quest and stage exist, the variable is declared in that quest's script, the verb is one of the console commands
FNV/xNVSE has (setstage, startquest, set).
"""
ALLOWED_VERBS = ('setstage', 'startquest', 'set')


def S(quest, stage, why=''):
    return {'verb': 'setstage', 'quest': quest, 'stage': stage, 'why': why}


def V(quest, var, value, why=''):
    return {'verb': 'set', 'quest': quest, 'var': var, 'value': value, 'why': why, 'verified': False}


VILLA = [S('NVDLC01MQ01', 5, 'Villa start state: collar + outfit + holorifle loadout, DLC radios on, Elijah barks started')]
COMPANIONS_FREE = VILLA + [
    S('NVDLC01MQ01a', 100, 'Dog freed (sets Followers.bDogAvailable, completes quest)'),
    S('NVDLC01MQ01b', 100, 'Dean recruited-able (Followers.bDeanAvailable)'),
    S('NVDLC01MQ01c', 100, 'Christine recruited-able (Followers.bChristineAvailable)'),
]
GALA_DONE = COMPANIONS_FREE + [
    S('NVDLC01MQ02a', 100, "Dog's Gala role done"),
    S('NVDLC01MQ02b', 100, "Dean's Gala role done"),
    S('NVDLC01MQ02c', 100, "Christine's Gala role done"),
    S('NVDLC01MQ02', 50, 'Gala event fired (unlocks SierraMadreEntryDoor, ghosts spawn)'),
    S('NVDLC01MQ02', 100, 'Act 2 complete'),
]

QUEST_PREREQ = {
    'NVDLC01MQ00': {'needs': ['chargen finished; RNVTARef enabled (quest script waits 30 s then sets stage 10)'], 'setup': [],
                    'note': 'start_enabled. Stage 10 is normally set 30 s after Radio New Vegas is enabled; stage 100 by the bunker gas trap.'},
    'NVDLC01FountainStartSequence': {
        'needs': ['NVDLC01Intro slideshow dialogue (started by the bunker gas trap)'], 'setup': [],
        'note': 'SetStage starts the quest and its GameMode script then runs: after ~5 s it calls StartQuest NVDLC01MQ01, SetStage NVDLC01MQ01 5, '
                'EnablePlayerControls and Activate Player on Elijah. A capture window that outlives 5 s records those timer-driven effects too.'},
    'NVDLC01MQ01': {'needs': ['Villa entered (NVDLC01FountainStartSequence timer sets stage 5)'], 'setup': [],
                    'note': 'Stage 5 is the real start: it adds and equips the jumpsuit, collar and holorifle and starts NVDLC01ElijahBarkTown.'},
    'NVDLC01MQ01a': {'needs': ['NVDLC01MQ01 stage 5'], 'setup': VILLA, 'note': 'Stage 30 is normally set by the quest script when the player holds NVDLC01DogCommandNote.'},
    'NVDLC01MQ01b': {'needs': ['NVDLC01MQ01 stage 5'], 'setup': VILLA, 'note': ''},
    'NVDLC01MQ01c': {'needs': ['NVDLC01MQ01 stage 5'], 'setup': VILLA, 'note': ''},
    'NVDLC01MQ01d': {'needs': ['NVDLC01MQ01 stage 5'], 'setup': VILLA, 'note': 'Normally started by Elijah dialogue after the three recruits.'},
    'NVDLC01MQ02a': {'needs': ['NVDLC01MQ01 stage 5', 'Dog free'], 'setup': COMPANIONS_FREE,
                     'note': 'Stage 5 is set by the last of MQ01a/b/c stage 100 and itself calls SetStage NVDLC01MQ01 100.'},
    'NVDLC01MQ02b': {'needs': ['Dean available'], 'setup': COMPANIONS_FREE, 'note': ''},
    'NVDLC01MQ02c': {'needs': ['Christine available'], 'setup': COMPANIONS_FREE, 'note': 'Stage order inside the quest is not derivable statically (50/60/70 are alternative branches of objective 40-70).'},
    'NVDLC01MQ02': {'needs': ['MQ02a, MQ02b, MQ02c complete (their stage 100 sets MQ02 stage 10)'],
                    'setup': COMPANIONS_FREE + [S('NVDLC01MQ02a', 100, 'Dog Gala done'), S('NVDLC01MQ02b', 100, 'Dean Gala done'), S('NVDLC01MQ02c', 100, 'Christine Gala done')],
                    'note': 'Stage 50 autosaves (only when no collar countdown is running) and warps Dean, Christine and Dog.'},
    'NVDLC01MQ03a': {'needs': ['MQ02 stage 100'], 'setup': GALA_DONE, 'note': 'MQ02 stage 100 calls SetStage NVDLC01MQ03 5 (MQ03 has no stage 5: a no-op).'},
    'NVDLC01MQ03b': {'needs': ['MQ03a stage 10 sets MQ03b stage 10'], 'setup': GALA_DONE + [S('NVDLC01MQ03a', 10, 'starts MQ03b and MQ03c')], 'note': ''},
    'NVDLC01MQ03c': {'needs': ['MQ03a stage 10 sets MQ03c stage 10'], 'setup': GALA_DONE + [S('NVDLC01MQ03a', 10, 'starts MQ03b and MQ03c')], 'note': ''},
    'NVDLC01MQ03d': {'needs': ['MQ03a stage 5'], 'setup': GALA_DONE + [S('NVDLC01MQ03a', 5, 'casino access objective')], 'note': 'Stages 10 and 20 are empty; stage 100 only stops the quest.'},
    'NVDLC01MQ03': {'needs': ['MQ03a, MQ03b, MQ03c complete (their stage 100 sets MQ03 stage 10)'],
                    'setup': GALA_DONE + [S('NVDLC01MQ03a', 100, 'Dog casino done'), S('NVDLC01MQ03b', 100, 'Dean theatre done'), S('NVDLC01MQ03c', 100, 'Christine suites done')],
                    'note': 'Quest script ends the game early if VaultCodeBox.bPlayerTrapped / bElijahTeamsWithPlayer / VaultMainTerminalREF.bReadPersonal is set; the sweep does not set them.'},
    'NVDLC01ElijahBarkTown': {'needs': ['NVDLC01MQ01 stage 5 starts it'], 'setup': VILLA, 'note': 'Bark timeline quest: stages are independent triggers.'},
}

# nominal order of stages inside each quest (for `setup`: stages that normally precede a stage). Not derived from the ESM: written
# from the stage texts / objective chain; "unverified" where noted in QUEST_PREREQ.
CHAIN = {
    'NVDLC01MQ00': [10, 100], 'NVDLC01MQ01': [5, 100],
    'NVDLC01MQ01a': [10, 20, 30, 40, 100], 'NVDLC01MQ01b': [10, 20, 100], 'NVDLC01MQ01c': [10, 20, 30, 40, 100],
    'NVDLC01MQ01d': [10, 100],
    'NVDLC01MQ02': [10, 50, 100], 'NVDLC01MQ02a': [5, 10, 30, 40, 100], 'NVDLC01MQ02b': [10, 30, 40, 100],
    'NVDLC01MQ02c': [10, 20, 30, 50, 60, 70, 100],
    'NVDLC01MQ03': [10, 20, 30, 40, 50, 100], 'NVDLC01MQ03a': [5, 10, 20, 30, 40, 50, 100],
    'NVDLC01MQ03b': [10, 20, 30, 40, 50, 60, 100], 'NVDLC01MQ03c': [10, 20, 30, 100], 'NVDLC01MQ03d': [10, 20, 100],
}
# stages that hang off the chain: stage -> stage after which they occur
BRANCH_FROM = {
    ('NVDLC01MQ01a', 255): 10, ('NVDLC01MQ01b', 255): 10, ('NVDLC01MQ01c', 255): 10, ('NVDLC01MQ02a', 255): 10,
    ('NVDLC01MQ02b', 255): 10, ('NVDLC01MQ02c', 255): 10, ('NVDLC01MQ03', 255): 10, ('NVDLC01MQ02', 255): 10,
    ('NVDLC01MQ03a', 60): 50, ('NVDLC01MQ03b', 15): 10,
}


def stage_setup(edid, stage):
    """Commands (structured) that precede `setstage edid stage` in the sweep: the quest's own earlier stages, in order."""
    chain = CHAIN.get(edid)
    if not chain:
        return []
    if (edid, stage) in BRANCH_FROM:
        upto = BRANCH_FROM[(edid, stage)]
        pre = [s for s in chain if s <= upto]
    elif stage in chain:
        pre = chain[:chain.index(stage)]
    else:
        pre = []
    return [S(edid, s, 'earlier stage on the nominal path') for s in pre]


EXCLUDE_QUEST = {
    'NVDLC01EnemyTest': 'developer enemy-test quest: its stage scripts player.moveto into test cells and SetPlayerTeammate on three NPCs; '
                        'ExcludeFromGate in rules.json; not part of any ending',
}
EXCLUDE = {}

SWEEP_ORDER = [
    'NVDLC01MQ00', 'NVDLC01MQ01', 'NVDLC01MQ01a', 'NVDLC01MQ01b', 'NVDLC01MQ01c', 'NVDLC01MQ01d',
    'NVDLC01MQ02a', 'NVDLC01MQ02b', 'NVDLC01MQ02c', 'NVDLC01MQ02', 'NVDLC01MQ03a', 'NVDLC01MQ03b', 'NVDLC01MQ03c',
    'NVDLC01MQ03d', 'NVDLC01ElijahBarkTown', 'NVDLC01MQ03', 'NVDLC01FountainStartSequence', 'NVDLC01EnemyTest',
]
HANG_RISK = {
    'NVDLC01FountainStartSequence': 'high: after ~5 s its quest script activates Elijah (dialogue) and enables controls; run last, expect a menu',
    'NVDLC01MQ03': 'medium: stage 100 unlocks gates, destroys the bunker terminal and starts NVDLC01ChipReward (3-day timer); run late',
    'NVDLC01MQ02': 'medium: stage 50 autosaves and moves three companions',
    'NVDLC01MQ00': 'low',
}
RESET_EXTRA = ['NVDLC01ElijahBarkTown', 'NVDLC01MQ03d']

NOT_STAGE_DRIVEN = [
    {'system': 'bomb collar', 'quests': ['NVDLC01BombCollarQuest', 'NVDLC01RadioSpeakers', 'NVDLC01ElijahCollarBark'],
     'why': 'continuous GameMode state machine (timers, radii, deaths); no stages. Needs event probes + the two-game test.'},
    {'system': 'toxic cloud', 'quests': ['NVDLC01GlobalToxicDamage', 'NVDLC01ToxicCloud'], 'why': 'per-frame damage to PlayerREF; no stages.'},
    {'system': 'companions', 'quests': ['NVDLC01Followers', 'NVDLC01DogDialogue', 'NVDLC01DeanDialogue', 'NVDLC01ChristineDialogue',
                                          'NVDLC01NewVegasFollowerFireQuest'],
     'why': 'driven by dialogue result scripts and the Hire/Fire trigger activators; see dialogue.md.'},
    {'system': 'intro and ending', 'quests': ['NVDLC01Intro', 'NVDLC01Ending', 'NVDLC01BunkerTransitionTimer', 'NVDLC01VillaTransitionTimer',
                                               'NVDLC01FadeToCreditsTimer'],
     'why': 'cinematics driven by trigger volumes and timers; slideshow via INFO SwapTexture scripts.'},
    {'system': 'radio and barks', 'quests': ['NVDLC01RadioElijah', 'NVDLC01RadioDog', 'NVDLC01RadioDeanQuest', 'NVDLC01RadioChristine',
                                                'NVDLC01RadioCasino', 'NVDLC01RadioAMBMusic', 'NVDLC01RadioDeanMusic'],
     'why': 'radio-type INFOs (248) with no stages; host-side audio.'},
]

# Non-stage probes: each is a precise, small thing L7 or the two-game test can run. `call_ref` = calling reference for the bridge
# exec.ref method (hex FormID is resolved by build.py). `expect_effects` are synccore effect names the capture must produce.
EVENT_PROBES = [
    {'id': 'hire-dean', 'two_game': True, 'call_ref': 'NVDLC01HireTriggerREF', 'cmd': 'Activate {NVDLC01DeanREF} 1',
     'script': 'NVDLC01HireScript',
     'expect_effects': ['CompanionOwner', 'SetFactionRank', 'SetPerk', 'SetScriptVar'],
     'why': 'recruit: SetPlayerTeammate, AddToFaction FollowerFaction, player.AddPerk NVDLC01UncleanLiving, Followers.rCurrentFollower'},
    {'id': 'fire-dean', 'two_game': True, 'call_ref': 'NVDLC01FireTriggerREF', 'cmd': 'Activate {NVDLC01DeanREF} 1',
     'script': 'NVDLC01FireScript',
     'expect_effects': ['CompanionOwner', 'SetFactionRank', 'SetPerk', 'SetScriptVar'],
     'why': 'dismiss: reverse of hire-dean, plus MoveToMarkerWithFade to the fountain'},
    {'id': 'collar-radius-enter', 'two_game': True, 'vars': [('NVDLC01BombCollarQuest', 'iNumRadii', 1)],
     'script': 'NVDLC01BombCollarQuestSCRIPT',
     'expect_effects': ['SetScriptVar'],
     'why': 'iNumRadii > 0 starts the 10 s countdown; with two players only the one in the radius may be hurt'},
    {'id': 'toxic-tick', 'two_game': True, 'script': 'NVDLC01GlobalToxicDamageSCRIPT', 'expect_effects': ['ModActorValue'],
     'why': 'one tick of PlayerREF.DamageActorValue Health 3; must reach only the slot standing in the Villa'},
    {'id': 'vault-elevator', 'two_game': True, 'call_ref': 'NVDLC01VaultElevatorREF', 'cmd': 'Activate {Player} 0',
     'script': 'NVDLC01VaultElevatorScript',
     'expect_effects': ['SetStage', 'DisableRef', 'MoveRef', 'SetLock'],
     'why': 'completes MQ03 for everybody and moves one player; needs a Barrier + PartyWarp'},
    {'id': 'mojave-gate', 'two_game': True, 'script': 'NVDLC01SMGateMojaveSCRIPT',
     'expect_effects': ['SetPlayerControls', 'MoveRef', 'StartQuest', 'SlideshowEnd'],
     'why': 'exit gate: button box -> radios restored -> controls locked -> slideshow marker'},
]
