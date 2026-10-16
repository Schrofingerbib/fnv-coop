"""Script command -> synccore Effect + L5 capture/apply status (as of internal lane notes round 8 and plugin/hooks/*).

Status vocabulary:  supported | partial | missing | n/a
  capture  = does the host emit this as an Effect inside the txn of the script that ran it (plugin/hooks capture_hub)?
  apply    = can a client apply it without running the script (plugin/hooks apply_remote)?
  n/a      = not shared state (cosmetic / local UI / handled by NV:MP actor sync) - nothing to capture.
`partial` also covers "delegated to NV:MP actor sync (L6), not proven in game" for NPC AI commands.

Sources of truth used for every row (so the table can be audited; run_tests.py re-checks them against the code):
  capture hooks in plugin/hooks/engine_hooks.cpp + engine_addrs.h (state of 2026-10-16, after L5's "census backlog" work):
    SetStage, objective SetState, SetRunning/SetCompleted/SetFailed, CompleteAllObjectives, TESReputation Add/Set/Remove/AddExact,
    RewardKarma, SetDisabled, Cmd_AddItem/RemoveItem (player -> AddItem/RemoveItem, other refs -> RefAddItem/RefRemoveItem),
    RewardXP, KillActor, ResurrectActor, MoveToMarker/WithFade, SetPos, ModAV/ForceAV/SetAV/DamageAV, AddToFaction/SetFactionRank/
    RemoveFromFaction -> SetFactionRank, AddPerk/RemovePerk (player) -> SetPerk, Lock/UnLock/SetOpenState/SetDestroyed -> ObjectState,
    AddNote/RemoveNote/AddTopic, Autosave/SystemSave/ForceSave -> RequestSave, RemoveAllItems (non-player), PlaceAtMe -> SpawnRef,
    Player::SetControlsDisabled -> SetPlayerControls; globals and quest vars by diff.
  block-only on guests, pass-through on the host (NOT captured): ModFactionRank, AddScriptPackage/RemoveScriptPackage,
    StartCombat/StopCombat, script Activate.
  apply_remote.cpp get_if<> list is the applier truth; any Effect not in it is not applied by RemoteApplier.
"""

SUP, PAR, MIS, NA = 'supported', 'partial', 'missing', 'n/a'

# hazard tags (internal questline notes list)
H_REWARD = 'player-reward'      # rewards / items / xp / notes given to "the player"
H_STAT = 'player-stat'          # ForceAV/RestoreAV/ResetHealth ... on the player
H_PERK = 'perk'
H_KARMA = 'karma-rep-fame'
H_FACTION = 'faction-membership'
H_SPAWN = 'spawn'
H_COMPANION = 'companion'
H_ESSENTIAL = 'essential-flag'
H_TELEPORT = 'teleport-or-cell-transition'
H_CINEMATIC = 'cinematic-or-control-lock'
H_MENU = 'menu-or-message'
H_TIMER = 'timer'
H_RADIO = 'radio'
H_ENDING = 'ending-slideshow'
H_KILL = 'kill-choice'
H_SAVE = 'save'
H_TRAVEL = 'fast-travel'
H_CASINO = 'casino'
H_DIALOGUE = 'forced-dialogue'
H_AI = 'npc-ai-or-combat'
H_LOCK = 'door-lock'
H_WORLD = 'world-object-state'
H_CRIME = 'crime-hostility'
H_INVENTORY = 'player-inventory-move'
H_TOPIC = 'topic-knowledge'
H_READ = 'player-reference-read'

ALL_HAZARDS = [H_REWARD, H_STAT, H_PERK, H_KARMA, H_FACTION, H_SPAWN, H_COMPANION, H_ESSENTIAL, H_TELEPORT, H_CINEMATIC,
               H_MENU, H_TIMER, H_RADIO, H_ENDING, H_KILL, H_SAVE, H_TRAVEL, H_CASINO, H_DIALOGUE, H_AI, H_LOCK, H_WORLD,
               H_CRIME, H_INVENTORY, H_TOPIC, H_READ]


def C(effect, capture, apply, tags=(), sev='info', note='', rule=None):
    return dict(effect=effect, capture=capture, apply=apply, tags=list(tags), sev=sev, note=note, rule=rule)


_NPC_AI = ('delegated to NV:MP host-owned actor sync (L6); no Effect, not proven in game')

CMD = {
    # ---- quest state: fully supported -------------------------------------------------------------------------
    'setstage': C('SetStage', SUP, SUP),
    'setobjectivedisplayed': C('SetObjective', SUP, SUP),
    'setobjectivecompleted': C('SetObjective', SUP, SUP),
    'startquest': C('StartQuest', SUP, SUP),
    'stopquest': C('StopQuest', SUP, SUP),
    'completequest': C('CompleteQuest', SUP, SUP),
    'failquest': C('FailQuest', SUP, SUP),
    'completeallobjectives': C('CompleteAllObjectives', SUP, SUP),
    'setquestdelay': C(None, NA, NA, sev='info', note='quest tick rate; quest scripts only run on the host'),
    'forceactivequest': C(None, NA, NA, sev='low', note='Pip-Boy active-quest pointer is per player UI state'),
    'addtopic': C('AddTopic', SUP, SUP, [H_TOPIC], note='shared journal topic (effect.h: topics, notes and map markers are shared)'),
    # ---- ref state --------------------------------------------------------------------------------------------
    'enable': C('EnableRef', SUP, SUP, note='captured on state change only (SetDisabled hook)'),
    'disable': C('DisableRef', SUP, SUP, note='captured on state change only (SetDisabled hook)'),
    'kill': C('KillActor', SUP, SUP),
    'killactor': C('KillActor', SUP, SUP),
    'resurrect': C('ResurrectActor', SUP, SUP),
    'markfordelete': C(None, MIS, MIS, [H_WORLD], 'medium', note='permanent removal of a ref; no hook (DespawnRef exists but nothing records it)'),
    'placeatme': C('SpawnRef', SUP, PAR, [H_SPAWN], 'medium',
                   note='Cmd_PlaceAtMe records SpawnRef (host allocates a stable created-ref); RemoteApplier has no get_if<SpawnRef>, apply side unverified'),
    'setdestroyed': C('ObjectState', SUP, SUP, [H_WORLD], note='ObjectState kObjDestroyed'),
    'cleardestruction': C(None, MIS, MIS, [H_WORLD], 'low'),
    'setopenstate': C('ObjectState', SUP, SUP, [H_WORLD], note='ObjectState kObjOpen'),
    'swaptexture': C('SwapTexture', MIS, MIS, [H_WORLD], 'low', note='Effect exists, no capture hook, applier Unsupported'),
    'lock': C('ObjectState', SUP, SUP, [H_LOCK], note='ObjectState kObjLock (the older SetLock Effect is superseded)'),
    'unlock': C('ObjectState', SUP, SUP, [H_LOCK], note='as Lock'),
    'setownership': C(None, MIS, MIS, [H_WORLD], 'low', note='ownership changes decide theft/crime'),
    'setcellownership': C(None, MIS, MIS, [H_WORLD], 'low'),
    'activate': C(None, MIS, MIS, [H_WORLD], 'medium',
                  note='script Activate (door/trap/activator); host-run, hook is guest-block only; with `player` as activator only the host player transits'),
    'entertrigger': C(None, PAR, PAR, [H_READ], 'medium',
                      note='Ref.EnterTrigger Player: asks the trigger about the host player only; party-aware evaluation needed'),
    'dropme': C(None, MIS, MIS, [H_WORLD], 'low'),
    'setactorfullname': C(None, MIS, MIS, [H_WORLD], 'low', note='display-name change of an NPC'),
    'showmap': C('SetMapMarker', MIS, MIS, [H_WORLD], 'low', note='Effect SetMapMarker exists; no Cmd_ShowMap hook'),
    # ---- items / rewards --------------------------------------------------------------------------------------
    'additem': C('AddItem', SUP, SUP, [H_REWARD], note='player subject; non-player containers are not captured', rule='RewardAll'),
    'additemns': C('AddItem', MIS, SUP, [H_REWARD], 'low', note='no Cmd hook for the no-sound variant'),
    'additemhealthpercent': C('AddItem', MIS, SUP, [H_REWARD], 'low', note='no Cmd hook'),
    'removeitem': C('RemoveItem', SUP, SUP, [H_REWARD], note='player subject; non-player containers are not captured',
                    rule='RemoveItemPerOwner'),
    'removeallitems': C('RemoveAllItems', PAR, SUP, [H_INVENTORY], 'medium',
                        note='records RemoveAllItems(container) for non-player subjects; the TRANSFER to the target (e.g. back to the player) is not captured'),
    'removealltypeditems': C(None, MIS, MIS, [H_INVENTORY], 'high',
                             note='casino frisk: player.RemoveAllTypedItems moves weapons into a container; no hook, host player only'),
    'equipitem': C(None, NA, NA, sev='low', note='equipment is per-actor presentation (NV:MP actor sync)'),
    'unequipitem': C(None, NA, NA, sev='low'),
    'addnote': C('AddNote', SUP, SUP, [H_REWARD], note='shared journal note (policy: every player gets quest notes)'),
    'removenote': C('RemoveNote', SUP, SUP, [H_REWARD]),
    'addspell': C(None, MIS, MIS, [H_STAT], 'low'),
    'removespell': C(None, MIS, MIS, [H_STAT], 'low'),
    'cios': C(None, MIS, MIS, [H_STAT], 'low', note='CastImmediateOnSelf: spell effect on the caster'),
    # ---- stats / reputation -----------------------------------------------------------------------------------
    'rewardxp': C('ModActorValue', SUP, SUP, [H_REWARD], note='folds into ModActorValue(kAvExperience)', rule='RewardAll'),
    'setav': C('ModActorValue', SUP, SUP, [H_STAT], note='player subject; SetAV is captured as a before/after delta'),
    'setactorvalue': C('ModActorValue', SUP, SUP, [H_STAT]),
    'modav': C('ModActorValue', SUP, SUP, [H_STAT]),
    'modactorvalue': C('ModActorValue', SUP, SUP, [H_STAT]),
    'damageactorvalue': C('ModActorValue', SUP, SUP, [H_STAT]),
    'restoreav': C('ModActorValue', MIS, MIS, [H_STAT], 'medium', note='RestoreAV has no hook (only Mod/Force/Set/Damage)'),
    'restoreactorvalue': C('ModActorValue', MIS, MIS, [H_STAT], 'medium', note='as RestoreAV'),
    'resethealth': C('ModActorValue', MIS, MIS, [H_STAT], 'medium', note='no hook'),
    'rewardkarma': C('ModKarma', SUP, SUP, [H_KARMA], rule='ShareKarmaRep'),
    'addreputation': C('ModReputation', SUP, SUP, [H_KARMA], rule='ShareKarmaRep'),
    'removereputation': C('ModReputation', SUP, SUP, [H_KARMA], rule='ShareKarmaRep'),
    'setreputation': C('ModReputation', SUP, SUP, [H_KARMA], rule='ShareKarmaRep'),
    'addreputationexact': C('ModReputation', SUP, SUP, [H_KARMA], rule='ShareKarmaRep'),
    'addperk': C('SetPerk', SUP, SUP, [H_PERK], note='player subject only (perkCmd tracks isPlayer); routed like rewards'),
    'removeperk': C('SetPerk', SUP, SUP, [H_PERK], note='as AddPerk'),
    # ---- faction membership / crime --------------------------------------------------------------------------
    'addtofaction': C('SetFactionRank', SUP, SUP, [H_FACTION], note='shared by owner decision; applied by console on the same actor on every machine'),
    'removefromfaction': C('SetFactionRank', SUP, SUP, [H_FACTION], note='as AddToFaction (rank < 0)'),
    'removefromallfactions': C('SetFactionRank', MIS, MIS, [H_FACTION], 'medium', note='no Cmd hook (only AddToFaction/RemoveFromFaction/SetFactionRank)'),
    'setfactionrank': C('SetFactionRank', SUP, SUP, [H_FACTION]),
    'sendassaultalarm': C(None, MIS, MIS, [H_CRIME], 'medium', note='crime event on the player; host player only'),
    'sendtrespassalarm': C(None, MIS, MIS, [H_CRIME], 'medium'),
    'setpcenemyoffaction': C(None, MIS, MIS, [H_CRIME], 'high', note='makes a faction hostile to the player; faction-wide AI state'),
    'clearfactionplayerenemyflag': C(None, MIS, MIS, [H_CRIME], 'high'),
    'ignorecrime': C(None, NA, NA, [H_CRIME], 'low', note='actor flag on host-owned NPCs'),
    'setally': C(None, PAR, PAR, [H_AI], 'low', note=_NPC_AI),
    'setenemy': C(None, PAR, PAR, [H_AI], 'low', note=_NPC_AI),
    # ---- NPC AI (host-owned actors) ---------------------------------------------------------------------------
    'startcombat': C(None, PAR, PAR, [H_AI], 'medium', note=_NPC_AI + '; `StartCombat Player` targets the host player only'),
    'stopcombat': C(None, PAR, PAR, [H_AI], 'low', note=_NPC_AI),
    'addscriptpackage': C(None, PAR, PAR, [H_AI], 'low', note=_NPC_AI),
    'removescriptpackage': C(None, PAR, PAR, [H_AI], 'low', note=_NPC_AI),
    'evp': C(None, NA, NA, [H_AI], 'info', note='EvaluatePackage'),
    'resetai': C(None, NA, NA, [H_AI], 'info'),
    'setrestrained': C(None, PAR, PAR, [H_AI], 'low', note=_NPC_AI + '; on the player it is a cutscene lock'),
    'setunconscious': C(None, PAR, PAR, [H_AI], 'low', note=_NPC_AI),
    'setalert': C(None, NA, NA, [H_AI], 'info'),
    'setcombatstyle': C(None, NA, NA, [H_AI], 'info'),
    'setignorefriendlyhits': C(None, NA, NA, [H_AI], 'info'),
    'sifh': C(None, NA, NA, [H_AI], 'info', note='SetIgnoreFriendlyHits'),
    'forceflee': C(None, PAR, PAR, [H_AI], 'low', note=_NPC_AI),
    'pushactoraway': C(None, NA, NA, [H_AI], 'info'),
    'look': C(None, NA, NA, [H_AI], 'info'),
    'playidle': C(None, NA, NA, [H_AI], 'info', note='animation'),
    'playgroup': C(None, NA, NA, [H_AI], 'info', note='animation'),
    'useweapon': C(None, PAR, PAR, [H_AI], 'low', note=_NPC_AI),
    'setsecexp': C(None, NA, NA, [H_AI], 'info', note='securitron facial expression'),
    # ---- movement ---------------------------------------------------------------------------------------------
    'moveto': C('MoveRef', SUP, PAR, [H_TELEPORT], note='applier uses console MoveTo; cross-cell without marker Unsupported', rule='PartyWarp'),
    'movetomarker': C('MoveRef', SUP, PAR, [H_TELEPORT], rule='PartyWarp'),
    'movetomarkerwithfade': C('MoveRef', SUP, PAR, [H_TELEPORT], rule='PartyWarp'),
    'movetofade': C('MoveRef', PAR, PAR, [H_TELEPORT], 'low', note='alias of MoveToMarkerWithFade? not in the hook list; unverified', rule='PartyWarp'),
    'setpos': C('MoveRef', SUP, PAR, [H_TELEPORT], note='same-cell absolute position only'),
    # ---- dialogue / UI ---------------------------------------------------------------------------------------
    'sayto': C('ForcedDialogue', MIS, MIS, [H_DIALOGUE], 'medium',
               note='Effect exists (event); Cmd_SayTo located but not hooked; spectator playback is L8 work'),
    'say': C('ForcedDialogue', MIS, MIS, [H_DIALOGUE], 'low', note='as SayTo; ambient voice lines'),
    'startconversation': C('ForcedDialogue', MIS, MIS, [H_DIALOGUE], 'high',
                           note='forces a conversation on the PLAYER; with the initiator-picks model the host player would be dragged in'),
    'startradioconversation': C('ForcedDialogue', MIS, MIS, [H_DIALOGUE, H_RADIO], 'low'),
    'showmessage': C(None, MIS, MIS, [H_MENU], 'medium', note='UI message box runs on the host only; button results (GetButtonPressed) are host-local'),
    'showwarning': C(None, MIS, MIS, [H_MENU], 'low'),
    'showbartermenu': C(None, MIS, MIS, [H_MENU], 'high', note='barter UI opens on the host; the dialogue initiator may be a guest'),
    'showrepairmenu': C(None, MIS, MIS, [H_MENU], 'low'),
    'showbarbermenu': C(None, MIS, MIS, [H_MENU], 'low'),
    'openteammatecontainer': C(None, MIS, MIS, [H_MENU, H_COMPANION], 'medium'),
    'showblackjackmenuparams': C(None, MIS, MIS, [H_MENU, H_CASINO], 'high',
                                 note='casino UI + chip/caps changes + CasinoData winnings are engine-local; not an Effect'),
    'showroulettemenuparams': C(None, MIS, MIS, [H_MENU, H_CASINO], 'high', note='as blackjack'),
    'showslotmachinemenuparams': C(None, MIS, MIS, [H_MENU, H_CASINO], 'high', note='as blackjack'),
    'applyimagespacemodifier': C(None, NA, NA, [H_CINEMATIC], 'info', note='screen effect, local'),
    'imod': C(None, NA, NA, [H_CINEMATIC], 'info', note='screen effect, local'),
    'pms': C(None, NA, NA, sev='info', note='PlayMagicShaderVisuals'),
    'playsound': C(None, NA, NA, sev='info'),
    'playsound3d': C(None, NA, NA, sev='info'),
    'addachievement': C(None, NA, NA, sev='info', note='Steam achievement, per player, local'),
    # ---- control / save / travel / companions / radio ---------------------------------------------------------
    'disableplayercontrols': C('SetPlayerControls', SUP, SUP, [H_CINEMATIC],
                               note='captured at the primitive Player::SetControlsDisabled; the rules engine decides who is locked', rule='SetPlayerControls'),
    'enableplayercontrols': C('SetPlayerControls', SUP, SUP, [H_CINEMATIC], note='as DisablePlayerControls', rule='SetPlayerControls'),
    'enablefasttravel': C('SetFastTravelLock', MIS, MIS, [H_TRAVEL], 'medium',
                          note='Cmd hook located not installed; rules engine has no FastTravelLock session effect'),
    'autosave': C('RequestSave', SUP, PAR, [H_SAVE], 'low',
                  note='RequestSave EVENT: host saves, clients write a sidecar (session level, not RemoteApplier); guests drop the local command', rule='SaveSuppress'),
    'systemsave': C('RequestSave', SUP, PAR, [H_SAVE], 'low', note='as Autosave', rule='SaveSuppress'),
    'setplayerteammate': C('CompanionOwner', MIS, MIS, [H_COMPANION], 'high',
                           note='Effect exists; no hook, applier Unsupported; owner-by-recruiter is an open decision', rule='CompanionOwner'),
    'setbroadcaststate': C(None, MIS, MIS, [H_RADIO], 'low', note='radio station on/off'),
    'setnpcradio': C(None, MIS, MIS, [H_RADIO], 'low'),
}

# synthetic commands emitted by the scanner for non-command statements
CMD['set-questvar'] = C('SetScriptVar', SUP, SUP, [], 'info', note='quest variable write: captured by diffing every quest ScriptEventList around the outermost scope')
CMD['set-refvar'] = C('RefScriptVar', MIS, PAR, [], 'medium',
                      note='Set Ref.var on an object/NPC script: ref-script vars are not captured (unbounded set, no hook); RemoteApplier can write ref-owned SetScriptVar')
CMD['set-global'] = C('SetGlobal', SUP, SUP, [], 'info', note='globals are captured by diffing all TESGlobals around the outermost scope')

# reads (condition functions). rule = how the host must evaluate with a party (docs/quest-policies README, rules.py COND_REDUCTION)
READS = {
    'isactionref': ('initiator', H_READ), 'getactionref': ('initiator', H_READ), 'getdistance': ('min-over-party', H_READ),
    'getincell': ('entry:all-in,exit:any-in', H_READ), 'getinworldspace': ('any', H_READ), 'getinsamecell': ('any', H_READ),
    'getav': ('initiator', H_READ), 'getactorvalue': ('initiator', H_READ), 'getbaseactorvalue': ('initiator', H_READ),
    'getitemcount': ('initiator', H_READ), 'getequipped': ('initiator', H_READ), 'iskiller': ('any-party-member', H_READ),
    'getdetected': ('any', H_READ), 'getplayerteammate': ('owner', H_READ), 'getreputation': ('shared', H_READ),
    'getreputationthreshold': ('shared', H_READ), 'getdisposition': ('initiator', H_READ), 'getisreference': ('initiator', H_READ),
    'getlos': ('any', H_READ), 'issneaking': ('initiator', H_READ), 'isweaponout': ('initiator', H_READ),
    'hasperk': ('initiator', H_READ), 'gethasnote': ('initiator', H_READ), 'getlocked': ('world', H_READ),
    'getcasinowinningslevel': ('per-player', H_CASINO), 'getbuttonpressed': ('initiator-menu', H_MENU),
    'ispc1stperson': ('initiator', H_READ), 'gettalkedtopc': ('initiator', H_READ),
    'getfactionrelation': ('shared', H_READ), 'getactorfactionplayerenemy': ('shared', H_READ),
    'getcurrenttime': ('host-clock', H_TIMER), 'getsecondspassed': ('host-clock', H_TIMER),
    'getrandompercent': ('host-roll', H_TIMER), 'getdayofweek': ('host-clock', H_TIMER),
}
# reads that only look at shared quest / world state: nothing to do for co-op (documented so the test can prove coverage)
SHARED_STATE_READS = {
    'getobjectivecompleted', 'getobjectivedisplayed', 'getstage', 'getstagedone', 'getquestcompleted', 'getquestrunning',
    'getqc', 'getqr', 'getdead', 'getdeadcount', 'getdisabled', 'getlinkedref', 'getself', 'getisid', 'getglobalvalue',
    'getinfaction', 'getfactionrank', 'ispeakline', 'iscenestate', 'isimagespaceactive', 'isspelltarget', 'istarter',
    'getfurnituremarkerid', 'getweaponanimtype', 'getcurrentaipackage', 'getsitting', 'isincombat', 'ishoot',
    'istoneinjail', 'gethealthpercentage', 'getsleeping', 'iscurrentfurnitureref', 'isnowglobecount', 'ispeechinterrupted',
    'istonesneak', 'istoneslideorblackmail', 'getanimaction', 'isidleplaying', 'isaldialogue', 'gethitlocation',
    'getshouldattack', 'isanimplaying',
}

SUBJECT_PLAYER = {'player', 'playerref'}
PLAYER_IMPLICIT = {'rewardxp', 'rewardkarma', 'addreputation', 'removereputation', 'setreputation', 'addreputationexact',
                   'addnote', 'removenote', 'addperk', 'removeperk', 'addachievement', 'showmap', 'enableplayercontrols',
                   'disableplayercontrols', 'enablefasttravel', 'setplayerteammate', 'sendassaultalarm',
                   'sendtrespassalarm', 'setpcenemyoffaction', 'forceactivequest'}


def is_player_stmt(stmt):
    if stmt.get('subject') and stmt['subject'].lower() in SUBJECT_PLAYER:
        return True
    if stmt['fn'] in PLAYER_IMPLICIT:
        return True
    return any(a.lower() in SUBJECT_PLAYER for a in stmt.get('args', [])[:2]) and stmt['fn'] in (
        'startcombat', 'sayto', 'startconversation', 'activate', 'entertrigger', 'setenemy', 'look', 'moveto')


def combine(capture, apply):
    if capture == NA and apply == NA:
        return NA
    if capture == SUP and apply == SUP:
        return SUP
    if capture == MIS and apply == MIS:
        return MIS
    return PAR


def lookup(stmt):
    """-> dict row adjusted for the statement's subject, or None when the command is unknown.
    `variant` is set when the subject changes the verdict (row key = fn + '@' + variant)."""
    base = CMD.get(stmt['fn'])
    if base is None:
        return None
    row = dict(base)
    row['tags'] = list(base['tags'])
    row['variant'] = None
    fn, player = stmt['fn'], is_player_stmt(stmt)
    subj = (stmt.get('subject') or '').lower()
    if fn in ('additem', 'removeitem') and not player and subj:
        row.update(effect='RefAddItem' if fn == 'additem' else 'RefRemoveItem', capture=SUP, apply=SUP, sev='info', variant='npc', tags=[],
                   note='non-player container: captured as RefAddItem/RefRemoveItem (deltas relative to the baseline world)')
    if fn in ('addperk', 'removeperk') and not player and subj:
        row.update(capture=MIS, apply=MIS, sev='medium', variant='npc', tags=[],
                   note='perk on an NPC (e.g. CompanionSuite on a companion): perkCmd only tracks the player')
    if fn in ('setav', 'setactorvalue', 'modav', 'modactorvalue', 'damageactorvalue', 'restoreav', 'restoreactorvalue',
              'resethealth') and subj and not player:
        row.update(capture=NA, apply=NA, sev='info', variant='npc', tags=[],
                   note='NPC actor value: host-owned actor, replicated by NV:MP')
    if fn in ('moveto', 'movetomarker', 'movetomarkerwithfade', 'movetofade', 'setpos') and not player:
        row.update(tags=[], sev='low', rule=None, variant='npc',
                   note='NPC/object move: captured; client applies through console MoveTo')
    if fn in ('addtofaction', 'removefromfaction', 'removefromallfactions', 'setfactionrank') and not player:
        row.update(sev='medium', variant='npc',
                   note='NPC faction change (Effect SetFactionRank carries an actor); affects AI for the whole world')
    if fn == 'removealltypeditems' and not player:
        row.update(sev='low', variant='npc', tags=[], note='NPC inventory wipe: host-owned actor, replicated by NV:MP')
    if fn == 'setrestrained' and player:
        row.update(tags=[H_CINEMATIC], sev='high', variant='player')
    if fn == 'activate' and player:
        row.update(tags=[H_WORLD, H_TELEPORT], sev='high', variant='player')
    if fn in ('kill', 'killactor') and subj:
        row['tags'] = [H_KILL]
    row['status'] = combine(row['capture'], row['apply'])
    row['player'] = player
    return row
