"""Script-function -> synccore Effect -> L5 capture/apply status table, and the line scanner that uses it.

Status semantics (sources: synccore/include/fnvcoop/effect.h, plugin/hooks/{engine_hooks,capture_hub,apply_remote}.cpp, internal lane notes):
  capture : does an L5 hook record this Effect while a host script runs inside a capture scope?
  apply   : can RemoteApplier replay the Effect on a client without running the script?
  supported = yes, partial = only some forms / known limitation, missing = no hook / no applier, n/a = nothing to replicate.
Statuses marked AUTO are DERIVED FROM THE CODE at import time (sync): an Effect type counts as captured when
`record(<Effect>{` appears in plugin/hooks/engine_hooks.cpp or capture_hub.cpp, and as applied when
`get_if<Effect>` appears in apply_remote.cpp. L4/L5 land new hooks continuously, so the table follows them instead of going stale;
run_tests.py fails when generated artifacts are older than the code (rerun build_all.py).
Severity is OUR judgement of the co-op damage when the status is not 'supported' (blocker > high > medium > low).
Capture scope (see apply_scope): quest GameMode blocks and object scripts are AMBIENT, i.e. outside any capture scope.
"""
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common  # noqa: E402

S, P, M, NA, AUTO = 'supported', 'partial', 'missing', 'n/a', 'auto'


def _read(rel):
    with open(os.path.join(common.REPO, rel), encoding='utf-8') as f:
        return f.read()


def l5_sets():
    """(Effect names in effect.h, Effects L5 records, Effects RemoteApplier applies, 'reduceCondition' wired into plugin/)."""
    names = set(re.findall(r'^struct (\w+) ', _read('synccore/include/fnvcoop/effect.h'), re.M))
    rec = _read('plugin/hooks/engine_hooks.cpp') + _read('plugin/hooks/capture_hub.cpp')
    recorded = set(re.findall(r'record\(\s*(?:fnvcoop::)?(\w+)\s*\{', rec)) | {'SetScriptVar'}   # quest vars: scope diff / state polling
    hooks = re.sub(r'//[^\n]*', '', _read('plugin/hooks/engine_hooks.cpp'))
    for n in names:   # effects built in a local variable and recorded later ("ObjectState os; ... record(os)")
        if re.search(r'\b' + n + r'\s+\w+\s*[;=]', hooks) and re.search(r'record\(\s*\w+\s*\)', hooks):
            recorded.add(n)
    applied = set(re.findall(r'get_if<(\w+)>', _read('plugin/hooks/apply_remote.cpp')))
    cond_wired = False
    for root, _dirs, files in os.walk(os.path.join(common.REPO, 'plugin')):
        if os.sep + 'tests' in root or 'third_party' in root:
            continue
        for fn in files:
            if fn.endswith(('.cpp', '.h')):
                try:
                    if 'reduceCondition' in open(os.path.join(root, fn), encoding='utf-8', errors='ignore').read():
                        cond_wired = True
                except OSError:
                    pass
    return names, recorded, applied, cond_wired


NAMES, RECORDED, APPLIED, COND_WIRED = l5_sets()


def tokens(effect, names=None):
    names = NAMES if names is None else names
    if not effect:
        return set()
    return {t for t in re.findall(r'[A-Z][A-Za-z]+', effect) if t in names}


def resolve(e):
    """Replace AUTO statuses by what the code says; remember which fields were derived."""
    e = dict(e)
    e['auto'] = {'capture': e['capture'] == AUTO, 'apply': e['apply'] == AUTO}
    t = tokens(e['effect'])
    if e['capture'] == AUTO:
        e['capture'] = S if t and t <= RECORDED else M
    if e['apply'] == AUTO:
        e['apply'] = S if t and t <= APPLIED else M
    return e


def E(effect, capture, apply, kind='write', hazards=(), sev='low', note=''):
    return resolve({'effect': effect, 'capture': capture, 'apply': apply, 'kind': kind, 'hazards': list(hazards),
                    'severity': sev, 'note': note})


# functions where the SUBJECT matters: key -> entry used when the subject is the player (FN holds the non-player entry)
PLAYER_SPLIT = {}
FN = {}


def add(names, entry, player_entry=None):
    for n in names.split():
        FN[n] = entry
        if player_entry:
            PLAYER_SPLIT[n] = player_entry


# ---- quest state -----------------------------------------------------------------------------------------------
add('setstage', E('SetStage', AUTO, AUTO, note='TESQuest::SetStage hook + Cmd_SetStage; applied under ScriptGate'))
add('setobjectivedisplayed setobjectivecompleted', E('SetObjective', AUTO, AUTO, note='BGSQuestObjective::SetState hook (flags bit0 displayed, bit1 completed)'))
add('startquest stopquest', E('StartQuest/StopQuest', AUTO, AUTO, note='TESQuest::SetRunning hook'))
add('completequest', E('CompleteQuest', AUTO, AUTO, note='TESQuest::SetCompleted hook'))
add('failquest', E('FailQuest', AUTO, AUTO, note='TESQuest::SetFailed hook'))
add('completeallobjectives', E('CompleteAllObjectives', AUTO, AUTO, note='TESQuest::CompleteAllObjectives'))
add('setglobalvalue', E('SetGlobal', AUTO, AUTO, note='TESGlobal diff around the outermost scope / state polling'))
add('addtopic', E('AddTopic', AUTO, AUTO, hazards=['topic-list'], sev='medium', note='journal topics are shared'))
add('forceactivequest', E('ForceActiveQuest', AUTO, AUTO, sev='low', note='the focused (tracked) quest is per player'))
add('resetquest', E(None, M, M, hazards=['quest-state'], sev='medium', note='no Effect type for resetting a quest'))
add('setquestobject', E(None, M, M, sev='low', note='quest-target flag on an object; no Effect'))
# ---- rewards / player stats ------------------------------------------------------------------------------------
add('rewardxp', E('ModActorValue(XP)', AUTO, AUTO, hazards=['reward', 'player-targeted'], note='Cmd_RewardXP hook -> kAvExperience; owner decision: full XP to every player'))
add('rewardkarma playerrewardkarma', E('ModKarma', AUTO, AUTO, hazards=['player-targeted'], note='Player::RewardKarma hook; karma is shared'))
add('addreputation setreputation modreputation addreputationexact removereputation',
    E('ModReputation', AUTO, AUTO, hazards=['player-targeted'], note='TESReputation::Add/Set hooks (field diff); reputation is shared'))
add('addachievement', E(None, NA, NA, sev='low', note='platform achievement; per-profile, nothing to replicate'))
add('addnote', E('AddNote', AUTO, AUTO, hazards=['reward', 'player-targeted'], sev='medium', note='Pipboy notes are shared (journal)'))
add('removenote', E('RemoveNote', AUTO, AUTO, hazards=['player-targeted'], sev='low', note='Pipboy notes are shared (journal)'))
add('addperk removeperk', E('SetPerk', AUTO, AUTO, hazards=['player-targeted'], sev='high', note='per-player perk rank, routed like rewards'))
add('modpcskill modpcattr addspecialpoints addtagskills', E(None, M, M, hazards=['player-targeted'], sev='medium', note='no Effect'))
add('modpcmiscstat', E(None, M, M, hazards=['player-targeted'], sev='low', note='misc stat; no Effect'))
add('incrementscriptedchallenge modchallengeprogress setchallengeprogress', E(None, NA, NA, sev='low', note='challenge counters; per-profile'))
# items: split by subject (player vs. container/NPC)
add('additem additemns additemhealthpercent removeitem removeitemns dropme dropitem',
    E('RefAddItem/RefRemoveItem', AUTO, AUTO, hazards=['container'], sev='medium', note='inventory of a non-player ref (container / NPC)'),
    E('AddItem/RemoveItem', AUTO, AUTO, hazards=['reward', 'player-targeted'], note='Cmd_AddItem/Cmd_RemoveItem on the player; Recipient routing is an L4 rule (quest items to all)'))
add('removeallitems', E('RemoveAllItems', AUTO, AUTO, hazards=['container'], sev='medium', note='clears the inventory of a ref'),
    E('RemoveAllItems', AUTO, AUTO, hazards=['container', 'player-targeted'], sev='medium', note='clears the PLAYER inventory'))
add('equipitem unequipitem unequipall', E(None, M, M, hazards=['npc-ai'], sev='low',
                                         note='equip state; no Effect (NPC gear is host-owned actor state)'),
    E(None, NA, NA, hazards=['player-targeted'], sev='low', note='per-player equipment'))
add('setav forceav modav modactorvalue forceactorvalue setactorvalue damageav damageactorvalue restoreav restoreactorvalue',
    E(None, M, M, hazards=['npc-ai'], sev='low',
      note='ModActorValue has no actor target (Recipient = players only); NPC AV changes (e.g. Aggression) are not replicated'),
    E('ModActorValue', AUTO, AUTO, hazards=['player-targeted'], note='AV before/after diff at command level'))
add('removealltypeditems', E(None, M, M, hazards=['container', 'player-targeted'], sev='medium',
                             note='bulk move of typed items between containers (Gomorrah equipment stash); no Effect of its own'))
add('dispelallspells resethealth setinchargen', E(None, M, M, hazards=['player-targeted'], sev='medium',
                                                 note='player-state reset during the ending (VEnding quest script); no Effect'))
add('cios scaonactor useweapon setsecexp castimmediateonself setcs settalkingactivatoractor', E(None, M, NA, hazards=['npc-ai', 'combat'], sev='low',
                                                               note='NPC spell/weapon/combat-AI command; no Effect (host-owned actor)'))
add('startradioconversation', E(None, M, M, hazards=['radio'], sev='low', note='radio broadcast conversation; no Effect'))
# ---- world state -----------------------------------------------------------------------------------------------
add('enable disable', E('EnableRef/DisableRef', AUTO, AUTO, hazards=['world'], note='TESObjectREFR::SetDisabled hook; recorded only on a state CHANGE'))
add('kill killactor', E('KillActor', AUTO, AUTO, hazards=['kill'], note='Cmd_KillActor hook; applier via console line'))
add('resurrect resurrectactor', E('ResurrectActor', AUTO, AUTO, note='Cmd_ResurrectActor hook; applier via console line'))
add('moveto movetomarker movetofade movetomarkerwithfade setpos',
    E('MoveRef', AUTO, P, hazards=['move'], sev='low',
      note='MoveToMarker/WithFade/SetPos capture; apply goes through a console line; cross-cell without a marker is Unsupported'),
    E('MoveRef', AUTO, P, hazards=['move', 'player-targeted', 'party-warp'], sev='blocker',
      note='player teleport: captured as MoveRef; the PartyWarp RULE expands it per slot (RulesEngine::filter), absent players are held; cross-cell without a marker is Unsupported'))
add('setdestroyed', E('ObjectState', AUTO, AUTO, hazards=['world', 'persistent-state'], sev='medium', note='destroyed flag (sticky)'))
add('lock unlock setlocked', E('ObjectState', AUTO, AUTO, hazards=['world'], sev='medium', note='lock state + level'))
add('setopenstate', E('ObjectState', AUTO, AUTO, hazards=['world'], sev='low', note='open/closed'))
add('activate setownership clearownership setcellownership setcellpublicflag setdestructible', E(None, M, M, hazards=['world'], sev='low', note='no Effect'))
add('placeatme placeatmeobject placeleveledactoratme createfullactorcopy', E('SpawnRef', AUTO, AUTO, hazards=['spawn'], sev='high',
                                                                          note='host allocates a stable created-ref id and sends SpawnRef'))
add('markfordelete removeme', E(None, M, M, hazards=['world', 'persistent-state'], sev='medium', note='no Effect (ref deletion of a base-game ref)'))
add('swaptexture', E('SwapTexture', AUTO, AUTO, hazards=['ending', 'slideshow'], sev='blocker',
                     note='ending slide changes (OnScreenSlideRef); the Effect exists in L4'))
add('setquestdelay', E(None, NA, NA, sev='low', note='quest tick rate; host-only'))
# ---- NPC / AI / combat (host-owned actors; relies on NV:MP actor sync) ------------------------------------------
add('addscriptpackage removescriptpackage evaluatepackage evp resetai follow stopwaiting setoutofrange setalert setrestrained setunconscious setghost setactoralpha setcombatstyle forceflee setignorefriendlyhits sifh setallreachable setallvisible setnoavoidance setforcesneak look stoplook pickidle playidle playgroup pushactoraway setessential setactorrefessential setscale setactorstate',
    E(None, M, NA, hazards=['npc-ai'], sev='low',
      note='NPC AI/animation/flags: no Effect type. Correct only if the NPC is host-owned and synced by NV:MP (out/coverage/hostowned.json)'))
FN['setessential'] = E(None, M, NA, hazards=['essential', 'npc-ai'], sev='medium', note='essential flag change; no Effect (host-owned actor state)')
FN['setactorrefessential'] = FN['setessential']
add('startcombat stopcombat stopcombatalarmonactor sendassaultalarm sendtrespassalarm createdetectionevent',
    E(None, M, NA, hazards=['combat', 'npc-ai'], sev='medium',
      note='combat start/stop and crime alarms: no Effect; host-owned AI must drive it; target==player means "the party"'))
add('setenemy setally setfactionreaction modfactionreaction clearfactionplayerenemyflag setpcfactionenemy setpcfactionally setpcfactionneutral setpcenemyoffaction',
    E(None, M, M, hazards=['faction-relations'], sev='high',
      note='faction-to-faction/player relations are global engine state with no Effect type; guests keep stale relations'))
add('addtofaction removefromfaction setfactionrank modfactionrank removefromallfactions',
    E('SetFactionRank', AUTO, AUTO, hazards=['faction-membership'], sev='medium', note='NPC faction membership'),
    E('SetFactionRank', AUTO, AUTO, hazards=['faction-membership', 'player-targeted'], sev='high',
      note='owner decision: player faction membership/rank is shared'))
add('setplayerteammate', E('CompanionOwner', AUTO, AUTO, hazards=['companion'], sev='high', note='companion ownership rule is open (default: recruiter)'),
    E('CompanionOwner', AUTO, AUTO, hazards=['companion', 'player-targeted'], sev='high', note='subject is the player'))
# ---- UI / dialogue / control ----------------------------------------------------------------------------------
add('say sayto', E('ForcedDialogue', AUTO, AUTO, hazards=['dialogue', 'cinematic'], sev='high',
                   note='script-forced conversation; there is no initiator to pick'))
add('showmessage messageex', E(None, M, M, hazards=['menu'], sev='low', note='local HUD/message-box; no Effect (decide: show to all vs initiator)'))
add('showmap', E('SetMapMarker', AUTO, AUTO, hazards=['menu'], sev='low', note='reveals a map marker (mapping to SetMapMarker UNVERIFIED)'))
add('showbartermenu showrepairmenu showdialogmenu showlockpickmenu showhackingmenu showcharactermenu showtraitmenu showperkmenu showcompanionwheel showreputationmenu showracemenu showmenu startconversation stopconversation',
    E(None, M, M, hazards=['menu'], sev='medium', note='UI menu on the player; service sub-session (L8 7.2) not captured at script level'))
add('disableplayercontrols enableplayercontrols disablecontrol enablecontrol setcontrolsdisabled',
    E('SetPlayerControls', AUTO, AUTO, hazards=['control-lock', 'cinematic'], sev='medium',
      note='captured at PlayerCharacter::SetControlsDisabled. Party-wide fan-out + release is the SetPlayerControls RULE (RulesEngine rewrites Recipient to All, 120 s timeout); without that rule the lock reaches only the origin'))
add('enablefasttravel enablefastravel disablefasttravel', E('SetFastTravelLock', AUTO, AUTO, hazards=['fast-travel'], sev='high',
                                                            note='party-wide fast-travel lock; the FastTravelLock rule only models scene-bound locks'))
add('autosave systemsave save quicksave forcesave playersavegame', E('RequestSave', AUTO, AUTO, hazards=['save'], sev='low', note='scripted save is an EVENT; the host saves'))
add('fadetoblack fadefromblack forceplayercamera forcefirstperson forcethirdperson setcameraon setcamerapos setcamerapov playbink startmovie setplayeraidriven disableallactors',
    E(None, M, M, hazards=['cinematic'], sev='medium', note='camera/cinematic control; local to the player it targets'))
add('imod applyimagespacemodifier removeimagespacemodifier imagespacemodifier setimagespace playsound playsound3d playmusic stopsound pms playmagicshadervisuals clearscreensplatter alwaysshowactorsubtitles',
    E(None, NA, NA, hazards=['cosmetic'], note='cosmetic/local'))
add('forceweather setweather releaseweatheroverride', E('SetWeather', AUTO, AUTO, hazards=['timer'], sev='medium', note='weather is host-authoritative'))
add('setglobaltimemultiplier settimescale', E('SetTimeScale', AUTO, AUTO, hazards=['timer'], sev='medium', note='time scale is host-authoritative'))
add('setgamehour', E('AdvanceTime', AUTO, AUTO, hazards=['timer'], sev='medium', note='clock jump (mapping to AdvanceTime UNVERIFIED)'))
add('setnoradio forceradiostationupdate pipboyradio pipboyradiooff setnpcradio', E(None, M, M, hazards=['radio'], sev='low', note='radio state; no Effect'))
add('setpcexpelled setpccrimeflags payfine payfinethief setcrimegold modcrimegold ignorecrime', E(None, M, M, hazards=['crime', 'player-targeted'], sev='low', note='crime state; no Effect'))
add('quit quitgame exitgame loadgame', E(None, M, M, hazards=['save'], sev='high', note='must never run on a client'))
add('setdisposition', E(None, M, M, sev='low', note='NPC disposition; no Effect'))

# ---- reads (condition functions) -> PartyAwareCond ---------------------------------------------------------------
READS_P = ('getdistance getincell getinsamecell getinworldspace getisreference getactionref getlineofsight getdetected '
           'getdetectionlevel getitemcount getequipped getav getactorvalue getbaseactorvalue haspk hasperk getplayerteammate '
           'getplayerteammatecount getpcfame getpcinfamy getpcmiscstat getpcexpelled getpcisclass getpcissex getpcisrace '
           'getpcinfaction getdisposition getfactionrank getinfaction getplayercontrolsdisabled isincombat getiskiller '
           'iskiller getcurrenttarget getlocked').split()
READ_ENTRY = E('PartyAwareCond', NA, P if COND_WIRED else M, kind='read', hazards=['player-ref-cond'], sev='high',
               note='RulesEngine::reduceCondition (game-free, synccore) reduces a condition over the party, but NO engine hook (Condition::Evaluate / Cmd_Get*) feeds it, so the host evaluates player-bound reads for the host player only')

CLEAR_OBJ = E('SetObjective', AUTO, AUTO, hazards=['quest-state'], sev='medium',
              note='un-displaying an objective: the hook records SetObjective{flags 0}, but the L4 merge rule for objective flags is OR (shadow.h), so a hide never reaches replicas / late joiners; oracle calls it ClearObjective (not in effect.h)')
CLEAR_OBJ['capture'] = P if CLEAR_OBJ['capture'] == S else CLEAR_OBJ['capture']
CLEAR_OBJ['apply'] = P if CLEAR_OBJ['apply'] == S else CLEAR_OBJ['apply']

BUTTON_READ = E(None, NA, M, kind='read', hazards=['menu', 'player-ref-cond'], sev='high',
                note="GetButtonPressed returns the LOCAL player's message-box choice (point-of-no-return confirmations such as Moore's 'are you ready'); the host script needs the party's answer, not the host player's")

QUESTVAR_EV = {'effect': 'SetScriptVar', 'capture': S, 'apply': S, 'kind': 'assign', 'hazards': [], 'severity': 'low',
               'note': 'quest variable: host-authoritative (scope diff, or state polling in coop), applied on clients'}
GLOBAL_EV = {'effect': 'SetGlobal', 'capture': S, 'apply': S, 'kind': 'assign', 'hazards': [], 'severity': 'low',
             'note': 'TESGlobal: host-authoritative (scope diff / state polling)'}
REFVAR_EV = resolve({'effect': 'RefScriptVar', 'capture': AUTO, 'apply': AUTO, 'kind': 'assign', 'hazards': ['npc-script-var'], 'severity': 'low',
                     'note': 'variable of an object/NPC script ("set Ref.var"): RefScriptVar'})
if 'RefScriptVar' not in RECORDED:   # until L5 records RefScriptVar the old ScriptEventList diff only covers quest owners
    REFVAR_EV['capture'] = M
if 'RefScriptVar' not in APPLIED and 'SetScriptVar' in APPLIED:
    REFVAR_EV['apply'] = P

TIMER_READS = {'getsecondspassed': 'timer', 'getdayspassed': 'timer', 'getgamehour': 'timer', 'getcurrenttime': 'timer', 'gettimedead': 'timer', 'getdayssince': 'timer'}

KEYWORDS = {'if', 'else', 'elseif', 'endif', 'begin', 'end', 'set', 'to', 'let', 'return', 'scn', 'scriptname', 'short', 'long',
            'int', 'float', 'ref', 'while', 'endwhile', 'not', 'and', 'or', 'player', 'playerref', 'gamemode', 'menumode',
            'onactivate', 'ontriggerenter', 'ontriggerleave', 'onload', 'onadd', 'ondeath', 'onhit', 'onreset', 'onequip',
            'onunequip', 'ondrop', 'onsell', 'onpackagestart', 'onpackagechange', 'onpackagedone', 'onpackageend',
            'onactorequip', 'onactorunequip', 'onmagiceffecthit', 'onhitwith', 'onopen', 'onclose', 'onunload',
            'onstartcombat', 'oncombatend', 'onsetstage', 'onrelease', 'ontrigger', 'onempty', 'onmurder', 'onknockout'}

PLAYER_TOK = {'player', 'playerref'}
TOK = re.compile(r'"[^"]*"|0[xX][0-9A-Fa-f]+|\d+[A-Za-z_]\w*|[A-Za-z_]\w*|\d+(?:\.\d+)?|\S')
SIMPLE_NAME = re.compile(r'^[a-z_]\w*$')


# ---- capture scope ---------------------------------------------------------------------------------------------
# L5 opens a capture scope ONLY at TESQuest::SetStage (QuestResult) and TESTopicInfo::RunResultScript (DialogueResult)
# (plugin/hooks/engine_hooks.cpp; CaptureHub::record drops everything else and counts it in stats.droppedNoScope).
# Quest GameMode blocks and object scripts (NPC/door/trigger/activator scripts) therefore run "ambient": their world and
# player mutations are not shipped. Exceptions: quest variables and globals are polled from the host
# (enableStatePolling/pollHostState), and a SetStage call opens its own scope even when issued from an ambient script.
AMBIENT_OK = {'set questvar', 'set global', 'setstage'}
SEV_RANK = {'info': 0, 'low': 1, 'medium': 2, 'high': 3, 'blocker': 4}
AMBIENT_FLOOR = {   # minimum severity of a dropped (ambient) mutation, by what it changes
    'disableplayercontrols': 'blocker', 'enableplayercontrols': 'blocker', 'disablecontrol': 'blocker', 'enablecontrol': 'blocker',
    'setobjectivedisplayed': 'high', 'setobjectivecompleted': 'high', 'setobjectivedisplayed(0)': 'high', 'startquest': 'high',
    'stopquest': 'high', 'completequest': 'high', 'failquest': 'high', 'completeallobjectives': 'high', 'rewardxp': 'high',
    'rewardkarma': 'high', 'playerrewardkarma': 'high', 'addreputation': 'high', 'setreputation': 'high', 'modreputation': 'high',
    'addreputationexact': 'high', 'removereputation': 'high', 'additem': 'high', 'removeitem': 'high', 'additemns': 'high',
    'enable': 'medium', 'disable': 'medium', 'kill': 'medium', 'killactor': 'medium', 'resurrect': 'medium', 'resurrectactor': 'medium',
    'moveto': 'medium', 'movetomarker': 'medium', 'movetofade': 'medium', 'movetomarkerwithfade': 'medium', 'setpos': 'medium',
}


def scope_of(where, origin):
    if origin == 'dep':
        return 'object'
    if where == 'quest_script':
        return 'quest-script'
    if where.startswith('stage'):
        return 'stage'
    return 'info'


def apply_scope(ev, scope):
    """Return ev with `scope` set and, for ambient scripts, the capture status downgraded to what L5 really does."""
    ev = dict(ev)
    ev['scope'] = scope
    if scope in ('stage', 'info') or ev['kind'] == 'read' or ev['fn'] in AMBIENT_OK:
        return ev
    if ev['capture'] in ('supported', 'partial'):
        ev['capture'] = M
        ev['hazards'] = sorted(set(ev['hazards']) | {'ambient'})
        floor = AMBIENT_FLOOR.get(ev['fn'])
        if floor and SEV_RANK[floor] > SEV_RANK.get(ev['severity'], 0):
            ev['severity'] = floor
        if ev['fn'] in ('moveto', 'movetomarker', 'movetofade', 'movetomarkerwithfade', 'setpos') and 'player-targeted' in ev['hazards']:
            ev['severity'] = 'blocker'
        ev['note'] = 'AMBIENT (' + scope + ' script, no capture scope open: CaptureHub drops it, droppedNoScope). ' + ev['note']
    return ev


def lookup(fn, subject_is_player):
    e = FN.get(fn)
    if e is None:
        return None
    if subject_is_player and fn in PLAYER_SPLIT:
        return PLAYER_SPLIT[fn]
    return e


def scan_script(src, local_vars, data):
    """Return events [{fn, subject, line, text, conditional, entry}] and unclassified [{fn, line, text}].

    local_vars: lowercase names declared in the script (and the owning quest script). data: common.Data.
    """
    emap = data.edid_map()
    quest_edids = {q['edid'].lower() for q in data.quests if q.get('edid')}
    glob_edids = {g['edid'].lower() for g in data.d.get('globals', []) if g.get('edid')}
    events, unclassified = [], []
    depth = 0
    aliases = set(PLAYER_TOK)
    for n, raw in enumerate(src.replace('\r', '').split('\n'), 1):
        line = common.strip(raw)
        if not line:
            continue
        toks = TOK.findall(line)
        low = [t.lower() for t in toks]
        head = low[0]
        if head in ('begin', 'end') or head in ('short', 'long', 'int', 'float', 'ref', 'scn', 'scriptname', 'string_var', 'array_var'):
            if head == 'end' and len(low) == 1:
                pass
            continue
        if head == 'endif':
            depth = max(0, depth - 1)
            continue
        cond_line = head in ('if', 'elseif', 'while')
        conditional = depth > 0 or head in ('elseif',)
        if head == 'if':
            pass
        # aliasing: set rX to player
        if head == 'set' and len(low) >= 4 and low[2] == 'to' and low[3] in aliases:
            aliases.add(low[1])
        # assignments
        if head == 'set':
            try:
                ti = low.index('to')
            except ValueError:
                ti = -1
            lhs = low[1:ti] if ti > 0 else low[1:2]
            if len(lhs) == 3 and lhs[1] == '.':
                owner, var = lhs[0], lhs[2]
                if owner in quest_edids:
                    events.append(dict(QUESTVAR_EV, fn='set questvar', subject=owner, line=n, text=line[:160], conditional=conditional or depth > 0))
                else:
                    events.append(dict(REFVAR_EV, fn='set refvar', subject=owner, line=n, text=line[:160], conditional=conditional or depth > 0))
            elif len(lhs) == 1:
                nm = lhs[0]
                if nm in local_vars:
                    pass  # local script variable: nothing shared
                elif nm in glob_edids:
                    events.append(dict(GLOBAL_EV, fn='set global', subject=None, line=n, text=line[:160], conditional=conditional or depth > 0))
                else:
                    unclassified.append({'fn': f'set {nm}', 'line': n, 'text': line[:160]})
            rest_start = (ti + 1) if ti > 0 else 2
        else:
            rest_start = 0
        # command / function tokens
        i = rest_start
        player_in_line = any(t in aliases for t in low)
        while i < len(low):
            t = low[i]
            if SIMPLE_NAME.match(t) and not (i > 0 and low[i - 1] == '.' and i > 1 and low[i - 2] in quest_edids):
                subj = low[i - 2] if i >= 2 and low[i - 1] == '.' else None
                is_cmd_pos = (i == rest_start) or (i > 0 and low[i - 1] in ('.', '(', '&', '|', '!', '=', '>', '<', '+', '-', '*', '/', ',')) or True
                # which tokens are function names? those in tables
                subj_player = subj is not None and subj in aliases
                if t in FN:
                    ent = lookup(t, subj_player or (subj is None and t in ('rewardxp', 'rewardkarma', 'playerrewardkarma', 'addreputation', 'setreputation', 'modreputation', 'addperk', 'removeperk', 'addnote', 'removenote', 'addspecialpoints', 'addtagskills')))
                    # args naming the player (e.g. StartCombat player, SetPlayerTeammate player) also make it player-bound
                    if t in ('startcombat', 'sendassaultalarm', 'setplayerteammate') and any(x in aliases for x in low[i + 1:i + 4]):
                        ent = dict(ent, hazards=ent['hazards'] + ['player-targeted'])
                    if t == 'setobjectivedisplayed' and len(low) > i + 3 and low[i + 3] == '0':
                        ent = CLEAR_OBJ
                        t = 'setobjectivedisplayed(0)'
                    if not cond_line:
                        events.append(dict(ent, fn=t, subject=subj, line=n, text=line[:160], conditional=conditional or depth > 0))
                elif t in READS_P or t.startswith(('getpc', 'getplayer')):
                    player_bound = subj_player or t.startswith(('getpc', 'getplayer')) or (subj is None and False)
                    if not player_bound and subj is None:
                        # bare GetX args naming the player (GetDistance player, GetInCell ... player)
                        player_bound = any(x in aliases for x in low[i + 1:i + 3]) and t in ('getdistance', 'getlineofsight', 'getisreference', 'getinsamecell', 'getdetected', 'getdetectionlevel')
                    if player_bound and t not in ('getplayername',):
                        events.append(dict(READ_ENTRY, fn=t, subject=subj, line=n, text=line[:160], conditional=conditional or depth > 0))
                elif t == 'getbuttonpressed':
                    events.append(dict(BUTTON_READ, fn=t, subject=subj, line=n, text=line[:160], conditional=conditional or depth > 0))
                elif t in TIMER_READS:
                    events.append({'fn': t, 'subject': subj, 'line': n, 'text': line[:160], 'conditional': conditional or depth > 0, 'effect': None,
                                   'capture': NA, 'apply': NA, 'kind': 'read', 'hazards': ['timer'], 'severity': 'info',
                                   'note': 'time source (host clock is authoritative; quest scripts only run on the host)'})
            i += 1
        # statement-start unknown commands (reported, never guessed)
        if not cond_line and head not in ('set',):
            idx = 0
            if len(low) >= 3 and low[1] == '.':
                idx = 2
            if idx < len(low):
                c = low[idx]
                if SIMPLE_NAME.match(c) and c not in FN and c not in KEYWORDS and c not in local_vars and c not in quest_edids \
                        and c not in glob_edids and c not in READS_P and c not in TIMER_READS and not c.startswith(('getpc', 'getplayer')) \
                        and c not in emap and c not in ('elseif', 'else', 'endif') and idx == (2 if len(low) >= 3 and low[1] == '.' else 0) and (idx == 2 or c not in emap):
                    unclassified.append({'fn': c, 'line': n, 'text': line[:160]})
        if head in ('if', 'while'):
            depth += 1
        elif head == 'else':
            pass
    return events, unclassified
