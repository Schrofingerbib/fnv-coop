#!/usr/bin/env python
"""Legion questline test gate.  Run:  python -I questlines/legion/run_tests.py     (prints ALL OK, exit 0)

Validates inventory.json, sweep.json, gaps.json/gaps.md, rules.patch.json, policy_review.md, dialogue.md/dialogue_specs.json against
out/questdata (IDs resolve, stages covered or excluded with a reason, patch keys exist, citations point at real script lines), checks the
generator output is byte-identical to the committed files, and then runs a MUTATION TWIN: each validator is re-run on a deliberately broken
copy and must fail. A validator that accepts a broken artefact is itself a test failure.
"""
import copy
import filecmp
import json
import os
import re
import shutil
import sys
import tempfile
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import common as C  # noqa: E402
import fnmap  # noqa: E402
import gaps as G  # noqa: E402
import gen  # noqa: E402
import rules_patch as RP  # noqa: E402

ROOT = C.ROOT
KINDS = set(RP.KINDS)
STATUS = {'supported', 'partial', 'missing', 'n/a', 'unclassified'}
SEV = set(G.SEV_ORDER)
HEX8 = re.compile(r'\b[0-9A-F]{8}\b')
SETUP_CMD = re.compile(r'^(startquest [0-9A-F]{8}|setstage [0-9A-F]{8} \d+|set [0-9A-F]{8} to -?\d+(\.\d+)?)$')
CITE = re.compile(r'\[\[([^\]|:]+):(\d+)\|([^\]]+)\]\]')


def load(name):
    with open(os.path.join(HERE, name), encoding='utf-8') as f:
        return json.load(f)


def read(name):
    with open(os.path.join(HERE, name), encoding='utf-8') as f:
        return f.read()


def effect_names():
    txt = open(os.path.join(ROOT, 'synccore', 'include', 'fnvcoop', 'effect.h'), encoding='utf-8').read()
    return set(re.findall(r'^struct (\w+) \{', txt, re.M)) | {'CompleteAllObjectives'}


class Ctx:
    """Everything the validators need, loaded once."""

    def __init__(self):
        self.d = C.Data()
        self.scripts = G.collect_scripts(self.d)
        self.by_label = {}
        for s in self.scripts:
            self.by_label.setdefault(s['label'], []).append(s)
        self.effects = effect_names()
        self.names = {}
        for src in (self.d.forms, self.d.refnames):
            for r, v in src.items():
                if v.get('edid'):
                    self.names[v['edid'].lower()] = r
        for q in self.d.quests:
            if q.get('edid'):
                self.names[q['edid'].lower()] = q['ref']
        for g in self.d.globals:
            if g.get('edid'):
                self.names[g['edid'].lower()] = g['ref']
        self.runtime_ids = {C.runtime_id(q['ref']): q for q in self.d.quests}
        self.runtime_ids.update({C.runtime_id(r): None for r in list(self.d.forms) + list(self.d.refnames)})
        self.runtime_ids.update({C.runtime_id(g['ref']): None for g in self.d.globals})

    def line_of(self, label, line):
        for s in self.by_label.get(label, []):
            ls = s['source'].replace('\r', '').split('\n')
            if 1 <= line <= len(ls):
                return ls[line - 1]
        return None


# ------------------------------------------------------------------ validators (each returns a list of error strings)
def v_inventory(cx, inv):
    err = []
    d = cx.d
    want = [e for e, _ in C.SCOPE]
    got = [q['edid'] for q in inv['quests']]
    if got != want:
        err.append('inventory quest list differs from scope: %s' % (set(want) ^ set(got)))
    for x in inv['quests']:
        q = d.by_edid.get(x['edid'])
        if not q:
            err.append('inventory: unknown quest %s' % x['edid'])
            continue
        if x['ref'] != q['ref'] or x['formid'] != C.runtime_id(q['ref']):
            err.append('inventory: id mismatch for %s' % x['edid'])
        if [s['index'] for s in x['stages']] != sorted(s['index'] for s in q['stages']):
            err.append('inventory: stage list of %s differs from quests.json' % x['edid'])
        if [o['index'] for o in x['objectives']] != [o['index'] for o in q['objectives']]:
            err.append('inventory: objective list of %s differs' % x['edid'])
        if x['dialogue']['infos'] != len(d.infos.get(q['ref'], [])):
            err.append('inventory: info count of %s differs' % x['edid'])
        if x['tier'] != d.risk[q['ref']]['tier']:
            err.append('inventory: tier of %s differs' % x['edid'])
        if x['role'] == 'prereq' and not x.get('stages_excluded'):
            err.append('inventory: prereq %s has no exclusion reason' % x['edid'])
        for t in x['prerequisites']['triggered_by']:
            if not t['where'] or not t['line']:
                err.append('inventory: trigger without location in %s' % x['edid'])
        for g in x['prerequisites']['gated_on']:
            if g['quest'] not in d.by_edid:
                err.append('inventory: gated_on quest %s unknown' % g['quest'])
    c = inv['counts']
    if c['quests'] != len(inv['quests']) or c['stages'] != sum(len(x['stages']) for x in inv['quests']):
        err.append('inventory: counts do not add up')
    return err


def v_sweep(cx, sw, inv):
    err = []
    d = cx.d
    swq = {q['edid']: q for q in sw['quests']}
    excl = {(e['quest'], e['stage']): e for e in sw['excluded']}
    for e in sw['excluded']:
        if not e.get('reason'):
            err.append('sweep: exclusion without reason %s' % e)
    for x in inv['quests']:
        q = d.by_edid[x['edid']]
        stages = sorted(s['index'] for s in q['stages'])
        if x['role'] == 'prereq':
            for s in stages:
                if (x['edid'], s) not in excl:
                    err.append('sweep: prereq stage %s %d neither swept nor excluded' % (x['edid'], s))
            continue
        if x['edid'] not in swq:
            err.append('sweep: owned quest %s missing' % x['edid'])
            continue
        sq = swq[x['edid']]
        swept = sorted(s['index'] for s in sq['stages'])
        if swept != stages:
            err.append('sweep: %s stages %s != quests.json %s' % (x['edid'], swept, stages))
        for s in stages:
            if s not in swept and (x['edid'], s) not in excl:
                err.append('sweep: stage %s %d neither swept nor excluded' % (x['edid'], s))
        if sq['ref'] != q['ref'] or sq['formid'] != C.runtime_id(q['ref']):
            err.append('sweep: id mismatch %s' % x['edid'])
        # setup
        su = sq['setup']
        for c in su['quest']:
            err += check_cmd(cx, c, x['edid'], None)
        for si, steps in su['stages'].items():
            if int(si) not in stages:
                err.append('sweep: setup for unknown stage %s %s' % (x['edid'], si))
            last = -1
            for c in steps:
                err += check_cmd(cx, c, x['edid'], int(si))
                if c['kind'] == 'setstage':
                    if c['stage'] >= int(si):
                        err.append('sweep: setup stage %d is not before target %s %s' % (c['stage'], x['edid'], si))
                    if c['stage'] <= last:
                        err.append('sweep: setup out of order %s %s' % (x['edid'], si))
                    last = c['stage']
        if set(su['stages']) != {str(s) for s in stages}:
            err.append('sweep: setup missing stages for %s' % x['edid'])
    # plan format compatible with plugin/snapshot/tools/goodsprings_sweep.py load_plan()
    for q in sw['quests']:
        try:
            [s['index'] for s in q['stages']]
            q['edid'], q['ref']
        except Exception as e:  # noqa: BLE001
            err.append('sweep: load_plan format %s' % e)
    for p in sw['natural_path']:
        q = d.by_edid.get(p['quest'])
        if not q or p['stage'] not in [s['index'] for s in q['stages']] or p['formid'] != C.runtime_id(q['ref']):
            err.append('sweep: bad natural_path step %s' % p)
    n = sum(len(q['stages']) for q in sw['quests'])
    if sw['sweep_summary']['stages_swept'] != n - sum(1 for e in sw['excluded'] if e['quest'] in swq):
        err.append('sweep: summary count wrong')
    return err


def check_cmd(cx, c, edid, stage):
    err = []
    if not SETUP_CMD.match(c['cmd']):
        err.append('sweep: malformed console command %r' % c['cmd'])
        return err
    for h in HEX8.findall(c['cmd']):
        if h not in cx.runtime_ids:
            err.append('sweep: FormID %s in %r does not resolve' % (h, c['cmd']))
    if c['kind'] == 'setstage':
        q = cx.d.by_edid.get(c['quest'])
        if not q or c['stage'] not in [s['index'] for s in q['stages']]:
            err.append('sweep: setup %r targets a stage that does not exist' % c['cmd'])
        elif C.runtime_id(q['ref']) not in c['cmd']:
            err.append('sweep: setup %r id does not belong to quest %s' % (c['cmd'], c['quest']))
    return err


def v_gaps(cx, g):
    err = []
    vocab = cx.effects
    total = collections_counter()
    for f in g['functions']:
        if f['status'] not in STATUS or f['severity'] not in SEV:
            err.append('gaps: bad status/severity on %s' % f['function'])
        e = f['effect']
        if e and not (e in vocab or e.startswith('PartyAwareCond:') or e.startswith('guest-originated trigger')):
            err.append('gaps: effect %r of %s is not a synccore Effect' % (e, f['function']))
        if f['status'] in ('missing', 'partial', 'unclassified'):
            if len(f.get('locations', [])) != f['count']:
                err.append('gaps: %s lists %d locations for %d uses' % (f['function'], len(f.get('locations', [])), f['count']))
            for loc in f.get('locations', []):
                line = cx.line_of(loc['where'], loc['line'])
                if line is None:
                    err.append('gaps: location %s:%d does not exist' % (loc['where'], loc['line']))
                    continue
                if f['tag'] == 'guest-trigger':
                    if f['function'] not in line.lower():
                        err.append('gaps: %s:%d is not a %s block' % (loc['where'], loc['line'], f['function']))
                elif f['tag'] != 'condition-read' and f['function'] not in line.lower().replace('.', ' ') \
                        and not f['function'].startswith('set '):
                    # aliases (evp, sifh...) and 'set quest var'/'set ref var' are checked by their own spellings
                    if not any(a for a, full in C.ALIASES.items() if full == f['function'] and a in line.lower()):
                        err.append('gaps: %s:%d does not contain %r: %r' % (loc['where'], loc['line'], f['function'], line.strip()[:60]))
                if loc['quest'] not in cx.d.by_edid or dict(C.SCOPE).get(loc['quest']) == 'prereq':
                    err.append('gaps: location quest %s outside scope' % loc['quest'])
            for loc in f.get('locations', []):
                total[f['severity']] += 1
    sev = g['totals']['unsupported_by_severity']
    for k in G.SEV_ORDER:
        if sev.get(k, 0) != total.get(k, 0):
            err.append('gaps: severity total %s %s != listed %s' % (k, sev.get(k), total.get(k)))
    if sum(g['totals']['by_status'].values()) != g['totals']['gap_sites']:
        err.append('gaps: status totals do not add up')
    for k, v in g['hazards'].items():
        if v['count'] < len(v['sites']):
            err.append('gaps: hazard %s count < sample' % k)
    md = read('gaps.md')
    for f in g['functions']:
        if f['severity'] in ('blocker', 'high') and f['status'] != 'supported' and '`%s`' % f['function'] not in md:
            err.append('gaps.md: missing blocker/high function %s' % f['function'])
    return err


def collections_counter():
    import collections
    return collections.Counter()


def v_patch(cx, patch):
    err = []
    d = cx.d
    existing = d.rules['quests']
    for edid, v in patch['quests'].items():
        if edid not in d.by_edid:
            err.append('patch: quest %s not in quests.json' % edid)
            continue
        if v['op'] == 'merge' and edid not in existing:
            err.append('patch: merge target %s not in rules.json' % edid)
        if v['op'] == 'add' and edid in existing:
            err.append('patch: add of %s but it already exists in rules.json' % edid)
        have = {r['kind']: r for r in existing.get(edid, {}).get('rules', [])}
        for r in v['add_rules']:
            if r['kind'] not in KINDS:
                err.append('patch: unknown rule kind %s on %s' % (r['kind'], edid))
            if r['kind'] in have:
                err.append('patch: %s add_rules kind %s already present' % (edid, r['kind']))
            if not r.get('evidence'):
                err.append('patch: %s %s has no evidence' % (edid, r['kind']))
            err += check_params(cx, edid, r)
        for r in v['update_rules']:
            if r['kind'] not in have:
                err.append('patch: %s update of absent kind %s' % (edid, r['kind']))
            elif r['was'] != have[r['kind']]['params']:
                err.append('patch: %s %s "was" does not match rules.json' % (edid, r['kind']))
            err += check_params(cx, edid, r)
        for r in v['remove_rules']:
            if r['kind'] not in have:
                err.append('patch: %s removes absent kind %s' % (edid, r['kind']))
        if v['op'] == 'add' and 'tier' not in v:
            err.append('patch: add of %s lacks tier' % edid)
    return err


def check_params(cx, edid, r):
    err = []
    p = r['params']
    for key in ('markers', 'cells'):
        for name in p.get(key, []):
            if name.lower() not in cx.names:
                err.append('patch: %s %s %r does not resolve in the extracted data' % (edid, key, name))
    for fn, red in (p.get('funcs') or {}).items():
        if red not in ('initiator', 'any-party-member', 'any', 'min-over-party', 'entry:all-in,exit:any-in'):
            err.append('patch: %s unknown reduction %r' % (edid, red))
    return err


def v_cites(cx, text, name):
    err = []
    cites = CITE.findall(text)
    if not cites:
        err.append('%s: no citations found' % name)
    for label, line, sub in cites:
        got = cx.line_of(label.strip(), int(line))
        if got is None:
            err.append('%s: cite %s:%s does not exist' % (name, label, line))
        elif sub.strip().lower() not in got.lower():
            err.append('%s: cite %s:%s %r not in %r' % (name, label, line, sub.strip(), got.strip()[:70]))
    return err


def v_dialogue(cx, dia, specs, md):
    err = []
    d = cx.d
    for r in dia['rows']:
        i = d.info_by_ref.get(r['info'])
        if not i or r['topic'] != i['topic'] or r['formid'] != C.runtime_id(r['info']):
            err.append('dialogue: row %s does not match infos' % r['info'])
    ids = set()
    for s in specs['specs']:
        if s['id'] in ids:
            err.append('dialogue: duplicate spec id %s' % s['id'])
        ids.add(s['id'])
        for q in s['quests']:
            if q not in d.by_edid:
                err.append('dialogue: spec %s quest %s unknown' % (s['id'], q))
        for st in s['steps']:
            if st['action'] == 'pick':
                i = d.info_by_ref.get(st['info'])
                if not i or i['topic'] != st['topic']:
                    err.append('dialogue: spec %s pick %s does not resolve' % (s['id'], st['info']))
            if st['action'] == 'talk' and st.get('npc_ref') and cx.d.resolve(st['npc_ref']) is None:
                err.append('dialogue: spec %s npc_ref unresolved' % s['id'])
            if st['action'] == 'assert':
                b = st['expect'].get('both', {}).get('stage')
                if b:
                    q = d.by_edid.get(b['quest'])
                    if not q or b['value'] not in [x['index'] for x in q['stages']]:
                        err.append('dialogue: spec %s asserts nonexistent stage' % s['id'])
    for m in re.finditer(r'INFOs? ([0-9A-F]{6})', md):
        if 'FalloutNV.esm:' + m.group(1) not in d.info_by_ref:
            err.append('dialogue.md: INFO %s does not exist' % m.group(1))
    for m in re.finditer(r'INFO ([0-9A-F]{6}) (?:begin|end)', md):
        pass
    for sid in ids:
        if sid not in md:
            err.append('dialogue.md: spec %s not referenced' % sid)
    return err


def v_fixtures(cx):
    """Tokenizer + classifier on a hand-written script with known answers."""
    err = []
    src = '\r\n'.join(['scn Fixture', 'begin OnTrigger player', 'if player.GetItemCount Foo > 0', '  SetStage Q 10',
                       '  DisablePlayerControls', '  player.MoveTo SomeMarker', '  PlaceAtMe Foo', '  RewardXP 10', 'endif', 'end'])
    kq, kg = {'q'}, set()
    uses = C.tokenize_script(src, kq, kg, cx.d.cmd_table)
    got = {(u['cmd'], u['role']) for u in uses}
    for need in [('getitemcount', 'cond'), ('setstage', 'stmt'), ('disableplayercontrols', 'stmt'), ('movetomarker', 'stmt'),
                 ('placeatme', 'stmt'), ('rewardxp', 'stmt')]:
        if need not in got:
            err.append('fixture: tokenizer missed %s' % (need,))
    res = {}
    for u in uses:
        r = G.classify_use(u, 'dep_obj')
        res[u['cmd']] = r
    if not res.get('disableplayercontrols') or res['disableplayercontrols'][1]['sev'] != 'blocker':
        err.append('fixture: DisablePlayerControls must be a blocker gap')
    if not res.get('placeatme') or res['placeatme'][0] != 'missing':
        err.append('fixture: PlaceAtMe must be missing')
    if not res.get('setstage') or res['setstage'][0] != 'supported':
        err.append('fixture: SetStage must be supported')
    if not res.get('getitemcount') or res['getitemcount'][1]['tag'] != 'condition-read':
        err.append('fixture: player.GetItemCount must be a condition-read gap')
    return err


def v_regen(cx):
    """gen.py output must be byte-identical to the committed files."""
    err = []
    tmp = tempfile.mkdtemp(prefix='legion_regen_', dir=os.environ.get('TMP') or None)
    try:
        gen.main(['--out', tmp])
        for n in ('inventory.json', 'sweep.json', 'gaps.json', 'gaps.md', 'rules.patch.json', 'dialogue.md', 'dialogue_specs.json',
                  'dialogue_data.json'):
            if not filecmp.cmp(os.path.join(tmp, n), os.path.join(HERE, n), shallow=False):
                err.append('regen: %s differs from the committed file (run gen.py)' % n)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return err


# ------------------------------------------------------------------ mutation twin
def mutations(cx, art):
    """(name, validator-callable returning errors on the broken copy)."""
    out = []

    def m(name, fn):
        out.append((name, fn))

    def inv_stage():
        i = copy.deepcopy(art['inv'])
        i['quests'][0]['stages'][0]['index'] += 1
        return v_inventory(cx, i)
    m('inventory: shifted stage index', inv_stage)

    def inv_formid():
        i = copy.deepcopy(art['inv'])
        i['quests'][1]['formid'] = '00FFFFFF'
        return v_inventory(cx, i)
    m('inventory: wrong formid', inv_formid)

    def sw_drop():
        s = copy.deepcopy(art['sw'])
        s['quests'][0]['stages'].pop()
        return v_sweep(cx, s, art['inv'])
    m('sweep: stage silently dropped', sw_drop)

    def sw_bad_id():
        s = copy.deepcopy(art['sw'])
        q = next(q for q in s['quests'] if q['setup']['quest'])
        q['setup']['quest'][0]['cmd'] = 'startquest 0DEADBEE'
        return v_sweep(cx, s, art['inv'])
    m('sweep: invented FormID', sw_bad_id)

    def sw_order():
        s = copy.deepcopy(art['sw'])
        q = next(q for q in s['quests'] if q['edid'] == 'VMS35')
        steps = q['setup']['stages']['100']
        steps[0], steps[1] = steps[1], steps[0]
        return v_sweep(cx, s, art['inv'])
    m('sweep: setup chain out of order', sw_order)

    def sw_future():
        s = copy.deepcopy(art['sw'])
        q = next(q for q in s['quests'] if q['edid'] == 'VMS35')
        q['setup']['stages']['20'].append(copy.deepcopy(q['setup']['stages']['100'][-1]))
        return v_sweep(cx, s, art['inv'])
    m('sweep: setup stage after target', sw_future)

    def sw_noreason():
        s = copy.deepcopy(art['sw'])
        s['excluded'][0]['reason'] = ''
        return v_sweep(cx, s, art['inv'])
    m('sweep: exclusion without reason', sw_noreason)

    def g_line():
        g = copy.deepcopy(art['gaps'])
        f = next(f for f in g['functions'] if f['status'] == 'missing' and f['locations'])
        f['locations'][0]['line'] = 99999
        return v_gaps(cx, g)
    m('gaps: nonexistent line', g_line)

    def g_effect():
        g = copy.deepcopy(art['gaps'])
        f = next(f for f in g['functions'] if f['effect'] == 'SetPlayerControls')
        f['effect'] = 'TotallyMadeUpEffect'
        return v_gaps(cx, g)
    m('gaps: invented Effect type', g_effect)

    def g_count():
        g = copy.deepcopy(art['gaps'])
        g['totals']['unsupported_by_severity']['blocker'] += 1
        return v_gaps(cx, g)
    m('gaps: severity total off by one', g_count)

    def p_kind():
        p = copy.deepcopy(art['patch'])
        q = next(q for q in p['quests'].values() if q['add_rules'])
        q['add_rules'][0]['kind'] = 'InventedKind'
        return v_patch(cx, p)
    m('patch: new rule kind', p_kind)

    def p_quest():
        p = copy.deepcopy(art['patch'])
        p['quests']['VNoSuchQuest'] = copy.deepcopy(next(iter(p['quests'].values())))
        return v_patch(cx, p)
    m('patch: unknown quest key', p_quest)

    def p_cell():
        p = copy.deepcopy(art['patch'])
        for r in p['quests']['VHDLegionBattle']['update_rules']:
            if r['kind'] == 'PartyTether':
                r['params']['cells'].append('NoSuchCellAnywhere')
        return v_patch(cx, p)
    m('patch: unresolved cell', p_cell)

    def p_was():
        p = copy.deepcopy(art['patch'])
        p['quests']['VHDLegionBattle']['update_rules'][0]['was'] = {'scope': 'nobody'}
        return v_patch(cx, p)
    m('patch: stale "was"', p_was)

    def c_text():
        t = art['policy'].replace('[[FortLuciusScript:69|player.MoveTo VHDLegateStartMarker]]', '[[FortLuciusScript:69|player.MoveTo Nowhere]]', 1)
        return v_cites(cx, t, 'policy_review.md')
    m('policy: cite text altered', c_text)

    def c_line():
        t = art['policy'].replace('[[FortLuciusScript:69|', '[[FortLuciusScript:70|', 1)
        return v_cites(cx, t, 'policy_review.md')
    m('policy: cite line moved', c_line)

    def d_info():
        dd = copy.deepcopy(art['dia'])
        dd['rows'][0]['info'] = 'FalloutNV.esm:FFFFFF'
        return v_dialogue(cx, dd, art['specs'], art['dmd'])
    m('dialogue: unknown INFO', d_info)

    def d_stage():
        sp = copy.deepcopy(art['specs'])
        for st in sp['specs'][0]['steps']:
            if st['action'] == 'assert':
                st['expect']['both']['stage']['value'] = 12345
        return v_dialogue(cx, art['dia'], sp, art['dmd'])
    m('dialogue: spec asserts nonexistent stage', d_stage)

    def f_break():
        saved = fnmap.FN['disableplayercontrols']
        fnmap.FN['disableplayercontrols'] = dict(saved, capture='supported', apply='supported', sev='info')
        try:
            return v_fixtures(cx)
        finally:
            fnmap.FN['disableplayercontrols'] = saved
    m('fixtures: DisablePlayerControls marked supported', f_break)

    def f_tok():
        saved = C.tokenize_script
        C.tokenize_script = lambda src, kq, kg, ct: [u for u in saved(src, kq, kg, ct) if u['cmd'] != 'placeatme']
        try:
            return v_fixtures(cx)
        finally:
            C.tokenize_script = saved
    m('fixtures: tokenizer drops PlaceAtMe', f_tok)
    return out


def main():
    results = []

    def step(name, fn):
        try:
            errs = fn()
        except Exception:  # noqa: BLE001
            errs = ['EXCEPTION ' + traceback.format_exc()]
        results.append((name, errs))
        print('%-44s %s' % (name, 'ok' if not errs else 'FAIL (%d)' % len(errs)))
        for e in errs[:12]:
            print('    - ' + e)
        return not errs

    cx = Ctx()
    art = {'inv': load('inventory.json'), 'sw': load('sweep.json'), 'gaps': load('gaps.json'), 'patch': load('rules.patch.json'),
           'policy': read('policy_review.md'), 'dia': load('dialogue_data.json'), 'specs': load('dialogue_specs.json'),
           'dmd': read('dialogue.md')}
    step('inventory resolves against quests.json', lambda: v_inventory(cx, art['inv']))
    step('sweep covers every stage / setup valid', lambda: v_sweep(cx, art['sw'], art['inv']))
    step('gaps locations exist, totals add up', lambda: v_gaps(cx, art['gaps']))
    step('rules.patch.json keys/kinds/params', lambda: v_patch(cx, art['patch']))
    step('policy_review.md citations', lambda: v_cites(cx, art['policy'], 'policy_review.md'))
    step('dialogue data/specs/md resolve', lambda: v_dialogue(cx, art['dia'], art['specs'], art['dmd']))
    step('tokenizer/classifier fixtures', lambda: v_fixtures(cx))
    step('generator output == committed files', lambda: v_regen(cx))
    # mutation twin
    miss = []
    for name, fn in mutations(cx, art):
        try:
            caught = bool(fn())
        except Exception:  # noqa: BLE001
            caught = True
        print('%-60s %s' % ('mutant: ' + name, 'caught' if caught else 'NOT CAUGHT'))
        if not caught:
            miss.append(name)
    results.append(('mutation twin', ['mutant not caught: %s' % m_ for m_ in miss]))
    bad = [n for n, e in results if e]
    if bad:
        print('FAILED: %s' % ', '.join(bad))
        return 1
    print('ALL OK')
    return 0


if __name__ == '__main__':
    sys.exit(main())
