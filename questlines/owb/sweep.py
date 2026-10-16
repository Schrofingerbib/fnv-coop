"""sweep.json: the L7 sweep config for the Old World Blues DLC, in the m1_goodsprings.json format plus ordered `setup`.

Every command is rendered from symbolic steps whose EDIDs are resolved through quests.json / forms.json.gz (refnames.json.gz
only as a flagged fallback), so no FormID is ever typed by hand. Console grammar used (all exist in FNV / xNVSE):
    setstage <hexQuest> <n>            startquest <hexQuest>          player.moveto <hexRef>
    player.additem <hexItem> <n>       player.addperk <hexPerk> 1     set <hexOwner>.<var> to <v>      set <hexGlobal> to <v>
"""
import collections
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import inventory  # noqa: E402
from common import PLUGIN  # noqa: E402

ORDER_LAST = ['NVDLC03MQ05', 'NVDLC03MQ01']   # cinematic / teleporting stages last (most likely to hang the game)
SKIPPED_QUESTS_REASON = 'no stages: script-only, dialogue or bark container quest (covered by gaps.json and dialogue.md)'

# ---- symbolic setup steps -------------------------------------------------------------------------------------
# ('stage', Q, n) ('start', Q) ('move', EDID) ('item', EDID, n) ('perk', EDID) ('var', OwnerEDID, var, v) ('global', EDID, v)
MQ01_DONE = [('stage', 'NVDLC03MQ01', 10), ('stage', 'NVDLC03MQ01', 100)]


def _quest_done(q, last, chain):
    return [('stage', q, n) for n in chain] + [('stage', q, last)]


# per quest: pre (once per quest), chain[stage] -> ordered prior stages (default: ascending lower stages without 255),
# loc[stage] -> EDID of a ref to stand at (default: objective target rule), variants[stage] -> [(name, [steps])]
SETUP = {
    'NVDLC03MQ00': {'pre': []},
    'NVDLC03MQ01': {'pre': [], 'loc': {10: 'NVDLC03StartMarker'}},
    'NVDLC03MQ02': {'pre': MQ01_DONE},
    'NVDLC03MQ03': {'pre': MQ01_DONE},
    'NVDLC03MQ04': {'pre': MQ01_DONE},
    'NVDLC03MQ05': {
        'pre': MQ01_DONE + [('stage', 'NVDLC03MQ02', 10), ('stage', 'NVDLC03MQ02', 20), ('stage', 'NVDLC03MQ02', 100),
                            ('stage', 'NVDLC03MQ03', 10), ('stage', 'NVDLC03MQ03', 100),
                            ('stage', 'NVDLC03MQ04', 10), ('stage', 'NVDLC03MQ04', 100)],
        'chain': {10: [], 20: [10], 25: [10, 20], 30: [10, 20, 25], 35: [10, 20, 25, 30], 40: [10, 20, 25, 30],
                  50: [10, 20, 25, 30, 40], 60: [10, 20, 25, 30, 40, 50], 70: [10, 20, 25, 30, 40, 50],
                  100: [10, 20, 25, 30, 40, 50, 70]},
        'loc': {100: 'NVDLC03EndMarker'},
        'variants': {
            50: [('autodoc-locked', []), ('autodoc-unlocked', [('var', 'NVDLC03SinkAutoDocREF', 'bAutoDocUnlocked', 1)])],
            60: [('think-tank-hostile-path', [])],
            70: [('after-hostility', [('stage', 'NVDLC03MQ05', 60)])],
            100: [('brain-not-restored', []),
                  ('brain-in-inventory', [('item', 'NVDLC03PlayerBrain', 1), ('perk', 'NVDLC03BrainlessPerk')])],
        }},
    'NVDLC03Enc': {'pre': MQ01_DONE, 'chain': {10: [], 20: [10]}},
    'NVDLC03GenericPerkQUEST': {'pre': [], 'chain': {n: [] for n in (0, 10, 11, 20, 30, 40, 50, 60, 70, 80, 90, 100)}},
    'NVDLC03HQBuddy': {'pre': MQ01_DONE, 'loc': {n: 'NVDLC03SinkAutoDocREF' for n in (0, 1, 10, 20, 30, 40, 100)}},
    'NVDLC03HQBuddyUpgrade': {'pre': MQ01_DONE + [('stage', 'NVDLC03HQBuddy', 1)],
                              'loc': {n: 'NVDLC03SinkAutoDocREF' for n in (0, 10, 20, 30, 40, 100)}},
    'NVDLC03HQBuddySamples': {'pre': MQ01_DONE + [('stage', 'NVDLC03HQBuddy', 1)],
                              'loc': {n: 'NVDLC03SinkAutoDocREF' for n in (0, 10, 20, 30, 100)}},
    'NVDLC03X8Setup': {'pre': MQ01_DONE + [('stage', 'NVDLC03MQ03', 10)], 'chain': {100: []}},
    'NVDLC03X13Setup': {'pre': MQ01_DONE + [('stage', 'NVDLC03MQ04', 10)], 'chain': {100: []}},
    'NVDLC03SonicEmitterQuest': {'pre': MQ01_DONE},
    'NVDLC03ThinkTankQuest': {'pre': MQ01_DONE,
                              'chain': {10: [], 15: [10], 25: [10], 35: [10], 45: [10], 55: [10], 100: [10], 255: [10]}},
    'NVDLC03ThinkTankDoctor0a': {'pre': MQ01_DONE, 'chain': {10: [], 100: [10], 255: [10]}},
    'NVDLC03ThinkTankDoctor0b': {'pre': MQ01_DONE, 'chain': {10: [], 100: [10], 255: [10]}},
    'NVDLC03ThinkTankDoctor8': {'pre': MQ01_DONE, 'chain': {10: [], 100: [10], 255: [10]}},
    'NVDLC03ThinkTankDoctorDala': {'pre': MQ01_DONE, 'chain': {10: [], 100: [10], 255: [10]}},
    'NVDLC03ThinkTankDoctorKlein': {'pre': MQ01_DONE, 'chain': {10: [], 100: [10], 255: [10]}},
    'NVDLC03ThinkTankDoctorBorous': {
        'pre': MQ01_DONE,
        'chain': {10: [], 20: [10], 30: [10, 20], 35: [10, 20, 30], 100: [10, 20, 30, 35], 255: [10]},
        'variants': {10: [('no-bowl', []), ('bowl-mq03-open', [('item', 'NVDLC03GabesBowl', 1)]),
                          ('bowl-mq03-done', [('item', 'NVDLC03GabesBowl', 1), ('stage', 'NVDLC03MQ03', 100)])],
                     20: [('mq03-open', []), ('mq03-done', [('stage', 'NVDLC03MQ03', 100)])]}},
}
# quest-level notes for stages the harness cannot fully evidence
STAGE_NOTES = {
    ('NVDLC03MQ01', 10): 'AddPerk x3 (Brainless/Spineless/Heartless), outfit swap and a GameMonth/GameDay rewrite: '
                         'player-targeted perks are NOT captured today (BLOCK_CMD only); expect missing AddPerk in the txn',
    ('NVDLC03MQ05', 100): 'ends the DLC: Teleporter AddItem/EquipItem, brain->BigBrained perk swap when the brain item is held; '
                          'swept last; the tail (TransitionToTheSink) also MoveTo player',
    ('NVDLC03MQ05', 60): 'SetEnemy Think Tank faction vs player: no Effect type exists',
    ('NVDLC03MQ00', 100): 'StopQuest x6 of the NCR/Legion reaction quests (VEFR*): cross-plugin shared state',
}


class Resolver:
    def __init__(self, d):
        self.d = d
        self.m = d.edid_to_ref()
        self.used = collections.Counter()

    def ref(self, edid):
        """-> (ref, source). Quests resolve through quests.json; others prefer forms.json.gz over refnames.json.gz."""
        d = self.d
        if edid in d.by_edid:
            return d.by_edid[edid]['ref'], 'quests.json'
        cands = sorted(self.m.get(edid.lower(), []))
        if not cands:
            raise KeyError('unresolved EDID %s' % edid)
        ranked = sorted(((0 if r in d.forms else 1, r) for r in cands))
        if len(ranked) > 1 and ranked[0][0] == ranked[1][0]:
            raise KeyError('ambiguous EDID %s: %s' % (edid, cands))
        r = ranked[0][1]
        return r, d.resolve(r)[0]

    def hexid(self, edid):
        r, src = self.ref(edid)
        self.used[src] += 1
        return self.d.runtime_id(r)


def render(res, step):
    k = step[0]
    if k == 'stage':
        return 'setstage %s %d' % (res.hexid(step[1]), step[2])
    if k == 'start':
        return 'startquest %s' % res.hexid(step[1])
    if k == 'move':
        return 'player.moveto %s' % res.hexid(step[1])
    if k == 'item':
        return 'player.additem %s %d' % (res.hexid(step[1]), step[2])
    if k == 'perk':
        return 'player.addperk %s 1' % res.hexid(step[1])
    if k == 'var':
        return 'set %s.%s to %s' % (res.hexid(step[1]), step[2], step[3])
    if k == 'global':
        return 'set %s to %s' % (res.hexid(step[1]), step[2])
    raise ValueError(step)


def _default_loc(q, stage):
    """EDID-less location: first OWB target of objective == stage, else the nearest lower objective, else first objective."""
    objs = sorted(q['objectives'], key=lambda o: o['index'])
    pick = None
    for o in objs:
        if o['index'] <= stage and any(t['ref'].startswith(PLUGIN + ':') for t in o['targets']):
            pick = o
    if pick is None:
        for o in objs:
            if any(t['ref'].startswith(PLUGIN + ':') for t in o['targets']):
                pick = o
                break
    if pick is None:
        return None
    return next(t for t in pick['targets'] if t['ref'].startswith(PLUGIN + ':'))


def build_sweep(d, inv=None):
    inv = inv or inventory.build_inventory(d)
    res = Resolver(d)
    quests = []
    skipped = []
    for q in inv['quests']:
        if not q['stages']:
            skipped.append({'edid': q['edid'], 'role': q['role'], 'reason': SKIPPED_QUESTS_REASON})
            continue
        spec = SETUP.get(q['edid'])
        if spec is None:
            raise KeyError('no SETUP entry for %s' % q['edid'])
        pre = [render(res, s) for s in spec['pre']]
        stages = []
        asc = [s['index'] for s in q['stages'] if 'fail_quest' not in s['flags'] and s['index'] != 255]
        for s in q['stages']:
            n = s['index']
            chain = spec.get('chain', {}).get(n)
            chain_mode = 'explicit' if chain is not None else 'ascending-prior-stages'
            if chain is None:
                chain = [x for x in asc if x < n]
            loc_edid = spec.get('loc', {}).get(n)
            cmds = []
            if loc_edid:
                cmds.append(render(res, ('move', loc_edid)))
                loc_src = 'edid:' + loc_edid
            else:
                tg = _default_loc(q, n)
                if tg:
                    cmds.append('player.moveto %s' % d.runtime_id(tg['ref']))
                    res.used[d.resolve(tg['ref'])[0] or 'unresolved'] += 1
                    loc_src = 'objective-target:' + tg['ref']
                else:
                    loc_src = None
            cmds += [render(res, ('stage', q['edid'], c)) for c in chain]
            variants = []
            for name, steps in spec.get('variants', {}).get(n, []):
                variants.append({'name': name, 'setup': [render(res, st) for st in steps]})
            ent = {
                'index': n, 'log': s['log'], 'flags': s['flags'], 'has_script': s['has_script'],
                'findings': s['findings'], 'oracle_unconditional': s['oracle_unconditional'],
                'oracle_conditional': s['oracle_conditional'],
                'setup': cmds, 'setup_chain': chain_mode, 'location_source': loc_src,
                'variants': variants,
                'command': 'setstage %s %d' % (q['runtime_formid'], n),
                'sweep': True,
            }
            note = STAGE_NOTES.get((q['edid'], n))
            if note:
                ent['note'] = note
            stages.append(ent)
        quests.append({
            'edid': q['edid'], 'ref': q['ref'], 'runtime_formid': q['runtime_formid'], 'name': q['name'],
            'role': q['role'], 'tier': q['tier'], 'tier_direct': q['tier_direct'], 'cats': q['categories'],
            'priority': q['priority'], 'flags': q['flags'], 'script': q['script'],
            'setup': pre, 'stages': stages, 'objectives': q['objectives'],
            'prerequisites': q['prerequisites'], 'dialogue': q['dialogue'],
        })
    quests.sort(key=lambda x: (x['edid'] in ORDER_LAST, ORDER_LAST.index(x['edid']) if x['edid'] in ORDER_LAST else 0,
                               x['ref']))
    reset = sorted({x['runtime_formid'] for x in quests})
    return {
        'milestone': 'OWB Old World Blues (DLC03)', 'slug': 'owb', 'plugin': PLUGIN, 'load_order_prefix': '03',
        'format': 'm1_goodsprings.json superset: quests[].{edid,ref,stages[].index} as L7 load_plan() reads, plus ordered '
                  'setup/variants and runtime hex FormIDs (console needs hex; EDIDs do not resolve)',
        'save_requirement': 'any Mojave save with OldWorldBlues.esm active (load order index 03). Stage setup teleports with '
                            'player.moveto, so the save location does not matter. Sweep order puts MQ05/MQ01 last.',
        'console_unverified': ['set <hexRef>.<var> to <v> on object-script variables (used by variants only)',
                               'set <hexGlobal> to <v>'],
        'core': {q['edid']: q['name'] for q in quests if q['role'] == 'main'},
        'system_quests': [q['edid'] for q in quests if q['role'] == 'system'],
        'touched_quests': [],
        'reset_ids': reset,
        'order_last': ORDER_LAST,
        'excluded_stages': [],
        'unswept_quests': skipped,
        'summary': {
            'quests': len(quests),
            'stages': sum(len(q['stages']) for q in quests),
            'stages_swept': sum(1 for q in quests for s in q['stages'] if s['sweep']),
            'stages_with_variants': sum(1 for q in quests for s in q['stages'] if s['variants']),
            'unswept_quests': len(skipped),
            'ids_by_source': dict(res.used),
        },
        'quests': quests,
    }
