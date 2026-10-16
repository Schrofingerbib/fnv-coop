"""Static tables for the factions questline: synccore Effect vocabulary, L5 capture/apply status, script function map.

L5 status was read from plugin/hooks/engine_hooks.cpp (what is recorded) and plugin/hooks/apply_remote.cpp (what
RemoteApplier handles) on 2026-10-16. Nothing here was verified in a running game. If L5/L10 moved since, update
EFFECT_STATUS and re-run gen.py; every other number follows.
"""
import re

# docs/protocol.md section 6 (tag order)
EFFECTS = ['SetStage', 'SetObjective', 'SetGlobal', 'EnableRef', 'DisableRef', 'AddItem', 'RemoveItem', 'ModActorValue',
           'ModReputation', 'ModKarma', 'StartQuest', 'StopQuest', 'MoveRef', 'KillActor', 'ResurrectActor',
           'SetScriptVar', 'SetPlayerControls', 'PartyTether', 'SlideshowEnd', 'SaveSuppress', 'CompanionOwner',
           'ForcedDialogue', 'BarrierArrive', 'BarrierRelease', 'SayOnceFlag', 'CompleteQuest', 'CompleteAllObjectives',
           'SetLock', 'SetFactionRank', 'SetPerk', 'SwapTexture', 'SetFastTravelLock', 'FailQuest']

S, P, M = 'supported', 'partial', 'missing'
# effect -> (capture, apply, evidence)
EFFECT_STATUS = {
    'SetStage': (S, S, 'engine_hooks.cpp:196 record(SetStage); apply_remote.cpp:79 (ScriptGate keeps result script off)'),
    'SetObjective': (S, S, 'engine_hooks.cpp:220; apply_remote.cpp:86'),
    'SetGlobal': (S, S, 'engine_hooks.cpp:173 (diff around the capture scope); apply_remote.cpp:94'),
    'EnableRef': (S, S, 'engine_hooks.cpp:278/295; apply_remote.cpp:100'),
    'DisableRef': (S, S, 'engine_hooks.cpp:278/294; apply_remote.cpp:111'),
    'AddItem': (P, S, 'engine_hooks.cpp:384 records player item changes only; L5.md: container/NPC AddItem not captured'),
    'RemoveItem': (P, S, 'same as AddItem'),
    'ModActorValue': (P, S, 'engine_hooks.cpp:405/487; apply uses an inferred vtable call (L5.md: unverified in game)'),
    'ModReputation': (S, S, 'engine_hooks.cpp:254; apply_remote.cpp:151'),
    'ModKarma': (S, S, 'engine_hooks.cpp:265/486; apply_remote.cpp:145'),
    'StartQuest': (S, S, 'engine_hooks.cpp:230; apply_remote.cpp:197'),
    'StopQuest': (S, S, 'engine_hooks.cpp:231; apply_remote.cpp:203'),
    'MoveRef': (S, P, 'engine_hooks.cpp:442-465 captures; apply needs the console exec line (setExecLine), else Unsupported'),
    'KillActor': (S, P, 'engine_hooks.cpp:414; apply needs exec line'),
    'ResurrectActor': (S, P, 'engine_hooks.cpp:422; apply needs exec line'),
    'SetScriptVar': (P, S, 'engine_hooks.cpp:164 polls quest variables (snapshot diff, not per write); apply_remote.cpp:165'),
    'SetPlayerControls': (M, M, 'no hook records DisablePlayerControls; RemoteApplier has no handler (L10 lock exists only for baseline sync)'),
    'PartyTether': (M, M, 'synccore defines the effect; no capture or apply in plugin/hooks'),
    'SlideshowEnd': (M, M, 'no capture or apply'),
    'SaveSuppress': (M, M, 'no capture or apply (L10.md: enforcement pending L5)'),
    'CompanionOwner': (M, M, 'SetPlayerTeammate is not hooked; no apply'),
    'ForcedDialogue': (M, M, 'L8 sink only emits dlg.event; in-game result runner is a stub (L10.md)'),
    'BarrierArrive': (P, P, 'L10 wired a SetStage gate (setStageGate) for barriers; scene-pass detection partial'),
    'BarrierRelease': (P, P, 'same'),
    'SayOnceFlag': (M, M, 'L10.md: SayOnce unsupported at apply'),
    'CompleteQuest': (S, S, 'engine_hooks.cpp:238; apply_remote.cpp:185'),
    'CompleteAllObjectives': (M, M, 'not recorded, not applied'),
    'SetLock': (M, M, 'Lock/Unlock not hooked'),
    'SetFactionRank': (M, M, 'AddToFaction/SetFactionRank not hooked; L10.md: 772 SetFactionRank effects unsupported at apply'),
    'SetPerk': (M, M, 'AddPerk/RemovePerk not hooked'),
    'SwapTexture': (M, M, 'not hooked'),
    'SetFastTravelLock': (M, M, 'EnableFastTravel not hooked'),
    'FailQuest': (S, S, 'engine_hooks.cpp:336; apply_remote.cpp:191'),
}
assert set(EFFECT_STATUS) == set(EFFECTS)

SEV_ORDER = ['blocker', 'high', 'medium', 'low', 'info']

# fn (lowercase classify name) -> dict(effect, sev, hazards, kind, note, scope)
#   kind: effect | read | ui | world | actor | cosmetic | system
FN = {}


def _m(names, effect=None, sev='info', hazards=(), kind='effect', note='', scope=None, cat=None):
    for n in names.split('|'):
        FN[(n.lower(), cat)] = dict(effect=effect, sev=sev, hazards=list(hazards), kind=kind, note=note, scope=scope)


# --- quest state (fully handled)
_m('setstage', 'SetStage')
_m('setobjectivedisplayed|setobjectivecompleted', 'SetObjective')
_m('set global|setglobalvalue', 'SetGlobal')
_m('set questvar', 'SetScriptVar', 'low', note='quest variable; captured by polling, host order')
_m('startquest', 'StartQuest')
_m('stopquest', 'StopQuest')
_m('completequest', 'CompleteQuest')
_m('failquest', 'FailQuest')
_m('next_quest', 'StartQuest', note='stage entry chains to a follow-up quest')
_m('setquestdelay', None, 'low', kind='system', note='quest processing delay; local')
_m('addtopic', None, 'medium', kind='system',
   note='topic unlock is per-save dialogue state; no Effect type (SayOnceFlag is the closest). Needs an AddTopic effect or baseline sync')
# --- world refs
_m('enable', 'EnableRef')
_m('disable', 'DisableRef')
_m('setdestroyed', None, 'medium', ['radio'], 'world', 'destroyed/intact state of a placed object; no Effect type, not in baseline either', 'host-world')
_m('setopenstate', None, 'medium', [], 'world', 'door/container open state; no Effect type', 'host-world')
_m('setownership', None, 'low', [], 'world', 'ownership flag; no Effect type', 'host-world')
_m('activate', None, 'medium', [], 'world', 'scripted activation of an object; host runs it, no Effect type', 'host-world')
_m('markfordelete', None, 'medium', [], 'world', 'permanent removal of a ref; no Effect type (DisableRef approximates)', 'host-world')
_m('placeatme', None, 'high', ['spawn'], 'world', 'spawns a new ref on the host; replicas never learn of it unless NV:MP actor sync covers it', 'host-world')
_m('lock|unlock', 'SetLock', 'medium', [], 'world', 'door/container lock state', 'host-world')
_m('playsound|playsound3d|playmusic', None, 'low', [], 'cosmetic', 'audio only; plays on the host machine only', None)
_m('playgroup|playidle', None, 'low', [], 'cosmetic', 'animation; actor is host-owned', 'host-actor')
# --- player rewards and stats
_m('rewardxp', 'ModActorValue', 'info', ['reward', 'player_target'], note='every player gets the full XP (owner decision)')
_m('rewardkarma', 'ModKarma', 'info', ['karma_rep', 'player_target'], note='karma is shared (owner decision)')
_m('addreputation', 'ModReputation', 'info', ['karma_rep', 'player_target'], note='faction reputation is shared (owner decision)')
_m('additem|additemhealthpercent|additemns', 'AddItem', 'low', ['reward', 'player_target'], cat='REWARD',
   note='player reward / quest item: Recipient=All via RewardAll / QuestItemDup')
_m('removeitem|removeitemns', 'RemoveItem', 'low', ['player_target'], cat='P-INV',
   note='hand-in removes from the handing player (RemoveItemPerOwner)')
_m('additem|additemhealthpercent|additemns|removeitem|removeitemns', None, 'low', [], 'actor',
   'item change on a non-player container/NPC; L5 does not capture it', 'host-actor', cat='ACTOR')
_m('equipitem|unequipitem', None, 'low', [], 'actor', 'NPC equipment; local', 'host-actor', cat='ACTOR')
_m('equipitem|unequipitem', None, 'medium', ['player_target'], 'effect', 'player equipment change; no Effect type', 'player', cat='P-INV')
_m('removeallitems', None, 'medium', ['player_target'], 'effect', 'bulk removal; no Effect type (RemoveItem is per item)', 'player', cat='P-INV')
_m('removeallitems', None, 'low', [], 'actor', 'NPC inventory wipe; local', 'host-actor', cat='ACTOR')
_m('addnote', None, 'medium', ['reward', 'player_target'], 'effect', 'note reward; no Effect type (owner: rewards go to every player)', 'player', cat='REWARD')
_m('removenote', None, 'low', ['player_target'], 'effect', 'note removal; no Effect type', 'player', cat='P-INV')
_m('addperk|removeperk', 'SetPerk', 'high', ['player_target', 'perk'], cat='P-STAT',
   note='perk on the player; owner has not decided shared vs recruiter-only')
_m('addperk|removeperk', 'SetPerk', 'low', ['perk'], 'actor', 'perk on a companion NPC', 'host-actor', cat='ACTOR')
_m('setav|setactorvalue|modactorvalue|modav|damageav|damageactorvalue|restoreav|restoreactorvalue|forceav', 'ModActorValue', 'medium',
   ['player_target'], cat='P-STAT', note='player stat change; deltas only (no SetAV)')
_m('setav|setactorvalue|modactorvalue|modav|damageav|damageactorvalue|restoreav|restoreactorvalue|forceav', None, 'low', [],
   'actor', 'NPC stat; host-owned actor', 'host-actor', cat='ACTOR')
# --- factions
_m('addtofaction|removefromfaction|setfactionrank', 'SetFactionRank', 'blocker', ['faction_change', 'player_target'], cat='S-STAT',
   note='player faction membership is shared (owner decision) but no hook captures it')
_m('addtofaction|removefromfaction|setfactionrank', 'SetFactionRank', 'medium', ['faction_change'], 'actor',
   'NPC faction swap changes hostility/AI; no capture', 'host-actor', cat='ACTOR')
_m('setally|setenemy|setignorefriendlyhits', None, 'low', [], 'actor', 'faction relation; local', 'host-actor')
# --- companions / control / travel / save
_m('setplayerteammate', 'CompanionOwner', 'blocker', ['companion'], cat='COMPANION',
   note='companion recruit/dismiss; default owner = recruiter')
_m('disableplayercontrols|enableplayercontrols', 'SetPlayerControls', 'blocker', ['cinematic'],
   note='control lock must reach every party member and release via an explicit effect')
_m('enablefasttravel|enablefastravel', 'SetFastTravelLock', 'medium', ['fast_travel'], note='party-wide fast-travel flag')
_m('autosave', 'SaveSuppress', 'low', ['save'], note='host owns save points')
# --- movement
_m('moveto|movetomarker', 'MoveRef', 'medium', ['teleport'], cat='MOVE',
   note='MoveRef; subject decides: player => PartyWarp, NPC => host-owned MoveRef')
# --- kill / resurrect
_m('kill|killactor', 'KillActor', 'medium', ['kill'], note='host-run kill; captured at command level')
_m('resurrect', 'ResurrectActor', 'low')
# --- dialogue / UI
_m('showmessage', None, 'medium', ['menu'], 'ui', 'message box on the machine that runs the script (host); other players see nothing', 'ui')
_m('showbartermenu', None, 'medium', ['menu'], 'ui', 'barter menu: needs the L8 service sub-session (7.2), initiator only', 'ui')
_m('startconversation|sayto|say', 'ForcedDialogue', 'medium', ['cinematic'], 'ui',
   'NPC-initiated speech; sayto/say are barks, StartConversation forces a scene', 'ui')
_m('speech_challenge', None, 'medium', ['skill_check'], 'ui', 'speech/skill challenge: initiator-evaluated (L8 per-initiator check)', 'ui')
# --- NPC AI (host-simulated)
_m('addscriptpackage|removescriptpackage|evaluatepackage|setcombatstyle|startcombat|stopcombat|stopcombatalarmonactor|'
   'setrestrained|setghost|setalert', None, 'low', [], 'actor', 'AI package / combat state; actor is host-owned (NV:MP motion)', 'host-actor')

# reads (conditions and script reads): evaluated on the host over the party
READ_PREFIX = ('get', 'isin')
READ_EXTRA = {'player-ref', 'isincombat'}


def lookup(f):
    """Return mapping dict for a classify finding, or None if unmapped."""
    fn = f['fn'].lower()
    cat = f['cat']
    if cat == 'P-COND' or f['fn'] == 'player-ref' or fn.startswith(READ_PREFIX) or fn in READ_EXTRA:
        sev = 'medium' if cat in ('P-COND', 'P-REF') else 'info'
        hz = []
        if fn in ('getplayerteammate',):
            hz = ['companion']
        if fn in ('getsecondspassed', 'getcurrenttime'):
            hz = ['timer']
        if fn in ('getitemcount', 'getequipped', 'getactorvalue', 'getpcissex', 'getissex', 'getpcissex'):
            hz = ['initiator_eval']
        return dict(effect=None, sev='low' if cat == 'Q-STATE' or fn.startswith('gets') else sev, hazards=hz, kind='read',
                    note='read/condition; evaluated by the host (initiator or party-reduced via PartyAwareCond)', scope=None)
    for key in ((fn, cat), (fn, None)):
        if key in FN:
            return FN[key]
    return None


# --- raw-source hazard scan (names from the brief); regex over comment-stripped lines
HAZARD_PATTERNS = [
    ('essential', r'\bset(essential|protected)\b', 'essential/protected flag toggle'),
    ('fast_travel', r'\b(setplayercantravel|enablefast\w*travel|disablefast\w*travel)\b', 'fast-travel lock'),
    ('menu', r'\b(show\w*menu|showmessage|showracemenu|showrecipemenu|showslotmachine\w*|showblackjack\w*|showcaravan\w*)\b', 'UI menu / message'),
    ('timer', r'\b(gamedayspassed|getsecondspassed|getcurrenttime|gamehour)\b', 'real-time or game-time timer'),
    ('ending', r'\b(endgame|onscreenslide|swaptexture)\b', 'ending / slideshow'),
    ('radio', r'radio', 'radio object / station'),
    ('cinematic', r'\b(disableplayercontrols|startconversation|imod|applyimagespacemodifier|fadeinout\w*|playbink\w*)\b', 'scripted scene / cutscene'),
    ('teleport', r'\b(moveto|movetomarker|centeroncell\w*|centeronworld)\b', 'teleport'),
    ('spawn', r'\bplaceatme\b', 'spawn'),
    ('companion', r'\b(setplayerteammate|getplayerteammate\w*)\b', 'companion'),
    ('save', r'\b(autosave|quicksave|forcesave)\b', 'save point'),
    ('perk', r'\b(addperk|removeperk)\b', 'perk'),
    ('faction_change', r'\b(addtofaction|removefromfaction|setfactionrank)\b', 'faction membership change'),
    ('karma_rep', r'\b(rewardkarma|addreputation|setreputation)\b', 'karma / reputation'),
]
HAZARD_RE = [(n, re.compile(p, re.I), d) for n, p, d in HAZARD_PATTERNS]
KILL_RE = re.compile(r'\b(kill|killactor|startcombat)\b', re.I)
