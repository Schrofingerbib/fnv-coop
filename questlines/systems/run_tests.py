"""python -I questlines/systems/run_tests.py   -> prints ALL OK and exits 0.

Validates every generated deliverable against the quest data (IDs resolve, every stage swept or excluded with a reason, patch keys exist,
effect names exist in synccore/effect.h, gap locations re-read from the script sources) and then runs mutation twins: broken variants that
the validators MUST reject.
"""
import copy
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import build  # noqa: E402
import census  # noqa: E402
import generate  # noqa: E402
import qlib  # noqa: E402
import rulegen  # noqa: E402
import scope  # noqa: E402

EFFECT_H = os.path.join(qlib.ROOT, 'synccore', 'include', 'fnvcoop', 'effect.h')
CMD_OK = re.compile(r'^(setstage [0-9A-F]{8} \d+|startquest [0-9A-F]{8}|set [0-9A-F]{8}\.[A-Za-z0-9_]+ to -?\d+)$')
HEX_IN_CMD = re.compile(r'\b([0-9A-F]{8})\b')


def effect_names():
    src = open(EFFECT_H, encoding='utf-8').read()
    return set(re.findall(r'^struct (\w+)', src, re.M))


class Ctx:
    def __init__(self):
        self.Q = qlib.quest_by_edid()
        self.graph = qlib.questgraph()
        self.line = {n['id']: n['line'] for n in self.graph['nodes']}
        self.res = build.resolver()
        self.hexes = set()
        for q in qlib.quests():
            self.hexes.add(build.hexid(q['ref']))
        for src in (qlib.forms(), qlib.refnames()):
            for ref in src:
                self.hexes.add(build.hexid(ref))
        self.effects = effect_names()
        self.rules_json = qlib.rules()['quests']
        self.sources = None

    def script_sources(self):
        """(quest edid, where) -> source, for the quest script, stage scripts and INFO begin/end of every quest."""
        if self.sources is None:
            s = {}
            ibq = scope.infos_by_quest()
            for q in qlib.quests():
                for kind, loc, src in scope.owned_scripts(q, ibq):
                    s[(q['edid'], loc)] = src
            self.sources = s
        return self.sources


def v_scope(recs, C):
    err = []
    want = {e for e, l in C.line.items() if l in scope.MY_LINES}
    comp = set(scope.companion_membership())
    have = {r['edid'] for r in recs}
    for e in sorted(want - have):
        err.append('scope: unassigned-bucket quest missing from scope: ' + e)
    for e in sorted(comp - have):
        err.append('scope: companion quest missing from scope: ' + e)
    for e in sorted(have - want - comp):
        err.append('scope: quest not in own lines and not companion: ' + e)
    if len(have) != len(recs):
        err.append('scope: duplicate quests')
    for r in recs:
        if r['edid'] not in C.Q:
            err.append('scope: unknown quest ' + r['edid'])
        elif r['overlap'] != (C.line[r['edid']] not in scope.MY_LINES):
            err.append('scope: overlap flag wrong for ' + r['edid'])
    return err


def v_types(types, recs, C):
    err = []
    seen = set()
    for r in types['quests']:
        if r['edid'] in seen:
            err.append('types: duplicate ' + r['edid'])
        seen.add(r['edid'])
        if r['type'] not in scope.TYPES:
            err.append('types: bad type %s for %s' % (r['type'], r['edid']))
        if r['policy'] not in scope.POLICIES:
            err.append('types: bad policy %s for %s' % (r['policy'], r['edid']))
        if (r['type'] == 'debug') != (r['policy'] == 'exclude'):
            err.append('types: debug<->exclude mismatch for ' + r['edid'])
        if not r['policy_reason']:
            err.append('types: no reason for ' + r['edid'])
    if seen != {r['edid'] for r in recs}:
        err.append('types: classification does not cover the scope')
    for e in scope.OVERRIDES:
        if e not in C.Q:
            err.append('types: override for unknown quest ' + e)
    for e in list(scope.DEBUG) + sorted(scope.ENDING):
        if e not in C.Q:
            err.append('types: curated EDID does not exist: ' + e)
    dbg = {r['edid'] for r in types['quests'] if r['type'] == 'debug'}
    if dbg != set(scope.DEBUG):
        err.append('types: debug set differs from owner-decision list')
    if sum(t['quests'] for t in types['types']) != len(recs):
        err.append('types: type table does not sum to scope size')
    return err


def v_inventory(inv, C):
    err = []
    for q in inv['quests']:
        src = C.Q.get(q['edid'])
        if not src:
            err.append('inventory: unknown quest ' + q['edid'])
            continue
        if q['formid'] != build.hexid(src['ref']):
            err.append('inventory: formid mismatch ' + q['edid'])
        if [s['index'] for s in q['stages']] != [s['index'] for s in src['stages']]:
            err.append('inventory: stage list mismatch ' + q['edid'])
        if [o['index'] for o in q['objectives']] != [o['index'] for o in src['objectives']]:
            err.append('inventory: objective list mismatch ' + q['edid'])
        for d in q['prerequisites']['hard_depends_on'] + q['prerequisites']['hard_depended_by']:
            if d not in C.Q:
                err.append('inventory: dep edid unknown ' + d)
        if q['dialogue']['topics'] != len(src['topics']) or q['dialogue']['infos'] != len(src['infos']):
            err.append('inventory: dialogue counts mismatch ' + q['edid'])
    if inv['scope']['quests'] != len(inv['quests']) or inv['scope']['stages'] != sum(len(q['stages']) for q in inv['quests']):
        err.append('inventory: scope counts inconsistent')
    return err


def v_sweep(sw, inv, C):
    err = []
    swept = {}
    for q in sw['quests']:
        if q['edid'] not in C.Q:
            err.append('sweep: unknown quest ' + q['edid'])
            continue
        src = C.Q[q['edid']]
        if q['formid'] != build.hexid(src['ref']):
            err.append('sweep: formid mismatch ' + q['edid'])
        swept[q['edid']] = {s['index'] for s in q['stages']}
        for c in q['setup'] + [c for s in q['stages'] for c in s['cmds']]:
            if not CMD_OK.match(c):
                err.append('sweep: malformed command %r in %s' % (c, q['edid']))
                continue
            for h in HEX_IN_CMD.findall(c.split('.')[0]):
                if h not in C.hexes:
                    err.append('sweep: FormID %s in %r does not resolve' % (h, c))
        for s in q['stages']:
            if s['cmds'] != ['setstage %s %d' % (q['formid'], s['index'])]:
                err.append('sweep: stage command mismatch %s %s' % (q['edid'], s['index']))
    excl = {e['edid']: e for e in sw['exclusions']}
    stageless = {e['edid'] for e in sw['stageless']}
    for q in inv['quests']:
        e = q['edid']
        want = {s['index'] for s in q['stages']}
        if e in excl:
            if not excl[e]['reason']:
                err.append('sweep: exclusion without reason ' + e)
            if q['policy'] != 'exclude':
                err.append('sweep: excluded quest is not policy=exclude ' + e)
        elif not want:
            if e not in stageless:
                err.append('sweep: stageless quest not listed ' + e)
        elif swept.get(e) != want:
            err.append('sweep: stages not fully covered for %s: missing %s' % (e, sorted(want - swept.get(e, set()))))
    if sw['summary']['stages'] != sum(len(v) for v in swept.values()):
        err.append('sweep: summary stage count wrong')
    for p in sw['probes']:
        for c in p['cmds']:
            if not CMD_OK.match(c):
                err.append('sweep: malformed probe command ' + c)
    return err


def v_census(cen, recs, C):
    err = []
    n_stmt = cen['meta']['statements']
    if sum(r['count'] for r in cen['rows']) != n_stmt:
        err.append('census: row counts %d != statements %d' % (sum(r['count'] for r in cen['rows']), n_stmt))
    for r in cen['rows']:
        if r['l5_status'] not in (census.SUP, census.PAR, census.MIS, census.LOC):
            err.append('census: bad status for ' + r['fn'])
        if r['effect']:
            for tok in re.split(r'[/(),<]', r['effect']):
                tok = tok.strip()
                if tok and re.match(r'^[A-Z]\w+$', tok) and tok not in C.effects and tok not in ('kAvExperience', 'Recipient', 'Experience', 'rank'):
                    err.append('census: effect %s (fn %s) not in synccore effect.h' % (tok, r['fn']))
        if r['count'] < 1 or r['scripts'] < 1:
            err.append('census: empty row ' + r['fn'])
    # independent recount of a few functions with a different method
    want = {'setstage': r'^\s*setstage\s', 'disableplayercontrols': r'^\s*disableplayercontrols\b', 'placeatme': r'\bplaceatme\b'}
    got = {k: 0 for k in want}
    for _k, _o, _l, src in qlib.iter_all_scripts():
        for raw in src.splitlines():
            line = qlib.strip_comment(raw)
            for k, rx in want.items():
                if k == 'placeatme':
                    if re.match(r'^\s*(\w+\.)?placeatme\b', line, re.I):
                        got[k] += 1
                elif re.match(rx, line, re.I):
                    got[k] += 1
    rows = {r['fn']: r for r in cen['rows']}
    for k, v in got.items():
        if rows[k]['count'] != v:
            err.append('census: independent recount of %s: %d vs %d' % (k, v, rows[k]['count']))
    bl = cen['hook_backlog']
    if any(b['status'] not in (census.MIS, census.PAR) for b in bl):
        err.append('census: backlog contains supported/local rows')
    sev = {'blocker': 3, 'major': 2, 'minor': 1, 'low': 0, 'info': 0}
    if any(sev[bl[i]['severity']] < sev[bl[i + 1]['severity']] for i in range(len(bl) - 1)):
        err.append('census: backlog not ranked by severity')
    return err


def v_gaps(gaps, recs, C):
    err = []
    src = C.script_sources()
    qs = {r['edid'] for r in recs}
    for it in gaps['items']:
        if it['quest'] not in qs:
            err.append('gaps: quest not in scope ' + it['quest'])
            continue
        s = src.get((it['quest'], it['where']))
        if s is None:
            err.append('gaps: no script at %s %s' % (it['quest'], it['where']))
            continue
        lines = s.splitlines()
        if not (1 <= it['line'] <= len(lines)):
            err.append('gaps: line out of range %s %s:%s' % (it['quest'], it['where'], it['line']))
            continue
        if it['raw'].lower() not in lines[it['line'] - 1].lower():
            err.append('gaps: %s not on line %s of %s %s' % (it['raw'], it['line'], it['quest'], it['where']))
        if it['l5_status'] == census.SUP:
            err.append('gaps: supported call listed ' + it['fn'])
    if sum(gaps['counts']['by_severity'].values()) != len(gaps['items']):
        err.append('gaps: severity counts do not sum')
    return err[:20]


def v_patch(patch, recs, C):
    err = []
    allowed = set(rulegen.KINDS16)
    for sect in ('add', 'modify'):
        for e, v in patch[sect].items():
            if e not in C.Q:
                err.append('patch: unknown quest ' + e)
            rs = v['rules'] if sect == 'add' else v['add_rules']
            for r in rs:
                if r['kind'] not in allowed:
                    err.append('patch: bad kind %s on %s' % (r['kind'], e))
                try:
                    json.dumps(r['params'])
                except TypeError:
                    err.append('patch: params not serialisable ' + e)
            if sect == 'modify':
                if e not in C.rules_json:
                    err.append('patch: modify target not in rules.json ' + e)
                for k in v['update_params']:
                    if k not in {x['kind'] for x in C.rules_json[e]['rules']} or k not in allowed:
                        err.append('patch: update_params for absent kind %s %s' % (e, k))
                for k in v['remove_kinds']:
                    if k not in {x['kind'] for x in C.rules_json[e]['rules']}:
                        err.append('patch: removes kind that is absent %s %s' % (e, k))
            else:
                if e in C.rules_json:
                    err.append('patch: add for quest already in rules.json ' + e)
    pol = {r['edid']: r['policy'] for r in recs}
    for e, p in pol.items():
        kinds = {x['kind'] for x in (patch['add'].get(e, {}).get('rules') or [])}
        if p == 'exclude' and kinds != {'ExcludeFromGate'}:
            err.append('patch: excluded quest lacks ExcludeFromGate ' + e)
        if p != 'exclude' and 'ExcludeFromGate' in kinds:
            err.append('patch: non-excluded quest has ExcludeFromGate ' + e)
    if not patch['proposals']:
        err.append('patch: no proposals documented')
    return err


def v_companions(comp, C):
    err = []
    for n, c in comp['companions'].items():
        for r, ref in c['refs'].items():
            if not ref:
                err.append('companions: ref does not resolve: ' + r)
        for p, ref in c['recruit_perk_on_player'].items():
            if not ref or qlib.forms().get(ref, qlib.refnames().get(ref, {})).get('sig') != 'PERK':
                err.append('companions: perk does not resolve to a PERK: ' + p)
        if c['home_marker'] and not c['home_marker_ref']:
            err.append('companions: home marker unresolved ' + c['home_marker'])
        if c['counts']['recruit'] < 1 or c['counts']['dismiss'] < 1 or c['counts']['perk_add'] < 1:
            err.append('companions: %s has no recruit/dismiss/perk evidence' % n)
        for q in c['personal_quests'] + c['dialogue_quests'] + c['timer_quests']:
            if q not in C.Q:
                err.append('companions: quest unknown ' + q)
    vn = C.Q['VNPCFollowers']['script_data']['source']
    for v in comp['follower_limit']['evidence_vars']:
        if v not in vn:
            err.append('companions: limit var not in VNPCFollowers ' + v)
    if len(comp['follower_limit']['evidence_vars']) < 4:
        err.append('companions: follower limit vars not all found')
    if not comp['wheel_topics']['topics']:
        err.append('companions: wheel topics empty')
    return err


def all_validators(d, C):
    return (v_scope(d['recs'], C) + v_types(d['types'], d['recs'], C) + v_inventory(d['inventory'], C)
            + v_sweep(d['sweep'], d['inventory'], C) + v_census(d['census'], d['recs'], C) + v_gaps(d['gaps'], d['recs'], C)
            + v_patch(d['patch'], d['recs'], C) + v_companions(d['companions'], C))


# ---------------------------------------------------------------- mutation twins
def mutants():
    """name -> (mutator(d) , validator name list that must object)"""
    def m_bogus_quest(d):
        d['inventory']['quests'].append(dict(copy.deepcopy(d['inventory']['quests'][0]), edid='VNotARealQuest'))

    def m_bad_formid(d):
        d['inventory']['quests'][0]['formid'] = 'DEADBEEF'

    def m_drop_stage(d):
        for q in d['sweep']['quests']:
            if len(q['stages']) > 1:
                q['stages'].pop()
                return

    def m_fabricated_hex(d):
        q = d['sweep']['quests'][0]
        q['stages'][0]['cmds'] = ['setstage 0FFFFFFF %d' % q['stages'][0]['index']]

    def m_bad_kind(d):
        e = next(iter(d['patch']['add']))
        d['patch']['add'][e]['rules'].append({'kind': 'TeleportEveryone', 'params': {}, 'origin': 'curated'})

    def m_gap_line(d):
        d['gaps']['items'][0]['line'] += 1000

    def m_debug_not_excluded(d):
        for r in d['types']['quests']:
            if r['type'] == 'debug':
                r['policy'] = 'shared'
                return

    def m_effect(d):
        for r in d['census']['rows']:
            if r['effect']:
                r['effect'] = 'TeleportParty'
                return

    def m_census_count(d):
        d['census']['rows'][0]['count'] += 1

    def m_companion_ref(d):
        d['companions']['companions']['Boone']['refs']['CraigBooneREF'] = None

    def m_scope_drop(d):
        d['recs'].pop()

    def m_supported_gap(d):
        d['gaps']['items'][0]['l5_status'] = census.SUP

    def m_exclusion_reason(d):
        if d['sweep']['exclusions']:
            d['sweep']['exclusions'][0]['reason'] = ''

    def m_patch_missing_exclude(d):
        for e, v in d['patch']['add'].items():
            if v['rules'] and v['rules'][0]['kind'] == 'ExcludeFromGate':
                v['rules'] = [{'kind': 'RewardAll', 'params': {}, 'origin': 'curated'}]
                return

    return {k[2:]: f for k, f in list(locals().items()) if k.startswith('m_')}


def run_mutants(d, C):
    caught, missed = [], []
    for name, fn in mutants().items():
        dd = copy.deepcopy(d)
        fn(dd)
        errs = all_validators(dd, C)
        (caught if errs else missed).append(name)
    return caught, missed


def main():
    d = generate.compute()
    C = Ctx()
    fails = []
    errs = all_validators(d, C)
    print('validators: %d problem(s)' % len(errs))
    for e in errs[:30]:
        print('  FAIL', e)
    if errs:
        fails.append('validators')
    # determinism
    d2 = generate.compute()
    for k in ('inventory', 'census', 'sweep', 'patch', 'types'):
        if json.dumps(d[k], sort_keys=True) != json.dumps(d2[k], sort_keys=True):
            print('  FAIL nondeterministic output:', k)
            fails.append('determinism-' + k)
    # on-disk deliverables are fresh
    for name, key in (('inventory.json', 'inventory'), ('census.json', 'census'), ('sweep.json', 'sweep'), ('rules.patch.json', 'patch'),
                      ('gaps.json', 'gaps'), ('types.json', 'types'), ('companions.json', 'companions')):
        p = os.path.join(qlib.HERE, name)
        if not os.path.exists(p):
            print('  FAIL missing deliverable', name)
            fails.append('missing-' + name)
            continue
        disk = json.load(open(p, encoding='utf-8'))
        if json.dumps(disk, sort_keys=True) != json.dumps(json.loads(json.dumps(d[key], ensure_ascii=False)), sort_keys=True):
            print('  FAIL stale deliverable (run generate.py):', name)
            fails.append('stale-' + name)
    for name in ('gaps.md', 'policy_review.md', 'dialogue.md', 'census.md', 'types.md', 'companions.md'):
        p = os.path.join(qlib.HERE, name)
        if not os.path.exists(p) or os.path.getsize(p) < 500:
            print('  FAIL missing/empty', name)
            fails.append('doc-' + name)
    # mutation twins
    caught, missed = run_mutants(d, C)
    print('mutation twins: %d caught, %d missed' % (len(caught), len(missed)))
    for m in missed:
        print('  FAIL mutant not caught:', m)
        fails.append('mutant-' + m)
    # the unmutated data must be clean while every mutant is dirty (guards against a validator that always fails)
    if errs:
        print('  (validators already failing on clean data: mutation result is not meaningful)')
    c = d['census']
    print('quests %d, stages in sweep %d, census %d fns / %d calls, gap calls %d'
          % (len(d['recs']), d['sweep']['summary']['stages'], c['distinct_functions'], c['meta']['statements'],
             d['gaps']['counts']['unsupported_calls']))
    if fails:
        print('FAILED:', ', '.join(fails))
        return 1
    print('ALL OK')
    return 0


if __name__ == '__main__':
    sys.exit(main())
