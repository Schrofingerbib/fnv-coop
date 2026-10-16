"""rules.patch.json + policy_review.md (prose with data-backed numbers and verified citations).

Patch semantics (apply_patch): starts from docs/quest-policies/rules.json, then
  amend[EDID]      {remove:[kinds], add:[rule...], set_params:{kind:params}}   quest must already be in rules.json
  new_quests[EDID] {tier, questline, curated, rules:[rule...]}                  quest must NOT be in rules.json
Rule kinds are limited to the 16 in tools/coverage/rules.py. Anything else lives under `proposals` and is never applied.
Every cell/marker/quest EditorID inside params must resolve in quests.json or forms.json.gz (case-insensitive).
"""
import copy
import json

import cmdtable as ct
import facts
import scope

KINDS = ['PartyTether', 'PartyWarp', 'SetPlayerControls', 'Barrier', 'PerPlayerScene', 'SlideshowEnd', 'CompanionOwner',
         'SaveSuppress', 'FastTravelLock', 'PartyAwareCond', 'QuestItemDup', 'RewardAll', 'RemoveItemPerOwner',
         'ShareKarmaRep', 'InitiatorDialogue', 'ExcludeFromGate']
QUESTLINE = 'Side: Freeside / Strip'


def R(kind, **params):
    return dict(kind=kind, params=params, origin='curated')


MED_PARAMS = {}

# ---- amendments to quests already in rules.json ------------------------------------------------------------------------
AMEND = {
    # doctor-treatment template (Julie Farkas / Alex Richards / Lt Markland): the patient is locked, nobody else
    'VMS24': dict(remove=['SetPlayerControls', 'PartyTether'], add=[R('PerPlayerScene')], why='F4', ev=['julie']),
    'VMS47': dict(remove=['SetPlayerControls', 'PartyTether'], add=[R('PerPlayerScene')], why='F4', ev=['julie']),
    'VFSHighTimes': dict(remove=['SetPlayerControls', 'PartyTether'], add=[R('PerPlayerScene')], why='F4', ev=['julie']),
    'VMS31': dict(remove=['SetPlayerControls', 'PartyTether'], add=[R('PerPlayerScene')], why='F4', ev=['alex']),
    'VMS36': dict(remove=['SetPlayerControls', 'PartyTether'], add=[R('PerPlayerScene')], why='F4', ev=['markland']),
    'VMS30': dict(add=[R('PerPlayerScene'), R('RemoveItemPerOwner'), R('ShareKarmaRep'), R('InitiatorDialogue'),
                       R('PartyAwareCond', funcs={'GetDistance': 'min-over-party', 'IsActionRef': 'initiator'})],
                  why='F1,F4', ev=['alex', 'pay30']),
    'VMS45': dict(add=[R('InitiatorDialogue'), R('PartyAwareCond', funcs={'IsActionRef': 'initiator'})],
                  why='F9', ev=['moore_noise']),
    # Caesar's surgery: the AutoDoc / Usanagi lock the activator (per player); the Fort Lucius/Caesar teleports, saves and
    # endgame message boxes belong to the Legion endgame, not to this quest (F9)
    'VMS35': dict(add=[R('PerPlayerScene'), R('CompanionOwner', owner='recruiter'), R('InitiatorDialogue'),
                       R('PartyAwareCond', funcs={'IsActionRef': 'initiator', 'GetPlayerTeammate': 'owner'})],
                  why='F3,F7,F9', ev=['autodoc', 'usanagi', 'fort_endgame']),
    # I Hear You Knocking: repairing the howitzer is a message box (F3); the SetPlayerControls/PartyWarp/SaveSuppress/
    # FastTravelLock rules came from the Fort Lucius Legion-endgame script
    'VMS32': dict(remove=['SetPlayerControls', 'PartyWarp', 'SaveSuppress', 'FastTravelLock'], why='F3,F9', ev=['fort_endgame', 'howitzer']),
    # elevators are free-roam: one rider, not the party
    'vfreeformlucky38': dict(remove=['PartyWarp'], add=[R('PerPlayerScene')], why='F5', ev=['elevator']),
    # Kings' Gambit: the Hoover Dam endgame lines from Colonel Moore's script are not part of this quest
    'VMS29': dict(remove=['PartyWarp', 'SaveSuppress'], set_params={'SetPlayerControls': dict(scope='all', release='host-effect', timeout_s=120, masks=[])},
                  why='F9', ev=['moore_noise']),
    'VMS42': dict(add=[R('RemoveItemPerOwner')], why='F1', ev=['c4']),
    'VMS38': dict(add=[R('RemoveItemPerOwner'), R('InitiatorDialogue')], why='F1', ev=['pay38']),
    'VFreeformTheStreet01': dict(add=[R('RemoveItemPerOwner'), R('PerPlayerScene')], why='F1,F6', ev=['pay_street']),
    # Omertas / Gomorrah (rules.json files these under Boulder City; gameplay is Gomorrah)
    'VMS21': dict(add=[R('PerPlayerScene'), R('InitiatorDialogue'), R('ShareKarmaRep'),
                       R('PartyAwareCond', funcs={'IsActionRef': 'initiator', 'GetActionRef': 'initiator'})],
                  why='F5,F6', ev=['gom_door']),
    'VMS21a': dict(add=[R('InitiatorDialogue'), R('RemoveItemPerOwner')], why='F1', ev=['pay21a']),
}

PAC = lambda **f: R('PartyAwareCond', funcs=f)  # noqa: E731

# ---- quests that have no entry in rules.json yet -------------------------------------------------------------------
NEW = {
    'VFreeformFreeside': ['T3', [R('RemoveItemPerOwner'), R('RewardAll'), R('ShareKarmaRep'), R('InitiatorDialogue'),
                                 PAC(IsActionRef='initiator', GetAV='initiator')], 'F1'],
    'vDialogueGomorrah': ['T3', [R('RemoveItemPerOwner'), R('RewardAll'), R('ShareKarmaRep'), R('InitiatorDialogue'), R('PerPlayerScene'),
                                 PAC(IsActionRef='initiator', GetActionRef='initiator')], 'F1,F6'],
    'vDialogueGomorrahGeneric': ['T3', [R('RemoveItemPerOwner'), R('RewardAll'), R('InitiatorDialogue'), R('PerPlayerScene')], 'F1,F6'],
    'vDialogueMedicalClinic': ['T3', [R('RemoveItemPerOwner'), R('PerPlayerScene'), R('InitiatorDialogue'), R('ShareKarmaRep')], 'F1,F4'],
    'vDialogueCasinoCashier': ['T2', [R('RemoveItemPerOwner'), R('PerPlayerScene'), R('InitiatorDialogue')], 'F1,F6'],
    'vCasinoCompsVikkiAndVance': ['T1', [R('PerPlayerScene')], 'F6'],
    'vCasinoCompsAtomicWrangler': ['T2', [R('PerPlayerScene')], 'F6'],
    'vCasinoCompsGomorrah': ['T1', [R('PerPlayerScene')], 'F6'],
    'VCasinoCompsUltraLuxe': ['T1', [R('PerPlayerScene')], 'F6'],
    'vCasinoCompsTheTops': ['T1', [R('PerPlayerScene')], 'F6'],
    'VDialogueTops': ['T2', [R('PerPlayerScene'), R('InitiatorDialogue')], 'F6'],
    'VTopsSecurity': ['T3', [R('PerPlayerScene'), PAC(IsActionRef='initiator')], 'F6'],
    'VUltraLuxeSecurity': ['T3', [R('PerPlayerScene'), PAC(IsActionRef='initiator')], 'F6'],
    'VMSTopsTalentPool': ['T2', [R('RewardAll'), R('ShareKarmaRep'), R('RemoveItemPerOwner'), R('InitiatorDialogue')], 'F1'],
    'VFSDebtCollector': ['T1', [R('RewardAll'), R('InitiatorDialogue')], 'F1'],
    'VFSAtomicPimp': ['T1', [R('RewardAll'), R('InitiatorDialogue')], 'F1'],
    'vCCLeftMyHeart': ['T2', [R('RewardAll'), R('InitiatorDialogue')], 'F1'],
    'vDialogueTheStrip': ['T2', [R('RemoveItemPerOwner'), R('RewardAll'), R('InitiatorDialogue')], 'F1'],
    'vFreeformTheStreet02': ['T2', [R('ShareKarmaRep'), R('RewardAll'), R('InitiatorDialogue')], 'F1'],
    'VDialogueArcadeGannon': ['T3', [R('CompanionOwner', owner='recruiter'), R('InitiatorDialogue'), PAC(GetPlayerTeammate='owner')], 'F7'],
    'vDialogueLily': ['T3', [R('CompanionOwner', owner='recruiter'), R('InitiatorDialogue')], 'F7'],
    'Lilysmedicinetimer': ['T3', [R('CompanionOwner', owner='recruiter')], 'F7'],
    'LilysPsychoticBreaks': ['T3', [R('CompanionOwner', owner='recruiter')], 'F7'],
}

# new kind + engine/contract proposals (never applied by apply_patch)
PROPOSALS = dict(
    kinds=[dict(
        kind='PerPlayerVars',
        params={'quest': 'EDID of a quest', 'vars': ['quest-script variable names that hold per-player state']},
        justification=('Casino state is stored in SHARED quest variables: bans and winnings tiers (vCasinoComps*.Banned/SmallFry/BigWinner/'
                       'Whale), frisk/weapon-confiscation state (vDialogueGomorrah.iInGomorrah/bGunsAllowed/iWeaponAlert, VDialogueTops.'
                       'PlayerFrisked), room rental (VFreeformTheStreet01.RoomRented). SetScriptVar is last-writer-wins and shared, so one '
                       'player winning big would ban or comp the whole party, and one player being frisked would mark everybody frisked. '
                       'PerPlayerScene cannot express persistent per-slot variables and no existing kind keys state by player.'),
        semantics=('Listed vars replicate as (slot, value); a read inside a script that runs for slot S sees S value; the host applies '
                   'SetScriptVar writes to the originating slot only. Bans/frisk then stay per player, shared quest state stays shared.'),
        interim='none: until implemented casino bans/frisks follow the host player only')],
    kind_uses={
        'vCasinoCompsAtomicWrangler': ['SmallFry', 'BigWinner', 'Whale', 'Banned', 'doonce'],
        'vCasinoCompsVikkiAndVance': ['bFloorManagerIncoming', 'SmallFry', 'BigWinner', 'Whale', 'Banned'],
        'vCasinoCompsGomorrah': ['bFloorManagerIncoming', 'SmallFry', 'BigWinner', 'Whale', 'Banned'],
        'VCasinoCompsUltraLuxe': ['bFloorManagerIncoming', 'SmallFry', 'BigWinner', 'Whale', 'Banned'],
        'vCasinoCompsTheTops': ['bFloorManagerIncoming', 'SmallFry', 'BigWinner', 'Whale', 'Banned'],
        'vDialogueGomorrah': ['iInGomorrah', 'bGunsAllowed', 'iWeaponAlert', 'iPlayerRemFaction', 'iTrespassAlert', 'bRecptIntimidate'],
        'VDialogueTops': ['PlayerFrisked', 'Unarmed', 'Holdout'],
    },
    engine_changes=[
        dict(id='E1', what='PerPlayerScene must have semantics: Default recipients of AddItem/RemoveItem/ModActorValue/SetPerk/SetPlayerControls '
                           'in txns whose cause quest has the rule resolve to Origin (today the kind is loaded and counted only: rules.cpp '
                           'handled-set, no code path).', owner='L4'),
        dict(id='E2', what='RemoveItemPerOwner must be purchase-atomic: when a txn contains a RemoveItem rewritten to Origin, AddItem/SetPerk/'
                           'ModActorValue on the player in the same txn with Default recipient must also go to Origin (today a DialogueResult '
                           'cause resolves them to All via policy.cpp isQuestRewardCause, so a purchase serves everybody).', owner='L4'),
        dict(id='E3', what='Follow-up script runs after a dialogue (Julie/Alex/Markland GameMode treatment blocks, Variable01 handshake) must '
                           'inherit the dialogue initiator as txn origin; today they run on the host with origin=host, so the patient is '
                           'never the origin when a guest is treated.', owner='L4+L8'),
        dict(id='E4', what='Finish protocol section 12 for the activation scripts listed in policy_review F2: acting-player resolution (L5 choke point) '
                           'and ghost refID (L6), pickups and TriggerEnter forwarding. v1 runs the interaction as the host player, so `player.MoveTo/RemoveItem/'
                           'RemoveAllTypedItems` inside forwarded OnActivate scripts (elevators, AutoDoc, frisk) still act on the host.', owner='L5+L6+L10'),
        dict(id='E5', what='GetButtonPressed message boxes must be shown to the activating player and the button routed to the host; '
                           'with forwarded activation the box opens on the host screen and only the host can answer it (the section 12 decision covers Fort -> Dam only).',
             owner='L5+L8'),
        dict(id='E6', what='Capture/apply still missing for commands used here (cmdtable.py, re-checked against engine_hooks.cpp / apply_remote.cpp by '
                           'run_tests.py): SetPlayerTeammate (CompanionOwner), Say/SayTo/StartConversation (ForcedDialogue), RestoreAV/ResetHealth, '
                           'EnableFastTravel, RemoveAllTypedItems and the transfer half of RemoveAllItems, RemoveFromAllFactions, SetPCEnemyOfFaction/'
                           'ClearFactionPlayerEnemyFlag/SendAssaultAlarm, ShowMap, MarkForDelete, SwapTexture, NPC-subject AddPerk, ref-script variables '
                           '(RefScriptVar). Apply side: SpawnRef and RequestSave have no RemoteApplier path.', owner='L5'),
        dict(id='E9', what='Casino frisk lockers are single shared containers (TopsWeaponLockerREF, TopsCompanionLocker1/2REF, vGOMPlayerEquipmentContainer, '
                           'vGOMCompanion1/2EquipmentContainer): two frisked players would mix their weapons. Extend the coordinator decision on per-slot player '
                           'storage (protocol section 12) to these lockers.', owner='L10+L4'),
        dict(id='E8', what='Player-filtered event blocks (`Begin OnHit player`, `OnDeath player`, `OnStartCombat player`) fire only for the host '
                           'player actor. A guest ghost\'s hit or kill must dispatch them too (any party member counts as "player"), otherwise '
                           'kill attribution, Kings-hate-player, theft/assault reactions and fail-on-kill quests only ever see the host.',
             owner='L5+L6'),
        dict(id='E7', what='forms.json.gz lacks the marker/cell EditorIDs rules.json already names (11 PartyWarp params unresolved) and the '
                           'cells scripts reference via GetInCell (FreesideAtomicWrangler, ULMembersOnly, OVWestSewers03, ...): extend the '
                           'forms table so the rules resolver can map them.', owner='L3'),
    ],
)

# scratch vars that must never replicate (VolatileKeys text format, `var <form> <name>`); validated against quest script vars
VOLATILE = {
    'vDialogueCasinoCashier': ['PlayerChipAmount', 'PlayerChipValue', 'NCR100', 'NCR20', 'NCR5', 'Legion100', 'Legion4'],
}


def evidence_specs():
    """citation key -> list of (script, needle). A citation is valid when the needle is found in a statement line of the script."""
    return {
        'julie': [('VFSJulieFarkasSCRIPT', 'disablePlayerControls'), ('VFSJulieFarkasSCRIPT', 'player.ResetHealth'),
                  ('VFSJulieFarkasSCRIPT', 'StartConversation player')],
        'alex': [('VCFHAlexRichardsScript', 'disablePlayerControls'), ('VCFHAlexRichardsScript', 'setstage VDoctors 10')],
        'markland': [('VBSLtMarklandSCRIPT', 'disablePlayerControls'), ('VBSLtMarklandSCRIPT', 'setstage VDoctors 10')],
        'elevator': [('Lucky38ElevatorDoorScript', 'player.MoveTo Lucky38BasementMarker'), ('Lucky38ElevatorDoorScript', 'GetButtonPressed')],
        'moore_noise': [('VHDColonelMooreSCRIPT', 'SystemSave'), ('VHDColonelMooreSCRIPT', 'Player.moveto vHDOliverTeleportMarker'),
                        ('VHDColonelMooreSCRIPT', 'DisablePlayerControls 1 1 1 1 0 1 1')],
        'autodoc': [('VMS35AutoDocScript', 'DisablePlayerControls'), ('VMS35AutoDocScript', 'KillActor FortCaesarRef'),
                    ('VMS35AutoDocScript', 'GetButtonPressed')],
        'fort_endgame': [('FortLuciusScript', 'VEndGameWarningMsgLegion'), ('FortLuciusScript', 'Set VHDBattleController.iBattleState to 2'),
                         ('FortLuciusScript', 'player.MoveTo VHDLegateStartMarker'), ('FortCaesarScript', 'player.MoveTo VHDLegateStartMarker'),
                         ('FortLuciusScript', 'SystemSave')],
        'howitzer': [('FortHowitzerScript', 'GetButtonPressed'), ('FortHowitzerScript', 'SetStage VMS32 30')],
        'c4': [('VMS42MicroclineRockScript', 'player.RemoveItem WeapNVC4PlasticExplosive 3')],
        'gom_door': [('GomorrahHotelDoorScript', 'Player.Moveto vGomPortMarker'), ('GomorrahHotelDoorScript', 'disableplayercontrols'),
                     ('GomorrahHotelDoorScript', 'player.addtofaction OmertaFaction')],
        'pay30': [('info:128FD5', 'Player.RemoveItem Caps001 50')],
        'pay38': [('info:134FBE', 'Player.RemoveItem Caps001 20')],
        'pay_street': [('info:11951A', 'Player.RemoveItem Caps001 20')],
        'pay21a': [],
        'pacer_death': [('VFSPacerScript', 'begin OnDeath player'), ('VFSPacerScript', 'SetEnemy KingsFaction PlayerFaction')],
        'janet_death': [('NellisJanetSCRIPT', 'begin OnDeath player')],
        'julie_death': [('VFSJulieFarkasSCRIPT', 'BEGIN OnDeath player'), ('VFSJulieFarkasSCRIPT', 'setenemy playerfaction FollowersOfTheApocalypseFaction')],
        'trooper_death': [('VMS30InjuredTrooper01Script', 'begin OnDeath Player'), ('VMS30InjuredTrooper01Script', 'SetStage VMS30 210')],
        'weathers_death': [('NVCCMrsWeathersSCRIPT', 'begin onDeath player'), ('NVCCMrsWeathersSCRIPT', 'setenemy playerFaction VCaesarsLegionCCoveFaction')],
        'vc': [('vCasinoCompsAtomicWranglerScript', 'GetCasinoWinningsLevel AtomicWranglerCasinoData == 4'),
               ('vCasinoCompsAtomicWranglerScript', 'set Banned to 1')],
        'frisk': [('TopsFriskingTriggerScript', 'player.RemoveAllTypedItems TopsWeaponLockerREF')],
        'lily': [('LilyScript', 'SetPlayerTeammate 1'), ('info:13F707', 'addperk CompanionSuite')],
        'cashier': [('info:145C76', 'GetItemCount TheTopsChip'), ('info:145C7F', 'AddItem TheTopsChip 40')],
        'clinic': [('info:14C128', 'AddPerk ImplantStrengthPerk'), ('info:14C128', 'RemoveItem caps001 4000')],
        'usanagi': [('DoctorUsanagiScript', 'disablePlayerControls')],
        'slot': [('SlotMachineWranglerScript', 'ShowSlotMachineMenuParams'), ('BlackjackTableAtomicWranglerScript', 'ShowBlackjackMenuParams')],
    }


def find_stmt(data, edid, needle):
    """First statement line of the script containing needle (case-insens). INFO ids search begin+end and return the block-local line."""
    import scriptscan
    src = facts.script_text(data, edid)
    if src is None:
        return None
    nl = needle.lower()
    if edid.startswith('info:'):
        ref = 'FalloutNV.esm:' + edid.split(':')[1].upper().zfill(6)
        i = data.all_infos.get(ref)
        for k in ('begin', 'end'):
            s = (i.get(k) or {}).get('source') if i else None
            if s:
                for n, raw in enumerate(s.replace('\r', '').split('\n'), 1):
                    if nl in scriptscan.strip_comment(raw).lower():
                        return n
        return None
    for n, raw in enumerate(src.replace('\r', '').split('\n'), 1):
        if nl in scriptscan.strip_comment(raw).lower():
            return n
    return None


def verify_citations(data):
    """-> {key: [(script, line, needle)]}, problems[]"""
    out, bad = {}, []
    for key, items in evidence_specs().items():
        out[key] = []
        for edid, needle in items:
            ln = find_stmt(data, edid, needle)
            if ln is None:
                bad.append(f'citation {key}: {edid} has no statement containing {needle!r}')
            else:
                out[key].append((edid, ln, needle))
    return out, bad


def apply_patch(rules, patch):
    """Pure function: returns a new rules dict. Raises ValueError on a structurally invalid patch."""
    out = copy.deepcopy(rules)
    qs = out['quests']
    for edid, a in patch.get('amend', {}).items():
        if edid not in qs:
            raise ValueError(f'amend: {edid} is not in rules.json')
        cur = qs[edid]['rules']
        have = {r['kind'] for r in cur}
        for k in a.get('remove', []):
            if k not in have:
                raise ValueError(f'amend {edid}: cannot remove {k}, quest does not have it')
        cur[:] = [r for r in cur if r['kind'] not in a.get('remove', [])]
        for k, p in a.get('set_params', {}).items():
            hit = [r for r in cur if r['kind'] == k]
            if not hit:
                raise ValueError(f'amend {edid}: set_params for missing kind {k}')
            hit[0]['params'] = copy.deepcopy(p)
        for r in a.get('add', []):
            if any(x['kind'] == r['kind'] for x in cur):
                raise ValueError(f'amend {edid}: {r["kind"]} already present')
            cur.append(copy.deepcopy(r))
    for edid, n in patch.get('new_quests', {}).items():
        if edid in qs:
            raise ValueError(f'new_quests: {edid} already in rules.json')
        qs[edid] = dict(tier=n['tier'], questline=n['questline'], curated=n.get('curated', True), rules=copy.deepcopy(n['rules']))
    return out


def build_patch(data):
    cites, bad = verify_citations(data)
    if bad:
        raise SystemExit('\n'.join(bad))
    amend = {}
    for edid, a in AMEND.items():
        amend[edid] = dict(remove=a.get('remove', []), add=a.get('add', []), set_params=a.get('set_params', {}), findings=a['why'],
                           evidence=[f'{e}:{ln} {n}' for k in a['ev'] for e, ln, n in cites.get(k, [])])
        amend[edid] = {k: v for k, v in amend[edid].items() if v or k in ('findings', 'evidence')}
    new = {}
    for edid, (tier, rules, why) in NEW.items():
        rk = data.risk[data.by_edid[edid]['ref']]
        new[edid] = dict(tier=rk['tier'], questline=QUESTLINE, curated=True, rules=rules, findings=why)
    return dict(
        schema=1, slug=scope.SLUG, base='docs/quest-policies/rules.json', kinds_allowed=KINDS,
        semantics=('apply_patch() in questlines/strip/policy.py: amend = remove kinds, set params, add rules on quests already in rules.json; '
                   'new_quests = quests with no rules.json entry. `proposals` is never applied.'),
        amend=amend, new_quests=new, proposals=PROPOSALS, volatile_keys=VOLATILE,
        blocked_params=dict(
            note='EditorIDs that scripts reference but forms.json.gz does not contain; NOT used in any rule param above (rule: never emit an unresolved id)',
            markers=['VHDLegateStartMarker', 'Lucky38BasementMarker', 'Lucky38CasinoFloorMarker', 'Lucky38SuiteMarker', 'Lucky38PenthouseMarker',
                     'vGomPortMarker', 'VMS42CaveExplosionMoveTo', 'VMS42ExplosionMarker'],
            cells=['FreesideAtomicWrangler', 'ULMembersOnly', 'OVWestSewers03', 'TopsAcesTheater']))


def patched_rules(data):
    return apply_patch(data.rules, build_patch(data))
