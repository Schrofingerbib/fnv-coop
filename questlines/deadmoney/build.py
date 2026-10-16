"""Generate the Dead Money questline artefacts from out/questdata (read-only).

    python -I questlines/deadmoney/build.py [--out DIR]       (default: this directory)

Writes inventory.json, sweep.json, gaps.json, gaps.md.  Pure function of the quest data + fnmap.py + hazards.py + prereq.py;
run_tests.py regenerates into a temp dir and validates, plus runs mutation twins on the generator's checks.
"""
import argparse
import collections
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import dmlib  # noqa: E402
import fnmap  # noqa: E402
import hazards  # noqa: E402
import prereq  # noqa: E402
from dmlib import classify  # noqa: E402

PLAYER = {'player', 'playerref'}
SET_RE = re.compile(r'^\s*set\s+([A-Za-z_]\w*)(?:\s*\.\s*([A-Za-z_]\w*))?\s+to\b', re.I)
SETSTAGE_RE = re.compile(r'\bsetstage\s+(\w+)\s+(\d+)', re.I)
STARTQUEST_RE = re.compile(r'\bstartquest\s+(\w+)', re.I)
BEGIN_PLAYER_RE = re.compile(r'^\s*begin\s+(\w+)\s+(player|playerref)\b', re.I)

ROLE_RULES = [
    ('debug', r'^NVDLC01EnemyTest$'),
    ('main-story', r'^NVDLC01MQ0\d[a-d]?$'),
    ('sequence', r'^NVDLC01(FountainStartSequence|Intro|Ending|FadeToCreditsTimer|BunkerTransitionTimer|VillaTransitionTimer|ArcadeGoodbye)$'),
    ('collar-system', r'^NVDLC01(BombCollarQuest|ToxicCloud|GlobalToxicDamage|RadioSpeakers|ElijahCollarBark)$'),
    ('companion', r'^NVDLC01(Followers|DogDialogue|DeanDialogue|ChristineDialogue|NewVegasFollowerFireQuest|DogBarkTimer|DeanBarkTimer|ChristineBarkTimer|ChristineTerminalFX)$'),
    ('elijah', r'^NVDLC01(ElijahDialogue|ElijahBark\w+)$'),
    ('radio', r'^NVDLC01Radio\w+$'),
    ('hologram-vendor', r'^NVDLC01(HoloSupport|HoloVendor|StarletDialogue|WTSupport|CasinoComps)$'),
    ('gala', r'^NVDLC01GalaEventQuest$'),
    ('reward-meta', r'^NVDLC01(ChipReward|Challenges|ClinicAutoDocQuest)$'),
]


def role_of(edid):
    for r, rx in ROLE_RULES:
        if re.match(rx, edid):
            return r
    return 'other'


# ---- script analysis ---------------------------------------------------------------------------------------------------
def analyse(d):
    """-> list of per-script dicts with function uses, variable writes and player-bound lines."""
    names = dmlib.command_names()
    kq = {q['edid'].lower() for q in d.quests_all['quests'] if q.get('edid')}
    kg = {g['edid'].lower() for g in d.globals if g.get('edid')}
    quest_edids = {q['edid'].lower() for q in d.quests_all['quests'] if q.get('edid')}
    out = []
    for s in dmlib.universe(d):
        find = classify.classify_source(s.source, kq, kg)
        pref_lines = {f['line'] for f in find if f['player'] or f['cat'] == 'P-REF'}
        uses = []
        lines = s.source.splitlines()
        for u in dmlib.function_uses(s.source, names):
            ln = lines[u['line'] - 1]
            toks = [t.lower() for t in classify.TOKEN.findall(classify.strip_comment(ln))]
            fl = u['fn']
            if fl in fnmap.SUBJECT_ONLY:
                u['player_bound'] = u['subject'] in PLAYER
            elif fl in fnmap.IMPLICIT_PLAYER:
                u['player_bound'] = True
            else:
                u['player_bound'] = (u['subject'] in PLAYER) or any(t in PLAYER for t in toks) or u['line'] in pref_lines
            uses.append(u)
        sets = []
        block = None
        depth = 0
        for n, raw in enumerate(lines, 1):
            line = classify.strip_comment(raw).strip()
            low = line.lower()
            if low.startswith('begin '):
                block = line.split()[1]
                depth = 0
            elif low == 'end':
                block = None
            if re.match(r'(if|while)\b', low):
                depth += 1
            elif re.match(r'(endif|endwhile)\b', low):
                depth = max(0, depth - 1)
            m = SET_RE.match(line)
            if not m:
                continue
            a, b = m.group(1), m.group(2)
            if b:
                kind = 'SetQuestVar' if a.lower() in quest_edids else 'SetRefVar'
                tgt = '%s.%s' % (a, b)
            elif a.lower() in kg:
                kind, tgt = 'SetGlobal', a
            else:
                kind, tgt = 'SetLocalVar', a
            sets.append({'line': n, 'kind': kind, 'target': tgt, 'block': block, 'text': line[:160], 'conditional': depth > 0,
                         'player_bound': n in pref_lines})
        pblocks = []
        for n, raw in enumerate(lines, 1):
            m = BEGIN_PLAYER_RE.match(classify.strip_comment(raw))
            if m:
                pblocks.append({'line': n, 'block': m.group(1), 'text': classify.strip_comment(raw).strip()[:100]})
        out.append({'script': s, 'uses': uses, 'sets': sets, 'player_blocks': pblocks,
                    'unknown': dmlib.unknown_calls(s.source, names)})
    return out


def row_for_set(kind, script_kind, conditional):
    if kind == 'SetGlobal':
        return fnmap.R('SetGlobal', 'mutation', fnmap.SUP, fnmap.SUP, 'info', 'quest', 'TESGlobal diff around the outermost scope')
    if kind == 'SetQuestVar':
        if script_kind in ('stage', 'info_begin', 'info_end'):
            return fnmap.R('SetScriptVar', 'mutation', fnmap.SUP, fnmap.SUP, 'info', 'quest',
                           'quest ScriptEventList diff around the result-script scope')
        return fnmap.R('SetScriptVar', 'mutation', fnmap.PAR, fnmap.SUP, 'medium', 'quest',
                       'written outside a capture scope (quest/object script): only state polling sees it; guests run object scripts and may write the same variable locally')
    if kind == 'SetRefVar':
        if script_kind in ('stage', 'info_begin', 'info_end'):
            return fnmap.R('SetScriptVar', 'mutation', fnmap.PAR, fnmap.SUP, 'medium', 'quest',
                           'ref variable written by a result script: L5 has no hook, but the owner ref is statically known from the script SCRO table '
                           '(L8 per-INFO pre-computation / stage SCRO) so a before/after diff of that ref is possible; apply exists')
        return fnmap.R('SetScriptVar', 'mutation', fnmap.MIS, fnmap.SUP, 'medium', 'quest',
                       'ref/object script variable written by an object or quest script: RefScriptVar exists in effect.h but no hook records it (L5 round 5); the SetScriptVar apply handles ref owners')
    # SetLocalVar
    if script_kind == 'quest':
        return fnmap.R('SetScriptVar', 'mutation', fnmap.PAR, fnmap.SUP, 'low', 'quest',
                       "the quest's own variable: replicated by the quest-var diff / state polling")
    return fnmap.R('-', 'flow', fnmap.NA, fnmap.NA, 'info', '', "object-script local variable, private to the running script")


def build_gaps(d, scripts):
    funcs = collections.OrderedDict()
    sites = []

    def add_site(label, kind, sc, line, text, block, conditional, canon, rowd, player_bound, cat):
        sev = fnmap.site_severity(rowd['severity'], rowd['status'], player_bound, rowd['kind'])
        sites.append({'script': label, 'script_kind': kind, 'line': line, 'block': block, 'text': text, 'fn': canon,
                      'conditional': conditional, 'player_bound': player_bound, 'status': rowd['status'], 'severity': sev,
                      'effect': rowd['effect'], 'tags': rowd['tags'], 'quest': sc.quest, 'topic': sc.topic})

    unclassified = collections.Counter()
    unknown_calls = []
    for a in scripts:
        sc = a['script']
        for u in a['uses']:
            r = fnmap.site_row(u['canon'], u['player_bound'])
            if r is None:
                unclassified[u['canon']] += 1
                continue
            add_site(sc.label, sc.kind, sc, u['line'], u['text'], u['block'], u['conditional'], u['canon'], r, u['player_bound'], None)
        for st in a['sets']:
            r = row_for_set(st['kind'], sc.kind, st['conditional'])
            add_site(sc.label, sc.kind, sc, st['line'], st['text'], st['block'], st['conditional'], st['kind'], r,
                     st['player_bound'], None)
        for pb in a['player_blocks']:
            r = fnmap.R('-', 'condition', fnmap.NA, fnmap.NA, 'info', 'player-effect',
                        "event block filtered on the player reference: fires for the host's player only, never for a guest avatar")
            r['status'] = fnmap.MIS
            r['severity'] = 'high'
            r['note'] = r['note'] + ' (needs PartyAwareCond / event routing)'
            add_site(sc.label, sc.kind, sc, pb['line'], pb['text'], pb['block'], False, 'OnPlayerEvent', r, True, None)
        for ln, fn, text in a['unknown']:
            unknown_calls.append({'script': sc.label, 'line': ln, 'fn': fn, 'text': text})

    # per-function roll-up
    for s in sites:
        f = funcs.setdefault(s['fn'], {'fn': s['fn'], 'count': 0, 'scripts': set(), 'by_severity': collections.Counter(),
                                       'by_kind': collections.Counter(), 'locations': []})
        f['count'] += 1
        f['scripts'].add(s['script'])
        f['by_severity'][s['severity']] += 1
        f['by_kind'][s['script_kind']] += 1
        f['locations'].append(s)
    rows = []
    for name, f in funcs.items():
        if name in ('SetQuestVar', 'SetRefVar', 'SetLocalVar', 'SetGlobal'):
            # status of a variable write depends on the script kind; the row shows the worst sample, the sites carry their own
            sample = row_for_set(name, 'object' if name != 'SetLocalVar' else 'quest', False)
            if name == 'SetQuestVar':
                sample = row_for_set(name, 'object', False)
        else:
            sample = fnmap.row(name) or fnmap.R('-', 'condition', fnmap.NA, fnmap.NA, 'info')
            if name == 'OnPlayerEvent':
                sample = dict(sample, status=fnmap.MIS, severity='high', note='event block filtered on the player reference',
                              effect='-', kind='condition', tags=['player-effect'])
        worst_status = max((l['status'] for l in f['locations']), key=lambda s: fnmap.RANK[s])
        worst_sev = max((l['severity'] for l in f['locations']), key=lambda s: fnmap.SEV_ORDER.index(s))
        keep_locs = worst_status in (fnmap.PAR, fnmap.MIS) or any(l['player_bound'] for l in f['locations'])
        rows.append({
            'fn': name, 'effect': sample['effect'], 'kind': sample['kind'], 'capture': sample['capture'],
            'apply': sample['apply'], 'status': worst_status, 'severity': worst_sev, 'tags': sample['tags'],
            'note': sample['note'], 'count': f['count'], 'script_count': len(f['scripts']),
            'by_severity': dict(f['by_severity']), 'by_script_kind': dict(f['by_kind']),
            'locations_complete': keep_locs,
            'locations': [{'script': l['script'], 'line': l['line'], 'kind': l['script_kind'], 'block': l['block'],
                           'status': l['status'], 'severity': l['severity'], 'player_bound': l['player_bound'],
                           'conditional': l['conditional'], 'text': l['text']}
                          for l in f['locations'] if keep_locs]})
    rows.sort(key=lambda r: (-fnmap.SEV_ORDER.index(r['severity']), -fnmap.RANK[r['status']], -r['count'], r['fn'].lower()))

    # curated co-op hazard sites, with exact lines resolved from the script text
    by_label = {a['script'].label.lower(): a['script'] for a in scripts}
    hz = []
    for h in hazards.H:
        sc = by_label.get(h['script'].lower())
        found = []
        if sc:
            for n, raw in enumerate(sc.source.splitlines(), 1):
                line = classify.strip_comment(raw).strip()
                if re.search(h['rx'], line, re.I):
                    found.append({'line': n, 'text': line[:160]})
        hz.append(dict(h, resolved=found, script_found=bool(sc)))
    return {'rows': rows, 'sites': sites, 'unclassified': dict(unclassified), 'unknown_calls': unknown_calls, 'hazards': hz}


def summarize_gaps(g, scripts):
    sites = g['sites']
    by_status = collections.Counter(s['status'] for s in sites)
    by_sev = collections.Counter(s['severity'] for s in sites if s['status'] in (fnmap.PAR, fnmap.MIS))
    pb_reads = sum(1 for s in sites if s['status'] == fnmap.NA and s['player_bound'] and s['effect'] == '-' and s['severity'] != 'info')
    return {
        'scripts_analysed': len(scripts),
        'scripts_by_kind': dict(collections.Counter(a['script'].kind for a in scripts)),
        'statements': len(sites),
        'distinct_functions': len(g['rows']),
        'by_status': dict(by_status),
        'gap_sites_by_severity': dict(by_sev),
        'player_bound_reads': pb_reads,
        'unclassified_functions': g['unclassified'],
        'unknown_calls': len(g['unknown_calls']),
        'curated_hazards': len(g['hazards']),
        'curated_hazards_by_severity': dict(collections.Counter(h['sev'] for h in g['hazards'])),
    }


# ---- inventory ---------------------------------------------------------------------------------------------------------
def build_inventory(d, scripts):
    risk = d.risk
    rules = json.load(open(os.path.join(dmlib.REPO, 'docs', 'quest-policies', 'rules.json'), encoding='utf-8'))['quests']
    graph = json.load(open(os.path.join(dmlib.REPO, 'out', 'coverage', 'questgraph.json'), encoding='utf-8'))
    inbound = collections.defaultdict(list)
    outbound = collections.defaultdict(list)
    for e in graph['edges']:
        inbound[e['dst']].append(e)
        outbound[e['src']].append(e)
    dm_ids = {q['edid'] for q in d.quests}
    # exact stage triggers: which script calls SetStage / StartQuest on which quest
    triggers = collections.defaultdict(list)
    starts = collections.defaultdict(list)
    for a in scripts:
        sc = a['script']
        for m in SETSTAGE_RE.finditer(sc.source):
            if m.group(1) in dm_ids:
                line = sc.source[:m.start()].count('\n') + 1
                triggers[(m.group(1), int(m.group(2)))].append({'by': sc.label, 'line': line, 'kind': sc.kind})
        for m in STARTQUEST_RE.finditer(sc.source):
            if m.group(1) in dm_ids:
                line = sc.source[:m.start()].count('\n') + 1
                starts[m.group(1)].append({'by': sc.label, 'line': line, 'kind': sc.kind})
    # related dialogue: INFOs (any plugin) whose conditions / scripts mention the quest
    cond_refs = collections.Counter()
    script_refs = collections.Counter()
    qrefs = {q['ref'] for q in d.quests}
    for i in d.infos:
        seen_c, seen_s = set(), set()
        for c in i['conditions']:
            p = c.get('p1')
            if isinstance(p, str) and p in qrefs:
                seen_c.add(p)
        for k in ('begin', 'end'):
            sc = i.get(k)
            if sc:
                seen_s.update(r for r in sc.get('refs', []) if r in qrefs)
        for p in seen_c:
            cond_refs[p] += 1
        for p in seen_s:
            script_refs[p] += 1
    nat = d.native['quests']
    by_label = collections.defaultdict(int)
    for a in scripts:
        s = a['script']
        if s.quest:
            by_label[(s.quest, s.kind)] += 1
    qs = []
    for q in sorted(d.quests, key=lambda q: q['ref']):
        rk = risk[q['ref']]
        edid = q['edid']
        stages = []
        for st in sorted(q['stages'], key=lambda s: s['index']):
            ns = (nat.get(edid) or {}).get('stages', {}).get(str(st['index'])) or {}
            uncond = [e['type'] for e in ns.get('unconditional', [])]
            cond = [e['type'] for e in ns.get('conditional', [])]
            stages.append({
                'index': st['index'],
                'flags': sorted({f for e in st['entries'] for f in e['flags']}),
                'log': next((e.get('text') for e in st['entries'] if e.get('text')), None),
                'has_script': any(e.get('script') for e in st['entries']),
                'entry_conditions': sum(len(e['conditions']) for e in st['entries']),
                'effects_unconditional': dict(collections.Counter(uncond)),
                'effects_conditional': dict(collections.Counter(cond)),
                'triggered_by': triggers.get((edid, st['index']), []),
            })
        objs = []
        for o in sorted(q['objectives'], key=lambda o: o['index']):
            objs.append({'index': o['index'], 'text': o.get('text'),
                         'targets': [{'ref': t['ref'], 'edid': d.edid_of(t['ref']), 'formid': dmlib.runtime_id(t['ref']) if t['ref'] else None,
                                      'conditions': len(t['conditions'])} for t in o['targets']]})
        prereq_edges = []
        for e in inbound.get(edid, []):
            if e['src'] in dm_ids and e['src'] != edid and e['kind'] in ('stage', 'start', 'complete', 'gate', 'var'):
                prereq_edges.append({'from': e['src'], 'kind': e['kind'], 'strength': e['strength'], 'count': e['count'],
                                     'evidence': [x['text'][:100] for x in e['evidence'][:2]]})
        rr = rules.get(edid)
        qs.append({
            'edid': edid, 'ref': q['ref'], 'formid': dmlib.runtime_id(q['ref']), 'name': q.get('name'), 'plugin': q['plugin'],
            'role': role_of(edid), 'flags': q['flags'], 'priority': q.get('priority'), 'delay': q.get('delay'),
            'start_enabled': 'start_enabled' in q['flags'],
            'tier': rk['tier'], 'tier_direct': rk['tier_direct'], 'risk_score': rk['score'], 'risk_cats': rk['cats'],
            'quest_script': (q.get('script_data') or {}).get('edid'),
            'quest_conditions': q['conditions'],
            'stages': stages,
            'objectives': objs,
            'prerequisites': {
                'quests': sorted({e['from'] for e in prereq_edges if e['kind'] in ('stage', 'start', 'complete', 'gate')}),
                'edges': prereq_edges,
                'started_by': starts.get(edid, []),
                'curated': prereq.QUEST_PREREQ.get(edid, {}).get('needs', []),
            },
            'dialogue': {'own_infos': len(q['infos']), 'own_topics': len(q['topics']),
                         'related_infos_by_condition': cond_refs.get(q['ref'], 0),
                         'related_infos_by_script': script_refs.get(q['ref'], 0)},
            'scripts': {k: v for k, v in sorted(
                {'stage': by_label[(edid, 'stage')], 'info_begin': by_label[(edid, 'info_begin')],
                 'info_end': by_label[(edid, 'info_end')]}.items())},
            'dep_scripts': rk['dep_scripts'],
            'rules_existing': [r['kind'] for r in rr['rules']] if rr else [],
            'rules_origin': (('curated' if rr.get('curated') else 'derived') if rr else None),
        })
    return {'schema': 1, 'slug': 'deadmoney', 'questline': 'DLC: Dead Money', 'plugin': dmlib.PLUGIN, 'load_order_prefix': dmlib.PREFIX,
            'generated_from': ['out/questdata/quests.json', 'effects_native.json.gz', 'risk.json', 'out/coverage/questgraph.json',
                               'docs/quest-policies/rules.json'],
            'summary': {'quests': len(qs), 'quests_with_stages': sum(1 for q in qs if q['stages']),
                        'stages': sum(len(q['stages']) for q in qs), 'objectives': sum(len(q['objectives']) for q in qs),
                        'own_infos': sum(q['dialogue']['own_infos'] for q in qs),
                        'by_role': dict(collections.Counter(q['role'] for q in qs)),
                        'by_tier': dict(collections.Counter(q['tier'] for q in qs))},
            'quests': qs}


# ---- sweep config -----
def hex_of(d, edid):
    if edid.lower() in ('player', 'playerref'):
        return '00000014'
    ref = d.edid_index().get(edid.lower())
    if ref is None:
        raise KeyError('unresolvable EditorID %s' % edid)
    return dmlib.runtime_id(ref)


def render(d, c):
    """structured prerequisite -> dict with console text `cmd` (hex FormIDs) and `alt` (EditorID form)."""
    o = dict(c)
    q = hex_of(d, c['quest'])
    if c['verb'] == 'setstage':
        o['cmd'], o['alt'] = 'setstage %s %d' % (q, c['stage']), 'setstage %s %d' % (c['quest'], c['stage'])
    elif c['verb'] == 'startquest':
        o['cmd'], o['alt'] = 'startquest %s' % q, 'startquest %s' % c['quest']
    else:
        o['cmd'] = 'set %s.%s to %s' % (q, c['var'], c['value'])
        o['alt'] = 'set %s.%s to %s' % (c['quest'], c['var'], c['value'])
    return o


def render_probe(d, p):
    o = dict(p)
    if p.get('call_ref'):
        o['call_ref_formid'] = hex_of(d, p['call_ref'])
    if p.get('cmd'):
        o['cmd_hex'] = re.sub(r'\{(\w+)\}', lambda m: hex_of(d, m.group(1)), p['cmd'])
    if p.get('vars'):
        o['vars'] = [{'quest': q, 'quest_formid': hex_of(d, q), 'var': v, 'value': val,
                      'cmd': 'set %s.%s to %s' % (hex_of(d, q), v, val)} for q, v, val in p['vars']]
    return o

# ---- sweep builder
def build_sweep(d, inv, scripts):
    risk = d.risk
    byid = {q['edid']: q for q in inv['quests']}
    out_q = []
    excluded = []
    # sweep order: prerequisites first, endings and cinematics last (hang risk), debug quests excluded
    order = prereq.SWEEP_ORDER
    stage_findings = {}
    for q in d.quests:
        rk = risk[q['ref']]
        for f in rk['stage_findings']:
            stage_findings.setdefault((q['edid'], f['where']), []).append(
                dict(cat=f['cat'], fn=f['fn'], level=f['level'], text=f['text']))
    for edid in order:
        q = byid[edid]
        if not q['stages']:
            continue
        rawq = d.by_edid[edid]
        stages = []
        for st in q['stages']:
            ex = prereq.EXCLUDE.get((edid, st['index'])) or (prereq.EXCLUDE_QUEST.get(edid))
            pre = prereq.stage_setup(edid, st['index'])
            row = {'index': st['index'], 'log': st['log'], 'flags': st['flags'], 'has_script': st['has_script'],
                   'findings': stage_findings.get((edid, 'stage %d' % st['index']), []),
                   'sweep': 'exclude' if ex else 'run', 'setup': [render(d, c) for c in pre]}
            if ex:
                row['exclude_reason'] = ex
                excluded.append({'quest': edid, 'stage': st['index'], 'reason': ex})
            stages.append(row)
        out_q.append({
            'edid': edid, 'ref': q['ref'], 'formid': q['formid'], 'name': q['name'], 'role': q['role'], 'tier': q['tier'],
            'tier_direct': q['tier_direct'], 'cats': q['risk_cats'], 'priority': q['priority'], 'flags': q['flags'],
            'script': rawq.get('script'),
            'stages': stages,
            'objectives': [{'index': o['index'], 'text': o['text'], 'targets': [{'ref': t['ref'], 'edid': t['edid']} for t in o['targets']]}
                           for o in q['objectives']],
            'setup': [render(d, c) for c in prereq.QUEST_PREREQ.get(edid, {}).get('setup', [])],
            'needs_note': prereq.QUEST_PREREQ.get(edid, {}).get('note'),
            'hang_risk': prereq.HANG_RISK.get(edid),
        })
    reset_ids = [q['formid'] for q in out_q]
    for k in prereq.RESET_EXTRA:
        reset_ids.append(inv_formid(inv, k))
    reset_ids = list(dict.fromkeys(reset_ids))
    cfg = {
        'schema': 1, 'milestone': 'Dead Money', 'slug': 'deadmoney', 'plugin': dmlib.PLUGIN, 'load_order_prefix': dmlib.PREFIX,
        'format': 'superset of out/questdata/m1_goodsprings.json for the fields plugin/snapshot/tools/goodsprings_sweep.load_plan reads '
                  '(quests[].edid/ref/stages[].index/tier). NOTE: that loader builds the console id from ref with load-order index 0; '
                  'for DeadMoney use quests[].formid (prefix 01). Only stages with sweep == "run" are meant to be executed.',
        'console': {'stage_cmd': 'setstage {formid} {stage}',
                    'notes': ['Console needs hex FormIDs; EditorIDs do not resolve (CONTRACTS / BRIEF).',
                              'SetStage on a quest that is not running starts it (engine SetRunning) - the M1 proof relies on this.',
                              '`set <quest>.<var> to <v>`: hex FormID as the quest token is UNVERIFIED; each such command carries "verified": false. '
                              'Fallback is the EditorID form recorded in "alt".',
                              'Ref-targeted commands (`<ref>.Cmd`) are rejected by the console parser (L5 round 8); use the bridge exec.ref method '
                              'with the calling reference given in "call_ref".']},
        'save': {'name': 'autosave', 'note': 'unknown: the M1 sweep reloads `autosave`; a Dead Money specific save is not required for '
                                              'setstage-driven stages but the bunker/Villa cells are not loaded, so Enable/Disable on '
                                              'unloaded refs may not be observable in the snapshot (unverified).'},
        'reset_ids': reset_ids,
        'reset_note': 'test.quest.reset clears stages/flags/objectives only, NOT script variables: DM quest scripts keep state in variables '
                      '(bTeleportOut, DoOnceMessage, fTimer ...); the sweep must reload the save between stages as M1 does.',
        'order': order,
        'summary': {
            'quests': len(out_q), 'stages': sum(len(q['stages']) for q in out_q),
            'stages_run': sum(1 for q in out_q for s in q['stages'] if s['sweep'] == 'run'),
            'stages_excluded': len(excluded)},
        'quests': out_q,
        'excluded': excluded,
        'not_stage_driven': prereq.NOT_STAGE_DRIVEN,
        'event_probes': [render_probe(d, p) for p in prereq.EVENT_PROBES],
    }
    return cfg


def inv_formid(inv, edid):
    for q in inv['quests']:
        if q['edid'] == edid:
            return q['formid']
    raise KeyError(edid)


# ---- gaps.md -----------------------------------------------------------------------------------------------------------
def compress_locations(locs, limit=60):
    by = collections.OrderedDict()
    for l in locs:
        by.setdefault(l['script'], []).append(l['line'])
    parts = []
    for k, v in by.items():
        v = sorted(set(v))
        parts.append('%s:%s' % (k, ','.join(map(str, v))))
    s = '; '.join(parts)
    return s


def render_gaps_md(g, summary):
    L = []
    L.append('# Dead Money: capture/apply gap list\n')
    L.append('Generated by `questlines/deadmoney/build.py` from the quest data (static analysis, nothing run in the game). '
             'Status = weaker of L5 host **capture** and replica **apply**, read statically from the pinned `l5_snapshot.json` '
             '(engine_hooks.cpp, apply_remote.cpp, effect.h; refresh with `refresh_l5.py`).\n')
    L.append('Locations are `script label:line`. Labels: SCPT EditorID for quest/object scripts; `<quest> stage <n>` for stage result scripts; '
             '`INFO <local id> begin|end` for topic result scripts; `TERM <edid> item <n>`; `PACK <edid> <slot>`.\n')
    L.append('## Totals\n')
    L.append('- scripts analysed: **%d** (%s)' % (summary['scripts_analysed'], ', '.join('%s %d' % kv for kv in sorted(summary['scripts_by_kind'].items()))))
    L.append('- statements mapped: **%d** over **%d** distinct functions' % (summary['statements'], summary['distinct_functions']))
    L.append('- by status: ' + ', '.join('%s %d' % kv for kv in sorted(summary['by_status'].items())))
    L.append('- gap sites by severity (partial/missing/event): ' + ', '.join('%s %d' % (k, summary['gap_sites_by_severity'].get(k, 0)) for k in ('blocker', 'high', 'medium', 'low', 'info')))
    L.append('- unclassified functions (must be 0): %d; unknown statement-leading calls (must be 0): %d' % (len(summary['unclassified_functions']), summary['unknown_calls']))
    L.append('- curated co-op hazard sites: %d (%s)\n' % (summary['curated_hazards'], ', '.join('%s %d' % kv for kv in sorted(summary['curated_hazards_by_severity'].items()))))
    L.append('Severity: blocker = questline cannot be finished together or a player is killed/soft-locked unfairly; high = shared progression '
             'state diverges or per-player state is lost; medium = recoverable divergence; low = audio/visual/UX. A player-targeted '
             'unsupported mutation is escalated one level at the call site.\n')

    L.append('## Curated co-op hazard sites (co-op specific, exact lines)\n')
    L.append('| id | sev | system | title | where | handling |')
    L.append('|---|---|---|---|---|---|')
    order = {'blocker': 0, 'high': 1, 'medium': 2, 'low': 3}
    for h in sorted(g['hazards'], key=lambda h: (order[h['sev']], h['id'])):
        where = ', '.join('%s:%d' % (h['script'], r['line']) for r in h['resolved'][:3]) or '(unresolved)'
        L.append('| %s | %s | %s | %s | %s | %s |' % (h['id'], h['sev'], h['system'], h['title'], where, h['handling'].replace('|', '/')))
    L.append('')
    L.append('Why each breaks with 2-4 players is in `gaps.json` (`hazards[].why`) and summarised in `policy_review.md`.\n')

    for status, title in ((fnmap.MIS, 'Missing (no capture and/or no apply)'), (fnmap.PAR, 'Partial')):
        rows = [r for r in g['rows'] if r['status'] == status and r['kind'] != 'condition']
        L.append('## %s\n' % title)
        L.append('| function | effect type | capture | apply | sev | count | tags | note |')
        L.append('|---|---|---|---|---|---|---|---|')
        for r in rows:
            L.append('| %s | %s | %s | %s | %s | %d | %s | %s |' % (r['fn'], r['effect'], r['capture'], r['apply'], r['severity'], r['count'],
                                                              ' '.join(r['tags']), r['note'].replace('|', '/')[:170]))
        L.append('')
        L.append('### Locations (%s)\n' % status)
        for r in rows:
            bad = [l for l in r['locations'] if l['status'] in (fnmap.PAR, fnmap.MIS)]
            L.append('**%s** (%d unsupported of %d, %s): %s\n' % (r['fn'], len(bad), r['count'], r['severity'], compress_locations(bad)))
    cond = [r for r in g['rows'] if r['kind'] == 'condition' and any(l['player_bound'] for l in r['locations'])]
    L.append('## Player-bound reads (host-side party-aware evaluation, rule PartyAwareCond)\n')
    L.append('| function | player-bound sites | all sites | locations (player-bound) |')
    L.append('|---|---|---|---|')
    for r in sorted(cond, key=lambda r: -sum(1 for l in r['locations'] if l['player_bound'])):
        pb = [l for l in r['locations'] if l['player_bound']]
        L.append('| %s | %d | %d | %s |' % (r['fn'], len(pb), r['count'], compress_locations(pb)[:900]))
    L.append('')
    sup = [r for r in g['rows'] if r['status'] == fnmap.SUP]
    L.append('## Supported (for completeness)\n')
    L.append(', '.join('%s (%d)' % (r['fn'], r['count']) for r in sup) + '\n')
    return '\n'.join(L)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument('--out', default=HERE)
    a = ap.parse_args(argv)
    d = dmlib.Data()
    scripts = analyse(d)
    g = build_gaps(d, scripts)
    summary = summarize_gaps(g, scripts)
    inv = build_inventory(d, scripts)
    sweep = build_sweep(d, inv, scripts)
    os.makedirs(a.out, exist_ok=True)

    def w(name, obj):
        with open(os.path.join(a.out, name), 'w', encoding='utf-8', newline='\n') as f:
            json.dump(obj, f, indent=1, ensure_ascii=False, sort_keys=False)
            f.write('\n')
    w('inventory.json', inv)
    w('sweep.json', sweep)
    gj = {'schema': 1, 'slug': 'deadmoney', 'summary': summary,
          'severity_rubric': {'blocker': 'questline cannot be completed together / a player is killed or soft-locked unfairly',
                              'high': 'shared progression state diverges or per-player state is lost',
                              'medium': 'recoverable divergence', 'low': 'audio / visual / UX only', 'info': 'supported or no cross-machine effect'},
          'l5_evidence': ['questlines/deadmoney/l5_snapshot.json (pinned reading of plugin/hooks/engine_hooks.cpp, '
                          'plugin/hooks/apply_remote.cpp, synccore/include/fnvcoop/effect.h)'],
          'functions': g['rows'],
          'hazards': [{k: v for k, v in h.items() if k != 'rx'} | {'rx': h['rx']} for h in g['hazards']]}
    w('gaps.json', gj)
    with open(os.path.join(a.out, 'gaps.md'), 'w', encoding='utf-8', newline='\n') as f:
        f.write(render_gaps_md(g, summary))
    print('quests %d stages %d | scripts %d statements %d functions %d | hazards %d'
          % (inv['summary']['quests'], inv['summary']['stages'], summary['scripts_analysed'], summary['statements'],
             summary['distinct_functions'], summary['curated_hazards']))
    return 0


if __name__ == '__main__':
    sys.exit(main())
