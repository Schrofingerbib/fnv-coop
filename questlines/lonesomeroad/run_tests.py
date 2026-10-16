"""Lonesome Road questline tests + mutation twin.   python -I questlines/lonesomeroad/run_tests.py   -> prints ALL OK, exit 0.

Part 1 (real): the committed deliverables are exactly what build.py regenerates, every ID resolves against out/questdata,
every stage is swept or excluded with a reason, console commands are valid, gap counts match an independent recount, and
the claims about L5 (what is captured/applied) are still true in the L5 source.
Part 2 (mutation twin): ~12 deliberately broken variants of the deliverables; each must make Part 1's checks fail.
"""
import sys

sys.dont_write_bytecode = True
import contextlib
import copy
import gzip
import json
import os
import re
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import analysis  # noqa: E402
import build  # noqa: E402
import common  # noqa: E402
import curated  # noqa: E402
import funcmap  # noqa: E402
import moments  # noqa: E402

REPO = common.REPO
KINDS16 = {'Barrier', 'CompanionOwner', 'ExcludeFromGate', 'FastTravelLock', 'InitiatorDialogue', 'PartyAwareCond', 'PartyTether', 'PartyWarp',
           'PerPlayerScene', 'QuestItemDup', 'RemoveItemPerOwner', 'RewardAll', 'SaveSuppress', 'SetPlayerControls', 'ShareKarmaRep', 'SlideshowEnd'}
REDUCTIONS = {'initiator', 'any-party-member', 'any', 'min-over-party', 'entry:all-in,exit:any-in'}
STATUSES = {'supported', 'partial', 'missing', 'host-only', 'read', 'presentation'}
DELIVERABLES_JSON = ['inventory.json', 'sweep.json', 'gaps.json', 'rules.patch.json']
DELIVERABLES_MD = ['gaps.md', 'policy_review.md', 'dialogue.md', 'driver_patch.md']
STATUS_DOC = os.path.join(REPO, 'docs', 'questlines', 'lonesomeroad.md')
CMD_RE = [re.compile(r'^startquest [0-9A-F]{8}$'), re.compile(r'^setstage [0-9A-F]{8} \d+$'), re.compile(r'^stopquest [0-9A-F]{8}$'),
          re.compile(r'^set [0-9A-F]{8}\.[A-Za-z_]\w* to -?\d+(\.\d+)?$'), re.compile(r'^[0-9A-F]{8}\.(enable|disable)$')]


def read(p):
    with open(p, encoding='utf-8') as f:
        return f.read()


class Ctx:
    def __init__(self):
        self.data = common.Data()
        self.items = analysis.collect_scripts(self.data)
        self.by_label = {}
        for it in self.items:
            self.by_label.setdefault(it['label'], []).append(it)
        self.lr = {q['edid']: q for q in self.data.quests}
        self.local_refs = {k for tbl in (self.data.forms, self.data.refnames) for k in tbl if k.startswith(common.PLUGIN)}
        self.local_refs |= {q['ref'] for q in self.data.quests} | {s['ref'] for s in self.data.lr_scripts} | {r for r in self.data.dials if r.startswith(common.PLUGIN)}
        self.local_refs |= {i['ref'] for i in self.data.lr_infos}
        self.hex_ok = {common.rid(r) for r in self.local_refs}
        self.eff_names = self._effect_names()
        self.hooks = read(os.path.join(REPO, 'plugin', 'hooks', 'engine_hooks.cpp'))
        self.apply = read(os.path.join(REPO, 'plugin', 'hooks', 'apply_remote.cpp'))

    @staticmethod
    def _effect_names():
        t = read(os.path.join(REPO, 'synccore', 'include', 'fnvcoop', 'effect.h'))
        m = re.search(r'using Effect = std::variant<(.*?)>;', t, re.S)
        return {x.strip() for x in m.group(1).split(',')}

    def resolves(self, name):
        return self.data.resolve_edid(name) is not None


def load_art(root):
    art = {n: json.loads(read(os.path.join(root, n))) for n in DELIVERABLES_JSON}
    art['md'] = {n: read(os.path.join(root, n)) for n in DELIVERABLES_MD if os.path.exists(os.path.join(root, n))}
    return art


# ----------------------------------------------------------------------------------------------------------- checks
def check_fresh(ctx, root):
    """committed files == regenerated files (deterministic, not stale)."""
    f = []
    tmp = tempfile.mkdtemp(prefix='lrq_')
    try:
        with contextlib.redirect_stdout(open(os.devnull, 'w')):
            build.main(['--out', tmp])
        for n in DELIVERABLES_JSON + ['gaps.md', 'policy_review.md', 'dialogue.md']:
            if read(os.path.join(tmp, n)) != read(os.path.join(root, n)):
                f.append('stale: %s differs from build.py output (run build.py)' % n)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return f


def check_inventory(ctx, art):
    f = []
    inv = art['inventory.json']
    want = {q['edid'] for q in ctx.data.quests}
    got = {q['edid'] for q in inv['quests']}
    if want != got:
        f.append('inventory quests differ: missing %s extra %s' % (sorted(want - got), sorted(got - want)))
    if len(want) != 24:
        f.append('expected 24 Lonesome Road quests in quests.json, found %d' % len(want))
    for q in inv['quests']:
        src = ctx.lr.get(q['edid'])
        if not src:
            continue
        if not q['formid'].startswith(common.PREFIX_HEX) or q['formid'] != '%s%s' % (common.PREFIX_HEX, src['ref'].split(':')[1].rjust(6, '0')):
            f.append('%s: bad runtime formid %s (ref %s, prefix must be %s)' % (q['edid'], q['formid'], src['ref'], common.PREFIX_HEX))
        if [s['index'] for s in q['stages']] != sorted(s['index'] for s in src['stages']):
            f.append('%s: stage list differs from quests.json' % q['edid'])
        if sorted(o['index'] for o in q['objectives']) != sorted(o['index'] for o in src['objectives']):
            f.append('%s: objective list differs from quests.json' % q['edid'])
        if q['risk']['tier'] != ctx.data.risk[src['ref']]['tier']:
            f.append('%s: risk tier differs from risk.json' % q['edid'])
        if q['dialogue']['infos'] != len(ctx.data.infos_by_quest.get(src['ref'], [])):
            f.append('%s: INFO count differs' % q['edid'])
    with_setter = sum(1 for q in inv['quests'] if any(s['set_by'] for s in q['stages']))
    if with_setter < 8:
        f.append('only %d staged quests have a recorded SetStage trigger (setter scan broken?)' % with_setter)
    c = inv['counts']
    if c['stages'] != sum(len(q['stages']) for q in ctx.data.quests):
        f.append('inventory stage total %d != quests.json' % c['stages'])
    return f


def check_sweep(ctx, art):
    f = []
    sw = art['sweep.json']
    swept = {}
    for q in sw['quests']:
        swept[q['edid']] = {s['index'] for s in q['stages']}
    excl_q = {e['quest']: e for e in sw['excluded_quests']}
    excl_s = {(e['quest'], e['stage']): e for e in sw['excluded_stages']}
    for q in ctx.data.quests:
        e = q['edid']
        if q['stages']:
            for s in q['stages']:
                if s['index'] not in swept.get(e, set()) and (e, s['index']) not in excl_s:
                    f.append('%s stage %d is neither swept nor excluded' % (e, s['index']))
            if e in excl_q:
                f.append('%s has stages but is listed as excluded quest' % e)
        elif e not in excl_q:
            f.append('%s has no stages and no exclusion reason' % e)
    for e in list(excl_q.values()) + list(excl_s.values()):
        if not e.get('reason') or len(e['reason']) < 8:
            f.append('exclusion without a real reason: %s' % e)
    for q in sw['quests']:
        src = ctx.lr.get(q['edid'])
        if not src:
            f.append('sweep quest %s is not in quests.json' % q['edid'])
            continue
        want = common.rid(src['ref'])
        if q['runtime_id'] != want:
            f.append('%s runtime_id %s != %s' % (q['edid'], q['runtime_id'], want))
        for s in q['stages']:
            if s['host'] != 'setstage %s %d' % (want, s['index']):
                f.append('%s stage %d host command wrong: %s' % (q['edid'], s['index'], s['host']))
            if s['index'] not in {x['index'] for x in src['stages']}:
                f.append('%s stage %d does not exist in quests.json' % (q['edid'], s['index']))
            cmds = [c['cmd'] for c in s['setup']] + [c['cmd'] for v in s['variants'] for c in v['setup']] + [s['host'], s['wait'].replace('GetStageDone', 'setstage')]
            for c in cmds:
                if not any(r.match(c) for r in CMD_RE):
                    f.append('%s stage %d: command not in the allowed console vocabulary: %r' % (q['edid'], s['index'], c))
                for hx in re.findall(r'\b[0-9A-F]{8}\b', c):
                    if hx not in ctx.hex_ok:
                        f.append('%s stage %d: FormID %s does not resolve to a Lonesome Road form' % (q['edid'], s['index'], hx))
            if not src.get('flags') or 'start_enabled' not in src['flags']:
                if not any(c['cmd'] == 'startquest %s' % want for c in s['setup']):
                    f.append('%s stage %d: setup lacks startquest' % (q['edid'], s['index']))
            if s['run_mode'] not in ('normal', 'isolate', 'excluded'):
                f.append('%s stage %d: bad run_mode' % (q['edid'], s['index']))
    n_stage = sum(len(q['stages']) for q in sw['quests'])
    if sw['summary']['stages'] != n_stage:
        f.append('sweep summary stage count wrong')
    for p in sw['probes']:
        for c in [p['host']] + [x['cmd'] for x in p['prime']]:
            if not any(r.match(c) for r in CMD_RE):
                f.append('probe %s command invalid: %r' % (p['quest'], c))
    ip = {i['info'] for i in sw['info_probes']}
    for i in ctx.data.lr_infos:
        if (i.get('begin') or i.get('end')) and i['ref'] not in ip:
            f.append('scripted INFO %s missing from info_probes' % i['ref'])
    return f


def check_ids(ctx, art, extra_text=()):
    """Every NVDLC04* identifier and every 04xxxxxx FormID emitted anywhere must resolve (never invent IDs)."""
    f = []
    texts = [json.dumps(art[n]) for n in DELIVERABLES_JSON] + list(art['md'].values()) + list(extra_text)
    seen_names, seen_hex = set(), set()
    for t in texts:
        seen_names |= set(common.IDENT_RE.findall(t))
        seen_hex |= set(common.HEX_RE.findall(t))
    for n in sorted(seen_names):
        if not ctx.resolves(n):
            f.append('EditorID does not resolve in questdata: %s' % n)
    for h in sorted(seen_hex):
        if h.upper() not in ctx.hex_ok:
            f.append('FormID does not resolve in questdata: %s' % h)
    return f


def _strip_src(src):
    return '\n'.join(common.classify.strip_comment(x) for x in src.replace('\r', '').split('\n'))


def check_gaps(ctx, art):
    f = []
    g = art['gaps.json']
    seen_labels = {it['label'] for it in ctx.items}
    total = 0
    for fn in g['functions']:
        if fn['status'] not in STATUSES:
            f.append('%s: bad status %s' % (fn['fn'], fn['status']))
        if fn['count'] != len(fn['locations']):
            f.append('%s: count != locations' % fn['fn'])
        total += fn['count']
        for l in fn['locations']:
            if l['script'] not in seen_labels:
                f.append('%s: location script %s unknown' % (fn['fn'], l['script']))
                continue
            its = ctx.by_label[l['script']]
            lines = {}
            for it in its:
                for n, t in common.script_lines(it['source']):
                    lines[n] = t
            if l['line'] not in lines:
                f.append('%s: %s line %d does not exist' % (fn['fn'], l['script'], l['line']))
            elif fn['fn'] not in ('set questvar', 'set objvar', 'set localvar', 'set global') and fn['fn'] not in lines[l['line']].lower():
                f.append('%s: %s line %d does not contain the function: %r' % (fn['fn'], l['script'], l['line'], lines[l['line']][:60]))
    if total != g['summary']['function_uses_mapped']:
        f.append('summary total %d != sum of counts %d' % (g['summary']['function_uses_mapped'], total))
    if g['summary']['unmapped_functions']:
        f.append('unmapped functions remain: %s' % sorted(g['summary']['unmapped_functions']))
    # independent recount (plain regex on comment-stripped text, no tokenizer, no subject logic)
    counts = {}
    for fnname in ('setplayerteammate', 'addperk', 'swaptexture', 'killactor', 'getbuttonpressed', 'showmessage', 'rewardxp', 'startconversation',
                   'disableplayercontrols', 'setessential', 'placeatme', 'addreputationexact'):
        n = 0
        for it in ctx.items:
            n += len(re.findall(r'(?<![A-Za-z0-9_])%s(?![A-Za-z0-9_])' % fnname, _strip_src(it['source']), re.I))
        counts[fnname] = n
    have = {x['fn']: x['count'] for x in g['functions']}
    for fnname, n in counts.items():
        # the begin-line "BEGIN MenuMode"-style tokens are not in this list, and quoted strings are not used by these names
        if have.get(fnname, 0) != n:
            f.append('independent recount differs for %s: gaps %d vs regex %d' % (fnname, have.get(fnname, 0), n))
    # onactivate pseudo gap
    s = g['summary']
    if s['onactivate_player_gated'] != len(g['onactivate_player_gated']):
        f.append('onactivate summary mismatch')
    return f


def check_funcmap_vs_l5(ctx):
    """funcmap claims (effect type exists; host capture yes/no; apply yes/no) must match the L5 source."""
    f = []
    for fn, ent in funcmap.F.items():
        ents = [ent] if 'status' in ent else [ent['player'], ent['other']]
        for e in ents:
            et = e['effect']
            if et is None:
                if e['status'] in ('supported',):
                    f.append('%s: supported without an effect type' % fn)
                continue
            if et not in ctx.eff_names:
                f.append('%s: effect type %s does not exist in synccore effect.h' % (fn, et))
                continue
            if e['status'] == 'supported' and not (e['capture'] == 'yes' and e['apply'] == 'yes'):
                f.append('%s: supported needs capture and apply yes' % fn)
            rec = (re.search(r'record\(\s*%s\s*\{' % et, ctx.hooks) is not None or
                   (et == 'SetScriptVar' and 'SetScriptVar' in ctx.hooks) or (et == 'SetGlobal' and 'SetGlobal{' in ctx.hooks))
            app = re.search(r'get_if<%s>' % et, ctx.apply) is not None
            if e['capture'] == 'yes' and not rec:
                f.append('%s: claims host capture of %s but engine_hooks.cpp never records it' % (fn, et))
            if e['capture'] == 'no' and rec and not e.get('sub'):
                f.append('%s: claims NO host capture of %s but engine_hooks.cpp records it (L5 moved: update funcmap)' % (fn, et))
            if e['apply'] == 'yes' and not app:
                f.append('%s: claims apply of %s but apply_remote.cpp has no branch' % (fn, et))
            if e['apply'] == 'no' and app and not e.get('sub'):
                f.append('%s: claims NO apply of %s but apply_remote.cpp has a branch (L5 moved: update funcmap)' % (fn, et))
    return f


def check_rules_patch(ctx, art):
    f = []
    p = art['rules.patch.json']
    rj = ctx.data.rules['quests']
    for edid, spec in p['quests'].items():
        if edid not in ctx.lr:
            f.append('patch quest %s is not a Lonesome Road quest in quests.json' % edid)
            continue
        cur = rj.get(edid)
        if spec['action'] == 'replace' and not cur:
            f.append('%s: action replace but rules.json has no such quest' % edid)
        if spec['action'] == 'add' and cur:
            f.append('%s: action add but rules.json already has it' % edid)
        if cur and spec['before'] != [x['kind'] for x in cur['rules']]:
            f.append('%s: "before" does not match rules.json' % edid)
        for r in spec['rules']:
            if r['kind'] not in KINDS16:
                f.append('%s: unknown rule kind %s (only the 16 existing kinds allowed; new kinds go under proposals)' % (edid, r['kind']))
            pr = r.get('params', {})
            for key in ('cells', 'markers'):
                for name in pr.get(key, []):
                    if not ctx.resolves(name):
                        f.append('%s: %s entry %s does not resolve' % (edid, key, name))
            if r['kind'] == 'PartyAwareCond':
                for fn, red in pr.get('funcs', {}).items():
                    if red not in REDUCTIONS:
                        f.append('%s: unknown reduction %r for %s' % (edid, red, fn))
                    if fn != fn.lower():
                        f.append('%s: PartyAwareCond key %s must be lowercase (engine lowercases the lookup)' % (edid, fn))
        if len({r['kind'] for r in spec['rules']}) != len(spec['rules']):
            f.append('%s: duplicate rule kind' % edid)
    for k in p['proposals']['new_kinds']:
        if k['kind'] in KINDS16:
            f.append('proposal kind %s already exists' % k['kind'])
    return f


def check_moments(ctx):
    f = []
    for m in moments.resolve(ctx.items, ctx.data):
        for e in m['evidence']:
            if e['line'] is None:
                f.append('moment %s: evidence %s /%s/ does not resolve to a script line' % (m['id'], e['script'], e['regex']))
        for c in m['tether'] or []:
            if not ctx.resolves(c):
                f.append('moment %s: tether cell %s does not resolve' % (m['id'], c))
    return f


def check_status_doc(ctx):
    if not os.path.exists(STATUS_DOC):
        return ['internal questline notes is missing']
    t = read(STATUS_DOC)
    f = []
    for need in ('TOP 10', 'ALL OK'):
        if need not in t:
            f.append('status doc lacks "%s"' % need)
    return f


def run_all(ctx, root, art, include_slow=True):
    f = []
    if include_slow:
        f += check_fresh(ctx, root)
    f += check_inventory(ctx, art)
    f += check_sweep(ctx, art)
    f += check_ids(ctx, art, [read(STATUS_DOC).split('## Test output')[0]] if os.path.exists(STATUS_DOC) else [])
    f += check_gaps(ctx, art)
    f += check_funcmap_vs_l5(ctx)
    f += check_rules_patch(ctx, art)
    f += check_moments(ctx)
    return f


# ----------------------------------------------------------------------------------------------------------- mutants
def mutants(ctx, art):
    """(name, mutated artifacts, expected check function) -- each must yield at least one failure from that check."""
    out = []

    def m(name, fn, check):
        a = copy.deepcopy(art)
        fn(a)
        out.append((name, a, check))

    m('sweep drops one stage', lambda a: a['sweep.json']['quests'][0]['stages'].pop(), check_sweep)
    m('sweep exclusion without reason', lambda a: a['sweep.json']['excluded_quests'][0].update(reason=''), check_sweep)
    m('sweep uses wrong load-order prefix', lambda a: a['sweep.json']['quests'][0].update(runtime_id='01' + a['sweep.json']['quests'][0]['runtime_id'][2:]), check_sweep)
    m('sweep disallowed console verb', lambda a: a['sweep.json']['quests'][1]['stages'][0]['setup'].append({'cmd': 'player.additem f 100', 'verify': False}), check_sweep)
    m('inventory formid prefix 01', lambda a: a['inventory.json']['quests'][0].update(formid='01' + a['inventory.json']['quests'][0]['formid'][2:]), check_inventory)
    m('inventory loses a stage', lambda a: next(q for q in a['inventory.json']['quests'] if q['stages'])['stages'].pop(), check_inventory)
    m('rules patch invents a marker', lambda a: a['rules.patch.json']['quests']['NVDLC04Ending']['rules'][1]['params'].update(markers=['NVDLC04FakeMarkerREF']), check_rules_patch)
    m('rules patch invents a rule kind', lambda a: a['rules.patch.json']['quests']['NVDLC04MQ03b']['rules'].append({'kind': 'NukeVote', 'params': {}, 'origin': 'curated'}), check_rules_patch)
    m('rules patch bad reduction', lambda a: a['rules.patch.json']['quests']['NVDLC04MQ03a']['rules'][1]['params']['funcs'].update(iskiller='max'), check_rules_patch)
    m('gaps drops a location', lambda a: next(x for x in a['gaps.json']['functions'] if x['fn'] == 'setplayerteammate')['locations'].pop(), check_gaps)
    m('gaps points at a wrong line', lambda a: next(x for x in a['gaps.json']['functions'] if x['fn'] == 'swaptexture')['locations'][0].update(line=1), check_gaps)
    m('gaps invents a script', lambda a: next(x for x in a['gaps.json']['functions'] if x['fn'] == 'addperk')['locations'][0].update(script='NVDLC04NoSuchSCRIPT'), check_gaps)
    m('doc cites an invented EditorID', lambda a: a['md'].update({'gaps.md': a['md']['gaps.md'] + '\nSee NVDLC04InventedQuest99.'}), check_ids)
    m('doc cites an invented FormID', lambda a: a['md'].update({'gaps.md': a['md']['gaps.md'] + '\nSee 04FFFFFF.'}), check_ids)
    return out


def mutant_l5_claim(ctx):
    """funcmap says AddPerk is not captured; flip the claim and the L5 consistency check must object."""
    saved = copy.deepcopy(funcmap.F['addperk'])
    try:
        funcmap.F['addperk']['player']['capture'] = 'yes'
        return check_funcmap_vs_l5(ctx)
    finally:
        funcmap.F['addperk'] = saved


def mutant_moment(ctx):
    saved = moments.MOMENTS[0]['evidence']
    try:
        moments.MOMENTS[0]['evidence'] = [('NVDLC04WreckageDoorSCRIPT', r'ThisLineDoesNotExist', 'bogus')]
        return check_moments(ctx)
    finally:
        moments.MOMENTS[0]['evidence'] = saved


def mutant_stale(ctx, root):
    """A hand-edited deliverable must be flagged as stale."""
    tmp = tempfile.mkdtemp(prefix='lrq_m_')
    try:
        for n in DELIVERABLES_JSON + DELIVERABLES_MD:
            if os.path.exists(os.path.join(root, n)):
                shutil.copy(os.path.join(root, n), os.path.join(tmp, n))
        with open(os.path.join(tmp, 'gaps.md'), 'a', encoding='utf-8') as fh:
            fh.write('\nhand edit\n')
        return check_fresh(ctx, tmp)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def main():
    ctx = Ctx()
    root = HERE
    art = load_art(root)
    bad = 0
    fails = run_all(ctx, root, art)
    fails += check_status_doc(ctx)
    for x in fails:
        print('FAIL', x)
    bad += len(fails)
    n_checks = 0
    # real
    s = art['sweep.json']['summary']
    inv = art['inventory.json']['counts']
    g = art['gaps.json']['summary']
    print('real: %d quests, %d stages inventoried; %d stages swept (%d isolate, %d variants); %d gap functions, gap uses %s; %d failures' % (
        inv['quests'], inv['stages'], s['stages'], s['stages_isolate'], s['stage_variants'], g['distinct_functions'], g['gap_uses_by_severity'], len(fails)))
    # mutation twin
    caught = 0
    muts = mutants(ctx, art)
    for name, a, check in muts:
        res = check(ctx, a)
        if res:
            caught += 1
            print('mutant caught: %-38s (%d failures, e.g. %s)' % (name, len(res), res[0][:90]))
        else:
            print('FAIL mutant NOT caught:', name)
            bad += 1
    for name, res in (('funcmap claims AddPerk is captured', mutant_l5_claim(ctx)), ('moment evidence does not resolve', mutant_moment(ctx)),
                      ('hand-edited gaps.md is stale', mutant_stale(ctx, root))):
        if res:
            caught += 1
            print('mutant caught: %-38s (%d failures, e.g. %s)' % (name, len(res), res[0][:90]))
        else:
            print('FAIL mutant NOT caught:', name)
            bad += 1
    total_mut = len(muts) + 3
    print('mutants: %d/%d caught' % (caught, total_mut))
    if bad:
        print('FAILED (%d problems)' % bad)
        return 1
    print('ALL OK')
    return 0


if __name__ == '__main__':
    sys.exit(main())
