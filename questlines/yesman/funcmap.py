"""Script function -> synccore Effect + L5 capture/apply status table for the Yes Man questline.

Sources of truth (read when this table was written; re-verify if L5 changes):
  capture : plugin/hooks/engine_hooks.cpp  (kEntries = the detours that call g_hub->record(...); BLOCK_CMD = dropped on a guest,
            NO Effect recorded), plugin/hooks/capture_hub.cpp (mutations outside a capture scope are dropped: droppedNoScope)
  apply   : plugin/hooks/apply_remote.cpp / apply_remote.h, plugin/coop/coop_module.cpp (SetPlayerControls, SaveSuppress,
            PartyTether, Barrier*), synccore/src/rules.cpp (rule engine rewrites recipients / expands PartyWarp)
  effects : synccore/include/fnvcoop/effect.h

Status vocabulary: supported | partial | missing.  `ambient` = statement executed in a quest-script GameMode block or an
object/NPC script, i.e. outside any capture scope (the hub only opens scopes at TESQuest::SetStage and
TESTopicInfo::RunResultScript), so a "supported" primitive is downgraded to partial (digest/heal may repair it later).
"""

SUP, PAR, MIS = 'supported', 'partial', 'missing'
ORDER = {SUP: 0, PAR: 1, MIS: 2}
SEV_ORDER = {'low': 0, 'medium': 1, 'high': 2}

# fn (lowercase) -> (effect, capture, apply, severity, tags, note)
F = {}


def add(names, effect, cap, app, sev, tags='', note=''):
    for n in names.split():
        F[n] = (effect, cap, app, sev, tuple(tags.split()), note)


add('setstage', 'SetStage', SUP, SUP, 'low', '', 'TESQuest::SetStage hook opens its own scope')
add('setobjectivedisplayed setobjectivecompleted', 'SetObjective', SUP, SUP, 'low', '', 'ObjectiveSetState hook; needs a scope')
add('startquest', 'StartQuest', SUP, SUP, 'low')
add('stopquest', 'StopQuest', SUP, SUP, 'low')
add('completequest', 'CompleteQuest', SUP, SUP, 'low')
add('failquest', 'FailQuest', SUP, SUP, 'low')
add('enable', 'EnableRef', SUP, SUP, 'low', '', 'recorded on a state CHANGE only (SetDisabled hook)')
add('disable', 'DisableRef', SUP, SUP, 'low', '', 'recorded on a state CHANGE only (SetDisabled hook)')
add('additem additemns', 'AddItem', SUP, SUP, 'low', 'player-effect', 'player target only; RewardAll/QuestItemDup rewrite the recipient')
add('removeitem', 'RemoveItem', SUP, SUP, 'low', 'player-effect', 'player target only')
add('rewardxp', 'ModActorValue', SUP, SUP, 'low', 'player-effect', 'XP pseudo-AV 0x10000; RewardAll')
add('rewardkarma playerrewardkarma', 'ModKarma', SUP, SUP, 'low', 'player-effect', 'shared karma (owner decision)')
add('addreputation removereputation setreputation addreputationexact modreputation', 'ModReputation', SUP, SUP, 'low',
    'player-effect', 'RepAdd/RepRemove/RepSet hooks; shared reputation (owner decision)')
add('moveto movetomarker movetomarkerwithfade setpos', 'MoveRef', SUP, PAR, 'medium', 'teleport',
    'captured (MoveToMarker/WithFade/SetPos hooks); apply via console line, cross-cell without marker is Unsupported; '
    'party-warp expansion needs the PartyWarp rule and a scope with cause.source = a rule-bearing quest')
add('kill killactor', 'KillActor', SUP, SUP, 'low', '', 'console `KillActor` run on the actor')
add('resurrect', 'ResurrectActor', SUP, SUP, 'low')
add('setav setactorvalue forceav forceactorvalue modav modactorvalue damageav damageactorvalue', 'ModActorValue', SUP, SUP,
    'medium', 'player-effect', 'captured for the player only (non-player AV changes are counted as unmapped avOnNonPlayer)')
add('restoreav restoreactorvalue', 'ModActorValue', MIS, MIS, 'medium', 'player-effect', 'RestoreAV is not hooked')
add('resethealth', None, MIS, MIS, 'low', 'player-effect', 'no hook, no Effect')
add('addtofaction setfactionrank removefromfaction modfactionrank', 'SetFactionRank', MIS, MIS, 'medium', 'faction',
    'BLOCK_CMD only: dropped on a guest, no Effect recorded, no apply (owner decision says player faction rank is shared)')
add('addperk removeperk', 'SetPerk', MIS, MIS, 'medium', 'perk player-effect', 'BLOCK_CMD only; SetPerk effect exists but is never recorded')
add('setplayerteammate', 'CompanionOwner', MIS, MIS, 'high', 'companion', 'no hook; CompanionOwner effect exists in synccore only')
add('openteammatecontainer', None, MIS, MIS, 'medium', 'companion menu', 'companion inventory menu is local UI')
add('disableplayercontrols enableplayercontrols enablecontrol disablecontrol', 'SetPlayerControls', MIS, SUP, 'high', 'cinematic',
    'no Cmd hook: only the apply side exists (SetPlayerControlsLocked / controls.lock) and the rules engine synthesises '
    'the lock; the host script lock itself is never captured')
add('enablefasttravel', 'SetFastTravelLock', MIS, MIS, 'high', 'fast-travel',
    'no hook and no apply; the FastTravelLock rule only rewrites txns that already carry a SetFastTravelLock')
add('autosave systemsave save quicksave', 'SaveSuppress', MIS, PAR, 'medium', 'save',
    'no Cmd hook; SaveSuppress is applied by coop_module when the rules engine emits it')
add('setquestdelay setquestobject forceactivequest', None, MIS, MIS, 'low', '', 'quest metadata, no Effect')
add('settalkingactivatoractor', None, MIS, MIS, 'low', 'dialogue', 'binds a terminal/activator to an NPC for talking')
add('say sayto startconversation stopconversation', 'ForcedDialogue', MIS, PAR, 'medium', 'dialogue',
    'no engine hook records ForcedDialogue; the L8 dialogue lane owns forced/scripted lines')
add('showmessage showwarning messageex', None, MIS, MIS, 'medium', 'menu', 'message box is local UI of whichever machine runs the script')
add('showmap showbartermenu showrepairmenu showcaravanmenu showmenu', None, MIS, MIS, 'medium', 'menu', 'local UI')
add('addnote removenote', None, MIS, MIS, 'low', 'player-effect', 'note item added to the player, no hook')
add('addachievement', None, MIS, MIS, 'low', 'achievement', 'Steam achievement of the machine that runs the script only (host)')
add('applyimagespacemodifier imod removeimagespacemodifier', None, MIS, MIS, 'medium', 'cinematic',
    'screen fade/FX is local: guests would not see the fade the host uses to hide a teleport')
add('playsound playsound3d playmusic', None, MIS, MIS, 'low', 'cosmetic')
add('pipboyradiooff', None, MIS, MIS, 'low', 'radio', 'player Pip-Boy radio state is per machine')
add('playgroup playidle pushactoraway look stoplook forceflee setsecuritronexpression', None, PAR, PAR, 'low', 'ai',
    'NPC animation/AI is host-simulated and rides NV:MP actor sync (unverified)')
add('evp evaluatepackage addscriptpackage removescriptpackage resetai sifh setignorefriendlyhits setrestrained setghost '
    'stopcombat startcombat stopcombatalarmonactor setcombatstyle ignorecrime sendassaultalarm sendtrespassalarm forcemodel',
    None, PAR, PAR, 'low', 'ai', 'NPC AI state: host-run, dropped on a guest (BLOCK_CMD), visible to guests only through NV:MP actor sync')
add('setessential', None, PAR, PAR, 'medium', 'essential', 'NPC flag changes on the host only')
add('setenemy setally setpcenemyoffaction clearfactionplayerenemyflag setfactionreaction', None, MIS, MIS, 'medium', 'faction',
    'faction relations are save state with no Effect; guests keep the old relation for local conditions/dialogue')
add('setdestroyed cleardestruction damageobject setopenstate setownership setdestructible', None, MIS, MIS, 'low', 'world',
    'object state with no Effect')
add('lock unlock', 'SetLock', MIS, MIS, 'low', 'world', 'BLOCK_CMD only; SetLock effect is never recorded')
add('activate', None, MIS, MIS, 'low', 'world', 'Activate command is blocked on guests, not captured')
add('placeatme', None, MIS, MIS, 'medium', 'spawn', 'spawned refs have no cross-client identity (host-only new ref)')
add('markfordelete', None, MIS, MIS, 'low', 'spawn')
add('cios scaonactor dispelallspells castimmediateonself', None, MIS, MIS, 'low', 'cosmetic', 'spell/effect cast on an actor')
add('setinchargen', None, MIS, MIS, 'high', 'cinematic', 'chargen/slideshow mode flag')
add('equipitem unequipitem', None, MIS, MIS, 'low', 'world', 'equipment change on an actor (companions/NPCs); no Effect')
add('setactorfullname', None, MIS, MIS, 'low', 'world')
add('removeme', None, MIS, MIS, 'low', 'spawn')

# assignments (kind == 'set') are classified in classify_occurrence
SET_GLOBAL = ('SetGlobal', SUP, SUP, 'low', (), 'globals are diffed around the outermost scope and polled on the host (pollHostState)')
SET_QUESTVAR = ('SetScriptVar', SUP, SUP, 'low', (), 'quest variables are polled by pollHostState (host authoritative)')
SET_REFVAR = ('SetScriptVar', PAR, SUP, 'low', (), 'NPC/object script variable: only replicated for whitelisted owners (baseline)')

# condition-level (kind == 'query') functions that read player-bound or party-dependent state
QUERY_PLAYER = {
    'getdistance': 'min-over-party', 'getincell': 'entry:all-in,exit:any-in', 'getinsamecell': 'any',
    'isactionref': 'initiator', 'getactionref': 'initiator', 'iskiller': 'any-party-member', 'getinworldspace': 'any',
    'getplayerteammate': 'any-party-member', 'getdetected': 'any', 'getequipped': 'initiator', 'getitemcount': 'initiator',
    'getav': 'initiator', 'getactorvalue': 'initiator', 'isweaponout': 'initiator', 'getweaponanimtype': 'initiator',
    'issneaking': 'initiator', 'isspelltarget': 'any', 'getpcenemyofaction': 'shared', 'getreputationthreshold': 'shared',
    'getinfaction': 'initiator', 'isininterior': 'any', 'getlineofsight': 'any', 'ishardcore': 'shared',
    'isplayermovingintonewspace': 'any-party-member',
}
# the cond-reduction vocabulary understood by synccore RulesEngine::reduceCondition
REDUCTIONS = {'initiator', 'any-party-member', 'any', 'min-over-party', 'entry:all-in,exit:any-in', 'shared'}

# statements that count towards "kill everyone" when >= KILL_BATCH in one script
KILL_BATCH = 5
# scopes where no capture scope is open (see module docstring)
AMBIENT_SCOPES = {'quest_script', 'dep'}
NO_SCOPE_NEEDED = {'setstage'}


def worst(a, b):
    return a if ORDER[a] >= ORDER[b] else b


def classify_occurrence(rec, scope, globals_lc, is_player_subj):
    """-> dict(effect, capture, apply, severity, tags, note, ambient) for one scanned statement (kind cmd/set)."""
    fn = rec['fn'].lower()
    if rec['kind'] == 'set':
        lhs = rec['args'][0] if rec['args'] else ''
        if '.' in lhs:
            eff = SET_QUESTVAR if scope != 'dep' else SET_REFVAR
        elif lhs.lower() in globals_lc:
            eff = SET_GLOBAL
        else:
            eff = SET_QUESTVAR if scope in ('quest_script', 'stage', 'info') else SET_REFVAR
        fn = 'set'
    else:
        eff = F.get(fn)
        if eff is None:
            eff = (None, MIS, MIS, 'low', ('unclassified',), 'function not in funcmap')
    effect, cap, app, sev, tags, note = eff
    tags = list(tags)
    if fn in ('additem', 'additemns', 'removeitem', 'setav', 'setactorvalue', 'forceav', 'forceactorvalue', 'modav',
              'modactorvalue', 'damageav', 'damageactorvalue', 'rewardxp', 'rewardkarma'):
        if fn not in ('rewardxp', 'rewardkarma') and not is_player_subj:
            cap, app = MIS, MIS
            note = 'non-player target is not captured (itemOnNonPlayer / avOnNonPlayer unmapped counters)'
            tags = [t for t in tags if t != 'player-effect']
            sev = 'low'
    if fn in ('moveto', 'movetomarker', 'movetomarkerwithfade', 'setpos') and is_player_subj:
        sev = 'high'
        tags.append('player-teleport')
    if fn in ('addtofaction', 'setfactionrank', 'removefromfaction', 'modfactionrank'):
        if is_player_subj:
            sev = 'high'
            tags.append('player-faction')
        else:
            tags.append('npc-faction')
            note = ('NPC faction membership is world state: guests evaluate GetInFaction on their NPC puppets (dialogue '
                    'conditions), so the change must reach them; ' + note)
    ambient = scope in AMBIENT_SCOPES and fn not in NO_SCOPE_NEEDED and fn != 'set'
    if ambient and cap == SUP:
        cap = PAR
        note = (note + '; ' if note else '') + 'ambient: no capture scope open (droppedNoScope), only digest/baseline repair'
    return {'effect': effect, 'capture': cap, 'apply': app, 'severity': sev, 'tags': sorted(set(tags)), 'note': note,
            'ambient': ambient}
