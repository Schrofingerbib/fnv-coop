#!/usr/bin/env python3
"""Generate every deliverable of the `leftovers` questline from out/questdata.

    python -I questlines/leftovers/gen.py [--status]

Writes inventory.json, sweep.json, gaps.json, gaps.md, rules.patch.json, policy_review.md, dialogue.md (this dir) and,
with --status, internal questline notes (includes the last test output saved by run_tests.py).
Read-only on out/questdata, docs/quest-policies, tools/. Never touches the game install.
"""
import collections
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import collect as C  # noqa: E402
import tables as T  # noqa: E402
import coverage as K  # noqa: E402

REPO = C.REPO
sys.path.insert(0, os.path.join(REPO, 'tools', 'coverage'))
import rules as RULES  # noqa: E402
import classify  # noqa: E402

DOCS_STATUS = os.path.join(REPO, 'docs', 'questlines', 'leftovers.md')
AV_NAMES = {43: 'Speech'}   # only Speech is verified in-repo (docs/dialogue-design.md, info_stats.py)


# --------------------------------------------------------------------------------------------------------------------
# name resolution
# --------------------------------------------------------------------------------------------------------------------
class Names:
    def __init__(self, ctx):
        forms = C.gz('forms.json.gz')
        refn = C.gz('refnames.json.gz')
        self.edid_of = {}
        self.sig_of = {}
        for d in (refn, forms):
            for k, v in d.items():
                if v.get('edid'):
                    self.edid_of[k] = v['edid']
                    self.sig_of[k] = v['sig']
        for q in ctx['d']['quests']:
            if q.get('edid'):
                self.edid_of[q['ref']] = q['edid']
                self.sig_of[q['ref']] = 'QUST'
        for g in ctx['d']['globals']:
            if g.get('edid'):
                self.edid_of[g['ref']] = g['edid']
                self.sig_of[g['ref']] = 'GLOB'
        self.ref_of_edid = {}
        for k, v in self.edid_of.items():
            self.ref_of_edid.setdefault(v.lower(), k)
        self.dials = {d['ref']: d for d in C.gz('dials.json.gz')}

    def name(self, ref):
        return self.edid_of.get(ref) if isinstance(ref, str) else None

    def resolve(self, edid):
        return self.ref_of_edid.get(edid.lower())


def edid_ok(names, edid):
    return names.resolve(edid) is not None


# --------------------------------------------------------------------------------------------------------------------
# inventory
# --------------------------------------------------------------------------------------------------------------------
def cond_view(names, c):
    return {'func': c['func'], 'op': c['op'], 'value': c['value'], 'p1': c.get('p1'), 'p1_edid': names.name(c.get('p1')),
            'p2': c.get('p2'), 'run_on': c.get('run_on')}


def theme_of(q):
    """First objective text, else first stage log, else None. Taken verbatim from the data; no story claims added."""
    for o in sorted(q['objectives'], key=lambda o: o['index']):
        if o.get('text'):
            return o['text'][:140]
    for st in sorted(q['stages'], key=lambda s: s['index']):
        for e in st['entries']:
            if e.get('text'):
                return e['text'][:140]
    return None


def build_inventory(ctx, names, order, policy_edids, rules_json):
    graph = json.load(open(os.path.join(REPO, 'out', 'coverage', 'questgraph.json'), encoding='utf-8'))
    reads = collections.defaultdict(list)
    driven = collections.defaultdict(list)
    for e in graph['edges']:
        if e['src'] != e['dst']:
            reads[e['src']].append(e)
            driven[e['dst']].append(e)
    dial_count = collections.Counter()
    for d in names.dials.values():
        for qr in d.get('quests', []):
            dial_count[qr] += 1
    quests = []
    for edid in C.QUESTS:
        q = ctx['by_edid'][edid]
        r = ctx['risk'][q['ref']]
        ins = ctx['infos'].get(q['ref'], [])
        stages = []
        for s in sorted(q['stages'], key=lambda s: s['index']):
            ents = s['entries']
            stages.append({
                'index': s['index'],
                'log': next((e.get('text') for e in ents if e.get('text')), None),
                'has_script': any(e.get('script') for e in ents),
                'flags': sorted({f for e in ents for f in e['flags']}),
                'conditions': [cond_view(names, c) for e in ents for c in e['conditions']],
            })
        objs = [{'index': o['index'], 'text': o.get('text'),
                 'targets': [{'ref': t['ref'], 'edid': names.name(t['ref']), 'conditions': len(t['conditions'])}
                             for t in o['targets']]}
                for o in sorted(q['objectives'], key=lambda o: o['index'])]
        edge_view = lambda e: {'quest': e['src'] if e['dst'] == edid else e['dst'], 'kind': e['kind'],  # noqa: E731
                               'strength': e['strength'], 'count': e['count'],
                               'evidence': [x['text'] for x in e['evidence'][:2]]}
        quests.append({
            'edid': edid, 'ref': q['ref'], 'formid': C.fid(q['ref'], order), 'name': q.get('name'), 'plugin': q['plugin'],
            'group': C.GROUP_OF[edid], 'theme': theme_of(q), 'also_covered_by': C.ALSO_COVERED_BY.get(edid), 'kind': r['kind'], 'tier': r['tier'],
            'tier_direct': r['tier_direct'], 'risk_score': r['score'], 'flags': q.get('flags'),
            'start_enabled': 'start_enabled' in (q.get('flags') or []),
            'stages': stages, 'objectives': objs,
            'prerequisites': {
                'quest_conditions': [cond_view(names, c) for c in q['conditions']],
                'reads_state_of': [edge_view(e) for e in reads.get(edid, [])
                                   if e['kind'] in ('gate', 'var') and e['strength'] == 'hard'],
                'driven_by': [edge_view(e) for e in driven.get(edid, []) if e['kind'] in ('stage', 'start', 'var')][:12],
                'driven_by_total': len(driven.get(edid, [])),
            },
            'dialogue': {'topics_field': len(q.get('topics', [])), 'dials': dial_count.get(q['ref'], 0), 'infos': len(ins),
                         'scripted_infos': sum(1 for i in ins if i.get('begin') or i.get('end'))},
            'has_policy_entry': edid in policy_edids, 'has_rules_entry': edid in rules_json['quests'],
        })
    xref = {}
    for edid, why in C.XREF.items():
        q = ctx['by_edid'].get(edid)
        xref[edid] = {'reason': why, 'in_data': q is not None,
                      'name': q.get('name') if q else None, 'ref': q['ref'] if q else None}
    summary = {
        'quests': len(quests),
        'quests_with_stages': sum(1 for q in quests if q['stages']),
        'stages': sum(len(q['stages']) for q in quests),
        'objectives': sum(len(q['objectives']) for q in quests),
        'infos': sum(q['dialogue']['infos'] for q in quests),
        'by_group': {g: len(v) for g, v in C.GROUPS.items()},
        'by_tier': dict(collections.Counter(q['tier'] for q in quests)),
    }
    return {'schema': 1, 'source': 'out/questdata/quests.json + risk.json + out/coverage/questgraph.json',
            'summary': summary, 'quests': quests, 'xref': xref}


# --------------------------------------------------------------------------------------------------------------------
# sweep
# --------------------------------------------------------------------------------------------------------------------
READ_RE = re.compile(r'(?i)(getstagedone|getquestcompleted)\s+(\w+)(?:\s+(\d+))?')


def cond_setup(ctx, names, order, owner_edid, c):
    """Translate one quest/stage condition into console prerequisite commands. Returns (cmds, unresolved_note)."""
    f = c['func']
    p1 = c.get('p1')
    if f not in ('GetStageDone', 'GetStage', 'GetQuestCompleted', 'GetQuestRunning', 'GetQuestVariable'):
        return [], None
    if not isinstance(p1, str) or p1 not in ctx['by_ref'] or ctx['by_ref'][p1]['edid'] == owner_edid:
        return [], None
    tq = ctx['by_ref'][p1]
    hx = C.fid(p1, order)
    v, op = c['value'], c['op']
    want_true = (op == '==' and v == 1) or (op == '!=' and v == 0) or (op in ('>=', '>') and v >= 0 and f != 'GetStageDone')
    if f == 'GetStageDone' and op == '==' and v == 1:
        return ['setstage %s %d' % (hx, int(c['p2']))], None
    if f == 'GetStage' and op in ('>=', '>', '=='):
        n = int(v) + (1 if op == '>' else 0)
        return (['setstage %s %d' % (hx, n)] if n > 0 else []), None
    if f == 'GetQuestCompleted' and want_true and op == '==':
        return ['completequest %s' % hx], None
    if f == 'GetQuestRunning' and op == '==' and v == 1:
        return ['startquest %s' % hx], None
    if f == 'GetQuestVariable' and op in ('>=', '==', '>'):
        vars_ = (tq.get('script_data') or {}).get('vars', [])
        name = next((x['name'] for x in vars_ if x['idx'] == c['p2']), None)
        if name:
            n = int(v) + (1 if op == '>' else 0)
            return ['set %s.%s to %d' % (hx, name, n)], None
        return [], 'quest variable index %s of %s has no name in script_data.vars' % (c['p2'], tq['edid'])
    return [], None


def build_sweep(ctx, names, order, inv):
    qout = []
    excluded = []
    cats_all = collections.Counter()
    order_last = []
    for iq in inv['quests']:
        edid = iq['edid']
        q = ctx['by_edid'][edid]
        r = ctx['risk'][q['ref']]
        hx = iq['formid']
        base = []
        if not iq['start_enabled']:
            base.append('startquest %s' % hx)
        qcmds, unresolved = [], []
        for c in q['conditions']:
            cm, un = cond_setup(ctx, names, order, edid, c)
            qcmds += cm
            if un:
                unresolved.append(un)
        stages = []
        for s in sorted(q['stages'], key=lambda s: s['index']):
            scmds = []
            for e in s['entries']:
                for c in e['conditions']:
                    cm, un = cond_setup(ctx, names, order, edid, c)
                    scmds += cm
                    if un:
                        unresolved.append(un)
            setup = list(dict.fromkeys(base + qcmds + scmds))
            fnd = [f for f in COLLECTED[edid] if f['where'] == 'stage %d' % s['index']]
            objfx = []
            for e in s['entries']:
                sc = e.get('script')
                if sc:
                    for kind, qn, n, v in re.findall(r'(?im)\bsetobjective(displayed|completed)\s+(\w+)\s+(\d+)\s+([01])',
                                                     sc['source']):
                        objfx.append({'stage': 'stage %d' % s['index'], 'kind': kind.lower(), 'quest': qn,
                                      'objective': int(n), 'value': int(v)})
            hints = []
            for e in s['entries']:
                sc = e.get('script')
                if not sc:
                    continue
                for n_, raw in enumerate(sc['source'].splitlines(), 1):
                    line = classify.strip_comment(raw)
                    for fn_, qn_, num_ in READ_RE.findall(line):
                        tq = ctx['by_edid'].get(qn_)
                        if not tq or qn_ == edid:
                            continue
                        h = C.fid(tq['ref'], order)
                        if fn_.lower() == 'getstagedone' and num_:
                            cmd = 'setstage %s %s' % (h, num_)
                        elif fn_.lower() == 'getquestcompleted':
                            cmd = 'completequest %s' % h
                        else:
                            continue
                        hints.append({'line': n_, 'reads': '%s %s %s' % (fn_, qn_, num_), 'suggested': cmd})
            stages.append({
                'index': s['index'],
                'log': next((e.get('text') for e in s['entries'] if e.get('text')), None),
                'flags': sorted({f for e in s['entries'] for f in e['flags']}),
                'has_script': any(e.get('script') for e in s['entries']),
                'setup': setup, 'command': 'setstage %s %d' % (hx, s['index']),
                'suggested_setup': hints,
                'findings': [dict(cat=f['cat'], fn=f['fn'], level=f['level'], text=f['text']) for f in fnd if f['level'] >= 1],
                'objective_effects': objfx,
            })
        if not stages:
            excluded.append({'edid': edid, 'stage': None,
                             'reason': 'no stages: dialogue/system container; covered by dialogue.md and gaps.json, nothing to setstage'})
        cats_all.update(r['cats'])
        has_cut = any(f['fn'] in ('disableplayercontrols', 'startconversation') for f in COLLECTED[edid])
        if has_cut or r['tier'] == 'T4':
            order_last.append(edid)
        qout.append({
            'edid': edid, 'ref': q['ref'], 'formid': hx, 'name': q.get('name'), 'role': 'core' if stages else 'system',
            'group': iq['group'], 'also_covered_by': iq['also_covered_by'], 'tier': r['tier'], 'tier_direct': r['tier_direct'], 'cats': r['cats'],
            'priority': q.get('priority'), 'flags': q.get('flags'), 'script': q.get('script'),
            'start_enabled': iq['start_enabled'],
            'setup': list(dict.fromkeys(base + qcmds)),
            'setup_unresolved': sorted(set(unresolved)),
            'activation_hints': [{'quest': d['quest'], 'kind': d['kind'], 'evidence': d['evidence']} for d in iq['prerequisites']['driven_by'][:6]],
            'activation_note': ('driven by the script of another quest (hints above are verbatim evidence; the stage of the driver that fires it is NOT derived)' if iq['prerequisites']['driven_by'] else ('start_enabled; started by dialogue or world triggers, no quest-script driver in the questgraph' if iq['start_enabled'] else 'no driver found in the questgraph: unknown how it starts')),
            'stages': stages,
            'objectives': [{'index': o['index'], 'text': o['text'], 'targets': o['targets']} for o in iq['objectives']],
            'dialogue': iq['dialogue'],
        })
    touched = {}
    members = set(C.QUESTS)
    for edid in C.QUESTS:
        q = ctx['by_edid'][edid]
        for where, sedid, src, dctx in C.scripts_of(ctx, q):
            for qn, _ in re.findall(r'(?im)\bsetstage\s+(\w+)\s+(\d+)', src):
                if qn in ctx['by_edid'] and qn not in members:
                    touched.setdefault(qn, set()).add('%s: setstage' % edid)
    out = {
        'milestone': 'Leftovers (unassigned VMS side quests + coverage cross-check)',
        'core': {q['edid']: q['name'] for q in qout if q['role'] == 'core'},
        'system_quests': [q['edid'] for q in qout if q['role'] == 'system'],
        'touched_quests': [{'edid': e, 'name': ctx['by_edid'][e].get('name'), 'stages': len(ctx['by_edid'][e]['stages']),
                            'why': sorted(w)} for e, w in sorted(touched.items())],
        'order_last': order_last,
        'excluded': excluded,
        'notes': [
            'Each stage is independent: the sweep reloads the autosave, runs `setup` in order, then `command`.',
            'VMS26 has empty stages (no log, objective text, scripts or references): setstage there is expected to change nothing observable; the sweep includes it so the stage count stays complete.',
            'CGTutorial is the chargen tutorial (28 stages, start_enabled, normally finished in the intro); sweeping its stages from a post-intro save may do nothing visible: unknown.',
            'VMS12/37/43/44/46/57 are also swept by the `factions` lane; run one copy only (they are marked also_covered_by).',
            'setup uses hex runtime FormIDs (loadorder.json). `set <hex>.<var> to n` is the brief-mandated form; if the console '
            'rejects a hex quest id for `set`, the EditorID form is quest edid + "." + var (same names).',
            'Console commands used: startquest, setstage, completequest, set. None of them were run in a game by this lane.',
            'Stage scripts run regardless of earlier stages (engine SetStage only raises); prerequisites are limited to '
            'the quest/stage conditions in the ESM. Script-internal reads (quest vars set by NPC scripts) are NOT modelled: unknown.',
        ],
        'summary': {
            'quests': len(qout), 'stages': sum(len(q['stages']) for q in qout),
            'objectives': sum(len(q['objectives']) for q in qout),
            'infos': sum(q['dialogue']['infos'] for q in qout),
            'cats': dict(cats_all),
        },
        'quests': qout,
    }
    return out


# --------------------------------------------------------------------------------------------------------------------
# gaps
# --------------------------------------------------------------------------------------------------------------------
def sev_rank(s):
    return T.SEV_ORDER.index(s)


def status_for(m):
    if m['kind'] == 'read':
        return 'n/a', 'n/a'
    if m['effect']:
        cap, app, _ = T.EFFECT_STATUS[m['effect']]
        return cap, app
    if m['kind'] == 'cosmetic':
        return 'missing', 'missing'
    return 'missing', 'missing'


def final_sev(m):
    sev = m['sev']
    if m['kind'] == 'read':
        return sev
    if m['effect']:
        cap, app, _ = T.EFFECT_STATUS[m['effect']]
        if cap == 'supported' and app == 'supported':
            # fully handled at Effect level; still 'low' where routing / in-game behaviour is a policy or unverified question
            return 'low' if set(m['hazards']) & {'perk', 'cinematic', 'faction_change'} else 'info'
        elif 'missing' in (cap, app):
            st = 'medium'
        else:
            st = 'low'
        return sev if sev_rank(sev) < sev_rank(st) else st
    return sev


def strip_comment(line):
    return classify.strip_comment(line)


def hazard_scan(ctx):
    """Line-level regex hazard scan over every script in the questline."""
    out = collections.defaultdict(list)
    for edid in C.QUESTS:
        q = ctx['by_edid'][edid]
        for where, sedid, src, dctx in C.scripts_of(ctx, q):
            kills = []
            for n, raw in enumerate(src.splitlines(), 1):
                line = strip_comment(raw).strip()
                if not line:
                    continue
                for name, rx, desc in T.HAZARD_RE:
                    if rx.search(line):
                        out[name].append({'quest': edid, 'where': where, 'script': sedid, 'line': n, 'text': line[:110]})
                if T.KILL_RE.search(line):
                    kills.append((n, line))
            if len(kills) >= 3:
                out['kill_everyone'].append({'quest': edid, 'where': where, 'script': sedid, 'line': kills[0][0],
                                             'text': '%d kill/startcombat lines in one script: %s' % (len(kills), kills[0][1][:80])})
    return out


def build_gaps(ctx):
    funcs = {}
    unmapped = collections.Counter()
    for edid in C.QUESTS:
        for f in COLLECTED[edid]:
            m = T.lookup(f)
            if m is None:
                unmapped[(f['fn'], f['cat'])] += 1
                continue
            key = '%s|%s' % (f['fn'].lower(), f['cat'])
            cap, app = status_for(m)
            e = funcs.setdefault(key, {
                'fn': f['fn'], 'cat': f['cat'], 'kind': m['kind'], 'effect': m['effect'], 'capture': cap, 'apply': app,
                'severity': final_sev(m), 'hazards': m['hazards'], 'scope': m['scope'], 'note': m['note'],
                'count': 0, 'quests': collections.Counter(), 'locations': []})
            e['count'] += 1
            e['quests'][edid] += 1
            e['locations'].append({'quest': edid, 'where': f['where'], 'script': f.get('script'), 'line': f['line'],
                                   'text': f['text'][:110]})
    for e in funcs.values():
        e['quests'] = dict(sorted(e['quests'].items(), key=lambda kv: -kv[1]))
    hz = hazard_scan(ctx)
    by_sev = collections.Counter()
    by_status = collections.Counter()
    for e in funcs.values():
        if e['kind'] == 'read':
            continue
        by_sev[e['severity']] += e['count']
        by_status['capture:' + e['capture']] += e['count']
        by_status['apply:' + e['apply']] += e['count']
    by_effect = collections.OrderedDict()
    for eff in T.EFFECTS_ALL:
        cap, app, ev = T.EFFECT_STATUS[eff]
        n = sum(e['count'] for e in funcs.values() if e['effect'] == eff)
        by_effect[eff] = {'capture': cap, 'apply': app, 'evidence': ev, 'occurrences_in_questline': n}
    return {
        'schema': 1,
        'note': 'L5 status read from plugin/hooks source on 2026-10-16, not verified in a running game.',
        'effects': by_effect,
        'functions': dict(sorted(funcs.items(), key=lambda kv: (sev_rank(kv[1]['severity']), -kv[1]['count']))),
        'hazards': {k: {'count': len(v), 'quests': dict(collections.Counter(x['quest'] for x in v)),
                        'locations': v} for k, v in sorted(hz.items())},
        'unmapped': sorted('%s|%s' % k for k in unmapped),
        'summary': {
            'findings': sum(e['count'] for e in funcs.values()),
            'non_read_findings': sum(e['count'] for e in funcs.values() if e['kind'] != 'read'),
            'distinct_functions': len(funcs),
            'severity_occurrences': {s: by_sev.get(s, 0) for s in T.SEV_ORDER},
            'severity_distinct_functions': {s: sum(1 for e in funcs.values() if e['kind'] != 'read' and e['severity'] == s)
                                            for s in T.SEV_ORDER},
            'status_occurrences': dict(by_status),
        },
    }


def loc_str(l):
    base = '%s %s' % (l['quest'], l['where'])
    if l.get('script'):
        base += ' [%s]' % l['script']
    return '%s:L%s' % (base, l['line'])


def gaps_md(g):
    s = g['summary']
    md = ['# Leftovers questline: script gaps (capture / apply)', '',
          '_Generated by `questlines/leftovers/gen.py` from `out/questdata` (quest, stage, info begin/end and dependency-object scripts)._',
          '_L5 status comes from reading `plugin/hooks/engine_hooks.cpp` and `apply_remote.cpp`; nothing here was run in a game._', '',
          '%d classified findings (%d are effects/mutations, the rest are condition reads) over %d distinct script functions.' %
          (s['findings'], s['non_read_findings'], s['distinct_functions']),
          'A dependency-object script (`dep obj X`) used by several quests is counted once per quest, so totals overstate distinct script lines; per-location lists are exact.', '',
          '## Severity (mutating findings only)', '', '| Severity | occurrences | distinct functions |', '|---|---:|---:|']
    for sv in T.SEV_ORDER:
        md.append('| %s | %d | %d |' % (sv, s['severity_occurrences'][sv], s['severity_distinct_functions'][sv]))
    md += ['', '## Synccore Effect types used by this questline', '',
           '| Effect | capture | apply | occurrences | evidence |', '|---|---|---|---:|---|']
    for eff, v in g['effects'].items():
        md.append('| %s | %s | %s | %d | %s |' % (eff, v['capture'], v['apply'], v['occurrences_in_questline'], v['evidence']))
    md += ['', '## Functions with no or partial support (mutating, severity above info)', '',
           '| fn | cat | Effect | capture | apply | sev | n | hazards | first locations (script EDID where known, line) |',
           '|---|---|---|---|---|---|---:|---|---|']
    for key, e in g['functions'].items():
        if e['kind'] == 'read' or e['severity'] == 'info':
            continue
        locs = ', '.join(loc_str(l) for l in e['locations'][:4])
        md.append('| %s | %s | %s | %s | %s | %s | %d | %s | %s%s |' % (
            e['fn'], e['cat'], e['effect'] or '(none)', e['capture'], e['apply'], e['severity'], e['count'],
            ','.join(e['hazards']) or '-', locs, ' ...' if len(e['locations']) > 4 else ''))
    md += ['', 'Full location lists for every row are in `gaps.json` (`functions[...].locations`).', '',
           '## Fully supported mutations', '', '| fn | cat | Effect | n |', '|---|---|---|---:|']
    for key, e in g['functions'].items():
        if e['kind'] != 'read' and e['severity'] == 'info':
            md.append('| %s | %s | %s | %d |' % (e['fn'], e['cat'], e['effect'], e['count']))
    md += ['', '## Condition reads (evaluated on the host; see policy_review.md for PartyAwareCond)', '',
           '| fn | cat | n | quests |', '|---|---|---:|---|']
    for key, e in g['functions'].items():
        if e['kind'] == 'read':
            md.append('| %s | %s | %d | %s |' % (e['fn'], e['cat'], e['count'], ', '.join(list(e['quests'])[:5])))
    md += ['', '## Co-op hazards (line-level scan of every script)', '',
           '| hazard | lines | quests | examples |', '|---|---:|---:|---|']
    for k, v in g['hazards'].items():
        md.append('| %s | %d | %d | %s |' % (k, v['count'], len(v['quests']), '; '.join(loc_str(l) for l in v['locations'][:3])))
    md += ['', 'Hazards named by the brief that have 0 hits are omitted from the table above; none of them is hidden.']
    if g['unmapped']:
        md += ['', '## UNMAPPED functions (tables.py must be extended)', ''] + ['- ' + u for u in g['unmapped']]
    return '\n'.join(md) + '\n'


# --------------------------------------------------------------------------------------------------------------------
# policy review / rules patch
# --------------------------------------------------------------------------------------------------------------------
# Curated proposals: edid -> list of (kind, params, why). Only kinds from tools/coverage/rules.py (16).
CURATED = {
    'VMS06': [
        ('PartyTether', {'cells': ['Vault22e'], 'gather': 'wait'},
         'Keely\'s download scene is gated on `player.GetInCell Vault22e` (dep obj Keely L26). Vault22e is the only Vault 22 CELL in the extracted forms; '
         'the other Vault 22 interiors are not extracted, so the tether is a partial cover (unknown which cell holds the final scene). The elevator MoveTo x57 is already covered by the existing PartyWarp rule.'),
    ],
    'VMS46': [
        ('RewardAll', {'include': ['perks']}, 'Player.AddPerk KhanTrick at stage 60: reward routed to every player (open owner question: perks).'),
        ('SetPlayerControls', {'scope': 'all', 'release': 'host-effect', 'timeout_s': 120, 'masks': []},
         'disablePlayerControls in infos 146F18, 146F0D, 146F04 end scripts (fade-to-black beats); a client that misses the release is soft-locked.'),
    ],
    'VMS12': [
        ('RemoveItemPerOwner', {}, 'Hand-in of HeadViolet / HeadVioletMangled (and the other two heads) at stage 110 removes the quest items from the handing player.'),
    ],
    'VMS13': [
        ('RemoveItemPerOwner', {}, 'Stage 100/110 `player.RemoveItem VMS13TeddyBear 1000` removes every copy; with QuestItemDup the other players keep theirs unless the removal is per owner.'),
    ],
    'VMS44': [
        ('RemoveItemPerOwner', {}, '83 RemoveItem lines (hand-in of radiation suits / radio parts to Logan, info end scripts); each must remove from the handing player only.'),
    ],
    'CGTutorial': [
        ('PerPlayerScene', {}, 'Tutorial hint ShowMessage x27 and its timers are per-player UI; whether the shared quest stage should advance for everyone is unknown (see policy_review.md).'),
    ],
}


def risk_view(fs):
    catmax = {}
    for f in fs:
        catmax[f['cat']] = max(catmax.get(f['cat'], 0), f['level'])
    return {'findings': [f for f in fs if f['level'] >= 1], 'stage_findings': [f for f in fs if f['where'].startswith('stage')],
            'cat_max_level': catmax}


def build_policy(ctx, names, inv, rules_json):
    existing = rules_json['quests']
    patch = collections.OrderedDict()
    rows = []
    for iq in inv['quests']:
        edid = iq['edid']
        derived = RULES.machine_rules(edid, risk_view(COLLECTED[edid]), None)
        have = {r['kind'] for r in existing.get(edid, {}).get('rules', [])}
        proposals = []
        for r in derived:
            if r['kind'] not in have:
                proposals.append({'kind': r['kind'], 'params': r['params'], 'origin': 'proposed-derived',
                                  'why': 'derived from script categories by tools/coverage/rules.py (CAT_KINDS) on the uncapped findings'})
        for kind, params, why in CURATED.get(edid, []):
            if kind not in have and kind not in {p['kind'] for p in proposals}:
                proposals.append({'kind': kind, 'params': params, 'origin': 'proposed-curated', 'why': why})
            elif kind not in have or any(p['kind'] == kind for p in proposals):
                for p in proposals:
                    if p['kind'] == kind and p['origin'] == 'proposed-derived':
                        p['params'] = {**p['params'], **params}
                        p['origin'] = 'proposed-curated'
                        p['why'] = why
            else:
                # already present with default params: record a param extension instead of a duplicate
                proposals.append({'kind': kind, 'params': params, 'origin': 'proposed-param-extension', 'why': why,
                                  'extends_existing': True})
        if proposals:
            patch[edid] = {'action': 'add-rules' if edid in existing else 'new-quest-entry',
                           'tier': iq['tier'], 'questline': 'Leftovers (%s)' % iq['group'], 'rules': proposals}
        rows.append({'edid': edid, 'tier': iq['tier'], 'name': iq['name'], 'group': iq['group'], 'existing': sorted(have),
                     'derived': [r['kind'] for r in derived], 'added': [p['kind'] for p in proposals]})
    return {'schema': 1,
            'doc': 'Proposed additions to docs/quest-policies/rules.json (owned by L9). Only the 16 existing kinds are used; '
                   'params extensions (faction_membership, include) are noted per rule and need an L9/L4 decision.',
            'kinds_used': sorted({p['kind'] for e in patch.values() for p in e['rules']}),
            'quests': patch}, rows


def policy_md(inv, patch, rows, g, rules_json):
    md = ['# Leftovers questline: policy review', '',
          'Checked against MASTER-PLAN owner decisions (karma/rep shared, faction membership shared, quest items duplicated, '
          'caps/XP to everyone, initiator picks dialogue, free roam + tether at story moments), `docs/quest-policies/rules.json` '
          'and the uncapped script findings. `rules.patch.json` proposes only additions, using the 16 existing kinds. '
          'Game-wide blockers are not re-derived here: see `internal questline notes`, `towns.md`, `ncr.md`. NOTE: those were written against an older L5; since then SetPlayerControls, SetFactionRank, SetPerk, ObjectState, AddTopic, AddNote, RefAddItem and others are captured and applied (tables.py / gaps.md have the current status), so their "missing" claims for those no longer hold.', '',
          'Scope note: VMS12/37/43/44/46/57 are also in the `factions` inventory (primary owner there). Their rows here are a second opinion; '
          'if `factions` and this lane disagree, the `factions` patch wins unless L9 decides otherwise.', '',
          '## Verdict per owner decision', '',
          '| Decision | Status in this questline | Evidence |', '|---|---|---|']
    f = g['functions']

    def cnt(fn, cat=None):
        return sum(e['count'] for k, e in f.items() if e['fn'].lower() == fn and (cat is None or e['cat'] == cat))
    md += [
        '| Karma shared | covered by ShareKarmaRep (RewardKarma %d lines); capture and apply both supported | gaps.json |' % cnt('rewardkarma'),
        '| Faction reputation shared | covered (AddReputation %d lines, VMS03/VMS04/VMS43/VMS46 among them); supported | gaps.json |' % cnt('addreputation'),
        '| Faction membership shared | **No player AddToFaction/RemoveFromFaction in this questline** (%d player-targeted lines). The %d NPC faction swaps (VMS43 Edwards ghoul/NCR flip x%d, VMS46 x%d) are captured as SetFactionRank; their AI effect on a client is unverified | gaps.json (ACTOR rows) |' % (
            cnt('addtofaction', 'S-STAT') + cnt('removefromfaction', 'S-STAT'),
            cnt('addtofaction', 'ACTOR') + cnt('removefromfaction', 'ACTOR') + cnt('setfactionrank', 'ACTOR'),
            sum(e['quests'].get('VMS43', 0) for e in f.values() if e['fn'].lower() in ('addtofaction', 'removefromfaction') and e['cat'] == 'ACTOR'),
            sum(e['quests'].get('VMS46', 0) for e in f.values() if e['fn'].lower() in ('addtofaction', 'removefromfaction') and e['cat'] == 'ACTOR')),
        '| Quest items duplicated | AddItem to player is captured; Recipient=All is L4 policy. Hand-ins (RemoveItem %d lines, 83 in VMS44) need RemoveItemPerOwner (proposed); NPC/container AddItem (%d lines) is captured as RefAddItem | gaps.json |' % (cnt('removeitem', 'P-INV'), cnt('additem', 'ACTOR')),
        '| Caps/XP to everyone | RewardXP %d lines supported; caps are AddItem Caps001 (REWARD) | gaps.json |' % cnt('rewardxp'),
        '| Initiator picks dialogue | holds for topic conversations; breaks for the NPC-forced scenes and menus listed in dialogue.md | dialogue.md |',
        '| Free roam, tether at story moments | These are side quests; almost all are free roam. One tether is proposed (VMS06 Vault22e). VMS01 already has a rules.json PartyTether. PartyTether has no capture/apply (missing/missing; session-level) | gaps.json, rules.patch.json |',
    ]
    md += ['', '## Barriers, tethers, cinematics, reward routing', '']
    md += [
        '- **Barriers at story moments.** None proposed. Every quest here is a shared side quest; a Barrier (all-players-pass) is only right for per-player scenes (chargen). '
        'CGTutorial is the one chargen-adjacent quest: it is a 28-stage tutorial driven by per-player actions, so `PerPlayerScene` (not Barrier) is proposed, with the open question of whether its stage should advance for non-acting players (unknown).',
        '- **Tethers.** VMS06 only (cell Vault22e, `gather: wait`). VMS01 (REPCONN launch) already carries a PartyTether in rules.json; its cell names are not in the extracted forms, so none is re-proposed. '
        'Open-world destinations (VMS02/VMS05 Primm and Goodsprings, VMS13 Westside, VMS22 sign photographs) are map markers: no tether proposed.',
        '- **Who controls cinematics.** The initiator of the dialogue/activator drives it. Control locks: VMS46 info end scripts (disablePlayerControls x%d) and VMS01 nvRepconLaunchButton (Disable L47, Enable L119). '
        'Proposed SetPlayerControls scope=all with a host-effect release and 120 s timeout. SetPlayerControls capture/apply exists (L5.md Round 9); the release rule and timeout are rule-level and unverified.' % cnt('disableplayercontrols'),
        '- **Reward routing.** XP, karma, reputation, items and notes follow the owner table. Open owner question (perks): VMS46 stage 60 `Player.AddPerk KhanTrick` (proposed RewardAll include perks) and VMS05 `player.RemovePerk WhiskeyRose` '
        '(companion perk follows the recruiter, CompanionOwner already in rules.json for VMS05). Notes (AddNote x%d: VMS02 Primm petitions, VMS03, VMS05, VMS46 recipe) are captured as AddNote; owner says rewards go to every player.' % cnt('addnote'),
        '- **Kill choices.** VMS03 Helios One tower: ControlPanelHeliosTower L115-119 KillActor x5 TestTrooper refs plus the quest script `IgnacioRivasREF.KillActor FantasticREF` (%d KillActor lines in total). KillActor is captured; apply is partial (needs the console exec line). VMS44 info 1482E9 end: Logan StartCombat Player (hostile NPC).' % cnt('killactor'),
        '- **Companions.** VMS05 has `RoseOfSharonCassidyREF.SetPlayerTeammate 0` (dismissal on hit/death path, dep obj RoseofSharonCassidy L80) and `GetPlayerTeammate` reads; CompanionOwner is already in rules.json for VMS05 but not captured/applied.',
        '- **Fast travel / saves.** VMS03 Helios control panel `EnableFastTravel 0` x2 (FastTravelLock already in rules.json); VMS06 elevator `AutoSave` (SaveSuppress already in rules.json).',
        '- **Dead content.** VMS26 (Jailhouse Rock) has 6 empty stages, no objective text and no references anywhere in scripts or the questgraph; its only content is 8 GetIsID greeting INFOs. No rule is proposed; treat as vestigial (unknown whether the NPCs exist in a cell).',
    ]
    md += ['', '## Per quest', '', '| Quest | Tier | Group | Existing rule kinds | Derived kinds | Proposed additions |', '|---|---|---|---|---|---|']
    for r in rows:
        md.append('| %s %s | %s | %s | %s | %s | %s |' % (r['edid'], '(%s)' % r['name'] if r['name'] else '', r['tier'], r['group'],
                                                       ', '.join(r['existing']) or '(no entry)', ', '.join(r['derived']) or '-',
                                                       ', '.join(r['added']) or '-'))
    md += ['', '## Quests with no rules.json entry', '',
           'rules.json only has entries for the T3/T4 policy quests. Every quest here with `has_rules_entry=false` in inventory.json '
           'gets a `new-quest-entry` in the patch when the derived rules are non-empty. VMS26 and VMS01REPCONGhouls derive nothing.', '',
           '## Proposed param extensions (need an L9/L4 decision before they are consumed)', '',
           '- `RewardAll.include = ["perks"]`: perk rewards (KhanTrick) go to every player.',
           '', '## Gaps in the rule vocabulary (no new kind is proposed)', '',
           '- NPC-forced conversation (StartConversation / Say / SayTo from object scripts): ForcedDialogue exists as an Effect but has no rule kind; InitiatorDialogue is the closest.',
           '- Object-state changes: SetDestroyed / SetOpenState / Lock are ObjectState now; Activate and MarkForDelete have no Effect type.',
           '- AddTopic is captured (AddTopic); the per-INFO said-once flag (SayOnceFlag) is not.']
    return '\n'.join(md) + '\n'





# --------------------------------------------------------------------------------------------------------------------
# dialogue
# --------------------------------------------------------------------------------------------------------------------
BRANCH_RE = re.compile(r'(?im)\b(setstage|completequest|failquest|stopquest|startquest)\b')
FACTION_RE = re.compile(r'(?im)\b(addtofaction|removefromfaction|setfactionrank|addreputation|rewardkarma)\b')
RECRUIT_RE = re.compile(r'(?im)\bsetplayerteammate\b')
BARTER_RE = re.compile(r'(?im)\bshow\w*menu\b')
FORCED_RE = re.compile(r'(?im)\bstartconversation\b')
CTRL_RE = re.compile(r'(?im)\b(disableplayercontrols|enablefasttravel)\b')
ITEM_COND = {'GetItemCount', 'GetEquipped'}


def info_checks(i, names=None):
    """Return list of skill/speech checks for an INFO."""
    out = []
    if i.get('speech_challenge') not in (None, 'none'):
        out.append('speech_challenge=%s' % i['speech_challenge'])
    sp = i.get('skill_perk')
    for c in i['conditions']:
        if c['func'] in ('GetActorValue', 'GetBaseActorValue', 'GetPermanentActorValue') and isinstance(c.get('p1'), int):
            out.append('%s %s %s %s' % (AV_NAMES.get(c['p1'], 'AV%d' % c['p1']), c['op'], c['value'], c['func']))
    if sp:
        out.append('skill=%s' % ((names.name(sp) if names else None) or sp))
    return out


def classify_info(i):
    tags = []
    scripts = ' '.join((i.get(k) or {}).get('source', '') for k in ('begin', 'end'))
    if info_checks(i):
        tags.append('check')
    if scripts and BRANCH_RE.search(scripts):
        tags.append('branch')
    if scripts and FACTION_RE.search(scripts):
        tags.append('faction')
    if scripts and RECRUIT_RE.search(scripts):
        tags.append('recruit')
    if scripts and BARTER_RE.search(scripts):
        tags.append('menu')
    if scripts and FORCED_RE.search(scripts):
        tags.append('forced')
    if scripts and CTRL_RE.search(scripts):
        tags.append('control')
    if any(c['func'] in ITEM_COND and c.get('run_on') in ('target', 'reference') for c in i['conditions']):
        tags.append('player-inventory-cond')
    return tags


def spec_step(names, i):
    topic = i['topic']
    d = names.dials.get(topic, {})
    line = i['responses'][0]['text'] if i['responses'] else ''
    return {'id': 'pick_p1_%s' % i['ref'].split(':')[1].lower(), 'action': 'pick', 'actor': 'p1', 'topic': topic,
            'topic_edid': d.get('edid'), 'prompt': i.get('prompt') or d.get('name'), 'info': i['ref'],
            'line': line[:40], 'expect': {'converged': True}}


def build_dialogue(ctx, names, inv):
    rows = collections.defaultdict(list)
    per_quest = {}
    for iq in inv['quests']:
        edid = iq['edid']
        q = ctx['by_edid'][edid]
        ins = ctx['infos'].get(q['ref'], [])
        cnt = collections.Counter()
        for i in ins:
            tags = classify_info(i)
            for t in tags:
                cnt[t] += 1
            for t in tags:
                rows[t].append((edid, i, tags))
        per_quest[edid] = cnt
    return rows, per_quest


def dialogue_md(ctx, names, inv, rows, per_quest):
    md = ['# Leftovers questline: dialogue notes (L8 spec style)', '',
          'Generated by `questlines/leftovers/gen.py` from `out/questdata/infos.jsonl.gz` / `dials.json.gz`. L8 rules '
          '(`docs/dialogue-design.md`): the initiator picks, everyone sees the line and result, result scripts run once on the host. '
          'Skill-index names: only Speech (43) is verified in this repo; other AV numbers are printed raw.', '']
    tot_infos = sum(len(ctx['infos'].get(ctx['by_edid'][e]['ref'], [])) for e in C.QUESTS)
    md += ['%d INFOs belong to the %d quests of this questline (VMS03, VMS43 and VMS46 hold most of them; VMS01, VMS04-VMS06, VMS09, VMS09a, VMS13 and CGTutorial have none, '
           'their conversations live in dialogue container quests owned by other lanes).' % (tot_infos, len(C.QUESTS)), '',
           '## Counts by pattern', '', '| Pattern | INFOs | meaning |', '|---|---:|---|']
    meaning = {'check': 'speech / skill / perk gate (per-initiator check)', 'branch': 'result script changes quest state (SetStage/Start/Stop/Complete/Fail)',
               'faction': 'faction-changing: AddToFaction/RemoveFromFaction/SetFactionRank/AddReputation/RewardKarma',
               'recruit': 'companion recruit/dismiss (SetPlayerTeammate)', 'menu': 'opens a barter/repair/etc. menu (service sub-session)',
               'forced': 'NPC forces a conversation (StartConversation)', 'control': 'locks controls or fast travel',
               'player-inventory-cond': 'condition on the player\'s inventory/equipment'}
    for k in ['check', 'branch', 'faction', 'recruit', 'menu', 'forced', 'control', 'player-inventory-cond']:
        md.append('| %s | %d | %s |' % (k, len(rows.get(k, [])), meaning[k]))

    def table(tag, limit):
        out = ['', '| quest | INFO | topic prompt | line | detail |', '|---|---|---|---|---|']
        seen = 0
        for edid, i, tags in sorted(rows.get(tag, []), key=lambda x: (x[0], x[1]['ref'])):
            d = names.dials.get(i['topic'], {})
            prompt = (i.get('prompt') or d.get('name') or '')[:50].replace('|', '/')
            line = (i['responses'][0]['text'] if i['responses'] else '')[:55].replace('|', '/')
            sc = ' '.join((i.get(k) or {}).get('source', '') for k in ('begin', 'end'))
            if tag == 'check':
                det = '; '.join(info_checks(i, names))[:70]
            else:
                det = ' '.join(sorted(set(m.lower() for m in re.findall(
                    r'(?i)\b(setstage|completequest|failquest|stopquest|startquest|addtofaction|removefromfaction|setfactionrank|addreputation|'
                    r'rewardkarma|setplayerteammate|show\w*menu|startconversation|disableplayercontrols|enablefasttravel)\b', sc))))[:70]
            out.append('| %s | %s | %s | %s | %s |' % (edid, i['ref'].split(':')[1], prompt, line, det))
            seen += 1
            if seen >= limit:
                break
        if len(rows.get(tag, [])) > limit:
            out.append('| ... | | | | %d more in the data |' % (len(rows[tag]) - limit))
        return out
    md += ['', '## Speech / skill checks', 'Per-initiator: the option exists or succeeds per the initiator\'s skill; quest consequences and XP are shared.']
    md += table('check', 40)
    md += ['', '## Choices that branch the questline', 'The initiator\'s pick sets the quest state for the whole party (host runs the end script once).']
    md += table('branch', 40)
    md += ['', '## Companion recruits', 'SetPlayerTeammate is not captured (CompanionOwner missing/missing). Owner decision pending (default: recruiter).']
    md += table('recruit', 20)
    md += ['', '## Faction-changing lines', 'Reputation and karma are shared (supported). Membership changes (AddToFaction/RemoveFromFaction/SetFactionRank) are not captured.']
    md += table('faction', 40)
    md += ['', '## Conversations the initiator-picks model breaks (or strains)', '']
    breaks = []
    nf = len(rows.get('forced', []))
    breaks.append('1. **NPC-forced scenes (%d INFOs with StartConversation; object scripts VMS09 FestusREF.StartConversation SSHQFestusLOS1/Addendum1, VMS46 VRRCAndersRef.StartConversation Player, '
                  'VMS43 RaulREF.StartConversation EdwardsREF, VMS37 CraigBooneREF.SayTo player ...).** No player picks to start them; L8 has no initiator for a host-triggered scene. '
                  'Proposal: ForcedDialogue with initiator = the party member nearest the NPC (min GetDistance over the party), the others spectate. VMS09 also runs a GetSecondsPassed timer around the Festus lines.' % nf)
    nm = len(rows.get('menu', []))
    breaks.append('2. **Barter / service menus (%d INFOs; VMS46 Great Khans armorer 1589EB/1589EF x5 ShowBarterMenu).** '
                  'Menu opens only for the initiator (service sub-session, dialogue-design 7.2); other players cannot trade in parallel.' % nm)
    ni = len(rows.get('player-inventory-cond', []))
    breaks.append('3. **Inventory-gated hand-ins (%d INFOs condition on the player\'s inventory/equipment; VMS44 alone does 83 RemoveItem).** Evaluated for the initiator only. '
                  'With QuestItemDup every player holds a copy, so the gate is satisfied, but RemoveItem removes only from the initiator (RemoveItemPerOwner), leaving duplicates. Decide whether a hand-in removes party-wide.' % ni)
    nc = len(rows.get('control', []))
    breaks.append('4. **Control locks inside dialogue end scripts (%d INFOs, all VMS46).** Lock must follow the conversation for all players; today SetPlayerControls has no capture or apply.' % nc)
    breaks.append('5. **Reputation-gated greetings (GetReputationThreshold reads in VMS03 x48 and VMS46 x27, shared) and GetIsSex reads (VMS46 x9).** Shared reputation is fine. '
                  'GetIsSex is evaluated on the initiator: a mixed-sex party sees different options depending on who talks (by design: initiator decides).')
    breaks.append('6. **Speech checks with a high-skill spectator.** The initiator may pass or fail where a spectator would differ; by design the initiator\'s skill decides (L8 scenario dlg_trudy_speech_fail_host_high shows the shape). '
                  'Checks in this questline are concentrated in VMS22 (Speech/Lie options with Mr. House), VMS43 and VMS46.')
    breaks.append('7. **Sign-photograph quest VMS22 (77 INFOs, 50 quest-variable sets).** The photograph goals are world actions, not dialogue; who delivers the final hand-in is the initiator by default. Whether each photo counts for the party depends on how the quest-variable writes are captured (SetScriptVar polling, host order): unknown.')
    md += breaks
    md += ['', '## Scenario stubs (L8 `pick` steps, generated from the data)', '',
           'These are step objects in the format of `plugin/dialogue/scenarios/*.json`; they are NOT yet runnable scenarios (no setup / talk steps, no expected state) '
           'and have not been executed.', '']
    stubs = []
    for tag in ('check', 'branch', 'faction', 'recruit'):
        pick = [x for x in rows.get(tag, []) if x[1].get('prompt') or names.dials.get(x[1]['topic'], {}).get('name')]
        for edid, i, tags in pick[:2]:
            stubs.append({'quest': edid, 'tag': tag, 'step': spec_step(names, i)})
    md += ['```json', json.dumps(stubs, indent=1, ensure_ascii=False), '```', '']
    md += ['## Quest-by-quest INFO pattern counts', '', '| quest | INFOs | ' + ' | '.join(['check', 'branch', 'faction', 'recruit', 'menu', 'forced', 'control']) + ' |',
           '|---|---:|' + '---:|' * 7]
    for iq in inv['quests']:
        if iq['dialogue']['infos']:
            c = per_quest[iq['edid']]
            md.append('| %s | %d | %s |' % (iq['edid'], iq['dialogue']['infos'], ' | '.join(str(c.get(k, 0)) for k in
                                                                                       ['check', 'branch', 'faction', 'recruit', 'menu', 'forced', 'control'])))
    return '\n'.join(md) + '\n', stubs


# --------------------------------------------------------------------------------------------------------------------
# blockers + status
# --------------------------------------------------------------------------------------------------------------------
def top_blockers(g, inv, patch):
    f = g['functions']

    def cnt(fn, cat=None, eff=None):
        return sum(e['count'] for e in f.values() if e['fn'].lower() == fn and (cat is None or e['cat'] == cat))

    def where(fn, cat=None, n=3):
        for e in f.values():
            if e['fn'].lower() == fn and (cat is None or e['cat'] == cat):
                return '; '.join(loc_str(l) for l in e['locations'][:n])
        return ''
    hz = g['hazards']

    def hz_n(k):
        return hz.get(k, {}).get('count', 0)
    items = [
        ('Companion Rose of Sharon Cassidy: recruit state is invisible to sync (CompanionOwner missing/missing)',
         'VMS05 dep obj RoseofSharonCassidy runs SetPlayerTeammate 0 (%d line) and its branches read GetPlayerTeammate; L5.md lists SetPlayerTeammate as still open (no teammate Effect). '
         'Same game-wide gap as factions.md #1 (VNPCFollowers). Her WhiskeyRose perk removal (%d) is a captured player SetPerk, but it fires only if the dismissal does. e.g. %s' %
         (cnt('setplayerteammate'), cnt('removeperk', 'P-STAT'), where('setplayerteammate')), 'blocker'),
        ('NPC-forced conversations have no initiator (ForcedDialogue missing/missing)',
         '%d StartConversation/Say/SayTo lines: VMS09 Festus (SSHQFestusLOS1/Addendum1, with a GetSecondsPassed timer), VMS46 Anders `StartConversation Player` in an info end script, VMS43 Raul/Edwards, VMS37 Boone, VMS12 Cook-Cook. '
         'The host runs them for the host player only. e.g. %s' %
         (cnt('startconversation') + cnt('sayto') + cnt('say'), where('startconversation', n=3)), 'high'),
        ('Teleports: Vault 22 elevator MoveTo x57 and REPCONN moves (MoveRef apply is partial)',
         '%d moveto lines: VMS06 V22ElevatorDoor (player MoveTo; PartyWarp is already in rules.json, but an L5.md Round 9 note says an object-script MoveTo outside a capture scope is dropped, or attributed to the quest cause when a SetStage nests; current status unknown) and VMS01 REPCONN JasonBright x4 (NPC). Cross-cell apply is Unsupported. e.g. %s' %
         (cnt('moveto'), where('moveto', n=3)), 'high'),
        ('Control locks are captured and applied, but who gets them and when they release is unverified',
         '%d Disable/EnablePlayerControls lines: VMS46 fade-to-black info end scripts (x5), VMS01 REPCONN launch button (disable L47, enable L119), VMS05 enable. SetPlayerControls is live-proven for the local player (L5.md Round 8); '
         'the Recipient default and the proposed host-effect release with a 120 s timeout are rule-level and not verified. Correction to factions.md #2, which still says missing/missing. e.g. %s' %
         (cnt('disableplayercontrols') + cnt('enableplayercontrols'), where('disableplayercontrols')), 'high'),
        ('Menus run on the host only (ShowMessage, ShowBarterMenu: no Effect)',
         '%d showmessage (CGTutorial x27 tutorial hints, VMS01 REPCONN terminal and death messages x10, VMS06 elevator) and %d showbartermenu (VMS46 Great Khans armorer). Spectators see nothing; only the initiator can trade. e.g. %s' %
         (cnt('showmessage'), cnt('showbartermenu'), where('showmessage', n=2)), 'medium'),
        ('Scripted kills: Helios One test troopers and the VMS03 quest script (KillActor apply partial)',
         '%d KillActor/kill lines (VMS03 ControlPanelHeliosTower L115-119 x5 TestTrooper, quest script L81 IgnacioRivasREF.KillActor FantasticREF; VMS05 Cass `kill`). Capture is supported, apply needs the console exec line. e.g. %s' %
         (cnt('killactor') + cnt('kill'), where('killactor', n=3)), 'medium'),
        ('Activate / MarkForDelete have no Effect (world actions on the host only)',
         '%d Activate lines (VMS01 REPCONN doors and launch button x8, VMS06, VMS02, VMS03, VMS04) and %d MarkForDelete (VMS02 PrimmSergeantLee). Activate is listed as still open in L5.md. e.g. %s' %
         (cnt('activate'), cnt('markfordelete'), where('activate', n=3)), 'medium'),
        ('Fast-travel lock and save suppression are not captured or applied',
         '%d EnableFastTravel (VMS03 Helios panel, SetFastTravelLock missing/missing) and %d AutoSave (VMS06 elevator; RequestSave is captured, apply and SaveSuppress are missing). e.g. %s' %
         (cnt('enablefasttravel'), cnt('autosave'), where('enablefasttravel')), 'medium'),
        ('Perk rewards and NPC faction swaps are captured but their routing / client-side effect is unverified',
         'VMS46 stage 60 `Player.AddPerk KhanTrick` (SetPerk Recipient routing; owner question open). VMS43 %d AddToFaction + %d RemoveFromFaction on Edwards and VMS46 %d: captured as SetFactionRank, '
         'but whether a client NPC changes allegiance in AI terms is unknown. e.g. %s' %
         (sum(e['quests'].get('VMS43', 0) for e in f.values() if e['fn'].lower() == 'addtofaction' and e['cat'] == 'ACTOR'),
          sum(e['quests'].get('VMS43', 0) for e in f.values() if e['fn'].lower() == 'removefromfaction' and e['cat'] == 'ACTOR'),
          sum(e['quests'].get('VMS46', 0) for e in f.values() if e['fn'].lower() in ('addtofaction', 'removefromfaction') and e['cat'] == 'ACTOR'),
          where('addperk', 'P-STAT')), 'medium'),
        ('Quest variables are synced by polling, not per write, and carry most of the branching',
         'SetScriptVar is captured by a snapshot diff (L5.md). %d quest-variable writes: VMS22 x50 (sign photographs), VMS43 x26, VMS46 x25, VMS02 x22, VMS03 x34. A variable flipped and flipped back inside one frame is lost; ordering is host order. e.g. %s' %
         (cnt('set questvar'), where('set questvar', n=2)), 'medium'),
        ('Dead and tutorial content: VMS26 is a stub, CGTutorial is per-player UI',
         'VMS26 has 6 empty stages, no objective text and no script references; only 8 GetIsID greeting INFOs. CGTutorial (28 stages, 29 menu/message lines, timers) advances on per-player actions; whether its stage should be shared is unknown. See policy_review.md.', 'low'),
    ]
    return items


def status_md(inv, sweep, g, patch, blockers, tests_out, cov):
    s = g['summary']
    inv_s = inv['summary']
    sw = sweep['summary']
    swept = sum(1 for q in sweep['quests'] for st in q['stages'])
    md = ['# Questline `leftovers`: status', '',
          'Generated by `questlines/leftovers/gen.py`; tests: `python -I questlines/leftovers/run_tests.py`.', '',
          'Scope: quests no other questline listed in its inventory.json `quests[]`. The lead named VMS12, VMS22, VMS26, VMS37, VMS43, VMS44, VMS46, VMS57; the coverage cross-check '
          '(`coverage_check.json`) then found the Goodsprings side quests VMS01-VMS06, VMS09, VMS09a, VMS13 (and VMS01REPCONGhouls) and CGTutorial uncovered too. '
          'VMS12/37/43/44/46/57 turned out to be in the `factions` inventory already (primary owner there): they are analysed here a second time and flagged `also_covered_by` in the sweep. '
          'VMS16b is an M1 Goodsprings system quest (template) and is not repeated. Game-wide blockers are referenced from `internal questline notes`, `towns.md` and `ncr.md`.', '',
          '## Counts', '',
          '| item | count |', '|---|---:|',
          '| quests in inventory | %d |' % inv_s['quests'],
          '| ... with stages | %d |' % inv_s['quests_with_stages'],
          '| ... dialogue / system containers (no stages) | %d |' % (inv_s['quests'] - inv_s['quests_with_stages']),
          '| stages | %d |' % inv_s['stages'],
          '| stages in sweep.json | %d |' % swept,
          '| stages explicitly excluded | %d (0 stage-level exclusions; %d containers excluded as having no stages) |' % (0, len(sweep['excluded'])),
          '| objectives | %d |' % inv_s['objectives'],
          '| INFOs | %d |' % inv_s['infos'],
          '| classified findings | %d (mutating %d) |' % (s['findings'], s['non_read_findings']),
          '| rules.patch.json quests | %d |' % len(patch['quests']),
          '| coverage: stage-bearing quests / covered / uncovered / debug excluded | %d / %d / %d / %d |' % (cov['stage_bearing_quests'], cov['covered_count'], cov['uncovered_count'], len(cov['debug_excluded'])),
          '', '## Gaps by severity (mutating findings)', '', '| severity | occurrences | distinct functions |', '|---|---:|---:|']
    for sv in T.SEV_ORDER:
        md.append('| %s | %d | %d |' % (sv, s['severity_occurrences'][sv], s['severity_distinct_functions'][sv]))
    md += ['', '## Top 10 blockers for co-op (ranked)', '']
    for n, (title, detail, sev) in enumerate(blockers[:10], 1):
        md.append('%d. **[%s] %s.** %s' % (n, sev, title, detail))
    md += ['', '## What was and was not verified', '',
           '- Verified (offline, by tests): every EDID/FormID in inventory, sweep and patch resolves in out/questdata; sweep covers every stage; patch kinds are among the 16; every script function is mapped; mutation twins fail as required.',
           '- L5 drift: tables.py was rebuilt from plugin/hooks and internal lane notes Round 9. The factions/towns/ncr status docs predate it; their claims that SetPlayerControls, SetFactionRank, SetPerk, ObjectState (SetDestroyed/SetOpenState/Lock), AddTopic, AddNote, RefAddItem/RefRemoveItem are uncaptured no longer hold. docs/protocol.md section 6 still lists 33 Effect tags, effect.h has %d (test: full effect list equals synccore effect.h variant).' % len(T.EFFECTS_ALL),
           '- NOT verified: nothing was run in a game. Console command syntax (`set <hex>.<var> to n`, `completequest`) is unverified; L5 status is from reading source on 2026-10-16.',
           '- Unknown: which Vault 22 cells hold the VMS06 download scene (only Vault22e is extracted); VMS01 REPCONN cell names; whether NV:MP syncs host-owned NPC AI state (assumed for rows marked host-actor); whether CGTutorial stages should advance for non-acting players; whether VMS26 NPCs exist in any cell.',
           '', '## Test output', '', '```', tests_out.strip() or '(run `python -I questlines/leftovers/run_tests.py` and then `gen.py --status`)', '```', '']
    return '\n'.join(md)


# --------------------------------------------------------------------------------------------------------------------
def dump(path, obj):
    with open(path, 'w', encoding='utf-8', newline='\n') as f:
        json.dump(obj, f, indent=1, ensure_ascii=False)
        f.write('\n')


def write_text(path, text):
    with open(path, 'w', encoding='utf-8', newline='\n') as f:
        f.write(text)


COLLECTED = {}


def build_all():
    ctx = C.load()
    names = Names(ctx)
    order = C.load_order()
    for edid in C.QUESTS:
        COLLECTED[edid] = C.collect(ctx, ctx['by_edid'][edid])
    policies = json.load(open(os.path.join(REPO, 'docs', 'quest-policies', 'policies.json'), encoding='utf-8'))
    rules_json = json.load(open(os.path.join(REPO, 'docs', 'quest-policies', 'rules.json'), encoding='utf-8'))
    inv = build_inventory(ctx, names, order, {p['edid'] for p in policies}, rules_json)
    sweep = build_sweep(ctx, names, order, inv)
    g = build_gaps(ctx)
    patch, rows = build_policy(ctx, names, inv, rules_json)
    rows_d, per_quest = build_dialogue(ctx, names, inv)
    return ctx, names, inv, sweep, g, patch, rows, rules_json, rows_d, per_quest


def main(argv=None):
    global HERE
    argv = list(sys.argv[1:] if argv is None else argv)
    out_dir = HERE
    if '--out' in argv:
        out_dir = argv[argv.index('--out') + 1]
        os.makedirs(out_dir, exist_ok=True)
    ctx, names, inv, sweep, g, patch, rows, rules_json, rows_d, per_quest = build_all()
    dump(os.path.join(out_dir, 'inventory.json'), inv)
    dump(os.path.join(out_dir, 'sweep.json'), sweep)
    dump(os.path.join(out_dir, 'gaps.json'), g)
    write_text(os.path.join(out_dir, 'gaps.md'), gaps_md(g))
    dump(os.path.join(out_dir, 'rules.patch.json'), patch)
    write_text(os.path.join(out_dir, 'policy_review.md'), policy_md(inv, patch, rows, g, rules_json))
    dmd, stubs = dialogue_md(ctx, names, inv, rows_d, per_quest)
    write_text(os.path.join(out_dir, 'dialogue.md'), dmd)
    blockers = top_blockers(g, inv, patch)
    cov = K.build()
    dump(os.path.join(out_dir, 'coverage_check.json'), cov)
    dump(os.path.join(out_dir, 'blockers.json'), [{'rank': i + 1, 'severity': s, 'title': t, 'detail': d}
                                                for i, (t, d, s) in enumerate(blockers)])
    tests_out = ''
    tp = os.path.join(HERE, 'last_test_output.txt')
    if os.path.exists(tp):
        tests_out = open(tp, encoding='utf-8').read()
    if '--status' in argv:
        os.makedirs(os.path.dirname(DOCS_STATUS), exist_ok=True)
        write_text(DOCS_STATUS, status_md(inv, sweep, g, patch, blockers, tests_out, cov))
    s = g['summary']
    print('coverage: %d stage-bearing, %d covered, %d uncovered' % (cov['stage_bearing_quests'], cov['covered_count'], cov['uncovered_count']))
    print('inventory: %d quests, %d stages | sweep: %d stages | gaps: %d findings, unmapped %d | patch: %d quests | dialogue stubs %d' %
          (inv['summary']['quests'], inv['summary']['stages'], sweep['summary']['stages'], s['findings'], len(g['unmapped']),
           len(patch['quests']), len(stubs)))
    return 0


if __name__ == '__main__':
    sys.exit(main())
