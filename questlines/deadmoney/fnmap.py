"""Curated map: script function -> synccore Effect type + L5 capture/apply status + co-op hazard tags.

Evidence for the L5 columns is a STATIC reading of the sources as pinned in l5_snapshot.json (refresh_l5.py; nothing was exercised in
the game for this lane):
  capture   = a host hook records the call as an Effect.   engine_hooks.cpp HK(...) rows whose handler is not BLOCK_CMD.
              "block-only" hooks (BLOCK_CMD) only drop the call on a guest; nothing is recorded.
  apply     = RemoteApplier can execute the Effect on a replica (apply_remote.cpp get_if branches, mostly by running the engine's own
              console command on the target reference).
  status    = the weaker of capture and apply, over the columns that apply to the row.
Status vocabulary: supported | partial | missing | n/a   (n/a: query, host-only bookkeeping or pure local feedback)
"supported" means "a capture hook and an apply branch exist in the source"; recipient routing (who receives a player-targeted effect) is a
host-policy question that the rules in policy_review.md cover, and in-game behaviour is unverified.
Severity (what the gap costs a 2-4 player session): blocker | high | medium | low | info
  blocker  the questline cannot be completed together, or a player is killed/soft-locked unfairly
  high     progression-relevant shared state diverges, or per-player state is lost
  medium   visible divergence that is recoverable
  low      audio / visual / UX only
  info     supported, or no cross-machine effect

Row = (effect, kind, capture, apply, severity, tags, note).  `effect` is a synccore effect.h type, "-" if the vocabulary has
none, or "A | B" when the call maps to different effects by argument.
"""

SUP, PAR, MIS, NA = 'supported', 'partial', 'missing', 'n/a'
RANK = {SUP: 0, NA: 0, PAR: 1, MIS: 2}
SEV_ORDER = ['info', 'low', 'medium', 'high', 'blocker']


def worst(*xs):
    xs = [x for x in xs if x != NA]
    return max(xs, key=lambda x: RANK[x]) if xs else NA


def R(effect, kind, cap, app, sev, tags='', note=''):
    return {'effect': effect, 'kind': kind, 'capture': cap, 'apply': app, 'status': worst(cap, app), 'severity': sev,
            'tags': [t for t in tags.split() if t], 'note': note}


FN = {}


def add(names, *a, **k):
    for n in names.split():
        FN[n.lower()] = R(*a, **k)


# ---- quest / world state the host already replicates --------------------------------------------------------------
add('SetStage', 'SetStage', 'mutation', SUP, SUP, 'info', 'quest', 'captured at TESQuest::SetStage; nested result script folded into one txn')
add('SetObjectiveDisplayed SetObjectiveCompleted', 'SetObjective', 'mutation', SUP, SUP, 'info', 'quest',
    'BGSQuestObjective::SetState hook; flags bit0 displayed, bit1 completed')
add('CompleteAllObjectives', 'CompleteAllObjectives', 'mutation', SUP, SUP, 'info', 'quest', 'QuestCompleteAllObjectives hook + applier branch')
add('StartQuest', 'StartQuest', 'mutation', SUP, SUP, 'info', 'quest', 'TESQuest::SetRunning')
add('StopQuest', 'StopQuest', 'mutation', SUP, SUP, 'info', 'quest', 'TESQuest::SetRunning(false)')
add('Enable Disable', 'EnableRef | DisableRef', 'mutation', SUP, SUP, 'info', 'world',
    'TESObjectREFR::SetDisabled; recorded only on a state CHANGE (L5 round 8): re-enabling an already enabled ref emits nothing')
add('AddItem', 'AddItem | RefAddItem', 'mutation', SUP, SUP, 'info', 'items',
    'player target -> AddItem; any other container -> RefAddItem{ref,item,count}; both have applier branches')
add('RemoveItem', 'RemoveItem | RefRemoveItem', 'mutation', SUP, SUP, 'info', 'items', 'same split as AddItem')
add('RewardXP', 'ModActorValue', 'mutation', SUP, SUP, 'info', 'player-effect', 'pseudo AV kAvExperience; policy routes to all players')
add('RewardKarma', 'ModKarma', 'mutation', SUP, SUP, 'info', 'karma-rep', 'Player::RewardKarma; shared per owner decision')
add('AddReputation RemoveReputation', 'ModReputation', 'mutation', SUP, SUP, 'info', 'karma-rep', 'TESReputation field diff; shared')
add('KillActor Kill', 'KillActor', 'mutation', SUP, SUP, 'high', 'kill',
    'captured and applied through the console executor; the player-targeted form (collar) additionally needs per-player routing')
add('ResurrectActor', 'ResurrectActor', 'mutation', SUP, SUP, 'info', 'kill', 'console executor')
add('SetActorValue ModActorValue ForceActorValue DamageActorValue', 'ModActorValue', 'mutation', SUP, SUP, 'medium', 'player-effect',
    'captured as a delta by before/after; the recipient policy decides who is hit (toxic-cloud damage must stay per player)')
add('RestoreActorValue', 'ModActorValue', 'mutation', MIS, SUP, 'medium', 'player-effect',
    'no hook on Cmd_RestoreActorValue (only Mod/Set/Force/Damage are hooked)')
add('ResetHealth', '-', 'mutation', MIS, NA, 'medium', 'player-effect companion', 'not hooked; heals the actor to full')
add('MoveToMarker MoveToMarkerWithFade', 'MoveRef', 'mutation', PAR, PAR, 'high', 'move cinematic',
    'captured with the marker REFR; MoveRef.partyWarp is a policy flag set by the rules engine, not by the hook; cross-cell absolute moves unsupported')
add('SetPos', 'MoveRef', 'mutation', PAR, PAR, 'medium', 'move', 'same-cell absolute moves apply through SetPos; cross-cell without marker is Unsupported')
add('SetAngle', '-', 'mutation', MIS, NA, 'low', 'move', 'no hook; rotation of a moved NPC')
add('Lock UnLock SetOpenState SetDestroyed', 'ObjectState', 'mutation', SUP, SUP, 'info', 'world lock',
    'Cmd_Lock/UnLock/SetOpenState/SetDestroyed are captured as ObjectState{ref,mask,...} and applied by the same console command on the ref; '
    'a destruction-stage script that fires on the machine that shot the object still runs there')
add('Activate', '-', 'mutation', MIS, NA, 'medium', 'world',
    'script Activate is a block-only hook; the activated object runs its OnActivate on the host, whose own effects are captured (indirect)')
add('PlaceAtMe', 'SpawnRef', 'mutation', SUP, MIS, 'high', 'spawn',
    'Cmd_PlaceAtMe is captured as SpawnRef{ref,base,cell,x,y,z}; RemoteApplier has no SpawnRef branch, so replicas never create the ref '
    '(collar explosions, ghost spawns, kitchen blasts)')
add('MarkForDelete RemoveMe', '-', 'mutation', MIS, NA, 'low', 'world', 'no hook (DespawnRef exists in effect.h, nothing records it)')
add('SwapTexture', 'SwapTexture', 'mutation', MIS, MIS, 'medium', 'ending', 'effect type exists; no hook, applier has no branch (intro / ending slides)')
add('AddFormToFormList', '-', 'mutation', MIS, NA, 'low', 'world',
    'form-list edits run in a quest script that never runs on guests, so guests lack the animation-condition entries (cosmetic)')
add('SetWeather', 'SetWeather', 'mutation', SUP, SUP, 'info', 'world', 'captured and applied (SetWeather/ForceWeather)')

# ---- player / party scope -------------------------------------------------------------------------------------------
add('AddPerk RemovePerk', 'SetPerk', 'mutation', SUP, SUP, 'medium', 'perk player-effect',
    'captured for player subjects as SetPerk{perk, rank, Recipient::dflt}; applied by running AddPerk/RemovePerk on the local player. '
    'Who receives it (recruiter, initiator) is a host-policy question')
add('AddToFaction RemoveFromFaction SetFactionRank', 'SetFactionRank', 'mutation', SUP, SUP, 'info', 'faction',
    'captured with {actor,faction,rank<0=removed}; applied by console on the actor')
add('RemoveFromAllFactions ModFactionRank', 'SetFactionRank', 'mutation', MIS, SUP, 'medium', 'faction',
    'ModFactionRank is block-only (needs the resulting rank); RemoveFromAllFactions has no hook')
add('SetPlayerTeammate', 'CompanionOwner', 'mutation', MIS, MIS, 'blocker', 'companion',
    'no hook (Cmd_SetPlayerTeammate is not in the HK table); CompanionOwner exists in effect.h and has no capture or apply path')
add('EquipItem UnEquipItem', '-', 'mutation', MIS, NA, 'high', 'items player-effect',
    'no hook; equipment is local to the machine that runs the script')
add('RemoveAllItems', 'RemoveAllItems', 'mutation', SUP, PAR, 'high', 'items',
    'captured for NON-player containers only (the hook skips the player on purpose); the effect has no destination, so the applier runs a bare '
    '`RemoveAllItems` and the items are deleted on replicas instead of moved (e.g. back to the player)')
add('RemoveAllTypedItems', '-', 'mutation', MIS, NA, 'blocker', 'items player-effect',
    'a different command from RemoveAllItems and not hooked; companion gear transfers to the player are invisible to the replica')
add('AddItemHealthPercent', 'AddItem', 'mutation', MIS, SUP, 'medium', 'items', 'different command from Cmd_AddItem; not hooked')
add('AddNote RemoveNote', 'AddNote | RemoveNote', 'mutation', SUP, SUP, 'info', 'items player-effect',
    'captured and applied (no Recipient field: every machine receives the note)')
add('AddCardToPlayer', '-', 'mutation', MIS, NA, 'low', 'items player-effect', 'Caravan card; no hook')
add('AddSpell RemoveSpell DispelAllSpells CastImmediateOnSelf', '-', 'mutation', MIS, NA, 'medium', 'player-effect',
    'magic effects on the player (rest buff, chem clearing) are local; no hook')
add('IncrementScriptedChallenge AddAchievement', '-', 'mutation', MIS, NA, 'low', 'player-effect',
    'challenge progress / achievements are per profile; host-only otherwise')
add('ForceActiveQuest', 'ForceActiveQuest', 'mutation', MIS, MIS, 'low', 'quest player-effect',
    'effect type exists in effect.h; no hook, no applier branch (the tracked quest is per player UI state)')
add('ShowMap', 'SetMapMarker', 'mutation', MIS, MIS, 'medium', 'world player-effect', 'effect type exists; no hook, no applier branch')
add('SetInChargen', '-', 'mutation', MIS, NA, 'medium', 'cinematic player-effect',
    'locks levelling/pip-boy during slideshows; local engine flag, no hook')
add('DisablePlayerControls EnablePlayerControls', 'SetPlayerControls', 'mutation', SUP, SUP, 'high', 'cinematic player-effect',
    'both commands end in PlayerCharacter::SetControlsDisabled, which is hooked and recorded with Recipient::dflt; applier branch exists. '
    'Scope (lock every player, release together) is the SetPlayerControls rule, not the hook')
add('Autosave', 'RequestSave', 'mutation', SUP, PAR, 'medium', 'save',
    'Autosave/SystemSave/ForceSave are captured as RequestSave{kind}; RemoteApplier has no branch: whether a guest saves is a session policy (rule SaveSuppress)')
add('EnableFastTravel', 'SetFastTravelLock', 'mutation', MIS, MIS, 'medium', 'travel', 'type exists; no hook')

# ---- NPC / AI (left to NV:MP actor sync or the host's own AI) --------------------------------------------------------
add('AddScriptPackage RemoveScriptPackage EvaluatePackage ResetAI', '-', 'mutation', MIS, NA, 'medium', 'ai',
    'AI package edits are host-only (block-only hooks); NV:MP actor sync must carry the resulting motion; unverified')
add('StartCombat StopCombat StopCombatAlarmOnActor SetEnemy SetAlly SetCombatStyle SCAOnActor', '-', 'mutation', MIS, NA, 'medium', 'ai',
    'combat state is host-side; left to NV:MP')
add('SetGhost SetRestrained SetUnconscious PushActorAway SetVATSTarget FireWeapon DamageObject', '-', 'mutation', MIS, NA, 'medium', 'ai',
    'actor flag / physics call without an effect type; host-side only')
add('SetActorRefEssential SetEssential', '-', 'mutation', MIS, NA, 'medium', 'essential', 'essential flag is host state; a client-side kill must not bypass it')
add('IgnoreCrime SetIgnoreFriendlyHits AlwaysShowActorSubtitles SetActorFullName SetTalkingActivatorActor SetDisposition', '-', 'mutation', MIS, NA,
    'low', 'companion', 'NPC configuration call; host-side, cosmetic on clients (name change Dog -> God is shown by the host only)')
add('SetActorAlpha Look StopLook PlayIdle PlayGroup', '-', 'mutation', MIS, NA, 'low', 'visual', 'animation / presentation of an NPC; NV:MP actor sync')
add('OpenTeammateContainer', '-', 'mutation', MIS, NA, 'medium', 'menu companion', 'companion inventory menu is local to the activating player')
add('Say SayTo StartConversation', 'ForcedDialogue', 'mutation', PAR, PAR, 'medium', 'dialogue',
    'ForcedDialogue event + L8 session exist; script-initiated Say/SayTo capture is not hooked (Cmd_Say/Cmd_SayTo located only)')

# ---- radio (Dead Money collars are driven by radio / speaker emitters) ----------------------------------------------
add('SetBroadcastState SetNPCRadio StartRadioConversation ForceRadioStationUpdate ResetPipboyManager', '-', 'mutation',
    MIS, NA, 'high', 'radio', 'radio stations are host objects; no hook or effect type; collars depend on emitter state')
add('PipBoyRadioOff PipboyRadio', '-', 'mutation', MIS, NA, 'medium', 'radio', "turns the player's own Pip-Boy radio off for the slideshow; local UI state, no hook")

# ---- UI / menus / presentation ---------------------------------------------------------------------------------------
add('ShowMessage', '-', 'mutation', MIS, NA, 'medium', 'menu',
    'a message box runs on the machine executing the script (host); the other players never see warnings, tutorials or the collar beep text')
add('ShowWarning', '-', 'mutation', NA, NA, 'info', 'menu', 'developer warning, no player-facing effect')
add('ShowBarterMenu ShowRecipeMenu ShowSlotMachineMenuParams ShowBlackJackMenuParams ShowRouletteMenuParams', '-', 'mutation', MIS, NA, 'medium',
    'menu', 'service sub-session (docs/dialogue-design.md 7.2) is design only')
add('SetQuestDelay', '-', 'flow', NA, NA, 'info', 'timer', 'host-only quest bookkeeping')
add('ForceTerminalBack', '-', 'flow', NA, NA, 'info', 'menu', 'terminal navigation, local')
add('PlaySound PlaySound3D PlayMusic', '-', 'mutation', MIS, NA, 'low', 'audio', 'audio on the host only')
add('PlayMagicShaderVisuals StopMagicShaderVisuals', '-', 'mutation', MIS, NA, 'low', 'visual', 'hologram shimmer shaders; NV:MP does not carry them')
add('ApplyImageSpaceModifier RemoveImageSpaceModifier', '-', 'mutation', MIS, NA, 'medium', 'cinematic visual',
    'screen fade-to-black used by transitions is a per-machine camera effect')
add('SetRumble PurgeCellBuffers EnterTrigger', '-', 'flow', NA, NA, 'info', 'audio', 'local feedback / engine housekeeping')

# ---- queries (conditions and reads): matter only when the subject is the player (host-side party-aware evaluation) ----
QUERY_SHARED = ('GetStage GetStageDone GetObjectiveCompleted GetObjectiveDisplayed GetQuestCompleted GetQuestRunning GetGlobalValue')
for n in QUERY_SHARED.split():
    FN[n.lower()] = R('-', 'condition', NA, NA, 'info', 'quest', 'shared story state, replicated by the sync core; identical on every machine')
QUERY_NPC = ('GetDisabled GetDead GetIsID GetSelf GetLinkedRef GetIsReference IsInList GetInFaction GetLocked GetOpenState GetBroadcastState '
             'GetSecondsPassed GetRandomPercent GetPos GetAngle GetHeadingAngle IsAnimPlaying IsInCombat IsCombatTarget GetContainer GetWeaponAnimType '
             'IsWeaponOut IsWeaponInList HasMagicEffect ScriptEffectElapsedSeconds GetCauseofDeath GetIsGhost GetKnockedState GetSitting GetDetected '
             'GetCombatTarget GetIsCurrentPackage IsImageSpaceActive IsTalking GetIsCreature IsInCriticalStage IsActorRefEssential IsLimbGone MenuMode '
             'GetButtonPressed GetActionRef GetTalkedToPC GetCasinoWinningsLevel IsInInterior IsSneaking')
for n in QUERY_NPC.split():
    FN[n.lower()] = R('-', 'condition', NA, NA, 'info', 'ai', 'NPC/world read evaluated on the host; a hazard only when the subject is the player (see player-bound list)')
QUERY_PLAYER = ('IsActionRef GetPlayerTeammate GetHasNote HasPerk GetItemCount GetLevel GetInCell GetInWorldspace GetInSameCell GetDistance '
                'GetLineOfSight GetActorValue GetBaseActorValue IsHardcore')
for n in QUERY_PLAYER.split():
    FN[n.lower()] = R('-', 'condition', NA, NA, 'info', 'player-effect', 'player-bound read: needs host-side party-aware evaluation (rule PartyAwareCond) when the subject is the player')
FN['ismenumode'] = FN['menumode']


def row(canon):
    return FN.get(canon.lower())


# On a line that touches the player, an unsupported mutation is one level worse than its table severity.
def site_severity(base, status, player_bound, kind):
    if status in (SUP, NA):
        return 'info' if kind != 'condition' else ('medium' if player_bound else 'info')
    i = SEV_ORDER.index(base)
    if player_bound and kind == 'mutation':
        i = min(i + 1, len(SEV_ORDER) - 1)
    return SEV_ORDER[i]


# ---- argument-dependent rows: the same command is a different gap depending on who it targets ---------------------------
def site_row(canon, player_bound):
    """Row to use at a call site. `player_bound`: the line names the player (subject or argument)."""
    c = canon.lower()
    base = FN.get(c)
    if base is None:
        return None
    if c in ('movetomarker', 'movetomarkerwithfade'):
        if player_bound:
            return R('MoveRef', 'mutation', PAR, PAR, 'high', 'move cinematic',
                     'player move: captured; MoveRef.partyWarp is set by the rules engine (PartyWarp), not by the hook; cross-cell/absolute moves unsupported')
        return R('MoveRef', 'mutation', SUP, SUP, 'info', 'move', 'NPC / object move: captured with the marker and applied through the console executor')
    if c in ('killactor', 'kill'):
        if player_bound:
            return R('KillActor', 'mutation', PAR, SUP, 'high', 'kill player-effect collar',
                     'captured as KillActor of the host player ref; which slot dies is a routing decision the contract has no field for (Recipient)')
        return R('KillActor', 'mutation', SUP, SUP, 'info', 'kill', 'NPC kill: captured and applied through the console executor')
    if c == 'removeallitems' and player_bound:
        return R('RemoveAllItems', 'mutation', MIS, NA, 'blocker', 'items player-effect',
                 'player.RemoveAllItems is deliberately NOT recorded (the hook tracks non-player containers only): the inventory strip never reaches the replica')
    if c in ('equipitem', 'unequipitem') and not player_bound:
        return R('-', 'mutation', MIS, NA, 'low', 'items companion', 'NPC outfit change (companion gear swap); host-side, NV:MP carries the visible result')
    if c in ('setpos', 'setangle') and not player_bound:
        return R(base['effect'], 'mutation', base['capture'], base['apply'], 'low', 'move', base['note'])
    if c in ('addperk', 'removeperk') and not player_bound:
        return R('-', 'mutation', MIS, NA, 'low', 'perk', 'perk change on a non-player: the hook records player subjects only')
    return base


# For these commands the word "player" in the arguments names the actor who ACTS (killer, speaker target, activator, listener),
# not the actor who is changed: only `player.<cmd>` counts as player-targeted.
SUBJECT_ONLY = frozenset('kill killactor say sayto startconversation activate look stoplook setenemy setally startcombat stopcombat '
                         'movetomarker movetomarkerwithfade setpos setangle placeatme addscriptpackage removescriptpackage '
                         'resetai evaluatepackage playgroup playidle pushactoraway setvatstarget firewep'.split())
# commands that change the player without naming it (implicit subject), per tools/questdata/lexicon.PLAYER_IMPLICIT
IMPLICIT_PLAYER = frozenset('rewardxp rewardkarma addreputation removereputation addnote removenote addperk removeperk '
                            'addcardtoplayer showmap setinchargen disableplayercontrols enableplayercontrols '
                            'incrementscriptedchallenge addachievement autosave'.split())
