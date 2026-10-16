"""Shared loaders and full (uncapped) finding collector for the factions questline.

Read-only over out/questdata. Never touches the game install.
"""
import gzip
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.normpath(os.path.join(HERE, '..', '..'))
OUT = os.path.join(REPO, 'out', 'questdata')
sys.path.insert(0, os.path.join(REPO, 'tools', 'questdata'))
import classify  # noqa: E402

# group -> quests. The group is a lane label (policy file / data evidence), not an in-game faction claim.
# VMS12/37/43/44/46/57 are also analysed by the `factions` lane (primary owner there); the lead asked this lane to
# cover them too, so entries here carry also_covered_by. The Goodsprings side quests VMS01-06/09/09a/13 (side-goodsprings.md),
# VMS22, VMS26 and CGTutorial are in no other lane's inventory.quests[] (found by coverage.py), so they are owned here.
GROUPS = {
    'goodsprings_side': ['VMS01', 'VMS01REPCONGhouls', 'VMS02', 'VMS03', 'VMS04', 'VMS05', 'VMS06', 'VMS09', 'VMS09a', 'VMS13'],
    'unowned_side': ['VMS22', 'VMS26'],
    'chargen_tutorial': ['CGTutorial'],
    'also_in_factions': ['VMS12', 'VMS37', 'VMS43', 'VMS44', 'VMS46', 'VMS57'],
}
QUESTS = [q for g in GROUPS.values() for q in g]
GROUP_OF = {q: g for g, qs in GROUPS.items() for q in qs}
ALSO_COVERED_BY = {q: 'factions' for q in GROUPS['also_in_factions']}

THEME = {}   # filled from the objective / log text by gen.py (no hand-written story claims)

# Quests the data/policy files put elsewhere; listed for cross reference only (no sweep, no patch).
XREF = {
    'VMS16b': 'Run Goodsprings Run: M1 Goodsprings template (out/questdata/m1_goodsprings.json system_quests)',
}


def load():
    d, infos = classify.load(OUT)
    extra = classify.load_extra(OUT)
    by_edid = {q['edid']: q for q in d['quests'] if q.get('edid')}
    by_ref = {q['ref']: q for q in d['quests']}
    risk = json.load(open(os.path.join(OUT, 'risk.json'), encoding='utf-8'))['quests']
    kq = {q['edid'].lower() for q in d['quests'] if q.get('edid')}
    kg = {g['edid'].lower() for g in d['globals'] if g.get('edid')}
    return dict(d=d, infos=infos, extra=extra, by_edid=by_edid, by_ref=by_ref, risk=risk, kq=kq, kg=kg)


def gz(name):
    with gzip.open(os.path.join(OUT, name), 'rt', encoding='utf-8') as f:
        return json.load(f)


def load_order():
    return json.load(open(os.path.join(REPO, 'tools', 'questdata', 'loadorder.json'), encoding='utf-8'))['plugins']


def fid(ref, order=None):
    """'FalloutNV.esm:144519' -> runtime hex FormID (load-order prefix + local id), 8 hex digits."""
    order = order or load_order()
    plugin, local = ref.split(':')
    return '%02X%06X' % (order.index(plugin), int(local, 16) & 0xFFFFFF)


def scripts_of(ctx, q):
    """Yield (where, script_edid, source, dialogue_ctx, line_base) for every script that belongs to the quest."""
    extra = ctx['extra']
    r = q['ref']
    sd = q.get('script_data')
    if sd and sd.get('source'):
        yield 'quest_script', sd.get('edid'), sd['source'], None
    for s in q['stages']:
        for e in s['entries']:
            sc = e.get('script')
            if sc and sc.get('source'):
                yield 'stage %d' % s['index'], None, sc['source'], None
    for i in ctx['infos'].get(r, ()):
        iid = i['ref'].split(':')[1]
        for k in ('begin', 'end'):
            sc = i.get(k)
            if sc and sc.get('source'):
                yield 'info %s %s' % (iid, k), None, sc['source'], 'dialogue'
    touched = extra.get('touched', {}).get(r) if extra.get('touched') else None
    touched = None if touched is None else set(touched) | {t['ref'] for o in q['objectives'] for t in o['targets']}
    seen = set()
    for dr in q.get('dep_refs', ()):
        if touched is not None and dr not in touched:
            continue
        for label, sc in classify.dep_scripts(dr, extra):
            if id(sc) in seen:
                continue
            seen.add(id(sc))
            if sc and sc.get('source'):
                yield 'dep %s' % label, sc.get('edid'), sc['source'], 'terminal' if label.startswith('term') else None


def collect(ctx, q):
    """Every classify finding for the quest (uncapped) with where / script edid. Mirrors classify.rollup."""
    kq, kg = ctx['kq'], ctx['kg']
    r = q['ref']
    out = []

    def add(fs, where, script_edid=None):
        for f in fs:
            f = dict(f)
            f['where'] = where
            f['script'] = script_edid
            out.append(f)
    for where, sedid, src, dctx in scripts_of(ctx, q):
        fs = classify.classify_source(src, kq, kg, dctx)
        add(fs, where, sedid)
    for s in q['stages']:
        for e in s['entries']:
            if e.get('next_quest'):
                add([classify._f(0, 'next_quest', 'Q-STATE', 1, 'next_quest', False, None)], 'stage %d' % s['index'])
    for i in ctx['infos'].get(r, ()):
        iid = i['ref'].split(':')[1]
        add(classify.classify_conditions(i['conditions'], 'info'), 'info %s cond' % iid)
        if 'speech_challenge' in i.get('flags', []) or i.get('speech_challenge') not in (None, 'none'):
            add([classify._f(0, 'speech challenge', 'CONV', 3, 'speech_challenge', True, None)], 'info %s' % iid)
        if i.get('add_topics'):
            add([classify._f(0, 'add topics', 'Q-STATE', 1, 'addtopic', False, None)], 'info %s' % iid)
    add(classify.classify_conditions(q['conditions'], 'quest'), 'quest cond')
    return out
