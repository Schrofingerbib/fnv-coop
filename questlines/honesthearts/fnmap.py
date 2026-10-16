"""Function -> synccore Effect type + L5 capture/apply status, for every call that occurs in the Honest Hearts scripts.

Sources of the statuses (all read-only, nothing here was run in the game):
  * synccore/include/fnvcoop/effect.h          what Effect types exist
  * plugin/hooks/apply_remote.cpp              which Effects the client applier handles (SetStage, SetObjective, SetGlobal,
                                               Enable/DisableRef, AddItem, RemoveItem, ModActorValue, ModKarma, ModReputation,
                                               SetScriptVar, CompleteQuest, FailQuest, StartQuest, StopQuest, Kill/Resurrect/MoveRef
                                               via console lines). Everything else returns Unsupported.
  * plugin/hooks/engine_addrs.h + internal lane notes   which engine entry points are hooked. Round 4 hooks for faction, perk,
                                               script package, combat, lock and Activate are GUEST-SUPPRESSION ONLY ("host: pass-through"),
                                               so they are not captured on the host. Control/fast-travel commands are located but not hooked.
  * plugin/hooks/capture_hub.h                 a capture scope only exists inside TESQuest::SetStage and TESTopicInfo::RunResultScript;
                                               a mutation anywhere else ("ambient gameplay": quest GameMode blocks, trigger/activate/death
                                               object scripts) is dropped (CaptureHub::Stats::droppedNoScope).
Each entry:
  kind      read | mutate | presentation | npc-ai | engine
  effect    synccore Effect type that would carry it (None = no type exists)
  capture   supported | partial | missing | n/a     (host side: is it recorded into the EffectTxn)
  apply     supported | partial | missing | n/a     (client side: is it applied from the EffectTxn)
  hazard    co-op hazard tags (see HAZARDS)
  severity  blocker | high | medium | low | none
  note      one line
"""

HAZARDS = {
    'PLAYER-TARGET': 'effect targets the player (rewards, perks, factions, AV)',
    'SPAWN': 'creates a new reference (duplicates if run on more than one machine)',
    'COMPANION': 'companion / follower state',
    'ESSENTIAL': 'essential / ghost / restrained flags that decide whether a key NPC can die',
    'MOVE': 'MoveTo / teleport',
    'CELL': 'cell transition',
    'CINEMATIC': 'control lock / forced camera / scripted scene',
    'MENU': 'UI menu opened by a script',
    'TIMER': 'game-time or real-time wait, time skip',
    'ENDING': 'ending slideshow / quest-ending state',
    'KILL-ALL': 'kills or fails every other quest',
    'TRAVEL': 'fast travel enable/disable',
    'SAVE': 'autosave / save blocking',
    'P-REF': 'reads the player reference (distance, cell, inventory, action ref)',
    'RANDOM': 'random roll',
    'AMBIENT': 'usually runs outside a capture scope (GameMode / trigger / death / activate block)',
}

R, M, P, E, N = 'read', 'mutate', 'presentation', 'npc-ai', 'engine'
_T = {}


def _a(names, kind, effect, capture, apply, hazard=(), severity='none', note=''):
    for n in names.split():
        _T[n.lower()] = {'kind': kind, 'effect': effect, 'capture': capture, 'apply': apply, 'hazard': list(hazard),
                         'severity': severity, 'note': note}


# ---- supported quest-state primitives -------------------------------------------------------------------------------
_a('setstage', M, 'SetStage', 'supported', 'supported', (), 'none',
   'TESQuest::SetStage hook opens the capture scope; client applies with the script gate closed')
_a('setobjectivedisplayed setobjectivecompleted', M, 'SetObjective', 'supported', 'supported', (), 'none',
   'BGSQuestObjective::SetState hook; flags bit0 displayed, bit1 completed')
_a('startquest stopquest', M, 'StartQuest/StopQuest', 'supported', 'supported', (), 'none', 'TESQuest::SetRunning hook')
_a('completequest', M, 'CompleteQuest', 'supported', 'supported', (), 'none', 'TESQuest::SetCompleted hook')
_a('completeallobjectives', M, 'CompleteAllObjectives', 'partial', 'missing', (), 'medium',
   'effect type + JSON codec exist; no Cmd hook and the applier has no case. Inferred: the per-objective SetState hook records '
   'SetObjective for each objective (unverified)')
_a('setglobal', M, 'SetGlobal', 'supported', 'supported', ('TIMER',), 'medium',
   'globals are captured by diffing all TESGlobals around the outermost scope; a GameHour/GameDay write outside a scope is not seen')
_a('setscriptvar', M, 'SetScriptVar', 'supported', 'partial', (), 'low',
   'quest vars: diff of every quest ScriptEventList around the scope + applier; ref (object-script) vars: applier only, capture not done')
_a('forceactivequest', M, None, 'missing', 'missing', (), 'low',
   'active-quest marker is per player (Pip-Boy). No Effect type; leave local or add a per-player effect')
_a('setquestdelay', E, None, 'n/a', 'n/a', (), 'none', 'quest update timer; quest scripts run on the host only')
_a('incrementscriptedchallenge addachievement', P, None, 'n/a', 'n/a', (), 'low',
   'achievement / challenge counter; local to each player profile, not part of shared story state')

# ---- inventory / rewards --------------------------------------------------------------------------------------------
_a('additem', M, 'AddItem', 'supported', 'supported', ('PLAYER-TARGET',), 'medium',
   'Cmd_AddItem hook (player target). Container target on a non-player is not captured. Recipient routing is a policy rule (RewardAll/QuestItemDup)')
_a('removeitem', M, 'RemoveItem', 'supported', 'supported', ('PLAYER-TARGET',), 'medium',
   'Cmd_RemoveItem hook. Quest items must be removed from every player (QuestItemDup / RemoveItemPerOwner)')
_a('additemhealthpercent', M, 'AddItem', 'missing', 'missing', ('PLAYER-TARGET',), 'medium',
   'not hooked (only Cmd_AddItem); used to fill the MQ07 reward locker')
_a('removeallitems', M, 'RemoveItem', 'missing', 'missing', ('PLAYER-TARGET',), 'medium',
   'not hooked; moves a whole inventory between actors (companion gear hand-back)')
_a('equipitem unequipitem', M, None, 'missing', 'missing', ('PLAYER-TARGET',), 'low',
   'equip state is per player / per actor; no Effect type')
_a('rewardxp', M, 'ModActorValue(Experience)', 'supported', 'supported', ('PLAYER-TARGET',), 'medium',
   'Cmd_RewardXP hook; RewardAll gives every player the full XP')
_a('rewardkarma', M, 'ModKarma', 'supported', 'supported', ('PLAYER-TARGET',), 'medium',
   'Player::RewardKarma hook; karma is shared (owner decision)')
_a('addnote', M, None, 'missing', 'missing', ('PLAYER-TARGET',), 'low', 'Pip-Boy note; per-player, no Effect type (reward to all)')
_a('setav setactorvalue forceav forceactorvalue damageav modav modactorvalue restoreav', M, 'ModActorValue', 'supported',
   'supported', ('PLAYER-TARGET',), 'medium',
   'player-target AV deltas captured (before/after); NPC-target AV is NPC state (npc-sync)')
_a('addperk removeperk', M, 'SetPerk', 'missing', 'missing', ('PLAYER-TARGET', 'COMPANION'), 'high',
   'Cmd_AddPerk/RemovePerk hooks are guest-suppression only (host pass-through): not captured; SetPerk has no applier case. '
   'Companion perks must go to the recruiter only')
_a('addtofaction removefromfaction setfactionrank modfactionrank', M, 'SetFactionRank', 'missing', 'missing',
   ('PLAYER-TARGET',), 'high',
   'guest-suppression only hooks (host pass-through): not captured; SetFactionRank has no applier case. Player faction membership is shared')
_a('addreputation setreputation modreputation', M, 'ModReputation', 'supported', 'supported', ('PLAYER-TARGET',), 'low',
   'TESReputation hooks (shared)')

# ---- refs / world ---------------------------------------------------------------------------------------------------
_a('enable disable', M, 'EnableRef/DisableRef', 'supported', 'supported', (), 'medium',
   'TESObjectREFR::SetDisabled hook, records state changes only; ambient (trigger/GameMode) calls are dropped')
_a('killactor kill', M, 'KillActor', 'supported', 'supported', ('ESSENTIAL',), 'low', 'Cmd_KillActor hook; applied through a console line')
_a('resurrectactor resurrect', M, 'ResurrectActor', 'supported', 'supported', (), 'low', 'Cmd_ResurrectActor hook; applied through a console line')
_a('moveto movetomarkerwithfade setpos', M, 'MoveRef', 'supported', 'partial', ('MOVE',), 'high',
   'Cmd_MoveToMarker / WithFade / SetPos hooks. Apply: console MoveTo for marker moves; cross-cell SetPos without marker is Unsupported. '
   'A player MoveTo needs the L4 PartyWarp expansion (rule PartyWarp, needs Options.playerRef)')
_a('placeatme', M, None, 'missing', 'missing', ('SPAWN',), 'high',
   'no hook, no Effect type (spawned refs are not tracked; "no ghost duplicate NPC" invariant)')
_a('markfordelete removeme', M, None, 'missing', 'missing', ('SPAWN',), 'medium',
   'no hook; deleted refs stay on guests unless the ref is also disabled')
_a('lock unlock', M, 'SetLock', 'missing', 'missing', (), 'medium',
   'Cmd_Lock/UnLock hooks are guest-suppression only; SetLock has no applier case')
_a('setopenstate activate', M, None, 'missing', 'missing', (), 'low',
   'door / activator state; Cmd_Activate observer is guest-only; no Effect type')
_a('setdestroyed swaptexture', M, 'SwapTexture', 'missing', 'missing', (), 'low', 'visual / destruction state of a ref')
_a('entertrigger', M, None, 'missing', 'missing', ('P-REF', 'AMBIENT'), 'medium',
   'fires a trigger volume with the (host) player as activator; guests are ghosts in the host world (NV:MP), so the host sees them')
_a('addformtoformlist additemtoleveledlist', M, None, 'missing', 'missing', (), 'medium',
   'global form-list / leveled-list edits; quest scripts are gated off on guests, so guests never get the DLC weapons in the perk lists')
_a('setownership setcellownership', M, None, 'missing', 'missing', (), 'low', 'ownership flags of refs / cells')
_a('removespell dispel dispelallspells castimmediateonself', M, None, 'missing', 'missing', ('PLAYER-TARGET',), 'low',
   'magic effects on actors; per-actor state')

# ---- NPC AI (npc-sync L6, not effects) ------------------------------------------------------------------------------
_a('evaluatepackage resetai addscriptpackage removescriptpackage playidle playgroup setcombatstyle startcombat stopcombat '
   'pushactoraway setangle setvatstarget say sayto', N, None, 'n/a', 'n/a', (), 'low',
   'NPC-local AI / animation; host-owned NPCs replicate through NV:MP (L6). Not an Effect')
_a('setenemy setally setfactionreaction', N, None, 'missing', 'missing', (), 'medium',
   'faction relations are global engine state; no hook, no Effect type. Decides who is hostile after the finale')
_a('setessential setghost setrestrained setignorefriendlyhits ignorecrime', N, None, 'missing', 'missing', ('ESSENTIAL',), 'medium',
   'actor flags that gate whether Joshua / Daniel / the White Legs leader can die; host-owned NPCs, not replicated as state')
_a('setplayerteammate', M, 'CompanionOwner', 'missing', 'missing', ('COMPANION', 'PLAYER-TARGET'), 'blocker',
   'no Cmd hook and no applier case. The hire/fire triggers and the Zion door auto-fire call it; owner must be the recruiter')
_a('openteammatecontainer', P, None, 'missing', 'missing', ('COMPANION', 'MENU'), 'low', 'opens the companion inventory menu for the activator')
_a('setactorfullname', M, None, 'missing', 'missing', (), 'low', 'display name change')

# ---- controls / camera / saves / travel / time ----------------------------------------------------------------------
_a('disableplayercontrols enableplayercontrols', M, 'SetPlayerControls', 'missing', 'partial', ('CINEMATIC',), 'blocker',
   'Cmd_Disable/EnablePlayerControls located (0x5CAE30 / 0x5CAD20) but not hooked; apply has SetPlayerControlsLocked (all 7 flags). '
   'L4 SetPlayerControls rule + 120 s timeout exist')
_a('setinchargen', M, None, 'missing', 'missing', ('CINEMATIC', 'ENDING', 'PLAYER-TARGET'), 'high',
   'no hook / Effect; used by the intro and ending slideshows to stop levelling. Must be paired with SlideshowEnd')
_a('enablefasttravel', M, 'SetFastTravelLock', 'missing', 'missing', ('TRAVEL',), 'high',
   'Cmd_EnableFastTravel located (0x5D13E0) but not hooked; L4 has the SetFastTravelLock effect + rule')
_a('autosave', M, 'SaveSuppress', 'missing', 'missing', ('SAVE',), 'high',
   'no hook; L10 lists "SaveSuppress enforcement (L5)" as open. Guests must not autosave mid-scene')
_a('forceweather releaseweatheroverride', P, None, 'missing', 'missing', (), 'low', 'weather override; world-global, set by host should reach guests')
_a('playmusic playsound playmagicshadervisuals applyimagespacemodifier removeimagespacemodifier imod isimagespaceactive '
   'showmap pipboyradiooff forceradiostationupdate resetpipboymanager resethealth', P, None, 'n/a', 'n/a', (), 'low',
   'presentation / per-player UI state; runs locally. ShowMap and Pip-Boy radio are per player (decide: host effect or initiator only)')
_a('showmessage', P, None, 'missing', 'missing', ('MENU',), 'medium',
   'HUD/message box; goes to the machine running the script. Needs a Recipient (initiator vs all) via L8 / console tee')
_a('showbartermenu showrepairmenu showcaravanmenu', P, None, 'missing', 'missing', ('MENU',), 'high',
   'service menu: dialogue-design 7.2 "design only in v1, not in the state machine"')
_a('startconversation', P, None, 'missing', 'missing', ('MENU',), 'medium',
   'script-forced conversation (Joshua / Daniel pickpocket intercept); L8 ForcedDialogue effect exists, capture not done')
_a('purgecellbuffers', E, None, 'n/a', 'n/a', (), 'none', 'PCB: engine cell buffer flush before a teleport; local')
_a('getbuttonpressed', R, None, 'n/a', 'n/a', ('MENU',), 'low', 'reads the result of a ShowMessage button box on the local machine')

# ---- reads (conditions and inline queries) --------------------------------------------------------------------------
_a('getitemcount getdistance isactionref hasperk iskiller getincell getinworldspace isininterior', R, None, 'n/a', 'partial', ('P-REF',), 'high',
   'evaluated on the host against the host player. Party-aware reduction exists in L4 (PartyAwareCond) but L5 has no player-fact hook '
   '(round 2 request 5: not done). IsActionRef Player is false for a guest ghost activating a host-side object')
_a('getlevel issneaking isincombat getactionref getplayercontrolsdisabled getisreference isplayerinregion', R, None, 'n/a', 'partial',
   ('P-REF',), 'medium',
   'player fact read on the host (host player only); L4 has the reductions, L5 has no player-fact hook')
_a('getstage getstagedone getobjectivecompleted getobjectivedisplayed getquestrunning getdead getdeadcount getdisabled getisid '
   'getself getinfaction getlocked getislockbroken getsleeping getisghost getunconscious getfactionrelation isessential '
   'gethealthpercentage getiscurrentpackage hasmagiceffect isspelltarget getweaponanimtype getangle getpos getsecondspassed '
   'getcurrenttime getlinkedref isrunningquest getqr getquestvariable getscriptvariable getglobalvalue getcurrentdayofweek',
   R, None, 'n/a', 'n/a', (), 'none', 'reads shared story / NPC state that is replicated; no sync needed')
_a('getrandompercent', R, None, 'n/a', 'n/a', ('RANDOM',), 'low', 'host rolls; outcome reaches guests as the NPC/ref state it causes')
_a('israining isps3 isplayermovingintonewspace', R, None, 'n/a', 'n/a', (), 'none', 'local engine query')


def lookup(fn):
    """Entry for a function name (case-insensitive) or None when unmapped."""
    return _T.get(fn.lower())


def all_names():
    return sorted(_T)


DISPLAY = {'moveto': 'MoveTo', 'setav': 'SetAV', 'damageav': 'DamageAV', 'forceav': 'ForceAV', 'modav': 'ModAV', 'restoreav': 'RestoreAV',
           'setinchargen': 'SetInCharGen', 'isininterior': 'IsInInterior'}
