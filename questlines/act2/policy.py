"""Policy review: derive, from the script evidence, which of the 16 existing rule kinds each quest needs, compare with
docs/quest-policies/rules.json, and emit rules.patch.json + the data-driven tables of policy_review.md."""
import collections
import importlib.util
import json
import re

import fnmap
import lib

KINDS = ['PartyTether', 'PartyWarp', 'SetPlayerControls', 'Barrier', 'PerPlayerScene', 'SlideshowEnd', 'CompanionOwner', 'SaveSuppress',
         'FastTravelLock', 'PartyAwareCond', 'QuestItemDup', 'RewardAll', 'RemoveItemPerOwner', 'ShareKarmaRep', 'InitiatorDialogue',
         'ExcludeFromGate']

CAMEL = {'getdistance': 'GetDistance', 'getincell': 'GetInCell', 'getinsamecell': 'GetInSameCell', 'isactionref': 'IsActionRef',
         'getactionref': 'IsActionRef', 'iskiller': 'IsKiller', 'getav': 'GetAV', 'getactorvalue': 'GetAV', 'getequipped': 'GetEquipped',
         'getinworldspace': 'GetInWorldspace', 'getdetected': 'GetDetected', 'getisreference': 'GetIsReference',
         'getitemcount': 'GetItemCount', 'getplayerteammate': 'GetPlayerTeammate'}

PLAYER_ITEM_FNS = ('additem', 'additemns', 'additemhealthpercent')
REP_FNS = ('addreputation', 'setreputation', 'addreputationexact', 'removereputation', 'modreputation', 'rewardkarma', 'playerrewardkarma')


def load_cond_reduction():
    spec = importlib.util.spec_from_file_location('cov_rules', lib.REPO / 'tools' / 'coverage' / 'rules.py')
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m.COND_REDUCTION


def _ev(r):
    u = r['unit']
    return {'script': u['script_edid'], 'line': r['line'], 'fn': r['fn'], 'text': r['text'][:110]}


def derive(D, rows, edids, infos_per_quest):
    """-> {quest: {kind: {'evidence': [...], 'params': {...}}}} from the script evidence."""
    mine = set(edids)
    need = {e: collections.defaultdict(lambda: {'evidence': [], 'markers': [], 'cells': [], 'masks': [], 'funcs': {}}) for e in edids}
    red = load_cond_reduction()

    def add(q, kind, r):
        n = need[q][kind]
        ev = _ev(r)
        if ev not in n['evidence']:
            n['evidence'].append(ev)
        return n

    for r in rows:
        u, d = r['unit'], r['d']
        if not u['touched']:
            continue
        qs = sorted(u['attrib'] & mine)
        if not qs:
            continue
        hz, fn, text = d['hazards'], r['fn'], r['text']
        for q in qs:
            if 'PLAYER_TELEPORT' in hz:
                n = add(q, 'PartyWarp', r)
                m = re.search(r'move\s*to(?:marker(?:withfade)?)?\s+(\w+)', text, re.I)
                if m:
                    n['markers'].append(m.group(1))
                add(q, 'PartyTether', r)
            if 'CONTROLS' in hz:
                n = add(q, 'SetPlayerControls', r)
                m = re.match(r'\s*disableplayercontrols\s+([01 ]+)$', text.strip(), re.I)
                if m:
                    n['masks'].append(' '.join(m.group(1).split()))
                if fn == 'disableplayercontrols':
                    add(q, 'PartyTether', r)
            if 'FAST_TRAVEL' in hz:
                add(q, 'FastTravelLock', r)
            if 'SAVE' in hz:
                add(q, 'SaveSuppress', r)
            if 'COMPANION' in hz and fn == 'setplayerteammate':
                add(q, 'CompanionOwner', r)
            if 'FORCED_DIALOGUE' in hz or 'MESSAGE_CHOICE' in hz or fn in ('showbartermenu', 'startconversation'):
                add(q, 'InitiatorDialogue', r)
            if 'PLAYER_REF_COND' in hz or 'GUEST_ACTIVATION' in hz:
                n = add(q, 'PartyAwareCond', r)
                f = CAMEL.get(fn)
                if f:
                    n['funcs'][f] = red.get(fn if fn in red else ('isactionref' if f == 'IsActionRef' else fn), d['effect'].split('(')[-1].rstrip(')'))
                if fn == 'getincell':
                    for c in re.findall(r'getincell\s+(\w+)', text, re.I):
                        n['cells'].append(c)
                        add(q, 'PartyTether', r)['cells'].append(c)
            if 'REPUTATION' in hz or 'KARMA' in hz or 'FACTION_RELATION' in hz:
                add(q, 'ShareKarmaRep', r)
            if 'PLAYER_REWARD' in hz and fn in ('rewardxp',) + PLAYER_ITEM_FNS + ('addnote',):
                add(q, 'RewardAll', r)
            if fn in PLAYER_ITEM_FNS and 'PLAYER_REWARD' in hz and u['scope'] in ('stage', 'quest_script', 'dep_obj'):
                add(q, 'QuestItemDup', r)
            if fn in ('removeitem', 'removeitemns', 'removealltypeditems') and 'PLAYER_INVENTORY' in hz and u['scope'] != 'stage':
                add(q, 'RemoveItemPerOwner', r)
    for q in edids:
        if infos_per_quest.get(q, 0):
            if 'InitiatorDialogue' not in need[q]:
                need[q]['InitiatorDialogue']['evidence'].append({'script': '(dialogue)', 'line': 0, 'fn': 'infos',
                                                                'text': '%d INFOs reach this quest' % infos_per_quest[q]})
    return need


def _ev_for(n, items):
    """Evidence lines that mention one of the proposed items (function names are matched case-insensitively)."""
    low = [str(i).lower() for i in items]
    out = [e for e in n['evidence'] if any(i in e['text'].lower() or i == e['fn'] for i in low)]
    return sorted(out, key=lambda e: (e['script'], e['line']))[:3]


def compare(D, need, edids):
    """-> list of per-quest dicts: existing kinds, needed kinds, missing kinds, params to add."""
    out = []
    for q in edids:
        base = D.rules['quests'][q]
        have = {r['kind']: r for r in base['rules']}
        row = {'quest': q, 'tier': base['tier'], 'curated': base['curated'], 'have': sorted(have), 'need': sorted(need[q]),
               'missing': sorted(set(need[q]) - set(have)), 'extra': sorted(set(have) - set(need[q])), 'modify': []}
        for kind, n in need[q].items():
            if kind not in have:
                continue
            p = have[kind]['params']
            if kind == 'PartyWarp':
                miss = sorted(set(n['markers']) - set(p.get('markers', [])))
                if miss:
                    row['modify'].append({'kind': kind, 'param': 'markers', 'add': miss, 'evidence': _ev_for(n, miss)})
            elif kind == 'PartyTether':
                miss = sorted(set(n['cells']) - set(p.get('cells', [])))
                if miss:
                    row['modify'].append({'kind': kind, 'param': 'cells', 'add': miss, 'evidence': _ev_for(n, miss)})
                if not p.get('cells') and not n['cells']:
                    row['modify'].append({'kind': kind, 'param': 'cells', 'add': [], 'note': 'empty cell list; no cell found in the scripts'})
            elif kind == 'SetPlayerControls':
                miss = sorted(set(n['masks']) - set(p.get('masks', [])))
                if miss:
                    row['modify'].append({'kind': kind, 'param': 'masks', 'add': miss, 'evidence': _ev_for(n, miss)})
            elif kind == 'PartyAwareCond':
                miss = {f: v for f, v in n['funcs'].items() if f not in p.get('funcs', {})}
                if miss:
                    row['modify'].append({'kind': kind, 'param': 'funcs', 'add': miss, 'evidence': _ev_for(n, list(miss))})
        out.append(row)
    return out


def build_patch(D, need, cmp_rows, edids, header):
    quests = collections.OrderedDict()
    unresolved = []
    for row in cmp_rows:
        q = row['quest']
        adds, mods = [], []
        for kind in row['missing']:
            n = need[q][kind]
            params = default_params(kind, n, D, unresolved)
            adds.append({'kind': kind, 'params': params, 'origin': 'act2-proposal', 'evidence': sorted(
                n['evidence'], key=lambda e: (e['script'], e['line']))[:4], 'evidence_total': len(n['evidence'])})
        for m in row['modify']:
            kind, param = m['kind'], m['param']
            if param in ('markers', 'cells'):
                ok = [x for x in m['add'] if D.ref_of_edid(x)]
                unresolved += [x for x in m['add'] if not D.ref_of_edid(x)]
                m = dict(m, add=ok)
                if not ok and not m.get('note'):
                    continue
            mods.append(m)
        if adds or mods:
            quests[q] = {'add': adds, 'modify': mods}
    return {
        'schema': 1, 'questline': lib.QUESTLINE, **header, 'base': 'docs/quest-policies/rules.json',
        'base_quests': len(D.rules['quests']), 'kinds_allowed': KINDS,
        'how_to_apply': 'for each quest: append every `add` rule (kind+params, origin act2-proposal); for each `modify`, union `add` into '
                        'the existing rule\'s params[param] (list) or merge it (funcs dict). Never remove an existing rule.',
        'quests': quests,
        'new_kinds': NEW_KINDS,
        'param_extensions': PARAM_EXTENSIONS,
        'unresolved_identifiers_omitted': sorted(set(unresolved)),
    }


def default_params(kind, n, D, unresolved):
    uniq = lambda xs: list(dict.fromkeys(xs))  # noqa: E731
    if kind == 'PartyWarp':
        mk = [m for m in uniq(n['markers']) if D.ref_of_edid(m)]
        unresolved += [m for m in uniq(n['markers']) if not D.ref_of_edid(m)]
        return {'markers': mk, 'mode': 'slot-offset', 'absent': 'hold'}
    if kind == 'PartyTether':
        cells = [c for c in uniq(n['cells']) if D.ref_of_edid(c)]
        unresolved += [c for c in uniq(n['cells']) if not D.ref_of_edid(c)]
        return {'cells': cells, 'gather': 'auto-warp'}
    if kind == 'SetPlayerControls':
        return {'scope': 'all', 'release': 'host-effect', 'timeout_s': 120, 'masks': uniq(n['masks'])}
    if kind == 'PartyAwareCond':
        return {'funcs': dict(sorted(n['funcs'].items()))}
    if kind == 'FastTravelLock':
        return {'scope': 'party'}
    if kind == 'QuestItemDup':
        return {'recipient': 'all'}
    if kind == 'CompanionOwner':
        return {'owner': 'recruiter'}
    return {}


NEW_KINDS = [
    {
        'kind': 'GuestEventRelay',
        'summary': 'A player-triggered script event (OnAdd/OnDrop player, OnTriggerEnter player, OnActivate by a player) that occurs on a '
                   'guest is relayed to the host, which runs the script block once with the originating player as `player`/IsActionRef and '
                   'captures its effects as usual.',
        'params': {'events': ['OnAdd', 'OnDrop', 'OnTriggerEnter', 'OnActivate', 'OnHit', 'OnDeath'], 'scripts': '[script EDIDs]',
                   'actor': 'originator', 'dedupe': 'per event id'},
        'why_not_existing': 'None of the 16 kinds routes a world event from a guest to the host. PartyAwareCond only reduces conditions the '
                            'host already evaluates; InitiatorDialogue covers INFOs only; QuestItemDup covers script AddItem, not loot.',
        'evidence': 'VPlatinumChipSCRIPT OnAdd player runs SetStage VMQTops 80 + CompleteAllObjectives (Ring-a-Ding-Ding completion), '
                    'VHouseVIPKeyCardSCRIPT/VBSPsychologyBookSCRIPT/VBSPediatricsBookSCRIPT OnAdd player advance quests; see gaps.json theme GUEST_EVENT.',
        'quests': ['VMQTops', 'VMQ02', 'VMS40'],
    },
    {
        'kind': 'ModalChoice',
        'summary': 'A scripted modal ShowMessage with buttons (GetButtonPressed) is shown to the initiating player, answered by that player, and the '
                   'answer is returned to the host script; point-of-no-return prompts (VEndGameWarningMsg*) additionally require party consent.',
        'params': {'decider': 'initiator', 'consent': 'party|none', 'timeout_s': 120},
        'why_not_existing': 'InitiatorDialogue only covers INFO topics. Elevator floor menus and the endgame warning are ShowMessage buttons in '
                            'object scripts.',
        'evidence': 'GetButtonPressed in Lucky38ElevatorDoorScript:25, Lucky38MrHouseElevatorDoorScript:15, Lucky38VictorElevatorScript:42, '
                    'FortCaesarScript:78,257, FortLuciusScript:59, VHDColonelMooreSCRIPT:126.',
        'quests': ['VMQ02', 'VMQ03a', 'VMQ03b', 'VMQ04', 'VMQCaesarFail', 'VMQNCRFail', 'VMQTops', 'VDialogueLucky38Entrance'],
    },
]

PARAM_EXTENSIONS = [
    {'kind': 'PartyWarp', 'param': 'absent', 'proposed_value': 'skip',
     'why': 'elevators and doors are free-roam transfers: a player who is not in the car must neither hold the others (absent=hold) nor be '
            'dragged along (absent=warp). Needs a third value: only players within `radius` of the trigger are moved.',
     'extra_param': {'radius': 512}, 'quests': ['VDialogueLucky38Entrance', 'VMQ02']},
    {'kind': 'ShareKarmaRep', 'param': 'faction_relations', 'proposed_value': True,
     'why': 'SetPCEnemyOfFaction / SetEnemy PlayerFaction / ClearFactionPlayerEnemyFlag change the player\'s faction standing, which the '
            'owner decision shares (party = one Courier); no existing kind carries faction relations.',
     'quests': ['VMQCaesarFail', 'VMQNCRFail', 'VMQ02', 'VMS18']},
]


def owner_checks(D, need, cmp_rows):
    """Owner decisions vs rules.json, one line per decision with the quests that violate it."""
    have = {r['quest']: set(r['have']) for r in cmp_rows}
    chk = []

    def viol(kind):
        return sorted(q for q, n in need.items() if kind in n and kind not in have[q])
    chk.append(('Karma / reputation shared (ShareKarmaRep)', 'ShareKarmaRep', viol('ShareKarmaRep')))
    chk.append(('Caps/XP quest rewards to every player (RewardAll)', 'RewardAll', viol('RewardAll')))
    chk.append(('Quest items duplicated to every player (QuestItemDup)', 'QuestItemDup', viol('QuestItemDup')))
    chk.append(('Dialogue: initiator chooses (InitiatorDialogue)', 'InitiatorDialogue', viol('InitiatorDialogue')))
    chk.append(('Story moments tether the party (PartyTether)', 'PartyTether', viol('PartyTether')))
    chk.append(('Scripted player teleports warp the party (PartyWarp)', 'PartyWarp', viol('PartyWarp')))
    chk.append(('Cutscene locks hit every player (SetPlayerControls)', 'SetPlayerControls', viol('SetPlayerControls')))
    chk.append(('Companions owned by the recruiter (CompanionOwner)', 'CompanionOwner', viol('CompanionOwner')))
    chk.append(('Fast-travel lock is party-wide (FastTravelLock)', 'FastTravelLock', viol('FastTravelLock')))
    chk.append(('No saves mid-scene (SaveSuppress)', 'SaveSuppress', viol('SaveSuppress')))
    chk.append(('Player-reference reads are party-aware (PartyAwareCond)', 'PartyAwareCond', viol('PartyAwareCond')))
    chk.append(('Hand-ins remove from the handing player (RemoveItemPerOwner)', 'RemoveItemPerOwner', viol('RemoveItemPerOwner')))
    return chk
