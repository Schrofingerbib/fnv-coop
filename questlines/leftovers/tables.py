"""Static tables for the factions questline: synccore Effect vocabulary, L5 capture/apply status, script function map.

L5 status was read from plugin/hooks/engine_hooks.cpp (what is recorded) and plugin/hooks/apply_remote.cpp (what
RemoteApplier handles) and internal lane notes "Round 9" on 2026-10-16. The factions/towns lanes were written against an
older L5: since then SetPlayerControls, CompleteAllObjectives, SetFactionRank, SetPerk, ObjectState, RefAddItem/RefRemoveItem,
RemoveAllItems, AddNote/RemoveNote, AddTopic, SetWeather, SetTimeScale, RequestSave and SpawnRef (capture only) were added. Nothing here was verified in a running game. If L5/L10 moved since, update
EFFECT_STATUS and re-run gen.py; every other number follows.
"""
import re

# docs/protocol.md section 6 (tag order)
EFFECTS = ['SetStage', 'SetObjective', 'SetGlobal', 'EnableRef', 'DisableRef', 'AddItem', 'RemoveItem', 'ModActorValue',
           'ModReputation', 'ModKarma', 'StartQuest', 'StopQuest', 'MoveRef', 'KillActor', 'ResurrectActor',
           'SetScriptVar', 'SetPlayerControls', 'PartyTether', 'SlideshowEnd', 'SaveSuppress', 'CompanionOwner',
           'ForcedDialogue', 'BarrierArrive', 'BarrierRelease', 'SayOnceFlag', 'CompleteQuest', 'CompleteAllObjectives',
           'SetLock', 'SetFactionRank', 'SetPerk', 'SwapTexture', 'SetFastTravelLock', 'FailQuest']
# docs/protocol.md section 6 (v3) lists the 33 above; synccore/include/fnvcoop/effect.h `using Effect = std::variant<...>` now has
# 16 more (appended in this order). protocol.md has not caught up: flagged in the status doc.
EFFECTS_EXTRA = ['SpawnRef', 'DespawnRef', 'ObjectState', 'RefScriptVar', 'RefAddItem', 'RefRemoveItem', 'RemoveAllItems', 'AddTopic',
                 'AddNote', 'RemoveNote', 'SetMapMarker', 'ForceActiveQuest', 'RequestSave', 'SetWeather', 'SetTimeScale', 'AdvanceTime']
EFFECTS_ALL = EFFECTS + EFFECTS_EXTRA

S, P, M = 'supported', 'partial', 'missing'
# effect -> (capture, apply, evidence)
EFFECT_STATUS = {
    'SetStage': (S, S, 'engine_hooks.cpp:196 record(SetStage); apply_remote.cpp:79 (ScriptGate keeps result script off)'),
    'SetObjective': (S, S, 'engine_hooks.cpp:220; apply_remote.cpp:86'),
    'SetGlobal': (S, S, 'engine_hooks.cpp:173 (diff around the capture scope); apply_remote.cpp:94'),
    'EnableRef': (S, S, 'engine_hooks.cpp:278/295; apply_remote.cpp:100'),
    'DisableRef': (S, S, 'engine_hooks.cpp:278/294; apply_remote.cpp:111'),
    'AddItem': (S, S, 'engine_hooks.cpp records player item changes; container/NPC AddItem is RefAddItem'),
    'RemoveItem': (S, S, 'same as AddItem (non-player: RefRemoveItem)'),
    'ModActorValue': (P, S, 'engine_hooks.cpp:405/487; apply uses an inferred vtable call (L5.md: unverified in game)'),
    'ModReputation': (S, S, 'engine_hooks.cpp:254; apply_remote.cpp:151'),
    'ModKarma': (S, S, 'engine_hooks.cpp:265/486; apply_remote.cpp:145'),
    'StartQuest': (S, S, 'engine_hooks.cpp:230; apply_remote.cpp:197'),
    'StopQuest': (S, S, 'engine_hooks.cpp:231; apply_remote.cpp:203'),
    'MoveRef': (S, P, 'engine_hooks.cpp:442-465 captures; apply needs the console exec line (setExecLine), else Unsupported'),
    'KillActor': (S, P, 'engine_hooks.cpp:414; apply needs exec line'),
    'ResurrectActor': (S, P, 'engine_hooks.cpp:422; apply needs exec line'),
    'SetScriptVar': (P, S, 'engine_hooks.cpp:164 polls quest variables (snapshot diff, not per write); apply_remote.cpp:165'),
    'SetPlayerControls': (S, S, 'engine_hooks.cpp h_Controls records at PlayerCharacter::SetControlsDisabled; apply_remote.cpp SetPlayerControls; live-proven controls.lock (L5.md Round 8/9). Rule-level release/timeout (host-effect, 120 s) is not verified here'),
    'PartyTether': (M, M, 'synccore defines the effect; no capture or apply in plugin/hooks'),
    'SlideshowEnd': (M, M, 'no capture or apply'),
    'SaveSuppress': (M, M, 'no capture or apply (L10.md: enforcement pending L5)'),
    'CompanionOwner': (M, M, 'SetPlayerTeammate is not hooked; no apply'),
    'ForcedDialogue': (M, M, 'L8 sink only emits dlg.event; in-game result runner is a stub (L10.md)'),
    'BarrierArrive': (P, P, 'L10 wired a SetStage gate (setStageGate) for barriers; scene-pass detection partial'),
    'BarrierRelease': (P, P, 'same'),
    'SayOnceFlag': (M, M, 'L10.md: SayOnce unsupported at apply'),
    'CompleteQuest': (S, S, 'engine_hooks.cpp:238; apply_remote.cpp:185'),
    'CompleteAllObjectives': (S, S, 'engine_hooks.cpp h_CompleteAll; apply_remote.cpp'),
    'SetLock': (M, M, 'defined in effect.h but unused: Lock/UnLock are captured as ObjectState instead'),
    'SetFactionRank': (S, S, 'engine_hooks.cpp factionCmd (SetFactionRank/AddToFaction/RemoveFromFaction, any actor); apply_remote.cpp runs the console command on the actor. ModFactionRank is blocked, not captured'),
    'SetPerk': (S, S, 'engine_hooks.cpp perkCmd records PLAYER AddPerk/RemovePerk only (NPC/companion perks are not captured); apply_remote.cpp. Recipient routing (shared vs recruiter) is policy, unverified'),
    'SwapTexture': (M, M, 'not hooked'),
    'SetFastTravelLock': (M, M, 'EnableFastTravel not hooked'),
    'FailQuest': (S, S, 'engine_hooks.cpp:336; apply_remote.cpp:191'),
    'SpawnRef': (S, M, 'engine_hooks.cpp records PlaceAtMe/PlaceLeveledActorAtMe (record(sp)); L5.md Round 9: apply stays Unsupported (no place-at-coordinates primitive)'),
    'DespawnRef': (M, M, 'not recorded, not applied'),
    'ObjectState': (S, S, 'engine_hooks.cpp objectStateCmd (Lock/UnLock/SetOpenState/SetDestroyed); apply_remote.cpp runs the console command on the ref'),
    'RefScriptVar': (M, M, 'not recorded (SetScriptVar covers ref owners at apply, quest vars are captured by polling)'),
    'RefAddItem': (S, S, 'engine_hooks.cpp records non-player container / NPC AddItem; apply_remote.cpp'),
    'RefRemoveItem': (S, S, 'engine_hooks.cpp records non-player RemoveItem; apply_remote.cpp'),
    'RemoveAllItems': (S, S, 'engine_hooks.cpp / apply_remote.cpp (non-player containers per L5.md Round 9)'),
    'AddTopic': (S, S, 'engine_hooks.cpp records AddTopic; apply_remote.cpp. SayOnceFlag (per-INFO said state) is still missing'),
    'AddNote': (S, S, 'engine_hooks.cpp / apply_remote.cpp'),
    'RemoveNote': (S, S, 'engine_hooks.cpp / apply_remote.cpp'),
    'SetMapMarker': (M, M, 'not recorded, not applied'),
    'ForceActiveQuest': (M, M, 'not recorded, not applied'),
    'RequestSave': (S, M, 'engine_hooks.cpp records Autosave/SystemSave/ForceSave; L5.md: apply is the session\'s job (no applier branch)'),
    'SetWeather': (S, S, 'engine_hooks.cpp / apply_remote.cpp'),
    'SetTimeScale': (S, S, 'engine_hooks.cpp / apply_remote.cpp'),
    'AdvanceTime': (M, M, 'not recorded, not applied'),
}
assert set(EFFECT_STATUS) == set(EFFECTS_ALL)

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
_m('addtopic', 'AddTopic', 'low', kind='system',
   note='topic unlock captured as AddTopic; the per-INFO said-once state (SayOnceFlag) is still missing')
# --- world refs
_m('enable', 'EnableRef')
_m('disable', 'DisableRef')
_m('setdestroyed', 'ObjectState', 'low', [], 'world', 'destroyed/intact state of a placed object; ObjectState', 'host-world')
_m('setopenstate', 'ObjectState', 'low', [], 'world', 'door/container open state; ObjectState', 'host-world')
_m('setownership', None, 'low', [], 'world', 'ownership flag; no Effect type', 'host-world')
_m('setactorfullname', None, 'low', [], 'world', 'renames an actor (display name only, e.g. VMS02 sheriff/deputy titles); no Effect type', 'host-actor')
_m('activate', None, 'medium', [], 'world', 'scripted activation of an object; host runs it, no Effect type', 'host-world')
_m('markfordelete', None, 'medium', [], 'world', 'permanent removal of a ref; no Effect type (DisableRef approximates)', 'host-world')
_m('placeatme', 'SpawnRef', 'high', ['spawn'], 'world', 'spawns a new ref on the host; SpawnRef is captured but the applier returns Unsupported, so replicas never see it unless NV:MP actor sync covers it', 'host-world')
_m('lock|unlock', 'ObjectState', 'low', [], 'world', 'door/container lock state; ObjectState', 'host-world')
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
_m('additem|additemhealthpercent|additemns', 'RefAddItem', 'low', [], 'actor',
   'item change on a non-player container/NPC; captured as RefAddItem', 'host-actor', cat='ACTOR')
_m('removeitem|removeitemns', 'RefRemoveItem', 'low', [], 'actor',
   'item change on a non-player container/NPC; captured as RefRemoveItem', 'host-actor', cat='ACTOR')
_m('equipitem|unequipitem', None, 'low', [], 'actor', 'NPC equipment; local', 'host-actor', cat='ACTOR')
_m('equipitem|unequipitem', None, 'medium', ['player_target'], 'effect', 'player equipment change; no Effect type', 'player', cat='P-INV')
_m('removeallitems', None, 'medium', ['player_target'], 'effect', 'bulk removal; no Effect type (RemoveItem is per item)', 'player', cat='P-INV')
_m('removeallitems', 'RemoveAllItems', 'low', [], 'actor', 'NPC inventory wipe; RemoveAllItems', 'host-actor', cat='ACTOR')
_m('addnote', 'AddNote', 'low', ['reward', 'player_target'], 'effect', 'note reward; AddNote (owner: rewards go to every player)', 'player', cat='REWARD')
_m('removenote', 'RemoveNote', 'low', ['player_target'], 'effect', 'note removal; RemoveNote', 'player', cat='P-INV')
_m('addperk|removeperk', 'SetPerk', 'high', ['player_target', 'perk'], cat='P-STAT',
   note='perk on the player: captured (SetPerk); owner has not decided shared vs recruiter-only (Recipient routing)')
_m('addperk|removeperk', None, 'low', ['perk'], 'actor', 'perk on a companion NPC; L5 records player perks only', 'host-actor', cat='ACTOR')
_m('setav|setactorvalue|modactorvalue|modav|damageav|damageactorvalue|restoreav|restoreactorvalue|forceav', 'ModActorValue', 'medium',
   ['player_target'], cat='P-STAT', note='player stat change; deltas only (no SetAV)')
_m('setav|setactorvalue|modactorvalue|modav|damageav|damageactorvalue|restoreav|restoreactorvalue|forceav', None, 'low', [],
   'actor', 'NPC stat; host-owned actor', 'host-actor', cat='ACTOR')
# --- factions
_m('addtofaction|removefromfaction|setfactionrank', 'SetFactionRank', 'high', ['faction_change', 'player_target'], cat='S-STAT',
   note='player faction membership is shared (owner decision); captured as SetFactionRank, sharing/Recipient routing unverified')
_m('addtofaction|removefromfaction|setfactionrank', 'SetFactionRank', 'medium', ['faction_change'], 'actor',
   'NPC faction swap changes hostility/AI; captured as SetFactionRank, NPC AI effect on the client unverified', 'host-actor', cat='ACTOR')
_m('setally|setenemy|setignorefriendlyhits', None, 'low', [], 'actor', 'faction relation; local', 'host-actor')
# --- companions / control / travel / save
_m('setplayerteammate', 'CompanionOwner', 'blocker', ['companion'], cat='COMPANION',
   note='companion recruit/dismiss; default owner = recruiter')
_m('disableplayercontrols|enableplayercontrols', 'SetPlayerControls', 'high', ['cinematic'],
   note='control lock captured at SetControlsDisabled and applied; it must reach every party member and the release must be explicit (rule-level, unverified)')
_m('enablefasttravel|enablefastravel', 'SetFastTravelLock', 'medium', ['fast_travel'], note='party-wide fast-travel flag')
_m('autosave', 'RequestSave', 'low', ['save'], note='RequestSave captured; SaveSuppress (block saving during a scene) is still missing and apply is the session\'s job')
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
    ('radio', r'radio(?!\w*parts)', 'radio object / station'),
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
