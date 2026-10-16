"""Validators for the towns questline outputs.  `validate(bundle, d)` returns a list of error strings (empty = valid).

bundle = {inventory, sweep, gaps, patch, specs, dialogue}  (the parsed JSON files)
"""
import os
import re

import analysis
import fnmap
import lib
import policy

HEX6 = re.compile(r'^[0-9A-F]{6}$')
HEX8 = re.compile(r'^[0-9A-F]{8}$')
CMD_START = re.compile(r'^startquest ([0-9A-F]{6}|[0-9A-F]{8})$')
CMD_SETSTAGE = re.compile(r'^setstage ([0-9A-F]{6}|[0-9A-F]{8}) (\d+)$')
CMD_OBJ = re.compile(r'^SetObjectiveDisplayed ([0-9A-F]{6}|[0-9A-F]{8}) (\d+) 1$')
CMD_SETVAR = re.compile(r'^set ([0-9A-F]{6}|[0-9A-F]{8})\.(\w+) to (-?\d+(\.\d+)?)$')
CMD_WAIT = re.compile(r'^GetStageDone ([0-9A-F]{6}|[0-9A-F]{8}) (\d+)$')
SEVERITIES = {'high', 'medium', 'low', 'ok'}
STATUSES = {'supported', 'partial', 'missing'}


def effect_names():
    """Effect type names declared in synccore/include/fnvcoop/effect.h (the variant list)."""
    txt = open(os.path.join(lib.REPO, 'synccore', 'include', 'fnvcoop', 'effect.h'), encoding='utf-8').read()
    m = re.search(r'using Effect = std::variant<(.*?)>;', txt, re.S)
    return {x.strip() for x in m.group(1).split(',') if x.strip()}


class Ctx:
    def __init__(self, d):
        self.d = d
        self.runtime = {}          # runtime hex -> quest ref
        for q in d.quests['quests']:
            self.runtime[analysis.hex_id(d, q['ref'])] = q['ref']
        self.edids = {}
        for r, f in d.forms.items():
            if f.get('edid'):
                self.edids.setdefault(f['edid'].lower(), []).append(r)
        for q in d.quests['quests']:
            if q.get('edid'):
                self.edids.setdefault(q['edid'].lower(), []).append(q['ref'])
        self.effects = effect_names()
        self.rules = policy.load_rules()['quests']
        self.scope = lib.all_quests()


def _resolve_cmd_id(ctx, hexid, errs, where):
    if hexid not in ctx.runtime:
        errs.append('%s: FormID %s does not resolve to a quest' % (where, hexid))
        return None
    return ctx.runtime[hexid]


def check_inventory(b, ctx, errs):
    d = ctx.d
    inv = b['inventory']['quests']
    got = [(q['town_assignment'], q['edid']) for q in inv]
    want = [(t, e) for t, e, r in ctx.scope]
    if got != want:
        errs.append('inventory: quest list differs from lib.all_quests()')
    # every policy-file section of the four towns must be present
    pol = analysis.policy_sections()
    for e in pol:
        if e not in {q['edid'] for q in inv}:
            errs.append('inventory: policy section %s missing' % e)
    for q in inv:
        src = d.by_edid.get(q['edid'])
        if not src:
            errs.append('inventory: %s not in quests.json' % q['edid'])
            continue
        if q['ref'] != src['ref'] or q['formid'] != src['formid']:
            errs.append('inventory: %s FormID %s/%s != data %s/%s' % (q['edid'], q['ref'], q['formid'], src['ref'], src['formid']))
        if q['runtime_formid'] != analysis.hex_id(d, src['ref']):
            errs.append('inventory: %s runtime id %s wrong' % (q['edid'], q['runtime_formid']))
        if [s['index'] for s in q['stages']] != sorted(s['index'] for s in src['stages']):
            errs.append('inventory: %s stage list differs from data' % q['edid'])
        if sorted(o['index'] for o in q['objectives']) != sorted(o['index'] for o in src['objectives']):
            errs.append('inventory: %s objective list differs from data' % q['edid'])
        if q['tier'] != d.risk[src['ref']]['tier']:
            errs.append('inventory: %s tier mismatch' % q['edid'])
        if q['dialogue']['infos'] != len(d.infos.get(src['ref'], [])):
            errs.append('inventory: %s info count mismatch' % q['edid'])
        if q['dialogue']['topics'] != len(src.get('topics', [])):
            errs.append('inventory: %s topic count mismatch' % q['edid'])
        if q['in_policy_file'] != (q['edid'] in pol):
            errs.append('inventory: %s in_policy_file flag wrong' % q['edid'])
        for k in ('gated_by', 'triggered_by_stage', 'written_by_var', 'triggers_stage', 'gates_other', 'writes_var_of'):
            for r in q['prerequisites'][k]:
                if r['quest'] not in d.by_edid:
                    errs.append('inventory: %s prerequisite %s (%s) not in quests.json' % (q['edid'], r['quest'], k))
        if q['geography_note'] == 'unknown' and q['role'] == 'core':
            errs.append('inventory: %s has no geography note' % q['edid'])


def check_sweep(b, ctx, errs):
    d = ctx.d
    sw = b['sweep']
    seen = set()
    tot = swept = 0
    for q in sw['quests']:
        src = d.by_edid.get(q['edid'])
        if not src or q['ref'] != src['ref']:
            errs.append('sweep: %s ref mismatch' % q['edid'])
            continue
        if q['runtime_formid'] != analysis.hex_id(d, src['ref']):
            errs.append('sweep: %s runtime id mismatch' % q['edid'])
        seen.add(q['edid'])
        idx = [s['index'] for s in q['stages']]
        exc = [s['index'] for s in q['excluded_stages']]
        want = sorted(s['index'] for s in src['stages'])
        if sorted(idx + exc) != want:
            errs.append('sweep: %s stages not covered: swept %s excluded %s data %s' % (q['edid'], idx, exc, want))
        if set(idx) & set(exc):
            errs.append('sweep: %s stage both swept and excluded' % q['edid'])
        for s in q['excluded_stages']:
            if not (s.get('reason') or '').strip():
                errs.append('sweep: %s stage %s excluded without a reason' % (q['edid'], s['index']))
        tot += len(want)
        swept += len(idx)
        if not src['stages'] and not q['stageless_reason']:
            errs.append('sweep: %s stageless without a reason' % q['edid'])
        vnames = {v['name'] for v in (src.get('script_data') or {}).get('vars', [])}
        cmds = list(q['setup'])
        for s in q['stages']:
            cmds += s['setup'] + s['chain']
            for v in s['variants']:
                cmds += v['setup']
            if not CMD_SETSTAGE.match(s['set_cmd']) or not CMD_WAIT.match(s['wait_cmd']):
                errs.append('sweep: %s stage %s bad command' % (q['edid'], s['index']))
            m = CMD_SETSTAGE.match(s['set_cmd'])
            if m and (_resolve_cmd_id(ctx, m.group(1), errs, 'sweep %s' % q['edid']) != src['ref'] or int(m.group(2)) != s['index']):
                errs.append('sweep: %s stage %s set_cmd targets another quest/stage' % (q['edid'], s['index']))
        objs = {o['index'] for o in src['objectives']}
        for o in q['objective_steps']:
            if o['objective'] not in objs or not CMD_OBJ.match(o['set_cmd']):
                errs.append('sweep: %s objective step %s invalid' % (q['edid'], o['objective']))
            cmds += o['setup']
        # command syntax + ids
        for c in cmds:
            cmd = c['cmd']
            m = CMD_START.match(cmd) or CMD_SETSTAGE.match(cmd) or CMD_SETVAR.match(cmd)
            if not m:
                errs.append('sweep: %s command not allowed/malformed: %s' % (q['edid'], cmd))
                continue
            ref = _resolve_cmd_id(ctx, m.group(1), errs, 'sweep %s' % q['edid'])
            if CMD_SETSTAGE.match(cmd) and ref:
                tq = d.by_ref[ref]
                if int(m.group(2)) not in {s['index'] for s in tq['stages']}:
                    errs.append('sweep: %s setup stage %s does not exist in %s' % (q['edid'], m.group(2), tq['edid']))
            if CMD_SETVAR.match(cmd) and ref:
                tq = d.by_ref[ref]
                names = {v['name'] for v in (tq.get('script_data') or {}).get('vars', [])}
                if m.group(2) not in names:
                    errs.append('sweep: %s var %s not a script variable of %s' % (q['edid'], m.group(2), tq['edid']))
        for a in q['advisory_prereqs']:
            if a['quest'] not in d.by_edid:
                errs.append('sweep: advisory prerequisite %s unknown' % a['quest'])
    if seen != {e for t, e, r in ctx.scope}:
        errs.append('sweep: quest set differs from scope')
    s = sw['summary']
    if (s['stages_total'], s['stages_swept'], s['stages_excluded']) != (tot, swept, tot - swept):
        errs.append('sweep: summary counts wrong %s vs %s' % (s, (tot, swept)))


def check_gaps(b, ctx, errs):
    d = ctx.d
    g = b['gaps']
    items = g['items']
    scope = {e for t, e, r in ctx.scope}
    srcs = {}
    for t, e, r in ctx.scope:
        q = d.by_edid[e]
        srcs[e] = q
    for n, it in enumerate(items):
        if it['quest'] not in scope:
            errs.append('gaps[%d]: quest %s not in scope' % (n, it['quest']))
            continue
        if it['effect'] is not None and it['effect'] not in ctx.effects:
            errs.append('gaps[%d]: effect %s is not a synccore Effect' % (n, it['effect']))
        if it['effect'] is not None and it['effect'] not in fnmap.EFFECT_STATUS:
            errs.append('gaps[%d]: effect %s has no L5 status' % (n, it['effect']))
        if it['status'] not in STATUSES or it['severity'] not in SEVERITIES or it['capture'] not in STATUSES or it['apply'] not in STATUSES:
            errs.append('gaps[%d]: bad status/severity' % n)
        if it['effect'] in fnmap.EFFECT_STATUS and it['class'] not in ('condition', 'read'):
            cap, app, _ = fnmap.EFFECT_STATUS[it['effect']]
            if (it['capture'], it['apply']) != (cap, app):
                errs.append('gaps[%d]: capture/apply differ from fnmap for %s' % (n, it['effect']))
        if it['status'] == 'supported' and it['severity'] != 'ok':
            errs.append('gaps[%d]: supported item with severity %s' % (n, it['severity']))
        if it['status'] != 'supported' and it['severity'] == 'ok':
            errs.append('gaps[%d]: unsupported item marked ok' % n)
        if it['class'] == 'unmapped':
            errs.append('gaps[%d]: unmapped function %s (extend fnmap.F)' % (n, it['fn']))
        # location sanity: line inside the script it names
        if it['source'] in ('stage', 'info-begin', 'info-end', 'quest-script') and it['line']:
            q = srcs[it['quest']]
            if it['source'] == 'quest-script':
                text = (q.get('script_data') or {}).get('source', '')
            elif it['source'] == 'stage':
                m = re.search(r'stage (\d+)$', it['where'])
                text = '\n'.join((e.get('script') or {}).get('source', '') for s in q['stages'] if m and s['index'] == int(m.group(1)) for e in s['entries'])
            else:
                text = None
            if text is not None and it['line'] > len(text.replace('\r', '').split('\n')):
                errs.append('gaps[%d]: line %s beyond script %s' % (n, it['line'], it['script']))
    # completeness: every oracle statement of the scope is an item
    refs = {srcs[e]['ref'] for e in scope}
    qscripts = {srcs[e]['script'] for e in scope if srcs[e].get('script')}
    want = 0
    for s in d.effects['scripts']:
        if s['quest'] in refs:
            want += len(s['effects']) + len(s['unmodeled'])
    for bl in d.effects['blocks']:
        if bl['script'] in qscripts:
            want += len(bl['effects']) + len(bl['unmodeled'])
    have = sum(1 for it in items if it['source'] in ('stage', 'info-begin', 'info-end', 'quest-script'))
    if want != have:
        errs.append('gaps: oracle statements %d != gap items %d' % (want, have))
    # summary
    from collections import Counter
    cs = Counter(i['severity'] for i in items)
    if dict(cs) != g['summary']['by_severity']:
        errs.append('gaps: summary by_severity wrong')
    uns = sum(v['count'] for v in g['summary']['unsupported'].values())
    if uns != sum(1 for i in items if i['status'] != 'supported'):
        errs.append('gaps: unsupported group counts do not add up')
    for k, v in g['summary']['unsupported'].items():
        if len(v['locations']) != v['count']:
            errs.append('gaps: group %s locations != count' % k)
    for t in g['text_hazards']:
        if t['quest'] not in scope:
            errs.append('gaps: text hazard quest %s' % t['quest'])


def check_patch(b, ctx, errs):
    d = ctx.d
    p = b['patch']
    if set(p['kinds_allowed']) != set(policy.KINDS) or len(policy.KINDS) != 16:
        errs.append('patch: kinds_allowed is not the 16 existing kinds')
    names = lambda sec: p.get(sec, {})

    def check_rule(edid, r, where):
        if r['kind'] not in policy.KINDS:
            errs.append('patch: %s %s unknown kind %s' % (edid, where, r['kind']))
        if r.get('origin') not in ('derived', 'curated'):
            errs.append('patch: %s %s bad origin' % (edid, where))
        if not (r.get('reason') or '').strip():
            errs.append('patch: %s %s (%s) has no reason' % (edid, where, r['kind']))
        prm = r.get('params', {})
        for key in ('cells',):
            for c in prm.get(key, []):
                if not any(ctx.d.forms[x]['sig'] == 'CELL' for x in ctx.edids.get(c.lower(), []) if x in ctx.d.forms):
                    errs.append('patch: %s cell %s does not resolve in forms.json' % (edid, c))
        for m in prm.get('markers', []):
            if m.lower() not in ctx.edids:
                errs.append('patch: %s marker %s does not resolve' % (edid, m))
        for fn, red in (prm.get('funcs') or {}).items():
            if red not in policy.REDUCTIONS:
                errs.append('patch: %s reduction %s for %s not in the L4 set' % (edid, red, fn))
        if r['kind'] == 'PartyTether' and not prm.get('cells'):
            errs.append('patch: %s adds an inert PartyTether (empty cells)' % edid)
    for sec, existing in (('quests', True), ('new_quests', False)):
        for edid, e in names(sec).items():
            if edid not in d.by_edid:
                errs.append('patch: %s not in quests.json' % edid)
                continue
            have = {r['kind'] for r in ctx.rules[edid]['rules']} if edid in ctx.rules else None
            if existing and have is None:
                errs.append('patch: %s listed under quests but has no rules.json entry' % edid)
            if not existing and have is not None:
                errs.append('patch: %s listed under new_quests but already has an entry' % edid)
            added = set()
            for r in e.get('add', []):
                check_rule(edid, r, 'add')
                if have and r['kind'] in have:
                    errs.append('patch: %s adds %s which it already has' % (edid, r['kind']))
                if r['kind'] in added:
                    errs.append('patch: %s adds %s twice' % (edid, r['kind']))
                added.add(r['kind'])
            for k, v in (e.get('set_params') or {}).items():
                if k not in policy.KINDS:
                    errs.append('patch: %s set_params kind %s' % (edid, k))
                if have is not None and k not in have:
                    errs.append('patch: %s set_params for %s which it does not have' % (edid, k))
                for fn, red in (v.get('funcs') or {}).items():
                    if red not in policy.REDUCTIONS:
                        errs.append('patch: %s reduction %s' % (edid, red))
            for k in e.get('remove', []):
                if have is None or k not in have:
                    errs.append('patch: %s removes %s which it does not have' % (edid, k))
            if not existing:
                if e.get('tier') != d.risk[d.by_edid[edid]['ref']]['tier']:
                    errs.append('patch: %s tier mismatch' % edid)
    if not p.get('engine_proposals'):
        errs.append('patch: engine_proposals missing')


def check_dialogue(b, ctx, errs):
    d = ctx.d
    infos = {i['ref']: i for ii in d.infos.values() for i in ii}
    dials = {x['ref'] for x in lib.gload('dials.json.gz')}
    scope = {e for t, e, r in ctx.scope}
    for r in b['dialogue']['rows']:
        if r['quest'] not in scope:
            errs.append('dialogue: quest %s' % r['quest'])
        if r['info'] not in infos:
            errs.append('dialogue: INFO %s does not resolve' % r['info'])
        elif infos[r['info']]['topic'] != r['topic'] or r['topic'] not in dials:
            errs.append('dialogue: topic of INFO %s wrong' % r['info'])
    for s in b['specs']['specs']:
        st = s['steps'][2]
        if st['info'] not in infos or infos[st['info']]['topic'] != st['topic'] or st['topic'] not in dials:
            errs.append('spec %s: INFO/topic do not resolve' % s['id'])
        n = s['steps'][1]
        if n['npc_ref'] not in d.forms or d.forms[n['npc_ref']]['sig'] not in ('ACHR', 'ACRE'):
            errs.append('spec %s: npc_ref %s does not resolve' % (s['id'], n['npc_ref']))
        for q in s['quests']:
            if q not in d.by_edid:
                errs.append('spec %s: quest %s' % (s['id'], q))
        for cmd in s['setup'].get('host', []):
            m = CMD_START.match(cmd) or CMD_SETSTAGE.match(cmd)
            if not m or m.group(1) not in ctx.runtime:
                errs.append('spec %s: bad setup command %s' % (s['id'], cmd))
        for qn in (st['expect'].get('stage') or {}):
            if qn.lower() not in ctx.edids:
                errs.append('spec %s: expected stage quest %s unknown' % (s['id'], qn))
        for k in (st['expect'].get('script_var') or {}):
            if k.split('.')[0].lower() not in ctx.edids:
                errs.append('spec %s: script var owner %s unknown' % (s['id'], k))
    if len(b['specs']['specs']) % 2:
        errs.append('dialogue specs must come in pass/fail pairs')


def validate(bundle, d, ctx=None):
    ctx = ctx or Ctx(d)
    errs = []
    check_inventory(bundle, ctx, errs)
    check_sweep(bundle, ctx, errs)
    check_gaps(bundle, ctx, errs)
    check_patch(bundle, ctx, errs)
    check_dialogue(bundle, ctx, errs)
    return errs
