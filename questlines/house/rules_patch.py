"""rules.patch.json for the Mr. House questline: proposed changes to docs/quest-policies/rules.json (owned by L9, NOT edited here).

Patch format (ops are applied in order to a deep copy of rules.json by `apply`):
  replace_rules {quest, rules[]}          replace the whole rule list of an existing quest
  remove_rule   {quest, kind}             drop one rule of an existing quest
  set_param     {quest, kind, param, value}   set/replace one param of an existing rule
  add_rule      {quest, rule}             append a rule (kind must not already be present)
  add_quest     {quest, entry}            new rules.json entry {tier, questline, curated, rules[]}
Every op carries `why` (the evidence). proposed_kinds / proposed_params list everything outside the 16 existing rule kinds, each
with a justification and the code that would have to change. Cross-lane entries are marked `cross_lane`.
"""
import copy
import hashlib
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import qlib  # noqa: E402

RULES_JSON = os.path.join(qlib.ROOT, 'docs', 'quest-policies', 'rules.json')
KINDS = ['Barrier', 'CompanionOwner', 'ExcludeFromGate', 'FastTravelLock', 'InitiatorDialogue', 'PartyAwareCond', 'PartyTether',
         'PartyWarp', 'PerPlayerScene', 'QuestItemDup', 'RemoveItemPerOwner', 'RewardAll', 'SaveSuppress', 'SetPlayerControls',
         'ShareKarmaRep', 'SlideshowEnd']
# keys of a rule's params whose values are EditorIDs that must resolve in the quest data
EDID_LIST_PARAMS = ('markers', 'cells', 'gather_markers')
EDID_SCALAR_PARAMS = ('gather_marker',)
KNOWN_REDUCTIONS = {'initiator', 'any-party-member', 'any', 'min-over-party', 'entry:all-in,exit:any-in'}

R = lambda kind, params=None, origin='curated': dict(kind=kind, params=params or {}, origin=origin)  # noqa: E731
ELEVATOR_MARKERS = ['Lucky38BasementMarker', 'Lucky38CasinoFloorMarker', 'Lucky38SuiteMarker', 'Lucky38CocktailMarker',
                    'Lucky38PenthouseMarker', 'Lucky38MrHouseElevatorMarker', 'Lucky38ControlRoomMarker', 'VHDLegateStartMarker']

WHY_WARP = ('PartyWarp markers here come from object scripts merged into the quest by risk.json (dep obj Lucky38ElevatorDoor:31 etc.): elevator rides '
            'are free-roam, per-player moves (owner decision: free roam; only story-critical moments warp the party). A quest-wide PartyWarp would '
            'drag the whole party to the basement/penthouse on every ride. The real story warp is the endgame one (MrHouseSCRIPT:78), carried by VMQHouse8.')
WHY_CTRL = ('SetPlayerControls is derived from dep obj MrHouse:48 (EnablePlayerControls) on every VMQHouse quest; the only lock in the House line is the '
            'endgame sequence (INFO 15EB69 end DisablePlayerControls, MrHouseSCRIPT:48 release) -> VMQHouse8 / VDialogueMrHouse only.')
WHY_SAVE = ('SaveSuppress is derived from dep obj MrHouse:137 (autosave) / :42 (SystemSave); the saves belong to the endgame (VMQHouse8 80 / VHDHouseBattle 10), '
            'not to every House quest.')


def build(d=None, mutate=None):
    d = d or qlib.data()
    ops = []
    add = lambda **kw: ops.append(kw)  # noqa: E731
    add(op='replace_rules', quest='VMQHouse', rules=[R('ExcludeFromGate')],
        why='Legacy parent quest: 9 stages with journal text only, no script, no setter in any script/INFO/stage (inventory.json), zero questgraph edges; '
            'the real quests are VMQHouse1..8. Not required for any ending. Its derived rules (PartyWarp, SetPlayerControls, PartyAwareCond) come only from '
            'objective targets (Lucky38MrHouseREF) pulling in MrHouseSCRIPT.')
    # VMQHouse1..7 + Lockdown: drop the endgame-only / elevator-derived rules
    for q in ['VMQHouse1', 'VMQHouse2', 'VMQHouse3', 'VMQHouse4', 'VMQHouse5', 'VMQHouse6', 'VMQHouse7', 'VMQHouseLockdown']:
        add(op='remove_rule', quest=q, kind='PartyWarp', why=WHY_WARP)
        add(op='remove_rule', quest=q, kind='SetPlayerControls', why=WHY_CTRL)
        add(op='remove_rule', quest=q, kind='SaveSuppress', why=WHY_SAVE)
    add(op='remove_rule', quest='VMQHouse1', kind='FastTravelLock',
        why='Derived from TRAVEL findings in dep obj FortCaesar / FortLegionary (Legion fort scripts, EnableFastTravel). The House line locks fast travel only in the '
            'Hoover Dam battle (VHDHouseBattleSCRIPT:19, stage 10 line 57) -> moved to VHDHouseBattle.')
    add(op='set_param', quest='VMQHouse1', kind='PartyAwareCond', param='funcs',
        value={'GetInCell': 'entry:all-in,exit:any-in', 'IsActionRef': 'initiator', 'GetAV': 'initiator', 'isactionref': 'initiator',
               'IsKiller': 'any-party-member'},
        why='Lucky38SecuritronSCRIPT:5 / Lucky38VictorEntranceSCRIPT: `If IsKiller Player == 1` decides whether a Securitron death fails the House line or '
            'is the Securitron-upgrade scene; with a party any member (or companion) can be the killer.')
    add(op='add_rule', quest='VMQHouse1', rule=R('PartyTether', {'cells': ['Lucky38BasementFloorB2'], 'gather': 'auto-warp', 'stages': [25]}),
        why='Stage 25 is the Securitron-upgrade cutscene (House talks through Lucky38UpgradeSecuritronsSCRIPT GameMode, SayTo sequence, fade, '
            'Player.MoveTo VHouseEndGameMarker; elevator locked by bVictorElevatorLocked). Story-critical moment: the whole party must be in the basement cell '
            '(owner decision: story moments require the party in the same area). `stages` is a proposed param: PartyTether is quest-wide today and would '
            'also gate stages 66/100/110 of this quest.')
    # endgame: the one place the House line warps / locks / saves the party
    add(op='replace_rules', quest='VMQHouse8', rules=[
        R('PartyWarp', {'markers': ['VHDHouseIndyBattleStartMarker', 'VHDHouseIndyCompanionMarker'], 'mode': 'slot-offset', 'absent': 'hold',
                        'trigger': 'stage:80'}),
        R('SetPlayerControls', {'scope': 'all', 'release': 'host-effect', 'timeout_s': 120, 'masks': ['1 1 1 1 1 1 1']}),
        R('SaveSuppress'), R('RewardAll')],
        why='VMQHouse8 stage 80 is the "I\'m ready" point (MrHouseSCRIPT:71). Everything MrHouseSCRIPT does next (player.moveto VHDHouseIndyBattleStartMarker :78, '
            'companions MoveTo VHDHouseIndyCompanionMarker :83-130, setstage VHDHouseBattle 10 :132, autosave :137) runs OUTSIDE any capture scope, so no MoveRef txn '
            'exists for PartyWarp to expand. PartyWarp.trigger (proposed param) makes the rules engine synthesize the party warp when stage 80 is requested.')
    add(op='replace_rules', quest='VMQHouseFail', rules=[],
        why='House dies / is cut off: the whole effect is the stage fan-out (SetStage VMQHouse1..8 66/110/200, captured) plus SetDestroyed/SetAlly/PlayIdle that no Effect carries. '
            'Its derived PartyWarp (MrHouse:78), SetPlayerControls (MrHouse:48), SaveSuppress (MrHouse:137) and InitiatorDialogue (no INFO in this quest) are artefacts of '
            'dep obj MrHouse. Shared story: one party member failing it fails it for all (owner decision: shared quest state).')
    # Hoover Dam battle
    add(op='set_param', quest='VHDHouseBattle', kind='PartyWarp', param='markers', value=['VHDHouseIndyBattleStartMarker', 'VHouseEndGameMarker'],
        why='ArcadeAtDamMarker moves ArcadeRef (a companion NPC, stage 10 line 3), never a player: the PartyWarp expansion only rewrites MoveRef whose ref is a player, so it is dead '
            'weight. VHouseEndGameMarker (VHDHouseBattleSCRIPT:64, post-battle return to the Lucky 38) is the real party warp.')
    add(op='set_param', quest='VHDHouseBattle', kind='PartyWarp', param='trigger', value='info:FalloutNV.esm:15609D',
        why='The post-battle warp starts from INFO 15609D end (`set VHDHouseBattle.bRunTimer to 1`) and runs in the quest script GameMode (ambient). Synthesize it from the INFO.')
    add(op='remove_rule', quest='VHDHouseBattle', kind='PartyTether',
        why='cells == [] so RulesEngine::tetherSatisfied() returns true immediately (synccore/src/rules.cpp:282): the curated tether never holds anything. A real tether '
            'would also be wrong: it is quest-wide, so it would gate every stage (10,20,30,40,50,90,100) including the free-roam battle ones. The ready-check happens before the battle (VDialogueMrHouse ReadyCheck).')
    add(op='add_rule', quest='VHDHouseBattle', rule=R('FastTravelLock', {'scope': 'party'}),
        why='VHDHouseBattleSCRIPT:19 `EnableFastTravel 0 0 1` whenever VHDBattleController.iBattleState > 0 and stage 10 line 57; the battle disables fast travel for everybody.')
    add(op='add_rule', quest='VHDHouseBattle', rule=R('SaveSuppress'),
        why='Battle start autosaves (MrHouseSCRIPT:137) and the ending sequence must not be saved mid-slideshow.')
    add(op='add_rule', quest='VHDHouseBattle', rule=R('RewardAll'),
        why='Stage 100: player.rewardxp 4000, AddAchievement 38/30; INFO 1554D8 end: player.additem VHDTermsOfSurrender 1. Owner decision: full reward to every player.')
    add(op='add_rule', quest='VHDHouseBattle', rule=R('QuestItemDup', {'recipient': 'all'}),
        why='VHDTermsOfSurrender (INFO 1554D8) and VHDoverridechip (VDialogueMrHouse 155113) are quest items.')
    add(op='add_rule', quest='VHDHouseBattle', rule=R('ShareKarmaRep'),
        why='Battle outcomes move NCR/Legion standing (SetEnemy PlayerFaction VCaesarsLegionFaction at stage 10, Oliver/Lanius topics): shared Courier.')
    # House dialogue container: every House stage + reward is produced by INFO result scripts of this quest
    add(op='add_quest', quest='VDialogueMrHouse', entry=dict(tier='T2', questline='Main: House', curated=True, rules=[
        R('InitiatorDialogue'), R('RewardAll'), R('QuestItemDup', {'recipient': 'all'}), R('ShareKarmaRep'),
        R('PartyAwareCond', {'funcs': {'GetItemCount': 'initiator', 'GetEquipped': 'initiator', 'GetAV': 'initiator', 'GetDistance': 'min-over-party'}}),
        R('ReadyCheck', {'infos': ['FalloutNV.esm:15EB69'], 'gather_marker': 'Lucky38PenthouseMarker', 'timeout_s': 120, 'on_timeout': 'cancel',
                         'confirm': 'all-players'})]),
        why='rules.json has no entry for VDialogueMrHouse (T2 is below the T3/T4 cut), but every House stage advance, the Platinum Chip hand-in (caps 1250+1000, chip removal), '
            'Barter/Speech/Medicine XP and the endgame confirmation are INFO result scripts whose Cause source is THIS quest: RulesEngine::rulesFor(cause.source) finds nothing, '
            'so InitiatorDialogue ("not your conversation" filter), RewardAll, QuestItemDup and ShareKarmaRep never apply to the House conversation. '
            'ReadyCheck (proposed kind) holds INFO 15EB69\'s end script until every player confirms.')
    add(op='add_quest', quest='VEnding', cross_lane='Q-ncr', entry=dict(tier='T4', questline='Main: House (shared ending)', curated=True, rules=[
        R('SlideshowEnd', {'run': 'host-once', 'lock': 'all', 'release': 'together'}),
        R('SetPlayerControls', {'scope': 'all', 'release': 'host-effect', 'timeout_s': 600, 'masks': ['1 1 1 1 1 1 1']}),
        R('PartyWarp', {'markers': ['PlayerSlideshowMarker'], 'mode': 'slot-offset', 'absent': 'hold', 'trigger': 'start'}),
        R('SaveSuppress'),
        R('PartyAwareCond', {'funcs': {'GetPCIsSex': 'initiator', 'GetMapMarkerVisible': 'initiator'}})]),
        why='The slideshow driver has no rules entry. VEndingScript GameMode (quest script, ambient) does DisablePlayerControls 1 1 1 1 1 1 1, SetInCharGen 1, '
            'player.MoveTo PlayerSlideshowMarker, PlayMusic, Disable companions; NarratorScript SayToDone chain + 193 SwapTexture calls render the slides. The SlideshowEnd '
            'rule on VHDHouseBattle only dedups a SlideshowEnd effect that nothing produces. GetPCIsSex / GetMapMarkerVisible select slides: initiator (= host for host-run scripts).')
    proposed_kinds = [
        dict(kind='ReadyCheck', params=dict(infos='[INFO FormRef]', gather_marker='EditorID of a REFR marker', timeout_s='int', on_timeout='cancel|proceed',
                                            confirm='all-players'),
             justification='Barrier/PartyTether are stage-keyed (RulesEngine::onStageRequested): they can only defer the engine SetStage call, not suspend the script that '
                           'issued it. MrHouseSCRIPT:71-138 keeps running after `SetStage VMQHouse8 80` is deferred (player.moveto, companions MoveTo, setstage VHDHouseBattle 10, autosave), '
                           'so a stage barrier cannot hold the irreversible endgame. The hold has to sit one step earlier, at the INFO whose end script starts it (15EB69 "I\'m ready"), '
                           'which L8 HostDialogue already sequences.',
             code_change='L4 rules.cpp: new kind + ScenePass collection keyed by INFO ref; L8 HostDialogue: delay running the end script of a ReadyCheck INFO until released/timeout.'),
        dict(kind='GuestLocalRun', params=dict(scripts='[stage/INFO/script ids]'),
             justification='ALREADY DECIDED by the owner (MASTER-PLAN owner decisions: per-machine quest scripts run locally on every machine via new rule kind GuestLocalRun, allow-list) '
                           'but absent from rules.json. House allow-list candidates: AddAchievement 34 (VMQHouse8 stage 80), AddAchievement 38/30 (VHDHouseBattle stage 100), '
                           'PlaySound/ApplyImageSpaceModifier fades in VHDHouseBattleSCRIPT:34-71. Not used in this patch because the kind is not implemented.',
             code_change='L4/L9: kind + L5 allow-list in suppress_policy (Script::Execute for listed quest scripts).')]
    proposed_params = [
        dict(kind='PartyWarp', param='trigger', values='"stage:<n>" | "start" | "info:<FormRef>"',
             justification='PartyWarp expands a CAPTURED player MoveRef (rules.cpp:479). All three House/ending warps (MrHouseSCRIPT:78, VHDHouseBattleSCRIPT:64, '
                           'VEndingScript player.MoveTo PlayerSlideshowMarker) execute in GameMode blocks outside a capture scope (capture_hub.cpp:86 drops them), so no MoveRef '
                           'ever reaches the engine. `trigger` lets the rules engine synthesize the party MoveRef set when the named stage/start/INFO fires.',
             code_change='L4 rules.cpp (synthesize on commit of the trigger) + L5 apply (MoveRef to marker already supported).'),
        dict(kind='Barrier/PartyTether', param='stages', values='[int]',
             justification='Rules are quest-wide (RulesEngine::onStageRequested): a PartyTether on VMQHouse1 would also gather the party for the fail stage 66 that every '
                           'Securitron OnDeath script sets (Lucky38SecuritronSCRIPT), and for stages 100/110. Used by the VMQHouse1 stage-25 tether in this patch.',
             code_change='L4 rules.cpp onStageRequested: skip when the stage is not in `stages`.')]
    doc = dict(schema=1, slug=qlib.SLUG, base='docs/quest-policies/rules.json', base_sha256=hashlib.sha256(open(RULES_JSON, 'rb').read()).hexdigest(),
               ops=ops, proposed_kinds=proposed_kinds, proposed_params=proposed_params,
               summary=dict(ops=len(ops), quests_touched=len({o['quest'] for o in ops}),
                            by_op={k: sum(1 for o in ops if o['op'] == k) for k in ('replace_rules', 'remove_rule', 'set_param', 'add_rule', 'add_quest')}))
    if mutate:
        _mutate(doc, mutate)
    return doc


def _mutate(doc, how):
    ops = doc['ops']
    if how == 'unknown_quest':
        ops[1]['quest'] = 'VMQHouse99'
    elif how == 'unknown_kind':
        ops.append(dict(op='add_rule', quest='VMQHouse1', rule=dict(kind='Teleportalot', params={}, origin='curated'), why='x'))
    elif how == 'bad_marker':
        for o in ops:
            if o['op'] == 'set_param' and o.get('param') == 'markers':
                o['value'] = ['VHDHouseIndyBattleStartMarker', 'NoSuchMarkerEver']
    elif how == 'remove_absent_rule':
        ops.append(dict(op='remove_rule', quest='VMQHouse8', kind='Barrier', why='x'))
    elif how == 'duplicate_kind':
        ops.append(dict(op='add_rule', quest='VMQHouse2', rule=dict(kind='RewardAll', params={}, origin='curated'), why='x'))
    elif how == 'no_why':
        del ops[2]['why']
    else:
        raise ValueError(how)


def apply(base, patch, allow_kinds=None):
    """Apply the patch to a deep copy of the rules.json document; raise ValueError on any inconsistency."""
    doc = copy.deepcopy(base)
    qs = doc['quests']
    allowed = set(KINDS) | {k['kind'] for k in patch.get('proposed_kinds', [])} if allow_kinds is None else set(allow_kinds)

    def rule_of(q, kind):
        for r in qs[q]['rules']:
            if r['kind'] == kind:
                return r
        return None

    for i, o in enumerate(patch['ops']):
        if not o.get('why'):
            raise ValueError(f'op {i} ({o["op"]} {o.get("quest")}) has no `why`')
        q = o['quest']
        if o['op'] == 'add_quest':
            if q in qs:
                raise ValueError(f'op {i}: add_quest {q} already exists')
            qs[q] = copy.deepcopy(o['entry'])
            for r in qs[q]['rules']:
                if r['kind'] not in allowed:
                    raise ValueError(f'op {i}: unknown kind {r["kind"]}')
            continue
        if q not in qs:
            raise ValueError(f'op {i}: quest {q} not in rules.json')
        if o['op'] == 'replace_rules':
            for r in o['rules']:
                if r['kind'] not in allowed:
                    raise ValueError(f'op {i}: unknown kind {r["kind"]}')
            qs[q]['rules'] = copy.deepcopy(o['rules'])
        elif o['op'] == 'remove_rule':
            r = rule_of(q, o['kind'])
            if r is None:
                raise ValueError(f'op {i}: {q} has no {o["kind"]} rule to remove')
            qs[q]['rules'].remove(r)
        elif o['op'] == 'set_param':
            r = rule_of(q, o['kind'])
            if r is None:
                raise ValueError(f'op {i}: {q} has no {o["kind"]} rule')
            r['params'][o['param']] = copy.deepcopy(o['value'])
        elif o['op'] == 'add_rule':
            r = o['rule']
            if r['kind'] not in allowed:
                raise ValueError(f'op {i}: unknown kind {r["kind"]}')
            if rule_of(q, r['kind']) is not None:
                raise ValueError(f'op {i}: {q} already has a {r["kind"]} rule')
            qs[q]['rules'].append(copy.deepcopy(r))
        else:
            raise ValueError(f'op {i}: unknown op {o["op"]}')
    return doc


def check_resolves(d, doc, quests):
    """Every EditorID-valued param of the touched quests resolves in the quest data; returns a list of problems."""
    bad = []
    all_infos = {i['ref'] for infos in d.infos_by_quest.values() for i in infos}
    for q in quests:
        for r in doc['quests'][q]['rules']:
            for p in EDID_LIST_PARAMS:
                for e in r['params'].get(p, []) or []:
                    if not d.edid_to_ref(e):
                        bad.append(f'{q}.{r["kind"]}.{p}: {e}')
            for p in EDID_SCALAR_PARAMS:
                e = r['params'].get(p)
                if e and not d.edid_to_ref(e):
                    bad.append(f'{q}.{r["kind"]}.{p}: {e}')
            for f, red in (r['params'].get('funcs') or {}).items():
                if red not in KNOWN_REDUCTIONS:
                    bad.append(f'{q}.{r["kind"]}.funcs.{f}: unknown reduction {red}')
            for ref in r['params'].get('infos', []) or []:
                if ref not in all_infos:
                    bad.append(f'{q}.{r["kind"]}.infos: {ref} is not an INFO')
            t = r['params'].get('trigger')
            if t and t.startswith('info:') and t[5:] not in all_infos:
                bad.append(f'{q}.{r["kind"]}.trigger: {t} unknown INFO')
    return bad
