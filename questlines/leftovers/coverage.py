"""Coverage cross-check: every stage-bearing quest in out/questdata/quests.json mapped to the questline(s) that cover it.

A quest is covered by a lane when it is listed as an entry (`edid`) of that lane's inventory.json `quests[]`, or in
prologue's `reference_done[]`, or is a system/core quest of the M1 Goodsprings template (out/questdata/m1_goodsprings.json).
Entries whose `role` marks them as only touched / gate-only do not count as coverage, they are recorded separately.
Debug quests (edid / name containing 'debug' or 'test', or the Patch0xQuest00 style) are excluded, not counted.
"""
import glob
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.normpath(os.path.join(HERE, '..', '..'))
OUT = os.path.join(REPO, 'out', 'questdata')

NOT_OWNED_ROLES = {'touched', 'gate_only', 'gate-only', 'xref', 'neighbour'}


def excluded_from_gate():
    """Quests that docs/quest-policies/rules.json already marks ExcludeFromGate (debug / demo content)."""
    r = json.load(open(os.path.join(REPO, 'docs', 'quest-policies', 'rules.json'), encoding='utf-8'))['quests']
    return {e for e, v in r.items() if any(x['kind'] == 'ExcludeFromGate' for x in v['rules'])}


def is_debug(q, excl=None):
    e = (q.get('edid') or '').lower()
    n = (q.get('name') or '').lower()
    excl = excluded_from_gate() if excl is None else excl
    return (q.get('edid') in excl or 'debug' in e or 'debug' in n or e.endswith('test') or e.startswith('patch0')
            or 'tester' in e)


def load_quests():
    return json.load(open(os.path.join(OUT, 'quests.json'), encoding='utf-8'))['quests']


def lane_entries(inv):
    """-> (owned_edids, touched_edids) from one inventory.json."""
    owned, touched = set(), set()
    for e in inv.get('quests') or []:
        if isinstance(e, dict) and isinstance(e.get('edid'), str):
            (touched if e.get('role') in NOT_OWNED_ROLES else owned).add(e['edid'])
    for e in inv.get('reference_done') or []:
        if isinstance(e, dict) and isinstance(e.get('edid'), str):
            owned.add(e['edid'])
    return owned, touched


def lane_map(extra_lanes=None):
    """slug -> (owned, touched). `extra_lanes` lets the caller (or a test) inject / remove lanes."""
    out = {}
    for f in sorted(glob.glob(os.path.join(REPO, 'questlines', '*', 'inventory.json'))):
        slug = os.path.basename(os.path.dirname(f))
        out[slug] = lane_entries(json.load(open(f, encoding='utf-8')))
    m1 = json.load(open(os.path.join(OUT, 'm1_goodsprings.json'), encoding='utf-8'))
    out['m1_goodsprings'] = (set(m1['core']) | set(m1['system_quests']) | {q['edid'] for q in m1.get('quests', []) if isinstance(q, dict) and q.get('edid')}, set())
    if extra_lanes:
        out.update(extra_lanes)
    return out


def build(lanes=None, quests=None):
    quests = quests if quests is not None else load_quests()
    lanes = lanes if lanes is not None else lane_map()
    stage_q = [q for q in quests if q['stages']]
    mapping, debug, touched_only, uncovered = {}, [], {}, []
    for q in sorted(stage_q, key=lambda q: q['edid']):
        e = q['edid']
        if is_debug(q):
            debug.append(e)
            continue
        cov = sorted(s for s, (own, _) in lanes.items() if e in own)
        if cov:
            mapping[e] = cov
        else:
            t = sorted(s for s, (_, tch) in lanes.items() if e in tch)
            if t:
                touched_only[e] = t
            uncovered.append(e)
    return {'schema': 1,
            'definition': 'covered = listed in a lane inventory.json quests[] (or prologue reference_done, or the M1 template) '
                          'with a role that is not touched/gate-only; debug quests excluded',
            'stage_bearing_quests': len(stage_q), 'debug_excluded': debug, 'covered_count': len(mapping),
            'uncovered_count': len(uncovered), 'uncovered': uncovered, 'touched_only': touched_only,
            'covered_by': mapping}
