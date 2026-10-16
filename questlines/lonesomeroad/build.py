"""Generate every machine-made deliverable of the Lonesome Road questline into questlines/lonesomeroad/.

    python -I questlines/lonesomeroad/build.py [--out DIR]

Outputs: inventory.json, sweep.json, gaps.json, gaps.md, rules.patch.json, policy_review.md, dialogue.md
Inputs are read-only (out/questdata, out/coverage, docs/quest-policies). Deterministic: same inputs -> same bytes.
"""
import argparse
import collections
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import analysis  # noqa: E402
import common  # noqa: E402
import curated  # noqa: E402
import moments  # noqa: E402

SETSTAGE_RE = re.compile(r'(?i)\bsetstage\s+(\w+)\s+(\d+)')
STARTQ_RE = re.compile(r'(?i)\b(startquest|forceactivequest)\s+(\w+)')
ITEM_RE = re.compile(r'(?im)^\s*(?:(\w+)\.)?(additem|removeitem|additemns|equipitem|additemhealthpercent)\s+(\w+)')
TOGGLE_RE = re.compile(r'(?im)^\s*(?:(\w+)\.)?(enable|disable)\b(?!\w)')
SUBJ_RE = re.compile(r'(?m)\b([A-Za-z_]\w*)\.(?=[A-Za-z])')
OBJ_RE = re.compile(r'(?im)\bsetobjective(displayed|completed)\s+(\w+)\s+(\d+)\s+([01])')


def dump(path, obj):
    with open(path, 'w', encoding='utf-8', newline='\n') as f:
        json.dump(obj, f, indent=1, ensure_ascii=False, sort_keys=False)
        f.write('\n')


def write(path, text):
    with open(path, 'w', encoding='utf-8', newline='\n') as f:
        f.write(text.rstrip('\n') + '\n')


def nm(data, ref):
    if not ref:
        return {'ref': ref}
    f = data.forms.get(ref) or data.refnames.get(ref) or {}
    o = {'ref': ref, 'rid': common.rid(ref) if ref.startswith(common.PLUGIN) else None, 'sig': f.get('sig'),
         'edid': f.get('edid') or data.ref_edid.get(ref), 'name': f.get('name')}
    b = f.get('base')
    if b:
        bf = data.forms.get(b) or data.refnames.get(b) or {}
        o['base'] = {'ref': b, 'edid': bf.get('edid'), 'sig': bf.get('sig')}
    return o


def cmd_resolve(data, tpl):
    """Resolve {Q:EDID} placeholders to runtime hex FormIDs."""
    def sub(m):
        kind, edid = m.group(1), m.group(2)
        ref = data.resolve_edid(edid)
        if not ref or not ref.startswith(common.PLUGIN):
            raise KeyError('cannot resolve %s in %s' % (edid, tpl))
        return common.rid(ref)
    return re.sub(r'\{([QR]):(\w+)\}', sub, tpl)


# ------------------------------------------------------------------------------------------------------ inventory
def stage_setters(data, items):
    """(quest edid lower, stage) -> [{script, line, text}] over every script in scope."""
    out = collections.defaultdict(list)
    started = collections.defaultdict(list)
    for it in items:
        for n, line in common.script_lines(it['source']):
            for q, st in SETSTAGE_RE.findall(line):
                out[(q.lower(), int(st))].append({'script': it['label'], 'line': n, 'text': common.clean(data, line, 120)})
            for fn, q in STARTQ_RE.findall(line):
                started[(q.lower(), fn.lower())].append({'script': it['label'], 'line': n, 'text': common.clean(data, line, 120)})
    return out, started


def cond_deps(data, q):
    """Quest-state conditions of a quest, its stages, objectives and INFOs, aggregated per (target quest, function)."""
    acc = collections.Counter()
    conds = list(q['conditions'])
    for s in q['stages']:
        for e in s['entries']:
            conds += e['conditions']
    for o in q['objectives']:
        for t in o['targets']:
            conds += t['conditions']
    for i in data.infos_by_quest.get(q['ref'], []):
        conds += i['conditions']
    for c in conds:
        if c['func'] in common.classify.QUEST_DEP_FUNCS and isinstance(c.get('p1'), str):
            tq = data.by_ref.get(c['p1'])
            if tq and tq['ref'] != q['ref']:
                acc[(tq['edid'], c['func'])] += 1
    return [{'quest': k[0], 'func': k[1], 'count': n} for k, n in sorted(acc.items())]


def build_inventory(data, items):
    setters, started = stage_setters(data, items)
    edges_in = collections.defaultdict(list)
    edges_out = collections.defaultdict(list)
    for e in data.graph['edges']:
        edges_in[e['dst']].append(e)
        edges_out[e['src']].append(e)
    qs = []
    for q in sorted(data.quests, key=lambda q: q['ref']):
        edid = q['edid']
        rk = data.risk[q['ref']]
        pol = data.policies.get(edid, {})
        role = curated.ROLE[edid]
        infos = data.infos_by_quest.get(q['ref'], [])
        eff = (data.effects_native['quests'].get(edid) or {}).get('stages', {})
        stages = []
        for s in sorted(q['stages'], key=lambda s: s['index']):
            ent = s['entries']
            e = eff.get(str(s['index']), {'unconditional': [], 'conditional': []})
            stages.append({
                'index': s['index'],
                'flags': sorted({f for x in ent for f in x['flags']}),
                'log': next((x.get('text') for x in ent if x.get('text')), None),
                'has_script': any(x.get('script') for x in ent),
                'set_by': setters.get((edid.lower(), s['index']), []),
                'effects_unconditional': sorted({x['type'] for x in e['unconditional']}),
                'effects_conditional': sorted({x['type'] for x in e['conditional']}),
                'risk_findings': [dict(cat=f['cat'], fn=f['fn'], level=f['level'], text=f['text'])
                                  for f in rk['stage_findings'] if f['where'] == 'stage %d' % s['index']],
            })
        topics = sorted({i['topic'] for i in infos})
        qs.append({
            'edid': edid, 'ref': q['ref'], 'formid': common.rid(q['ref']), 'name': q.get('name'),
            'kind': role[0], 'summary': role[1], 'flags': q.get('flags'), 'priority': q.get('priority'),
            'start_enabled': 'start_enabled' in (q.get('flags') or []),
            'script': nm(data, q['script']) if q.get('script') else None,
            'stages': stages,
            'objectives': [{'index': o['index'], 'text': o.get('text'), 'targets': [nm(data, t['ref']) for t in o['targets'] if t['ref']]}
                           for o in sorted(q['objectives'], key=lambda o: o['index'])],
            'prerequisites': {
                'graph_inbound': [{'from': e['src'], 'kind': e['kind'], 'strength': e['strength'], 'count': e['count'],
                                   'evidence': [{'where': x['where'], 'text': common.clean(data, x['text'], 100)} for x in e['evidence'][:3]]} for e in edges_in.get(edid, [])],
                'quest_conditions': cond_deps(data, q),
                'started_by': [x for fn in ('startquest', 'forceactivequest') for x in started.get((edid.lower(), fn), [])
                               if fn == 'startquest'],
            },
            'dependents': [{'to': e['dst'], 'kind': e['kind'], 'strength': e['strength'], 'count': e['count']} for e in edges_out.get(edid, [])],
            'dialogue': {'infos': len(infos), 'topics_listed': len(q.get('topics', [])), 'distinct_topics_in_infos': len(topics),
                         'scripted_infos': sum(1 for i in infos if i.get('begin') or i.get('end')),
                         'speakers': sorted({i['speaker'] for i in infos if i.get('speaker')})},
            'risk': {'tier': rk['tier'], 'tier_direct': rk['tier_direct'], 'score': rk['score'], 'cats': rk['cats'],
                     'policy_tier': pol.get('tier'), 'policy_score': pol.get('score'), 'questline': pol.get('questline')},
            'rules_json': [x['kind'] for x in (data.rules['quests'].get(edid) or {}).get('rules', [])] or None,
        })
    return {'schema': 1, 'questline': curated.QUESTLINE, 'plugin': common.PLUGIN, 'load_order_prefix': common.PREFIX_HEX,
            'counts': {'quests': len(qs), 'stages': sum(len(q['stages']) for q in qs), 'objectives': sum(len(q['objectives']) for q in qs),
                       'infos': sum(q['dialogue']['infos'] for q in qs),
                       'quests_with_stages': sum(1 for q in qs if q['stages'])},
            'quests': qs}


# ------------------------------------------------------------------------------------------------------ sweep
def build_sweep(data, inv, items):
    by = {q['edid']: q for q in inv['quests']}
    quests, excluded = [], []
    all_text = {}
    for it in items:
        for q in it['quests']:
            all_text.setdefault(q, []).append(it)
    def ok(name):
        return name.lower() in ('self', 'player') or not common.IDENT_RE.match(name) or data.resolve_edid(name) is not None

    for iq in inv['quests']:
        edid = iq['edid']
        q = data.by_edid[edid]
        if not iq['stages']:
            excluded.append({'quest': edid, 'formid': iq['formid'], 'reason': curated.NO_STAGE_REASON[edid]})
            continue
        rid_ = iq['formid']
        resets = [rid_] + [by[x]['formid'] for x in curated.RESET_EXTRA.get(edid, [])]
        stages = []
        for s in iq['stages']:
            ex = curated.STAGE.get((edid, s['index']), {})
            setup = []
            if not iq['start_enabled']:
                setup.append({'cmd': 'startquest %s' % rid_, 'verify': False})
            for c in ex.get('setup', []):
                setup.append(c)
            variants = []
            for v in ex.get('variants', []):
                variants.append({'name': v['name'], 'why': v['why'],
                                 'setup': [dict(c, cmd=cmd_resolve(data, c['cmd']), alt=c.get('alt')) for c in v['setup']]})
            if (edid, s['index']) in curated.EXCLUDED_STAGES:
                run_mode = 'excluded'
            else:
                run_mode = ex.get('run_mode', 'normal')
            stages.append({
                'index': s['index'], 'log': s['log'], 'flags': s['flags'], 'has_script': s['has_script'],
                'host': 'setstage %s %d' % (rid_, s['index']), 'host_edid': 'setstage %s %d' % (edid, s['index']),
                'wait': 'GetStageDone %s %d' % (rid_, s['index']),
                'setup': [dict(c, cmd=cmd_resolve(data, c['cmd'])) for c in setup], 'variants': variants,
                'run_mode': run_mode, 'note': ex.get('note'),
                'expect': {'effects_unconditional': s['effects_unconditional'], 'effects_conditional': s['effects_conditional']},
                'natural_trigger': [dict(x) for x in s['set_by']][:6],
                'findings': s['risk_findings'],
                'objective_effects': [],
            })
        # objective effects from stage scripts
        for st in stages:
            src = next((e.get('script') for sx in q['stages'] if sx['index'] == st['index'] for e in sx['entries'] if e.get('script')), None)
            if src and src.get('source'):
                for kind, qn, n, v in OBJ_RE.findall(src['source']):
                    st['objective_effects'].append({'kind': kind.lower(), 'quest': qn, 'objective': int(n), 'value': int(v)})
        # per-quest aggregates (same shape as m1_goodsprings.json)
        scr = all_text.get(edid, [])
        gl, items_, toggles, subj, setst = set(), {}, {}, set(), set()
        for it in scr:
            src = it['source']
            for who, fn, item in ITEM_RE.findall(src):
                key = (item, fn.lower(), (who or 'self').lower())
                items_[key] = items_.get(key, 0) + 1
            for who, fn in TOGGLE_RE.findall(src):
                toggles.setdefault((who or 'self'), set()).add(fn.lower())
            subj.update(x for x in SUBJ_RE.findall(src) if x.lower() not in ('player', 'playerref'))
            for qn, idx in SETSTAGE_RE.findall(src):
                if qn.lower() != edid.lower():
                    setst.add('%s %s' % (qn, idx))
        for it in scr:
            for g in data.d['globals']:
                if g.get('edid') and re.search(r'(?i)\b%s\b' % re.escape(g['edid']), it['source']):
                    gl.add(g['edid'])
        quests.append({
            'edid': edid, 'ref': q['ref'], 'runtime_id': rid_, 'name': q.get('name'), 'role': iq['kind'],
            'tier': iq['risk']['tier'], 'tier_direct': iq['risk']['tier_direct'], 'cats': iq['risk']['cats'], 'priority': q.get('priority'),
            'flags': q.get('flags'), 'script': q.get('script'), 'reset_ids': resets, 'start_enabled': iq['start_enabled'],
            'stages': stages,
            'objectives': [{'index': o['index'], 'text': o['text'], 'targets': o['targets']} for o in iq['objectives']],
            'globals': sorted(gl),
            'items': [{'item': i, 'fn': f, 'subject': w if ok(w) else '~unresolved~', 'occurrences': n} for (i, f, w), n in sorted(items_.items()) if ok(i)],
            'enable_disable': {w: sorted(f) for w, f in sorted(toggles.items()) if ok(w)},
            'script_subjects': sorted(x for x in subj if ok(x)),
            'unresolved_subjects': sum(1 for x in subj if not ok(x)) + sum(1 for w in toggles if not ok(w)),
            'setstage_other_quests': sorted(setst),
            'npcs': [nm(data, e) for e in iq['dialogue']['speakers']],
            'dialogue': iq['dialogue'],
        })
    probes = []
    for qn, prime, cmd, secs, mode, note in curated.PROBES:
        ref = data.resolve_edid(qn)
        qid = common.rid(ref)
        probes.append({'quest': qn, 'runtime_id': qid, 'prime': [dict(cmd=p.replace('{Q}', qid), verify=True) for p in prime],
                       'host': cmd.replace('{Q}', qid), 'observe_s': secs, 'run_mode': mode, 'note': note})
    info_probes = []
    for i in data.lr_infos:
        if not (i.get('begin') or i.get('end')):
            continue
        t = data.dials.get(i['topic'], {})
        eff = data.effects_native['infos'].get(i['ref'])
        info_probes.append({'info': i['ref'], 'rid': common.rid(i['ref']), 'quest': data.ref_edid.get(i['quest']),
                            'topic': t.get('edid'), 'topic_rid': common.rid(i['topic']) if i['topic'].startswith(common.PLUGIN) else None,
                            'flags': i.get('flags'), 'scripts': [k for k in ('begin', 'end') if i.get(k)],
                            'oracle_end': (eff or {}).get('end'), 'oracle_begin': (eff or {}).get('begin')})
    stage_total = sum(len(q['stages']) for q in quests)
    return {
        'milestone': 'Lonesome Road (DLC04)', 'plugin': common.PLUGIN, 'load_order_prefix': common.PREFIX_HEX,
        'core': {q['edid']: q['name'] for q in quests if q['role'] == 'story' and q['name']},
        'system_quests': [e['quest'] for e in excluded],
        'excluded_quests': excluded,
        'excluded_stages': [{'quest': k[0], 'stage': k[1], 'reason': v} for k, v in curated.EXCLUDED_STAGES.items()],
        'summary': {'quests_swept': len(quests), 'stages': stage_total,
                    'stages_isolate': sum(1 for q in quests for s in q['stages'] if s['run_mode'] == 'isolate'),
                    'stage_variants': sum(len(s['variants']) for q in quests for s in q['stages']),
                    'objectives': sum(len(q['objectives']) for q in quests), 'probes': len(probes), 'info_probes': len(info_probes)},
        'driver_requirements': [
            'goodsprings_sweep.py derives the console id as "%X" % int(ref.rsplit(":",1)[1],16), which DROPS the load-order prefix. '
            'DLC quests need quest.runtime_id (prefix 04). Patch proposal: questlines/lonesomeroad/driver_patch.md',
            'plan sort must put run_mode=isolate stages last, each in a fresh launch (cinematic locks, combat, StartQuest NVDLC04Ending).',
            'commands marked verify=true use `set <hexquest>.<var> to N`; not proven in this repo, alt gives the EDID form.',
            'probes[] and info_probes[] are new sections; the existing driver only reads quests[].stages[].',
        ],
        'quests': quests, 'probes': probes, 'info_probes': info_probes,
    }


# ------------------------------------------------------------------------------------------------------ rules patch
def build_rules_patch(data):
    existing = data.rules['quests']
    ops = {}
    for edid, spec in curated.RULES.items():
        ref = data.resolve_edid(edid)
        rk = data.risk.get(ref, {})
        cur = existing.get(edid)
        ops[edid] = {
            'action': 'replace' if cur else 'add',
            'tier': rk.get('tier'), 'questline': curated.QUESTLINE, 'curated': True,
            'rules': spec['rules'], 'rationale': spec['why'],
            'before': [x['kind'] for x in cur['rules']] if cur else None,
            'added_kinds': sorted({r['kind'] for r in spec['rules']} - ({x['kind'] for x in cur['rules']} if cur else set())),
            'removed_kinds': sorted(({x['kind'] for x in cur['rules']} if cur else set()) - {r['kind'] for r in spec['rules']}),
        }
    return {'schema': 1, 'target': 'docs/quest-policies/rules.json',
            'how_to_apply': 'for each entry: action=replace overwrites quests[EDID]; action=add inserts it. Owner of the file is L9 (this is a proposal).',
            'removed_bogus_markers': ['rLinkedRef'],
            'quests': ops, 'proposals': curated.PROPOSALS}


# ------------------------------------------------------------------------------------------------------ gaps.md
SEVS = ['critical', 'high', 'medium', 'low', 'info']


def gaps_md(g):
    s = g['summary']
    L = ['# Lonesome Road: script function and effect gaps', '',
         'Generated by `questlines/lonesomeroad/build.py` from `out/questdata` (scripts, stage/INFO result scripts, dependency object scripts). '
         'Do not edit by hand. Full locations: `gaps.json`.', '',
         '## Reading this', '',
         'Status vocabulary: **supported** (host capture and replica apply both exist), **partial** (Effect type exists but a half is missing), '
         '**missing** (no Effect type or hook), **host-only** (NPC/AI/animation state that only the host owns; guests see it through NV:MP actor '
         'replication, unverified), **read** (a query; player-referencing reads are `p-ref`), **presentation** (sound, music, image-space, FX).',
         'A *gap* is a use with status partial or missing. Severity: critical (soft-lock or story fork), high (shared story state forks), '
         'medium (per-player loss or story-tied presentation), low (cosmetic).', '',
         'L5 facts used: Role::Guest skips every quest-owned script (`Script::info.type == 1`) and drops mutators from object scripts; the host '
         'captures SetStage/objective/quest running/rep/karma/enable-disable/AddItem/RemoveItem/XP/Kill/Resurrect/MoveTo/AV commands and quest variables '
         'only; AddPerk/AddToFaction/SetFactionRank/Lock/UnLock/Activate/AddScriptPackage/StartCombat are block-only (dropped on guests, never recorded on '
         'the host); RemoteApplier has no branch for SetPerk, SetFactionRank, SetLock, SwapTexture, CompanionOwner, SetPlayerControls, '
         'ForcedDialogue; guest-originated activation intent is designed, not implemented (`internal lane notes` round 4).', '',
         '## Totals', '',
         '| item | count |', '|---|---:|',
         '| scripts analysed | %d |' % s['scripts']]
    for k, v in sorted(s['scripts_by_kind'].items()):
        L.append('| &nbsp;&nbsp;%s scripts | %d |' % (k, v))
    L += ['| object scripts not reachable from any quest (NPC/creature/trigger scripts) | %d |' % s['unreached_object_scripts'],
          '| function uses mapped | %d |' % s['function_uses_mapped'], '| distinct functions | %d |' % s['distinct_functions'], '']
    L += ['| status | uses |', '|---|---:|'] + ['| %s | %d |' % (k, v) for k, v in sorted(s['uses_by_status'].items(), key=lambda x: -x[1])] + ['']
    L += ['| gap severity (partial + missing uses) | uses |', '|---|---:|'] + ['| %s | %d |' % (k, s['gap_uses_by_severity'][k]) for k in SEVS] + ['']
    L += ['OnActivate blocks: %d, of which %d are gated on `IsActionRef Player` and %d of those mutate shared state '
          '(`gaps.json: onactivate_player_gated`). Every one is a guest-originated activation the host cannot see.' %
          (s['onactivate_blocks'], s['onactivate_player_gated'], s['onactivate_player_gated_mutating']), '']
    L += ['## Gap functions (partial or missing), worst first', '',
          '| fn | effect type | capture | apply | status | worst sev | uses | scripts | hazards | note |', '|---|---|---|---|---|---|---:|---:|---|---|']
    for f in g['functions']:
        if not any(st in ('partial', 'missing') for st in f['statuses']):
            continue
        st = ', '.join('%s %d' % (k, v) for k, v in sorted(f['statuses'].items(), key=lambda x: -x[1]))
        L.append('| `%s` | %s | %s | %s | %s | %s | %d | %d | %s | %s |' % (
            f['fn'], f['effect'] or '-', f['capture'], f['apply'], st, f['sev'], f['count'], f['scripts'], ', '.join(f['hazards']) or '-',
            (f['note'] or '').replace('|', '/')[:150]))
    L += ['', '## Exact locations (script, line) of every critical and high gap, first 8 per function', '']
    for f in g['functions']:
        locs = [l for l in f['locations'] if l['status'] in ('partial', 'missing') and l['sev'] in ('critical', 'high')]
        if not locs:
            continue
        L.append('### `%s` (%d critical/high gap uses of %d)' % (f['fn'], len(locs), f['count']))
        L.append('')
        for l in locs[:8]:
            L.append('- `%s` line %d [%s] `%s`' % (l['script'], l['line'], l['sev'], common.clip(l['text'], 90).replace('`', "'")))
        if len(locs) > 8:
            L.append('- ... %d more in gaps.json' % (len(locs) - 8))
        L.append('')
    L += ['## Co-op hazards (brief section 3)', '', '| hazard | uses | top functions | example locations |', '|---|---:|---|---|']
    for h, v in g['hazards'].items():
        ex = '; '.join('%s:%d' % (l['script'], l['line']) for l in v['locations'][:3])
        L.append('| %s | %d | %s | %s |' % (h, v['count'], ', '.join('%s %d' % kv for kv in list(v['functions'].items())[:4]), ex))
    L += ['', '## Script entry-point blocks', '', '| script kind | blocks |', '|---|---|']
    for k, v in g['blocks'].items():
        L.append('| %s | %s |' % (k, ', '.join('%s %d' % kv for kv in sorted(v.items(), key=lambda x: -x[1]))))
    L += ['', '## INFO condition functions', '', '| function | uses | class | rule |', '|---|---:|---|---|']
    for c in g['conditions'][:25]:
        L.append('| %s | %d | %s | %s |' % (c['func'], c['count'], c['class'], c['rule']))
    L += ['', '## Static effect oracle types (effects_native.json.gz, this DLC)', '', '| effect type | count | L5 status |', '|---|---:|---|']
    for e in g['oracle_effect_types']:
        L.append('| %s | %d | %s |' % (e['type'], e['count'], e['status']))
    L += ['', '## Gap uses per quest (all reaching scripts)', '', '| quest | mapped uses | critical | high | medium | low |', '|---|---:|---:|---:|---:|---:|']
    for q, v in g['by_quest'].items():
        gs = v['gaps_by_severity']
        L.append('| %s | %d | %d | %d | %d | %d |' % (q, v['uses'], gs.get('critical', 0), gs.get('high', 0), gs.get('medium', 0), gs.get('low', 0)))
    L += ['', 'Unmapped functions: %s' % (', '.join(sorted(s['unmapped_functions'])) or 'none (every function used in this DLC has a table entry)'), '']
    return '\n'.join(L)


# ------------------------------------------------------------------------------------------------------ main
def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument('--out', default=HERE)
    a = ap.parse_args(argv)
    data = common.Data()
    items = analysis.collect_scripts(data)
    inv = build_inventory(data, items)
    gaps = analysis.build_gaps(data)
    sweep = build_sweep(data, inv, items)
    patch = build_rules_patch(data)
    dump(os.path.join(a.out, 'inventory.json'), inv)
    dump(os.path.join(a.out, 'sweep.json'), sweep)
    dump(os.path.join(a.out, 'gaps.json'), gaps)
    dump(os.path.join(a.out, 'rules.patch.json'), patch)
    write(os.path.join(a.out, 'gaps.md'), gaps_md(gaps))
    import docs_gen  # noqa: E402
    write(os.path.join(a.out, 'policy_review.md'), docs_gen.policy_review(data, items, inv, gaps, patch))
    write(os.path.join(a.out, 'dialogue.md'), docs_gen.dialogue_md(data, inv, items))
    print('inventory: %d quests, %d stages | sweep: %d stages (%d isolate, %d variants), %d probes, %d info probes | gaps: %d functions, %s' % (
        inv['counts']['quests'], inv['counts']['stages'], sweep['summary']['stages'], sweep['summary']['stages_isolate'],
        sweep['summary']['stage_variants'], sweep['summary']['probes'], sweep['summary']['info_probes'], gaps['summary']['distinct_functions'],
        gaps['summary']['gap_uses_by_severity']))
    return 0


if __name__ == '__main__':
    sys.exit(main())
