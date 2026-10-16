"""rules.patch.json generator (existing 16 rule kinds only; new-kind proposals live under "proposals" and are not validated as kinds)."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import build  # noqa: E402
import qlib  # noqa: E402

KINDS16 = ['PartyTether', 'PartyWarp', 'SetPlayerControls', 'Barrier', 'PerPlayerScene', 'SlideshowEnd', 'CompanionOwner',
           'SaveSuppress', 'FastTravelLock', 'PartyAwareCond', 'QuestItemDup', 'RewardAll', 'RemoveItemPerOwner',
           'ShareKarmaRep', 'InitiatorDialogue', 'ExcludeFromGate']

LOCAL_TYPES = {'achievement', 'casino', 'controller', 'perk_effect', 'dlc_setup', 'chargen', 'radio'}
PARTY_KINDS_DIALOGUE = {'RewardAll', 'QuestItemDup', 'ShareKarmaRep', 'InitiatorDialogue', 'PartyAwareCond', 'CompanionOwner'}


def rule(kind, params, origin='curated'):
    return {'kind': kind, 'params': params, 'origin': origin}


def per_player(why):
    return rule('PerPlayerScene', {'scope': 'local-machine', 'run': 'every-machine', 'why': why})


def desired_rules(r, risk):
    rs = _desired(r, risk)
    if r['policy'] == 'per-player' and 'PerPlayerScene' not in {x['kind'] for x in rs}:
        rs = rs + [per_player(r['policy_reason'])]
    return rs


def _desired(r, risk):
    """-> list of rules for one in-scope quest record r (scope.build_scope())."""
    cov = build.covrules()
    e, t = r['edid'], r['type']
    if r['policy'] == 'exclude':
        return [rule('ExcludeFromGate', {'why': r['policy_reason']})]
    derived = cov.machine_rules(e, risk, None)
    kinds = {x['kind']: x for x in derived}
    out = []
    if t in LOCAL_TYPES:
        out.append(per_player(r['policy_reason']))
        if t == 'chargen':
            out += [x for x in derived if x['kind'] in ('SetPlayerControls',)]
        return out
    if t.startswith('companion'):
        out += [x for x in derived if x['kind'] != 'CompanionOwner']
        out.append(rule('CompanionOwner', {'owner': 'recruiter', 'cap_scope': 'per-owner', 'humanoid': 1, 'critter': 1, 'party_total': 4}))
        out.append(rule('PartyAwareCond', {'funcs': {'getinsamecell': 'any-of-owner-party', 'getdistance': 'min-over-party',
                                                      'isactionref': 'initiator', 'getplayerteammate': 'owner'}}))
        out.append(rule('InitiatorDialogue', {}))
        if r['policy'] == 'per-player':
            out.append(per_player(r['policy_reason']))
        return _dedup(out)
    if t == 'ending':
        out += derived
        out.append(rule('SlideshowEnd', {'run': 'host-once', 'lock': 'all', 'release': 'together'}))
        out.append(rule('SaveSuppress', {}))
        out.append(rule('Barrier', {'step': 'all-players-pass', 'before': 'slideshow'}))
        return _dedup(out)
    if t in ('dialogue_freeform', 'dialogue_generic'):
        out += [x for x in derived if x['kind'] in PARTY_KINDS_DIALOGUE]
        if t == 'dialogue_freeform' and 'InitiatorDialogue' not in {x['kind'] for x in out}:
            out.append(rule('InitiatorDialogue', {}))
        return _dedup(out)
    if t == 'timer' and r['policy'] == 'per-player':
        return [per_player(r['policy_reason'])] + [x for x in derived if x['kind'] == 'SetPlayerControls']
    out += derived
    if t in ('timer', 'scripted_scene') and 'SetPlayerControls' in kinds:
        out.append(rule('SaveSuppress', {}))
    return _dedup(out)


def _dedup(rs):
    seen, out = set(), []
    for x in rs:
        if x['kind'] not in seen:
            seen.add(x['kind'])
            out.append(x)
    return out


PROPOSALS = [
    {'name': 'GuestLocalRun', 'status': 'proposal (not in patch)',
     'justification': 'L5 round 4 rule 1 skips EVERY quest-owned script on a guest. About 20 in-scope quests (achievement tracker, casino winnings, perk '
                      'effects, controller rumble, DLC setup, chargen helpers) only make sense when run on each machine. PerPlayerScene (scene/effects '
                      'target the originating player) is the closest existing kind, so the patch uses it with scope=local-machine, but its documented '
                      'meaning does not say "run the quest script on a guest". A dedicated kind would let SuppressPolicy::skipScript consult an allow-list '
                      'instead of overloading PerPlayerScene.',
     'params': {'run': 'every-machine', 'blocks': ['GameMode'], 'forbid_effects': ['SetStage', 'StartQuest', 'SetGlobal(shared)']}},
    {'name': 'OwnerCap', 'status': 'proposal (not in patch)',
     'justification': 'Follower cap per recruiting player (vanilla: 1 humanoid + 1 critter via shared VNPCFollowers vars). Encoded as extra CompanionOwner '
                      'params in this patch (cap_scope/humanoid/critter/party_total) so no new kind is strictly required; a runtime that wants '
                      'to reject a recruit must also rewrite the VNPCFollowers condition results per owner (PartyAwareCond over GetQuestVariable).'},
]


def build_rules_patch(recs):
    rj = qlib.rules()['quests']
    R = qlib.risk()
    Q = qlib.quest_by_edid()
    add, modify, unchanged = {}, {}, []
    for r in recs:
        e = r['edid']
        want = desired_rules(r, R[Q[e]['ref']])
        if e in rj:
            have = {x['kind']: x for x in rj[e]['rules']}
            missing = [x for x in want if x['kind'] not in have]
            upd = {x['kind']: x['params'] for x in want if x['kind'] == 'CompanionOwner' and x['kind'] in have and have[x['kind']]['params'] != x['params']}
            extra = [k for k in have if k not in {x['kind'] for x in want} and k not in ('PartyTether', 'PartyWarp') and r['type'] in LOCAL_TYPES]
            if missing or extra or upd:
                modify[e] = {'tier': r['tier'], 'add_rules': missing, 'remove_kinds': extra, 'update_params': upd,
                             'why': 'type=%s policy=%s' % (r['type'], r['policy'])}
            else:
                unchanged.append(e)
        elif want:
            add[e] = {'tier': r['tier'], 'questline': 'Systems: ' + r['type'], 'curated': True, 'rules': want,
                      'why': 'type=%s policy=%s' % (r['type'], r['policy'])}
    return {'schema': 1, 'target': 'docs/quest-policies/rules.json', 'owner_of_target': 'L9',
            'kinds_used_allowed': KINDS16, 'add': add, 'modify': modify, 'unchanged_in_rules_json': unchanged,
            'proposals': PROPOSALS}
