"""Milestone M1 (Goodsprings) dependency set -> out/questdata/m1_goodsprings.json.

Usage: python m1.py [--in DIR] [--out DIR] [--data GAME_DATA_DIR]
Core quests (by real EDID, verified against the ESM): VCG01 Ain't That a Kick in the Head, VCG02 Back in the Saddle,
VCG03 By a Campfire on the Trail, VMS16 Ghost Town Gunfight. System/dialogue quests are the Goodsprings-hosting and
prologue containers; "touched" quests are one hop away (script refs, setstage calls, conditions).
"""
import argparse
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import classify  # noqa: E402
import extract  # noqa: E402
import forms as forms_mod  # noqa: E402

CORE = {'VCG01': "Ain't That a Kick in the Head", 'VCG02': 'Back in the Saddle',
        'VCG03': 'By a Campfire on the Trail', 'VMS16': 'Ghost Town Gunfight'}
SYSTEM = ['VMS16b', 'VCG00', 'VCG04', 'VFreeformGoodsprings', 'vDialogueGoodspringsPowderGanger',
          'VDialogueGoodspringsGeneric', 'vGSRadioQuest', 'VERGoodsprings01']
ITEM_RE = re.compile(r'(?im)^\s*(?:(\w+)\.)?(additem|removeitem|additemns|equipitem)\s+(\w+)(?:\s+(-?\d+))?')
TOGGLE_RE = re.compile(r'(?im)^\s*(?:(\w+)\.)?(enable|disable)\b(?!\w)')
SETSTAGE_RE = re.compile(r'(?im)\bsetstage\s+(\w+)\s+(\d+)')
SET_RE = re.compile(r'(?im)^\s*set\s+(\w+)\s+to\b')
SUBJ_RE = re.compile(r'(?m)\b([A-Za-z_]\w*)\.(?=[A-Za-z])')
OBJ_RE = re.compile(r'(?im)\bsetobjective(displayed|completed)\s+(\w+)\s+(\d+)\s+([01])')


def scripts_of(q, infos):
    out = []
    if q.get('script_data') and q['script_data'].get('source'):
        out.append(('quest_script', q['script_data']))
    for s in q['stages']:
        for e in s['entries']:
            if e.get('script'):
                out.append((f'stage {s["index"]}', e['script']))
    for i in infos:
        for k in ('begin', 'end'):
            if i.get(k):
                out.append((f'info {i["ref"].split(":")[1]} {k}', i[k]))
    return out


def conds_of(q, infos):
    cs = list(q['conditions'])
    for s in q['stages']:
        for e in s['entries']:
            cs += e['conditions']
    for o in q['objectives']:
        for t in o['targets']:
            cs += t['conditions']
    for i in infos:
        cs += i['conditions']
    return cs


def main(argv=None):
    here = os.path.dirname(os.path.abspath(__file__))
    default = os.path.join(here, '..', '..', 'out', 'questdata')
    ap = argparse.ArgumentParser()
    ap.add_argument('--in', dest='indir', default=default)
    ap.add_argument('--out', default=default)
    ap.add_argument('--data', default=extract.DEFAULT_DATA)
    a = ap.parse_args(argv)
    d, infos = classify.load(a.indir)
    risk = json.load(open(os.path.join(a.indir, 'risk.json'), encoding='utf-8'))['quests']
    by_edid = {q['edid']: q for q in d['quests'] if q.get('edid')}
    by_ref = {q['ref']: q for q in d['quests']}
    globs = {g['ref']: g for g in d['globals']}
    glob_names = {g['edid'].lower(): g['edid'] for g in d['globals'] if g.get('edid')}
    for k, nm in CORE.items():
        assert by_edid[k]['name'] == nm, (k, by_edid[k]['name'])
    members = list(CORE) + [s for s in SYSTEM if s in by_edid]

    # one-hop "touched" quests
    touched = {}
    for k in members:
        q = by_edid[k]
        ins = infos.get(q['ref'], [])
        for _, sc in scripts_of(q, ins):
            for r in sc['refs']:
                if r in by_ref and r != q['ref']:
                    touched.setdefault(by_ref[r]['edid'], set()).add(f'{k}: script ref')
            for qn, _ in SETSTAGE_RE.findall(sc['source']):
                if qn in by_edid and qn != k:
                    touched.setdefault(qn, set()).add(f'{k}: setstage')
        for c in conds_of(q, ins):
            if c['func'] in classify.QUEST_DEP_FUNCS and c['p1'] in by_ref and c['p1'] != q['ref']:
                touched.setdefault(by_ref[c['p1']]['edid'], set()).add(f'{k}: condition {c["func"]}')
    for k in list(touched):
        if k in members:
            del touched[k]

    # collect refs to name
    want = set()
    for k in members:
        q = by_edid[k]
        ins = infos.get(q['ref'], [])
        for o in q['objectives']:
            want.update(t['ref'] for t in o['targets'] if t['ref'])
        for _, sc in scripts_of(q, ins):
            want.update(r for r in sc['refs'] if r)
        want.update(i['speaker'] for i in ins if i.get('speaker'))
        for c in conds_of(q, ins):
            for p in (c.get('p1'), c.get('p2'), c.get('ref')):
                if isinstance(p, str) and ':' in p:
                    want.add(p)
    paths = [os.path.join(a.data, n) for n in extract.PLUGINS if os.path.exists(os.path.join(a.data, n))]
    forms = forms_mod.lookup(paths, want)
    forms.update(forms_mod.lookup(paths, {f['base'] for f in forms.values() if f.get('base')} - set(forms)))

    def nm(ref):
        f = forms.get(ref)
        if not f:
            return {'ref': ref}
        o = {'ref': ref, 'sig': f['sig'], 'edid': f.get('edid'), 'name': f.get('name')}
        b = forms.get(f.get('base') or '')
        if b:
            o['base'] = {'ref': f['base'], 'sig': b['sig'], 'edid': b.get('edid'), 'name': b.get('name')}
        return o

    agg = {'globals': set(), 'items': {}, 'refs_toggled': {}, 'npcs': {}, 'cross_quest_setstage': set(), 'objects': {}}
    qout = []
    for k in members:
        q = by_edid[k]
        ins = infos.get(q['ref'], [])
        rq = risk[q['ref']]
        scr = scripts_of(q, ins)
        gl, items, toggles, subj, setstages, objs = set(), {}, {}, set(), set(), []
        for where, sc in scr:
            src = sc['source']
            for g in SET_RE.findall(src):
                if g.lower() in glob_names:
                    gl.add(glob_names[g.lower()])
            for r in sc['refs']:
                if r in globs and globs[r].get('edid'):
                    gl.add(globs[r]['edid'])
            for who, fn, item, n in ITEM_RE.findall(src):
                key = (item, fn.lower(), (who or 'self').lower())
                items[key] = items.get(key, 0) + 1
            for who, fn in TOGGLE_RE.findall(src):
                toggles.setdefault((who or 'self'), set()).add(fn.lower())
            subj.update(x for x in SUBJ_RE.findall(src) if x.lower() not in ('player', 'playerref'))
            for qn, idx in SETSTAGE_RE.findall(src):
                if qn != k:
                    setstages.add((qn, int(idx)))
            if where.startswith('stage'):
                for kind, qn, n, v in OBJ_RE.findall(src):
                    objs.append({'stage': where, 'kind': kind.lower(), 'quest': qn, 'objective': int(n), 'value': int(v)})
        for c in conds_of(q, ins):
            for p in (c.get('p1'), c.get('value')):
                if isinstance(p, str) and p in globs and globs[p].get('edid'):
                    gl.add(globs[p]['edid'])
        npcs = {}
        for i in ins:
            if i.get('speaker'):
                npcs[i['speaker']] = nm(i['speaker'])
        for o in q['objectives']:
            for t in o['targets']:
                if t['ref']:
                    npcs[t['ref']] = nm(t['ref'])
        refs_used = {}
        for _, sc in scr:
            for r in sc['refs']:
                if r in forms:
                    refs_used[r] = nm(r)
        agg['globals'] |= gl
        for (item, fn, who), n in items.items():
            agg['items'].setdefault(item, {}).setdefault(f'{who}.{fn}', 0)
            agg['items'][item][f'{who}.{fn}'] += n
        for who, fns in toggles.items():
            agg['refs_toggled'].setdefault(who, set()).update(fns)
        for r, v in {**npcs, **refs_used}.items():
            (agg['npcs'] if (v.get('sig') in ('NPC_', 'CREA', 'ACHR', 'ACRE')) else agg['objects'])[r] = v
        agg['cross_quest_setstage'] |= {f'{a_} {b_}' for a_, b_ in setstages}
        qout.append({
            'edid': k, 'ref': q['ref'], 'name': q.get('name'), 'role': 'core' if k in CORE else 'system',
            'tier': rq['tier'], 'tier_direct': rq['tier_direct'], 'cats': rq['cats'], 'priority': q.get('priority'),
            'flags': q.get('flags'), 'script': q.get('script'),
            'stages': [{'index': s['index'], 'log': next((e.get('text') for e in s['entries'] if e.get('text')), None),
                        'flags': sorted({f for e in s['entries'] for f in e['flags']}),
                        'has_script': any(e.get('script') for e in s['entries']),
                        'findings': [dict(cat=f['cat'], fn=f['fn'], level=f['level'], text=f['text'])
                                     for f in rq['stage_findings'] if f['where'] == f'stage {s["index"]}'],
                        'objective_effects': [o for o in objs if o['stage'] == f'stage {s["index"]}']}
                       for s in sorted(q['stages'], key=lambda s: s['index'])],
            'objectives': [{'index': o['index'], 'text': o.get('text'), 'targets': [nm(t['ref']) for t in o['targets']]}
                           for o in q['objectives']],
            'globals': sorted(gl), 'items': [{'item': i, 'fn': f, 'subject': w, 'occurrences': n}
                                             for (i, f, w), n in sorted(items.items())],
            'enable_disable': {w: sorted(f) for w, f in sorted(toggles.items())},
            'script_subjects': sorted(subj), 'setstage_other_quests': sorted(f'{a_} {b_}' for a_, b_ in setstages),
            'npcs': sorted(npcs.values(), key=lambda v: v['ref']),
            'dialogue': {'infos': len(ins), 'scripted_infos': sum(1 for i in ins if i.get('begin') or i.get('end')),
                         'speakers': sorted({i['speaker'] for i in ins if i.get('speaker')}),
                         'topics': len(q.get('topics', []))},
        })
    out = {
        'milestone': 'M1 Goodsprings', 'core': CORE, 'system_quests': [s for s in SYSTEM if s in by_edid],
        'touched_quests': [{'edid': e, 'name': by_edid[e].get('name'), 'stages': len(by_edid[e]['stages']),
                            'why': sorted(w)} for e, w in sorted(touched.items())],
        'summary': {
            'quests': len(qout), 'stages': sum(len(q['stages']) for q in qout),
            'objectives': sum(len(q['objectives']) for q in qout),
            'infos': sum(q['dialogue']['infos'] for q in qout),
            'globals': sorted(agg['globals']),
            'items': {k: v for k, v in sorted(agg['items'].items())},
            'enable_disable_subjects': {k: sorted(v) for k, v in sorted(agg['refs_toggled'].items())},
            'cross_quest_setstage': sorted(agg['cross_quest_setstage']),
            'npcs': sorted(agg['npcs'].values(), key=lambda v: v['ref']),
            'objects': sorted(agg['objects'].values(), key=lambda v: v['ref']),
        },
        'quests': qout,
    }
    p = os.path.join(a.out, 'm1_goodsprings.json')
    with open(p, 'w', encoding='utf-8') as f:
        json.dump(out, f, separators=(',', ':'), ensure_ascii=False)
    s = out['summary']
    print(f'M1: {s["quests"]} quests, {s["stages"]} stages, {s["objectives"]} objectives, {s["infos"]} INFOs, '
          f'{len(s["globals"])} globals, {len(s["items"])} items, {len(s["npcs"])} NPC refs, '
          f'{len(out["touched_quests"])} touched quests -> {p} ({os.path.getsize(p)} bytes)')
    return 0


if __name__ == '__main__':
    sys.exit(main())
