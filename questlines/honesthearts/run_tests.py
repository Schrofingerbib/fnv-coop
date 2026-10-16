"""Tests for the Honest Hearts questline deliverables.   python -I questlines/honesthearts/run_tests.py   -> prints ALL OK, exit 0.

Validators work on the *written* JSON/MD files and on freshly generated (JSON round-tripped) artifacts; the mutation twins run the
generator with a deliberate defect (or corrupt a valid artifact) and require the validators to reject it.
"""
import collections
import copy
import json
import os
import re
import sys
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import build  # noqa: E402
import fnmap  # noqa: E402
import qlib  # noqa: E402
import scan  # noqa: E402

REPO = qlib.REPO
ALLOWED_STATUS = {'supported', 'partial', 'missing', 'n/a'}
ALLOWED_OVERALL = {'supported', 'partial', 'missing', 'not_needed'}
ALLOWED_SEV = {'none', 'low', 'medium', 'high', 'blocker'}
ALLOWED_KIND = {'read', 'mutate', 'presentation', 'npc-ai', 'engine'}
REDUCTIONS = {'initiator', 'any-party-member', 'any', 'min-over-party', 'entry:all-in,exit:any-in'}
ROLES = {'story', 'story-optional', 'sidequest', 'dialogue', 'slideshow', 'encounter-system', 'scene-script', 'companions', 'fail-tracker', 'system'}
SWEEP_HAZARDS = {'lock', 'player-move', 'travel', 'save', 'spawn', 'companion', 'mass-fail'}
DLG_ACTIONS = {'setup', 'talk', 'pick', 'pick_info', 'pick_nowait', 'parallel', 'leave', 'event', 'assert'}
DLG_EXPECT = {'spectator_lines', 'script_runs', 'script_var', 'stage', 'objective_completed', 'xp_each_player', 'reputation_shared', 'denied',
              'closed', 'closed_after', 'lock_released', 'converged', 'opened', 'spectators'}
FILES_JSON = ['inventory.json', 'sweep.json', 'gaps.json', 'rules.patch.json', 'dialogue_specs.json', 'blockers.json']
FILES_MD = ['gaps.md', 'dialogue.md', 'policy_review.md']
HAZ_CLASSES = ['player-targeted effects', 'spawns', 'companions', 'essential / ghost flags', 'MoveTo / teleports', 'cell transitions',
               'cinematics / control locks', 'menus', 'timers', 'radio', 'endings / slideshows', '"kill everyone" choices', 'player reads (P-REF)']


class Ctx:
    def __init__(self):
        self.d = build.state()['d']
        self.u = qlib.identifier_universe(self.d)
        self.ix = build.state()['ix']
        self.load_order = qlib.jload(os.path.join(REPO, 'tools', 'questdata', 'loadorder.json'))['plugins']
        self.prefix = self.load_order.index('HonestHearts.esm')
        eff = open(os.path.join(REPO, 'synccore', 'include', 'fnvcoop', 'effect.h'), encoding='utf-8').read()
        self.effect_types = set(re.findall(r'(?m)^struct (\w+)', eff))
        self.rules = qlib.jload(os.path.join(REPO, 'docs', 'quest-policies', 'rules.json'))
        self.meta = qlib.jload(os.path.join(qlib.QD, 'quests.json'))['meta']
        self.refs = set(self.d.forms) | set(self.d.refnames) | set(self.d.all_quests)
        for lst in self.d.infos.values():
            for i in lst:
                self.refs.add(i['ref'])
        for ref in self.d.extra['scpt']:
            self.refs.add(ref)
        self.cell_names = {f['edid'] for f in self.d.forms.values() if f['sig'] in ('CELL', 'WRLD') and f.get('edid')}
        self.ref_names = {f['edid'] for f in list(self.d.forms.values()) + list(self.d.refnames.values()) if f.get('edid')
                          and f.get('sig') in ('REFR', 'ACHR', 'ACRE')}


def rt_id(ctx, ref):
    return '%08X' % ((ctx.prefix << 24) | int(ref.split(':')[1], 16))


# ---------------------------------------------------------------------------------------------------------------------
def check_inventory(inv, ctx, errs):
    d = ctx.d
    want = {q['edid']: q for q in d.quests}
    got = {}
    for q in inv.get('quests', []):
        if q['edid'] in got:
            errs.append('inventory: duplicate %s' % q['edid'])
        got[q['edid']] = q
    for e in sorted(set(want) - set(got)):
        errs.append('inventory: quest %s missing' % e)
    for e in sorted(set(got) - set(want)):
        errs.append('inventory: %s is not an Honest Hearts quest in quests.json' % e)
    if inv.get('load_order_index') != ctx.prefix or ctx.prefix != 2:
        errs.append('inventory: load order index must be 2 (HonestHearts.esm), got %r / file %r' % (inv.get('load_order_index'), ctx.prefix))
    for e, q in got.items():
        w = want.get(e)
        if not w:
            continue
        if q['ref'] != w['ref']:
            errs.append('inventory %s: ref %s != %s' % (e, q['ref'], w['ref']))
        if q['formid'] != rt_id(ctx, w['ref']):
            errs.append('inventory %s: formid %s != runtime id %s (load order prefix %02X)' % (e, q['formid'], rt_id(ctx, w['ref']), ctx.prefix))
        if [s['index'] for s in q['stages']] != sorted(s['index'] for s in w['stages']):
            errs.append('inventory %s: stage list differs from quests.json' % e)
        if [o['index'] for o in q['objectives']] != sorted(o['index'] for o in w['objectives']):
            errs.append('inventory %s: objective list differs from quests.json' % e)
        if q['dialogue']['topics'] != len(w['topics']) or q['dialogue']['infos'] != len(w['infos']):
            errs.append('inventory %s: dialogue counts differ' % e)
        rk = d.risk.get(w['ref'])
        if not rk or q['tier'] != rk['tier']:
            errs.append('inventory %s: tier %r != risk %r' % (e, q['tier'], rk and rk['tier']))
        if q['role'] not in ROLES:
            errs.append('inventory %s: bad role %r' % (e, q['role']))
        gset = {(x['src'], x['dst'], x['kind']) for x in d.graph['edges']}
        for x in q['graph']['in']:
            if (x['src'], e, x['kind']) not in gset:
                errs.append('inventory %s: graph edge %s -> %s (%s) not in questgraph.json' % (e, x['src'], e, x['kind']))
        for x in q['graph']['out']:
            if (e, x['dst'], x['kind']) not in gset:
                errs.append('inventory %s: graph edge %s -> %s (%s) not in questgraph.json' % (e, e, x['dst'], x['kind']))
        for t in q['prerequisites']['triggered_by']:
            if t['fn'] == 'SetStage' and t['stage'] is not None and t['stage'] not in [s['index'] for s in q['stages']]:
                errs.append('inventory %s: triggered by SetStage %s which is not a stage of the quest' % (e, t['stage']))
    n_stage = ctx.meta['plugins']['HonestHearts.esm']['stages']
    if sum(len(q['stages']) for q in inv.get('quests', [])) != n_stage:
        errs.append('inventory: stage total %d != extractor meta %d' % (sum(len(q['stages']) for q in inv.get('quests', [])), n_stage))
    if len(inv.get('quests', [])) != ctx.meta['plugins']['HonestHearts.esm']['stats']['QUST']['records']:
        errs.append('inventory: quest total %d != extractor meta QUST records' % len(inv.get('quests', [])))
    c = inv.get('counts', {})
    if c.get('quests') != len(inv.get('quests', [])) or c.get('stages') != sum(len(q['stages']) for q in inv.get('quests', [])):
        errs.append('inventory: counts block inconsistent')


CMD_RES = [re.compile(r'^startquest ([0-9A-F]{8})$'), re.compile(r'^stopquest ([0-9A-F]{8})$'), re.compile(r'^setstage ([0-9A-F]{8}) (\d+)$'),
           re.compile(r'^set ([0-9A-F]{8})\.([A-Za-z_][A-Za-z0-9_]*) to (-?\d+)$')]


def check_cmd(cmd, ctx, errs, where, before_stage=None, quest=None):
    d = ctx.d
    by_id = {rt_id(ctx, q['ref']): q for q in d.quests}
    for rx in CMD_RES:
        m = rx.match(cmd)
        if not m:
            continue
        qid = m.group(1)
        q = by_id.get(qid)
        if q is None:
            errs.append('%s: command %r targets an id that is not an Honest Hearts quest' % (where, cmd))
            return
        if cmd.startswith('setstage'):
            n = int(m.group(2))
            if n not in [s['index'] for s in q['stages']]:
                errs.append('%s: %r: %s has no stage %d' % (where, cmd, q['edid'], n))
            if before_stage is not None and quest == q['edid'] and n >= before_stage:
                errs.append('%s: prerequisite stage %d is not before stage %d' % (where, n, before_stage))
        if cmd.startswith('set '):
            var = m.group(2)
            src = (q.get('script_data') or {}).get('source') or ''
            if var not in {x.group(2) for x in re.finditer(r'(?im)^\s*(short|long|int|float|ref|reference)\s+(\w+)', src)}:
                errs.append('%s: %r: %s does not declare variable %s' % (where, cmd, q['edid'], var))
        return
    errs.append('%s: command %r is not in the allowed grammar (startquest/stopquest/setstage/set <id>.<var> to <n>)' % (where, cmd))


def check_sweep(sw, inv, ctx, errs):
    d = ctx.d
    inv_q = {q['edid']: q for q in inv.get('quests', [])}
    sq = {q['edid']: q for q in sw.get('quests', [])}
    ex = {q['edid']: q for q in sw.get('excluded_quests', [])}
    if set(sq) & set(ex):
        errs.append('sweep: quests both swept and excluded: %s' % sorted(set(sq) & set(ex)))
    for e in sorted(set(inv_q) - set(sq) - set(ex)):
        errs.append('sweep: quest %s neither swept nor excluded' % e)
    for e in sorted((set(sq) | set(ex)) - set(inv_q)):
        errs.append('sweep: %s is not in the inventory' % e)
    for e, x in ex.items():
        if not x.get('reason'):
            errs.append('sweep: excluded quest %s has no reason' % e)
        if e in d.by_edid and d.by_edid[e]['stages']:
            errs.append('sweep: quest %s has stages but is excluded' % e)
    excl_stage = {(x['quest'], x['stage']) for x in sw.get('exclusions', [])}
    for x in sw.get('exclusions', []):
        if not x.get('reason'):
            errs.append('sweep: exclusion %r has no reason' % (x,))
    total = 0
    for e, q in d.by_edid.items():
        if not q['stages']:
            continue
        s = sq.get(e)
        if s is None:
            errs.append('sweep: quest %s with %d stages is not swept' % (e, len(q['stages'])))
            continue
        have = {st['index'] for st in s['stages']}
        for st in q['stages']:
            if st['index'] not in have and (e, st['index']) not in excl_stage:
                errs.append('sweep: %s stage %d neither swept nor excluded with a reason' % (e, st['index']))
        for i in have - {st['index'] for st in q['stages']}:
            errs.append('sweep: %s stage %d does not exist' % (e, i))
        qid = rt_id(ctx, q['ref'])
        if s['formid'] != qid or not s['formid'].startswith('%02X' % ctx.prefix):
            errs.append('sweep %s: formid %s != %s' % (e, s['formid'], qid))
        for c in s.get('setup', []):
            check_cmd(c['cmd'], ctx, errs, 'sweep %s setup' % e)
        for st in s['stages']:
            total += 1
            if st['command'] != 'setstage %s %d' % (qid, st['index']):
                errs.append('sweep %s stage %d: command %r' % (e, st['index'], st['command']))
            check_cmd(st['command'], ctx, errs, 'sweep %s stage %d' % (e, st['index']))
            for c in st.get('pre', []):
                check_cmd(c['cmd'], ctx, errs, 'sweep %s stage %d pre' % (e, st['index']), before_stage=st['index'], quest=e)
            for v in st.get('variants', []):
                for c in v['setup']:
                    check_cmd(c['cmd'], ctx, errs, 'sweep %s stage %d variant %s' % (e, st['index'], v['id']))
            for h in st.get('hazards', []):
                if h not in SWEEP_HAZARDS:
                    errs.append('sweep %s stage %d: unknown hazard %s' % (e, st['index'], h))
    if sw.get('summary', {}).get('stages') != total:
        errs.append('sweep: summary stages %r != %d' % (sw.get('summary', {}).get('stages'), total))
    want_ids = [s['formid'] for s in sw.get('quests', [])]
    if sw.get('reset_ids') != want_ids:
        errs.append('sweep: reset_ids do not match the swept quests')
    for r in sw.get('reset_ids', []):
        if not r.startswith('%02X' % ctx.prefix) or len(r) != 8:
            errs.append('sweep: reset id %s lacks the load-order prefix' % r)
    if sorted(sw.get('order', [])) != sorted(sq):
        errs.append('sweep: order is not a permutation of the swept quests')
    for e in sw.get('summary', {}).get('run_last', []):
        if e not in sq:
            errs.append('sweep: run_last %s not swept' % e)


def check_l7_compat(sw, errs):
    """The L7 sweep (plugin/snapshot/tools/goodsprings_sweep.py load_plan) reads edid, ref, stages[].index and tier_direct / tier."""
    for q in sw.get('quests', []):
        for k in ('edid', 'ref', 'stages'):
            if k not in q:
                errs.append('sweep %s: missing %s (L7 load_plan needs it)' % (q.get('edid'), k))
        if not re.match(r'^HonestHearts\.esm:[0-9A-F]{6}$', q.get('ref', '')):
            errs.append('sweep %s: ref %r is not plugin:LOCALID' % (q.get('edid'), q.get('ref')))
        if not q.get('tier_direct') and not q.get('tier'):
            errs.append('sweep %s: no tier' % q.get('edid'))
        for st in q.get('stages', []):
            if not isinstance(st.get('index'), int):
                errs.append('sweep %s: stage without integer index' % q.get('edid'))
    for k in ('milestone', 'core', 'system_quests', 'touched_quests', 'summary', 'quests'):
        if k not in sw:
            errs.append('sweep: top-level key %s of the m1_goodsprings.json format is missing' % k)


def unit_source(label, ctx):
    cache = ctx.__dict__.setdefault('_unit_cache', {})
    if label not in cache:
        cache[label] = _unit_source(label, ctx)
    return cache[label]


def _unit_source(label, ctx):
    d = ctx.d
    kind, rest = label.split(' ', 1)
    if kind == 'stage':
        qe, tail = rest.split(':')
        idx, ent = tail.split('.')
        q = d.by_edid[qe]
        for st in q['stages']:
            if st['index'] == int(idx):
                return st['entries'][int(ent)]['script']['source']
    elif kind == 'info':
        ref, which = rest.split(' ')
        idx = ctx.__dict__.setdefault('_info_idx', {i['ref']: i for lst in d.infos.values() for i in lst})
        return idx[ref][which]['source']
    else:
        for ref, s in d.extra['scpt'].items():
            if ref.startswith('HonestHearts.esm:') and s.get('edid') == rest:
                return (s.get('script') or {}).get('source')
    raise KeyError(label)


SIMPLE_FN = ['SetStage', 'StartQuest', 'StopQuest', 'DisablePlayerControls', 'EnablePlayerControls', 'SetPlayerTeammate', 'PlaceAtMe', 'Autosave',
             'AddItem', 'RemoveItem', 'RewardXP', 'RewardKarma', 'AddPerk', 'RemovePerk', 'EnableFastTravel', 'ShowMessage']


def independent_count(ctx, fn):
    n = 0
    rx = re.compile(r'(?<![A-Za-z0-9_])%s(?![A-Za-z0-9_])' % fn, re.I)
    for u in scan.units(ctx.d):
        for line in u['source'].replace('\r', '').split('\n'):
            line = qlib.strip_comment(line)
            line = re.sub(r'"[^"]*"', '""', line)
            n += len(rx.findall(line))
    return n


def check_gaps(g, ctx, errs, deep=True):
    funcs = g.get('functions', [])
    names = [f['fn'].lower() for f in funcs]
    if len(names) != len(set(names)):
        errs.append('gaps: duplicate function entries')
    total = 0
    for f in funcs:
        total += f['occurrences']
        if f['occurrences'] != len(f['locations']):
            errs.append('gaps %s: occurrences != locations' % f['fn'])
        if f['status'] not in ALLOWED_OVERALL or f['capture'] not in ALLOWED_STATUS or f['apply'] not in ALLOWED_STATUS:
            errs.append('gaps %s: bad status %r/%r/%r' % (f['fn'], f['status'], f['capture'], f['apply']))
        if f['severity'] not in ALLOWED_SEV or f['kind'] not in ALLOWED_KIND:
            errs.append('gaps %s: bad severity/kind' % f['fn'])
        if f['effect']:
            for part in f['effect'].split('/'):
                base = re.sub(r'\(.*\)$', '', part)
                if base not in ctx.effect_types:
                    errs.append('gaps %s: effect type %r is not in synccore effect.h' % (f['fn'], f['effect']))
        m = fnmap.lookup(f['fn'])
        if m is None:
            errs.append('gaps %s: not in fnmap' % f['fn'])
        elif (m['capture'], m['apply']) != (f['capture'], f['apply']):
            errs.append('gaps %s: capture/apply differ from fnmap' % f['fn'])
        if f['status'] in ('missing', 'partial') and not f['locations']:
            errs.append('gaps %s: unsupported with no locations' % f['fn'])
        if deep:
            for l in f['locations']:
                try:
                    src = unit_source(l['unit'], ctx)
                except KeyError:
                    errs.append('gaps %s: unit %r not found' % (f['fn'], l['unit']))
                    continue
                lines = src.replace('\r', '').split('\n')
                if not (1 <= l['line'] <= len(lines)):
                    errs.append('gaps %s: %s line %d out of range' % (f['fn'], l['unit'], l['line']))
                    continue
                if qlib.clip(qlib.strip_comment(lines[l['line'] - 1]).strip(), 160) != l['text']:
                    errs.append('gaps %s: %s line %d text does not match the script' % (f['fn'], l['unit'], l['line']))
                    break
    c = g.get('counts', {})
    if c.get('calls') != total:
        errs.append('gaps: counts.calls %r != sum of occurrences %d' % (c.get('calls'), total))
    if sum(c.get('by_status', {}).values()) != len(funcs) or c.get('functions') != len(funcs):
        errs.append('gaps: by_status does not add up')
    for hc in HAZ_CLASSES:
        if hc not in g.get('hazards', {}):
            errs.append('gaps: hazard class %r missing' % hc)
    by = {f['fn'].lower(): f for f in funcs}
    for fn, want in (('setplayerteammate', ('missing', 'blocker')), ('disableplayercontrols', ('missing', 'blocker')),
                     ('placeatme', ('missing', 'high')), ('addperk', ('missing', 'high')), ('setstage', ('supported', None))):
        f = by.get(fn)
        if f is None:
            errs.append('gaps: %s missing from the report' % fn)
        else:
            if f['capture'] != want[0]:
                errs.append('gaps: golden: %s capture is %s, expected %s (change fnmap.py AND this test together, with the L5 evidence)' % (fn, f['capture'], want[0]))
            if want[1] and f['severity'] != want[1]:
                errs.append('gaps: golden: %s severity %s != %s' % (fn, f['severity'], want[1]))
    if deep:
        for fn in SIMPLE_FN:
            f = by.get(fn.lower())
            got = f['occurrences'] if f else 0
            want = independent_count(ctx, fn)
            if got != want:
                errs.append('gaps: %s occurrences %d != independent token count %d' % (fn, got, want))


def check_rules(p, ctx, errs):
    d = ctx.d
    base = ctx.rules['quests']
    try:
        patched = build.apply_patch(ctx.rules, p)
    except RuntimeError as e:
        errs.append('rules.patch: %s' % e)
        return
    for op in p.get('patches', []):
        if op['op'] not in ('replace_params', 'add_rule', 'add_quest'):
            errs.append('rules.patch: bad op %r' % op['op'])
        if op['quest'] not in d.by_edid:
            errs.append('rules.patch: quest %s is not an Honest Hearts quest' % op['quest'])
        kinds = [op['kind']] if 'kind' in op else [r['kind'] for r in op.get('rules', [])]
        for k in kinds:
            if k not in build.KINDS:
                errs.append('rules.patch: kind %s is not one of the 16 existing kinds' % k)
        if op['op'] == 'replace_params' and op['quest'] in base and not any(r['kind'] == op['kind'] for r in base[op['quest']]['rules']):
            errs.append('rules.patch: replace_params %s/%s: key does not exist in rules.json' % (op['quest'], op['kind']))
        if op['op'] == 'add_quest' and op['quest'] in base:
            errs.append('rules.patch: add_quest %s already in rules.json' % op['quest'])
        rule_lists = [(op['kind'], op['params'])] if 'kind' in op else [(r['kind'], r['params']) for r in op['rules']]
        for kind, params in rule_lists:
            if not isinstance(params, dict):
                errs.append('rules.patch: params of %s/%s not an object' % (op['quest'], kind))
                continue
            for m in params.get('markers', []):
                if m not in ctx.ref_names:
                    errs.append('rules.patch: %s %s marker %s does not resolve to a placed ref' % (op['quest'], kind, m))
            for cname in params.get('cells', []):
                if cname not in ctx.cell_names:
                    errs.append('rules.patch: %s %s cell %s does not resolve to a CELL/WRLD' % (op['quest'], kind, cname))
            if kind == 'PartyAwareCond':
                for fn, red in params.get('funcs', {}).items():
                    if red not in REDUCTIONS or not fn:
                        errs.append('rules.patch: bad reduction %s for %s' % (red, fn))
        for e in op.get('evidence', []):
            if not e.get('line') and e['spec'].get('cond') is None:
                errs.append('rules.patch: evidence without a line for %s' % op['quest'])
    tether = [r for r in patched['NVDLC02MQ00']['rules'] if r['kind'] == 'PartyTether']
    if not tether or 'GSDocMitchellHouse' in tether[0]['params'].get('cells', []):
        errs.append('rules.patch: NVDLC02MQ00 PartyTether must not use GSDocMitchellHouse (it defers every MQ00 stage until all players are back in Doc Mitchell house)')
    for q, e in patched.items():
        if q.startswith('NVDLC02'):
            ks = [r['kind'] for r in e['rules']]
            if len(ks) != len(set(ks)):
                errs.append('rules.patch: duplicate kinds for %s after patch' % q)
    ids = [x['id'] for x in p.get('proposed', [])]
    if len(ids) != len(set(ids)) or not ids:
        errs.append('rules.patch: proposed ids empty or duplicated')
    for x in p.get('proposed', []):
        if not x.get('evidence') or any(not e.get('line') and 'cond' not in e['spec'] for e in x['evidence']):
            errs.append('rules.patch: proposal %s lacks resolved evidence' % x['id'])


def check_dialogue(ds, ctx, errs):
    d = ctx.d
    ix = ctx.ix
    ids = set()
    specs = ds.get('specs', [])
    if len(specs) < 12:
        errs.append('dialogue: only %d conversations (need the key ones)' % len(specs))
    for s in specs:
        if s['id'] in ids:
            errs.append('dialogue: duplicate id %s' % s['id'])
        ids.add(s['id'])
        for q in s['quests']:
            if q not in d.by_edid:
                errs.append('dialogue %s: quest %s unknown' % (s['id'], q))
        for st in s['steps']:
            if st['action'] not in DLG_ACTIONS:
                errs.append('dialogue %s: action %s' % (s['id'], st['action']))
            for k in st.get('expect', {}):
                if k not in DLG_EXPECT:
                    errs.append('dialogue %s: expect key %s' % (s['id'], k))
            if st['action'] == 'pick_info':
                loc = ix.infos.get(st['info'].split(':')[1].upper())
                if not loc or not st['info'].startswith('HonestHearts.esm:'):
                    errs.append('dialogue %s: INFO %s does not exist' % (s['id'], st['info']))
                    continue
                text = ' | '.join(r['text'] for r in loc[1]['responses'])
                if build._norm(st['line']) not in build._norm(text):
                    errs.append('dialogue %s: line %r is not in INFO %s' % (s['id'], st['line'], st['info']))
                stg = st['expect'].get('stage')
                if stg:
                    q = d.by_edid.get(stg['quest']) or next((x for x in d.all_quests.values() if x.get('edid') == stg['quest']), None)
                    if q is None or stg['value'] not in [x['index'] for x in q['stages']]:
                        errs.append('dialogue %s: expected stage %r does not exist' % (s['id'], stg))
                for k, v in st['expect'].get('script_var', {}).items():
                    qn, var = k.split('.')
                    q = d.by_edid.get(qn)
                    if q is None or var not in qlib.re.findall(r'(?im)^\s*(?:short|long|int|float|ref)\s+(\w+)', (q.get('script_data') or {}).get('source') or ''):
                        errs.append('dialogue %s: script var %s not declared' % (s['id'], k))
            if st['action'] == 'talk':
                if st['npc_ref'] not in ctx.refs:
                    errs.append('dialogue %s: npc_ref %s unknown' % (s['id'], st['npc_ref']))
            if st['action'] == 'setup':
                for p, cmds in st['players'].items():
                    for c in cmds:
                        if not re.match(r'^player\.forceav [a-z]+ \d+$', c):
                            errs.append('dialogue %s: setup command %r' % (s['id'], c))
        if s.get('save') is not None:
            errs.append('dialogue %s: save must stay null (unknown) until a Honest Hearts start save exists' % s['id'])
        if not s.get('breaks_initiator_model'):
            errs.append('dialogue %s: breaks_initiator_model missing' % s['id'])
    must = ['dlg_hh_jed_departure_gate', 'dlg_hh_daniel_choice', 'dlg_hh_followschalk_hire_fire', 'dlg_hh_waking_cloud_hire_fire',
            'dlg_hh_joshua_suw_confrontation', 'dlg_hh_ending_slides']
    for m in must:
        if m not in ids:
            errs.append('dialogue: required conversation %s missing' % m)


def check_also(also, errs):
    if not also:
        errs.append('blockers: the "also" list is empty')
    for x in also:
        if not x['evidence'] or any(not e.get('line') for e in x['evidence']) or '{' in x['text']:
            errs.append('blockers also %s: evidence / text' % x['id'])


def check_blockers(b, ctx, errs):
    bl = b.get('blockers', [])
    if len(bl) != 10:
        errs.append('blockers: need exactly 10, got %d' % len(bl))
    for i, x in enumerate(bl, 1):
        if x['rank'] != i:
            errs.append('blockers: rank %r at position %d' % (x['rank'], i))
        if x['severity'] not in ALLOWED_SEV or not x['text'] or '{' in x['text']:
            errs.append('blockers %s: bad severity / unformatted text' % x['id'])
        if not x['evidence'] or any(not e.get('line') and 'cond' not in e['spec'] for e in x['evidence']):
            errs.append('blockers %s: evidence does not resolve' % x['id'])
    sev = [SEV for SEV in (x['severity'] for x in bl)]
    order = ['blocker', 'high', 'medium', 'low']
    if [order.index(s) for s in sev] != sorted(order.index(s) for s in sev):
        errs.append('blockers: not ranked by severity')


def check_docs(docs, ctx, errs):
    for name in FILES_MD:
        t = docs.get(name)
        if not t or len(t) < 500:
            errs.append('%s missing or tiny' % name)
            continue
        for m in set(re.findall(r'(?<![A-Za-z0-9_])NVDLC02[A-Za-z0-9_]*', t)):
            if m.lower() not in ctx.u:
                errs.append('%s: identifier %s does not resolve in the data' % (name, m))
    pr = docs.get('policy_review.md', '')
    for sec in ('## 1. Coverage', '## 2. Owner decisions', '## 3. Story moments', '## 4. Existing rules', '## 5. Proposals', '## 6. Open questions'):
        if sec not in pr:
            errs.append('policy_review.md: section %r missing' % sec)
    for need in ('Joshua', 'caravan', 'weight', 'tether', 'barrier', 'cinematic', 'reward'):
        if need.lower() not in pr.lower():
            errs.append('policy_review.md does not cover %r' % need)
    dm = docs.get('dialogue.md', '')
    for cv in ('dlg_hh_jed_departure_gate', 'dlg_hh_daniel_choice', 'dlg_hh_ending_slides'):
        if cv not in dm:
            errs.append('dialogue.md misses %s' % cv)
    if 'Exact locations' not in docs.get('gaps.md', ''):
        errs.append('gaps.md: no exact-location section')


def check_idents(objs, ctx, errs):
    rx = re.compile(r'^(HonestHearts|FalloutNV|DeadMoney)\.esm:[0-9A-F]{6}$')
    ids8 = re.compile(r'^[0-9A-F]{8}$')
    by_id = {rt_id(ctx, q['ref']) for q in ctx.d.quests}

    def walk(o, path):
        if isinstance(o, dict):
            for k, v in o.items():
                walk(v, path + '/' + str(k))
        elif isinstance(o, list):
            for i, v in enumerate(o):
                walk(v, path + '[%d]' % i)
        elif isinstance(o, str):
            if rx.match(o) and o not in ctx.refs:
                errs.append('ident: %s = %s does not resolve' % (path, o))
            if ids8.match(o) and path.endswith(('formid', 'reset_ids')) and o not in by_id:
                errs.append('ident: %s = %s is not a runtime quest id' % (path, o))
            for m in re.findall(r'(?<![A-Za-z0-9_])NVDLC02[A-Za-z0-9_]*', o):
                if m.lower() not in ctx.u:
                    errs.append('ident: %s mentions %s which is not in the data' % (path, m))
    for name, o in objs.items():
        walk(o, name)


def validate(art, ctx, deep=True):
    errs = []
    check_inventory(art['inventory'], ctx, errs)
    check_sweep(art['sweep'], art['inventory'], ctx, errs)
    check_l7_compat(art['sweep'], errs)
    check_gaps(art['gaps'], ctx, errs, deep)
    check_rules(art['rules_patch'], ctx, errs)
    check_dialogue(art['dialogue_specs'], ctx, errs)
    check_blockers({'blockers': art['blockers']}, ctx, errs)
    check_also(art['also'], errs)
    check_docs(art['docs'], ctx, errs)
    objs = {k: art[k] for k in ('inventory', 'sweep', 'rules_patch', 'dialogue_specs')}
    objs['blockers'] = art['blockers']
    objs['also'] = art['also']
    check_idents(objs, ctx, errs)
    return errs


def rt(o):
    return json.loads(json.dumps(o))


def art_from_generated(g):
    return {'inventory': rt(g['inventory']), 'sweep': rt(g['sweep']), 'gaps': rt(g['gaps']), 'rules_patch': rt(g['rules_patch']),
            'dialogue_specs': rt(g['dialogue_specs']), 'blockers': rt(g['blockers']), 'also': rt(g['also']), 'docs': dict(g['docs'])}


def art_from_disk():
    a = {}
    for k, f in (('inventory', 'inventory.json'), ('sweep', 'sweep.json'), ('gaps', 'gaps.json'), ('rules_patch', 'rules.patch.json'),
                 ('dialogue_specs', 'dialogue_specs.json')):
        a[k] = qlib.jload(os.path.join(HERE, f))
    bj = qlib.jload(os.path.join(HERE, 'blockers.json'))
    a['blockers'] = bj['blockers']
    a['also'] = bj['also']
    a['docs'] = {n: open(os.path.join(HERE, n), encoding='utf-8').read() for n in FILES_MD}
    return a


# ---------------------------------------------------------------------------------------------------------------------
class Runner:
    def __init__(self):
        self.passed = 0
        self.failed = []

    def check(self, name, fn):
        try:
            res = fn()
            errs = res if isinstance(res, list) else []
        except Exception as e:  # noqa: BLE001
            errs = ['exception: %s' % ''.join(traceback.format_exception_only(type(e), e)).strip()]
        if errs:
            self.failed.append(name)
            print('FAIL %s' % name)
            for e in errs[:12]:
                print('     - %s' % e)
            if len(errs) > 12:
                print('     ... %d more' % (len(errs) - 12))
        else:
            self.passed += 1
            print('PASS %s' % name)

    def twin(self, name, fn):
        """fn returns the list of errors the validators produced for a deliberately broken input; must be non-empty."""
        try:
            errs = fn()
        except RuntimeError as e:
            errs = ['generator refused: %s' % e]
        except Exception as e:  # noqa: BLE001
            errs = ['exception: %s' % e]
        if errs:
            self.passed += 1
            print('PASS twin %s (caught: %s)' % (name, errs[0][:110]))
        else:
            self.failed.append('twin ' + name)
            print('FAIL twin %s: the broken variant was NOT caught' % name)


def corrupt_line_shift(a):
    f = next(f for f in a['gaps']['functions'] if f['fn'] == 'SetPlayerTeammate')
    f['locations'][0]['line'] += 1


def corrupt_drop_function(a):
    a['gaps']['functions'] = [f for f in a['gaps']['functions'] if f['fn'] != 'SetPlayerTeammate']


def corrupt_loosen_status(a):
    f = next(f for f in a['gaps']['functions'] if f['fn'] == 'DisablePlayerControls')
    f['capture'] = 'supported'
    f['status'] = 'supported'


def corrupt_marker(a):
    for p in a['rules_patch']['patches']:
        if p['op'] == 'add_quest':
            for r in p['rules']:
                if r['kind'] == 'PartyWarp':
                    r['params']['markers'].append('NVDLC02NoSuchMarkerREF')
                    return


def corrupt_doc_token(a):
    a['docs']['policy_review.md'] += '\nSee NVDLC02ImaginaryQuestThing for details.\n'


def corrupt_dialogue_stage(a):
    for s in a['dialogue_specs']['specs']:
        for st in s['steps']:
            if st.get('expect', {}).get('stage'):
                st['expect']['stage']['value'] = 999
                return


def corrupt_sweep_prefix(a):
    q = a['sweep']['quests'][0]
    q['stages'][0]['command'] = q['stages'][0]['command'].replace(' 02', ' 01', 1)


def corrupt_blocker(a):
    a['blockers'][0]['evidence'][0]['line'] = 0


def corrupt_inventory_stage(a):
    a['inventory']['quests'][3]['stages'] = a['inventory']['quests'][3]['stages'][1:] or [{'index': 7777}]


def corrupt_graph(a):
    q = next(q for q in a['inventory']['quests'] if q['graph']['in'] or q['graph']['out'])
    (q['graph']['in'] or q['graph']['out'])[0]['kind'] = 'nonsense'


def corrupt_also(a):
    a['also'][0]['evidence'][0]['line'] = 0


def corrupt_l7(a):
    del a['sweep']['quests'][0]['ref']


def corrupt_count(a):
    a['gaps']['counts']['calls'] += 1


def main():
    r = Runner()
    ctx = Ctx()
    print('Honest Hearts questline tests (plugin index %02X, %d quests in quests.json)' % (ctx.prefix, len(ctx.d.quests)))

    def t_fnmap():
        errs = []
        for fn in fnmap.all_names():
            e = fnmap.lookup(fn)
            if e['capture'] not in ALLOWED_STATUS or e['apply'] not in ALLOWED_STATUS or e['severity'] not in ALLOWED_SEV or e['kind'] not in ALLOWED_KIND:
                errs.append('fnmap %s: bad enum' % fn)
            for h in e['hazard']:
                if h not in fnmap.HAZARDS:
                    errs.append('fnmap %s: unknown hazard %s' % (fn, h))
        if fnmap.lookup('SetPlayerTeammate')['capture'] != 'missing' or fnmap.lookup('SetStage')['capture'] != 'supported':
            errs.append('fnmap golden statuses changed')
        return errs
    r.check('fnmap table', t_fnmap)

    def t_scan():
        calls, n = scan.scan_all(ctx.d)
        errs = []
        un = {c['fn'] for c in calls if not fnmap.lookup(c['fn'])}
        if un:
            errs.append('unmapped functions: %s' % sorted(un))
        if n < 600 or len(calls) < 4000:
            errs.append('scan found too little (%d units, %d calls)' % (n, len(calls)))
        return errs
    r.check('scanner covers all HH scripts and every function is mapped', t_scan)

    gen = build.generate()
    live = art_from_generated(gen)
    r.check('generated artifacts validate', lambda: validate(live, ctx))

    def t_disk():
        errs = validate(art_from_disk(), ctx)
        for f in FILES_JSON:
            if not os.path.exists(os.path.join(HERE, f)):
                errs.append('%s not written (run build.py)' % f)
        return errs
    r.check('written files validate', t_disk)

    def t_fresh():
        errs = []
        for name, key in (('inventory.json', 'inventory'), ('sweep.json', 'sweep'), ('gaps.json', 'gaps'), ('rules.patch.json', 'rules_patch'),
                          ('dialogue_specs.json', 'dialogue_specs')):
            if qlib.jload(os.path.join(HERE, name)) != live[key]:
                errs.append('%s is stale: run python -I questlines/honesthearts/build.py' % name)
        for name in FILES_MD:
            if open(os.path.join(HERE, name), encoding='utf-8').read().rstrip('\n') != live['docs'][name].rstrip('\n'):
                errs.append('%s is stale' % name)
        bj = qlib.jload(os.path.join(HERE, 'blockers.json'))
        if bj['blockers'] != live['blockers'] or bj['also'] != live['also']:
            errs.append('blockers.json is stale')
        return errs
    r.check('written files are fresh (equal to a regeneration)', t_fresh)

    def t_policy_facts():
        base = ctx.rules['quests']
        errs = []
        for q, e in base.items():
            if q.startswith('NVDLC02') and any(x['kind'] == 'ShareKarmaRep' for x in e['rules']):
                errs.append('rules.json now has ShareKarmaRep for %s: update policy_review.md owner check' % q)
        cells = [x for x in base['NVDLC02MQ00']['rules'] if x['kind'] == 'PartyTether'][0]['params']['cells']
        if cells != ['GSDocMitchellHouse']:
            errs.append('rules.json MQ00 tether changed (%r): re-check the PARAM_FIXES rationale' % cells)
        rc = open(os.path.join(REPO, 'synccore', 'src', 'rules.cpp'), encoding='utf-8').read()
        if 'if (cells.empty()) return true;' not in rc:
            errs.append('rules.cpp: an empty PartyTether cell list no longer means "no gate": re-check the PARAM_FIXES rationale')
        m = re.search(r'red == "any-party-member"[^}]*?\{(.*?)\}', rc, re.S)
        if not m or 'return 1.0;' not in m.group(1):
            errs.append('rules.cpp: any-party-member no longer returns 1.0/0.0: re-check the max-over-party / sum-over-party proposals')
        return errs
    r.check('facts the policy review relies on still hold in rules.json and rules.cpp', t_policy_facts)

    def t_patch_covers():
        p = live['rules_patch']
        errs = []
        quests_added = {x['quest'] for x in p['patches'] if x['op'] == 'add_quest'}
        for must in ('NVDLC02IntroSlides', 'NVDLC02Ending', 'NVDLC02MQ04Timer', 'NVDLC02MQ05Timer2', 'NVDLC02DialogueDaniel', 'NVDLC02FollowersQuest'):
            if must not in quests_added:
                errs.append('patch does not add rules for %s' % must)
        return errs
    r.check('rules patch covers slideshows, timers, dialogue and companions', t_patch_covers)

    print('--- mutation twins (each broken variant must be rejected) ---')
    for m in build.MUTATIONS:
        def f(m=m):
            g = build.generate(m)
            return validate(art_from_generated(g), ctx)
        r.twin('generator:' + m, f)
    for name, fn in (('line_shift', corrupt_line_shift), ('drop_function', corrupt_drop_function), ('loosen_status', corrupt_loosen_status),
                     ('bad_marker', corrupt_marker), ('doc_token', corrupt_doc_token), ('dialogue_stage', corrupt_dialogue_stage),
                     ('sweep_prefix', corrupt_sweep_prefix), ('blocker_evidence', corrupt_blocker), ('inventory_stage', corrupt_inventory_stage),
                     ('count_mismatch', corrupt_count),
                     ('graph_edge', corrupt_graph), ('also_evidence', corrupt_also), ('l7_format', corrupt_l7)):
        def f(fn=fn):
            a = copy.deepcopy(live)
            fn(a)
            return validate(a, ctx)
        r.twin('artifact:' + name, f)

    print()
    print('%d checks passed, %d failed' % (r.passed, len(r.failed)))
    if r.failed:
        print('FAILED: ' + ', '.join(r.failed))
        return 1
    print('ALL OK')
    return 0


if __name__ == '__main__':
    sys.exit(main())
