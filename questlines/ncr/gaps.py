"""gaps.json + gaps.md builder: every script function/effect in the NCR questline's stage, topic, quest and dependency scripts,
mapped to a synccore Effect type and its L5 capture/apply status.

python -I questlines/ncr/gaps.py
"""
import collections
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common  # noqa: E402
import fntable  # noqa: E402

SEV_ORDER = ['blocker', 'high', 'medium', 'low']
KILL_EVERYONE_MIN = 4   # >= N Kill commands in one script = a "kill everyone" cleanup
TIMER_TOKENS = ('gamedayspassed',)


def status_of(ev):
    r = {'supported': 0, 'n/a': 0, 'partial': 1, 'missing': 2}
    w = max(r[ev['capture']], r[ev['apply']])
    return {0: 'supported', 1: 'partial', 2: 'missing'}[w]


def script_sources(D, edid, role):
    """Yield (where, script_dict, script_label) for one quest incl. dependency-object scripts."""
    q = D.by_edid[edid]
    dial_edid = {d['ref']: d.get('edid') for d in D.dials}
    for where, sc, se in D.quest_scripts(q):
        label = se or f'{edid}:{where}'
        if where.startswith('info '):
            ref = 'FalloutNV.esm:' + where.split()[1]
            info = next((i for i in D.infos.get(q['ref'], []) if i['ref'].endswith(where.split()[1])), None)
            if info:
                label = f'{edid} topic {dial_edid.get(info["topic"]) or info["topic"]} info {where.split()[1]} {where.split()[2]}'
        yield where, sc, label, 'own'
    extra = D.extra()
    touched = extra['touched'].get(q['ref']) if extra.get('touched') else None
    touched = None if touched is None else set(touched) | {t['ref'] for o in q['objectives'] for t in o['targets']}
    seen = set()
    for dr in q.get('dep_refs', ()):
        if touched is not None and dr not in touched:
            continue
        for label, sc in classify_dep(dr, extra):
            if id(sc) in seen:
                continue
            seen.add(id(sc))
            yield f'dep {label}', sc, f'dep {label}', 'dep'


def classify_dep(dr, extra):
    import classify
    return classify.dep_scripts(dr, extra)


def collect(D):
    scope = [(e, 'core') for e in common.CORE] + [(e, 'support') for e in common.SUPPORT] + [(e, 'system') for e in common.SYSTEM]
    events, unclassified, scripts_scanned = [], [], 0
    seen_dep_global = set()    # a dependency-object script touched by several quests is counted once (first quest in scope order)
    owners = {}                # dependency-object label -> every swept quest that touches it (the "owner" an ambient scope would need)
    for edid, role in scope:
        if role == 'system':
            continue
        for where, sc, label, origin in script_sources(D, edid, role):
            if origin == 'dep':
                owners.setdefault(label, set()).add(edid)
    for edid, role in scope:
        q = D.by_edid[edid]
        qvars = {v['name'].lower() for v in (q.get('script_data') or {}).get('vars', [])}
        seen_dep_label = set()
        for where, sc, label, origin in script_sources(D, edid, role):
            if origin == 'dep':
                if role == 'system':
                    continue    # container quests: their dependency objects are covered through the core quests that touch them
                key = (where, sc.get('compiled_size'), len(sc.get('source', '')))
                if key in seen_dep_label or key in seen_dep_global:
                    continue
                seen_dep_label.add(key)
                seen_dep_global.add(key)
            lv = {v['name'].lower() for v in sc.get('vars', [])} | (qvars if origin == 'own' else set())
            evs, unc = fntable.scan_script(sc['source'], lv, D)
            scripts_scanned += 1
            scope = fntable.scope_of(where, origin)
            evs = [fntable.apply_scope(e, scope) for e in evs]
            kills = [e for e in evs if e['fn'] in ('kill', 'killactor')]
            if len(kills) >= KILL_EVERYONE_MIN:
                for e in kills:
                    e['hazards'] = e['hazards'] + ['kill-everyone']
            low = sc['source'].lower()
            for n, raw in enumerate(sc['source'].replace('\r', '').split('\n'), 1):
                if any(t in raw.lower() for t in TIMER_TOKENS) and not raw.strip().startswith(';'):
                    evs.append({'scope': scope, 'fn': 'gamedayspassed', 'subject': None, 'line': n, 'text': raw.strip()[:160], 'conditional': True, 'effect': None,
                                'capture': 'n/a', 'apply': 'n/a', 'kind': 'read', 'hazards': ['timer'], 'severity': 'info',
                                'note': 'in-game-days timer: GameDaysPassed is a host global (SetGlobal diff); fine as long as only the host runs the quest script'})
            for e in evs:
                e.update({'quest': edid, 'role': role, 'where': where, 'script': label, 'origin': origin,
                          'owners': sorted(owners.get(label, {edid})) if origin == 'dep' else [edid]})
            for u in unc:
                u.update({'quest': edid, 'where': where, 'script': label})
            events += evs
            unclassified += unc
    return events, unclassified, scripts_scanned


def aggregate(events):
    groups = collections.OrderedDict()
    for e in events:
        key = (e['fn'], e['effect'], e['capture'], e['apply'], e['severity'], 'ambient' in e['hazards'])
        g = groups.setdefault(key, {'fn': e['fn'], 'effect': e['effect'], 'capture': e['capture'], 'apply': e['apply'], 'severity': e['severity'],
                                    'kind': e['kind'], 'note': e['note'], 'hazards': set(), 'count': 0, 'locations': [], 'by_quest': collections.Counter()})
        g['hazards'].update(e['hazards'])
        g['count'] += 1
        g['by_quest'][e['quest']] += 1
        g['locations'].append({'quest': e['quest'], 'where': e['where'], 'script': e['script'], 'scope': e['scope'], 'line': e['line'],
                               'subject': e.get('subject'), 'conditional': e['conditional'], 'text': e['text']})
    out = []
    for g in groups.values():
        g['hazards'] = sorted(g['hazards'])
        g['by_quest'] = dict(sorted(g['by_quest'].items(), key=lambda kv: -kv[1]))
        g['status'] = status_of(g)
        out.append(g)
    return out


def build(D=None):
    D = D or common.Data()
    events, unclassified, nscripts = collect(D)
    groups = aggregate(events)
    gaps = [g for g in groups if g['status'] != 'supported' and g['severity'] != 'info']
    supported = [{'fn': g['fn'], 'effect': g['effect'], 'count': g['count']} for g in groups if g['status'] == 'supported']
    sev_rank = {s: i for i, s in enumerate(SEV_ORDER)}
    gaps.sort(key=lambda g: (sev_rank.get(g['severity'], 9), -g['count'], g['fn']))
    by_sev = collections.OrderedDict((s, {'functions': 0, 'occurrences': 0}) for s in SEV_ORDER)
    by_status = collections.Counter()
    for g in gaps:
        by_sev[g['severity']]['functions'] += 1
        by_sev[g['severity']]['occurrences'] += g['count']
        by_status[g['status']] += g['count']
    haz = collections.defaultdict(lambda: {'occurrences': 0, 'functions': set(), 'quests': collections.Counter()})
    for g in gaps:
        for h in g['hazards']:
            haz[h]['occurrences'] += g['count']
            haz[h]['functions'].add(g['fn'])
            for q, n in g['by_quest'].items():
                haz[h]['quests'][q] += n
    # kill-everyone is tagged per event; recount from events
    ke = [e for e in events if 'kill-everyone' in e['hazards']]
    haz['kill-everyone'] = {'occurrences': len(ke), 'functions': {'kill'}, 'quests': collections.Counter(e['quest'] for e in ke)}
    hazards = {h: {'occurrences': v['occurrences'], 'functions': sorted(v['functions']), 'quests': dict(v['quests'].most_common())}
               for h, v in sorted(haz.items(), key=lambda kv: -kv[1]['occurrences'])}
    unc = collections.OrderedDict()
    for u in unclassified:
        g = unc.setdefault(u['fn'], {'fn': u['fn'], 'count': 0, 'locations': []})
        g['count'] += 1
        g['locations'].append({'quest': u['quest'], 'where': u['where'], 'script': u['script'], 'line': u['line'], 'text': u['text']})
    by_quest = collections.defaultdict(lambda: collections.Counter())
    for g in gaps:
        for q, n in g['by_quest'].items():
            by_quest[q][g['severity']] += n
    scope_counts = collections.Counter(e['scope'] for e in events)
    amb = [e for e in events if 'ambient' in e['hazards']]
    amb_scripts = collections.Counter((e['script'], e['scope']) for e in amb)
    amb_src = {}
    for e in amb:
        a = amb_src.setdefault((e['script'], e['scope']), {'script': e['script'], 'scope': e['scope'], 'owners': e['owners'], 'dropped': collections.Counter(), 'lines': []})
        a['dropped'][e['fn']] += 1
        a['lines'].append(e['line'])
    ambient = {'events_by_scope': dict(scope_counts), 'dropped_events': len(amb),
               'dropped_by_fn': dict(collections.Counter(e['fn'] for e in amb).most_common()),
               'scripts_with_dropped_events': len(amb_scripts),
               'top_scripts': [{'script': k[0], 'scope': k[1], 'dropped_events': n, 'owners': amb_src[k]['owners']} for k, n in amb_scripts.most_common(25)],
               'sources': [{'script': a['script'], 'scope': a['scope'], 'owners': a['owners'], 'dropped': dict(a['dropped']), 'lines': sorted(set(a['lines']))}
                           for a in sorted(amb_src.values(), key=lambda a: -sum(a['dropped'].values()))],
               'rule': 'L5 opens a capture scope only at TESQuest::SetStage and TESTopicInfo::RunResultScript; quest GameMode blocks and object scripts '
                       'are ambient and their mutations are dropped (CaptureHub::droppedNoScope). Quest variables/globals are polled; SetStage opens its own scope.'}
    return {
        'schema': 1, 'slug': common.SLUG,
        'method': 'questlines/ncr/gaps.py scans quest scripts, stage scripts, INFO begin/end result scripts and dependency-object scripts '
                  '(classify.dep_scripts, same touched filter as risk.json) of the questline quests; function table in questlines/ncr/fntable.py',
        'status_sources': ['synccore/include/fnvcoop/effect.h', 'plugin/hooks/engine_hooks.cpp', 'plugin/hooks/apply_remote.cpp',
                           'plugin/hooks/effect_json.cpp', 'internal lane notes'],
        'scope': {'core': common.CORE, 'support': common.SUPPORT, 'system': common.SYSTEM},
        'summary': {'scripts_scanned': nscripts, 'script_events': len(events), 'supported_occurrences': sum(s['count'] for s in supported),
                    'gap_functions': len(gaps), 'gap_occurrences': sum(g['count'] for g in gaps), 'by_severity': by_sev,
                    'by_status_occurrences': dict(by_status), 'unclassified_functions': len(unc),
                    'unclassified_occurrences': sum(u['count'] for u in unc.values())},
        'ambient': ambient,
        'by_quest': {q: dict(c) for q, c in sorted(by_quest.items(), key=lambda kv: -sum(kv[1].values()))},
        'hazards': hazards,
        'gaps': gaps,
        'supported': sorted(supported, key=lambda s: -s['count']),
        'unclassified': sorted(unc.values(), key=lambda u: -u['count']),
    }


def group_locations(locs):
    g = collections.OrderedDict()
    for l in locs:
        g.setdefault((l['quest'], l['script'], l['where']), []).append(l['line'])
    return g


def to_md(gp):
    s = gp['summary']
    L = ['# NCR questline: capture/apply gap list', '',
         f'Generated by `questlines/ncr/gaps.py` (do not hand-edit). Scanned {s["scripts_scanned"]} scripts '
         f'({len(gp["scope"]["core"])} core + {len(gp["scope"]["support"])} support + {len(gp["scope"]["system"])} system quests, '
         f'including INFO result scripts and the dependency-object scripts the quests touch). '
         f'{s["script_events"]} function/effect occurrences: {s["supported_occurrences"]} fully supported by L5 today, '
         f'{s["gap_occurrences"]} in {s["gap_functions"]} gap functions, {s["unclassified_occurrences"]} unclassified (listed honestly at the end).', '',
         'Status = worst of L5 capture and apply (`supported` / `partial` / `missing`; `n/a` = nothing to replicate). Severity is our judgement of the '
         'co-op damage (blocker > high > medium > low). Source of truth for statuses: `plugin/hooks/*.cpp` + `internal lane notes` (checked against code).', '',
         '## Gaps by severity', '', '| severity | gap functions | occurrences |', '|---|---:|---:|']
    for sv, v in s['by_severity'].items():
        L.append(f'| {sv} | {v["functions"]} | {v["occurrences"]} |')
    L += ['', '## Co-op hazards (gap occurrences carrying the hazard)', '', '| hazard | occurrences | functions | quests |', '|---|---:|---|---|']
    for h, v in gp['hazards'].items():
        qs = ', '.join(f'{q} {n}' for q, n in list(v['quests'].items())[:5])
        L.append(f'| {h} | {v["occurrences"]} | {", ".join(v["functions"][:6])} | {qs} |')
    am = gp['ambient']
    L += ['', '## Ambient scripts (no capture scope): the mutations L5 drops', '',
          am['rule'], '',
          f'Of {s["script_events"]} occurrences, {am["events_by_scope"].get("quest-script", 0)} are in quest GameMode blocks and {am["events_by_scope"].get("object", 0)} in dependency-object scripts '
          f'(the rest: {am["events_by_scope"].get("stage", 0)} stage scripts, {am["events_by_scope"].get("info", 0)} INFO result scripts, which ARE in scope). '
          f'{am["dropped_events"]} occurrences in {am["scripts_with_dropped_events"]} scripts would be captured in scope but are dropped here.', '',
          '| dropped function | occurrences |', '|---|---:|'] + [f'| {k} | {v} |' for k, v in am['dropped_by_fn'].items()] +          ['', '| script (top 25) | scope | owner quest(s) an ambient scope would use | dropped occurrences |', '|---|---|---|---:|'] + [f'| {t["script"]} | {t["scope"]} | {", ".join(t["owners"])} | {t["dropped_events"]} |' for t in am['top_scripts']] + ['', 'The full owner map is `gaps.json` -> `ambient.sources`.']
    L += ['', '## By quest (gap occurrences)', '', '| quest | blocker | high | medium | low |', '|---|---:|---:|---:|---:|']
    for q, c in gp['by_quest'].items():
        L.append(f'| {q} | {c.get("blocker", 0)} | {c.get("high", 0)} | {c.get("medium", 0)} | {c.get("low", 0)} |')
    L += ['', '## Gaps (every unsupported function, with exact locations)', '',
          'Location format: `script label` `where`: line numbers. `dep ...` = dependency-object script (label is the object EDID as in risk.json). '
          'Low/medium gaps list at most 25 script groups; the full list is in `gaps.json`.', '']
    for g in gp['gaps']:
        L.append(f'### {g["fn"]}  [{g["severity"]}]  x{g["count"]}')
        L.append('')
        L.append(f'- Effect: `{g["effect"]}`; capture **{g["capture"]}**, apply **{g["apply"]}**, status **{g["status"]}**'
                 + (f'; hazards: {", ".join(g["hazards"])}' if g['hazards'] else ''))
        L.append(f'- Note: {g["note"]}')
        L.append('- Quests: ' + ', '.join(f'{q} {n}' for q, n in list(g['by_quest'].items())[:8]))
        grp = group_locations(g['locations'])
        cap = None if g['severity'] in ('blocker', 'high') else 25
        for i, ((q, script, where), lines) in enumerate(grp.items()):
            if cap is not None and i >= cap:
                L.append(f'- ... and {len(grp) - cap} more script groups (see gaps.json)')
                break
            L.append(f'  - `{script}` ({where}): L{",".join(str(x) for x in lines)}')
        L.append('')
    L += ['## Unclassified commands (NOT mapped; status unknown)', '']
    if gp['unclassified']:
        for u in gp['unclassified']:
            first = u['locations'][0]
            L.append(f'- `{u["fn"]}` x{u["count"]} e.g. `{first["script"]}` L{first["line"]}: `{first["text"][:90]}`')
    else:
        L.append('(none)')
    L += ['', '## Fully supported (for reference)', '', ', '.join(f'{x["fn"]} x{x["count"]}' for x in gp['supported'][:40]), '']
    return '\n'.join(L)


def main():
    gp = build()
    common.jdump(gp, os.path.join(common.HERE, 'gaps.json'), indent=None)
    with open(os.path.join(common.HERE, 'gaps.md'), 'w', encoding='utf-8', newline='\n') as f:
        f.write(to_md(gp))
    s = gp['summary']
    print(f'gaps: {s["scripts_scanned"]} scripts, {s["script_events"]} events, {s["gap_functions"]} gap functions / {s["gap_occurrences"]} occurrences, '
          f'severity {dict((k, v["occurrences"]) for k, v in s["by_severity"].items())}, unclassified {s["unclassified_functions"]}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
