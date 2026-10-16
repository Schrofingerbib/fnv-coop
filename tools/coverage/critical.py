"""Main-quest critical paths per ending + the order to tackle quests for the fastest 'main story co-op'.

Spines are curated (the graph alone cannot separate the paths: the Hoover Dam battle quests gate each other
and vHDBattleController touches all four endings). The graph then (a) orders each spine by hard enable edges,
(b) adds *discovered* hard prerequisites (<= 2 hops, story quests only, not belonging to another path), which
are flagged `discovered` so a human can confirm them.
"""
from graph import ancestors
from model import ENDINGS, story_quests, tier_of, TIERS

COST = {'T0': 1, 'T1': 2, 'T2': 4, 'T3': 8, 'T4': 16}

PROLOGUE = ['VCG00', 'VCG01', 'VCG02', 'VCG03', 'VMQ01', 'VMQTops']
FINALE = ['vHDBattleController']
SPINES = {
    'House': ['VDialogueMrHouse', 'VDialogueLucky38Entrance', 'VMQHouse', 'VMQHouse1', 'VMQHouse2',
              'VMQHouse3', 'VMQHouse4', 'VMQHouse5', 'VMQHouse6', 'VMQHouse7', 'VMQHouse8', 'VHDHouseBattle'],
    'Yes Man': ['VMQYesMan01', 'VMQYesMan01a', 'VMQYesMan02', 'VMQYesMan03', 'VHDIndependentBattle'],
    'NCR': ['VMQ03a', 'VMQ04', 'VMQ05'],
    'Legion': ['VMQ02', 'VMQ03b', 'VDialogueCaesar', 'VHDLegionBattle'],
}
# quests an ending's *own* closure must not drag in (they belong to a different ending)
OTHER = {e: {q for e2, s in SPINES.items() if e2 != e for q in s} |
         {s[0] for e2, s in ENDINGS.items() if e2 != e} for e in SPINES}


def _order(g, nodes, spine_index):
    """Kahn order on hard enable edges inside `nodes`; ties and cycles broken by curated index then name."""
    nodes = set(nodes)
    ind = {n: set() for n in nodes}
    for (s, t, k, st) in g.edges:
        if st == 'hard' and k != 'gate' and s in nodes and t in nodes:
            ind[t].add(s)
        elif st == 'hard' and k == 'gate' and s in nodes and t in nodes:
            ind[t].add(s)
    key = lambda n: (spine_index.get(n, 10_000), n)
    out, left = [], set(nodes)
    while left:
        ready = sorted((n for n in left if not (ind[n] & left)), key=key)
        if not ready:                       # cycle: break at lowest curated index
            ready = [min(left, key=key)]
        n = ready[0]
        out.append(n)
        left.discard(n)
    return out


def critical_paths(data, g):
    story = {q['edid'] for q in story_quests(data)}
    by = data['by_edid']
    idx = {}
    for i, n in enumerate(PROLOGUE + FINALE + [x for s in SPINES.values() for x in s]):
        idx.setdefault(n, i)
    paths = {}
    for end, seeds in ENDINGS.items():
        base = [q for q in PROLOGUE + SPINES[end] + FINALE if q in by]
        disc = ancestors(g, seeds, story, max_depth=2)
        extra = [n for n in disc if n not in base and n not in OTHER[end] and n in story]
        nodes = base + extra
        order = _order(g, nodes, idx)
        tail = [n for n in FINALE + seeds if n in order]          # finale quests always close the path
        order = [n for n in order if n not in tail] + list(dict.fromkeys(tail))
        paths[end] = {
            'ending_quests': seeds,
            'quests': [{'edid': n, 'name': by[n].get('name'), 'tier': tier_of(data, by[n]),
                        'discovered': n in extra} for n in order],
        }
    return paths


def tackle_order(data, paths, status, target='game-pass', at_least=None):
    """Greedy plan: shared quests (needed by every ending) first, then the cheapest remaining ending.
    Returns list of {order, edid, name, tier, cost, needed_by, status, unlocks_ending}."""
    need = {}
    for end, p in paths.items():
        for q in p['quests']:
            need.setdefault(q['edid'], set()).add(end)
    done = lambda e: at_least(status[e], target)
    plan, planned = [], set()
    ends_left = set(paths)

    def emit(edid, unlocks=None):
        if edid in planned:
            return
        planned.add(edid)
        q = data['by_edid'][edid]
        t = tier_of(data, q)
        plan.append({'order': len(plan) + 1, 'edid': edid, 'name': q.get('name'), 'tier': t,
                     'cost': COST[t], 'needed_by': sorted(need[edid]), 'done': done(edid),
                     'unlocks_ending': unlocks})

    first = next(iter(paths.values()))['quests']
    shared = [q['edid'] for q in first if q['edid'] in PROLOGUE]
    for e in shared:
        emit(e)
    remaining = lambda end: sum(COST[q['tier']] for q in paths[end]['quests']
                                if q['edid'] not in planned and not done(q['edid']))
    while ends_left:
        end = min(ends_left, key=lambda x: (remaining(x), x))
        ends_left.discard(end)
        seq = [q['edid'] for q in paths[end]['quests'] if q['edid'] not in planned]
        for i, e in enumerate(seq):
            emit(e, end if i == len(seq) - 1 else None)
        if not seq and plan:
            plan[-1]['unlocks_ending'] = (plan[-1]['unlocks_ending'] or '') or end
    return plan
