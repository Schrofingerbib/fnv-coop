"""Script function / effect -> synccore Effect type + L5 capture/apply status for the towns questline.

Sources for the status values (all read-only, see questlines/towns/gaps.md for the caveats):
  synccore/include/fnvcoop/effect.h   Effect variant (33 types)
  internal lane notes + plugin/hooks/apply_remote.{h,cpp} + engine_hooks.cpp   what is captured / applied in game today
  internal lane notes + synccore/include/fnvcoop/rules.h                       session-level rule kinds

Status vocabulary: supported | partial | missing.   `na` = no sync needed (host-only / cosmetic) is NOT used for the
status; it is recorded in `class` so the report can separate "really missing" from "host-only by design".
"""

# ---- effect type -> (capture, apply, note) -------------------------------------------------------------------------
EFFECT_STATUS = {
    'SetStage': ('supported', 'supported', 'hooked + applied under ScriptGate (L5 rounds 1-8, live-verified)'),
    'SetObjective': ('supported', 'supported', 'objective state byte (live-verified)'),
    'SetGlobal': ('supported', 'supported', 'captured by diffing all TESGlobals around the outermost scope; apply is a field write (M)'),
    'EnableRef': ('partial', 'supported', 'hook is SetDisabled and records only on a state CHANGE (L5 round 8): a ref already in that state is invisible'),
    'DisableRef': ('partial', 'supported', 'same as EnableRef'),
    'AddItem': ('partial', 'partial', 'player inventory only; container/NPC AddItem capture not implemented (L5 "Not implemented")'),
    'RemoveItem': ('partial', 'partial', 'same as AddItem'),
    'ModActorValue': ('supported', 'partial', 'capture ok (XP pseudo-AV 0x10000, AV cmds); apply uses Actor vtbl +0x3A8, unverified in game'),
    'ModReputation': ('supported', 'supported', 'TESReputation::Set (V)'),
    'ModKarma': ('supported', 'supported', 'Player::RewardKarma (M)'),
    'StartQuest': ('supported', 'supported', 'TESQuest::SetRunning (V)'),
    'StopQuest': ('supported', 'supported', 'TESQuest::SetRunning (V)'),
    'MoveRef': ('partial', 'partial', 'MoveTo/MoveToMarker/MoveToMarkerWithFade/SetPos captured; apply via console MoveTo/SetPos, cross-cell without marker Unsupported; MoveToFade (no marker arg) not hooked'),
    'KillActor': ('supported', 'supported', 'live-verified L5 round 8 (command level only; script-driven deaths via OnDeath go through the NV:MP ghost)'),
    'ResurrectActor': ('supported', 'supported', 'live-verified L5 round 8'),
    'SetScriptVar': ('partial', 'partial', 'quest variables captured by diff and applied; ref/object-script variables are NOT captured (no hook), apply exists'),
    'CompleteQuest': ('supported', 'supported', 'TESQuest::SetCompleted'),
    'FailQuest': ('supported', 'supported', 'TESQuest::SetFailed 0x60CAF0'),
    'CompleteAllObjectives': ('missing', 'missing', 'Effect type + JSON codec exist; no capture hook, no applier branch'),
    'SetFactionRank': ('missing', 'missing', 'Effect type + codec exist; Cmd_SetFactionRank is only BLOCKed on guests (BLOCK_CMD), never recorded; no applier'),
    'SetPerk': ('missing', 'missing', 'Effect type + codec exist; AddPerk/RemovePerk hooked only to block on guests; no capture, no applier'),
    'SetLock': ('missing', 'missing', 'Effect type + codec exist; no hook, no applier'),
    'SwapTexture': ('missing', 'missing', 'Effect type + codec exist; no hook, no applier'),
    'ForcedDialogue': ('partial', 'partial', 'L8 dialogue lane owns SayTo/StartConversation sync; not an L5 capture'),
    'CompanionOwner': ('missing', 'missing', 'rule-level only (RulesEngine CompanionOwner); SetPlayerTeammate is not captured'),
    'SetPlayerControls': ('partial', 'partial', 'controls.lock/unlock verified (L5 round 8) and rule-driven locks work in L10; DisablePlayerControls script calls are not captured as effects'),
}

# ---- script function (lowercase) -> (effect type or None, class, hazards, note) ------------------------------------
# class: state (world/quest state that must converge) | player (per-player or shared player stat/inventory) |
#        host-only (AI/behaviour that only the host runs; clients see it through NV:MP actor sync) | ui | cosmetic | noise
# hazards (BRIEF list): player-effect, spawn, companion, essential, teleport, cinematic, control-lock, menu, timer, radio,
#        ending, kill-all, reward, karma-rep, faction, ai-state, quest-var, ui
F = {}


def _f(names, eff, cls, hazards=(), note=''):
    for n in names.split():
        F[n] = (eff, cls, tuple(hazards), note)


_f('setstage', 'SetStage', 'state')
_f('setobjectivedisplayed setobjectivecompleted', 'SetObjective', 'state')
_f('completequest', 'CompleteQuest', 'state')
_f('failquest', 'FailQuest', 'state')
_f('completeallobjectives', 'CompleteAllObjectives', 'state', (), 'sets every objective completed')
_f('startquest', 'StartQuest', 'state')
_f('stopquest', 'StopQuest', 'state')
_f('set_global setglobalvalue', 'SetGlobal', 'state')
_f('enable', 'EnableRef', 'state')
_f('disable', 'DisableRef', 'state')
_f('markfordelete', None, 'state', (), 'permanent removal of a ref; closest Effect is DisableRef (not equivalent)')
_f('resurrect resurrectactor', 'ResurrectActor', 'state')
_f('kill killactor', 'KillActor', 'state')
_f('additem additemns', 'AddItem', 'player', ('reward',))
_f('removeitem', 'RemoveItem', 'player', ())
_f('equipitem unequipitem', None, 'player', ('player-effect',), 'equip state is per-actor; NPC equip is host-only')
_f('rewardxp', 'ModActorValue', 'player', ('reward',))
_f('addreputation setreputation modreputation', 'ModReputation', 'player', ('karma-rep', 'faction'))
_f('rewardkarma playerrewardkarma', 'ModKarma', 'player', ('karma-rep',))
_f('setav forceav modav restoreav damageav setactorvalue modactorvalue', 'ModActorValue', 'player', ('player-effect',))
_f('addperk removeperk', 'SetPerk', 'player', ('player-effect',))
_f('addtofaction removefromfaction setfactionrank modfactionrank', 'SetFactionRank', 'state', ('faction',),
   'owner decision: player faction membership/rank is shared; NPC faction changes are world state')
_f('moveto movetomarker movetomarkerwithfade movetofade setpos', 'MoveRef', 'state', ('teleport',))
_f('set_questvar setquestvar', 'SetScriptVar', 'state', ('quest-var',))
_f('addtopic', None, 'state', (), 'topic unlock (per-player journal in vanilla); L8 owns say-once/topic state')
_f('addnote', None, 'player', ('reward',), 'Pip-Boy note; no Effect type (could be AddItem of the note form)')
_f('removenote', None, 'player', ('player-effect',))
_f('addscriptpackage removescriptpackage evaluatepackage evp resetai', None, 'host-only', ('ai-state',),
   'AI package changes run on the host; actors are host-owned (NV:MP actor sync)')
_f('startcombat stopcombat setenemy setally setpcenemyoffaction setpcfactionenemy setpcfactionally', None, 'host-only', ('ai-state',),
   'combat/relations: host-owned actors; a faction-relation change (SetEnemy PlayerFaction X) is persistent world state with no Effect')
_f('setrestrained setcombatstyle setignorefriendlyhits ignorecrime setghost setessential', None, 'host-only', ('ai-state',))
_f('placeatme', None, 'state', ('spawn',), 'spawn: no Effect type; the host spawns once, clients must see the NV:MP actor, not run PlaceAtMe')
_f('sayto say startconversation settalkingactivatoractor', 'ForcedDialogue', 'ui', ('cinematic',))
_f('showmessage showwarning', None, 'ui', ('menu',), 'message box: shown to the player who triggered it (or everyone?) - policy needed')
_f('showbartermenu showrepairmenu showcaravanmenu openteammatecontainer', None, 'ui', ('menu',), 'initiator-only menu')
_f('showmap', None, 'ui', ('ui',), 'map marker reveal is per-player in vanilla')
_f('addachievement', None, 'cosmetic', (), 'per-player Steam/achievement')
_f('applyimagespacemodifier imod', None, 'cosmetic', ('cinematic',))
_f('disableplayercontrols', 'SetPlayerControls', 'player', ('control-lock', 'cinematic'))
_f('enableplayercontrols', 'SetPlayerControls', 'player', ('control-lock',))
_f('setplayerteammate', 'CompanionOwner', 'player', ('companion',))
_f('setquestdelay', None, 'host-only', ('timer',), 'quest script tick rate; host-only')
_f('setownership setcellownership', None, 'state', (), 'ownership flag on a ref/cell (affects theft crime); no Effect')
_f('unlock lock', 'SetLock', 'state')
_f('castimmediateonself', None, 'host-only', (), 'spell cast')
_f('activate', None, 'state', (), 'script Activate: Intent mechanism (L5 round 6) re-runs it on the host')
_f('playidle playsound', None, 'cosmetic')
_f('addformtoformlist', None, 'state')
_f('swaptexture', 'SwapTexture', 'cosmetic')
# parse noise from the oracle's tokenizer (conditions/labels mistaken for commands)
_f('bplayerdetected bclbranch bhsugavequest) if(getobjectivedisplayed', None, 'noise', (), 'oracle tokenizer artefact (a script variable or an if( ... ) condition read as a command)')

# canonical lowercase function names found in risk findings that are *reads* (conditions / getters)
READS = {'getobjectivecompleted', 'getobjectivedisplayed', 'getstagedone', 'getstage', 'getquestcompleted',
         'getquestrunning', 'getglobalvalue', 'getdistance', 'getincell', 'getinsamecell', 'getinworldspace', 'getactionref',
         'getinfaction', 'getplayerteammate', 'getisreference', 'getdetected', 'getitemcount', 'getactorvalue',
         'getreputationthreshold', 'getequipped', 'getissex', 'getpcissex', 'hasperk', 'player-ref'}

# risk-finding names whose canonical spelling differs
ALIAS = {'set questvar': 'set_questvar', 'set global': 'set_global'}

PLAYER_NAMES = {'player', 'playerref'}


def lookup(fn):
    k = ALIAS.get(fn.lower(), fn.lower())
    return F.get(k)


def status_of(effect):
    """-> (capture, apply, note)"""
    return EFFECT_STATUS[effect]


def worst(a, b):
    order = {'supported': 0, 'partial': 1, 'missing': 2}
    return a if order[a] >= order[b] else b
