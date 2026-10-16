"""Script function -> synccore Effect(s) + L5 capture/apply verdict, for the Mr. House questline.

The verdict is DERIVED from the current source tree (l5facts.py: which hooks exist, which Effect types engine_hooks.cpp records, which
Effect types RemoteApplier / the coop layer apply, whether ambient mutations are recorded), not hard-coded, because L4/L5 keep adding
hooks. This table only says what each function IS: which Effect types would carry it, which engine hook captures it, what kind of
thing it is (mode) and how bad a gap is for the House path.

Status vocabulary (same as the other questline lanes):
  supported  an Effect exists, a hook records it on the host, and a client applies it
  partial    captured or applied but not both, or a stated limitation (player subject only ...)
  missing    nothing carries it (or it is dropped because it runs outside a capture scope)
  n/a        read-only query / condition input

mode:  effect (maps to Effect types) | readonly | presentation (sound/anim/UI, no shared state) | hostsim (NPC AI run by the host sim;
reaches clients only through NV:MP actor replication, not synccore) | none (shared state with no Effect at all)

Hazard tags (brief): PLAYER-EFFECT SPAWN COMPANION ESSENTIAL TELEPORT CELL-TRANSITION CINEMATIC MENU TIMER RADIO ENDING KILL-ALL SAVE
FAST-TRAVEL FACTION UI-LOCAL PRESENTATION WORLD-STATE AMBIENT
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import l5facts  # noqa: E402

_FACTS = None


def facts():
    global _FACTS
    if _FACTS is None:
        _FACTS = l5facts.load()
    return _FACTS


def set_facts(f):
    """Tests inject synthetic facts (None = reload from the tree)."""
    global _FACTS
    _FACTS = f


SPEC = {}


def _add(names, effects=(), hook=None, mode='effect', sev='low', tags=(), note='', player_only=False):
    """names: whitespace separated; '_' inside a name stands for a space (scanner pseudo-functions `set global` ...)."""
    for n in names.split():
        SPEC[n.replace('_', ' ')] = dict(effects=list(effects), hook=hook, mode=mode, sev=sev, tags=list(tags), note=note, player_only=player_only)


# ---------------------------------------------------------------------------------------------------- quest state
_add('setstage', ['SetStage'], 'SetStage', sev='info', note='the SetStage hook opens its own capture scope')
_add('startquest', ['StartQuest'], 'QuestSetRunning', sev='info')
_add('stopquest', ['StopQuest'], 'QuestSetRunning', sev='info')
_add('completequest', ['CompleteQuest'], 'QuestSetCompleted', sev='info')
_add('failquest', ['FailQuest'], 'QuestSetFailed', sev='info')
_add('setobjectivedisplayed setobjectivecompleted', ['SetObjective'], 'ObjectiveSetState', sev='info')
_add('completeallobjectives', ['CompleteAllObjectives'], 'QuestCompleteAllObjectives', sev='low')
_add('set_global', ['SetGlobal'], None, sev='info',
     note='TESGlobal diff around the outermost scope + host state poll; per-machine clock globals (GameHour, GameDaysPassed, TimeScale) are not observable (L7 R-UNOBS)')
_add('set_questvar', ['SetScriptVar'], None, sev='low',
     note='quest-var diff around the outermost scope; writes of a continuously running quest script arrive through the host state poll as state, not inside the txn')
_add('set_refvar', ['RefScriptVar'], None, sev='medium', tags=['WORLD-STATE'],
     note='`Set Ref.var` on object/NPC scripts: RefScriptVar is never recorded; e.g. Lucky38MrHouseREF.bSaveGame / VHDColonelMooreREF.bSaveGame handshakes')
_add('setquestdelay', mode='none', sev='low', note='quest tick rate; harmless on the host-only quest script')
_add('addtopic', ['AddTopic'], 'Cmd_AddTopic', sev='low', tags=['MENU'])
_add('addnote', ['AddNote'], 'Cmd_AddNote', sev='low', tags=['PLAYER-EFFECT'])
_add('removenote', ['RemoveNote'], 'Cmd_RemoveNote', sev='low', tags=['PLAYER-EFFECT'])

# ---------------------------------------------------------------------------------------------------- queries (n/a)
_add('getstage getstagedone getquestrunning getquestcompleted getobjectivedisplayed getobjectivecompleted getdead getdisabled getdestroyed '
     'getsecondspassed getlinkedref getself getlocked getsitting getdeadcount isps3 getweaponanimtype getanimaction isweaponout getisid '
     'gethealthpercentage getcurrenttime isspelltarget getisreference getcombattarget getglobalvalue gethasnote getactorvalue getinfaction '
     'ishardcore isplayerinregion isplayermovingintonewspace purgecellbuffers getdestructionstage', mode='readonly', sev='info')
_add('getdistance getincell getinsamecell getinworldspace', mode='readonly', sev='medium', tags=['PLAYER-EFFECT'],
     note='player-relative condition: needs the PartyAwareCond reduction (rules engine reduceCondition); evaluated per machine otherwise')
_add('isactionref getactionref', mode='readonly', sev='medium', tags=['PLAYER-EFFECT'],
     note='activator identity: the host sees a guest as its NV:MP ghost; PartyAwareCond maps it to the initiator')
_add('getpcissex', mode='readonly', sev='low', tags=['ENDING'], note='reads the HOST character only (one Courier)')
_add('getmapmarkervisible', mode='readonly', sev='medium', tags=['ENDING', 'PLAYER-EFFECT'],
     note='discovered-location flag of the host save: ending slides branch on it')
_add('iskiller issneaking isincombat getplayerteammate getpcenemyoffaction getfactionrelation getitemcount getequipped getav '
     'getreputationthreshold gettalkedtopc', mode='readonly', sev='low', tags=['PLAYER-EFFECT'],
     note='reads player state: host-evaluated, collapsed over the party by PartyAwareCond when it matters')
_add('getbuttonpressed menumode', mode='readonly', sev='high', tags=['MENU', 'UI-LOCAL'],
     note='result of the LOCAL message box: only the machine that showed it has a value')
_add('read_clock', mode='readonly', sev='medium', tags=['TIMER'],
     note='GameDaysPassed/GameHour/TimeScale are per-machine clocks; a wait on them is driven by the host clock')

# ---------------------------------------------------------------------------------------------------- refs / world
_add('enable', ['EnableRef'], 'Cmd_Enable', sev='info', tags=['WORLD-STATE'])
_add('disable', ['DisableRef'], 'Cmd_Disable', sev='info', tags=['WORLD-STATE'])
_add('setdestroyed', ['ObjectState'], 'Cmd_SetDestroyed', sev='high', tags=['WORLD-STATE'],
     note='doors, elevators, power-plant stairs and Lucky38MrHouseREF (House dead) change state')
_add('setopenstate', ['ObjectState'], 'Cmd_SetOpenState', sev='low', tags=['WORLD-STATE'])
_add('lock', ['ObjectState'], 'Cmd_Lock', sev='medium', tags=['WORLD-STATE'])
_add('unlock', ['ObjectState'], 'Cmd_UnLock', sev='medium', tags=['WORLD-STATE'])
_add('kill killactor', ['KillActor'], 'Cmd_KillActor', sev='info', tags=['KILL-ALL'])
_add('resurrect', ['ResurrectActor'], 'Cmd_ResurrectActor', sev='info')
_add('placeatme', ['SpawnRef'], 'Cmd_PlaceAtMe', sev='high', tags=['SPAWN'], note='created refs are announced by SpawnRef; the host allocates the stable id')
_add('placeleveledactoratme', ['SpawnRef'], 'Cmd_PlaceLeveledActorAtMe', sev='high', tags=['SPAWN'])
_add('markfordelete', ['DespawnRef'], None, sev='low', tags=['WORLD-STATE'], note='DespawnRef has no producer')
_add('setownership cleardestruction', mode='none', sev='low', tags=['WORLD-STATE'])
_add('activate', mode='hostsim', sev='medium', tags=['WORLD-STATE'],
     note='scripted activation: guests drop it (BLOCK_CMD) and the guest-originated Activate intent is a documented but unimplemented design (L5 round 4)')

# ---------------------------------------------------------------------------------------------------- player-targeted
_add('rewardxp', ['ModActorValue'], 'Cmd_RewardXP', sev='info', tags=['PLAYER-EFFECT'], note='RewardAll: every player receives the full XP (owner decision)')
_add('rewardkarma', ['ModKarma'], 'PlayerRewardKarma', sev='info', tags=['PLAYER-EFFECT'])
_add('addreputation modreputation', ['ModReputation'], 'RepAdd', sev='info', tags=['PLAYER-EFFECT'])
_add('setreputation', ['ModReputation'], 'RepSet', sev='info', tags=['PLAYER-EFFECT'])
_add('modactorvalue', ['ModActorValue'], 'Cmd_ModActorValue', sev='low', tags=['PLAYER-EFFECT'], player_only=True)
_add('forceactorvalue', ['ModActorValue'], 'Cmd_ForceActorValue', sev='low', tags=['PLAYER-EFFECT'], player_only=True)
_add('damageactorvalue', ['ModActorValue'], 'Cmd_DamageActorValue', sev='low', tags=['PLAYER-EFFECT'], player_only=True)
_add('setactorvalue setav', ['ModActorValue'], 'Cmd_SetActorValue', sev='low', tags=['PLAYER-EFFECT'], player_only=True)
_add('restoreactorvalue', mode='none', sev='low', tags=['PLAYER-EFFECT'], note='heals; per-player vitals are NV:MP state, not synccore (no Cmd_RestoreActorValue hook)')
_add('resethealth', mode='none', sev='low', tags=['PLAYER-EFFECT'])
_add('additem', ['AddItem'], 'Cmd_AddItem', sev='info', tags=['PLAYER-EFFECT'],
     note='QuestItemDup/RewardAll route Recipient=All; a non-player subject becomes RefAddItem')
_add('removeitem', ['RemoveItem'], 'Cmd_RemoveItem', sev='info', tags=['PLAYER-EFFECT'],
     note='RemoveItemPerOwner vs quest-item removal from everyone is rule-decided; a non-player subject becomes RefRemoveItem')
_add('additemhealthpercent', ['AddItem'], None, sev='low', tags=['PLAYER-EFFECT'], note='variant of AddItem: not hooked')
_add('removeallitems', ['RemoveAllItems'], 'Cmd_RemoveAllItems', sev='low', tags=['PLAYER-EFFECT'], note='only non-player owners are recorded')
_add('equipitem unequipitem', mode='none', sev='low', tags=['PLAYER-EFFECT'])
_add('addperk', ['SetPerk'], 'Cmd_AddPerk', sev='medium', tags=['PLAYER-EFFECT'])
_add('removeperk', ['SetPerk'], 'Cmd_RemovePerk', sev='medium', tags=['PLAYER-EFFECT'])
_add('addtofaction setfactionrank', ['SetFactionRank'], 'Cmd_AddToFaction', sev='medium', tags=['FACTION'])
_add('removefromfaction', ['SetFactionRank'], 'Cmd_RemoveFromFaction', sev='medium', tags=['FACTION'])
_add('modfactionrank', mode='none', sev='medium', tags=['FACTION'], note='delta-style: BLOCK_CMD only, no Effect recorded (needs the resulting rank)')
_add('removefromallfactions', ['SetFactionRank'], None, sev='medium', tags=['FACTION'], note='not hooked')
_add('addspell removespell dispelallspells', mode='none', sev='low', tags=['PLAYER-EFFECT'])
_add('addachievement', mode='none', sev='low', tags=['PLAYER-EFFECT'],
     note='Steam achievements are per machine; owner decision: GuestLocalRun (rule kind decided, not implemented)')
_add('showmap', mode='none', sev='low', tags=['UI-LOCAL'])

# ---------------------------------------------------------------------------------------------------- teleports / travel / controls / saves
_add('moveto movetomarker', ['MoveRef'], 'Cmd_MoveToMarker', sev='high', tags=['TELEPORT', 'CELL-TRANSITION'],
     note='player subject: MoveRef.partyWarp is never set by the capture (only the rules engine expands PartyWarp, with an injected playerRef) and guests DROP it '
          '(suppress_policy blockMutation has no local-player exception), so object-script teleports such as elevator doors do nothing on a guest')
_add('movetomarkerwithfade', ['MoveRef'], 'Cmd_MoveToMarkerWithFade', sev='high', tags=['TELEPORT', 'CELL-TRANSITION'])
_add('setpos', ['MoveRef'], 'Cmd_SetPos', sev='low', tags=['TELEPORT'], note='same-cell only on apply')
_add('disableplayercontrols enableplayercontrols', ['SetPlayerControls'], 'PlayerSetControlsDisabled', sev='high', tags=['CINEMATIC'],
     note='captured at PlayerCharacter::SetControlsDisabled (both commands end there); the coop layer applies it coarsely (any disable locks all seven classes)')
_add('enablefasttravel', ['SetFastTravelLock'], None, sev='high', tags=['FAST-TRAVEL'],
     note='Cmd_EnableFastTravel is located but not hooked; battle start disables fast travel for everybody')
_add('autosave', ['RequestSave'], 'Cmd_Autosave', sev='high', tags=['SAVE'])
_add('systemsave', ['RequestSave'], 'Cmd_SystemSave', sev='high', tags=['SAVE'])
_add('setinchargen', mode='none', sev='high', tags=['CINEMATIC', 'ENDING'], note='slideshow/chargen UI state of the local player')

# ---------------------------------------------------------------------------------------------------- dialogue / UI / presentation
_add('showmessage', mode='none', sev='high', tags=['MENU', 'UI-LOCAL'],
     note='message text and buttons appear only on the machine that ran the script (the host for quest/INFO scripts); buttoned boxes need an initiator-routed UI effect')
_add('showbartermenu', mode='none', sev='medium', tags=['MENU', 'UI-LOCAL'], note='service menu belongs to the initiator')
_add('startconversation', ['ForcedDialogue'], None, sev='high', tags=['CINEMATIC'], note='NPC-initiated conversation: must be routed to a chosen initiator')
_add('say sayto', ['ForcedDialogue'], None, sev='medium', tags=['CINEMATIC'], note='SayTo drives the whole ending narration chain (NarratorScript SayToDone blocks)')
_add('settalkingactivatoractor', mode='presentation', sev='low', tags=['PRESENTATION'])
_add('swaptexture', ['SwapTexture'], None, sev='high', tags=['ENDING', 'PRESENTATION'],
     note='every ending slide is a SwapTexture on OnScreenSlideRef; quest/INFO scripts do not run on guests so guests would see no slides')
_add('playsound playsound3d playmusic playbink pipboyradiooff applyimagespacemodifier removeimagespacemodifier imod cios playidle playgroup '
     'stoplook look setsecuritronexpression setnpcradio startradioconversation', mode='presentation', sev='low', tags=['PRESENTATION'],
     note='cosmetic; quest/INFO scripts run on the host only, so guests miss it unless an object script plays it locally')

# ---------------------------------------------------------------------------------------------------- NPC AI / combat (host-owned actors)
_add('startcombat stopcombat stopcombatalarmonactor sendassaultalarm forceflee setghost setrestrained resetai evaluatepackage '
     'addscriptpackage removescriptpackage setcombatstyle setcs setignorefriendlyhits ignorecrime setessential', mode='hostsim', sev='low', tags=['WORLD-STATE'],
     note='no Effect: the host sim runs it; clients see the result only through NV:MP actor replication (unverified); StartCombat/StopCombat/AddScriptPackage/RemoveScriptPackage are blocked on guests')
_add('setenemy setally', mode='none', sev='medium', tags=['FACTION', 'WORLD-STATE'],
     note='world-level faction relation (SetAlly Lucky38Faction playerFaction in VMQHouseFail, SetEnemy PlayerFaction VCaesarsLegionFaction at battle start): no hook, no Effect')
_add('setplayerteammate', ['CompanionOwner'], None, sev='medium', tags=['COMPANION'])


def lookup(fn):
    return SPEC.get(fn)


SCOPE_FREE = {'setstage', 'set global', 'set questvar'}   # not bound to an enclosing capture scope
AMBIENT_KINDS = ('quest', 'object', 'driver')
SWAP_NONPLAYER = {'additem': ('RefAddItem', 'Cmd_AddItem'), 'removeitem': ('RefRemoveItem', 'Cmd_RemoveItem')}
AMBIENT_NOTE = ('AMBIENT: this statement runs outside any capture scope (quest-script block / object script), so CaptureHub::record() drops it '
                '(droppedNoScope); only a SetStage it contains opens a scope. NV:MP world sync may still carry the visible result (unverified)')
GUEST_BLOCK = {'addscriptpackage': 'AddScriptPackage', 'removescriptpackage': 'RemoveScriptPackage', 'startcombat': 'StartCombat',
               'stopcombat': 'StopCombat', 'activate': 'ActivateCmd'}


def resolve(fn, player=False, kind=None, facts_=None):
    """Verdict for one function in one context. kind: quest|object|driver|stage|info-begin|info-end|None."""
    f = facts_ or facts()
    s = SPEC[fn]
    effects, hook, mode = list(s['effects']), s['hook'], s['mode']
    sev, tags, note = s['sev'], list(s['tags']), s['note']
    if mode == 'readonly':
        return dict(effect=None, status='n/a', capture='read-only', apply='read-only', sev=sev, tags=tags, note=note)
    if fn in SWAP_NONPLAYER and not player:
        effects, hook = [SWAP_NONPLAYER[fn][0]], SWAP_NONPLAYER[fn][1]
        sev = 'medium' if sev in ('info', 'low') else sev
        note = 'non-player subject (NPC/container inventory) becomes ' + effects[0]
    if fn in ('enable', 'disable') and player:
        return dict(effect=None, status='n/a', capture='player subject: not a world toggle', apply='-', sev='info', tags=[], note='')
    if fn == 'removeallitems' and player:
        effects, hook, mode = [], None, 'none'
        note = 'player subject is not captured (RemoveAllItems records non-player owners only)'
    if s['player_only'] and not player:
        return dict(effect=None, status='missing', capture='non-player subject: not captured (NPC vitals are NV:MP actor state)', apply='none',
                    sev='low', tags=tags, note='')
    if mode in ('none', 'presentation', 'hostsim') or not effects:
        why = {'none': 'no Effect type carries it', 'presentation': 'presentation only, no shared state', 'hostsim': 'no Effect; host-owned NPC sim'}.get(mode, 'no Effect type')
        st = 'partial' if mode == 'hostsim' else 'missing'
        cap = f'no ({why})'
        blk = GUEST_BLOCK.get(fn)
        if mode == 'hostsim' and blk and blk in f['block_only']:
            cap += '; blocked on guests'
        return dict(effect=None, status=st, capture=cap, apply='none', sev=sev, tags=tags, note=note)
    produced = all(e in f['producers'] for e in effects)
    hooked = hook is None or hook in f['hooks']
    ok_cap = produced and hooked
    ok_app = all(e in f['apply_engine'] or e in f['apply_coop'] for e in effects)
    name = '/'.join(effects)
    if ok_cap:
        cap_txt = f'yes ({hook or "diff/poll"})'
    elif produced and not hooked:
        cap_txt = f'no: hook {hook} not installed'
    else:
        cap_txt = f'no: nothing records {name} in engine_hooks.cpp'
    app_txt = 'yes' if ok_app else f'no: no apply branch for {name} (RemoteApplier / coop layer)'
    status = 'supported' if ok_cap and ok_app else ('partial' if ok_cap or ok_app else 'missing')
    if status == 'supported' and s['player_only']:
        status = 'partial'
        cap_txt += '; player subject only'
    if fn in ('moveto', 'movetomarker', 'movetomarkerwithfade'):
        if player:
            if status == 'supported':
                status = 'partial'
            cap_txt += '; MoveRef.partyWarp is never set, and a guest drops a local-player MoveTo'
        else:
            sev, tags = 'info', ['TELEPORT']
            note = 'actor/object subject: captured as MoveRef and applied by console (live-verified round 8)'
    scoped = hook is not None and fn not in SCOPE_FREE
    if scoped and kind in AMBIENT_KINDS and status in ('supported', 'partial') and f['ambient_recorded'] is False:
        was = status
        status = 'missing'
        if fn in ('moveto', 'movetomarker', 'movetomarkerwithfade') and player:
            sev = 'high'
        elif sev in ('info', 'low'):
            sev = 'medium'
        tags.append('AMBIENT')
        note = AMBIENT_NOTE + f' (would be {was} inside a scope)'
        cap_txt = 'no: ambient (outside a capture scope)'
    return dict(effect=name, status=status, capture=cap_txt, apply=app_txt, sev=sev, tags=tags, note=note)
