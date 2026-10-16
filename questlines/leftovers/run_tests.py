#!/usr/bin/env python3
"""Tests for the `leftovers` questline deliverables, with a mutation twin.

    python -I questlines/leftovers/run_tests.py     (prints ALL OK and exits 0 when everything holds)

Part 1 validates the committed artifacts against out/questdata (IDs resolve, every stage swept or excluded with a reason,
patch keys exist, every script function mapped, L5 status table agrees with the hook source, locations point at real lines,
generation is deterministic).
Part 2 (mutation twin) applies deliberately broken variants and requires every validator to catch its mutant.
"""
import copy
import json
import os
import re
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import collect as C
import coverage as K  # noqa: E402
import tables as T  # noqa: E402
import gen as G  # noqa: E402

REPO = C.REPO
KINDS16 = {'Barrier', 'CompanionOwner', 'ExcludeFromGate', 'FastTravelLock', 'InitiatorDialogue', 'PartyAwareCond', 'PartyTether',
           'PartyWarp', 'PerPlayerScene', 'QuestItemDup', 'RemoveItemPerOwner', 'RewardAll', 'SaveSuppress', 'SetPlayerControls',
           'ShareKarmaRep', 'SlideshowEnd'}
ORIGINS = {'proposed-derived', 'proposed-curated', 'proposed-param-extension'}


def load_json(name):
    with open(os.path.join(HERE, name), encoding='utf-8') as f:
        return json.load(f)


class World:
    """Everything validators need, loaded once."""

    def __init__(self):
        self.ctx = C.load()
        self.names = G.Names(self.ctx)
        self.order = C.load_order()
        self.rules = json.load(open(os.path.join(REPO, 'docs', 'quest-policies', 'rules.json'), encoding='utf-8'))
        self.known_hex = {}
        for table in (self.names.edid_of,):
            for ref in table:
                try:
                    self.known_hex[C.fid(ref, self.order)] = ref
                except (ValueError, IndexError):
                    pass
        for q in self.ctx['d']['quests']:
            self.known_hex[C.fid(q['ref'], self.order)] = q['ref']
        self.src = {}
        for edid in C.QUESTS:
            d = {}
            for where, sedid, src, dctx in C.scripts_of(self.ctx, self.ctx['by_edid'][edid]):
                d.setdefault(where, []).append(src)
            self.src[edid] = d


# ---------------------------------------------------------------------------------------------------------------------
# validators: each returns a list of error strings
# ---------------------------------------------------------------------------------------------------------------------
def v_inventory(inv, w):
    errs = []
    by = {q['edid']: q for q in inv['quests']}
    if set(by) != set(C.QUESTS):
        errs.append('inventory quest set differs from collect.QUESTS')
    for edid, iq in by.items():
        q = w.ctx['by_edid'].get(edid)
        if q is None:
            errs.append('%s: not in quests.json' % edid)
            continue
        if iq['ref'] != q['ref']:
            errs.append('%s: ref mismatch' % edid)
        if iq['formid'] != C.fid(q['ref'], w.order):
            errs.append('%s: formid %s != %s' % (edid, iq['formid'], C.fid(q['ref'], w.order)))
        if iq['formid'] not in w.known_hex:
            errs.append('%s: formid does not resolve' % edid)
        want = sorted(s['index'] for s in q['stages'])
        got = [s['index'] for s in iq['stages']]
        if got != want:
            errs.append('%s: stage list %s != %s' % (edid, got, want))
        if sorted(o['index'] for o in iq['objectives']) != sorted(o['index'] for o in q['objectives']):
            errs.append('%s: objective list differs' % edid)
        if iq['tier'] != w.ctx['risk'][q['ref']]['tier']:
            errs.append('%s: tier differs from risk.json' % edid)
        for o in iq['objectives']:
            for t in o['targets']:
                if t['ref'] and t['edid'] != w.names.name(t['ref']):
                    errs.append('%s objective %s: target edid %r != %r' % (edid, o['index'], t['edid'], w.names.name(t['ref'])))
        for grp in ('reads_state_of', 'driven_by'):
            for e in iq['prerequisites'][grp]:
                if e['quest'] not in w.ctx['by_edid']:
                    errs.append('%s: %s names unknown quest %s' % (edid, grp, e['quest']))
        if C.GROUP_OF[edid] != iq['group']:
            errs.append('%s: group mismatch' % edid)
    return errs


CMD_RE = re.compile(r'^(startquest|completequest) ([0-9A-F]{8})$|^setstage ([0-9A-F]{8}) (\d+)$|^set ([0-9A-F]{8})\.(\w+) to (-?\d+)$')


def check_cmd(cmd, w):
    m = CMD_RE.match(cmd)
    if not m:
        return 'unparseable command %r' % cmd
    hx = m.group(2) or m.group(3) or m.group(5)
    ref = w.known_hex.get(hx)
    if ref is None:
        return 'FormID %s does not resolve (%r)' % (hx, cmd)
    q = w.ctx['by_ref'].get(ref)
    if q is None:
        return 'FormID %s is not a quest (%r)' % (hx, cmd)
    if m.group(3) and int(m.group(4)) not in {s['index'] for s in q['stages']}:
        return 'stage %s not in quest %s (%r)' % (m.group(4), q['edid'], cmd)
    if m.group(5) and m.group(6) not in {v['name'] for v in (q.get('script_data') or {}).get('vars', [])}:
        return 'variable %s not in quest %s (%r)' % (m.group(6), q['edid'], cmd)
    return None


def v_sweep(sw, inv, w):
    errs = []
    inv_by = {q['edid']: q for q in inv['quests']}
    sw_by = {q['edid']: q for q in sw['quests']}
    if set(sw_by) != set(inv_by):
        errs.append('sweep quests differ from inventory')
    excluded = {(e['edid'], e['stage']): e for e in sw['excluded']}
    for e in sw['excluded']:
        if not (e.get('reason') or '').strip():
            errs.append('exclusion without reason: %s' % e)
    for edid, iq in inv_by.items():
        sq = sw_by.get(edid)
        if sq is None:
            continue
        got = {s['index']: s for s in sq['stages']}
        for s in iq['stages']:
            if s['index'] in got:
                st = got[s['index']]
                want = 'setstage %s %d' % (iq['formid'], s['index'])
                if st['command'] != want:
                    errs.append('%s stage %s: command %r != %r' % (edid, s['index'], st['command'], want))
            elif (edid, s['index']) not in excluded:
                errs.append('%s stage %s neither swept nor excluded' % (edid, s['index']))
        if not iq['stages'] and (edid, None) not in excluded:
            errs.append('%s: container without exclusion entry' % edid)
        for s in sq['stages']:
            if s['index'] not in {x['index'] for x in iq['stages']}:
                errs.append('%s: swept stage %s not in inventory' % (edid, s['index']))
            for cmd in s['setup'] + [s['command']] + [h['suggested'] for h in s.get('suggested_setup', [])]:
                e = check_cmd(cmd, w)
                if e:
                    errs.append('%s stage %s: %s' % (edid, s['index'], e))
        for cmd in sq['setup']:
            e = check_cmd(cmd, w)
            if e:
                errs.append('%s setup: %s' % (edid, e))
    if sw['summary']['stages'] != sum(len(q['stages']) for q in sw['quests']):
        errs.append('sweep summary stage count wrong')
    return errs


def find_line(w, edid, where, line, text):
    """True when the located script line contains the finding text."""
    if line == 0:
        return True    # condition / metadata findings have no script line
    srcs = w.src[edid].get(where)
    if not srcs:
        return False
    needle = re.sub(r'\s+', ' ', text.strip()).lower()[:40]
    for src in srcs:
        lines = src.splitlines()
        if 0 < line <= len(lines):
            have = re.sub(r'\s+', ' ', lines[line - 1].strip()).lower()
            if needle and needle[:30] in have:
                return True
    return False


def v_gaps(g, w, sample_every=1):
    errs = []
    if g['unmapped']:
        errs.append('unmapped functions: %s' % g['unmapped'])
    if list(g['effects']) != T.EFFECTS_ALL:
        errs.append('effect list differs from tables.EFFECTS')
    n = 0
    for key, e in g['functions'].items():
        if e['count'] != len(e['locations']):
            errs.append('%s: count != len(locations)' % key)
        for l in e['locations']:
            n += 1
            if n % sample_every:
                continue
            if l['quest'] not in w.ctx['by_edid']:
                errs.append('%s: unknown quest %s' % (key, l['quest']))
            elif (l['where'].startswith(('stage', 'quest_script', 'dep')) or l['where'].endswith((' begin', ' end')))                     and not find_line(w, l['quest'], l['where'], l['line'], l['text']):
                errs.append('%s: %s %s line %s does not contain %r' % (key, l['quest'], l['where'], l['line'], l['text'][:30]))
        if e['kind'] != 'read' and e['effect'] is not None and e['effect'] not in T.EFFECTS_ALL:
            errs.append('%s: effect %s not in vocabulary' % (key, e['effect']))
    for h, v in g['hazards'].items():
        if v['count'] != len(v['locations']):
            errs.append('hazard %s count mismatch' % h)
    sev_total = sum(e['count'] for e in g['functions'].values() if e['kind'] != 'read')
    if sev_total != sum(g['summary']['severity_occurrences'].values()):
        errs.append('severity totals do not add up')
    return errs


def v_status_vs_source(status):
    """tables.EFFECT_STATUS must agree with what plugin/hooks actually contains (drift guard)."""
    errs = []
    hooks = open(os.path.join(REPO, 'plugin', 'hooks', 'engine_hooks.cpp'), encoding='utf-8', errors='replace').read()
    apply_ = open(os.path.join(REPO, 'plugin', 'hooks', 'apply_remote.cpp'), encoding='utf-8', errors='replace').read()
    for eff, (cap, app, _) in status.items():
        if eff.startswith('Barrier'):
            continue
        in_cap = bool(re.search(r'record\(\s*%s\b' % eff, hooks)) or (eff == 'SetScriptVar' and 'recordScriptVar' in hooks) \
            or bool(re.search(r'\b%s\s+(\w+);(?:(?!\n\s*\n).)*?record\(\s*\1\s*\)' % eff, hooks, re.S))
        in_app = bool(re.search(r'get_if<\s*%s\s*>' % eff, apply_))
        if cap in ('supported', 'partial') and not in_cap:
            errs.append('%s: capture=%s but engine_hooks.cpp has no record(%s)' % (eff, cap, eff))
        if cap == 'missing' and in_cap:
            errs.append('%s: capture=missing but engine_hooks.cpp records it' % eff)
        if app in ('supported', 'partial') and not in_app:
            errs.append('%s: apply=%s but apply_remote.cpp has no get_if<%s>' % (eff, app, eff))
        if app == 'missing' and in_app:
            errs.append('%s: apply=missing but apply_remote.cpp handles it' % eff)
    return errs


def v_effect_h(effects=None):
    """tables.EFFECTS_ALL must equal the `using Effect = std::variant<...>` list in synccore/include/fnvcoop/effect.h (same order)."""
    txt = open(os.path.join(REPO, 'synccore', 'include', 'fnvcoop', 'effect.h'), encoding='utf-8').read()
    m = re.search(r'using Effect\s*=\s*std::variant<(.*?)>;', txt, re.S)
    names = [x.strip() for x in m.group(1).split(',') if x.strip()]
    return [] if names == (effects or T.EFFECTS_ALL) else ['effect.h variant %s != tables.EFFECTS_ALL' % names]


def v_vocab(effects=None):
    """tables.EFFECTS must equal the tag table of docs/protocol.md section 6 (same order)."""
    txt = open(os.path.join(REPO, 'docs', 'protocol.md'), encoding='utf-8').read()
    sec = txt[txt.index('## 6. Effect tags'):]
    nxt = re.search(r'^## ', sec[5:], re.M)
    sec = sec[:nxt.start() + 5] if nxt else sec
    tags = re.findall(r'^\|\s*(\d+)\s*\|\s*(\w+)\s*\|', sec, re.M)
    names = [n for i, n in sorted((int(i), n) for i, n in tags)]
    return [] if names == (effects or T.EFFECTS) else ['protocol.md effect tags %s != tables.EFFECTS' % names]


def v_patch(patch, w):
    errs = []
    for edid, e in patch['quests'].items():
        if edid not in w.ctx['by_edid']:
            errs.append('patch quest %s not in quests.json' % edid)
            continue
        have = {r['kind'] for r in w.rules['quests'].get(edid, {}).get('rules', [])}
        if e['action'] == 'add-rules' and edid not in w.rules['quests']:
            errs.append('%s: add-rules but no rules.json entry' % edid)
        if e['action'] == 'new-quest-entry' and edid in w.rules['quests']:
            errs.append('%s: new-quest-entry but rules.json already has it' % edid)
        if e['action'] not in ('add-rules', 'new-quest-entry'):
            errs.append('%s: unknown action %s' % (edid, e['action']))
        if not e['rules']:
            errs.append('%s: empty rules' % edid)
        for r in e['rules']:
            if r['kind'] not in KINDS16:
                errs.append('%s: kind %s not one of the 16' % (edid, r['kind']))
            if r['origin'] not in ORIGINS:
                errs.append('%s: bad origin %s' % (edid, r['origin']))
            if r['kind'] in have and not r.get('extends_existing'):
                errs.append('%s: %s already present in rules.json' % (edid, r['kind']))
            if not (r.get('why') or '').strip():
                errs.append('%s: %s has no why' % (edid, r['kind']))
            p = r['params']
            for cell in p.get('cells', []):
                ref = w.names.resolve(cell)
                if ref is None or w.names.sig_of.get(ref) != 'CELL':
                    errs.append('%s: cell %s does not resolve to a CELL' % (edid, cell))
            for mk in p.get('markers', []):
                if w.names.resolve(mk) is None:
                    errs.append('%s: marker %s does not resolve' % (edid, mk))
    if set(patch['kinds_used']) - KINDS16:
        errs.append('kinds_used has unknown kinds')
    return errs


def v_dialogue(md_text, w):
    errs = []
    m = re.search(r'```json\n(.*?)\n```', md_text, re.S)
    if not m:
        return ['dialogue.md has no JSON stub block']
    stubs = json.loads(m.group(1))
    if not stubs:
        errs.append('no scenario stubs')
    infos = {}
    for lst in w.ctx['infos'].values():
        for i in lst:
            infos[i['ref']] = i
    for s in stubs:
        st = s['step']
        i = infos.get(st['info'])
        if i is None:
            errs.append('stub info %s not in data' % st['info'])
            continue
        if i['topic'] != st['topic']:
            errs.append('stub %s topic mismatch' % st['info'])
        d = w.names.dials.get(st['topic'])
        if d is None or d['edid'] != st['topic_edid']:
            errs.append('stub %s topic edid mismatch' % st['info'])
        line = i['responses'][0]['text'] if i['responses'] else ''
        if not line.startswith(st['line']):
            errs.append('stub %s line mismatch' % st['info'])
        if w.ctx['by_ref'][i['quest']]['edid'] != s['quest']:
            errs.append('stub %s quest mismatch' % st['info'])
    return errs


def v_coverage(cov, w):
    """coverage_check.json: partition of the stage-bearing quests, claimed lanes really list the quest, uncovered == 0."""
    errs = []
    stage_q = {q['edid'] for q in w.ctx['d']['quests'] if q['stages']}
    debug = set(cov['debug_excluded'])
    covered = set(cov['covered_by'])
    if covered & debug:
        errs.append('debug quests also marked covered: %s' % sorted(covered & debug))
    missing = stage_q - covered - debug - set(cov['uncovered'])
    if missing:
        errs.append('stage-bearing quests absent from the check: %s' % sorted(missing)[:10])
    if covered - stage_q:
        errs.append('covered quests that have no stages: %s' % sorted(covered - stage_q)[:10])
    if cov['uncovered_count'] != len(cov['uncovered']) or cov['covered_count'] != len(covered):
        errs.append('counts disagree with lists')
    if cov['uncovered_count'] != 0:
        errs.append('uncovered quests remain: %s' % cov['uncovered'])
    if cov['stage_bearing_quests'] != len(stage_q):
        errs.append('stage_bearing_quests %s != %d' % (cov['stage_bearing_quests'], len(stage_q)))
    lanes = K.lane_map()
    for edid, lanes_of in cov['covered_by'].items():
        if not lanes_of:
            errs.append('%s: empty lane list' % edid)
        for ln in lanes_of:
            if ln not in lanes:
                errs.append('%s: lane %s has no inventory' % (edid, ln))
            elif edid not in lanes[ln][0]:
                errs.append('%s: lane %s does not list it' % (edid, ln))
    for edid in C.QUESTS:
        if 'leftovers' not in cov['covered_by'].get(edid, []):
            errs.append('%s: own quest is not mapped to leftovers' % edid)
    for edid in ('VMS12', 'VMS22', 'VMS26', 'VMS37', 'VMS43', 'VMS44', 'VMS46', 'VMS57'):
        if edid not in cov['covered_by']:
            errs.append('lead-named quest %s not covered' % edid)
    return errs


def v_hazard_coverage(g):
    need = {'essential', 'spawn', 'companion', 'teleport', 'cinematic', 'menu', 'timer', 'radio', 'ending', 'fast_travel', 'save', 'perk',
            'faction_change', 'karma_rep'}
    have = {n for n, _, _ in T.HAZARD_PATTERNS}
    errs = ['hazard pattern missing: %s' % n for n in sorted(need - have)]
    if 'kill_everyone' not in g['hazards']:
        errs.append('kill_everyone scan produced no entry')
    return errs


# ---------------------------------------------------------------------------------------------------------------------
class Runner:
    def __init__(self):
        self.fail = []
        self.n = 0

    def check(self, name, errs):
        self.n += 1
        if errs:
            self.fail.append(name)
            print('FAIL %s' % name)
            for e in errs[:8]:
                print('   - %s' % e)
            if len(errs) > 8:
                print('   ... %d more' % (len(errs) - 8))
        else:
            print('ok   %s' % name)

    def mutant(self, name, errs):
        """A mutant must be caught: errs must be non-empty."""
        self.n += 1
        if errs:
            print('ok   mutant caught: %s (%d errors)' % (name, len(errs)))
        else:
            self.fail.append('mutant survived: ' + name)
            print('FAIL mutant SURVIVED: %s' % name)


def main():
    w = World()
    inv, sw, g, patch = (load_json(n) for n in ('inventory.json', 'sweep.json', 'gaps.json', 'rules.patch.json'))
    dmd = open(os.path.join(HERE, 'dialogue.md'), encoding='utf-8').read()
    r = Runner()
    print('== real artifacts ==')
    r.check('inventory resolves against quests.json/risk.json', v_inventory(inv, w))
    r.check('sweep covers every stage (or excludes with reason); all commands resolve', v_sweep(sw, inv, w))
    r.check('gaps: all functions mapped, locations point at real script lines', v_gaps(g, w))
    r.check('L5 status table agrees with plugin/hooks source', v_status_vs_source(T.EFFECT_STATUS))
    r.check('effect vocabulary equals docs/protocol.md section 6', v_vocab())
    r.check('full effect list equals synccore effect.h variant', v_effect_h())
    r.check('rules.patch.json keys, kinds, cells resolve', v_patch(patch, w))
    r.check('dialogue stubs match infos/dials data', v_dialogue(dmd, w))
    r.check('hazard coverage (brief list)', v_hazard_coverage(g))
    cov = load_json('coverage_check.json')
    r.check('coverage_check.json: every stage-bearing quest mapped, uncovered == 0', v_coverage(cov, w))
    ctr = {'inv': inv['summary']['stages'], 'sweep': sw['summary']['stages'], 'data': sum(len(w.ctx['by_edid'][e]['stages']) for e in C.QUESTS)}
    r.check('stage counts agree (inventory == sweep == quests.json)', [] if len(set(ctr.values())) == 1 else ['%s' % ctr])
    with tempfile.TemporaryDirectory() as td:
        G.main(['--out', td])
        errs = []
        for name in ('inventory.json', 'sweep.json', 'gaps.json', 'gaps.md', 'rules.patch.json', 'policy_review.md', 'dialogue.md', 'blockers.json', 'coverage_check.json'):
            a = open(os.path.join(td, name), encoding='utf-8').read()
            b = open(os.path.join(HERE, name), encoding='utf-8').read()
            if a != b:
                errs.append('%s on disk differs from a fresh generation (run gen.py)' % name)
        r.check('committed files equal a fresh deterministic generation', errs)

    print('== mutation twin: each broken variant must be caught ==')
    m = copy.deepcopy(inv)
    m['quests'][0]['formid'] = m['quests'][0]['formid'][:-1] + ('0' if m['quests'][0]['formid'][-1] != '0' else '1')
    r.mutant('inventory formid off by one', v_inventory(m, w))
    m = copy.deepcopy(inv)
    q = next(q for q in m['quests'] if len(q['stages']) > 1)
    q['stages'].pop()
    r.mutant('inventory drops a stage', v_inventory(m, w))
    m = copy.deepcopy(inv)
    m['quests'][1]['stages'] and m['quests'][1]['objectives'].append({'index': 9999, 'text': 'x', 'targets': []})
    r.mutant('inventory invents an objective', v_inventory(m, w))
    m = copy.deepcopy(sw)
    q = next(q for q in m['quests'] if len(q['stages']) > 1)
    q['stages'].pop()
    r.mutant('sweep silently drops a stage', v_sweep(m, inv, w))
    m = copy.deepcopy(sw)
    q = next(q for q in m['quests'] if q['stages'])
    q['stages'][0]['setup'].append('setstage 0DEADBEE 10')
    r.mutant('sweep setup with an invented FormID', v_sweep(m, inv, w))
    m = copy.deepcopy(sw)
    q = next(q for q in m['quests'] if q['stages'])
    q['stages'][0]['setup'].append('startquest %s' % q['formid'].replace('0', 'F', 1))
    r.mutant('sweep setup with a corrupted FormID', v_sweep(m, inv, w))
    m = copy.deepcopy(sw)
    m['excluded'].append({'edid': 'VMS55', 'stage': 1, 'reason': ''})
    r.mutant('sweep exclusion without a reason', v_sweep(m, inv, w))
    m = copy.deepcopy(g)
    key = next(k for k, e in m['functions'].items() if e['kind'] != 'read' and e['locations'] and e['locations'][0]['line'] > 0
               and not e['locations'][0]['where'].startswith('info'))
    m['functions'][key]['locations'][0]['line'] += 1
    r.mutant('gaps location line shifted by one', v_gaps(m, w))
    m = copy.deepcopy(g)
    m['unmapped'] = ['frobnicate|ACTOR']
    r.mutant('gaps with an unmapped function', v_gaps(m, w))
    m = copy.deepcopy(g)
    key = next(iter(m['functions']))
    m['functions'][key]['locations'].pop()
    r.mutant('gaps count out of sync with locations', v_gaps(m, w))
    bad = copy.deepcopy(T.EFFECT_STATUS)
    bad['CompanionOwner'] = ('supported', 'supported', 'x')
    r.mutant('status table claims CompanionOwner captured', v_status_vs_source(bad))
    bad = copy.deepcopy(T.EFFECT_STATUS)
    bad['ObjectState'] = ('missing', 'missing', 'x')
    r.mutant('status table claims ObjectState missing (alias capture)', v_status_vs_source(bad))
    bad = copy.deepcopy(T.EFFECT_STATUS)
    bad['SetStage'] = ('missing', 'missing', 'x')
    r.mutant('status table claims SetStage missing', v_status_vs_source(bad))
    r.mutant('vocabulary list missing an effect', v_vocab(T.EFFECTS[:-1]))
    r.mutant('effect.h list missing an effect', v_effect_h(T.EFFECTS_ALL[:-1]))
    m = copy.deepcopy(patch)
    k = next(iter(m['quests']))
    m['quests'][k]['rules'][0]['kind'] = 'TeleportEverybody'
    r.mutant('patch uses an invented rule kind', v_patch(m, w))
    m = copy.deepcopy(patch)
    m['quests']['VMS_DOES_NOT_EXIST'] = copy.deepcopy(m['quests'][k])
    r.mutant('patch names an unknown quest', v_patch(m, w))
    m = copy.deepcopy(patch)
    tq = next(e for e in m['quests'].values() if any(rr['kind'] == 'PartyTether' for rr in e['rules']))
    next(rr for rr in tq['rules'] if rr['kind'] == 'PartyTether')['params']['cells'] = ['NoSuchCell']
    r.mutant('patch tether names an unresolvable cell', v_patch(m, w))
    m = copy.deepcopy(patch)
    kk = next(k2 for k2, e in m['quests'].items() if e['action'] == 'add-rules')
    have_kind = next(iter({r2['kind'] for r2 in w.rules['quests'][kk]['rules']}))
    m['quests'][kk]['rules'].append({'kind': have_kind, 'params': {}, 'origin': 'proposed-derived', 'why': 'dup'})
    r.mutant('patch re-proposes an existing rule', v_patch(m, w))
    bad_md = re.sub(r'"info": "FalloutNV.esm:([0-9A-F]+)"', lambda mo: '"info": "FalloutNV.esm:%s"' % ('F' + mo.group(1)[1:]), dmd, count=1)
    r.mutant('dialogue stub with a wrong INFO id', v_dialogue(bad_md, w))
    bad_md = dmd.replace('"line": "', '"line": "ZZ', 1)
    r.mutant('dialogue stub with a wrong line', v_dialogue(bad_md, w))
    m = copy.deepcopy(g)
    m['hazards'].pop('kill_everyone')
    r.mutant('hazard scan loses kill_everyone', v_hazard_coverage(m))

    m = copy.deepcopy(cov)
    victim = next(iter(m['covered_by']))
    del m['covered_by'][victim]
    r.mutant('coverage drops a quest', v_coverage(m, w))
    m = copy.deepcopy(cov)
    m['covered_by'][next(iter(m['covered_by']))] = ['no_such_lane']
    r.mutant('coverage claims a nonexistent lane', v_coverage(m, w))
    m = copy.deepcopy(cov)
    m['covered_by']['VMS26'] = ['towns']
    r.mutant('coverage claims a lane that does not list the quest', v_coverage(m, w))
    m = copy.deepcopy(cov)
    m['uncovered'] = ['VMS26']
    m['uncovered_count'] = 1
    del m['covered_by']['VMS26']
    m['covered_count'] -= 1
    r.mutant('coverage reports an uncovered quest', v_coverage(m, w))
    lanes_wo = {k: v for k, v in K.lane_map().items() if k != 'leftovers'}
    r.mutant('coverage without this lane has uncovered quests', K.build(lanes=lanes_wo)['uncovered'])

    print()
    if r.fail:
        print('FAILED: %d of %d checks: %s' % (len(r.fail), r.n, ', '.join(r.fail)))
        return 1
    print('%d checks passed' % r.n)
    print('ALL OK')
    return 0


class Tee:
    def __init__(self, *fs):
        self.fs = fs

    def write(self, x):
        for f in self.fs:
            f.write(x)

    def flush(self):
        for f in self.fs:
            f.flush()


if __name__ == '__main__':
    import io
    buf = io.StringIO()
    real = sys.stdout
    sys.stdout = Tee(real, buf)
    try:
        code = main()
    finally:
        sys.stdout = real
    with open(os.path.join(HERE, 'last_test_output.txt'), 'w', encoding='utf-8', newline=chr(10)) as f:
        f.write(buf.getvalue())
    sys.exit(code)
