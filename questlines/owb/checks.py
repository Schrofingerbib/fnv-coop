"""Validators for the OWB deliverables. Each returns a list of error strings ([] = ok); run_tests.py also feeds each one a
deliberately broken copy (mutation twin) and requires it to report at least one error."""
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import rules_patch  # noqa: E402
import scan  # noqa: E402
from common import PLUGIN  # noqa: E402

HEX = re.compile(r'^[0-9A-F]{8}$')
STATUSES = {'supported', 'partial', 'unsupported', 'n/a', 'unknown'}
SEVERITIES = {'critical', 'high', 'medium', 'low', 'info'}


def _ref_of_hex(d, h):
    """'03002FCC' -> 'OldWorldBlues.esm:002FCC' (None when the prefix is not in the load order)."""
    idx = int(h[:2], 16)
    if idx >= len(d.loadorder):
        return None
    return '%s:%s' % (d.loadorder[idx], h[2:])


# ------------------------------------------------------------------------------------------------ inventory
def validate_inventory(d, inv):
    errs = []
    owb = {q['edid']: q for q in d.owb_quests()}
    got = {q['edid']: q for q in inv['quests']}
    new = d.__dict__.get('_meta_new')
    if set(got) != set(owb):
        errs.append('quest set differs: missing %s extra %s' % (sorted(set(owb) - set(got)), sorted(set(got) - set(owb))))
    for edid, q in got.items():
        src = owb.get(edid)
        if not src:
            continue
        if q['ref'] != src['ref']:
            errs.append('%s ref %s != %s' % (edid, q['ref'], src['ref']))
        if q['runtime_formid'] != d.runtime_id(src['ref']) or not q['runtime_formid'].startswith('03'):
            errs.append('%s runtime id %s' % (edid, q['runtime_formid']))
        if [s['index'] for s in q['stages']] != sorted(s['index'] for s in src['stages']):
            errs.append('%s stage list differs' % edid)
        if [o['index'] for o in q['objectives']] != sorted(o['index'] for o in src['objectives']):
            errs.append('%s objective list differs' % edid)
        for o in q['objectives']:
            for t in o['targets']:
                if d.resolve(t['ref'])[0] is None:
                    errs.append('%s objective %d target %s does not resolve' % (edid, o['index'], t['ref']))
        if q['tier'] != d.risk[src['ref']]['tier']:
            errs.append('%s tier mismatch' % edid)
        if q['role'] not in ('main', 'side', 'system', 'dialogue'):
            errs.append('%s role %s' % (edid, q['role']))
    s = inv['summary']
    if s['quests'] != len(got) or s['stages'] != sum(len(q['stages']) for q in got.values()):
        errs.append('summary counts wrong')
    return errs


def validate_roles(inv_module, d):
    errs = []
    names = {q['edid'] for q in d.owb_quests()}
    for e in inv_module.CORE + inv_module.SIDE:
        if e not in names:
            errs.append('role table names unknown quest %s' % e)
    return errs


# ------------------------------------------------------------------------------------------------ sweep
CMD_RE = {
    'setstage': re.compile(r'^setstage ([0-9A-F]{8}) (\d+)$'),
    'startquest': re.compile(r'^startquest ([0-9A-F]{8})$'),
    'moveto': re.compile(r'^player\.moveto ([0-9A-F]{8})$'),
    'additem': re.compile(r'^player\.additem ([0-9A-F]{8}) (\d+)$'),
    'addperk': re.compile(r'^player\.addperk ([0-9A-F]{8}) 1$'),
    'setvar': re.compile(r'^set ([0-9A-F]{8})\.(\w+) to (-?\d+)$'),
    'setglobal': re.compile(r'^set ([0-9A-F]{8}) to (-?\d+)$'),
}


def check_command(d, cmd, sig_quest_vars):
    """-> error string or None"""
    for kind, rx in CMD_RE.items():
        m = rx.match(cmd)
        if not m:
            continue
        h = m.group(1)
        ref = _ref_of_hex(d, h)
        src, rec = d.resolve(ref) if ref else (None, None)
        if src is None:
            return 'command %r: %s resolves to nothing' % (cmd, h)
        if src == 'refnames.json.gz' and kind in ('setstage', 'startquest', 'additem', 'addperk'):
            return 'command %r: %s only in refnames.json.gz' % (cmd, h)
        if kind in ('setstage', 'startquest'):
            if ref not in d.by_ref:
                return 'command %r: %s is not a quest' % (cmd, h)
            if kind == 'setstage' and int(m.group(2)) not in {s['index'] for s in d.by_ref[ref]['stages']}:
                return 'command %r: stage %s not defined on %s' % (cmd, m.group(2), d.by_ref[ref]['edid'])
        if kind == 'additem' and rec.get('sig') not in ('MISC', 'WEAP', 'ARMO', 'NOTE', 'AMMO', 'ALCH', 'KEYM', 'BOOK', 'CMNY', 'IMOD', 'CHIP', 'MISC'):
            return 'command %r: %s is %s, not an item' % (cmd, h, rec.get('sig'))
        if kind == 'addperk' and rec.get('sig') != 'PERK':
            return 'command %r: %s is %s, not a perk' % (cmd, h, rec.get('sig'))
        if kind == 'moveto' and rec.get('sig') not in ('REFR', 'ACHR', 'ACRE', None):
            return 'command %r: %s is %s, not a reference' % (cmd, h, rec.get('sig'))
        if kind == 'setvar' and ref in d.by_ref:
            q = d.by_ref[ref]
            names = {v['name'].lower() for v in (q.get('script_data') or {}).get('vars', [])}
            if m.group(2).lower() not in names:
                return 'command %r: quest %s has no variable %s' % (cmd, q['edid'], m.group(2))
        return None
    return 'command %r matches no allowed grammar' % cmd


def validate_sweep(d, inv, sw):
    errs = []
    inv_stages = {(q['edid'], s['index']) for q in inv['quests'] for s in q['stages']}
    sw_stages = {(q['edid'], s['index']) for q in sw['quests'] for s in q['stages'] if s['sweep']}
    excl = {(e['quest'], e['stage']): e for e in sw['excluded_stages']}
    for k in inv_stages:
        if k not in sw_stages and k not in excl:
            errs.append('stage %s %d neither swept nor excluded' % k)
    for k, e in excl.items():
        if not e.get('reason'):
            errs.append('excluded stage %s %d has no reason' % k)
    for k in sw_stages - inv_stages:
        errs.append('sweep stage %s %d not in inventory' % k)
    inv_q = {q['edid'] for q in inv['quests'] if q['stages']}
    unswept = {u['edid'] for u in sw['unswept_quests']}
    for q in inv['quests']:
        if not q['stages'] and q['edid'] not in unswept:
            errs.append('quest %s without stages is not listed in unswept_quests' % q['edid'])
    for q in sw['quests']:
        if q['edid'] not in inv_q:
            errs.append('sweep quest %s has no stages in inventory' % q['edid'])
        if d.runtime_id(q['ref']) != q['runtime_formid']:
            errs.append('%s runtime id mismatch' % q['edid'])
        if q['edid'] not in d.by_edid:
            errs.append('%s unknown EDID' % q['edid'])
        cmds = list(q['setup'])
        for s in q['stages']:
            cmds += s['setup'] + [x for v in s['variants'] for x in v['setup']]
            if s['command'] != 'setstage %s %d' % (q['runtime_formid'], s['index']):
                errs.append('%s stage %d command malformed' % (q['edid'], s['index']))
            if not s['setup'] and False:
                errs.append('%s stage %d has no setup' % (q['edid'], s['index']))
        for c in cmds:
            e = check_command(d, c, None)
            if e:
                errs.append('%s: %s' % (q['edid'], e))
    for h in sw['reset_ids']:
        if not HEX.match(h) or _ref_of_hex(d, h) not in d.by_ref:
            errs.append('reset id %s is not a quest' % h)
    if 'refnames.json.gz' in sw['summary']['ids_by_source'] or None in sw['summary']['ids_by_source']:
        errs.append('sweep uses ids that only resolve through refnames.json.gz / nothing: %s' % sw['summary']['ids_by_source'])
    return errs


# ------------------------------------------------------------------------------------------------ gaps
def _unit_sources(d):
    return {u['id']: u['source'].replace('\r', '').split('\n') for u in scan.units(d)}


def validate_gaps(d, gp, units=None):
    errs = []
    units = units or _unit_sources(d)
    if gp['statement_level_calls_not_in_table']:
        errs.append('statement-level calls missing from the function table: %s' % gp['statement_level_calls_not_in_table'])
    total = 0
    for r in gp['functions']:
        if r['status'] not in STATUSES:
            errs.append('%s bad status %s' % (r['fn'], r['status']))
        if r['severity'] not in SEVERITIES:
            errs.append('%s bad severity %s' % (r['fn'], r['severity']))
        total += r['count']
        if len(r['locations']) + r.get('locations_truncated', 0) != r['count']:
            errs.append('%s location count %d+%d != %d' % (r['fn'], len(r['locations']), r.get('locations_truncated', 0), r['count']))
        for loc in r['locations']:
            src = units.get(loc['unit'])
            if src is None:
                errs.append('unknown unit %s' % loc['unit'])
                continue
            if not (1 <= loc['line'] <= len(src)):
                errs.append('%s:%d out of range' % (loc['unit'], loc['line']))
                continue
            line = src[loc['line'] - 1]
            token = r['fn'] if r['fn'] not in ('SetRefVar', 'SetQuestVar', 'SetLocalVar', 'SetGlobal', 'SetUnresolved') else 'set'
            if token.lower() not in line.lower():
                errs.append('%s:%d does not contain %r: %r' % (loc['unit'], loc['line'], token, line.strip()[:80]))
    if total != gp['calls']:
        errs.append('sum of function counts %d != calls %d' % (total, gp['calls']))
    for t, h in gp['hazards'].items():
        if h['count'] != len(h['locations']):
            errs.append('hazard %s count mismatch' % t)
        for loc in h['locations'][:400]:
            src = units.get(loc['unit'])
            if src is None or not (1 <= loc['line'] <= len(src)):
                errs.append('hazard %s bad location %s:%s' % (t, loc['unit'], loc['line']))
    return errs


def validate_gaps_vs_risk(d, gp):
    """risk.json findings (independent L3 classifier) for OWB quests must all land on a line that gaps.json also covers."""
    errs = []
    covered = set()
    by_line_fn = set()   # fallback for 'dep obj <ACTIVATOR EDID>': L3 names the base object, not the script
    for r in gp['functions']:
        for loc in r['locations']:
            covered.add((loc['unit'].lower(), loc['line']))
            by_line_fn.add((loc['line'], r['fn'].lower()))
    for h in gp['hazards'].values():
        for loc in h['locations']:
            covered.add((loc['unit'].lower(), loc['line']))
    # truncated supported/n-a locations are excluded from `covered`, so only test the hazard-relevant categories
    cats = {'CTRL', 'CTRL-RELEASE', 'MOVE', 'COMPANION', 'SAVE'}
    for q in d.owb_quests():
        rk = d.risk[q['ref']]
        script_name = (q.get('script_data') or {}).get('edid')
        for f in rk['findings'] + rk['stage_findings']:
            if f['cat'] not in cats:
                continue
            where = f['where']
            if where.startswith('dep obj '):
                unit = where[len('dep obj '):] + 'SCRIPT'
            elif where.startswith('dep term') or where.startswith('dep pack') or where.startswith('dep '):
                continue
            elif where == 'quest_script':
                unit = script_name or ''
            elif where.startswith('stage '):
                unit = '%s:%s' % (q['edid'], where)
            else:
                continue
            if (unit.lower(), f['line']) not in covered and (f['line'], f['fn']) not in by_line_fn and f['fn'] in (
                    'disableplayercontrols', 'enableplayercontrols', 'moveto'):
                errs.append('risk finding %s %s:%s (%s) not in gaps.json' % (q['edid'], unit, f['line'], f['text'][:40]))
    return errs


# ------------------------------------------------------------------------------------------------ rules patch
def validate_patch(d, patch):
    errs = []
    base = set(patch['base_kinds'])
    newk = {k['kind'] for k in patch['new_kinds']}
    cur = d.rules
    names = {q['edid'] for q in d.owb_quests()}
    resolver = None
    sys.path.insert(0, HERE)
    import sweep
    resolver = sweep.Resolver(d)
    if base != set(rules_patch.BASE_KINDS) or len(base) != 16:
        errs.append('base kinds are not the 16 existing kinds')
    all_kinds = set()
    for k in cur.values():
        for r in k['rules']:
            all_kinds.add(r['kind'])
    if not all_kinds <= base:
        errs.append('rules.json uses kinds outside the documented 16: %s' % sorted(all_kinds - base))
    for nk in patch['new_kinds']:
        if len(nk.get('justification', '')) < 80:
            errs.append('new kind %s lacks a justification' % nk['kind'])
        if nk['kind'] in base:
            errs.append('new kind %s duplicates an existing kind' % nk['kind'])
    for edid, p in patch['quests'].items():
        if edid not in names:
            errs.append('%s is not an OWB quest' % edid)
            continue
        if p['op'] == 'replace' and edid not in cur:
            errs.append('%s: op replace but rules.json has no entry' % edid)
        if p['op'] == 'add' and edid in cur:
            errs.append('%s: op add but rules.json already has an entry' % edid)
        if p['op'] not in ('replace', 'add'):
            errs.append('%s: bad op' % edid)
        if not p.get('why'):
            errs.append('%s: no rationale' % edid)
        seen = set()
        for r in p['rules']:
            if r['kind'] not in base | newk:
                errs.append('%s: unknown kind %s' % (edid, r['kind']))
            if (r['kind'], json.dumps(r['params'], sort_keys=True)) in seen:
                errs.append('%s: duplicate rule %s' % (edid, r['kind']))
            seen.add((r['kind'], json.dumps(r['params'], sort_keys=True)))
            errs += _check_params(d, resolver, edid, r)
    for edid in cur:
        if edid in names and edid not in patch['quests']:
            errs.append('%s has a rules.json entry but the patch says nothing about it' % edid)
    return errs


def _edids_in(v):
    if isinstance(v, str):
        yield v
    elif isinstance(v, list):
        for x in v:
            yield from _edids_in(x)


def _check_params(d, resolver, edid, r):
    errs = []
    k, pr = r['kind'], r['params']
    for key in rules_patch.EDID_PARAM_KEYS:
        for name in _edids_in(pr.get(key, [])):
            try:
                ref, src = resolver.ref(name)
            except KeyError as e:
                errs.append('%s/%s: %s=%r unresolved (%s)' % (edid, k, key, name, e))
                continue
            if src == 'refnames.json.gz':
                errs.append('%s/%s: %s=%r only in refnames.json.gz' % (edid, k, key, name))
    if k == 'SetPlayerControls':
        if pr.get('scope') not in ('all', 'origin'):
            errs.append('%s: SetPlayerControls scope %r' % (edid, pr.get('scope')))
        for m in pr.get('masks', []):
            if not re.match(r'^[01]( [01]){6}$', m):
                errs.append('%s: bad control mask %r' % (edid, m))
        if pr.get('release') != 'host-effect' or not isinstance(pr.get('timeout_s'), int):
            errs.append('%s: SetPlayerControls needs release=host-effect and timeout_s' % edid)
    if k == 'PartyTether' and not (pr.get('cells') or pr.get('anchor')):
        errs.append('%s: PartyTether without cells or anchor' % edid)
    if k == 'PartyWarp' and not (pr.get('markers') or pr.get('cells')):
        errs.append('%s: PartyWarp without markers or cells' % edid)
    if k == 'PartyWarp' and pr.get('absent') not in ('hold', 'warp'):
        errs.append('%s: PartyWarp absent=%r' % (edid, pr.get('absent')))
    if k == 'PartyTether' and pr.get('gather') not in ('auto-warp', 'wait'):
        errs.append('%s: PartyTether gather=%r' % (edid, pr.get('gather')))
    if k == 'PartyAwareCond' and not isinstance(pr.get('funcs'), dict):
        errs.append('%s: PartyAwareCond without funcs' % edid)
    if k == 'LocalQuestScript':
        for name in pr.get('scripts', []):
            if name not in d.by_edid:
                errs.append('%s: LocalQuestScript names unknown quest %s' % (edid, name))
    for key in ('cells',):
        for name in _edids_in(pr.get(key, [])):
            try:
                ref, src = resolver.ref(name)
            except KeyError:
                continue   # already reported above
            if d.resolve(ref)[1].get('sig') != 'CELL':
                errs.append('%s/%s: %r is not a cell' % (edid, k, name))
    return errs


# ------------------------------------------------------------------------------------------------ docs / specs
def validate_specs(d, specs):
    errs = []
    infos = {i['ref']: i for i in d.infos}
    for sid, sp in specs.items():
        ids = [s['id'] for s in sp['steps']]
        if len(ids) != len(set(ids)):
            errs.append('%s duplicate step ids' % sid)
        if sp.get('schema') != 1 or sp.get('kind') != 'dialogue':
            errs.append('%s bad header' % sid)
        for s in sp['steps']:
            if s['action'] not in ('setup', 'talk', 'parallel', 'pick', 'pick_info', 'pick_nowait', 'leave', 'event', 'assert'):
                errs.append('%s step %s bad action' % (sid, s['id']))
            for key in ('info', 'greeting_info'):
                if s.get(key) and s[key] not in infos:
                    errs.append('%s %s %s not an INFO of the DLC' % (sid, s['id'], s[key]))
            if s['action'] == 'pick':
                i = infos.get(s['info'])
                if i and (not i['responses'] or not i['responses'][0]['text'].startswith(s['line'])):
                    errs.append('%s %s line does not match INFO text' % (sid, s['id']))
                if i and d.dial_by_ref[i['topic']]['name'] != s['prompt']:
                    errs.append('%s %s prompt does not match topic name' % (sid, s['id']))
        for cmd in sum([x for s in sp['steps'] if s['action'] == 'setup' for x in s.get('players', {}).values()], []) + \
                sp['setup'].get('cmds', []) + sum(sp['setup'].get('players', {}).values(), []):
            if cmd.startswith(('setstage', 'player.additem', 'player.addperk')):
                e = check_command(d, cmd, None)
                if e:
                    errs.append('%s: %s' % (sid, e))
    return errs


def validate_docs_current(expected, actual):
    """expected/actual: {path: text}"""
    return ['stale or missing: %s' % p for p, t in expected.items() if actual.get(p) != t]
