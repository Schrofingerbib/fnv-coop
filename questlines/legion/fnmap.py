"""Script command -> synccore Effect type + L5 capture/apply status (hand-curated, evidence-based).

Status vocabulary: supported | partial | missing | n/a
  capture = does the HOST record this mutation into an EffectTxn today (plugin/hooks/engine_hooks.cpp h_* detours,
            CaptureHub diffs of quest vars + globals)
  apply   = does a replica realise the effect (plugin/hooks/apply_remote.cpp RemoteApplier, plugin/coop/coop_module.cpp)
  n/a     = nothing to replicate (read-only condition, cosmetic, or host-authoritative NPC state that NV:MP carries)
Evidence strings cite the code that was read; "unknown" means nobody verified it in game.
Severity: blocker (story diverges / cannot progress for a non-host player), high, medium, low, info.
"""

S, P, M, N = 'supported', 'partial', 'missing', 'n/a'


def E(effect, cap, app, tag='', sev='info', note='', basis='', npc=None, plr=None):
    return {'effect': effect, 'capture': cap, 'apply': app, 'tag': tag, 'sev': sev, 'note': note, 'basis': basis,
            'npc': npc, 'plr': plr}


H = 'engine_hooks.cpp'
A = 'apply_remote.cpp'

FN = {
    # ---- quest state
    'setstage': E('SetStage', S, S, 'quest-state', 'info', 'h_SetStage scope on entry; replicas replay with ScriptGate', f'{H}:h_SetStage {A}:SetStage'),
    'setobjectivedisplayed': E('SetObjective', S, S, 'quest-state', 'info', 'flags bit0', f'{H}:h_ObjState'),
    'setobjectivecompleted': E('SetObjective', S, S, 'quest-state', 'info',
                               'merge is OR so "completed 0" (un-complete) cannot be expressed as a delta; baseline/applyForce can',
                               f'{H}:h_ObjState'),
    'completeallobjectives': E('CompleteAllObjectives', P, S, 'quest-state', 'low',
                               'no dedicated hook; each objective flips through h_ObjState so the per-objective effects are captured', f'{H}:h_ObjState'),
    'startquest': E('StartQuest', S, S, 'quest-state', 'info', '', f'{H}:h_SetRunning'),
    'stopquest': E('StopQuest', S, S, 'quest-state', 'info', '', f'{H}:h_SetRunning'),
    'completequest': E('CompleteQuest', S, S, 'quest-state', 'info', '', f'{H}:h_SetCompleted {A}:CompleteQuest'),
    'failquest': E('FailQuest', S, S, 'quest-state', 'info', '', f'{H}:h_SetFailed'),
    'forceactivequest': E(None, M, M, 'quest-state', 'low', 'active-quest pointer (PipBoy tracker) is per-player UI state; no effect type', ''),
    'resetquest': E(None, M, M, 'quest-state', 'medium', 'clears stages/objectives; no effect type, would desync the journal', ''),
    'setquestdelay': E(None, N, N, 'timer', 'low', 'quest script cadence; runs on the host only', ''),
    'set quest var': E('SetScriptVar', S, S, 'quest-state', 'info', 'quest vars are diffed around the outermost capture scope + polled by L10', f'{H}:g_varSnap'),
    'set ref var': E('SetScriptVar', M, S, 'quest-state', 'medium',
                     'ref/object script vars are NOT captured (L5 "limits"); apply exists. Guests cannot run object scripts that mutate, so their copy goes stale',
                     'internal lane notes round 2 item 11'),
    'set global': E('SetGlobal', S, S, 'quest-state', 'info', 'TESGlobal diff around the scope', f'{H}:line 173'),
    'set local': E(None, N, N, 'local', 'info', 'script-local variable', ''),
    'addtopic': E(None, M, M, 'dialogue', 'medium', 'topic knowledge is player state; no effect type', ''),
    # ---- world refs
    'enable': E('EnableRef', S, S, 'world', 'info', 'captured on state change only (SetDisabled hook)', f'{H}:h_SetDisabled'),
    'disable': E('DisableRef', S, S, 'world', 'info', 'captured on state change only', f'{H}:h_SetDisabled'),
    'setdestroyed': E(None, M, M, 'world', 'medium', 'destroyed flag on doors/ladders/props (e.g. HooverDamFallLadderREF); no effect type -> replicas keep the intact object', ''),
    'cleardestruction': E(None, M, M, 'world', 'low', '', ''),
    'damageobject': E(None, M, M, 'world', 'low', '', ''),
    'markfordelete': E(None, M, M, 'world', 'low', 'host deletes the ref; replica keeps a ghost', ''),
    'setownership': E(None, M, M, 'world', 'low', '', ''),
    'lock': E('SetLock', M, M, 'world', 'medium', 'Lock/Unlock are block-only hooks on guests; no capture, no applier branch for SetLock', f'{H}:BLOCK_CMD(Lock)'),
    'unlock': E('SetLock', M, M, 'world', 'medium', 'door gating (e.g. VHDOliverDoorREF.Unlock) not replicated', f'{H}:BLOCK_CMD(UnLock)'),
    'setopenstate': E(None, M, M, 'world', 'low', '', ''),
    'activate': E(None, M, M, 'trigger', 'medium', 'script Activate is only observed on guests (setActivateObserver); no effect; forcing a door/elevator on the host does not move the other players',
                  'internal lane notes round 7'),
    'placeatme': E(None, M, M, 'spawn', 'high', 'spawns have no Effect: each machine would spawn its own copy or the clients see nothing (invariant "no ghost duplicate NPC")', ''),
    'placeleveledactoratme': E(None, M, M, 'spawn', 'high', 'as placeatme', ''),
    'playgroup': E(None, N, N, 'cosmetic', 'info', 'animation on a host-owned actor', ''),
    'playidle': E(None, N, N, 'cosmetic', 'info', '', ''),
    'stoplook': E(None, N, N, 'cosmetic', 'info', 'AI look-at on host-owned actor', ''),
    'look': E(None, N, N, 'cosmetic', 'info', '', ''),
    'pushactoraway': E(None, N, N, 'cosmetic', 'info', '', ''),
    # ---- actors (host-owned; NV:MP carries pose/health/AI result)
    'kill': E('KillActor', S, S, 'kill', 'info', 'KillActor via console line on the replica', f'{H}:h_Kill {A}:KillActor'),
    'killactor': E('KillActor', S, S, 'kill', 'info', '', f'{H}:h_Kill'),
    'resurrect': E('ResurrectActor', S, S, 'kill', 'info', '', f'{H}:h_Resurrect'),
    'resetai': E(None, N, N, 'npc-ai', 'info', 'host-owned NPC AI', ''),
    'evaluatepackage': E(None, N, N, 'npc-ai', 'info', 'host-owned NPC AI; the package outcome reaches clients through NV:MP actor sync', ''),
    'addscriptpackage': E(None, N, N, 'npc-ai', 'low', 'host-owned NPC AI. NOT n/a when the target is the player (script packages on the player = cutscene walk)', f'{H}:BLOCK_CMD(AddScriptPackage)',
                          plr={'capture': M, 'apply': M, 'sev': 'high', 'tag': 'cinematic'}),
    'removescriptpackage': E(None, N, N, 'npc-ai', 'low', 'as addscriptpackage', f'{H}:BLOCK_CMD(RemoveScriptPackage)'),
    'startcombat': E(None, N, N, 'combat', 'low', 'host-owned NPC combat; guests drop it (block hook)', f'{H}:BLOCK_CMD(StartCombat)'),
    'stopcombat': E(None, N, N, 'combat', 'low', '', f'{H}:BLOCK_CMD(StopCombat)'),
    'stopcombatalarmonactor': E(None, N, N, 'combat', 'low', '', ''),
    'sendassaultalarm': E(None, N, N, 'combat', 'low', 'crime alarm on host-owned NPC', ''),
    'forceflee': E(None, N, N, 'combat', 'low', '', ''),
    'setcombatstyle': E(None, N, N, 'combat', 'low', '', ''),
    'setghost': E(None, M, M, 'essential', 'medium', 'invulnerability flag on a host-owned NPC; no effect type, replica NPC may still die/aggro on the client', ''),
    'setessential': E(None, M, M, 'essential', 'medium', 'essential flag; no effect type (replica death handling decides)', ''),
    'setrestrained': E(None, N, N, 'npc-ai', 'low', 'host-owned NPC', ''),
    'ignorecrime': E(None, N, N, 'npc-ai', 'low', '', ''),
    'setignorefriendlyhits': E(None, N, N, 'combat', 'low', '', ''),
    'resetinventory': E(None, M, M, 'npc-inventory', 'low', 'NPC inventory reset; no effect', ''),
    'resethealth': E(None, N, N, 'npc-ai', 'info', '', ''),
    'equipitem': E(None, M, M, 'npc-inventory', 'low', 'equip state is carried by NV:MP actor sync for NPCs; for the player it is per-player', ''),
    'restoreactorvalue': E('ModActorValue', M, M, 'player-target', 'low', 'no detour for RestoreAV (only Mod/Set/Force/DamageAV); NPC health is host-owned',
                           f'{H}:h_ModAV..h_DamageAV', npc={'capture': N, 'apply': N, 'sev': 'info', 'tag': 'npc-ai'}),
    'setactorvalue': E('ModActorValue', S, S, 'player-target', 'info', 'player-target AV deltas by before/after; NPC AV is host-owned', f'{H}:h_SetAV',
                       npc={'capture': N, 'apply': N, 'sev': 'info', 'tag': 'npc-ai'}),
    'forceactorvalue': E('ModActorValue', S, S, 'player-target', 'info', '', f'{H}:h_ForceAV', npc={'capture': N, 'apply': N, 'sev': 'info', 'tag': 'npc-ai'}),
    'modactorvalue': E('ModActorValue', S, S, 'player-target', 'info', '', f'{H}:h_ModAV', npc={'capture': N, 'apply': N, 'sev': 'info', 'tag': 'npc-ai'}),
    'damageactorvalue': E('ModActorValue', S, S, 'player-target', 'info', '', f'{H}:h_DamageAV', npc={'capture': N, 'apply': N, 'sev': 'info', 'tag': 'npc-ai'}),
    # ---- factions, perks, companions, hostility
    'addtofaction': E('SetFactionRank', M, M, 'faction', 'high', 'block-only on guests; host does not record it; no applier branch for SetFactionRank. Owner decision: player faction membership is SHARED',
                      f'{H}:BLOCK_CMD(AddToFaction)', npc={'capture': N, 'apply': N, 'sev': 'info', 'tag': 'npc-ai'}),
    'removefromfaction': E('SetFactionRank', M, M, 'faction', 'high', 'as addtofaction', f'{H}:BLOCK_CMD(RemoveFromFaction)',
                           npc={'capture': N, 'apply': N, 'sev': 'info', 'tag': 'npc-ai'}),
    'removefromallfactions': E('SetFactionRank', M, M, 'faction', 'medium', '', ''),
    'setfactionrank': E('SetFactionRank', M, M, 'faction', 'high', '', f'{H}:BLOCK_CMD(SetFactionRank)'),
    'addperk': E('SetPerk', M, M, 'perk', 'high', 'block-only; no capture, no applier branch', f'{H}:BLOCK_CMD(AddPerk)'),
    'removeperk': E('SetPerk', M, M, 'perk', 'high', 'companion perks (CompanionSuite on the NPC, BetterHealing on the player) are removed on enslave/dismiss paths', f'{H}:BLOCK_CMD(RemovePerk)',
                    npc={'capture': N, 'apply': N, 'sev': 'info', 'tag': 'npc-ai'}),
    'setplayerteammate': E('CompanionOwner', M, M, 'companion', 'high', 'companion recruit/dismiss: no capture hook, applier has no CompanionOwner branch (rules engine only tracks ownership)', ''),
    'openteammatecontainer': E(None, M, M, 'menu', 'low', 'per-player UI', ''),
    'setenemy': E(None, M, M, 'faction-hostility', 'high', 'faction-vs-faction hostility (e.g. PlayerFaction vs NCRFactionNV) is persistent shared story state; no effect type (proposal: SetFactionRelation)', ''),
    'setally': E(None, M, M, 'faction-hostility', 'high', 'as setenemy', ''),
    'setpcenemyoffaction': E(None, M, M, 'faction-hostility', 'high', 'player-vs-faction enemy flag (shared reputation side)', ''),
    'clearfactionplayerenemyflag': E(None, M, M, 'faction-hostility', 'high', '', ''),
    'setreputation': E('ModReputation', S, S, 'karma-rep', 'info', 'owner decision: shared; absolute set captured as field diff', f'{H}:h_RepSet'),
    'addreputation': E('ModReputation', S, S, 'karma-rep', 'info', '', f'{H}:h_RepAdd'),
    'addreputationexact': E('ModReputation', S, S, 'karma-rep', 'info', '', f'{H}:h_RepAddExact'),
    'rewardkarma': E('ModKarma', S, S, 'karma-rep', 'info', 'owner decision: shared', f'{H}:h_Karma'),
    'rewardxp': E('ModActorValue', S, S, 'reward', 'info', 'XP pseudo-AV 0x10000; RewardAll rule fans out to every player', f'{H}:h_RewardXP'),
    'additem': E('AddItem', S, S, 'reward', 'info', 'player target only; RewardAll/QuestItemDup rules route', f'{H}:itemCmd',
                 npc={'capture': M, 'apply': M, 'sev': 'low', 'tag': 'npc-inventory'}),
    'removeitem': E('RemoveItem', S, S, 'reward', 'info', 'player target only; RemoveItemPerOwner rule for hand-ins', f'{H}:itemCmd',
                    npc={'capture': M, 'apply': M, 'sev': 'low', 'tag': 'npc-inventory'}),
    'removealltypeditems': E('RemoveItem', M, M, 'reward', 'medium', 'bulk typed removal (e.g. confiscating the player gear when Arcade is enslaved); not detoured', '',
                             npc={'capture': M, 'apply': M, 'sev': 'low', 'tag': 'npc-inventory'}),
    'addnote': E(None, M, M, 'reward', 'medium', 'notes/holotapes are rewards that go to every player; no effect type', ''),
    'removenote': E(None, M, M, 'reward', 'low', '', ''),
    'addspell': E(None, M, M, 'player-target', 'low', '', ''),
    'removespell': E(None, M, M, 'player-target', 'low', '', ''),
    'dispelallspells': E(None, N, N, 'cosmetic', 'info', '', ''),
    'addachievement': E(None, N, N, 'player-target', 'info', 'per-player platform achievement; host-run script only unlocks it on the host (cosmetic)', ''),
    # ---- movement / control / cinematics
    'movetomarker': E('MoveRef', S, S, 'move', 'info', 'console MoveTo run on the target on replicas. For the PLAYER the PartyWarp rule must expand it', f'{H}:h_MoveTo {A}:MoveRef',
                      npc={'capture': S, 'apply': S, 'sev': 'info', 'tag': 'move'}),
    'movetomarkerwithfade': E('MoveRef', S, S, 'move', 'info', '', f'{H}:h_MoveToFade'),
    'setpos': E('MoveRef', P, P, 'move', 'low', 'same-cell absolute only', f'{H}:h_SetPos'),
    'disableplayercontrols': E('SetPlayerControls', M, P, 'cinematic', 'blocker', 'command not detoured (located only); the rules engine can emit SetPlayerControls from rules.json but the script command itself is not captured; apply is a coarse all-classes lock', 'internal lane notes round 2 item 6; coop_module.cpp'),
    'enableplayercontrols': E('SetPlayerControls', M, P, 'cinematic', 'blocker', 'as disableplayercontrols: no capture of the release => stuck lock unless the rules engine timeout/failsafe fires', ''),
    'enablefasttravel': E('SetFastTravelLock', M, M, 'travel', 'high', 'Cmd_EnableFastTravel located not hooked; FastTravelLock rule exists in the rules engine only', 'internal lane notes round 2 item 6'),
    'setinchargen': E(None, M, M, 'ending', 'high', 'ending-slideshow control flag; no effect (SlideshowEnd effect exists but nothing emits it from this command)', ''),
    'autosave': E('SaveSuppress', M, P, 'save', 'medium', 'scripted save on the host only; SaveSuppress is session state with no detour', ''),
    'systemsave': E('SaveSuppress', M, P, 'save', 'medium', '', ''),
    'closeinteriorormenu': E(None, N, N, 'ui', 'low', '', ''),
    # ---- dialogue / UI / audio
    'showmessage': E(None, M, M, 'menu', 'medium', 'message boxes are per-player UI; with buttons (GetButtonPressed) the answering player decides a branch', ''),
    'getbuttonpressed': E(None, M, M, 'menu', 'high', 'reads which button the local player pressed in a message box', ''),
    'showmap': E(None, M, M, 'ui', 'low', 'map marker reveal; per-player', ''),
    'showrepairmenu': E(None, P, P, 'menu', 'low', 'service menu: L8 service sub-session design (docs/dialogue-design.md 7.2)', ''),
    'showbartermenu': E(None, P, P, 'menu', 'low', 'as showrepairmenu', ''),
    'say': E('ForcedDialogue', M, M, 'dialogue', 'medium', 'bark on a host-owned NPC; voice/subtitle for guests needs the L8 spectator channel (Cmd_Say located, not hooked)', ''),
    'sayto': E('ForcedDialogue', M, M, 'dialogue', 'medium', 'forced line to the player: who is "the player" for a guest?', ''),
    'startconversation': E('ForcedDialogue', M, M, 'dialogue', 'high', 'forces the NPC to talk to the player (e.g. FortCaesarRef.StartConversation player at VMS35 100); initiator undefined for a script', ''),
    'startradioconversation': E(None, M, M, 'radio', 'low', '', ''),
    'setnpcradio': E(None, M, M, 'radio', 'low', '', ''),
    'pipboyradiooff': E(None, M, M, 'radio', 'low', 'per-player PipBoy radio state', ''),
    'settalkingactivatoractor': E(None, N, N, 'cosmetic', 'info', '', ''),
    'playsound': E(None, N, N, 'cosmetic', 'info', '', ''),
    'playsound3d': E(None, N, N, 'cosmetic', 'info', '', ''),
    'playmusic': E(None, N, N, 'cosmetic', 'info', 'music is local to the host unless replayed; cosmetic', ''),
    'imagespacemodifier': E(None, N, N, 'cosmetic', 'low', 'screen fade: per-player visual during cutscenes', ''),
    'applyimagespacemodifier': E(None, N, N, 'cosmetic', 'low', '', ''),
    'removeimagespacemodifier': E(None, N, N, 'cosmetic', 'low', '', ''),
    'pms': E(None, N, N, 'cosmetic', 'info', 'PlayMagicShaderVisuals', ''),
    'scaonactor': E(None, N, N, 'combat', 'low', 'StopCombatAlarmOnActor', ''),
    'sifh': E(None, N, N, 'combat', 'low', 'SetIgnoreFriendlyHits', ''),
    'setcs': E(None, M, M, 'world', 'medium', 'SetCS changes a game setting globally (cell-streaming / camera); no effect', ''),
    'setpcsleephours': E(None, M, M, 'player-target', 'low', '', ''),
}

# read-only functions that are NOT co-op hazards (shared state, host-readable): status "read"
SAFE_READ = {
    'getdead', 'getobjectivedisplayed', 'getobjectivecompleted', 'getstage', 'getstagedone', 'getquestcompleted',
    'getquestrunning', 'getglobalvalue', 'getdisabled', 'getdestroyed', 'getreputationthreshold', 'getreputation',
    'getlinkedref', 'getsecondspassed', 'getcurrenttime', 'getself', 'getrandompercent', 'gethealthpercentage',
    'getlocked', 'getdestructionstage', 'isspelltarget', 'isimagespaceactive', 'getisid', 'getinfaction',
    'getweaponanimtype', 'getcurrentaipackage', 'getanimaction', 'getsitting', 'isanimplaying', 'ishardcore',
    'getactorfactionplayerenemy',
}
# read functions that look at THE PLAYER (or an unnamed actor that may be the player): need PartyAwareCond reduction
PREF_READ = {
    'getdistance': 'min-over-party', 'getincell': 'entry:all-in,exit:any-in', 'getinsamecell': 'any',
    'getactionref': 'initiator', 'isactionref': 'initiator', 'getisreference': 'initiator',
    'getplayerteammate': 'initiator (companions are owned by the recruiter)', 'getequipped': 'initiator',
    'getitemcount': 'initiator', 'getactorvalue': 'initiator', 'isincombat': 'any-party-member',
    'issneaking': 'any-party-member', 'isplayerinregion': 'any', 'isweaponout': 'any-party-member',
    'getdetected': 'any-party-member', 'gethasnote': 'initiator', 'isplayermovingintonewspace': 'any',
    'isininterior': 'any', 'getinworldspace': 'any', 'iskiller': 'any-party-member', 'gettalkedtopc': 'initiator',
}
INHERENT_PLAYER = {'isplayerinregion', 'isplayermovingintonewspace', 'getplayerteammate', 'gettalkedtopc', 'getactionref',
                   'isactionref', 'gethasnote'}

# faction/perk operations that belong to the companion system (follower/teammate bookkeeping) even on an NPC subject
import re as _re
COMPANION_RE = _re.compile(r'FollowerFaction|TeammateFaction|CompanionSuite|PlayerTeammate|NVCompanion|vNPCFollowers', _re.I)

# reads that are co-op gaps in their own right (they observe the LOCAL player's UI, not shared world state)
READ_AS_GAP = {'getbuttonpressed'}
