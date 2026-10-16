#!/usr/bin/env python3
"""Generate every deliverable of the `factions` questline from out/questdata.

    python -I questlines/factions/gen.py [--status]

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

REPO = C.REPO
sys.path.insert(0, os.path.join(REPO, 'tools', 'coverage'))
import rules as RULES  # noqa: E402
import classify  # noqa: E402

DOCS_STATUS = os.path.join(REPO, 'docs', 'questlines', 'factions.md')
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
            'group': C.GROUP_OF[edid], 'theme': C.THEME.get(edid), 'kind': r['kind'], 'tier': r['tier'],
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
            'group': iq['group'], 'tier': r['tier'], 'tier_direct': r['tier_direct'], 'cats': r['cats'],
            'priority': q.get('priority'), 'flags': q.get('flags'), 'script': q.get('script'),
            'start_enabled': iq['start_enabled'],
            'setup': list(dict.fromkeys(base + qcmds)),
            'setup_unresolved': sorted(set(unresolved)),
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
        'milestone': 'Factions (Nellis / Camp Golf / NCR side, Mojave other, BoS, Boomers, Khans, Powder Gangers, Remnants, Followers)',
        'core': {q['edid']: q['name'] for q in qout if q['role'] == 'core'},
        'system_quests': [q['edid'] for q in qout if q['role'] == 'system'],
        'touched_quests': [{'edid': e, 'name': ctx['by_edid'][e].get('name'), 'stages': len(ctx['by_edid'][e]['stages']),
                            'why': sorted(w)} for e, w in sorted(touched.items())],
        'order_last': order_last,
        'excluded': excluded,
        'notes': [
            'Each stage is independent: the sweep reloads the autosave, runs `setup` in order, then `command`.',
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
            st = 'info'
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
    for eff in T.EFFECTS:
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
    md = ['# Factions questline: script gaps (capture / apply)', '',
          '_Generated by `questlines/factions/gen.py` from `out/questdata` (quest, stage, info begin/end and dependency-object scripts)._',
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
HV_CELLS = ['HiddenValley01', 'HiddenValley02', 'HiddenValleyBunker1', 'HiddenValleyBunker2']
CURATED = {
    'VMS55': [
        ('PartyTether', {'cells': HV_CELLS, 'gather': 'wait'},
         'Elder McNamara induction scene in Hidden Valley is story-critical; exactly which of the four Hidden Valley cells holds it is unknown, so all four are listed.'),
        ('ShareKarmaRep', {'faction_membership': True},
         'AddReputation RepNVBrotherhood at stages 100/101; if the owner wants BoS standing to include faction membership it is shared too. Param extension, not a new kind.'),
    ],
    'VMS55a': [
        ('PartyTether', {'cells': HV_CELLS, 'gather': 'wait'}, 'Report-back to Hardin completes BoS membership.'),
        ('ShareKarmaRep', {'faction_membership': True}, 'Stage 100 admits the Courier to the Brotherhood (vendor stock + standing): shared, party = one Courier.'),
    ],
    'VMS55b': [
        ('ShareKarmaRep', {'faction_membership': True}, 'Induction after the Black Mountain device is shared.'),
    ],
    'VDialogueHiddenValley': [
        ('SetPlayerControls', {'scope': 'all', 'release': 'host-effect', 'timeout_s': 120, 'masks': []},
         'Disable/EnablePlayerControls in infos 0A3CE0, 16329B, 15A304, 15A93D end scripts; a client that misses the release is soft-locked.'),
        ('FastTravelLock', {'scope': 'party'}, 'EnableFastTravel 0 in info 16329B end script.'),
    ],
    'VMS39': [('CompanionOwner', {'owner': 'recruiter'}, 'Boone companion quest; the trip needs the recruiter\'s companion.')],
    'VMS53': [('CompanionOwner', {'owner': 'recruiter'}, 'Boone companion quest; hand-in of the beret and wife rescue depend on the companion.')],
    'VMS54': [('CompanionOwner', {'owner': 'recruiter'}, 'Arcade companion quest ("With Arcade, convince the Remnants...").')],
    'VDialogueRemnants': [
        ('RewardAll', {'include': ['perks']},
         'player.AddPerk PowerArmorTraining (infos 15A75E, 15A75D, 15B397): perk is a quest reward, so every player gets it; owner has not ruled on perks, flagged in policy_review.md.'),
    ],
    'VMS46': [
        ('RewardAll', {'include': ['perks']}, 'Player.AddPerk KhanTrick at stage 60: reward routed to every player (open owner question).'),
        ('SetPlayerControls', {'scope': 'all', 'release': 'host-effect', 'timeout_s': 120, 'masks': []},
         'disablePlayerControls in infos 146F18, 146F0D, 146F04 end scripts (Aba Daba Honeymoon cutscene beats).'),
    ],
    'VNPCFollowers': [
        ('CompanionOwner', {'owner': 'recruiter'}, 'System quest that recruits / dismisses every companion (SetPlayerTeammate, companion perks).'),
        ('ShareKarmaRep', {'faction_membership': True}, 'Player.AddToFaction/RemoveFromFaction VBooneFaction on recruit/dismiss.'),
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
                           'tier': iq['tier'], 'questline': 'Factions (%s)' % iq['group'], 'rules': proposals}
        rows.append({'edid': edid, 'tier': iq['tier'], 'name': iq['name'], 'group': iq['group'], 'existing': sorted(have),
                     'derived': [r['kind'] for r in derived], 'added': [p['kind'] for p in proposals]})
    return {'schema': 1,
            'doc': 'Proposed additions to docs/quest-policies/rules.json (owned by L9). Only the 16 existing kinds are used; '
                   'params extensions (faction_membership, include) are noted per rule and need an L9/L4 decision.',
            'kinds_used': sorted({p['kind'] for e in patch.values() for p in e['rules']}),
            'quests': patch}, rows


def policy_md(inv, patch, rows, g, rules_json):
    md = ['# Factions questline: policy review', '',
          'Checked against MASTER-PLAN owner decisions (karma/rep shared, faction membership shared, quest items duplicated, '
          'caps/XP to everyone, initiator picks dialogue, free roam + tether at story moments), `docs/quest-policies/rules.json` '
          'and the uncapped script findings. `rules.patch.json` proposes only additions, using the 16 existing kinds.', '',
          '## Verdict per owner decision', '',
          '| Decision | Status in this questline | Evidence |', '|---|---|---|']
    f = g['functions']

    def cnt(fn, cat=None):
        return sum(e['count'] for k, e in f.items() if e['fn'].lower() == fn and (cat is None or e['cat'] == cat))
    md += [
        '| Karma shared | covered by ShareKarmaRep (RewardKarma %d lines, mostly dialogue end scripts); capture and apply both supported | gaps.json |' % cnt('rewardkarma'),
        '| Faction reputation shared | covered (AddReputation %d lines: NCR, Boomer, Brotherhood, Great Khans, Powder Gangers); supported | gaps.json |' % cnt('addreputation'),
        '| Faction membership shared | **NOT enforceable today**: player AddToFaction/RemoveFromFaction (%d lines, all VBooneFaction in this questline; Brotherhood induction itself is reputation + vendor stock, no player faction call) has no hook and no apply (SetFactionRank missing/missing). NPC faction swaps (%d lines: Hidden Valley hit squads, Remnants, Khan armor) are also uncaptured | gaps.json (S-STAT / ACTOR rows) |' % (cnt('addtofaction', 'S-STAT') + cnt('removefromfaction', 'S-STAT'), cnt('addtofaction', 'ACTOR') + cnt('removefromfaction', 'ACTOR') + cnt('setfactionrank', 'ACTOR')),
        '| Quest items duplicated | AddItem to player is captured; routing Recipient=All is L4 policy. NPC/container AddItem (%d lines) is not captured | gaps.json |' % cnt('additem', 'ACTOR'),
        '| Caps/XP to everyone | RewardXP %d lines supported; caps are AddItem Caps001 (REWARD) | gaps.json |' % cnt('rewardxp'),
        '| Initiator picks dialogue | holds for topic conversations; breaks for the cases listed in dialogue.md (forced StartConversation scenes, barter, hand-ins) | dialogue.md |',
        '| Free roam, tether at story moments | needs PartyTether, but PartyTether has no capture/apply (missing/missing) and only 4 questline-relevant cell EDIDs resolve in the extracted forms (Hidden Valley x4); other scene locations are map markers or cells that were not extracted | gaps.json, rules.patch.json |',
    ]
    md += ['', '## Barriers, tethers, cinematics, reward routing', '']
    md += [
        '- **Barriers at story moments.** The faction induction moments are shared, not per-player, so a Barrier (all-players-pass) is not proposed; '
        'a PartyTether is. Barrier remains right only for per-player scenes (chargen), none of which is in this questline.',
        '- **Tethers.** Proposed for VMS55/VMS55a (Hidden Valley cells, `gather: wait`). Companion trips (VMS39 Bitter Springs / Coyote Tail Ridge, VMS54 Remnants bunker, VMS49 Vault 22) are open-world map markers, not cells; '
        'PartyTether takes cells, so a marker-based tether needs an L4/L9 decision (unknown). VMS49 already carries PartyWarp markers.',
        '- **Who controls cinematics.** Every Disable/EnablePlayerControls in this questline sits inside a dialogue end script (VDialogueHiddenValley, VMS46) or an object script (DoctorUsanagi). '
        'The initiator drives the dialogue; SetPlayerControls scope=all with a host-effect release and a 120 s timeout failsafe is proposed. Capture/apply for SetPlayerControls is missing today, so a client could be left locked or unlocked incorrectly.',
        '- **Reward routing.** XP, karma, reputation, items and notes follow the owner table. Open owner questions: (1) perks granted to the player as quest rewards (PowerArmorTraining from Hidden Valley / Remnants, KhanTrick) - proposed RewardAll (every player), recruiter-only is the alternative; '
        '(2) companion perks (Boone Spotting, Arcade BetterHealing, Veronica ScribeAssistant) added with `player.AddPerk` while the companion is active - these follow the recruiter (CompanionOwner), not the party; '
        '(3) hand-in RemoveItem: RemoveItemPerOwner (handing player only) with QuestItemDup keeping the other players\' copies.',
        '- **Kill choices.** Hazard scan flags scripts with 3+ kill/startcombat lines (see gaps.md hazards table, `kill_everyone`). KillActor is captured; apply is partial (needs the console exec line).',
        '- **Companions.** Default owner = recruiter (open owner item). SetPlayerTeammate is not captured at all; the five companion quests here (VMS39, VMS49, VMS53, VMS54, VNPCFollowers) cannot be co-op correct until CompanionOwner is captured and applied.',
    ]
    md += ['', '## Per quest', '', '| Quest | Tier | Group | Existing rule kinds | Derived kinds | Proposed additions |', '|---|---|---|---|---|---|']
    for r in rows:
        md.append('| %s %s | %s | %s | %s | %s | %s |' % (r['edid'], '(%s)' % r['name'] if r['name'] else '', r['tier'], r['group'],
                                                       ', '.join(r['existing']) or '(no entry)', ', '.join(r['derived']) or '-',
                                                       ', '.join(r['added']) or '-'))
    md += ['', '## Quests with no rules.json entry', '',
           'rules.json only has entries for the T3/T4 policy quests. Every quest in this questline with `has_rules_entry=false` in inventory.json '
           'gets a `new-quest-entry` in the patch when the derived rules are non-empty.', '',
           '## Proposed param extensions (need an L9/L4 decision before they are consumed)', '',
           '- `ShareKarmaRep.faction_membership = true`: player AddToFaction/SetFactionRank follows the owner decision "membership shared".',
           '- `RewardAll.include = ["perks"]`: perk rewards go to every player.',
           '', '## Gaps in the rule vocabulary (no new kind is proposed)', '',
           '- Companion perks: `CompanionOwner` could carry `perks: recruiter-only`; today it only names the owner.',
           '- Marker-based tether for open-world destinations (see Tethers).',
           '- Faction membership capture is a hook gap (L5), not a rule gap.']
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
    md = ['# Factions questline: dialogue notes (L8 spec style)', '',
          'Generated by `questlines/factions/gen.py` from `out/questdata/infos.jsonl.gz` / `dials.json.gz`. L8 rules '
          '(`docs/dialogue-design.md`): the initiator picks, everyone sees the line and result, result scripts run once on the host. '
          'Skill-index names: only Speech (43) is verified in this repo; other AV numbers are printed raw.', '']
    tot_infos = sum(len(ctx['infos'].get(ctx['by_edid'][e]['ref'], [])) for e in C.QUESTS)
    md += ['%d INFOs belong to the %d quests of this questline (the faction stories mostly live in dialogue container quests such as '
           'VDialogueHiddenValley, VFreeformNellis, VDialogueRemnants, VMS46, VNPCFollowers).' % (tot_infos, len(C.QUESTS)), '',
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
    breaks.append('1. **NPC-forced scenes (%d INFOs with StartConversation, plus object scripts like HVIntercomREF.StartConversation player GREETING, '
                  'EdgarHardin StartConversation player HVHardinSoundsPlayerOut).** No player picks to start them; L8 has no initiator for a host-triggered scene. '
                  'Proposal: ForcedDialogue with initiator = the party member nearest the NPC (min GetDistance over the party), the others spectate.' % nf)
    nm = len(rows.get('menu', []))
    breaks.append('2. **Barter / service menus (%d INFOs; VDialogueHiddenValley armorer 15FD06 x12, Great Khans armorer 1589EF/1589EB, NCRCF 155E04/05).** '
                  'Menu opens only for the initiator (service sub-session, dialogue-design 7.2); other players cannot trade in parallel.' % nm)
    ni = len(rows.get('player-inventory-cond', []))
    breaks.append('3. **Inventory-gated hand-ins (%d INFOs condition on the player\'s inventory/equipment).** Evaluated for the initiator only. '
                  'A party member who holds the quest item but is not the initiator cannot satisfy the line; with QuestItemDup every player holds a copy, so the gate is satisfied but RemoveItem removes only from the initiator (RemoveItemPerOwner), leaving duplicates. Decide whether hand-in removes party-wide.' % ni)
    nc = len(rows.get('control', []))
    breaks.append('4. **Control locks inside dialogue end scripts (%d INFOs).** Lock must follow the conversation for all players; today SetPlayerControls has no capture or apply.' % nc)
    nr = len(rows.get('recruit', []))
    breaks.append('5. **Companion recruit/dismiss lines (%d INFOs plus VNPCFollowers quest script).** The initiator recruits; other players\' companions are separate. Dismiss lines run `player.RemoveFromFaction` / `player.RemovePerk` on the host player ref, which under "player" mapping must mean the recruiter.' % nr)
    breaks.append('6. **Reputation-gated greetings (GetReputationThreshold, 284 condition reads, shared) and GetPlayerTeammate-conditioned lines.** Shared values are fine; GetPlayerTeammate must mean "recruiter has a companion" (initiator-evaluated).')
    breaks.append('7. **Speech checks with a high-skill spectator.** The initiator may pass or fail where a spectator would differ; by design the initiator\'s skill decides (L8 scenario dlg_trudy_speech_fail_host_high shows the shape).')
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
        ('Companion recruit/dismiss is invisible to sync (CompanionOwner missing/missing)',
         'VNPCFollowers is the one quest that recruits and dismisses every companion: %d SetPlayerTeammate lines, %d GetPlayerTeammate reads, and the Boone/Arcade/Veronica quests '
         '(VMS39, VMS53, VMS54, VMS49) all depend on it. Nothing captures a recruit, so non-initiators never see the companion follow. e.g. %s' %
         (cnt('setplayerteammate'), cnt('getplayerteammate'), where('setplayerteammate')), 'blocker'),
        ('Control locks have no capture/apply (SetPlayerControls missing/missing)',
         '%d Disable/EnablePlayerControls lines in Hidden Valley dialogue end scripts, VMS46 and the Dr Usanagi object script; a missed release soft-locks a client. e.g. %s' %
         (cnt('disableplayercontrols') + cnt('enableplayercontrols'), where('disableplayercontrols')), 'blocker'),
        ('Companion faction marker and companion perks are player-targeted and uncaptured (SetFactionRank, SetPerk missing/missing)',
         '%d Player.AddToFaction/RemoveFromFaction VBooneFaction lines and %d player.AddPerk/RemovePerk lines (Spotting, BetterHealing, ScribeAssistant, ...). '
         'Owner decision says membership is shared; for perks the owner has not ruled (companion perks should follow the recruiter). e.g. %s' %
         (cnt('addtofaction', 'S-STAT') + cnt('removefromfaction', 'S-STAT'), cnt('addperk', 'P-STAT') + cnt('removeperk', 'P-STAT'),
          where('removeperk', 'P-STAT', 2)), 'blocker'),
        ('BoS induction rewards land in NPC containers, which L5 does not capture',
         'VMS55a/VMS55b stage 100 stock the Torres vendor container (VendorContainerTorresREF.additemhealthpercent ArmorPowerT45d ...): %d NPC item adds and %d NPC item removes in the questline '
         '(VMS55a %d, VMS55b %d adds). Clients would likely see an unstocked vendor (unverified). e.g. %s' %
         (cnt('additemhealthpercent', 'ACTOR') + cnt('additem', 'ACTOR'), cnt('removeitem', 'ACTOR'),
          sum(e['quests'].get('VMS55a', 0) for e in f.values() if e['fn'].lower() == 'additemhealthpercent' and e['cat'] == 'ACTOR'),
          sum(e['quests'].get('VMS55b', 0) for e in f.values() if e['fn'].lower() == 'additemhealthpercent' and e['cat'] == 'ACTOR'),
          where('additemhealthpercent', 'ACTOR', 2)), 'high'),
        ('Teleports and scripted moves (MoveRef apply is partial; no PartyWarp applier)',
         '%d moveto lines: VMS49 Vault 22 elevator MoveTo x57 (player), Primm Powder Ganger reactions x15, companion moves in VNPCFollowers, Hidden Valley NPC moves. e.g. %s' %
         (cnt('moveto'), where('moveto', n=3)), 'high'),
        ('Spawns on the host only (PlaceAtMe has no Effect type)',
         '%d placeatme lines (NCRCF explosions, Nellis ant explosions, Camp Searchlight aura). e.g. %s' % (cnt('placeatme'), where('placeatme')), 'high'),
        ('Quest-reward perks to the player (SetPerk missing/missing, owner decision open)',
         'PowerArmorTraining (Hidden Valley infos 1633C0/1638D8, Remnants 15A75E/15A75D/15B397) and KhanTrick (VMS46 stage 60). e.g. %s' % where('addperk', 'P-STAT', 3), 'high'),
        ('Dialogue topics unlocked by AddTopic are not synced (no AddTopic effect; SayOnceFlag missing/missing)',
         '%d AddTopic findings (Camp Golf infos etc.); joiners and spectators miss unlocked topics and say-once state.' % cnt('addtopic'), 'medium'),
        ('Object state changes with no Effect (SetDestroyed/SetOpenState/Lock/Unlock/Activate)',
         'Ranger Station radios (VMS52, VMSRSGlobalCommBreakdown), Nellis generators/arrays, bottle cap press (VMS51): %d setdestroyed, %d setopenstate, %d lock/unlock, %d activate.' %
         (cnt('setdestroyed'), cnt('setopenstate'), cnt('lock') + cnt('unlock'), cnt('activate')), 'medium'),
        ('Menus run on the host only (ShowMessage, ShowBarterMenu)',
         '%d showmessage and %d showbartermenu lines (barter in Hidden Valley, Great Khans, NCRCF, Nellis); spectators see nothing, only the initiator can trade.' %
         (cnt('showmessage'), cnt('showbartermenu')), 'medium'),
        ('Fast-travel lock and autosave are not captured (SetFastTravelLock, SaveSuppress missing)',
         '%d EnableFastTravel and %d AutoSave lines (VMS49 Vault 22, Hidden Valley intercom scene).' % (cnt('enablefasttravel'), cnt('autosave')), 'medium'),
    ]
    return items


def status_md(inv, sweep, g, patch, blockers, tests_out):
    s = g['summary']
    inv_s = inv['summary']
    sw = sweep['summary']
    swept = sum(1 for q in sweep['quests'] for st in q['stages'])
    md = ['# Questline `factions`: status', '',
          'Generated by `questlines/factions/gen.py`; tests: `python -I questlines/factions/run_tests.py`.', '',
          'Scope: Nellis / Camp Golf / NCR side quests and remaining Mojave side quests (`side-nellis-camp-golf-ncr.md`, `side-mojave-other.md`), '
          'plus faction content not owned by another lane: Brotherhood of Steel (Hidden Valley), Boomers (Nellis), Great Khans (VMS46 + generic), '
          'Powder Gangers, Enclave Remnants, Followers of the Apocalypse, and the companion system quest. White Legs lives entirely in Honest Hearts (Q-honesthearts) and is cross-referenced only.', '',
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
          '', '## Gaps by severity (mutating findings)', '', '| severity | occurrences | distinct functions |', '|---|---:|---:|']
    for sv in T.SEV_ORDER:
        md.append('| %s | %d | %d |' % (sv, s['severity_occurrences'][sv], s['severity_distinct_functions'][sv]))
    md += ['', '## Top 10 blockers for co-op (ranked)', '']
    for n, (title, detail, sev) in enumerate(blockers[:10], 1):
        md.append('%d. **[%s] %s.** %s' % (n, sev, title, detail))
    md += ['', '## What was and was not verified', '',
           '- Verified (offline, by tests): every EDID/FormID in inventory, sweep and patch resolves in out/questdata; sweep covers every stage; patch kinds are among the 16; every script function is mapped; mutation twins fail as required.',
           '- NOT verified: nothing was run in a game. Console command syntax (`set <hex>.<var> to n`, `completequest`) is unverified; L5 status is from reading source on 2026-10-16.',
           '- Unknown: which of the four Hidden Valley cells holds each BoS scene; marker-based tether semantics; whether NV:MP syncs host-owned NPC AI state (assumed for rows marked host-actor).',
           '', '## Test output', '', '```', tests_out.strip() or '(run `python -I questlines/factions/run_tests.py` and then `gen.py --status`)', '```', '']
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
    dump(os.path.join(out_dir, 'blockers.json'), [{'rank': i + 1, 'severity': s, 'title': t, 'detail': d}
                                                for i, (t, d, s) in enumerate(blockers)])
    tests_out = ''
    tp = os.path.join(HERE, 'last_test_output.txt')
    if os.path.exists(tp):
        tests_out = open(tp, encoding='utf-8').read()
    if '--status' in argv:
        os.makedirs(os.path.dirname(DOCS_STATUS), exist_ok=True)
        write_text(DOCS_STATUS, status_md(inv, sweep, g, patch, blockers, tests_out))
    s = g['summary']
    print('inventory: %d quests, %d stages | sweep: %d stages | gaps: %d findings, unmapped %d | patch: %d quests | dialogue stubs %d' %
          (inv['summary']['quests'], inv['summary']['stages'], sweep['summary']['stages'], s['findings'], len(g['unmapped']),
           len(patch['quests']), len(stubs)))
    return 0


if __name__ == '__main__':
    sys.exit(main())
