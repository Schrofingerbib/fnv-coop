"""Script function -> synccore Effect type + L5 capture/apply status (as of the repo state this was written against).

Sources of truth (read when this table is reviewed):
  * synccore/include/fnvcoop/effect.h                     which Effect types exist
  * plugin/hooks/engine_addrs.h + engine_hooks.cpp        which commands/engine setters are hooked (CAPTURE)
  * plugin/hooks/apply_remote.{h,cpp}, plugin/coop/*.cpp  which effects have an engine apply (APPLY)
  * internal lane notes rounds 4-8                           "block-only" hooks record NOTHING (suppression gate only)
  * synccore/include/fnvcoop/rules.h                      rule kinds with/without a session effect

Status vocabulary (both for capture and apply)
  supported   hooked/applied, unit-tested (live-proven rounds are noted)
  partial     works for some subjects/arguments only, or only one half exists
  missing     no hook / no engine apply / no Effect type
  host-local  deliberately not replicated: runs only on the host (NPC AI, audio, camera); relies on NV:MP actor sync (L6)
  n/a         read-only expression, nothing to replicate (replicated state is read instead)
"""
import re

STATUS_ORDER = {'supported': 0, 'n/a': 0, 'host-local': 1, 'partial': 2, 'missing': 3}
SEV_ORDER = {'none': 0, 'info': 1, 'low': 2, 'medium': 3, 'high': 4, 'critical': 5}
PLAYER_WORDS = {'player', 'playerref'}


def worst(a, b):
    return a if STATUS_ORDER[a] >= STATUS_ORDER[b] else b


def overall(capture, apply_):
    w = worst(capture, apply_)
    return {'supported': 'supported', 'n/a': 'supported', 'host-local': 'host-local', 'partial': 'partial', 'missing': 'missing'}[w]


def E(effect, cap, app, sev, hazards=(), note=''):
    return {'effect': effect, 'capture': cap, 'apply': app, 'severity': sev, 'hazards': list(hazards), 'note': note}


# fn (lowercase) -> descriptor; entries that depend on the subject/arguments use RULES below instead
TABLE = {}


def _t(names, d):
    for n in names.split():
        TABLE[n] = d


# --- quest state ---------------------------------------------------------------------------------------------------
_t('setstage', E('SetStage', 'supported', 'supported', 'none', (), 'live-proven (L5 rounds 6-8)'))
_t('setobjectivedisplayed setobjectivecompleted', E('SetObjective', 'supported', 'supported', 'none'))
_t('startquest stopquest', E('StartQuest/StopQuest', 'supported', 'supported', 'none'))
_t('completequest', E('CompleteQuest', 'supported', 'supported', 'none'))
_t('failquest', E('FailQuest', 'supported', 'supported', 'none', (), 'TESQuest::SetFailed hook + FailQuest effect (L5 round 8)'))
_t('completeallobjectives', E('CompleteAllObjectives', 'missing', 'missing', 'medium', (),
                              'effect type exists and has a JSON codec; no capture hook and RemoteApplier returns Unsupported'))
_t('resetquest', E(None, 'missing', 'missing', 'medium', (), 'no Effect type; clears stage/objective flags, only a baseline force-set could undo it'))
_t('addtopic', E(None, 'missing', 'missing', 'low', (), 'topic-known flag; SetTopicKnown only proposed in dialogue-design.md section 8'))
_t('setquestobject', E(None, 'missing', 'missing', 'low', (), 'quest-item flag on a form; no Effect type'))
_t('setquestdelay', E(None, 'host-local', 'host-local', 'info', (), 'quest tick rate; quest scripts only run on the host'))
_t('forceactivequest setactivequest', E(None, 'missing', 'missing', 'low', (), 'per-player Pip-Boy tracking'))
_t('set', E('SetGlobal', 'supported', 'supported', 'none', (), 'TESGlobal diff around the outermost scope (M confidence)'))
_t('setquestvar', E('SetScriptVar', 'supported', 'supported', 'none', (), 'quest-var diff (L5 round 5); ref-owner vars are apply-only'))
_t('setglobalvalue', E('SetGlobal', 'supported', 'supported', 'none'))

# --- world / refs ----------------------------------------------------------------------------------------------------
_t('enable disable', E('EnableRef/DisableRef', 'supported', 'supported', 'none', (),
                       'state-change-only capture: a rerun on an already-disabled ref records nothing (L5 round 8 gap 1)'))
_t('markfordelete', E(None, 'missing', 'missing', 'medium', ('DELETE',), 'permanent ref removal, no Effect type'))
_t('setdestroyed', E(None, 'missing', 'missing', 'low', (), 'destruction state of a ref, no Effect type'))
_t('activate', E(None, 'partial', 'missing', 'medium', ('GUEST_ACTIVATION',),
                 'script Activate is hooked on guests only as an observer (L5 round 7); no effect, no host-side replay'))
_t('lock unlock', E('SetLock', 'missing', 'missing', 'medium', ('DOOR_STATE',),
                    'Cmd_Lock/Cmd_UnLock are block-only hooks (round 4): they suppress on guests but record nothing; SetLock has no applier'))
_t('setopenstate', E(None, 'missing', 'missing', 'low', ('DOOR_STATE',), 'door open state'))
_t('setownership', E(None, 'missing', 'missing', 'low', (), 'ownership of a ref'))
_t('damageobject', E(None, 'missing', 'missing', 'low', (), 'object health'))
_t('setcellpublicflag', E(None, 'missing', 'missing', 'low', (), 'cell flag'))
_t('setactorfullname', E(None, 'missing', 'missing', 'low', (), 'display name'))
_t('placeatme', E(None, 'missing', 'missing', 'high', ('SPAWN',),
                  'no Effect type: a spawn done on the host exists only through NV:MP actor sync; a replayed script would double-spawn'))
_t('imod applyimagespacemodifier', E(None, 'host-local', 'host-local', 'low', ('CINEMATIC',), 'screen effect only the host player sees'))
_t('playsound playsound3d', E(None, 'host-local', 'host-local', 'info', (), 'audio'))
_t('entertrigger', E(None, 'host-local', 'host-local', 'info', (), 'fires a trigger volume script on the host'))
_t('addachievement', E(None, 'missing', 'missing', 'low', ('PLAYER_REWARD',), 'Steam achievement; per-player, nothing replicated'))
_t('showmap', E(None, 'missing', 'missing', 'low', ('MENU',), 'map marker discovery is per-player Pip-Boy state'))

# --- NPC AI / animation: host-local by design -----------------------------------------------------------------------
_t('evp resetai addscriptpackage removescriptpackage stopcombat stoplook look playgroup playidle setrestrained setcombatstyle '
   'setalert forceflee setsecuritronexpression resethealth cios scaonactor sendassaultalarm ignorecrime setunconscious '
   'pushactoraway follow stopwaiting evaluatepackage setoutofrange setactorstate',
   E(None, 'host-local', 'host-local', 'info', (), 'NPC AI/animation runs on the host; replicated by NV:MP actor sync (L6)'))
_t('startcombat', E(None, 'host-local', 'host-local', 'info', (), 'hooked block-only on guests; combat state left to NV:MP'))
_t('setghost setessential', E(None, 'host-local', 'host-local', 'low', ('ESSENTIAL',),
                              'NPC flag set on the host only; how NV:MP resolves damage on a guest is unverified'))
_t('setignorefriendlyhits', E(None, 'host-local', 'host-local', 'low', (), 'NPC flag'))
_t('resetinventory', E(None, 'host-local', 'host-local', 'low', (), 'NPC inventory reset, host-owned actor'))
_t('say', E(None, 'host-local', 'host-local', 'info', ('RADIO_OR_BARK',), 'bark/voice line; spectators rely on L6 Say events (unverified)'))

# --- kills -----------------------------------------------------------------------------------------------------------
_t('kill killactor', E('KillActor', 'supported', 'supported', 'none', ('KILL',), 'console-line apply, live-proven (L5 round 8)'))
_t('resurrect resurrectactor', E('ResurrectActor', 'supported', 'supported', 'none'))

# --- rewards / player state ---------------------------------------------------------------------------------------------
_t('rewardxp', E('ModActorValue(XP)', 'supported', 'supported', 'none', ('PLAYER_REWARD',), 'routing to every player needs the RewardAll rule'))
_t('rewardkarma playerrewardkarma', E('ModKarma', 'supported', 'supported', 'none', ('KARMA',)))
_t('addreputation setreputation addreputationexact removereputation modreputation',
   E('ModReputation', 'supported', 'supported', 'none', ('REPUTATION',), 'TESReputation::Add/Set/AddExact/Remove hooked'))
_t('addnote removenote', E(None, 'missing', 'missing', 'medium', ('PLAYER_REWARD',), 'Pip-Boy note; per-player, no Effect type'))
_t('showmessage messageex', E(None, 'missing', 'missing', 'medium', ('MENU',), 'UI message (may carry choices): shown only on the machine that ran the script'))
_t('showbartermenu showmenu showdialogmenu', E(None, 'missing', 'missing', 'medium', ('MENU',), 'per-player UI; initiator only (dialogue-design.md 7.2)'))
_t('openteammatecontainer', E(None, 'missing', 'missing', 'low', ('MENU', 'COMPANION'), 'companion inventory UI'))
_t('setplayerteammate', E('CompanionOwner', 'missing', 'missing', 'high', ('COMPANION',),
                          'no hook on Cmd_SetPlayerTeammate; effect exists in synccore with no applier'))
_t('removeperk addperk', E('SetPerk', 'missing', 'missing', 'high', ('PERK',),
                           'Cmd_AddPerk/RemovePerk are block-only hooks (round 4): suppressed on guests, never recorded; SetPerk has no applier'))
_t('setpcenemyoffaction setpcfactionenemy setpcfactionally setpcfactionneutral clearfactionplayerenemyflag',
   E(None, 'missing', 'missing', 'high', ('FACTION_RELATION',), 'the player own standing with a faction (shared by owner decision); no hook, no Effect type'))
_t('systemsave autosave save quicksave', E('SaveSuppress', 'missing', 'partial', 'low', ('SAVE',),
                                           'SaveSuppress exists as session state; script saves are not hooked'))
_t('disableplayercontrols enableplayercontrols disablecontrol enablecontrol',
   E('SetPlayerControls', 'missing', 'partial', 'high', ('CINEMATIC', 'CONTROLS'),
     'not captured from scripts (only the rules engine / baseline lock use it); applier realises a coarse all-controls flag'))
_t('enablefasttravel', E('SetFastTravelLock', 'missing', 'missing', 'medium', ('FAST_TRAVEL',),
                         'effect exists, no capture hook, no engine apply'))
_t('additem removeitem', E('AddItem/RemoveItem', 'supported', 'supported', 'none'))
_t('additemns additemhealthpercent removeitemns', E('AddItem/RemoveItem', 'missing', 'supported', 'low', ('PLAYER_REWARD',),
                                                    'Cmd_AddItemHealthPercent/AddItemNS/RemoveItemNS are different engine commands from the hooked Cmd_AddItem/Cmd_RemoveItem'))
_t('addspell removespell', E(None, 'missing', 'missing', 'low', ('PLAYER_REWARD',), 'spell/ability grant; per-player when the subject is the player'))
_t('settalkingactivatoractor', E(None, 'host-local', 'host-local', 'info', ('RADIO_OR_BARK',), 'radio/announcer voice routing'))
_t('getincell getitemcount getav getactorvalue getdistance getinsamecell getisreference',
   E(None, 'n/a', 'n/a', 'none', (), 'bare read at statement start (parse artefact of a multi-line condition); counted at expression level'))
_t('equipitem unequipitem', E(None, 'missing', 'missing', 'low', (), 'equip state'))
_t('removealltypeditems removeallitems', E(None, 'missing', 'missing', 'medium', ('PLAYER_INVENTORY',), 'bulk removal, no hook'))
_t('restoreactorvalue restoreav', E('ModActorValue', 'missing', 'partial', 'low', (), 'RestoreAV is not among the hooked AV commands'))
_t('modav modactorvalue forceav forceactorvalue setav setactorvalue damageav damageactorvalue',
   E('ModActorValue', 'supported', 'supported', 'none', (), 'player target: delta by before/after; apply path inferred from Cmd_ModActorValue (unverified in game)'))
_t('startconversation', E('ForcedDialogue', 'missing', 'missing', 'high', ('FORCED_DIALOGUE', 'CINEMATIC'), 'effect type exists; no capture, L8 playback not wired'))
_t('sayto', E('ForcedDialogue', 'missing', 'missing', 'high', ('FORCED_DIALOGUE',), 'Cmd_SayTo located (0x5C9100) but not hooked'))
_t('moveto movetomarker movetomarkerwithfade setpos setangle',
   E('MoveRef', 'supported', 'partial', 'high', ('TELEPORT',), 'capture + console apply live-proven for single refs; PartyWarp (all party members) needs RulesEngine playerRef and is inactive without it'))
_t('setenemy setally setfactionreaction modfactionreaction',
   E(None, 'host-local', 'host-local', 'info', (), 'AI relation between actors/factions'))
_t('addtofaction removefromfaction setfactionrank modfactionrank removefromallfactions',
   E('SetFactionRank', 'missing', 'missing', 'info', (), 'Cmd_* block-only hooks; no recording, no applier'))

# --- expression level ---------------------------------------------------------------------------------------------------
_t('getstage getstagedone getquestrunning getquestcompleted getobjectivedisplayed getobjectivecompleted getquestvariable getglobalvalue',
   E(None, 'n/a', 'n/a', 'none', (), 'reads replicated quest state'))
_t('getsecondspassed', E(None, 'n/a', 'n/a', 'none', ('TIMER',), 'quest timers run only where the quest script runs (host)'))
_t('getcurrenttime getdayspassed getdayssince gettimedead getgamehour',
   E(None, 'n/a', 'n/a', 'low', ('TIMER',), 'game clock must be host-authoritative; clients drift unless the clock is replicated (unverified)'))
_t('getdead getdisabled getlocked getisid getisreference getlineofsight getcurrenttarget isincombat getinfaction getfactionrank gethasnote getdetected',
   E(None, 'n/a', 'n/a', 'none', (), 'actor/ref state read on the host'))

PREF_EXPR = {
    'getdistance': ('min-over-party', 'high'), 'getincell': ('entry:all-in,exit:any-in', 'high'),
    'getinsamecell': ('any', 'medium'), 'getinworldspace': ('any', 'medium'), 'isactionref': ('initiator', 'high'),
    'getactionref': ('initiator', 'high'), 'iskiller': ('any-party-member', 'high'), 'getisreference': ('initiator', 'medium'),
    'getplayerteammate': ('initiator', 'medium'), 'getitemcount': ('initiator', 'medium'), 'getequipped': ('initiator', 'medium'),
    'getav': ('initiator', 'medium'), 'getactorvalue': ('initiator', 'medium'), 'getdetected': ('any', 'medium'),
    'player-ref': ('party-collapsed (per call)', 'medium'), 'getisplayablerace': ('initiator', 'low'),
    'getpcmiscstat': ('initiator', 'low'), 'getpcfame': ('shared', 'low'), 'getpcinfamy': ('shared', 'low'),
}


def describe_expr(fn, text):
    """Descriptor for an expression-level (condition/read) use of the player reference."""
    red, sev = PREF_EXPR.get(fn, ('party-collapsed (per call)', 'medium'))
    hz = ['PLAYER_REF_COND']
    if fn in ('isactionref', 'getactionref'):
        hz.append('GUEST_ACTIVATION')
    return E('PartyAwareCond (%s)' % red, 'n/a', 'missing', sev, hz,
             'synccore RulesEngine::reduceCondition exists (game-free); no engine hook evaluates script/condition reads over the party')


def _has_player_arg(text):
    return any(w in PLAYER_WORDS for w in re.findall(r'[A-Za-z_]+', text.lower()))


def describe_stmt(s, quest_vars, known_quests):
    """Descriptor for one statement dict (lib.statements). Subject/argument-dependent cases are refined here."""
    fn = s['fn']
    subj = (s['subject'] or '').lower()
    text = s['text']
    d = TABLE.get(fn)
    player_subj = subj in PLAYER_WORDS
    if d is None:
        return None
    d = dict(d, hazards=list(d['hazards']))
    if fn == 'setquestvar':
        # `set Quest.var` is a quest var (supported); `set Ref.var` is a ref script var (apply-only)
        m = re.match(r'\s*set\s+([A-Za-z_]\w*)\.', text, re.I)
        if m and m.group(1).lower() not in known_quests:
            d.update(capture='missing', apply='supported', note='ref/object script variable: apply exists (round 7), no capture (quest vars only)',
                     severity='low')
        return d
    if fn in ('additem', 'additemns', 'additemhealthpercent', 'removeitem', 'removeitemns'):
        if player_subj and fn in ('additem', 'removeitem'):
            return E('AddItem/RemoveItem', 'supported', 'supported', 'none', ('PLAYER_REWARD', 'PLAYER_INVENTORY'),
                     'recipient routing (AddItem Recipient=All / RemoveItem per owner) needs the QuestItemDup/RewardAll/RemoveItemPerOwner rules')
        if player_subj:
            return E('AddItem/RemoveItem', 'missing', 'supported', 'low', ('PLAYER_REWARD', 'PLAYER_INVENTORY'),
                     'the NS/HealthPercent variants are separate engine commands that are not hooked')
        return E('AddItem/RemoveItem', 'missing', 'partial', 'low', (),
                 'container/NPC target: capture of non-player AddItem is not recorded (L5 unmapped().itemOnNonPlayer); NPC inventories are host-owned')
    if fn in ('modav', 'modactorvalue', 'forceav', 'forceactorvalue', 'setav', 'setactorvalue', 'damageav', 'damageactorvalue',
              'restoreav', 'restoreactorvalue'):
        if not player_subj:
            return E(None, 'host-local', 'host-local', 'info', (), 'NPC actor value, host-owned actor')
        if fn.startswith('restore'):
            return d
        return d
    if fn in ('removealltypeditems', 'removeallitems') and not player_subj:
        return E(None, 'host-local', 'host-local', 'low', (), 'NPC inventory, host-owned actor')
    if fn in ('addtofaction', 'removefromfaction', 'setfactionrank', 'modfactionrank', 'removefromallfactions'):
        if player_subj:
            return E('SetFactionRank', 'missing', 'missing', 'high', ('FACTION',),
                     'player faction membership is SHARED by owner decision; Cmd_* are block-only hooks (nothing recorded), SetFactionRank has no applier')
        return E('SetFactionRank', 'missing', 'missing', 'low', ('FACTION',), 'NPC faction membership (host-owned actor)')
    if fn in ('setenemy', 'setally') and 'playerfaction' in text.lower():
        return E(None, 'host-local', 'missing', 'medium', ('FACTION_RELATION',),
                 'relation against PlayerFaction is written on the host (host-owned NPCs act on it via NV:MP); the guest faction table and any '
                 'non-host-owned NPC never see it; no Effect type (unverified in game)')
    if fn in ('sayto', 'startconversation'):
        if not _has_player_arg(text) and not player_subj:
            return E(None, 'host-local', 'host-local', 'info', (), 'NPC-to-NPC line, played by the host NPCs')
        return d
    if fn in ('showmessage', 'messageex') and 'endgamewarning' in text.lower():
        return E(None, 'missing', 'missing', 'high', ('MESSAGE_CHOICE', 'POINT_OF_NO_RETURN'),
                 'endgame warning (VEndGameWarningMsg*): a modal Yes/No prompt that starts the final battle; shown and answered only where '
                 'the script runs (host), with no party agreement')
    if fn in ('moveto', 'movetomarker', 'movetomarkerwithfade', 'setpos', 'setangle'):
        if player_subj:
            return E('MoveRef(party-warp)', 'supported', 'partial', 'high', ('TELEPORT', 'PLAYER_TELEPORT'),
                     'player teleport: only the host player moves unless PartyWarp is active (RulesEngine needs playerRef); other players are not moved')
        return E('MoveRef', 'supported', 'supported', 'none', ('TELEPORT',), 'ref/NPC move: captured, console-line apply live-proven')
    if fn == 'enablefasttravel':
        arg = re.findall(r'enablefasttravel\s+(\d)', text, re.I)
        if arg and arg[0] == '0':
            d['severity'] = 'high'
            d['hazards'].append('FAST_TRAVEL_LOCK_ON')
        return d
    if fn in ('setenemy', 'setally', 'startcombat', 'stopcombat') and _has_player_arg(text) and fn == 'startcombat':
        d['note'] += '; target is the player (combat vs the guest player left to NV:MP)'
    return d


GAP_STATUSES = ('missing', 'partial')


def is_gap(d):
    return overall(d['capture'], d['apply']) in GAP_STATUSES


# --- script event blocks fired by the player ---------------------------------------------------------------------------
BLOCK_NOTE = ('the block fires on the machine whose player caused it; on a guest the Guest gate drops its mutators and nothing '
              'tells the host (guest-originated triggers are only a design in L5.md round 4, not built)')


def describe_block(block, arg):
    """Descriptor for `begin <block> <arg>` blocks that are triggered by the player. None = not player-triggered."""
    b, a = block.lower(), arg.lower().strip()
    pl = a in PLAYER_WORDS
    if b in ('onadd', 'ondrop', 'onequip', 'onunequip') and pl:
        return E('(guest event)', 'missing', 'missing', 'high', ('GUEST_EVENT', 'PLAYER_INVENTORY_EVENT'),
                 'item enters/leaves THE PLAYER inventory (loot, trade, drop): not a script command, so no capture; ' + BLOCK_NOTE)
    if b == 'ontriggerenter' and pl:
        return E('(guest event)', 'missing', 'missing', 'high', ('GUEST_EVENT', 'GUEST_ACTIVATION'),
                 'trigger volume entered by the player; ' + BLOCK_NOTE)
    if b == 'onactivate' and pl:
        return E('(guest event)', 'partial', 'missing', 'high', ('GUEST_EVENT', 'GUEST_ACTIVATION'),
                 'activation by the player; guests only get an observer (L5 round 7); ' + BLOCK_NOTE)
    if b in ('onhit', 'onstartcombat', 'ondeath') and pl:
        return E('(guest event)', 'missing', 'missing', 'medium', ('GUEST_EVENT', 'KILL_ATTRIBUTION'),
                 'hit/combat/kill attributed to THE PLAYER ref; a guest attacker is a puppet actor, not the host player ref (unverified); '
                 + BLOCK_NOTE)
    return None


SCAN_BUTTON = E('ShowMessageChoice', 'missing', 'missing', 'high', ('MESSAGE_CHOICE',),
                'GetButtonPressed: the script waits for a button on a modal ShowMessage; only the machine that ran the script (host) can '
                'answer, a guest activator (elevator floor, endgame prompt) can not')

SCAN_TIMESKIP = E('SetGlobal', 'supported', 'partial', 'medium', ('TIME_SKIP',),
                  'set GameHour: captured as a SetGlobal (global diff) but whether each engine clock follows it, and what the party experiences '
                  'during the fade, is unverified; a scripted time skip must apply to every player')
