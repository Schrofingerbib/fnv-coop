"""Validation + mutation twin for questlines/yesman.   python -I questlines/yesman/run_tests.py   -> prints ALL OK, exit 0.

validate(bundle, data, fresh) returns a list of error strings.  The real bundle (files on disk) must produce [];
every mutant (a deliberately broken copy) must produce at least one error, otherwise the test itself is broken.
"""
import copy
import gzip
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, '..', '..'))
sys.path.insert(0, HERE)
import gen  # noqa: E402
import scan  # noqa: E402
import funcmap  # noqa: E402

REF_RE = re.compile(r'(?i)falloutnv\.esm:([0-9A-Fa-f]{6})')
PARAM_KEYS = {
    'PartyTether': {'cells', 'gather'}, 'PartyWarp': {'markers', 'mode', 'absent'},
    'SetPlayerControls': {'scope', 'release', 'timeout_s', 'masks'}, 'Barrier': {'step'}, 'PerPlayerScene': set(),
    'SlideshowEnd': {'run', 'lock', 'release'}, 'CompanionOwner': {'owner'}, 'SaveSuppress': set(),
    'FastTravelLock': {'scope'}, 'PartyAwareCond': {'funcs'}, 'QuestItemDup': {'recipient'}, 'RewardAll': set(),
    'RemoveItemPerOwner': set(), 'ShareKarmaRep': set(), 'InitiatorDialogue': set(), 'ExcludeFromGate': set(),
}
SETUP_CMD = re.compile(r'^(startquest|setstage|setobjectivedisplayed|setobjectivecompleted) ([0-9A-F]+)( \d+){0,2}$')
STATUS = {'supported', 'partial', 'missing'}


def read(name):
    with open(os.path.join(HERE, name), encoding='utf-8') as f:
        return f.read()


class Data:
    """Everything validate() needs from out/questdata, loaded once."""

    def __init__(self):
        c = gen.load()
        self.c = c
        self.quest_edids = {q['edid'] for q in c.d['quests'] if q.get('edid')}
        self.quest_by_id = {gen.hexid(q['ref']): q for q in c.d['quests']}
        refs = {q['ref'] for q in c.d['quests']} | set(c.forms) | set(c.refnames) | set(c.dials)
        refs |= {g['ref'] for g in c.d['globals']} | {f['ref'] for f in c.d['factions']} | {i['ref'] for i in c.allinfos}
        self.local_ids = {r.split(':')[1].upper() for r in refs if r.lower().startswith('falloutnv.esm:')}
        self.edids = set(self.quest_edids) | {v['edid'] for v in c.forms.values() if v.get('edid')} | \
            {v['edid'] for v in c.refnames.values() if v.get('edid')} | {g['edid'] for g in c.d['globals'] if g.get('edid')}
        self.info_topic = {i['ref']: i['topic'] for i in c.allinfos}
        self.rules = c.rules['quests']
        self.kinds = self._kinds()
        self.effects = self._effects()

    @staticmethod
    def _kinds():
        txt = open(os.path.join(REPO, 'synccore', 'src', 'rules.cpp'), encoding='utf-8').read()
        m = re.search(r'handled = \{([^}]*)\}', txt, re.S)
        return set(re.findall(r'"(\w+)"', m.group(1)))

    @staticmethod
    def _effects():
        txt = open(os.path.join(REPO, 'synccore', 'include', 'fnvcoop', 'effect.h'), encoding='utf-8').read()
        return set(re.findall(r'^struct (\w+)\s*\{', txt, re.M))


def load_bundle():
    b = {n: json.loads(read(n)) for n in ('inventory.json', 'sweep.json', 'gaps.json', 'rules.patch.json', 'dialogue_specs.json')}
    b['md'] = {n: read(n) for n in ('gaps.md', 'policy_table.md', 'policy_review.md', 'dialogue.md')}
    return b


def all_refs_in(text):
    return {m.upper() for m in REF_RE.findall(text)}


def validate(b, D, fresh=None):
    E = []
    err = E.append
    inv, sw, gp, pt, ds, md = b['inventory.json'], b['sweep.json'], b['gaps.json'], b['rules.patch.json'], b['dialogue_specs.json'], b['md']
    c = D.c

    # ---- 0. freshness of generated files (skipped for mutants of generated files)
    if fresh is not None:
        for n, txt in fresh.items():
            cur = b['md'][n] if n in b['md'] else None
            if n in b and not isinstance(b[n], str):
                if json.loads(txt) != b[n]:
                    err('stale generated file: ' + n)
            elif cur is not None and cur != txt:
                err('stale generated file: ' + n)

    # ---- 1. inventory vs quests.json
    inv_q = {q['edid']: q for q in inv['quests']}
    if set(inv_q) != set(gen.MEMBERS):
        err('inventory members differ: %s' % sorted(set(inv_q) ^ set(gen.MEMBERS)))
    for e in gen.MEMBERS:
        q = c.by[e]
        iq = inv_q.get(e)
        if not iq:
            continue
        if iq['ref'] != q['ref'] or iq['console_id'] != gen.hexid(q['ref']):
            err('%s: ref/console id mismatch' % e)
        if [s['index'] for s in iq['stages']] != sorted(s['index'] for s in q['stages']):
            err('%s: stage list differs from quests.json' % e)
        if sorted(o['index'] for o in iq['objectives']) != sorted(o['index'] for o in q['objectives']):
            err('%s: objective list differs from quests.json' % e)
        if not iq.get('tier'):
            err('%s: no risk tier' % e)
    s = inv['summary']
    if s['stages'] != sum(len(c.by[e]['stages']) for e in gen.MEMBERS) or s['quests'] != len(gen.MEMBERS):
        err('inventory summary counts wrong')
    if s['related_result_infos'] != len(c.related):
        err('inventory related info count wrong')

    # ---- 2. sweep: coverage + setup validity
    sw_q = {q['edid']: q for q in sw['quests']}
    excluded = {(x['quest'], x['stage']): x.get('reason') for x in sw.get('excluded', [])}
    for e in gen.MEMBERS:
        have = {st['index'] for st in sw_q.get(e, {}).get('stages', [])}
        for st in c.by[e]['stages']:
            if st['index'] not in have and not excluded.get((e, st['index'])):
                err('sweep misses stage %s %d without a reason' % (e, st['index']))
        for (qq, ix), why in excluded.items():
            if qq == e and ix in have:
                err('stage %s %d is both swept and excluded' % (qq, ix))
    nstage = sum(len(q['stages']) for q in sw['quests'])
    if sw['summary']['stages_swept'] != nstage:
        err('sweep summary stages_swept wrong')
    for q in sw['quests']:
        e = q['edid']
        qid = q['console_id']
        if D.quest_by_id.get(qid, {}).get('edid') != e:
            err('%s: console id %s does not resolve to the quest' % (e, qid))
        if q['stages'] and not q.get('reset'):
            err('%s: no reset op' % e)
        for r in q.get('reset', []):
            if r.get('op') != 'reset' or not r.get('quests'):
                err('%s: malformed reset op' % e)
            for x in r.get('quests', []):
                if x not in D.quest_by_id:
                    err('%s: reset id %s is not a quest' % (e, x))
        if not q['stages'] and not q.get('quest_script_runs') and not q.get('no_stages_reason'):
            err('%s: no stages, no quest_script_runs and no reason' % e)
        for st in (q['stages'] or [{'index': 0, 'action': None, 'setup': [], 'phase': 'normal'}]):
            if not q['stages']:
                for run in q.get('quest_script_runs', []):
                    if run.get('wait_s', 0) <= 0:
                        err('%s: run %s has no wait' % (e, run.get('name')))
                st = dict(st, variants=[])
                st['index'] = 0
            act = st.get('action') or {'cmd': 'setstage %s 0' % qid}
            if act.get('cmd') != 'setstage %s %d' % (qid, st['index']):
                err('%s %s: action is not the setstage under test' % (e, st['index']))
            if st.get('phase') not in ('normal', 'last'):
                err('%s %s: bad phase' % (e, st['index']))
            for chain in [st['setup']] + [v['pre'] for v in st.get('variants', [])] + [r['setup'] for r in q.get('quest_script_runs', [])]:
                started = set()
                for op in chain:
                    if op.get('op') == 'console':
                        m = SETUP_CMD.match(op['cmd'])
                        if not m:
                            err('%s %s: setup command not allowed: %s' % (e, st['index'], op['cmd']))
                            continue
                        verb, hid = m.group(1), m.group(2)
                        tq = D.quest_by_id.get(hid)
                        if not tq:
                            err('%s %s: setup id %s is not a quest' % (e, st['index'], hid))
                            continue
                        args = op['cmd'].split()[2:]
                        if verb == 'startquest':
                            started.add(hid)
                        elif verb == 'setstage':
                            if hid not in started and hid != qid:
                                pass
                            if int(args[0]) not in {x['index'] for x in tq['stages']}:
                                err('%s %s: setup stage %s is not a stage of %s' % (e, st['index'], args[0], tq['edid']))
                            if hid == qid and hid not in started:
                                err('%s %s: setstage before startquest' % (e, st['index']))
                            if hid == qid and int(args[0]) >= st['index'] and tq['edid'] == e and int(args[0]) != st['index']:
                                err('%s %s: predecessor stage %s is not lower' % (e, st['index'], args[0]))
                        else:
                            if int(args[0]) not in {x['index'] for x in tq['objectives']}:
                                err('%s %s: setup objective %s not in %s' % (e, st['index'], args[0], tq['edid']))
                            if hid == qid and hid not in started:
                                err('%s %s: objective set before startquest' % (e, st['index']))
                    elif op.get('op') == 'reset':
                        for x in op.get('quests', []):
                            if x not in D.quest_by_id:
                                err('%s %s: reset id %s is not a quest' % (e, st['index'], x))
                    elif op.get('op') == 'apply_txn':
                        for ef in op['effects']:
                            if ef.get('type') != 'SetScriptVar':
                                err('%s %s: apply_txn effect type %s not allowed' % (e, st['index'], ef.get('type')))
                                continue
                            tq = next((x for x in c.d['quests'] if x['ref'].lower() == ef['owner']), None)
                            idxs = {v['idx'] for v in ((tq or {}).get('script_data') or {}).get('vars', [])}
                            if not tq or ef['var'] not in idxs:
                                err('%s %s: SetScriptVar owner/var does not exist' % (e, st['index']))
                    else:
                        err('%s %s: unknown setup op %s' % (e, st['index'], op.get('op')))

    # ---- 3. every ref and edid resolves
    for name, obj in (('inventory', inv), ('sweep', sw), ('gaps', gp), ('rules.patch', pt), ('dialogue_specs', ds)):
        for r in all_refs_in(json.dumps(obj)):
            if r.upper() not in D.local_ids:
                err('%s: FormID FalloutNV.esm:%s does not resolve' % (name, r))
    for name, txt in md.items():
        for r in all_refs_in(txt):
            if r.upper() not in D.local_ids:
                err('%s: FormID FalloutNV.esm:%s does not resolve' % (name, r))

    # ---- 4. gaps
    funcs = gp['functions']
    nonq = [f for f in funcs if f['kind'] != 'query']
    if gp['summary']['occurrences'] != sum(f['total'] for f in nonq):
        err('gaps: summary occurrences != sum of function totals')
    if gp['summary']['functions'] != len(nonq):
        err('gaps: summary function count wrong')
    if sum(gp['summary']['by_overall_status'].values()) != gp['summary']['occurrences']:
        err('gaps: status totals do not add up')
    if gp['summary']['unclassified']:
        err('gaps: unclassified functions %s' % gp['summary']['unclassified'])
    for f in funcs:
        if f['kind'] == 'query':
            continue
        if f['effect'] is not None and f['effect'] not in D.effects:
            err('gaps: %s maps to unknown Effect %s' % (f['fn'], f['effect']))
        if f['capture'] not in STATUS or f['apply'] not in STATUS or f['overall'] not in STATUS:
            err('gaps: %s has a bad status' % f['fn'])
        if sum(f['counts'].values()) != f['total']:
            err('gaps: %s counts do not sum to total' % f['fn'])
        if f['overall'] != 'supported':
            if not f['locations'] and not f['dep_locations_total']:
                err('gaps: unsupported %s has no locations' % f['fn'])
        for x in f['locations'] + f['dep_locations']:
            if not x['script'] or not isinstance(x['line'], int) or x['line'] < 1:
                err('gaps: %s has a location without script/line' % f['fn'])
                break
    # independent re-scan: every statement-level function of the own scripts must be present in gaps.json
    seen = {f['fn'] for f in nonq}
    for it in gen.script_items(c):
        if it['scope'] == 'dep':
            continue
        for r in scan.scan(it['source']):
            if r['kind'] == 'cmd' and r['fn'].lower() not in seen:
                err('gaps: function %s (%s) missing from gaps.json' % (r['fn'], it['id']))
    for h in gp['hazards']:
        if h['count'] != h['direct'] + h['dep']:
            err('gaps: hazard %s counts inconsistent' % h['id'])

    # ---- 5. rules.patch.json
    rules_now = {(q, r['kind']) for q, v in D.rules.items() for r in v['rules']}
    caps = set(pt.get('capabilities_needed', {}))
    ids = set()
    for p in pt['patch']:
        if p['id'] in ids:
            err('patch: duplicate id %s' % p['id'])
        ids.add(p['id'])
        if p['op'] not in ('add', 'replace', 'remove'):
            err('patch %s: bad op' % p['id'])
        if p['kind'] not in D.kinds:
            err('patch %s: kind %s is not one of the %d existing kinds' % (p['id'], p['kind'], len(D.kinds)))
        if p['quest'] not in D.quest_edids:
            err('patch %s: quest %s does not exist' % (p['id'], p['quest']))
        has = (p['quest'], p['kind']) in rules_now
        if p['op'] in ('replace', 'remove') and not has:
            err('patch %s: %s %s/%s but the rule does not exist in rules.json' % (p['id'], p['op'], p['quest'], p['kind']))
        if p['op'] == 'add' and has:
            err('patch %s: add of an existing rule %s/%s (use replace)' % (p['id'], p['quest'], p['kind']))
        if p['op'] != 'remove':
            extra = set(p.get('params', {})) - PARAM_KEYS.get(p['kind'], set())
            if extra:
                err('patch %s: unknown params %s for %s' % (p['id'], sorted(extra), p['kind']))
            for key in ('cells', 'markers'):
                for e in p.get('params', {}).get(key, []):
                    if e not in D.edids:
                        err('patch %s: %s %s does not resolve' % (p['id'], key, e))
            if p['kind'] == 'PartyAwareCond':
                for fn, red in p['params'].get('funcs', {}).items():
                    if red not in funcmap.REDUCTIONS:
                        err('patch %s: reduction %s unknown' % (p['id'], red))
        if p['quest'] in D.quest_edids and p['quest'] not in D.rules and p['op'] == 'add' and not (p.get('tier') and p.get('questline')):
            err('patch %s: new rules entry for %s needs tier + questline' % (p['id'], p['quest']))
        for r in p.get('requires', []):
            if r not in caps:
                err('patch %s: requires unknown capability %s' % (p['id'], r))
        if not p.get('reason'):
            err('patch %s: no reason' % p['id'])
    for x in pt.get('cross_lane', []):
        if x['quest'] not in D.quest_edids:
            err('cross_lane %s: quest missing' % x['id'])
        for k in x['add']:
            if k not in D.kinds:
                err('cross_lane %s: kind %s unknown' % (x['id'], k))
        for k, prm in x.get('params', {}).items():
            if k not in x['add']:
                err('cross_lane %s: params for %s which is not added' % (x['id'], k))
            if set(prm) - PARAM_KEYS.get(k, set()):
                err('cross_lane %s: unknown params for %s' % (x['id'], k))
            for key in ('cells', 'markers'):
                for e in prm.get(key, []):
                    if e not in D.edids:
                        err('cross_lane %s: %s %s does not resolve' % (x['id'], key, e))
        for r in x.get('requires', []):
            if r not in caps:
                err('cross_lane %s: requires unknown capability %s' % (x['id'], r))
    for pr in pt.get('proposals', []):
        if not pr.get('justification') or not pr.get('evidence'):
            err('proposal %s lacks justification/evidence' % pr.get('id'))

    # ---- 6. dialogue specs
    if len(ds['specs']) < 15:
        err('dialogue_specs: fewer than 15 specs')
    for sp in ds['specs']:
        for st in sp['steps']:
            if st['action'] == 'pick':
                if D.info_topic.get(st['info']) != st['topic']:
                    err('dialogue %s: info %s is not in topic %s' % (sp['id'], st['info'], st['topic']))
                if st['topic'] not in c.dials:
                    err('dialogue %s: topic %s unknown' % (sp['id'], st['topic']))
        for q in sp['quests']:
            if q not in D.quest_edids:
                err('dialogue %s: quest %s unknown' % (sp['id'], q))

    # ---- 7. prose docs mention what they must
    pr = md['policy_review.md']
    for e in gen.MEMBERS:
        if e not in pr:
            err('policy_review.md does not mention %s' % e)
    for p in pt['patch']:
        if p['id'] not in pr:
            err('policy_review.md does not mention patch %s' % p['id'])
    for p in pt.get('proposals', []) + pt.get('cross_lane', []):
        if p['id'] not in pr:
            err('policy_review.md does not mention %s' % p['id'])
    for cid in caps:
        if cid not in pr:
            err('policy_review.md does not mention capability %s' % cid)
    for sp in ds['specs']:
        if sp['id'] not in md['dialogue.md']:
            err('dialogue.md does not mention %s' % sp['id'])
    return E


# ----------------------------------------------------------------------------------------------- mutants
def mutants(b):
    def m_drop_stage(x):
        x['sweep.json']['quests'][1]['stages'].pop(0)

    def m_bad_form(x):
        x['sweep.json']['quests'][0]['stages'][0]['setup'][0]['cmd'] = 'startquest FFFFFF'

    def m_bad_pred(x):
        for q in x['sweep.json']['quests']:
            for st in q['stages']:
                for op in st['setup']:
                    if op['cmd'].startswith('setstage'):
                        op['cmd'] = op['cmd'].rsplit(' ', 1)[0] + ' 4242'
                        return

    def m_no_start(x):
        for q in x['sweep.json']['quests']:
            for st in q['stages']:
                if len(st['setup']) > 1:
                    st['setup'] = [op for op in st['setup'] if not op['cmd'].startswith('startquest')]
                    return

    def m_inv_stage(x):
        x['inventory.json']['quests'][0]['stages'][0]['index'] += 1

    def m_gap_count(x):
        x['gaps.json']['functions'][0]['total'] += 1

    def m_gap_effect(x):
        for f in x['gaps.json']['functions']:
            if f['effect']:
                f['effect'] = 'TeleportEveryone'
                return

    def m_gap_loc(x):
        for f in x['gaps.json']['functions']:
            if f['kind'] != 'query' and f['overall'] != 'supported':
                f['locations'] = []
                f['dep_locations'] = []
                f['dep_locations_total'] = 0
                return

    def m_gap_drop_fn(x):
        x['gaps.json']['functions'] = [f for f in x['gaps.json']['functions'] if f['fn'] != 'setstage']

    def m_gap_ref(x):
        x['gaps.json']['functions'][0]['note'] = 'see FalloutNV.esm:ABCDEF'

    def m_patch_kind(x):
        x['rules.patch.json']['patch'][0]['kind'] = 'WarpEveryone'

    def m_patch_quest(x):
        x['rules.patch.json']['patch'][1]['quest'] = 'VMQYesMan99'

    def m_patch_replace_missing(x):
        x['rules.patch.json']['patch'][0]['quest'] = 'VMQYesMan02'

    def m_patch_cell(x):
        x['rules.patch.json']['patch'][0]['params']['cells'].append('Lucky38NoSuchCell')

    def m_patch_param(x):
        x['rules.patch.json']['patch'][0]['params']['teleport'] = True

    def m_patch_cap(x):
        x['rules.patch.json']['patch'][3]['requires'] = ['C99']

    def m_dlg_info(x):
        for st in x['dialogue_specs.json']['specs'][0]['steps']:
            if st['action'] == 'pick':
                st['info'] = 'FalloutNV.esm:000001'

    def m_md_quest(x):
        x['md']['policy_review.md'] = x['md']['policy_review.md'].replace('VHDIndependentBattle', 'VHDIndependent')

    def m_md_ref(x):
        x['md']['dialogue.md'] += '\nsee FalloutNV.esm:FFFFFF\n'

    def m_run_var(x):
        for q in x['sweep.json']['quests']:
            for r in q.get('quest_script_runs', []):
                for op in r['setup']:
                    for ef in op.get('effects', []):
                        ef['var'] = 9999
                        return

    def m_md_patch_id(x):
        x['md']['policy_review.md'] = x['md']['policy_review.md'].replace('P04', 'P--')

    return [
        ('sweep drops a stage', m_drop_stage), ('sweep setup uses an unknown FormID', m_bad_form),
        ('sweep predecessor stage does not exist', m_bad_pred), ('sweep setstage before startquest', m_no_start),
        ('inventory stage index changed', m_inv_stage), ('gaps total miscounted', m_gap_count),
        ('gaps maps to a fake Effect', m_gap_effect), ('gaps unsupported function has no location', m_gap_loc),
        ('gaps loses a function the scripts call', m_gap_drop_fn), ('gaps carries an unresolvable FormID', m_gap_ref),
        ('patch uses a 17th rule kind', m_patch_kind), ('patch targets an unknown quest', m_patch_quest),
        ('patch replaces a rule that is not there', m_patch_replace_missing), ('patch cell does not resolve', m_patch_cell),
        ('patch param key invented', m_patch_param), ('patch requires unknown capability', m_patch_cap),
        ('dialogue spec info not in topic', m_dlg_info), ('policy_review forgets a quest', m_md_quest),
        ('dialogue.md carries an unresolvable FormID', m_md_ref), ('policy_review forgets a patch id', m_md_patch_id),
        ('script-only run sets a variable that does not exist', m_run_var),
    ]


def main():
    drift = gen.live_drift()
    if drift:
        print('NOTE: live docs/quest-policies/rules.json differs from the pinned fixture for %s (informational; run gen.py --refresh-fixtures then gen.py to re-pin)' % ', '.join(sorted(drift)))
    D = Data()
    fresh = gen.build_all()
    b = load_bundle()
    errs = validate(b, D, fresh)
    for e in errs:
        print('FAIL:', e)
    n_ok = 1
    if errs:
        print('real bundle: %d errors' % len(errs))
        return 1
    print('real bundle: 0 errors (freshness, inventory, sweep coverage + setup, ids, gaps, patch, dialogue, prose)')
    # mutants (generated-file mutants run without the freshness check so the structural checks themselves must catch them)
    bad = 0
    for name, fn in mutants(b):
        mb = copy.deepcopy(b)
        fn(mb)
        e = validate(mb, D, None)
        if not e:
            print('MUTANT SURVIVED:', name)
            bad += 1
        else:
            n_ok += 1
    # the freshness check itself
    mb = copy.deepcopy(b)
    mb['gaps.json']['summary']['related_infos'] += 1
    if not validate(mb, D, fresh):
        print('MUTANT SURVIVED: stale gaps.json undetected by freshness check')
        bad += 1
    else:
        n_ok += 1
    total = len(mutants(b)) + 1
    print('mutation twin: %d/%d mutants caught' % (total - bad, total))
    if bad:
        return 1
    print('ALL OK')
    return 0


if __name__ == '__main__':
    sys.exit(main())
