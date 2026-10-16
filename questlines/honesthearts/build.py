"""Generator for the Honest Hearts questline deliverables.

    python -I questlines/honesthearts/build.py            # (re)write inventory.json, sweep.json, gaps.*, rules.patch.json, dialogue.*, policy_review.md

Everything is derived from out/questdata (read-only) plus questlines/honesthearts/curated.py (hand-written judgment whose
evidence is resolved against the data; a spec that does not resolve is a hard error). `generate(mutation=...)` returns the
artifacts in memory; run_tests.py uses the mutations as test twins (a broken generator the validators must reject).
"""
import collections
import copy
import datetime
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import curated  # noqa: E402
import fnmap  # noqa: E402
import qlib  # noqa: E402
import scan  # noqa: E402

KINDS = ['Barrier', 'CompanionOwner', 'ExcludeFromGate', 'FastTravelLock', 'InitiatorDialogue', 'PartyAwareCond', 'PartyTether',
         'PartyWarp', 'PerPlayerScene', 'QuestItemDup', 'RemoveItemPerOwner', 'RewardAll', 'SaveSuppress', 'SetPlayerControls',
         'ShareKarmaRep', 'SlideshowEnd']
SEV_ORDER = ['none', 'low', 'medium', 'high', 'blocker']
STATUS_RANK = {'supported': 0, 'n/a': 0, 'partial': 1, 'missing': 2}
AV_NAMES = {5: 'Strength', 6: 'Perception', 7: 'Endurance', 8: 'Charisma', 9: 'Intelligence', 10: 'Agility', 11: 'Luck',
            32: 'Barter', 33: 'BigGuns', 34: 'EnergyWeapons', 35: 'Explosives', 36: 'Lockpick', 37: 'Medicine', 38: 'MeleeWeapons',
            39: 'Repair', 40: 'Science', 41: 'Guns', 42: 'Sneak', 43: 'Speech', 44: 'Survival', 45: 'Unarmed', 46: 'InventoryWeight',
            23: 'Karma'}
MUTATIONS = ['wrong_prefix', 'drop_stage', 'bogus_edid', 'unknown_effect', 'bad_patch_kind', 'bad_patch_quest', 'bad_evidence',
             'bad_setup_cmd', 'drop_quest', 'bad_info', 'tether_old_cell']


def fatal(msg):
    raise RuntimeError(msg)


# ---------------------------------------------------------------------------------------------------------------------
# evidence resolution
# ---------------------------------------------------------------------------------------------------------------------
def _norm(s):
    return re.sub(r'\s+', ' ', s.replace('\r', '')).strip().lower()


class Index:
    def __init__(self, d):
        self.d = d
        self.scripts = {}
        for ref, s in d.extra['scpt'].items():
            if ref.startswith(qlib.PLUGIN + ':'):
                sc = s.get('script') or {}
                self.scripts[s.get('edid')] = {'ref': ref, 'source': sc.get('source') or '', 'type': sc.get('type')}
        self.infos = {}
        for qref, lst in d.infos.items():
            for i in lst:
                self.infos[i['ref'].split(':')[1].upper()] = (qref, i)
        self.edids = {}
        for ref, f in d.forms.items():
            if f.get('edid'):
                self.edids.setdefault(f['edid'], []).append(ref)
        for q in d.all_quests.values():
            if q.get('edid'):
                self.edids.setdefault(q['edid'], []).append(q['ref'])
        for ref, f in d.refnames.items():
            if f.get('edid'):
                self.edids.setdefault(f['edid'], []).append(ref)
        for name, s in self.scripts.items():
            self.edids.setdefault(name, []).append(s['ref'])

    def quest_script(self, edid):
        q = self.d.by_edid[edid]
        sd = q.get('script_data') or {}
        return sd.get('source') or ''

    def stage_source(self, edid, idx):
        q = self.d.by_edid[edid]
        out = []
        for st in q['stages']:
            if st['index'] == idx:
                out += [e['script']['source'] for e in st['entries'] if e.get('script') and e['script'].get('source')]
        return '\n'.join(out)


def find_line(src, needle):
    n = _norm(needle)
    for i, ln in enumerate(src.replace('\r', '').split('\n'), 1):
        if n in _norm(qlib.strip_comment(ln)):
            return i, qlib.clip(qlib.strip_comment(ln).strip(), 160)
    return None, None


def resolve_evidence(ix, ev):
    """-> {'where', 'line', 'text'}; raises when the evidence does not resolve."""
    if 'script' in ev:
        s = ix.scripts.get(ev['script'])
        if not s:
            fatal('evidence: script %r not found' % ev['script'])
        line, text = find_line(s['source'], ev['find'])
        where = 'script %s' % ev['script']
    elif 'quest_script' in ev:
        src = ix.quest_script(ev['quest_script'])
        line, text = find_line(src, ev['find'])
        where = 'quest script %s' % ev['quest_script']
    elif 'stage' in ev:
        qe, idx = ev['stage'].split(':')
        src = ix.stage_source(qe, int(idx))
        line, text = find_line(src, ev['find'])
        where = 'stage %s:%s' % (qe, idx)
    elif 'info' in ev:
        loc = ix.infos.get(ev['info'].upper())
        if not loc:
            fatal('evidence: INFO %r not found' % ev['info'])
        info = loc[1]
        if 'cond' in ev:
            funcs = [c['func'] for c in info['conditions']]
            if ev['cond'] not in funcs:
                fatal('evidence: INFO %s has no condition %s (has %s)' % (ev['info'], ev['cond'], funcs))
            return {'where': 'info %s conditions' % ev['info'], 'line': 0, 'text': ev['cond']}
        line, text = None, None
        for k in ('begin', 'end'):
            sc = info.get(k)
            if sc and sc.get('source'):
                line, text = find_line(sc['source'], ev['find'])
                if line:
                    where = 'info %s %s' % (ev['info'], k)
                    break
        else:
            where = 'info %s' % ev['info']
    else:
        fatal('evidence spec %r' % (ev,))
    if line is None:
        fatal('evidence does not resolve: %r' % (ev,))
    return {'where': where, 'line': line, 'text': text}


def resolve_all(ix, evs):
    return [dict(resolve_evidence(ix, e), spec=e) for e in evs]


# ---------------------------------------------------------------------------------------------------------------------
# helpers on data
# ---------------------------------------------------------------------------------------------------------------------
def role_of(edid):
    if edid.startswith('NVDLC02Dialogue') or edid.endswith('GenericDialogue') or edid == 'NVDLC02GenericAdultCombat':
        return 'dialogue'
    if re.match(r'^NVDLC02MQ0[0-7][a-d]?$', edid):
        return 'story-optional' if edid in ('NVDLC02MQ05a', 'NVDLC02MQ05b', 'NVDLC02MQ05c') else 'story'
    if re.match(r'^NVDLC02MS0\d$', edid):
        return 'sidequest'
    if edid in ('NVDLC02Ending', 'NVDLC02IntroSlides'):
        return 'slideshow'
    if edid in ('NVDLC02Enc', 'NVDLC02Eve'):
        return 'encounter-system'
    if 'Timer' in edid or 'ZionWLEnc' in edid or 'ForceGreet' in edid or edid == 'NVDLC02MQ03CodeHolderQuest':
        return 'scene-script'
    if edid in ('NVDLC02FollowersQuest', 'NVDLC02FollowersBanter'):
        return 'companions'
    if edid == 'NVDLC02DeathTracker':
        return 'fail-tracker'
    return 'system'


def fmt_cond(d, c):
    def r(x):
        if isinstance(x, str) and ':' in x:
            return d.edid_of(x) or x
        return x
    return '%s(%s,%s) %s %s' % (c['func'], r(c.get('p1')), r(c.get('p2')), c['op'], c['value'])


def declared_vars(src):
    return {m.group(2) for m in re.finditer(r'(?im)^\s*(short|long|int|float|ref|reference)\s+(\w+)', src or '')}


def runtime(d, ref):
    return d.fid(ref)


def stage_calls_index(calls):
    idx = collections.defaultdict(list)
    for c in calls:
        if c['unit_kind'] == 'stage':
            idx[(c['quest'], c['stage'])].append(c)
    return idx


def collect_triggers(d, calls):
    """target HH quest edid -> list of {by, fn, stage, line}; plus external quests touched."""
    trig = collections.defaultdict(list)
    ext = collections.defaultdict(list)
    for c in calls:
        if c['fn'] not in ('SetStage', 'StartQuest', 'StopQuest'):
            continue
        for m in re.finditer(r'(?i)\b(setstage|startquest|stopquest)\s+(\w+)(?:\s+(\d+))?', c['text']):
            if m.group(1).lower() != c['fn'].lower():
                continue
            tgt = m.group(2)
            rec = {'by': c['unit'], 'by_quest': c.get('quest') or c.get('script'), 'fn': c['fn'], 'stage': int(m.group(3)) if m.group(3) else None,
                   'line': c['line'], 'block': c['block']}
            (trig if tgt in d.by_edid else ext)[tgt].append(rec)
    return trig, ext


# ---------------------------------------------------------------------------------------------------------------------
# inventory
# ---------------------------------------------------------------------------------------------------------------------
def build_inventory(d, ix, calls, trig, mutation=None):
    quests = []
    for q in sorted(d.quests, key=lambda q: q['edid']):
        edid = q['edid']
        rk = d.risk.get(q['ref']) or {}
        infos = d.infos.get(q['ref'], [])
        speakers = sorted({d.edid_of(i['speaker']) for i in infos if i.get('speaker') and d.edid_of(i['speaker'])})
        n_unresolved_speakers = len({i['speaker'] for i in infos if i.get('speaker') and not d.edid_of(i['speaker'])})
        stages = []
        for st in sorted(q['stages'], key=lambda s: s['index']):
            ents = st['entries']
            stages.append({'index': st['index'], 'flags': sorted({f for e in ents for f in e['flags']}),
                           'log': next((e['text'] for e in ents if e.get('text')), None), 'entries': len(ents),
                           'has_script': any(e.get('script') and e['script'].get('source') for e in ents)})
        objectives = [{'index': o['index'], 'text': o.get('text')} for o in sorted(q['objectives'], key=lambda o: o['index'])]
        qconds = [fmt_cond(d, c) for c in q['conditions']]
        sconds = sorted({fmt_cond(d, c) for st in q['stages'] for e in st['entries'] for c in e['conditions']})
        own = [t for t in trig.get(edid, []) if not (t['by_quest'] == edid and t['by'].startswith('stage '))]
        tby = sorted(({'by': t['by'], 'fn': t['fn'], 'stage': t['stage'], 'line': t['line']} for t in own if t['fn'] != 'StopQuest'),
                     key=lambda t: (t['by'], t['line']))
        stopped = sorted(({'by': t['by'], 'line': t['line']} for t in own if t['fn'] == 'StopQuest'), key=lambda t: (t['by'], t['line']))
        # dialogue anywhere in the plugin that conditions on, or whose scripts touch, this quest
        rel_infos, rel_topics, rel_quests = 0, set(), collections.Counter()
        pat = re.compile(r'(?<![A-Za-z0-9_])%s(?![A-Za-z0-9_])' % re.escape(edid), re.I)
        for qref2, lst2 in d.infos.items():
            for i in lst2:
                hit = any(c.get('p1') == q['ref'] for c in i['conditions']) or any(
                    i.get(k) and i[k].get('source') and pat.search(i[k]['source']) for k in ('begin', 'end'))
                if hit:
                    rel_infos += 1
                    if i.get('topic'):
                        rel_topics.add(i['topic'])
                    rel_quests[d.edid_of(qref2)] += 1
        rules = (d.rules['quests'].get(edid) or {}).get('rules', [])
        gin = [{'src': e['src'], 'kind': e['kind'], 'strength': e['strength'], 'count': e['count'], 'example': qlib.clip(e['evidence'][0]['text'], 100)}
               for e in d.graph['edges'] if e['dst'] == edid and e['src'] != edid]
        gout = [{'dst': e['dst'], 'kind': e['kind'], 'strength': e['strength'], 'count': e['count'], 'example': qlib.clip(e['evidence'][0]['text'], 100)}
                for e in d.graph['edges'] if e['src'] == edid and e['dst'] != edid]
        item = {
            'edid': edid, 'ref': q['ref'],
            'formid': runtime(d, q['ref']),
            'name': q.get('name'), 'role': role_of(edid), 'purpose': curated.PURPOSE.get(edid) or curated.DIALOGUE_NOTES.get(edid),
            'tier': rk.get('tier'), 'tier_direct': rk.get('tier_direct'), 'risk_score': rk.get('score'),
            'risk_cats': rk.get('cat_max_level'), 'flags': q['flags'], 'start_enabled': 'start_enabled' in q['flags'],
            'script': q.get('script'), 'script_edid': (q.get('script_data') or {}).get('edid'),
            'stages': stages, 'objectives': objectives,
            'dialogue': {'topics': len(q['topics']), 'infos': len(q['infos']), 'scripted_infos': sum(1 for i in infos if i.get('begin') or i.get('end')),
                         'related_infos': rel_infos, 'related_topics': len(rel_topics), 'related_in_quests': dict(sorted(rel_quests.items())),
                         'speakers': speakers,
                         'speakers_unresolved': n_unresolved_speakers},
            'prerequisites': {'quest_conditions': qconds, 'stage_conditions': sconds, 'triggered_by': tby, 'stopped_by': stopped,
                              'entry_note': curated.ENTRY_NOTES.get(edid)},
            'rules_existing': [r['kind'] for r in rules],
            'graph': {'in': sorted(gin, key=lambda e: (e['src'], e['kind'])), 'out': sorted(gout, key=lambda e: (e['dst'], e['kind']))},
        }
        quests.append(item)
    if mutation == 'wrong_prefix':
        for it in quests:
            it['formid'] = '01' + it['formid'][2:]
    if mutation == 'drop_quest':
        quests = [q for q in quests if q['edid'] != 'NVDLC02MQ04']
    if mutation == 'bogus_edid':
        quests[0]['edid'] = quests[0]['edid'] + 'X'
    by_role = collections.Counter(q['role'] for q in quests)
    by_tier = collections.Counter(q['tier'] for q in quests)
    inv = {'schema': 1, 'questline': qlib.SLUG, 'plugin': qlib.PLUGIN, 'load_order_index': d.plugins.index(qlib.PLUGIN),
           'source': ['out/questdata/quests.json', 'out/questdata/risk.json', 'out/questdata/infos.jsonl.gz', 'out/coverage/questgraph.json',
                      'docs/quest-policies/rules.json', 'tools/questdata/loadorder.json'],
           'counts': {'quests': len(quests), 'with_stages': sum(1 for q in quests if q['stages']),
                      'stages': sum(len(q['stages']) for q in quests), 'objectives': sum(len(q['objectives']) for q in quests),
                      'infos': sum(q['dialogue']['infos'] for q in quests), 'topics': sum(q['dialogue']['topics'] for q in quests),
                      'by_role': dict(sorted(by_role.items())), 'by_tier': dict(sorted((k or 'unknown', v) for k, v in by_tier.items()))},
           'quests': quests}
    return inv


# ---------------------------------------------------------------------------------------------------------------------
# sweep
# ---------------------------------------------------------------------------------------------------------------------
OBJ_RE = re.compile(r'(?im)^\s*setobjective(displayed|completed)\s+(\w+)\s+(\d+)\s+([01])')
GETSTAGE_RE = re.compile(r'(?i)\bget(stage|stagedone)\s+(\w+)\s*(==|>=|>)?\s*(\d+)?')
GETOBJ_RE = re.compile(r'(?i)\bgetobjective(displayed|completed)\s+(\w+)\s+(\d+)')
QVAR_RE = re.compile(r'(?i)\b(\w+)\.(\w+)\s*(==|!=|>=|<=|<|>)\s*(-?\d+(?:\.\d+)?)')


def build_sweep(d, ix, calls, inv, trig, ext, mutation=None):
    sc_idx = stage_calls_index(calls)
    quests_out, excluded_quests, exclusions = [], [], []
    run_last = []
    total_stages = 0
    for item in inv['quests']:
        edid = item['edid']
        q = d.by_edid.get(edid)
        if q is None:
            continue
        if not q['stages']:
            excluded_quests.append({'edid': edid, 'ref': q['ref'], 'reason': 'no stages: %s quest (%d INFOs, %d topics); covered by gaps.* and dialogue.md, nothing for a stage sweep to set'
                                    % (item['role'], item['dialogue']['infos'], item['dialogue']['topics'])})
            continue
        cid = item['formid']
        rk = d.risk.get(q['ref']) or {}
        setup = []
        if not item['start_enabled']:
            setup.append({'cmd': 'startquest %s' % cid, 'why': 'quest is not start-enabled; objectives only display for a running quest'})
        qsrc = (q.get('script_data') or {}).get('source') or ''
        reqs = set()
        for m in GETSTAGE_RE.finditer(qsrc):
            oq, op, n = m.group(2), m.group(3), m.group(4)
            if oq in d.by_edid and oq != edid and op in ('==', '>=') and n:
                reqs.add((oq, int(n)))
        for oq, n in sorted(reqs):
            if any(s['index'] == n for s in d.by_edid[oq]['stages']):
                setup.append({'cmd': 'setstage %s %d' % (d.fid(d.by_edid[oq]['ref']), n),
                              'why': 'quest script of %s only reacts when %s has reached stage %d' % (edid, oq, n)})
        stages = []
        stage_src = {s['index']: ix.stage_source(edid, s['index']) for s in q['stages']}
        for st in sorted(q['stages'], key=lambda s: s['index']):
            idx = st['index']
            src = stage_src[idx]
            pre, seen = [], set()
            # same-quest objective / stage reads -> earlier stages that produce them
            reads = []
            for m in GETOBJ_RE.finditer(src):
                if m.group(2) == edid:
                    reads.append(('obj', m.group(1).lower(), int(m.group(3))))
            for m in GETSTAGE_RE.finditer(src):
                if m.group(2) == edid and m.group(4) and m.group(3) in ('==', '>='):
                    reads.append(('stage', m.group(1).lower(), int(m.group(4))))
            for kind, sub, n in reads:
                cand = None
                if kind == 'stage':
                    if n < idx and any(s['index'] == n for s in q['stages']):
                        cand = n
                else:
                    for s2 in sorted(q['stages'], key=lambda s: s['index']):
                        if s2['index'] >= idx:
                            break
                        pat = re.compile(r'(?im)^\s*setobjective%s\s+%s\s+%d\s+1' % ('displayed' if sub == 'displayed' else 'completed', re.escape(edid), n))
                        if pat.search(stage_src[s2['index']]):
                            cand = s2['index']
                            break
                if cand is not None and cand not in seen:
                    seen.add(cand)
                    pre.append({'cmd': 'setstage %s %d' % (cid, cand), 'why': 'stage %d script reads %s %s %d' % (idx, 'objective' if kind == 'obj' else 'stage', sub, n)})
            pre.sort(key=lambda p: int(p['cmd'].split()[-1]))
            # quest-variable branches of other HH quests
            vars_seen = collections.OrderedDict()
            for m in QVAR_RE.finditer(src):
                oq, var, op, val = m.group(1), m.group(2), m.group(3), m.group(4)
                if oq in d.by_edid and var in declared_vars((d.by_edid[oq].get('script_data') or {}).get('source')):
                    vars_seen.setdefault((oq, var), []).append((op, float(val)))
            variants = []
            if vars_seen:
                keys = list(vars_seen)[:4]
                a, b = [], []
                for oq, var in keys:
                    ops = vars_seen[(oq, var)]
                    eq = next((v for o, v in ops if o == '=='), None)
                    on = int(eq) if eq is not None else 1
                    off = 0 if on != 0 else 1
                    a.append({'cmd': 'set %s.%s to %d' % (d.fid(d.by_edid[oq]['ref']), var, on), 'why': '%s.%s read by stage %d script' % (oq, var, idx)})
                    b.append({'cmd': 'set %s.%s to %d' % (d.fid(d.by_edid[oq]['ref']), var, off), 'why': '%s.%s read by stage %d script' % (oq, var, idx)})
                variants = [{'id': 'vars-on', 'setup': a}, {'id': 'vars-off', 'setup': b}]
            calls_here = sc_idx.get((edid, idx), [])
            hz = set()
            for c in calls_here:
                fl = c['fn'].lower()
                if fl in ('disableplayercontrols', 'setinchargen'):
                    hz.add('lock')
                elif fl in ('moveto', 'movetomarkerwithfade', 'setpos') and (c.get('subject') or '').lower() == 'player':
                    hz.add('player-move')
                elif fl == 'enablefasttravel':
                    hz.add('travel')
                elif fl == 'autosave':
                    hz.add('save')
                elif fl == 'placeatme':
                    hz.add('spawn')
                elif fl == 'setplayerteammate':
                    hz.add('companion')
            if len(re.findall(r'(?i)setstage\s+\w+\s+255', src)) >= 5:
                hz.add('mass-fail')
            hz = sorted(hz)
            objfx = []
            for kind, qn, n, v in OBJ_RE.findall(src):
                objfx.append({'quest': qn, 'objective': int(n), 'kind': kind.lower(), 'value': int(v)})
            findings = [{'cat': f['cat'], 'fn': f['fn'], 'level': f['level'], 'text': f['text'], 'line': f.get('line')}
                        for f in rk.get('stage_findings', []) if f.get('where') == 'stage %d' % idx]
            stages.append({'index': idx, 'log': next((e['text'] for e in st['entries'] if e.get('text')), None),
                           'flags': sorted({f for e in st['entries'] for f in e['flags']}), 'has_script': bool(src.strip()),
                           'findings': findings, 'objective_effects': objfx, 'pre': pre, 'variants': variants,
                           'command': 'setstage %s %d' % (cid, idx), 'hazards': hz})
            total_stages += 1
        if mutation == 'drop_stage' and edid == 'NVDLC02MQ01':
            stages = stages[:-1]
        if mutation == 'bad_setup_cmd' and edid == 'NVDLC02MQ01':
            setup.append({'cmd': 'teleportplayer 02008893', 'why': 'bogus'})
        last = any(set(s['hazards']) & {'lock', 'player-move', 'mass-fail'} for s in stages) or item['role'] in ('slideshow',)
        if last:
            run_last.append(edid)
        quests_out.append({
            'edid': edid, 'ref': q['ref'], 'formid': cid, 'name': q.get('name'), 'role': item['role'], 'tier': rk.get('tier'),
            'tier_direct': rk.get('tier_direct'), 'cats': rk.get('cat_max_level'), 'priority': q['priority'], 'flags': q['flags'],
            'script': q.get('script'), 'globals': [], 'start_enabled': item['start_enabled'], 'setup': setup,
            'stages': stages, 'objectives': item['objectives'], 'run_last': last})
    reset_ids = [q['formid'] for q in quests_out]
    touched = []
    for tq, recs in sorted(ext.items()):
        f = ix.edids.get(tq)
        why = sorted({'%s %s' % (r['fn'], r['by']) for r in recs})[:4]
        touched.append({'edid': tq, 'ref': f[0] if f else None, 'name': None, 'stages': None, 'why': why})
    core = {q['edid']: q['name'] for q in quests_out if q['role'] in ('story',) and re.match(r'^NVDLC02MQ0\d$', q['edid'])}
    sweep = {
        'milestone': 'DLC02 Honest Hearts', 'core': core, 'system_quests': [q['edid'] for q in quests_out if q['role'] in ('slideshow', 'encounter-system')],
        'touched_quests': touched,
        'summary': {'quests': len(quests_out), 'stages': total_stages, 'objectives': sum(len(q['objectives']) for q in quests_out),
                    'excluded_quests': len(excluded_quests), 'exclusions': len(exclusions), 'run_last': run_last},
        'reset_ids': reset_ids,
        'reset_note': 'L7 goodsprings_sweep.py builds reset ids as "%X" % local_id, which drops the load-order prefix. For HonestHearts.esm use these 8-digit ids (index 02 << 24 | local id).',
        'console_notes': ['all FormIDs are runtime ids (load order index 02); loadorder.json order verified by the runner',
                          '`set <questFormID>.<var> to <n>` syntax with a hex id is unverified in this console build; vars are checked against the quest script declarations',
                          'setup / pre / variants run BEFORE capture.begin; only `command` is inside the capture window'],
        'order': [q['edid'] for q in quests_out if q['edid'] not in run_last] + run_last,
        'exclusions': exclusions, 'excluded_quests': excluded_quests, 'quests': quests_out}
    return sweep


# ---------------------------------------------------------------------------------------------------------------------
# gaps
# ---------------------------------------------------------------------------------------------------------------------
HAZARD_CLASSES = [
    ('player-targeted effects', 'rewards, perks, factions, AV, karma on the player', lambda c, e: 'PLAYER-TARGET' in e['hazard']),
    ('spawns', 'PlaceAtMe / MarkForDelete', lambda c, e: 'SPAWN' in e['hazard']),
    ('companions', 'SetPlayerTeammate, hire/fire triggers, companion perks', lambda c, e: 'COMPANION' in e['hazard'] or re.search(r'(?i)(hire|fire)trigger', c['text'])),
    ('essential / ghost flags', 'SetEssential, SetGhost, SetRestrained', lambda c, e: 'ESSENTIAL' in e['hazard'] and c['fn'].lower() != 'killactor'),
    ('MoveTo / teleports', 'MoveTo, MoveToMarkerWithFade, SetPos', lambda c, e: 'MOVE' in e['hazard']),
    ('cell transitions', 'door scripts, GetInCell, GetInWorldspace',
     lambda c, e: c['fn'].lower() in ('getincell', 'getinworldspace', 'isininterior') or (c['unit_kind'] == 'object' and 'door' in (c.get('script') or '').lower() and c['fn'].lower() == 'activate')),
    ('cinematics / control locks', 'Disable/EnablePlayerControls, SetInCharGen, forced greets',
     lambda c, e: 'CINEMATIC' in e['hazard'] or (c['fn'].lower() == 'addscriptpackage' and re.search(r'(?i)forcegreet|nosave', c['text']))),
    ('menus', 'ShowMessage, barter / caravan / repair, GetButtonPressed', lambda c, e: 'MENU' in e['hazard']),
    ('timers', 'GetSecondsPassed timers, GetCurrentTime windows, game-day skips, SetGlobal GameHour/GameDay',
     lambda c, e: c['fn'].lower() in ('getsecondspassed', 'getcurrenttime', 'setquestdelay') or 'TIMER' in e['hazard']),
    ('radio', 'Pip-Boy radio and NV radio towers', lambda c, e: c['fn'].lower() in ('pipboyradiooff', 'forceradiostationupdate') or re.search(r'(?i)radio', c['text'])),
    ('endings / slideshows', 'SwapTexture slides, narrator SayTo chain, SetInCharGen',
     lambda c, e: 'ENDING' in e['hazard'] or c['fn'].lower() == 'swaptexture' or re.search(r'(?i)slideshow|ending', c['text'])),
    ('"kill everyone" choices', 'KillActor / Kill, fail-every-quest stages, hostile faction flips',
     lambda c, e: c['fn'].lower() in ('killactor', 'kill') or (c['fn'].lower() == 'setenemy' and re.search(r'(?i)playerfaction', c['text']))),
    ('player reads (P-REF)', 'inventory, distance, action ref, perks of the player', lambda c, e: 'P-REF' in e['hazard']),
]


def sev_max(a, b):
    return a if SEV_ORDER.index(a) >= SEV_ORDER.index(b) else b


def build_gaps(d, ix, calls, n_units, mutation=None):
    funcs = collections.OrderedDict()
    mutating_ambient = 0
    for c in calls:
        e = fnmap.lookup(c['fn'])
        if e is None:
            fatal('unmapped function %s' % c['fn'])
        key = c['fn'].lower()
        f = funcs.setdefault(key, {'fn': c['fn'], 'kind': e['kind'], 'effect': e['effect'], 'capture': e['capture'], 'apply': e['apply'],
                                   'hazard': e['hazard'], 'note': e['note'], 'severity_map': e['severity'], 'locations': []})
        in_scope = c['unit_kind'] in ('stage', 'info') or c['fn'].lower() == 'setstage'
        ambient = (e['kind'] == 'mutate' and e['effect'] is not None and not in_scope)
        if ambient:
            mutating_ambient += 1
        loc = {'unit': c['unit'], 'unit_kind': c['unit_kind'], 'quest': c.get('quest'), 'script': c.get('script'),
               'line': c['line'], 'block': c['block'], 'text': c['text'], 'conditional': c['conditional'], 'in_capture_scope': in_scope,
               'ambient': ambient, 'subject': c.get('subject')}
        f['locations'].append(loc)
    out = []
    for key, f in funcs.items():
        cap, app = f['capture'], f['apply']
        if f['kind'] != 'mutate' and cap == 'n/a' and app in ('n/a',):
            status = 'not_needed'
        else:
            rank = max(STATUS_RANK[cap], STATUS_RANK[app])
            status = ['supported', 'partial', 'missing'][rank]
        ambient_n = sum(1 for l in f['locations'] if l['ambient'])
        sev = f['severity_map']
        reason = None
        if ambient_n and ambient_n == len(f['locations']) and f['kind'] == 'mutate' and f['effect']:
            sev = sev_max(sev, 'high')
            reason = 'every occurrence is outside a capture scope (ambient)'
            if status == 'supported':
                status = 'partial'
        elif ambient_n:
            reason = '%d of %d occurrences are outside a capture scope (ambient)' % (ambient_n, len(f['locations']))
        if f['kind'] == 'read' and cap == 'n/a' and app == 'partial':
            pass
        if mutation == 'unknown_effect' and f['fn'] == 'SetStage':
            f['effect'] = 'SetStageTypo'
        out.append({
            'fn': f['fn'], 'kind': f['kind'], 'effect': f['effect'], 'capture': cap, 'apply': app, 'status': status, 'severity': sev,
            'hazard': f['hazard'], 'note': f['note'], 'status_reason': reason, 'occurrences': len(f['locations']),
            'scripts': len({l['unit'] for l in f['locations']}), 'ambient_occurrences': ambient_n,
            'locations': sorted(f['locations'], key=lambda l: (l['unit'], l['line']))})
    out.sort(key=lambda f: (-SEV_ORDER.index(f['severity']), -STATUS_RANK.get(f['status'], 0), -f['occurrences'], f['fn'].lower()))
    hazards = {}
    for name, desc, pred in HAZARD_CLASSES:
        locs = []
        for c in calls:
            e = fnmap.lookup(c['fn'])
            try:
                hit = pred(c, e)
            except Exception:  # noqa: BLE001
                hit = False
            if hit:
                locs.append({'fn': c['fn'], 'unit': c['unit'], 'quest': c.get('quest'), 'line': c['line'], 'text': qlib.clip(c['text'], 120)})
        hazards[name] = {'description': desc, 'count': len(locs), 'scripts': len({l['unit'] for l in locs}),
                         'locations': locs[:60], 'truncated': max(0, len(locs) - 60)}
    by_status = collections.Counter()
    by_sev = collections.Counter()
    loc_status = collections.Counter()
    loc_sev = collections.Counter()
    for f in out:
        by_status[f['status']] += 1
        by_sev[f['severity']] += 1
        loc_status[f['status']] += f['occurrences']
        loc_sev[f['severity']] += f['occurrences']
    gaps = {'schema': 1, 'questline': qlib.SLUG, 'plugin': qlib.PLUGIN,
            'method': 'every command / condition function token in stage, topic-info (begin/end), quest and object scripts of HonestHearts.esm (scan.py); '
                      'mapped by fnmap.py to a synccore Effect type and the L5 capture/apply status read from plugin/hooks (source-level, not run in game)',
            'counts': {'units': n_units, 'calls': len(calls), 'functions': len(out), 'by_status': dict(by_status), 'by_severity': dict(by_sev),
                       'occurrences_by_status': dict(loc_status), 'occurrences_by_severity': dict(loc_sev), 'ambient_mutating_calls': mutating_ambient},
            'effect_types': sorted({f['effect'] for f in out if f['effect']}),
            'functions': out, 'hazards': hazards}
    return gaps


def gaps_md(gaps):
    c = gaps['counts']
    L = ['# Honest Hearts: capture / apply gaps', '',
         'Generated by `questlines/honesthearts/build.py` from `out/questdata` + `fnmap.py`. Statuses are read from the L5 sources (`plugin/hooks`), **not** observed in game.', '',
         '- %d script units scanned, %d calls, %d distinct functions' % (c['units'], c['calls'], c['functions']),
         '- functions by status: ' + ', '.join('%s %d' % kv for kv in sorted(c['by_status'].items())),
         '- functions by severity: ' + ', '.join('%s %d' % (s, c['by_severity'].get(s, 0)) for s in reversed(SEV_ORDER)),
         '- call occurrences by status: ' + ', '.join('%s %d' % kv for kv in sorted(c['occurrences_by_status'].items())),
         '- mutating calls with an Effect type that sit OUTSIDE a capture scope (quest GameMode blocks, object scripts): **%d**' % c['ambient_mutating_calls'],
         '', 'Capture scopes exist only inside `TESQuest::SetStage` and `TESTopicInfo::RunResultScript` (`plugin/hooks/capture_hub.h`); a mutation outside a scope is dropped (`droppedNoScope`). '
         'Stage and INFO scripts are therefore the only captured places today; the many GameMode / OnTrigger / OnActivate / OnDeath blocks of this DLC are "ambient".', '',
         '## Unsupported or partial functions (status != supported / not_needed)', '',
         '| severity | function | effect type | capture | apply | occurrences | scripts | ambient | hazards |', '|---|---|---|---|---|---:|---:|---:|---|']
    for f in gaps['functions']:
        if f['status'] in ('missing', 'partial'):
            L.append('| %s | %s | %s | %s | %s | %d | %d | %d | %s |' % (f['severity'], f['fn'], f['effect'] or '-', f['capture'], f['apply'], f['occurrences'],
                                                                        f['scripts'], f['ambient_occurrences'], ', '.join(f['hazard']) or '-'))
    L += ['', '## Exact locations (script + line)', '']
    for f in gaps['functions']:
        if f['status'] not in ('missing', 'partial') or f['severity'] in ('none',):
            continue
        L.append('### %s  (%s, %s)' % (f['fn'], f['severity'], f['status']))
        L.append('')
        L.append(f['note'].rstrip('.') + '.' + ((' Status reason: ' + f['status_reason'] + '.') if f['status_reason'] else ''))
        L.append('')
        locs = f['locations']
        for l in locs[:40]:
            tag = ' [ambient]' if l['ambient'] else ''
            L.append('- `%s` line %d%s: `%s`' % (l['unit'], l['line'], tag, qlib.clip(l['text'], 110).replace('`', "'")))
        if len(locs) > 40:
            L.append('- ... %d more in gaps.json' % (len(locs) - 40))
        L.append('')
    L += ['## Co-op hazard classes', '', '| class | calls | scripts | what |', '|---|---:|---:|---|']
    for name, h in gaps['hazards'].items():
        L.append('| %s | %d | %d | %s |' % (name, h['count'], h['scripts'], h['description']))
    L.append('')
    for name, h in gaps['hazards'].items():
        L.append('### %s (%d)' % (name, h['count']))
        L.append('')
        for l in h['locations'][:15]:
            L.append('- `%s` line %d: `%s`' % (l['unit'], l['line'], l['text'].replace('`', "'")))
        if h['count'] > 15:
            L.append('- ... %d more in gaps.json' % (h['count'] - 15))
        L.append('')
    return '\n'.join(L)


# ---------------------------------------------------------------------------------------------------------------------
# rules patch
# ---------------------------------------------------------------------------------------------------------------------
def default_params(kind, markers=None):
    return {
        'PartyTether': {'cells': [], 'gather': 'wait'},
        'PartyWarp': {'markers': markers or [], 'mode': 'slot-offset', 'absent': 'hold'},
        'SetPlayerControls': {'scope': 'all', 'release': 'host-effect', 'timeout_s': 120, 'masks': []},
        'SaveSuppress': {}, 'SlideshowEnd': {'run': 'host-once', 'lock': 'all', 'release': 'together'},
        'CompanionOwner': {'owner': 'recruiter'}, 'RewardAll': {}, 'InitiatorDialogue': {}, 'QuestItemDup': {'recipient': 'all'},
        'PartyAwareCond': {'funcs': {}}, 'FastTravelLock': {'scope': 'party'}, 'ShareKarmaRep': {}, 'PerPlayerScene': {},
        'Barrier': {'step': 'all-players-pass'}, 'RemoveItemPerOwner': {}, 'ExcludeFromGate': {}}[kind]


def player_markers(calls, quest_edid, d):
    """EDIDs of the markers a quest's own scripts (stage/quest) move the Player to."""
    res = []
    for c in calls:
        if c['fn'].lower() == 'moveto' and (c.get('subject') or '').lower() == 'player' and c.get('quest') == quest_edid:
            m = re.search(r'(?i)player\.moveto\s+(\w+)', c['text'])
            if m and m.group(1) not in res:
                res.append(m.group(1))
    return res


def build_rules_patch(d, ix, calls, mutation=None):
    base = d.rules['quests']
    patches = []
    qline = 'DLC: Honest Hearts'
    for fx in curated.PARAM_FIXES:
        q, kind = fx['quest'], fx['kind']
        entry = base.get(q)
        if entry is None or not any(r['kind'] == kind for r in entry['rules']):
            fatal('param fix targets missing rule %s/%s' % (q, kind))
        patches.append({'op': 'replace_params', 'quest': q, 'kind': kind, 'params': copy.deepcopy(fx['params']), 'why': fx['why'],
                        'evidence': resolve_all(ix, fx['evidence'])})
    # rules per quest: curated NEW_QUEST_RULES + NEEDS
    all_q = sorted(set(curated.NEEDS) | set(curated.NEW_QUEST_RULES))
    for q in all_q:
        if q not in d.by_edid:
            fatal('curated rules for unknown quest %s' % q)
        have = {r['kind'] for r in (base.get(q) or {}).get('rules', [])}
        wanted = collections.OrderedDict()
        for kind, params, why in curated.NEW_QUEST_RULES.get(q, []):
            wanted[kind] = (params, why)
        for kind, why in curated.NEEDS.get(q, {}).items():
            if kind not in KINDS:
                fatal('NEEDS uses unknown kind %s' % kind)
            if kind not in wanted:
                params = default_params(kind, player_markers(calls, q, d))
                wanted[kind] = (params, why)
        rules = []
        for kind, (params, why) in wanted.items():
            if kind in have:
                continue
            rules.append({'kind': kind, 'params': copy.deepcopy(params), 'origin': 'curated', 'why': why})
        if not rules:
            continue
        if q in base:
            for r in rules:
                patches.append({'op': 'add_rule', 'quest': q, 'kind': r['kind'], 'params': r['params'], 'origin': 'curated', 'why': r['why']})
        else:
            rk = d.risk.get(d.by_edid[q]['ref']) or {}
            patches.append({'op': 'add_quest', 'quest': q, 'tier': rk.get('tier') or 'T3', 'questline': qline, 'curated': True,
                            'rules': [{'kind': r['kind'], 'params': r['params'], 'origin': r['origin']} for r in rules],
                            'why': 'quest has no entry in rules.json: ' + '; '.join('%s (%s)' % (r['kind'], r['why']) for r in rules)})
    proposed = []
    for p in curated.PROPOSED:
        proposed.append({'id': p['id'], 'applies_to': p['applies_to'], 'params': p.get('params'), 'why': p['why'], 'evidence': resolve_all(ix, p['evidence'])})
    if mutation == 'bad_patch_kind' and patches:
        patches[0] = dict(patches[0], kind='NotAKind')
    if mutation == 'bad_patch_quest' and patches:
        patches[1] = dict(patches[1], quest='NVDLC02Nope')
    if mutation == 'tether_old_cell':
        for p in patches:
            if p['quest'] == 'NVDLC02MQ00' and p['kind'] == 'PartyTether':
                p['params'] = {'cells': ['GSDocMitchellHouse'], 'gather': 'auto-warp'}
    return {'schema': 1, 'questline': qlib.SLUG, 'base': 'docs/quest-policies/rules.json',
            'format': 'patches[]: op in replace_params | add_rule | add_quest. replace_params/add_rule target an existing quest (and for replace_params an existing rule kind); '
                      'add_quest creates a quest entry. Only the 16 existing kinds are used; new kinds / params / reductions are in proposed[].',
            'kinds_used': sorted({p['kind'] for p in patches if 'kind' in p} | {r['kind'] for p in patches for r in p.get('rules', [])}),
            'patches': patches, 'proposed': proposed}


def apply_patch(base_rules, patch):
    """Returns a patched deep copy of rules.json['quests']; raises on any inconsistency (used by the tests)."""
    q = copy.deepcopy(base_rules['quests'])
    for p in patch['patches']:
        op = p['op']
        if op == 'add_quest':
            if p['quest'] in q:
                fatal('add_quest: %s already has rules' % p['quest'])
            for r in p['rules']:
                if r['kind'] not in KINDS:
                    fatal('unknown kind %s' % r['kind'])
            q[p['quest']] = {'tier': p['tier'], 'questline': p['questline'], 'curated': p['curated'], 'rules': copy.deepcopy(p['rules'])}
            continue
        if p['quest'] not in q:
            fatal('%s: quest %s not in rules.json' % (op, p['quest']))
        if p['kind'] not in KINDS:
            fatal('unknown kind %s' % p['kind'])
        rules = q[p['quest']]['rules']
        hit = [r for r in rules if r['kind'] == p['kind']]
        if op == 'replace_params':
            if not hit:
                fatal('replace_params: %s has no %s rule' % (p['quest'], p['kind']))
            hit[0]['params'] = copy.deepcopy(p['params'])
        elif op == 'add_rule':
            if hit:
                fatal('add_rule: %s already has %s' % (p['quest'], p['kind']))
            rules.append({'kind': p['kind'], 'params': copy.deepcopy(p['params']), 'origin': p.get('origin', 'curated')})
        else:
            fatal('unknown op %s' % op)
    return q


# ---------------------------------------------------------------------------------------------------------------------
# dialogue
# ---------------------------------------------------------------------------------------------------------------------
def info_summary(d, ix, local):
    loc = ix.infos.get(local.upper())
    if not loc:
        fatal('INFO %s not found' % local)
    qref, i = loc
    text = ' | '.join(r['text'] for r in i['responses'])
    scr = {}
    for k in ('begin', 'end'):
        sc = i.get(k)
        if sc and sc.get('source'):
            scr[k] = [ln.strip() for ln in sc['source'].replace('\r', '').split('\n') if ln.strip() and not ln.strip().startswith(';')]
    conds = [c for c in i['conditions'] if c['func'] not in ('GetIsID', 'GetIsVoiceType')]
    return qref, i, text, scr, conds


def cond_setup(i):
    """player.forceav steps that make the initiator pass / the others fail a single skill gate."""
    steps = {}
    for c in i['conditions']:
        if c['func'] == 'GetActorValue' and c['p1'] in AV_NAMES and c['p1'] not in (46, 23):
            thr = int(c['value'])
            ok = c['op'] in ('>=', '>', '==')
            val = thr if ok else max(0, thr - 1)
            av = AV_NAMES[c['p1']].lower()
            steps.setdefault('p1', []).append('player.forceav %s %d' % (av, val))
            steps.setdefault('host', []).append('player.forceav %s %d' % (av, 0 if ok else thr))
            steps.setdefault('p2', []).append('player.forceav %s %d' % (av, 0 if ok else thr))
    return steps


def build_dialogue(d, ix, mutation=None):
    specs, rows = [], []
    npc_ref_of_base = {}
    for ref, f in d.forms.items():
        if f['sig'] == 'ACHR' and f.get('base'):
            npc_ref_of_base.setdefault(f['base'], (ref, f.get('edid')))
    for cv in curated.CONVERSATIONS:
        q = d.by_edid.get(cv['quest'])
        if q is None:
            fatal('conversation quest %s' % cv['quest'])
        steps, infos_out = [], []
        base = cv.get('npc_base')
        npc = npc_ref_of_base.get(base) if base else None
        if cv.get('greeting'):
            g = ix.infos.get(cv['greeting'].upper())
            if not g:
                fatal('greeting INFO %s' % cv['greeting'])
        setup_players = collections.OrderedDict()
        for local, starts, what in cv['infos']:
            qref, i, text, scr, conds = info_summary(d, ix, local)
            if mutation == 'bad_info':
                starts = 'zzzz not the text'
            if _norm(starts) not in _norm(text):
                fatal('conversation %s INFO %s: line does not start with %r (data: %r)' % (cv['id'], local, starts, text[:80]))
            owner = d.edid_of(qref)
            if owner not in d.by_edid:
                fatal('conversation %s INFO %s belongs to %s, which is not an Honest Hearts quest' % (cv['id'], local, owner))
            infos_out.append({'ref': i['ref'], 'quest': owner, 'topic': d.edid_of(i['topic']), 'speaker': i.get('speaker'),
                              'flags': i.get('flags', []), 'text': qlib.clip(text, 200), 'what': what,
                              'conditions': [fmt_cond(d, c) for c in conds][:6], 'scripts': {k: v[:8] for k, v in scr.items()}})
            for p, cmds in cond_setup(i).items():
                setup_players.setdefault(p, [])
                setup_players[p] += [c for c in cmds if c not in setup_players[p]]
        if setup_players:
            steps.append({'id': 'prep', 'action': 'setup', 'players': dict(setup_players)})
        talk_step = None
        if npc:
            talk_step = {'action': 'talk', 'actor': 'p1', 'npc': npc[1], 'npc_ref': npc[0],
                         'greeting_info': ('HonestHearts.esm:' + cv['greeting']) if cv.get('greeting') else None,
                         'greeting_unknown': not cv.get('greeting'),
                         'line': (ix.infos[cv['greeting'].upper()][1]['responses'][0]['text'][:12] if cv.get('greeting') else None),
                         'expect': {'opened': True, 'spectators': ['host', 'p2']}}
        need_talk = talk_step is not None
        n_goodbye = 0
        for io in infos_out:
            if need_talk and talk_step is not None:
                steps.append(dict(talk_step, id='talk_p1_%d' % (len([x for x in steps if x['action'] == 'talk']) + 1),
                                  alternative=bool(n_goodbye)))
                need_talk = False
            expect = {'spectator_lines': [io['text'][:40]], 'converged': True}
            allscr = ' ; '.join(' ; '.join(v) for v in io['scripts'].values())
            m = re.search(r'(?i)setstage\s+(\w+)\s+(\d+)', allscr)
            if m:
                expect['stage'] = {'quest': m.group(1), 'value': int(m.group(2))}
            m = re.search(r'(?i)rewardxp\s+(\d+)', allscr)
            if m:
                expect['xp_each_player'] = int(m.group(1))
            m = re.search(r'(?i)rewardkarma\s+(-?\d+)', allscr)
            if m:
                expect['reputation_shared'] = True
            m = re.search(r'(?i)set\s+(\w+)\.(\w+)\s+to\s+(-?\d+)', allscr)
            if m:
                owner_q = d.by_edid.get(m.group(1))
                if owner_q is not None and m.group(2) in declared_vars((owner_q.get('script_data') or {}).get('source')):
                    expect['script_var'] = {'%s.%s' % (m.group(1), m.group(2)): int(m.group(3))}
            if 'goodbye' in io['flags']:
                expect['closed_after'] = 'Goodbye'
                need_talk = True
                n_goodbye += 1
            steps.append({'id': 'pick_info_' + io['ref'].split(':')[1].lower(), 'action': 'pick_info', 'actor': 'p1', 'info': io['ref'],
                          'line': io['text'][:40], 'note': io['what'], 'expect': expect})
        run_mode = 'alternatives-from-same-save' if n_goodbye > 1 else 'sequence'
        quests = [cv['quest']] + [x for x in sorted({re.sub(r'.*?(NVDLC02\w+?)\b.*', r'\1', s) for io in infos_out for s in
                                                      re.findall(r'(?i)(?:setstage|startquest)\s+(NVDLC02\w+)', ' '.join(' '.join(v) for v in io['scripts'].values()))})
                                  if x in d.by_edid and x != cv['quest']]
        rk = d.risk.get(q['ref']) or {}
        spec = {'schema': 1, 'kind': 'dialogue', 'id': cv['id'], 'name': cv['name'], 'plugin': qlib.PLUGIN, 'summary': cv['summary'],
                'tier': rk.get('tier') or 'T3', 'players': {'host': 0, 'p1': 1, 'p2': 2}, 'quests': quests,
                'save': None, 'save_note': 'unknown: no Honest Hearts start save exists yet; L7/L2 must provide one (the Zion arrival or the Narrows)',
                'policy_checks': ['dialogue-ownership', 'player-reference-mapping', 'reward-both-vs-owner'],
                'setup': {}, 'run_mode': run_mode,
                'run_mode_note': ('each conversation after the first that ends with a goodbye INFO is an ALTERNATIVE run from the same pre-state (the story choices are mutually exclusive), not a continuation'
                                  if run_mode != 'sequence' else 'steps run in order in one conversation'),
                'steps': steps,
                'invariants': ['no_duplicate_reward', 'one_conversation_per_npc', 'result_script_ran_once_on_host', 'host.shared == client.shared'],
                'oracle': 'docs/dialogue-design.md', 'breaks_initiator_model': cv['breaks']}
        specs.append(spec)
        rows.append({'conv': cv, 'infos': infos_out, 'spec_id': cv['id']})
    return specs, rows


def dialogue_md(rows, d):
    L = ['# Honest Hearts: key conversations (L8 spec style)', '',
         'Generated by `questlines/honesthearts/build.py`; the machine-readable specs are in `dialogue_specs.json` (same fields as `plugin/dialogue/scenarios/*.json`). '
         'Every INFO, speaker and line start below is checked against `infos.jsonl.gz` by `run_tests.py`.', '',
         'Owner rule: the player who starts a conversation makes the choices; everyone sees the line and the result; karma is shared; rewards go to every player. '
         'No Honest Hearts start save exists yet, so every spec has `save: null` ("unknown"); an L7/L2 save in the Narrows or at the Zion arrival is needed before these can run.', '',
         '## Conversations the initiator-picks model breaks', '']
    for r in rows:
        cv = r['conv']
        L.append('- **%s** (`%s`): %s' % (cv['name'], cv['id'], cv['breaks']))
    L.append('')
    for r in rows:
        cv = r['conv']
        L.append('## %s' % cv['name'])
        L.append('')
        L.append('`%s` in quest `%s`. %s' % (cv['id'], cv['quest'], cv['summary']))
        L.append('')
        L.append('| INFO | topic | what it does | conditions | script |')
        L.append('|---|---|---|---|---|')
        for io in r['infos']:
            scr = '; '.join('%s: %s' % (k, ' / '.join(v[:3])) for k, v in io['scripts'].items())
            L.append('| %s | %s | %s | %s | %s |' % (io['ref'].split(':')[1], io['topic'] or '-', io['what'].replace('|', '/'),
                                                      '; '.join(io['conditions'][:3]).replace('|', '/') or '-', qlib.clip(scr.replace('|', '/'), 200) or '-'))
        L.append('')
        L.append('Initiator-picks verdict: %s' % cv['breaks'])
        L.append('')
    return '\n'.join(L)


# ---------------------------------------------------------------------------------------------------------------------
# policy review
# ---------------------------------------------------------------------------------------------------------------------
def policy_md(d, ix, inv, rules_patch, calls, gaps, metrics):
    base = d.rules['quests']
    patched = apply_patch(d.rules, rules_patch)
    L = ['# Honest Hearts: policy review', '',
         'Checks every quest against the locked owner decisions (`docs/MASTER-PLAN.md`) and the machine rules in `docs/quest-policies/rules.json` (owned by L9). '
         'Proposed changes are in `rules.patch.json` (existing 16 kinds only); things the 16 kinds cannot express are listed as proposals in section 5.', '']
    n_rule = sum(1 for q in inv['quests'] if q['edid'] in base)
    L += ['## 1. Coverage of rules.json', '',
          '%d of %d Honest Hearts quests have an entry in rules.json today; after the patch %d have one. %d patch operations (%d replace_params, %d add_rule, %d add_quest).'
          % (n_rule, len(inv['quests']), sum(1 for q in inv['quests'] if q['edid'] in patched), len(rules_patch['patches']),
             sum(1 for p in rules_patch['patches'] if p['op'] == 'replace_params'), sum(1 for p in rules_patch['patches'] if p['op'] == 'add_rule'),
             sum(1 for p in rules_patch['patches'] if p['op'] == 'add_quest')), '',
          '| quest | role | tier | stages | rules today | rules after patch |', '|---|---|---|---:|---|---|']
    for q in inv['quests']:
        e = q['edid']
        now = [r['kind'] for r in (base.get(e) or {}).get('rules', [])]
        aft = [r['kind'] for r in (patched.get(e) or {}).get('rules', [])]
        if q['role'] in ('dialogue',) and not now and not aft and not q['dialogue']['scripted_infos']:
            continue
        L.append('| %s | %s | %s | %d | %s | %s |' % (e, q['role'], q['tier'], len(q['stages']), ', '.join(now) or '-', ', '.join(aft) or '-'))
    L += ['', '## 2. Owner decisions, checked against this questline', '', '| decision | verdict | finding | evidence (script line) |', '|---|---|---|---|']
    for oc in curated.OWNER_CHECKS:
        ev = resolve_all(ix, oc['evidence'])
        L.append('| %s | %s | %s | %s |' % (oc['decision'], oc['verdict'], oc['finding'].replace('|', '/'),
                                             '; '.join('%s:%d' % (e['where'], e['line']) for e in ev)))
    L += ['', '## 3. Story moments: barriers, tethers, cinematic control, reward routing', '']
    for m in curated.MOMENTS:
        for qn in m['quests']:
            if qn not in d.by_edid:
                fatal('moment %s: unknown quest %s' % (m['id'], qn))
        L += ['### %s: %s' % (m['id'], m['moment']), '', 'Quests: %s' % ', '.join('`%s`' % q for q in m['quests']), '',
              '- barrier / consent: %s' % m['barrier'], '- tether: %s' % m['tether'], '- who controls the cinematic: %s' % m['cinematic_control'], '- reward routing: %s' % m['reward'], '']
    L += ['## 4. Existing rules that are wrong or incomplete', '']
    for p in rules_patch['patches']:
        if p['op'] == 'replace_params':
            L.append('- `%s` / `%s`: %s (evidence: %s)' % (p['quest'], p['kind'], p['why'], '; '.join('%s line %d' % (e['where'], e['line']) for e in p['evidence'])))
    L += ['', '## 5. Proposals the 16 kinds cannot express', '']
    for p in rules_patch['proposed']:
        L.append('### %s (extends `%s`)' % (p['id'], p['applies_to']))
        L.append('')
        L.append(p['why'])
        if p.get('params'):
            L.append('')
            L.append('Params: `%s`' % json.dumps(p['params']))
        L.append('')
        L.append('Evidence: ' + '; '.join('%s line %d' % (e['where'], e['line']) for e in p['evidence']))
        L.append('')
    L += ['## 6. Open questions for the owner (not decided here)', '',
          '1. **Companions across players.** Follows-Chalk, Waking Cloud and Joshua use single shared hired/fired flags. Can two players each hold a different companion, or is the DLC limited to one companion per party (as vanilla)? The rules default `CompanionOwner = recruiter` is assumed.',
          '2. **Jed paying once.** The 175 caps and the caravan XP: once per party or once per player? The owner rule "every player gets the full reward" says per player; the narrative is one caravan.',
          '3. **Fork decision.** Is the Joshua / Daniel choice a party vote, host decides, or initiator decides (current)? Proposed ChoiceConsent defaults to a party vote with the host breaking ties.',
          '4. **Friendly fire.** Should an attack on Joshua or Daniel by a party member end the DLC for all (vanilla MQ06)? Proposed FriendlyFireGuard asks the host to confirm.',
          '5. **Unknowns**: no Honest Hearts start save exists, the in-game console syntax for `set <hexQuestId>.var to n` is unverified, and every L5 status here is source-level (nothing was run in the game).', '']
    return '\n'.join(L)


# ---------------------------------------------------------------------------------------------------------------------
# top-level
# ---------------------------------------------------------------------------------------------------------------------
def metrics_of(d, calls, gaps):
    def cnt(*fns):
        s = {f.lower() for f in fns}
        return sum(1 for c in calls if c['fn'].lower() in s)
    mq06_fail = len(re.findall(r'(?i)setstage\s+NVDLC02\w+\s+255', ' '.join(
        s for _, _, s in qlib.stage_scripts(d.by_edid['NVDLC02MQ06']))))
    presence = {'enable', 'disable', 'killactor', 'resurrectactor'}
    amb_pres = sum(f['ambient_occurrences'] for f in gaps['functions'] if f['kind'] == 'mutate' and f['effect'] and f['fn'].lower() in presence)
    return {'ambient_mutators': gaps['counts']['ambient_mutating_calls'], 'ambient_presence': amb_pres,
            'ambient_story': gaps['counts']['ambient_mutating_calls'] - amb_pres, 'faction_calls': cnt('AddToFaction', 'RemoveFromFaction', 'SetFactionRank'),
            'spawn_calls': cnt('PlaceAtMe'), 'autosave_calls': cnt('Autosave'), 'formlist_calls': cnt('AddFormToFormList', 'AddItemToLeveledList'),
            'mq06_fail_count': mq06_fail}


def build_blockers(d, ix, metrics):
    out = []
    for i, b in enumerate(curated.BLOCKERS, 1):
        out.append({'rank': i, 'id': b['id'], 'severity': b['severity'], 'lane': b['lane'], 'title': b['title'], 'text': b['text'].format(**metrics),
                    'fix': b['fix'], 'evidence': resolve_all(ix, b['evidence'])})
    return out


def build_also(d, ix, metrics):
    return [{'id': a['id'], 'severity': a['severity'], 'title': a['title'], 'text': a['text'].format(**metrics), 'evidence': resolve_all(ix, a['evidence'])}
            for a in curated.ALSO]


_STATE = {}


def state():
    """Data + indexes + scan, loaded once per process (read-only; the mutation twins reuse it)."""
    if not _STATE:
        d = qlib.Data()
        calls, n_units = scan.scan_all(d)
        trig, ext = collect_triggers(d, calls)
        _STATE.update(d=d, ix=Index(d), calls=calls, n_units=n_units, trig=trig, ext=ext)
    return _STATE


def generate(mutation=None):
    if mutation is not None and mutation not in MUTATIONS:
        fatal('unknown mutation %s' % mutation)
    st = state()
    d, ix, calls, n_units, trig, ext = st['d'], st['ix'], st['calls'], st['n_units'], st['trig'], st['ext']
    inv = build_inventory(d, ix, calls, trig, mutation)
    sweep = build_sweep(d, ix, calls, inv, trig, ext, mutation)
    gaps = build_gaps(d, ix, calls, n_units, mutation)
    metrics = metrics_of(d, calls, gaps)
    rules_patch = build_rules_patch(d, ix, calls, mutation)
    specs, rows = build_dialogue(d, ix, mutation)
    blockers = build_blockers(d, ix, metrics)
    also = build_also(d, ix, metrics)
    if mutation == 'bad_evidence':
        resolve_evidence(ix, {'script': 'NVDLC02ZionDoorSCRIPT', 'find': 'this text is not in the script'})
    docs = {'gaps.md': gaps_md(gaps), 'dialogue.md': dialogue_md(rows, d),
            'policy_review.md': policy_md(d, ix, inv, rules_patch, calls, gaps, metrics)}
    return {'d': d, 'ix': ix, 'calls': calls, 'inventory': inv, 'sweep': sweep, 'gaps': gaps, 'rules_patch': rules_patch,
            'dialogue_specs': {'schema': 1, 'kind': 'dialogue-set', 'plugin': qlib.PLUGIN, 'specs': specs}, 'blockers': blockers, 'also': also,
            'metrics': metrics, 'docs': docs}


def write_all(art):
    def w(name, obj):
        with open(os.path.join(HERE, name), 'w', encoding='utf-8', newline='\n') as f:
            if isinstance(obj, str):
                f.write(obj if obj.endswith('\n') else obj + '\n')
            else:
                json.dump(obj, f, indent=1, ensure_ascii=False)
                f.write('\n')
    w('inventory.json', art['inventory'])
    w('sweep.json', art['sweep'])
    w('gaps.json', art['gaps'])
    w('rules.patch.json', art['rules_patch'])
    w('dialogue_specs.json', art['dialogue_specs'])
    w('blockers.json', {'schema': 1, 'metrics': art['metrics'], 'blockers': art['blockers'], 'also': art['also']})
    for name, text in art['docs'].items():
        w(name, text)


if __name__ == '__main__':
    a = generate()
    write_all(a)
    print('wrote:', ', '.join(sorted(os.listdir(HERE))))
    print('quests %d stages %d functions %d calls %d' % (a['inventory']['counts']['quests'], a['inventory']['counts']['stages'],
                                                         a['gaps']['counts']['functions'], a['gaps']['counts']['calls']))
