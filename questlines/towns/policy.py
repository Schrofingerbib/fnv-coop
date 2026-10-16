"""rules.patch.json builder for the towns questline.

Two layers:
  derived  - kinds implied by the gap items (analysis.gaps) that the quest's current rules.json entry lacks
  curated  - hand-written proposals with a reason (tethers, escorts, controls, reductions, encounter exclusion)

Only the 16 existing rule kinds are used.  Engine-level proposals (new params) go under `engine_proposals`, not in the patch.
"""
import json
import os

import lib

KINDS = ['PartyTether', 'PartyWarp', 'SetPlayerControls', 'Barrier', 'PerPlayerScene', 'SlideshowEnd', 'CompanionOwner',
         'SaveSuppress', 'FastTravelLock', 'PartyAwareCond', 'QuestItemDup', 'RewardAll', 'RemoveItemPerOwner',
         'ShareKarmaRep', 'InitiatorDialogue', 'ExcludeFromGate']

# reductions the L4 RulesEngine::reduceCondition understands
REDUCTIONS = {'initiator', 'any-party-member', 'any', 'min-over-party', 'entry:all-in,exit:any-in'}
COND_REDUCTION = {'getdistance': 'min-over-party', 'getincell': 'entry:all-in,exit:any-in', 'getinsamecell': 'any',
                  'isactionref': 'initiator', 'iskiller': 'any-party-member', 'getinworldspace': 'any', 'getav': 'initiator',
                  'getequipped': 'initiator'}
CAMEL = {'getdistance': 'GetDistance', 'getincell': 'GetInCell', 'getinsamecell': 'GetInSameCell', 'isactionref': 'IsActionRef',
         'iskiller': 'IsKiller', 'getinworldspace': 'GetInWorldSpace', 'getequipped': 'GetEquipped', 'getdetected': 'GetDetected',
         'getplayerteammate': 'GetPlayerTeammate', 'getisreference': 'GetIsReference', 'getinfaction': 'GetInFaction',
         'getactionref': 'GetActionRef'}
# extra reductions proposed for player-ref reads the L9 table does not know (see policy_review.md section "reductions")
EXTRA_REDUCTION = {'getdetected': 'any-party-member', 'getplayerteammate': 'any-party-member', 'getisreference': 'initiator',
                   'getinfaction': 'initiator', 'getactionref': 'initiator'}


def load_rules():
    with open(os.path.join(lib.REPO, 'docs', 'quest-policies', 'rules.json'), encoding='utf-8') as f:
        return json.load(f)


def implied_kinds(items, n_infos):
    """-> {kind: reason}"""
    out = {}

    def add(k, why):
        out.setdefault(k, why)
    for i in items:
        f = i['fn'].lower()
        if i['effect'] in ('ModReputation', 'ModKarma') or f.startswith('modactorvalue(karma'):
            add('ShareKarmaRep', 'script changes reputation/karma (%s %s:%s)' % (i['where'], i['script'], i['line']))
        if (i['class'] == 'player' and i['effect'] in ('AddItem', 'ModActorValue') and 'reward' in i['hazards']) or f == 'addnote':
            add('RewardAll', 'player reward (%s) at %s line %s' % (i['fn'], i['where'], i['line']))
        if i['effect'] == 'RemoveItem' and i['player_targeted']:
            add('RemoveItemPerOwner', 'RemoveItem from the player at %s line %s' % (i['where'], i['line']))
        if f == 'setplayerteammate':
            add('CompanionOwner', 'SetPlayerTeammate at %s line %s' % (i['where'], i['line']))
        if f in ('disableplayercontrols', 'enableplayercontrols'):
            add('SetPlayerControls', '%s at %s:%s' % (i['fn'], i['script'], i['line']))
        if i['effect'] == 'ForcedDialogue':
            add('InitiatorDialogue', '%s at %s:%s' % (i['fn'], i['script'], i['line']))
        if i['class'] == 'condition' and i['hazards']:
            add('PartyAwareCond', 'player-ref condition %s at %s:%s' % (i['fn'], i['script'], i['line']))
    if n_infos:
        add('InitiatorDialogue', '%d dialogue INFOs' % n_infos)
    return out


def cond_funcs(items):
    funcs = {}
    for i in items:
        if i['class'] != 'condition':
            continue
        fn = i['fn'].lower()
        if fn in COND_REDUCTION:
            funcs[CAMEL[fn]] = COND_REDUCTION[fn]
        elif fn in EXTRA_REDUCTION:
            funcs[CAMEL[fn]] = EXTRA_REDUCTION[fn]
    return funcs


def _rule(kind, params=None, why='', origin='derived'):
    return {'kind': kind, 'params': params or {}, 'origin': origin, 'reason': why}


# curated: EDID -> {"add": [...], "set_params": {kind: {param: value}}, "remove": [kind], "why": str}
def curated(d):
    cells = {k: f for k, f in ((f.get('edid'), f) for f in d.forms.values() if f['sig'] == 'CELL')}
    gomorrah = [c for c in ('Gomorrah01', 'Gomorrah03', 'GomorrahJoanaRoom') if c in cells]
    return {
        'VNipton': {
            'add': [_rule('CompanionOwner', {'owner': 'recruiter'},
                          'stage 30 runs CraigBooneREF.SayTo player VDialogueCraigBooneNiptonReact only if VNPCFollowers.bBooneHired: the reaction line and the '
                          'VDialogueCraigBoone.nOpenness bump belong to the recruiter\'s Boone, not to whichever player finishes the quest',
                          'curated')],
            'set_params': {'PartyAwareCond': {'funcs': {'GetPlayerTeammate': 'any-party-member'}}},
        },
        'VMS20': {
            'set_params': {'PartyAwareCond': {'funcs': {'GetDetected': 'any-party-member'}}},
            'note': 'Barrier/PartyTether intentionally NOT proposed: the ruins cell is not in forms.json (unknown EDID) and the current RulesEngine tether is quest-wide '
                    '(see engine_proposals.PartyTether.stages)',
        },
        'VES01VictorGSMyBodyguard': {
            'set_params': {'PartyAwareCond': {'funcs': {'IsInCombat': 'any-party-member', 'GetHealthPercentage': 'any-party-member'}}},
            'note': 'quest script reads player.IsInCombat and player.GetHealthPercentage (<= 0.5) to summon Victor: any hurt party member should summon him',
        },
        'VMS19': {
            'add': [_rule('SetPlayerControls', {'scope': 'all', 'release': 'host-effect', 'timeout_s': 120, 'masks': []},
                          'dep scripts VFSJulieFarkas / VFSPacer call disablePlayerControls 5x and enable 3x around scripted scenes', 'curated'),
                    _rule('RewardAll', {}, 'stage 100/101 player.rewardxp 1200/1500 - the curated entry replaced the derived rules and dropped this', 'curated'),
                    _rule('ShareKarmaRep', {}, 'stage 100 adjusts NCR/Kings reputation - dropped by the curated entry', 'curated'),
                    _rule('InitiatorDialogue', {}, 'King / Pacer / Julie Farkas SayTo+StartConversation scenes and 12 branching INFOs', 'curated')],
        },
        'VMS21': {
            'add': [_rule('InitiatorDialogue', {}, '8 SayTo player + 7 ShowMessage in VMS21Cachino/BigSal/Clanden/Troike/Nero dep scripts; ending choices are dialogue', 'curated'),
                    _rule('SetPlayerControls', {'scope': 'all', 'release': 'host-effect', 'timeout_s': 120, 'masks': []},
                          'GomorrahHotelDoor01 disableplayercontrols line 13 / enable line 22 around the port transition', 'curated'),
                    _rule('ShareKarmaRep', {}, 'Omerta outcome decisions change faction standing (stage 50/55/60)', 'curated')],
            'set_params': {},
            'note': 'PartyWarp intentionally NOT proposed: the player.MoveTo calls are Lucky38/Gomorrah elevator + door scripts that move the ACTIVATOR only; '
                    'a rule-bearing PartyWarp would drag the whole party at every elevator ride',
        },
        'VMS21a': {
            'add': [_rule('CompanionOwner', {'owner': 'recruiter'},
                          'Joana and her friends follow the player via AddScriptPackage during the escort (stages 60-90): the follow target must be one player',
                          'curated'),
                    _rule('FastTravelLock', {'scope': 'party'},
                          'escort stages 60-90 (Gomorrah lobby -> Freeside meeting between midnight and dawn): fast travel strands the escorts',
                          'curated'),
                    _rule('InitiatorDialogue', {}, '41 dialogue INFOs incl. AddToFaction OmertaTraitors results (info 11F861)', 'curated'),
                    _rule('RemoveItemPerOwner', {}, 'hand-in removes the item from the handing player only', 'curated')],
        },
        'VES01aVictorInNovac': {
            'set_params': {'PartyAwareCond': {'funcs': {'IsPlayerInRegion': 'any'}}},
            'note': 'GSVictorRef is ONE host-owned actor moved to Novac when any player enters vMapVictorInNovacRegion; two players in different regions make him jump',
        },
        'VERNovac01': {'add': [_rule('ExcludeFromGate', {}, 'random-encounter quest (28 PlaceAtMe spawns), not required for any ending', 'curated')]},
        'VERPrimmDen01': {'add': [_rule('ExcludeFromGate', {}, 'random-encounter quest, not required for any ending', 'curated')]},
        'VES09BoulderCity': {'add': [_rule('ExcludeFromGate', {}, 'random-encounter quest (20 MoveToMarkerWithFade of encounter actors), not required for any ending', 'curated')]},
        'VES04FactReactPrimmArea01PowderGangers': {'add': [_rule('ExcludeFromGate', {}, 'Powder Ganger faction-reaction encounter script, not required for any ending', 'curated')]},
        'VES04FactionReactionPrimmArea01PowderGangers': {'add': [_rule('ExcludeFromGate', {}, 'container of the reaction script, no stages', 'curated')]},
    }


def build_patch(d, gap_items):
    rules = load_rules()['quests']
    cur = curated(d)
    by_quest = {}
    for i in gap_items:
        by_quest.setdefault(i['quest'], []).append(i)
    patch = {'schema': 1, 'base': 'docs/quest-policies/rules.json', 'owner': 'questline towns',
             'kinds_allowed': KINDS, 'quests': {}, 'new_quests': {},
             'engine_proposals': {
                 'PartyTether.stages': 'optional param: list of stage indexes the tether applies to. Today a PartyTether is quest-wide '
                                       '(RulesEngine defers EVERY stage request of the quest until the party is in `cells`), which makes it unusable for quests '
                                       'that span several locations (VMS21a, VMS19, VMS15). Not a new kind; a param on an existing one.',
                 'PartyAwareCond.reductions': 'IsInCombat / GetHealthPercentage / GetDetected / GetPlayerTeammate / IsPlayerInRegion are not in '
                                              'tools/coverage/rules.py COND_REDUCTION; L4 falls back to `initiator` (counted in unknownReductions). '
                                              'Values used here are all in REDUCTIONS.',
             }}
    graph_line = {n['id']: n['line'] for n in d.graph['nodes']}
    for town, edid, role in lib.all_quests():
        q = d.by_edid[edid]
        items = by_quest.get(edid, [])
        n_infos = len(d.infos.get(q['ref'], []))
        existing = rules.get(edid)
        have = {r['kind'] for r in existing['rules']} if existing else set()
        imp = implied_kinds(items, n_infos)
        c = cur.get(edid, {})
        adds = []
        for k, why in imp.items():
            if k in have or any(a['kind'] == k for a in c.get('add', [])):
                continue
            params = {}
            if k == 'PartyAwareCond':
                params = {'funcs': cond_funcs(items)}
            elif k == 'SetPlayerControls':
                params = {'scope': 'all', 'release': 'host-effect', 'timeout_s': 120, 'masks': []}
            elif k == 'CompanionOwner':
                params = {'owner': 'recruiter'}
            elif k == 'QuestItemDup':
                params = {'recipient': 'all'}
            adds.append(_rule(k, params, why, 'derived'))
        adds += [a for a in c.get('add', []) if a['kind'] not in have or not existing]
        entry = {}
        if adds:
            entry['add'] = adds
        sp = {k: v for k, v in c.get('set_params', {}).items() if k in have}
        # a set_params on a kind the quest does not have yet becomes an add of that kind
        for k, v in c.get('set_params', {}).items():
            if k not in have and not any(a['kind'] == k for a in adds):
                entry.setdefault('add', []).append(_rule(k, v, c.get('note', 'curated reduction'), 'curated'))
        if sp:
            entry['set_params'] = sp
        if c.get('note'):
            entry['note'] = c['note']
        if not entry:
            continue
        if existing:
            patch['quests'][edid] = entry
        else:
            entry['tier'] = d.risk[q['ref']]['tier']
            entry['questline'] = graph_line.get(edid)
            patch['new_quests'][edid] = entry
    return patch
