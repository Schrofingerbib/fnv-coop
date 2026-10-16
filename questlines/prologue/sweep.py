"""sweep.json builder: the L7 capture-sweep configuration for the prologue questline.

Format = out/questdata/m1_goodsprings.json compatible (what plugin/snapshot/tools/goodsprings_sweep.py::load_plan reads:
quests[].edid / ref / tier(_direct) / stages[].index) plus the extra keys the brief asks for (`setup`, `excluded_stages`,
`reset_quests`, `infos`). A stage that is excluded from the sweep is NOT in `stages`; it is in `excluded_stages` with a reason.
"""
import re

import model

# Natural play order of the stages of each quest (curated from the stage texts + the scripts that set them). `setstage` works from
# any state, so this is a convenience for a realistic starting point, not an engine requirement. Stages of a quest that are not in
# its chain (alternatives, failure branches) get only `startquest` (plus the entries in EXTRA_PRE).
CHAINS = {
    'VMQ01': [10, 40, 50, 100],                       # 15 and 60 hang off 10 (texts: optional Victor talk / see objective 60)
    'nVPrimmDeputyConv': [20, 26, 30, 35, 45],        # 25 (Beagle rescued) hangs off 20; 100/110/120/130 are the endings
    'VNipton': [25],
    'VES01aVictorInNovac': [10],
    'VMQTops': [5],
}
CHAIN_HANG_OFF = {                     # stage -> the chain stages that must precede it
    ('VMQ01', 15): [10], ('VMQ01', 60): [10, 40, 50],
    ('nVPrimmDeputyConv', 25): [20], ('nVPrimmDeputyConv', 100): [20, 26],
    ('nVPrimmDeputyConv', 110): [20, 26, 30, 35, 45], ('nVPrimmDeputyConv', 120): [20, 26, 30, 35, 45],
    ('nVPrimmDeputyConv', 130): [20, 26, 30, 35, 45],
    ('VNipton', 30): [25], ('VNipton', 35): [25], ('VNipton', 70): [25],
    ('VES01aVictorInNovac', 100): [10],
    ('VMQTops', 10): [5],
}
# stage -> variants driven by a quest variable the stage script reads (the oracle lists the effect as conditional)
VARIANTS = {
    ('VMQ01', 40): [('primm-open', 'bPrimmClosed', 0), ('primm-closed', 'bPrimmClosed', 1)],
}
EXCLUDE_REASON = {
    'VMQTops': 'Ring-a-Ding-Ding: the Tops/Fort scene from stage 15 on belongs to the Act 2 / Ring-a-Ding-Ding questline '
               '(docs/quest-policies/main-shared-ring-a-ding-ding-and-act-2.md). This questline stops at "Benny located" '
               '(stage 5 = House points at Benny, stage 10 = Benny is at the Tops).',
}

DECL_RE = re.compile(r'(?im)^\s*(?:short|int|long|float|ref)\s+(\w+)')


def quest_vars(q):
    sd = q.get('script_data') or {}
    return {m.group(1).lower() for m in DECL_RE.finditer(sd.get('source', ''))}


def cmd_startquest(q):
    return 'startquest %s' % model.hexid(q['ref'])


def cmd_setstage(q, n):
    return 'setstage %s %d' % (model.hexid(q['ref']), n)


def stage_setup(q, stage):
    """Ordered console prerequisites for reaching `stage` of quest q from a reset state."""
    edid = q['edid']
    pre = CHAIN_HANG_OFF.get((edid, stage))
    if pre is None:
        chain = CHAINS.get(edid, [])
        pre = [s for s in chain if s < stage] if stage in chain else []
    return [cmd_startquest(q)] + [cmd_setstage(q, s) for s in pre]


def build_plan():
    """-> dict(quests=[...], reset=[hex...]) with per-stage setup and the exclusion table."""
    D = model.data()
    quests = []
    for edid, role in model.SCOPE.items():
        q = D['by_edid'][edid]
        swept = {s['index'] for s in model.in_scope_stages(edid)}
        all_idx = sorted({s['index'] for s in q['stages']})
        stages, excluded = [], []
        for idx in all_idx:
            if idx in swept:
                entry = dict(index=idx, console='setstage %s %d' % (model.hexid(q['ref']), idx), setup=stage_setup(q, idx))
                if (edid, idx) in VARIANTS:
                    entry['variants'] = [dict(name=n, setup=['set %s.%s to %d' % (edid, v, val)], var=v, value=val,
                                              syntax_note='quest-variable console syntax uses the quest EditorID; unverified in game')
                                         for n, v, val in VARIANTS[(edid, idx)]]
                oracle = D['native']['quests'].get(edid, {}).get('stages', {}).get(str(idx), {})
                entry['oracle'] = dict(unconditional=len(oracle.get('unconditional', [])), conditional=len(oracle.get('conditional', [])),
                                       types=sorted({e['type'] for k in ('unconditional', 'conditional') for e in oracle.get(k, [])}))
                stages.append(entry)
            else:
                excluded.append(dict(index=idx, reason=EXCLUDE_REASON[edid]))
        quests.append(dict(
            edid=edid, ref=q['ref'], formid=model.hexid(q['ref']), name=q.get('name'), role=role,
            tier=D['risk'][q['ref']]['tier'], tier_direct=D['risk'][q['ref']]['tier_direct'],
            plugin=q['plugin'],
            setup=[cmd_startquest(q)], stages=stages, excluded_stages=excluded,
            note=('no stages: story effects are in INFO result scripts, see `infos`' if not all_idx else None)))
    # quests the stage scripts touch (other than their own) must be reset too: their stage/flags would otherwise leak between runs
    reset = []
    for q in quests:
        if q['stages'] or q['excluded_stages']:   # like m1: only quests that have stages are reset (containers are stateless)
            reset.append(q['ref'])
    for q in list(quests):
        oracle = D['native']['quests'].get(q['edid'], {}).get('stages', {})
        for sidx, st in oracle.items():
            if int(sidx) not in {s['index'] for s in q['stages']}:
                continue
            for e in st.get('unconditional', []) + st.get('conditional', []):
                r = e.get('quest')
                if r and r in D['by_ref'] and r not in reset:
                    reset.append(r)
        for s in D['by_edid'][q['edid']]['stages']:
            for en in s['entries']:
                nq = en.get('next_quest')
                if nq and nq in D['by_ref'] and nq not in reset and s['index'] in {x['index'] for x in q['stages']}:
                    reset.append(nq)
    return dict(quests=quests, reset=reset)


def build_infos():
    """Scripted INFOs of the questline with their static oracle: not stage-sweepable, covered by dialogue specs / the two-game test."""
    D = model.data()
    nat = D['native']['infos']
    import gzip
    import json
    dials = {d['ref']: d for d in json.load(gzip.open(model.QD + '/dials.json.gz', 'rt', encoding='utf-8'))}
    rows = []
    for edid in model.SCOPE:
        for i in model.in_scope_infos(edid):
            if not (i.get('begin') or i.get('end')):
                continue
            o = nat.get(i['ref'], {})
            t = dials.get(i['topic'], {})
            rows.append(dict(info=i['ref'], quest=edid, topic=i['topic'], topic_edid=t.get('edid'),
                             hooks=[k for k in ('begin', 'end') if i.get(k)],
                             oracle={k: dict(unconditional=len(o.get(k, {}).get('unconditional', [])),
                                             conditional=len(o.get(k, {}).get('conditional', [])))
                                     for k in ('begin', 'end') if i.get(k)}))
    return rows


def build_sweep(dialogue_info_refs):
    D = model.data()
    p = build_plan()
    infos = build_infos()
    for r in infos:
        r['covered_by'] = ('dialogue_specs' if r['info'] in dialogue_info_refs else
                           'two-game test via dialogue runner (not stage-sweepable)')
    n_stages = sum(len(q['stages']) for q in p['quests'])
    n_excl = sum(len(q['excluded_stages']) for q in p['quests'])
    return dict(
        schema=1, milestone='Prologue (Q-prologue): They Went That-a-Way + Primm/Nipton/Novac/Boulder City legs + Benny lead-in',
        format='m1_goodsprings.json compatible: quests[].edid/ref/tier_direct/stages[].index (L7 goodsprings_sweep.load_plan); '
               'extras: setup, excluded_stages, reset_quests, infos',
        loadorder=D['loadorder']['plugins'], core={q['edid']: q['name'] for q in p['quests'] if q['role'] == 'core'},
        reference_done={e: D['by_edid'][e].get('name') for e in model.REFERENCE if e in D['by_edid']},
        reset_quests=[model.hexid(r) for r in p['reset']],
        quests=p['quests'], infos=infos,
        summary=dict(quests=len(p['quests']), stages_swept=n_stages, stages_excluded=n_excl,
                     stages_total=n_stages + n_excl, scripted_infos=len(infos),
                     quests_without_stages=[q['edid'] for q in p['quests'] if not q['stages'] and not q['excluded_stages']]),
        usage='L7: copy goodsprings_sweep.py, point load_plan at this file (or pass --plan), run each stage entry\'s `setup` '
              'after test.quest.reset({reset_quests}) and before `console`.')
